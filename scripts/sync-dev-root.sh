#!/usr/bin/env bash
# Keep the mutable dev-root staging checkout in sync with origin/dev, then hand
# supervisor changes to the immutable runtime promotion transaction.  dev-root
# is never supervisor command authority and this script never directly signals
# a supervisor.
#
#   - resolve the root the LIVE supervisor process actually runs from
#     (/proc/<pid>/cwd) only to bind the incumbent.  An immutable active root is
#     evidence and rollback authority: it is never fetched, stashed, reset, or
#     otherwise updated in place.
#   - fetch origin/dev into mutable dev-root and refresh that staging checkout.
#   - materialize the accepted target as a standalone, clean
#     command-runtimes/<40-hex> checkout when promotion is needed.
#   - ask promote-supervisor-runtime.sh to perform the config/PID transaction.
#     The promotion operator owns intent, TERM, config CAS, launch, and rollback.
set -uo pipefail

DEV_ROOT="${1:-/home/lupin/pantheon-ci-deploy/dev-root}"
LIVE_CONFIG="${2:-/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json}"
REF="${SYNC_REF:-origin/dev}"
PID_FILE="${PANTHEON_SUPERVISOR_PID:-/home/lupin/pantheon/.orchestrator/supervisor.pid}"
COMMAND_RUNTIME_PARENT="/home/lupin/pantheon-ci-deploy/command-runtimes"
DEV_TOOL_RESIDUE_PATHS=(
  ".orchestrator/assistant-dev-packets"
  ".orchestrator/evidence"
  ".orchestrator/task-briefs"
  ".orchestrator/status-derived-views.lock"
  ".orchestrator/supervisor.lock"
)
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[sync-dev-root $(stamp)] $*"; }

cd "$DEV_ROOT" || { log "FATAL: cannot cd $DEV_ROOT"; exit 1; }
DEV_ROOT="$(pwd -P)"

