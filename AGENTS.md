# AGENTS.md — Kinetiq Studio Engineering Constitution

This repository is built using a **parallel multi-agent enterprise workflow** combining **ChatGPT Business (Codex Cloud + `@codex review`)** and **Google Antigravity (`/teamwork`, `/boost`, `/goal`, Git Worktrees)**.

Every AI agent and human contributor **must** follow these boundaries and invariants.

---

## 1. Architecture & Master Plan
- **System Architecture:** `docs/ARCHITECTURE.md`
- **30-Day Enterprise Multi-Squad Plan:** `docs/ENTERPRISE_MULTI_AGENT_PLAN.md`
- **Phase 0 Setup Guide:** `docs/PHASE0_SETUP.md`
- **ADRs:** `docs/adr/0001-*.md` through `0007-*.md`

---

## 2. Six Strict Squad Ownership Zones (Zero-Overlap Rule)

Before editing any file, check which **Squad** your assigned GitHub Issue belongs to. **NEVER modify files outside your squad's allowed paths.** If you need a shared contract or schema change, stop and coordinate via a `squad:contracts` PR first.

| Squad Label | Allowed Paths (Exclusive Write Zone) | Primary Agent Fit |
| :--- | :--- | :--- |
| `squad:contracts` | `packages/contracts/**`, `apps/api/app/models.py`, `apps/api/alembic/**`, `apps/api/app/main.py`, `package.json` | Human Architect / `/boost` |
| `squad:control-plane` | `apps/api/app/core/**`, `apps/api/app/identity/**`, `apps/api/app/storage/**`, `apps/api/app/jobs/**`, `apps/api/app/events/**`, `apps/api/app/orchestrator.py` | ChatGPT Codex Cloud |
| `squad:ai-director` | `apps/api/app/director/**`, `apps/api/app/qa/**`, `apps/api/app/mcp/**`, `apps/api/app/projects/**`, `apps/api/app/timeline/**` | Codex Cloud / Antigravity |
| `squad:workers` | `workers/**`, `apps/api/app/workers/**`, `apps/api/app/exports/**` | ChatGPT Codex Cloud |
| `squad:web-studio` | `apps/web/**`, `packages/remotion-studio/**` | Antigravity (+ Browser Subagent) |
| `squad:qa-devops` | `.github/**`, `infra/**`, `apps/api/tests/**`, `docs/**`, `scripts/**` | Antigravity / Codex Review |

---

## 3. Non-Negotiable Technical Invariants

1. **Remotion Version Pin:** `remotion` and `@remotion/player` MUST stay pinned to the exact version `"4.0.547"` (no `^` caret) in both `packages/remotion-studio/package.json` and `apps/web/package.json`.
2. **Google Drive Resumable Chunk Rule:** Every non-final chunk uploaded by workers MUST be an exact multiple of `256 KiB` (`262,144` bytes; default `8 MiB`) and carry `Authorization: Bearer <token>` on `PUT` requests.
3. **Timeline Concurrency:** Never mutate `TimelineDocument` without checking `If-Match` / `expected_revision` (return `409 Conflict` on mismatch) and pairing every `TimelineCommand` with an inverse command.
4. **Dual-Database Compatibility:** All SQLAlchemy queries and Alembic migrations MUST work on both **Phase 0 SQLite (WAL)** and **Phase 1 PostgreSQL 16** (e.g., `with_for_update(skip_locked=True)` only on Postgres, CAS `UPDATE` fallback on SQLite).
5. **Terms-of-Service & Security:** Never scrape or automate consumer ChatGPT/Gemini web logins or hijack CLI OAuth sessions. Keep Google OAuth restricted to `openid email https://www.googleapis.com/auth/drive.file`. Never commit `.env`, tokens, or `.db` files.
6. **Scope Discipline:** Keep changes strictly scoped to the assigned Issue. Do not refactor unrelated modules or change dependency managers (`npm` workspaces on Node 22, `pip` on Python 3.12).

---

## 4. Git, Branching & PR Conventions

- **Branch Naming:**
  - `feat/sq<issue>-<short-slug>` (e.g., `feat/sq2-media-worker-kokoro`)
  - `fix/sq<issue>-<short-slug>`
- **Commit Format (Conventional Commits):**
  - `feat(workers): add kokoro per-scene tts and whisperx alignment`
  - `fix(control-plane): handle expired drive upload token refresh`
- **Every PR must:**
  1. Reference its Issue (`Closes #N`).
  2. Touch only its Squad's allowed paths.
  3. Pass all pre-PR checks locally and in GitHub Actions CI.

---

## 5. Mandatory Verification Commands (Before Opening a PR)

### TypeScript & Web (`Node 22+`)
```bash
npm run typecheck
npm run build
```

### FastAPI & Workers (`Python 3.12+`)
```bash
ruff check apps/api workers
cd apps/api && STORAGE_PROVIDER=local RUN_EMBEDDED_ORCHESTRATOR=false pytest -v
```

---

## 6. Code Review Rules (for `@codex review` & Human Reviewers)
Flag as **P0 / P1 Blocking** if a PR:
- Edits files outside its assigned Squad directory without a linked `squad:contracts` PR.
- Breaks the `"4.0.547"` Remotion exact version pin or introduces `next/font/google` offline build hangs.
- Introduces SQLite-breaking Postgres-only syntax without a dialect branch (or vice versa).
- Breaks Google Drive 256 KiB chunk alignment or strips `Authorization` headers on chunk `PUT`s.
- Bypasses `If-Match` revision checking, HOTL QA retry limits (`<= 2`), or Copilot `est_cu > 1.0` spend confirmation.
