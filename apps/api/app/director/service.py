from __future__ import annotations

import json
import random
import re
import uuid

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFoundError, QuotaConfirmationRequiredError, ValidationFailedError
from app.director.providers import GeminiProvider, convert_tool_calls_to_commands
from app.director.schemas import (
    CopilotProposalResponse,
    DirectorBriefResponse,
    PlannedSceneSpec,
    PlannedStoryboard,
)
from app.events.service import emit_event
from app.models import AIRun, Project
from app.timeline.commands import apply_command
from app.timeline.schemas import (
    DiagramProps,
    ScenePromptBundle,
    SceneQASummary,
    StyleBible,
    TimelineCommand,
    TimelineDocument,
    TimelineFormat,
    TimelineTracks,
    VisualSceneItem,
    VoiceConfig,
)
from app.timeline.service import apply_timeline_commands, get_latest_timeline, save_timeline_document


def _format_dims(aspect: str, target_res: str) -> tuple[int, int]:
    if aspect == "9:16":
        if target_res == "4k":
            return 2160, 3840
        if target_res == "720p":
            return 720, 1280
        return 1080, 1920
    if target_res == "4k":
        return 3840, 2160
    if target_res == "720p":
        return 1280, 720
    return 1920, 1080


def build_deterministic_storyboard(
    *,
    topic: str,
    aspect: str,
    duration_preset: str,
    script_override: str | None = None,
) -> PlannedStoryboard:
    """Offline / Phase-0 fallback educational storyboard generator."""
    if script_override and script_override.strip():
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script_override.strip()) if s.strip()]
    else:
        count = 8 if duration_preset == "reel_60s" else 12
        sentences = [
            f"Most people misunderstand {topic}, yet a single core principle explains everything.",
            f"To see how {topic} works in practice, start by looking at the hidden bottleneck underneath.",
            f"Traditional approaches fail here because scale changes the physics of the system.",
            f"Compare the baseline rate against what happens once compounding feedback kicks in.",
            f"Three distinct stages turn raw energy or information into measurable output.",
            f"First, the input signal is filtered; second, the core mechanism amplifies structure; third, noise drops away.",
            f"Real-world measurements show why small efficiency gains beat brute force every single time.",
            f"Once you spot this pattern in {topic}, you can apply the exact same model to your own work.",
        ][:count]

    specs: list[PlannedSceneSpec] = []
    for i, sentence in enumerate(sentences):
        if i % 3 == 1:
            specs.append(
                PlannedSceneSpec(
                    narration=sentence,
                    duration_ms=7000,
                    kind="diagram",
                    diagram_template="BarCompare" if i % 2 == 1 else "StepBreakdown",
                    diagram_props=DiagramProps(
                        title=f"{topic[:34]} — Key Metrics",
                        subtitle="Native 1080p/4K Vector Comparison",
                        labels=["Baseline", "Optimized", "Peak Scale"],
                        values=[22, 64, 96],
                        steps=["Filter Signal", "Amplify Structure", "Eliminate Noise"],
                        highlight_index=2,
                    ),
                )
            )
        elif i % 4 == 2:
            specs.append(
                PlannedSceneSpec(
                    narration=sentence,
                    duration_ms=6500,
                    kind="image_motion",
                    camera="macro_pan",
                    model="qwen_image",
                    qwen_image_prompt=(
                        f"Clean editorial infographic still illustrating: {sentence}. "
                        f"Deep obsidian #0B0F19 background, amber #F59E0B highlights, crisp legible typography."
                    ),
                )
            )
        else:
            specs.append(
                PlannedSceneSpec(
                    narration=sentence,
                    duration_ms=6500,
                    kind="ai_video",
                    camera="slow_dolly_in" if i % 2 == 0 else "cinematic_orbit",
                    model="ltx25_fp8",
                    ltx25_prompt=(
                        f"A slow cinematic dolly-in through atmospheric volumetric lighting illustrating: {sentence}. "
                        "35mm documentary lens, rich obsidian shadows, warm amber rim light, smooth continuous motion."
                    ),
                    wan22_prompt=f"Smooth camera push-in, physically realistic lighting and motion illustrating: {sentence}.",
                    qwen_image_prompt=f"High-detail 1080p keyframe illustrating {sentence}, cinematic obsidian & amber documentary style.",
                )
            )

    return PlannedStoryboard(
        palette=["#0B0F19", "#F59E0B", "#22D3EE", "#F8FAFC"],
        look="cinematic documentary, volumetric light, 35mm lens, subtle film grain",
        negative="blurry text, watermark, distorted hands, flicker, low resolution",
        hook=sentences[0] if sentences else topic,
        seo_title=f"{topic} Explained in Plain English",
        description=f"Deep breakdown of {topic} with visual models and clear examples.",
        hashtags=["#education", "#science", "#learnontiktok", "#explainer"],
        scenes=specs,
    )


