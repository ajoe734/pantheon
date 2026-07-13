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
# Retain sensitive values in shell variables while preventing even preflight
# helpers from inheriting them accidentally.
export -n BFF_AUTH_TOKEN PANTHEON_BFF_JWT_SECRET PANTHEON_BFF_OIDC_CLIENT_SECRET \
  PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON 2>/dev/null || true

for command in jq curl docker; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "ERROR: ${command} is required." >&2
    exit 2
  fi
done

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

cd "${REPO_ROOT}"

auth_header="$(mktemp)"
identity_body="$(mktemp)"
mode_body="$(mktemp)"
previous_env_json="$(mktemp)"
chmod 0600 "${auth_header}" "${identity_body}" "${mode_body}" "${previous_env_json}"
trap 'rm -f "${auth_header}" "${identity_body}" "${mode_body}" "${previous_env_json}"' EXIT
printf 'Authorization: Bearer %s\n' "${BFF_AUTH_TOKEN}" >"${auth_header}"
# The credential now exists only in the 0600 header file. Do not leak it to
# docker, jq, curl, or unrelated child processes.
BFF_AUTH_TOKEN=""

policy_names=(
  PANTHEON_ASSISTANT_KERNEL_ENABLED
  PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH
  PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS
  PANTHEON_BFF_AUTH_STUB
  PANTHEON_BFF_AUTH_MODE
  PANTHEON_BFF_STUB_CAPABILITIES
  PANTHEON_BFF_JWT_SECRET
  PANTHEON_BFF_JWT_ISSUER
  PANTHEON_BFF_JWT_AUDIENCE
  PANTHEON_BFF_OIDC_CLIENT_ID
  PANTHEON_BFF_OIDC_CLIENT_SECRET
  PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON
  PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS
  PANTHEON_BFF_DEV_LOGIN_ROLES
  PANTHEON_BFF_DEV_LOGIN_SUBJECT
  PANTHEON_BFF_DEV_LOGIN_MFA_VERIFIED
  PANTHEON_BFF_TENANT_ID
  PANTHEON_BFF_ALLOWED_TENANTS
  PANTHEON_BFF_ROLE_CLAIMS
  PANTHEON_BFF_ROLE_MAP
  PANTHEON_BFF_ROLE_MAP_MODE
  PANTHEON_BFF_DEFAULT_ROLE
  PANTHEON_BFF_MFA_REQUIRED
  PANTHEON_BFF_MFA_CLAIMS
  PANTHEON_BFF_MFA_VALUES
  PANTHEON_STATUS_ROOT_HOST
  PANTHEON_STATUS_ROOT_CONTAINER
)

container_id="$(docker compose -p "${COMPOSE_PROJECT_NAME}" -f "${COMPOSE_FILE}" ps -q operator-bff)"
if [ -z "${container_id}" ]; then
  echo "ERROR: operator-bff must already be running so its prior configuration can be rolled back." >&2
  rm -f "${auth_header}" "${identity_body}" "${mode_body}" "${previous_env_json}"
  exit 2
fi
if ! docker inspect --format '{{json .Config.Env}}' "${container_id}" >"${previous_env_json}" \
  || ! jq -e 'type == "array"' "${previous_env_json}" >/dev/null; then
  echo "ERROR: unable to snapshot the current operator-bff configuration." >&2
  rm -f "${auth_header}" "${identity_body}" "${mode_body}" "${previous_env_json}"
  exit 2
fi

current_env_value() {
  local name="$1"
  jq -r --arg prefix "${name}=" '
    [.[] | select(startswith($prefix)) | ltrimstr($prefix)] | last // empty
  ' "${previous_env_json}"
}

set_policy_if_unset() {
  local name="$1"
  local fallback="$2"
  local value
  if [[ -v "${name}" ]]; then
    case "${name}" in
      PANTHEON_BFF_JWT_SECRET|PANTHEON_BFF_OIDC_CLIENT_SECRET|PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON)
        export -n "${name}" 2>/dev/null || true
        ;;
      *) export "${name}" ;;
    esac
    return
  fi
  if jq -e --arg prefix "${name}=" 'any(.[]; startswith($prefix))' "${previous_env_json}" >/dev/null; then
    value="$(current_env_value "${name}")"
  else
    value="${fallback}"
  fi
  printf -v "${name}" '%s' "${value}"
  case "${name}" in
    PANTHEON_BFF_JWT_SECRET|PANTHEON_BFF_OIDC_CLIENT_SECRET|PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON)
      export -n "${name}" 2>/dev/null || true
      ;;
    *) export "${name}" ;;
  esac
}

