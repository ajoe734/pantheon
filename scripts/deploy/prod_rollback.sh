#!/usr/bin/env bash
# Pantheon production rollback: restore FE symlink, BFF upstream, runtime-config,
# active manifest/baseline.json/compose env and the baseline singleton owner to the journaled
# baseline. For an explicit first-release bootstrap abort, stops both the green
# candidate and any started singleton and restores the safe 503 no-active-release
# proxy/FE posture without leaving routes or symlinks pointing at the stopped candidate.
# Application rollback only; this is NOT database recovery and never touches the
# production data disk.
#
# Usage: prod_rollback.sh <release_id> | prod_rollback.sh --protected <release_id>
# Env:   PROD_ROOT (state/fe/proxy/config tree), PROD_OPS_CMD (side-effect adapter)
# Exit:  0 rolled back, 3 restoration failed, 4 no rollback target (bootstrap)

set -uo pipefail

# --- Protected rollback operation: prod_rollback.sh --protected <release_id> (workflow operation=rollback) ---
# Fail-closed preconditions (each refusal changes nothing), then the plain rollback below, then post-rollback verification
# via `prod_ops.sh verify_rollback` (restored owner digests + runtime-manager credential check; public FE/BFF identity is
# already proven by baseline_health inside the plain rollback). Needs PROD_BLUE_PORT PROD_GREEN_PORT as well.
# Refusal exit codes (JSON on stderr): 1 MISSING_INPUT 10 ROLLBACK_RELEASE_NOT_ACTIVE 11 ROLLBACK_NO_JOURNAL
#   12 ROLLBACK_BOOTSTRAP_JOURNAL 13 ROLLBACK_NO_BASELINE 14 ROLLBACK_NO_MANIFEST.
# After the rollback: 0 ok (one JSON result line on stdout), 3 restoration failed, 4 passthrough, 6 over budget
#   (PROD_ROLLBACK_BUDGET_SECONDS, default 120), 7 post-rollback verification failed.
if [[ "${1:-}" == "--protected" ]]; then
  shift
  SELF="${BASH_SOURCE[0]}"
  RELEASE_ID="${1:?release_id required}"
  PROD_ROOT="${PROD_ROOT:?PROD_ROOT required}"
  PROD_OPS_CMD="${PROD_OPS_CMD:?PROD_OPS_CMD required}"
  (
refuse() { printf '{"status":"refused","error_code":"%s","message":"%s"}\n' "$2" "$3" >&2; exit "$1"; }
for v in PROD_ROOT PROD_OPS_CMD RELEASE_ID PROD_BLUE_PORT PROD_GREEN_PORT; do
  [[ -n "${!v:-}" ]] || refuse 1 MISSING_INPUT "required input ${v} is missing"
done
[[ "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]] || refuse 1 MISSING_INPUT "release_id has invalid characters"

STATE="${PROD_ROOT}/state"
JOURNAL="${STATE}/journal/${RELEASE_ID}"
BUDGET="${PROD_ROLLBACK_BUDGET_SECONDS:-120}"

# Precondition 1: release_id must be the release currently active.
ACTIVE_ID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("release_id",""))' "${STATE}/active_manifest.json" 2>/dev/null)" || ACTIVE_ID=""
[[ -n "$ACTIVE_ID" && "$ACTIVE_ID" == "$RELEASE_ID" ]] \
  || refuse 10 ROLLBACK_RELEASE_NOT_ACTIVE "release_id does not equal the active release; nothing changed"
# Precondition 2: current journal exists and is not a bootstrap journal (rolling back a first release removes Production).
[[ -d "$JOURNAL" ]] || refuse 11 ROLLBACK_NO_JOURNAL "no current journal for the active release; nothing changed"
[[ ! -e "${JOURNAL}/bootstrap" ]] \
  || refuse 12 ROLLBACK_BOOTSTRAP_JOURNAL "active release is a bootstrap release; rolling back would remove Production; nothing changed"
# Precondition 3: the journal names a retained baseline.
BASE_PORT="$(python3 - "${JOURNAL}/baseline_release.json" "$RELEASE_ID" <<'PY' 2>/dev/null
import json, sys
rec = json.load(open(sys.argv[1]))
rid = str(rec.get("release_id") or "").strip()
img = str(rec.get("operator_bff_image") or "")
port = str(rec.get("active_port") or "").strip()
if not rid or rid == sys.argv[2] or rec.get("healthy") is not True or "@sha256:" not in img or not port:
    sys.exit(1)
print(port)
PY
)" || refuse 13 ROLLBACK_NO_BASELINE "journal does not name a retained healthy baseline; nothing changed"
PREV_FE="$(cat "${JOURNAL}/prev_fe_target" 2>/dev/null)"
[[ -n "$PREV_FE" && -d "$PREV_FE" && -f "${JOURNAL}/prev_upstream.caddy" ]] \
  || refuse 13 ROLLBACK_NO_BASELINE "retained baseline FE directory or upstream is missing; nothing changed"

MANIFEST="${RELEASE_MANIFEST_PATH:-${PROD_ROOT}/manifests/${RELEASE_ID}.json}"
[[ -f "$MANIFEST" ]] || refuse 14 ROLLBACK_NO_MANIFEST "retained release manifest is missing; nothing changed"

# Port roles exactly as the switch set them for rollback: ACTIVE = baseline port, TARGET = the candidate being undone.
if [[ "$BASE_PORT" == "$PROD_GREEN_PORT" ]]; then TARGET_PORT="$PROD_BLUE_PORT"; else BASE_PORT="$PROD_BLUE_PORT"; TARGET_PORT="$PROD_GREEN_PORT"; fi
export PROD_ACTIVE_PORT="$BASE_PORT" PROD_TARGET_PORT="$TARGET_PORT" RELEASE_MANIFEST_PATH="$MANIFEST"
export PROD_BOOTSTRAP_FIRST_RELEASE=false RELEASE_ID PROD_ROOT PROD_OPS_CMD

BASE_ID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["release_id"])' "${JOURNAL}/baseline_release.json")"
result() { # result <status> <exit_code>
  local elapsed_ms=$(( ($(date +%s%N) - START_NS) / 1000000 ))
  printf '{"status":"%s","from_release":"%s","to_release":"%s","elapsed_ms":%s,"rollback_budget_seconds":%s,"exit_code":%s}\n' \
    "$1" "$RELEASE_ID" "$BASE_ID" "$elapsed_ms" "$BUDGET" "$2"
}
START_NS="$(date +%s%N)"
"$SELF" "$RELEASE_ID"; rc=$?
if (( rc != 0 )); then result rollback_failed "$rc"; exit "$rc"; fi
# Reuse the switch's checks: restored owners at baseline digests + runtime-manager credential check.
if ! "$PROD_OPS_CMD" verify_rollback "$RELEASE_ID"; then result rollback_unverified 7; exit 7; fi
ELAPSED_S=$(( ($(date +%s%N) - START_NS) / 1000000000 ))
if (( ELAPSED_S > BUDGET )); then result rollback_over_budget 6; exit 6; fi
printf '{"release_id":"%s","release_state":"prod_rolled_back"}\n' "$RELEASE_ID" > "${STATE}/${RELEASE_ID}.json"
result rolled_back 0
exit 0
  )
  exit $?
