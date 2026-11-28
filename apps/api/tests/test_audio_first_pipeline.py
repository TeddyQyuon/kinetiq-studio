import hashlib
import io
import json
import wave

import pytest

from test_e2e_mock_pipeline import (
    TINY_PNG,
    _build_minimal_wav,
    _register_worker,
    _resumable_upload,
    e2e_studio as e2e_studio,
)


@pytest.fixture
def project(e2e_studio):
    api = e2e_studio.client
    response = api.post("/v1/projects", json={"title": "Sound waves", "topic": "How sound travels"})
    assert response.status_code == 200, response.text
    detail = response.json()
    root = f"/v1/projects/{detail['id']}"
    doc = detail["timeline"]
    doc["tracks"]["visual"] = [
        {"id": "opening", "kind": "image_motion", "narration": "First", "start_ms": 0, "end_ms": 4000},
        {"id": "example", "kind": "diagram", "narration": "Second", "start_ms": 4000, "end_ms": 7000,
         "status": "ready", "template": "StatCallout", "props": {"title": "Example"}},
    ]
    updated = api.put(root + "/timeline", json={"expected_revision": doc["revision"], "document": doc})
    assert updated.status_code == 200, updated.text
    return root


def claim(api, worker, capability):
    worker_id, headers = worker
    res = api.post("/v1/workers/jobs/claim", headers=headers,
                   json={"worker_id": worker_id, "capabilities": [capability]})
    assert res.status_code == 200, res.text
    return res.json()


def complete(api, worker, job, kind, filename, mime, data, **info):
    artifact = _resumable_upload(api, worker[1], job["id"], filename=filename, content_type=mime,
                                 asset_kind=kind, payload=data)
    return api.post(f"/v1/workers/jobs/{job['id']}/complete", headers=worker[1],
                    json={"artifacts": [{**artifact, **info}]})


def media_worker(api):
    return _register_worker(api, name="speech", profile="media", capabilities=["tts", "align"])


def complete_audio(api, worker, duration_ms):
    job = claim(api, worker, "tts")
    result = complete(api, worker, job, "audio", "voice.wav", "audio/wav", _build_minimal_wav(),
                      duration_ms=duration_ms)
    assert result.status_code == 200, result.text
    return job


def complete_words(api, worker, word, start=100, end=500):
    job = claim(api, worker, "align")
    assert api.get(job["input_download_url"]).content == _build_minimal_wav()
    data = json.dumps({"words": [{"word": word, "start_ms": start, "end_ms": end}]}).encode()
    result = complete(api, worker, job, "words", "words.json", "application/json", data)
    assert result.status_code == 200, result.text
    return job


def test_generation_and_export_share_immutable_speech_dependencies(project, e2e_studio):
    from app.models import Job, Scene

    api = e2e_studio.client
    started = api.post(project + "/generate").json()
    assert started["queued_jobs"] == 5
    assert api.post(project + "/generate").json()["job_ids"] == started["job_ids"]
    doc = api.get(project + "/timeline").json()
    assert [s["status"] for s in doc["tracks"]["visual"]] == ["generating_keyframe", "generating_audio"]
    with e2e_studio.sessions() as db:
        assert db.get(Scene, "example").status == "generating_audio"

    gpu = _register_worker(api, name="image", profile="ltx25_pro", capabilities=["keyframe_qwen"])
    keyframe = claim(api, gpu, "keyframe_qwen")
    assert complete(api, gpu, keyframe, "keyframe", "key.png", "image/png", TINY_PNG).is_success
    export = api.post(project + "/exports", json={"resolution": "1080p"})
    assert export.status_code == 200, export.text

    with e2e_studio.sessions() as db:
        jobs = db.query(Job).filter(Job.job_type.in_(["tts", "align"])).all()
        assert len(jobs) == 4
        assert set(db.query(Job).filter_by(job_type="render").one().depends_on) == {j.id for j in jobs}
        assert all(j.id in started["job_ids"] for j in jobs)
        for scene in doc["tracks"]["visual"]:
            tts, = [j for j in jobs if j.scene_id == scene["id"] and j.job_type == "tts"]
            align, = [j for j in jobs if j.scene_id == scene["id"] and j.job_type == "align"]
            assert tts.payload == {"narration": scene["narration"], "voice": doc["voice"]}
            assert align.payload == {**tts.payload, "tts_job_id": tts.id}
            assert align.depends_on == [tts.id]


