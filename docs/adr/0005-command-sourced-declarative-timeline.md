# ADR-0005: Command-Sourced Declarative Timeline Document

- **Status:** Accepted
- **Date:** 2026-11-27

## Context
Supporting manual timeline edits, IndexedDB offline auto-save, multi-step AI Copilot edits with before/after diff previews, Undo/Redo, and external Antigravity MCP edits on raw mutable state quickly leads to race conditions and broken state.

## Decision
- Represent every project as a strictly typed `TimelineDocument` v1 (`project.json`) validated identically by **Pydantic v2** (backend) and **TypeScript + JSON Schema** (`packages/contracts`).
- Express every mutation (UI click, AI Copilot tool call, or Antigravity MCP command) as an immutable **`TimelineCommand`** paired with its inverse command.
- Enforce optimistic concurrency (`If-Match: revision` → `409 Conflict` on mismatch) and persist periodic revision snapshots to Google Drive `history/v{rev}.json`.

## Consequences
- **Positive:** Unlimited deterministic Undo/Redo, safe preview-before-apply for AI edits, and one shared mutation engine across web UI, API, and MCP.
- **Negative:** Every new editor feature must be modeled as a command with an inverse.
