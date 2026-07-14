#!/usr/bin/env bash
# Deploy Pantheon non-prod VM compose stacks from a verified git commit.
#
# This script is designed for GitHub Actions, but it can also be run by an
# operator from a workstation with gcloud access. The VM's human-facing checkout
# is used only as the git object source and snapshot target; deployment runs from
# a managed clean worktree under ~/pantheon-ci-deploy.

set -euo pipefail

# GitHub Actions and operator shells may export these values. Keep them
# available to this Bash process, but remove them from the inherited
# environment before even path-resolution helpers are invoked.
export -n GITHUB_TOKEN DEV_BFF_JWT_SECRET DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON \
  DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH DEV_MANAGEMENT_AI_DB_PASSWORD \
  DEV_MANAGEMENT_AI_DATABASE_URL PANTHEON_BFF_JWT_SECRET \
  PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON \
  PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH \
  PANTHEON_MANAGEMENT_AI_DB_PASSWORD MANAGEMENT_AI_STORE_DSN \
  MANAGEMENT_AI_DATABASE_URL 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PROJECT_ID="${PROJECT_ID:-pantheon-benjamin-20260528}"
REMOTE_USER="${REMOTE_USER:-lupin}"

DEV_VM="${DEV_VM:-pantheon-lupin-dev}"
DEV_ZONE="${DEV_ZONE:-asia-east1-b}"
DEV_REMOTE_DIR="${DEV_REMOTE_DIR:-/home/lupin/code/pantheon}"
DEV_BFF_CANONICAL_CORS_ORIGIN="${DEV_BFF_CANONICAL_CORS_ORIGIN:-https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io}"
DEV_BFF_CORS_ORIGINS="${DEV_BFF_CORS_ORIGINS:-${DEV_BFF_CANONICAL_CORS_ORIGIN},https://pantheon-ai-system-front-dev.lovable.app,https://pantheon-dev.lovable.app}"
DEV_BFF_REQUIRED_CORS_ORIGINS="${DEV_BFF_REQUIRED_CORS_ORIGINS:-https://preview--pantheon-dev.lovable.app,https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com,https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app,https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com}"
DEV_BFF_AUTH_STUB="${DEV_BFF_AUTH_STUB:-false}"
DEV_BFF_AUTH_MODE="${DEV_BFF_AUTH_MODE:-strict}"
DEV_BFF_JWT_SECRET="${DEV_BFF_JWT_SECRET:-}"
DEV_BFF_JWT_ISSUER="${DEV_BFF_JWT_ISSUER:-pantheon-dev}"
DEV_BFF_JWT_AUDIENCE="${DEV_BFF_JWT_AUDIENCE:-bff-operators}"
DEV_BFF_JWKS_URI="${DEV_BFF_JWKS_URI-}"
DEV_BFF_OIDC_DISCOVERY_URL="${DEV_BFF_OIDC_DISCOVERY_URL-}"
DEV_BFF_OIDC_ISSUER="${DEV_BFF_OIDC_ISSUER-}"
DEV_BFF_OIDC_AUDIENCE="${DEV_BFF_OIDC_AUDIENCE-}"
DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON="${DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON:-}"
DEV_BFF_DEV_LOGIN_TTL_SECONDS="${DEV_BFF_DEV_LOGIN_TTL_SECONDS:-900}"
# Retired shared dev-login variables must neither configure the BFF nor leak to
# cloud/SSH child processes when inherited from an operator shell.
unset DEV_BFF_OIDC_CLIENT_ID DEV_BFF_OIDC_CLIENT_SECRET
DEV_BFF_TENANT_ID="${DEV_BFF_TENANT_ID:-tenant-dev}"
DEV_BFF_ALLOWED_TENANTS="${DEV_BFF_ALLOWED_TENANTS:-${DEV_BFF_TENANT_ID},pantheon-dev}"
DEV_BFF_ROLE_CLAIMS="${DEV_BFF_ROLE_CLAIMS:-roles,role}"
DEV_BFF_ROLE_MAP="${DEV_BFF_ROLE_MAP-}"
DEV_BFF_ROLE_MAP_MODE="${DEV_BFF_ROLE_MAP_MODE:-passthrough}"
DEV_BFF_MFA_REQUIRED="${DEV_BFF_MFA_REQUIRED:-false}"
DEV_BFF_MFA_CLAIMS="${DEV_BFF_MFA_CLAIMS:-amr,acr,mfa,mfa_verified}"
DEV_BFF_MFA_VALUES="${DEV_BFF_MFA_VALUES:-true,1,yes,mfa,otp,totp,webauthn}"
DEV_ASSISTANT_KERNEL_ENABLED="${DEV_ASSISTANT_KERNEL_ENABLED:-true}"
DEV_ASSISTANT_CONTROL_MODE_STORE_PATH="${DEV_ASSISTANT_CONTROL_MODE_STORE_PATH:-/data/bff/assistant-control-mode.json}"
DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-300}"
DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH="${DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH-}"
DEV_ASSISTANT_REPAIR_REPO_URL="${DEV_ASSISTANT_REPAIR_REPO_URL:-/workspace/status-root}"
DEV_ASSISTANT_REPAIR_REMOTE_URL="${DEV_ASSISTANT_REPAIR_REMOTE_URL:-https://github.com/ajoe734/pantheon.git}"
DEV_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS="${DEV_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS:-https://github.com/ajoe734/execute-plans.git}"
DEV_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS="${DEV_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS:-https://github.com/ajoe734/execute-plans.git}"
DEV_BFF_STUB_CAPABILITIES="${DEV_BFF_STUB_CAPABILITIES-}"
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
  --project-id <id>      GCP project. Default: pantheon-benjamin-20260528.
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
  DEV_BFF_CANONICAL_CORS_ORIGIN DEV_BFF_CORS_ORIGINS
  DEV_BFF_REQUIRED_CORS_ORIGINS DEV_BFF_AUTH_STUB DEV_BFF_AUTH_MODE
  DEV_BFF_JWT_SECRET DEV_BFF_JWT_ISSUER DEV_BFF_JWT_AUDIENCE
  DEV_BFF_JWKS_URI DEV_BFF_OIDC_DISCOVERY_URL
  DEV_BFF_OIDC_ISSUER DEV_BFF_OIDC_AUDIENCE
  DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON
  DEV_BFF_DEV_LOGIN_TTL_SECONDS
  DEV_BFF_TENANT_ID DEV_BFF_ALLOWED_TENANTS
  DEV_BFF_ROLE_CLAIMS DEV_BFF_ROLE_MAP DEV_BFF_ROLE_MAP_MODE
  DEV_BFF_MFA_REQUIRED DEV_BFF_MFA_CLAIMS DEV_BFF_MFA_VALUES
  DEV_ASSISTANT_KERNEL_ENABLED DEV_ASSISTANT_CONTROL_MODE_STORE_PATH
  DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH
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

