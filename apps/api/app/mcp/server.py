from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.director.providers import get_brain_provider
from app.director.review import review_draft as critique_timeline
from app.models import Project
from app.timeline.schemas import TimelineCommand
from app.timeline.service import apply_timeline_commands, get_latest_timeline

try:
    from mcp.server.fastmcp import FastMCP

    def _new_server():
        return FastMCP("kinetiq-studio-mcp", stateless_http=True, streamable_http_path="/")
except ImportError:
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:  # optional when the MCP package is absent

        def _new_server():
            return None
    else:

        class MountedMCPServer(MCPServer):
            """MCP 2 moved HTTP transport settings from construction to app creation."""

            def streamable_http_app(self):
                return super().streamable_http_app(streamable_http_path="/", stateless_http=True)

        def _new_server():
            return MountedMCPServer("kinetiq-studio-mcp")


class MCPBearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        if request.url.path.startswith("/mcp") and settings.mcp_access_token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {settings.mcp_access_token}":
                return JSONResponse(status_code=401, content={"error": "unauthorized_mcp_token"})
        return await call_next(request)


def _register_tools(mcp):
    @mcp.tool()
    def get_timeline_summary(project_id: str) -> dict[str, Any]:
        """Read the current declarative timeline summary for a project."""
        with SessionLocal() as db:
            doc = get_latest_timeline(db, project_id)
            return {
                "project_id": doc.project_id,
                "revision": doc.revision,
                "format": doc.format.model_dump(),
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

    @mcp.tool()
    async def review_draft(project_id: str) -> dict[str, Any]:
        """Critique pacing, opening-hook retention and diagram density for the whole draft."""
        with SessionLocal() as db:
            doc = get_latest_timeline(db, project_id)
            project = db.get(Project, project_id)
            result = await critique_timeline(db, project, doc, get_brain_provider(project.brain_mode))
            db.commit()
            return result

    @mcp.tool()
    def get_scene(project_id: str, scene_id: str) -> dict[str, Any]:
        """Inspect full details, prompts, and QA scores of a single scene."""
        with SessionLocal() as db:
            doc = get_latest_timeline(db, project_id)
            scene = next((s for s in doc.tracks.visual if s.id == scene_id), None)
            if not scene:
                return {"error": f"Scene {scene_id} not found"}
            return scene.model_dump(mode="json")

    @mcp.tool()
    def apply_editor_commands(
        project_id: str, expected_revision: int, commands: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Apply up to 25 validated TimelineCommand edits atomically as Antigravity (author='mcp')."""
        from pydantic import TypeAdapter

        parsed = [TypeAdapter(TimelineCommand).validate_python(c) for c in commands[:25]]
        with SessionLocal() as db:
            updated = apply_timeline_commands(db, project_id, expected_revision, parsed, author="mcp")
            return {"ok": True, "new_revision": updated.revision, "scene_count": len(updated.tracks.visual)}

    return mcp


class _MountedMCP:
    """Build a fresh SDK transport for each FastAPI lifespan (SDK managers are single-use)."""

    def streamable_http_app(self):
        async def dispatch(scope, receive, send):
            await self._app(scope, receive, send)

        return dispatch

    @property
    def session_manager(self):
        return self

    @asynccontextmanager
    async def run(self):
        server = _register_tools(_new_server())
        self._app = server.streamable_http_app()
        try:
            async with server.session_manager.run():
                yield
        finally:
            self._app = None


def create_mcp_server():
    return _MountedMCP() if _new_server() is not None else None
