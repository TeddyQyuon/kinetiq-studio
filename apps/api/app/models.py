from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("usr"))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class OAuthCredential(Base):
    __tablename__ = "oauth_credentials"
    __table_args__ = (Index("ix_oauth_user_provider", "user_id", "provider", unique=True),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("oau"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="google")
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("prj"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    aspect: Mapped[str] = mapped_column(String(16), nullable=False, default="16:9")
    target_res: Mapped[str] = mapped_column(String(16), nullable=False, default="1080p")
    duration_preset: Mapped[str] = mapped_column(String(32), nullable=False, default="reel_60s")
    qa_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="hotl_smart")
    brain_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="gemini_auto")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    drive_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    revisions: Mapped[list["TimelineRevision"]] = relationship(cascade="all, delete-orphan")
    scenes: Mapped[list["Scene"]] = relationship(cascade="all, delete-orphan")
    assets: Mapped[list["Asset"]] = relationship(cascade="all, delete-orphan")
    jobs: Mapped[list["Job"]] = relationship(cascade="all, delete-orphan")


class TimelineRevision(Base):
    __tablename__ = "timeline_revisions"
    __table_args__ = (Index("ix_timeline_rev_unique", "project_id", "revision", unique=True),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rev"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    author: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    command_batch: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    drive_snapshot_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (Index("ix_scenes_project_order", "project_id", "order_index"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    qa_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    qa_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    keyframe_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    master_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proxy_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upscaled_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ast"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    scene_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    drive_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")
    sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sync_state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("wrk"))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="google_colab")
    profile: Mapped[str] = mapped_column(String(64), nullable=False, default="ltx25_pro")
    gpu_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vram_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="offline")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cu_used_month: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_cap", "status", "capability", "priority"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("job"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    scene_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    depends_on: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gpu_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    est_cu: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AIRun(Base):
    __tablename__ = "ai_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("air"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    proposal_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    proposal_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class QAReview(Base):
    __tablename__ = "qa_reviews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("qar"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    scene_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    narration_match: Mapped[float] = mapped_column(Float, nullable=False)
    artifact_free: Mapped[float] = mapped_column(Float, nullable=False)
    style_consistency: Mapped[float] = mapped_column(Float, nullable=False)
    safe_zone: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    fix_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("exp"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    master_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    srt_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    publish_kit: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class EventOutbox(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_project_id", "project_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
