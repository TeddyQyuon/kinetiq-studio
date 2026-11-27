"""Render an immutable timeline with the studio composition, then master and publish it."""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from kinetiq_agent_core import WorkerClient

ENTRY_POINT = Path(__file__).resolve().with_name("composition.tsx")
REPO = ENTRY_POINT.parents[2]
SIZES = {"720p": (1280, 720), "1080p": (1920, 1080), "4k": (3840, 2160)}


def prepare_document(document: dict, resolution: str) -> dict:
    document = copy.deepcopy(document)
    width, height = SIZES[resolution]
    if document["format"]["aspect"] == "9:16":
        width, height = height, width
    document["format"].update(target=resolution, width=width, height=height)
    for scene in document["tracks"]["visual"]:
        # The shared player prefers proxy. Give the export its own full-res references.
        assets = scene.get("assets") or {}
        assets.pop("proxy", None)
        if resolution == "4k" and assets.get("upscaled_4k"):
            assets["master"] = assets["upscaled_4k"]
        if scene["kind"] == "ai_video" and not assets.get("master"):
            raise ValueError(f"Missing full-resolution clip for {scene['id']}")
        if scene["kind"] == "image_motion" and not (assets.get("master") or assets.get("keyframe")):
            raise ValueError(f"Missing image for {scene['id']}")
        scene["assets"] = assets
    return document


def download_inputs(document: dict, urls: dict, scene_words: dict, directory: Path) -> dict[str, Path]:
    tracks = document["tracks"]
    assets = {}
    for scene in tracks["visual"]:
        refs = scene["assets"]
        if scene["kind"] != "diagram":
            if refs.get("master"):
                assets[refs["master"]] = ".mp4"
            else:
                assets[refs["keyframe"]] = ".png"
    for clip in tracks["voice"]:
        if clip.get("asset"):
            assets[clip["asset"]] = ".wav"
    if music := tracks["music"].get("asset"):
        assets[music] = ".audio"
    if words := tracks["captions"].get("words_asset"):
        assets[words] = ".json"
    assets.update({asset: ".json" for asset in scene_words.values()})

    files = {}
    # Fetch signed inputs once before launching Chromium. Long renders can outlive their URLs.
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for i, (asset, suffix) in enumerate(assets.items()):
            path = directory / f"input-{i}{suffix}"
            with client.stream("GET", urls[asset]) as response:
                if not response.is_success:
                    raise RuntimeError(f"Download of asset {asset} returned HTTP {response.status_code}")
                with path.open("wb") as output:
                    output.writelines(response.iter_bytes())
            files[asset] = path
    return files


def _timestamp(ms: int) -> str:
    seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_captions(tracks: dict, scene_words: dict, files: dict[str, Path], output: Path) -> None:
    def read_words(asset):
        data = json.loads(files[asset].read_text(encoding="utf-8"))
        return data["words"] if isinstance(data, dict) else data

    captions = tracks["captions"]
    global_words = captions.get("words") or []
    if not global_words and captions.get("words_asset"):
        global_words = read_words(captions["words_asset"])
    cues = []
    if global_words:
        cues = [(w["start_ms"], w["end_ms"], w["word"]) for w in global_words]
    else:
        clips = {c["scene_id"]: c for c in tracks["voice"]}
        for scene in tracks["visual"]:
            clip = clips.get(scene["id"], scene)
            start, end = clip["start_ms"], clip["end_ms"]
            if asset := scene_words.get(scene["id"]):
                for w in read_words(asset):
                    cues.append((start + max(0, w["start_ms"]), min(end, start + w["end_ms"]), w["word"]))
            elif scene["narration"]:
                cues.append((start, end, scene["narration"]))

    blocks = []
    for start, end, text in sorted(cues):
        text = " ".join(text.split())
        if end > start >= 0 and text:
            blocks.append(f"{len(blocks) + 1}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}")
    # An empty subtitle track still has a final newline and can be uploaded.
    output.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def master_audio_video(
    picture: Path, tracks: dict, files: dict[str, Path], duration: float, output: Path
) -> None:
    inputs = ["-i", str(picture)]
    filters = []
    labels = []
    windows = []
    for clip in tracks["voice"]:
        if not clip.get("asset"):
            continue
        idx = len(labels) + 1
        start, end = clip["start_ms"], clip["end_ms"]
        if end <= start or start < 0:
            raise ValueError("Invalid narration clip interval")
        inputs += ["-i", str(files[clip["asset"]])]
        filters.append(
            f"[{idx}:a]atrim=duration={(end-start)/1000},asetpts=PTS-STARTPTS,"
            f"adelay={start}:all=1[a{idx}]"
        )
        labels.append(f"[a{idx}]")
        windows.append(f"between(t,{start/1000},{end/1000})")

    music = tracks["music"]
    if music.get("asset"):
        idx = len(labels) + 1
        inputs += ["-stream_loop", "-1", "-i", str(files[music["asset"]])]
        gain = f"volume={float(music.get('volume', 1.0))}"
        if windows:
            gain += ",volume=-26dB:enable='" + "+".join(windows) + "'"
        filters.append(f"[{idx}:a]atrim=duration={duration},{gain}[a{idx}]")
        labels.append(f"[a{idx}]")
    if not labels:
        inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        labels.append("[1:a]")

    mix = "".join(labels) + f"amix=inputs={len(labels)}:normalize=0,apad,atrim=duration={duration},"
    norm = "loudnorm=I=-14:TP=-1.5:LRA=11"
    graph = ";".join([*filters, mix + norm])
    base = ["ffmpeg", "-nostdin", "-hide_banner", "-y", *inputs, "-filter_complex"]
    # Measure the actual mix for integrated normalization. Silence has no finite loudness.
    analysis = subprocess.run(
        [*base, graph + ":print_format=json[mix]", "-map", "[mix]", "-f", "null", "-"],
        check=True, stderr=subprocess.PIPE, text=True,
    )
    stats, _ = json.JSONDecoder().raw_decode(analysis.stderr[analysis.stderr.rindex("{"):])
    keys = {"input_i": "measured_I", "input_lra": "measured_LRA", "input_tp": "measured_TP",
            "input_thresh": "measured_thresh", "target_offset": "offset"}
    if all(math.isfinite(float(stats[k])) for k in keys):
        graph += ":linear=true" + "".join(f":{option}={stats[key]}" for key, option in keys.items())
    subprocess.run([
        *base, graph + "[mix]", "-map", "0:v:0", "-map", "[mix]",
        "-c:v", "libx264", "-profile:v", "high", "-crf", "17", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        str(output),
    ], check=True)


