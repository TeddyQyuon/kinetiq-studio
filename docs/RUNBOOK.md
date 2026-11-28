# Kinetiq Studio day-one runbook

Run commands from the repository root unless a step changes directories. Start with one short project and check that its MP4 and subtitles download before committing GPU time to a long lesson.

## 1. Start the laptop studio

Install Git and Node.js 22+ with npm. For the native launcher, install Python 3.12+, FFmpeg and ffprobe. For the container stack, install Docker with Compose v2. The native launcher can create a private root `.env` with generated secrets on its first run. Keep `APP_ENV=phase0_local` and `STORAGE_PROVIDER=local` for the first smoke test.

| Platform | Launch command |
| --- | --- |
| Windows PowerShell | `.\scripts\dev-local.ps1` |
| macOS / Linux | `bash scripts/dev-local.sh` |

Leave the launcher running. It starts the API, Studio and local media/render workers. The native media worker uses mock speech by default. Add `--real-media` on either platform for Kokoro and WhisperX. Open **http://localhost:3000** and run this in a second terminal.

```sh
node scripts/smoke-test.mjs
```

**Docker alternative.** Configure the root `.env` from `.env.example` with your own secrets. The base startup command is `docker compose up -d --build`. Use this variant on a local-only laptop so the Next.js status probe reaches the API inside Compose while browsers use the published port.

```sh
docker compose -f docker-compose.yml -f - up -d --build <<'YAML'
services:
  web:
    environment:
      API_INTERNAL_URL: http://api:8000
YAML
docker compose ps -a
node scripts/smoke-test.mjs
```

`worker-bootstrap` should exit 0 after provisioning credentials. `api`, `media-worker` and `render-worker` should become healthy or running. For real speech in Compose, set `MEDIA_INSTALL_MODELS=true` and `MEDIA_MOCK_MODE=false` before rebuilding. Check **Workers → System Health & Telemetry**, and verify the media and render registrations. A web-only smoke can pass while Studio is in Demo Mode. Check `/api/backend-status` for `"online":true` when workers are expected.

## 2. Connect Drive and Colab

1. Enable the Google Drive API for your Google Cloud project. Configure a Web OAuth client with `openid`, `email` and `https://www.googleapis.com/auth/drive.file`. Add your account as a test user if the consent screen is in testing. Register the exact local callback `http://localhost:8000/v1/auth/google/callback`.
2. In the root `.env`, set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` and `STORAGE_PROVIDER=drive`. Restart the API and orchestrator with that configuration. In Studio, open **Settings → Connect Google Drive**, sign in and verify Drive shows connected.
3. For a temporary laptop connection, install cloudflared and run the following in a separate terminal. **Phase 0 grants owner access to anyone who can reach the API.** A Quick Tunnel publishes that API at its generated URL. Use a disposable local vault for a tunnel rehearsal. For projects connected to your personal Drive, use the authenticated Phase 1 API below for Colab.

   ```sh
   cloudflared tunnel --url http://localhost:8000
   ```

4. Set `PUBLIC_API_URL` to the external HTTPS API origin and restart the API before generating jobs. Colab needs that origin for signed input and upload URLs. The local browser can continue using `NEXT_PUBLIC_API_URL=http://localhost:8000`. Keep the laptop awake and the tunnel process running during a rehearsal. Update the origin and Colab secret if the tunnel URL changes.
5. In **Workers**, mint a GPU token for `ltx25_pro`. Open the launcher in Google Colab, select an L4 or A100 runtime, and enable notebook access to the Colab secrets `API_BASE_URL` and `WORKER_TOKEN`. Set `HF_TOKEN` when model access requires it. Select the matching `PROFILE` in cell 1 and run cells 1–5. Wait for the worker to show online before choosing **Generate All Scenes**. [Colab launcher](../workers/gpu/colab_launcher.ipynb)
6. Cell 6 is optional. Choose `media` with a separate `MEDIA_WORKER_TOKEN` for speech/alignment, or `colab_ai` for the notebook text helper. Approve a flagged keyframe and clip on the Plan board. In the editor, check narration and captions, then queue **Render Native 1080p MP4** under Exports. When the render is ready, download both the MP4 and SRT.

## 3. Connect Antigravity

Set a random `MCP_ACCESS_TOKEN` in the API environment and restart the API. In Studio Settings, choose **Copy MCP Config**. Paste the configuration into Antigravity's MCP settings, replace `<YOUR_MCP_ACCESS_TOKEN>` privately and confirm the URL is the intended API origin followed by `/mcp`. The transport uses HTTP with `Authorization: Bearer <token>`. Reload the connection and call the read-only `get_timeline_summary` with a project ID. Compare its revision with the editor.

## 4. Deploy and check the VPS

