#!/usr/bin/env bash
set -euo pipefail

# This wrapper can execute from an immutable command runtime.  Export the
# inheritable form before any helper interpreter starts; ``python -B`` alone
# would not protect Python subprocesses launched by ai_status.py.
export PYTHONDONTWRITEBYTECODE=1

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

is_auto_worker_status_command() {
  [[ -n "${ORCH_RUN_ID:-}" ]] || [[ -n "${PANTHEON_WORKTREE_ROOT:-}" ]] || [[ -n "${ORCH_WORKSPACE_PATH:-}" ]]
}

fail_closed() {
  echo "$*" >&2
  exit 2
}

REPO_TASK_STATE_MODE=""
if [[ -f "$ROOT_DIR/.orchestrator/config.json" ]]; then
  REPO_TASK_STATE_MODE="$(python3 -c 'import json,sys; print(str((json.load(open(sys.argv[1])).get("task_state_store") or {}).get("mode") or "").strip().lower())' "$ROOT_DIR/.orchestrator/config.json")"
fi

if [[ "$REPO_TASK_STATE_MODE" == "authoritative" ]]; then
  [[ "${PANTHEON_TASK_STATE_STORE_MODE:-}" == "authoritative" ]] \
    || fail_closed "PANTHEON_TASK_STATE_STORE_MODE=authoritative is required by the installed command runtime"
  [[ "${PANTHEON_TASK_STATE_EVENT_LOG:-}" = /* ]] \
    || fail_closed "PANTHEON_TASK_STATE_EVENT_LOG must be an absolute path in authoritative mode"
fi

first_symlink_component() {
  local path="$1"
  local rest part current
  rest="${path#/}"
  current="/"
  IFS='/' read -r -a parts <<<"$rest"
  for part in "${parts[@]}"; do
    [[ -z "$part" ]] && continue
    if [[ "$current" == "/" ]]; then
      current="/$part"
    else
      current="$current/$part"
    fi
    if [[ -L "$current" ]]; then
      printf '%s\n' "$current"
      return 0
    fi
    if [[ ! -e "$current" ]]; then
      return 1
    fi
  done
  return 1
}

normalize_github_slug() {
  local value="$1"
  value="${value%.git}"
  case "$value" in
    git@github.com:*) value="${value#git@github.com:}" ;;
    ssh://git@github.com/*) value="${value#ssh://git@github.com/}" ;;
    https://github.com/*) value="${value#https://github.com/}" ;;
    http://github.com/*) value="${value#http://github.com/}" ;;
  esac
  value="${value#/}"
  value="${value%/}"
  printf '%s\n' "$value"
}

validate_command_root() {
  local raw_root="$1"
  local expected_sha="${PANTHEON_COMMAND_RUNTIME_SHA:-}"
  local expected_remote="${PANTHEON_COMMAND_REMOTE:-ajoe734/pantheon}"
  local base_ref="${PANTHEON_COMMAND_BASE_REF:-origin/dev}"
  local symlink_component command_root git_root source_sha remote_url actual_remote expected_slug

  [[ -n "$raw_root" ]] || fail_closed "PANTHEON_COMMAND_ROOT is required for auto-worker status commands"
  [[ "$raw_root" = /* ]] || fail_closed "PANTHEON_COMMAND_ROOT must be an absolute path"
  if symlink_component="$(first_symlink_component "$raw_root")"; then
    fail_closed "PANTHEON_COMMAND_ROOT cannot include a symlink component: $symlink_component"
  fi
  [[ -d "$raw_root" ]] || fail_closed "PANTHEON_COMMAND_ROOT does not exist or is not a directory: $raw_root"
  command_root="$(cd "$raw_root" && pwd -P)"
  git_root="$(git -C "$command_root" rev-parse --show-toplevel 2>/dev/null)" \
    || fail_closed "PANTHEON_COMMAND_ROOT must be a git repository root: $command_root"
  git_root="$(cd "$git_root" && pwd -P)"
  [[ "$git_root" == "$command_root" ]] \
    || fail_closed "PANTHEON_COMMAND_ROOT must be a git repository root: $command_root"

  source_sha="$(git -C "$command_root" rev-parse HEAD 2>/dev/null)" \
    || fail_closed "PANTHEON_COMMAND_ROOT source SHA is unavailable: $command_root"
  [[ -n "$expected_sha" ]] || fail_closed "PANTHEON_COMMAND_RUNTIME_SHA is required for auto-worker status commands"
  [[ "$source_sha" == "$expected_sha" ]] \
    || fail_closed "PANTHEON_COMMAND_RUNTIME_SHA mismatch: command root is $source_sha, expected $expected_sha"

  remote_url="$(git -C "$command_root" remote get-url origin 2>/dev/null)" \
    || fail_closed "PANTHEON_COMMAND_ROOT must have origin remote: $command_root"
  actual_remote="$(normalize_github_slug "$remote_url")"
  expected_slug="$(normalize_github_slug "$expected_remote")"
  [[ "$actual_remote" == "$expected_slug" ]] \
    || fail_closed "PANTHEON_COMMAND_ROOT remote mismatch: $actual_remote != $expected_slug"

  git -C "$command_root" rev-parse --verify "$base_ref" >/dev/null 2>&1 \
    || fail_closed "PANTHEON_COMMAND_BASE_REF is unavailable: $base_ref"
  git -C "$command_root" merge-base --is-ancestor "$source_sha" "$base_ref" >/dev/null 2>&1 \
    || fail_closed "PANTHEON_COMMAND_ROOT source SHA $source_sha is not merged into $base_ref"

  [[ -f "$command_root/scripts/ai_status.py" ]] \
    || fail_closed "PANTHEON_COMMAND_ROOT is missing scripts/ai_status.py: $command_root"
  printf '%s\n' "$command_root"
}

if is_auto_worker_status_command; then
  [[ -n "${PANTHEON_STATUS_ROOT:-}" ]] \
    || fail_closed "PANTHEON_STATUS_ROOT is required for auto-worker status commands"
fi

if is_auto_worker_status_command || [[ -n "${PANTHEON_COMMAND_ROOT:-}" ]]; then
  COMMAND_ROOT="$(validate_command_root "${PANTHEON_COMMAND_ROOT:-}")"
  export PANTHEON_STATUS_COMMAND_WRAPPER_ROOT="$ROOT_DIR"
  exec python3 -B "$COMMAND_ROOT/scripts/ai_status.py" "$@"
fi

exec python3 -B "$ROOT_DIR/scripts/ai_status.py" "$@"
