# Kinetiq Studio — System Design Architecture (v1.1)

**Stack:** Next.js (TypeScript) + FastAPI (Python 3.12+) + PostgreSQL / SQLite + Redis (optional in Phase 0) + Google Drive Vault + Google Colab Workers.

---

## 0. Goals, Non-Goals & Quality Targets

### Goals
- Generate **faceless English-narrated educational videos**: 1-minute vertical Reels/TikTok (`9:16`, `1080×1920`) and 5–10+ minute horizontal lessons (`16:9`, `1920×1080` native Full HD, optional `3840×2160` 4K).
- Guide local GPU generation (**LTX-2.5**, **Wan 2.2**, **Qwen-Image**, **FlashVSR+ / SeedVR2**) with frontier AI (**Gemini API via Google AI Pro**, **ChatGPT Plus JSON Co-Director Bridge**, and **Antigravity MCP**).
- Enforce **Automatic Gemini Vision QA + Human-on-the-Loop (HOTL)**: AI checks every keyframe automatically, auto-heals weak frames (up to 2 retries), and escalates uncertain/exhausted scenes to human review without stalling healthy scenes.
- Edit in a **CapCut/Descript-style browser timeline editor** powered by `@remotion/player` and an **AI Editing Copilot** with command-sourced undo/redo.
- Store every finished scene clip, keyframe, timeline revision, and export automatically in **your Google Drive (2 TB)** so zero work is lost across Colab disconnects or browser crashes, then download the final master to your laptop with one click.

### Non-Goals for v1
- Automated direct posting to TikTok or Facebook (exports include a copy-paste Publish Kit for manual upload).
- Lip-synced talking-head avatars (optimized strictly for faceless educational storytelling + diagrams).
- Real-time multi-user concurrent editing (single-owner in Phase 0/1; schema ready for multi-tenant teams later).

### Service-Level Targets
| Requirement | Target |
|---|---|
| **Zero Lost Work** | Every finished scene clip uploads directly to Google Drive (`master/` + `proxy/`) within 60 s of completion |
| **Fault Recovery** | Colab disconnects: interrupted scene lease expires in 300 s and resumes on reconnect; completed scenes are never re-rendered |
| **Output Quality** | Native `1080×1920` / `1920×1080` minimum, optional `3840×2160` 4K, H.264 High (`crf 17`, `yuv420p`), audio normalized to `-14 LUFS` |
| **Editor Responsiveness** | Timeline scrubbing `< 150 ms` via 540p fast-seek proxies cached in browser OPFS |
| **Cost Guardrail** | Stay within `200 Colab Compute Units / month` via L4 FP8 defaults, auto-QA retry cap (`2`), and 15-min idle shutdown |
| **Security** | Least-privilege `drive.file` OAuth scope, AES-256-GCM encrypted refresh tokens, revocable SHA-256 hashed worker/MCP tokens |

---

## 1. System Context & Deployment Phases

### Phase 0: Laptop Local Control Plane + Google Colab + Google Drive (Start Here)
```text
┌──────────────┐   http://localhost:3000    ┌────────────────────────────────────┐
│  Your Browser│◄──────────────────────────►│  Next.js Studio UI (apps/web)      │
│  (OPFS+IDB)  │                            └──────────────────┬─────────────────┘
└──────────────┘                                               │ REST + SSE
                                                               ▼
┌──────────────┐   MCP (Bearer Token)       ┌────────────────────────────────────┐
│  Antigravity │◄──────────────────────────►│  FastAPI Modular Monolith (:8000)  │
└──────────────┘                            │  + Embedded Orchestrator + SQLite  │
                                            └─────────┬─────────────────▲────────┘
                                                      │                 │
┌──────────────┐   Outbound HTTPS via                 │                 │ OAuth 2.0
│ Google Colab │◄──Cloudflare Quick Tunnel────────────┘                 │ Gemini API
│ (L4 / A100)  │   (cloudflared --url http://localhost:8000)            ▼
│ ComfyUI +    │                                            ┌────────────────────┐
│ Resumable    │───8 MiB Chunks (Bearer token + HTTP 308)──►│ Google Cloud       │
│ Uploader     │                                            │ • Drive API v3     │
└──────────────┘                                            │ • Gemini AI Pro    │
                                                            └────────────────────┘
```