def storyboard_to_timeline(project: Project, plan: PlannedStoryboard, revision: int) -> TimelineDocument:
    w, h = _format_dims(project.aspect, project.target_res)
    cursor = 0
    visual: list[VisualSceneItem] = []

    for idx, spec in enumerate(plan.scenes, start=1):
        sid = f"scn_{idx:03d}"
        dur = max(2500, spec.duration_ms)
        is_diagram = spec.kind == "diagram"
        visual.append(
            VisualSceneItem(
                id=sid,
                start_ms=cursor,
                end_ms=cursor + dur,
                kind=spec.kind,
                narration=spec.narration,
                prompt=None
                if is_diagram
                else ScenePromptBundle(
                    ltx25=spec.ltx25_prompt or spec.qwen_image_prompt,
                    wan22=spec.wan22_prompt or spec.ltx25_prompt,
                    qwen_image=spec.qwen_image_prompt or spec.ltx25_prompt,
                ),
                camera=spec.camera,
                model=spec.model,
                seed=random.randint(10000, 999999),
                template=spec.diagram_template,
                props=spec.diagram_props,
                qa=SceneQASummary(
                    score=10.0 if is_diagram else None,
                    attempts=0,
                    verdict="passed" if is_diagram else "pending",
                    reason="Native Remotion vector diagram" if is_diagram else None,
                ),
                status="ready" if is_diagram else "planned",
            )
        )
        cursor += dur

    return TimelineDocument(
        schema_version=1,
        project_id=project.id,
        revision=revision,
        format=TimelineFormat(
            aspect=project.aspect,  # type: ignore[arg-type]
            fps=30,
            target=project.target_res,  # type: ignore[arg-type]
            width=w,
            height=h,
        ),
        style_bible=StyleBible(
            palette=plan.palette,
            look=plan.look,
            negative=plan.negative,
            safe_zone="9x16_reels" if project.aspect == "9:16" else "16x9_standard",
        ),
        voice=VoiceConfig(engine="kokoro", voice_id="am_michael", speed=1.0),
        tracks=TimelineTracks(visual=visual),
    )


async def plan_project_storyboard(
    db: Session,
    project: Project,
    *,
    script_override: str | None = None,
    brain_mode: str | None = None,
) -> TimelineDocument:
    settings = get_settings()
    mode = brain_mode or project.brain_mode

    if mode == "gemini_auto" and settings.gemini_api_key:
        try:
            provider = GeminiProvider(settings.gemini_api_key, settings.gemini_pro_model)
            plan = await provider.generate_storyboard(
                db,
                project_id=project.id,
                topic=project.topic,
                aspect=project.aspect,
                duration_preset=project.duration_preset,
                script_override=script_override,
            )
        except Exception:
            plan = build_deterministic_storyboard(
                topic=project.topic,
                aspect=project.aspect,
                duration_preset=project.duration_preset,
                script_override=script_override,
            )
    else:
        plan = build_deterministic_storyboard(
            topic=project.topic,
            aspect=project.aspect,
            duration_preset=project.duration_preset,
            script_override=script_override,
        )

    doc = storyboard_to_timeline(project, plan, project.current_revision)
    project.status = "planned"
    return save_timeline_document(db, project, project.current_revision, doc, author="ai")


def build_chatgpt_brief(project: Project) -> DirectorBriefResponse:
    schema_hint = PlannedStoryboard.model_json_schema()
    instructions = (
        f"Act as an elite faceless educational video director (Vox / Kurzgesagt style).\n"
        f"Topic: {project.topic}\n"
        f"Format: {project.aspect} ({project.target_res}), Preset: {project.duration_preset}\n"
        "Return ONLY raw JSON (no markdown fences) matching the JSON Schema below. "
        "Mix 'ai_video', 'diagram' (BarCompare/StepBreakdown/StatCallout), and 'image_motion' scenes. "
        "Write detailed camera + lighting screenplay prompts in ltx25_prompt."
    )
    return DirectorBriefResponse(
        project_id=project.id,
        instructions=instructions,
        schema_hint=schema_hint,
    )


def import_chatgpt_storyboard(db: Session, project: Project, raw_json: str) -> TimelineDocument:
    cleaned = raw_json.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL).strip()
    try:
        plan = PlannedStoryboard.model_validate_json(cleaned)
    except Exception as exc:
        raise ValidationFailedError(f"Invalid ChatGPT storyboard JSON: {exc}") from exc

    doc = storyboard_to_timeline(project, plan, project.current_revision)
    project.status = "planned"
    return save_timeline_document(db, project, project.current_revision, doc, author="ai")


