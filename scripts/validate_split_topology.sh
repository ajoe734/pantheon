#!/usr/bin/env bash
# Validate the Pantheon dev single-VM and staging dual-VM compose contract.
#
# This script only evaluates rendered docker compose configuration. It does not
# build images or start containers.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

for cmd in docker jq; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $cmd" >&2
    exit 127
  fi
done

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

ROOT_JSON="$TMP_DIR/root-compose.json"
CONTROL_JSON="$TMP_DIR/control-compose.json"
EXEC_JSON="$TMP_DIR/exec-compose.json"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

compose_json() {
  local output="$1"
  shift
  docker compose "$@" config --format json >"$output"
}

require_service() {
  local file="$1"
  local service="$2"
  jq -e --arg service "$service" '.services[$service] != null' "$file" >/dev/null \
    || fail "missing required service '$service' in $file"
}

forbid_service() {
  local file="$1"
  local service="$2"
  jq -e --arg service "$service" '.services[$service] == null' "$file" >/dev/null \
    || fail "forbidden service '$service' is present in $file"
}

require_env() {
  local file="$1"
  local service="$2"
  local key="$3"
  local expected="$4"
  jq -e \
    --arg service "$service" \
    --arg key "$key" \
    --arg expected "$expected" \
    '.services[$service].environment[$key] == $expected' \
    "$file" >/dev/null \
    || fail "$service must set $key=$expected in $file"
}

require_env_nonempty() {
  local file="$1"
  local service="$2"
  local key="$3"
  jq -e \
    --arg service "$service" \
    --arg key "$key" \
    '(.services[$service].environment[$key] // "") | length > 0' \
    "$file" >/dev/null \
    || fail "$service must set non-empty $key in $file"
}

forbid_env_key() {
  local file="$1"
  local key="$2"
  local matches
  matches="$(
    jq -r --arg key "$key" '
      .services
      | to_entries[]
      | select((.value.environment // {})[$key] != null)
      | .key
    ' "$file"
  )"
  [[ -z "$matches" ]] || fail "forbidden env key $key appears in services: $matches"
}

compose_json "$ROOT_JSON" -f docker-compose.yml
compose_json "$CONTROL_JSON" --env-file env/prod-control.env.example -f docker-compose.control.yml
compose_json "$EXEC_JSON" --env-file env/prod-exec.env.example -f docker-compose.exec.yml

grep -q "topology: dev-single-vm-baseline" docker-compose.yml \
  || fail "docker-compose.yml must be labelled as dev-single-vm-baseline"

for service in operator-bff runtime-manager governance deployment telemetry signal-store; do
  require_service "$ROOT_JSON" "$service"
done

for service in operator-bff telemetry governance deployment incidents postmortems capital evolution lineage-read registry persona; do
  require_service "$CONTROL_JSON" "$service"
done

for service in runtime-manager broker-adapter exchange-adapter pantheon-paper-runtime signal-store; do
  require_service "$EXEC_JSON" "$service"
done

for service in runtime-manager broker-adapter exchange-adapter pantheon-paper-runtime pantheon-lean-live signal-store router; do
  forbid_service "$CONTROL_JSON" "$service"
done

for service in operator-bff persona registry promotion lineage-read governance telemetry incidents postmortems capital evolution evaluation feedback memory optimizer-svc deployment; do
  forbid_service "$EXEC_JSON" "$service"
done

for key in BROKER_API_KEY BROKER_API_SECRET EXCHANGE_API_KEY EXCHANGE_API_SECRET SHIOAJI_API_KEY SHIOAJI_SECRET_KEY KRAKEN_API_KEY KRAKEN_API_SECRET TEJ_API_KEY; do
  forbid_env_key "$CONTROL_JSON" "$key"
done

require_env "$CONTROL_JSON" operator-bff PANTHEON_ENV staging-live
require_env "$CONTROL_JSON" operator-bff PANTHEON_LIVE_BROKER_ENABLED true
require_env "$CONTROL_JSON" operator-bff BFF_READ_SURFACE_STATE fresh
require_env "$CONTROL_JSON" operator-bff PANTHEON_BFF_CORS_ORIGINS https://pantheon-ai-system-front-staging-live.lovable.app
require_env "$CONTROL_JSON" operator-bff PANTHEON_INTERNAL_API_URL http://10.140.0.5:28081
require_env "$CONTROL_JSON" operator-bff PANTHEON_RUNTIME_MANAGER_URL http://10.140.0.5:28081
require_env_nonempty "$CONTROL_JSON" operator-bff PANTHEON_RUNTIME_MANAGER_TOKEN
require_env "$CONTROL_JSON" operator-bff PANTHEON_GOVERNANCE_APPROVAL_API_URL http://governance:8082
require_env "$CONTROL_JSON" operator-bff PANTHEON_DEPLOYMENT_API_URL http://deployment:8095
require_env "$CONTROL_JSON" telemetry PANTHEON_RUNTIME_MANAGER_URL http://10.140.0.5:28081
require_env_nonempty "$CONTROL_JSON" telemetry PANTHEON_RUNTIME_MANAGER_TOKEN

require_env "$EXEC_JSON" runtime-manager PANTHEON_SINGLE_RUNTIME_ENFORCED true
require_env "$EXEC_JSON" broker-adapter PANTHEON_RUNTIME_MANAGER_URL http://runtime-manager:8081
require_env "$EXEC_JSON" exchange-adapter PANTHEON_RUNTIME_MANAGER_URL http://runtime-manager:8081
require_env "$EXEC_JSON" pantheon-paper-runtime PANTHEON_RUNTIME_MANAGER_URL http://runtime-manager:8081
require_env "$EXEC_JSON" pantheon-paper-runtime PANTHEON_TELEMETRY_URL http://10.140.0.4:38083
require_env "$EXEC_JSON" broker-adapter PANTHEON_SECRETS_OPTIONAL true
require_env "$EXEC_JSON" exchange-adapter PANTHEON_SECRETS_OPTIONAL true

echo "ok  dev single-VM and staging dual-VM compose contract validated"
