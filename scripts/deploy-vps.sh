#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ENV_FILE="${REPO_ROOT}/.env"
BACKUP_DIR="${REPO_ROOT}/storage/backups"
HEALTH_TIMEOUT=120
DRY_RUN=false
SKIP_BACKUP=false
SKIP_BUILD=false
SKIP_DNS_CHECK=false

usage() {
  cat <<'EOF'
Kinetiq Studio — Phase 1 VPS Deployment Script

Usage:
  ./scripts/deploy-vps.sh [options]

Options:
  --env-file <path>         Path to production .env file (default: <repo>/.env)
  --backup-dir <path>       Directory for pg_dump backups & deploy SHA markers
                            (default: <repo>/storage/backups)
  --health-timeout <sec>    Max seconds to wait for DB, API, Web, and HTTPS checks
                            (default: 120)
  --dry-run                 Run preflight checks (env, Caddyfile, compose config, DNS)
                            without mutating containers or database
  --skip-backup             Skip pre-deploy pg_dump when postgres is already running
  --skip-build              Skip `docker compose build` before starting containers
  --skip-dns-check          Skip advisory DNS resolution check for Caddy domains
  -h, --help                Show this help message
EOF
}

log() {
  printf '[deploy-vps] %s\n' "$*"
}

warn() {
  printf '[deploy-vps] WARN: %s\n' "$*" >&2
}

fail() {
  printf '[deploy-vps] ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "Required command not found: $1"
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --env-file)
      [ $# -ge 2 ] || fail "--env-file requires a path"
      ENV_FILE="$2"
      shift 2
      ;;
    --env-file=*)
      ENV_FILE="${1#*=}"
      shift
      ;;
    --backup-dir)
      [ $# -ge 2 ] || fail "--backup-dir requires a path"
      BACKUP_DIR="$2"
      shift 2
      ;;
    --backup-dir=*)
      BACKUP_DIR="${1#*=}"
      shift
      ;;
    --health-timeout)
      [ $# -ge 2 ] || fail "--health-timeout requires a number"
      HEALTH_TIMEOUT="$2"
      shift 2
      ;;
    --health-timeout=*)
      HEALTH_TIMEOUT="${1#*=}"
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --skip-backup)
      SKIP_BACKUP=true
      shift
      ;;
    --skip-build)
      SKIP_BUILD=true
      shift
      ;;
    --skip-dns-check)
      SKIP_DNS_CHECK=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown argument: $1 (use --help for usage)"
      ;;
  esac
done

case "$HEALTH_TIMEOUT" in
  ''|*[!0-9]*)
    fail "--health-timeout must be a positive integer (got '$HEALTH_TIMEOUT')"
    ;;
esac

if [ "$HEALTH_TIMEOUT" -lt 1 ]; then
  fail "--health-timeout must be >= 1"
fi

require_cmd node
require_cmd docker
require_cmd curl

if ! docker compose version >/dev/null 2>&1; then
  fail "Docker Compose v2 ('docker compose') is required."
fi

[ -f "$ENV_FILE" ] || fail "Env file not found: $ENV_FILE"

if command -v stat >/dev/null 2>&1; then
  ENV_PERMS="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE" 2>/dev/null || true)"
  if [ -n "$ENV_PERMS" ] && [ "${ENV_PERMS#?}" != "00" ]; then
    warn "Env file $ENV_FILE has permissions $ENV_PERMS; recommend 'chmod 600 $ENV_FILE'."
  fi
fi

log "Running production environment preflight check..."
node "${REPO_ROOT}/scripts/check-env.mjs" --file "$ENV_FILE"

ENV_JSON="$(node "${REPO_ROOT}/scripts/check-env.mjs" --file "$ENV_FILE" --json)"
STUDIO_DOMAIN="$(printf '%s' "$ENV_JSON" | node -e "const d=JSON.parse(require('fs').readFileSync(0,'utf8')); process.stdout.write(d.domains.studio || '');")"
API_DOMAIN="$(printf '%s' "$ENV_JSON" | node -e "const d=JSON.parse(require('fs').readFileSync(0,'utf8')); process.stdout.write(d.domains.api || '');")"

[ -n "$STUDIO_DOMAIN" ] || fail "STUDIO_DOMAIN missing from preflight output"
[ -n "$API_DOMAIN" ] || fail "API_DOMAIN missing from preflight output"

CADDYFILE="${REPO_ROOT}/infra/Caddyfile"
[ -f "$CADDYFILE" ] || fail "Missing Caddyfile at $CADDYFILE"
grep -Fq '{$STUDIO_DOMAIN}' "$CADDYFILE" || fail "infra/Caddyfile must reference {\$STUDIO_DOMAIN}"
grep -Fq '{$API_DOMAIN}' "$CADDYFILE" || fail "infra/Caddyfile must reference {\$API_DOMAIN}"

compose() {
  local -a env_args=(
    "PATH=${PATH}"
    "HOME=${HOME:-/root}"
  )
  local var
  for var in DOCKER_HOST DOCKER_CONTEXT DOCKER_CONFIG DOCKER_TLS_VERIFY DOCKER_CERT_PATH DOCKER_API_VERSION COMPOSE_PROJECT_NAME TMPDIR XDG_RUNTIME_DIR; do
    if [ -n "${!var:-}" ]; then
      env_args+=("${var}=${!var}")
    fi
  done
  env -i "${env_args[@]}" docker compose \
    --env-file "$ENV_FILE" \
    -f "${REPO_ROOT}/docker-compose.yml" \
    -f "${REPO_ROOT}/infra/docker-compose.vps.yml" \
    "$@"
}