def render_job(client: WorkerClient, job: dict) -> None:
    payload = job["payload"]
    job_id = job["id"]
    resolution = payload["resolution"]
    doc = prepare_document(payload["document"], resolution)
    tracks = doc["tracks"]
    fps = doc["format"]["fps"]
    end_ms = max(item["end_ms"] for track in ("visual", "voice", "overlays") for item in tracks[track])
    frames = math.ceil(end_ms * fps / 1000)
    scene_words = payload.get("scene_words", {})

    with tempfile.TemporaryDirectory(prefix="kinetiq-render-") as tmp:
        root = Path(tmp)
        with client.keep_lease(job_id):
            public = root / "media"
            public.mkdir()
            files = download_inputs(doc, payload["media_urls"], scene_words, public)
            captions = root / "captions.srt"
            write_captions(tracks, scene_words, files, captions)
            props = root / "props.json"
            props.write_text(json.dumps({
                "document": doc, "mediaFiles": {k: p.name for k, p in files.items()}, "durationInFrames": frames,
            }), encoding="utf-8")
            picture = root / "picture.mp4"
            args = [
                str(REPO / "node_modules/.bin/remotion"), "render", str(ENTRY_POINT), "EduComposition", str(picture),
                f"--props={props}", f"--public-dir={public}", "--muted", "--crf=10", "--image-format=png",
                "--concurrency=2",
            ]
            if browser := os.getenv("REMOTION_BROWSER_EXECUTABLE"):
                args.append(f"--browser-executable={browser}")
            subprocess.run(args, cwd=REPO, check=True)
            master = root / f"final_{resolution}.mp4"
            master_audio_video(picture, tracks, files, frames / fps, master)
            artifacts = [
                client.upload_artifact(job_id, master, asset_kind="export", content_type="video/mp4",
                                       width=doc["format"]["width"], height=doc["format"]["height"],
                                       duration_ms=round(frames * 1000 / fps)),
                client.upload_artifact(job_id, captions, asset_kind="srt", content_type="application/x-subrip"),
            ]
        client.complete_job(job_id, artifacts, gpu_seconds=0)


def main() -> None:
    client = WorkerClient(os.environ["API_BASE_URL"], os.environ["WORKER_TOKEN"])
    client.register(provider="local", profile="render", gpu_model=None, vram_gb=None, capabilities=["render"])
    client.start_heartbeat()
    try:
        while True:
            job = client.claim_job(["render"])
            if job is None:
                time.sleep(3)
                continue
            try:
                render_job(client, job)
            except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError, httpx.HTTPError) as exc:
                client.fail_job(job["id"], str(exc))
    finally:
        client.stop_heartbeat()
        client.client.close()


if __name__ == "__main__":
    main()
