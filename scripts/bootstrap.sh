#!/usr/bin/env bash
# Pantheon control-plane bootstrap: start the selected compose stack, run DB
# migrations, replay telemetry DLQ write-failures, and verify control-plane
# services are healthy.
#
# Usage:
#   bash scripts/bootstrap.sh [--compose-file <file>] [--env-file <file>] [--skip-migration] [--skip-telemetry-replay]
#
#   Default compose file : docker-compose.control.yml
#   Default env file     : .env (auto-loaded when present)
#
# Examples:
#   # Staging VM1 control-plane bring-up
#   bash scripts/bootstrap.sh
#
#   # Dev single-VM baseline bring-up
#   bash scripts/bootstrap.sh --compose-file docker-compose.yml
#
#   # Use a specific env file
#   bash scripts/bootstrap.sh --env-file env/prod-control.env.example
#
#   # Skip DB migration (e.g. already applied)
#   bash scripts/bootstrap.sh --skip-migration
#
#   # Skip the deployment DLQ replay pass
#   bash scripts/bootstrap.sh --skip-telemetry-replay
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.control.yml"
ENV_FILE=""
SKIP_MIGRATION=false
SKIP_TELEMETRY_REPLAY=false

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --env-file)     ENV_FILE="$2";     shift 2 ;;
    --skip-migration) SKIP_MIGRATION=true; shift ;;
    --skip-telemetry-replay) SKIP_TELEMETRY_REPLAY=true; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Load .env if present and no explicit env-file given
if [[ -z "$ENV_FILE" && -f "$ROOT_DIR/.env" ]]; then
  ENV_FILE="$ROOT_DIR/.env"
