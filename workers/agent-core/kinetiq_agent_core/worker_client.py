from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from kinetiq_agent_core.resumable_upload import upload_file_resumable


class WorkerClient:
    def __init__(self, api_base_url: str, worker_token: str) -> None:
        self.api_url = api_base_url.rstrip("/")
        self.public_api_url = os.getenv("PUBLIC_API_URL", self.api_url).rstrip("/")
        self.headers = {"Authorization": f"Bearer {worker_token}"}
        self.client = httpx.Client(timeout=60)
        self.worker_id: str | None = None
        self._stop_evt = threading.Event()

    def _worker_url(self, url: str) -> str:
        # Signed API URLs use the browser-facing origin, which may be localhost in
        # Compose. Keep the signed path/query verbatim and leave Drive URLs alone.
        prefix = self.public_api_url + "/"
        return self.api_url + "/" + url[len(prefix):] if url.startswith(prefix) else url

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
                        except httpx.HTTPError:
                            logging.getLogger(__name__).warning("Worker heartbeat interrupted")
                    self._stop_evt.wait(interval_seconds)

        threading.Thread(target=_loop, daemon=True).start()

    @contextmanager
    def keep_lease(self, job_id: str, interval_seconds: int = 30) -> Iterator[None]:
        """Keep the job leased while native rendering, model inference or uploads block."""
        stop = threading.Event()
        lost = threading.Event()
        url = f"{self.api_url}/v1/workers/jobs/{job_id}/progress"
        self.client.post(url, headers=self.headers, json={}).raise_for_status()

        def renew() -> None:
            while not stop.wait(interval_seconds):
                try:
                    response = self.client.post(url, headers=self.headers, json={})
                    if response.status_code in {401, 404, 409}:
                        lost.set()
                        return
                    response.raise_for_status()
                except httpx.HTTPError:
                    logging.getLogger(__name__).warning("Lease renewal interrupted for %s", job_id)

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()
        if lost.is_set():
            raise RuntimeError(f"Lost lease for {job_id}")

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
        job = resp.json()
        if job.get("input_download_url"):
            job["input_download_url"] = self._worker_url(job["input_download_url"])
        if urls := job["payload"].get("media_urls"):
            job["payload"]["media_urls"] = {key: self._worker_url(url) for key, url in urls.items()}
        return job

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
            r = self.client.post(f"{self.api_url}/v1/workers/jobs/{job_id}/upload-token-refresh", headers=self.headers)
            r.raise_for_status()
            return r.json().get("authorization", "")

        uploaded = upload_file_resumable(
            file_path,
            session_uri=self._worker_url(s["session_uri"]) if s["provider"] == "local" else s["session_uri"],
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
