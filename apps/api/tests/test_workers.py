"""Worker contracts exercised with local files, a temporary vault, and an isolated DB."""

import hashlib
import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.timeline.schemas import VisualSceneItem
from app.workers.workflows import build_comfy_graph


@pytest.fixture
def worker_modules(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "workers/agent-core"))


def test_media_wav_and_alignment_preserve_sentence_pause(worker_modules, tmp_path):
    from workers.media.align_whisperx import align_words
    from workers.media.tts_kokoro import synthesize

    script = "One two. Three!"
    wav = tmp_path / "scene.wav"
    duration_ms = synthesize(script, wav, mock=True)
    words = align_words(script, wav, mock=True)
    assert [w["word"] for w in words] == script.split()
    assert words[-1]["end_ms"] == duration_ms
    assert words[2]["start_ms"] - words[1]["end_ms"] == 250
    with wave.open(str(wav)) as audio:
        rate = audio.getframerate()
        audio.setpos(words[1]["end_ms"] * rate // 1000)
        assert audio.readframes(rate // 4) == bytes(rate // 4 * audio.getsampwidth())
    duplicate = tmp_path / "second.wav"
    synthesize(script, duplicate, mock=True)
    assert wav.read_bytes() == duplicate.read_bytes()


@pytest.mark.skipif(not all(shutil.which(cmd) for cmd in ("ffmpeg", "ffprobe")), reason="FFmpeg required")
def test_proxy_is_540p_with_frequent_keyframes(worker_modules, tmp_path):
    from workers.media.runner import create_proxy

    master = tmp_path / "master.mp4"
    proxy = tmp_path / "proxy.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=320x180:r=30:d=1.5", str(master)
    ], check=True)
    assert create_proxy(master, proxy) == (960, 540)
    frames = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=key_frame", "-of", "json", str(proxy),
    ]))["frames"]
    keys = [i for i, frame in enumerate(frames) if frame["key_frame"]]
    assert keys[0] == 0
    assert max(b - a for a, b in zip(keys, [*keys[1:], len(frames)])) <= 15


def test_render_uses_vertical_4k_and_offsets_scene_captions(worker_modules, tmp_path):
    from workers.render.runner import prepare_document, write_captions

    doc = {
        "format": {"aspect": "9:16"},
        "tracks": {"visual": [{"kind": "ai_video", "id": "v", "start_ms": 3000, "end_ms": 4000,
                               "assets": {"master": "hd", "proxy": "preview", "upscaled_4k": "uhd"}}],
                   "voice": [{"scene_id": "v", "start_ms": 3100, "end_ms": 3900}], "captions": {}},
    }
    render = prepare_document(doc, "4k")
    assert (render["format"]["width"], render["format"]["height"]) == (2160, 3840)
    refs = render["tracks"]["visual"][0]["assets"]
    assert refs["master"] == "uhd" and not refs.get("proxy")
    assert doc["tracks"]["visual"][0]["assets"]["proxy"] == "preview"
    words = tmp_path / "words.json"
    words.write_text(json.dumps({"words": [{"word": "Hello", "start_ms": 100, "end_ms": 1100}]}))
    srt = tmp_path / "captions.srt"
    write_captions(render["tracks"], {"v": "words"}, {"words": words}, srt)
    assert srt.read_text().strip() == "1\n00:00:03,200 --> 00:00:03,900\nHello"


