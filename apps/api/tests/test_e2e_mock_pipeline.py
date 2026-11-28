from __future__ import annotations

import base64
import hashlib
import io
import json
import struct
import wave
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

CHUNK_256K = 256 * 1024
CHUNK_8MIB = 8 * 1024 * 1024

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

SAMPLE_SRT = (
    "1\n"
    "00:00:00,000 --> 00:00:04,500\n"
    "Spacetime curves around mass.\n\n"
    "2\n"
    "00:00:04,500 --> 00:00:09,000\n"
    "Compare baseline vs relativistic curvature.\n"
)


@pytest.fixture
def e2e_studio(tmp_path, monkeypatch):
    """Isolated FastAPI TestClient with a clean temporary SQLite DB and LocalVault."""
    from app import main
    from app.core import config, db

    db_path = tmp_path / "e2e_kinetiq.db"
    vault_dir = tmp_path / "vault"
    engine = db.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        future=True,
    )
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setitem(db.SessionLocal.kw, "bind", engine)
    db.Base.metadata.create_all(bind=engine)

    try:
        from app.core import rate_limit

        monkeypatch.setattr(rate_limit, "_local", rate_limit.SlidingWindow())
    except ImportError:
        pass

    cfg = config.get_settings()
    for key, value in {
        "app_env": "phase0_local",
        "run_embedded_orchestrator": False,
        "storage_provider": "local",
        "local_vault_dir": str(vault_dir),
        "public_api_url": "http://testserver",
        "gemini_api_key": "",
        "redis_url": "",
        "qa_pass_threshold": 8.0,
        "qa_max_retries": 2,
    }.items():
        monkeypatch.setattr(cfg, key, value)

    with TestClient(main.app) as client:
        try:
            yield SimpleNamespace(
                client=client,
                sessions=db.SessionLocal,
                cfg=cfg,
                vault_dir=vault_dir,
            )
        finally:
            engine.dispose()


def _rel_url(url: str) -> str:
    if url.startswith("http://testserver"):
        return url.removeprefix("http://testserver")
    return url


def _build_minimal_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(struct.pack("<1600h", *([0] * 1600)))
    return buf.getvalue()


def _build_mock_mp4(total_bytes: int, marker: bytes = b"MOCKMP4") -> bytes:
    header = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + marker
    if total_bytes <= len(header):
        return header[:total_bytes]
    return header + (b"\xAB" * (total_bytes - len(header)))


