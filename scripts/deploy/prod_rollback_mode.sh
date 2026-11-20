#!/usr/bin/env bash
# Pantheon production protected ROLLBACK operation (workflow `operation=rollback`).
#
# Runs prod_rollback.sh for the ACTIVE release after a successful switch, with fail-closed preconditions and the
# post-rollback verification the switch itself uses. Application rollback only: this is NOT data recovery and never
# touches the data disk. Every refusal below happens BEFORE any mutation.
#
# Required env: PROD_ROOT PROD_OPS_CMD RELEASE_ID PROD_BLUE_PORT PROD_GREEN_PORT
# Optional env: RELEASE_MANIFEST_PATH (default ${PROD_ROOT}/manifests/<release_id>.json), PROD_ROLLBACK_BUDGET_SECONDS (120)
# Refusal exit codes (JSON on stderr, error_code):
#   1 MISSING_INPUT  10 ROLLBACK_RELEASE_NOT_ACTIVE  11 ROLLBACK_NO_JOURNAL  12 ROLLBACK_BOOTSTRAP_JOURNAL
#   13 ROLLBACK_NO_BASELINE  14 ROLLBACK_NO_MANIFEST
# After the rollback: 0 success (one JSON result line on stdout), 3 restoration failed, 6 over budget,
#   7 post-rollback verification failed; prod_rollback.sh exit 4 is passed through.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
"${SCRIPT_DIR}/prod_rollback.sh" "$RELEASE_ID"; rc=$?
if (( rc != 0 )); then result rollback_failed "$rc"; exit "$rc"; fi
# Reuse the switch's checks: restored owners at baseline digests + runtime-manager credential check.
if ! "$PROD_OPS_CMD" verify_rollback "$RELEASE_ID"; then result rollback_unverified 7; exit 7; fi
ELAPSED_S=$(( ($(date +%s%N) - START_NS) / 1000000000 ))
if (( ELAPSED_S > BUDGET )); then result rollback_over_budget 6; exit 6; fi
printf '{"release_id":"%s","release_state":"prod_rolled_back"}\n' "$RELEASE_ID" > "${STATE}/${RELEASE_ID}.json"
result rolled_back 0
exit 0
