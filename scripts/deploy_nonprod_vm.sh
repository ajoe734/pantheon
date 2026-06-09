#!/usr/bin/env bash
# Deploy Pantheon non-prod VM compose stacks from a verified git commit.
#
# This script is designed for GitHub Actions, but it can also be run by an
# operator from a workstation with gcloud access. The VM's human-facing checkout
# is used only as the git object source and snapshot target; deployment runs from
# a managed clean worktree under ~/pantheon-ci-deploy.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-pantheon-493602}"
REMOTE_USER="${REMOTE_USER:-edna}"

DEV_VM="${DEV_VM:-pantheon-dev-vm1}"
DEV_ZONE="${DEV_ZONE:-asia-east1-b}"
DEV_REMOTE_DIR="${DEV_REMOTE_DIR:-/home/edna/code/pantheon}"
DEV_BFF_CORS_ORIGINS="${DEV_BFF_CORS_ORIGINS:-https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io,https://pantheon-ai-system-front-dev.lovable.app,https://pantheon-dev.lovable.app}"
DEV_BFF_REQUIRED_CORS_ORIGINS="${DEV_BFF_REQUIRED_CORS_ORIGINS:-https://preview--pantheon-dev.lovable.app,https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com,https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app,https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com}"
DEV_BFF_AUTH_STUB="${DEV_BFF_AUTH_STUB:-true}"
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
DEV_APP_DB_USER="${DEV_APP_DB_USER:-${PANTHEON_APP_DB_USER:-pantheon_app}}"

STAGING_CONTROL_VM="${STAGING_CONTROL_VM:-pantheon-taiwan}"
STAGING_CONTROL_ZONE="${STAGING_CONTROL_ZONE:-asia-east1-b}"
STAGING_CONTROL_REMOTE_DIR="${STAGING_CONTROL_REMOTE_DIR:-/home/edna/code/pantheon}"

STAGING_EXEC_VM="${STAGING_EXEC_VM:-pantheon-exec-vm2-20260424}"
STAGING_EXEC_ZONE="${STAGING_EXEC_ZONE:-asia-east1-a}"
STAGING_EXEC_REMOTE_DIR="${STAGING_EXEC_REMOTE_DIR:-/home/edna/code/pantheon}"
STAGING_EXEC_HEALTH_URL="${STAGING_EXEC_HEALTH_URL:-http://10.140.0.5:28081}"

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
  --component <name>     auto, root, control, exec, or all. Default: auto.
                         auto maps to root for dev and all for staging-live.
  --sha <commit>         Required unless GITHUB_SHA is set. Commit to deploy.
  --project-id <id>      GCP project. Default: pantheon-493602.
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
  DEV_BFF_CORS_ORIGINS DEV_BFF_AUTH_STUB
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
  PANTHEON_STATUS_ROOT_CONTAINER="${PANTHEON_STATUS_ROOT_CONTAINER:-$DEV_STATUS_ROOT_CONTAINER}"
  PANTHEON_STATUS_ROOT_HOST="${PANTHEON_STATUS_ROOT_HOST:-${DEV_STATUS_ROOT_HOST:-$DEV_REMOTE_DIR}}"
}

