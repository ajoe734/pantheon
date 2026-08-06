#!/usr/bin/env bash
# Keep the supervisor's dev-root checkout in sync with origin/dev and self-heal
# config drift. dev-root has no built-in auto-sync, so merged fixes (incl.
# supervisor.py) never go live until someone manually deploys -- this closes
# that gap. Safe to run on a cron: a no-op when already current.
#
#   - resolve the root the LIVE supervisor process actually runs from
#     (/proc/<pid>/cwd) and sync that root too. Syncing only the default
#     dev-root path is what let the live supervisor sit 63 commits behind
#     origin/dev while this script reported success (SUP-PROVIDER-POOL-PROBE-
#     GATE-001). Set SYNC_ACTIVE_ROOT=0 to report the split without repairing it.
#   - fetch origin/dev; if a root is behind, stash dirty TRACKED changes
#     (untracked runtime files like watchdog-state.json are left in place) and
#     fast-forward via `git reset --hard origin/dev`.
#   - re-provision the split-root live config so repo-owned schema migrations
#     land while environment overrides and canonical status paths are preserved;
#   - run check_config_drift.py --fix to realign non-allowlisted live config
#     toggles (e.g. a hand-disabled chair_review) and report dev-root lag;
#   - if code or live config changed, durably declare a PID-bound intentional
#     restart before SIGTERM so the watchdog relaunches without charging the
#     crash-loop budget (flock guarantees one instance).
set -uo pipefail

DEV_ROOT="${1:-/home/lupin/pantheon-ci-deploy/dev-root}"
LIVE_CONFIG="${2:-/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json}"
STATUS_ROOT="${PANTHEON_STATUS_ROOT:-/home/lupin/pantheon}"
REF="${SYNC_REF:-origin/dev}"
PID_FILE="${PANTHEON_SUPERVISOR_PID:-/home/lupin/pantheon/.orchestrator/supervisor.pid}"
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
  log "ACTIVE_ROOT_SPLIT: live supervisor pid=$pid runs from $ACTIVE_ROOT, not $DEV_ROOT"
fi

updated=0
sync_root() {
  # $1 = repo root, $2 = label. Sets `updated=1` when the root moved.
  local root="$1" label="$2" behind head_before
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

config_updated=0
live_hash_before="$(sha256sum "$LIVE_CONFIG" 2>/dev/null | awk '{print $1}')"

if ! sync_root "$DEV_ROOT" "dev-root"; then
  log "FATAL: cannot sync dev-root $DEV_ROOT"; exit 1
fi
if [[ -n "$ACTIVE_ROOT" && "$ACTIVE_ROOT" != "$DEV_ROOT" ]]; then
  if [[ "${SYNC_ACTIVE_ROOT:-1}" == "1" ]]; then
    sync_root "$ACTIVE_ROOT" "active-supervisor-root" || log "active supervisor root sync failed; split persists"
  else
    log "SYNC_ACTIVE_ROOT=0; leaving active supervisor root $ACTIVE_ROOT unrepaired"
  fi
  log "evidence: dev-root HEAD=$(git -C "$DEV_ROOT" rev-parse HEAD) active HEAD=$(git -C "$ACTIVE_ROOT" rev-parse HEAD 2>/dev/null || echo unknown) target=$(git -C "$DEV_ROOT" rev-parse "$REF")"
fi

if [[ -f scripts/provision_live_supervisor_config.py && -f .orchestrator/config.json ]]; then
  if ! python3 scripts/provision_live_supervisor_config.py \
    --repo-config .orchestrator/config.json \
    --live-config "$LIVE_CONFIG" \
    --command-root "$DEV_ROOT" \
    --status-root "$STATUS_ROOT" >/dev/null; then
    log "FATAL: live supervisor config provisioning failed"
    exit 1
  fi
fi

if [[ -f scripts/check_config_drift.py ]]; then
  python3 scripts/check_config_drift.py \
    --repo-config .orchestrator/config.json \
    --live-config "$LIVE_CONFIG" \
    --dev-root "$DEV_ROOT" --ref "$REF" --fix || log "config-drift guard reported drift (auto-fixed where allowed)"
fi

live_hash_after="$(sha256sum "$LIVE_CONFIG" 2>/dev/null | awk '{print $1}')"
if [[ "$live_hash_before" != "$live_hash_after" ]]; then
  config_updated=1
  log "updated split-root live supervisor config"
fi

if [[ "$updated" -eq 1 || "$config_updated" -eq 1 ]]; then
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    target_sha="$(git -C "$DEV_ROOT" rev-parse "$REF")"
    if ! python3 "$DEV_ROOT/.orchestrator/supervisor_watchdog.py" \
      --config "$LIVE_CONFIG" \
      --record-intent-pid "$pid" \
      --record-intent-target "$target_sha" >/dev/null; then
      log "FATAL: failed to record intentional supervisor restart; leaving pid=$pid running"
      exit 1
    fi
    log "recorded intentional supervisor restart pid=$pid target=${target_sha:0:10}"
    log "restarting supervisor pid=$pid to load new code (watchdog cron will relaunch)"
    kill -TERM "$pid" 2>/dev/null || true
  fi
fi

log "done (updated=${updated} config_updated=${config_updated})"
