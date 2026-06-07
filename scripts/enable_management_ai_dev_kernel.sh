#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-pantheon}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
BFF_BASE_URL="${BFF_BASE_URL:-http://127.0.0.1:18001}"

export PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED:-true}"
export PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-/data/bff/assistant-control-mode.json}"
export PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-300}"
export PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES-assistant.kernel.debug,assistant.kernel.repair}"

cd "${REPO_ROOT}"

echo "Enabling Management AI dev kernel control mode for operator-bff"
echo "compose_project=${COMPOSE_PROJECT_NAME}"
echo "compose_file=${COMPOSE_FILE}"
echo "bff_base_url=${BFF_BASE_URL}"
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

for attempt in $(seq 1 20); do
  if curl -fsS --max-time 5 "${BFF_BASE_URL}/bff/assistant/mode" >"${mode_body}"; then
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
