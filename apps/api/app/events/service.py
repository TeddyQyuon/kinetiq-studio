from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import EventOutbox


def emit_event(
    db: Session,
    event_type: str,
    payload: dict[str, Any],
    *,
    project_id: str | None = None,
) -> EventOutbox:
    """Persist event inside the current DB transaction (Transactional Outbox)."""
    ev = EventOutbox(
        project_id=project_id,
        event_type=event_type,
        payload=payload,
    )
    db.add(ev)
    db.flush()
    return ev


def format_sse(ev: EventOutbox) -> str:
    body = {
        "id": ev.id,
        "project_id": ev.project_id,
        "event_type": ev.event_type,
        "payload": ev.payload,
        "created_at": ev.created_at.isoformat(),
    }
    return f"id: {ev.id}\nevent: {ev.event_type}\ndata: {json.dumps(body)}\n\n"
