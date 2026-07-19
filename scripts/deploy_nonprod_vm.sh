#!/usr/bin/env bash
# Deploy Pantheon non-prod VM compose stacks from a verified git commit.
#
# This script is designed for GitHub Actions, but it can also be run by an
# operator from a workstation with gcloud access. The VM's human-facing checkout
# is used only as the git object source and snapshot target; deployment runs from
# a managed clean worktree under ~/pantheon-ci-deploy.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-pantheon-lupin-dev-20260719}"
REMOTE_USER="${REMOTE_USER:-lupin}"

DEV_VM="${DEV_VM:-pantheon-lupin-dev}"
DEV_ZONE="${DEV_ZONE:-asia-east1-b}"
DEV_REMOTE_DIR="${DEV_REMOTE_DIR:-/home/lupin/pantheon}"
DEV_BFF_CANONICAL_CORS_ORIGIN="${DEV_BFF_CANONICAL_CORS_ORIGIN:-https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io}"
DEV_BFF_CORS_ORIGINS="${DEV_BFF_CORS_ORIGINS:-${DEV_BFF_CANONICAL_CORS_ORIGIN},https://pantheon-ai-system-front-dev.lovable.app,https://pantheon-dev.lovable.app}"
DEV_BFF_REQUIRED_CORS_ORIGINS="${DEV_BFF_REQUIRED_CORS_ORIGINS:-https://preview--pantheon-dev.lovable.app,https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com,https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app,https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com}"
DEV_BFF_PUBLIC_HOST="${DEV_BFF_PUBLIC_HOST:-pantheon-lupin-dev-bff.35.201.204.12.sslip.io}"
DEV_FE_PUBLIC_HOST="${DEV_FE_PUBLIC_HOST:-pantheon-lupin-dev-fe.35.201.204.12.sslip.io}"
DEV_FE_STATIC_ROOT="${DEV_FE_STATIC_ROOT:-/var/www/pantheon-dev-fe}"
# Strict by default: the dev deploy must not silently re-force stub/permissive
# auth on every run. docker-compose.yml's own PANTHEON_BFF_AUTH_STUB/MODE
# defaults are strict/false, but this script always passes an explicit value
# into the compose environment (see PANTHEON_BFF_AUTH_STUB= below), which
# overrides the compose file default regardless of what it says. Operators who
# need a permissive dev session must opt in explicitly via
# DEV_BFF_AUTH_STUB=true DEV_BFF_AUTH_MODE=permissive.
DEV_BFF_AUTH_STUB="${DEV_BFF_AUTH_STUB:-false}"
DEV_BFF_AUTH_MODE="${DEV_BFF_AUTH_MODE:-strict}"
# Governed verifier/dev-login credentials for the strict auth cutover. These
# must come from a secret source (GitHub Actions secrets in CI), never from
# compose file defaults. When strict mode is requested without them, the
# preflight gate below refuses to deploy rather than shipping a strict-looking
# BFF where every protected route is actually unusable.
DEV_BFF_JWT_SECRET="${DEV_BFF_JWT_SECRET:-}"
DEV_BFF_JWT_ISSUER="${DEV_BFF_JWT_ISSUER:-pantheon-dev}"
DEV_BFF_JWT_AUDIENCE="${DEV_BFF_JWT_AUDIENCE:-bff-operators}"
DEV_BFF_JWKS_URI="${DEV_BFF_JWKS_URI:-}"
DEV_BFF_OIDC_DISCOVERY_URL="${DEV_BFF_OIDC_DISCOVERY_URL:-}"
DEV_BFF_OIDC_ISSUER="${DEV_BFF_OIDC_ISSUER:-}"
DEV_BFF_OIDC_AUDIENCE="${DEV_BFF_OIDC_AUDIENCE:-}"
DEV_BFF_OIDC_CLIENT_ID="${DEV_BFF_OIDC_CLIENT_ID:-}"
DEV_BFF_OIDC_CLIENT_SECRET="${DEV_BFF_OIDC_CLIENT_SECRET:-}"
DEV_BFF_ROLE_CLAIMS="${DEV_BFF_ROLE_CLAIMS:-roles,role}"
DEV_BFF_ROLE_MAP="${DEV_BFF_ROLE_MAP:-}"
DEV_BFF_ROLE_MAP_MODE="${DEV_BFF_ROLE_MAP_MODE:-passthrough}"
DEV_BFF_DEFAULT_ROLE="${DEV_BFF_DEFAULT_ROLE:-viewer}"
# Human-provisioned service credential shared only by operator-bff and the
# OpenClaw adapter. There is intentionally no generated/local fallback.
DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN="${DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN:-}"
DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED="${DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED:-true}"
DEV_BFF_TENANT_ID="${DEV_BFF_TENANT_ID:-tenant-dev}"
DEV_BFF_ALLOWED_TENANTS="${DEV_BFF_ALLOWED_TENANTS:-${DEV_BFF_TENANT_ID},pantheon-dev}"
DEV_ASSISTANT_KERNEL_ENABLED="${DEV_ASSISTANT_KERNEL_ENABLED:-true}"
DEV_ASSISTANT_CONTROL_MODE_STORE_PATH="${DEV_ASSISTANT_CONTROL_MODE_STORE_PATH:-/data/bff/assistant-control-mode.json}"
DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-300}"
DEV_ASSISTANT_REPAIR_REPO_URL="${DEV_ASSISTANT_REPAIR_REPO_URL:-/workspace/status-root}"
DEV_ASSISTANT_REPAIR_REMOTE_URL="${DEV_ASSISTANT_REPAIR_REMOTE_URL:-https://github.com/ajoe734/pantheon.git}"
DEV_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS="${DEV_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS:-https://github.com/ajoe734/execute-plans.git}"
DEV_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS="${DEV_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS:-https://github.com/ajoe734/execute-plans.git}"
DEV_BFF_STUB_CAPABILITIES="${DEV_BFF_STUB_CAPABILITIES:-assistant.kernel.debug,assistant.kernel.repair}"
DEV_STATUS_ROOT_HOST="${DEV_STATUS_ROOT_HOST:-}"
DEV_STATUS_ROOT_CONTAINER="${DEV_STATUS_ROOT_CONTAINER:-/workspace/status-root}"
DEV_MANAGEMENT_AI_STORE_BACKEND="${DEV_MANAGEMENT_AI_STORE_BACKEND:-postgres}"
DEV_MANAGEMENT_AI_STORE_SCHEMA="${DEV_MANAGEMENT_AI_STORE_SCHEMA:-management_ai}"
DEV_MANAGEMENT_AI_DB_USER="${DEV_MANAGEMENT_AI_DB_USER:-pantheon_management_ai}"
DEV_MANAGEMENT_AI_DB_PASSWORD="${DEV_MANAGEMENT_AI_DB_PASSWORD:-pantheon_management_ai_dev}"
DEV_MANAGEMENT_AI_DB_NAME="${DEV_MANAGEMENT_AI_DB_NAME:-pantheon}"
DEV_MANAGEMENT_AI_DATABASE_URL="${DEV_MANAGEMENT_AI_DATABASE_URL:-}"
DEV_MANAGEMENT_AI_ATTACH_BUCKET="${DEV_MANAGEMENT_AI_ATTACH_BUCKET:-}"
DEV_MANAGEMENT_AI_ATTACH_LOCATION="${DEV_MANAGEMENT_AI_ATTACH_LOCATION:-asia-east1}"
PANTHEON_DEV_DOCKER_PRUNE="${PANTHEON_DEV_DOCKER_PRUNE:-true}"
PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE="${PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE:-true}"
DEV_APP_DB_USER="${DEV_APP_DB_USER:-${PANTHEON_APP_DB_USER:-pantheon_app}}"

STAGING_CONTROL_VM="${STAGING_CONTROL_VM:-pantheon-lupin-staging-control}"
STAGING_CONTROL_ZONE="${STAGING_CONTROL_ZONE:-asia-east1-b}"
STAGING_CONTROL_REMOTE_DIR="${STAGING_CONTROL_REMOTE_DIR:-/home/lupin/code/pantheon}"

STAGING_EXEC_VM="${STAGING_EXEC_VM:-pantheon-lupin-staging-exec}"
STAGING_EXEC_ZONE="${STAGING_EXEC_ZONE:-asia-east1-b}"
STAGING_EXEC_REMOTE_DIR="${STAGING_EXEC_REMOTE_DIR:-/home/lupin/code/pantheon}"
STAGING_EXEC_HEALTH_URL="${STAGING_EXEC_HEALTH_URL:-http://10.50.0.21:28081}"
STAGING_BFF_CANONICAL_CORS_ORIGIN="${STAGING_BFF_CANONICAL_CORS_ORIGIN:-https://pantheon-lupin-staging-fe.104.155.223.192.sslip.io}"
STAGING_BFF_CORS_ORIGINS="${STAGING_BFF_CORS_ORIGINS:-${STAGING_BFF_CANONICAL_CORS_ORIGIN},https://pantheon-ai-system-front-staging-live.lovable.app}"

DEPLOY_ENV=""
COMPONENT="auto"
DEPLOY_SHA="${GITHUB_SHA:-}"
ALLOW_DIRTY="${PANTHEON_ALLOW_DIRTY_DEPLOY:-false}"
ALLOW_EXAMPLE_ENV="${PANTHEON_ALLOW_EXAMPLE_ENV:-false}"
DRY_RUN=false

verify_dev_environment_lease_contract() {
  if [[ "${DEPLOY_ENV}" != "dev" ]]; then
    return
  fi

  local lease_state_file="${PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE:-}"
  local guarded_lease_id="${PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID:-}"

  [[ -n "${guarded_lease_id}" ]] \
    || error "dev deployment requires the pinned lease guard lease ID"
  [[ -f "${lease_state_file}" && ! -L "${lease_state_file}" ]] \
    || error "dev deployment requires a regular lease state file"

  python3 - "${lease_state_file}" "${guarded_lease_id}" "${DEPLOY_SHA}" <<'PY'
import json
import re
import sys

state_path, guarded_lease_id, deploy_sha = sys.argv[1:]
with open(state_path, encoding="utf-8") as handle:
    state = json.load(handle)

expected = {
    "schemaVersion": 1,
    "repository": "ajoe734/execute-plans",
    "branch": "environment-coordination",
    "path": ".pantheon/environment-leases/pantheon-dev-environment.json",
    "resource": "pantheon-dev-environment",
    "mode": "deployment",
    "leaseId": guarded_lease_id,
    "expectedBackendSha": deploy_sha,
}
for key, expected_value in expected.items():
    if state.get(key) != expected_value:
        actual = state.get(key)
        raise SystemExit(
            f"dev environment lease {key} mismatch: "
            f"expected {expected_value!r}, got {actual!r}"
        )
if not re.fullmatch(r"[0-9a-f]{40}", deploy_sha):
    raise SystemExit("dev deployment SHA must be a full lowercase commit SHA")
PY

  info "dev environment lease contract verified: ${guarded_lease_id} -> ${DEPLOY_SHA}"
}

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy_nonprod_vm.sh --environment <dev|staging-live> --sha <commit> [options]