async def create_copilot_proposal(
    db: Session,
    project: Project,
    *,
    message: str,
    selected_scene_id: str | None = None,
) -> CopilotProposalResponse:
    settings = get_settings()
    doc = get_latest_timeline(db, project.id)

    summary = {
        "project_id": project.id,
        "aspect": doc.format.aspect,
        "selected_scene_id": selected_scene_id,
        "scenes": [
            {
                "id": s.id,
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "kind": s.kind,
                "status": s.status,
                "narration": s.narration,
            }
            for s in doc.tracks.visual
        ],
    }

    if settings.gemini_api_key:
        provider = GeminiProvider(settings.gemini_api_key, settings.gemini_pro_model)
        assistant_msg, tool_calls = await provider.copilot_propose(
            db, project_id=project.id, timeline_summary=summary, user_message=message
        )
        raw_cmds = convert_tool_calls_to_commands(tool_calls)
    else:
        # Phase 0 offline heuristic command parser so Copilot UI is immediately testable
        raw_cmds = []
        lower = message.lower()
        target_id = selected_scene_id or (doc.tracks.visual[0].id if doc.tracks.visual else None)
        if "pause" in lower or "tighten" in lower:
            raw_cmds.append({"type": "tighten_pauses", "max_gap_ms": 80})
        if ("chart" in lower or "diagram" in lower) and doc.tracks.visual:
            raw_cmds.append(
                {
                    "type": "insert_diagram",
                    "at_ms": doc.tracks.visual[0].end_ms,
                    "duration_ms": 6000,
                    "scene_id": f"scn_{uuid.uuid4().hex[:6]}",
                    "narration": "Here is how those numbers compare side by side.",
                    "template": "BarCompare",
                    "props": {
                        "title": "Key Comparison",
                        "labels": ["Before", "After"],
                        "values": [24, 92],
                    },
                }
            )
        if ("redo" in lower or "regenerate" in lower or "orbit" in lower) and target_id:
            raw_cmds.append(
                {
                    "type": "regenerate_clip",
                    "scene_id": target_id,
                    "camera": "cinematic_orbit" if "orbit" in lower else "slow_dolly_in",
                }
            )
        if not raw_cmds and target_id:
            raw_cmds.append(
                {
                    "type": "update_captions",
                    "patch": {"style": "bold_reels", "accent_color": "#F59E0B"},
                }
            )
        assistant_msg = f"Prepared {len(raw_cmds)} previewable timeline command(s) for review."

    # Validate commands strictly through TimelineCommand Pydantic union
    validated: list[TimelineCommand] = []
    temp_doc = doc
    est_cu = 0.0
    affected: set[str] = set()
    for rc in raw_cmds[:25]:
        cmd = TimelineCommand  # type: ignore[assignment]
        from pydantic import TypeAdapter
        parsed_cmd = TypeAdapter(TimelineCommand).validate_python(rc)
        temp_doc, cu = apply_command(temp_doc, parsed_cmd)
        est_cu += cu
        validated.append(parsed_cmd)
        if hasattr(parsed_cmd, "scene_id"):
            affected.add(getattr(parsed_cmd, "scene_id"))

    proposal_id = f"prop_{uuid.uuid4().hex[:10]}"
    requires_conf = est_cu > 1.0
    payload = {
        "proposal_id": proposal_id,
        "assistant_message": assistant_msg,
        "commands": [c.model_dump(mode="json") for c in validated],
        "affected_scenes": sorted(affected),
        "est_cu": round(est_cu, 3),
        "requires_confirmation": requires_conf,
        "status": "pending",
    }
    db.add(
        AIRun(
            id=proposal_id,
            project_id=project.id,
            provider="gemini" if settings.gemini_api_key else "offline_copilot",
            model=settings.gemini_pro_model if settings.gemini_api_key else "heuristic",
            task="copilot_proposal",
            prompt_hash=str(hash(message))[:16],
            outcome="ok",
            proposal_status="pending",
            proposal_payload=payload,
        )
    )
    emit_event(db, "copilot.proposal", payload, project_id=project.id)
    db.commit()
    return CopilotProposalResponse.model_validate(payload)


def accept_copilot_proposal(
    db: Session,
    *,
    project_id: str,
    proposal_id: str,
    expected_revision: int,
    confirm_gpu_spend: bool,
) -> TimelineDocument:
    run = db.query(AIRun).filter(AIRun.id == proposal_id, AIRun.project_id == project_id).first()
    if not run or not run.proposal_payload:
        raise NotFoundError(f"Copilot proposal {proposal_id} not found")

    est_cu = float(run.proposal_payload.get("est_cu", 0.0))
    if est_cu > 1.0 and not confirm_gpu_spend:
        raise QuotaConfirmationRequiredError(
            f"Proposal requires ~{est_cu:.2f} CU of GPU spend; pass confirm_gpu_spend=true to proceed."
        )

    from pydantic import TypeAdapter
    cmds = [TypeAdapter(TimelineCommand).validate_python(c) for c in run.proposal_payload["commands"]]
    updated = apply_timeline_commands(db, project_id, expected_revision, cmds, author="ai")
    run.proposal_status = "accepted"
    run.proposal_payload = {**run.proposal_payload, "status": "accepted"}
    db.commit()
    return updated


def reject_copilot_proposal(db: Session, *, project_id: str, proposal_id: str) -> dict[str, str]:
    run = db.query(AIRun).filter(AIRun.id == proposal_id, AIRun.project_id == project_id).first()
    if not run or not run.proposal_payload:
        raise NotFoundError(f"Copilot proposal {proposal_id} not found")
    run.proposal_status = "rejected"
    run.proposal_payload = {**run.proposal_payload, "status": "rejected"}
    db.commit()
    return {"proposal_id": proposal_id, "status": "rejected"}
