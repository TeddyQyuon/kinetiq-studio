from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal, init_db
from app.core.errors import register_error_handlers
from app.core.logging import TraceIdMiddleware, configure_logging
from app.events.router import router as events_router
from app.exports.router import router as exports_router
from app.identity.router import router as identity_router
from app.mcp.server import MCPBearerAuthMiddleware, create_mcp_server
from app.orchestrator import orchestrator_loop
from app.projects.router import router as projects_router
from app.storage.router import router as storage_router
from app.timeline.router import router as timeline_router
from app.workers.router import router as workers_router

configure_logging()
settings = get_settings()
mcp_server = create_mcp_server()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    async with AsyncExitStack() as stack:
        bg_task = None
        if settings.run_embedded_orchestrator:
            bg_task = asyncio.create_task(orchestrator_loop(30))
        if mcp_server is not None and hasattr(mcp_server, "session_manager"):
            await stack.enter_async_context(mcp_server.session_manager.run())
        try:
            yield
        finally:
            if bg_task is not None:
                bg_task.cancel()


app = FastAPI(
    title="Kinetiq Studio Control Plane",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(TraceIdMiddleware)
app.add_middleware(MCPBearerAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(identity_router)
app.include_router(projects_router)
app.include_router(timeline_router)
app.include_router(workers_router)
app.include_router(storage_router)
app.include_router(exports_router)
app.include_router(events_router)

if mcp_server is not None:
    mcp_app = mcp_server.streamable_http_app() if hasattr(mcp_server, "streamable_http_app") else mcp_server.http_app()
    app.mount("/mcp", mcp_app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env, "storage": settings.storage_provider}


@app.get("/ready")
def ready() -> dict[str, str]:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ready"}
