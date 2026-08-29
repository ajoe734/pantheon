#!/usr/bin/env bash
# Refresh mutable staging, materialize one immutable command runtime, and
# replace the supervisor against one explicit coordination worktree.
#
# The script never infers control-plane authority from a process cwd, a
# product checkout, or a global PID path.  `dev-root` is staging only; live
# runtime state belongs to COORDINATION_ROOT and launch source belongs to the
# exact command-runtimes/<SHA> checkout.
set -euo pipefail

DEV_ROOT="${1:-/home/lupin/pantheon-ci-deploy/dev-root}"
LIVE_CONFIG="${2:-/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json}"
COORDINATION_ROOT="${3:-${PANTHEON_COORDINATION_ROOT:-/home/lupin/pantheon-ci-deploy/coordination-root}}"
AUTHORITY_ENV_FILE="${4:-${PANTHEON_SUPERVISOR_VERIFIER_ENV_FILE:-/home/lupin/pantheon-ci-deploy/runtime/supervisor-authority-public.env}}"
REF="${SYNC_REF:-origin/dev}"
COMMAND_RUNTIME_PARENT="/home/lupin/pantheon-ci-deploy/command-runtimes"
INTEGRATION_RUNTIME_PARENT="${PANTHEON_INTEGRATION_RUNTIME_PARENT:-/home/lupin/pantheon-ci-deploy/integration-runtimes}"
EXECUTE_PLANS_SOURCE_ROOT="${PANTHEON_EXECUTE_PLANS_SOURCE_ROOT:-/home/lupin/code/execute-plans}"
COMMAND_RUNTIME_KEEP="${COMMAND_RUNTIME_KEEP:-5}"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[sync-dev-root $(stamp)] $*"; }

cd "$DEV_ROOT" || { log "FATAL: cannot cd $DEV_ROOT"; exit 1; }
DEV_ROOT="$(pwd -P)"
cd "$COORDINATION_ROOT" || { log "FATAL: cannot cd coordination root $COORDINATION_ROOT"; exit 1; }
COORDINATION_ROOT="$(pwd -P)"
cd "$EXECUTE_PLANS_SOURCE_ROOT" || { log "FATAL: cannot cd execute-plans source root $EXECUTE_PLANS_SOURCE_ROOT"; exit 1; }
EXECUTE_PLANS_SOURCE_ROOT="$(pwd -P)"

if [[ "$DEV_ROOT" == "$COORDINATION_ROOT" ]]; then
  log "FATAL: dev-root is staging and must not also be the coordination root"
  exit 1
fi
if ! git -C "$COORDINATION_ROOT" rev-parse --git-dir >/dev/null 2>&1 \
  || [[ ! -f "$COORDINATION_ROOT/ai-status.json" ]] \
  || [[ ! -d "$COORDINATION_ROOT/.orchestrator" ]]; then
  log "FATAL: coordination root must be a Git worktree with ai-status.json and .orchestrator"
  exit 1
fi

