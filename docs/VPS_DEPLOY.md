# Phase 1 VPS Deployment, Cutover & Rollback Runbook

This guide covers promoting **Kinetiq Studio** from **Phase 0 (Laptop + SQLite + Cloudflare Quick Tunnel)** to **Phase 1 (Dedicated Ubuntu VPS + PostgreSQL 16 + Redis 7 + Split Orchestrator + Caddy Automatic TLS)**.

---

## 1. Phase 0 vs. Phase 1 Architecture Differences

| Component | Phase 0 (Laptop Local) | Phase 1 (VPS Production) |
|---|---|---|
| **Control Plane** | `uvicorn` on `localhost:8000` + `cloudflared` tunnel | Docker `api` (`APP_ENV=phase1_vps`, internal port `8000` only) |
| **Orchestrator** | Embedded asyncio loop inside FastAPI | Dedicated `orchestrator` container (`RUN_EMBEDDED_ORCHESTRATOR=false` on `api`) |
| **Database** | SQLite WAL (`kinetiq.db`) | PostgreSQL 16 (`FOR UPDATE SKIP LOCKED`, internal network only) |
| **SSE Fan-Out** | Single-process DB polling | Redis 7 + Transactional Outbox (`flush_interval -1` in Caddy) |
| **TLS & Ingress** | Cloudflare Quick Tunnel ephemeral URL | Caddy 2 (`infra/Caddyfile`) on ports `80`/`443` with automatic Let's Encrypt TLS |
| **GPU Workers** | Colab points to `*.trycloudflare.com` | Colab / RunPod points directly to `https://<API_DOMAIN>` |

---

## 2. VPS Prerequisites

1. **Server Sizing:** Ubuntu 22.04/24.04 LTS, **4 vCPU / 8 GB RAM**, 40+ GB SSD.
2. **Installed Tooling:**
   - Docker Engine + Docker Compose v2 (`docker compose version`)
   - Node.js 22+ (`node -v`) — required by `scripts/check-env.mjs` and `scripts/deploy-vps.sh`
   - `curl` and `git`
3. **Firewall (`ufw`):** Expose **only** SSH (`22/tcp`), HTTP (`80/tcp`), and HTTPS (`443/tcp`). Do **not** expose `5432`, `6379`, `8000`, or `3000`:
   ```bash
   sudo ufw allow 22/tcp
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw enable
   ```
4. **DNS Records:** Create `A` (and optional `AAAA`) records pointing both subdomains to your VPS public IP before running deployment:
   - `STUDIO_DOMAIN` (for example `studio.yourdomain.com`) → `<VPS_IP>`
   - `API_DOMAIN` (for example `api.yourdomain.com`) → `<VPS_IP>`
5. **Google Cloud OAuth (Least-Privilege `drive.file`):**
   - In Google Cloud Console → **OAuth 2.0 Client ID**, add Authorized Redirect URI:
     `https://<API_DOMAIN>/v1/auth/google/callback`
   - Keep scopes strictly limited to `openid`, `email`, and `https://www.googleapis.com/auth/drive.file`.

---

## 3. Generate Production Secrets & Configure `.env`

Generate unique, high-entropy secrets on the VPS (never reuse Phase 0 defaults or copy the same value across keys):

```bash
openssl rand -base64 32   # -> ENCRYPTION_MASTER_KEY
openssl rand -hex 32      # -> SESSION_SECRET
openssl rand -hex 32      # -> MEDIA_SIGNING_SECRET
openssl rand -base64 24   # -> POSTGRES_PASSWORD
openssl rand -hex 32      # -> MCP_ACCESS_TOKEN (if exposing /mcp)
```

Create `/opt/kinetiq-studio/.env` (and lock permissions with `chmod 600 .env`):