def _if_match(api, project) -> dict[str, str]:
    rev = api.get(project + "/timeline").json()["revision"]
    return {"If-Match": str(rev)}


def test_out_of_order_speech_reflows_live_timeline_and_preserves_frozen_export(project, e2e_studio):
    from app.models import Job

    api = e2e_studio.client
    doc = api.get(project + "/timeline").json()
    doc["tracks"]["visual"][0].update(kind="diagram", status="ready")
    api.put(project + "/timeline", json={"expected_revision": doc["revision"], "document": doc}).raise_for_status()

    worker = media_worker(api)
    later = api.post(project + "/scenes/example/revoice", headers=_if_match(api, project)).json()
    assert complete_audio(api, worker, 3000)["id"] == later["tts_job_id"]
    assert complete_words(api, worker, "Second")["id"] == later["align_job_id"]
    assert api.get(project + "/timeline").json()["tracks"]["captions"]["words"][0]["start_ms"] == 4100

    earlier = api.post(project + "/scenes/opening/revoice", headers=_if_match(api, project)).json()
    assert api.post(project + "/generate").json()["job_ids"] == [earlier["tts_job_id"], earlier["align_job_id"]]
    frozen = api.post(project + "/exports", json={"resolution": "1080p"}).json()
    complete_audio(api, worker, 1000)
    mid = api.get(project).json()
    tracks = mid["timeline"]["tracks"]
    assert [(s["start_ms"], s["end_ms"], s["status"]) for s in tracks["visual"]] == [
        (0, 1500, "ready"), (1500, 4500, "ready"),
    ]
    clips = {clip["scene_id"]: clip for clip in tracks["voice"]}
    assert [(clips[s]["start_ms"], clips[s]["end_ms"]) for s in ("opening", "example")] == [(0, 1000), (1500, 4500)]
    assert api.get(mid["media_urls"][clips["opening"]["asset"]]).content == _build_minimal_wav()
    assert tracks["captions"]["words"] == [{"word": "Second", "start_ms": 1600, "end_ms": 2000}]

    # Export after TTS and before alignment must reuse the alignment tied to that TTS job.
    api.post(project + "/exports", json={"resolution": "1080p"}).raise_for_status()
    with e2e_studio.sessions() as db:
        render = db.query(Job).filter_by(job_type="render").order_by(Job.created_at.desc()).first()
        assert render.depends_on == [earlier["align_job_id"]]
    aligned = complete_words(api, worker, "First", 50, 350)
    assert aligned["id"] == earlier["align_job_id"]
    live = api.get(project + "/timeline").json()
    assert live["tracks"]["captions"]["words"] == [
        {"word": "First", "start_ms": 50, "end_ms": 350},
        {"word": "Second", "start_ms": 1600, "end_ms": 2000},
    ]
    retry = api.post(f"/v1/workers/jobs/{aligned['id']}/complete", headers=worker[1], json={"artifacts": []})
    assert retry.json()["already_completed"]
    assert api.get(project + "/timeline").json()["revision"] == live["revision"]
    payload = claim(api, worker, "render")["payload"]
    assert payload["revision"] == frozen["revision"]
    assert [w["start_ms"] for w in payload["document"]["tracks"]["captions"]["words"]] == [50, 4100]

    assert api.post(project + "/generate").json()["job_ids"] == []
    api.post(project + "/exports", json={"resolution": "1080p"}).raise_for_status()
    with e2e_studio.sessions() as db:
        assert db.query(Job).filter_by(job_type="render").order_by(Job.created_at.desc()).first().depends_on == []
    new_take = api.post(project + "/scenes/example/revoice", headers=_if_match(api, project)).json()
    assert new_take["ok"] and new_take["tts_job_id"] != later["tts_job_id"]
    assert api.post(project + "/scenes/example/revoice", headers=_if_match(api, project)).json() == new_take