### Phase 1: VPS Promotion (After Testing)
Once Phase 0 testing passes, promote the exact same codebase using [`infra/docker-compose.vps.yml`](../infra/docker-compose.vps.yml) and [`infra/Caddyfile`](../infra/Caddyfile):
- **Caddy** handles automatic HTTPS TLS (`studio.yourdomain` → `web:3000`, `api.yourdomain` → `api:8000` with `flush_interval -1` for SSE).
- **PostgreSQL 16** (`FOR UPDATE SKIP LOCKED`) and **Redis 7** (SSE fan-out) replace SQLite.
- **`orchestrator`** runs as a dedicated worker container (`RUN_EMBEDDED_ORCHESTRATOR=false` on `api`).
- Colab connects directly to `https://api.yourdomain` so your laptop can stay off while 10-minute renders complete.

---

## 2. Backend Modular Monolith (`apps/api/app/`)

```text
apps/api/app/
├── core/        config, SQLAlchemy engine (SQLite WAL + Postgres), AES-GCM crypto, trace logging, errors
├── identity/    Google OAuth 2.0 PKCE (drive.file), encrypted credential refresh, /v1/me
├── timeline/    TimelineDocument v1 Pydantic contract, command processor, If-Match concurrency, snapshots
├── director/    BrainProvider (Gemini API + ChatGPT Plus JSON Bridge + OpenAI BYOK), tool registry
├── qa/          HOTL weighted rubric (0–10), auto-heal policy, needs_review escalation
├── jobs/        Durable DAG queue (depends_on, idempotency_key, 300s leases, exponential retry)
├── workers/     Colab/RunPod registration, heartbeat, claim, upload-session, ComfyUI graph compiler
├── storage/     StorageProvider interface → GoogleDriveProvider + LocalVaultProvider (HTTP 308 chunks)
├── projects/    Project CRUD, planning, generation dispatch, copilot proposals, HOTL scene review
├── exports/     Export jobs, Drive range-aware media streaming, laptop downloads, publish kit
├── events/      Transactional Outbox table + SSE endpoint with Last-Event-ID replay
├── mcp/         Streamable HTTP MCP server (/mcp) exposing editor tools to Antigravity
└── orchestrator.py  Lease reaper, offline worker detector, DAG pipeline advancer, vault reconciler
```

---

## 3. Declarative Timeline Document & Command Sourcing

`project.json` (`TimelineDocument` v1, schema in [`packages/contracts/schemas/timeline.v1.schema.json`](../packages/contracts/schemas/timeline.v1.schema.json)) is the single source of truth shared between the **browser `@remotion/player` preview**, the **AI Copilot / Antigravity MCP**, and the **final 1080p/4K Remotion + FFmpeg render**:

- **Optimistic Concurrency:** Every mutation requires `If-Match: <revision>` (or explicit revision field). Conflicting edits return `409 Conflict` so pending local edits can rebase cleanly.
- **Command-Sourced Edits:** Every manual UI change, AI Copilot proposal acceptance, or Antigravity MCP action executes as a typed `TimelineCommand` (`trim_scene`, `split_scene`, `move_scene`, `delete_scene`, `tighten_pauses`, `edit_narration`, `regenerate_clip`, `approve_scene_qa`, `convert_scene_to_diagram`, `insert_diagram`, `add_overlay`, `update_captions`, `mix_audio`) paired with its inverse command for unlimited Undo/Redo.
- **Snapshot Policy:** Full JSON snapshots sync to `Drive/history/v{rev}.json` before every AI batch edit, before every export, and every 20 edits.

---

## 4. Database Schema (12 Core Tables)

Managed via Alembic (`apps/api/alembic/versions/0001_initial_core_schema.py`), compatible with both PostgreSQL 16 and Phase 0 SQLite:

