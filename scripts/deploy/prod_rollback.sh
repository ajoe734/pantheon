#!/usr/bin/env bash
# Pantheon production rollback: restore FE symlink, BFF upstream, runtime-config,
# active manifest/compose env and the baseline singleton owner to the journaled
# baseline. For an explicit first-release bootstrap abort, stops both the green
# candidate and any started singleton and restores the safe 503 no-active-release
# proxy/FE posture without leaving routes or symlinks pointing at the stopped candidate.
# Application rollback only; this is NOT database recovery and never touches the
# production data disk.
#
# Usage: prod_rollback.sh <release_id>
# Env:   PROD_ROOT (state/fe/proxy/config tree), PROD_OPS_CMD (side-effect adapter)
# Exit:  0 rolled back, 3 restoration failed, 4 no rollback target (bootstrap)

set -uo pipefail

RELEASE_ID="${1:?release_id required}"
PROD_ROOT="${PROD_ROOT:?PROD_ROOT required}"
PROD_OPS_CMD="${PROD_OPS_CMD:?PROD_OPS_CMD required}"
JOURNAL="${PROD_ROOT}/state/journal/${RELEASE_ID}"
FE_CURRENT="${PROD_ROOT}/fe/current"
UPSTREAM_FILE="${PROD_ROOT}/proxy/bff_upstream.caddy"
RUNTIME_CONFIG_FILE="${PROD_ROOT}/config/runtime-config.js"
ACTIVE_MANIFEST_FILE="${PROD_ROOT}/state/active_manifest.json"
COMPOSE_ENV_FILE="${PROD_COMPOSE_ENV_FILE:-${PROD_ROOT}/config/compose.env}"
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
  # Must stop candidate + singleton and restore safe no-active-release proxy/FE posture.
  mkdir -p "$(dirname "$UPSTREAM_FILE")" "${PROD_ROOT}/config"
  printf 'respond "no production release active" 503\n' > "${UPSTREAM_FILE}.rollback.$$" \
    && mv -f "${UPSTREAM_FILE}.rollback.$$" "$UPSTREAM_FILE"
  rm -f "$FE_CURRENT" "$ACTIVE_MANIFEST_FILE"
  if [[ -f "${JOURNAL}/prev_runtime_config.js" ]]; then
    cp "${JOURNAL}/prev_runtime_config.js" "${RUNTIME_CONFIG_FILE}.rollback.$$" \
      && mv -f "${RUNTIME_CONFIG_FILE}.rollback.$$" "$RUNTIME_CONFIG_FILE"
  else
    rm -f "$RUNTIME_CONFIG_FILE"
  fi
  if [[ -f "${JOURNAL}/singletons_drained" ]]; then
    "$PROD_OPS_CMD" drain_singletons "$RELEASE_ID" || true
  fi
  "$PROD_OPS_CMD" stop_green "$RELEASE_ID" || true
  if [[ -f "${JOURNAL}/bff_switched" ]]; then
    "$PROD_OPS_CMD" reload_proxy "$RELEASE_ID" || true
  fi
  rm -f "${JOURNAL}/bff_switched" "${JOURNAL}/fe_switched" "${JOURNAL}/singletons_drained" 2>/dev/null || true
  emit no_rollback_target "bootstrap release has no prior baseline; candidate and singleton stopped, safe 503 posture restored"
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
restore_runtime_config() {
  if [[ -f "${JOURNAL}/prev_runtime_config.js" ]]; then
    cp "${JOURNAL}/prev_runtime_config.js" "${RUNTIME_CONFIG_FILE}.rollback.$$" || return 1
    mv -f "${RUNTIME_CONFIG_FILE}.rollback.$$" "$RUNTIME_CONFIG_FILE" || return 1
  fi
  if [[ -f "${JOURNAL}/prev_manifest.json" ]]; then
    cp "${JOURNAL}/prev_manifest.json" "${ACTIVE_MANIFEST_FILE}.rollback.$$" || return 1
    mv -f "${ACTIVE_MANIFEST_FILE}.rollback.$$" "$ACTIVE_MANIFEST_FILE" || return 1
  fi
  if [[ -f "${JOURNAL}/prev_compose.env" ]]; then
    cp "${JOURNAL}/prev_compose.env" "${COMPOSE_ENV_FILE}.rollback.$$" || return 1
    mv -f "${COMPOSE_ENV_FILE}.rollback.$$" "$COMPOSE_ENV_FILE" || return 1
  fi
  return 0
}

step restore_upstream restore_upstream
step restore_fe_symlink restore_symlink
step restore_runtime_config restore_runtime_config
step reload_proxy "$PROD_OPS_CMD" reload_proxy "$RELEASE_ID"
# Hand the singleton owners back to the baseline exactly once; green must be stopped first.
step stop_green "$PROD_OPS_CMD" stop_green "$RELEASE_ID"
step resume_baseline_singletons "$PROD_OPS_CMD" resume_baseline_singletons "$RELEASE_ID"
step baseline_health "$PROD_OPS_CMD" baseline_health "$RELEASE_ID"

if (( ${#FAILED[@]} > 0 )); then
  emit rollback_failed "failed steps: ${FAILED[*]}"
  exit 3
fi
emit rolled_back "FE, BFF upstream, runtime config and singleton owner restored to baseline"
exit 0