def test_commands_and_accepted_copilot_dispatch_once(project, e2e_studio):
    api = e2e_studio.client
    worker = media_worker(api)
    api.post(project + "/scenes/example/revoice", headers=_if_match(api, project)).raise_for_status()
    complete_audio(api, worker, 3100)
    stale_align = claim(api, worker, "align")
    doc = api.get(project + "/timeline").json()
    assert doc["tracks"]["visual"][1]["end_ms"] == 7000  # within the 250 ms tolerance

    edit = api.post(project + "/timeline/commands", json={
        "expected_revision": doc["revision"],
        "commands": [{"type": "edit_narration", "scene_id": "example", "narration": "Revised"}],
    })
    assert edit.is_success, edit.text
    assert edit.json()["tracks"]["voice"][0]["asset"] is None
    revision = edit.json()["revision"]
    stale = complete(api, worker, stale_align, "words", "old.json", "application/json",
                     b'[{"word":"Second","start_ms":0,"end_ms":100}]')
    assert stale.is_success and api.get(project + "/timeline").json()["revision"] == revision
    assert api.get(project + "/timeline").json()["tracks"]["captions"]["words"] in (None, [])
    new = complete_audio(api, worker, 0)
    assert new["payload"]["narration"] == "Revised"
    assert api.get(project + "/timeline").json()["tracks"]["voice"][0]["end_ms"] == 7000
    complete_words(api, worker, "Revised")
    doc = api.get(project + "/timeline").json()
    assert len(api.get(project + "/jobs").json()) == 4

    proposal = api.post(project + "/copilot", json={"message": "Regenerate the opening", "selected_scene_id": "opening"})
    url = project + f"/copilot/{proposal.json()['proposal_id']}/accept"
    assert api.post(url, json={"expected_revision": 1}).status_code == 409
    assert len(api.get(project + "/jobs").json()) == 4
    accepted = api.post(url, json={"expected_revision": doc["revision"]})
    assert accepted.is_success, accepted.text
    assert api.post(url, json={"expected_revision": doc["revision"]}).is_success
    assert [j["job_type"] for j in api.get(project + "/jobs").json()].count("keyframe_qwen") == 1
    assert api.post(project + "/scenes/opening/regenerate", json={}).is_success
    assert [j["job_type"] for j in api.get(project + "/jobs").json()].count("keyframe_qwen") == 2

    doc = api.get(project + "/timeline").json()
    mixed = api.post(project + "/timeline/commands", json={"expected_revision": doc["revision"],
                     "commands": [{"type": "mix_audio", "voice_speed": 1.2}]})
    assert mixed.is_success and mixed.json()["tracks"]["voice"][0]["asset"] is None
    assert len(api.get(project + "/jobs").json()) == 6


def test_invalid_alignment_rolls_back_then_valid_retry_succeeds(project, e2e_studio):
    from app.models import Asset

    api = e2e_studio.client
    api.post(project + "/scenes/example/revoice", headers=_if_match(api, project)).raise_for_status()
    worker = media_worker(api)
    complete_audio(api, worker, 3000)
    align = claim(api, worker, "align")
    revision = api.get(project + "/timeline").json()["revision"]
    for data in (b"!" * (1024 * 1024 + 1), b"not json", b'[{"word":"hi","start_ms":5,"end_ms":1}]'):
        invalid = complete(api, worker, align, "words", "words.json", "application/json", data)
        assert invalid.status_code == 422, invalid.text
        assert api.get(project + "/timeline").json()["revision"] == revision
    with e2e_studio.sessions() as db:
        assert db.query(Asset).filter_by(kind="words").count() == 0
    assert complete(api, worker, align, "words", "words.json", "application/json",
                    b'[{"word":"Hi","start_ms":0,"end_ms":1}]').is_success