1. `users` — Owner / multi-user identity (`id`, `email`, `google_sub`, `display_name`).
2. `oauth_credentials` — Encrypted Google OAuth refresh token (`drive.file` scope) + expiry.
3. `projects` — Project metadata, `aspect_ratio`, `target_res`, `qa_policy`, `brain_mode`, `drive_folder_id`, `current_revision`.
4. `timeline_revisions` — Immutable revision log (`project_id`, `revision`, `document`, `author`, `command_batch`).
5. `scenes` — Indexed scene projection (`order_index`, `start_ms`, `end_ms`, `kind`, `status`, `qa_score`, `qa_attempts`, asset links).
6. `assets` — Media artifacts (`audio`, `keyframe`, `master`, `proxy`, `export`, `words`, `srt`) with `drive_file_id` / `storage_key`, `sha256`, `size_bytes`, `sync_state`.
7. `jobs` — Durable DAG work units (`idempotency_key` unique, `depends_on` JSON list, `status`, `lease_until`, `attempts`, `max_attempts`, `next_run_at`, `gpu_seconds`, `est_cu`).
8. `workers` — Registered Colab / VPS / RunPod nodes (`token_hash`, `gpu_model`, `vram_gb`, `capabilities`, `status`, `last_heartbeat`, `cu_used_month`).
9. `ai_runs` — Audit log of Gemini / ChatGPT / OpenAI calls (model, tokens, latency, outcome).
10. `qa_reviews` — Keyframe & clip QA scores (`narration_match`, `artifact_free`, `style_consistency`, `safe_zone`), weighted score, verdict, fix action.
11. `exports` — Export jobs (`1080p` / `4k`), `master_asset_id`, `srt_asset_id`, `publish_kit`.
12. `events` — Transactional Outbox (`id` auto-increment sequence, `project_id`, `event_type`, `payload`) powering `GET /v1/events` with `Last-Event-ID` replay.

---

## 5. Human-on-the-Loop (HOTL) Vision QA State Machine

```text
keyframe_qwen completes
        │
        ▼
  qa.keyframe (Gemini Vision)
  Weighted score = 0.40(narration_match) + 0.30(artifact_free)
                 + 0.20(style_consistency) + 0.10(safe_zone)
        │
        ├──[qa_policy == hitl_strict_gate]──────────────────► scene.status = "needs_review"
        │                                                     (human clicks Approve / Edit)
        ├──[score >= 8.0]──► auto-pass ──► unlock i2v job
        │
        └──[score < 8.0]
               ├──[qa_attempts < 2]──► auto-heal (rewrite prompt / reseed / convert_to_diagram)
               │                       and enqueue retry keyframe
               └──[qa_attempts >= 2]─► escalate to scene.status = "needs_review"
                                       (human can Force Approve, Rewrite, or Convert to Diagram)
```

Human override endpoints (`POST /v1/projects/{id}/scenes/{sid}/approve`, `POST .../regenerate`, and `convert_scene_to_diagram`) let you intervene at any time without blocking healthy scenes.

---

## 6. Google Drive Vault & Verified Resumable Upload Protocol

- **Scope:** `openid email https://www.googleapis.com/auth/drive.file` — the app can only access folders/files it created inside `KinetiqStudio/{slug}_{id}/`.
- **Self-Describing Metadata:** Every uploaded file sets Drive `appProperties = {project_id, scene_id, kind, revision, sha256}` so the database can be reconstructed from Drive alone.
- **Direct Worker → Drive Chunks:**
  1. Worker requests `POST /v1/workers/jobs/{id}/upload-session`.
  2. API initializes Drive v3 Resumable Upload (`POST https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable`) and returns `session_uri`, `upload_headers: {"Authorization": "Bearer <short-lived-token>"}`, and `token_refresh_url`.
  3. Worker uploads sequential **8 MiB (`8,388,608` bytes = `32 × 256 KiB`)** chunks via `PUT` with `Content-Range` and `Authorization: Bearer ...`.
  4. On connection drop (`401` refreshes token via API; network drop/5xx queries `Content-Range: bytes */total` → parses `HTTP 308` `Range` header and resumes from the exact next byte).
  5. Worker calls `POST /v1/workers/jobs/{id}/complete` with `sha256` and `size_bytes`. API verifies against Drive/Vault before marking the asset `synced` and allowing the worker to delete its temp file.

---

## 7. Security & Cost Guardrails

- **AI Safety:** Tool calls validated by Pydantic v2 + JSON Schema; capped at `25` commands per turn; any AI turn estimating `> 1.0 CU` returns a pending confirmation proposal (`QuotaConfirmationRequired`) instead of executing silently.
- **Worker & MCP Auth:** Bearer tokens hashed with SHA-256 at rest; workers only reach `/v1/workers/*`; MCP tools require `MCP_ACCESS_TOKEN`.
- **Media Playback:** `<video>` tags fetch `/v1/media/{asset_id}?t=<HMAC-10min-token>` supporting HTTP `206 Partial Content` range seeking.
- **Colab CU Protection:** L4 FP8 (`--lowvram`) + `/free` VRAM clear between clips + 15-minute idle `google.colab.runtime.unassign()` inside `colab_launcher.ipynb`.