fi

RELEASE_ID="${1:?release_id required}"
PROD_ROOT="${PROD_ROOT:?PROD_ROOT required}"
PROD_OPS_CMD="${PROD_OPS_CMD:?PROD_OPS_CMD required}"
JOURNAL="${PROD_ROOT}/state/journal/${RELEASE_ID}"
FE_CURRENT="${PROD_ROOT}/fe/current"
UPSTREAM_FILE="${PROD_ROOT}/proxy/bff_upstream.caddy"
RUNTIME_CONFIG_FILE="${PROD_ROOT}/config/runtime-config.js"
ACTIVE_MANIFEST_FILE="${PROD_ROOT}/state/active_manifest.json"
BASELINE_FILE="${PROD_ROOT}/state/baseline.json"
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
  # No prior baseline existed: a baseline.json written by a successful bootstrap must not survive as a fake healthy target.
  rm -f "$FE_CURRENT" "$ACTIVE_MANIFEST_FILE" "$BASELINE_FILE"
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
  if [[ -f "${JOURNAL}/prev_baseline.json" ]]; then
    cp "${JOURNAL}/prev_baseline.json" "${BASELINE_FILE}.rollback.$$" || return 1
    mv -f "${BASELINE_FILE}.rollback.$$" "$BASELINE_FILE" || return 1
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