emit_remote_export() {
  local name="$1"
  local value="$2"
  [[ "$name" =~ ^[A-Z][A-Z0-9_]*$ ]] || error "invalid remote environment name"
  printf 'export %s=%q\n' "$name" "$value"
}

emit_remote_assignment() {
  local name="$1"
  local value="$2"
  [[ "$name" =~ ^[A-Z][A-Z0-9_]*$ ]] || error "invalid remote environment name"
  # Sensitive streamed values stay as shell-local variables.  The remote
  # script persists them to mode-0600 files before invoking any helper child.
  printf '%s=%q\n' "$name" "$value"
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
  PANTHEON_STATUS_ROOT_CONTAINER="${PANTHEON_STATUS_ROOT_CONTAINER:-$DEV_STATUS_ROOT_CONTAINER}"
  PANTHEON_STATUS_ROOT_HOST="${PANTHEON_STATUS_ROOT_HOST:-${DEV_STATUS_ROOT_HOST:-$DEV_REMOTE_DIR}}"
}

validate_dev_bff_auth_boundary() {
  if [[ "$DEPLOY_ENV" != "dev" ]]; then
    return
  fi

  [[ "$DEV_BFF_AUTH_STUB" == "false" ]] \
    || error "dev deploy requires DEV_BFF_AUTH_STUB=false"
  [[ "$DEV_BFF_AUTH_MODE" == "strict" ]] \
    || error "dev deploy requires DEV_BFF_AUTH_MODE=strict"
  [[ -z "$DEV_BFF_STUB_CAPABILITIES" ]] \
    || error "dev deploy requires DEV_BFF_STUB_CAPABILITIES to be empty"
  [[ -z "${PANTHEON_BFF_STUB_CAPABILITIES:-}" ]] \
    || error "dev deploy requires PANTHEON_BFF_STUB_CAPABILITIES to be empty"

  local credential_name
  local credential_value
  for credential_name in DEV_BFF_JWT_SECRET DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON; do
    credential_value="${!credential_name}"
    if [[ -n "$credential_value" && ! "$credential_value" =~ [^[:space:]] ]]; then
      error "${credential_name} must not be whitespace-only"
    fi
  done

  if [[ -n "$DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON" || -n "$DEV_BFF_JWT_SECRET" ]]; then
    [[ -n "$DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON" && -n "$DEV_BFF_JWT_SECRET" ]] \
      || error "dev deploy requires both DEV_BFF_JWT_SECRET and governed client profiles"
    command -v python3 >/dev/null 2>&1 || error "python3 is required for canonical dev-auth validation"
    local auth_validation_dir profiles_file jwt_secret_file
    auth_validation_dir="$(mktemp -d)"
    chmod 0700 "${auth_validation_dir}"
    profiles_file="${auth_validation_dir}/profiles.json"
    jwt_secret_file="${auth_validation_dir}/jwt-secret"
    (umask 077; printf '%s' "$DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON" >"${profiles_file}")
    (umask 077; printf '%s' "$DEV_BFF_JWT_SECRET" >"${jwt_secret_file}")
    export -n DEV_BFF_JWT_SECRET DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON 2>/dev/null || true
    if ! python3 "${REPO_ROOT}/services/control-plane/bff/dev_auth_validation.py" profiles \
      --profiles-file "${profiles_file}" \
      --jwt-secret-file "${jwt_secret_file}" >/dev/null; then
      rm -rf "${auth_validation_dir}"
      error "DEV_BFF dev-login configuration failed canonical validation"
    fi
    rm -rf "${auth_validation_dir}"
  fi
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
    # Dev-only credentials must not be inherited by staging subprocesses or
    # forwarded to a staging host, even when the caller exported them.
    unset DEV_BFF_JWT_SECRET DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON
    unset DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH
    unset DEV_MANAGEMENT_AI_DB_PASSWORD DEV_MANAGEMENT_AI_DATABASE_URL
    DEV_BFF_JWT_SECRET=""
    DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON=""
    DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH=""
    DEV_MANAGEMENT_AI_DB_PASSWORD=""
    DEV_MANAGEMENT_AI_DATABASE_URL=""
    ;;
  *)
    error "--environment must be dev or staging-live"
    ;;
esac

configure_management_ai_dev_env
configure_management_ai_dev_kernel_env
validate_dev_bff_auth_boundary

