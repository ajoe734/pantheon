#!/usr/bin/env bash
# Pantheon production blue/green atomic switch controller.
#
# Never rebuilds FE or BFF: it accepts only the immutable manifest artifacts.
# Sequence: preflight -> snapshot + healthy baseline -> stage FE dir -> green on alternate
# port -> gates -> singleton drain -> BFF upstream switch -> FE atomic symlink switch ->
# post-switch exact identity. Any failure after the first mutation runs prod_rollback.sh.
#
# Side effects on the VM go through PROD_OPS_CMD <step> <release_id> [args]; the controller
# owns only the local state tree (PROD_ROOT). Local runs with a fake adapter prove controller
# behavior only; they are never hosted drill evidence.
#
# Required env: PROD_ROOT PROD_OPS_CMD RELEASE_ID RELEASE_MANIFEST_PATH EXPECTED_MANIFEST_SHA256
#   STAGING_EVIDENCE_PATH APPROVALS_PATH WORKFLOW_INITIATOR FE_ARTIFACT_PATH
#   PROD_BLUE_PORT PROD_GREEN_PORT plus PROD_* configuration (see prod_preflight.py).
# Optional: ENV_POLICY_PATH (actual GitHub production Environment record; absent => approver must differ from initiator),
#   PROD_BOOTSTRAP_FIRST_RELEASE=true, PROD_ROLLBACK_BUDGET_SECONDS (default 120).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die_json() { printf '{"status":"error","error_code":"%s","message":"%s"}\n' "$1" "$2" >&2; exit 1; }

for v in PROD_ROOT PROD_OPS_CMD RELEASE_ID RELEASE_MANIFEST_PATH EXPECTED_MANIFEST_SHA256 \
         STAGING_EVIDENCE_PATH APPROVALS_PATH WORKFLOW_INITIATOR FE_ARTIFACT_PATH \
         PROD_BLUE_PORT PROD_GREEN_PORT; do
  [[ -n "${!v:-}" ]] || die_json "MISSING_INPUT" "required input ${v} is missing"
done
[[ "$PROD_BLUE_PORT" != "$PROD_GREEN_PORT" ]] || die_json "PORT_COLLISION" "green must use an alternate port"

STATE="${PROD_ROOT}/state"
JOURNAL="${STATE}/journal/${RELEASE_ID}"
FE_CURRENT="${PROD_ROOT}/fe/current"
FE_RELEASE="${PROD_ROOT}/fe/releases/${RELEASE_ID}"
UPSTREAM_FILE="${PROD_ROOT}/proxy/bff_upstream.caddy"
BASELINE_FILE="${STATE}/baseline.json"
BUDGET="${PROD_ROLLBACK_BUDGET_SECONDS:-120}"
mkdir -p "$STATE" "${PROD_ROOT}/fe/releases" "${PROD_ROOT}/proxy"

PREFLIGHT_ARGS=(all --manifest "$RELEASE_MANIFEST_PATH" --expected-sha256 "$EXPECTED_MANIFEST_SHA256"
  --staging-evidence "$STAGING_EVIDENCE_PATH" --approvals "$APPROVALS_PATH"
  --initiator "$WORKFLOW_INITIATOR" --baseline "$BASELINE_FILE")
[[ -z "${ENV_POLICY_PATH:-}" ]] || PREFLIGHT_ARGS+=(--env-policy "$ENV_POLICY_PATH")
BOOTSTRAP=false
if [[ "${PROD_BOOTSTRAP_FIRST_RELEASE:-false}" == "true" ]]; then
  BOOTSTRAP=true; PREFLIGHT_ARGS+=(--bootstrap)
fi

