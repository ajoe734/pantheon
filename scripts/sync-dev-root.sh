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
REF="${SYNC_REF:-origin/dev}"
COMMAND_RUNTIME_PARENT="/home/lupin/pantheon-ci-deploy/command-runtimes"
COMMAND_RUNTIME_KEEP="${COMMAND_RUNTIME_KEEP:-5}"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[sync-dev-root $(stamp)] $*"; }

cd "$DEV_ROOT" || { log "FATAL: cannot cd $DEV_ROOT"; exit 1; }
DEV_ROOT="$(pwd -P)"
cd "$COORDINATION_ROOT" || { log "FATAL: cannot cd coordination root $COORDINATION_ROOT"; exit 1; }
COORDINATION_ROOT="$(pwd -P)"

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
if ! git -C "$DEV_ROOT" fetch --quiet origin "$fetch_ref"; then
  log "FATAL: fetch $REF failed in $DEV_ROOT"
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

config_drift=0
if [[ -f "$LIVE_CONFIG" && -f "$DEV_ROOT/scripts/check_config_drift.py" ]]; then
  drift_report="$(mktemp)"
  if ! python3 "$DEV_ROOT/scripts/check_config_drift.py" \
    --repo-config "$DEV_ROOT/.orchestrator/config.json" \
    --live-config "$LIVE_CONFIG" \
    --dev-root "$DEV_ROOT" --ref "$REF" --json >"$drift_report"; then
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
    --live-config "$LIVE_CONFIG" \
    --status-root "$COORDINATION_ROOT" \
    --keep "$COMMAND_RUNTIME_KEEP"; then
    log "WARN: command-runtimes prune failed (non-fatal, promotion unaffected)"
  fi
}

active_root="$(current_command_root 2>/dev/null || true)"
if [[ "$active_root" == "$candidate_root" && "$config_drift" -eq 0 ]]; then
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
  --live-config "$LIVE_CONFIG"; then
  log "FATAL: supervisor replacement failed"
  exit 1
fi

prune_old_command_runtimes
log "done (staging=$DEV_ROOT coordination=$COORDINATION_ROOT promotion=replaced)"
