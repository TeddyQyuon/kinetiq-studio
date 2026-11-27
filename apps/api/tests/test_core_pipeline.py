from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from pathlib import Path

# Configure isolated SQLite + local vault BEFORE importing app modules
_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TMP_DIR.name) / 'test_kinetiq.db'}"
os.environ["LOCAL_VAULT_DIR"] = str(Path(_TMP_DIR.name) / "vault")
os.environ["STORAGE_PROVIDER"] = "local"
os.environ["RUN_EMBEDDED_ORCHESTRATOR"] = "false"
os.environ["PUBLIC_API_URL"] = "http://testserver"
os.environ["GEMINI_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_health_and_ready() -> None:
    with TestClient(app) as client:
        h = client.get("/health")
        assert h.status_code == 200
        assert h.json()["status"] == "ok"

        r = client.get("/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ready"


def test_project_creation_and_if_match_concurrency() -> None:
    with TestClient(app) as client:
        res = client.post(
            "/v1/projects",
            json={
                "title": "How Gravity Works",
                "topic": "Spacetime curvature and black holes",
                "aspect": "9:16",
                "target_res": "1080p",
                "duration_preset": "reel_60s",
                "qa_policy": "hotl_smart",
                "brain_mode": "gemini_auto",
            },
        )
        assert res.status_code == 200
        proj = res.json()
        pid = proj["id"]
        assert proj["current_revision"] == 1
        assert len(proj["timeline"]["tracks"]["visual"]) >= 4

        ok = client.post(
            f"/v1/projects/{pid}/timeline/commands",
            headers={"If-Match": "1"},
            json={
                "expected_revision": 1,
                "author": "user",
                "commands": [{"type": "tighten_pauses", "max_gap_ms": 80}],
            },
        )
        assert ok.status_code == 200
        assert ok.json()["revision"] == 2

        conflict = client.post(
            f"/v1/projects/{pid}/timeline/commands",
            headers={"If-Match": "1"},
            json={
                "expected_revision": 1,
                "author": "user",
                "commands": [{"type": "tighten_pauses", "max_gap_ms": 40}],
            },
        )
        assert conflict.status_code == 409


def test_worker_resumable_upload_and_hotl_qa_pipeline() -> None:
    with TestClient(app) as client:
        p = client.post(
            "/v1/projects",
            json={
                "title": "Quantum Tunneling",
                "topic": "Barrier penetration at nanoscale",
                "aspect": "16:9",
                "target_res": "1080p",
                "duration_preset": "reel_60s",
                "qa_policy": "hotl_smart",
                "brain_mode": "gemini_auto",
            },
        ).json()
        pid = p["id"]

        tok = client.post(
            "/v1/workers/tokens",
            json={"name": "test-l4", "provider": "google_colab", "profile": "ltx25_pro"},
        ).json()
        w_headers = {"Authorization": f"Bearer {tok['worker_token']}"}

        reg = client.post(
            "/v1/workers/register",
            headers=w_headers,
            json={
                "provider": "google_colab",
                "profile": "ltx25_pro",
                "gpu_model": "NVIDIA L4",
                "vram_gb": 24,
                "capabilities": ["keyframe_qwen", "i2v_ltx25"],
            },
        )
        assert reg.status_code == 200
        wid = reg.json()["worker_id"]

        gen = client.post(f"/v1/projects/{pid}/generate")
        assert gen.status_code == 200
        assert gen.json()["queued_jobs"] > 0

        claim = client.post(
            "/v1/workers/jobs/claim",
            headers=w_headers,
            json={"worker_id": wid, "capabilities": ["keyframe_qwen", "i2v_ltx25"]},
        )
        assert claim.status_code == 200
        job = claim.json()
        assert job["job_type"] == "keyframe_qwen"
        assert job["comfy_graph"] is not None

        sess = client.post(
            f"/v1/workers/jobs/{job['id']}/upload-session",
            headers=w_headers,
            json={
                "filename": "scene_001.png",
                "content_type": "image/png",
                "total_bytes": len(TINY_PNG),
                "asset_kind": "keyframe",
            },
        ).json()

        rel_upload_uri = sess["session_uri"].replace("http://testserver", "")
        put_res = client.put(
            rel_upload_uri,
            headers={"Content-Range": f"bytes 0-{len(TINY_PNG) - 1}/{len(TINY_PNG)}"},
            content=TINY_PNG,
        )
        assert put_res.status_code == 201

        comp = client.post(
            f"/v1/workers/jobs/{job['id']}/complete",
            headers=w_headers,
            json={
                "artifacts": [
                    {
                        "asset_kind": "keyframe",
                        "storage_key": sess["storage_key"],
                        "content_type": "image/png",
                        "size_bytes": len(TINY_PNG),
                        "sha256": hashlib.sha256(TINY_PNG).hexdigest(),
                        "width": 1920,
                        "height": 1080,
                    }
                ],
                "gpu_seconds": 6.0,
            },
        )
        assert comp.status_code == 200

        reviews = client.get(f"/v1/projects/{pid}/qa-reviews").json()
        assert len(reviews) >= 1
        assert reviews[0]["verdict"] == "passed"

        jobs = client.get(f"/v1/projects/{pid}/jobs").json()
        assert any(j["job_type"] == "i2v_ltx25" and j["scene_id"] == job["scene_id"] for j in jobs)


def test_copilot_proposal_and_accept() -> None:
    with TestClient(app) as client:
        proj = client.post(
            "/v1/projects",
            json={
                "title": "Copilot Test",
                "topic": "Neural networks",
                "aspect": "16:9",
                "target_res": "1080p",
                "duration_preset": "reel_60s",
                "qa_policy": "hotl_smart",
                "brain_mode": "gemini_auto",
            },
        ).json()
        pid = proj["id"]
        prop = client.post(
            f"/v1/projects/{pid}/copilot",
            json={"message": "Tighten pauses and add a comparison chart"},
        )
        assert prop.status_code == 200
        pdata = prop.json()
        assert len(pdata["commands"]) >= 1

        acc = client.post(
            f"/v1/projects/{pid}/copilot/{pdata['proposal_id']}/accept",
            json={"expected_revision": proj["current_revision"], "confirm_gpu_spend": True},
        )
        assert acc.status_code == 200
        assert acc.json()["revision"] == proj["current_revision"] + 1
