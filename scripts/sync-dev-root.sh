#!/usr/bin/env bash
# Keep the supervisor's dev-root checkout in sync with origin/dev and self-heal
# config drift. dev-root has no built-in auto-sync, so merged fixes (incl.
# supervisor.py) never go live until someone manually deploys -- this closes
# that gap. Safe to run on a cron: a no-op when already current.
#
#   - fetch origin/dev; if dev-root is behind, stash dirty TRACKED changes
#     (untracked runtime files like watchdog-state.json are left in place) and
#     fast-forward via `git reset --hard origin/dev`.
#   - run check_config_drift.py --fix to realign non-allowlisted live config
#     toggles (e.g. a hand-disabled chair_review) and report dev-root lag.
#   - if code changed, SIGTERM the supervisor so the watchdog cron relaunches
#     it on the new code/config (flock guarantees a single instance).
set -uo pipefail

DEV_ROOT="${1:-/home/lupin/pantheon-ci-deploy/dev-root}"
LIVE_CONFIG="${2:-/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json}"
REF="${SYNC_REF:-origin/dev}"
PID_FILE="${PANTHEON_SUPERVISOR_PID:-/home/lupin/code/pantheon/.orchestrator/supervisor.pid}"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[sync-dev-root $(stamp)] $*"; }

cd "$DEV_ROOT" || { log "FATAL: cannot cd $DEV_ROOT"; exit 1; }

if ! git fetch --quiet origin "${REF#origin/}"; then
  log "fetch $REF failed; aborting"; exit 1
fi

behind="$(git rev-list --count "HEAD..$REF" 2>/dev/null || echo '?')"
log "dev-root behind $REF by ${behind}"

updated=0
if [[ "$behind" =~ ^[0-9]+$ && "$behind" -gt 0 ]]; then
  if ! git diff --quiet || ! git diff --cached --quiet; then
    git stash push -m "sync-dev-root-dirty-$(git rev-parse --short HEAD)-$(date -u +%Y%m%dT%H%M%SZ)" \
      >/dev/null 2>&1 && log "stashed dirty tracked changes (recoverable via git stash list)"
  fi
  if git reset --hard "$REF" >/dev/null 2>&1; then
    updated=1
    log "updated dev-root -> $(git rev-parse --short HEAD)"
  else
    log "ERROR: git reset --hard $REF failed"
  fi
fi

if [[ -f scripts/check_config_drift.py ]]; then
  python3 scripts/check_config_drift.py \
    --repo-config .orchestrator/config.json \
    --live-config "$LIVE_CONFIG" \
    --dev-root "$DEV_ROOT" --ref "$REF" --fix || log "config-drift guard reported drift (auto-fixed where allowed)"
fi

if [[ "$updated" -eq 1 ]]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    log "restarting supervisor pid=$pid to load new code (watchdog cron will relaunch)"
    kill -TERM "$pid" 2>/dev/null || true
  fi
fi

log "done (updated=${updated})"