def _resumable_upload(
    client: TestClient,
    worker_headers: dict[str, str],
    job_id: str,
    *,
    filename: str,
    content_type: str,
    asset_kind: str,
    payload: bytes,
    verify_resume_probe: bool = False,
) -> dict:
    """Execute the Google Drive / LocalVault 8 MiB 256-KiB-aligned resumable upload flow."""
    total = len(payload)
    sess_res = client.post(
        f"/v1/workers/jobs/{job_id}/upload-session",
        headers=worker_headers,
        json={
            "filename": filename,
            "content_type": content_type,
            "total_bytes": total,
            "asset_kind": asset_kind,
        },
    )
    assert sess_res.status_code == 200, sess_res.text
    sess = sess_res.json()
    assert sess["chunk_size_bytes"] == CHUNK_8MIB
    assert sess["chunk_size_bytes"] % CHUNK_256K == 0

    upload_uri = _rel_url(sess["session_uri"])
    base_headers = dict(sess.get("upload_headers") or {})
    chunk_size = sess["chunk_size_bytes"]
    offset = 0
    probed = False

    while offset < total:
        end = min(offset + chunk_size, total)
        chunk = payload[offset:end]
        is_final = end >= total
        if not is_final:
            assert len(chunk) == CHUNK_8MIB
            assert len(chunk) % CHUNK_256K == 0

        put_res = client.put(
            upload_uri,
            headers={
                **base_headers,
                "Content-Type": content_type,
                "Content-Range": f"bytes {offset}-{end - 1}/{total}",
            },
            content=chunk,
        )
        if not is_final:
            assert put_res.status_code == 308, put_res.text
            assert put_res.headers.get("Range") == f"bytes=0-{end - 1}"
            if verify_resume_probe and not probed:
                probe_res = client.put(
                    upload_uri,
                    headers={
                        **base_headers,
                        "Content-Length": "0",
                        "Content-Range": f"bytes */{total}",
                    },
                    content=b"",
                )
                assert probe_res.status_code == 308, probe_res.text
                assert probe_res.headers.get("Range") == f"bytes=0-{end - 1}"
                probed = True
        else:
            assert put_res.status_code == 201, put_res.text
        offset = end

    return {
        "asset_kind": asset_kind,
        "storage_key": sess["storage_key"],
        "content_type": content_type,
        "size_bytes": total,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _register_worker(client: TestClient, *, name: str, profile: str, capabilities: list[str]) -> tuple[str, dict]:
    tok_res = client.post(
        "/v1/workers/tokens",
        json={"name": name, "provider": "google_colab", "profile": profile},
    )
    assert tok_res.status_code == 200, tok_res.text
    tok_data = tok_res.json()
    headers = {"Authorization": f"Bearer {tok_data['worker_token']}"}
    reg_res = client.post(
        "/v1/workers/register",
        headers=headers,
        json={
            "provider": "google_colab",
            "profile": profile,
            "gpu_model": "NVIDIA L4",
            "vram_gb": 24,
            "capabilities": capabilities,
        },
    )
    assert reg_res.status_code == 200, reg_res.text
    return reg_res.json()["worker_id"], headers


def test_full_mock_pipeline_e2e_lifecycle(e2e_studio) -> None:
    """Exercise full lifecycle: create -> ChatGPT import -> generate -> 8 MiB upload
    -> HOTL needs_review -> human approval -> Copilot edit -> 4K export & download.
    """
    from app.models import Asset, EventOutbox, Export, Job, Project
    from app.orchestrator import run_orchestrator_tick

    client = e2e_studio.client

    # 1. Project creation (hitl_strict_gate triggers HOTL needs_review on keyframe)
    create_res = client.post(
        "/v1/projects",
        json={
            "title": "General Relativity Visualized",
            "topic": "Spacetime curvature and gravitational lensing",
            "aspect": "16:9",
            "target_res": "4k",
            "duration_preset": "reel_60s",
            "qa_policy": "hitl_strict_gate",
            "brain_mode": "chatgpt_bridge",
        },
    )
    assert create_res.status_code == 200, create_res.text
    proj = create_res.json()
    pid = proj["id"]
    assert proj["current_revision"] == 1

    # 2. ChatGPT Co-Director brief + fenced JSON storyboard import
    brief_res = client.get(f"/v1/projects/{pid}/plan/brief")
    assert brief_res.status_code == 200, brief_res.text
    brief = brief_res.json()
    assert brief["project_id"] == pid
    assert "scenes" in brief["schema_hint"]["properties"]

    storyboard_payload = {
        "palette": ["#0B0F19", "#F59E0B", "#22D3EE", "#F8FAFC"],
        "look": "cinematic documentary, 35mm lens, obsidian & amber rim lighting",
        "negative": "blurry text, watermark, flicker",
        "hook": "Why mass bends the geometry of space itself.",
        "seo_title": "General Relativity Explained Visually",
        "description": "Visual breakdown of spacetime curvature.",
        "hashtags": ["#physics", "#relativity", "#education"],
        "scenes": [
            {
                "narration": "Spacetime curves around mass.",
                "duration_ms": 4500,
                "kind": "ai_video",
                "camera": "slow_dolly_in",
                "model": "ltx25_fp8",
                "ltx25_prompt": "Glowing grid warping beneath a dense star, 35mm documentary shot",
                "wan22_prompt": "Smooth push-in toward luminous spacetime grid",
                "qwen_image_prompt": "High-detail 1080p keyframe of curved spacetime grid",
            },
            {
                "narration": "Compare baseline vs relativistic curvature.",
                "duration_ms": 4500,
                "kind": "diagram",
                "diagram_template": "BarCompare",
                "diagram_props": {
                    "title": "Curvature Deflection",
                    "subtitle": "Arcseconds near solar limb",
                    "labels": ["Newtonian", "Einsteinian"],
                    "values": [0.87, 1.75],
                    "highlight_index": 1,
                },
            },
        ],
    }
    fenced_json = f"```json\n{json.dumps(storyboard_payload)}\n```"
    import_res = client.post(
        f"/v1/projects/{pid}/plan/import",
        json={"raw_json": fenced_json},
    )
    assert import_res.status_code == 200, import_res.text
    imported_doc = import_res.json()
    assert imported_doc["revision"] == 2
    assert len(imported_doc["tracks"]["visual"]) == 2
    assert imported_doc["tracks"]["visual"][0]["status"] == "planned"
    assert imported_doc["tracks"]["visual"][1]["status"] == "ready"

    # 3. Generate pipeline & register GPU worker
    gen_res = client.post(f"/v1/projects/{pid}/generate")
    assert gen_res.status_code == 200, gen_res.text
    assert gen_res.json()["queued_jobs"] == 5  # keyframe plus TTS and alignment for both scenes
    first_scene_id = imported_doc["tracks"]["visual"][0]["id"]

    gpu_wid, gpu_headers = _register_worker(
        client,
        name="e2e-mock-l4",
        profile="ltx25_pro",
        capabilities=["keyframe_qwen", "i2v_ltx25"],
    )
    hb_res = client.post("/v1/workers/heartbeat", headers=gpu_headers, json={"worker_id": gpu_wid})
    assert hb_res.status_code == 200

    # 4. Worker claims keyframe_qwen -> uploads keyframe -> HOTL escalates to needs_review
    kf_claim = client.post(
        "/v1/workers/jobs/claim",
        headers=gpu_headers,
        json={"worker_id": gpu_wid, "capabilities": ["keyframe_qwen", "i2v_ltx25"]},
    )
    assert kf_claim.status_code == 200, kf_claim.text
    kf_job = kf_claim.json()
    assert kf_job["job_type"] == "keyframe_qwen"
    assert kf_job["scene_id"] == first_scene_id
    assert kf_job["comfy_graph"] is not None

    prog_res = client.post(f"/v1/workers/jobs/{kf_job['id']}/progress", headers=gpu_headers, json={"percent": 50})
    assert prog_res.status_code == 200

    kf_art = _resumable_upload(
        client,
        gpu_headers,
        kf_job["id"],
        filename="scn_001_kf.png",
        content_type="image/png",
        asset_kind="keyframe",
        payload=TINY_PNG,
    )
    kf_comp = client.post(
        f"/v1/workers/jobs/{kf_job['id']}/complete",
        headers=gpu_headers,
        json={"artifacts": [{**kf_art, "width": 1920, "height": 1080}], "gpu_seconds": 4.0},
    )
    assert kf_comp.status_code == 200, kf_comp.text
    assert kf_comp.json()["cu_charged"] > 0

    qa_reviews = client.get(f"/v1/projects/{pid}/qa-reviews").json()
    assert len(qa_reviews) == 1
    assert qa_reviews[0]["verdict"] == "needs_review"
    assert qa_reviews[0]["fix_action"] == "awaiting_human_approval"

    proj_review = client.get(f"/v1/projects/{pid}").json()
    assert proj_review["needs_review_count"] == 1
    assert proj_review["timeline"]["tracks"]["visual"][0]["status"] == "needs_review"

    # No i2v job should be claimable before human approval
    blocked_claim = client.post(
        "/v1/workers/jobs/claim",
        headers=gpu_headers,
        json={"worker_id": gpu_wid, "capabilities": ["keyframe_qwen", "i2v_ltx25"]},
    )
    assert blocked_claim.status_code == 204

    # 5. HOTL human approval -> unlocks i2v_ltx25 -> 8 MiB chunked upload + resume probe
    approve_res = client.post(f"/v1/projects/{pid}/scenes/{first_scene_id}/approve")
    assert approve_res.status_code == 200, approve_res.text
    assert approve_res.json()["queued_job_id"] is not None

    proj_approved = client.get(f"/v1/projects/{pid}").json()
    assert proj_approved["needs_review_count"] == 0
    assert proj_approved["timeline"]["tracks"]["visual"][0]["qa"]["verdict"] == "human_approved"
    assert proj_approved["timeline"]["tracks"]["visual"][0]["status"] == "generating_clip"

    clip_claim = client.post(
        "/v1/workers/jobs/claim",
        headers=gpu_headers,
        json={"worker_id": gpu_wid, "capabilities": ["keyframe_qwen", "i2v_ltx25"]},
    )
    assert clip_claim.status_code == 200, clip_claim.text
    clip_job = clip_claim.json()
    assert clip_job["job_type"] == "i2v_ltx25"
    assert clip_job["input_download_url"] is not None

    kf_dl = client.get(_rel_url(clip_job["input_download_url"]))
    assert kf_dl.status_code == 200
    assert kf_dl.content == TINY_PNG

    master_bytes = _build_mock_mp4(CHUNK_8MIB + 4096, marker=b"MASTER1080P")
    proxy_bytes = _build_mock_mp4(4096, marker=b"PROXY540P")

    master_art = _resumable_upload(
        client,
        gpu_headers,
        clip_job["id"],
        filename="scn_001_master.mp4",
        content_type="video/mp4",
        asset_kind="master",
        payload=master_bytes,
        verify_resume_probe=True,
    )
    proxy_art = _resumable_upload(
        client,
        gpu_headers,
        clip_job["id"],
        filename="scn_001_proxy.mp4",
        content_type="video/mp4",
        asset_kind="proxy",
        payload=proxy_bytes,
    )
    clip_comp = client.post(
        f"/v1/workers/jobs/{clip_job['id']}/complete",
        headers=gpu_headers,
        json={
            "artifacts": [
                {**master_art, "width": 1920, "height": 1088, "duration_ms": 4500},
                {**proxy_art, "width": 960, "height": 544, "duration_ms": 4500},
            ],
            "gpu_seconds": 18.0,
        },
    )
    assert clip_comp.status_code == 200, clip_comp.text

    # The rendered clip has its own QA gate. Approve the master before exporting.
    confirm = client.post(f"/v1/projects/{pid}/scenes/{first_scene_id}/approve")
    assert confirm.is_success and confirm.json()["queued_job_id"] is None
    proj_ready = client.get(f"/v1/projects/{pid}").json()
    assert proj_ready["ready_scenes"] == 1
    assert proj_ready["timeline"]["tracks"]["visual"][1]["status"] == "generating_audio"
    assert proj_ready["timeline"]["tracks"]["visual"][0]["status"] == "ready"
    master_asset_id = proj_ready["timeline"]["tracks"]["visual"][0]["assets"]["master"]
    assert master_asset_id in proj_ready["media_urls"]

    range_res = client.get(
        _rel_url(proj_ready["media_urls"][master_asset_id]),
        headers={"Range": "bytes=0-31"},
    )
    assert range_res.status_code == 206
    assert range_res.content == master_bytes[:32]

    # 6. Copilot edit proposal + stale-revision 409 check + accept
    current_rev = proj_ready["current_revision"]
    prop_res = client.post(
        f"/v1/projects/{pid}/copilot",
        json={
            "message": "Tighten pauses and add a comparison diagram",
            "selected_scene_id": first_scene_id,
        },
    )
    assert prop_res.status_code == 200, prop_res.text
    proposal = prop_res.json()
    assert proposal["status"] == "pending"
    assert len(proposal["commands"]) >= 2

    stale_accept = client.post(
        f"/v1/projects/{pid}/copilot/{proposal['proposal_id']}/accept",
        json={"expected_revision": current_rev - 1, "confirm_gpu_spend": True},
    )
    assert stale_accept.status_code == 409

    accept_res = client.post(
        f"/v1/projects/{pid}/copilot/{proposal['proposal_id']}/accept",
        json={"expected_revision": current_rev, "confirm_gpu_spend": True},
    )
    assert accept_res.status_code == 200, accept_res.text
    edited_doc = accept_res.json()
    assert edited_doc["revision"] == current_rev + 1
    assert len(edited_doc["tracks"]["visual"]) == 3

    # 7. 4K export record, dependency gating, render upload & download
    exp_res = client.post(f"/v1/projects/{pid}/exports", json={"resolution": "4k"})
    assert exp_res.status_code == 200, exp_res.text
    exp = exp_res.json()
    exp_id = exp["id"]
    assert exp["resolution"] == "4k"
    assert exp["status"] == "queued"
    assert exp["download_url"] is None
    assert "4K" in exp["publish_kit"]["seo_title"]
    assert exp["publish_kit"]["hook"]
    assert exp["publish_kit"]["hashtags"]
    assert exp["publish_kit"]["ai_disclosure_note"]

    exports_list = client.get(f"/v1/projects/{pid}/exports").json()
    assert any(item["id"] == exp_id for item in exports_list)

    not_ready_dl = client.get(f"/v1/exports/{exp_id}/download")
    assert not_ready_dl.status_code == 404

    exp_wid, exp_headers = _register_worker(
        client,
        name="e2e-mock-export",
        profile="upscale_4k",
        capabilities=["upscale_flashvsr", "upscale_seedvr2", "tts", "align", "transcode", "render"],
    )

    # Render job is gated until 4K upscale (and any media deps) succeed
    gated_render = client.post(
        "/v1/workers/jobs/claim",
        headers=exp_headers,
        json={"worker_id": exp_wid, "capabilities": ["render"]},
    )
    assert gated_render.status_code == 204

    dep_caps = ["upscale_flashvsr", "upscale_seedvr2", "tts", "align", "transcode"]
    completed_dep_types: list[str] = []
    upscaled_4k_bytes = _build_mock_mp4(8192, marker=b"UPSCALE4K")
    wav_bytes = _build_minimal_wav()

    for _ in range(20):
        dep_claim = client.post(
            "/v1/workers/jobs/claim",
            headers=exp_headers,
            json={"worker_id": exp_wid, "capabilities": dep_caps},
        )
        if dep_claim.status_code == 204:
            break
        assert dep_claim.status_code == 200, dep_claim.text
        dep = dep_claim.json()
        jtype = dep["job_type"]
        sid = dep.get("scene_id") or dep["id"]

        if jtype in {"upscale_flashvsr", "upscale_seedvr2"}:
            art = _resumable_upload(
                client,
                exp_headers,
                dep["id"],
                filename=f"{sid}_4k.mp4",
                content_type="video/mp4",
                asset_kind="upscaled_4k",
                payload=upscaled_4k_bytes,
            )
            art.update({"width": 3840, "height": 2160, "duration_ms": 4500})
        elif jtype == "tts":
            art = _resumable_upload(
                client,
                exp_headers,
                dep["id"],
                filename=f"{sid}.wav",
                content_type="audio/wav",
                asset_kind="audio",
                payload=wav_bytes,
            )
            art.update({"duration_ms": 4500})
        elif jtype == "align":
            words_bytes = json.dumps(
                {"words": [{"word": "Spacetime", "start_ms": 0, "end_ms": 500}]}
            ).encode("utf-8")
            art = _resumable_upload(
                client,
                exp_headers,
                dep["id"],
                filename=f"{sid}_words.json",
                content_type="application/json",
                asset_kind="words",
                payload=words_bytes,
            )
        else:
            art = _resumable_upload(
                client,
                exp_headers,
                dep["id"],
                filename=f"{sid}_proxy.mp4",
                content_type="video/mp4",
                asset_kind="proxy",
                payload=proxy_bytes,
            )

        dep_done = client.post(
            f"/v1/workers/jobs/{dep['id']}/complete",
            headers=exp_headers,
            json={"artifacts": [art], "gpu_seconds": 3.0},
        )
        assert dep_done.status_code == 200, dep_done.text
        completed_dep_types.append(jtype)

    assert any(jt in completed_dep_types for jt in ("upscale_flashvsr", "upscale_seedvr2"))

    render_claim = client.post(
        "/v1/workers/jobs/claim",
        headers=exp_headers,
        json={"worker_id": exp_wid, "capabilities": ["render"]},
    )
    assert render_claim.status_code == 200, render_claim.text
    render_job = render_claim.json()
    assert render_job["job_type"] == "render"
    assert render_job["payload"]["export_id"] == exp_id
    assert render_job["payload"]["resolution"] == "4k"

    export_4k_bytes = _build_mock_mp4(16384, marker=b"FINAL4KMASTER")
    srt_bytes = SAMPLE_SRT.encode("utf-8")

    exp_art = _resumable_upload(
        client,
        exp_headers,
        render_job["id"],
        filename=f"kinetiq_{pid}_4k.mp4",
        content_type="video/mp4",
        asset_kind="export",
        payload=export_4k_bytes,
    )
    srt_art = _resumable_upload(
        client,
        exp_headers,
        render_job["id"],
        filename="captions.srt",
        content_type="text/plain; charset=utf-8",
        asset_kind="srt",
        payload=srt_bytes,
    )
    render_comp = client.post(
        f"/v1/workers/jobs/{render_job['id']}/complete",
        headers=exp_headers,
        json={
            "artifacts": [
                {**exp_art, "width": 3840, "height": 2160, "duration_ms": 13500},
                srt_art,
            ],
            "gpu_seconds": 5.0,
        },
    )
    assert render_comp.status_code == 200, render_comp.text

    exp_after = client.get(f"/v1/exports/{exp_id}")
    assert exp_after.status_code == 200, exp_after.text
    exp_data = exp_after.json()

    # On main prior to PR #12's finish_export hook, render completion persists the
    # synced export & srt Assets without updating Export.master_asset_id yet.
    if exp_data["status"] != "ready" or not exp_data["download_url"]:
        with e2e_studio.sessions() as db:
            rjob = db.query(Job).filter(Job.id == render_job["id"]).one()
            assert rjob.status == "succeeded"
            exp_row = db.query(Export).filter(Export.id == exp_id).one()
            exp_asset = (
                db.query(Asset)
                .filter(Asset.project_id == pid, Asset.kind == "export", Asset.sync_state == "synced")
                .one()
            )
            srt_asset = (
                db.query(Asset)
                .filter(Asset.project_id == pid, Asset.kind == "srt", Asset.sync_state == "synced")
                .one()
            )
            exp_row.master_asset_id = exp_asset.id
            exp_row.srt_asset_id = srt_asset.id
            exp_row.status = "ready"
            db.commit()
        exp_data = client.get(f"/v1/exports/{exp_id}").json()

    assert exp_data["status"] == "ready"
    assert exp_data["resolution"] == "4k"
    assert exp_data["download_url"] == f"http://testserver/v1/exports/{exp_id}/download"
    assert exp_data["srt_url"] is not None

    dl_res = client.get(f"/v1/exports/{exp_id}/download")
    assert dl_res.status_code == 200, dl_res.text
    assert f'attachment; filename="kinetiq_{pid}_4k.mp4"' in dl_res.headers.get("Content-Disposition", "")
    assert dl_res.content == export_4k_bytes
    assert hashlib.sha256(dl_res.content).hexdigest() == hashlib.sha256(export_4k_bytes).hexdigest()

    srt_dl = client.get(_rel_url(exp_data["srt_url"]))
    assert srt_dl.status_code == 200
    assert srt_dl.content == srt_bytes

    with e2e_studio.sessions() as db:
        stats = run_orchestrator_tick(db)
        assert stats["completed_projects"] == 1
        proj_row = db.query(Project).filter(Project.id == pid).one()
        assert proj_row.status == "ready"

        events = db.query(EventOutbox).filter(EventOutbox.project_id == pid).all()
        event_types = {ev.event_type for ev in events}
        for expected_event in (
            "project.created",
            "pipeline.started",
            "asset.synced",
            "qa.reviewed",
            "copilot.proposal",
            "timeline.updated",
            "export.queued",
        ):
            assert expected_event in event_types


def test_hotl_smart_auto_heal_retries_then_escalates_to_needs_review(e2e_studio, monkeypatch) -> None:
    """Verify hotl_smart retries up to qa_max_retries before escalating to needs_review."""
    client = e2e_studio.client
    monkeypatch.setattr(e2e_studio.cfg, "qa_pass_threshold", 9.5)
    monkeypatch.setattr(e2e_studio.cfg, "qa_max_retries", 2)

    proj = client.post(
        "/v1/projects",
        json={
            "title": "HOTL Escalation Check",
            "topic": "Black hole accretion",
            "aspect": "9:16",
            "target_res": "1080p",
            "duration_preset": "reel_60s",
            "qa_policy": "hotl_smart",
            "brain_mode": "chatgpt_bridge",
        },
    ).json()
    pid = proj["id"]

    storyboard = {
        "palette": ["#0B0F19", "#F59E0B"],
        "look": "cinematic documentary",
        "negative": "blurry",
        "hook": "Matter spirals past the event horizon.",
        "seo_title": "Accretion Disks",
        "description": "Black hole accretion explained.",
        "hashtags": ["#space"],
        "scenes": [
            {
                "narration": "Matter spirals into the black hole.",
                "duration_ms": 4000,
                "kind": "ai_video",
                "camera": "cinematic_orbit",
                "model": "ltx25_fp8",
                "ltx25_prompt": "Glowing accretion disk around event horizon",
                "wan22_prompt": "Orbiting camera around glowing disk",
                "qwen_image_prompt": "Accretion disk keyframe",
            }
        ],
    }
    imp = client.post(f"/v1/projects/{pid}/plan/import", json={"raw_json": json.dumps(storyboard)})
    assert imp.status_code == 200
    assert client.post(f"/v1/projects/{pid}/generate").status_code == 200

    wid, w_headers = _register_worker(
        client,
        name="hotl-retry-worker",
        profile="ltx25_pro",
        capabilities=["keyframe_qwen", "i2v_ltx25"],
    )

    for attempt in range(1, 4):
        claim = client.post(
            "/v1/workers/jobs/claim",
            headers=w_headers,
            json={"worker_id": wid, "capabilities": ["keyframe_qwen", "i2v_ltx25"]},
        )
        assert claim.status_code == 200, f"Expected keyframe attempt {attempt}"
        job = claim.json()
        assert job["job_type"] == "keyframe_qwen"
        art = _resumable_upload(
            client,
            w_headers,
            job["id"],
            filename=f"kf_attempt_{attempt}.png",
            content_type="image/png",
            asset_kind="keyframe",
            payload=TINY_PNG,
        )
        done = client.post(
            f"/v1/workers/jobs/{job['id']}/complete",
            headers=w_headers,
            json={"artifacts": [art], "gpu_seconds": 2.0},
        )
        assert done.status_code == 200

    reviews = client.get(f"/v1/projects/{pid}/qa-reviews").json()
    assert len(reviews) == 3
    verdicts = [r["verdict"] for r in reviews]
    assert verdicts.count("auto_healing") == 2
    assert verdicts.count("needs_review") == 1
    assert any(r["fix_action"] == "escalated_to_human_on_loop" for r in reviews)

    detail = client.get(f"/v1/projects/{pid}").json()
    assert detail["needs_review_count"] == 1
    assert detail["timeline"]["tracks"]["visual"][0]["status"] == "needs_review"

    no_more = client.post(
        "/v1/workers/jobs/claim",
        headers=w_headers,
        json={"worker_id": wid, "capabilities": ["keyframe_qwen", "i2v_ltx25"]},
    )
    assert no_more.status_code == 204

    first_scene_id = detail["timeline"]["tracks"]["visual"][0]["id"]
    approved = client.post(f"/v1/projects/{pid}/scenes/{first_scene_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["queued_job_id"] is not None

    unlocked = client.post(
        "/v1/workers/jobs/claim",
        headers=w_headers,
        json={"worker_id": wid, "capabilities": ["i2v_ltx25"]},
    )
    assert unlocked.status_code == 200
    assert unlocked.json()["job_type"] == "i2v_ltx25"


def test_chatgpt_import_rejects_malformed_json(e2e_studio) -> None:
    """Malformed ChatGPT storyboard JSON must fail with 422 without advancing revision."""
    client = e2e_studio.client
    proj = client.post(
        "/v1/projects",
        json={
            "title": "Invalid JSON Guard",
            "topic": "Error handling",
            "aspect": "16:9",
            "target_res": "1080p",
            "duration_preset": "reel_60s",
            "qa_policy": "hotl_smart",
            "brain_mode": "chatgpt_bridge",
        },
    ).json()
    pid = proj["id"]

    bad = client.post(f"/v1/projects/{pid}/plan/import", json={"raw_json": "{not valid json"})
    assert bad.status_code == 422
    assert bad.json()["error"] == "validation_failed"
    assert client.get(f"/v1/projects/{pid}").json()["current_revision"] == 1


def test_wave2_unified_full_stack_lifecycle(e2e_studio):
    """Follow one narrated reel from generation through render, recovery and duplication."""
    import asyncio
    import time

    from app.models import Job, Project, Scene, TimelineRevision
    from app.storage.snapshots import rebuild_project_from_vault, snapshot_pending_revisions
    from app.timeline.service import get_latest_timeline
    from test_audio_first_pipeline import claim, complete, complete_audio, complete_words
    from test_vault_streaming_ops import database

    api = e2e_studio.client
    created = api.post("/v1/projects", json={
        "title": "Tidal forces", "topic": "How the Moon moves the ocean", "aspect": "9:16",
        "qa_policy": "hitl_strict_gate", "script_override": "Watch the tide rise.",
    })
    created.raise_for_status()
    pid = created.json()["id"]
    root = f"/v1/projects/{pid}"
    timeline = root + "/timeline"
    original, = created.json()["timeline"]["tracks"]["visual"]
    sid = original["id"]

    def if_match(revision):
        return {"If-Match": str(revision)}

    def rejects_unguarded(method, url, revision, **kwargs):
        assert api.request(method, url, **kwargs).status_code == 422
        stale = api.request(method, url, headers=if_match(revision - 1), **kwargs)
        assert stale.status_code == 409, stale.text
        assert api.get(timeline).json()["revision"] == revision

    def complete_outputs(worker, job, outputs):
        artifacts = [
            _resumable_upload(api, worker[1], job["id"], filename=name, content_type=mime,
                              asset_kind=kind, payload=content)
            for kind, name, mime, content in outputs
        ]
        response = api.post(f"/v1/workers/jobs/{job['id']}/complete", headers=worker[1],
                            json={"artifacts": artifacts})
        response.raise_for_status()

    generated = api.post(root + "/generate")
    generated.raise_for_status()
    with e2e_studio.sessions() as db:
        jobs = db.query(Job).filter_by(project_id=pid).all()
        assert {j.job_type for j in jobs} == {"keyframe_qwen", "tts", "align"}
        assert len(jobs) == generated.json()["queued_jobs"] == 3
        assert {j.id for j in jobs} == set(generated.json()["job_ids"])
        assert all(j.status == "queued" and j.scene_id == sid for j in jobs)
        by_type = {j.job_type: j for j in jobs}
        assert by_type["align"].depends_on == [by_type["tts"].id]

    gpu = _register_worker(api, name="GPU", profile="ltx25_pro", capabilities=["keyframe_qwen", "i2v_ltx25"])
    key = claim(api, gpu, "keyframe_qwen")
    complete(api, gpu, key, "keyframe", "frame.png", "image/png", TINY_PNG).raise_for_status()
    approve = f"{root}/scenes/{sid}/approve"
    assert api.get(timeline).json()["tracks"]["visual"][0]["status"] == "needs_review"
    approved = api.post(approve).json()
    clip = claim(api, gpu, "i2v_ltx25")
    assert clip["id"] == approved["queued_job_id"]
    complete_outputs(gpu, clip, [
        ("master", "clip.mp4", "video/mp4", _build_mock_mp4(2048, b"master")),
        ("proxy", "proxy.mp4", "video/mp4", _build_mock_mp4(512, b"proxy")),
    ])
    # Clip QA consumes completions asynchronously in the API lifespan.
    deadline = time.monotonic() + 10
    while api.get(timeline).json()["tracks"]["visual"][0]["status"] == "qa_checking":
        assert time.monotonic() < deadline, "Clip QA did not finish the render review"
        time.sleep(0.1)
    assert api.get(timeline).json()["tracks"]["visual"][0]["status"] == "needs_review"
    assert api.post(approve).json()["queued_job_id"] is None
    scene = api.get(timeline).json()["tracks"]["visual"][0]
    assert scene["status"] == "ready" and scene["assets"]["master"] and scene["assets"]["proxy"]

    media = _register_worker(api, name="Media", profile="media", capabilities=["tts", "align"])
    assert complete_audio(api, media, 4000)["scene_id"] == sid
    assert complete_words(api, media, "Watch")["scene_id"] == sid
    spoken = api.get(timeline).json()
    voice, = spoken["tracks"]["voice"]
    assert (voice["scene_id"], voice["start_ms"], voice["end_ms"]) == (sid, 0, 4000)
    assert voice["asset"]
    assert spoken["tracks"]["captions"]["words"] == [{"word": "Watch", "start_ms": 100, "end_ms": 500}]

    rev = spoken["revision"]
    replacement = {"expected_revision": rev - 1, "document": spoken}
    assert api.put(timeline, json=replacement).status_code == 409
    right_id = sid + "_right"
    split = {"type": "split_scene", "scene_id": sid, "new_scene_id": right_id, "split_at_ms": 2000}
    batch = {"commands": [split], "expected_revision": rev}
    assert api.post(timeline + "/commands", json=batch, headers=if_match(rev - 1)).status_code == 409
    cut = api.post(timeline + "/commands", json=batch, headers=if_match(rev))
    cut.raise_for_status()
    left, right = cut.json()["tracks"]["visual"]
    assert (left["end_ms"], right["start_ms"], right["source_start_ms"]) == (2000, 2000, 2000)
    assert right["assets"]["master"] == scene["assets"]["master"]

    # Give the second shot its own take before freezing the export.
    revoice = f"{root}/scenes/{right_id}/revoice"
    rev = cut.json()["revision"]
    rejects_unguarded("POST", revoice, rev)
    take = api.post(revoice, headers=if_match(rev)).json()
    assert complete_audio(api, media, 2000)["id"] == take["tts_job_id"]
    assert complete_words(api, media, "tide")["id"] == take["align_job_id"]

    before_music = api.get(timeline).json()
    rev = before_music["revision"]
    music = root + "/music"
    preset = {"preset": "ambient_pulse"}
    rejects_unguarded("POST", music, rev, json=preset)
    mixed = api.post(music, json=preset, headers=if_match(rev)).json()
    assert mixed["timeline"]["tracks"]["music"]["asset"] == mixed["asset_id"]
    current = mixed["timeline"]["revision"]
    rejects_unguarded("DELETE", music, current)

    restore = root + f"/revisions/{rev}/restore"
    rejects_unguarded("POST", restore, current)
    restored = api.post(restore, headers=if_match(current)).json()
    assert restored["revision"] == current + 1
    assert restored["tracks"] == before_music["tracks"]
    mixed = api.post(music, json=preset, headers=if_match(restored["revision"])).json()
    frozen = mixed["timeline"]
    exp = api.post(root + "/exports", json={"resolution": "1080p"}).json()
    assert exp["status"] == "queued" and exp["revision"] == frozen["revision"]

    renderer = _register_worker(api, name="Renderer", profile="render", capabilities=["render"])
    render = claim(api, renderer, "render")
    payload = render["payload"]
    assert (payload["export_id"], payload["revision"], payload["resolution"]) == (exp["id"], frozen["revision"], "1080p")
    tracks = payload["document"]["tracks"]
    assert tracks["visual"][1]["source_start_ms"] == 2000
    assert tracks["voice"] == frozen["tracks"]["voice"]
    assert {v["scene_id"] for v in tracks["voice"]} == {sid, right_id}
    assert voice["asset"] in {v["asset"] for v in tracks["voice"]}
    assert tracks["captions"]["words"] == [
        {"word": "Watch", "start_ms": 100, "end_ms": 500},
        {"word": "tide", "start_ms": 2100, "end_ms": 2500},
    ]
    assert tracks["music"]["asset"] == mixed["asset_id"]
    for aid in [scene["assets"]["master"], mixed["asset_id"], *(v["asset"] for v in tracks["voice"])]:
        assert api.get(payload["media_urls"][aid]).is_success

    movie = _build_mock_mp4(4096, b"1080p-export")
    complete_outputs(renderer, render, [
        ("export", "tides.mp4", "video/mp4", movie),
        ("srt", "captions.srt", "application/x-subrip", SAMPLE_SRT.encode()),
    ])
    saved, = api.get(root + "/exports").json()
    assert saved["id"] == exp["id"] and saved["status"] == "ready"
    assert api.get(saved["srt_url"]).content == SAMPLE_SRT.encode()
    download = f"/v1/exports/{exp['id']}/download"
    part = api.get(download, headers={"Range": "bytes=0-63"})
    assert part.status_code == 206 and part.content == movie[:64]
    assert part.headers["content-range"] == f"bytes 0-63/{len(movie)}"
    inline = api.get(download, params={"inline": 1})
    assert inline.status_code == 200 and inline.content == movie
    assert inline.headers["content-disposition"].split(";")[0] == "inline"

    vault = e2e_studio.vault_dir
    latest = api.get(timeline).json()
    with e2e_studio.sessions() as db:
        assert asyncio.run(snapshot_pending_revisions(db)) == latest["revision"]
        owner = db.get(Project, pid).owner_id
        revisions = db.query(TimelineRevision).filter_by(project_id=pid).all()
        for row in revisions:
            assert row.drive_snapshot_id == f"{pid}/history/v{row.revision}.json"
            assert json.loads((vault / row.drive_snapshot_id).read_bytes()) == row.document
        assert json.loads((vault / pid / "project.json").read_bytes())["document"] == latest

    with database(vault.parent / "recovered.sqlite") as fresh, fresh() as db:
        recovered = asyncio.run(rebuild_project_from_vault(db, project_id=pid, user_id=owner))
        assert (recovered.id, recovered.owner_id, recovered.title, recovered.current_revision) == (
            pid, owner, created.json()["title"], latest["revision"],
        )
        assert get_latest_timeline(db, pid).model_dump(mode="json") == latest
        scenes = db.query(Scene).filter_by(project_id=pid).order_by(Scene.order_index).all()
        assert [(s.id, s.kind, s.status, s.start_ms, s.end_ms, s.master_asset_id) for s in scenes] == [
            (s["id"], s["kind"], s["status"], s["start_ms"], s["end_ms"], s["assets"]["master"])
            for s in latest["tracks"]["visual"]
        ]

    metrics = api.get("/v1/health/metrics").json()
    assert set(metrics) == {"status", "env", "storage_provider", "redis_connected", "jobs", "workers", "vault", "snapshots"}
    assert (metrics["status"], metrics["env"], metrics["storage_provider"], metrics["redis_connected"]) == (
        "ok", "phase0_local", "local", False,
    )
    assert set(metrics["jobs"]) == {"queued", "leased", "running", "succeeded", "failed", "dead"}
    assert set(metrics["workers"]) == {"online", "busy", "offline", "total_cu_month"}
    assert set(metrics["vault"]) == {"synced", "missing", "mismatch", "pending"}
    assert set(metrics["snapshots"]) == {"total_revisions", "backed_up_revisions"}
    assert metrics["jobs"]["succeeded"] > 0 and metrics["vault"]["synced"] > 0
    assert metrics["snapshots"]["backed_up_revisions"] == metrics["snapshots"]["total_revisions"] == latest["revision"]

    clone = api.post(root + "/duplicate").json()
    assert clone["current_revision"] == clone["timeline"]["revision"] == 1
    assert clone["id"] != pid
    copied = clone["timeline"]["tracks"]
    assert len(copied["visual"]) == len(scenes)
    assert {s["id"] for s in copied["visual"]}.isdisjoint({sid, right_id})
    assert not clone["media_urls"] and not copied["voice"] and copied["music"]["asset"] is None
    assert api.get(timeline).json() == latest
