"""Control Plane download, disaster recovery, and operational contracts."""

import asyncio
import json
import re
from contextlib import aclosing, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker


@contextmanager
def database(path):
    from app.core.db import Base

    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(conn, _record):
        conn.execute("PRAGMA foreign_keys=ON")

    try:
        Base.metadata.create_all(engine)
        yield sessionmaker(engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()


@pytest.fixture
def studio(tmp_path, monkeypatch):
    from app import main
    from app.core import db
    from app.core.config import get_settings
    from app.events import router as events
    from app.identity import access

    cfg = get_settings()
    for name, value in {
        "local_vault_dir": str(tmp_path / "vault"),
        "storage_provider": "local",
        "app_env": "phase0_local",
        "auto_export_draft_on_ready": False,
        "redis_url": "",
    }.items():
        monkeypatch.setattr(cfg, name, value)
    with database(tmp_path / "studio.sqlite") as sessions:
        for module in (db, access, events):
            monkeypatch.setattr(module, "SessionLocal", sessions)
        client = TestClient(main.app, base_url="https://studio.test")
        try:
            yield SimpleNamespace(client=client, sessions=sessions, cfg=cfg)
        finally:
            client.close()


def new_project(db):
    from app.projects.schemas import ProjectCreateRequest
    from app.projects.service import create_project_with_initial_timeline

    return create_project_with_initial_timeline(
        db,
        ProjectCreateRequest(
            title="Tidal power",
            topic="How tidal turbines generate energy",
            aspect="9:16",
            target_res="4k",
        ),
    )


@pytest.fixture
def movie(studio):
    from app.models import Asset, Export
    from app.storage.local import vault_path

    data = b"0123456789abcdef"
    with studio.sessions() as db:
        project = new_project(db)
        key = f"{project.id}/exports/tidal.mp4"
        path = vault_path(key)
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        asset = Asset(
            project_id=project.id,
            kind="export",
            storage_key=key,
            storage_provider="local",
            size_bytes=len(data),
            content_type="video/mp4",
            sync_state="synced",
        )
        db.add(asset)
        db.flush()
        export = Export(project_id=project.id, revision=1, resolution="4k", status="ready", master_asset_id=asset.id)
        db.add(export)
        db.commit()
        return SimpleNamespace(
            data=data,
            key=key,
            asset_id=asset.id,
            owner=project.owner_id,
            export_id=export.id,
            url=f"/v1/exports/{export.id}/download",
            filename=f"kinetiq_{project.id}_4k.mp4",
        )


@pytest.mark.parametrize(
    ("range_header", "bounds", "inline"),
    [
        (None, None, False),
        ("bytes=2-5", (2, 5), True),
        ("bytes=15-", (15, 15), False),
        ("bytes=-3", (13, 15), True),
        ("bytes=-99", (0, 15), False),
        ("BYTES=1-2", (1, 2), True),
        ("bytes=14-99", (14, 15), False),
    ],
)
def test_local_export_download(studio, movie, range_header, bounds, inline):
    response = studio.client.get(
        movie.url,
        params={"inline": "1"} if inline else {},
        headers={"Range": range_header} if range_header else {},
    )
    assert (
        response.headers["content-disposition"]
        == f'{"inline" if inline else "attachment"}; filename="{movie.filename}"'
    )
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "video/mp4"
    if bounds is None:
        assert response.status_code == 200
        assert "content-range" not in response.headers
        expected = movie.data
    else:
        start, end = bounds
        assert response.status_code == 206
        assert response.headers["content-range"] == f"bytes {start}-{end}/{len(movie.data)}"
        expected = movie.data[start : end + 1]
    assert response.content == expected
    assert response.headers["content-length"] == str(len(expected))


@pytest.mark.parametrize("header", ["bytes=16-", "bytes=9-2", "bytes=-0", "bytes=-", "bytes=a-b", "bytes=0-1,4-5"])
def test_unsatisfiable_export_range(studio, movie, header):
    response = studio.client.get(movie.url, headers={"Range": header})
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(movie.data)}"


def test_local_stream_in_bounded_chunks(movie):
    from app.storage.drive import LocalVaultProvider

    async def read():
        provider = LocalVaultProvider()
        chunks = [
            chunk
            async for chunk in provider.stream_range(
                user_id=movie.owner,
                storage_key=movie.key,
                drive_file_id=None,
                start=2,
                end=12,
                chunk_size=4,
            )
        ]
        assert b"".join(chunks) == movie.data[2:13]
        assert all(0 < len(chunk) <= 4 for chunk in chunks)
        data, size, _ = await provider.read_range(
            user_id=movie.owner, storage_key=movie.key, drive_file_id=None, start=0, end=1
        )
        assert (data, size) == (movie.data[:2], len(movie.data))

    asyncio.run(read())