set_policy_if_unset PANTHEON_ASSISTANT_KERNEL_ENABLED true
set_policy_if_unset PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH /data/bff/assistant-control-mode.json
set_policy_if_unset PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS 300
set_policy_if_unset PANTHEON_BFF_AUTH_STUB false
set_policy_if_unset PANTHEON_BFF_AUTH_MODE strict
set_policy_if_unset PANTHEON_BFF_STUB_CAPABILITIES ""
set_policy_if_unset PANTHEON_BFF_JWT_SECRET ""
set_policy_if_unset PANTHEON_BFF_JWT_ISSUER pantheon-dev
set_policy_if_unset PANTHEON_BFF_JWT_AUDIENCE bff-operators
set_policy_if_unset PANTHEON_BFF_OIDC_CLIENT_ID ""
set_policy_if_unset PANTHEON_BFF_OIDC_CLIENT_SECRET ""
set_policy_if_unset PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON ""
set_policy_if_unset PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS 900
set_policy_if_unset PANTHEON_BFF_DEV_LOGIN_ROLES operator,reviewer,approver
set_policy_if_unset PANTHEON_BFF_DEV_LOGIN_SUBJECT ""
set_policy_if_unset PANTHEON_BFF_DEV_LOGIN_MFA_VERIFIED false
set_policy_if_unset PANTHEON_BFF_TENANT_ID tenant-dev
set_policy_if_unset PANTHEON_BFF_ALLOWED_TENANTS tenant-dev
set_policy_if_unset PANTHEON_BFF_ROLE_CLAIMS roles,role
set_policy_if_unset PANTHEON_BFF_ROLE_MAP ""
set_policy_if_unset PANTHEON_BFF_ROLE_MAP_MODE passthrough
set_policy_if_unset PANTHEON_BFF_DEFAULT_ROLE operator
set_policy_if_unset PANTHEON_BFF_MFA_REQUIRED false
set_policy_if_unset PANTHEON_BFF_MFA_CLAIMS amr,acr,mfa,mfa_verified
set_policy_if_unset PANTHEON_BFF_MFA_VALUES true,1,yes,mfa,otp,totp,webauthn
set_policy_if_unset PANTHEON_STATUS_ROOT_HOST "$(resolve_status_root_host)"
set_policy_if_unset PANTHEON_STATUS_ROOT_CONTAINER /workspace/status-root

if [ "${PANTHEON_ASSISTANT_KERNEL_ENABLED}" != "true" ]; then
  echo "ERROR: PANTHEON_ASSISTANT_KERNEL_ENABLED must be exactly true for this enable operation." >&2
  exit 2
fi
if [ "${PANTHEON_BFF_AUTH_STUB}" != "false" ] \
  || [ "${PANTHEON_BFF_AUTH_MODE}" != "strict" ] \
  || [ -n "${PANTHEON_BFF_STUB_CAPABILITIES}" ]; then
  echo "ERROR: dev kernel mode requires AUTH_STUB=false, AUTH_MODE=strict, and empty stub capabilities." >&2
  exit 2
fi
for required_name in \
  PANTHEON_BFF_JWT_SECRET PANTHEON_BFF_JWT_ISSUER PANTHEON_BFF_JWT_AUDIENCE \
  PANTHEON_BFF_TENANT_ID PANTHEON_BFF_ALLOWED_TENANTS PANTHEON_BFF_DEV_LOGIN_ROLES \
  PANTHEON_BFF_ROLE_CLAIMS PANTHEON_BFF_ROLE_MAP_MODE PANTHEON_BFF_DEFAULT_ROLE \
  PANTHEON_BFF_MFA_CLAIMS PANTHEON_BFF_MFA_VALUES; do
  if [[ ! "${!required_name}" =~ [^[:space:]] ]]; then
    echo "ERROR: ${required_name} must remain configured while recreating operator-bff." >&2
    exit 2
  fi
done
if [ -z "${PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON}" ]; then
  echo "ERROR: recreating operator-bff requires governed dev-login client profiles; the legacy shared client is not accepted." >&2
  exit 2
fi
if [ -n "${PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON}" ] \
  && ! jq -e 'type == "object" and length > 0' <<<"${PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON}" >/dev/null; then
  echo "ERROR: PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON must be a non-empty JSON object." >&2
  exit 2
fi
# Runtime signing/login secrets are needed by Compose, not by identity/mode
# probes or other helper processes.
export -n PANTHEON_BFF_JWT_SECRET PANTHEON_BFF_OIDC_CLIENT_SECRET \
  PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON 2>/dev/null || true

if ! curl -fsS --max-time 10 -H "@${auth_header}" \
  "${BFF_BASE_URL}/bff/me" >"${identity_body}"; then
  echo "ERROR: the supplied BFF_AUTH_TOKEN was rejected before operator-bff restart." >&2
  exit 2