@pytest.mark.parametrize("aspect,size", [("9:16", (1080, 1920)), ("16:9", (1920, 1080))])
def test_seedvr2_4k_l4_fallback_preserves_scene_and_reduces_memory(aspect, size):
    scene = VisualSceneItem(id="s", kind="ai_video", narration="A test.", start_ms=0, end_ms=2000, seed=0)

    def graph(model, vram):
        nodes = build_comfy_graph(
            job_type="upscale_seedvr2", scene=scene, aspect=aspect, target_res="4k", style_look="",
            negative_prompt="", input_filename="master.mp4", gpu_model=model, vram_gb=vram,
        )
        assert "__" not in json.dumps(nodes)
        return {node["class_type"]: node["inputs"] for node in nodes.values()}

    l4 = graph("NVIDIA L4", 24)
    a100 = graph("NVIDIA A100", 80)
    reader = l4["VHS_LoadVideo"]
    assert (reader["custom_width"], reader["custom_height"]) == size
    assert reader["video"] == "master.mp4"
    upscale = l4["SeedVR2VideoUpscaler"]
    assert (upscale["resolution"], upscale["max_resolution"], upscale["seed"]) == (2160, 3840, 0)
    assert upscale["batch_size"] < a100["SeedVR2VideoUpscaler"]["batch_size"]
    assert "fp8" in l4["SeedVR2LoadDiTModel"]["model"]
    vae = l4["SeedVR2LoadVAEModel"]
    assert vae["decode_tiled"] and vae["decode_tile_size"] < a100["SeedVR2LoadVAEModel"]["decode_tile_size"]


