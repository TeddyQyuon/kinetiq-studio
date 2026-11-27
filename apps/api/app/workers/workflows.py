from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app.timeline.schemas import VisualSceneItem

WORKFLOWS_DIR = Path(__file__).resolve().parent / "workflows"

PROFILE_CAPABILITIES: dict[str, list[str]] = {
    "ltx25_pro": ["keyframe_qwen", "i2v_ltx25"],
    "wan22_fast": ["keyframe_qwen", "i2v_wan22"],
    "upscale_4k": ["upscale_flashvsr", "upscale_seedvr2"],
    "media": ["tts", "align", "transcode"],
    "render": ["render"],
}


def compute_dit_dimensions(aspect: str, target_res: str) -> dict[str, int]:
    """Return DiT-safe dimensions divisible by 32/64."""
    if aspect == "9:16":
        return {
            "base_width": 544,
            "base_height": 960,
            "width": 1088,
            "height": 1920,
            "out_width": 2160 if target_res == "4k" else 1080,
            "out_height": 3840 if target_res == "4k" else 1920,
        }
    return {
        "base_width": 960,
        "base_height": 544,
        "width": 1920,
        "height": 1088,
        "out_width": 3840 if target_res == "4k" else 1920,
        "out_height": 2160 if target_res == "4k" else 1080,
    }


def _replace_tokens(node: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        return {k: _replace_tokens(v, replacements) for k, v in node.items()}
    if isinstance(node, list):
        return [_replace_tokens(v, replacements) for v in node]
    if isinstance(node, str) and node in replacements:
        return replacements[node]
    return node


def build_comfy_graph(
    *,
    job_type: str,
    scene: VisualSceneItem,
    aspect: str,
    target_res: str,
    style_look: str,
    negative_prompt: str,
    input_filename: str | None = None,
    gpu_model: str | None = None,
    vram_gb: int | None = None,
) -> dict[str, Any]:
    dims = compute_dit_dimensions(aspect, target_res)
    duration_sec = max(3.0, (scene.end_ms - scene.start_ms) / 1000.0)
    frames = min(241, max(49, int(duration_sec * 24) + 1))
    seed = scene.seed if scene.seed is not None else 424242
    low_vram = "L4" in (gpu_model or "").upper().split() or (vram_gb or 0) < 38

    if job_type == "keyframe_qwen":
        tpl_name = "qwen_image_1080p.json"
        prompt_text = (scene.prompt.qwen_image if scene.prompt else None) or scene.narration
        prompt_text = f"{prompt_text}. Style: {style_look}."
    elif job_type == "i2v_wan22":
        tpl_name = "wan22_i2v_fast.json"
        prompt_text = (scene.prompt.wan22 if scene.prompt else None) or scene.narration
    elif job_type == "upscale_seedvr2":
        tpl_name = "seedvr2_4k.json"
        prompt_text = ""
    elif job_type == "upscale_flashvsr":
        tpl_name = "flashvsr_4k.json"
        prompt_text = ""
    else:
        tpl_name = "ltx25_i2v_1080p.json"
        prompt_text = (scene.prompt.ltx25 if scene.prompt else None) or scene.narration
        prompt_text = f"{prompt_text}. Camera: {scene.camera or 'slow_dolly_in'}. Style: {style_look}."

    raw = json.loads((WORKFLOWS_DIR / tpl_name).read_text(encoding="utf-8"))
    replacements: dict[str, Any] = {
        "__PROMPT__": prompt_text,
        "__NEGATIVE_PROMPT__": negative_prompt,
        "__WIDTH__": dims["width"],
        "__HEIGHT__": dims["height"],
        "__BASE_WIDTH__": dims["base_width"],
        "__BASE_HEIGHT__": dims["base_height"],
        "__FRAMES__": frames,
        "__SEED__": seed,
        "__INPUT_IMAGE__": input_filename or "placeholder_keyframe.png",
        "__INPUT_VIDEO__": input_filename or "placeholder_clip.mp4",
        "__FILENAME_PREFIX__": f"{scene.id}_{job_type}",
    }
    if job_type in {"upscale_seedvr2", "upscale_flashvsr"}:
        # Crop the DiT padding (e.g. 1920x1088) before the aspect-preserving upscale.
        master = compute_dit_dimensions(aspect, "1080p")
        replacements["__MASTER_WIDTH__"] = master["out_width"]
        replacements["__MASTER_HEIGHT__"] = master["out_height"]
    if job_type == "upscale_flashvsr":
        replacements["__SEED__"] = max(1, seed % (2**64))
    elif job_type == "upscale_seedvr2":
        replacements.update({
            "__FRAME_BATCH__": 5 if low_vram else 17,
            "__VAE_TILE_SIZE__": 512 if low_vram else 1024,
            "__BLOCKS_TO_SWAP__": 24 if low_vram else 0,
            "__SEED__": seed % (2**32),
        })
    graph = _replace_tokens(copy.deepcopy(raw), replacements)

    # Fallback cleanly to T2V when LTX-2.5 has no input keyframe yet
    if tpl_name == "ltx25_i2v_1080p.json" and not input_filename:
        graph.pop("6", None)
        graph.pop("7", None)
        graph["9"] = {
            "class_type": "EmptyLTXVLatentVideo",
            "inputs": {
                "width": dims["base_width"],
                "height": dims["base_height"],
                "length": frames,
                "batch_size": 1,
            },
        }
        graph["10"]["inputs"]["positive"] = ["8", 0]
        graph["10"]["inputs"]["negative"] = ["8", 1]
        graph["10"]["inputs"]["latent_image"] = ["9", 0]

    # Keep the queued model/capability when Colab assigns an L4. Existing
    # generation templates already load FP8 DiTs; tile their VAE decode too.
    if low_vram and (gpu_model or vram_gb):
        for node in graph.values():
            if node["class_type"] == "VAEDecode":
                node["class_type"] = "VAEDecodeTiled"
                node["inputs"].update(tile_size=512, overlap=64, temporal_size=16, temporal_overlap=4)

    return graph