@pytest.mark.parametrize("preset", ["ambient_pulse", "warm_pad", "documentary_minimal"])
def test_music_preset_upload_and_delete(project, e2e_studio, preset):
    from app.models import Asset

    api = e2e_studio.client
    result = api.post(
        project + "/music",
        headers=_if_match(api, project),
        json={"preset": preset, "volume": 0.18, "duck_db": -19},
    )
    assert result.is_success, result.text
    data = result.json()
    assert set(data) == {"asset_id", "media_url", "timeline"}
    assert data["timeline"]["tracks"]["music"] == {
        "asset": data["asset_id"], "mood": preset, "volume": 0.18, "duck_db": -19,
    }
    audio = api.get(data["media_url"]).content
    with wave.open(io.BytesIO(audio)) as wav:
        assert wav.getnframes() == 15 * wav.getframerate()
        assert any(wav.readframes(1024))
    with e2e_studio.sessions() as db:
        stored = db.get(Asset, data["asset_id"])
        assert (stored.kind, stored.sync_state, stored.size_bytes) == ("music", "synced", len(audio))
        assert stored.sha256 == hashlib.sha256(audio).hexdigest()
        assert stored.storage_key == f"{stored.project_id}/music/{stored.id}.wav"
    assert api.delete(project + "/music", headers={"If-Match": "1"}).status_code == 409
    removed = api.delete(project + "/music", headers=_if_match(api, project))
    assert removed.json()["tracks"]["music"]["asset"] is None
    assert api.get(data["media_url"]).content == audio  # frozen exports retain the original upload


def test_uploaded_music_and_independent_project_duplicate(project, e2e_studio):
    from app.models import Scene, TimelineRevision

    api = e2e_studio.client
    data = _build_minimal_wav()
    uploaded = api.post(
        project + "/music",
        headers=_if_match(api, project),
        files={"file": ("sample.wav", data, "audio/wav")},
        data={"volume": "0.42", "duck_db": "-12", "mood": "quiet"},
    )
    assert uploaded.is_success, uploaded.text
    original = uploaded.json()
    assert api.get(original["media_url"]).content == data
    assert original["timeline"]["tracks"]["music"]["mood"] == "quiet"
    hdrs = _if_match(api, project)
    assert api.post(project + "/music", headers=hdrs, files={"file": ("sample.exe", data, "audio/wav")}).status_code == 415
    assert api.post(project + "/music", headers=hdrs, files={"file": ("sample.wav", data, "text/plain")}).status_code == 415
    assert api.post(project + "/music", headers=hdrs, json={"preset": "unknown"}).status_code == 422
    too_big = api.post(
        project + "/music",
        headers=hdrs,
        files={"file": ("big.wav", b"0" * (25 * 1024 * 1024 + 1), "audio/wav")},
    )
    assert too_big.status_code == 413

    doc = original["timeline"]
    doc["tracks"]["visual"][0].update(assets={"keyframe": "kf"}, qa={"score": 9, "attempts": 2, "verdict": "passed"},
                                     status="ready", source_start_ms=200)
    api.put(project + "/timeline", json={"expected_revision": doc["revision"], "document": doc}).raise_for_status()
    worker = media_worker(api)
    api.post(project + "/scenes/example/revoice", headers=_if_match(api, project)).raise_for_status()
    complete_audio(api, worker, 3000)
    complete_words(api, worker, "Second")
    source = api.get(project).json()
    assert source["timeline"]["tracks"]["voice"][0]["asset"]
    for body in ({}, {"json": {"title": "Sound, take two"}}):
        result = api.post(project + "/duplicate", **body)
        assert result.is_success, result.text
        copy = result.json()
        assert copy["title"] == ("Sound, take two" if body else "Sound waves (Copy)")
        for attr in ("topic", "aspect", "target_res", "duration_preset", "qa_policy", "brain_mode"):
            assert copy[attr] == source[attr]
        assert copy["current_revision"] == copy["timeline"]["revision"] == 1
        assert copy["timeline"]["project_id"] == copy["id"] != source["id"]
        tracks = copy["timeline"]["tracks"]
        assert tracks["voice"] == [] and tracks["music"]["asset"] is None and not tracks["captions"]["words"]
        assert copy["media_urls"] == {}
        assert tracks["visual"][0]["qa"] == {"score": None, "attempts": 0, "verdict": "pending", "reason": None}
        assert tracks["visual"][0]["assets"] is None and tracks["visual"][0]["status"] == "planned"
        assert tracks["visual"][1]["props"] == source["timeline"]["tracks"]["visual"][1]["props"]
        ids = [f"{copy['id']}_scn_{i:03d}" for i in (1, 2)]
        assert [s["id"] for s in tracks["visual"]] == ids
        with e2e_studio.sessions() as db:
            assert db.query(TimelineRevision).filter_by(project_id=copy["id"]).count() == 1
            assert [s.id for s in db.query(Scene).filter_by(project_id=copy["id"]).order_by(Scene.order_index)] == ids
    assert api.get(project).json()["timeline"] == source["timeline"]