fi
if [[ -n "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  echo "==> Loaded env: $ENV_FILE"
fi

COMPOSE_ARGS=(-f "$COMPOSE_FILE")
if [[ -n "$ENV_FILE" ]]; then
  COMPOSE_ARGS+=(--env-file "$ENV_FILE")
fi

# ---------------------------------------------------------------------------
# Step 1: Start infra services and wait for them to be healthy
# ---------------------------------------------------------------------------
INFRA_SERVICES=(postgres minio nats)
echo "==> [1/5] Starting infra services: ${INFRA_SERVICES[*]}"
docker compose "${COMPOSE_ARGS[@]}" up -d "${INFRA_SERVICES[@]}"

_wait_healthy() {
  local svc="$1"
  local timeout="${2:-120}"
  local elapsed=0
  echo -n "    $svc "
  while true; do
    local health
    health=$(docker compose "${COMPOSE_ARGS[@]}" ps --format json "$svc" 2>/dev/null \
      | python3 -c "
import sys, json
data = sys.stdin.read().strip()
if not data:
    print('')
    sys.exit(0)
# docker compose ps --format json can return a list or a single object
try:
    parsed = json.loads(data)
    if isinstance(parsed, list):
        for item in parsed:
            if item.get('Service') == '$svc':
                print(item.get('Health', ''))
                sys.exit(0)
        print('')
    else:
        print(parsed.get('Health', ''))
except Exception:
    print('')
" 2>/dev/null || true)
    if [[ "$health" == "healthy" ]]; then
      echo " healthy"
      return 0
    fi
    if [[ $elapsed -ge $timeout ]]; then
      echo " TIMEOUT after ${timeout}s"
      return 1
    fi
    echo -n "."
    sleep 3
    elapsed=$((elapsed + 3))
  done
}

for svc in "${INFRA_SERVICES[@]}"; do
  _wait_healthy "$svc"
done

# Bootstrap MinIO bucket
echo "==> [1/5] Creating MinIO bucket via minio-init..."
docker compose "${COMPOSE_ARGS[@]}" run --rm minio-init

# ---------------------------------------------------------------------------
# Step 2: Run DB migrations
# ---------------------------------------------------------------------------
if [[ "$SKIP_MIGRATION" == "true" ]]; then
  echo "==> [2/5] Skipping DB migrations (--skip-migration flag set)"
else
  echo "==> [2/5] Running DB migrations..."
  # Run inside the postgres container where psql is available; Python migration
  # runs from host using the published port.
  POSTGRES_PORT="${POSTGRES_PORT:-15432}"
  POSTGRES_USER_VAL="${POSTGRES_USER:-postgres}"
  POSTGRES_PASSWORD_VAL="${POSTGRES_PASSWORD:-postgres}"
  PANTHEON_APP_DB_USER_VAL="${PANTHEON_APP_DB_USER:-pantheon_app}"
  PANTHEON_APP_DB_PASSWORD_VAL="${PANTHEON_APP_DB_PASSWORD:-pantheon_app}"
  PANTHEON_APP_DB_NAME_VAL="${PANTHEON_APP_DB_NAME:-pantheon}"

  if command -v psql >/dev/null 2>&1; then
    PSQL_RUNNER=(
      env "PGPASSWORD=${POSTGRES_PASSWORD_VAL}" psql
      -h 127.0.0.1 -p "$POSTGRES_PORT"
      -U "$POSTGRES_USER_VAL" -d postgres
      -v ON_ERROR_STOP=1
    )
  else
    echo "    host psql not found; using postgres container client"
    PSQL_RUNNER=(
      docker compose "${COMPOSE_ARGS[@]}" exec -T
      -e "PGPASSWORD=${POSTGRES_PASSWORD_VAL}"
      postgres
      psql -U "$POSTGRES_USER_VAL" -d postgres
      -v ON_ERROR_STOP=1
    )
  fi

  # Ensure app user + database exist (idempotent)
  "${PSQL_RUNNER[@]}" \
    -c "DO \$\$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${PANTHEON_APP_DB_USER_VAL}') THEN
            EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', '${PANTHEON_APP_DB_USER_VAL}', '${PANTHEON_APP_DB_PASSWORD_VAL}');
          END IF;
        END \$\$;"

  CREATE_DB_SQL="$("${PSQL_RUNNER[@]}" -At -c \
    "SELECT format('CREATE DATABASE %I OWNER %I', '${PANTHEON_APP_DB_NAME_VAL}', '${PANTHEON_APP_DB_USER_VAL}')
     WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${PANTHEON_APP_DB_NAME_VAL}')")"
  if [[ -n "$CREATE_DB_SQL" ]]; then
    "${PSQL_RUNNER[@]}" -c "$CREATE_DB_SQL"
  fi

  "${PSQL_RUNNER[@]}" \
    -c "GRANT ALL PRIVILEGES ON DATABASE \"${PANTHEON_APP_DB_NAME_VAL}\" TO \"${PANTHEON_APP_DB_USER_VAL}\";"

  if command -v psql >/dev/null 2>&1; then
    APP_PSQL_RUNNER=(
      env "PGPASSWORD=${PANTHEON_APP_DB_PASSWORD_VAL}" psql
      -h 127.0.0.1 -p "$POSTGRES_PORT"
      -U "$PANTHEON_APP_DB_USER_VAL" -d "$PANTHEON_APP_DB_NAME_VAL"
      -v ON_ERROR_STOP=1
    )
  else
    APP_PSQL_RUNNER=(
      docker compose "${COMPOSE_ARGS[@]}" exec -T
      -e "PGPASSWORD=${PANTHEON_APP_DB_PASSWORD_VAL}"
      postgres
      psql -U "$PANTHEON_APP_DB_USER_VAL" -d "$PANTHEON_APP_DB_NAME_VAL"
      -v ON_ERROR_STOP=1
    )
  fi

  "${APP_PSQL_RUNNER[@]}" <<'SQL'
CREATE SEQUENCE IF NOT EXISTS telemetry_events_ingested_seq_seq AS BIGINT;

CREATE TABLE IF NOT EXISTS telemetry_events (
    event_id     TEXT        PRIMARY KEY,
    event_type   TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    payload      JSONB       NOT NULL,
    ingested_seq BIGINT      NOT NULL DEFAULT nextval('telemetry_events_ingested_seq_seq'),
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE telemetry_events
    ADD COLUMN IF NOT EXISTS ingested_seq BIGINT;
ALTER TABLE telemetry_events
    ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp();
ALTER TABLE telemetry_events
    ALTER COLUMN ingested_seq SET DEFAULT nextval('telemetry_events_ingested_seq_seq');
UPDATE telemetry_events
    SET ingested_seq = nextval('telemetry_events_ingested_seq_seq')
    WHERE ingested_seq IS NULL;
ALTER TABLE telemetry_events
    ALTER COLUMN ingested_seq SET NOT NULL;
ALTER TABLE telemetry_events
    ALTER COLUMN ingested_at SET DEFAULT clock_timestamp();
UPDATE telemetry_events
    SET ingested_at = clock_timestamp()
    WHERE ingested_at IS NULL;
ALTER TABLE telemetry_events
    ALTER COLUMN ingested_at SET NOT NULL;
ALTER SEQUENCE telemetry_events_ingested_seq_seq
    OWNED BY telemetry_events.ingested_seq;

CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_events_ingested_seq
    ON telemetry_events (ingested_seq);

CREATE INDEX IF NOT EXISTS idx_telemetry_events_ingested_at
    ON telemetry_events (ingested_at DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_events_event_type_ingested_seq
    ON telemetry_events (event_type, ingested_seq);

CREATE INDEX IF NOT EXISTS idx_telemetry_events_created_at
    ON telemetry_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_events_event_type
    ON telemetry_events (event_type);

CREATE INDEX IF NOT EXISTS idx_telemetry_events_binding_id
    ON telemetry_events ((payload->>'binding_id'));

CREATE INDEX IF NOT EXISTS idx_telemetry_events_runtime_id
    ON telemetry_events ((payload->>'runtime_id'));

CREATE INDEX IF NOT EXISTS idx_telemetry_events_deployment_stage
    ON telemetry_events ((payload->>'deployment_stage'));

CREATE INDEX IF NOT EXISTS idx_telemetry_events_payload_gin
    ON telemetry_events USING GIN (payload);
SQL
fi

# ---------------------------------------------------------------------------
# Step 3: Start all application services
# ---------------------------------------------------------------------------
echo "==> [3/5] Starting all application services..."
docker compose "${COMPOSE_ARGS[@]}" up -d

APP_SERVICES=(
  telemetry incidents postmortems operator-bff persona
  evaluation feedback memory registry optimizer-svc
  promotion capital evolution lineage-read
)

for svc in "${APP_SERVICES[@]}"; do
  _wait_healthy "$svc" 120
done

# ---------------------------------------------------------------------------
# Step 4: Replay telemetry DLQ write-failure entries
# ---------------------------------------------------------------------------
if [[ "$SKIP_TELEMETRY_REPLAY" == "true" ]]; then
  echo "==> [4/5] Skipping telemetry DLQ replay (--skip-telemetry-replay flag set)"
else
  echo "==> [4/5] Replaying telemetry DLQ write-failure entries..."
  # -e forwards the caller's credential into the container: the block below runs
  # inside telemetry, so a variable exported only in this shell is not visible
  # to it. The tenant settings are already part of the container environment.
  docker compose "${COMPOSE_ARGS[@]}" exec -T \
    -e "PANTHEON_TELEMETRY_OPERATOR_TOKEN=${PANTHEON_TELEMETRY_OPERATOR_TOKEN:-}" \
    telemetry python - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

# /api/telemetry/replay is guarded by require_telemetry_authority(("operator",
# "admin")). This request previously carried no credential at all, so it earned
# a 401 and aborted the whole bring-up. PANTHEON_TELEMETRY_SERVICE_TOKEN is not
# a substitute: it authenticates but carries no operator role, so it earns 403.
# Forward an operator credential supplied by the caller instead, following the
# PANTHEON_*_TOKEN convention used for the other service APIs. Replaying the
# telemetry DLQ is a best-effort convenience, so a missing credential is
# reported and skipped rather than failing the bring-up.
TOKEN = (os.getenv("PANTHEON_TELEMETRY_OPERATOR_TOKEN") or "").strip()
TENANT = (
    (os.getenv("PANTHEON_TELEMETRY_SERVICE_TENANTS") or "").split(",")[0].strip()
    or (os.getenv("PANTHEON_TENANT_ID") or "").strip()
    or "default"
)

if not TOKEN:
    print(
        "    telemetry DLQ replay skipped: set PANTHEON_TELEMETRY_OPERATOR_TOKEN to an"
        " operator or admin credential to enable it",
        file=sys.stderr,
    )
    sys.exit(0)

url = "http://127.0.0.1:8083/api/telemetry/replay"
req = urllib.request.Request(url, data=b"", method="POST")
req.add_header("Authorization", "Bearer " + TOKEN)
req.add_header("X-Tenant-Id", TENANT)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
        payload = json.loads(body) if body else {}
except urllib.error.HTTPError as exc:
    detail = exc.read().decode(errors="replace")
    print(f"    telemetry DLQ replay failed: HTTP {exc.code} {detail}", file=sys.stderr)
    sys.exit(1)
except Exception as exc:
    print(f"    telemetry DLQ replay failed: {exc}", file=sys.stderr)
    sys.exit(1)

print(f"    telemetry DLQ replay: replayed={payload.get('replayed', 0)}")
PY
fi

# ---------------------------------------------------------------------------
# Step 5: Health summary
# ---------------------------------------------------------------------------
echo ""
echo "==> [5/5] Final service status:"
docker compose "${COMPOSE_ARGS[@]}" ps

UNHEALTHY=$(docker compose "${COMPOSE_ARGS[@]}" ps --format json 2>/dev/null \
  | python3 -c "
import sys, json
data = sys.stdin.read().strip()
if not data:
    sys.exit(0)
try:
    items = json.loads(data)
    if isinstance(items, dict):
        items = [items]
    bad = [i.get('Service','?') for i in items if i.get('Health','') not in ('healthy','')]
    if bad:
        print('UNHEALTHY:', ' '.join(bad))
        sys.exit(1)
except Exception:
    pass
" 2>/dev/null || true)

if [[ -n "$UNHEALTHY" ]]; then
  echo ""
  echo "ERROR: Some services are not healthy: $UNHEALTHY"
  echo "       Run: docker compose ${COMPOSE_ARGS[*]} logs <service>"
  exit 1
fi

echo ""
echo "==> Bootstrap complete. All services are healthy."
