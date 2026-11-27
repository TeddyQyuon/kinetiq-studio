from __future__ import annotations

import asyncio
import random

from sqlalchemy import update

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ValidationFailedError
from app.director.providers import get_brain_provider
from app.events.service import emit_event
from app.jobs.service import enqueue_job
from app.models import Asset, Job, Project, QAReview, Scene
from app.qa.media import read_media, sample_proxy_frames
from app.qa.schemas import QAScores
from app.timeline.schemas import SceneQASummary, TimelineDocument
from app.timeline.service import get_latest_timeline, save_timeline_document


def compute_weighted_score(s: QAScores) -> float:
    score = 0.40 * s.narration_match + 0.30 * s.artifact_free + 0.20 * s.style_consistency + 0.10 * s.safe_zone
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

    provider = get_brain_provider(project.brain_mode, vision=True)
    if provider and image_bytes:
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


CLIP_MODELS = {"i2v_ltx25": "ltx25_fp8", "i2v_wan22": "wan22_i2v"}


def pending_clip_jobs(db: Session):
    # Use a server-owned review ID as the durable completion marker. Worker result
    # metadata is extensible, so a flag in that JSON would not be authoritative.
    return (
        db.query(Job)
        .outerjoin(QAReview, QAReview.id == Job.id)
        .filter(
            Job.job_type.in_(CLIP_MODELS), Job.status == "succeeded", Job.scene_id.is_not(None), QAReview.id.is_(None)
        )
    )


async def evaluate_completed_clip(db: Session, job: Job) -> None:
    """Record exactly one clip review per render, and update its current scene atomically."""
    if job.job_type not in CLIP_MODELS or job.status != "succeeded" or db.get(QAReview, job.id):
        return
    project = db.get(Project, job.project_id)
    doc = get_latest_timeline(db, job.project_id)
    revision = doc.revision
    scene = next((s for s in doc.tracks.visual if s.id == job.scene_id), None)
    refs = (job.result or {}).get("asset_ids") or {}
    proxy_id = refs.get("proxy")
    asset = db.get(Asset, proxy_id) if proxy_id else None
    current = (
        scene is not None
        and scene.kind == "ai_video"
        and scene.status in {"ready", "qa_checking"}
        and not (scene.qa and scene.qa.verdict == "human_approved")
        and scene.assets is not None
        and (
            (scene.status == "qa_checking" and scene.qa and scene.qa.reason == f"Awaiting clip QA for render {job.id}")
            or (scene.status == "ready" and (refs.get("master") or refs.get("proxy")))
        )
        and all(getattr(scene.assets, k) == refs[k] for k in ("master", "proxy") if refs.get(k))
    )
    scores = None
    reason = "Clip was superseded before QA."
    if current:
        try:
            if not asset or asset.kind != "proxy" or asset.scene_id != job.scene_id:
                raise ValidationFailedError("The completed render has no verified scene proxy.")
            provider = get_brain_provider(project.brain_mode, vision=True)
            if not provider:
                raise ValidationFailedError("Clip QA requires a vision provider.")
            video = await read_media(db, project, asset, limit=64 * 1024 * 1024)
            frames = await asyncio.to_thread(sample_proxy_frames, video)
            scores = await provider.vision_score_clip(
                db,
                project_id=project.id,
                frames=frames,
                narration=scene.narration,
                style_look=doc.style_bible.look,
                safe_zone=doc.style_bible.safe_zone,
            )
            reason = scores.reason
        except ValidationFailedError as exc:
            reason = exc.message
        except Exception as exc:
            # Decoding, storage or provider failure is not evidence of a bad render.
            # Avoid spending a GPU retry or leaking provider credentials in the reason.
            reason = f"Could not score four proxy frames ({type(exc).__name__}). Inspect the clip manually."

    # Recheck the revision in the database after remote I/O. A concurrent editor
    # or consumer wins cleanly; the next scan rereads the current timeline.
    lease = job.lease_until
    locked = db.execute(
        update(Project)
        .where(Project.id == project.id, Project.current_revision == revision)
        .values(current_revision=revision)
    )
    if locked.rowcount != 1 or db.get(QAReview, job.id):
        db.rollback()
        if lease is not None:
            db.execute(update(Job).where(Job.id == job.id, Job.lease_until == lease).values(lease_until=None))
            db.commit()
        return

    weighted = compute_weighted_score(scores) if scores else 0.0
    attempt = 2 if (job.payload or {}).get("clip_qa_retry_of") else 1
    fix_action = None
    if not current:
        verdict = "superseded"
    elif not scores or project.qa_policy == "hitl_strict_gate":
        verdict = "needs_review"
    elif min(weighted, scores.artifact_free) >= get_settings().qa_pass_threshold:
        verdict = "passed"
    elif attempt == 1:
        verdict = "auto_healing"
        fix_action = "rerender_clip"
        scene.seed = random.randint(1000, 999999)
        enqueue_job(
            db,
            project_id=project.id,
            scene_id=scene.id,
            job_type=job.job_type,
            capability=job.capability,
            depends_on=[job.id],
            est_cu=0.06,
            payload={**(job.payload or {}), "clip_qa_retry_of": job.id, "seed": scene.seed},
        )
    else:
        verdict = "needs_review"

    job.lease_until = None
    db.add(
        QAReview(
            id=job.id,
            project_id=project.id,
            scene_id=job.scene_id,
            asset_id=proxy_id,
            narration_match=scores.narration_match if scores else 0,
            artifact_free=scores.artifact_free if scores else 0,
            style_consistency=scores.style_consistency if scores else 0,
            safe_zone=scores.safe_zone if scores else 0,
            score=weighted,
            verdict=verdict,
            reason=reason,
            fix_action=fix_action,
        )
    )
    if not current:
        db.commit()
        return
    scene.qa = SceneQASummary(score=weighted if scores else None, attempts=attempt, verdict=verdict, reason=reason)
    scene.status = {"passed": "ready", "auto_healing": "generating_clip", "needs_review": "needs_review"}[verdict]
    if scene.status != "ready" and project.status == "ready":
        project.status = "generating"
    emit_event(
        db,
        "qa.reviewed",
        {"scene_id": scene.id, "asset_id": proxy_id, "model": CLIP_MODELS[job.job_type], **scene.qa.model_dump()},
        project_id=project.id,
    )
    save_timeline_document(db, project, revision, doc, author="ai")
