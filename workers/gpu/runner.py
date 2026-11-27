from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from kinetiq_agent_core import WorkerClient

PROFILE_CAPS: dict[str, list[str]] = {
    "ltx25_pro": ["keyframe_qwen", "i2v_ltx25"],
    "wan22_fast": ["keyframe_qwen", "i2v_wan22"],
    "upscale_4k": ["upscale_flashvsr", "upscale_seedvr2"],
}

# Minimal valid 1x1 PNG for --mock dry-run pipeline testing without a GPU
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def detect_gpu() -> tuple[str, int]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            text=True,
            timeout=5,
        ).strip()
        if out:
            name, mem_mb = [x.strip() for x in out.splitlines()[0].split(",", 1)]
            return name, max(1, round(int(mem_mb) / 1024))
    except (OSError, subprocess.SubprocessError, ValueError):
        return "Unknown", 0
    return "Unknown", 0


def make_540p_proxy(master_mp4: Path, proxy_mp4: Path) -> Path:
    """Create a lightweight 540p fast-seek MP4 proxy for smooth browser timeline editing."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(master_mp4),
        "-vf",
        "scale=-2:540",
        "-c:v",
        "libx264",
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
        str(proxy_mp4),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proxy_mp4


def make_mock_clip(path: Path, job: dict[str, Any]) -> None:
    payload = job.get("payload") or {}
    width, height = (3840, 2160) if job["job_type"].startswith("upscale") else (1920, 1080)
    if payload.get("aspect") == "9:16":
        width, height = height, width
    seconds = max(1 / 24, payload.get("duration_ms", 1000) / 1000)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=s={width}x{height}:r=24",
         "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
        check=True,
    )


class ComfyOutOfMemory(RuntimeError):
    def __init__(self, prompt_id: str, data: dict):
        super().__init__("ComfyUI ran out of GPU memory")
        self.prompt_id = prompt_id
        self.node_type = str(data.get("node_type", ""))[:80]


def cleanup_comfy(comfy_url: str, oom: ComfyOutOfMemory | None = None) -> dict[str, bool]:
    requests = []
    if oom:
        # Remove only this worker's prompt. Other clients may share the queue.
        requests.append(("queue", {"delete": [oom.prompt_id]}))
    requests.append(("free", {"unload_models": oom is not None, "free_memory": True}))
    result = {}
    for endpoint, body in requests:
        try:
            httpx.post(f"{comfy_url}/{endpoint}", json=body, timeout=5).raise_for_status()
            result[endpoint] = True
        except httpx.HTTPError:
            print(f"ComfyUI /{endpoint} cleanup unavailable.")
            result[endpoint] = False
    return result


def execute_comfy_graph(
    client: WorkerClient,
    comfy_url: str,
    comfy_output_dir: Path,
    job_id: str,
    graph: dict[str, Any],
) -> Path:
    import websocket

    cid = str(uuid.uuid4())
    ws_url = comfy_url.replace("http://", "ws://").replace("https://", "wss://") + f"/ws?clientId={cid}"
    ws = websocket.create_connection(ws_url)
    try:
        with httpx.Client(timeout=30) as hc:
            res = hc.post(f"{comfy_url}/prompt", json={"prompt": graph, "client_id": cid})
            res.raise_for_status()
            prompt_id = res.json()["prompt_id"]

        while True:
            msg = ws.recv()
            if isinstance(msg, bytes):
                continue
            ev = json.loads(msg)
            if ev.get("data", {}).get("prompt_id") != prompt_id:
                continue
            if ev.get("type") in {"execution_error", "execution_interrupted"}:
                data = ev["data"]
                message = str(data.get("exception_message") or ev["type"])
                kind = str(data.get("exception_type", "")).lower()
                detail = message.lower()
                if ("outofmemoryerror" in kind or "cuda out of memory" in detail
                        or "cuda error: out of memory" in detail):
                    raise ComfyOutOfMemory(prompt_id, data)
                raise RuntimeError(message)
            if ev.get("type") == "progress":
                d = ev["data"]
                pct = int(100 * d["value"] / max(d["max"], 1))
                client.report_progress(job_id, pct, step=d["value"], total_steps=d["max"])
            elif ev.get("type") == "executing" and ev["data"].get("node") is None:
                break
    finally:
        ws.close()

    with httpx.Client(timeout=30) as hc:
        hist = hc.get(f"{comfy_url}/history/{prompt_id}").json()[prompt_id]["outputs"]

    for node_out in hist.values():
        for key in ("gifs", "videos", "images"):
            if key in node_out:
                info = node_out[key][0]
                return comfy_output_dir / info.get("subfolder", "") / info["filename"]
    raise RuntimeError("ComfyUI finished without output media")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kinetiq Studio GPU Worker Runner")
    parser.add_argument("--profile", default="ltx25_pro", choices=list(PROFILE_CAPS.keys()))
    parser.add_argument("--provider", default="google_colab", choices=["google_colab", "runpod", "local"])
    parser.add_argument("--comfy-url", default=os.getenv("COMFY_URL", "http://127.0.0.1:8188"))
    parser.add_argument("--comfy-dir", default=os.getenv("COMFY_DIR", "/content/ComfyUI"))
    parser.add_argument("--idle-minutes", type=int, default=15, help="Auto-stop after N idle minutes on Colab")
    parser.add_argument("--mock", action="store_true", help="Simulate GPU output without ComfyUI")
    args = parser.parse_args()

    api_url = os.environ["API_BASE_URL"]
    token = os.environ["WORKER_TOKEN"]
    caps = PROFILE_CAPS[args.profile]
    gpu_model, vram_gb = ("Mock-L4-24GB", 24) if args.mock else detect_gpu()

    work_dir = Path("./worker_tmp").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    comfy_input = Path(args.comfy_dir) / "input"
    comfy_output = Path(args.comfy_dir) / "output"

    wc = WorkerClient(api_url, token)
    wid = wc.register(
        provider=args.provider,
        profile=args.profile,
        gpu_model=gpu_model,
        vram_gb=vram_gb,
        capabilities=caps,
    )
    wc.start_heartbeat(15)
    print(f"Registered worker {wid} ({gpu_model}, {vram_gb}GB) | profile={args.profile} | mock={args.mock}")

    last_work = time.time()
    try:
        while True:
            job = wc.claim_job(caps)
            if not job:
                if args.provider == "google_colab" and (time.time() - last_work) > args.idle_minutes * 60:
                    print(f"Idle for {args.idle_minutes}m — shutting down to save Colab Compute Units.")
                    try:
                        from google.colab import runtime  # type: ignore
                        runtime.unassign()
                    except ImportError:
                        print("Colab runtime unavailable; stopping the worker.")
                    break
                time.sleep(3)
                continue

            last_work = time.time()
            job_id = job["id"]
            t0 = time.time()
            oom_handled = False
            try:
                with wc.keep_lease(job_id):
                    is_keyframe = job["job_type"] == "keyframe_qwen"
                    if args.mock:
                        if is_keyframe:
                            out_path = work_dir / f"{job_id}.png"
                            out_path.write_bytes(TINY_PNG)
                        else:
                            out_path = work_dir / f"{job_id}.mp4"
                            make_mock_clip(out_path, job)
                    else:
                        if job.get("input_download_url") and job.get("input_filename"):
                            comfy_input.mkdir(parents=True, exist_ok=True)
                            filename = job["input_filename"]
                            if Path(filename).name != filename:
                                raise ValueError("Expected a plain ComfyUI input filename")
                            with httpx.stream("GET", job["input_download_url"], timeout=60, follow_redirects=True) as r:
                                r.raise_for_status()
                                with (comfy_input / filename).open("wb") as dest:
                                    for chunk in r.iter_bytes():
                                        dest.write(chunk)
                        out_path = execute_comfy_graph(wc, args.comfy_url, comfy_output, job_id, job["comfy_graph"])

                    if is_keyframe:
                        artifacts = [wc.upload_artifact(job_id, out_path, asset_kind="keyframe", content_type="image/png")]
                    else:
                        kind = "upscaled_4k" if job["job_type"].startswith("upscale") else "master"
                        proxy_path = work_dir / f"{out_path.stem}_proxy.mp4"
                        make_540p_proxy(out_path, proxy_path)
                        a_master = wc.upload_artifact(job_id, out_path, asset_kind=kind, content_type="video/mp4")
                        a_proxy = wc.upload_artifact(job_id, proxy_path, asset_kind="proxy", content_type="video/mp4")
                        artifacts = [a_master, a_proxy]
                wc.complete_job(job_id, artifacts, gpu_seconds=time.time() - t0)
                if not is_keyframe:
                    proxy_path.unlink(missing_ok=True)
                out_path.unlink(missing_ok=True)
            except ComfyOutOfMemory as exc:
                cleanup = cleanup_comfy(args.comfy_url, exc)
                oom_handled = True
                # The control plane persists JobFailRequest.error as a string.
                wc.fail_job(job_id, json.dumps({
                    "code": "cuda_oom", "message": str(exc), "node_type": exc.node_type,
                    "profile": args.profile, "vram_gb": vram_gb, "cleanup": cleanup,
                }, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001 - isolate failed jobs and continue polling.
                wc.fail_job(job_id, str(exc))
            finally:
                last_work = time.time()
                if not args.mock and not oom_handled:
                    cleanup_comfy(args.comfy_url)
    finally:
        wc.stop_heartbeat()
        wc.client.close()


if __name__ == "__main__":
    main()