def test_large_local_download_stops_reading_on_disconnect(studio, movie, monkeypatch):
    from fastapi import Request
    from app.exports.router import download_export
    from app.storage.drive import LocalVaultProvider
    from app.storage.local import vault_path

    size = 5 * 1024**3
    with vault_path(movie.key).open("r+b") as fh:
        fh.truncate(size)
        fh.seek(-1, 2)
        fh.write(b"z")
    tail = studio.client.get(movie.url, headers={"Range": "bytes=-1"})
    assert tail.status_code == 206 and tail.content == b"z"
    assert tail.headers["content-range"] == f"bytes {size - 1}-{size - 1}/{size}"

    original = LocalVaultProvider.stream_range
    reads = []

    async def observed(provider, *args, **kwargs):
        async with aclosing(original(provider, *args, **kwargs)) as chunks:
            async for chunk in chunks:
                reads.append(len(chunk))
                yield chunk

    monkeypatch.setattr(LocalVaultProvider, "stream_range", observed)

    async def cancel_after_first_chunk(db):
        started = asyncio.Event()
        scope = {"type": "http", "method": "GET", "headers": [], "asgi": {"spec_version": "2.3"}}
        response = await download_export(movie.export_id, Request(scope), db=db)

        async def receive():
            await started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                assert dict(message["headers"])[b"content-length"] == str(size).encode()
            elif message["type"] == "http.response.body":
                started.set()
                await asyncio.Event().wait()  # Socket backpressure while the browser closes.

        await asyncio.wait_for(response(scope, receive, send), timeout=2)

    with studio.sessions() as db:
        asyncio.run(cancel_after_first_chunk(db))
    assert reads == [2 * 1024**2]


class TrackedBody(httpx.AsyncByteStream):
    """Count upstream reads so a reintroduced eager GET fails on the first chunk."""

    def __init__(self, data, chunk_size=3):
        self.data = data
        self.chunk_size = chunk_size
        self.sent = 0
        self.closed = False

    async def __aiter__(self):
        for offset in range(0, len(self.data), self.chunk_size):
            chunk = self.data[offset : offset + self.chunk_size]
            self.sent += len(chunk)
            yield chunk

    async def aclose(self):
        self.closed = True


def drive_download_transport(monkeypatch, movie, chunk_size=3):
    from app.storage import drive

    bodies = []
    requested_ranges = []

    def respond(request):
        assert request.method == "GET"
        assert request.url.path.endswith("/drive-file")
        assert request.headers["authorization"] == "Bearer test-token"
        size = len(movie.data)
        if request.url.params.get("alt") != "media":
            return httpx.Response(200, json={"id": "drive-file", "size": str(size), "mimeType": "video/mp4"})
        sent_range = request.headers["range"]
        requested_ranges.append(sent_range)
        first, last = sent_range.removeprefix("bytes=").split("-")
        start, end = int(first), int(last) if last else size - 1
        body = TrackedBody(movie.data[start : end + 1], chunk_size=chunk_size)
        bodies.append(body)
        return httpx.Response(
            206,
            stream=body,
            headers={
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Type": "video/mp4",
                "Content-Length": str(end - start + 1),
            },
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        drive.httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(respond), **kw)
    )
    monkeypatch.setattr(drive, "get_valid_google_access_token", AsyncMock(return_value="test-token"))
    return bodies, requested_ranges


def test_drive_stream_is_lazy_and_closes_on_early_exit(studio, movie, monkeypatch):
    from app.storage.drive import GoogleDriveProvider

    bodies, requested_ranges = drive_download_transport(monkeypatch, movie)

    async def read(db):
        provider = GoogleDriveProvider(db)
        async with aclosing(
            provider.stream_range(
                user_id=movie.owner,
                storage_key=movie.key,
                drive_file_id="drive-file",
                start=1,
                end=12,
                chunk_size=3,
            )
        ) as chunks:
            assert await anext(chunks) == movie.data[1:4]
            assert bodies[0].sent == 3
            assert not bodies[0].closed
        assert bodies[0].closed
        assert requested_ranges == ["bytes=1-12"]

    with studio.sessions() as db:
        asyncio.run(read(db))


