from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.events.service import CHANNEL, format_sse
from app.models import EventOutbox

router = APIRouter(prefix="/v1", tags=["events"])
logger = logging.getLogger(__name__)


def _read_page(cursor: int, project_id: str | None):
    with SessionLocal() as db:
        query = db.query(EventOutbox).filter(EventOutbox.id > cursor)
        if project_id:
            query = query.filter((EventOutbox.project_id == project_id) | EventOutbox.project_id.is_(None))
        return query.order_by(EventOutbox.id).limit(100).all()


async def event_stream(cursor: int, project_id: str | None):
    url = get_settings().redis_url
    loop = asyncio.get_running_loop()
    heartbeat = loop.time() + 15
    retry_at = 0.0
    async with AsyncExitStack() as stack:
        sub = None
        while True:
            try:
                if url and sub is None and loop.time() >= retry_at:
                    client = await stack.enter_async_context(Redis.from_url(url, socket_connect_timeout=1))
                    sub = await stack.enter_async_context(client.pubsub())
                    # Receive SUBSCRIBE ack before taking the replay snapshot.
                    async with asyncio.timeout(3):
                        await sub.subscribe(CHANNEL)
                        await sub.get_message(timeout=None)

                rows = await asyncio.to_thread(_read_page, cursor, project_id)
                for ev in rows:
                    cursor = ev.id
                    yield format_sse(ev)
                if rows:
                    heartbeat = loop.time() + 15
                    continue
                if loop.time() >= heartbeat:
                    yield ": keepalive\n\n"
                    heartbeat = loop.time() + 15
                if sub is None:
                    await asyncio.sleep(1)
                else:
                    await sub.get_message(ignore_subscribe_messages=True, timeout=max(0, heartbeat - loop.time()))
            except (RedisError, OSError, TimeoutError):
                if not url:
                    raise
                logger.warning("Redis subscription unavailable, polling outbox", exc_info=True)
                await stack.aclose()
                sub = None
                retry_at = loop.time() + 15


@router.get("/events")
async def stream_events(
    project_id: str | None = Query(default=None),
    last_event_id_q: int | None = Query(default=None, alias="last_event_id"),
    last_event_id_hdr: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    cursor = last_event_id_q or 0
    if (
        last_event_id_q is None
        and last_event_id_hdr
        and last_event_id_hdr.isascii()
        and last_event_id_hdr.isdigit()
        and len(last_event_id_hdr) < 20
    ):
        cursor = int(last_event_id_hdr)
    return StreamingResponse(
        event_stream(max(0, min(cursor, 2**63 - 1)), project_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
