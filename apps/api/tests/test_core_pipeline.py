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


def test_split_trim_and_regenerate_source_start_ms() -> None:
    with TestClient(app) as client:
        proj = client.post(
            "/v1/projects",
            json={
                "title": "Split Offset Test",
                "topic": "Relativity",
                "aspect": "16:9",
                "target_res": "1080p",
                "duration_preset": "reel_60s",
                "qa_policy": "hotl_smart",
                "brain_mode": "gemini_auto",
            },
        ).json()
        pid = proj["id"]
        scn1 = proj["timeline"]["tracks"]["visual"][0]
        split_at = scn1["start_ms"] + 2500

        r1 = client.post(
            f"/v1/projects/{pid}/timeline/commands",
            headers={"If-Match": "1"},
            json={
                "expected_revision": 1,
                "author": "user",
                "commands": [
                    {
                        "type": "split_scene",
                        "scene_id": scn1["id"],
                        "split_at_ms": split_at,
                        "new_scene_id": "scn_split_b",
                    }
                ],
            },
        )
        assert r1.status_code == 200
        visual = r1.json()["tracks"]["visual"]
        left = next(s for s in visual if s["id"] == scn1["id"])
        right = next(s for s in visual if s["id"] == "scn_split_b")
        assert left["source_start_ms"] == 0
        assert right["source_start_ms"] == 2500

        # Nested split on right half should cascade source_start_ms
        r2 = client.post(
            f"/v1/projects/{pid}/timeline/commands",
            headers={"If-Match": "2"},
            json={
                "expected_revision": 2,
                "author": "user",
                "commands": [
                    {
                        "type": "split_scene",
                        "scene_id": "scn_split_b",
                        "split_at_ms": right["start_ms"] + 1500,
                        "new_scene_id": "scn_split_c",
                    }
                ],
            },
        )
        assert r2.status_code == 200
        part_c = next(s for s in r2.json()["tracks"]["visual"] if s["id"] == "scn_split_c")
        assert part_c["source_start_ms"] == 4000

        # Trim start edge of part_c forward by 500ms -> source_start_ms advances to 4500ms
        r3 = client.post(
            f"/v1/projects/{pid}/timeline/commands",
            headers={"If-Match": "3"},
            json={
                "expected_revision": 3,
                "author": "user",
                "commands": [
                    {
                        "type": "trim_scene",
                        "scene_id": "scn_split_c",
                        "start_ms": part_c["start_ms"] + 500,
                        "end_ms": part_c["end_ms"],
                    }
                ],
            },
        )
        assert r3.status_code == 200
        trimmed_c = next(s for s in r3.json()["tracks"]["visual"] if s["id"] == "scn_split_c")
        assert trimmed_c["source_start_ms"] == 4500

        # Regenerating resets source_start_ms back to 0
        r4 = client.post(
            f"/v1/projects/{pid}/scenes/scn_split_c/regenerate",
            headers={"If-Match": "4"},
            json={"prompt": "Fresh clip"},
        )
        assert r4.status_code == 200
        tl = client.get(f"/v1/projects/{pid}/timeline").json()
        regen_c = next(s for s in tl["tracks"]["visual"] if s["id"] == "scn_split_c")
        assert regen_c["source_start_ms"] == 0


def test_regenerate_scene_enforces_if_match() -> None:
    with TestClient(app) as client:
        proj = client.post(
            "/v1/projects",
            json={
                "title": "Regenerate Concurrency Test",
                "topic": "Thermodynamics",
                "aspect": "16:9",
                "target_res": "1080p",
                "duration_preset": "reel_60s",
                "qa_policy": "hotl_smart",
                "brain_mode": "gemini_auto",
            },
        ).json()
        pid = proj["id"]
        sid = proj["timeline"]["tracks"]["visual"][0]["id"]

        bad = client.post(
            f"/v1/projects/{pid}/scenes/{sid}/regenerate",
            headers={"If-Match": "abc"},
            json={"prompt": "Malformed header"},
        )
        assert bad.status_code == 422

        ok = client.post(
            f"/v1/projects/{pid}/scenes/{sid}/regenerate",
            headers={"If-Match": "1"},
            json={"prompt": "Updated scene prompt"},
        )
        assert ok.status_code == 200
        assert ok.json()["revision"] == 2

        stale = client.post(
            f"/v1/projects/{pid}/scenes/{sid}/regenerate",
            headers={"If-Match": "1"},
            json={"prompt": "Stale request"},
        )
        assert stale.status_code == 409


def test_restore_revision_enforces_if_match() -> None:
    with TestClient(app) as client:
        proj = client.post(
            "/v1/projects",
            json={
                "title": "Restore Concurrency Test",
                "topic": "Electromagnetism",
                "aspect": "16:9",
                "target_res": "1080p",
                "duration_preset": "reel_60s",
                "qa_policy": "hotl_smart",
                "brain_mode": "gemini_auto",
            },
        ).json()
        pid = proj["id"]
        sid = proj["timeline"]["tracks"]["visual"][0]["id"]

        # Advance timeline from revision 1 -> 2
        updated = client.post(
            f"/v1/projects/{pid}/timeline/commands",
            headers={"If-Match": "1"},
            json={
                "expected_revision": 1,
                "author": "user",
                "commands": [{"type": "edit_narration", "scene_id": sid, "narration": "Updated line"}],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["revision"] == 2

        missing = client.post(f"/v1/projects/{pid}/revisions/1/restore")
        assert missing.status_code == 422

        bad = client.post(f"/v1/projects/{pid}/revisions/1/restore", headers={"If-Match": "nope"})
        assert bad.status_code == 422

        stale = client.post(f"/v1/projects/{pid}/revisions/1/restore", headers={"If-Match": "1"})
        assert stale.status_code == 409

        ok = client.post(f"/v1/projects/{pid}/revisions/1/restore", headers={"If-Match": "2"})
        assert ok.status_code == 200
        assert ok.json()["revision"] == 3