log "Validating merged Docker Compose configuration..."
compose config --quiet

if [ "$SKIP_DNS_CHECK" = false ]; then
  if command -v getent >/dev/null 2>&1; then
    for domain in "$STUDIO_DOMAIN" "$API_DOMAIN"; do
      resolved_ip="$(getent ahosts "$domain" 2>/dev/null | awk 'NR==1 {print $1}' || true)"
      if [ -n "$resolved_ip" ]; then
        log "DNS resolved ${domain} -> ${resolved_ip}"
      else
        warn "DNS record for ${domain} did not resolve yet; Caddy TLS issuance will wait for DNS propagation."
      fi
    done
  else
    warn "'getent' not available; skipping DNS resolution check."
  fi
fi

if [ "$DRY_RUN" = true ]; then
  log "Dry-run preflight complete (studio=${STUDIO_DOMAIN}, api=${API_DOMAIN}). No changes made."
  exit 0
fi

mkdir -p "$BACKUP_DIR"

CURRENT_SHA=""
if command -v git >/dev/null 2>&1 && git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  CURRENT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
  if [ -f "${BACKUP_DIR}/current_deploy_sha.txt" ]; then
    cp "${BACKUP_DIR}/current_deploy_sha.txt" "${BACKUP_DIR}/last_deploy_sha.txt"
  fi
fi

BACKUP_FILE=""
if [ "$SKIP_BACKUP" = false ]; then
  if compose ps --status running --services | grep -qx "postgres"; then
    UTC_TS="$(date -u +%Y%m%dT%H%M%SZ)"
    BACKUP_FILE="${BACKUP_DIR}/kinetiq_${UTC_TS}.dump"
    log "Creating pre-deploy PostgreSQL backup at ${BACKUP_FILE}..."
    compose exec -T postgres pg_dump -U postgres -Fc kinetiq > "$BACKUP_FILE"
    if [ ! -s "$BACKUP_FILE" ]; then
      rm -f "$BACKUP_FILE"
      fail "PostgreSQL backup produced an empty file; aborting deployment."
    fi
    log "Backup verified ($(wc -c < "$BACKUP_FILE") bytes)."
  else
    log "PostgreSQL container not running yet (fresh install); skipping pre-deploy pg_dump."
  fi
else
  log "Skipping pre-deploy PostgreSQL backup (--skip-backup)."
fi

if [ "$SKIP_BUILD" = false ]; then
  log "Building Phase 1 container images..."
  compose build
fi

log "Starting postgres and redis..."
compose up -d postgres redis

wait_until() {
  desc="$1"
  shift
  elapsed=0
  while [ "$elapsed" -lt "$HEALTH_TIMEOUT" ]; do
    if "$@" >/dev/null 2>&1; then
      log "${desc} ready (${elapsed}s)."
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  fail "Timed out after ${HEALTH_TIMEOUT}s waiting for ${desc}."
}

wait_until "PostgreSQL" compose exec -T postgres pg_isready -U postgres
wait_until "Redis" compose exec -T redis redis-cli ping

log "Stopping existing api and orchestrator containers before migrations..."
compose stop api orchestrator

log "Running Alembic database migrations (alembic upgrade head)..."
if ! compose run --rm --no-deps api alembic upgrade head; then
  printf '\n[deploy-vps] ERROR: Alembic migration failed!\n' >&2
  if [ -n "$BACKUP_FILE" ]; then
    printf '[deploy-vps] Restore pre-deploy DB backup with:\n' >&2
    printf '  docker compose --env-file "%s" -f "%s/docker-compose.yml" -f "%s/infra/docker-compose.vps.yml" exec -T postgres pg_restore -U postgres -d kinetiq --clean --if-exists < "%s"\n' \
      "$ENV_FILE" "$REPO_ROOT" "$REPO_ROOT" "$BACKUP_FILE" >&2
  fi
  printf '[deploy-vps] See docs/VPS_DEPLOY.md for full rollback steps.\n' >&2
  exit 1
fi

log "Starting api, orchestrator, web, and caddy..."
compose up -d --remove-orphans api orchestrator web caddy

log "Validating Caddyfile inside caddy container..."
compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

wait_until "API /health (internal)" compose exec -T api curl -fsS http://127.0.0.1:8000/health
wait_until "API /ready (internal)" compose exec -T api curl -fsS http://127.0.0.1:8000/ready
wait_until "Web UI (internal)" compose exec -T web node -e "fetch('http://127.0.0.1:3000').then((r)=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

if ! compose ps --status running --services | grep -qx "orchestrator"; then
  fail "Dedicated orchestrator container is not running!"
fi

wait_until "HTTPS https://${API_DOMAIN}/health" curl -fsS --max-time 10 "https://${API_DOMAIN}/health"
wait_until "HTTPS https://${API_DOMAIN}/ready" curl -fsS --max-time 10 "https://${API_DOMAIN}/ready"
wait_until "HTTPS https://${STUDIO_DOMAIN}/" curl -fsS --max-time 10 "https://${STUDIO_DOMAIN}/"

if [ -n "$CURRENT_SHA" ]; then
  printf '%s\n' "$CURRENT_SHA" > "${BACKUP_DIR}/current_deploy_sha.txt"
fi

log "Deployment succeeded!"
log "  Studio UI : https://${STUDIO_DOMAIN}"
log "  Control API: https://${API_DOMAIN}"
if [ -n "$BACKUP_FILE" ]; then
  log "  Pre-deploy DB dump: ${BACKUP_FILE}"
fi
log "  Rollback runbook  : docs/VPS_DEPLOY.md"