fi
if ! jq -e '
  ((.data.roles // []) | any(. == "operator" or . == "admin"))
  and (.data.session.mfa_verified == true or .data.currentUser.mfa_verified == true or .data.mfa_verified == true)
  and ([.data.capabilities[]?] | any(. == "assistant.kernel.debug" or . == "assistant.kernel.repair"))
' "${identity_body}" >/dev/null; then
  echo "ERROR: BFF_AUTH_TOKEN requires operator/admin, MFA, and assistant.kernel.debug or assistant.kernel.repair." >&2
  exit 2
fi
if ! curl -fsS --max-time 10 -H "@${auth_header}" \
  "${BFF_BASE_URL}/bff/assistant/mode" >"${mode_body}"; then
  echo "ERROR: unable to preflight the current assistant mode." >&2
  exit 2
fi
if ! jq -e '
  .data.control_mode.active == false
  and .data.control_mode.state == "inactive"
  and ((.data.control_mode.managementSessionId? // null) == null)
  and ((.data.control_mode.management_session_id? // null) == null)
' "${mode_body}" >/dev/null; then
  echo "ERROR: refuse to recreate operator-bff while this actor has an active control-mode session." >&2
  exit 2
fi

mutated="false"
completed="false"

restore_previous_policy() {
  local name value
  for name in "${policy_names[@]}"; do
    value="$(current_env_value "${name}")"
    if jq -e --arg prefix "${name}=" 'any(.[]; startswith($prefix))' "${previous_env_json}" >/dev/null; then
      printf -v "${name}" '%s' "${value}"
      case "${name}" in
        PANTHEON_BFF_JWT_SECRET|PANTHEON_BFF_OIDC_CLIENT_SECRET|PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON)
          export -n "${name}" 2>/dev/null || true
          ;;
        *) export "${name}" ;;
      esac
    else
      unset "${name}"
    fi
  done
  export -n PANTHEON_BFF_JWT_SECRET PANTHEON_BFF_OIDC_CLIENT_SECRET \
    PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON 2>/dev/null || true
  PANTHEON_BFF_JWT_SECRET="${PANTHEON_BFF_JWT_SECRET:-}" \
  PANTHEON_BFF_OIDC_CLIENT_SECRET="${PANTHEON_BFF_OIDC_CLIENT_SECRET:-}" \
  PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON="${PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON:-}" \
    docker compose -p "${COMPOSE_PROJECT_NAME}" -f "${COMPOSE_FILE}" \
    up -d --no-deps --force-recreate operator-bff
}

cleanup() {
  local original_status="$?"
  local final_status="${original_status}"
  trap - EXIT
  set +e
  if [ "${mutated}" = "true" ] && [ "${completed}" != "true" ]; then
    echo "Enable failed; rolling operator-bff back to its captured policy configuration." >&2
    if restore_previous_policy; then
      echo "operator-bff policy rollback completed." >&2
    else
      echo "ERROR: operator-bff policy rollback failed." >&2
      if [ "${original_status}" -eq 0 ]; then
        final_status=1
      fi
    fi
  fi
  rm -f "${auth_header}" "${identity_body}" "${mode_body}" "${previous_env_json}"
  exit "${final_status}"
}
trap cleanup EXIT

echo "Enabling Management AI dev kernel control mode for operator-bff"
echo "compose_project=${COMPOSE_PROJECT_NAME}"
echo "compose_file=${COMPOSE_FILE}"
echo "bff_base_url=${BFF_BASE_URL}"
echo "bff_auth_token=configured"
echo "status_root_host=${PANTHEON_STATUS_ROOT_HOST}"
echo "status_root_container=${PANTHEON_STATUS_ROOT_CONTAINER}"
echo "kernel_enabled=${PANTHEON_ASSISTANT_KERNEL_ENABLED}"
echo "auth_policy=preserved"

mutated="true"
PANTHEON_BFF_JWT_SECRET="${PANTHEON_BFF_JWT_SECRET}" \
PANTHEON_BFF_OIDC_CLIENT_SECRET="${PANTHEON_BFF_OIDC_CLIENT_SECRET}" \
PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON="${PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON}" \
  docker compose -p "${COMPOSE_PROJECT_NAME}" -f "${COMPOSE_FILE}" \
  up -d --no-deps --force-recreate operator-bff
docker compose -p "${COMPOSE_PROJECT_NAME}" -f "${COMPOSE_FILE}" ps operator-bff

for attempt in $(seq 1 20); do
  if curl -fsS --max-time 5 -H "@${auth_header}" \
    "${BFF_BASE_URL}/bff/assistant/mode" >"${mode_body}"; then
    if jq -e '
      .data.kernel_enabled == true
      and .data.control_mode.configured == true
      and .data.control_mode.active == false
      and .data.control_mode.state == "inactive"
      and ((.data.control_mode.managementSessionId? // null) == null)
      and ((.data.control_mode.management_session_id? // null) == null)
    ' "${mode_body}" >/dev/null; then
      jq '{kernel_enabled:.data.kernel_enabled, control_mode:.data.control_mode}' "${mode_body}"
      completed="true"
      exit 0
    fi
  fi
  echo "Waiting for exact operator-bff kernel postcondition (${attempt}/20)..."
  sleep 1
done

echo "ERROR: operator-bff restarted without the exact enabled/configured/inactive postcondition." >&2
exit 1
