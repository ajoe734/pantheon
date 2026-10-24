#!/usr/bin/env bash
# Pantheon production rollback: restore FE symlink, BFF upstream/config and the
# singleton owner to the journaled baseline. Application rollback only; this is
# NOT database recovery and never touches the production data disk.
#
# Usage: prod_rollback.sh <release_id>
# Env:   PROD_ROOT (state/fe/proxy tree), PROD_OPS_CMD (side-effect adapter)
# Exit:  0 rolled back, 3 restoration failed, 4 no rollback target (bootstrap)

set -uo pipefail

RELEASE_ID="${1:?release_id required}"
PROD_ROOT="${PROD_ROOT:?PROD_ROOT required}"
PROD_OPS_CMD="${PROD_OPS_CMD:?PROD_OPS_CMD required}"
JOURNAL="${PROD_ROOT}/state/journal/${RELEASE_ID}"
FE_CURRENT="${PROD_ROOT}/fe/current"
UPSTREAM_FILE="${PROD_ROOT}/proxy/bff_upstream.caddy"
START_NS="$(date +%s%N)"
FAILED=()

emit() { printf '{"status":"%s","release_id":"%s","detail":"%s","elapsed_ms":%s}\n' "$1" "$RELEASE_ID" "$2" "$(( ($(date +%s%N) - START_NS) / 1000000 ))"; }
step() { # step <name> <command...>
  local name="$1"; shift
  if ! "$@"; then FAILED+=("$name"); fi
}

if [[ ! -d "$JOURNAL" ]]; then
  emit rollback_failed "journal missing"; exit 3
fi

if [[ -f "${JOURNAL}/bootstrap" ]]; then
  # First release: there is no prior healthy release to restore. Do not invent one.
  "$PROD_OPS_CMD" stop_green "$RELEASE_ID" || true
  rm -f "${JOURNAL}/bff_switched" "${JOURNAL}/fe_switched" 2>/dev/null || true
  emit no_rollback_target "bootstrap release has no prior baseline; green stopped"
  exit 4
fi

restore_upstream() {
  [[ -f "${JOURNAL}/prev_upstream.caddy" ]] || return 1
  cp "${JOURNAL}/prev_upstream.caddy" "${UPSTREAM_FILE}.rollback.$$" || return 1
  mv -f "${UPSTREAM_FILE}.rollback.$$" "$UPSTREAM_FILE"
}
restore_symlink() {
  local prev
  prev="$(cat "${JOURNAL}/prev_fe_target" 2>/dev/null)" || return 1
  [[ -n "$prev" && -d "$prev" ]] || return 1
  ln -sfn "$prev" "${FE_CURRENT}.rollback.$$" || return 1
  mv -T -f "${FE_CURRENT}.rollback.$$" "$FE_CURRENT"
}

step restore_upstream restore_upstream
step restore_fe_symlink restore_symlink
step reload_proxy "$PROD_OPS_CMD" reload_proxy "$RELEASE_ID"
# Hand the singleton owners back to the baseline exactly once; green must be stopped first.
step stop_green "$PROD_OPS_CMD" stop_green "$RELEASE_ID"
step resume_baseline_singletons "$PROD_OPS_CMD" resume_baseline_singletons "$RELEASE_ID"
step baseline_health "$PROD_OPS_CMD" baseline_health "$RELEASE_ID"

if (( ${#FAILED[@]} > 0 )); then
  emit rollback_failed "failed steps: ${FAILED[*]}"
  exit 3
fi
emit rolled_back "FE, BFF upstream and singleton owner restored to baseline"
exit 0