if [[ "$DRY_RUN" == "true" ]]; then
  info "dry run"
  info "project=${PROJECT_ID}"
  info "environment=${DEPLOY_ENV}"
  info "component=${COMPONENT}"
  info "sha=${DEPLOY_SHA}"
  info "allow_dirty=${ALLOW_DIRTY}"
  info "allow_example_env=${ALLOW_EXAMPLE_ENV}"
  info "dev_bff_cors_origins=${DEV_BFF_CORS_ORIGINS}"
  info "dev_bff_auth_stub=${DEV_BFF_AUTH_STUB}"
  info "dev_bff_auth_mode=${DEV_BFF_AUTH_MODE}"
  info "dev_bff_dev_login_configured=$([[ -n "$DEV_BFF_JWT_SECRET" ]] && echo true || echo false)"
  info "dev_bff_profiled_login_configured=$([[ -n "$DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON" ]] && echo true || echo false)"
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

if [[ "$DEPLOY_ENV" == "dev" ]]; then
  [[ -n "$DEV_BFF_JWT_SECRET" && -n "$DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON" ]] \
    || error "dev deployment is blocked until JWT secret and governed login profiles are configured"
fi

# Keep deploy credentials in this shell only. Remote delivery uses the SSH
# stdin stream below; gcloud never receives them in argv or its environment.
export -n GITHUB_TOKEN DEV_BFF_JWT_SECRET DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON \
  DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH 2>/dev/null || true