def test_drive_export_disconnect_releases_upstream_after_one_chunk(studio, movie, monkeypatch):
    from fastapi import Request
    from app.exports.router import download_export
    from app.models import Asset, Export

    chunk_size = 2 * 1024 * 1024
    movie.data = b"x" * (chunk_size * 2 + 1)
    bodies, _ = drive_download_transport(monkeypatch, movie, chunk_size=chunk_size)

    async def disconnect(message):
        if message["type"] == "http.response.body":
            assert len(message["body"]) == chunk_size
            assert bodies[0].sent == chunk_size
            raise BrokenPipeError("browser disconnected")

    async def download(db, export_id):
        request = Request({"type": "http", "headers": []})
        response = await download_export(export_id, request, db=db)
        with pytest.raises(BrokenPipeError):
            await response.stream_response(disconnect)
        assert bodies[0].closed

    with studio.sessions() as db:
        asset = db.get(Asset, movie.asset_id)
        asset.storage_provider, asset.drive_file_id = "drive", "drive-file"
        asset.size_bytes = len(movie.data)
        db.commit()
        export_id = db.scalar(select(Export.id).where(Export.master_asset_id == asset.id))
        asyncio.run(download(db, export_id))


@pytest.mark.parametrize("endpoint", ["media", "export"])
def test_drive_routes_stream_media_using_asset_provider(studio, movie, monkeypatch, endpoint):
    from app.core.crypto import sign_media_token
    from app.models import Asset
    from app.storage.drive import GoogleDriveProvider

    with studio.sessions() as db:
        asset = db.get(Asset, movie.asset_id)
        asset.drive_file_id = "drive-file"
        asset.storage_provider = "drive"
        db.commit()
    bodies, requested_ranges = drive_download_transport(monkeypatch, movie)
    monkeypatch.setattr(GoogleDriveProvider, "read_range", AsyncMock(side_effect=AssertionError("buffered read")))
    path = movie.url if endpoint == "export" else f"/v1/media/{movie.asset_id}?t={sign_media_token(movie.asset_id)}"
    partial = studio.client.get(path, headers={"Range": "bytes=4-7"})
    assert partial.status_code == 206
    assert partial.content == movie.data[4:8]
    assert partial.headers["content-range"] == f"bytes 4-7/{len(movie.data)}"
    assert partial.headers["content-length"] == "4"
    assert partial.headers["accept-ranges"] == "bytes"
    assert requested_ranges == ["bytes=4-7"]
    full = studio.client.get(path)
    assert full.status_code == 200
    assert full.content == movie.data
    assert full.headers["content-length"] == str(len(movie.data))
    assert all(body.closed for body in bodies)
    invalid = studio.client.get(path, headers={"Range": "bytes=99-"})
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == f"bytes */{len(movie.data)}"
    assert len(bodies) == 2


def test_snapshot_history_and_recovery_in_empty_database(studio, tmp_path):
    from app.models import EventOutbox, Scene, TimelineRevision
    from app.storage.local import vault_path
    from app.storage.snapshots import rebuild_project_from_vault, snapshot_pending_revisions
    from app.timeline.schemas import EditNarrationCmd
    from app.timeline.service import apply_timeline_commands, get_latest_timeline

    with studio.sessions() as db:
        original = new_project(db)
        project_id, owner_id = original.id, original.owner_id
        initial = get_latest_timeline(db, project_id)
        current = apply_timeline_commands(
            db,
            project_id,
            1,
            [
                EditNarrationCmd(
                    type="edit_narration",
                    scene_id=initial.tracks.visual[0].id,
                    narration="The current turns the rotor.",
                ),
            ],
        )
        assert asyncio.run(snapshot_pending_revisions(db, limit=1)) == 1
        head = json.loads(vault_path(f"{project_id}/project.json").read_text())
        assert head["document"] == current.model_dump(mode="json")
        assert asyncio.run(snapshot_pending_revisions(db, limit=1)) == 1
        assert asyncio.run(snapshot_pending_revisions(db)) == 0
        for revision in (initial, current):
            key = f"{project_id}/history/v{revision.revision}.json"
            stored = db.query(TimelineRevision).filter_by(project_id=project_id, revision=revision.revision).one()
            assert stored.drive_snapshot_id == key
            assert json.loads(vault_path(key).read_text()) == revision.model_dump(mode="json")
        assert vault_path(f"{project_id}/project.json").is_file()
        assert db.query(EventOutbox).filter_by(event_type="vault.snapshot_saved", project_id=project_id).count() == 2

    with database(tmp_path / "recovered.sqlite") as recovered:
        with recovered() as db:
            project = asyncio.run(rebuild_project_from_vault(db, project_id=project_id, user_id=owner_id))
            for field in (
                "id",
                "owner_id",
                "title",
                "topic",
                "status",
                "aspect",
                "duration_preset",
                "target_res",
                "qa_policy",
                "brain_mode",
                "current_revision",
            ):
                assert getattr(project, field) == getattr(original, field)
            asyncio.run(rebuild_project_from_vault(db, project_id=project_id, user_id=owner_id))
        with recovered() as db:
            assert get_latest_timeline(db, project_id) == current
            assert db.query(TimelineRevision).filter_by(project_id=project_id, revision=current.revision).count() == 1
            scenes = db.scalars(select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)).all()
            assert [(s.id, s.start_ms, s.end_ms, s.status, s.kind) for s in scenes] == [
                (s.id, s.start_ms, s.end_ms, s.status, s.kind) for s in current.tracks.visual
            ]


