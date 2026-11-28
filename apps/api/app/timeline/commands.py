from __future__ import annotations

import copy
import random

from app.core.errors import NotFoundError
from app.projects.speech import retime_speech
from app.timeline.schemas import (
    ScenePromptBundle,
    SceneQASummary,
    TimelineCommand,
    TimelineDocument,
    VisualSceneItem,
)


def _reflow(scenes: list[VisualSceneItem], gap_ms: int = 0) -> list[VisualSceneItem]:
    cursor = 0
    out: list[VisualSceneItem] = []
    for s in scenes:
        dur = max(1000, s.end_ms - s.start_ms)
        out.append(s.model_copy(update={"start_ms": cursor, "end_ms": cursor + dur}))
        cursor += dur + gap_ms
    return out


def apply_command(doc: TimelineDocument, cmd: TimelineCommand) -> tuple[TimelineDocument, float]:
    """Deterministically apply a TimelineCommand and return (updated_doc, est_cu)."""
    nxt = doc.model_copy(deep=True)
    visual = nxt.tracks.visual
    est_cu = 0.0
    retime = cmd.type in {"move_scene", "delete_scene", "insert_diagram", "tighten_pauses"}

    if cmd.type == "trim_scene":
        idx = next((i for i, s in enumerate(visual) if s.id == cmd.scene_id), None)
        if idx is None:
            raise NotFoundError(f"Scene {cmd.scene_id} not found")
        prev = visual[idx]
        prev_source_start = prev.source_start_ms or 0
        next_start = max(0, cmd.start_ms)
        next_end = max(next_start + 500, cmd.end_ms)
        next_source_start = (
            max(0, cmd.source_start_ms)
            if cmd.source_start_ms is not None
            else max(0, prev_source_start + (next_start - prev.start_ms))
        )
        prev.start_ms = next_start
        prev.end_ms = next_end
        prev.source_start_ms = next_source_start

    elif cmd.type == "split_scene":
        idx = next((i for i, s in enumerate(visual) if s.id == cmd.scene_id), None)
        if idx is None:
            raise NotFoundError(f"Scene {cmd.scene_id} not found")
        orig = visual[idx]
        split_at = min(orig.end_ms - 500, max(orig.start_ms + 500, cmd.split_at_ms))
        base_source_start = orig.source_start_ms or 0
        left = orig.model_copy(update={"end_ms": split_at, "source_start_ms": base_source_start})
        right = copy.deepcopy(orig)
        right.id = cmd.new_scene_id
        right.start_ms = split_at
        right.source_start_ms = base_source_start + (split_at - orig.start_ms)
        visual[idx : idx + 1] = [left, right]

    elif cmd.type == "move_scene":
        idx = next((i for i, s in enumerate(visual) if s.id == cmd.scene_id), None)
        if idx is None:
            raise NotFoundError(f"Scene {cmd.scene_id} not found")
        item = visual.pop(idx)
        bounded = max(0, min(len(visual), cmd.to_index))
        visual.insert(bounded, item)
        nxt.tracks.visual = _reflow(visual)

    elif cmd.type == "delete_scene":
        idx = next((i for i, s in enumerate(visual) if s.id == cmd.scene_id), None)
        if idx is None:
            raise NotFoundError(f"Scene {cmd.scene_id} not found")
        visual.pop(idx)
        nxt.tracks.visual = _reflow(visual)

    elif cmd.type == "restore_scene":
        idx = next((i for i, s in enumerate(visual) if s.id == cmd.scene.id), None)
        if idx is None:
            bounded = max(0, min(len(visual), cmd.index))
            visual.insert(bounded, cmd.scene)
            nxt.tracks.visual = _reflow(visual)
            retime = True
        else:
            retime = visual[idx].start_ms != cmd.scene.start_ms
            visual[idx] = cmd.scene.model_copy(deep=True)

    elif cmd.type == "tighten_pauses":
        nxt.tracks.visual = _reflow(visual, gap_ms=max(0, min(300, cmd.max_gap_ms)))

    elif cmd.type == "edit_narration":
        idx = next((i for i, s in enumerate(visual) if s.id == cmd.scene_id), None)
        if idx is None:
            raise NotFoundError(f"Scene {cmd.scene_id} not found")
        visual[idx].narration = cmd.narration
        if visual[idx].status in {"planned", "ready"}:
            visual[idx].status = "generating_audio"

    elif cmd.type == "regenerate_clip":
        idx = next((i for i, s in enumerate(visual) if s.id == cmd.scene_id), None)
        if idx is None:
            raise NotFoundError(f"Scene {cmd.scene_id} not found")
        s = visual[idx]
        if cmd.prompt:
            s.prompt = ScenePromptBundle(ltx25=cmd.prompt, wan22=cmd.prompt, qwen_image=cmd.prompt)
        if cmd.model:
            s.model = cmd.model
        if cmd.camera:
            s.camera = cmd.camera
        s.seed = cmd.seed if cmd.seed is not None else random.randint(1000, 999999)
        s.source_start_ms = 0
        s.status = "generating_keyframe"
        est_cu = 0.08

    elif cmd.type == "approve_scene_qa":
        idx = next((i for i, s in enumerate(visual) if s.id == cmd.scene_id), None)
        if idx is None:
            raise NotFoundError(f"Scene {cmd.scene_id} not found")
        s = visual[idx]
        prev_score = s.qa.score if s.qa and s.qa.score is not None else 8.5
        prev_attempts = s.qa.attempts if s.qa else 1
        s.qa = SceneQASummary(
            score=prev_score,
            attempts=prev_attempts,
            verdict="human_approved",
            reason="Approved by human-on-the-loop",
        )
        has_master = bool(s.assets and s.assets.master)
        s.status = "generating_clip" if (s.kind == "ai_video" and not has_master) else "ready"
        if s.status == "generating_clip":
            est_cu = 0.06

    elif cmd.type == "convert_scene_to_diagram":
        idx = next((i for i, s in enumerate(visual) if s.id == cmd.scene_id), None)
        if idx is None:
            raise NotFoundError(f"Scene {cmd.scene_id} not found")
        s = visual[idx]
        s.kind = "diagram"
        s.template = cmd.template
        s.props = cmd.props
        s.qa = SceneQASummary(score=10.0, attempts=(s.qa.attempts if s.qa else 0), verdict="human_approved", reason="Native Remotion diagram")
        s.status = "ready"

    elif cmd.type == "insert_diagram":
        new_scene = VisualSceneItem(
            id=cmd.scene_id,
            start_ms=cmd.at_ms,
            end_ms=cmd.at_ms + max(2000, cmd.duration_ms),
            kind="diagram",
            narration=cmd.narration,
            template=cmd.template,
            props=cmd.props,
            qa=SceneQASummary(score=10.0, attempts=0, verdict="passed", reason="Native Remotion diagram"),
            status="ready",
        )
        pos = next((i for i, s in enumerate(visual) if s.start_ms >= cmd.at_ms), len(visual))
        visual.insert(pos, new_scene)
        nxt.tracks.visual = _reflow(visual)

    elif cmd.type == "add_overlay":
        nxt.tracks.overlays.append(cmd.overlay)

    elif cmd.type == "remove_overlay":
        nxt.tracks.overlays = [o for o in nxt.tracks.overlays if o.id != cmd.overlay_id]

    elif cmd.type == "update_captions":
        merged = nxt.tracks.captions.model_dump()
        merged.update(cmd.patch)
        nxt.tracks.captions = nxt.tracks.captions.model_validate(merged)

    elif cmd.type == "mix_audio":
        if cmd.mood is not None:
            nxt.tracks.music.mood = cmd.mood
        if cmd.duck_db is not None:
            nxt.tracks.music.duck_db = cmd.duck_db
        if cmd.volume is not None:
            nxt.tracks.music.volume = cmd.volume
        if cmd.voice_speed is not None:
            nxt.voice.speed = cmd.voice_speed

    if retime:
        retime_speech(nxt, doc.tracks.visual)
    nxt.revision += 1
    return nxt, est_cu