# 1. Fail-closed preflight (config, manifest, staging evidence, approval, baseline posture).
PREFLIGHT_OUT="$(python3 "${SCRIPT_DIR}/prod_preflight.py" "${PREFLIGHT_ARGS[@]}")" || {
  printf '%s\n' "$PREFLIGHT_OUT" >&2
  die_json "PREFLIGHT_FAILED" "production preflight failed closed; no state mutated"
}
FE_SHA="$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["frontend_artifact_sha256"])' "$PREFLIGHT_OUT")"
BFF_IMAGE="$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["operator_bff_image"])' "$PREFLIGHT_OUT")"

# 2. Immutable artifact enforcement: FE bytes must match the manifest checksum.
ACTUAL_FE_SHA="sha256:$(sha256sum "$FE_ARTIFACT_PATH" | cut -d' ' -f1)"
[[ "$ACTUAL_FE_SHA" == "$FE_SHA" ]] || die_json "FE_ARTIFACT_MISMATCH" "FE artifact sha ${ACTUAL_FE_SHA} != manifest ${FE_SHA}"
if [[ -e "$FE_RELEASE" ]]; then
  [[ "$(cat "${FE_RELEASE}/.artifact_sha256" 2>/dev/null)" == "$FE_SHA" ]] \
    || die_json "IMMUTABLE_RELEASE_DIR" "release dir exists with different content; never overwritten"
fi

# 3. Snapshot preflight + healthy retained baseline (absent only for explicit bootstrap).
"$PROD_OPS_CMD" snapshot_preflight "$RELEASE_ID" || die_json "SNAPSHOT_FAILED" "pre-release snapshot failed; nothing switched"
if [[ "$BOOTSTRAP" == "false" ]]; then
  "$PROD_OPS_CMD" baseline_health "$RELEASE_ID" || die_json "BASELINE_UNHEALTHY" "retained baseline is not healthy; refusing to switch"
fi

# 4. Journal the restore targets BEFORE any mutation.
mkdir -p "$JOURNAL"
if [[ "$BOOTSTRAP" == "true" ]]; then
  : > "${JOURNAL}/bootstrap"
else
  readlink "$FE_CURRENT" > "${JOURNAL}/prev_fe_target" || die_json "NO_CURRENT_FE" "baseline FE symlink missing"
  cp "$UPSTREAM_FILE" "${JOURNAL}/prev_upstream.caddy" || die_json "NO_CURRENT_UPSTREAM" "baseline BFF upstream missing"
fi

rollback_and_exit() {
  local reason="$1"
  printf '{"status":"switch_failed","release_id":"%s","reason":"%s"}\n' "$RELEASE_ID" "$reason" >&2
  local t0 rc elapsed
  t0="$(date +%s)"
  "${SCRIPT_DIR}/prod_rollback.sh" "$RELEASE_ID"; rc=$?
  elapsed=$(( $(date +%s) - t0 ))
  case "$rc" in
    0) if (( elapsed > BUDGET )); then
         printf '{"status":"rollback_over_budget","elapsed_s":%s,"budget_s":%s}\n' "$elapsed" "$BUDGET" >&2; exit 6
       fi
       write_state prod_rolled_back; exit 2 ;;
    4) write_state prod_aborted; exit 4 ;;
    *) write_state rollback_failed; exit 3 ;;
  esac
}
write_state() {
  printf '{"release_id":"%s","release_state":"%s","manifest_sha256":"%s"}\n' \
    "$RELEASE_ID" "$1" "$EXPECTED_MANIFEST_SHA256" > "${STATE}/${RELEASE_ID}.json"
}

# 5. Stage FE into its own immutable directory (no live traffic yet).
if [[ ! -e "$FE_RELEASE" ]]; then
  STAGE_TMP="${FE_RELEASE}.staging.$$"
  mkdir -p "$STAGE_TMP" && tar -xzf "$FE_ARTIFACT_PATH" -C "$STAGE_TMP" \
    && printf '%s' "$FE_SHA" > "${STAGE_TMP}/.artifact_sha256" \
    && mv -T "$STAGE_TMP" "$FE_RELEASE" \
    || { rm -rf "$STAGE_TMP"; "$PROD_OPS_CMD" stop_green "$RELEASE_ID" 2>/dev/null; die_json "FE_STAGE_FAILED" "FE staging failed; nothing switched"; }