fetch_ref="${REF#origin/}"
if [[ "$REF" == origin/* ]]; then
  # dev-root intentionally has a narrow remote fetch config. An unqualified
  # `git fetch origin dev` updates FETCH_HEAD only and can leave origin/dev
  # stale, which previously produced a false "behind 0" result. Update the
  # exact remote-tracking ref consumed below regardless of that config.
  fetch_ref="${REF#origin/}:refs/remotes/origin/${REF#origin/}"
fi

# The pid file is the same one the watchdog uses; /proc/<pid>/cwd is the only
# authority for which checkout is actually executing the control loop.
pid="$(cat "$PID_FILE" 2>/dev/null || true)"
ACTIVE_ROOT=""
root_split=0
if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  active_cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  if [[ -n "$active_cwd" ]]; then
    ACTIVE_ROOT="$(git -C "$active_cwd" rev-parse --show-toplevel 2>/dev/null || true)"
  fi
fi
if [[ -z "$ACTIVE_ROOT" ]]; then
  log "active supervisor root unresolved (pid=${pid:-none}); syncing $DEV_ROOT only"
elif [[ "$ACTIVE_ROOT" == "$DEV_ROOT" ]]; then
  log "active supervisor root matches dev-root ($DEV_ROOT)"
else
  root_split=1
  log "ACTIVE_ROOT_SPLIT_PROTECTED: live supervisor pid=$pid runs from $ACTIVE_ROOT, not $DEV_ROOT"
fi

updated=0
source_advance=0
sync_root() {
  # $1 = repo root, $2 = label, $3 = whether this checkout may move.
  local root="$1" label="$2" may_move="$3" behind head_before
  if ! git -C "$root" rev-parse --git-dir >/dev/null 2>&1; then
    log "ERROR: $label $root is not a git checkout; skipping"
    return 1
  fi
  if ! git -C "$root" fetch --quiet origin "$fetch_ref"; then
    log "fetch $REF failed in $label $root"
    return 1
  fi
  behind="$(git -C "$root" rev-list --count "HEAD..$REF" 2>/dev/null || echo '?')"
  head_before="$(git -C "$root" rev-parse --short HEAD 2>/dev/null || echo '?')"
  log "$label ($root) at $head_before, behind $REF by ${behind}"
  if [[ "$behind" =~ ^[0-9]+$ && "$behind" -gt 0 ]]; then
    source_advance=1
    if [[ "$may_move" != "1" ]]; then
      log "ACTIVE_MUTABLE_ROOT_PROTECTED: fetched $REF but will not stash or reset running root $root"
      return 0
    fi
    if ! git -C "$root" diff --quiet || ! git -C "$root" diff --cached --quiet; then
      git -C "$root" stash push -m "sync-dev-root-dirty-${head_before}-$(date -u +%Y%m%dT%H%M%SZ)" \
        >/dev/null 2>&1 && log "$label stashed dirty tracked changes (recoverable via git stash list)"
    fi
    if git -C "$root" reset --hard "$REF" >/dev/null 2>&1; then
      updated=1
      log "updated $label -> $(git -C "$root" rev-parse --short HEAD)"
    else
      log "ERROR: git reset --hard $REF failed in $label $root"
      return 1
    fi
  fi
  return 0
}

clean_dev_tool_residue() {
  # Remove only known ephemeral development-tool residue. Never use an
  # unscoped `git clean -fdx`: dev-root may contain ignored local configuration
  # that is unrelated to supervisor staging and must survive a source refresh.
  local root="$1" may_clean="$2" preview
  if [[ "$may_clean" != "1" ]]; then
    log "DEV_TOOL_RESIDUE_CLEANUP_SKIPPED: $root is the active supervisor root"
    return 0
  fi
  if [[ -z "$ACTIVE_ROOT" || "$ACTIVE_ROOT" == "$root" ]]; then
    log "DEV_TOOL_RESIDUE_CLEANUP_SKIPPED: immutable incumbent is not independently bound"
    return 0
  fi
  if ! preview="$(git -C "$root" clean -ndx -- "${DEV_TOOL_RESIDUE_PATHS[@]}")"; then
    log "ERROR: scoped development-tool residue preview failed in $root"
    return 1
  fi
  if [[ -z "$preview" ]]; then
    log "development-tool residue already clean in $root"
    return 0
  fi
  if ! git -C "$root" clean -fdx -- "${DEV_TOOL_RESIDUE_PATHS[@]}" >/dev/null; then
    log "ERROR: scoped development-tool residue cleanup failed in $root"
    return 1
  fi
  log "removed scoped development-tool residue from $root"
}

config_updated=0
live_hash_before="$(sha256sum "$LIVE_CONFIG" 2>/dev/null | awk '{print $1}')"

dev_root_may_move=1
if [[ -n "$ACTIVE_ROOT" && "$ACTIVE_ROOT" == "$DEV_ROOT" ]]; then
  dev_root_may_move=0
fi
if ! sync_root "$DEV_ROOT" "dev-root" "$dev_root_may_move"; then
  log "FATAL: cannot sync dev-root $DEV_ROOT"; exit 1
fi
if ! clean_dev_tool_residue "$DEV_ROOT" "$dev_root_may_move"; then
  log "FATAL: cannot clean scoped development-tool residue in $DEV_ROOT"; exit 1
fi
target_sha="$(git -C "$DEV_ROOT" rev-parse "$REF")"
if [[ ! "$target_sha" =~ ^[0-9a-f]{40}$ ]]; then
  log "FATAL: accepted target did not resolve to a lowercase full SHA: $target_sha"
  exit 1
fi
candidate_root="${COMMAND_RUNTIME_PARENT}/${target_sha}"

if [[ -n "$ACTIVE_ROOT" && "$ACTIVE_ROOT" != "$DEV_ROOT" ]]; then
  log "leaving active immutable supervisor root untouched: $ACTIVE_ROOT"
  log "evidence: dev-root HEAD=$(git -C "$DEV_ROOT" rev-parse HEAD) active HEAD=$(git -C "$ACTIVE_ROOT" rev-parse HEAD 2>/dev/null || echo unknown) target=$(git -C "$DEV_ROOT" rev-parse "$REF")"
fi

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

materialize_candidate_runtime() {
  local source_root="$1" destination="$2" sha="$3"
  local temporary_parent runtime origin_url accepted_dev
  python3 - "$COMMAND_RUNTIME_PARENT" <<'PY' || return 1
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
    --command-root "$destination" \
    --validate-command-root-only >/dev/null
}

if [[ -z "$ACTIVE_ROOT" ]]; then
  if [[ "$source_advance" -eq 1 || "$config_drift" -eq 1 ]]; then
    log "PROMOTION_HANDOFF_REQUIRED: no live incumbent is bound; run the governed dev first-install/deploy path"
  fi
  log "done (updated=${updated} source_advance=${source_advance} config_drift=${config_drift} root_split=${root_split} promotion=no-incumbent)"
  exit "$config_drift"
fi

if [[ "$ACTIVE_ROOT" == "$candidate_root" ]]; then
  if [[ "$config_drift" -eq 1 ]]; then
    log "FATAL: active immutable runtime already equals target but config drift exists; select a new accepted runtime for governed promotion"
    exit 1
  fi
  log "done (updated=${updated} source_advance=${source_advance} config_drift=0 root_split=${root_split} promotion=no-op-current-root)"
  exit 0
fi

if ! materialize_candidate_runtime "$DEV_ROOT" "$candidate_root" "$target_sha"; then
  log "FATAL: immutable candidate materialization/validation failed for $candidate_root"
  exit 1
fi

promotion_args=(--promote --repo "$candidate_root")
log "requesting governed supervisor promotion incumbent=$ACTIVE_ROOT candidate=$candidate_root"
if ! "$candidate_root/scripts/promote-supervisor-runtime.sh" "${promotion_args[@]}"; then
  log "FATAL: governed supervisor promotion handoff failed; incumbent was not directly signalled by sync-dev-root"
  exit 1
fi

live_hash_after="$(sha256sum "$LIVE_CONFIG" 2>/dev/null | awk '{print $1}')"
if [[ "$live_hash_before" != "$live_hash_after" ]]; then
  config_updated=1
fi
log "done (updated=${updated} source_advance=${source_advance} config_updated=${config_updated} config_drift=${config_drift} root_split=${root_split} promotion=requested)"
