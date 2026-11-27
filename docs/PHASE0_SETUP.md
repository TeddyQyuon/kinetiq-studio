# Phase 0 Setup Guide (Laptop + Google Colab + Google Drive)

Phase 0 lets you run the entire **Kinetiq Studio** locally on your laptop with **$0/month extra server hosting**, while still using **Google Colab (L4/A100)** for GPU generation, **Gemini (Google AI Pro)** for direction & Vision QA, and **Google Drive (2 TB)** for fault-tolerant storage.

Once tested, you can promote the exact same stack to a VPS using [`infra/docker-compose.vps.yml`](../infra/docker-compose.vps.yml).

---

## Prerequisites

1. **Node.js 20+** (for `apps/web` and `packages/remotion-studio`)
2. **Python 3.12+** (for `apps/api` and `workers/agent-core`) — *or* **Docker Desktop**
3. **Cloudflared** (`cloudflared` CLI) to expose `localhost:8000` over HTTPS so Google Colab can poll jobs
4. **Google AI Pro** account (includes 200 Colab Compute Units/mo + 2 TB Drive + AI Studio Gemini credits)

---

## Step 1: Configure Google Cloud OAuth (`drive.file`) & Gemini API

1. Open **Google AI Studio** (signed into your Google AI Pro account) → redeem your monthly developer credits if applicable → create a `GEMINI_API_KEY`.
2. Open **Google Cloud Console** → create a project (e.g., `KinetiqStudio`).
3. Enable **Google Drive API**.
4. Configure **OAuth Consent Screen** (External → Testing → add your Gmail as a Test User) with scopes:
   - `openid`
   - `email`
   - `https://www.googleapis.com/auth/drive.file`
5. Create **OAuth Client ID** (Web application):
   - Authorized redirect URI: `http://localhost:8000/v1/auth/google/callback`
6. Copy `.env.example` to `.env` in the repo root and fill in:
   ```env
   GEMINI_API_KEY=your-gemini-api-key
   GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-client-secret
   STORAGE_PROVIDER=drive   # or keep "local" to test offline first!
   ```

---

## Step 2: Start the Control Plane & Studio Web App

### Mode A — Zero-Docker Local Mode (Fastest on Windows/Mac)

**Terminal 1 — FastAPI Control Plane + Embedded Orchestrator:**
```bash
cd apps/api
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Mac/Linux:          source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Terminal 2 — Next.js Studio UI:**
```bash
npm install
npm run dev:web
```

Open **http://localhost:3000** (API docs available at **http://localhost:8000/docs**).

### Mode B — Docker Compose Local Mode (Postgres + Redis + Split Orchestrator)
```bash
docker compose up --build
```

---

## Step 3: Test End-to-End Without a GPU (`--mock` Mode)

Verify the full job queue, lease recovery, 8 MiB resumable upload, HOTL QA flow, and live SSE timeline updates on your laptop before spending any Colab Compute Units:

1. In the Web UI, go to **GPU Workers → Connect Colab** and create a Worker Token.
2. Run the agent locally in `--mock` mode:
   ```bash
   cd workers/gpu
   pip install -r requirements.txt
   pip install -e ../agent-core
   API_BASE_URL=http://localhost:8000 WORKER_TOKEN=<your_token> python runner.py --profile ltx25_pro --mock
   ```

---

## Step 4: Connect Your Real Google Colab L4 / A100 GPU

1. Expose your local FastAPI server over HTTPS using **Cloudflare Quick Tunnel** (keep your laptop awake while Colab is rendering in Phase 0):
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
   Copy the generated `https://something.trycloudflare.com` URL.
2. Upload [`workers/gpu/colab_launcher.ipynb`](../workers/gpu/colab_launcher.ipynb) to Google Colab.
3. Select **Runtime → Change runtime type → L4 GPU** (best Compute Unit efficiency) or **A100**.
4. Enable **Background Execution** in Colab Pro/AI Pro.
5. Open Colab **Secrets (🔑 icon)** and set:
   - `API_BASE_URL` = `https://something.trycloudflare.com`
   - `WORKER_TOKEN` = your token from `/workers`
   - `HF_TOKEN` = your Hugging Face token *(required only for gated `Lightricks/LTX-2.5`; `wan22_fast` is Apache 2.0 ungated)*
6. Run all 5 cells. Colab boots headless ComfyUI (`127.0.0.1:8188`), downloads only your chosen profile weights via `hf_transfer`, polls your API over HTTPS, and auto-unassigns after 15 minutes of inactivity.

---

## Step 5: Promote to Phase 1 VPS (When Ready)

Once you are happy with the workflow:
1. Provision a small Ubuntu VPS (4 vCPU / 8 GB RAM, e.g., Hetzner/DigitalOcean) and point two DNS A records (`studio.yourdomain.com`, `api.yourdomain.com`).
2. Update `.env` with `APP_ENV=phase1_vps`, strong random secrets (`ENCRYPTION_MASTER_KEY`, `SESSION_SECRET`, `MEDIA_SIGNING_SECRET`), `RUN_EMBEDDED_ORCHESTRATOR=false`, and your domain URLs (`PUBLIC_API_URL`, `PUBLIC_WEB_URL`, `GOOGLE_REDIRECT_URI`).
3. Start the VPS stack with Caddy automatic TLS:
   ```bash
   docker compose -f docker-compose.yml -f infra/docker-compose.vps.yml up -d --build
   ```