fi

# 6. Green on the alternate port, then every pre-traffic gate.
if ! "$PROD_OPS_CMD" start_green "$RELEASE_ID" "$PROD_GREEN_PORT" "$BFF_IMAGE"; then
  "$PROD_OPS_CMD" stop_green "$RELEASE_ID" 2>/dev/null
  write_state prod_aborted
  die_json "GREEN_START_FAILED" "green failed to start; blue untouched"
fi
for gate in health auth cors tenant read_only schema old_new_compat; do
  if ! "$PROD_OPS_CMD" gate "$RELEASE_ID" "$gate" "$PROD_GREEN_PORT"; then
    "$PROD_OPS_CMD" stop_green "$RELEASE_ID" 2>/dev/null
    write_state prod_aborted
    die_json "GATE_FAILED" "pre-traffic gate ${gate} failed; blue untouched"
  fi
done
write_state prod_green_verified

# 7. Singleton drain: exactly one owner at any time (never run blue and green owners together).
"$PROD_OPS_CMD" drain_singletons "$RELEASE_ID" || rollback_and_exit "singleton_drain_failed"
: > "${JOURNAL}/singletons_drained"

# 8. Compensated BFF upstream switch (atomic file replace + proxy reload).
printf 'reverse_proxy 127.0.0.1:%s\n' "$PROD_GREEN_PORT" > "${UPSTREAM_FILE}.new.$$" || rollback_and_exit "upstream_write_failed"
mv -f "${UPSTREAM_FILE}.new.$$" "$UPSTREAM_FILE" || rollback_and_exit "upstream_replace_failed"
: > "${JOURNAL}/bff_switched"
"$PROD_OPS_CMD" reload_proxy "$RELEASE_ID" || rollback_and_exit "proxy_reload_failed"
"$PROD_OPS_CMD" activate_singletons "$RELEASE_ID" || rollback_and_exit "singleton_activate_failed"

# 9. Atomic FE symlink switch.
ln -sfn "$FE_RELEASE" "${FE_CURRENT}.new.$$" || rollback_and_exit "fe_link_failed"
mv -T -f "${FE_CURRENT}.new.$$" "$FE_CURRENT" || rollback_and_exit "fe_switch_failed"
: > "${JOURNAL}/fe_switched"

# 10. Post-switch exact hosted identities (FE artifact sha + BFF image digest).
IDENTITY="$("$PROD_OPS_CMD" post_identity "$RELEASE_ID")" || rollback_and_exit "post_identity_unavailable"
IDENT_OK="$(python3 - "$IDENTITY" "$FE_SHA" "$BFF_IMAGE" <<'PY'
import json, sys
try:
    got = json.loads(sys.argv[1])
except json.JSONDecodeError:
    print("no"); raise SystemExit
print("yes" if got.get("frontend_artifact_sha256") == sys.argv[2] and got.get("operator_bff_image") == sys.argv[3] else "no")
PY
)"
[[ "$IDENT_OK" == "yes" ]] || rollback_and_exit "post_switch_identity_mismatch"

# Retain exact previous pair as rollback baseline; this release becomes the new baseline.
printf '{"release_id":"%s","healthy":true,"frontend_artifact_sha256":"%s","operator_bff_image":"%s","blue_port":"%s"}\n' \
  "$RELEASE_ID" "$FE_SHA" "$BFF_IMAGE" "$PROD_GREEN_PORT" > "${STATE}/baseline.json.new.$$" \
  && mv -f "${STATE}/baseline.json.new.$$" "$BASELINE_FILE"
write_state prod_active
printf '{"status":"prod_active","release_id":"%s","observation_window_minutes":30,"note":"release_complete requires hosted drills and 30-minute observation; not asserted here"}\n' "$RELEASE_ID"