```env
# --- Phase 1 Runtime ---
APP_ENV=phase1_vps
RUN_EMBEDDED_ORCHESTRATOR=false

# --- Database & Redis (compose service hostnames) ---
POSTGRES_PASSWORD=<generated-24B-base64-password>
DATABASE_URL=postgresql+psycopg://postgres:<same-postgres-password>@postgres:5432/kinetiq
REDIS_URL=redis://redis:6379/0

# --- Cryptographic Secrets (ALL MUST BE UNIQUE & >= 32 CHARS) ---
ENCRYPTION_MASTER_KEY=<generated-32B-base64-key>
SESSION_SECRET=<generated-64-hex-secret>
MEDIA_SIGNING_SECRET=<generated-64-hex-secret>
MCP_ACCESS_TOKEN=<generated-64-hex-token>

# --- Caddy Automatic TLS Domains (bare FQDNs, no https://) ---
STUDIO_DOMAIN=studio.yourdomain.com
API_DOMAIN=api.yourdomain.com

# --- Public URLs & CORS ---
PUBLIC_API_URL=https://api.yourdomain.com
NEXT_PUBLIC_API_URL=https://api.yourdomain.com
PUBLIC_WEB_URL=https://studio.yourdomain.com
CORS_ORIGINS=["https://studio.yourdomain.com"]

# --- Storage Vault & Google OAuth ---
STORAGE_PROVIDER=drive
OWNER_GOOGLE_EMAIL=you@gmail.com
GOOGLE_CLIENT_ID=<your-client-id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<your-client-secret>
GOOGLE_REDIRECT_URI=https://api.yourdomain.com/v1/auth/google/callback

# --- AI Brain Layer ---
GEMINI_API_KEY=<your-gemini-api-key>
GEMINI_PRO_MODEL=gemini-2.5-pro
GEMINI_FLASH_MODEL=gemini-2.5-flash
OPENAI_API_KEY=
```

Lock file permissions and validate with `scripts/check-env.mjs`:

```bash
chmod 600 .env
node scripts/check-env.mjs --file .env
```

`check-env.mjs` exits `0` only when all required secrets (`ENCRYPTION_MASTER_KEY`, `SESSION_SECRET`, `MEDIA_SIGNING_SECRET`, `POSTGRES_PASSWORD`), Caddy domains (`STUDIO_DOMAIN`, `API_DOMAIN`), HTTPS URLs, `CORS_ORIGINS`, and Google OAuth redirect settings pass production checks. It never prints secret values.

---

## 4. Phase 0 → Phase 1 Cutover Procedure

1. **Quiesce Phase 0 laptop workers:** Let active Colab/media/render jobs finish and stop `cloudflared` and local `uvicorn`.
2. **Clone / pull the release commit on the VPS:**
   ```bash
   git fetch origin
   git checkout main
   git pull --ff-only origin main
   ```
3. **Run deployment preflight (`--dry-run`):**
   ```bash
   ./scripts/deploy-vps.sh --env-file .env --dry-run
   ```
   This validates `.env`, `infra/Caddyfile`, merged Docker Compose config (`docker-compose.yml` + `infra/docker-compose.vps.yml`), and DNS resolution without touching containers.
4. **Execute the deployment:**
   ```bash
   ./scripts/deploy-vps.sh --env-file .env
   ```
   What `deploy-vps.sh` performs in order:
   - Re-runs `node scripts/check-env.mjs --file .env`.
   - Records the previous deployed Git SHA (`storage/backups/last_deploy_sha.txt`) and takes a pre-deploy `pg_dump -Fc` snapshot (`storage/backups/kinetiq_<UTC_TS>.dump`) if `postgres` is already running.
   - Builds images (`docker compose build`) and starts `postgres` + `redis`, waiting for `pg_isready` and `redis-cli ping`.
   - Runs `alembic upgrade head` in a one-off `api` container before bringing up traffic-serving containers.
   - Starts `api`, `orchestrator`, `web`, and `caddy`, validates `/etc/caddy/Caddyfile`, and verifies internal + external HTTPS `/health` and `/ready` endpoints.
5. **Re-authenticate Google Drive Vault on VPS:**
   - Open `https://<STUDIO_DOMAIN>/settings` and complete Google OAuth sign-in so the VPS encrypts your refresh token under the new `ENCRYPTION_MASTER_KEY`.
6. **Cut over Colab GPU Workers:**
   - In `https://<STUDIO_DOMAIN>/workers`, generate a fresh VPS `WORKER_TOKEN`.
   - Update Colab Secrets: `API_BASE_URL=https://<API_DOMAIN>` and `WORKER_TOKEN=<new-token>`.
   - Retire the Phase 0 `cloudflared` tunnel.

---

## 5. Post-Cutover Verification Checklist

