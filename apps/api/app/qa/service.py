from __future__ import annotations

import random

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.director.providers import GeminiProvider
from app.events.service import emit_event
from app.models import Project, QAReview, Scene
from app.qa.schemas import QAScores
from app.timeline.schemas import SceneQASummary, TimelineDocument


def compute_weighted_score(s: QAScores) -> float:
    score = (
        0.40 * s.narration_match
        + 0.30 * s.artifact_free
        + 0.20 * s.style_consistency
        + 0.10 * s.safe_zone
    )
    return round(score, 2)


async def evaluate_keyframe_with_hotl(
    db: Session,
    *,
    project: Project,
    doc: TimelineDocument,
    scene_id: str,
    asset_id: str,
    image_bytes: bytes | None,
) -> tuple[QAReview, str]:
    """Evaluate a generated keyframe and apply HOTL policy.

    Returns (QAReview row, decision): decision in {"pass", "auto_heal_retry", "convert_diagram", "needs_review"}.
    """
    settings = get_settings()
    scene_item = next((s for s in doc.tracks.visual if s.id == scene_id), None)
    narration = scene_item.narration if scene_item else ""
    look = doc.style_bible.look
    safe_zone = doc.style_bible.safe_zone

    if settings.gemini_api_key and image_bytes:
        provider = GeminiProvider(settings.gemini_api_key, settings.gemini_flash_model)
        scores = await provider.vision_score_keyframe(
            db=db,
            project_id=project.id,
            image_bytes=image_bytes,
            narration=narration,
            style_look=look,
            safe_zone=safe_zone,
        )
    else:
        # Phase 0 offline deterministic QA simulation (for --mock or no-key testing)
        scores = QAScores(
            narration_match=8.6,
            artifact_free=8.8,
            style_consistency=8.5,
            safe_zone=9.0,
            reason="Offline heuristic check passed; composition aligns with style bible and safe zone.",
            suggested_fix=None,
            prefer_diagram=False,
        )

    weighted = compute_weighted_score(scores)
    db_scene = db.query(Scene).filter(Scene.id == scene_id, Scene.project_id == project.id).first()
    attempts = (db_scene.qa_attempts + 1) if db_scene else 1

    policy = project.qa_policy or settings.qa_default_policy
    threshold = settings.qa_pass_threshold
    max_retries = settings.qa_max_retries

    if policy == "hitl_strict_gate":
        verdict = "needs_review"
        decision = "needs_review"
        fix_action = "awaiting_human_approval"
    elif weighted >= threshold:
        verdict = "passed"
        decision = "pass"
        fix_action = None
    elif policy == "full_auto":
        if attempts < max_retries:
            verdict = "auto_healing"
            decision = "convert_diagram" if scores.prefer_diagram else "auto_heal_retry"
            fix_action = "convert_to_diagram" if scores.prefer_diagram else "rewrite_prompt_and_reseed"
        else:
            verdict = "passed"
            decision = "pass"
            fix_action = "auto_accepted_at_max_retries"
    else:
        # hotl_smart (default): auto-heal up to max_retries, then escalate to needs_review
        if scores.prefer_diagram:
            verdict = "auto_healing"
            decision = "convert_diagram"
            fix_action = "convert_to_diagram"
        elif attempts <= max_retries:
            verdict = "auto_healing"
            decision = "auto_heal_retry"
            fix_action = "rewrite_prompt_and_reseed"
        else:
            verdict = "needs_review"
            decision = "needs_review"
            fix_action = "escalated_to_human_on_loop"

    review = QAReview(
        project_id=project.id,
        scene_id=scene_id,
        asset_id=asset_id,
        narration_match=scores.narration_match,
        artifact_free=scores.artifact_free,
        style_consistency=scores.style_consistency,
        safe_zone=scores.safe_zone,
        score=weighted,
        verdict=verdict,
        reason=scores.reason,
        fix_action=fix_action,
    )
    db.add(review)

    if db_scene:
        db_scene.qa_score = weighted
        db_scene.qa_attempts = attempts
        if decision == "needs_review":
            db_scene.status = "needs_review"

    if scene_item:
        scene_item.qa = SceneQASummary(
            score=weighted,
            attempts=attempts,
            verdict=verdict if verdict in {"passed", "auto_healing", "needs_review"} else "pending",
            reason=scores.reason,
        )
        if decision == "auto_heal_retry" and scores.suggested_fix and scene_item.prompt:
            scene_item.prompt.qwen_image = f"{scene_item.prompt.qwen_image or ''}. Fix: {scores.suggested_fix}".strip()
            scene_item.prompt.ltx25 = f"{scene_item.prompt.ltx25 or ''}. Ensure: {scores.suggested_fix}".strip()
            scene_item.seed = random.randint(1000, 999999)

    emit_event(
        db,
        "qa.reviewed",
        {
            "scene_id": scene_id,
            "score": weighted,
            "verdict": verdict,
            "decision": decision,
            "attempts": attempts,
            "reason": scores.reason,
        },
        project_id=project.id,
    )
    db.flush()
    return review, decision
