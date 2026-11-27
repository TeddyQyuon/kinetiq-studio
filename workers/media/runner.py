from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from kinetiq_agent_core import WorkerClient

from .align_whisperx import align_words
from .tts_kokoro import synthesize

CAPABILITIES = ["tts", "align", "transcode"]


def create_proxy(source: Path, destination: Path) -> tuple[int, int]:
    """Encode only the video track. Narration lives on a separate timeline track."""
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-filter_threads",
            "2",
            "-threads",
            "2",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-vf",
            "scale=-2:540",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-g",
            "15",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-an",
            str(destination),
        ],
        check=True,
    )
    dimensions = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(destination),
        ],
        text=True,
    )
    width, height = dimensions.strip().split(",")
    return int(width), int(height)


def process_job(client: WorkerClient, job: dict, directory: Path, *, mock: bool = False) -> dict:
    job_type = job["job_type"]
    job_id = job["id"]
    payload = job["payload"]
    if job_type == "tts":
        output = directory / "scene.wav"
        voice = payload.get("voice") or {}
        duration = synthesize(
            payload["narration"],
            output,
            voice_id=voice.get("voice_id", "am_michael"),
            speed=voice.get("speed", 1.0),
            mock=mock,
        )
        return client.upload_artifact(
            job_id, output, asset_kind="audio", content_type="audio/wav", duration_ms=duration
        )

    if job_type not in {"align", "transcode"}:
        raise ValueError(f"Unsupported media job type {job_type}")
    source = directory / ("source.wav" if job_type == "align" else "master.mp4")
    if not job.get("input_download_url"):
        raise ValueError(f"{job_type} job requires a signed input download URL")
    # The URL is already signed. Only send worker credentials to worker API endpoints.
    with httpx.stream("GET", job["input_download_url"], timeout=120) as response:
        if not response.is_success:
            raise RuntimeError(f"Media input download returned HTTP {response.status_code}")
        with source.open("wb") as out:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                out.write(chunk)

    if job_type == "align":
        words = align_words(payload["narration"], source, mock=mock)
        output = directory / "words.json"
        output.write_text(json.dumps({"words": words}, ensure_ascii=False), encoding="utf-8")
        return client.upload_artifact(job_id, output, asset_kind="words", content_type="application/json")

    output = directory / "proxy.mp4"
    width, height = create_proxy(source, output)
    return client.upload_artifact(
        job_id, output, asset_kind="proxy", content_type="video/mp4", width=width, height=height
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Kinetiq CPU media worker")
    parser.add_argument("--mock", action="store_true", help="Use deterministic, model-free synthesis and alignment")
    args = parser.parse_args()
    client = WorkerClient(os.environ["API_BASE_URL"], os.environ["WORKER_TOKEN"])
    client.register(provider="local", profile="media", gpu_model=None, vram_gb=None, capabilities=CAPABILITIES)
    client.start_heartbeat()
    try:
        while True:
            job = client.claim_job(CAPABILITIES)
            if not job:
                time.sleep(3)
                continue
            try:
                with tempfile.TemporaryDirectory(prefix="kinetiq-media-") as directory:
                    with client.keep_lease(job["id"]):
                        artifact = process_job(client, job, Path(directory), mock=args.mock)
                    client.complete_job(job["id"], [artifact], gpu_seconds=0)
            except Exception as exc:  # noqa: BLE001 - isolate job/model failures from the polling loop.
                client.fail_job(job["id"], str(exc))
    finally:
        client.stop_heartbeat()
        client.client.close()


if __name__ == "__main__":
    main()