Run these checks from a laptop or jump host after `deploy-vps.sh` exits `0`:

```bash
# 1. API health and DB readiness over HTTPS
curl -fsS https://api.yourdomain.com/health
curl -fsS https://api.yourdomain.com/ready

# 2. Web Studio SSR response
curl -fsSI https://studio.yourdomain.com/ | head -n 5

# 3. Verify Caddy disables buffering on SSE (/v1/events)
curl -N --max-time 5 -i https://api.yourdomain.com/v1/events

# 4. Container status on VPS (no internal ports exposed publicly)
docker compose --env-file .env -f docker-compose.yml -f infra/docker-compose.vps.yml ps
```

Finally, run a `--mock` GPU smoke test from a workstation against the VPS (`API_BASE_URL=https://api.yourdomain.com WORKER_TOKEN=<token> python workers/gpu/runner.py --profile ltx25_pro --mock`) and confirm a test project moves through keyframe → HOTL approval → clip → export.

---

## 6. Rollback Runbook

### Scenario A — Application Code Rollback (No Schema Change)

If the new release fails smoke tests and no destructive migration occurred:

```bash
# 1. Identify previous healthy SHA recorded by deploy-vps.sh
PREV_SHA="$(cat storage/backups/last_deploy_sha.txt)"
git checkout "$PREV_SHA"

# 2. Rebuild and restart without touching DB data
./scripts/deploy-vps.sh --env-file .env --skip-backup
```

### Scenario B — Database / Migration Rollback (With `pg_restore`)

If `alembic upgrade head` or post-migration behavior corrupts state, restore the exact pre-deploy dump created in `storage/backups/`:

```bash
COMPOSE_CMD="docker compose --env-file .env -f docker-compose.yml -f infra/docker-compose.vps.yml"
LATEST_DUMP="$(ls -1t storage/backups/kinetiq_*.dump | head -n 1)"

# 1. Stop writers before restoring
$COMPOSE_CMD stop api orchestrator web

# 2. Restore PostgreSQL custom-format dump cleanly
$COMPOSE_CMD exec -T postgres pg_restore -U postgres -d kinetiq --clean --if-exists < "$LATEST_DUMP"

# 3. Check out previous healthy commit and re-deploy
PREV_SHA="$(cat storage/backups/last_deploy_sha.txt)"
git checkout "$PREV_SHA"
./scripts/deploy-vps.sh --env-file .env --skip-backup
```

### Scenario C — Emergency Fallback to Phase 0 Laptop

Because every synced scene clip, keyframe, timeline snapshot, and export lives in Google Drive (`KinetiqStudio/{slug}_{id}/`) with self-describing `appProperties`, you can immediately fall back to your Phase 0 laptop:

1. Stop VPS containers (`$COMPOSE_CMD down`) so no duplicate worker leases run.
2. Start local FastAPI + Next.js (`docs/PHASE0_SETUP.md`) and `cloudflared tunnel --url http://localhost:8000`.
3. Point Colab Secrets `API_BASE_URL` back to the `trycloudflare.com` URL.

---

## 7. Secret Rotation & Ongoing Backups

| Secret | Rotation Impact & Steps |
|---|---|
| `SESSION_SECRET` | Invalidates active browser session cookies; owner signs in again via Google OAuth. |
| `MEDIA_SIGNING_SECRET` | Invalidates short-lived (`10 min`) `/v1/media/{id}?t=...` URLs; reload Studio UI to mint fresh URLs. |
| `ENCRYPTION_MASTER_KEY` | **Decrypts `oauth_credentials`:** rotating this key invalidates stored encrypted OAuth tokens — re-run Google OAuth in `/settings` immediately after rotating. |
| `POSTGRES_PASSWORD` | Update in Postgres (`ALTER USER postgres WITH PASSWORD ...`) and `.env` together, then redeploy. |

**Recommended nightly backup cron (host crontab):**

```cron
0 3 * * * cd /opt/kinetiq-studio && docker compose --env-file .env -f docker-compose.yml -f infra/docker-compose.vps.yml exec -T postgres pg_dump -U postgres -Fc kinetiq > storage/backups/nightly_$(date -u +\%Y\%m\%d).dump && find storage/backups -name 'nightly_*.dump' -mtime +14 -delete
```