On the VPS, install Docker with Compose v2 (including `!reset` support), Node.js 22+, Git and curl. Point the Studio and API DNS records at the VPS and allow public HTTP/HTTPS to Caddy. Configure the production root `.env` using the production template. Use distinct secrets for encryption, sessions, media signing, MCP and PostgreSQL. Preserve the encryption key with the database backup. [Production environment template](VPS_DEPLOY.md#3-generate-production-secrets--configure-env)

Set `APP_ENV=phase1_vps`, `RUN_EMBEDDED_ORCHESTRATOR=false`, the PostgreSQL and Redis URLs, and your `OWNER_GOOGLE_EMAIL`. Match the HTTPS web/API origins, CORS origin, domains and Google OAuth callback to your actual hosts. Register the production callback with Google.

```sh
chmod 600 .env
node scripts/check-env.mjs --file .env
bash scripts/deploy-vps.sh --env-file .env --dry-run
bash scripts/deploy-vps.sh --env-file .env
docker compose -f docker-compose.yml -f infra/docker-compose.vps.yml ps -a
node scripts/smoke-test.mjs \
  --api-url "$(node --env-file=.env -p 'process.env.PUBLIC_API_URL')" \
  --web-url "$(node --env-file=.env -p 'process.env.PUBLIC_WEB_URL')" --timeout-ms 10000
```

`check-env.mjs` validates Phase 1 settings. The smoke CLI probes both services by default. Use `--api-only` or `--web-only` to isolate a failing service. Expect `/health` to report `ok` and `/ready` to report `ready`. A signed-out 401 on metrics, identity or projects is accepted only when health identifies `phase1_vps`. Sign in as the configured owner, reconnect Drive, and point Colab at the VPS API with a token from that deployment.

## 5. Recover a project or roll back PostgreSQL

**Recover the latest project from the vault.** Drive keeps the manifest in `KinetiqStudio/{slug}_{id}/project.json` and immutable timelines in `history/vN.json`. The local vault uses `{project_id}/project.json` and the same history layout. Read the project and owner IDs from the manifest. Use the matching owner and OAuth client for direct Drive recovery, or download the project folder, rename it to its project ID and place it under a local vault root.

This example rebuilds into a **fresh SQLite file** from that local copy. Run it with the API Python environment active. When prompted, enter the absolute directory containing the project-ID folder and the ID itself.

```bash
(
  set -eu
  read -rp 'Vault root: ' LOCAL_VAULT_DIR
  read -rp 'Project ID: ' PROJECT_ID
  export LOCAL_VAULT_DIR PROJECT_ID STORAGE_PROVIDER=local
  recovery_dir=$(mktemp -d)
  export DATABASE_URL="sqlite:///$recovery_dir/recovered.sqlite"
  cd apps/api
  python - <<'PY'
import asyncio, json, os
from pathlib import Path
from app.core.db import SessionLocal, init_db
from app.storage.snapshots import rebuild_project_from_vault
from app.timeline.service import get_latest_timeline

pid = os.environ["PROJECT_ID"]
folder = Path(os.environ["LOCAL_VAULT_DIR"]) / pid
head = json.loads((folder / "project.json").read_bytes())
init_db()
with SessionLocal() as db:
    proj = asyncio.run(rebuild_project_from_vault(db, project_id=pid, user_id=head["owner"]["id"]))
    doc = get_latest_timeline(db, pid)
    history = json.loads((folder / "history" / f"v{doc.revision}.json").read_bytes())
    assert doc.model_dump(mode="json") == head["document"] == history
    print(proj.title, "revision", doc.revision, "scenes", len(doc.tracks.visual))
PY
  echo "Recovered database: $recovery_dir/recovered.sqlite"
)
```

The helper restores the project, head revision and scene projection. Use a PostgreSQL backup for the full asset catalogue, credentials, jobs and export records.

**Roll back a failed VPS deployment.** Select the pre-deploy dump and matching healthy commit from that deployment. Restoring rewinds all database writes since the dump. Pause external workers and stop writers before restoring. Keep an independent copy of the failed state.

```bash
(
  set -eu
  dc=(docker compose --env-file .env -f docker-compose.yml -f infra/docker-compose.vps.yml)
  read -rp 'Path to the pre-deploy .dump: ' dump
  read -rp 'Verified healthy commit (see storage/backups/last_deploy_sha.txt): ' good_sha
  git rev-parse --verify "$good_sha^{commit}" >/dev/null
  "${dc[@]}" exec -T postgres pg_restore --list < "$dump" >/dev/null
  "${dc[@]}" stop web api orchestrator media-worker render-worker
  "${dc[@]}" exec -T postgres pg_dump -U postgres -Fc kinetiq > "storage/backups/pre_restore_$(date -u +%Y%m%dT%H%M%SZ).dump"
  "${dc[@]}" exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS kinetiq WITH (FORCE);" -c "CREATE DATABASE kinetiq;"
  "${dc[@]}" exec -T postgres pg_restore -U postgres -d kinetiq --no-owner --single-transaction < "$dump"
  git switch --detach "$good_sha"
  bash scripts/deploy-vps.sh --env-file .env --skip-backup
)
```

Repeat the Phase 1 smoke checks and verify a known export download before resuming Colab.
