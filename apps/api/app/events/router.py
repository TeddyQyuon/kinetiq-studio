from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse

from app.core.db import SessionLocal
from app.events.service import format_sse
from app.models import EventOutbox

router = APIRouter(prefix="/v1", tags=["events"])


@router.get("/events")
async def stream_events(
    project_id: str | None = Query(default=None),
    last_event_id_q: int | None = Query(default=None, alias="last_event_id"),
    last_event_id_hdr: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    cursor = 0
    if last_event_id_q is not None:
        cursor = last_event_id_q
    elif last_event_id_hdr and last_event_id_hdr.isdigit():
        cursor = int(last_event_id_hdr)

    async def event_generator() -> AsyncGenerator[str, None]:
        nonlocal cursor
        ticks_without_event = 0
        while True:
            with SessionLocal() as db:
                query = db.query(EventOutbox).filter(EventOutbox.id > cursor)
                if project_id:
                    query = query.filter(
                        (EventOutbox.project_id == project_id) | (EventOutbox.project_id.is_(None))
                    )
                rows = query.order_by(EventOutbox.id.asc()).limit(100).all()

            if rows:
                ticks_without_event = 0
                for ev in rows:
                    cursor = ev.id
                    yield format_sse(ev)
            else:
                ticks_without_event += 1
                if ticks_without_event >= 15:
                    ticks_without_event = 0
                    yield ": keepalive\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