def test_snapshot_upload_allows_concurrent_edits_and_serializes_publication(studio, monkeypatch):
    from sqlalchemy import text
    from app.models import TimelineRevision
    from app.storage.drive import LocalVaultProvider
    from app.storage.local import vault_path
    from app.storage.snapshots import snapshot_pending_revisions
    from app.timeline.schemas import EditNarrationCmd
    from app.timeline.service import apply_timeline_commands, get_latest_timeline

    with studio.sessions() as db:
        project = new_project(db)
        first = get_latest_timeline(db, project.id)
        write = LocalVaultProvider.write_json
        edited = False

        async def edit_during_upload(provider, *args, **kwargs):
            nonlocal edited
            if not edited:
                edited = True
                with studio.sessions() as other:
                    other.execute(text("PRAGMA busy_timeout=100"))
                    apply_timeline_commands(
                        other,
                        project.id,
                        first.revision,
                        [
                            EditNarrationCmd(
                                type="edit_narration",
                                scene_id=first.tracks.visual[0].id,
                                narration="The narration changed while the backup was in progress.",
                            ),
                        ],
                    )
                    assert await snapshot_pending_revisions(other) == 0
            return await write(provider, *args, **kwargs)

        monkeypatch.setattr(LocalVaultProvider, "write_json", edit_during_upload)
        asyncio.run(snapshot_pending_revisions(db))
        asyncio.run(snapshot_pending_revisions(db))
        assert asyncio.run(snapshot_pending_revisions(db)) == 0
        assert edited
        assert db.query(TimelineRevision).filter(TimelineRevision.drive_snapshot_id.is_not(None)).count() == 2
        head = json.loads(vault_path(f"{project.id}/project.json").read_text())
        assert head["document"]["revision"] == 2


def test_partial_snapshot_retries_without_duplicate_events(studio):
    from app.models import EventOutbox, TimelineRevision
    from app.storage.local import vault_path
    from app.storage.snapshots import snapshot_pending_revisions

    with studio.sessions() as db:
        project = new_project(db)
        # Force replacement of the head file to fail after history has been saved.
        head = vault_path(f"{project.id}/project.json")
        head.mkdir(parents=True)
        assert asyncio.run(snapshot_pending_revisions(db)) == 0
        assert db.scalar(select(TimelineRevision)).drive_snapshot_id is None
        assert db.query(EventOutbox).filter_by(event_type="vault.snapshot_saved").count() == 0
        head.rmdir()
        assert asyncio.run(snapshot_pending_revisions(db)) == 1
        assert head.is_file()
        assert db.query(EventOutbox).filter_by(event_type="vault.snapshot_saved").count() == 1


