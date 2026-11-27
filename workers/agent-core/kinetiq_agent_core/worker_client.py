from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import httpx

from kinetiq_agent_core.resumable_upload import upload_file_resumable


class WorkerClient:
    def __init__(self, api_base_url: str, worker_token: str) -> None:
        self.api_url = api_base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {worker_token}"}
        self.client = httpx.Client(timeout=60)
        self.worker_id: str | None = None
        self._stop_evt = threading.Event()

    def register(
        self,
        *,
        provider: str,
        profile: str,
        gpu_model: str | None,
        vram_gb: int | None,
        capabilities: list[str],
    ) -> str:
        resp = self.client.post(
            f"{self.api_url}/v1/workers/register",
            headers=self.headers,
            json={
                "provider": provider,
                "profile": profile,
                "gpu_model": gpu_model,
                "vram_gb": vram_gb,
                "capabilities": capabilities,
            },
        )
        resp.raise_for_status()
        self.worker_id = resp.json()["worker_id"]
        return self.worker_id

    def start_heartbeat(self, interval_seconds: int = 15) -> None:
        def _loop() -> None:
            with httpx.Client(timeout=10) as hc:
                while not self._stop_evt.is_set():
                    if self.worker_id:
                        try:
                            hc.post(
                                f"{self.api_url}/v1/workers/heartbeat",
                                headers=self.headers,
                                json={"worker_id": self.worker_id},
                            )
                        except Exception:
                            pass
                    self._stop_evt.wait(interval_seconds)

        threading.Thread(target=_loop, daemon=True).start()

    def stop_heartbeat(self) -> None:
        self._stop_evt.set()

    def claim_job(self, capabilities: list[str]) -> dict[str, Any] | None:
        if not self.worker_id:
            raise RuntimeError("Worker must register before claiming jobs")
        resp = self.client.post(
            f"{self.api_url}/v1/workers/jobs/claim",
            headers=self.headers,
            json={"worker_id": self.worker_id, "capabilities": capabilities},
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        return resp.json()

    def report_progress(self, job_id: str, percent: int, step: int | None = None, total_steps: int | None = None) -> None:
        self.client.post(
            f"{self.api_url}/v1/workers/jobs/{job_id}/progress",
            headers=self.headers,
            json={"percent": percent, "step": step, "total_steps": total_steps},
        )

    def upload_artifact(
        self,
        job_id: str,
        file_path: Path,
        *,
        asset_kind: str,
        content_type: str,
        width: int | None = None,
        height: int | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        total_bytes = file_path.stat().st_size
        sess = self.client.post(
            f"{self.api_url}/v1/workers/jobs/{job_id}/upload-session",
            headers=self.headers,
            json={
                "filename": file_path.name,
                "content_type": content_type,
                "total_bytes": total_bytes,
                "asset_kind": asset_kind,
            },
        )
        sess.raise_for_status()
        s = sess.json()

        def _refresh_auth() -> str:
            if not s.get("token_refresh_url"):
                return ""
            r = self.client.post(s["token_refresh_url"], headers=self.headers)
            r.raise_for_status()
            return r.json().get("authorization", "")

        uploaded = upload_file_resumable(
            file_path,
            session_uri=s["session_uri"],
            upload_headers=s.get("upload_headers") or {},
            content_type=content_type,
            chunk_size_bytes=int(s.get("chunk_size_bytes", 8 * 1024 * 1024)),
            refresh_auth_fn=_refresh_auth,
        )
        return {
            "asset_kind": asset_kind,
            "storage_key": s["storage_key"],
            "drive_file_id": uploaded.get("drive_file_id"),
            "content_type": content_type,
            "size_bytes": uploaded["size_bytes"],
            "sha256": uploaded.get("sha256"),
            "width": width,
            "height": height,
            "duration_ms": duration_ms,
        }

    def complete_job(self, job_id: str, artifacts: list[dict[str, Any]], gpu_seconds: float) -> dict[str, Any]:
        resp = self.client.post(
            f"{self.api_url}/v1/workers/jobs/{job_id}/complete",
            headers=self.headers,
            json={"artifacts": artifacts, "gpu_seconds": round(gpu_seconds, 2), "result_meta": {}},
        )
        resp.raise_for_status()
        return resp.json()

    def fail_job(self, job_id: str, error: str) -> None:
        self.client.post(
            f"{self.api_url}/v1/workers/jobs/{job_id}/fail",
            headers=self.headers,
            json={"error": error[:1500]},
        )
