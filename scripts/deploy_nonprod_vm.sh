#!/usr/bin/env bash
# Deploy Pantheon non-prod VM compose stacks from a verified git commit.
#
# This script is designed for GitHub Actions, but it can also be run by an
# operator from a workstation with the configured remote transport. The VM's human-facing checkout
# is used only as the git object source and snapshot target; deployment runs from
# a managed clean worktree under ~/pantheon-ci-deploy.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PANTHEON_DEPLOY_CONTROLLER_CONTRACT_VERSION="dev-root-isolation-v1"

# Large hosted dev datasets can make one lifecycle projection tick take
# 150-180 seconds. Keep the compose default fail-closed at 120 seconds, while
# managed dev deploys explicitly allow one slow tick plus scheduling headroom.
DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS="${DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS:-300}"
# Strict by default: the dev deploy must not silently re-force stub/permissive
# auth on every run. docker-compose.yml's own PANTHEON_BFF_AUTH_STUB/MODE
# defaults are strict/false, but this script always passes an explicit value
# into the compose environment (see PANTHEON_BFF_AUTH_STUB= below), which
# overrides the compose file default regardless of what it says. Operators who
# need a permissive dev session must opt in explicitly via
# DEV_BFF_AUTH_STUB=true DEV_BFF_AUTH_MODE=permissive.
DEV_BFF_AUTH_STUB="${DEV_BFF_AUTH_STUB:-false}"
DEV_BFF_AUTH_MODE="${DEV_BFF_AUTH_MODE:-strict}"
DEV_BFF_AUTH_READINESS_TIMEOUT_SECONDS="${DEV_BFF_AUTH_READINESS_TIMEOUT_SECONDS:-120}"
DEV_BFF_AUTH_READINESS_POLL_INTERVAL_SECONDS="${DEV_BFF_AUTH_READINESS_POLL_INTERVAL_SECONDS:-2}"
DEV_DEPLOY_DEADLINE_SECONDS="${DEV_DEPLOY_DEADLINE_SECONDS:-${DEV_DEPLOY_TIMEOUT_SECONDS:-7200}}"
DEV_ROLLBACK_BACKEND_SHA="${DEV_ROLLBACK_BACKEND_SHA:-${PANTHEON_DEV_ROLLBACK_BACKEND_SHA:-}}"
DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED="${DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED:-false}"
# Governed verifier/dev-login credentials for the strict auth cutover. These
# must come from a secret source (GitHub Actions secrets in CI), never from
# compose file defaults. When strict mode is requested without them, the
# preflight gate below refuses to deploy rather than shipping a strict-looking
# BFF where every protected route is actually unusable.
DEV_BFF_JWT_SECRET="${DEV_BFF_JWT_SECRET:-}"
PANTHEON_DEV_CAPITAL_JWT_SECRET="${PANTHEON_DEV_CAPITAL_JWT_SECRET:-$DEV_BFF_JWT_SECRET}"
DEV_BFF_JWT_ISSUER="${DEV_BFF_JWT_ISSUER:-pantheon-dev}"
DEV_BFF_JWT_AUDIENCE="${DEV_BFF_JWT_AUDIENCE:-bff-operators}"
DEV_BFF_JWKS_URI="${DEV_BFF_JWKS_URI:-}"
DEV_BFF_OIDC_DISCOVERY_URL="${DEV_BFF_OIDC_DISCOVERY_URL:-}"
DEV_BFF_OIDC_ISSUER="${DEV_BFF_OIDC_ISSUER:-}"
DEV_BFF_OIDC_AUDIENCE="${DEV_BFF_OIDC_AUDIENCE:-}"
DEV_BFF_OIDC_CLIENT_ID="${DEV_BFF_OIDC_CLIENT_ID:-}"
DEV_BFF_OIDC_CLIENT_SECRET="${DEV_BFF_OIDC_CLIENT_SECRET:-}"
# Dedicated server-bound identities used by governed dev proofs. Client IDs
# are public identifiers with stable dev defaults; every secret must be
# supplied independently by the deploy environment. Keeping these pairs
# separate is what gives approval/apply evidence distinct immutable subjects.
DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID="${DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID:-pantheon-dev-viewer-v1}"
DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET="${DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET:-}"
DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID="${DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID:-pantheon-dev-approver-v1}"
DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET="${DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET:-}"
DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID="${DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID:-pantheon-dev-risk-owner-v1}"
DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET="${DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET:-}"
DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID="${DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID:-pantheon-dev-operator-a-v1}"
DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET="${DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET:-}"
DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID="${DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID:-pantheon-dev-operator-b-v1}"
DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET="${DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET:-}"
DEV_BFF_MFA_REQUIRED="${DEV_BFF_MFA_REQUIRED:-true}"
DEV_BFF_MFA_CLAIMS="${DEV_BFF_MFA_CLAIMS:-amr,acr,mfa,mfa_verified,firebase.sign_in_second_factor}"
DEV_BFF_MFA_VALUES="${DEV_BFF_MFA_VALUES:-true,1,yes,mfa,otp,totp,webauthn}"
DEV_BFF_REQUIRE_EMAIL_VERIFIED="${DEV_BFF_REQUIRE_EMAIL_VERIFIED:-true}"
# Keep the legacy/generic operator credential as the explicit no-MFA negative
# fixture. Governed actors use dedicated credentials and MFA-positive tokens.
DEV_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED="${DEV_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED:-false}"
DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED="${DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED:-true}"
DEV_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED="${DEV_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED:-true}"
DEV_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED="${DEV_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED:-true}"
DEV_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED="${DEV_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED:-true}"
DEV_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED="${DEV_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED:-true}"
DEV_BFF_ROLE_CLAIMS="${DEV_BFF_ROLE_CLAIMS:-roles,role}"
DEV_BFF_ROLE_MAP="${DEV_BFF_ROLE_MAP:-}"
DEV_BFF_ROLE_MAP_MODE="${DEV_BFF_ROLE_MAP_MODE:-passthrough}"
DEV_BFF_DEFAULT_ROLE="${DEV_BFF_DEFAULT_ROLE:-viewer}"
# Human-provisioned service credential shared only by operator-bff and the
# OpenClaw adapter. There is intentionally no generated/local fallback.
DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN="${DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN:-}"
DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED="${DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED:-true}"
# Optional long-lived Claude CLI credential for the openclaw-gateway
# container's own `claude -p` calls. Empty means it stays on its persisted
# interactive-login session.
DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN="${DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN:-}"
DEV_BFF_TENANT_ID="${DEV_BFF_TENANT_ID:-tenant-dev}"
DEV_BFF_ALLOWED_TENANTS="${DEV_BFF_ALLOWED_TENANTS:-${DEV_BFF_TENANT_ID},pantheon-dev}"
DEV_ASSISTANT_KERNEL_ENABLED="${DEV_ASSISTANT_KERNEL_ENABLED:-true}"
DEV_ASSISTANT_CONTROL_MODE_STORE_PATH="${DEV_ASSISTANT_CONTROL_MODE_STORE_PATH:-/data/bff/assistant-control-mode.json}"
DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH="${DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH:-}"
DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-300}"
DEV_BFF_STUB_CAPABILITIES="${DEV_BFF_STUB_CAPABILITIES:-assistant.kernel.debug,assistant.kernel.repair}"
DEV_MANAGEMENT_AI_STORE_BACKEND="${DEV_MANAGEMENT_AI_STORE_BACKEND:-postgres}"
DEV_MANAGEMENT_AI_STORE_SCHEMA="${DEV_MANAGEMENT_AI_STORE_SCHEMA-management_ai}"
DEV_MANAGEMENT_AI_DB_USER="${DEV_MANAGEMENT_AI_DB_USER:-pantheon_management_ai}"
DEV_MANAGEMENT_AI_DB_PASSWORD="${DEV_MANAGEMENT_AI_DB_PASSWORD:-pantheon_management_ai_dev}"
DEV_MANAGEMENT_AI_DB_NAME="${DEV_MANAGEMENT_AI_DB_NAME:-pantheon}"
DEV_MANAGEMENT_AI_DATABASE_URL="${DEV_MANAGEMENT_AI_DATABASE_URL:-}"
DEV_MANAGEMENT_AI_ATTACH_BUCKET="${DEV_MANAGEMENT_AI_ATTACH_BUCKET:-}"
DEV_MANAGEMENT_AI_ATTACH_LOCATION="${DEV_MANAGEMENT_AI_ATTACH_LOCATION:-asia-east1}"
PANTHEON_DEV_DOCKER_PRUNE="${PANTHEON_DEV_DOCKER_PRUNE:-false}"
# Telemetry cleanup is maintenance, not a required deployment step.  A root
# deploy must stay bounded even when canonical telemetry has grown large.
# Enable it explicitly for a scheduled/one-off maintenance run.
PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE="${PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE:-false}"
DEV_COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-}"
SOURCE_REFRESH_EGRESS_MODE="${PANTHEON_EXTERNAL_EGRESS:-deny}"
SOURCE_REFRESH_ALLOWED_HOSTS="${PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS:-}"
SOURCE_REFRESH_SELECTED="false"
case ",${DEV_COMPOSE_PROFILES}," in
  *,source-ingest-scheduler,*) SOURCE_REFRESH_SELECTED="true" ;;
esac
if [[ "${SOURCE_REFRESH_SELECTED}" == "true" ]]; then
  SOURCE_REFRESH_CONTROLLER_MODE="reconcile_and_pull"
  SOURCE_REFRESH_TRUTH_LEVEL="reconciled_live_proof"
  SOURCE_REFRESH_MAX_TICKS="${SOURCE_INGEST_CONTROLLER_MAX_TICKS:-1}"
  SOURCE_REFRESH_RESTART_POLICY="no"
else
  # The default owner is a durable internal reconciler. Provider egress stays
  # available only through the explicit bounded profile above.
  SOURCE_REFRESH_CONTROLLER_MODE="reconcile_only"
  SOURCE_REFRESH_TRUTH_LEVEL="scheduled_tick"
  SOURCE_REFRESH_MAX_TICKS="0"
  SOURCE_REFRESH_RESTART_POLICY="unless-stopped"
fi
SOURCE_REFRESH_MAX_CONCURRENCY="${SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY:-1}"
SOURCE_REFRESH_MAX_RECORDS="${SOURCE_INGEST_MAX_RECORDS:-100}"
SOURCE_REFRESH_CONNECTOR_ID="${SOURCE_INGEST_BOUNDED_CONNECTOR_ID:-tw-twse-tpex-official-market}"
SOURCE_REFRESH_TIMEOUT_SECONDS="${SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS:-1800}"
if [[ "${SOURCE_REFRESH_SELECTED}" == "true" \
  && "${SOURCE_REFRESH_CONNECTOR_ID}" == "tw-twse-tpex-official-market" ]]; then
  # The reviewed official connector uses TWSE OpenAPI for today's full-market
  # close and the TWSE market site for bounded per-symbol monthly history.
  # Keep the second host connector-specific instead of widening default egress.
  case ",${SOURCE_REFRESH_ALLOWED_HOSTS}," in
    *,www.twse.com.tw,*) ;;
    *) SOURCE_REFRESH_ALLOWED_HOSTS="${SOURCE_REFRESH_ALLOWED_HOSTS:+${SOURCE_REFRESH_ALLOWED_HOSTS},}www.twse.com.tw" ;;
  esac
fi
DEV_APP_DB_USER="${DEV_APP_DB_USER:-${PANTHEON_APP_DB_USER:-pantheon_app}}"

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
  local lease_expected_backend_sha="${PANTHEON_DEV_LEASE_EXPECTED_BACKEND_SHA:-${DEPLOY_SHA}}"

  # Empty-host bootstrap deliberately deploys a read-only predecessor before
  # the candidate.  The job-owned lease remains bound to the candidate SHA;
  # only this explicitly marked bootstrap invocation may deploy a different
  # predecessor SHA under that lease.  Ordinary deploys cannot override the
  # lease identity.
  if [[ "${lease_expected_backend_sha}" != "${DEPLOY_SHA}" ]]; then
    [[ "${PANTHEON_DEV_BOOTSTRAP_PREDECESSOR:-false}" == "true" ]] \
      || error "dev lease expected backend override is only permitted for an explicit bootstrap predecessor"
  fi

  [[ -n "${guarded_lease_id}" ]] \
    || error "dev deployment requires the pinned lease guard lease ID"
  [[ -f "${lease_state_file}" && ! -L "${lease_state_file}" ]] \
    || error "dev deployment requires a regular lease state file"

  python3 - "${lease_state_file}" "${guarded_lease_id}" "${lease_expected_backend_sha}" <<'PY'
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

  info "dev environment lease contract verified: ${guarded_lease_id} -> ${lease_expected_backend_sha} (deploy=${DEPLOY_SHA})"
}

validate_target_selection() {
  case "${PROJECT_ID:-}" in
    pantheon-benjamin-20260528|pantheon-lupin-dev-20260719)
      error "GCP project ${PROJECT_ID} is retired; refusing to deploy"
      ;;
  esac

  local check_vars=(
    PROJECT_ID
    REMOTE_USER
    DEV_VM
    DEV_ZONE
    DEV_REMOTE_DIR
    DEV_DEPLOY_SSH_HOST
    DEV_DEPLOY_SSH_USER
    DEV_BFF_PUBLIC_HOST
    DEV_FE_PUBLIC_HOST
    DEV_FE_STATIC_ROOT
    DEV_BFF_CORS_ORIGINS
    DEV_BFF_CANONICAL_CORS_ORIGIN
    DEV_BFF_REQUIRED_CORS_ORIGINS
    PANTHEON_DEPLOY_WORKTREE_ROOT
    STAGING_CONTROL_VM
    STAGING_CONTROL_ZONE
    STAGING_CONTROL_REMOTE_DIR
    STAGING_EXEC_VM
    STAGING_EXEC_ZONE
    STAGING_EXEC_REMOTE_DIR
    STAGING_EXEC_HEALTH_URL
    STAGING_BFF_CORS_ORIGINS
    STAGING_BFF_CANONICAL_CORS_ORIGIN
  )
  local var_name val
  local retired_pattern='sslip\.io|104\.155\.223\.192|35\.201\.204\.12|35\.201\.239\.38|34\.81\.75\.241|35\.236\.178\.81|pantheon-benjamin-20260528|pantheon-lupin-dev-20260719|pantheon-lupin-dev|/home/lupin|^lupin$'
  for var_name in "${check_vars[@]}"; do
    val="${!var_name:-}"
    if [[ -n "$val" && "$val" =~ ${retired_pattern} ]]; then
      error "${var_name} contains retired target identity (${val}); refusing to deploy"
    fi
  done

  case "${DEPLOY_ENV}" in
    dev)
      [[ -n "${PROJECT_ID:-}" ]] || error "dev deployment requires --project-id or PROJECT_ID to be set"
      [[ -n "${REMOTE_USER:-}" ]] || error "dev deployment requires REMOTE_USER to be set"
      local required_dev_vars=(
        DEV_VM
        DEV_ZONE
        DEV_REMOTE_DIR
        DEV_DEPLOY_SSH_HOST
        DEV_BFF_PUBLIC_HOST
        DEV_FE_PUBLIC_HOST
        DEV_FE_STATIC_ROOT
        DEV_BFF_CORS_ORIGINS
      )
      for var_name in "${required_dev_vars[@]}"; do
        if [[ -z "${!var_name:-}" ]]; then
          error "dev deployment requires ${var_name} to be set; refusing to deploy with missing target identity"
        fi
      done
      ;;
    staging-live)
      [[ -n "${PROJECT_ID:-}" ]] || error "staging-live deployment requires --project-id or PROJECT_ID to be set"
      [[ -n "${REMOTE_USER:-}" ]] || error "staging-live deployment requires REMOTE_USER to be set"
      local required_staging_vars=()
      case "${COMPONENT}" in
        control)
          required_staging_vars=(STAGING_CONTROL_VM STAGING_CONTROL_ZONE STAGING_CONTROL_REMOTE_DIR STAGING_BFF_CORS_ORIGINS)
          ;;
        exec)
          required_staging_vars=(STAGING_EXEC_VM STAGING_EXEC_ZONE STAGING_EXEC_REMOTE_DIR STAGING_EXEC_HEALTH_URL)
          ;;
        all)
          required_staging_vars=(STAGING_CONTROL_VM STAGING_CONTROL_ZONE STAGING_CONTROL_REMOTE_DIR STAGING_EXEC_VM STAGING_EXEC_ZONE STAGING_EXEC_REMOTE_DIR STAGING_EXEC_HEALTH_URL STAGING_BFF_CORS_ORIGINS)
          ;;
      esac
      for var_name in "${required_staging_vars[@]}"; do
        if [[ -z "${!var_name:-}" ]]; then
          error "staging-live deployment requires ${var_name} to be set; refusing to deploy with missing target identity"
        fi
      done
      ;;
  esac
}

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy_nonprod_vm.sh --environment <dev|staging-live> --sha <commit> [options]

