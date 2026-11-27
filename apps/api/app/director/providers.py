from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ValidationFailedError
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


@dataclass
class Reply:
    text: str
    calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class BrainProvider(Protocol):
    name: str
    model: str
    supports_images: bool = True

    async def request(self, prompt: str, images: list[bytes], *, tools: bool) -> Reply: ...

    async def run(
        self,
        db: Session,
        project_id: str,
        task: str,
        prompt: str,
        images: list[bytes] | None = None,
        *,
        tools: bool = False,
    ) -> Reply:
        start = time.monotonic()
        reply = await self.request(prompt, images or [], tools=tools)
        _log_ai_run(
            db,
            project_id=project_id,
            provider=self.name,
            model=self.model,
            task=task,
            prompt_text=prompt,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            latency_ms=int((time.monotonic() - start) * 1000),
        )
        return reply

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
        count = {"reel_60s": 9, "lesson_5m": 24, "lesson_10m": 40}[duration_preset]
        framing = (
            "Keep subjects in the middle 55%, bottom 25% and right 15% clear."
            if aspect == "9:16"
            else "Leave negative space for lower-third captions."
        )
        prompt = (
            f"Direct a faceless educational video on {topic}, {aspect}, about {count} scenes. {framing} "
            "Alternate video, diagrams and image motion. Write cinematic camera/lighting prompts for LTX, "
            "physical motion for Wan, and still compositions for Qwen. "
            f"Return only JSON matching {json.dumps(PlannedStoryboard.model_json_schema())}."
        )
        if script_override:
            prompt += f"\nUse this exact script and split it into scenes:\n{script_override}"
        reply = await self.run(db, project_id, "plan_storyboard", prompt)
        return PlannedStoryboard.model_validate_json(reply.text)

    async def vision_score_keyframe(
        self, db: Session, *, project_id: str, image_bytes: bytes, narration: str, style_look: str, safe_zone: str
    ) -> QAScores:
        prompt = (
            f"Grade this keyframe against narration {narration!r}, style {style_look!r} and framing {safe_zone!r}. "
            "Flag garbled text, extra limbs and distortion. Suggest a diagram for numeric explanations. "
            f"Return JSON matching {json.dumps(QAScores.model_json_schema())}."
        )
        reply = await self.run(db, project_id, "vision_qa_keyframe", prompt, [image_bytes])
        return QAScores.model_validate_json(reply.text)

    async def vision_score_clip(
        self, db: Session, *, project_id: str, frames: list[bytes], narration: str, style_look: str, safe_zone: str
    ) -> QAScores:
        if len(frames) != 4:
            raise ValueError("Clip QA requires four chronological frames")
        prompt = (
            "Compare these four frames from the same shot in time order. Check for unintended shape warping, "
            "identity drift, morphing and exposure/texture flicker across frames. Distinguish intended motion. "
            "Lower artifact_free for temporal defects and name the affected frames in reason. "
            f"Narration: {narration!r}. Look: {style_look!r}. Safe zone: {safe_zone!r}. "
            f"Return only JSON matching {json.dumps(QAScores.model_json_schema())}."
        )
        reply = await self.run(db, project_id, "vision_qa_clip", prompt, frames)
        return QAScores.model_validate_json(reply.text)

    async def copilot_propose(
        self, db: Session, *, project_id: str, timeline_summary: dict[str, Any], user_message: str
    ) -> tuple[str, list[dict[str, Any]]]:
        prompt = (
            f"You are Kinetiq Studio's editing Copilot. Timeline: {json.dumps(timeline_summary)}\n"
            f"User request: {user_message}\nUse review_draft for critiques. Propose edits only if requested."
        )
        reply = await self.run(db, project_id, "copilot_edit", prompt, tools=True)
        return reply.text or "Prepared timeline tools for review.", reply.calls


def _encoded(image: bytes) -> tuple[str, str]:
    mime = "image/jpeg" if image.startswith(b"\xff\xd8") else "image/png"
    return mime, base64.b64encode(image).decode("ascii")


class GeminiProvider(BrainProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def request(self, prompt: str, images: list[bytes], *, tools: bool) -> Reply:
        parts: list[dict] = [{"text": prompt}]
        for image in images:
            mime, data = _encoded(image)
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
        body = {"contents": [{"role": "user", "parts": parts}]}
        if tools:
            body["tools"] = [{"functionDeclarations": COPILOT_TOOL_SPECS}]
        else:
            body["generationConfig"] = {"responseMimeType": "application/json"}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{GEMINI_API_BASE}/{self.model}:generateContent", headers={"x-goog-api-key": self.api_key}, json=body
            )
            response.raise_for_status()
        data = response.json()
        parts = data["candidates"][0]["content"]["parts"]
        usage = data.get("usageMetadata", {})
        return Reply(
            "\n".join(p["text"] for p in parts if "text" in p),
            [p["functionCall"] for p in parts if "functionCall" in p],
            usage.get("promptTokenCount", 0),
            usage.get("candidatesTokenCount", 0),
        )


class OpenAIProvider(BrainProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key
        self.model = model

    async def request(self, prompt: str, images: list[bytes], *, tools: bool) -> Reply:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for image in images:
            mime, data = _encoded(image)
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        body = {"model": self.model, "messages": [{"role": "user", "content": content}]}
        if tools:
            body["tools"] = [{"type": "function", "function": spec} for spec in COPILOT_TOOL_SPECS]
        else:
            body["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
            response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        calls = [c["function"] for c in message.get("tool_calls") or []]
        usage = data.get("usage", {})
        return Reply(
            message.get("content") or "",
            [{"name": c["name"], "args": json.loads(c["arguments"])} for c in calls],
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )


class ColabAIProvider(BrainProvider):
    name = "colab_ai"
    model = "colab-default"
    supports_images = False

    async def request(self, prompt: str, images: list[bytes], *, tools: bool) -> Reply:
        if images:
            raise ValidationFailedError("Colab's generate_text interface accepts text only.")
        try:
            from google.colab import ai
        except ImportError as exc:
            raise ValidationFailedError("Run colab_ai inside a Google Colab notebook runtime.") from exc
        if tools:
            prompt += (
                f"\nAvailable tools: {json.dumps(COPILOT_TOOL_SPECS)}. Return only JSON with "
                "message (string) and calls (array of {name, args})."
            )
        raw = (await asyncio.to_thread(ai.generate_text, prompt)).strip()
        if raw.startswith("\x60\x60\x60") and raw.endswith("\x60\x60\x60"):
            raw = raw[3:-3].removeprefix("json").strip()
        if tools:
            obj = json.loads(raw)
            return Reply(obj["message"], obj["calls"])
        return Reply(raw)


def get_brain_provider(mode: str, *, vision: bool = False) -> BrainProvider | None:
    settings = get_settings()
    if mode == "colab_ai":
        return None if vision else ColabAIProvider()
    if mode == "openai_api":
        if not settings.openai_api_key:
            raise ValidationFailedError("openai_api requires OPENAI_API_KEY.")
        return OpenAIProvider(settings.openai_api_key)
    if settings.gemini_api_key:
        return GeminiProvider(
            settings.gemini_api_key, settings.gemini_flash_model if vision else settings.gemini_pro_model
        )
    if settings.openai_api_key:
        return OpenAIProvider(settings.openai_api_key)
    return None


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
