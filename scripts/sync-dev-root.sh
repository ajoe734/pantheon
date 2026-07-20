#!/usr/bin/env bash
# Keep the supervisor's dev-root checkout in sync with origin/dev and self-heal
# config drift. dev-root has no built-in auto-sync, so merged fixes (incl.
# supervisor.py) never go live until someone manually deploys -- this closes
# that gap. Safe to run on a cron: a no-op when already current.
#
#   - fetch origin/dev; if dev-root is behind, stash dirty TRACKED changes
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

fetch_ref="${REF#origin/}"
if [[ "$REF" == origin/* ]]; then
  # dev-root intentionally has a narrow remote fetch config. An unqualified
  # `git fetch origin dev` updates FETCH_HEAD only and can leave origin/dev
  # stale, which previously produced a false "behind 0" result. Update the
  # exact remote-tracking ref consumed below regardless of that config.
  fetch_ref="${REF#origin/}:refs/remotes/origin/${REF#origin/}"
fi
if ! git fetch --quiet origin "$fetch_ref"; then
  log "fetch $REF failed; aborting"; exit 1
fi

behind="$(git rev-list --count "HEAD..$REF" 2>/dev/null || echo '?')"
log "dev-root behind $REF by ${behind}"

updated=0
config_updated=0
live_hash_before="$(sha256sum "$LIVE_CONFIG" 2>/dev/null | awk '{print $1}')"
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
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    target_sha="$(git rev-parse HEAD)"
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