@pytest.fixture
def export_api(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.core.db import Base, SessionLocal, get_db
    from app.core.errors import register_error_handlers
    from app.exports.router import router as exports
    from app.identity.service import ensure_default_owner
    from app.models import Asset, Project, TimelineRevision
    from app.storage.router import router as storage
    from app.timeline.schemas import TimelineDocument
    from app.workers.router import router as workers

    monkeypatch.setattr(get_settings(), "storage_provider", "local")
    monkeypatch.setattr(get_settings(), "local_vault_dir", str(tmp_path / "vault"))
    monkeypatch.setattr(get_settings(), "public_api_url", "http://testserver")
    engine = create_engine(f"sqlite:///{tmp_path / 'workers.db'}", connect_args={"check_same_thread": False})
    # Bind both request dependencies and internal API sessions to the temporary DB.
    sessions = SessionLocal
    monkeypatch.setitem(sessions.kw, "bind", engine)
    Base.metadata.create_all(engine)

    with sessions() as db:
        owner = ensure_default_owner(db)
        project = Project(id="project", owner_id=owner.id, title="A test", topic="Timelines")
        db.add(project)
        db.flush()
        doc = TimelineDocument.model_validate({
            "project_id": project.id,
            "format": {"aspect": "16:9", "width": 1920, "height": 1080},
            "style_bible": {"palette": [], "look": "", "negative": "", "safe_zone": "16x9_standard"},
            "tracks": {"visual": [{"id": "s", "kind": "ai_video", "start_ms": 0, "end_ms": 2000,
                                   "narration": "Original.", "assets": {"master": "hd", "proxy": "preview"}}]},
        })
        db.add(TimelineRevision(project_id=project.id, revision=1, document=doc.model_dump()))
        db.add(Asset(id="hd", project_id=project.id, scene_id="s", kind="master",
                     storage_key="project/clips/master/hd.mp4", sync_state="synced"))
        db.commit()

    def session():
        with sessions() as db:
            yield db

    app = FastAPI()
    register_error_handlers(app)
    for router in (exports, workers, storage):
        app.include_router(router)
    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        yield client, sessions
    engine.dispose()


def test_export_claim_and_atomic_completion(export_api):
    from app.models import Asset, EventOutbox, Project, TimelineRevision

    api, sessions = export_api

    def register(profile):
        data = api.post("/v1/workers/tokens", json={"name": profile, "provider": "local", "profile": profile}).json()
        return data["worker_id"], {"Authorization": f"Bearer {data['worker_token']}"}

    def claim(worker, cap):
        wid, headers = worker
        res = api.post("/v1/workers/jobs/claim", headers=headers, json={"worker_id": wid, "capabilities": [cap]})
        return res

    def upload(job, headers, name, kind, mime, data):
        url = f"/v1/workers/jobs/{job['id']}/upload-session"
        body = {"filename": name, "asset_kind": kind, "content_type": mime, "total_bytes": len(data)}
        res = api.post(url, headers=headers, json=body)
        assert res.status_code == 200, res.text
        session = res.json()
        assert session["chunk_size_bytes"] == 8 * 1024 * 1024
        put = api.put(session["session_uri"], content=data, headers={"Content-Range": f"bytes 0-{len(data)-1}/{len(data)}"})
        assert put.status_code == 201, put.text
        return {"asset_kind": kind, "content_type": mime, "storage_key": session["storage_key"],
                "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def complete(job, headers, *artifacts):
        return api.post(f"/v1/workers/jobs/{job['id']}/complete", headers=headers, json={"artifacts": artifacts})

    media, gpu, render = register("media"), register("upscale_4k"), register("render")
    exp = api.post("/v1/projects/project/exports", json={"resolution": "4k"}).json()
    path = f"/v1/exports/{exp['id']}"
    assert exp["status"] == "queued" and claim(render, "render").status_code == 204
    # A later edit deletes the original scene while the queued export still needs it.
    with sessions() as db:
        old = db.query(TimelineRevision).one().document
        db.add(TimelineRevision(project_id="project", revision=2,
                                document={**old, "revision": 2, "tracks": {**old["tracks"], "visual": []}}))
        db.get(Project, "project").current_revision = 2
        db.commit()

    tts = claim(media, "tts").json()
    assert tts["payload"]["narration"] == "Original."
    audio = upload(tts, media[1], "scene.wav", "audio", "audio/wav", b"test audio")
    assert complete(tts, media[1], audio).status_code == 200
    align = claim(media, "align").json()
    assert api.get(align["input_download_url"]).content == b"test audio"
    timings = upload(align, media[1], "words.json", "words", "application/json", b'{"words":[]}')
    assert complete(align, media[1], timings).status_code == 200
    upscale = claim(gpu, "upscale_seedvr2").json()
    assert "/v1/media/hd?" in upscale["input_download_url"]
    uhd = upload(upscale, gpu[1], "uhd.mp4", "upscaled_4k", "video/mp4", b"upscaled")
    assert complete(upscale, gpu[1], uhd).status_code == 200

    job = claim(render, "render").json()
    payload, urls = job["payload"], job["payload"]["media_urls"]
    tracks = payload["document"]["tracks"]
    assert payload["document"]["revision"] == 1 and len(tracks["visual"]) == 1
    assert "hd" not in urls and "preview" not in urls
    assert api.get(urls[tracks["visual"][0]["assets"]["master"]]).content == b"upscaled"
    assert api.get(urls[tracks["voice"][0]["asset"]]).content == b"test audio"
    assert api.get(urls[payload["scene_words"]["s"]]).json() == {"words": []}

    video = upload(job, render[1], "final_4k.mp4", "export", "video/mp4", b"test master")
    subtitles = upload(job, render[1], "captions.srt", "srt", "application/x-subrip", b"test srt")
    assert complete(job, render[1], video).status_code == 422
    assert complete(job, media[1], video, subtitles).status_code == 409
    assert complete(job, render[1], video, {**subtitles, "sha256": "0" * 64}).status_code == 422
    assert api.get(path).json()["download_url"] is None
    with sessions() as db:
        assert db.query(Asset).filter(Asset.kind.in_(["export", "srt"])).count() == 0

    assert complete(job, render[1], video, subtitles).status_code == 200
    assert complete(job, render[1], video, subtitles).json()["already_completed"]
    ready = api.get(path).json()
    assert ready["status"] == "ready"
    assert api.get(ready["download_url"]).content == b"test master"
    assert api.get(ready["srt_url"]).content == b"test srt"
    with sessions() as db:
        assert db.query(EventOutbox).filter_by(event_type="export.ready").count() == 1


def test_resumable_upload_recovers_final_drive_id(worker_modules, monkeypatch, tmp_path):
    import httpx

    from kinetiq_agent_core import resumable_upload

    size = 8 * 1024 * 1024
    path = tmp_path / "master.mp4"
    path.write_bytes(b"x" * size + b"tail")
    total = path.stat().st_size
    seen = []

    def drive(request):
        seen.append((request.headers["Content-Range"], request.headers["Authorization"], len(request.content)))
        match len(seen):
            case 1:
                return httpx.Response(308, headers={"Range": f"bytes=0-{size - 1}"})
            case 2:
                return httpx.Response(401)
            case 3:
                return httpx.Response(308, headers={"Range": f"bytes=0-{size - 1}"})
            case 4:
                raise httpx.ReadError("Lost final acknowledgement", request=request)
            case 5:
                return httpx.Response(200, json={"id": "completed-file"})
            case _:
                pytest.fail("Unexpected extra upload")

    session = httpx.Client(transport=httpx.MockTransport(drive))
    monkeypatch.setattr(resumable_upload.httpx, "Client", lambda **kwargs: session)
    monkeypatch.setattr(resumable_upload.time, "sleep", lambda _: None)
    result = resumable_upload.upload_file_resumable(
        path, session_uri="https://drive.example/upload", upload_headers={"Authorization": "Bearer old"},
        content_type="video/mp4", refresh_auth_fn=lambda: "Bearer new",
    )
    assert result["drive_file_id"] == "completed-file"
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert seen == [
        (f"bytes 0-{size - 1}/{total}", "Bearer old", size),
        (f"bytes {size}-{total - 1}/{total}", "Bearer old", 4),
        (f"bytes */{total}", "Bearer new", 0),
        (f"bytes {size}-{total - 1}/{total}", "Bearer new", 4),
        (f"bytes */{total}", "Bearer new", 0),
    ]


def test_bootstrap_cli_reuse_rotation_and_explicit_token(tmp_path):
    import os
    import sqlite3
    import stat
    import sys

    from app.core.crypto import hash_token

    file = tmp_path / "tokens/media.token"
    database = tmp_path / "bootstrap.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{database}", "MEDIA_WORKER_TOKEN": ""}
    cmd = [sys.executable, "-m", "app.workers.bootstrap", "--name", "local-media", "--profile", "media_local",
           "--capabilities", "tts,align,transcode", "--token-file", str(file), "--json"]

    def run(*extra):
        output = subprocess.check_output([*cmd, *extra], env=env, cwd=Path(__file__).resolve().parents[1], text=True)
        token = file.read_text().strip()
        assert token.startswith("wrk_") and token not in output
        if os.name == "posix":
            assert stat.S_IMODE(file.stat().st_mode) == 0o600
        with sqlite3.connect(database) as db:
            [(worker_id, digest, caps)] = db.execute("SELECT id, token_hash, capabilities FROM workers")
        assert digest == hash_token(token) and json.loads(caps) == ["tts", "align", "transcode"]
        metadata = json.loads(output)
        assert metadata["worker_id"] == worker_id
        return metadata, token

    first, original = run()
    mtime = file.stat().st_mtime_ns
    if os.name == "posix":
        file.chmod(0o644)
    second, reused = run()
    assert second["reused"] and reused == original and file.stat().st_mtime_ns == mtime
    file.write_text("wrk_mismatched")
    rotated, fresh = run()
    assert rotated["worker_id"] == first["worker_id"] and fresh not in {original, "wrk_mismatched"}
    file.unlink()
    assert run()[1] != fresh
    env["MEDIA_WORKER_TOKEN"] = "wrk_from_environment"
    assert run()[1] == env["MEDIA_WORKER_TOKEN"]
    assert run("--token", "wrk_explicit")[1] == "wrk_explicit"


@pytest.mark.parametrize("runner_name", ["media", "render"])
@pytest.mark.parametrize("source", ["cli", "env_file", "bearer"])
def test_worker_entrypoint_token_resolution(worker_modules, monkeypatch, tmp_path, runner_name, source):
    import importlib
    import sys
    from unittest.mock import Mock

    runner = importlib.import_module(f"workers.{runner_name}.runner")
    token_file = tmp_path / "worker.token"
    token_file.write_text("wrk_from_file\n")
    args = ["worker"]
    monkeypatch.setenv("API_BASE_URL", "http://api:8000")
    monkeypatch.delenv("WORKER_TOKEN", raising=False)
    monkeypatch.setenv("WORKER_TOKEN_FILE", str(tmp_path / "missing"))
    if source == "cli":
        args += ["--token-file", str(token_file)]
    elif source == "env_file":
        monkeypatch.setenv("WORKER_TOKEN_FILE", str(token_file))
    else:
        monkeypatch.setenv("WORKER_TOKEN", "wrk_env")
    monkeypatch.setattr(sys, "argv", args)
    client = Mock()
    client.return_value.claim_job.side_effect = KeyboardInterrupt
    monkeypatch.setattr(runner, "WorkerClient", client)
    with pytest.raises(KeyboardInterrupt):
        runner.main()
    client.assert_called_once_with("http://api:8000", "wrk_env" if source == "bearer" else "wrk_from_file")
    client.return_value.register.assert_called_once()


def test_split_clip_reaches_remotion_props(worker_modules, tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from workers.render import runner

    scenes = [
        {"id": "left", "kind": "ai_video", "assets": {"master": "shared"}, "start_ms": 0,
         "end_ms": 750, "source_start_ms": 0, "narration": ""},
        {"id": "right", "kind": "ai_video", "assets": {"master": "shared"}, "start_ms": 750,
         "end_ms": 1250, "source_start_ms": 1625, "narration": ""},
    ]
    document = {"format": {"aspect": "16:9", "fps": 24}, "tracks": {
        "visual": scenes, "voice": [], "captions": {}, "overlays": [], "music": {}}}
    job = {"id": "split", "payload": {"resolution": "720p", "document": document, "media_urls": {}}}
    monkeypatch.setattr(runner, "download_inputs", lambda *a: {"shared": tmp_path / "original.mp4"})
    monkeypatch.setattr(runner, "master_audio_video", lambda *a: None)
    captured = []

    def render(args, **kwargs):
        assert args[args.index("render") + 1:args.index("render") + 3] == [str(runner.ENTRY_POINT), "EduComposition"]
        path = next(arg.removeprefix("--props=") for arg in args if arg.startswith("--props="))
        captured.append(json.loads(Path(path).read_text()))

    monkeypatch.setattr(runner.subprocess, "run", render)
    runner.render_job(MagicMock(), job)
    [props] = captured
    assert props["document"]["tracks"]["visual"] == scenes
    assert props["mediaFiles"] == {"shared": "original.mp4"} and props["durationInFrames"] == 30


@pytest.mark.parametrize("free_status", [200, 503])
def test_comfy_oom_reports_structured_failure_and_keeps_polling(worker_modules, monkeypatch, tmp_path, free_status):
    import sys
    from unittest.mock import MagicMock, Mock

    import httpx
    from workers.gpu import runner

    job = {"id": "job", "job_type": "keyframe_qwen", "comfy_graph": {}}
    ws = Mock()
    ws.recv.return_value = json.dumps({"type": "execution_error", "data": {
        "prompt_id": "prompt", "exception_type": "torch.OutOfMemoryError",
        "exception_message": "Allocation on device", "node_type": "KSampler"}})
    monkeypatch.setitem(sys.modules, "websocket", Mock(create_connection=Mock(return_value=ws)))
    calls = []

    def serve(request):
        calls.append((request.url.path, json.loads(request.content)))
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "prompt"})
        return httpx.Response(free_status if request.url.path == "/free" else 200)

    session = httpx.Client(transport=httpx.MockTransport(serve))
    cleanup_session = httpx.Client(transport=httpx.MockTransport(serve))
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: session)
    monkeypatch.setattr(runner.httpx, "post", cleanup_session.post)
    monkeypatch.setenv("API_BASE_URL", "http://api")
    monkeypatch.setenv("WORKER_TOKEN", "wrk_gpu")
    monkeypatch.setattr(sys, "argv", ["worker", "--provider", "local"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "detect_gpu", lambda: ("L4", 24))
    worker = MagicMock()
    worker.claim_job.side_effect = [job, KeyboardInterrupt]
    monkeypatch.setattr(runner, "WorkerClient", lambda *a: worker)
    with pytest.raises(KeyboardInterrupt):
        runner.main()
    ws.close.assert_called_once()
    assert calls[1:] == [("/queue", {"delete": ["prompt"]}), ("/free", {"unload_models": True, "free_memory": True})]
    job_id, error = worker.fail_job.call_args.args
    meta = json.loads(error)
    assert job_id == "job" and meta["code"] == "cuda_oom" and meta["node_type"] == "KSampler"
    assert meta["cleanup"] == {"queue": True, "free": free_status == 200}
    assert worker.claim_job.call_count == 2
    cleanup_session.close()


def test_docker_worker_routes_signed_inputs_and_uploads(worker_modules, monkeypatch, tmp_path):
    import httpx
    from kinetiq_agent_core import worker_client

    public, internal = "http://localhost:8000", "http://api:8000"
    signed = "/v1/media/clip?t=expiry.signature"
    drive = "https://www.googleapis.com/upload/drive/v3/files?upload_id=id"
    uploads = iter([("local", public + "/v1/storage/uploads/id"), ("drive", drive)])

    def api(request):
        assert request.url.host == "api"
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json={"input_download_url": public + signed,
                                            "payload": {"media_urls": {"clip": public + signed, "remote": drive}}})
        provider, uri = next(uploads)
        return httpx.Response(200, json={"provider": provider, "storage_key": "key", "session_uri": uri})

    monkeypatch.setenv("PUBLIC_API_URL", public)
    transport = httpx.Client(transport=httpx.MockTransport(api))
    monkeypatch.setattr(worker_client.httpx, "Client", lambda **kwargs: transport)
    seen = []

    def upload(path, **kwargs):
        seen.append(kwargs["session_uri"])
        return {"size_bytes": path.stat().st_size}

    monkeypatch.setattr(worker_client, "upload_file_resumable", upload)
    wc = worker_client.WorkerClient(internal, "wrk_demo")
    wc.worker_id = "worker"
    job = wc.claim_job(["render"])
    assert job["input_download_url"] == internal + signed
    assert job["payload"]["media_urls"] == {"clip": internal + signed, "remote": drive}
    output = tmp_path / "asset"
    output.write_bytes(b"media")
    for _ in range(2):
        wc.upload_artifact("job", output, asset_kind="audio", content_type="audio/wav")
    assert seen == [internal + "/v1/storage/uploads/id", drive]
    transport.close()