Options:
  --environment <name>   Required. dev or staging-live.
  --component <name>     auto, root, bff, control, exec, or all. Default: auto.
                         auto maps to root for dev and all for staging-live.
                         bff (dev only): rebuild only operator-bff; paper fleet
                         and all other services are left running untouched.
  --sha <commit>         Required unless GITHUB_SHA is set. Commit to deploy.
  --project-id <id>      GCP project. Default: pantheon-lupin-dev-20260719.
  --allow-dirty          Emergency only: stash dirty managed deploy worktree
                         changes before checkout.
  --allow-example-env    Allow staging to use env/*.env.example if real env files
                         are absent. Intended for rehearsal only.
  --dry-run              Print the target plan without SSHing.
  --help                 Show this message.

Environment overrides:
  REMOTE_USER
  PANTHEON_DEPLOY_WORKTREE_ROOT
  GITHUB_TOKEN
  DEV_VM DEV_ZONE DEV_REMOTE_DIR
  DEV_BFF_PUBLIC_HOST DEV_FE_PUBLIC_HOST DEV_FE_STATIC_ROOT
  DEV_BFF_CANONICAL_CORS_ORIGIN DEV_BFF_CORS_ORIGINS
  DEV_BFF_REQUIRED_CORS_ORIGINS DEV_BFF_AUTH_STUB DEV_BFF_AUTH_MODE
  DEV_BFF_JWT_SECRET DEV_BFF_JWT_ISSUER DEV_BFF_JWT_AUDIENCE
  DEV_BFF_JWKS_URI DEV_BFF_OIDC_DISCOVERY_URL
  DEV_BFF_OIDC_ISSUER DEV_BFF_OIDC_AUDIENCE
  DEV_BFF_OIDC_CLIENT_ID DEV_BFF_OIDC_CLIENT_SECRET
  DEV_BFF_ROLE_CLAIMS DEV_BFF_ROLE_MAP DEV_BFF_ROLE_MAP_MODE DEV_BFF_DEFAULT_ROLE
  DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED
  DEV_BFF_TENANT_ID DEV_BFF_ALLOWED_TENANTS
  DEV_ASSISTANT_KERNEL_ENABLED DEV_ASSISTANT_CONTROL_MODE_STORE_PATH
  DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS
  DEV_ASSISTANT_REPAIR_REPO_URL DEV_ASSISTANT_REPAIR_REMOTE_URL
  DEV_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS DEV_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS
  DEV_BFF_STUB_CAPABILITIES
  DEV_STATUS_ROOT_HOST DEV_STATUS_ROOT_CONTAINER
  DEV_MANAGEMENT_AI_STORE_BACKEND DEV_MANAGEMENT_AI_STORE_SCHEMA
  DEV_MANAGEMENT_AI_DB_USER DEV_MANAGEMENT_AI_DB_PASSWORD DEV_MANAGEMENT_AI_DB_NAME
  DEV_MANAGEMENT_AI_DATABASE_URL
  DEV_MANAGEMENT_AI_ATTACH_BUCKET DEV_MANAGEMENT_AI_ATTACH_LOCATION
  DEV_APP_DB_USER PANTHEON_APP_DB_USER
  STAGING_CONTROL_VM STAGING_CONTROL_ZONE STAGING_CONTROL_REMOTE_DIR
  STAGING_EXEC_VM STAGING_EXEC_ZONE STAGING_EXEC_REMOTE_DIR
  STAGING_EXEC_HEALTH_URL
  STAGING_BFF_CANONICAL_CORS_ORIGIN STAGING_BFF_CORS_ORIGINS
EOF
}

info() {
  echo "[nonprod-deploy] $*"
}

error() {
  echo "[nonprod-deploy] ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || error "$1 is required"
}

is_placeholder_credential() {
  local normalized="${1,,}"
  case "$normalized" in
    replace-me*|changeme*|change-me*|example*|dummy*|placeholder*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

shell_quote() {
  printf "%q" "$1"
}

append_csv_unique() {
  local merged="$1"
  local extras="$2"
  local item

  IFS=',' read -r -a extra_items <<< "$extras"
  for item in "${extra_items[@]}"; do
    item="${item//[[:space:]]/}"
    [[ -z "$item" ]] && continue
    if [[ ",${merged}," != *",${item},"* ]]; then
      if [[ -n "$merged" ]]; then
        merged+=",${item}"
      else
        merged="$item"
      fi
    fi
  done

  printf "%s" "$merged"
}

configure_management_ai_dev_env() {
  if [[ "$DEPLOY_ENV" != "dev" ]]; then
    return
  fi

  if [[ -z "$DEV_MANAGEMENT_AI_DATABASE_URL" ]]; then
    DEV_MANAGEMENT_AI_DATABASE_URL="postgresql://${DEV_MANAGEMENT_AI_DB_USER}:${DEV_MANAGEMENT_AI_DB_PASSWORD}@postgres:5432/${DEV_MANAGEMENT_AI_DB_NAME}"
  fi

  MANAGEMENT_AI_STORE_BACKEND="${MANAGEMENT_AI_STORE_BACKEND:-$DEV_MANAGEMENT_AI_STORE_BACKEND}"
  MANAGEMENT_AI_STORE_SCHEMA="${MANAGEMENT_AI_STORE_SCHEMA:-$DEV_MANAGEMENT_AI_STORE_SCHEMA}"
  MANAGEMENT_AI_DATABASE_URL="${MANAGEMENT_AI_DATABASE_URL:-$DEV_MANAGEMENT_AI_DATABASE_URL}"
  # Dev compose has a durable local attachment store; use GCS only when configured.
  PANTHEON_MGMT_AI_ATTACH_BUCKET="${PANTHEON_MGMT_AI_ATTACH_BUCKET:-$DEV_MANAGEMENT_AI_ATTACH_BUCKET}"
}

configure_management_ai_dev_kernel_env() {
  if [[ "$DEPLOY_ENV" != "dev" ]]; then
    return
  fi

  PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED:-$DEV_ASSISTANT_KERNEL_ENABLED}"
  PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-$DEV_ASSISTANT_CONTROL_MODE_STORE_PATH}"
  PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-$DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS}"
  PANTHEON_ASSISTANT_REPAIR_REPO_URL="${PANTHEON_ASSISTANT_REPAIR_REPO_URL:-$DEV_ASSISTANT_REPAIR_REPO_URL}"
  PANTHEON_ASSISTANT_REPAIR_REMOTE_URL="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL:-$DEV_ASSISTANT_REPAIR_REMOTE_URL}"
  PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS:-$DEV_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS}"
  PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS:-$DEV_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS}"
  PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES:-$DEV_BFF_STUB_CAPABILITIES}"
  PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN:-$DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN}"
  PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED:-$DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED}"
  PANTHEON_STATUS_ROOT_CONTAINER="${PANTHEON_STATUS_ROOT_CONTAINER:-$DEV_STATUS_ROOT_CONTAINER}"
  PANTHEON_STATUS_ROOT_HOST="${PANTHEON_STATUS_ROOT_HOST:-${DEV_STATUS_ROOT_HOST:-$DEV_REMOTE_DIR}}"
}

DEV_BFF_CORS_ORIGINS="$(append_csv_unique "$DEV_BFF_CORS_ORIGINS" "$DEV_BFF_CANONICAL_CORS_ORIGIN")"
DEV_BFF_CORS_ORIGINS="$(append_csv_unique "$DEV_BFF_CORS_ORIGINS" "$DEV_BFF_REQUIRED_CORS_ORIGINS")"
STAGING_BFF_CORS_ORIGINS="$(append_csv_unique "$STAGING_BFF_CORS_ORIGINS" "$STAGING_BFF_CANONICAL_CORS_ORIGIN")"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment)
      DEPLOY_ENV="${2:-}"
      shift 2
      ;;
    --component)
      COMPONENT="${2:-}"
      shift 2
      ;;
    --sha)
      DEPLOY_SHA="${2:-}"
      shift 2
      ;;
    --project-id)
      PROJECT_ID="${2:-}"
      shift 2
      ;;
    --allow-dirty)
      ALLOW_DIRTY=true
      shift
      ;;
    --allow-example-env)
      ALLOW_EXAMPLE_ENV=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      error "unknown option: $1"
      ;;
  esac
done

[[ -n "$DEPLOY_ENV" ]] || error "--environment is required"
[[ -n "$DEPLOY_SHA" ]] || error "--sha is required unless GITHUB_SHA is set"
[[ -n "$PROJECT_ID" ]] || error "--project-id is required or PROJECT_ID must be set"

case "$DEPLOY_ENV" in
  dev)
    [[ "$COMPONENT" == "auto" ]] && COMPONENT="root"
    case "$COMPONENT" in
      root|bff) ;;
      *) error "dev supports only --component root or --component bff" ;;
    esac
    ;;
  staging-live)
    [[ "$COMPONENT" == "auto" ]] && COMPONENT="all"
    case "$COMPONENT" in
      control|exec|all) ;;
      *) error "staging-live supports --component control, exec, or all" ;;
    esac
    ;;
  *)
    error "--environment must be dev or staging-live"
    ;;
esac

configure_management_ai_dev_env
configure_management_ai_dev_kernel_env

if [[ "$DRY_RUN" == "true" ]]; then
  info "dry run"
  info "project=${PROJECT_ID}"
  info "environment=${DEPLOY_ENV}"
  info "component=${COMPONENT}"
  info "sha=${DEPLOY_SHA}"
  info "allow_dirty=${ALLOW_DIRTY}"
  info "allow_example_env=${ALLOW_EXAMPLE_ENV}"
  info "dev_bff_cors_origins=${DEV_BFF_CORS_ORIGINS}"
  info "dev_bff_public_host=${DEV_BFF_PUBLIC_HOST}"
  info "dev_fe_public_host=${DEV_FE_PUBLIC_HOST}"
  info "dev_fe_static_root=${DEV_FE_STATIC_ROOT}"
  info "dev_bff_auth_stub=${DEV_BFF_AUTH_STUB}"
  info "dev_bff_auth_mode=${DEV_BFF_AUTH_MODE}"
  info "dev_bff_jwt_secret_configured=$([[ -n "$DEV_BFF_JWT_SECRET" ]] && echo true || echo false)"
  info "dev_bff_jwt_issuer_configured=$([[ -n "$DEV_BFF_JWT_ISSUER" ]] && echo true || echo false)"
  info "dev_bff_jwt_audience_configured=$([[ -n "$DEV_BFF_JWT_AUDIENCE" ]] && echo true || echo false)"
  info "dev_bff_jwks_configured=$([[ -n "$DEV_BFF_JWKS_URI" || -n "$DEV_BFF_OIDC_DISCOVERY_URL" ]] && echo true || echo false)"
  info "dev_bff_external_oidc_contract_configured=$([[ -n "$DEV_BFF_OIDC_ISSUER" && -n "$DEV_BFF_OIDC_AUDIENCE" ]] && echo true || echo false)"
  info "dev_bff_oidc_client_configured=$([[ -n "$DEV_BFF_OIDC_CLIENT_ID" && -n "$DEV_BFF_OIDC_CLIENT_SECRET" ]] && echo true || echo false)"
  info "dev_bff_role_claims_configured=$([[ -n "$DEV_BFF_ROLE_CLAIMS" ]] && echo true || echo false)"
  info "dev_bff_role_map_configured=$([[ -n "$DEV_BFF_ROLE_MAP" ]] && echo true || echo false)"
  info "dev_bff_role_map_mode=${DEV_BFF_ROLE_MAP_MODE}"
  info "dev_bff_default_role=${DEV_BFF_DEFAULT_ROLE}"
  info "dev_openclaw_adapter_service_auth_required=${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED:-}"
  info "dev_openclaw_adapter_service_token_configured=$([[ -n "${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN:-}" ]] && echo true || echo false)"
  info "dev_assistant_kernel_enabled=${PANTHEON_ASSISTANT_KERNEL_ENABLED:-}"
  info "dev_assistant_control_mode_store_path=${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-}"
  info "dev_assistant_control_idle_ttl_seconds=${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-}"
  info "dev_assistant_repair_repo_url=${PANTHEON_ASSISTANT_REPAIR_REPO_URL:-}"
  info "dev_assistant_repair_remote_url=${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL:-}"
  info "dev_assistant_repair_repo_url_execute_plans=${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS:-}"
  info "dev_assistant_repair_remote_url_execute_plans=${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS:-}"
  info "dev_bff_stub_capabilities_configured=$([[ -n "${PANTHEON_BFF_STUB_CAPABILITIES:-}" ]] && echo true || echo false)"
  info "dev_status_root_host=${PANTHEON_STATUS_ROOT_HOST:-}"
  info "dev_status_root_container=${PANTHEON_STATUS_ROOT_CONTAINER:-}"
  info "dev_docker_prune=${PANTHEON_DEV_DOCKER_PRUNE}"
  info "management_ai_store_backend=${MANAGEMENT_AI_STORE_BACKEND:-}"
  info "management_ai_store_schema=${MANAGEMENT_AI_STORE_SCHEMA:-}"
  info "management_ai_database_user=${DEV_MANAGEMENT_AI_DB_USER}"
  info "management_ai_database_url_configured=$([[ -n "${MANAGEMENT_AI_DATABASE_URL:-}" ]] && echo true || echo false)"
  info "management_ai_attach_bucket=${PANTHEON_MGMT_AI_ATTACH_BUCKET:-}"
  info "management_ai_attach_location=${DEV_MANAGEMENT_AI_ATTACH_LOCATION}"
  info "staging_exec_health_url=${STAGING_EXEC_HEALTH_URL}"
  info "staging_bff_cors_origins=${STAGING_BFF_CORS_ORIGINS}"
  exit 0
fi

# The shared dev lease is verified before any other dev gate so a stale or
# mismatched lease is rejected first, before any dev bucket, SSH, checkout,
# compose, or smoke mutation.  Staging-live is an independent environment and must not
# depend on the shared dev lease.
verify_dev_environment_lease_contract

if [[ "$DEPLOY_ENV" == "dev" && "$DEV_BFF_AUTH_MODE" == "strict" && "$DEV_BFF_AUTH_STUB" != "true" ]]; then
  if [[ -z "$DEV_BFF_JWT_SECRET" || -z "$DEV_BFF_OIDC_CLIENT_ID" || -z "$DEV_BFF_OIDC_CLIENT_SECRET" ]]; then
    error "strict auth cutover requested (DEV_BFF_AUTH_MODE=strict, DEV_BFF_AUTH_STUB=${DEV_BFF_AUTH_STUB}) but no governed verifier/dev-login credentials are configured (DEV_BFF_JWT_SECRET, DEV_BFF_OIDC_CLIENT_ID, DEV_BFF_OIDC_CLIENT_SECRET); refusing to deploy a BFF where every protected route would be unusable"
  fi
fi

if [[ "$DEPLOY_ENV" == "dev" ]]; then
  case "${DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED,,}" in
    1|true|yes|on)
      if [[ -z "${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN:-}" ]] \
        || is_placeholder_credential "${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN:-}"; then
        error "strict OpenClaw adapter service auth requires a human-provisioned DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN; refusing to deploy with an empty or fabricated service credential"
      fi
      ;;
    0|false|no|off)
      ;;
    *)
      error "DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED must be true or false"
      ;;
  esac
fi

require_cmd gcloud

ensure_management_ai_bucket() {
  if [[ "$DEPLOY_ENV" != "dev" ]]; then
    return
  fi

  local bucket="${PANTHEON_MGMT_AI_ATTACH_BUCKET:-}"
  if [[ -z "$bucket" ]]; then
    info "dev Management AI attachment bucket not configured; using local attachment store"
    return
  fi

  info "preflight Management AI attachment bucket: gs://${bucket}"
  if gcloud storage buckets describe "gs://${bucket}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    info "bucket visible to deploy runner: gs://${bucket}"
  else
    info "bucket not visible to deploy runner; dev VM will attempt idempotent provisioning"
  fi
}

ensure_management_ai_bucket

ssh_bash() {
  local vm="$1"
  local zone="$2"
  local remote_dir="$3"
  local remote_component="$4"
  local command_prefix

  command_prefix="PANTHEON_DEPLOY_ENV=$(shell_quote "$DEPLOY_ENV")"
  command_prefix+=" PANTHEON_DEPLOY_COMPONENT=$(shell_quote "$remote_component")"
  command_prefix+=" PANTHEON_DEPLOY_SHA=$(shell_quote "$DEPLOY_SHA")"
  command_prefix+=" PANTHEON_DEPLOY_PROJECT_ID=$(shell_quote "$PROJECT_ID")"
  command_prefix+=" PANTHEON_REMOTE_DIR=$(shell_quote "$remote_dir")"
  command_prefix+=" PANTHEON_DEPLOY_WORKTREE_ROOT=$(shell_quote "${PANTHEON_DEPLOY_WORKTREE_ROOT:-}")"
  command_prefix+=" PANTHEON_GITHUB_TOKEN=$(shell_quote "${GITHUB_TOKEN:-}")"
  command_prefix+=" PANTHEON_ALLOW_DIRTY_DEPLOY=$(shell_quote "$ALLOW_DIRTY")"
  command_prefix+=" PANTHEON_ALLOW_EXAMPLE_ENV=$(shell_quote "$ALLOW_EXAMPLE_ENV")"
  command_prefix+=" PANTHEON_DEV_BFF_CORS_ORIGINS=$(shell_quote "$DEV_BFF_CORS_ORIGINS")"
  command_prefix+=" PANTHEON_DEV_BFF_PUBLIC_HOST=$(shell_quote "$DEV_BFF_PUBLIC_HOST")"
  command_prefix+=" PANTHEON_DEV_FE_PUBLIC_HOST=$(shell_quote "$DEV_FE_PUBLIC_HOST")"
  command_prefix+=" PANTHEON_DEV_FE_STATIC_ROOT=$(shell_quote "$DEV_FE_STATIC_ROOT")"
  command_prefix+=" PANTHEON_DEV_BFF_AUTH_STUB=$(shell_quote "$DEV_BFF_AUTH_STUB")"
  command_prefix+=" PANTHEON_DEV_BFF_AUTH_MODE=$(shell_quote "$DEV_BFF_AUTH_MODE")"
  command_prefix+=" PANTHEON_DEV_BFF_JWT_SECRET=$(shell_quote "$DEV_BFF_JWT_SECRET")"
  command_prefix+=" PANTHEON_DEV_BFF_JWT_ISSUER=$(shell_quote "$DEV_BFF_JWT_ISSUER")"
  command_prefix+=" PANTHEON_DEV_BFF_JWT_AUDIENCE=$(shell_quote "$DEV_BFF_JWT_AUDIENCE")"
  command_prefix+=" PANTHEON_DEV_BFF_JWKS_URI=$(shell_quote "$DEV_BFF_JWKS_URI")"
  command_prefix+=" PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL=$(shell_quote "$DEV_BFF_OIDC_DISCOVERY_URL")"
  command_prefix+=" PANTHEON_DEV_BFF_OIDC_ISSUER=$(shell_quote "$DEV_BFF_OIDC_ISSUER")"
  command_prefix+=" PANTHEON_DEV_BFF_OIDC_AUDIENCE=$(shell_quote "$DEV_BFF_OIDC_AUDIENCE")"
  command_prefix+=" PANTHEON_DEV_BFF_OIDC_CLIENT_ID=$(shell_quote "$DEV_BFF_OIDC_CLIENT_ID")"
  command_prefix+=" PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET=$(shell_quote "$DEV_BFF_OIDC_CLIENT_SECRET")"
  command_prefix+=" PANTHEON_DEV_BFF_ROLE_CLAIMS=$(shell_quote "$DEV_BFF_ROLE_CLAIMS")"
  command_prefix+=" PANTHEON_DEV_BFF_ROLE_MAP=$(shell_quote "$DEV_BFF_ROLE_MAP")"
  command_prefix+=" PANTHEON_DEV_BFF_ROLE_MAP_MODE=$(shell_quote "$DEV_BFF_ROLE_MAP_MODE")"
  command_prefix+=" PANTHEON_DEV_BFF_DEFAULT_ROLE=$(shell_quote "$DEV_BFF_DEFAULT_ROLE")"
  command_prefix+=" PANTHEON_DEV_BFF_TENANT_ID=$(shell_quote "$DEV_BFF_TENANT_ID")"
  command_prefix+=" PANTHEON_DEV_BFF_ALLOWED_TENANTS=$(shell_quote "$DEV_BFF_ALLOWED_TENANTS")"
  command_prefix+=" PANTHEON_ASSISTANT_KERNEL_ENABLED=$(shell_quote "${PANTHEON_ASSISTANT_KERNEL_ENABLED:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH=$(shell_quote "${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS=$(shell_quote "${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REPO_URL=$(shell_quote "${PANTHEON_ASSISTANT_REPAIR_REPO_URL:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REMOTE_URL=$(shell_quote "${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS=$(shell_quote "${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS=$(shell_quote "${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS:-}")"
  command_prefix+=" PANTHEON_BFF_STUB_CAPABILITIES=$(shell_quote "${PANTHEON_BFF_STUB_CAPABILITIES:-}")"
  command_prefix+=" PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN=$(shell_quote "${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN:-}")"
  command_prefix+=" PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED=$(shell_quote "${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED:-}")"
  command_prefix+=" PANTHEON_STATUS_ROOT_HOST=$(shell_quote "${PANTHEON_STATUS_ROOT_HOST:-}")"
  command_prefix+=" PANTHEON_STATUS_ROOT_CONTAINER=$(shell_quote "${PANTHEON_STATUS_ROOT_CONTAINER:-}")"
  command_prefix+=" PANTHEON_DEV_DOCKER_PRUNE=$(shell_quote "${PANTHEON_DEV_DOCKER_PRUNE:-true}")"
  command_prefix+=" PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE=$(shell_quote "${PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE:-true}")"
  command_prefix+=" MANAGEMENT_AI_STORE_BACKEND=$(shell_quote "${MANAGEMENT_AI_STORE_BACKEND:-}")"
  command_prefix+=" MANAGEMENT_AI_STORE_SCHEMA=$(shell_quote "${MANAGEMENT_AI_STORE_SCHEMA:-}")"
  command_prefix+=" MANAGEMENT_AI_STORE_DSN=$(shell_quote "${MANAGEMENT_AI_STORE_DSN:-}")"
  command_prefix+=" MANAGEMENT_AI_DATABASE_URL=$(shell_quote "${MANAGEMENT_AI_DATABASE_URL:-}")"
  command_prefix+=" PANTHEON_MGMT_AI_ATTACH_BUCKET=$(shell_quote "${PANTHEON_MGMT_AI_ATTACH_BUCKET:-}")"
  command_prefix+=" PANTHEON_MGMT_AI_ATTACH_LOCATION=$(shell_quote "${DEV_MANAGEMENT_AI_ATTACH_LOCATION:-}")"
  command_prefix+=" PANTHEON_MANAGEMENT_AI_DB_USER=$(shell_quote "${DEV_MANAGEMENT_AI_DB_USER:-}")"
  command_prefix+=" PANTHEON_MANAGEMENT_AI_DB_PASSWORD=$(shell_quote "${DEV_MANAGEMENT_AI_DB_PASSWORD:-}")"
  command_prefix+=" PANTHEON_MANAGEMENT_AI_DB_NAME=$(shell_quote "${DEV_MANAGEMENT_AI_DB_NAME:-}")"
  command_prefix+=" PANTHEON_MANAGEMENT_AI_APP_DB_USER=$(shell_quote "${DEV_APP_DB_USER:-pantheon_app}")"
  command_prefix+=" PANTHEON_STAGING_EXEC_HEALTH_URL=$(shell_quote "$STAGING_EXEC_HEALTH_URL")"
  command_prefix+=" PANTHEON_STAGING_BFF_CORS_ORIGINS=$(shell_quote "$STAGING_BFF_CORS_ORIGINS")"
  command_prefix+=" bash -s"

  info "ssh ${vm} (${zone}) component=${remote_component} sha=${DEPLOY_SHA}"
  gcloud compute ssh "${REMOTE_USER}@${vm}" \
    --project="${PROJECT_ID}" \
    --zone="${zone}" \
    --quiet \
    --command="${command_prefix}" <<'REMOTE'
set -euo pipefail

info() {
  echo "[remote-deploy] $*"
}

error() {
  echo "[remote-deploy] ERROR: $*" >&2
  exit 1
}

curl_with_retry() {
  local url="$1"
  local attempts="${2:-12}"
  local delay="${3:-5}"
  local i

  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS "$url" >/dev/null; then
      return 0
    fi
    sleep "$delay"
  done

  curl -fsS "$url" >/dev/null
}

ensure_dev_caddy_ingress() (
  if [[ "${PANTHEON_DEPLOY_ENV}" != "dev" ]]; then
    return
  fi

  local bff_host="${PANTHEON_DEV_BFF_PUBLIC_HOST}"
  local fe_host="${PANTHEON_DEV_FE_PUBLIC_HOST}"
  local fe_root="${PANTHEON_DEV_FE_STATIC_ROOT}"
  local template="deploy/caddy/dev.Caddyfile.tmpl"
  local rendered

  [[ "$bff_host" =~ ^[A-Za-z0-9.-]+$ ]] \
    || error "invalid dev BFF public host: ${bff_host}"
  [[ "$fe_host" =~ ^[A-Za-z0-9.-]+$ ]] \
    || error "invalid dev FE public host: ${fe_host}"
  [[ "$fe_root" =~ ^/[A-Za-z0-9._/-]+$ ]] \
    || error "invalid dev FE static root: ${fe_root}"
  [[ -f "$template" && ! -L "$template" ]] \
    || error "versioned dev Caddy template is missing or unsafe: ${template}"

  if ! command -v caddy >/dev/null 2>&1; then
    info "installing Caddy for dev HTTPS ingress"
    sudo -n apt-get update
    sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y caddy
  fi

  rendered="$(mktemp)"
  trap 'rm -f "$rendered"' EXIT
  sed \
    -e "s|__BFF_HOST__|${bff_host}|g" \
    -e "s|__FE_HOST__|${fe_host}|g" \
    -e "s|__FE_ROOT__|${fe_root}|g" \
    "$template" >"$rendered"
  sudo -n install -o root -g root -m 0644 "$rendered" /etc/caddy/Caddyfile
  sudo -n caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
  sudo -n systemctl enable --now caddy
  sudo -n systemctl reload caddy
  curl_with_retry "https://${bff_host}/health" 12 5 \
    || error "dev BFF HTTPS ingress did not become healthy: ${bff_host}"
  info "dev Caddy HTTPS ingress verified: ${bff_host}"
)

assert_bff_source_sha() {
  local url="$1"
  local payload
  local actual

  payload="$(curl -fsS "$url")"
  actual="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("source_commit_sha") or "")' <<<"$payload")"
  if [[ "$actual" != "${PANTHEON_DEPLOY_SHA}" ]]; then
    error "BFF source SHA mismatch: expected ${PANTHEON_DEPLOY_SHA}, got ${actual:-missing}"
  fi
  info "BFF source SHA verified: ${actual}"
}

assert_bff_auth_gate() {
  local base_url="$1"

  if [[ "${PANTHEON_DEV_BFF_AUTH_MODE}" != "strict" || "${PANTHEON_DEV_BFF_AUTH_STUB}" == "true" ]]; then
    info "strict auth gate skipped (auth_mode=${PANTHEON_DEV_BFF_AUTH_MODE}, auth_stub=${PANTHEON_DEV_BFF_AUTH_STUB})"
    return 0
  fi

  info "asserting hosted BFF auth posture is strict (auth_stub=false, auth_mode=strict)"
  local version_payload
  version_payload="$(curl -fsS "${base_url}/bff/version")"
  python3 -c '
import json, sys

payload = json.loads(sys.argv[1])
posture = payload.get("config_posture")
if not isinstance(posture, dict):
    # Compatibility with deployment targets that predate the canonical
    # config_posture envelope. New BFF versions publish posture only there.
    posture = payload
auth_stub = posture.get("auth_stub")
auth_mode = posture.get("auth_mode")
assert auth_stub is False, f"auth_stub={auth_stub!r}, expected False"
assert auth_mode == "strict", f"auth_mode={auth_mode!r}, expected strict"
' "$version_payload" || error "hosted BFF auth posture is not strict: ${version_payload}"

  if [[ -z "${PANTHEON_DEV_BFF_OIDC_CLIENT_ID}" || -z "${PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET}" ]]; then
    error "strict auth cutover requires dev-login verifier credentials on the deploy runner; none were provided"
  fi

  info "asserting authenticated dev-login round trip succeeds"
  local login_payload
  local login_body
  login_body="$(python3 -c 'import json,sys; print(json.dumps({"grant_type":"client_credentials","client_id":sys.argv[1],"client_secret":sys.argv[2]}))' \
    "${PANTHEON_DEV_BFF_OIDC_CLIENT_ID}" "${PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET}")"
  login_payload="$(curl -fsS -X POST "${base_url}/bff/auth/dev-login" \
    -H 'Content-Type: application/json' \
    -d "${login_body}")" \
    || error "authenticated dev-login round trip failed against ${base_url}/bff/auth/dev-login"
  local access_token
  access_token="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' <<<"$login_payload")"
  curl -fsS "${base_url}/bff/me" -H "Authorization: Bearer ${access_token}" >/dev/null \
    || error "authenticated /bff/me check failed with a freshly issued dev-login token"
  local readiness_payload
  readiness_payload="$(curl -fsS "${base_url}/bff/auth/readiness" \
    -H "Authorization: Bearer ${access_token}")" \
    || error "strict browser readiness probe failed against ${base_url}/bff/auth/readiness"
  python3 -c '
import json
import sys

expected_sha = sys.argv[1]
payload = json.loads(sys.argv[2])
data = payload.get("data") or {}
auth = data.get("auth") or {}
assert data.get("sourceCommitSha") == expected_sha, data.get("sourceCommitSha")
assert data.get("authReady") is True, data
assert data.get("providerReady") is True, data
assert data.get("ready") is True, data
assert auth.get("mode") == "strict", auth
assert auth.get("stub") is False, auth
assert auth.get("sessionKind") in {"bearer", "cookie"}, auth
assert auth.get("operatorRoleReady") is True, auth
assert auth.get("interactionCapabilityReady") is True, auth
assert auth.get("verifierReady") is True, auth
' "${PANTHEON_DEPLOY_SHA}" "${readiness_payload}" \
    || error "strict browser readiness contract is not satisfied"
  info "authenticated dev-login and strict browser readiness round trip succeeded"

  info "asserting a fixed/arbitrary bearer is rejected (fail-closed negative gate)"
  local fixed_bearer_status
  fixed_bearer_status="$(curl -s -o /dev/null -w '%{http_code}' "${base_url}/bff/me" -H 'Authorization: Bearer op-fixed:operator:mfa')"
  if [[ "$fixed_bearer_status" == "200" ]]; then
    error "hosted BFF accepted a fixed/arbitrary bearer token at ${base_url}/bff/me (strict auth cutover is not effective)"
  fi
  info "fixed bearer correctly rejected with HTTP ${fixed_bearer_status}"
}

snapshot_remote_state() {
  local project="$1"
  local compose_file="$2"
  local ts
  local dir
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  dir="${HOME}/pantheon-deploy-snapshots/${ts}-${PANTHEON_DEPLOY_ENV}-${PANTHEON_DEPLOY_COMPONENT}"
  mkdir -p "$dir"
  git rev-parse HEAD >"${dir}/git-head.txt" 2>&1 || true
  git status --short >"${dir}/git-status.txt" 2>&1 || true
  git diff >"${dir}/git-diff.patch" 2>&1 || true
  docker compose -p "$project" -f "$compose_file" ps >"${dir}/compose-ps.txt" 2>&1 || true
  info "snapshot written: ${dir}"
}

preserve_known_deploy_runtime_state() {
  local known_paths=(
    ".orchestrator/metrics"
    ".orchestrator/task-briefs"
    ".orchestrator/watchdog-state.json"
    "trade_journey_events.json"
  )
  local planning_pointer_path=".orchestrator/planning-session-pointer.json"
  local planning_session_path=""
  local present_paths=()
  local path
  local runtime_status
  local stash_label
  local exclude_file

  if [[ -e "$planning_pointer_path" || -L "$planning_pointer_path" ]]; then
    # Resolve the session from the current runtime pointer before stashing or
    # detaching. The validator is loaded from the exact target commit so this
    # first deployment of a validator change does not depend on the old
    # checkout containing the helper. It accepts only canonical repo-relative
    # planning session paths and rejects traversal and symlink escapes.
    planning_session_path="$({
      git show "${PANTHEON_DEPLOY_SHA}:scripts/deploy_planning_runtime_paths.py" \
        || error "target commit is missing the planning runtime path validator"
    } | python3 - "$PWD" "$planning_pointer_path")" \
      || error "canonical planning runtime pointer failed path validation"
    known_paths+=("$planning_pointer_path" "$planning_session_path")
  fi

  for path in "${known_paths[@]}"; do
    if [[ ! -e "$path" ]]; then
      continue
    fi
    # Runtime-owned untracked files may not be readable by the deploy user.
    # Register them in this checkout's private exclude file before asking git
    # for worktree status; otherwise `git stash --include-untracked` attempts
    # to open the file and aborts the deployment with EACCES. The file remains
    # in place across the detached checkout and the repository-level ignore in
    # the target commit makes this local exclusion unnecessary thereafter.
    if ! git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
      exclude_file="$(git rev-parse --git-path info/exclude)"
      mkdir -p "$(dirname "$exclude_file")"
      if ! grep -Fqx "/${path}" "$exclude_file" 2>/dev/null; then
        printf '/%s\n' "$path" >>"$exclude_file"
      fi
      continue
    fi
    # Skip gitignored runtime paths (e.g. .orchestrator/metrics,
    # .orchestrator/watchdog-state.json). `git checkout` never touches ignored
    # files, so they survive the detach untouched and do not need stashing; and
    # `git stash push -- <ignored-pathspec>` hard-errors ("paths are ignored by
    # .gitignore"), which under `set -e` aborts the whole deploy. Only tracked
    # paths can be clobbered by checkout, so only those need preserving.
    if git check-ignore -q -- "$path"; then
      continue
    fi
    present_paths+=("$path")
  done

  if [[ "${#present_paths[@]}" -eq 0 ]]; then
    return
  fi

  runtime_status="$(git status --porcelain -- "${present_paths[@]}")"
  if [[ -z "$runtime_status" ]]; then
    return
  fi

  stash_label="deploy-runtime-state-${PANTHEON_DEPLOY_ENV}-${PANTHEON_DEPLOY_COMPONENT}-${PANTHEON_DEPLOY_SHA:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
  info "preserving known deploy runtime state before checkout (${stash_label})"
  git stash push --include-untracked -m "$stash_label" -- "${present_paths[@]}" >/dev/null
}

preserve_target_tracked_untracked_paths() {
  local target_tracked_paths=()
  local entry
  local status
  local path
  local stash_label

  while IFS= read -r -d '' entry; do
    status="${entry:0:2}"
    path="${entry:3}"
    if [[ "$status" != "??" || -z "$path" ]]; then
      continue
    fi
    if git cat-file -e "${PANTHEON_DEPLOY_SHA}:${path}" 2>/dev/null; then
      target_tracked_paths+=("$path")
    fi
  done < <(git status --porcelain -z)

  if [[ "${#target_tracked_paths[@]}" -eq 0 ]]; then
    return
  fi

  stash_label="deploy-target-tracked-untracked-${PANTHEON_DEPLOY_ENV}-${PANTHEON_DEPLOY_COMPONENT}-${PANTHEON_DEPLOY_SHA:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
  info "preserving untracked paths that target commit tracks before checkout (${stash_label})"
  git stash push --include-untracked -m "$stash_label" -- "${target_tracked_paths[@]}" >/dev/null
}

require_clean_checkout() {
  local status
  local stash_label

  preserve_known_deploy_runtime_state
  preserve_target_tracked_untracked_paths

  status="$(git status --porcelain)"
  if [[ -n "$status" && "${PANTHEON_ALLOW_DIRTY_DEPLOY}" != "true" ]]; then
    git status --short >&2
    error "managed deploy worktree is dirty; refusing deploy without --allow-dirty"
  fi

  if [[ -n "$status" ]]; then
    stash_label="deploy-dirty-${PANTHEON_DEPLOY_ENV}-${PANTHEON_DEPLOY_COMPONENT}-${PANTHEON_DEPLOY_SHA:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
    info "dirty managed deploy worktree allowed by explicit flag; stashing local changes before checkout (${stash_label})"
    git stash push --include-untracked -m "$stash_label" >/dev/null
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    git status --short >&2
    error "managed deploy worktree is still dirty after preserve step"
  fi
}

git_fetch_origin() {
  local prune_flag=()
  if [[ "${1:-}" == "--prune" ]]; then
    prune_flag=(--prune)
    shift
  fi

  if [[ -n "${PANTHEON_GITHUB_TOKEN:-}" ]]; then
    local github_basic_auth
    github_basic_auth="$(printf 'x-access-token:%s' "${PANTHEON_GITHUB_TOKEN}" | base64 | tr -d '\n')"
    info "fetch auth: github token present"
    git -c "http.extraheader=AUTHORIZATION: basic ${github_basic_auth}" \
      fetch --recurse-submodules=no "${prune_flag[@]}" origin "$@"
  else
    info "fetch auth: no github token"
    git fetch --recurse-submodules=no "${prune_flag[@]}" origin "$@"
  fi
}

git_fetch_origin_default_refs() {
  git_fetch_origin \
    --prune \
    '+refs/heads/*:refs/remotes/origin/*' \
    '+refs/tags/*:refs/tags/*'
}

prepare_deploy_worktree() {
  local sha="${PANTHEON_DEPLOY_SHA}"
  local source_dir="${PANTHEON_REMOTE_DIR}"
  local root="${PANTHEON_DEPLOY_WORKTREE_ROOT:-${HOME}/pantheon-ci-deploy}"
  local deploy_dir="${root}/${PANTHEON_DEPLOY_ENV}-${PANTHEON_DEPLOY_COMPONENT}"
  local marker="${root}/.${PANTHEON_DEPLOY_ENV}-${PANTHEON_DEPLOY_COMPONENT}.marker"

  cd "$source_dir"
  info "fetching origin"
  git_fetch_origin_default_refs
  if ! git cat-file -e "${sha}^{commit}" 2>/dev/null; then
    git_fetch_origin "$sha"
  fi

  mkdir -p "$root"

  if [[ -e "$deploy_dir" ]]; then
    [[ -f "$marker" ]] || error "refusing to reuse unmarked deploy path: ${deploy_dir}"
    [[ "$(cat "$marker")" == "$deploy_dir" ]] || error "deploy marker does not match ${deploy_dir}"
    git -C "$deploy_dir" rev-parse --is-inside-work-tree >/dev/null
    cd "$deploy_dir"
    require_clean_checkout
    info "reusing managed deploy worktree ${deploy_dir}"
    git_fetch_origin_default_refs
    git checkout --detach "$sha"
  else
    info "creating managed deploy worktree ${deploy_dir}"
    git worktree add --detach "$deploy_dir" "$sha"
    printf '%s\n' "$deploy_dir" >"$marker"
    cd "$deploy_dir"
  fi

  git submodule update --init --recursive
  info "prepared deploy worktree ${deploy_dir} at ${sha}"
}

real_env_or_example() {
  local real_file="$1"
  local example_file="$2"

  if [[ -f "$real_file" ]]; then
    printf '%s\n' "$real_file"
    return
  fi

  if [[ -f "${PANTHEON_REMOTE_DIR}/${real_file}" ]]; then
    printf '%s\n' "${PANTHEON_REMOTE_DIR}/${real_file}"
    return
  fi

  if [[ "${PANTHEON_ALLOW_EXAMPLE_ENV}" == "true" && -f "$example_file" ]]; then
    info "using example env file for rehearsal: ${example_file}" >&2
    printf '%s\n' "$example_file"
    return
  fi

  error "missing ${real_file}; pass --allow-example-env only for rehearsal"
}

use_local_management_ai_attachment_store() {
  local reason="$1"

  info "Management AI attachment bucket unavailable (${reason}); using local attachment store"
  PANTHEON_MGMT_AI_ATTACH_BUCKET=""
  export PANTHEON_MGMT_AI_ATTACH_BUCKET
}

ensure_dev_management_ai_bucket() {
  if [[ "${PANTHEON_DEPLOY_ENV}" != "dev" ]]; then
    return
  fi

  local bucket="${PANTHEON_MGMT_AI_ATTACH_BUCKET:-}"
  if [[ -z "$bucket" ]]; then
    info "dev Management AI attachment bucket not configured; using local attachment store"
    return
  fi
  command -v curl >/dev/null 2>&1 || error "curl is required on the dev VM to provision ${bucket}"
  command -v python3 >/dev/null 2>&1 || error "python3 is required on the dev VM to parse metadata token JSON"

  local project="${PANTHEON_DEPLOY_PROJECT_ID:-}"
  [[ -n "$project" ]] || error "PANTHEON_DEPLOY_PROJECT_ID is required for bucket provisioning"
  local location="${PANTHEON_MGMT_AI_ATTACH_LOCATION:-asia-east1}"
  local location_upper
  location_upper="$(printf '%s' "$location" | tr '[:lower:]' '[:upper:]')"

  case "$bucket" in
    *[!a-z0-9.-]*)
      error "invalid GCS bucket name for Management AI attachments: ${bucket}"
      ;;
  esac

  local token_json
  local access_token
  token_json="$(
    curl -fsS \
      -H "Metadata-Flavor: Google" \
      "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
  )"
  access_token="$(printf '%s' "$token_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
  [[ -n "$access_token" ]] || error "metadata service did not return an access token"

  info "ensuring Management AI attachment bucket from dev VM metadata identity: gs://${bucket}"
  local probe_object
  local probe_object_encoded
  local probe_file
  local probe_read_file
  probe_object="management-ai-attachments/.deploy-probe-${PANTHEON_DEPLOY_ENV}-$(date -u +%Y%m%dT%H%M%SZ)-$$.txt"
  probe_object_encoded="$(
    python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$probe_object"
  )"
  probe_file="$(mktemp)"
  probe_read_file="$(mktemp)"
  printf 'pantheon management ai attachment bucket probe %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$probe_file"

  if curl -fsS \
    -X POST \
    -H "Authorization: Bearer ${access_token}" \
    -H "Content-Type: text/plain" \
    "https://storage.googleapis.com/upload/storage/v1/b/${bucket}/o?uploadType=media&name=${probe_object_encoded}" \
    --data-binary "@${probe_file}" >/dev/null 2>&1; then
    if curl -fsS \
      -H "Authorization: Bearer ${access_token}" \
      "https://storage.googleapis.com/storage/v1/b/${bucket}/o/${probe_object_encoded}?alt=media" >"${probe_read_file}" \
      && cmp -s "$probe_file" "$probe_read_file"; then
      curl -fsS \
        -X DELETE \
        -H "Authorization: Bearer ${access_token}" \
        "https://storage.googleapis.com/storage/v1/b/${bucket}/o/${probe_object_encoded}" >/dev/null 2>&1 || true
      rm -f "$probe_file" "$probe_read_file"
      info "bucket object read/write probe passed: gs://${bucket}/${probe_object}"
      return
    fi
    curl -fsS \
      -X DELETE \
      -H "Authorization: Bearer ${access_token}" \
      "https://storage.googleapis.com/storage/v1/b/${bucket}/o/${probe_object_encoded}" >/dev/null 2>&1 || true
  fi
  rm -f "$probe_file" "$probe_read_file"

  info "bucket object read/write probe failed; attempting bucket metadata/create bootstrap"
  if curl -fsS \
    -H "Authorization: Bearer ${access_token}" \
    "https://storage.googleapis.com/storage/v1/b/${bucket}" >/dev/null 2>&1; then
    info "bucket exists: gs://${bucket}"
    use_local_management_ai_attachment_store "object probe failed for gs://${bucket}"
    return
  else
    local create_payload
    create_payload="$(
      printf '{"name":"%s","location":"%s","iamConfiguration":{"uniformBucketLevelAccess":{"enabled":true}}}' \
        "$bucket" "$location_upper"
    )"
    if curl -fsS \
      -X POST \
      -H "Authorization: Bearer ${access_token}" \
      -H "Content-Type: application/json" \
      "https://storage.googleapis.com/storage/v1/b?project=${project}" \
      -d "$create_payload" >/dev/null; then
      info "bucket created: gs://${bucket}"
    else
      use_local_management_ai_attachment_store "metadata/create bootstrap failed for gs://${bucket}"
      return
    fi
  fi
}

ensure_dev_management_ai_postgres_role() {
  if [[ "${PANTHEON_DEPLOY_ENV}" != "dev" ]]; then
    return
  fi
  if [[ "${MANAGEMENT_AI_STORE_BACKEND:-}" != "postgres" ]]; then
    info "Management AI postgres bootstrap skipped: backend=${MANAGEMENT_AI_STORE_BACKEND:-}"
    return
  fi

  local mgmt_user="${PANTHEON_MANAGEMENT_AI_DB_USER:-pantheon_management_ai}"
  local mgmt_pass="${PANTHEON_MANAGEMENT_AI_DB_PASSWORD:-pantheon_management_ai_dev}"
  local mgmt_db="${PANTHEON_MANAGEMENT_AI_DB_NAME:-pantheon}"
  local mgmt_schema="${MANAGEMENT_AI_STORE_SCHEMA:-management_ai}"
  local app_user="${PANTHEON_MANAGEMENT_AI_APP_DB_USER:-${PANTHEON_APP_DB_USER:-pantheon_app}}"

  info "ensuring Management AI postgres owner role/schema: user=${mgmt_user} schema=${mgmt_schema} app_user=${app_user}"
  COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-}" \
    docker compose -p pantheon -f docker-compose.yml up -d postgres

  local i
  for ((i = 1; i <= 30; i++)); do
    if docker compose -p pantheon -f docker-compose.yml exec -T postgres \
      pg_isready -U "${POSTGRES_USER:-postgres}" -d "${mgmt_db}" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done

  docker compose -p pantheon -f docker-compose.yml exec -T \
    -e MGMT_AI_DB_USER="${mgmt_user}" \
    -e MGMT_AI_DB_PASSWORD="${mgmt_pass}" \
    -e MGMT_AI_DB_NAME="${mgmt_db}" \
    -e MGMT_AI_SCHEMA="${mgmt_schema}" \
    -e MGMT_AI_APP_USER="${app_user}" \
    postgres sh -s <<'REMOTE_DB'
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "${POSTGRES_USER:-postgres}" \
  --dbname "${MGMT_AI_DB_NAME}" \
  -v mgmt_user="${MGMT_AI_DB_USER}" \
  -v mgmt_pass="${MGMT_AI_DB_PASSWORD}" \
  -v mgmt_db="${MGMT_AI_DB_NAME}" \
  -v mgmt_schema="${MGMT_AI_SCHEMA}" \
  -v app_user="${MGMT_AI_APP_USER}" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'mgmt_user', :'mgmt_pass')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'mgmt_user')
\gexec

ALTER ROLE :"mgmt_user" LOGIN PASSWORD :'mgmt_pass';
GRANT CONNECT ON DATABASE :"mgmt_db" TO :"mgmt_user";
GRANT CREATE ON DATABASE :"mgmt_db" TO :"mgmt_user";
CREATE SCHEMA IF NOT EXISTS :"mgmt_schema" AUTHORIZATION :"mgmt_user";
ALTER SCHEMA :"mgmt_schema" OWNER TO :"mgmt_user";
GRANT USAGE, CREATE ON SCHEMA :"mgmt_schema" TO :"mgmt_user";
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA :"mgmt_schema" TO :"mgmt_user";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA :"mgmt_schema" TO :"mgmt_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"mgmt_user" IN SCHEMA :"mgmt_schema" GRANT ALL PRIVILEGES ON TABLES TO :"mgmt_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"mgmt_user" IN SCHEMA :"mgmt_schema" GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO :"mgmt_user";

SELECT set_config('pantheon.mgmt_ai_schema', :'mgmt_schema', false);
SELECT set_config('pantheon.mgmt_ai_owner', :'mgmt_user', false);
SELECT set_config('pantheon.mgmt_ai_app_user', :'app_user', false);

DO $repair$
DECLARE
  mgmt_schema text := current_setting('pantheon.mgmt_ai_schema');
  owner_user text := current_setting('pantheon.mgmt_ai_owner');
  app_user text := current_setting('pantheon.mgmt_ai_app_user');
  item record;
BEGIN
  FOR item IN
    SELECT format('%I.%I', n.nspname, c.relname) AS qualified_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = mgmt_schema
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  LOOP
    EXECUTE format('ALTER TABLE %s OWNER TO %I', item.qualified_name, owner_user);
  END LOOP;

  FOR item IN
    SELECT format('%I.%I', n.nspname, c.relname) AS qualified_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = mgmt_schema
      AND c.relkind = 'S'
  LOOP
    EXECUTE format('ALTER SEQUENCE %s OWNER TO %I', item.qualified_name, owner_user);
  END LOOP;

  IF app_user <> owner_user AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_user) THEN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', mgmt_schema, app_user);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO %I', mgmt_schema, app_user);
    EXECUTE format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA %I TO %I', mgmt_schema, app_user);
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
      owner_user,
      mgmt_schema,
      app_user
    );
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
      owner_user,
      mgmt_schema,
      app_user
    );
  END IF;
END
$repair$;
SQL
REMOTE_DB
}

prune_dev_management_ai_telemetry_for_disk() {
  if [[ "${PANTHEON_DEPLOY_ENV}" != "dev" || "${PANTHEON_DEPLOY_COMPONENT}" != "root" ]]; then
    return
  fi
  if [[ "${MANAGEMENT_AI_STORE_BACKEND:-}" != "postgres" ]]; then
    info "Management AI telemetry prune skipped: backend=${MANAGEMENT_AI_STORE_BACKEND:-}"
    return
  fi
  if [[ "${PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE:-true}" != "true" ]]; then
    info "dev Postgres telemetry prune disabled before root deploy"
    return
  fi

  local mgmt_db="${PANTHEON_MANAGEMENT_AI_DB_NAME:-pantheon}"
  local mgmt_schema="${MANAGEMENT_AI_STORE_SCHEMA:-management_ai}"

  info "pruning dev Postgres telemetry_events before root deploy: db=${mgmt_db} schema=${mgmt_schema}"
  COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-}" \
    docker compose -p pantheon -f docker-compose.yml up -d postgres

  local i
  for ((i = 1; i <= 30; i++)); do
    if docker compose -p pantheon -f docker-compose.yml exec -T postgres \
      pg_isready -U "${POSTGRES_USER:-postgres}" -d "${mgmt_db}" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done

  docker compose -p pantheon -f docker-compose.yml exec -T \
    -e MGMT_AI_DB_NAME="${mgmt_db}" \
    -e MGMT_AI_SCHEMA="${mgmt_schema}" \
    postgres sh -s <<'REMOTE_DB'
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "${POSTGRES_USER:-postgres}" \
  --dbname "${MGMT_AI_DB_NAME}" \
  -v mgmt_schema="${MGMT_AI_SCHEMA}" <<'SQL'
SELECT n.nspname AS schema,
       c.relname AS table,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname = 'telemetry_events'
  AND c.relkind IN ('r', 'p')
ORDER BY pg_total_relation_size(c.oid) DESC;

SELECT set_config('pantheon.mgmt_ai_schema', :'mgmt_schema', false);

DO $prune$
DECLARE
  item record;
  target_schema text := current_setting('pantheon.mgmt_ai_schema');
BEGIN
  FOR item IN
    SELECT n.nspname AS schema_name, c.relname AS table_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = 'telemetry_events'
      AND c.relkind IN ('r', 'p')
      AND n.nspname IN (target_schema, 'public')
  LOOP
    RAISE NOTICE 'truncating %.%', item.schema_name, item.table_name;
    EXECUTE format('TRUNCATE TABLE %I.%I', item.schema_name, item.table_name);
  END LOOP;
END
$prune$;

VACUUM;

SELECT n.nspname AS schema,
       c.relname AS table,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname = 'telemetry_events'
  AND c.relkind IN ('r', 'p')
ORDER BY pg_total_relation_size(c.oid) DESC;
SQL
REMOTE_DB
  docker_storage_diagnostics "after dev Postgres telemetry prune"
}

dump_dev_root_failure_diagnostics() {
  local source_ingest_container_id=""
  local search_container_id=""

  info "dev root compose ps after failure"
  docker compose -p pantheon -f docker-compose.yml ps || true
  info "source-ingest service logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=240 source-ingest || true
  source_ingest_container_id="$(
    docker compose -p pantheon -f docker-compose.yml ps -a -q source-ingest 2>/dev/null || true
  )"
  if [[ -n "$source_ingest_container_id" ]]; then
    info "source-ingest container restart and health state after failure"
    docker inspect --format \
      'status={{.State.Status}} restart_count={{.RestartCount}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}not_configured{{end}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}} error={{json .State.Error}}' \
      "$source_ingest_container_id" || true
  fi
  info "search-svc service logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=240 search-svc || true
  search_container_id="$(
    docker compose -p pantheon -f docker-compose.yml ps -a -q search-svc 2>/dev/null || true
  )"
  if [[ -n "$search_container_id" ]]; then
    info "search-svc container restart and health state after failure"
    docker inspect --format \
      'status={{.State.Status}} restart_count={{.RestartCount}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}not_configured{{end}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}} error={{json .State.Error}}' \
      "$search_container_id" || true
  fi
  info "evolution daily sweep scheduler logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=120 evolution-daily-sweep-scheduler || true
  info "operator-bff logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=240 operator-bff || true
  info "postgres logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=120 postgres || true
  info "loop-run-projector-scheduler logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=120 loop-run-projector-scheduler || true
  info "source-ingest-scheduler logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=120 source-ingest-scheduler || true
}

verify_dev_evolution_daily_sweep() {
  local compose=(docker compose -p pantheon -f docker-compose.yml)
  local attempt
  local logs=""
  local status=""

  for attempt in $(seq 1 30); do
    logs="$("${compose[@]}" logs --no-color --since=10m evolution-daily-sweep-scheduler 2>&1 || true)"
    if printf '%s\n' "$logs" | grep -Fq '"tick":'; then
      status="$(curl -fsS http://127.0.0.1:18093/api/evolution/sweep-status 2>/dev/null || true)"
      if python3 -c '
import json
import sys

payload = json.loads(sys.argv[1])
assert payload.get("last_success_at")
assert int(payload.get("total_sweeps_run") or 0) >= 1
' "$status" 2>/dev/null; then
        info "evolution daily sweep scheduler emitted a successful tick"
        printf '%s\n' "$logs"
        info "evolution daily sweep status"
        printf '%s\n' "$status"
        return 0
      fi
    fi
    sleep 2
  done

  info "evolution daily sweep scheduler did not emit a successful tick"
  "${compose[@]}" ps -a evolution evolution-daily-sweep-scheduler || true
  printf '%s\n' "$logs"
  printf '%s\n' "$status"
  return 1
}

docker_storage_diagnostics() {
  local label="$1"

  info "docker storage diagnostics (${label}): filesystem usage"
  df -h . /var/lib/docker /var/lib/containerd 2>/dev/null || df -h . || true
  info "docker storage diagnostics (${label}): docker system df"
  docker system df || true
}

prune_dev_docker_storage_for_build() {
  if [[ "${PANTHEON_DEPLOY_ENV}" != "dev" || "${PANTHEON_DEPLOY_COMPONENT}" != "root" ]]; then
    return
  fi

  if [[ "${PANTHEON_DEV_DOCKER_PRUNE:-true}" != "true" ]]; then
    info "dev Docker prune disabled before root build"
    docker_storage_diagnostics "before build"
    return
  fi

  docker_storage_diagnostics "before prune"
  info "pruning dev Docker build cache and unused containers/images before root build"
  docker builder prune -af || true
  docker container prune -f || true
  docker image prune -af || true
  docker system prune -f || true
  docker_storage_diagnostics "after prune"
}

cd "${PANTHEON_REMOTE_DIR}"
git rev-parse --is-inside-work-tree >/dev/null

case "${PANTHEON_DEPLOY_COMPONENT}" in
  root)
    snapshot_remote_state pantheon docker-compose.yml
    prepare_deploy_worktree
    # Dev deploys activate every documented compose profile. Each profile is
    # either a long-running daemon, an init container, or a one-shot smoke
    # whose Dockerfile + smoke script have been verified to build and pass
    # locally with stub backends (no real-money / no real-broker side effects).
    #
    # Profile inventory (alphabetical):
    #   activation-ready-smoke       oss-activation-ready-smoke-matrix
    #   dormant-smoke                experiments/finrl/qlib/rllib/ray-tune/trl
    #   openclaw                     openclaw-gateway + openclaw-data-init
    #   openclaw-activation-ready-e2e  openclaw-activation-ready-e2e
    #   search-index-scheduler       search-index-scheduler
    #   smoke                        smoke-stack (depends on full service set)
    #   source-ingest-scheduler      source-ingest-scheduler
    #   source-search-bounded        source-search-bounded-smoke
    #
    # Operators can narrow scope via PANTHEON_DEV_COMPOSE_PROFILES.
    PANTHEON_DEV_COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-activation-ready-smoke,dormant-smoke,openclaw,openclaw-activation-ready-e2e,search-index-scheduler,smoke,source-ingest-scheduler,source-search-bounded}"
    ensure_dev_management_ai_bucket
    ensure_dev_management_ai_postgres_role
    prune_dev_management_ai_telemetry_for_disk
    COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES}" \
      docker compose -p pantheon -f docker-compose.yml config --quiet
    prune_dev_docker_storage_for_build
    COMPOSE_BAKE=false \
    COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES}" \
    GIT_SHA="${PANTHEON_DEPLOY_SHA}" \
    BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    PANTHEON_ENV=dev \
    PANTHEON_LIVE_BROKER_ENABLED=false \
    BROKER_PAPER_ENABLED=true \
    AGORA_WORKSHOP_STORE_BACKEND=postgres \
    AGORA_WORKSHOP_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_WORKSHOP_STORE_SCHEMA=agora \
    AGORA_GOVERNANCE_STORE_BACKEND=postgres \
    AGORA_GOVERNANCE_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_GOVERNANCE_STORE_SCHEMA=agora \
    AGORA_RESEARCH_STORE_BACKEND=postgres \
    AGORA_RESEARCH_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_RESEARCH_STORE_SCHEMA=agora_research \
    AGORA_TRADING_ROOM_STORE_BACKEND=postgres \
    AGORA_TRADING_ROOM_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_TRADING_ROOM_STORE_SCHEMA=agora \
    PANTHEON_BFF_CORS_ORIGINS="${PANTHEON_DEV_BFF_CORS_ORIGINS}" \
    PANTHEON_BFF_AUTH_STUB="${PANTHEON_DEV_BFF_AUTH_STUB}" \
    PANTHEON_BFF_AUTH_MODE="${PANTHEON_DEV_BFF_AUTH_MODE}" \
    PANTHEON_BFF_JWT_SECRET="${PANTHEON_DEV_BFF_JWT_SECRET}" \
    PANTHEON_BFF_JWT_ISSUER="${PANTHEON_DEV_BFF_JWT_ISSUER}" \
    PANTHEON_BFF_JWT_AUDIENCE="${PANTHEON_DEV_BFF_JWT_AUDIENCE}" \
    PANTHEON_BFF_JWKS_URI="${PANTHEON_DEV_BFF_JWKS_URI}" \
    PANTHEON_BFF_OIDC_DISCOVERY_URL="${PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL}" \
    PANTHEON_BFF_OIDC_ISSUER="${PANTHEON_DEV_BFF_OIDC_ISSUER}" \
    PANTHEON_BFF_OIDC_AUDIENCE="${PANTHEON_DEV_BFF_OIDC_AUDIENCE}" \
    PANTHEON_BFF_OIDC_CLIENT_ID="${PANTHEON_DEV_BFF_OIDC_CLIENT_ID}" \
    PANTHEON_BFF_OIDC_CLIENT_SECRET="${PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET}" \
    PANTHEON_BFF_ROLE_CLAIMS="${PANTHEON_DEV_BFF_ROLE_CLAIMS}" \
    PANTHEON_BFF_ROLE_MAP="${PANTHEON_DEV_BFF_ROLE_MAP}" \
    PANTHEON_BFF_ROLE_MAP_MODE="${PANTHEON_DEV_BFF_ROLE_MAP_MODE}" \
    PANTHEON_BFF_DEFAULT_ROLE="${PANTHEON_DEV_BFF_DEFAULT_ROLE}" \
    PANTHEON_BFF_TENANT_ID="${PANTHEON_DEV_BFF_TENANT_ID}" \
    PANTHEON_BFF_ALLOWED_TENANTS="${PANTHEON_DEV_BFF_ALLOWED_TENANTS}" \
    PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED}" \
    PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH}" \
    PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS}" \
    PANTHEON_ASSISTANT_REPAIR_REPO_URL="${PANTHEON_ASSISTANT_REPAIR_REPO_URL}" \
    PANTHEON_ASSISTANT_REPAIR_REMOTE_URL="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL}" \
    PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS}" \
    PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS}" \
    PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES}" \
    PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN}" \
    PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED}" \
    PANTHEON_STATUS_ROOT_HOST="${PANTHEON_STATUS_ROOT_HOST}" \
    PANTHEON_STATUS_ROOT_CONTAINER="${PANTHEON_STATUS_ROOT_CONTAINER}" \
      docker compose -p pantheon -f docker-compose.yml up -d --build \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    curl_with_retry http://127.0.0.1:18001/health \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    curl_with_retry http://127.0.0.1:18001/readyz \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    assert_bff_source_sha http://127.0.0.1:18001/bff/version \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    assert_bff_auth_gate http://127.0.0.1:18001 \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    ensure_dev_caddy_ingress \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    verify_dev_evolution_daily_sweep \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    # Prove the Trade Journey action ledger is genuinely durable on the dev
    # PostgreSQL instance and that clock-drift diagnostics survive the built
    # runtime image. This intentionally restarts operator-bff and verifies
    # receipt replay before the workflow's public smokes run.
    PANTHEON_DEV_REPO="$(pwd)" bash scripts/verify_trade_journey_residual_dev.sh \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    ;;

  bff)
    # Rebuild and restart only operator-bff.  All other compose services —
    # including the paper fleet and runtime-manager — are left running.
    # Use this component when deploying a BFF-only fix to avoid the OOM
    # pressure that a full root-stack rebuild causes on the dev VM.
    snapshot_remote_state pantheon docker-compose.yml
    prepare_deploy_worktree
    COMPOSE_BAKE=false \
    COMPOSE_PROFILES="" \
    GIT_SHA="${PANTHEON_DEPLOY_SHA}" \
    BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    PANTHEON_ENV=dev \
    PANTHEON_LIVE_BROKER_ENABLED=false \
    BROKER_PAPER_ENABLED=true \
    AGORA_WORKSHOP_STORE_BACKEND=postgres \
    AGORA_WORKSHOP_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_WORKSHOP_STORE_SCHEMA=agora \
    AGORA_GOVERNANCE_STORE_BACKEND=postgres \
    AGORA_GOVERNANCE_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_GOVERNANCE_STORE_SCHEMA=agora \
    AGORA_RESEARCH_STORE_BACKEND=postgres \
    AGORA_RESEARCH_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_RESEARCH_STORE_SCHEMA=agora_research \
    AGORA_TRADING_ROOM_STORE_BACKEND=postgres \
    AGORA_TRADING_ROOM_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_TRADING_ROOM_STORE_SCHEMA=agora \
    PANTHEON_BFF_CORS_ORIGINS="${PANTHEON_DEV_BFF_CORS_ORIGINS}" \
    PANTHEON_BFF_AUTH_STUB="${PANTHEON_DEV_BFF_AUTH_STUB}" \
    PANTHEON_BFF_AUTH_MODE="${PANTHEON_DEV_BFF_AUTH_MODE}" \
    PANTHEON_BFF_JWT_SECRET="${PANTHEON_DEV_BFF_JWT_SECRET}" \
    PANTHEON_BFF_JWT_ISSUER="${PANTHEON_DEV_BFF_JWT_ISSUER}" \
    PANTHEON_BFF_JWT_AUDIENCE="${PANTHEON_DEV_BFF_JWT_AUDIENCE}" \
    PANTHEON_BFF_JWKS_URI="${PANTHEON_DEV_BFF_JWKS_URI}" \
    PANTHEON_BFF_OIDC_DISCOVERY_URL="${PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL}" \
    PANTHEON_BFF_OIDC_ISSUER="${PANTHEON_DEV_BFF_OIDC_ISSUER}" \
    PANTHEON_BFF_OIDC_AUDIENCE="${PANTHEON_DEV_BFF_OIDC_AUDIENCE}" \
    PANTHEON_BFF_OIDC_CLIENT_ID="${PANTHEON_DEV_BFF_OIDC_CLIENT_ID}" \
    PANTHEON_BFF_OIDC_CLIENT_SECRET="${PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET}" \
    PANTHEON_BFF_ROLE_CLAIMS="${PANTHEON_DEV_BFF_ROLE_CLAIMS}" \
    PANTHEON_BFF_ROLE_MAP="${PANTHEON_DEV_BFF_ROLE_MAP}" \
    PANTHEON_BFF_ROLE_MAP_MODE="${PANTHEON_DEV_BFF_ROLE_MAP_MODE}" \
    PANTHEON_BFF_DEFAULT_ROLE="${PANTHEON_DEV_BFF_DEFAULT_ROLE}" \
    PANTHEON_BFF_TENANT_ID="${PANTHEON_DEV_BFF_TENANT_ID}" \
    PANTHEON_BFF_ALLOWED_TENANTS="${PANTHEON_DEV_BFF_ALLOWED_TENANTS}" \
    PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED}" \
    PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH}" \
    PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS}" \
    PANTHEON_ASSISTANT_REPAIR_REPO_URL="${PANTHEON_ASSISTANT_REPAIR_REPO_URL}" \
    PANTHEON_ASSISTANT_REPAIR_REMOTE_URL="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL}" \
    PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS}" \
    PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS}" \
    PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES}" \
    PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN}" \
    PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED}" \
    PANTHEON_STATUS_ROOT_HOST="${PANTHEON_STATUS_ROOT_HOST}" \
    PANTHEON_STATUS_ROOT_CONTAINER="${PANTHEON_STATUS_ROOT_CONTAINER}" \
    MANAGEMENT_AI_STORE_BACKEND="${MANAGEMENT_AI_STORE_BACKEND}" \
    MANAGEMENT_AI_STORE_SCHEMA="${MANAGEMENT_AI_STORE_SCHEMA}" \
    MANAGEMENT_AI_DATABASE_URL="${MANAGEMENT_AI_DATABASE_URL}" \
    PANTHEON_MGMT_AI_ATTACH_BUCKET="${PANTHEON_MGMT_AI_ATTACH_BUCKET}" \
    PANTHEON_MGMT_AI_ATTACH_LOCATION="${PANTHEON_MGMT_AI_ATTACH_LOCATION:-asia-east1}" \
      docker compose -p pantheon -f docker-compose.yml up -d --build --no-deps operator-bff \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    curl_with_retry http://127.0.0.1:18001/health \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    curl_with_retry http://127.0.0.1:18001/readyz \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    assert_bff_source_sha http://127.0.0.1:18001/bff/version \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    assert_bff_auth_gate http://127.0.0.1:18001 \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    ensure_dev_caddy_ingress \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    ;;

  exec)
    snapshot_remote_state pantheon-exec docker-compose.exec.yml
    prepare_deploy_worktree
    env_file="$(real_env_or_example env/prod-exec.env env/prod-exec.env.example)"
    docker compose --env-file "$env_file" -p pantheon-exec -f docker-compose.exec.yml config --quiet
    COMPOSE_BAKE=false GIT_SHA="${PANTHEON_DEPLOY_SHA}" \
      docker compose --env-file "$env_file" -p pantheon-exec -f docker-compose.exec.yml up -d --build
    curl_with_retry http://127.0.0.1:28081/__health__
    curl_with_retry http://127.0.0.1:28097/__health__
    curl_with_retry http://127.0.0.1:28098/__health__
    # Paper-runtime readiness requires a RuntimeBinding; master auto-deploy
    # only proves the execution substrate is live before control-plane binding.
    curl_with_retry http://127.0.0.1:28110/livez
    ;;

  control)
    snapshot_remote_state pantheon-control docker-compose.control.yml
    prepare_deploy_worktree
    env_file="$(real_env_or_example env/prod-control.env env/prod-control.env.example)"
    docker compose --env-file "$env_file" -p pantheon-control -f docker-compose.control.yml config --quiet
    COMPOSE_BAKE=false \
    GIT_SHA="${PANTHEON_DEPLOY_SHA}" \
    PANTHEON_ENV=staging-live \
    PANTHEON_LIVE_BROKER_ENABLED=true \
    PANTHEON_BFF_CORS_ORIGINS="${PANTHEON_STAGING_BFF_CORS_ORIGINS}" \
      docker compose --env-file "$env_file" -p pantheon-control -f docker-compose.control.yml up -d --build
    curl_with_retry http://127.0.0.1:38001/health
    assert_bff_source_sha http://127.0.0.1:38001/bff/version
    curl_with_retry "${PANTHEON_STAGING_EXEC_HEALTH_URL%/}/__health__"
    ;;

  *)
    error "unsupported remote component: ${PANTHEON_DEPLOY_COMPONENT}"
    ;;
esac

info "component ${PANTHEON_DEPLOY_COMPONENT} deployed"
REMOTE
}

deploy_dev_root() {
  ssh_bash "$DEV_VM" "$DEV_ZONE" "$DEV_REMOTE_DIR" root
}

deploy_dev_bff() {
  ssh_bash "$DEV_VM" "$DEV_ZONE" "$DEV_REMOTE_DIR" bff
}

deploy_staging_exec() {
  ssh_bash "$STAGING_EXEC_VM" "$STAGING_EXEC_ZONE" "$STAGING_EXEC_REMOTE_DIR" exec
}

deploy_staging_control() {
  ssh_bash "$STAGING_CONTROL_VM" "$STAGING_CONTROL_ZONE" "$STAGING_CONTROL_REMOTE_DIR" control
}

case "${DEPLOY_ENV}:${COMPONENT}" in
  dev:root)
    deploy_dev_root
    ;;
  dev:bff)
    deploy_dev_bff
    ;;
  staging-live:exec)
    deploy_staging_exec
    ;;
  staging-live:control)
    deploy_staging_control
    ;;
  staging-live:all)
    deploy_staging_exec
    deploy_staging_control
    ;;
  *)
    error "unsupported deployment target ${DEPLOY_ENV}:${COMPONENT}"
    ;;
esac

info "deployment complete: ${DEPLOY_ENV}/${COMPONENT} ${DEPLOY_SHA}"