def test_drive_snapshots_authorized_create_overwrite_and_recovery(studio, tmp_path, monkeypatch):
    from app.identity.service import ensure_default_owner
    from app.models import OAuthCredential, TimelineRevision
    from app.storage import drive
    from app.storage.snapshots import rebuild_project_from_vault, snapshot_pending_revisions
    from app.timeline.schemas import EditNarrationCmd
    from app.timeline.service import apply_timeline_commands, get_latest_timeline

    files = {}
    contents = {}
    uploads = {}
    updates = []

    def respond(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        path = request.url.path
        if request.method == "GET" and path == "/drive/v3/files":
            query = request.url.params["q"]
            name = re.search(r"name='([^']+)'", query)[1]
            parent = re.search(r"'([^']+)' in parents", query)
            matches = [
                fid
                for fid, meta in files.items()
                if meta["name"] == name and (not parent or parent[1] in meta["parents"])
            ]
            return httpx.Response(200, json={"files": [{"id": fid} for fid in matches]})
        if request.method == "POST" and path == "/drive/v3/files":
            folder_id = f"folder-{len(files)}"
            files[folder_id] = json.loads(request.content)
            return httpx.Response(200, json={"id": folder_id})
        if path.startswith("/upload/drive/v3/files"):
            assert request.url.params["uploadType"] == "resumable"
            meta = json.loads(request.content)
            if request.method == "PATCH":
                fid = path.rsplit("/", 1)[1]
                updates.append(fid)
                files[fid].update(meta)
            else:
                assert request.method == "POST"
                fid = f"file-{len(files)}"
                files[fid] = meta
            uploads[fid] = int(request.headers["x-upload-content-length"])
            return httpx.Response(200, headers={"Location": f"https://upload.test/session/{fid}"})
        fid = path.rsplit("/", 1)[1]
        if request.method == "PUT":
            length = uploads.pop(fid)
            assert request.headers["Content-Range"] == f"bytes 0-{length - 1}/{length}"
            assert len(request.content) == length
            contents[fid] = json.loads(request.content)
            return httpx.Response(200, json={"id": fid})
        assert request.method == "GET" and request.url.params["alt"] == "media"
        return httpx.Response(200, json=contents[fid])

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        drive.httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(respond), **kw)
    )
    monkeypatch.setattr(drive, "get_valid_google_access_token", AsyncMock(return_value="test-token"))
    studio.cfg.storage_provider = "drive"

    def connect(db):
        owner = ensure_default_owner(db)
        db.add(OAuthCredential(user_id=owner.id, provider="google", scopes="drive.file", encrypted_refresh_token="x"))
        db.commit()
        return owner.id

    with studio.sessions() as db:
        owner_id = connect(db)
        project = new_project(db)
        project_id = project.id
        assert asyncio.run(snapshot_pending_revisions(db)) == 1
        first = get_latest_timeline(db, project_id)
        current = apply_timeline_commands(
            db,
            project_id,
            1,
            [
                EditNarrationCmd(
                    type="edit_narration",
                    scene_id=first.tracks.visual[0].id,
                    narration="Turbines extract energy from moving water.",
                ),
            ],
        )
        assert asyncio.run(snapshot_pending_revisions(db)) == 1
        (head_id,) = [fid for fid, meta in files.items() if meta["name"] == "project.json"]
        assert updates == [head_id]
        for rev in db.scalars(select(TimelineRevision)).all():
            fid = rev.drive_snapshot_id
            assert files[fid]["name"] == f"v{rev.revision}.json"
            assert contents[fid] == rev.document
            history = files[files[fid]["parents"][0]]
            folder = files[history["parents"][0]]
            assert history["name"] == "history"
            assert folder["name"].endswith(f"_{project_id}")
            assert files[folder["parents"][0]]["name"] == "KinetiqStudio"
    with database(tmp_path / "drive-recovered.sqlite") as recovered, recovered() as db:
        assert connect(db) == owner_id
        restored = asyncio.run(rebuild_project_from_vault(db, project_id=project_id, user_id=owner_id))
        assert restored.id == project_id
        assert get_latest_timeline(db, project_id) == current


def test_disconnected_drive_skips_pending_revisions(studio, monkeypatch):
    from app.models import TimelineRevision
    from app.storage import drive
    from app.storage.snapshots import snapshot_pending_revisions

    with studio.sessions() as db:
        new_project(db)
        studio.cfg.storage_provider = "drive"
        monkeypatch.setattr(drive.httpx, "AsyncClient", Mock(side_effect=AssertionError("unexpected HTTP call")))
        assert asyncio.run(snapshot_pending_revisions(db)) == 0
        assert db.scalar(select(TimelineRevision)).drive_snapshot_id is None


