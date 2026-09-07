#!/usr/bin/env bash
# Run the governed dev paper baseline bootstrap and, only on failure, collect
# bounded diagnostics before returning control to the dev-environment-lease
# guard. This script *is* the guarded child: it must execute entirely while
# the guard still owns the lease and heartbeat, because a later, separate
# guard invocation cannot observe a heartbeat this same guard already stopped
# for quarantine after a failing child (see run_with_dev_environment_lease.sh
# cleanup_command / stop_heartbeat_for_quarantine).
#
# Collector failure or timeout must never change the baseline's original
# exit status: this script always exits with that exact status so the
# caller's guard performs its normal failure recording and quarantine.
#
# Required environment:
#   DEV_PAPER_DIAGNOSTICS_DIR        runner-local directory prepared by the
#                                     caller before the guard was launched.
#                                     Only diagnostics.json, SHA256SUMS and
#                                     collection-status.json are ever written
#                                     here; this directory is uploaded whole,
#                                     so nothing raw/unbounded may land in it.
#   DEV_PAPER_DIAGNOSTICS_COLLECTOR  path to collect_dev_paper_diagnostics.py
#   EXPECTED_BFF_SHA                 full 40-hex candidate BFF commit SHA
#
# Optional environment:
#   EXPECTED_FE_SHA                  full 40-hex candidate FE commit SHA
#   DEV_PAPER_RUN_ID                 numeric run identity (e.g. GITHUB_RUN_ID)
#   DEV_PAPER_ATTEMPT                numeric attempt identity
#   DEV_PAPER_PHASE                  short [a-z0-9_-] phase label
#   DEV_PAPER_DIAGNOSTICS_TIMEOUT_SECONDS  overall collection deadline
#                                     (default 120); bounds a stalled/hung
#                                     SSH channel that per-command/connect
#                                     timeouts inside the collector cannot.
set -uo pipefail

error() {
  echo "[dev-paper-baseline-diagnostics] ERROR: $*" >&2
}

[[ -n "${DEV_PAPER_DIAGNOSTICS_DIR:-}" && -d "${DEV_PAPER_DIAGNOSTICS_DIR}" ]] \
  || { error "DEV_PAPER_DIAGNOSTICS_DIR is required and must exist"; exit 75; }
[[ -f "${DEV_PAPER_DIAGNOSTICS_COLLECTOR:-}" && ! -L "${DEV_PAPER_DIAGNOSTICS_COLLECTOR}" ]] \
  || { error "DEV_PAPER_DIAGNOSTICS_COLLECTOR is missing or is a symlink"; exit 75; }
[[ "${EXPECTED_BFF_SHA:-}" =~ ^[0-9a-f]{40}$ ]] \
  || { error "EXPECTED_BFF_SHA must be a full commit SHA"; exit 75; }
[[ -z "${EXPECTED_FE_SHA:-}" || "${EXPECTED_FE_SHA}" =~ ^[0-9a-f]{40}$ ]] \
  || { error "EXPECTED_FE_SHA must be empty or a full commit SHA"; exit 75; }
[[ -z "${DEV_PAPER_RUN_ID:-}" || "${DEV_PAPER_RUN_ID}" =~ ^[0-9]{1,20}$ ]] \
  || { error "DEV_PAPER_RUN_ID must be empty or numeric"; exit 75; }
[[ -z "${DEV_PAPER_ATTEMPT:-}" || "${DEV_PAPER_ATTEMPT}" =~ ^[0-9]{1,10}$ ]] \
  || { error "DEV_PAPER_ATTEMPT must be empty or numeric"; exit 75; }
[[ -z "${DEV_PAPER_PHASE:-}" || "${DEV_PAPER_PHASE}" =~ ^[a-z0-9_-]{1,64}$ ]] \
  || { error "DEV_PAPER_PHASE must be empty or a short lowercase token"; exit 75; }
[[ "${DEV_PAPER_DIAGNOSTICS_TIMEOUT_SECONDS:-120}" =~ ^[0-9]{1,4}$ ]] \
  || { error "DEV_PAPER_DIAGNOSTICS_TIMEOUT_SECONDS must be numeric"; exit 75; }
collection_deadline_seconds="${DEV_PAPER_DIAGNOSTICS_TIMEOUT_SECONDS:-120}"

