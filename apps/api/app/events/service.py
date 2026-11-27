from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from redis import Redis, RedisError
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import EventOutbox

CHANNEL = "kinetiq:outbox"
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _publisher(url: str) -> Redis:
    return Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)


@event.listens_for(Session, "after_commit")
def _notify_committed(db: Session) -> None:
    tx = db.get_nested_transaction() or db.get_transaction()
    last_id = db.info.pop(tx, None)
    if last_id is None:
        return
    if tx.nested:
        db.info[tx.parent] = last_id
        return
    try:
        _publisher(get_settings().redis_url).publish(CHANNEL, str(last_id))
    except (RedisError, OSError):
        # The transaction is durable. Subscribers also catch up on keepalive.
        logger.warning("Redis outbox notification failed", exc_info=True)


@event.listens_for(Session, "after_transaction_end")
def _discard_notification(db, transaction) -> None:
    db.info.pop(transaction, None)


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
    # PostgreSQL sequences alone do not order concurrent transactions by commit.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(78425136)"))
    db.add(ev)
    db.flush()
    if get_settings().redis_url:
        db.info[db.get_nested_transaction() or db.get_transaction()] = ev.id
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