Options:
  --environment <name>   Required. dev or staging-live.
  --component <name>     auto, root, bff, control, exec, or all. Default: auto.
                         auto maps to root for dev and all for staging-live.
                         bff (dev only): rebuild operator-bff and its lifecycle
                         projector; paper fleet and all other services stay running.
  --sha <commit>         Required unless GITHUB_SHA is set. Commit to deploy.
  --project-id <id>      GCP project. Default: pantheon-dev-20260902 for dev.
  --allow-dirty          Emergency only: stash dirty managed deploy worktree
                         changes before checkout.
  --allow-example-env    Allow staging to use env/*.env.example if real env files
                         are absent. Intended for rehearsal only.
  --dry-run              Print the target plan without SSHing.
  --rollback-sha <commit>
                         Optional. Baseline BFF commit to restore if post-rollout
                         gates fail.
  --deadline-seconds <seconds>
                         Deploy command deadline in seconds. Default: 7200.
  --deploy-timeout-seconds <seconds>
                         Alias for --deadline-seconds.
  --help                 Show this message.

Environment overrides:
  REMOTE_USER
  DEV_ROLLBACK_BACKEND_SHA PANTHEON_DEV_ROLLBACK_BACKEND_SHA
  DEV_DEPLOY_DEADLINE_SECONDS DEV_DEPLOY_TIMEOUT_SECONDS
  PANTHEON_DEPLOY_WORKTREE_ROOT
  GITHUB_TOKEN
  DEV_VM DEV_ZONE DEV_REMOTE_DIR
  DEV_DEPLOY_SSH_HOST DEV_DEPLOY_SSH_USER DEV_DEPLOY_SSH_PORT
  DEV_DEPLOY_SSH_KEY_FILE DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE
  DEV_BFF_PUBLIC_HOST DEV_FE_PUBLIC_HOST DEV_FE_STATIC_ROOT
  DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS
  DEV_BFF_CANONICAL_CORS_ORIGIN DEV_BFF_CORS_ORIGINS
  DEV_BFF_REQUIRED_CORS_ORIGINS DEV_BFF_AUTH_STUB DEV_BFF_AUTH_MODE
  DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED
  DEV_BFF_JWT_SECRET DEV_BFF_JWT_ISSUER DEV_BFF_JWT_AUDIENCE
  DEV_BFF_JWKS_URI DEV_BFF_OIDC_DISCOVERY_URL
  DEV_BFF_OIDC_ISSUER DEV_BFF_OIDC_AUDIENCE
  DEV_BFF_OIDC_CLIENT_ID DEV_BFF_OIDC_CLIENT_SECRET
  DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET
  DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET
  DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET
  DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET
  DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET
  DEV_BFF_MFA_REQUIRED DEV_BFF_MFA_CLAIMS DEV_BFF_MFA_VALUES
  DEV_BFF_REQUIRE_EMAIL_VERIFIED DEV_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED
  DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED
  DEV_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED DEV_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED
  DEV_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED DEV_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED
  DEV_BFF_ROLE_CLAIMS DEV_BFF_ROLE_MAP DEV_BFF_ROLE_MAP_MODE DEV_BFF_DEFAULT_ROLE
  DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED
  DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN
  DEV_BFF_TENANT_ID DEV_BFF_ALLOWED_TENANTS
  DEV_ASSISTANT_KERNEL_ENABLED DEV_ASSISTANT_CONTROL_MODE_STORE_PATH
  DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH
  DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS
  DEV_BFF_STUB_CAPABILITIES
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
  MANAGEMENT_AI_STORE_SCHEMA="${MANAGEMENT_AI_STORE_SCHEMA-${DEV_MANAGEMENT_AI_STORE_SCHEMA}}"
  MANAGEMENT_AI_DATABASE_URL="${MANAGEMENT_AI_DATABASE_URL:-$DEV_MANAGEMENT_AI_DATABASE_URL}"
  # Dev compose has a durable local attachment store; use GCS only when configured.
  PANTHEON_MGMT_AI_ATTACH_BUCKET="${PANTHEON_MGMT_AI_ATTACH_BUCKET:-$DEV_MANAGEMENT_AI_ATTACH_BUCKET}"

  if [[ "${MANAGEMENT_AI_STORE_BACKEND:-}" == "postgres" && "${PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE:-false}" == "true" ]]; then
    if [[ -z "$MANAGEMENT_AI_STORE_SCHEMA" || ! "$MANAGEMENT_AI_STORE_SCHEMA" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
      error "MANAGEMENT_AI_STORE_SCHEMA is empty or invalid SQL identifier: '$MANAGEMENT_AI_STORE_SCHEMA'"
    fi
    if [[ "${MANAGEMENT_AI_STORE_SCHEMA,,}" == "public" ]]; then
      error "MANAGEMENT_AI_STORE_SCHEMA cannot be 'public'; refusing to target canonical telemetry schema"
    fi
  fi
}

configure_management_ai_dev_kernel_env() {
  if [[ "$DEPLOY_ENV" != "dev" ]]; then
    return
  fi

  PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED:-$DEV_ASSISTANT_KERNEL_ENABLED}"
  PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-$DEV_ASSISTANT_CONTROL_MODE_STORE_PATH}"
  PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH="${PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH:-$DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH}"
  PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-$DEV_ASSISTANT_CONTROL_IDLE_TTL_SECONDS}"
  PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES:-$DEV_BFF_STUB_CAPABILITIES}"
  PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN:-$DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN}"
  PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED:-$DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED}"
  PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN="${PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN:-$DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN}"
}

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
    --deadline-seconds|--deploy-timeout-seconds)
      DEV_DEPLOY_DEADLINE_SECONDS="${2:-}"
      shift 2
      ;;
    --rollback-sha)
      DEV_ROLLBACK_BACKEND_SHA="${2:-}"
      shift 2
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

[[ "$DEV_DEPLOY_DEADLINE_SECONDS" =~ ^[0-9]+$ && "$DEV_DEPLOY_DEADLINE_SECONDS" -ge 1 ]] \
  || error "DEV_DEPLOY_DEADLINE_SECONDS must be a positive integer"

[[ -n "$DEPLOY_ENV" ]] || error "--environment is required"
[[ -n "$DEPLOY_SHA" ]] || error "--sha is required unless GITHUB_SHA is set"

case "$DEPLOY_ENV" in
  dev)
    [[ "$COMPONENT" == "auto" ]] && COMPONENT="root"
    case "$COMPONENT" in
      root|bff) ;;
      *) error "dev supports only --component root or --component bff" ;;
    esac
    # Documented dev defaults apply only when unset. If explicitly empty,
    # keep empty so validate_target_selection fails closed.
    if [[ -z "${PROJECT_ID+x}" ]]; then PROJECT_ID="pantheon-dev-20260902"; fi
    if [[ -z "${REMOTE_USER+x}" ]]; then REMOTE_USER="chloe_ong_dev_cctech_support_com"; fi
    if [[ -z "${DEV_VM+x}" ]]; then DEV_VM="pantheon-dev-deploy"; fi
    if [[ -z "${DEV_ZONE+x}" ]]; then DEV_ZONE="asia-east1-b"; fi
    if [[ -z "${DEV_REMOTE_DIR+x}" ]]; then DEV_REMOTE_DIR="/home/chloe_ong_dev_cctech_support_com/pantheon"; fi
    if [[ -z "${DEV_DEPLOY_SSH_HOST+x}" ]]; then DEV_DEPLOY_SSH_HOST="34.81.52.222"; fi
    if [[ -z "${DEV_BFF_CANONICAL_CORS_ORIGIN+x}" ]]; then DEV_BFF_CANONICAL_CORS_ORIGIN="https://app.dev.mvl-cap.tw"; fi
    if [[ -z "${DEV_BFF_REQUIRED_CORS_ORIGINS+x}" ]]; then DEV_BFF_REQUIRED_CORS_ORIGINS="https://preview--pantheon-dev.lovable.app,https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com,https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app,https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com"; fi
    if [[ -z "${DEV_BFF_CORS_ORIGINS+x}" ]]; then
      DEV_BFF_CORS_ORIGINS="${DEV_BFF_CANONICAL_CORS_ORIGIN}"
      DEV_BFF_CORS_ORIGINS="$(append_csv_unique "$DEV_BFF_CORS_ORIGINS" "$DEV_BFF_REQUIRED_CORS_ORIGINS")"
    elif [[ -n "$DEV_BFF_CORS_ORIGINS" ]]; then
      DEV_BFF_CORS_ORIGINS="$(append_csv_unique "$DEV_BFF_CORS_ORIGINS" "${DEV_BFF_CANONICAL_CORS_ORIGIN:-}")"
      DEV_BFF_CORS_ORIGINS="$(append_csv_unique "$DEV_BFF_CORS_ORIGINS" "${DEV_BFF_REQUIRED_CORS_ORIGINS:-}")"
    fi
    if [[ -z "${DEV_BFF_PUBLIC_HOST+x}" ]]; then DEV_BFF_PUBLIC_HOST="api.dev.mvl-cap.tw"; fi
    if [[ -z "${DEV_FE_PUBLIC_HOST+x}" ]]; then DEV_FE_PUBLIC_HOST="app.dev.mvl-cap.tw"; fi
    if [[ -z "${DEV_FE_STATIC_ROOT+x}" ]]; then DEV_FE_STATIC_ROOT="/var/www/pantheon-dev-fe"; fi
    ;;
  staging-live)
    [[ "$COMPONENT" == "auto" ]] && COMPONENT="all"
    case "$COMPONENT" in
      control|exec|all) ;;
      *) error "staging-live supports --component control, exec, or all" ;;
    esac
    if [[ -n "${STAGING_BFF_CORS_ORIGINS:-}" && -n "${STAGING_BFF_CANONICAL_CORS_ORIGIN:-}" ]]; then
      STAGING_BFF_CORS_ORIGINS="$(append_csv_unique "$STAGING_BFF_CORS_ORIGINS" "$STAGING_BFF_CANONICAL_CORS_ORIGIN")"
    elif [[ -z "${STAGING_BFF_CORS_ORIGINS+x}" && -n "${STAGING_BFF_CANONICAL_CORS_ORIGIN:-}" ]]; then
      STAGING_BFF_CORS_ORIGINS="$STAGING_BFF_CANONICAL_CORS_ORIGIN"
    fi
    ;;
  *)
    error "--environment must be dev or staging-live"
    ;;
esac

validate_target_selection

case "$DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED" in
  true|false) ;;
  *) error "DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED must be true or false" ;;
esac
if [[ "$DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED" == "true" && (
  "$DEPLOY_ENV" != "dev" ||
  "$COMPONENT" != "root" ||
  "$DEV_BFF_AUTH_MODE" != "strict" ||
  "$DEV_BFF_AUTH_STUB" == "true" ||
  "$ALLOW_DIRTY" == "true"
) ]]; then
  error "PPL-ALLOC-009 dev proof requires a clean dev/root deploy with strict non-stub auth"
fi

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
  info "dev_deploy_deadline_seconds=${DEV_DEPLOY_DEADLINE_SECONDS}"
  info "dev_bff_cors_origins=${DEV_BFF_CORS_ORIGINS:-}"
  info "dev_bff_public_host=${DEV_BFF_PUBLIC_HOST:-}"
  info "dev_fe_public_host=${DEV_FE_PUBLIC_HOST:-}"
  info "dev_fe_static_root=${DEV_FE_STATIC_ROOT:-}"
  info "dev_bff_auth_stub=${DEV_BFF_AUTH_STUB}"
  info "dev_bff_auth_mode=${DEV_BFF_AUTH_MODE}"
  info "dev_ppl_alloc_009_dev_proof_enabled=${DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED}"
  info "dev_bff_mfa_required=${DEV_BFF_MFA_REQUIRED}"
  info "dev_bff_jwt_secret_configured=$([[ -n "$DEV_BFF_JWT_SECRET" ]] && echo true || echo false)"
  info "dev_bff_jwt_issuer_configured=$([[ -n "$DEV_BFF_JWT_ISSUER" ]] && echo true || echo false)"
  info "dev_bff_jwt_audience_configured=$([[ -n "$DEV_BFF_JWT_AUDIENCE" ]] && echo true || echo false)"
  info "dev_bff_jwks_configured=$([[ -n "$DEV_BFF_JWKS_URI" || -n "$DEV_BFF_OIDC_DISCOVERY_URL" ]] && echo true || echo false)"
  info "dev_bff_external_oidc_contract_configured=$([[ -n "$DEV_BFF_OIDC_ISSUER" && -n "$DEV_BFF_OIDC_AUDIENCE" ]] && echo true || echo false)"
  info "dev_bff_oidc_client_configured=$([[ -n "$DEV_BFF_OIDC_CLIENT_ID" && -n "$DEV_BFF_OIDC_CLIENT_SECRET" ]] && echo true || echo false)"
  for identity in VIEWER APPROVER RISK_OWNER OPERATOR_A OPERATOR_B; do
    id_var="DEV_BFF_DEV_LOGIN_${identity}_CLIENT_ID"
    secret_var="DEV_BFF_DEV_LOGIN_${identity}_CLIENT_SECRET"
    info "dev_bff_dev_login_${identity,,}_configured=$([[ -n "${!id_var}" && -n "${!secret_var}" ]] && echo true || echo false)"
  done
  info "dev_bff_role_claims_configured=$([[ -n "$DEV_BFF_ROLE_CLAIMS" ]] && echo true || echo false)"
  info "dev_bff_role_map_configured=$([[ -n "$DEV_BFF_ROLE_MAP" ]] && echo true || echo false)"
  info "dev_bff_role_map_mode=${DEV_BFF_ROLE_MAP_MODE}"
  info "dev_bff_default_role=${DEV_BFF_DEFAULT_ROLE}"
  info "dev_openclaw_adapter_service_auth_required=${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED:-}"
  info "dev_openclaw_adapter_service_token_configured=$([[ -n "${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN:-}" ]] && echo true || echo false)"
  info "dev_openclaw_claude_code_oauth_token_configured=$([[ -n "${PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN:-}" ]] && echo true || echo false)"
  info "dev_assistant_kernel_enabled=${PANTHEON_ASSISTANT_KERNEL_ENABLED:-}"
  info "dev_assistant_control_mode_store_path=${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-}"
  info "dev_assistant_control_passphrase_hash_configured=$([[ -n "${PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH:-}" ]] && echo true || echo false)"
  info "dev_assistant_control_idle_ttl_seconds=${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-}"
  info "dev_bff_stub_capabilities_configured=$([[ -n "${PANTHEON_BFF_STUB_CAPABILITIES:-}" ]] && echo true || echo false)"
  info "dev_docker_prune=${PANTHEON_DEV_DOCKER_PRUNE}"
  info "dev_compose_profiles=${DEV_COMPOSE_PROFILES:-<default-safe>}"
  info "source_refresh_egress_mode=${SOURCE_REFRESH_EGRESS_MODE}"
  info "source_refresh_allowed_hosts_configured=$([[ -n "${SOURCE_REFRESH_ALLOWED_HOSTS}" ]] && echo true || echo false)"
  info "source_refresh_controller_mode=${SOURCE_REFRESH_CONTROLLER_MODE}"
  info "source_refresh_truth_level=${SOURCE_REFRESH_TRUTH_LEVEL}"
  info "source_refresh_max_ticks=${SOURCE_REFRESH_MAX_TICKS}"
  info "source_refresh_restart_policy=${SOURCE_REFRESH_RESTART_POLICY}"
  info "source_refresh_max_concurrency=${SOURCE_REFRESH_MAX_CONCURRENCY}"
  info "source_refresh_max_records=${SOURCE_REFRESH_MAX_RECORDS}"
  info "management_ai_store_backend=${MANAGEMENT_AI_STORE_BACKEND:-}"
  info "management_ai_store_schema=${MANAGEMENT_AI_STORE_SCHEMA:-}"
  info "management_ai_database_user=${DEV_MANAGEMENT_AI_DB_USER}"
  info "management_ai_database_url_configured=$([[ -n "${MANAGEMENT_AI_DATABASE_URL:-}" ]] && echo true || echo false)"
  info "management_ai_attach_bucket=${PANTHEON_MGMT_AI_ATTACH_BUCKET:-}"
  info "management_ai_attach_location=${DEV_MANAGEMENT_AI_ATTACH_LOCATION}"
  info "staging_exec_health_url=${STAGING_EXEC_HEALTH_URL:-}"
  info "staging_bff_cors_origins=${STAGING_BFF_CORS_ORIGINS:-}"
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
  for identity in VIEWER APPROVER RISK_OWNER OPERATOR_A OPERATOR_B; do
    id_var="DEV_BFF_DEV_LOGIN_${identity}_CLIENT_ID"
    secret_var="DEV_BFF_DEV_LOGIN_${identity}_CLIENT_SECRET"
    if [[ -z "${!id_var}" || -z "${!secret_var}" ]]; then
      error "strict auth cutover requires dedicated ${identity,,} dev-login credentials (${id_var}, ${secret_var}); refusing to deploy without distinct governed proof actors"
    fi
  done
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

if [[ "$DEPLOY_ENV" == "dev" ]]; then
  require_cmd ssh
  [[ -x "$SCRIPT_DIR/dev_vm_ssh.sh" ]] \
    || error "dev direct-SSH transport is missing or not executable: $SCRIPT_DIR/dev_vm_ssh.sh"
else
  require_cmd gcloud
fi

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
  if command -v gcloud >/dev/null 2>&1 \
    && gcloud storage buckets describe "gs://${bucket}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
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
  command_prefix+=" PANTHEON_DEPLOY_RECEIPT_ROOT=$(shell_quote "${PANTHEON_DEPLOY_RECEIPT_ROOT:-}")"
  command_prefix+=" PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH=$(shell_quote "${PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH:-}")"
  command_prefix+=" PANTHEON_DEV_FRONTEND_SHA=$(shell_quote "${PANTHEON_DEV_FRONTEND_SHA:-${FRONTEND_SHA:-}}")"
  command_prefix+=" PANTHEON_DEV_ROLLBACK_BACKEND_SHA=$(shell_quote "${DEV_ROLLBACK_BACKEND_SHA:-}")"
  command_prefix+=" PANTHEON_GITHUB_TOKEN=$(shell_quote "${GITHUB_TOKEN:-}")"
  command_prefix+=" PANTHEON_ALLOW_DIRTY_DEPLOY=$(shell_quote "$ALLOW_DIRTY")"
  command_prefix+=" PANTHEON_ALLOW_EXAMPLE_ENV=$(shell_quote "$ALLOW_EXAMPLE_ENV")"
  command_prefix+=" PANTHEON_DEV_BFF_CORS_ORIGINS=$(shell_quote "${DEV_BFF_CORS_ORIGINS:-}")"
  command_prefix+=" PANTHEON_DEV_BFF_PUBLIC_HOST=$(shell_quote "${DEV_BFF_PUBLIC_HOST:-}")"
  command_prefix+=" PANTHEON_DEV_FE_PUBLIC_HOST=$(shell_quote "${DEV_FE_PUBLIC_HOST:-}")"
  command_prefix+=" PANTHEON_DEV_FE_STATIC_ROOT=$(shell_quote "${DEV_FE_STATIC_ROOT:-}")"
  command_prefix+=" PANTHEON_DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS=$(shell_quote "$DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS")"
  command_prefix+=" PANTHEON_DEV_BFF_AUTH_STUB=$(shell_quote "$DEV_BFF_AUTH_STUB")"
  command_prefix+=" PANTHEON_DEV_BFF_AUTH_MODE=$(shell_quote "$DEV_BFF_AUTH_MODE")"
  command_prefix+=" PANTHEON_DEV_BFF_AUTH_READINESS_TIMEOUT_SECONDS=$(shell_quote "$DEV_BFF_AUTH_READINESS_TIMEOUT_SECONDS")"
  command_prefix+=" PANTHEON_DEV_BFF_AUTH_READINESS_POLL_INTERVAL_SECONDS=$(shell_quote "$DEV_BFF_AUTH_READINESS_POLL_INTERVAL_SECONDS")"
  command_prefix+=" PANTHEON_DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED=$(shell_quote "$DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED")"
  command_prefix+=" PANTHEON_DEV_BFF_JWT_SECRET=$(shell_quote "$DEV_BFF_JWT_SECRET")"
  command_prefix+=" PANTHEON_DEV_CAPITAL_JWT_SECRET=$(shell_quote "$DEV_BFF_JWT_SECRET")"
  command_prefix+=" PANTHEON_DEV_BFF_JWT_ISSUER=$(shell_quote "$DEV_BFF_JWT_ISSUER")"
  command_prefix+=" PANTHEON_DEV_BFF_JWT_AUDIENCE=$(shell_quote "$DEV_BFF_JWT_AUDIENCE")"
  command_prefix+=" PANTHEON_DEV_BFF_JWKS_URI=$(shell_quote "$DEV_BFF_JWKS_URI")"
  command_prefix+=" PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL=$(shell_quote "$DEV_BFF_OIDC_DISCOVERY_URL")"
  command_prefix+=" PANTHEON_DEV_BFF_OIDC_ISSUER=$(shell_quote "$DEV_BFF_OIDC_ISSUER")"
  command_prefix+=" PANTHEON_DEV_BFF_OIDC_AUDIENCE=$(shell_quote "$DEV_BFF_OIDC_AUDIENCE")"
  command_prefix+=" PANTHEON_DEV_BFF_OIDC_CLIENT_ID=$(shell_quote "$DEV_BFF_OIDC_CLIENT_ID")"
  command_prefix+=" PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET=$(shell_quote "$DEV_BFF_OIDC_CLIENT_SECRET")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID=$(shell_quote "$DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET=$(shell_quote "$DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID=$(shell_quote "$DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET=$(shell_quote "$DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID=$(shell_quote "$DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET=$(shell_quote "$DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID=$(shell_quote "$DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET=$(shell_quote "$DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID=$(shell_quote "$DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET=$(shell_quote "$DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET")"
  command_prefix+=" PANTHEON_DEV_BFF_MFA_REQUIRED=$(shell_quote "$DEV_BFF_MFA_REQUIRED")"
  command_prefix+=" PANTHEON_DEV_BFF_MFA_CLAIMS=$(shell_quote "$DEV_BFF_MFA_CLAIMS")"
  command_prefix+=" PANTHEON_DEV_BFF_MFA_VALUES=$(shell_quote "$DEV_BFF_MFA_VALUES")"
  command_prefix+=" PANTHEON_DEV_BFF_REQUIRE_EMAIL_VERIFIED=$(shell_quote "$DEV_BFF_REQUIRE_EMAIL_VERIFIED")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED=$(shell_quote "$DEV_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED=$(shell_quote "$DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED=$(shell_quote "$DEV_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED=$(shell_quote "$DEV_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED=$(shell_quote "$DEV_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED")"
  command_prefix+=" PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED=$(shell_quote "$DEV_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED")"
  command_prefix+=" PANTHEON_DEV_BFF_ROLE_CLAIMS=$(shell_quote "$DEV_BFF_ROLE_CLAIMS")"
  command_prefix+=" PANTHEON_DEV_BFF_ROLE_MAP=$(shell_quote "$DEV_BFF_ROLE_MAP")"
  command_prefix+=" PANTHEON_DEV_BFF_ROLE_MAP_MODE=$(shell_quote "$DEV_BFF_ROLE_MAP_MODE")"
  command_prefix+=" PANTHEON_DEV_BFF_DEFAULT_ROLE=$(shell_quote "$DEV_BFF_DEFAULT_ROLE")"
  command_prefix+=" PANTHEON_DEV_BFF_TENANT_ID=$(shell_quote "$DEV_BFF_TENANT_ID")"
  command_prefix+=" PANTHEON_DEV_BFF_ALLOWED_TENANTS=$(shell_quote "$DEV_BFF_ALLOWED_TENANTS")"
  command_prefix+=" PANTHEON_ASSISTANT_KERNEL_ENABLED=$(shell_quote "${PANTHEON_ASSISTANT_KERNEL_ENABLED:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH=$(shell_quote "${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH=$(shell_quote "${PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS=$(shell_quote "${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-}")"
  command_prefix+=" PANTHEON_BFF_STUB_CAPABILITIES=$(shell_quote "${PANTHEON_BFF_STUB_CAPABILITIES:-}")"
  command_prefix+=" PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN=$(shell_quote "${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN:-}")"
  command_prefix+=" PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED=$(shell_quote "${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED:-}")"
  command_prefix+=" PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN=$(shell_quote "${PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN:-}")"
  command_prefix+=" PANTHEON_DEV_DOCKER_PRUNE=$(shell_quote "${PANTHEON_DEV_DOCKER_PRUNE:-false}")"
  command_prefix+=" PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE=$(shell_quote "${PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE:-false}")"
  command_prefix+=" PANTHEON_DEV_COMPOSE_PROFILES=$(shell_quote "${DEV_COMPOSE_PROFILES}")"
  command_prefix+=" PANTHEON_EXTERNAL_EGRESS=$(shell_quote "${SOURCE_REFRESH_EGRESS_MODE}")"
  command_prefix+=" PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=$(shell_quote "${SOURCE_REFRESH_ALLOWED_HOSTS}")"
  command_prefix+=" SOURCE_INGEST_CONTROLLER_MODE=$(shell_quote "${SOURCE_REFRESH_CONTROLLER_MODE}")"
  command_prefix+=" SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL=$(shell_quote "${SOURCE_REFRESH_TRUTH_LEVEL}")"
  command_prefix+=" SOURCE_INGEST_CONTROLLER_MAX_TICKS=$(shell_quote "${SOURCE_REFRESH_MAX_TICKS}")"
  command_prefix+=" SOURCE_INGEST_CONTROLLER_RESTART_POLICY=$(shell_quote "${SOURCE_REFRESH_RESTART_POLICY}")"
  command_prefix+=" SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY=$(shell_quote "${SOURCE_REFRESH_MAX_CONCURRENCY}")"
  command_prefix+=" SOURCE_INGEST_MAX_RECORDS=$(shell_quote "${SOURCE_REFRESH_MAX_RECORDS}")"
  command_prefix+=" SOURCE_INGEST_BOUNDED_CONNECTOR_ID=$(shell_quote "${SOURCE_REFRESH_CONNECTOR_ID}")"
  command_prefix+=" SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS=$(shell_quote "${SOURCE_REFRESH_TIMEOUT_SECONDS}")"
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
  command_prefix+=" PANTHEON_STAGING_EXEC_HEALTH_URL=$(shell_quote "${STAGING_EXEC_HEALTH_URL:-}")"
  command_prefix+=" PANTHEON_STAGING_BFF_CORS_ORIGINS=$(shell_quote "${STAGING_BFF_CORS_ORIGINS:-}")"
  command_prefix+=" bash -s"

  local deadline_seconds="${DEV_DEPLOY_DEADLINE_SECONDS:-7200}"
  local -a remote_command
  if [[ "$DEPLOY_ENV" == "dev" ]]; then
    info "direct ssh ${REMOTE_USER}@${DEV_DEPLOY_SSH_HOST} component=${remote_component} sha=${DEPLOY_SHA} (deadline=${deadline_seconds}s)"
    export DEV_DEPLOY_SSH_HOST REMOTE_USER
    remote_command=("$SCRIPT_DIR/dev_vm_ssh.sh" exec "$command_prefix")
  else
    info "gcloud ssh ${vm} (${zone}) component=${remote_component} sha=${DEPLOY_SHA} (deadline=${deadline_seconds}s)"
    remote_command=(
      gcloud compute ssh "${REMOTE_USER}@${vm}"
      --project="${PROJECT_ID}"
      --zone="${zone}"
      --quiet
      --command="${command_prefix}"
    )
  fi

  python3 -c '
import os
import signal
import subprocess
import sys
import time

deadline_seconds = float(sys.argv[1])
cmd = sys.argv[2:]
stdin_data = sys.stdin.buffer.read()

def terminate_pg(pgid):
    try:
        os.killpg(pgid, signal.SIGSTOP)
        os.killpg(pgid, signal.SIGTERM)
        os.killpg(pgid, signal.SIGCONT)
    except ProcessLookupError:
        return
    for _ in range(20):
        time.sleep(0.25)
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass

proc = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE,
    start_new_session=True,
)
pgid = proc.pid

def handle_sig(sig, frame):
    terminate_pg(pgid)
    sys.exit(128 + sig)

signal.signal(signal.SIGINT, handle_sig)
signal.signal(signal.SIGTERM, handle_sig)

try:
    proc.communicate(input=stdin_data, timeout=deadline_seconds)
    exit_code = proc.returncode
except subprocess.TimeoutExpired:
    terminate_pg(pgid)
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass
    print(
        f"[nonprod-deploy] ERROR: deploy command exceeded deadline of {int(deadline_seconds)}s; direct SSH process group terminated",
        file=sys.stderr,
    )
    sys.exit(75)
except Exception:
    terminate_pg(pgid)
    raise

sys.exit(exit_code)
' "${deadline_seconds}" "${remote_command[@]}" <<'REMOTE'
set -euo pipefail

info() {
  echo "[remote-deploy] $*"
}

error() {
  echo "[remote-deploy] ERROR: $*" >&2
  exit 1
}

validate_source_refresh_profile() {
  local selected="false"
  case ",${PANTHEON_DEV_COMPOSE_PROFILES:-}," in
    *,source-ingest-scheduler,*) selected="true" ;;
  esac

  if [[ "$selected" != "true" ]]; then
    [[ "${PANTHEON_EXTERNAL_EGRESS:-deny}" == "deny" ]] \
      || error "external egress must remain deny when source-ingest-scheduler is not selected"
    [[ -z "${PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS:-}" ]] \
      || error "external host allowlist requires the bounded source-ingest-scheduler profile"
    [[ "${SOURCE_INGEST_CONTROLLER_MODE:-}" == "reconcile_only" ]] \
      || error "default source-ingest owner must use reconcile_only mode"
    [[ "${SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL:-}" == "scheduled_tick" ]] \
      || error "default source-ingest owner must use scheduled_tick truth"
    [[ "${SOURCE_INGEST_CONTROLLER_MAX_TICKS:-}" == "0" ]] \
      || error "default source-ingest owner must remain unbounded"
    [[ "${SOURCE_INGEST_CONTROLLER_RESTART_POLICY:-}" == "unless-stopped" ]] \
      || error "default source-ingest owner must use unless-stopped restart policy"
    return 0
  fi

  [[ "${SOURCE_INGEST_CONTROLLER_MODE:-}" == "reconcile_and_pull" ]] \
    || error "bounded source refresh requires reconcile_and_pull mode"
  [[ "${SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL:-}" == "reconciled_live_proof" ]] \
    || error "bounded source refresh requires reconciled_live_proof truth"
  [[ "${SOURCE_INGEST_CONTROLLER_RESTART_POLICY:-}" == "no" ]] \
    || error "bounded source refresh must not restart after its finite tick budget"
  [[ "${PANTHEON_EXTERNAL_EGRESS:-deny}" == "allowlist" ]] \
    || error "source-ingest-scheduler requires PANTHEON_EXTERNAL_EGRESS=allowlist"
  [[ -n "${PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS:-}" ]] \
    || error "source-ingest-scheduler requires a reviewed exact host allowlist"
  [[ "${SOURCE_INGEST_CONTROLLER_MAX_TICKS:-}" =~ ^[0-9]+$ ]] \
    && (( SOURCE_INGEST_CONTROLLER_MAX_TICKS >= 1 && SOURCE_INGEST_CONTROLLER_MAX_TICKS <= 24 )) \
    || error "SOURCE_INGEST_CONTROLLER_MAX_TICKS must be between 1 and 24"
  [[ "${SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY:-}" =~ ^[0-9]+$ ]] \
    && (( SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY >= 1 && SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY <= 4 )) \
    || error "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY must be between 1 and 4"
  [[ "${SOURCE_INGEST_MAX_RECORDS:-}" =~ ^[0-9]+$ ]] \
    && (( SOURCE_INGEST_MAX_RECORDS >= 1 && SOURCE_INGEST_MAX_RECORDS <= 500 )) \
    || error "SOURCE_INGEST_MAX_RECORDS must be between 1 and 500"
  [[ "${SOURCE_INGEST_BOUNDED_CONNECTOR_ID:-}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || error "SOURCE_INGEST_BOUNDED_CONNECTOR_ID must contain one exact connector id"
  [[ "${SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS:-}" =~ ^[0-9]+$ ]] \
    && (( SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS >= 30 && SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS <= 3600 )) \
    || error "SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS must be between 30 and 3600"

  python3 - "${PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS}" "${SOURCE_INGEST_BOUNDED_CONNECTOR_ID}" <<'PY'
import sys

from services.external_egress import allowed_hosts

hosts = allowed_hosts(
    {
        "PANTHEON_EXTERNAL_EGRESS": "allowlist",
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": sys.argv[1],
    }
)
if not hosts:
    raise SystemExit("source refresh exact host allowlist is empty")
if sys.argv[2] == "tw-twse-tpex-official-market":
    required = {"openapi.twse.com.tw", "www.twse.com.tw", "www.tpex.org.tw"}
    missing = sorted(required - hosts)
    if missing:
        raise SystemExit(
            "official TWSE/TPEx refresh requires exact hosts: " + ",".join(sorted(required))
        )
print(f"validated {len(hosts)} exact source refresh hosts")
PY
  export SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS="${SOURCE_INGEST_BOUNDED_CONNECTOR_ID}"
  export SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS="${SOURCE_INGEST_BOUNDED_CONNECTOR_ID}"
}

# One required scheduled/async worker per twelve-loop lane. The deploy fails
# closed if the selected profile set does not resolve every one of these, so a
# narrowed PANTHEON_DEV_COMPOSE_PROFILES can no longer silently deactivate a
# loop. source-ingest-scheduler and source-ingest-agora-projector are
# deliberately absent: they stay opt-in behind the bounded egress profile that
# validate_source_refresh_profile guards.
REQUIRED_LOOP_WORKERS=(
  # source_ingestion
  source-ingest
  # strategy_distillation
  strategy-distillation-worker
  # alpha_replication
  alpha-replication-worker
  # persona_teaching
  training-session-svc
  training-session-preview-worker
  # agora_interaction_evidence
  policy-learning-svc
  agora-interaction-worker
  # human_imitation_shadow_evaluation
  policy-learning-shadow-eval-scheduler
  # consultation
  consultation-svc
  # promotion_deployment
  deployment
  deployment-outbox-consumer
  runtime-manager
  # capital_pool_execution
  broker
  capital
  paper-fleet-reconciler
  paper-signal-producer
  # telemetry_reconciliation
  reconciliation-drift-svc
  reconciliation-drift-consumer
  reconciliation-drift-scheduler
  reconciliation-drift-incident-listener
  # evolution
  evolution
  evolution-dispatch-worker
  evolution-daily-sweep-scheduler
  evolution-threshold-sweep-producer
  # bff_health_monitoring
  operator-bff
  loop-run-projector-scheduler
  # shared search index behind source + agora reads
  search-svc
  search-index-scheduler
)

# The legacy static paper runtime duplicates the binding-scoped workers that
# paper-fleet-reconciler owns. Two writers on the same bindings is the exact
# duplicate-worker failure this manifest is supposed to prevent.
FORBIDDEN_DUPLICATE_WORKERS=(
  pantheon-paper-runtime
)

validate_required_loop_workers() {
  local resolved missing=() duplicates=() worker

  resolved="$(
    COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-}" \
      docker compose -p pantheon -f docker-compose.yml config --services
  )" || error "unable to resolve compose services for the selected profiles"

  for worker in "${REQUIRED_LOOP_WORKERS[@]}"; do
    if ! grep -qxF -- "$worker" <<<"$resolved"; then
      missing+=("$worker")
    fi
  done
  for worker in "${FORBIDDEN_DUPLICATE_WORKERS[@]}"; do
    if grep -qxF -- "$worker" <<<"$resolved"; then
      duplicates+=("$worker")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    error "required loop workers not activated by the selected profiles: ${missing[*]}"
  fi
  if (( ${#duplicates[@]} > 0 )); then
    error "duplicate legacy workers activated alongside the loop manifest: ${duplicates[*]}"
  fi

  info "required_loop_workers=${#REQUIRED_LOOP_WORKERS[@]} all activated; no duplicate legacy workers"
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

wait_for_exact_bff_lifecycle_readiness() {
  local url="$1"

  # Full root replacements must re-stamp the lifecycle bundle when the
  # deployment SHA changes. The current hosted state is about 3 GiB and the
  # fail-closed atomic republish has taken about 8 minutes before the exact SHA
  # becomes observable. Give that deployment-pending phase a bounded 600-second
  # window; acceptance still requires the exact SHA and accepted live truth.
  # Once exact trusted recovery is observable, grant only monotonic recovery a
  # bounded 180-second extension (780 seconds total).
  # If an exact/live projector briefly becomes generally unavailable during
  # dependency warm-up, retain that exact evidence for at most 30 seconds and
  # require /livez or /bff/version to keep proving the same running target.
  # Compose separately gates /livez, so each invocation gets its own
  # lifecycle-readiness acceptance budget; image/process startup cannot
  # consume it.
  python3 scripts/wait_for_bff_lifecycle_readiness.py \
    --url "$url" \
    --expected-deployment-sha "${PANTHEON_DEPLOY_SHA}" \
    --initial-timeout-seconds 600 \
    --recovery-extension-seconds 180 \
    --stalled-timeout-seconds 45 \
    --poll-interval-seconds 2 \
    --request-timeout-seconds 2 \
    --exact-evidence-max-age-seconds 30
}

wait_for_bounded_source_refresh_service() {
  local service="$1"
  local timeout_seconds="${SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS}"
  local started_epoch
  local container_id=""
  local state=""
  local exit_code=""
  started_epoch="$(date +%s)"

  while (( $(date +%s) - started_epoch < timeout_seconds )); do
    container_id="$(docker compose -p pantheon -f docker-compose.yml ps -a -q "$service" 2>/dev/null || true)"
    if [[ -z "$container_id" ]]; then
      sleep 5
      continue
    fi
    state="$(docker inspect --format '{{.State.Status}}' "$container_id")"
    case "$state" in
      exited)
        exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$container_id")"
        [[ "$exit_code" == "0" ]] \
          || error "bounded source refresh service ${service} exited with code ${exit_code}"
        printf '%s\n' "$container_id"
        return 0
        ;;
      dead)
        error "bounded source refresh service ${service} entered dead state"
        ;;
      created|running|restarting)
        sleep 5
        ;;
      *)
        error "bounded source refresh service ${service} has unexpected state ${state}"
        ;;
    esac
  done

  error "bounded source refresh service ${service} did not reach terminal state within ${timeout_seconds}s"
}

resolve_bounded_source_refresh_active_symbols() {
  local priority_symbols=""

  case ",${PANTHEON_DEV_COMPOSE_PROFILES:-}," in
    *,source-ingest-scheduler,*) ;;
    *)
      export SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS=""
      return 0
      ;;
  esac

  if ! priority_symbols="$(
    docker compose -p pantheon -f docker-compose.yml run --rm --no-deps -T \
      --entrypoint python runtime-manager - <<'PY'
import json
import os
import re
from pathlib import Path


path = Path(os.environ.get("PANTHEON_RUNTIME_BINDING_STORE_PATH", "/data/runtime/runtime_bindings.json"))
if not path.exists():
    print("")
    raise SystemExit(0)
try:
    bindings = json.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"active RuntimeBinding store is unreadable: {exc}") from exc
if not isinstance(bindings, list) or any(not isinstance(binding, dict) for binding in bindings):
    raise SystemExit("active RuntimeBinding store must contain a JSON list of objects")

symbols = []
for binding in bindings:
    mode = str(binding.get("deployment_mode") or binding.get("execution_mode") or "").strip().lower()
    status = str(binding.get("status") or "").strip().lower()
    if mode != "paper" or status != "active":
        continue
    metadata = binding.get("metadata") if isinstance(binding.get("metadata"), dict) else {}
    symbol = str(binding.get("symbol") or metadata.get("symbol") or "").strip().upper()
    policy = binding.get("market_data_policy") or metadata.get("market_data_policy")
    if not symbol:
        if policy:
            binding_id = str(binding.get("binding_id") or "<unknown>")
            raise SystemExit(f"active paper RuntimeBinding {binding_id} requires market data but has no symbol")
        continue
    if re.fullmatch(r"[A-Z0-9_-]+\.(?:TW|TWSE|TWO|TPEX)", symbol) is None:
        continue
    if symbol not in symbols:
        symbols.append(symbol)

print(",".join(symbols))
PY
  )"; then
    error "could not resolve active paper RuntimeBinding symbols for bounded source refresh"
  fi
  [[ -z "$priority_symbols" || "$priority_symbols" =~ ^[A-Z0-9_.-]+(,[A-Z0-9_.-]+)*$ ]] \
    || error "resolved active paper RuntimeBinding symbols are malformed"
  export SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS="$priority_symbols"
  if [[ -n "$priority_symbols" ]]; then
    info "bounded source refresh prioritizing active paper symbols: ${priority_symbols}"
  else
    info "bounded source refresh found no active paper Taiwan market symbols"
  fi
}

verify_bounded_source_refresh_readback() {
  local deploy_started_at="$1"
  local scheduler_container_id=""
  local projector_container_id=""
  local evidence_dir=""

  case ",${PANTHEON_DEV_COMPOSE_PROFILES:-}," in
    *,source-ingest-scheduler,*) ;;
    *) return 0 ;;
  esac

  scheduler_container_id="$(wait_for_bounded_source_refresh_service source-ingest-scheduler)" \
    || return 1
  projector_container_id="$(wait_for_bounded_source_refresh_service source-ingest-agora-projector)" \
    || return 1
  evidence_dir="$(mktemp -d)"
  curl -fsS --get \
    --data-urlencode "connector_id=${SOURCE_INGEST_BOUNDED_CONNECTOR_ID}" \
    http://127.0.0.1:18097/api/source-ingest/receipts \
    -o "${evidence_dir}/receipts.json" \
    || { rm -rf "${evidence_dir}"; return 1; }
  curl -fsS http://127.0.0.1:18097/api/source-ingest/controller/readback \
    -o "${evidence_dir}/readback.json" \
    || { rm -rf "${evidence_dir}"; return 1; }
  docker cp \
    "${projector_container_id}:/data/bff/agora_watchlist.json" \
    "${evidence_dir}/agora_watchlist.json" \
    || { rm -rf "${evidence_dir}"; return 1; }

  if ! python3 - \
    "${SOURCE_INGEST_BOUNDED_CONNECTOR_ID}" \
    "${deploy_started_at}" \
    "${evidence_dir}/receipts.json" \
    "${evidence_dir}/readback.json" \
    "${evidence_dir}/agora_watchlist.json" \
    "${SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS:-}" <<'PY'
import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from services.execution.market_snapshot_admission import (
    evaluate_taiwan_market_freshness,
    is_taiwan_symbol,
)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


connector_id, deploy_started_at, receipts_path, readback_path, projection_path, priority_csv = sys.argv[1:]
started = timestamp(deploy_started_at)
receipts = load(receipts_path).get("receipts") or []
candidates = [
    receipt
    for receipt in receipts
    if (
        isinstance(receipt, dict)
        and receipt.get("connector_id") == connector_id
        and receipt.get("status") == "completed"
        and receipt.get("typed_failure") is None
        and receipt.get("source_timestamp")
        and receipt.get("source_timestamp_status") == "valid"
        and timestamp(receipt.get("finished_at") or receipt.get("created_at")) >= started
    )
]
if not candidates:
    raise SystemExit("bounded source refresh produced no new successful source-time-valid receipt")
receipt = max(candidates, key=lambda item: timestamp(item.get("finished_at") or item.get("created_at")))
run_id = str(receipt["ingest_run_id"])

connectors = load(readback_path).get("connectors") or []
connector = next(
    (item for item in connectors if isinstance(item, dict) and item.get("connector_id") == connector_id),
    None,
)
if connector is None:
    raise SystemExit("bounded source refresh connector is absent from controller readback")
freshness = connector.get("freshness") if isinstance(connector.get("freshness"), dict) else {}
latest_receipt = freshness.get("latest_receipt") if isinstance(freshness.get("latest_receipt"), dict) else {}
latest_record = connector.get("latest_source_record") if isinstance(connector.get("latest_source_record"), dict) else {}
provenance = latest_record.get("provenance") if isinstance(latest_record.get("provenance"), dict) else {}
source_id = str(latest_record.get("source_id") or "")
if latest_receipt.get("ingest_run_id") != run_id:
    raise SystemExit("controller freshness does not bind the new ingest receipt")
if freshness.get("source_timestamp_status") != "valid":
    raise SystemExit("controller freshness does not report valid provider source time")
if provenance.get("source_ingest_run_id") != run_id or not source_id:
    raise SystemExit("controller source record does not bind the new ingest run")

projection = load(projection_path)
rows = projection.values() if isinstance(projection, dict) else []
projected = next(
    (
        row
        for row in rows
        if (
            isinstance(row, dict)
            and row.get("connectorId") == connector_id
            and row.get("ingestRunId") == run_id
            and row.get("sourceId") == source_id
        )
    ),
    None,
)
if projected is None:
    raise SystemExit("Agora projection does not bind the new receipt/run/source")
projected_freshness = projected.get("freshness") if isinstance(projected.get("freshness"), dict) else {}
if (
    not projected.get("asOf")
    or projected_freshness.get("sourceTimestamp") != projected.get("asOf")
    or projected_freshness.get("sourceTimeStatus") != "valid"
    or projected_freshness.get("status") not in {"fresh", "stale"}
    or not isinstance(projected_freshness.get("stale"), bool)
):
    raise SystemExit("Agora projection is missing explicit source-time/freshness truth")


def canonical_taiwan_symbol(value):
    symbol, suffix = value.upper().rsplit(".", 1)
    if suffix in {"TW", "TWSE"}:
        return f"{symbol}.TWSE"
    if suffix in {"TWO", "TPEX"}:
        return f"{symbol}.TPEX"
    raise SystemExit(f"unsupported active Taiwan paper symbol: {value}")


for requested_symbol in [item for item in priority_csv.split(",") if item]:
    url = (
        "http://127.0.0.1:18097/api/source-ingest/snapshots/latest?symbol="
        + urllib.parse.quote(requested_symbol, safe="")
    )
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            snapshot = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise SystemExit(
            f"active paper snapshot is unavailable for {requested_symbol}: {exc}"
        ) from exc
    execution_symbol = requested_symbol.upper()
    canonical_symbol = canonical_taiwan_symbol(execution_symbol)
    if snapshot.get("symbol") != execution_symbol:
        raise SystemExit(
            f"active paper snapshot identity mismatch for {requested_symbol}: "
            f"{snapshot.get('symbol')!r} != {execution_symbol!r}"
        )
    closes = snapshot.get("closes")
    if (
        not isinstance(closes, list)
        or len(closes) < 2
        or any(
            isinstance(close, bool)
            or not isinstance(close, (int, float))
            or not math.isfinite(float(close))
            or float(close) <= 0
            for close in closes
        )
    ):
        raise SystemExit(
            f"active paper snapshot requires at least two finite official closes "
            f"for {requested_symbol}: closes={closes!r}"
        )
    event_time = timestamp(snapshot.get("event_time"))
    now_dt = datetime.now(timezone.utc)
    age_seconds = (now_dt - event_time).total_seconds()
    if age_seconds < 0:
        raise SystemExit(
            f"active paper snapshot event_time is in the future for {requested_symbol}: "
            f"event_time={snapshot.get('event_time')} age_seconds={age_seconds:.6f}"
        )
    lineage = snapshot.get("lineage") if isinstance(snapshot.get("lineage"), dict) else {}
    connector_ids = lineage.get("connector_ids") if isinstance(lineage.get("connector_ids"), list) else []
    source_ids = lineage.get("source_ids") if isinstance(lineage.get("source_ids"), list) else []
    source_venue = "TWSE" if canonical_symbol.endswith(".TWSE") else "TPEx"
    expected_prefix = f"tw-official:tw_price_daily:{source_venue}:"
    if connector_id not in connector_ids or not any(str(source_id).startswith(expected_prefix) for source_id in source_ids):
        raise SystemExit(f"active paper snapshot lacks official exchange lineage for {requested_symbol}")
    if is_taiwan_symbol(canonical_symbol):
        # Governed Taiwan (Asia/Taipei) market-session freshness rule shared
        # with market_snapshot_admission: admits the latest official close
        # across weekends/evidenced holidays instead of a flat 24h gate,
        # while still fail-closing weekday staleness, a stale refresh
        # receipt, unverifiable calendar evidence, and non-official lineage.
        observed_at_raw = snapshot.get("observed_at")
        refresh_dt = timestamp(observed_at_raw) if observed_at_raw else None
        ev = snapshot.get("calendar_evidence")
        if ev is None and isinstance(lineage, dict):
            ev = lineage.get("calendar_evidence")
        tw_ok, tw_reason, tw_detail = evaluate_taiwan_market_freshness(
            event_time_dt=event_time,
            now_dt=now_dt,
            refresh_receipt_dt=refresh_dt,
            lineage=lineage,
            max_refresh_age_seconds=86400,
            calendar_evidence=ev,
        )
        if not tw_ok:
            raise SystemExit(
                f"active paper snapshot failed Taiwan market-session freshness for "
                f"{requested_symbol}: {tw_reason} {tw_detail}"
            )
    elif age_seconds > 86400:
        raise SystemExit(
            f"active paper snapshot is outside 24h for {requested_symbol}: "
            f"event_time={snapshot.get('event_time')} age_seconds={int(age_seconds)}"
        )
    print(
        "active paper snapshot accepted "
        f"execution={execution_symbol} official={canonical_symbol} "
        f"event_time={snapshot.get('event_time')} closes={len(closes)} "
        f"snapshot={snapshot.get('snapshot_id')}"
    )
print(
    "bounded source refresh accepted "
    f"connector={connector_id} run={run_id} source={source_id} "
    f"freshness={projected_freshness['status']}"
)
PY
  then
    rm -rf "${evidence_dir}"
    return 1
  fi
  rm -rf "${evidence_dir}"
  info "bounded source refresh services exited zero and receipt/projection readback advanced"
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

assert_ppl_alloc_009_dev_proof_gate() {
  local expected="PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED=${PANTHEON_DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED}"
  docker inspect pantheon-operator-bff-1 \
    --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep -F -x "${expected}" >/dev/null \
    || error "operator-bff PPL-ALLOC-009 dev proof posture does not match ${expected}"
  info "operator-bff PPL-ALLOC-009 dev proof posture verified: ${PANTHEON_DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED}"
}

assert_dedicated_dev_login_identity() {
  local base_url="$1"
  local expected_identity="$2"
  local expected_role="$3"
  local client_id="$4"
  local client_secret="$5"
  local login_body
  local login_payload

  login_body="$(python3 -c 'import json,sys; print(json.dumps({"grant_type":"client_credentials","client_id":sys.argv[1],"client_secret":sys.argv[2]}))' \
    "$client_id" "$client_secret")"
  login_payload="$(curl -fsS -X POST "${base_url}/bff/auth/dev-login" \
    -H 'Content-Type: application/json' -d "$login_body")" \
    || return 1
  python3 -c '
import base64
import json
import sys

expected_identity, expected_role, raw = sys.argv[1:]
payload = json.loads(raw)
assert (payload.get("meta") or {}).get("identity") == expected_identity, payload.get("meta")
token = payload["access_token"]
encoded = token.split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
assert set(claims.get("roles") or []) == {expected_role}, claims.get("roles")
assert claims.get("mfa_verified") is True, "issued token is missing MFA verification"
subject = str(claims.get("sub") or "")
assert subject, "issued token is missing sub"
print(subject)
' "$expected_identity" "$expected_role" "$login_payload"
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

  if [[ -z "${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID}" || -z "${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET}" ]]; then
    error "strict auth cutover requires dedicated operator A dev-login credentials on the deploy runner; none were provided"
  fi

  info "asserting authenticated dev-login round trip succeeds"
  local login_payload
  local login_body
  login_body="$(python3 -c 'import json,sys; print(json.dumps({"grant_type":"client_credentials","client_id":sys.argv[1],"client_secret":sys.argv[2]}))' \
    "${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID}" "${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET}")"
  login_payload="$(curl -fsS -X POST "${base_url}/bff/auth/dev-login" \
    -H 'Content-Type: application/json' \
    -d "${login_body}")" \
    || error "authenticated dev-login round trip failed against ${base_url}/bff/auth/dev-login"
  local access_token
  access_token="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' <<<"$login_payload")"
  curl -fsS "${base_url}/bff/me" -H "Authorization: Bearer ${access_token}" >/dev/null \
    || error "authenticated /bff/me check failed with a freshly issued dev-login token"
  local readiness_timeout="${PANTHEON_DEV_BFF_AUTH_READINESS_TIMEOUT_SECONDS:-${DEV_BFF_AUTH_READINESS_TIMEOUT_SECONDS:-120}}"
  local readiness_poll_interval="${PANTHEON_DEV_BFF_AUTH_READINESS_POLL_INTERVAL_SECONDS:-${DEV_BFF_AUTH_READINESS_POLL_INTERVAL_SECONDS:-2}}"
  local readiness_started
  readiness_started="$(date +%s)"
  local readiness_payload=""
  local readiness_error=""
  local readiness_rc=0

  info "asserting authenticated dev-login and strict browser readiness round trip succeed (bounded timeout=${readiness_timeout}s)"
  while true; do
    readiness_error=""
    if readiness_payload="$(curl -fsS "${base_url}/bff/auth/readiness" \
      -H "Authorization: Bearer ${access_token}" 2>&1)"; then
      if readiness_error="$(python3 -c '
import json
import sys

expected_sha = sys.argv[1]
try:
    payload = json.loads(sys.argv[2])
except Exception as exc:
    print(f"invalid JSON readiness payload: {exc}")
    sys.exit(1)

data = payload.get("data") or {}
auth = data.get("auth") or {}
source_sha = data.get("sourceCommitSha")
auth_ready = data.get("authReady")
provider_ready = data.get("providerReady")
overall_ready = data.get("ready")
provider_info = data.get("provider")
auth_mode = auth.get("mode")
auth_stub = auth.get("stub")
session_kind = auth.get("sessionKind")
operator_role_ready = auth.get("operatorRoleReady")
interaction_ready = auth.get("interactionCapabilityReady")
verifier_ready = auth.get("verifierReady")
try:
    assert data.get("sourceCommitSha") == expected_sha, f"sourceCommitSha={source_sha!r}, expected {expected_sha!r}"
    assert data.get("authReady") is True, f"authReady={auth_ready!r}"
    assert data.get("ready") is True, f"ready={overall_ready!r}"
    assert auth.get("mode") == "strict", f"auth.mode={auth_mode!r}"
    assert auth.get("stub") is False, f"auth.stub={auth_stub!r}"
    assert auth.get("sessionKind") in {"bearer", "cookie"}, f"auth.sessionKind={session_kind!r}"
    assert auth.get("operatorRoleReady") is True, f"auth.operatorRoleReady={operator_role_ready!r}"
    assert auth.get("interactionCapabilityReady") is True, f"auth.interactionCapabilityReady={interaction_ready!r}"
    assert auth.get("verifierReady") is True, f"auth.verifierReady={verifier_ready!r}"
except AssertionError as err:
    # Deployed configuration cannot change while we poll. Retrying a posture
    # violation only converts a precise "this build came up with the wrong auth
    # posture" into a vague "contract not satisfied within Ns timeout", which
    # sends whoever reads it hunting for a slow dependency that does not exist.
    # Fail immediately with the real reason instead (exit 2 = terminal).
    terminal_markers = ("auth.mode=", "auth.stub=", "auth.sessionKind=")
    detail = str(err)
    if any(marker in detail for marker in terminal_markers):
        print(f"terminal contract violation (retrying cannot change deployed config): {detail}")
        sys.exit(2)
    print(f"contract assertion failed: {detail}")
    sys.exit(1)

# Assistant provider health is observability only, never a release gate. The BFF
# computes ready/authReady without it on purpose (see _bff_auth_readiness: "a
# provider outage or probe failure must never flip a validly authenticated
# strict session to not-ready"), so a provider credential outage must not block
# or roll back an otherwise healthy release.
if provider_ready is not True:
    print(f"advisory: assistant provider not ready (providerReady={provider_ready!r}, provider={provider_info!r})")
' "${PANTHEON_DEPLOY_SHA}" "${readiness_payload}" 2>&1)"; then
        if [[ -n "${readiness_error}" ]]; then
          info "${readiness_error}"
        fi
        break
      else
        readiness_rc=$?
        if (( readiness_rc == 2 )); then
          # Terminal: deployed configuration will not change by polling.
          error "strict browser readiness contract cannot be satisfied by retrying: ${readiness_error}"
        fi
      fi
    else
      readiness_error="strict browser readiness probe failed against ${base_url}/bff/auth/readiness: ${readiness_payload}"
    fi

    if (( $(date +%s) - readiness_started >= readiness_timeout )); then
      error "strict browser readiness contract is not satisfied within ${readiness_timeout}s timeout: ${readiness_error}"
    fi
    sleep "${readiness_poll_interval}"
  done
  info "authenticated dev-login and strict browser readiness round trip succeeded"

  info "asserting five dedicated dev-login identities and distinct subjects"
  local identity
  local identity_upper
  local id_var
  local secret_var
  local expected_role
  local subject
  local dedicated_subjects=()
  for identity in viewer approver risk_owner operator_a operator_b; do
    identity_upper="${identity^^}"
    id_var="PANTHEON_DEV_BFF_DEV_LOGIN_${identity_upper}_CLIENT_ID"
    secret_var="PANTHEON_DEV_BFF_DEV_LOGIN_${identity_upper}_CLIENT_SECRET"
    expected_role="$identity"
    [[ "$identity" == operator_a || "$identity" == operator_b ]] && expected_role="operator"
    subject="$(assert_dedicated_dev_login_identity \
      "$base_url" "$identity" "$expected_role" "${!id_var}" "${!secret_var}")" \
      || error "dedicated ${identity} dev-login round trip or server-bound claims check failed"
    dedicated_subjects+=("$subject")
  done
  python3 -c '
import sys
subjects = sys.argv[1:]
assert len(subjects) == 5, subjects
assert len(set(subjects)) == len(subjects), subjects
' "${dedicated_subjects[@]}" \
    || error "dedicated dev-login identities did not issue five distinct subjects"
  info "dedicated dev-login identities issued five valid distinct subjects"

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
  local root
  if [[ -n "${PANTHEON_DEPLOY_WORKTREE_ROOT:-}" ]]; then
    root="${PANTHEON_DEPLOY_WORKTREE_ROOT}"
  elif [[ "${PANTHEON_DEPLOY_ENV}" == "dev" ]]; then
    root="${HOME}/pantheon-ci-deploy/managed-deploy-worktrees"
  else
    # Preserve the established staging-live layout.
    root="${HOME}/pantheon-ci-deploy"
  fi
  local deploy_dir="${root}/${PANTHEON_DEPLOY_ENV}-${PANTHEON_DEPLOY_COMPONENT}"
  local marker="${root}/.${PANTHEON_DEPLOY_ENV}-${PANTHEON_DEPLOY_COMPONENT}.marker"

  if [[ "${PANTHEON_DEPLOY_ENV}" == "dev" ]]; then
    # BEGIN_DEV_DEPLOY_PATH_ISOLATION_PY
    python3 - "$root" "$deploy_dir" <<'PY'
from pathlib import Path
import sys


def lexical_absolute_path(value: str, label: str, *, must_exist: bool) -> Path:
    raw = Path(value)
    if not raw.is_absolute():
        raise SystemExit(f"{label} must be absolute: {raw}")
    if any(part in {".", ".."} for part in raw.parts):
        raise SystemExit(f"{label} must not contain dot traversal: {raw}")
    for candidate in (raw, *raw.parents):
        if candidate.is_symlink():
            raise SystemExit(
                f"{label} contains a symlink component: {candidate}"
            )
    if must_exist and not raw.is_dir():
        raise SystemExit(f"{label} must be an existing directory: {raw}")
    return raw.resolve(strict=must_exist)


deploy_root = lexical_absolute_path(
    sys.argv[1],
    "deploy worktree root",
    must_exist=False,
)
deploy_dir = lexical_absolute_path(
    sys.argv[2],
    "deploy worktree",
    must_exist=False,
)
deploy_root.mkdir(parents=True, exist_ok=True)
created_deploy_root = lexical_absolute_path(
    str(deploy_root),
    "deploy worktree root",
    must_exist=True,
)
if created_deploy_root != deploy_root:
    raise SystemExit(
        "deploy worktree root changed identity while being created: "
        f"before={deploy_root} after={created_deploy_root}"
    )
PY
    # END_DEV_DEPLOY_PATH_ISOLATION_PY
  else
    mkdir -p "$root"
  fi

  cd "$source_dir"
  info "fetching origin"
  git_fetch_origin_default_refs
  if ! git cat-file -e "${sha}^{commit}" 2>/dev/null; then
    git_fetch_origin "$sha"
  fi

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
  if [[ "${PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE:-false}" != "true" ]]; then
    info "dev Postgres telemetry prune disabled before root deploy"
    return
  fi

  local mgmt_db="${PANTHEON_MANAGEMENT_AI_DB_NAME:-pantheon}"
  local mgmt_schema="${MANAGEMENT_AI_STORE_SCHEMA:-}"

  if [[ -z "$mgmt_schema" || ! "$mgmt_schema" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
    error "refusing to prune telemetry_events: MANAGEMENT_AI_STORE_SCHEMA is empty or invalid SQL identifier: '$mgmt_schema'"
  fi
  if [[ "${mgmt_schema,,}" == "public" ]]; then
    error "refusing to prune telemetry_events: MANAGEMENT_AI_STORE_SCHEMA resolves to canonical public schema"
  fi

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

  # The expensive canonical-preservation sentinel is meaningful only when
  # there is an allow-listed derived telemetry table to truncate.  On the
  # normal dev layout only public.telemetry_events exists; hashing it twice
  # would consume deployment time without releasing any disk.
  local derived_telemetry_table_count
  derived_telemetry_table_count="$(
    docker compose -p pantheon -f docker-compose.yml exec -T \
      -e MGMT_AI_DB_NAME="${mgmt_db}" \
      -e MGMT_AI_SCHEMA="${mgmt_schema}" \
      postgres sh -s <<'REMOTE_DB'
set -euo pipefail
psql -v ON_ERROR_STOP=1 \
  --username "${POSTGRES_USER:-postgres}" \
  --dbname "${MGMT_AI_DB_NAME}" \
  --tuples-only --no-align \
  -v mgmt_schema="${MGMT_AI_SCHEMA}" \
  -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :'mgmt_schema' AND c.relname = 'telemetry_events' AND c.relkind IN ('r', 'p');"
REMOTE_DB
  )"
  derived_telemetry_table_count="${derived_telemetry_table_count//[[:space:]]/}"
  if [[ ! "${derived_telemetry_table_count}" =~ ^[0-9]+$ ]]; then
    error "unable to determine whether ${mgmt_schema}.telemetry_events exists before dev telemetry prune"
  fi
  if [[ "${derived_telemetry_table_count}" == "0" ]]; then
    info "dev Postgres telemetry prune skipped: no derived ${mgmt_schema}.telemetry_events exists"
    return
  fi

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
  target_schema_clean text := lower(trim(target_schema));

  canonical_exists boolean := false;
  canonical_count_before bigint := 0;
  canonical_count_after bigint := 0;
  canonical_min_created_before timestamptz := null;
  canonical_min_created_after timestamptz := null;
  canonical_checksum_before text := 'none';
  canonical_checksum_after text := 'none';

  canonical_matched_count bigint := 0;
  canonical_matched_checksum text := 'none';

  pruned_tables text[] := ARRAY[]::text[];
  sentinel_json jsonb;
BEGIN
  -- 1. Fail closed on empty, invalid identifier, or canonical public schema
  IF target_schema IS NULL OR trim(target_schema) = '' THEN
    RAISE EXCEPTION 'refusing to prune telemetry_events: target schema is empty';
  END IF;

  IF target_schema !~ '^[a-zA-Z_][a-zA-Z0-9_]*$' THEN
    RAISE EXCEPTION 'refusing to prune telemetry_events: target schema "%" is not a valid SQL identifier', target_schema;
  END IF;

  IF target_schema_clean = 'public' THEN
    RAISE EXCEPTION 'refusing to prune telemetry_events: MANAGEMENT_AI_STORE_SCHEMA resolves to canonical public schema';
  END IF;

  -- 2. Capture canonical public telemetry state before mutation
  SELECT EXISTS (
    SELECT 1 FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'telemetry_events' AND c.relkind IN ('r', 'p')
  ) INTO canonical_exists;

  IF canonical_exists THEN
    DROP TABLE IF EXISTS _pantheon_canonical_telemetry_pre;
    CREATE TEMP TABLE _pantheon_canonical_telemetry_pre ON COMMIT DROP AS
      SELECT r.event_id, r.created_at, md5(to_jsonb(r)::text) AS row_digest
      FROM public.telemetry_events r;

    SELECT COUNT(*), MIN(created_at),
           COALESCE(MD5(STRING_AGG(COALESCE(event_id::text, '') || ':' || COALESCE(row_digest, ''), ',' ORDER BY created_at ASC, event_id ASC)), 'empty')
      INTO canonical_count_before, canonical_min_created_before, canonical_checksum_before
      FROM _pantheon_canonical_telemetry_pre;
  END IF;

  -- 3. Discover and prune allow-listed derived tables in target_schema ONLY
  FOR item IN
    SELECT n.nspname AS schema_name, c.relname AS table_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = 'telemetry_events'
      AND c.relkind IN ('r', 'p')
      AND n.nspname = target_schema
  LOOP
    RAISE NOTICE 'truncating %.%', item.schema_name, item.table_name;
    EXECUTE format('TRUNCATE TABLE %I.%I', item.schema_name, item.table_name);
    pruned_tables := array_append(pruned_tables, format('%s.%s', item.schema_name, item.table_name));
  END LOOP;

  -- 4. Capture canonical post-state and enforce concurrency-safe preservation sentinel
  IF canonical_exists THEN
    SELECT COUNT(*), MIN(created_at),
           COALESCE(MD5(STRING_AGG(COALESCE(cur.event_id::text, '') || ':' || COALESCE(md5(to_jsonb(cur)::text), ''), ',' ORDER BY cur.created_at ASC, cur.event_id ASC)), 'empty')
      INTO canonical_count_after, canonical_min_created_after, canonical_checksum_after
      FROM public.telemetry_events cur;

    SELECT COUNT(*),
           COALESCE(MD5(STRING_AGG(COALESCE(cur.event_id::text, '') || ':' || COALESCE(md5(to_jsonb(cur)::text), ''), ',' ORDER BY pre.created_at ASC, pre.event_id ASC)), 'empty')
      INTO canonical_matched_count, canonical_matched_checksum
      FROM public.telemetry_events cur
      JOIN _pantheon_canonical_telemetry_pre pre ON cur.event_id = pre.event_id
      WHERE md5(to_jsonb(cur)::text) = pre.row_digest;

    IF canonical_matched_count != canonical_count_before
       OR canonical_matched_checksum != canonical_checksum_before
       OR canonical_count_after < canonical_count_before
       OR (canonical_count_before > 0 AND canonical_min_created_after > canonical_min_created_before) THEN
      RAISE EXCEPTION 'canonical telemetry drift detected: count before=% matched=% after=%, min_created before=% after=%, checksum before=% matched=% after=%',
        canonical_count_before, canonical_matched_count, canonical_count_after,
        canonical_min_created_before, canonical_min_created_after,
        canonical_checksum_before, canonical_matched_checksum, canonical_checksum_after;
    END IF;

    DROP TABLE IF EXISTS _pantheon_canonical_telemetry_pre;
  END IF;

  -- 5. Build and emit deterministic sentinel artifact
  sentinel_json := jsonb_build_object(
    'canonical_table', 'public.telemetry_events',
    'canonical_row_count_before', canonical_count_before,
    'canonical_row_count_after', canonical_count_after,
    'canonical_matched_count', canonical_matched_count,
    'canonical_min_created_at_before', canonical_min_created_before,
    'canonical_min_created_at_after', canonical_min_created_after,
    'canonical_checksum_before', canonical_checksum_before,
    'canonical_checksum_after', canonical_checksum_after,
    'canonical_matched_checksum', canonical_matched_checksum,
    'derived_schema', target_schema,
    'derived_tables_pruned', to_jsonb(pruned_tables),
    'result', 'preserved'
  );
  RAISE NOTICE 'TELEMETRY_PRUNE_SENTINEL: %', sentinel_json::text;
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
  local paper_signal_producer_container_id=""

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
  info "paper-signal-producer service logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=240 paper-signal-producer || true
  paper_signal_producer_container_id="$(
    docker compose -p pantheon -f docker-compose.yml ps -a -q paper-signal-producer 2>/dev/null || true
  )"
  if [[ -n "$paper_signal_producer_container_id" ]]; then
    info "paper-signal-producer container restart and health state after failure"
    docker inspect --format \
      'status={{.State.Status}} restart_count={{.RestartCount}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}not_configured{{end}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}} error={{json .State.Error}}' \
      "$paper_signal_producer_container_id" || true
  fi
  info "agora-interaction-worker service logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=240 agora-interaction-worker || true
  local agora_container_id=""
  agora_container_id="$(
    docker compose -p pantheon -f docker-compose.yml ps -a -q agora-interaction-worker 2>/dev/null || true
  )"
  if [[ -n "$agora_container_id" ]]; then
    info "agora-interaction-worker container restart and health state after failure"
    docker inspect --format \
      'status={{.State.Status}} restart_count={{.RestartCount}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}not_configured{{end}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}} error={{json .State.Error}}' \
      "$agora_container_id" || true
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

retire_legacy_static_paper_runtime() {
  # paper-fleet-reconciler and paper-signal-producer are now unconditional
  # members of the required loop worker manifest, so co-enabling this profile
  # always means two writers on the same bindings. On the root deploy path
  # validate_required_loop_workers rejects that combination before we get
  # here; this branch survives only for components that skip that guard.
  case ",${PANTHEON_DEV_COMPOSE_PROFILES:-}," in
    *,static-paper-runtime,*)
      info "static paper runtime profile explicitly enabled; leaving compatibility worker active"
      return 0
      ;;
  esac

  info "retiring legacy unbound static paper runtime; fleet reconciler is authoritative"
  COMPOSE_PROFILES=static-paper-runtime \
    docker compose -p pantheon -f docker-compose.yml rm -f -s pantheon-paper-runtime
}

retire_dormant_and_one_off_profile_containers() {
  local active_profiles=",${PANTHEON_DEV_COMPOSE_PROFILES:-},"
  if [[ "${active_profiles}" != *",dormant-smoke,"* ]]; then
    info "retiring inactive dormant-smoke profile containers"
    COMPOSE_PROFILES="dormant-smoke" \
      docker compose -p pantheon -f docker-compose.yml rm -f -s \
        mlflow-dormant-smoke \
        finrl-dormant-smoke \
        rllib-dormant-smoke \
        ray-tune-dormant-smoke \
        qlib-dormant-smoke \
        trl-dormant-smoke \
        experiments-dormant-smoke 2>/dev/null || true
  fi
  if [[ "${active_profiles}" != *",smoke,"* ]]; then
    info "retiring inactive smoke profile containers"
    COMPOSE_PROFILES="smoke" \
      docker compose -p pantheon -f docker-compose.yml rm -f -s smoke-stack 2>/dev/null || true
  fi
  if [[ "${active_profiles}" != *",activation-ready-smoke,"* ]]; then
    info "retiring inactive activation-ready-smoke profile containers"
    COMPOSE_PROFILES="activation-ready-smoke" \
      docker compose -p pantheon -f docker-compose.yml rm -f -s oss-activation-ready-smoke-matrix 2>/dev/null || true
  fi
  if [[ "${active_profiles}" != *",openclaw-activation-ready-e2e,"* ]]; then
    info "retiring inactive openclaw-activation-ready-e2e profile containers"
    COMPOSE_PROFILES="openclaw-activation-ready-e2e" \
      docker compose -p pantheon -f docker-compose.yml rm -f -s openclaw-activation-ready-e2e 2>/dev/null || true
  fi
  if [[ "${active_profiles}" != *",source-search-bounded,"* ]]; then
    info "retiring inactive source-search-bounded profile containers"
    COMPOSE_PROFILES="source-search-bounded" \
      docker compose -p pantheon -f docker-compose.yml rm -f -s source-search-bounded-smoke 2>/dev/null || true
  fi
  if [[ "${active_profiles}" != *",lifecycle-capacity-benchmark,"* ]]; then
    info "retiring inactive lifecycle-capacity-benchmark profile containers"
    COMPOSE_PROFILES="lifecycle-capacity-benchmark" \
      docker compose -p pantheon -f docker-compose.yml rm -f -s lifecycle-projector-capacity-benchmark 2>/dev/null || true
  fi
}

verify_dev_paper_fleet() {
  local attempt
  local status=""

  for attempt in $(seq 1 30); do
    status="$(curl -fsS http://127.0.0.1:18011/readyz 2>/dev/null || true)"
    if python3 -c '
import json
import sys

payload = json.loads(sys.argv[1])
workers = list(payload.get("workers") or [])
assert payload.get("ready") is True
assert payload.get("live") is True
assert payload.get("last_error") in (None, "")
assert payload.get("monitoring_last_error") in (None, "")
assert int(payload.get("cycle_count") or 0) >= 1
assert int(payload.get("worker_count") or 0) == int(payload.get("running_count") or 0)
assert all(worker.get("status") == "running" for worker in workers)
assert all(worker.get("heartbeat_status") == "active" for worker in workers)
' "$status" 2>/dev/null; then
      info "paper fleet reconciler is ready and all desired workers are active"
      printf '%s\n' "$status"
      return 0
    fi
    sleep 2
  done

  info "paper fleet reconciler did not converge"
  docker compose -p pantheon -f docker-compose.yml ps -a paper-fleet-reconciler || true
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=240 paper-fleet-reconciler || true
  printf '%s\n' "$status"
  return 1
}

verify_dev_evolution_daily_sweep() {
  local compose=(docker compose -p pantheon -f docker-compose.yml)
  local attempt
  local logs=""
  local status=""

  for attempt in $(seq 1 30); do
    logs="$("${compose[@]}" logs --no-color --since=10m evolution-daily-sweep-scheduler 2>&1 || true)"
    if printf '%s\n' "$logs" | grep -Fq '"tick":'; then
      # The Evolution API is authenticated in the default Compose topology.
      # Resolve its existing token and tenant inside the container so neither
      # credential is expanded into the host command line or deployment logs.
      status="$("${compose[@]}" exec -T evolution python -c '
import os
import urllib.request

token = os.environ.get("EVOLUTION_AUTH_TOKEN", "").strip()
tenant_id = os.environ.get("EVOLUTION_DEFAULT_TENANT_ID", "").strip()
headers = {}
if token:
    headers["Authorization"] = f"Bearer {token}"
if tenant_id:
    headers["X-Tenant-Id"] = tenant_id
request = urllib.request.Request(
    "http://127.0.0.1:8093/api/evolution/sweep-status",
    headers=headers,
)
with urllib.request.urlopen(request, timeout=5) as response:
    print(response.read().decode("utf-8"))
' 2>/dev/null || true)"
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

verify_exact_component_deployment() {
  local target_services=("$@")
  local expected_sha="${GIT_SHA:-${PANTHEON_DEPLOY_SHA:-${DEPLOY_SHA:-}}}"
  local expected_frontend_sha="${PANTHEON_DEV_FRONTEND_SHA:-${PANTHEON_FE_SHA:-}}"
  local deploy_environment="${PANTHEON_DEPLOY_ENV:-dev}"
  local deploy_component="${PANTHEON_DEPLOY_COMPONENT:-root}"
  local bff_url="${PANTHEON_BFF_BASE_URL:-https://${PANTHEON_DEV_BFF_PUBLIC_HOST:-${DEV_BFF_PUBLIC_HOST:-api.dev.mvl-cap.tw}}}"
  local fe_url="${PANTHEON_FE_BASE_URL:-https://${PANTHEON_DEV_FE_PUBLIC_HOST:-${DEV_FE_PUBLIC_HOST:-app.dev.mvl-cap.tw}}}"
  local receipt_root="${PANTHEON_DEPLOY_RECEIPT_ROOT:-${HOME}/pantheon-ci-deploy/deployment-receipts}"
  local receipt_path="${PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH:-${receipt_root}/${deploy_environment}/${deploy_component}/backend-components-receipt.json}"
  local retired_url_pattern='sslip\.io|104\.155\.223\.192|35\.201\.204\.12|35\.201\.239\.38|34\.81\.75\.241|35\.236\.178\.81'
  if [[ "$bff_url" =~ ${retired_url_pattern} || "$fe_url" =~ ${retired_url_pattern} ]]; then
    printf '[remote-deploy] exact component verification rejects retired target identity in URLs\n' >&2
    return 1
  fi
  local missing=() restarting=() unhealthy=() wrong_sha=() duplicates=() identity_errors=()
  local now deploy_checkout_sha
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  if (( ${#target_services[@]} == 0 )); then
    target_services=("${REQUIRED_LOOP_WORKERS[@]}")
  fi

  if [[ ! "$expected_sha" =~ ^[0-9a-f]{40}$ ]]; then
    printf '[remote-deploy] exact component verification requires a full backend SHA (got %s)\n' "${expected_sha:-empty}" >&2
    return 1
  fi
  if [[ ! "$expected_frontend_sha" =~ ^[0-9a-f]{40}$ ]]; then
    printf '[remote-deploy] exact component verification requires a full frontend SHA (got %s)\n' "${expected_frontend_sha:-empty}" >&2
    return 1
  fi
  deploy_checkout_sha="$(git rev-parse HEAD 2>/dev/null || true)"
  if [[ "$deploy_checkout_sha" != "$expected_sha" ]]; then
    printf '[remote-deploy] deploy checkout SHA %s does not match expected backend SHA %s\n' "${deploy_checkout_sha:-missing}" "$expected_sha" >&2
    return 1
  fi

  local service
  declare -A seen_target_services=()
  for service in "${target_services[@]}"; do
    if [[ -z "$service" || -n "${seen_target_services[$service]:-}" ]]; then
      printf '[remote-deploy] exact component verification received an empty or duplicate service name: %s\n' "${service:-<empty>}" >&2
      return 1
    fi
    seen_target_services["$service"]=1
  done

  info "verifying exact deployment and running state for ${#target_services[@]} component(s); expected_sha=${expected_sha}"

  local container_ids container_id status restart_count health image image_id compose_image_id image_rev cmd entry_json
  local receipt_entries=()

  # Bounded stabilization loop for services starting up
  local attempt max_attempts=15
  for attempt in $(seq 1 $max_attempts); do
    missing=()
    duplicates=()
    restarting=()
    unhealthy=()
    wrong_sha=()
    identity_errors=()
    receipt_entries=()
    local has_starting=false

    for service in "${target_services[@]}"; do
      container_ids="$(
        docker compose -p pantheon -f docker-compose.yml ps -a -q "$service" 2>/dev/null || true
      )"
      if [[ -z "$container_ids" ]]; then
        missing+=("$service")
        continue
      fi
      local count
      count="$(wc -w <<<"$container_ids")"
      if (( count > 1 )); then
        duplicates+=("$service (${count} containers)")
      fi
      container_id="$(head -n1 <<<"$container_ids" | tr -d ' ')"

      status="$(docker inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null || true)"
      restart_count="$(docker inspect --format '{{.RestartCount}}' "$container_id" 2>/dev/null || true)"
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}not_configured{{end}}' "$container_id" 2>/dev/null || true)"
      image="$(docker inspect --format '{{.Config.Image}}' "$container_id" 2>/dev/null || true)"
      image_id="$(docker inspect --format '{{.Image}}' "$container_id" 2>/dev/null || true)"
      compose_image_id="$(docker compose -p pantheon -f docker-compose.yml images -q "$service" 2>/dev/null || true)"
      # Docker Compose v2 has emitted both canonical ``sha256:<digest>`` and
      # bare 64-character digests from ``images -q`` across supported
      # releases. Docker inspect remains canonical, so normalize only the
      # well-formed bare representation before comparing the two identities.
      # Any other output (including multiple IDs) stays unchanged and fails
      # the strict format check below.
      if [[ "$compose_image_id" =~ ^[0-9a-f]{64}$ ]]; then
        compose_image_id="sha256:${compose_image_id}"
      fi
      image_rev="$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$container_id" 2>/dev/null || true)"
      cmd="$(docker inspect --format '{{json .Config.Cmd}}' "$container_id" 2>/dev/null || true)"

      if [[ "$status" != "running" ]]; then
        restarting+=("${service}: status=${status}, restart_count=${restart_count}")
      fi
      if [[ ! "$restart_count" =~ ^[0-9]+$ || "$restart_count" != "0" ]]; then
        restarting+=("${service}: restart_count=${restart_count:-unknown}")
      fi
      if [[ "$health" == "starting" ]]; then
        has_starting=true
      elif [[ "$health" != "healthy" && "$health" != "not_configured" ]]; then
        unhealthy+=("${service}: health=${health}")
      fi
      if [[ -z "$image" ]]; then
        identity_errors+=("${service}: image identity is missing")
      fi
      if [[ ! "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
        identity_errors+=("${service}: container image ID is invalid (${image_id:-missing})")
      fi
      if [[ ! "$compose_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
        identity_errors+=("${service}: Compose image ID is invalid (${compose_image_id:-missing})")
      elif [[ "$image_id" != "$compose_image_id" ]]; then
        identity_errors+=("${service}: container image ID ${image_id:-missing} != Compose image ID ${compose_image_id}")
      fi
      if [[ -n "$image_rev" && "$image_rev" != "<no value>" && "$image_rev" != "unknown" && "$image_rev" != "$expected_sha" ]]; then
        wrong_sha+=("${service}: image_rev=${image_rev:-missing} != expected=${expected_sha}")
      fi

      if ! entry_json="$(python3 -c '
import json, sys
service, cid, img, image_id, compose_image_id, rev, stat, rcount, hlth, cmd_json, exp_sha = sys.argv[1:12]
try:
    cmd_val = json.loads(cmd_json)
except Exception as exc:
    raise SystemExit(f"invalid command JSON for {service}: {exc}")
if not cmd_val:
    raise SystemExit(f"empty command identity for {service}")
if not rcount.isdigit():
    raise SystemExit(f"invalid restart count for {service}: {rcount!r}")
print(json.dumps({
    "service": service,
    "container_id": cid,
    "image": img,
    "image_id": image_id,
    "compose_image_id": compose_image_id,
    "image_revision": rev if rev not in ("", "<no value>", "unknown") else None,
    "source_revision": exp_sha,
    "source_identity_method": "oci_revision" if rev == exp_sha else "deploy_checkout_and_compose_image_id",
    "status": stat,
    "restart_count": int(rcount),
    "health": hlth,
    "command": cmd_val,
    "matches_expected_sha": rev == exp_sha if rev not in ("", "<no value>", "unknown") else None,
    "matches_expected_image": image_id == compose_image_id,
}))
' "$service" "$container_id" "$image" "$image_id" "$compose_image_id" "$image_rev" "$status" "$restart_count" "$health" "$cmd" "$expected_sha")"; then
        identity_errors+=("${service}: receipt identity serialization failed")
        continue
      fi
      receipt_entries+=("$entry_json")
    done

    if [[ "$has_starting" == "true" ]] && (( attempt < max_attempts )); then
      sleep 2
      continue
    fi
    break
  done

  if (( ${#receipt_entries[@]} != ${#target_services[@]} )); then
    identity_errors+=("receipt entries=${#receipt_entries[@]} required=${#target_services[@]}")
  fi

  local verification_status="passed"
  if (( ${#missing[@]} > 0 || ${#duplicates[@]} > 0 || ${#restarting[@]} > 0 || ${#unhealthy[@]} > 0 || ${#wrong_sha[@]} > 0 || ${#identity_errors[@]} > 0 )); then
    verification_status="failed"
  fi

  local required_services_json receipt_entries_json failures_json
  required_services_json="$(printf '%s\n' "${target_services[@]}" | python3 -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin if line.rstrip("\n")]))')" \
    || { printf '[remote-deploy] unable to serialize required service identities\n' >&2; return 1; }
  receipt_entries_json="$(printf '%s\n' "${receipt_entries[@]}" | python3 -c 'import json,sys; print(json.dumps([json.loads(line) for line in sys.stdin if line.strip()]))')" \
    || { printf '[remote-deploy] unable to serialize component receipt entries\n' >&2; return 1; }
  failures_json="$(python3 -c '
import json, sys
keys = ("missing", "duplicates", "not_running_or_restarted", "unhealthy", "wrong_sha", "identity_errors")
values = [json.loads(value) for value in sys.argv[1:]]
print(json.dumps(dict(zip(keys, values))))
' \
    "$(printf '%s\n' "${missing[@]}" | python3 -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin if line.rstrip("\n")]))')" \
    "$(printf '%s\n' "${duplicates[@]}" | python3 -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin if line.rstrip("\n")]))')" \
    "$(printf '%s\n' "${restarting[@]}" | python3 -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin if line.rstrip("\n")]))')" \
    "$(printf '%s\n' "${unhealthy[@]}" | python3 -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin if line.rstrip("\n")]))')" \
    "$(printf '%s\n' "${wrong_sha[@]}" | python3 -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin if line.rstrip("\n")]))')" \
    "$(printf '%s\n' "${identity_errors[@]}" | python3 -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin if line.rstrip("\n")]))')")" \
    || { printf '[remote-deploy] unable to serialize component verification failures\n' >&2; return 1; }

  if ! mkdir -p -- "$(dirname "$receipt_path")"; then
    printf '[remote-deploy] unable to create backend component receipt directory: %s\n' "$(dirname "$receipt_path")" >&2
    return 1
  fi
  if ! python3 - \
    "$now" "$expected_sha" "$expected_frontend_sha" "$bff_url" "$fe_url" \
    "$deploy_environment" "$deploy_component" "$verification_status" "$receipt_path" \
    "$required_services_json" "$receipt_entries_json" "$failures_json" <<'PY'
import json
import os
import sys
from pathlib import Path

(
    now,
    backend_sha,
    frontend_sha,
    bff_url,
    fe_url,
    deploy_environment,
    deploy_component,
    verification_status,
    output_value,
    required_services_json,
    entries_json,
    failures_json,
) = sys.argv[1:]
required_services = json.loads(required_services_json)
entries = json.loads(entries_json)
failures = json.loads(failures_json)
services = {entry["service"]: entry for entry in entries}
if len(services) != len(entries):
    raise SystemExit("component receipt contains duplicate service entries")
complete = set(services) == set(required_services) and len(required_services) == len(entries)
all_passed = verification_status == "passed" and complete and not any(failures.values())
if verification_status == "passed" and not all_passed:
    raise SystemExit("component receipt cannot record passed status with incomplete evidence")
receipt = {
    "schema_version": "pantheon.deployment.backend_required_components_receipt.v1",
    "task": {
        "id": "ACG-DEPLOY-EXACT-GATES-20260828"
    },
    "task_id": "ACG-DEPLOY-EXACT-GATES-20260828",
    "status": verification_status,
    "result": verification_status,
    "mode": "hosted",
    "observed_at": now,
    "expected_sha": backend_sha,
    "deploy_source_sha": backend_sha,
    "deployment_environment": deploy_environment,
    "deployment_component": deploy_component,
    "unskipped_mandatory_cases": True,
    "skipped_mandatory_count": 0,
    "exact_pair": {
        "backend_sha": backend_sha,
        "frontend_sha": frontend_sha,
        "bff_url": bff_url,
        "fe_url": fe_url,
    },
    "required_services": required_services,
    "total_services": len(entries),
    "services": services,
    "verification_failures": failures,
    "all_passed": all_passed,
}
output_path = Path(output_value)
temporary_path = output_path.with_name(f".{output_path.name}.tmp.{os.getpid()}")
try:
    with temporary_path.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, output_path)
finally:
    temporary_path.unlink(missing_ok=True)
PY
  then
    printf '[remote-deploy] unable to atomically write backend component receipt: %s\n' "$receipt_path" >&2
    return 1
  fi
  info "backend component receipt written atomically outside the deploy worktree: ${receipt_path}"

  if (( ${#missing[@]} > 0 )); then
    printf '[remote-deploy] required component(s) missing: %s\n' "${missing[*]}" >&2
  fi
  if (( ${#duplicates[@]} > 0 )); then
    printf '[remote-deploy] duplicate containers found for required singleton service(s): %s\n' "${duplicates[*]}" >&2
  fi
  if (( ${#restarting[@]} > 0 )); then
    printf '[remote-deploy] required component(s) not in stable running state: %s\n' "${restarting[*]}" >&2
  fi
  if (( ${#unhealthy[@]} > 0 )); then
    printf '[remote-deploy] required component(s) unhealthy or unknown: %s\n' "${unhealthy[*]}" >&2
  fi
  if (( ${#wrong_sha[@]} > 0 )); then
    printf '[remote-deploy] required component(s) have missing or mismatched image revision: %s\n' "${wrong_sha[*]}" >&2
  fi
  if (( ${#identity_errors[@]} > 0 )); then
    printf '[remote-deploy] required component identity evidence is incomplete: %s\n' "${identity_errors[*]}" >&2
  fi
  if [[ "$verification_status" != "passed" ]]; then
    return 1
  fi

  info "exact component verification passed for ${#target_services[@]} service(s)"
  return 0
}

docker_storage_diagnostics() {
  local label="$1"

  info "docker storage diagnostics (${label}): filesystem usage"
  df -h . /var/lib/docker /var/lib/containerd 2>/dev/null || df -h . || true
  info "docker storage diagnostics (${label}): docker system df"
  docker system df || true
}

run_bounded_docker_prune() {
  local label="$1"
  shift
  local timeout_seconds="${PANTHEON_DEV_DOCKER_PRUNE_TIMEOUT_SECONDS:-45}"

  if ! [[ "${timeout_seconds}" =~ ^[0-9]+$ ]] || (( timeout_seconds < 1 || timeout_seconds > 120 )); then
    info "warning: invalid PANTHEON_DEV_DOCKER_PRUNE_TIMEOUT_SECONDS=${timeout_seconds}; skipping ${label}"
    return 0
  fi
  if ! command -v timeout >/dev/null 2>&1; then
    info "warning: timeout utility unavailable; skipping ${label}"
    return 0
  fi

  info "running bounded Docker maintenance: ${label} (timeout=${timeout_seconds}s)"
  if timeout --signal=TERM --kill-after=10s "${timeout_seconds}s" "$@"; then
    return 0
  fi

  local status=$?
  info "warning: ${label} exited with status ${status}; continuing deployment"
  return 0
}

prune_dev_docker_storage_for_build() {
  if [[ "${PANTHEON_DEPLOY_ENV}" != "dev" || "${PANTHEON_DEPLOY_COMPONENT}" != "root" ]]; then
    return
  fi

  if [[ "${PANTHEON_DEV_DOCKER_PRUNE:-false}" != "true" ]]; then
    info "dev Docker prune disabled before root build"
    docker_storage_diagnostics "before build"
    return
  fi

  docker_storage_diagnostics "before prune"
  info "pruning dev Docker build cache and unused containers/images before root build"
  run_bounded_docker_prune "builder cache" docker builder prune -af
  run_bounded_docker_prune "stopped containers" docker container prune -f
  run_bounded_docker_prune "unused images" docker image prune -af
  run_bounded_docker_prune "system cache" docker system prune -f
  docker_storage_diagnostics "after prune"
}

cleanup_stale_compose_replacement_containers() {
  if ! command -v docker >/dev/null 2>&1; then
    info "docker command unavailable; skipping stale replacement container cleanup"
    return 0
  fi

  info "checking for stale Compose replacement containers (project=pantheon)"
  local raw_list
  raw_list="$(docker ps -a --filter "label=com.docker.compose.project=pantheon" --format '{{.ID}}\t{{.Names}}\t{{.State}}\t{{.Status}}' 2>/dev/null || true)"

  if [[ -z "${raw_list}" ]]; then
    return 0
  fi

  local count=0
  while IFS=$'\t' read -r cid cname cstate cstatus; do
    [[ -z "${cid}" ]] && continue
    local clean_name="${cname#/}"

    # Never touch running or restarting containers
    if [[ "${cstate}" == "running" || "${cstate}" == "restarting" || "${cstatus}" =~ ^Up([[:space:]]|$) || "${cstatus}" =~ ^Restarting([[:space:]]|$) ]]; then
      continue
    fi

    # Detect only non-running containers with hash-prefixed pantheon names (e.g. 1234567890ab_pantheon-..., d20e73e97086_pantheon_postgres_1)
    if [[ "${clean_name}" =~ ^[0-9a-fA-F]+[-_]pantheon ]]; then
      info "removing stale Compose replacement container: ${clean_name} (id=${cid}, state=${cstate:-unknown})"
      if docker rm -f "${cid}" >/dev/null 2>&1; then
        count=$((count + 1))
      else
        info "warning: failed to remove stale container ${clean_name} (id=${cid})"
      fi
    fi
  done <<< "${raw_list}"

  if (( count > 0 )); then
    info "cleaned up ${count} stale Compose replacement container(s)"
  fi
}

rollback_dev_bff_on_failure() {
  local failed_stage="$1"
  local rollback_sha="${PANTHEON_DEV_ROLLBACK_BACKEND_SHA:-${DEV_PRE_DEPLOY_BFF_SHA:-}}"

  dump_dev_root_failure_diagnostics

  if [[ -z "${rollback_sha}" || ! "${rollback_sha}" =~ ^[0-9a-f]{40}$ || "${rollback_sha}" == "${PANTHEON_DEPLOY_SHA}" ]]; then
    info "automatic BFF rollback skipped: no distinct valid baseline rollback SHA available (rollback_sha=${rollback_sha:-none})"
    exit 1
  fi

  info "post-up failure at ${failed_stage}; automatically rolling back dev operator-bff and lifecycle projector to baseline ${rollback_sha}"

  if git checkout --detach "${rollback_sha}" >/dev/null 2>&1; then
    COMPOSE_BAKE=false \
    COMPOSE_PROFILES="" \
    GIT_SHA="${rollback_sha}" \
    BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    PANTHEON_ENV=dev \
    LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS="${PANTHEON_DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS}" \
    PANTHEON_CANARY_EXECUTION_ENABLED=false \
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
    PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED="false" \
    PANTHEON_BFF_JWT_SECRET="${PANTHEON_DEV_BFF_JWT_SECRET}" \
    CAPITAL_JWT_SECRET="${PANTHEON_DEV_CAPITAL_JWT_SECRET}" \
    PANTHEON_BFF_JWT_ISSUER="${PANTHEON_DEV_BFF_JWT_ISSUER}" \
    PANTHEON_BFF_JWT_AUDIENCE="${PANTHEON_DEV_BFF_JWT_AUDIENCE}" \
    PANTHEON_BFF_JWKS_URI="${PANTHEON_DEV_BFF_JWKS_URI}" \
    PANTHEON_BFF_OIDC_DISCOVERY_URL="${PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL}" \
    PANTHEON_BFF_OIDC_ISSUER="${PANTHEON_DEV_BFF_OIDC_ISSUER}" \
    PANTHEON_BFF_OIDC_AUDIENCE="${PANTHEON_DEV_BFF_OIDC_AUDIENCE}" \
    PANTHEON_BFF_OIDC_CLIENT_ID="${PANTHEON_DEV_BFF_OIDC_CLIENT_ID}" \
    PANTHEON_BFF_OIDC_CLIENT_SECRET="${PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET}" \
    PANTHEON_BFF_MFA_REQUIRED="${PANTHEON_DEV_BFF_MFA_REQUIRED}" \
    PANTHEON_BFF_MFA_CLAIMS="${PANTHEON_DEV_BFF_MFA_CLAIMS}" \
    PANTHEON_BFF_MFA_VALUES="${PANTHEON_DEV_BFF_MFA_VALUES}" \
    PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED="${PANTHEON_DEV_BFF_REQUIRE_EMAIL_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED}" \
    PANTHEON_BFF_ROLE_CLAIMS="${PANTHEON_DEV_BFF_ROLE_CLAIMS}" \
    PANTHEON_BFF_ROLE_MAP="${PANTHEON_DEV_BFF_ROLE_MAP}" \
    PANTHEON_BFF_ROLE_MAP_MODE="${PANTHEON_DEV_BFF_ROLE_MAP_MODE}" \
    PANTHEON_BFF_DEFAULT_ROLE="${PANTHEON_DEV_BFF_DEFAULT_ROLE}" \
    PANTHEON_BFF_TENANT_ID="${PANTHEON_DEV_BFF_TENANT_ID}" \
    PANTHEON_BFF_ALLOWED_TENANTS="${PANTHEON_DEV_BFF_ALLOWED_TENANTS}" \
    PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED}" \
    PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH}" \
    PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH="${PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH}" \
    PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS}" \
    PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES}" \
    PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN}" \
    PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED}" \
    PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN="${PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN}" \
    MANAGEMENT_AI_STORE_BACKEND="${MANAGEMENT_AI_STORE_BACKEND}" \
    MANAGEMENT_AI_STORE_SCHEMA="${MANAGEMENT_AI_STORE_SCHEMA}" \
    MANAGEMENT_AI_DATABASE_URL="${MANAGEMENT_AI_DATABASE_URL}" \
    PANTHEON_MGMT_AI_ATTACH_BUCKET="${PANTHEON_MGMT_AI_ATTACH_BUCKET}" \
    PANTHEON_MGMT_AI_ATTACH_LOCATION="${PANTHEON_MGMT_AI_ATTACH_LOCATION:-asia-east1}" \
      docker compose -p pantheon -f docker-compose.yml up -d --build --force-recreate --no-deps operator-bff agora-interaction-worker loop-run-projector-scheduler \
      || info "warning: docker compose up failed during rollback execution"

    curl_with_retry http://127.0.0.1:18001/health 6 5 || true
    actual_restored="$(curl -fsS http://127.0.0.1:18001/bff/version 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("source_commit_sha") or "")' 2>/dev/null || true)"
    if [[ "${actual_restored}" == "${rollback_sha}" ]]; then
      info "automatic BFF rollback verified: operator-bff restored to baseline ${rollback_sha}"
    else
      info "warning: automatic BFF rollback unable to verify restored SHA: expected ${rollback_sha}, got ${actual_restored:-none}"
    fi
  else
    info "warning: unable to checkout baseline rollback SHA ${rollback_sha}"
  fi

  exit 1
}

cd "${PANTHEON_REMOTE_DIR}"
git rev-parse --is-inside-work-tree >/dev/null

case "${PANTHEON_DEPLOY_COMPONENT}" in
  root)
    snapshot_remote_state pantheon docker-compose.yml
    prepare_deploy_worktree
    # Keep the requested immutable identity in the environment for every
    # Compose call in this root deployment.  In particular, the lifecycle
    # projector is force-recreated after the full stack build; a command-local
    # GIT_SHA on only the first `compose up` would recreate that projector with
    # the compose default (`unknown`) and make the exact-SHA readiness gate
    # impossible to satisfy.
    export GIT_SHA="${PANTHEON_DEPLOY_SHA}"
    # Dev deploys activate the required persistent root compose profile: openclaw.
    # Dormant smoke profiles (e.g. dormant-smoke for MLflow/FinRL/RLlib/Ray-Tune/Qlib/TRL/experiments),
    # one-off smoke profiles (activation-ready-smoke, openclaw-activation-ready-e2e, smoke, source-search-bounded),
    # and optional integrations are kept out of the default persistent root deploy to prevent
    # deployment timeouts and host memory exhaustion.
    #
    # Required loop workers are default-on in docker-compose.yml, and validate_required_loop_workers
    # enforces that the persistent stack contains all required twelve-loop workers.
    #
    # Operators can supply explicit profiles via PANTHEON_DEV_COMPOSE_PROFILES when running bounded verifications.
    PANTHEON_DEV_COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-openclaw}"
    validate_source_refresh_profile
    validate_required_loop_workers
    source_refresh_deploy_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    ensure_dev_management_ai_bucket
    ensure_dev_management_ai_postgres_role
    prune_dev_management_ai_telemetry_for_disk
    retire_dormant_and_one_off_profile_containers
    COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES}" \
    PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED="${PANTHEON_DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED}" \
      LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS="${PANTHEON_DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS}" \
      docker compose -p pantheon -f docker-compose.yml config --quiet
    prune_dev_docker_storage_for_build
    # Phase 2: Build candidate images before mutating the active runtime.
    COMPOSE_BAKE=false \
    COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES}" \
    GIT_SHA="${PANTHEON_DEPLOY_SHA}" \
    BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      docker compose -p pantheon -f docker-compose.yml build \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    resolve_bounded_source_refresh_active_symbols \
      || rollback_dev_bff_on_failure "source_refresh_active_symbols"
    DEV_PRE_DEPLOY_BFF_SHA="$(curl -fsS http://127.0.0.1:18001/bff/version 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("source_commit_sha") or "")' 2>/dev/null || true)"
    PANTHEON_DEV_ROLLBACK_BACKEND_SHA="${PANTHEON_DEV_ROLLBACK_BACKEND_SHA:-${DEV_PRE_DEPLOY_BFF_SHA:-}}"
    # Phase 3: Rollout persistent root runtime.
    cleanup_stale_compose_replacement_containers
    COMPOSE_BAKE=false \
    COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES}" \
    BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    PANTHEON_ENV=dev \
    LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS="${PANTHEON_DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS}" \
    PANTHEON_EXTERNAL_EGRESS="${PANTHEON_EXTERNAL_EGRESS:-deny}" \
    PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS="${PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS:-}" \
    SOURCE_INGEST_CONTROLLER_MODE="${SOURCE_INGEST_CONTROLLER_MODE}" \
    SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL="${SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL}" \
    SOURCE_INGEST_CONTROLLER_MAX_TICKS="${SOURCE_INGEST_CONTROLLER_MAX_TICKS}" \
    SOURCE_INGEST_CONTROLLER_RESTART_POLICY="${SOURCE_INGEST_CONTROLLER_RESTART_POLICY}" \
    SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS="${SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS:-}" \
    SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS="${SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS:-}" \
    SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY="${SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY:-1}" \
    SOURCE_INGEST_MAX_RECORDS="${SOURCE_INGEST_MAX_RECORDS:-100}" \
    PANTHEON_CANARY_EXECUTION_ENABLED=false \
    PANTHEON_LIVE_BROKER_ENABLED=false \
    BROKER_PAPER_ENABLED=true \
    PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED=true \
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
    PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED="${PANTHEON_DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED}" \
    PANTHEON_BFF_JWT_SECRET="${PANTHEON_DEV_BFF_JWT_SECRET}" \
    CAPITAL_JWT_SECRET="${PANTHEON_DEV_CAPITAL_JWT_SECRET}" \
    PANTHEON_BFF_JWT_ISSUER="${PANTHEON_DEV_BFF_JWT_ISSUER}" \
    PANTHEON_BFF_JWT_AUDIENCE="${PANTHEON_DEV_BFF_JWT_AUDIENCE}" \
    PANTHEON_BFF_JWKS_URI="${PANTHEON_DEV_BFF_JWKS_URI}" \
    PANTHEON_BFF_OIDC_DISCOVERY_URL="${PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL}" \
    PANTHEON_BFF_OIDC_ISSUER="${PANTHEON_DEV_BFF_OIDC_ISSUER}" \
    PANTHEON_BFF_OIDC_AUDIENCE="${PANTHEON_DEV_BFF_OIDC_AUDIENCE}" \
    PANTHEON_BFF_OIDC_CLIENT_ID="${PANTHEON_DEV_BFF_OIDC_CLIENT_ID}" \
    PANTHEON_BFF_OIDC_CLIENT_SECRET="${PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET}" \
    PANTHEON_BFF_MFA_REQUIRED="${PANTHEON_DEV_BFF_MFA_REQUIRED}" \
    PANTHEON_BFF_MFA_CLAIMS="${PANTHEON_DEV_BFF_MFA_CLAIMS}" \
    PANTHEON_BFF_MFA_VALUES="${PANTHEON_DEV_BFF_MFA_VALUES}" \
    PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED="${PANTHEON_DEV_BFF_REQUIRE_EMAIL_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED}" \
    PANTHEON_BFF_ROLE_CLAIMS="${PANTHEON_DEV_BFF_ROLE_CLAIMS}" \
    PANTHEON_BFF_ROLE_MAP="${PANTHEON_DEV_BFF_ROLE_MAP}" \
    PANTHEON_BFF_ROLE_MAP_MODE="${PANTHEON_DEV_BFF_ROLE_MAP_MODE}" \
    PANTHEON_BFF_DEFAULT_ROLE="${PANTHEON_DEV_BFF_DEFAULT_ROLE}" \
    PANTHEON_BFF_TENANT_ID="${PANTHEON_DEV_BFF_TENANT_ID}" \
    PANTHEON_BFF_ALLOWED_TENANTS="${PANTHEON_DEV_BFF_ALLOWED_TENANTS}" \
    PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED}" \
    PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH}" \
    PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH="${PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH}" \
    PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS}" \
    PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES}" \
    PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN}" \
    PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED}" \
    PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN="${PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN}" \
      docker compose -p pantheon -f docker-compose.yml up -d \
      || rollback_dev_bff_on_failure "docker_compose_up"
    # `up -d --build` only recreates a container Compose judges to need it.
    # The legacy lifecycle projector runs with `restart: no` (deliberate
    # anti-OOM containment: it can otherwise consume the host, so it is never
    # auto-restarted) -- if it hung or died mid-poll on a prior deploy without
    # its config changing, Compose leaves the stale/hung container in place
    # and the exact-SHA readiness gate below can never observe a fresh
    # publish. Force it every root deploy so a wedged projector cannot
    # silently survive across deploys.
    docker compose -p pantheon -f docker-compose.yml up -d --force-recreate --no-deps loop-run-projector-scheduler \
      || rollback_dev_bff_on_failure "projector_recreate"
    # Phase 4: Post-Deploy Bounded Verification
    verify_bounded_source_refresh_readback "${source_refresh_deploy_started_at}" \
      || rollback_dev_bff_on_failure "source_refresh_readback"
    PANTHEON_DEV_REPO="$(pwd)" \
      bash scripts/openclaw-configure-shared-model-pool.sh \
      || rollback_dev_bff_on_failure "shared_model_pool"
    retire_legacy_static_paper_runtime \
      || rollback_dev_bff_on_failure "retire_legacy_paper"
    retire_dormant_and_one_off_profile_containers \
      || rollback_dev_bff_on_failure "retire_dormant_profiles"
    verify_dev_paper_fleet \
      || rollback_dev_bff_on_failure "paper_fleet"
    # Root deployment replaces the lifecycle projector and BFF together. The
    # BFF can become HTTP-ready while /readyz still reports the prior projector
    # identity, so use the same exact-deployment, bounded recovery gate as the
    # later residual restart smoke. A wrong or missing projector SHA can retry
    # only inside the ordinary base window and can never grant the extension.
    wait_for_exact_bff_lifecycle_readiness \
      http://127.0.0.1:18001/readyz \
      || rollback_dev_bff_on_failure "bff_lifecycle_readiness"
    assert_bff_source_sha http://127.0.0.1:18001/bff/version \
      || rollback_dev_bff_on_failure "bff_source_sha"
    assert_bff_auth_gate http://127.0.0.1:18001 \
      || rollback_dev_bff_on_failure "bff_auth_gate"
    assert_ppl_alloc_009_dev_proof_gate \
      || rollback_dev_bff_on_failure "ppl_alloc_009_proof_gate"
    ensure_dev_caddy_ingress \
      || rollback_dev_bff_on_failure "caddy_ingress"
    verify_dev_evolution_daily_sweep \
      || rollback_dev_bff_on_failure "evolution_daily_sweep"
    # Prove the Trade Journey action ledger is genuinely durable on the dev
    # PostgreSQL instance and that clock-drift diagnostics survive the built
    # runtime image. This intentionally restarts operator-bff and verifies
    # receipt replay before the workflow's public smokes run.
    PANTHEON_DEV_REPO="$(pwd)" bash scripts/verify_trade_journey_residual_dev.sh \
      || rollback_dev_bff_on_failure "trade_journey_residual"
    verify_exact_component_deployment \
      || rollback_dev_bff_on_failure "exact_component_deployment"
    ;;

  bff)
    # Rebuild operator-bff and the lifecycle projector that owns the readiness
    # evidence it serves. All other compose services — including the paper
    # fleet and runtime-manager — are left running.
    # Use this component when deploying a BFF-only fix to avoid the OOM
    # pressure that a full root-stack rebuild causes on the dev VM.
    snapshot_remote_state pantheon docker-compose.yml
    prepare_deploy_worktree
    export GIT_SHA="${PANTHEON_DEPLOY_SHA}"
    # Phase 2: Build candidate operator-bff and loop-run-projector-scheduler images.
    COMPOSE_BAKE=false \
    COMPOSE_PROFILES="" \
    GIT_SHA="${PANTHEON_DEPLOY_SHA}" \
    BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      docker compose -p pantheon -f docker-compose.yml build operator-bff agora-interaction-worker loop-run-projector-scheduler \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    DEV_PRE_DEPLOY_BFF_SHA="$(curl -fsS http://127.0.0.1:18001/bff/version 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("source_commit_sha") or "")' 2>/dev/null || true)"
    PANTHEON_DEV_ROLLBACK_BACKEND_SHA="${PANTHEON_DEV_ROLLBACK_BACKEND_SHA:-${DEV_PRE_DEPLOY_BFF_SHA:-}}"
    # Phase 3: Recreate operator-bff and loop-run-projector-scheduler.
    cleanup_stale_compose_replacement_containers
    COMPOSE_BAKE=false \
    COMPOSE_PROFILES="" \
    GIT_SHA="${PANTHEON_DEPLOY_SHA}" \
    BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    PANTHEON_ENV=dev \
    LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS="${PANTHEON_DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS}" \
    PANTHEON_CANARY_EXECUTION_ENABLED=false \
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
    PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED="${PANTHEON_DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED}" \
    PANTHEON_BFF_JWT_SECRET="${PANTHEON_DEV_BFF_JWT_SECRET}" \
    CAPITAL_JWT_SECRET="${PANTHEON_DEV_CAPITAL_JWT_SECRET}" \
    PANTHEON_BFF_JWT_ISSUER="${PANTHEON_DEV_BFF_JWT_ISSUER}" \
    PANTHEON_BFF_JWT_AUDIENCE="${PANTHEON_DEV_BFF_JWT_AUDIENCE}" \
    PANTHEON_BFF_JWKS_URI="${PANTHEON_DEV_BFF_JWKS_URI}" \
    PANTHEON_BFF_OIDC_DISCOVERY_URL="${PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL}" \
    PANTHEON_BFF_OIDC_ISSUER="${PANTHEON_DEV_BFF_OIDC_ISSUER}" \
    PANTHEON_BFF_OIDC_AUDIENCE="${PANTHEON_DEV_BFF_OIDC_AUDIENCE}" \
    PANTHEON_BFF_OIDC_CLIENT_ID="${PANTHEON_DEV_BFF_OIDC_CLIENT_ID}" \
    PANTHEON_BFF_OIDC_CLIENT_SECRET="${PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET}" \
    PANTHEON_BFF_MFA_REQUIRED="${PANTHEON_DEV_BFF_MFA_REQUIRED}" \
    PANTHEON_BFF_MFA_CLAIMS="${PANTHEON_DEV_BFF_MFA_CLAIMS}" \
    PANTHEON_BFF_MFA_VALUES="${PANTHEON_DEV_BFF_MFA_VALUES}" \
    PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED="${PANTHEON_DEV_BFF_REQUIRE_EMAIL_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_MFA_VERIFIED}" \
    PANTHEON_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED="${PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_MFA_VERIFIED}" \
    PANTHEON_BFF_ROLE_CLAIMS="${PANTHEON_DEV_BFF_ROLE_CLAIMS}" \
    PANTHEON_BFF_ROLE_MAP="${PANTHEON_DEV_BFF_ROLE_MAP}" \
    PANTHEON_BFF_ROLE_MAP_MODE="${PANTHEON_DEV_BFF_ROLE_MAP_MODE}" \
    PANTHEON_BFF_DEFAULT_ROLE="${PANTHEON_DEV_BFF_DEFAULT_ROLE}" \
    PANTHEON_BFF_TENANT_ID="${PANTHEON_DEV_BFF_TENANT_ID}" \
    PANTHEON_BFF_ALLOWED_TENANTS="${PANTHEON_DEV_BFF_ALLOWED_TENANTS}" \
    PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED}" \
    PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH}" \
    PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH="${PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH}" \
    PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS}" \
    PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES}" \
    PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN}" \
    PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED="${PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED}" \
    PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN="${PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN}" \
    MANAGEMENT_AI_STORE_BACKEND="${MANAGEMENT_AI_STORE_BACKEND}" \
    MANAGEMENT_AI_STORE_SCHEMA="${MANAGEMENT_AI_STORE_SCHEMA}" \
    MANAGEMENT_AI_DATABASE_URL="${MANAGEMENT_AI_DATABASE_URL}" \
    PANTHEON_MGMT_AI_ATTACH_BUCKET="${PANTHEON_MGMT_AI_ATTACH_BUCKET}" \
    PANTHEON_MGMT_AI_ATTACH_LOCATION="${PANTHEON_MGMT_AI_ATTACH_LOCATION:-asia-east1}" \
      docker compose -p pantheon -f docker-compose.yml up -d --force-recreate --no-deps operator-bff agora-interaction-worker loop-run-projector-scheduler \
      || rollback_dev_bff_on_failure "bff_recreate"
    # Phase 4: Post-Deploy Verification Gates
    wait_for_exact_bff_lifecycle_readiness \
      http://127.0.0.1:18001/readyz \
      || rollback_dev_bff_on_failure "bff_lifecycle_readiness"
    assert_bff_source_sha http://127.0.0.1:18001/bff/version \
      || rollback_dev_bff_on_failure "bff_source_sha"
    assert_bff_auth_gate http://127.0.0.1:18001 \
      || rollback_dev_bff_on_failure "bff_auth_gate"
    assert_ppl_alloc_009_dev_proof_gate \
      || rollback_dev_bff_on_failure "ppl_alloc_009_proof_gate"
    ensure_dev_caddy_ingress \
      || rollback_dev_bff_on_failure "caddy_ingress"
    verify_exact_component_deployment operator-bff agora-interaction-worker loop-run-projector-scheduler \
      || rollback_dev_bff_on_failure "bff_exact_component_deployment"
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
