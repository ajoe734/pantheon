#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-pantheon}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
BFF_BASE_URL="${BFF_BASE_URL:-http://127.0.0.1:18001}"
BFF_AUTH_TOKEN="${BFF_AUTH_TOKEN:-}"
PANTHEON_SUPERVISOR_CONFIG="${PANTHEON_SUPERVISOR_CONFIG:-/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json}"

if [ -z "${BFF_AUTH_TOKEN}" ]; then
  echo "ERROR: set BFF_AUTH_TOKEN to an explicit short-lived privileged BFF JWT." >&2
  exit 2
fi
if [ "${BFF_AUTH_TOKEN}" = "pantheon-dev-browser:viewer" ]; then
  echo "ERROR: the public browser viewer token is read-only and cannot enable kernel mode." >&2
  exit 2
fi

resolve_status_root_host() {
  if [ -n "${PANTHEON_STATUS_ROOT_HOST:-}" ]; then
    printf '%s\n' "${PANTHEON_STATUS_ROOT_HOST}"
    return
  fi

  if [ -f "${PANTHEON_SUPERVISOR_CONFIG}" ] && command -v python3 >/dev/null 2>&1; then
    local resolved
    resolved="$(
      python3 - "${PANTHEON_SUPERVISOR_CONFIG}" <<'PY'
import json
import sys
from pathlib import Path

try:
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    status_file = (config.get("paths") or {}).get("status_file")
    if status_file:
        print(Path(status_file).resolve().parent)
except Exception:
    pass
PY
    )"
    if [ -n "${resolved}" ]; then
      printf '%s\n' "${resolved}"
      return
    fi
  fi

  printf '%s\n' "${REPO_ROOT}"
}

export PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED:-true}"
export PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-/data/bff/assistant-control-mode.json}"
export PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-300}"
export PANTHEON_BFF_AUTH_STUB="${PANTHEON_BFF_AUTH_STUB:-false}"
export PANTHEON_BFF_AUTH_MODE="${PANTHEON_BFF_AUTH_MODE:-strict}"
export PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES-}"
export PANTHEON_BFF_JWT_SECRET="${PANTHEON_BFF_JWT_SECRET:-}"
export PANTHEON_BFF_OIDC_CLIENT_ID="${PANTHEON_BFF_OIDC_CLIENT_ID:-}"
export PANTHEON_BFF_OIDC_CLIENT_SECRET="${PANTHEON_BFF_OIDC_CLIENT_SECRET:-}"
export PANTHEON_STATUS_ROOT_HOST="$(resolve_status_root_host)"
export PANTHEON_STATUS_ROOT_CONTAINER="${PANTHEON_STATUS_ROOT_CONTAINER:-/workspace/status-root}"

if [ "${PANTHEON_BFF_AUTH_STUB}" != "false" ] \
  || [ "${PANTHEON_BFF_AUTH_MODE}" != "strict" ] \
  || [ -n "${PANTHEON_BFF_STUB_CAPABILITIES}" ]; then
  echo "ERROR: dev kernel mode requires AUTH_STUB=false, AUTH_MODE=strict, and empty stub capabilities." >&2
  exit 2
fi
if [[ ! "${PANTHEON_BFF_JWT_SECRET}" =~ [^[:space:]] \
  || ! "${PANTHEON_BFF_OIDC_CLIENT_ID}" =~ [^[:space:]] \
  || ! "${PANTHEON_BFF_OIDC_CLIENT_SECRET}" =~ [^[:space:]] ]]; then
  echo "ERROR: recreating operator-bff requires governed JWT and dev-login client secrets." >&2
  exit 2
fi

cd "${REPO_ROOT}"

echo "Enabling Management AI dev kernel control mode for operator-bff"
echo "compose_project=${COMPOSE_PROJECT_NAME}"
echo "compose_file=${COMPOSE_FILE}"
echo "bff_base_url=${BFF_BASE_URL}"
echo "bff_auth_token=configured"
echo "status_root_host=${PANTHEON_STATUS_ROOT_HOST}"
echo "status_root_container=${PANTHEON_STATUS_ROOT_CONTAINER}"
echo "kernel_enabled=${PANTHEON_ASSISTANT_KERNEL_ENABLED}"
if [ -n "${PANTHEON_BFF_STUB_CAPABILITIES}" ]; then
  echo "stub_capabilities=configured"
else
  echo "stub_capabilities=empty"
fi

docker compose -p "${COMPOSE_PROJECT_NAME}" -f "${COMPOSE_FILE}" up -d --no-deps --force-recreate operator-bff
docker compose -p "${COMPOSE_PROJECT_NAME}" -f "${COMPOSE_FILE}" ps operator-bff

mode_body="$(mktemp)"
trap 'rm -f "${mode_body}"' EXIT
auth_args=(-H "Authorization: Bearer ${BFF_AUTH_TOKEN}")

for attempt in $(seq 1 20); do
  if curl -fsS --max-time 5 "${auth_args[@]}" "${BFF_BASE_URL}/bff/assistant/mode" >"${mode_body}"; then
    if command -v jq >/dev/null 2>&1; then
      jq '{kernel_enabled:.data.kernel_enabled, control_mode:.data.control_mode}' "${mode_body}"
    else
      cat "${mode_body}"
      printf '\n'
    fi
    exit 0
  fi
  echo "Waiting for operator-bff mode endpoint (${attempt}/20)..."
  sleep 1
done

echo "operator-bff restarted, but ${BFF_BASE_URL}/bff/assistant/mode did not become ready." >&2
exit 1
