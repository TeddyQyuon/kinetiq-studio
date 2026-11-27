import asyncio
import hashlib
import shutil
import socket
import subprocess
import time
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def studio(tmp_path, monkeypatch):
    # Delay app imports so collection never changes the pipeline suite's environment.
    from app import main
    from app.core import db, rate_limit
    from app.core.config import get_settings
    from app.events import router as events
    from app.identity import access

    engine = create_engine(f"sqlite:///{tmp_path / 'studio.sqlite'}")
    sessions = sessionmaker(engine, expire_on_commit=False)
    db.Base.metadata.create_all(engine)
    for module in (db, access, events):
        monkeypatch.setattr(module, "SessionLocal", sessions)
    monkeypatch.setattr(rate_limit, "_local", rate_limit.SlidingWindow())
    cfg = get_settings()
    for key, value in dict(
        app_env="phase0_local",
        redis_url="",
        storage_provider="local",
        local_vault_dir=str(tmp_path / "vault"),
        session_secret="s" * 48,
        google_client_id="client",
        google_client_secret="secret",
        owner_google_email="owner@example.test",
    ).items():
        monkeypatch.setattr(cfg, key, value)
    client = TestClient(main.app, base_url="https://studio.test")
    try:
        yield SimpleNamespace(client=client, sessions=sessions, cfg=cfg)
    finally:
        client.close()
        engine.dispose()


def mock_google(monkeypatch, profile):
    from app.identity import service

    def respond(request):
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh"})
        return httpx.Response(200, json=profile)

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        service, "httpx", SimpleNamespace(AsyncClient=lambda **kw: httpx.AsyncClient(transport=transport))
    )


def login(client):
    start = client.get("/v1/auth/google/start")
    assert start.status_code == 200
    return client.get(
        "/v1/auth/google/callback", params={"code": "code", "state": start.json()["state"]}, follow_redirects=False
    )