def test_restore_scene_inserts_all_supported_visual_kinds(project, e2e_studio):
    api = e2e_studio.client
    revision = api.get(project + "/timeline").json()["revision"]
    for index, kind in enumerate(["ai_video", "image_motion", "diagram"]):
        scene = {"kind": kind, "id": f"new-{index}", "narration": "", "start_ms": 0, "end_ms": 2300}
        response = api.post(project + "/timeline/commands", json={"expected_revision": revision,
                            "commands": [{"type": "restore_scene", "index": index, "scene": scene}]})
        assert response.is_success, response.text
        assert response.json()["tracks"]["visual"][index]["id"] == scene["id"]
        revision = response.json()["revision"]


def test_drive_music_upload_verifies_remote_checksum(project, e2e_studio, monkeypatch):
    from types import SimpleNamespace

    import httpx
    from app.models import Asset
    from app.projects import media
    from app.storage import drive

    api = e2e_studio.client
    monkeypatch.setattr(e2e_studio.cfg, "storage_provider", "drive")
    data = _build_minimal_wav()
    digest = hashlib.sha256(data).hexdigest()
    checksum = "0" * 64
    uploads = []

    def google(request):
        assert request.headers["authorization"] == "Bearer test-token"
        if request.url.path == "/drive/v3/files" and request.method == "GET":
            return httpx.Response(200, json={"files": []})
        if request.url.path == "/drive/v3/files" and request.method == "POST":
            return httpx.Response(200, json={"id": "folder"})
        if request.url.path == "/upload/drive/v3/files":
            uploads.append(json.loads(request.content))
            return httpx.Response(200, headers={"Location": "https://drive.test/upload"})
        if request.url.path == "/upload" and request.method == "PUT":
            assert request.content == data
            assert request.headers["content-range"] == f"bytes 0-{len(data)-1}/{len(data)}"
            return httpx.Response(200, json={"id": "drive-file"})
        assert request.url.path == "/drive/v3/files/drive-file"
        return httpx.Response(200, json={"size": str(len(data)), "sha256Checksum": checksum})

    async def token(db, user_id):
        return "test-token"

    mock_http = SimpleNamespace(AsyncClient=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(google), **kw))
    monkeypatch.setattr(drive, "get_valid_google_access_token", token)
    monkeypatch.setattr(drive, "httpx", mock_http)
    monkeypatch.setattr(media, "httpx", mock_http)
    files = {"file": ("ambient.wav", data, "audio/wav")}
    before = api.get(project + "/timeline").json()
    hdrs = {"If-Match": str(before["revision"])}
    assert api.post(project + "/music", headers=hdrs, files=files).status_code == 422
    assert api.get(project + "/timeline").json() == before
    checksum = digest
    result = api.post(project + "/music", headers=hdrs, files=files)
    assert result.is_success, result.text
    with e2e_studio.sessions() as db:
        asset = db.query(Asset).one()
        assert asset.id == result.json()["asset_id"]
        assert (asset.storage_provider, asset.drive_file_id, asset.sync_state) == ("drive", "drive-file", "synced")
        assert uploads[-1]["name"] == f"{asset.id}.wav"
        assert uploads[-1]["appProperties"]["sha256"] == asset.sha256 == digest