@pytest.mark.skipif(not all(shutil.which(cmd) for cmd in ("ffmpeg", "ffprobe")), reason="FFmpeg required")
def test_renderer_masters_silent_timeline(worker_modules, tmp_path):
    from workers.render.runner import master_audio_video

    picture, output = tmp_path / "silent.mp4", tmp_path / "final.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=160x90:r=24:d=0.5",
                    "-c:v", "libx264", "-threads", "1", str(picture)], check=True)
    master_audio_video(picture, {"voice": [], "music": {}}, {}, 0.5, output)
    codecs = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
                                      "-of", "csv=p=0", str(output)], text=True)
    assert codecs.splitlines() == ["h264", "aac"]


def test_concurrent_first_boot_creates_one_identity(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import sessionmaker

    from app.core import db
    from app.core.crypto import hash_token
    from app.models import Worker
    from app.workers import bootstrap

    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(bootstrap, "SessionLocal", sessions)
    path = tmp_path / "worker.token"
    start = Barrier(4)

    def boot():
        start.wait(timeout=10)
        return bootstrap.bootstrap("worker", "media", ["tts"], path)

    with ThreadPoolExecutor(4) as pool:
        results = [f.result(timeout=15) for f in [pool.submit(boot) for _ in range(4)]]
    with sessions() as session:
        worker = session.query(Worker).one()
        assert worker.token_hash == hash_token(path.read_text().strip())
        assert {result["worker_id"] for result in results} == {worker.id}
    assert sum(result["reused"] for result in results) == 3
    engine.dispose()