unset PANTHEON_BFF_JWT_SECRET PANTHEON_BFF_OIDC_CLIENT_ID PANTHEON_BFF_OIDC_CLIENT_SECRET PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON
unset PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH
export -n DEV_MANAGEMENT_AI_DB_PASSWORD DEV_MANAGEMENT_AI_DATABASE_URL 2>/dev/null || true
export -n PANTHEON_MANAGEMENT_AI_DB_PASSWORD 2>/dev/null || true
export -n MANAGEMENT_AI_STORE_DSN MANAGEMENT_AI_DATABASE_URL 2>/dev/null || true

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
  info "ssh ${vm} (${zone}) component=${remote_component} sha=${DEPLOY_SHA}"
  {
    emit_remote_export PANTHEON_DEPLOY_ENV "$DEPLOY_ENV"
    emit_remote_export PANTHEON_DEPLOY_COMPONENT "$remote_component"
    emit_remote_export PANTHEON_DEPLOY_SHA "$DEPLOY_SHA"
    emit_remote_export PANTHEON_DEPLOY_PROJECT_ID "$PROJECT_ID"
    emit_remote_export PANTHEON_REMOTE_DIR "$remote_dir"
    emit_remote_export PANTHEON_DEPLOY_WORKTREE_ROOT "${PANTHEON_DEPLOY_WORKTREE_ROOT:-}"
    emit_remote_assignment PANTHEON_GITHUB_TOKEN "${GITHUB_TOKEN:-}"
    emit_remote_export PANTHEON_ALLOW_DIRTY_DEPLOY "$ALLOW_DIRTY"
    emit_remote_export PANTHEON_ALLOW_EXAMPLE_ENV "$ALLOW_EXAMPLE_ENV"
    emit_remote_export PANTHEON_DEV_BFF_CORS_ORIGINS "$DEV_BFF_CORS_ORIGINS"
    emit_remote_export PANTHEON_DEV_BFF_AUTH_STUB "$DEV_BFF_AUTH_STUB"
    emit_remote_export PANTHEON_DEV_BFF_AUTH_MODE "$DEV_BFF_AUTH_MODE"
    emit_remote_assignment PANTHEON_DEV_BFF_JWT_SECRET "$DEV_BFF_JWT_SECRET"
    emit_remote_export PANTHEON_DEV_BFF_JWT_ISSUER "$DEV_BFF_JWT_ISSUER"
    emit_remote_export PANTHEON_DEV_BFF_JWT_AUDIENCE "$DEV_BFF_JWT_AUDIENCE"
    emit_remote_export PANTHEON_DEV_BFF_JWKS_URI "$DEV_BFF_JWKS_URI"
    emit_remote_export PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL "$DEV_BFF_OIDC_DISCOVERY_URL"
    emit_remote_export PANTHEON_DEV_BFF_OIDC_ISSUER "$DEV_BFF_OIDC_ISSUER"
    emit_remote_export PANTHEON_DEV_BFF_OIDC_AUDIENCE "$DEV_BFF_OIDC_AUDIENCE"
    emit_remote_assignment PANTHEON_DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON "$DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON"
    emit_remote_export PANTHEON_DEV_BFF_DEV_LOGIN_TTL_SECONDS "$DEV_BFF_DEV_LOGIN_TTL_SECONDS"
    emit_remote_export PANTHEON_DEV_BFF_TENANT_ID "$DEV_BFF_TENANT_ID"
    emit_remote_export PANTHEON_DEV_BFF_ALLOWED_TENANTS "$DEV_BFF_ALLOWED_TENANTS"
    emit_remote_export PANTHEON_DEV_BFF_ROLE_CLAIMS "$DEV_BFF_ROLE_CLAIMS"
    emit_remote_export PANTHEON_DEV_BFF_ROLE_MAP "$DEV_BFF_ROLE_MAP"
    emit_remote_export PANTHEON_DEV_BFF_ROLE_MAP_MODE "$DEV_BFF_ROLE_MAP_MODE"
    emit_remote_export PANTHEON_DEV_BFF_MFA_REQUIRED "$DEV_BFF_MFA_REQUIRED"
    emit_remote_export PANTHEON_DEV_BFF_MFA_CLAIMS "$DEV_BFF_MFA_CLAIMS"
    emit_remote_export PANTHEON_DEV_BFF_MFA_VALUES "$DEV_BFF_MFA_VALUES"
    emit_remote_export PANTHEON_ASSISTANT_KERNEL_ENABLED "${PANTHEON_ASSISTANT_KERNEL_ENABLED:-}"
    emit_remote_export PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH "${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-}"
    emit_remote_export PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS "${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-}"
    emit_remote_assignment PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH "$DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH"
    emit_remote_export PANTHEON_ASSISTANT_REPAIR_REPO_URL "${PANTHEON_ASSISTANT_REPAIR_REPO_URL:-}"
    emit_remote_export PANTHEON_ASSISTANT_REPAIR_REMOTE_URL "${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL:-}"
    emit_remote_export PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS "${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS:-}"
    emit_remote_export PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS "${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS:-}"
    emit_remote_export PANTHEON_BFF_STUB_CAPABILITIES "${PANTHEON_BFF_STUB_CAPABILITIES:-}"
    emit_remote_export PANTHEON_STATUS_ROOT_HOST "${PANTHEON_STATUS_ROOT_HOST:-}"
    emit_remote_export PANTHEON_STATUS_ROOT_CONTAINER "${PANTHEON_STATUS_ROOT_CONTAINER:-}"
    emit_remote_export PANTHEON_DEV_DOCKER_PRUNE "${PANTHEON_DEV_DOCKER_PRUNE:-true}"
    emit_remote_export PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE "${PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE:-true}"
    emit_remote_export MANAGEMENT_AI_STORE_BACKEND "${MANAGEMENT_AI_STORE_BACKEND:-}"
    emit_remote_export MANAGEMENT_AI_STORE_SCHEMA "${MANAGEMENT_AI_STORE_SCHEMA:-}"
    emit_remote_assignment MANAGEMENT_AI_STORE_DSN "${MANAGEMENT_AI_STORE_DSN:-}"
    emit_remote_assignment MANAGEMENT_AI_DATABASE_URL "${MANAGEMENT_AI_DATABASE_URL:-}"
    emit_remote_export PANTHEON_MGMT_AI_ATTACH_BUCKET "${PANTHEON_MGMT_AI_ATTACH_BUCKET:-}"
    emit_remote_export PANTHEON_MGMT_AI_ATTACH_LOCATION "${DEV_MANAGEMENT_AI_ATTACH_LOCATION:-}"
    emit_remote_export PANTHEON_MANAGEMENT_AI_DB_USER "${DEV_MANAGEMENT_AI_DB_USER:-}"
    emit_remote_assignment PANTHEON_MANAGEMENT_AI_DB_PASSWORD "${DEV_MANAGEMENT_AI_DB_PASSWORD:-}"
    emit_remote_export PANTHEON_MANAGEMENT_AI_DB_NAME "${DEV_MANAGEMENT_AI_DB_NAME:-}"
    emit_remote_export PANTHEON_MANAGEMENT_AI_APP_DB_USER "${DEV_APP_DB_USER:-pantheon_app}"
    emit_remote_export PANTHEON_STAGING_EXEC_HEALTH_URL "$STAGING_EXEC_HEALTH_URL"
    emit_remote_export PANTHEON_STAGING_BFF_CORS_ORIGINS "$STAGING_BFF_CORS_ORIGINS"
    cat <<'REMOTE'
set -euo pipefail

# Streamed credentials arrive as non-exported shell assignments. Persist them
# before invoking any helper so unrelated child processes never inherit them.
PANTHEON_REMOTE_SECRET_DIR="$(mktemp -d)"
chmod 0700 "${PANTHEON_REMOTE_SECRET_DIR}"
cleanup_remote_secrets() {
  rm -rf "${PANTHEON_REMOTE_SECRET_DIR}"
}
trap cleanup_remote_secrets EXIT

persist_remote_secret() {
  local variable_name="$1"
  local file_name="$2"
  local value="${!variable_name-}"
  (umask 077; printf '%s' "${value}" >"${PANTHEON_REMOTE_SECRET_DIR}/${file_name}")
  printf -v "${variable_name}" '%s' ""
  export -n "${variable_name}" 2>/dev/null || true
}

load_remote_secret() {
  local file_name="$1"
  local target_name="$2"
  local value
  value="$(<"${PANTHEON_REMOTE_SECRET_DIR}/${file_name}")"
  printf -v "${target_name}" '%s' "${value}"
  export -n "${target_name}" 2>/dev/null || true
}

persist_remote_secret PANTHEON_GITHUB_TOKEN github-token
persist_remote_secret PANTHEON_DEV_BFF_JWT_SECRET dev-bff-jwt-secret
persist_remote_secret PANTHEON_DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON dev-bff-login-profiles
persist_remote_secret PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH assistant-control-passphrase-hash
persist_remote_secret MANAGEMENT_AI_STORE_DSN management-ai-store-dsn
persist_remote_secret MANAGEMENT_AI_DATABASE_URL management-ai-database-url
persist_remote_secret PANTHEON_MANAGEMENT_AI_DB_PASSWORD management-ai-db-password

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

DEV_BFF_TRUST_SNAPSHOT_AVAILABLE=false
DEV_BFF_TRUST_EXPECTED_FILE="${PANTHEON_REMOTE_SECRET_DIR}/dev-bff-trust-expected.json"
DEV_BFF_TRUST_ACTUAL_FILE="${PANTHEON_REMOTE_SECRET_DIR}/dev-bff-trust-actual.json"
DEV_BFF_TRUST_NAMES_FILE="${PANTHEON_REMOTE_SECRET_DIR}/dev-bff-trust-names.txt"
DEV_BFF_CREDENTIAL_EXPECTED_FILE="${PANTHEON_REMOTE_SECRET_DIR}/dev-bff-credential-expected.json"
DEV_BFF_CREDENTIAL_NAMES_FILE="${PANTHEON_REMOTE_SECRET_DIR}/dev-bff-credential-names.txt"
dev_bff_trust_names=(
  PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH
  PANTHEON_BFF_JWT_ISSUER
  PANTHEON_BFF_JWT_AUDIENCE
  PANTHEON_BFF_JWKS_URI
  PANTHEON_BFF_OIDC_DISCOVERY_URL
  PANTHEON_BFF_OIDC_ISSUER
  PANTHEON_BFF_OIDC_AUDIENCE
  PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS
  PANTHEON_BFF_TENANT_ID
  PANTHEON_BFF_ALLOWED_TENANTS
  PANTHEON_BFF_ROLE_CLAIMS
  PANTHEON_BFF_ROLE_MAP
  PANTHEON_BFF_ROLE_MAP_MODE
  PANTHEON_BFF_MFA_REQUIRED
  PANTHEON_BFF_MFA_CLAIMS
  PANTHEON_BFF_MFA_VALUES
)
(umask 077; printf '%s\n' "${dev_bff_trust_names[@]}" >"${DEV_BFF_TRUST_NAMES_FILE}")
(umask 077; printf '%s\n' \
  PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH \
  PANTHEON_BFF_JWT_SECRET \
  PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON \
  >"${DEV_BFF_CREDENTIAL_NAMES_FILE}")

capture_dev_bff_trust_snapshot() {
  if [[ "${PANTHEON_DEPLOY_ENV}" != "dev" ]]; then
    return
  fi

  local container_id
  container_id="$(docker compose -p pantheon -f docker-compose.yml ps -q operator-bff)"
  if [[ -z "${container_id}" ]]; then
    info "operator-bff is not running; using governed bootstrap trust inputs"
    return
  fi
  if ! docker inspect --format '{{json .Config.Env}}' "${container_id}" >"${DEV_BFF_TRUST_EXPECTED_FILE}"; then
    error "unable to snapshot the running operator-bff trust environment"
  fi
  chmod 0600 "${DEV_BFF_TRUST_EXPECTED_FILE}"
  DEV_BFF_TRUST_SNAPSHOT_AVAILABLE=true
  info "captured authoritative operator-bff trust for exact post-deploy readback"
}

snapshot_env_value() {
  local name="$1"
  local target_name="$2"
  local value
  IFS= read -r -d '' value < <(
    python3 - "${DEV_BFF_TRUST_EXPECTED_FILE}" "${name}" <<'PY'
import json
import sys
from pathlib import Path

entries = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
prefix = f"{sys.argv[2]}="
matches = [entry[len(prefix):] for entry in entries if entry.startswith(prefix)]
if len(matches) != 1:
    raise SystemExit(1)
sys.stdout.write(matches[0])
PY
    printf '\0'
  ) || error "running operator-bff lacks an unambiguous ${name} trust value"
  printf -v "${target_name}" '%s' "${value}"
}

apply_dev_bff_trust_policy() {
  local passphrase_hash
  if [[ "${DEV_BFF_TRUST_SNAPSHOT_AVAILABLE}" == "true" ]]; then
    python3 services/control-plane/bff/dev_auth_validation.py compare-env \
      --expected-file "${DEV_BFF_TRUST_EXPECTED_FILE}" \
      --actual-file "${DEV_BFF_TRUST_EXPECTED_FILE}" \
      --names-file "${DEV_BFF_TRUST_NAMES_FILE}" >/dev/null \
      || error "running operator-bff trust snapshot is incomplete or ambiguous"
    snapshot_env_value PANTHEON_BFF_JWT_ISSUER PANTHEON_DEV_BFF_JWT_ISSUER
    snapshot_env_value PANTHEON_BFF_JWT_AUDIENCE PANTHEON_DEV_BFF_JWT_AUDIENCE
    snapshot_env_value PANTHEON_BFF_JWKS_URI PANTHEON_DEV_BFF_JWKS_URI
    snapshot_env_value PANTHEON_BFF_OIDC_DISCOVERY_URL PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL
    snapshot_env_value PANTHEON_BFF_OIDC_ISSUER PANTHEON_DEV_BFF_OIDC_ISSUER
    snapshot_env_value PANTHEON_BFF_OIDC_AUDIENCE PANTHEON_DEV_BFF_OIDC_AUDIENCE
    snapshot_env_value PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS PANTHEON_DEV_BFF_DEV_LOGIN_TTL_SECONDS
    snapshot_env_value PANTHEON_BFF_TENANT_ID PANTHEON_DEV_BFF_TENANT_ID
    snapshot_env_value PANTHEON_BFF_ALLOWED_TENANTS PANTHEON_DEV_BFF_ALLOWED_TENANTS
    snapshot_env_value PANTHEON_BFF_ROLE_CLAIMS PANTHEON_DEV_BFF_ROLE_CLAIMS
    snapshot_env_value PANTHEON_BFF_ROLE_MAP PANTHEON_DEV_BFF_ROLE_MAP
    snapshot_env_value PANTHEON_BFF_ROLE_MAP_MODE PANTHEON_DEV_BFF_ROLE_MAP_MODE
    snapshot_env_value PANTHEON_BFF_MFA_REQUIRED PANTHEON_DEV_BFF_MFA_REQUIRED
    snapshot_env_value PANTHEON_BFF_MFA_CLAIMS PANTHEON_DEV_BFF_MFA_CLAIMS
    snapshot_env_value PANTHEON_BFF_MFA_VALUES PANTHEON_DEV_BFF_MFA_VALUES
    snapshot_env_value PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH passphrase_hash
    (umask 077; printf '%s' "${passphrase_hash}" >"${PANTHEON_REMOTE_SECRET_DIR}/assistant-control-passphrase-hash")
  else
    load_remote_secret assistant-control-passphrase-hash passphrase_hash
  fi

  if [[ "${PANTHEON_ASSISTANT_KERNEL_ENABLED}" == "true" \
    && ! "${passphrase_hash}" =~ [^[:space:]] ]]; then
    error "dev kernel deployment requires a governed control-passphrase hash"
  fi
  passphrase_hash=""
}

prepare_dev_bff_credential_readback() {
  python3 - \
    "${DEV_BFF_CREDENTIAL_EXPECTED_FILE}" \
    "${PANTHEON_REMOTE_SECRET_DIR}/assistant-control-passphrase-hash" \
    "${PANTHEON_REMOTE_SECRET_DIR}/dev-bff-jwt-secret" \
    "${PANTHEON_REMOTE_SECRET_DIR}/dev-bff-login-profiles" <<'PY'
import json
import sys
from pathlib import Path

output, passphrase_file, jwt_file, profiles_file = map(Path, sys.argv[1:])
entries = [
    "PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH=" + passphrase_file.read_text(encoding="utf-8"),
    "PANTHEON_BFF_JWT_SECRET=" + jwt_file.read_text(encoding="utf-8"),
    "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON=" + profiles_file.read_text(encoding="utf-8"),
]
output.write_text(json.dumps(entries, separators=(",", ":")), encoding="utf-8")
PY
  chmod 0600 "${DEV_BFF_CREDENTIAL_EXPECTED_FILE}"
}

verify_dev_bff_trust_readback() {
  local container_id
  container_id="$(docker compose -p pantheon -f docker-compose.yml ps -q operator-bff)"
  [[ -n "${container_id}" ]] || error "operator-bff missing during trust readback"
  docker inspect --format '{{json .Config.Env}}' "${container_id}" >"${DEV_BFF_TRUST_ACTUAL_FILE}"
  chmod 0600 "${DEV_BFF_TRUST_ACTUAL_FILE}"
  if [[ "${DEV_BFF_TRUST_SNAPSHOT_AVAILABLE}" == "true" ]]; then
    python3 services/control-plane/bff/dev_auth_validation.py compare-env \
      --expected-file "${DEV_BFF_TRUST_EXPECTED_FILE}" \
      --actual-file "${DEV_BFF_TRUST_ACTUAL_FILE}" \
      --names-file "${DEV_BFF_TRUST_NAMES_FILE}" >/dev/null \
      || error "operator-bff deployment changed preserved trust configuration"
  fi
  python3 services/control-plane/bff/dev_auth_validation.py compare-env \
    --expected-file "${DEV_BFF_CREDENTIAL_EXPECTED_FILE}" \
    --actual-file "${DEV_BFF_TRUST_ACTUAL_FILE}" \
    --names-file "${DEV_BFF_CREDENTIAL_NAMES_FILE}" >/dev/null \
    || error "operator-bff credential/passphrase readback does not match the governed inputs"
  info "operator-bff preserved trust readback passed"
}

load_dev_compose_secrets() {
  load_remote_secret dev-bff-jwt-secret PANTHEON_DEV_BFF_JWT_SECRET
  load_remote_secret dev-bff-login-profiles PANTHEON_DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON
  load_remote_secret assistant-control-passphrase-hash PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH
  load_remote_secret management-ai-store-dsn MANAGEMENT_AI_STORE_DSN
  load_remote_secret management-ai-database-url MANAGEMENT_AI_DATABASE_URL
  load_remote_secret management-ai-db-password PANTHEON_MANAGEMENT_AI_DB_PASSWORD
}

clear_dev_compose_secrets() {
  PANTHEON_DEV_BFF_JWT_SECRET=""
  PANTHEON_DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON=""
  PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH=""
  MANAGEMENT_AI_STORE_DSN=""
  MANAGEMENT_AI_DATABASE_URL=""
  PANTHEON_MANAGEMENT_AI_DB_PASSWORD=""
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
  )
  local present_paths=()
  local path
  local runtime_status
  local stash_label

  for path in "${known_paths[@]}"; do
    if [[ ! -e "$path" ]]; then
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

require_clean_checkout() {
  local status
  local stash_label

  preserve_known_deploy_runtime_state

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
  local github_token
  if [[ "${1:-}" == "--prune" ]]; then
    prune_flag=(--prune)
    shift
  fi

  load_remote_secret github-token github_token
  if [[ -n "${github_token}" ]]; then
    local github_basic_auth
    github_basic_auth="$(printf 'x-access-token:%s' "${github_token}" | base64 | tr -d '\n')"
    github_token=""
    info "fetch auth: github token present"
    git -c "http.extraheader=AUTHORIZATION: basic ${github_basic_auth}" \
      fetch --recurse-submodules=no "${prune_flag[@]}" origin "$@"
    github_basic_auth=""
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
  local mgmt_pass
  load_remote_secret management-ai-db-password mgmt_pass
  mgmt_pass="${mgmt_pass:-pantheon_management_ai_dev}"
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
  info "dev root compose ps after failure"
  docker compose -p pantheon -f docker-compose.yml ps || true
  info "evolution daily sweep scheduler logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=120 evolution-daily-sweep-scheduler || true
  info "operator-bff logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=240 operator-bff || true
  info "postgres logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=120 postgres || true
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
capture_dev_bff_trust_snapshot

case "${PANTHEON_DEPLOY_COMPONENT}" in
  root)
    snapshot_remote_state pantheon docker-compose.yml
    prepare_deploy_worktree
    apply_dev_bff_trust_policy
    prepare_dev_bff_credential_readback
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
    load_dev_compose_secrets
    COMPOSE_BAKE=false \
    COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES}" \
    GIT_SHA="${PANTHEON_DEPLOY_SHA}" \
    PANTHEON_ENV=dev \
    PANTHEON_DEPLOYMENT_STAGE=dev \
    PANTHEON_LIVE_BROKER_ENABLED=false \
    BROKER_PAPER_ENABLED=true \
    AGORA_WORKSHOP_STORE_BACKEND=postgres \
    AGORA_WORKSHOP_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_WORKSHOP_STORE_SCHEMA=agora \
    AGORA_RESEARCH_STORE_BACKEND=postgres \
    AGORA_RESEARCH_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_RESEARCH_STORE_SCHEMA=agora_research \
    AGORA_TRADING_ROOM_STORE_BACKEND=postgres \
    AGORA_TRADING_ROOM_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_TRADING_ROOM_STORE_SCHEMA=agora \
    PANTHEON_BFF_CORS_ORIGINS="${PANTHEON_DEV_BFF_CORS_ORIGINS}" \
    PANTHEON_BFF_AUTH_STUB="${PANTHEON_DEV_BFF_AUTH_STUB}" \
    PANTHEON_BFF_AUTH_MODE="${PANTHEON_DEV_BFF_AUTH_MODE}" \
    PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS= \
    PANTHEON_BFF_JWT_SECRET="${PANTHEON_DEV_BFF_JWT_SECRET}" \
    PANTHEON_BFF_JWT_ISSUER="${PANTHEON_DEV_BFF_JWT_ISSUER}" \
    PANTHEON_BFF_JWT_AUDIENCE="${PANTHEON_DEV_BFF_JWT_AUDIENCE}" \
    PANTHEON_BFF_JWKS_URI="${PANTHEON_DEV_BFF_JWKS_URI}" \
    PANTHEON_BFF_OIDC_DISCOVERY_URL="${PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL}" \
    PANTHEON_BFF_OIDC_ISSUER="${PANTHEON_DEV_BFF_OIDC_ISSUER}" \
    PANTHEON_BFF_OIDC_AUDIENCE="${PANTHEON_DEV_BFF_OIDC_AUDIENCE}" \
    PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON="${PANTHEON_DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON}" \
    PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS="${PANTHEON_DEV_BFF_DEV_LOGIN_TTL_SECONDS}" \
    PANTHEON_BFF_TENANT_ID="${PANTHEON_DEV_BFF_TENANT_ID}" \
    PANTHEON_BFF_ALLOWED_TENANTS="${PANTHEON_DEV_BFF_ALLOWED_TENANTS}" \
    PANTHEON_BFF_ROLE_CLAIMS="${PANTHEON_DEV_BFF_ROLE_CLAIMS}" \
    PANTHEON_BFF_ROLE_MAP="${PANTHEON_DEV_BFF_ROLE_MAP}" \
    PANTHEON_BFF_ROLE_MAP_MODE="${PANTHEON_DEV_BFF_ROLE_MAP_MODE}" \
    PANTHEON_BFF_DEFAULT_ROLE=viewer \
    PANTHEON_BFF_MFA_REQUIRED="${PANTHEON_DEV_BFF_MFA_REQUIRED}" \
    PANTHEON_BFF_MFA_CLAIMS="${PANTHEON_DEV_BFF_MFA_CLAIMS}" \
    PANTHEON_BFF_MFA_VALUES="${PANTHEON_DEV_BFF_MFA_VALUES}" \
    PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED}" \
    PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH}" \
    PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS}" \
    PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH="${PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH}" \
    PANTHEON_ASSISTANT_REPAIR_REPO_URL="${PANTHEON_ASSISTANT_REPAIR_REPO_URL}" \
    PANTHEON_ASSISTANT_REPAIR_REMOTE_URL="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL}" \
    PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS}" \
    PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS}" \
    PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES}" \
    PANTHEON_STATUS_ROOT_HOST="${PANTHEON_STATUS_ROOT_HOST}" \
    PANTHEON_STATUS_ROOT_CONTAINER="${PANTHEON_STATUS_ROOT_CONTAINER}" \
    MANAGEMENT_AI_STORE_DSN="${MANAGEMENT_AI_STORE_DSN}" \
    MANAGEMENT_AI_DATABASE_URL="${MANAGEMENT_AI_DATABASE_URL}" \
    PANTHEON_MANAGEMENT_AI_DB_PASSWORD="${PANTHEON_MANAGEMENT_AI_DB_PASSWORD}" \
      docker compose -p pantheon -f docker-compose.yml up -d --build \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    clear_dev_compose_secrets
    curl_with_retry http://127.0.0.1:18001/health \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    curl_with_retry http://127.0.0.1:18001/readyz \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    assert_bff_source_sha http://127.0.0.1:18001/bff/version \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    verify_dev_bff_trust_readback \
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
    apply_dev_bff_trust_policy
    prepare_dev_bff_credential_readback
    load_dev_compose_secrets
    COMPOSE_BAKE=false \
    COMPOSE_PROFILES="" \
    GIT_SHA="${PANTHEON_DEPLOY_SHA}" \
    PANTHEON_ENV=dev \
    PANTHEON_DEPLOYMENT_STAGE=dev \
    PANTHEON_LIVE_BROKER_ENABLED=false \
    BROKER_PAPER_ENABLED=true \
    AGORA_WORKSHOP_STORE_BACKEND=postgres \
    AGORA_WORKSHOP_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_WORKSHOP_STORE_SCHEMA=agora \
    AGORA_RESEARCH_STORE_BACKEND=postgres \
    AGORA_RESEARCH_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_RESEARCH_STORE_SCHEMA=agora_research \
    AGORA_TRADING_ROOM_STORE_BACKEND=postgres \
    AGORA_TRADING_ROOM_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon \
    AGORA_TRADING_ROOM_STORE_SCHEMA=agora \
    PANTHEON_BFF_CORS_ORIGINS="${PANTHEON_DEV_BFF_CORS_ORIGINS}" \
    PANTHEON_BFF_AUTH_STUB="${PANTHEON_DEV_BFF_AUTH_STUB}" \
    PANTHEON_BFF_AUTH_MODE="${PANTHEON_DEV_BFF_AUTH_MODE}" \
    PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS= \
    PANTHEON_BFF_JWT_SECRET="${PANTHEON_DEV_BFF_JWT_SECRET}" \
    PANTHEON_BFF_JWT_ISSUER="${PANTHEON_DEV_BFF_JWT_ISSUER}" \
    PANTHEON_BFF_JWT_AUDIENCE="${PANTHEON_DEV_BFF_JWT_AUDIENCE}" \
    PANTHEON_BFF_JWKS_URI="${PANTHEON_DEV_BFF_JWKS_URI}" \
    PANTHEON_BFF_OIDC_DISCOVERY_URL="${PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL}" \
    PANTHEON_BFF_OIDC_ISSUER="${PANTHEON_DEV_BFF_OIDC_ISSUER}" \
    PANTHEON_BFF_OIDC_AUDIENCE="${PANTHEON_DEV_BFF_OIDC_AUDIENCE}" \
    PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON="${PANTHEON_DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON}" \
    PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS="${PANTHEON_DEV_BFF_DEV_LOGIN_TTL_SECONDS}" \
    PANTHEON_BFF_TENANT_ID="${PANTHEON_DEV_BFF_TENANT_ID}" \
    PANTHEON_BFF_ALLOWED_TENANTS="${PANTHEON_DEV_BFF_ALLOWED_TENANTS}" \
    PANTHEON_BFF_ROLE_CLAIMS="${PANTHEON_DEV_BFF_ROLE_CLAIMS}" \
    PANTHEON_BFF_ROLE_MAP="${PANTHEON_DEV_BFF_ROLE_MAP}" \
    PANTHEON_BFF_ROLE_MAP_MODE="${PANTHEON_DEV_BFF_ROLE_MAP_MODE}" \
    PANTHEON_BFF_DEFAULT_ROLE=viewer \
    PANTHEON_BFF_MFA_REQUIRED="${PANTHEON_DEV_BFF_MFA_REQUIRED}" \
    PANTHEON_BFF_MFA_CLAIMS="${PANTHEON_DEV_BFF_MFA_CLAIMS}" \
    PANTHEON_BFF_MFA_VALUES="${PANTHEON_DEV_BFF_MFA_VALUES}" \
    PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED}" \
    PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH}" \
    PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS}" \
    PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH="${PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH}" \
    PANTHEON_ASSISTANT_REPAIR_REPO_URL="${PANTHEON_ASSISTANT_REPAIR_REPO_URL}" \
    PANTHEON_ASSISTANT_REPAIR_REMOTE_URL="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL}" \
    PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS}" \
    PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS}" \
    PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES}" \
    PANTHEON_STATUS_ROOT_HOST="${PANTHEON_STATUS_ROOT_HOST}" \
    PANTHEON_STATUS_ROOT_CONTAINER="${PANTHEON_STATUS_ROOT_CONTAINER}" \
    MANAGEMENT_AI_STORE_BACKEND="${MANAGEMENT_AI_STORE_BACKEND}" \
    MANAGEMENT_AI_STORE_SCHEMA="${MANAGEMENT_AI_STORE_SCHEMA}" \
    MANAGEMENT_AI_STORE_DSN="${MANAGEMENT_AI_STORE_DSN}" \
    MANAGEMENT_AI_DATABASE_URL="${MANAGEMENT_AI_DATABASE_URL}" \
    PANTHEON_MANAGEMENT_AI_DB_PASSWORD="${PANTHEON_MANAGEMENT_AI_DB_PASSWORD}" \
    PANTHEON_MGMT_AI_ATTACH_BUCKET="${PANTHEON_MGMT_AI_ATTACH_BUCKET}" \
    PANTHEON_MGMT_AI_ATTACH_LOCATION="${PANTHEON_MGMT_AI_ATTACH_LOCATION:-asia-east1}" \
      docker compose -p pantheon -f docker-compose.yml up -d --build --no-deps operator-bff \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    clear_dev_compose_secrets
    curl_with_retry http://127.0.0.1:18001/health \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    curl_with_retry http://127.0.0.1:18001/readyz \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    assert_bff_source_sha http://127.0.0.1:18001/bff/version \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    verify_dev_bff_trust_readback \
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
  } | gcloud compute ssh "${REMOTE_USER}@${vm}" \
    --project="${PROJECT_ID}" \
    --zone="${zone}" \
    --quiet \
    --ssh-flag=-T \
    --command="bash -s"
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