DEV_BFF_CORS_ORIGINS="$(append_csv_unique "$DEV_BFF_CORS_ORIGINS" "$DEV_BFF_REQUIRED_CORS_ORIGINS")"

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
    [[ "$COMPONENT" == "root" ]] || error "dev supports only --component root"
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
  info "dev_bff_auth_stub=${DEV_BFF_AUTH_STUB}"
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
  info "management_ai_store_backend=${MANAGEMENT_AI_STORE_BACKEND:-}"
  info "management_ai_store_schema=${MANAGEMENT_AI_STORE_SCHEMA:-}"
  info "management_ai_database_user=${DEV_MANAGEMENT_AI_DB_USER}"
  info "management_ai_database_url_configured=$([[ -n "${MANAGEMENT_AI_DATABASE_URL:-}" ]] && echo true || echo false)"
  info "management_ai_attach_bucket=${PANTHEON_MGMT_AI_ATTACH_BUCKET:-}"
  info "management_ai_attach_location=${DEV_MANAGEMENT_AI_ATTACH_LOCATION}"
  info "staging_exec_health_url=${STAGING_EXEC_HEALTH_URL}"
  exit 0
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
  command_prefix+=" PANTHEON_DEV_BFF_AUTH_STUB=$(shell_quote "$DEV_BFF_AUTH_STUB")"
  command_prefix+=" PANTHEON_ASSISTANT_KERNEL_ENABLED=$(shell_quote "${PANTHEON_ASSISTANT_KERNEL_ENABLED:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH=$(shell_quote "${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS=$(shell_quote "${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REPO_URL=$(shell_quote "${PANTHEON_ASSISTANT_REPAIR_REPO_URL:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REMOTE_URL=$(shell_quote "${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS=$(shell_quote "${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS:-}")"
  command_prefix+=" PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS=$(shell_quote "${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS:-}")"
  command_prefix+=" PANTHEON_BFF_STUB_CAPABILITIES=$(shell_quote "${PANTHEON_BFF_STUB_CAPABILITIES:-}")"
  command_prefix+=" PANTHEON_STATUS_ROOT_HOST=$(shell_quote "${PANTHEON_STATUS_ROOT_HOST:-}")"
  command_prefix+=" PANTHEON_STATUS_ROOT_CONTAINER=$(shell_quote "${PANTHEON_STATUS_ROOT_CONTAINER:-}")"
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

require_clean_checkout() {
  local status
  local stash_label

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

prepare_deploy_worktree() {
  local sha="${PANTHEON_DEPLOY_SHA}"
  local source_dir="${PANTHEON_REMOTE_DIR}"
  local root="${PANTHEON_DEPLOY_WORKTREE_ROOT:-${HOME}/pantheon-ci-deploy}"
  local deploy_dir="${root}/${PANTHEON_DEPLOY_ENV}-${PANTHEON_DEPLOY_COMPONENT}"
  local marker="${root}/.${PANTHEON_DEPLOY_ENV}-${PANTHEON_DEPLOY_COMPONENT}.marker"

  cd "$source_dir"
  info "fetching origin"
  git_fetch_origin --prune
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
    git_fetch_origin --prune
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

dump_dev_root_failure_diagnostics() {
  info "dev root compose ps after failure"
  docker compose -p pantheon -f docker-compose.yml ps || true
  info "operator-bff logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=240 operator-bff || true
  info "postgres logs after failure"
  docker compose -p pantheon -f docker-compose.yml logs --no-color --tail=120 postgres || true
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
    COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES}" \
      docker compose -p pantheon -f docker-compose.yml config --quiet
    COMPOSE_BAKE=false \
    COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES}" \
    PANTHEON_ENV=dev \
    PANTHEON_LIVE_BROKER_ENABLED=false \
    PANTHEON_BFF_CORS_ORIGINS="${PANTHEON_DEV_BFF_CORS_ORIGINS}" \
    PANTHEON_BFF_AUTH_STUB="${PANTHEON_DEV_BFF_AUTH_STUB}" \
    PANTHEON_ASSISTANT_KERNEL_ENABLED="${PANTHEON_ASSISTANT_KERNEL_ENABLED}" \
    PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH="${PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH}" \
    PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS="${PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS}" \
    PANTHEON_ASSISTANT_REPAIR_REPO_URL="${PANTHEON_ASSISTANT_REPAIR_REPO_URL}" \
    PANTHEON_ASSISTANT_REPAIR_REMOTE_URL="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL}" \
    PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS}" \
    PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS="${PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS}" \
    PANTHEON_BFF_STUB_CAPABILITIES="${PANTHEON_BFF_STUB_CAPABILITIES}" \
    PANTHEON_STATUS_ROOT_HOST="${PANTHEON_STATUS_ROOT_HOST}" \
    PANTHEON_STATUS_ROOT_CONTAINER="${PANTHEON_STATUS_ROOT_CONTAINER}" \
      docker compose -p pantheon -f docker-compose.yml up -d --build \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    curl_with_retry http://127.0.0.1:18001/health \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    curl_with_retry http://127.0.0.1:18001/readyz \
      || { dump_dev_root_failure_diagnostics; exit 1; }
    ;;

  exec)
    snapshot_remote_state pantheon-exec docker-compose.exec.yml
    prepare_deploy_worktree
    env_file="$(real_env_or_example env/prod-exec.env env/prod-exec.env.example)"
    docker compose --env-file "$env_file" -p pantheon-exec -f docker-compose.exec.yml config --quiet
    COMPOSE_BAKE=false docker compose --env-file "$env_file" -p pantheon-exec -f docker-compose.exec.yml up -d --build
    curl_with_retry http://127.0.0.1:28081/__health__
    curl_with_retry http://127.0.0.1:28097/__health__
    curl_with_retry http://127.0.0.1:28098/__health__
    curl_with_retry http://127.0.0.1:28110/__health__
    ;;

  control)
    snapshot_remote_state pantheon-control docker-compose.control.yml
    prepare_deploy_worktree
    env_file="$(real_env_or_example env/prod-control.env env/prod-control.env.example)"
    docker compose --env-file "$env_file" -p pantheon-control -f docker-compose.control.yml config --quiet
    COMPOSE_BAKE=false \
    PANTHEON_ENV=staging-live \
    PANTHEON_LIVE_BROKER_ENABLED=true \
      docker compose --env-file "$env_file" -p pantheon-control -f docker-compose.control.yml up -d --build
    curl_with_retry http://127.0.0.1:38001/health
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