fetch_ref="${REF#origin/}"
if [[ "$REF" == origin/* ]]; then
  fetch_ref="${REF#origin/}:refs/remotes/origin/${REF#origin/}"
fi

if ! git -C "$DEV_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  log "FATAL: dev-root is not a Git checkout: $DEV_ROOT"
  exit 1
fi
if ! git -C "$EXECUTE_PLANS_SOURCE_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  log "FATAL: execute-plans source root is not a Git checkout: $EXECUTE_PLANS_SOURCE_ROOT"
  exit 1
fi
if ! git -C "$DEV_ROOT" fetch --quiet origin "$fetch_ref"; then
  log "FATAL: fetch $REF failed in $DEV_ROOT"
  exit 1
fi
if ! git -C "$EXECUTE_PLANS_SOURCE_ROOT" fetch --quiet origin \
  "dev:refs/remotes/origin/dev"; then
  log "FATAL: fetch origin/dev failed in $EXECUTE_PLANS_SOURCE_ROOT"
  exit 1
fi

behind="$(git -C "$DEV_ROOT" rev-list --count "HEAD..$REF" 2>/dev/null || echo '?')"
head_before="$(git -C "$DEV_ROOT" rev-parse --short HEAD)"
if [[ "$behind" =~ ^[0-9]+$ && "$behind" -gt 0 ]]; then
  if ! git -C "$DEV_ROOT" diff --quiet || ! git -C "$DEV_ROOT" diff --cached --quiet; then
    git -C "$DEV_ROOT" stash push -m "sync-dev-root-staging-${head_before}-$(date -u +%Y%m%dT%H%M%SZ)" >/dev/null
    log "stashed tracked staging changes (recoverable via git stash list)"
  fi
  git -C "$DEV_ROOT" reset --hard "$REF" >/dev/null
  log "updated staging dev-root -> $(git -C "$DEV_ROOT" rev-parse --short HEAD)"
else
  log "staging dev-root at $head_before, behind $REF by ${behind}"
fi

target_sha="$(git -C "$DEV_ROOT" rev-parse "$REF")"
if [[ ! "$target_sha" =~ ^[0-9a-f]{40}$ ]]; then
  log "FATAL: accepted target did not resolve to a lowercase full SHA: $target_sha"
  exit 1
fi
candidate_root="${COMMAND_RUNTIME_PARENT}/${target_sha}"
execute_plans_sha="$(git -C "$EXECUTE_PLANS_SOURCE_ROOT" rev-parse origin/dev)"
if [[ ! "$execute_plans_sha" =~ ^[0-9a-f]{40}$ ]]; then
  log "FATAL: execute-plans origin/dev did not resolve to a lowercase full SHA: $execute_plans_sha"
  exit 1
fi
pantheon_integration_root="${INTEGRATION_RUNTIME_PARENT}/pantheon/${target_sha}"
execute_plans_integration_root="${INTEGRATION_RUNTIME_PARENT}/execute_plans/${execute_plans_sha}"

materialize_integration_runtime() {
  local repository_id="$1" source_root="$2" destination="$3" sha="$4" branch="$5"
  local repository_parent temporary_parent runtime origin_url fetched_sha common_dir
  repository_parent="${INTEGRATION_RUNTIME_PARENT}/${repository_id}"
  if ! python3 - "$repository_parent" "$repository_id" <<'PY'
import re
import stat
import sys
from pathlib import Path

parent = Path(sys.argv[1])
repository_id = sys.argv[2]
if not re.fullmatch(r"[a-z][a-z0-9_]*", repository_id):
    raise SystemExit(f"invalid integration repository id: {repository_id}")
if not parent.is_absolute() or any(part in {".", ".."} for part in parent.parts):
    raise SystemExit(f"integration runtime parent must be canonical absolute path: {parent}")
for component in (parent, *parent.parents):
    if component.is_symlink():
        raise SystemExit(f"integration runtime parent contains symlink component: {component}")
parent.mkdir(parents=True, exist_ok=True)
if not parent.is_dir() or stat.S_ISLNK(parent.lstat().st_mode):
    raise SystemExit(f"integration runtime parent is not a direct directory: {parent}")
PY
  then
    return 1
  fi
  if [[ -L "$destination" ]]; then
    log "ERROR: integration runtime destination is a symlink: $destination"
    return 1
  fi
  origin_url="$(git -C "$source_root" config --get remote.origin.url)"
  if [[ -z "$origin_url" ]]; then
    log "ERROR: repository source has no origin URL: $source_root"
    return 1
  fi
  if [[ ! -e "$destination" ]]; then
    if ! temporary_parent="$(mktemp -d "${repository_parent}/.integration-materialize-${sha}.XXXXXX")"; then
      return 1
    fi
    runtime="${temporary_parent}/runtime"
    if ! git clone --quiet --no-local --no-checkout "$source_root" "$runtime"; then
      rm -rf -- "$temporary_parent"
      return 1
    fi
    if ! git -C "$runtime" remote set-url origin "$origin_url" \
      || ! git -C "$runtime" fetch --quiet --no-tags origin \
        "+refs/heads/${branch}:refs/remotes/origin/${branch}"; then
      rm -rf -- "$temporary_parent"
      return 1
    fi
    fetched_sha="$(git -C "$runtime" rev-parse "origin/${branch}")"
    if [[ "$fetched_sha" != "$sha" ]] \
      || ! git -C "$runtime" checkout --quiet --detach "$sha"; then
      rm -rf -- "$temporary_parent"
      return 1
    fi
    if ! python3 - "$runtime" "$destination" "$repository_parent" <<'PY'
import ctypes
import errno
import os
import sys
from pathlib import Path

source, destination, parent = map(Path, sys.argv[1:])
libc = ctypes.CDLL(None, use_errno=True)
renameat2 = libc.renameat2
renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
renameat2.restype = ctypes.c_int
if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
    error = ctypes.get_errno()
    if error != errno.EEXIST:
        raise OSError(error, os.strerror(error), destination)
fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
    then
      rm -rf -- "$temporary_parent"
      return 1
    fi
    rm -rf -- "$temporary_parent"
  fi

  fetched_sha="$(git -C "$destination" rev-parse HEAD 2>/dev/null || true)"
  common_dir="$(git -C "$destination" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [[ "$fetched_sha" != "$sha" ]] \
    || [[ "$(git -C "$destination" rev-parse --show-toplevel 2>/dev/null || true)" != "$destination" ]] \
    || [[ "$common_dir" != "$destination/.git" ]] \
    || git -C "$destination" symbolic-ref -q HEAD >/dev/null 2>&1 \
    || [[ -n "$(git -C "$destination" status --porcelain --untracked-files=all 2>/dev/null)" ]] \
    || [[ "$(git -C "$destination" config --get remote.origin.url 2>/dev/null || true)" != "$origin_url" ]]; then
    log "ERROR: integration runtime identity/cleanliness validation failed: $destination"
    return 1
  fi
  local checkout_probe common_probe
  checkout_probe="$(mktemp "$destination/.integration-write-probe.XXXXXX")" \
    || { log "ERROR: integration checkout is not writable: $destination"; return 1; }
  common_probe="$(mktemp "$common_dir/.integration-write-probe.XXXXXX")" \
    || { rm -f -- "$checkout_probe"; log "ERROR: integration common-dir is not writable: $common_dir"; return 1; }
  rm -f -- "$checkout_probe" "$common_probe"
}

if ! materialize_integration_runtime \
  "pantheon" "$DEV_ROOT" "$pantheon_integration_root" "$target_sha" "${REF#origin/}"; then
  log "FATAL: Pantheon integration runtime materialization failed: $pantheon_integration_root"
  exit 1
fi
if ! materialize_integration_runtime \
  "execute_plans" "$EXECUTE_PLANS_SOURCE_ROOT" "$execute_plans_integration_root" "$execute_plans_sha" "dev"; then
  log "FATAL: execute-plans integration runtime materialization failed: $execute_plans_integration_root"
  exit 1
fi

current_command_root() {
  python3 - "$LIVE_CONFIG" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
watchdog = payload.get("watchdog")
argv = watchdog.get("supervisor_command") if isinstance(watchdog, dict) else None
if not isinstance(argv, list):
    raise SystemExit(1)
entries = [Path(item) for item in argv if isinstance(item, str) and Path(item).name == "supervisor.py"]
if len(entries) != 1 or not entries[0].is_absolute():
    raise SystemExit(1)
print(entries[0].parent.parent.resolve())
PY
}

install_auto_integrator() {
  local runtime_root="$1"
  local installer="${runtime_root}/scripts/auto_integrator_install.py"
  if [[ ! -f "$installer" ]]; then
    log "WARNING: auto-integrator installer missing from runtime=$runtime_root"
    return 0
  fi
  if python3 -B "$installer" \
    --repo "$runtime_root" \
    --status-root "$COORDINATION_ROOT" \
    --config-file "$LIVE_CONFIG"; then
    log "auto-integrator repointed at runtime=$runtime_root config=$LIVE_CONFIG"
  else
    log "WARNING: auto-integrator install failed for runtime=$runtime_root -- reviewed PRs will remain open until the supervisor-owned integration lane is restored"
  fi
}

config_drift=0
if [[ -f "$LIVE_CONFIG" && -f "$DEV_ROOT/scripts/check_config_drift.py" ]]; then
  drift_report="$(mktemp)"
  if ! python3 "$DEV_ROOT/scripts/check_config_drift.py" \
    --repo-config "$DEV_ROOT/.orchestrator/config.json" \
    --live-config "$LIVE_CONFIG" \
    --dev-root "$DEV_ROOT" --ref "$REF" \
    --repository-source-root "pantheon=$DEV_ROOT" \
    --repository-source-root "execute_plans=$EXECUTE_PLANS_SOURCE_ROOT" \
    --repository-integration-root "pantheon=$pantheon_integration_root" \
    --repository-integration-root "execute_plans=$execute_plans_integration_root" \
    --json >"$drift_report"; then
    config_drift=1
    log "CONFIG_DRIFT_REQUIRES_PROMOTION: $(tr '\n' ' ' <"$drift_report")"
  fi
  rm -f -- "$drift_report"
fi

prune_old_command_runtimes() {
  local prune_script="${DEV_ROOT}/scripts/prune_command_runtimes.py"
  if [[ ! -f "$prune_script" ]]; then
    return 0
  fi
  if ! python3 -B "$prune_script" \
    --parent "$COMMAND_RUNTIME_PARENT" \
    --integration-parent "$INTEGRATION_RUNTIME_PARENT" \
    --live-config "$LIVE_CONFIG" \
    --status-root "$COORDINATION_ROOT" \
    --keep "$COMMAND_RUNTIME_KEEP"; then
    log "WARN: command-runtimes prune failed (non-fatal, promotion unaffected)"
  fi
}

active_root="$(current_command_root 2>/dev/null || true)"
if [[ "$active_root" == "$candidate_root" && "$config_drift" -eq 0 ]]; then
  install_auto_integrator "$candidate_root"
  prune_old_command_runtimes
  log "done (staging=$DEV_ROOT coordination=$COORDINATION_ROOT promotion=no-op-current-runtime)"
  exit 0
fi

materialize_candidate_runtime() {
  local source_root="$1" destination="$2" sha="$3"
  local temporary_parent runtime origin_url accepted_dev
  python3 - "$COMMAND_RUNTIME_PARENT" <<'PY'
import os
import stat
import sys
from pathlib import Path

parent = Path(sys.argv[1])
if not parent.is_absolute() or any(part in {".", ".."} for part in parent.parts):
    raise SystemExit(f"command runtime parent must be canonical absolute path: {parent}")
for component in (parent, *parent.parents):
    if component.is_symlink():
        raise SystemExit(f"command runtime parent contains symlink component: {component}")
parent.mkdir(parents=True, exist_ok=True)
if not parent.is_dir() or stat.S_ISLNK(parent.lstat().st_mode):
    raise SystemExit(f"command runtime parent is not a direct directory: {parent}")
PY
  if [[ -L "$destination" ]]; then
    log "ERROR: immutable candidate destination is a symlink: $destination"
    return 1
  fi
  if [[ ! -e "$destination" ]]; then
    temporary_parent="$(mktemp -d "${COMMAND_RUNTIME_PARENT}/.runtime-materialize-${sha}.XXXXXX")"
    runtime="${temporary_parent}/runtime"
    if ! git clone --quiet --no-local --no-checkout "$source_root" "$runtime"; then
      rm -rf -- "$temporary_parent"
      return 1
    fi
    origin_url="$(git -C "$source_root" config --get remote.origin.url)"
    accepted_dev="$(git -C "$source_root" rev-parse "$REF")"
    git -C "$runtime" remote set-url origin "$origin_url" \
      && git -C "$runtime" fetch --quiet --no-tags "$source_root" "$sha" \
      && git -C "$runtime" update-ref refs/remotes/origin/dev "$accepted_dev" \
      && git -C "$runtime" checkout --quiet --detach "$sha" \
      || { rm -rf -- "$temporary_parent"; return 1; }
    if ! python3 - "$runtime" "$destination" "$COMMAND_RUNTIME_PARENT" <<'PY'
import ctypes
import errno
import os
import sys
from pathlib import Path

source, destination, parent = map(Path, sys.argv[1:])
libc = ctypes.CDLL(None, use_errno=True)
renameat2 = libc.renameat2
renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
renameat2.restype = ctypes.c_int
if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
    error = ctypes.get_errno()
    if error != errno.EEXIST:
        raise OSError(error, os.strerror(error), destination)
fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
    then
      rm -rf -- "$temporary_parent"
      return 1
    fi
    rm -rf -- "$temporary_parent"
  fi
  python3 -B "$destination/scripts/provision_live_supervisor_config.py" \
    --command-root "$destination" --validate-command-root-only >/dev/null
}

if ! materialize_candidate_runtime "$DEV_ROOT" "$candidate_root" "$target_sha"; then
  log "FATAL: immutable candidate materialization/validation failed for $candidate_root"
  exit 1
fi

log "replacing supervisor from explicit config identity=${active_root:-none} candidate=$candidate_root coordination=$COORDINATION_ROOT"
if ! "$candidate_root/scripts/promote-supervisor-runtime.sh" \
  --promote --repo "$candidate_root" --status-root "$COORDINATION_ROOT" \
  --live-config "$LIVE_CONFIG" \
  --authority-env-file "$AUTHORITY_ENV_FILE" \
  --repository-source-root "pantheon=$DEV_ROOT" \
  --repository-source-root "execute_plans=$EXECUTE_PLANS_SOURCE_ROOT" \
  --repository-integration-root "pantheon=$pantheon_integration_root" \
  --repository-integration-root "execute_plans=$execute_plans_integration_root"; then
  log "FATAL: supervisor replacement failed"
  exit 1
fi

# The systemd/cron watchdog is a separate, non-LLM safety net that restarts a
# dead supervisor. Its unit hardcodes an absolute path into this exact
# immutable command-runtimes/<sha> checkout, which is pruned over time -- so
# without this step it silently goes stale on every promotion, and the
# restart-if-dead safety net quietly stops working once that sha is gone.
# Best-effort: a watchdog install problem must never block landing new
# supervisor code, so failures here are logged, never fatal.
if [[ -f "$candidate_root/scripts/supervisor_watchdog_install.py" ]]; then
  if [[ -f "$AUTHORITY_ENV_FILE" && ! -L "$AUTHORITY_ENV_FILE" ]]; then
    if python3 -B "$candidate_root/scripts/supervisor_watchdog_install.py" \
      --repo "$candidate_root" \
      --config "$LIVE_CONFIG" \
      --authority-env-file "$AUTHORITY_ENV_FILE" \
      --method auto \
      --start-now; then
      log "watchdog repointed at candidate=$candidate_root"
    else
      log "WARNING: watchdog repoint failed for candidate=$candidate_root -- supervisor code landed, but the restart-if-dead safety net may be stale until this is fixed"
    fi
  else
    log "WARNING: watchdog authority env file missing or not a regular file ($AUTHORITY_ENV_FILE) -- skipped watchdog repoint"
  fi
fi

install_auto_integrator "$candidate_root"

prune_old_command_runtimes
log "done (staging=$DEV_ROOT coordination=$COORDINATION_ROOT promotion=replaced)"