def test_vps_owner_cookie_csrf_and_revocation(studio, monkeypatch):
    from app.identity.cookies import CSRF, SESSION

    studio.cfg.app_env = "phase1_vps"
    c = studio.client
    for path in ("/v1/me", "/v1/workers", "/v1/projects", "/v1/events"):
        assert c.get(path).status_code == 401
    assert c.post("/v1/workers/tokens", json={"name": "w"}).status_code == 401
    assert c.delete("/v1/workers/unknown", headers={"Authorization": "Bearer unused"}).status_code == 401
    profile = {"sub": "sub1", "email": "other@example.test", "email_verified": True}
    mock_google(monkeypatch, profile)
    assert login(c).status_code == 401
    profile["email"] = studio.cfg.owner_google_email
    response = login(c)
    assert response.is_redirect
    session_header = next(v for v in response.headers.get_list("set-cookie") if v.startswith(SESSION + "="))
    assert all(value in session_header.lower() for value in ("httponly", "samesite=lax", "secure", "path=/"))
    cookie = c.cookies[SESSION]
    csrf = c.get("/v1/me").json()["csrf_token"]
    assert csrf == c.cookies[CSRF]
    token_route = "/v1/workers/tokens"
    assert c.post(token_route, json={"name": "w"}).status_code == 403
    assert c.post(token_route, json={"name": "w"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    c.cookies.set(CSRF, "other", domain="studio.test", path="/")
    assert c.post(token_route, json={"name": "w"}, headers={"X-CSRF-Token": "other"}).status_code == 403
    c.cookies.set(CSRF, csrf, domain="studio.test", path="/")
    assert c.post(token_route, json={"name": "w"}, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    c.cookies.set(SESSION, cookie, domain="studio.test", path="/")
    assert c.get("/v1/me").status_code == 401


def test_session_integrity_expiry_and_pkce_browser_binding(studio):
    from app.core.config import Settings
    from app.core.errors import UnauthorizedError
    from app.identity.cookies import SESSION, seal, unseal

    with pytest.raises(ValueError, match="SESSION_SECRET"):
        Settings(app_env="phase1_vps", session_secret="dev-only-session-secret-change-me")
    for cookie in (seal(SESSION, "data", -1), seal(SESSION, "data", 600) + "x", seal("other", "data", 600)):
        with pytest.raises(UnauthorizedError):
            unseal(SESSION, cookie)
    studio.cfg.app_env = "phase1_vps"
    state = studio.client.get("/v1/auth/google/start").json()["state"]
    studio.client.cookies.clear()
    assert studio.client.get("/v1/auth/google/callback", params={"code": "code", "state": state}).status_code == 401


def test_local_mode_and_per_worker_sliding_limits(studio, monkeypatch):
    from app.core import rate_limit

    c = studio.client
    assert c.get("/v1/me").status_code == 200
    credentials = [c.post("/v1/workers/tokens", json={"name": name}).json() for name in ("one", "two")]
    studio.cfg.app_env = "phase1_vps"
    monkeypatch.setattr(studio.cfg, "worker_rate_limit", 2)
    monkeypatch.setattr(studio.cfg, "worker_rate_window_seconds", 10)
    now = 100
    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(monotonic=lambda: now))

    def heartbeat(i):
        worker = credentials[i]
        return c.post(
            "/v1/workers/heartbeat",
            json={"worker_id": worker["worker_id"]},
            headers={"Authorization": "Bearer " + worker["worker_token"]},
        )

    assert heartbeat(0).status_code == 200
    now += 6
    assert heartbeat(0).status_code == 200
    assert heartbeat(0).headers["retry-after"] == "4"
    assert heartbeat(1).status_code == 200
    now += 4
    assert heartbeat(0).status_code == 200
    assert heartbeat(0).status_code == 429


def test_reconciliation_checks_provider_and_only_emits_transitions(studio, monkeypatch):
    from app.identity.service import ensure_default_owner
    from app.models import Asset, EventOutbox, Project
    from app.storage import drive
    from app.storage.local import vault_path
    from app.storage.reconcile import reconcile_vault

    statuses = {"gone": 404, "busy": 503}
    props = {"project_id": "p", "kind": "proxy", "scene_id": ""}

    def respond(request):
        file_id = request.url.path.split("/")[-1]
        assert "appProperties" in request.url.params["fields"]
        assert request.headers["Authorization"] == "Bearer token"
        return httpx.Response(
            statuses.get(file_id, 200),
            json={
                "size": "3",
                "appProperties": props if file_id != "other" else {**props, "kind": "master"},
            },
        )

    async def token(db, owner):
        return "token"

    monkeypatch.setattr(drive, "get_valid_google_access_token", token)
    monkeypatch.setattr(drive.httpx, "AsyncClient", mock_transport_client(respond))
    with studio.sessions() as db:
        owner = ensure_default_owner(db)
        db.add(Project(id="p", owner_id=owner.id, title="p", topic="t"))
        db.commit()
        rows = {}
        for name in ("good", "short", "corrupt", "lost", "gone", "busy", "other", "drive-ok"):
            local = name in ("good", "short", "corrupt", "lost")
            a = Asset(
                project_id="p",
                kind="proxy",
                storage_provider="local" if local else "drive",
                storage_key=name,
                size_bytes=3,
                drive_file_id=None if local else name,
                sync_state="synced",
            )
            if local and name != "lost":
                path = vault_path(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"ab" if name == "short" else b"abc")
            if name == "corrupt":
                a.sha256 = hashlib.sha256(b"xyz").hexdigest()
            db.add(a)
            rows[name] = a
        db.commit()
        assert asyncio.run(reconcile_vault(db)) == 5
        assert {n: a.sync_state for n, a in rows.items()} == {
            "good": "synced",
            "short": "mismatch",
            "corrupt": "mismatch",
            "lost": "missing",
            "gone": "missing",
            "busy": "synced",
            "other": "mismatch",
            "drive-ok": "synced",
        }
        assert asyncio.run(reconcile_vault(db)) == 0
        vault_path("lost").write_bytes(b"abc")
        assert asyncio.run(reconcile_vault(db)) == 1
        assert rows["lost"].sync_state == "synced"
        events = db.scalars(select(EventOutbox)).all()
        assert len(events) == 6 and all(e.project_id == "p" for e in events)


def mock_transport_client(handler):
    client = httpx.AsyncClient
    return lambda **kw: client(transport=httpx.MockTransport(handler))


def test_publish_only_after_outer_commit(studio, monkeypatch):
    from app.events import service

    publisher = Mock()
    monkeypatch.setattr(service, "_publisher", lambda url: publisher)
    studio.cfg.redis_url = "redis://unused"
    with studio.sessions() as db:
        service.emit_event(db, "discard", {})
        db.rollback()
        with db.begin_nested():
            first = service.emit_event(db, "keep", {})
        nested = db.begin_nested()
        service.emit_event(db, "discard", {})
        nested.rollback()
        publisher.publish.assert_not_called()
        db.commit()
        publisher.publish.assert_called_once_with(service.CHANNEL, str(first.id))


@pytest.fixture
def live_redis(tmp_path):
    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("live Redis integration requires redis-server on PATH")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(
        [executable, "--port", str(port), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no"],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
    )
    url = f"redis://127.0.0.1:{port}"
    try:
        with Redis.from_url(url) as client:
            for _ in range(100):
                try:
                    if client.ping():
                        break
                except RedisConnectionError:
                    time.sleep(0.02)
            else:
                pytest.fail("Redis did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.parametrize("redis_enabled", [False, True])
def test_sse_replay_and_live_fanout(studio, request, redis_enabled):
    from app.events.router import stream_events
    from app.events.service import emit_event

    if redis_enabled:
        studio.cfg.redis_url = request.getfixturevalue("live_redis")
    with studio.sessions() as db:
        cursor = emit_event(db, "previous", {}).id
        emit_event(db, "unrelated", {}, project_id="other")
        for i in range(101):
            emit_event(db, "replay", {"i": i}, project_id="p")
        global_id = emit_event(db, "global", {}).id
        db.commit()

    async def read_streams():
        async def connect():
            response = await stream_events("p", None, str(cursor))
            return response.body_iterator

        async with aclosing(await connect()) as a, aclosing(await connect()) as b:
            for stream in (a, b):
                for _ in range(101):
                    assert "event: replay\n" in await anext(stream)
                assert (await anext(stream)).startswith(f"id: {global_id}\nevent: global\n")
            if redis_enabled:
                with Redis.from_url(studio.cfg.redis_url) as observer:
                    from app.events.service import CHANNEL

                    assert observer.pubsub_numsub(CHANNEL)[0][1] == 2
            tasks = [asyncio.create_task(anext(stream)) for stream in (a, b)]
            await asyncio.sleep(0.05)
            with studio.sessions() as db:
                live_id = emit_event(db, "live", {}, project_id="p").id
                db.commit()
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=4)
            assert all(s.startswith(f"id: {live_id}\nevent: live\n") for s in results)

    asyncio.run(read_streams())


def test_shared_redis_rate_limit(studio, live_redis, monkeypatch):
    from fastapi import HTTPException
    from app.core.rate_limit import check_worker_rate

    studio.cfg.redis_url = live_redis
    monkeypatch.setattr(studio.cfg, "worker_rate_limit", 1)
    check_worker_rate("worker:one")
    with pytest.raises(HTTPException) as exc:
        check_worker_rate("worker:one")
    assert exc.value.status_code == 429
    check_worker_rate("worker:two")


def test_redis_failure_preserves_committed_replay(studio, monkeypatch):
    from app.events import router, service
    from redis.exceptions import ConnectionError

    studio.cfg.redis_url = "redis://unavailable"
    publisher = Mock()
    publisher.publish.side_effect = ConnectionError("offline")
    monkeypatch.setattr(service, "_publisher", lambda url: publisher)
    monkeypatch.setattr(router.Redis, "from_url", Mock(side_effect=ConnectionError("offline")))
    with studio.sessions() as db:
        event_id = service.emit_event(db, "kept", {}).id
        db.commit()

    async def replay():
        async with aclosing(router.event_stream(0, None)) as stream:
            assert (await anext(stream)).startswith(f"id: {event_id}\nevent: kept\n")

    asyncio.run(replay())


def test_resumable_local_upload_survives_retry(studio):
    from app.storage.local import start_upload, vault_path

    key = "p/clips/master/clip.bin"
    token = start_upload(key, 6)
    url = f"/v1/storage/uploads/{token}"
    studio.cfg.app_env = "phase1_vps"
    c = studio.client

    def put(content_range, data=b""):
        return c.put(url, headers={"Content-Range": content_range}, content=data)

    assert put("bytes 0-2/6", b"abc").status_code == 308
    assert put("bytes */6").headers["range"] == "bytes=0-2"
    assert not vault_path(key).exists()
    assert put("bytes 0-2/6", b"abc").status_code == 409
    assert put("bytes 3-5/6", b"def").status_code == 201
    assert put("bytes */6").status_code == 201
    assert vault_path(key).read_bytes() == b"abcdef"


def test_local_oauth_does_not_require_session_cookies(studio, monkeypatch):
    mock_google(monkeypatch, {"email": "local@example.test", "sub": "owner"})
    c = studio.client
    state = c.get("/v1/auth/google/start").json()["state"]
    c.cookies.clear()
    params = {"code": "code", "state": state}
    assert c.get("/v1/auth/google/callback", params=params, follow_redirects=False).is_redirect
    assert c.get("/v1/me").json()["drive_connected"]
    assert c.get("/v1/auth/google/callback", params=params, follow_redirects=False).status_code == 401
