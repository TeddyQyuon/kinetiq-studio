from __future__ import annotations

from typing import Any

# Canonical tool definitions shared across Gemini Function Calling, Copilot, and Antigravity MCP.
COPILOT_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "review_draft",
        "description": "Critique pacing, hook retention and diagram density across the whole current timeline.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "trim_scene",
        "description": "Adjust the start_ms and end_ms boundaries of an existing scene.",
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string"},
                "start_ms": {"type": "integer"},
                "end_ms": {"type": "integer"},
            },
            "required": ["scene_id", "start_ms", "end_ms"],
        },
    },
    {
        "name": "edit_narration",
        "description": "Rewrite the English voiceover script for a specific scene (re-voices only that scene).",
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string"},
                "narration": {"type": "string"},
            },
            "required": ["scene_id", "narration"],
        },
    },
    {
        "name": "regenerate_clip",
        "description": "Queue a single scene to Colab GPU for new keyframe/video generation with an improved prompt, model, or camera motion.",
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string"},
                "prompt": {"type": "string"},
                "model": {"type": "string", "enum": ["ltx25_fp8", "wan22_i2v", "qwen_image"]},
                "camera": {
                    "type": "string",
                    "enum": ["static", "slow_dolly_in", "aerial_pullback", "cinematic_orbit", "macro_pan"],
                },
            },
            "required": ["scene_id"],
        },
    },
    {
        "name": "insert_diagram",
        "description": "Insert a native 1080p/4K Remotion educational diagram scene (BarCompare, StepBreakdown, TimelineFlow, or StatCallout) at a timestamp.",
        "parameters": {
            "type": "object",
            "properties": {
                "at_ms": {"type": "integer"},
                "duration_ms": {"type": "integer"},
                "narration": {"type": "string"},
                "template": {"type": "string", "enum": ["BarCompare", "StepBreakdown", "TimelineFlow", "StatCallout"]},
                "title": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "values": {"type": "array", "items": {"type": "number"}},
                "steps": {"type": "array", "items": {"type": "string"}},
                "stat_value": {"type": "string"},
                "stat_label": {"type": "string"},
            },
            "required": ["at_ms", "duration_ms", "narration", "template", "title"],
        },
    },
    {
        "name": "convert_scene_to_diagram",
        "description": "Convert an AI scene into a crisp native Remotion educational diagram (0 GPU cost).",
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string"},
                "template": {"type": "string", "enum": ["BarCompare", "StepBreakdown", "TimelineFlow", "StatCallout"]},
                "title": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "values": {"type": "array", "items": {"type": "number"}},
                "steps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["scene_id", "template", "title"],
        },
    },
    {
        "name": "tighten_pauses",
        "description": "Remove dead air between scenes for higher TikTok/Reels retention.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_gap_ms": {"type": "integer"},
            },
            "required": ["max_gap_ms"],
        },
    },
    {
        "name": "update_captions",
        "description": "Change kinetic caption style, accent color, or vertical placement.",
        "parameters": {
            "type": "object",
            "properties": {
                "style": {"type": "string", "enum": ["kinetic_highlight", "clean_subtitle", "bold_reels"]},
                "accent_color": {"type": "string"},
                "position": {"type": "string", "enum": ["bottom", "center_lower"]},
            },
        },
    },
    {
        "name": "mix_audio",
        "description": "Adjust background music mood, ducking level (dB), or narration speed.",
        "parameters": {
            "type": "object",
            "properties": {
                "mood": {"type": "string"},
                "duck_db": {"type": "number"},
                "voice_speed": {"type": "number"},
            },
        },
    },
]