status_file="${DEV_PAPER_DIAGNOSTICS_DIR}/collection-status.json"
diagnostic_file="${DEV_PAPER_DIAGNOSTICS_DIR}/diagnostics.json"
diagnostic_tmp="${diagnostic_file}.tmp.$$"
checksum_file="${DEV_PAPER_DIAGNOSTICS_DIR}/SHA256SUMS"

# Deliberately outside DEV_PAPER_DIAGNOSTICS_DIR: that directory is uploaded,
# and raw remote stderr may contain unbounded/unsanitized output (including
# secret sentinels). Raw stderr is NEVER copied into any uploaded artifact.
# Only bounded structural allowlisted failure categories are emitted.
collector_stderr_raw="$(mktemp "${DEV_PAPER_DIAGNOSTICS_DIR%/}.collector-stderr.XXXXXX")"
cleanup() {
  rm -f "${collector_stderr_raw}" "${diagnostic_tmp}" 2>/dev/null
}
on_signal() {
  local sig="$1"
  cleanup
  trap - "${sig}"
  kill -s "${sig}" $$
}
trap cleanup EXIT
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

write_status() {
  local collection_status="$1"
  local bootstrap_exit="$2"
  local collector_exit="${3:-}"
  local explicit_category="${4:-}"
  local tmp="${status_file}.tmp.$$"

  python3 - "${tmp}" "${status_file}" "${collection_status}" "${bootstrap_exit}" "${collector_exit}" "${explicit_category}" "${collector_stderr_raw}" <<'PY'
import datetime as dt
import json
import re
import sys

tmp, status_file, collection_status, bootstrap_exit, collector_exit, explicit_category, stderr_path = sys.argv[1:8]

bootstrap_exit_value = None
if bootstrap_exit not in ("", "null"):
    try:
        bootstrap_exit_value = int(bootstrap_exit)
    except ValueError:
        bootstrap_exit_value = None

collector_exit_value = None
if collector_exit not in ("", "null"):
    try:
        collector_exit_value = int(collector_exit)
    except ValueError:
        collector_exit_value = None

# Bounded structural allowlisted failure fields ONLY.
# Raw stderr, tokens, passwords, bodies, and free-form messages are NEVER copied.
error_category = explicit_category if explicit_category not in ("", "null") else None
if not error_category and collector_exit_value not in (0, None):
    if collector_exit_value in (124, 137):
        error_category = "timeout"
    else:
        raw_stderr = b""
        try:
            with open(stderr_path, "rb") as handle:
                raw_stderr = handle.read(4096)
        except OSError:
            pass
        if raw_stderr:
            text = raw_stderr.decode("utf-8", errors="replace")
            if re.search(r"(?i)connection\s+timed\s+out|connecttimeout", text):
                error_category = "ssh_connection_timeout"
            elif re.search(r"(?i)connection\s+refused", text):
                error_category = "ssh_connection_refused"
            elif re.search(r"(?i)host\s+key\s+verification\s+failed", text):
                error_category = "ssh_host_key_mismatch"
            elif re.search(r"(?i)permission\s+denied", text):
                error_category = "ssh_permission_denied"
            elif re.search(r"(?i)no\s+route\s+to\s+host", text):
                error_category = "ssh_no_route"
            elif re.search(r"(?i)could\s+not\s+resolve\s+hostname", text):
                error_category = "ssh_dns_failure"
            else:
                m = re.search(r"\b(SyntaxError|ImportError|ModuleNotFoundError|MemoryError|TimeoutError|PersonaWriteOwnerUnavailable|ProvisioningLeaseLost)\b", text)
                if m:
                    error_category = f"python_{m.group(1)}"
                else:
                    error_category = "collector_command_failed"

document = {
    "schemaVersion": 1,
    "collectionStatus": collection_status,
    "bootstrapExit": bootstrap_exit_value,
    "collectorExit": collector_exit_value,
    "collectorErrorCategory": error_category,
    "collectorStderrSummary": error_category,
    "collectedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(document, handle, sort_keys=True)
    handle.write("\n")
PY
  mv -f "${tmp}" "${status_file}"
}

SSH="${GITHUB_WORKSPACE}/.agora-gate-controller/scripts/dev_vm_ssh.sh"
[[ -f "${SSH}" && ! -L "${SSH}" ]] || { error "dev_vm_ssh.sh is missing or is a symlink"; exit 75; }

# Initialize runner-known terminal evidence before running anything. A
# cancellation or lease loss that kills this whole process group must still
# leave behind a status file that says collection never started/finished,
# instead of erasing all evidence by never writing one.
write_status "not_started" "" "" ""

bootstrap_status=0
"${SSH}" exec \
  "set -euo pipefail; docker exec pantheon-operator-bff-1 python /workspace/scripts/bootstrap_dev_paper_baseline.py --timeout-seconds 420 --poll-seconds 5" \
  || bootstrap_status=$?

if [[ "${bootstrap_status}" -eq 0 ]]; then
  write_status "not_required" "${bootstrap_status}" 0 ""
  exit 0
fi

# Baseline termination due to signal (e.g. lease loss or cancellation by guard)
# must terminate the wrapper immediately without invoking remote diagnostics.
if [[ "${bootstrap_status}" -eq 130 || "${bootstrap_status}" -eq 143 ]]; then
  exit "${bootstrap_status}"
fi

# Baseline failed. The lease and heartbeat are still owned by this guarded
# child; collect now, while capture is still possible, then return the
# original failure unchanged. Record that collection is in flight (with the
# already-known bootstrap exit) before running the collector, so a
# collection-phase cancellation/timeout still leaves the real bootstrap
# outcome on disk instead of nothing.
write_status "collecting" "${bootstrap_status}" "" ""

collector_args=(--expected-bff-sha "${EXPECTED_BFF_SHA}")
[[ -n "${EXPECTED_FE_SHA:-}" ]] && collector_args+=(--expected-fe-sha "${EXPECTED_FE_SHA}")
[[ -n "${DEV_PAPER_RUN_ID:-}" ]] && collector_args+=(--run-id "${DEV_PAPER_RUN_ID}")
[[ -n "${DEV_PAPER_ATTEMPT:-}" ]] && collector_args+=(--attempt "${DEV_PAPER_ATTEMPT}")
[[ -n "${DEV_PAPER_PHASE:-}" ]] && collector_args+=(--phase "${DEV_PAPER_PHASE}")
collector_args+=(--bootstrap-exit "${bootstrap_status}")

collector_invocation="python3 -"
for arg in "${collector_args[@]}"; do
  collector_invocation+=" ${arg}"
done

collector_rc=0
# Use --foreground so timeout does NOT create an independent process group
# outside the pinned guard's COMMAND_PGID. This keeps the collector and all
# descendants under the guard's pause/termination authority (STOP/TERM/CONT/KILL).
timeout --foreground --kill-after=5s "${collection_deadline_seconds}s" \
  "${SSH}" exec "${collector_invocation}" \
    <"${DEV_PAPER_DIAGNOSTICS_COLLECTOR}" >"${diagnostic_tmp}" 2>"${collector_stderr_raw}" \
  || collector_rc=$?

if [[ "${collector_rc}" -eq 124 || "${collector_rc}" -eq 137 ]]; then
  collection_status="timeout"
  rm -f "${diagnostic_tmp}"
  write_status "${collection_status}" "${bootstrap_status}" "${collector_rc}" "timeout"
elif [[ "${collector_rc}" -ne 0 ]]; then
  collection_status="collector_command_failed"
  rm -f "${diagnostic_tmp}"
  write_status "${collection_status}" "${bootstrap_status}" "${collector_rc}" ""
else
  validation_outcome="$(
    python3 - "${diagnostic_tmp}" "${diagnostic_file}" "${checksum_file}" \
      "${EXPECTED_BFF_SHA}" "${EXPECTED_FE_SHA:-}" \
      "${DEV_PAPER_RUN_ID:-}" "${DEV_PAPER_ATTEMPT:-}" "${DEV_PAPER_PHASE:-}" \
      "${bootstrap_status}" <<'PY'
import hashlib
import json
import os
import re
import sys

diag_tmp, diag_dest, checksum_dest, expected_bff, expected_fe, invoked_run_id, invoked_attempt, invoked_phase, bootstrap_status = sys.argv[1:10]

try:
    with open(diag_tmp, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    print("invalid_json")
    sys.exit(0)

if not isinstance(data, dict):
    print("invalid_json")
    sys.exit(0)

if data.get("schema_version") != "pantheon.dev-paper-diagnostics.v1":
    print("schema_error")
    sys.exit(0)

required_fields = (
    "schema_version",
    "run_id",
    "attempt",
    "collected_at",
    "phase",
    "expected_fe_sha",
    "expected_bff_sha",
    "observed_source_sha",
    "container_id",
    "image_id",
    "identity_matches",
    "bootstrap_exit",
    "collection_status",
    "services",
)
if not all(field in data for field in required_fields):
    print("schema_error")
    sys.exit(0)

# Validate types
if not isinstance(data["identity_matches"], bool):
    print("schema_error")
    sys.exit(0)

if not isinstance(data["services"], dict):
    print("schema_error")
    sys.exit(0)

if not isinstance(data["collected_at"], str) or not data["collected_at"]:
    print("schema_error")
    sys.exit(0)

if not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)$", data["collected_at"]):
    print("schema_error")
    sys.exit(0)

# Bind expected BFF SHA
if data["expected_bff_sha"] != expected_bff:
    print("schema_error")
    sys.exit(0)

# Bind expected FE SHA
expected_fe_val = expected_fe if expected_fe else None
if data["expected_fe_sha"] != expected_fe_val:
    print("schema_error")
    sys.exit(0)

# Bind run_id
invoked_run_id_val = invoked_run_id if invoked_run_id else None
if data["run_id"] != invoked_run_id_val:
    print("schema_error")
    sys.exit(0)

# Bind attempt
invoked_attempt_val = invoked_attempt if invoked_attempt else None
if data["attempt"] != invoked_attempt_val:
    print("schema_error")
    sys.exit(0)

# Bind phase
invoked_phase_val = invoked_phase if invoked_phase else None
if data["phase"] != invoked_phase_val:
    print("schema_error")
    sys.exit(0)

# Bind bootstrap_exit
if str(data["bootstrap_exit"]) != str(bootstrap_status):
    print("schema_error")
    sys.exit(0)

outcome = data["collection_status"]
if outcome not in ("ok", "partial", "identity_mismatch"):
    print("invalid_envelope")
    sys.exit(0)

# Validate identity_matches and identity fields
if data["identity_matches"] is True:
    if data["observed_source_sha"] != expected_bff:
        print("identity_mismatch")
        sys.exit(0)
    if not (isinstance(data["container_id"], str) and re.fullmatch(r"[0-9a-f]{64}", data["container_id"])):
        print("schema_error")
        sys.exit(0)
    if not (isinstance(data["image_id"], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", data["image_id"])):
        print("schema_error")
        sys.exit(0)
    if outcome == "identity_mismatch":
        print("invalid_envelope")
        sys.exit(0)
else:
    outcome = "identity_mismatch"

try:
    with open(diag_tmp, "rb") as handle:
        content = handle.read()
    digest = hashlib.sha256(content).hexdigest()
    os.replace(diag_tmp, diag_dest)
    with open(checksum_dest, "w", encoding="utf-8") as handle:
        handle.write(f"{digest}  diagnostics.json\n")
except Exception:
    print("checksum_error")
    sys.exit(0)

print(outcome)
PY
  )"

  case "${validation_outcome}" in
    ok)
      collection_status="ok"
      write_status "${collection_status}" "${bootstrap_status}" "${collector_rc}" ""
      ;;
    partial)
      collection_status="partial"
      write_status "${collection_status}" "${bootstrap_status}" "${collector_rc}" "partial_collection"
      ;;
    identity_mismatch)
      collection_status="identity_mismatch"
      write_status "${collection_status}" "${bootstrap_status}" "${collector_rc}" "identity_mismatch"
      ;;
    checksum_error)
      collection_status="checksum_error"
      rm -f "${diagnostic_file}" "${checksum_file}" "${diagnostic_tmp}"
      write_status "${collection_status}" "${bootstrap_status}" "${collector_rc}" "checksum_write_failed"
      ;;
    schema_error)
      collection_status="schema_error"
      rm -f "${diagnostic_tmp}" "${diagnostic_file}" "${checksum_file}"
      write_status "${collection_status}" "${bootstrap_status}" "${collector_rc}" "schema_mismatch"
      ;;
    invalid_envelope)
      collection_status="invalid_envelope"
      rm -f "${diagnostic_tmp}" "${diagnostic_file}" "${checksum_file}"
      write_status "${collection_status}" "${bootstrap_status}" "${collector_rc}" "invalid_envelope_status"
      ;;
    *)
      collection_status="invalid_json"
      rm -f "${diagnostic_tmp}" "${diagnostic_file}" "${checksum_file}"
      write_status "${collection_status}" "${bootstrap_status}" "${collector_rc}" "invalid_json"
      ;;
  esac
fi

exit "${bootstrap_status}"