def test_stale_tts_completes_for_frozen_export_after_voice_change(project, e2e_studio):
    api = e2e_studio.client
    doc = api.get(project + "/timeline").json()
    doc["tracks"]["visual"] = [doc["tracks"]["visual"][1]]
    api.put(project + "/timeline", json={"expected_revision": doc["revision"], "document": doc}).raise_for_status()
    first = api.post(project + "/scenes/example/revoice", headers=_if_match(api, project)).json()
    worker = media_worker(api)
    old_job = claim(api, worker, "tts")
    assert old_job["id"] == first["tts_job_id"]
    frozen = api.post(project + "/exports", json={"resolution": "1080p"}).json()
    doc = api.get(project + "/timeline").json()
    doc["voice"]["voice_id"] = "bf_emma"
    updated = api.put(project + "/timeline", json={"expected_revision": doc["revision"], "document": doc}).json()
    assert len(api.get(project + "/jobs").json()) == 3  # selecting a voice alone spends no compute
    old = complete(api, worker, old_job, "audio", "original.wav", "audio/wav", _build_minimal_wav(), duration_ms=8000)
    assert old.is_success
    complete_words(api, worker, "Original")
    assert api.get(project + "/timeline").json() == updated
    frozen_render = claim(api, worker, "render")["payload"]
    assert frozen_render["revision"] == frozen["revision"]
    assert frozen_render["document"]["tracks"]["captions"]["words"][0]["word"] == "Original"

    second = api.post(project + "/exports", json={"resolution": "1080p"})
    assert second.is_success, second.text
    new_job = claim(api, worker, "tts")
    assert new_job["id"] != old_job["id"] and new_job["payload"]["voice"]["voice_id"] == "bf_emma"


def test_inserting_a_scene_moves_existing_voice_and_captions(project, e2e_studio):
    api = e2e_studio.client
    api.post(project + "/scenes/example/revoice", headers=_if_match(api, project)).raise_for_status()
    worker = media_worker(api)
    complete_audio(api, worker, 3000)
    complete_words(api, worker, "Second")
    before = api.get(project + "/timeline").json()
    scene = {"id": "inserted", "kind": "ai_video", "narration": "", "start_ms": 0, "end_ms": 2000}
    command = {"type": "restore_scene", "index": 1, "scene": scene}
    response = api.post(project + "/timeline/commands", json={"expected_revision": before["revision"], "commands": [command]})
    assert response.is_success, response.text
    tracks = response.json()["tracks"]
    assert tracks["visual"][2]["start_ms"] == tracks["voice"][0]["start_ms"] == 6000
    assert tracks["voice"][0]["asset"] == before["tracks"]["voice"][0]["asset"]
    assert tracks["captions"]["words"] == [{"word": "Second", "start_ms": 6100, "end_ms": 6500}]


def test_concurrent_command_conflict_rolls_back_job_dispatch(project, e2e_studio):
    from app.core.errors import ConflictError
    from app.models import Job, Project
    from app.timeline.schemas import EditNarrationCmd
    from app.timeline.service import apply_timeline_commands

    api = e2e_studio.client
    with e2e_studio.sessions() as stale:
        old = stale.get(Project, project.split("/")[-1])
        revision = old.current_revision
        command = {"type": "mix_audio", "volume": 0.8}
        api.post(project + "/timeline/commands", json={"expected_revision": revision, "commands": [command]}).raise_for_status()
        with pytest.raises(ConflictError):
            apply_timeline_commands(stale, old.id, revision, [
                EditNarrationCmd(type="edit_narration", scene_id="opening", narration="Losing edit"),
            ], dispatch_jobs=True)
    with e2e_studio.sessions() as db:
        assert db.query(Job).count() == 0
    latest = api.get(project + "/timeline").json()
    assert latest["revision"] == revision + 1 and latest["tracks"]["visual"][0]["narration"] == "First"


def test_revoice_and_music_require_if_match(project, e2e_studio):
    api = e2e_studio.client
    assert api.post(project + "/scenes/example/revoice").status_code == 422
    assert api.post(project + "/scenes/example/revoice", headers={"If-Match": "1"}).status_code == 409
    assert api.post(project + "/music", json={"preset": "ambient_pulse"}).status_code == 422
    assert api.post(project + "/music", headers={"If-Match": "1"}, json={"preset": "ambient_pulse"}).status_code == 409
    assert api.delete(project + "/music").status_code == 422
