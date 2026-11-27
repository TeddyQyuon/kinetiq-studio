from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.director.schemas import PlannedStoryboard
from app.director.tools import COPILOT_TOOL_SPECS
from app.models import AIRun
from app.qa.schemas import QAScores

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _log_ai_run(
    db: Session,
    *,
    project_id: str,
    provider: str,
    model: str,
    task: str,
    prompt_text: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int = 0,
    outcome: str = "ok",
) -> None:
    db.add(
        AIRun(
            project_id=project_id,
            provider=provider,
            model=model,
            task=task,
            prompt_hash=hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:32],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            outcome=outcome,
        )
    )
    db.flush()


class GeminiProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def generate_storyboard(
        self,
        db: Session,
        *,
        project_id: str,
        topic: str,
        aspect: str,
        duration_preset: str,
        script_override: str | None,
    ) -> PlannedStoryboard:
        target_scenes = 9 if duration_preset == "reel_60s" else (24 if duration_preset == "lesson_5m" else 40)
        safe_zone_rule = (
            "Keep focal subjects centered in the middle 55% of frame; keep bottom 25% and right 15% clear for TikTok/Reels UI."
            if aspect == "9:16"
            else "Balanced 16:9 documentary framing with clean negative space for lower-third captions."
        )
        prompt = (
            f"You are an elite faceless educational video director (Vox / Kurzgesagt style).\n"
            f"Topic: {topic}\n"
            f"Aspect: {aspect} | Target scenes: ~{target_scenes}\n"
            f"Safe zone rule: {safe_zone_rule}\n"
            + (f"Use this exact script and split into scenes:\n{script_override}\n" if script_override else "")
            + "Return ONLY valid JSON matching PlannedStoryboard with alternating 'ai_video', 'diagram' (BarCompare/StepBreakdown/StatCallout), and 'image_motion' scenes. "
            "Write rich screenplay-prose prompts in ltx25_prompt, physics motion in wan22_prompt, and crisp still composition in qwen_image_prompt."
        )

        t0 = time.time()
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.6},
        }
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                f"{GEMINI_API_BASE}/{self.model}:generateContent",
                params={"key": self.api_key},
                json=body,
            )
            res.raise_for_status()
            data = res.json()

        latency = int((time.time() - t0) * 1000)
        usage = data.get("usageMetadata", {})
        _log_ai_run(
            db,
            project_id=project_id,
            provider="gemini",
            model=self.model,
            task="plan_storyboard",
            prompt_text=prompt,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0)),
            latency_ms=latency,
        )
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        return PlannedStoryboard.model_validate_json(raw_text)

    async def vision_score_keyframe(
        self,
        db: Session,
        *,
        project_id: str,
        image_bytes: bytes,
        narration: str,
        style_look: str,
        safe_zone: str,
    ) -> QAScores:
        prompt = (
            f"Grade this generated educational video keyframe (0-10 each) as JSON:\n"
            f"- narration_match: matches '{narration}'\n"
            f"- artifact_free: free of garbled text, extra limbs, distortion\n"
            f"- style_consistency: matches '{style_look}'\n"
            f"- safe_zone: clean framing for '{safe_zone}'\n"
            f"Include 'reason', optional 'suggested_fix', and boolean 'prefer_diagram' (true if text/numbers are better as a clean chart)."
        )
        t0 = time.time()
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(image_bytes).decode("ascii")}},
                    ],
                }
            ],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        }
        async with httpx.AsyncClient(timeout=45) as client:
            res = await client.post(
                f"{GEMINI_API_BASE}/{self.model}:generateContent",
                params={"key": self.api_key},
                json=body,
            )
            res.raise_for_status()
            data = res.json()

        usage = data.get("usageMetadata", {})
        _log_ai_run(
            db,
            project_id=project_id,
            provider="gemini",
            model=self.model,
            task="vision_qa_keyframe",
            prompt_text=prompt,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0)),
            latency_ms=int((time.time() - t0) * 1000),
        )
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        return QAScores.model_validate_json(raw)

    async def copilot_propose(
        self,
        db: Session,
        *,
        project_id: str,
        timeline_summary: dict[str, Any],
        user_message: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        prompt = (
            "You are the AI Video Editing Copilot for Kinetiq Studio.\n"
            f"Current timeline summary:\n{json.dumps(timeline_summary)}\n\n"
            f"User request: {user_message}\n\n"
            "Propose tool calls to edit the timeline safely. Keep explanations concise."
        )
        t0 = time.time()
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"functionDeclarations": COPILOT_TOOL_SPECS}],
        }
        async with httpx.AsyncClient(timeout=45) as client:
            res = await client.post(
                f"{GEMINI_API_BASE}/{self.model}:generateContent",
                params={"key": self.api_key},
                json=body,
            )
            res.raise_for_status()
            data = res.json()

        usage = data.get("usageMetadata", {})
        _log_ai_run(
            db,
            project_id=project_id,
            provider="gemini",
            model=self.model,
            task="copilot_edit",
            prompt_text=prompt,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0)),
            latency_ms=int((time.time() - t0) * 1000),
        )

        parts = data["candidates"][0]["content"].get("parts", [])
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for p in parts:
            if "text" in p:
                text_parts.append(p["text"])
            if "functionCall" in p:
                tool_calls.append(p["functionCall"])

        return ("\n".join(text_parts) or "Prepared timeline edit commands."), tool_calls


def convert_tool_calls_to_commands(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map Gemini/MCP flat tool calls into strict TimelineCommand dicts."""
    commands: list[dict[str, Any]] = []
    for call in tool_calls[:25]:
        name = call.get("name")
        args = dict(call.get("args") or {})
        if name == "insert_diagram":
            commands.append(
                {
                    "type": "insert_diagram",
                    "at_ms": int(args.get("at_ms", 0)),
                    "duration_ms": int(args.get("duration_ms", 6000)),
                    "scene_id": f"scn_{uuid.uuid4().hex[:6]}",
                    "narration": str(args.get("narration", "")),
                    "template": args.get("template", "BarCompare"),
                    "props": {
                        "title": args.get("title", "Comparison"),
                        "labels": args.get("labels"),
                        "values": args.get("values"),
                        "steps": args.get("steps"),
                        "stat_value": args.get("stat_value"),
                        "stat_label": args.get("stat_label"),
                    },
                }
            )
        elif name == "convert_scene_to_diagram":
            commands.append(
                {
                    "type": "convert_scene_to_diagram",
                    "scene_id": args["scene_id"],
                    "template": args.get("template", "StepBreakdown"),
                    "props": {
                        "title": args.get("title", "Key Breakdown"),
                        "labels": args.get("labels"),
                        "values": args.get("values"),
                        "steps": args.get("steps"),
                    },
                }
            )
        elif name == "update_captions":
            commands.append({"type": "update_captions", "patch": args})
        elif name in {"trim_scene", "edit_narration", "regenerate_clip", "tighten_pauses", "mix_audio"}:
            commands.append({"type": name, **args})
    return commands
