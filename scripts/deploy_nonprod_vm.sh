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
  --allow-dirty          Emergency only: allow a dirty managed deploy worktree.
  --allow-example-env    Allow staging to use env/*.env.example if real env files
                         are absent. Intended for rehearsal only.
  --dry-run              Print the target plan without SSHing.
  --help                 Show this message.

Environment overrides:
  REMOTE_USER
  PANTHEON_DEPLOY_WORKTREE_ROOT
  GITHUB_TOKEN
  DEV_VM DEV_ZONE DEV_REMOTE_DIR
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

if [[ "$DRY_RUN" == "true" ]]; then
  info "dry run"
  info "project=${PROJECT_ID}"
  info "environment=${DEPLOY_ENV}"
  info "component=${COMPONENT}"
  info "sha=${DEPLOY_SHA}"
  info "allow_dirty=${ALLOW_DIRTY}"
  info "allow_example_env=${ALLOW_EXAMPLE_ENV}"
  info "staging_exec_health_url=${STAGING_EXEC_HEALTH_URL}"
  exit 0
fi

require_cmd gcloud

ssh_bash() {
  local vm="$1"
  local zone="$2"
  local remote_dir="$3"
  local remote_component="$4"
  local command_prefix

  command_prefix="PANTHEON_DEPLOY_ENV=$(shell_quote "$DEPLOY_ENV")"
  command_prefix+=" PANTHEON_DEPLOY_COMPONENT=$(shell_quote "$remote_component")"
  command_prefix+=" PANTHEON_DEPLOY_SHA=$(shell_quote "$DEPLOY_SHA")"
  command_prefix+=" PANTHEON_REMOTE_DIR=$(shell_quote "$remote_dir")"
  command_prefix+=" PANTHEON_DEPLOY_WORKTREE_ROOT=$(shell_quote "${PANTHEON_DEPLOY_WORKTREE_ROOT:-}")"
  command_prefix+=" PANTHEON_GITHUB_TOKEN=$(shell_quote "${GITHUB_TOKEN:-}")"
  command_prefix+=" PANTHEON_ALLOW_DIRTY_DEPLOY=$(shell_quote "$ALLOW_DIRTY")"
  command_prefix+=" PANTHEON_ALLOW_EXAMPLE_ENV=$(shell_quote "$ALLOW_EXAMPLE_ENV")"
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
  if [[ "${PANTHEON_ALLOW_DIRTY_DEPLOY}" == "true" ]]; then
    info "dirty managed deploy worktree allowed by explicit flag"
    return
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    git status --short >&2
    error "managed deploy worktree is dirty; refusing deploy without --allow-dirty"
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

cd "${PANTHEON_REMOTE_DIR}"
git rev-parse --is-inside-work-tree >/dev/null

case "${PANTHEON_DEPLOY_COMPONENT}" in
  root)
    snapshot_remote_state pantheon docker-compose.yml
    prepare_deploy_worktree
    docker compose -p pantheon -f docker-compose.yml config --quiet
    COMPOSE_BAKE=false \
    PANTHEON_ENV=dev \
    PANTHEON_LIVE_BROKER_ENABLED=false \
    PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app \
      docker compose -p pantheon -f docker-compose.yml up -d --build
    curl_with_retry http://127.0.0.1:18001/health
    curl_with_retry http://127.0.0.1:18001/readyz
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
