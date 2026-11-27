from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.timeline.schemas import TimelineCommand
from app.timeline.service import apply_timeline_commands, get_latest_timeline

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - optional when mcp package not installed
    FastMCP = None  # type: ignore[assignment]


class MCPBearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        if request.url.path.startswith("/mcp") and settings.mcp_access_token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {settings.mcp_access_token}":
                return JSONResponse(status_code=401, content={"error": "unauthorized_mcp_token"})
        return await call_next(request)


def create_mcp_server():
    if FastMCP is None:
        return None

    mcp = FastMCP("kinetiq-studio-mcp", stateless_http=True)

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
    def get_scene(project_id: str, scene_id: str) -> dict[str, Any]:
        """Inspect full details, prompts, and QA scores of a single scene."""
        with SessionLocal() as db:
            doc = get_latest_timeline(db, project_id)
            scene = next((s for s in doc.tracks.visual if s.id == scene_id), None)
            if not scene:
                return {"error": f"Scene {scene_id} not found"}
            return scene.model_dump(mode="json")

    @mcp.tool()
    def apply_editor_commands(project_id: str, expected_revision: int, commands: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply up to 25 validated TimelineCommand edits atomically as Antigravity (author='mcp')."""
        from pydantic import TypeAdapter

        parsed = [TypeAdapter(TimelineCommand).validate_python(c) for c in commands[:25]]
        with SessionLocal() as db:
            updated = apply_timeline_commands(db, project_id, expected_revision, parsed, author="mcp")
            return {"ok": True, "new_revision": updated.revision, "scene_count": len(updated.tracks.visual)}

    return mcp