@pytest.mark.parametrize("scenario", ["enabled", "disabled", "existing", "missing_clip", "still_generating"])
def test_ready_transition_and_draft_export(studio, scenario):
    from app.models import Export, Job
    from app.orchestrator import run_orchestrator_tick
    from app.timeline.service import get_latest_timeline, save_timeline_document

    studio.cfg.auto_export_draft_on_ready = scenario != "disabled"
    with studio.sessions() as db:
        project = new_project(db)
        doc = get_latest_timeline(db, project.id)
        for item in doc.tracks.visual:
            item.kind = "diagram"
            item.status = "ready"
        # An invalid final scene catches leaked TTS/align jobs for earlier valid scenes.
        if scenario == "missing_clip":
            doc.tracks.visual[-1].kind = "ai_video"
            doc.tracks.visual[-1].assets = None
        if scenario == "still_generating":
            doc.tracks.visual[-1].status = "generating_clip"
        doc = save_timeline_document(db, project, 1, doc)
        project.status = "generating"
        if scenario == "existing":
            db.add(Export(project_id=project.id, revision=doc.revision, resolution="4k"))
        db.commit()
        first = run_orchestrator_tick(db)
        assert first["completed_projects"] == int(scenario != "still_generating")
        assert run_orchestrator_tick(db)["completed_projects"] == 0
    with studio.sessions() as db:
        from app.models import Project

        assert db.get(Project, project.id).status == ("generating" if scenario == "still_generating" else "ready")
        exports = db.scalars(select(Export)).all()
        jobs = db.scalars(select(Job)).all()
        if scenario == "enabled":
            assert [(e.project_id, e.revision, e.resolution) for e in exports] == [(project.id, doc.revision, "1080p")]
            (render,) = [j for j in jobs if j.job_type == "render"]
            assert render.payload["export_id"] == exports[0].id
            assert {j.job_type for j in jobs if j.id in render.depends_on} == {"tts", "align"}
        else:
            assert [e.resolution for e in exports] == (["4k"] if scenario == "existing" else [])
            assert not jobs


def test_health_metrics_contract_and_vps_auth(studio):
    from app.identity.cookies import SESSION, seal
    from app.models import Asset, Job, OAuthCredential, TimelineRevision, Worker

    url = "/v1/health/metrics"
    expected = {
        "status": "ok",
        "env": "phase0_local",
        "storage_provider": "local",
        "redis_connected": False,
        "jobs": {"queued": 0, "leased": 0, "running": 0, "succeeded": 0, "failed": 0, "dead": 0},
        "workers": {"online": 0, "busy": 0, "offline": 0, "total_cu_month": 0.0},
        "vault": {"synced": 0, "missing": 0, "mismatch": 0, "pending": 0},
        "snapshots": {"total_revisions": 0, "backed_up_revisions": 0},
    }
    assert studio.client.get(url).json() == expected
    with studio.sessions() as db:
        project = new_project(db)
        db.scalar(select(TimelineRevision)).drive_snapshot_id = "backed-up-file"
        for state in ("queued", "queued", "running", "dead"):
            db.add(
                Job(
                    project_id=project.id,
                    job_type="tts",
                    capability="tts",
                    status=state,
                    idempotency_key=f"{state}-{len(db.new)}",
                )
            )
        for name, cu in (("online", 1.25), ("busy", 2.5), ("offline", 4)):
            db.add(Worker(name=name, token_hash=name, status=name, cu_used_month=cu))
        for state in ("synced", "synced", "pending", "missing"):
            db.add(Asset(project_id=project.id, kind="master", storage_key="movie", sync_state=state))
        cred = OAuthCredential(
            user_id=project.owner_id, provider="google", scopes="drive.file", encrypted_refresh_token="x"
        )
        db.add(cred)
        db.commit()
    expected["jobs"].update(queued=2, running=1, dead=1)
    expected["workers"].update(online=1, busy=1, offline=1, total_cu_month=7.75)
    expected["vault"].update(synced=2, pending=1, missing=1)
    expected["snapshots"].update(total_revisions=1, backed_up_revisions=1)
    assert studio.client.get(url).json() == expected
    studio.cfg.app_env = "phase1_vps"
    assert studio.client.get(url).status_code == 401
    studio.client.cookies.set(SESSION, seal(SESSION, f"{cred.id}.csrf", 60), domain="studio.test", path="/")
    expected["env"] = "phase1_vps"
    assert studio.client.get(url).json() == expected


@pytest.mark.parametrize("reachable", [False, True])
def test_metrics_redis_ping(studio, monkeypatch, reachable):
    from redis.asyncio import Redis

    probe = AsyncMock()
    probe.__aenter__.return_value = probe
    if reachable:
        probe.ping.return_value = True
    else:
        probe.ping.side_effect = RedisConnectionError("offline")
    studio.cfg.redis_url = "redis://localhost:6379"
    create = Mock(return_value=probe)
    monkeypatch.setattr(Redis, "from_url", create)
    response = studio.client.get("/v1/health/metrics")
    assert response.status_code == 200
    assert response.json()["redis_connected"] is reachable
    probe.ping.assert_awaited_once()
