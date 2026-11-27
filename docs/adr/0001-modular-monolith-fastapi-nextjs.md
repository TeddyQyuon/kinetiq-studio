# ADR-0001: Modular Monolith (FastAPI + Next.js) Over Microservices

- **Status:** Accepted
- **Date:** 2026-11-27

## Context
The platform requires a responsive browser-based multi-track video editor, AI orchestration (Gemini, ChatGPT Plus JSON bridge, Antigravity MCP), Google Drive resumable upload brokering, and GPU worker coordination. Splitting these into separate microservices (auth service, timeline service, AI service, storage service) would multiply deployment complexity, cross-service auth, and schema drift for a solo owner / small team.

## Decision
Use a **two-language modular monolith**:
- **`apps/web` (TypeScript + Next.js App Router + Remotion)** for the studio UI, timeline editor, and preview rendering.
- **`apps/api` (Python 3.12+ + FastAPI + Pydantic v2 + SQLAlchemy 2.0)** organized into strict internal domain modules (`identity`, `projects`, `timeline`, `director`, `qa`, `jobs`, `workers`, `storage`, `exports`, `events`, `mcp`) plus a dedicated `orchestrator` process entry point sharing the same codebase.

## Consequences
- **Positive:** Single API deployment, transactional consistency across timelines/jobs/events, zero cross-service network hops, and effortless Phase 0 local laptop execution.
- **Negative:** GPU inference and heavy media rendering must remain isolated in outbound-polling worker processes (`workers/gpu`, `workers/media`, `workers/render`) so the API stays fast and stateless.
