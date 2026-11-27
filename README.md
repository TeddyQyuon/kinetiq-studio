# Kinetiq Studio — Enterprise AI Faceless Educational Video Platform

An AI-directed, fault-tolerant educational video studio built for **native 1080p and 4K faceless videos** (1-minute vertical Reels for TikTok/Facebook and 5–10+ minute horizontal lessons).

- **Frontend & Browser Video Editor:** Next.js (App Router, TypeScript strict) + `@remotion/player` + Command-Sourced Timeline + AI Copilot
- **Control Plane & Orchestrator:** FastAPI (Python 3.12+) + Pydantic v2 + SQLAlchemy 2.0 + Alembic + Streamable HTTP `/mcp`
- **AI Brain Layer:** Gemini API (`Google AI Pro` credits) + **Auto Vision QA (Human-on-the-Loop)** + **ChatGPT Plus JSON Co-Director Bridge** + **Antigravity MCP**
- **GPU Workers:** Google Colab (`L4` / `A100`) running Headless ComfyUI (`LTX-2.5`, `Wan 2.2`, `Qwen-Image`, `FlashVSR+ / SeedVR2`)
- **Fault-Tolerant Vault:** Auto-uploads every finished scene (`master/` + `540p proxy/`), timeline revision, and final MP4 directly to **your Google Drive** via 8 MiB resumable chunks

---

## Documentation

- [**System Design Architecture (v1.1)**](docs/ARCHITECTURE.md)
- [**Phase 0 Laptop Setup Guide**](docs/PHASE0_SETUP.md)
- **Architecture Decision Records (`docs/adr/`):**
  - [ADR-0001: Modular Monolith (FastAPI + Next.js)](docs/adr/0001-modular-monolith-fastapi-nextjs.md)
  - [ADR-0002: Durable DB Job Queue & Transactional Outbox](docs/adr/0002-durable-db-job-queue-and-transactional-outbox.md)
  - [ADR-0003: Google Drive Vault & Dual-Rendition Proxies](docs/adr/0003-google-drive-vault-and-dual-rendition-proxies.md)
  - [ADR-0004: Automatic HOTL Vision QA & Hybrid Scene Routing](docs/adr/0004-hotl-vision-qa-and-hybrid-scene-routing.md)
  - [ADR-0005: Command-Sourced Declarative Timeline](docs/adr/0005-command-sourced-declarative-timeline.md)
  - [ADR-0006: Unified Remotion Preview & Final Render](docs/adr/0006-remotion-unified-preview-and-render.md)
  - [ADR-0007: Multi-Brain Director, ChatGPT Bridge & Antigravity MCP](docs/adr/0007-multi-brain-director-chatgpt-bridge-and-mcp.md)

---

## Repository Layout

```text
kinetiq-studio/
├── apps/
│   ├── web/                  Next.js Studio UI, @remotion/player Editor, HOTL QA Board & Copilot
│   └── api/                  FastAPI Modular Monolith, Alembic, Orchestrator, HOTL QA & /mcp
├── packages/
│   ├── contracts/            Canonical TimelineDocument v1 JSON Schema, TS Types & Command Engine
│   └── remotion-studio/      Shared React Video Compositions (BarCompare, StepBreakdown, KineticCaptions)
├── workers/
│   ├── agent-core/           Shared Python Worker Client & 8 MiB Google Drive Resumable Uploader
│   ├── gpu/                  Headless ComfyUI Runner + colab_launcher.ipynb + RunPod Dockerfile
│   ├── media/                Kokoro-82M TTS + WhisperX Alignment Worker Specs
│   └── render/               Remotion + FFmpeg 1080p/4K Mastering Worker Specs
├── infra/                    Phase 1 VPS Docker Compose Override + Caddyfile (Auto-TLS)
└── docs/                     System Architecture, Phase 0 Runbook & ADRs
```

---

## Phase 0 Quickstart (Run Locally Today)

### 1. Run the Studio Web App (Node.js 20+)
Works immediately with a full interactive Demo Workspace even before starting the Python API:

```bash
npm install
npm run dev:web
```
Open **http://localhost:3000**.

### 2. Run the FastAPI Control Plane (Python 3.12+)
```bash
cp .env.example .env
cd apps/api
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

See [**docs/PHASE0_SETUP.md**](docs/PHASE0_SETUP.md) for Google Drive OAuth, `cloudflared` tunneling, `--mock` dry runs, and connecting [`workers/gpu/colab_launcher.ipynb`](workers/gpu/colab_launcher.ipynb).
