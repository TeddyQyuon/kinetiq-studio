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
import websocket

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
    except Exception:
        pass
    return "CPU-Mock", 0


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


def execute_comfy_graph(
    client: WorkerClient,
    comfy_url: str,
    comfy_output_dir: Path,
    job_id: str,
    graph: dict[str, Any],
) -> Path:
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
            if ev.get("type") == "progress":
                d = ev["data"]
                pct = int(100 * d["value"] / max(d["max"], 1))
                client.report_progress(job_id, pct, step=d["value"], total_steps=d["max"])
            elif ev.get("type") == "executing" and ev["data"].get("node") is None and ev["data"].get("prompt_id") == prompt_id:
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
                    except Exception:
                        pass
                    break
                time.sleep(3)
                continue

            last_work = time.time()
            job_id = job["id"]
            t0 = time.time()
            try:
                if args.mock:
                    for pct in (25, 65, 100):
                        time.sleep(0.5)
                        wc.report_progress(job_id, pct)

                    if job["job_type"] == "keyframe_qwen":
                        kf = work_dir / f"{job['scene_id'] or job_id}_kf.png"
                        kf.write_bytes(TINY_PNG)
                        art = wc.upload_artifact(
                            job_id, kf, asset_kind="keyframe", content_type="image/png", width=1920, height=1080
                        )
                        wc.complete_job(job_id, [art], gpu_seconds=time.time() - t0)
                        kf.unlink(missing_ok=True)
                    else:
                        master = work_dir / f"{job['scene_id'] or job_id}_master.mp4"
                        proxy = work_dir / f"{job['scene_id'] or job_id}_proxy.mp4"
                        master.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isomMOCKVIDEO")
                        proxy.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isomMOCKPROXY")
                        a1 = wc.upload_artifact(job_id, master, asset_kind="master", content_type="video/mp4", width=1920, height=1088)
                        a2 = wc.upload_artifact(job_id, proxy, asset_kind="proxy", content_type="video/mp4", width=960, height=544)
                        wc.complete_job(job_id, [a1, a2], gpu_seconds=time.time() - t0)
                        master.unlink(missing_ok=True)
                        proxy.unlink(missing_ok=True)
                else:
                    if job.get("input_download_url") and job.get("input_filename"):
                        comfy_input.mkdir(parents=True, exist_ok=True)
                        with httpx.Client(timeout=60) as hc:
                            r = hc.get(job["input_download_url"])
                            r.raise_for_status()
                            (comfy_input / job["input_filename"]).write_bytes(r.content)

                    out_path = execute_comfy_graph(wc, args.comfy_url, comfy_output, job_id, job["comfy_graph"])
                    if job["job_type"] == "keyframe_qwen":
                        art = wc.upload_artifact(job_id, out_path, asset_kind="keyframe", content_type="image/png")
                        wc.complete_job(job_id, [art], gpu_seconds=time.time() - t0)
                    else:
                        kind = "upscaled_4k" if job["job_type"].startswith("upscale") else "master"
                        proxy_path = work_dir / f"{out_path.stem}_proxy.mp4"
                        make_540p_proxy(out_path, proxy_path)
                        a_master = wc.upload_artifact(job_id, out_path, asset_kind=kind, content_type="video/mp4")
                        a_proxy = wc.upload_artifact(job_id, proxy_path, asset_kind="proxy", content_type="video/mp4")
                        wc.complete_job(job_id, [a_master, a_proxy], gpu_seconds=time.time() - t0)
                        proxy_path.unlink(missing_ok=True)
                    out_path.unlink(missing_ok=True)
            except Exception as exc:
                wc.fail_job(job_id, str(exc))
            finally:
                if not args.mock:
                    try:
                        httpx.post(f"{args.comfy_url}/free", json={"unload_models": False, "free_memory": True}, timeout=5)
                    except Exception:
                        pass
    finally:
        wc.stop_heartbeat()


if __name__ == "__main__":
    main()
