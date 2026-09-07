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
diagnostic_tmp="${diagnostic_file}.tmp"
# Deliberately outside DEV_PAPER_DIAGNOSTICS_DIR: that directory is uploaded
# wholesale, and raw remote stderr may contain unbounded/unsanitized output
# (including secret-shaped fixture output reproduced by an independent
# review). Only a bounded, redacted summary of this file is ever written
# into the uploaded directory, via write_status below.
collector_stderr_raw="$(mktemp "${DEV_PAPER_DIAGNOSTICS_DIR%/}.collector-stderr.XXXXXX")"
cleanup() {
  rm -f "${collector_stderr_raw}"
}
trap cleanup EXIT

write_status() {
  local collection_status="$1"
  local bootstrap_exit="$2"
  local tmp="${status_file}.tmp.$$"
  python3 - "${tmp}" "${collection_status}" "${bootstrap_exit}" "${collector_stderr_raw}" <<'PY'
import datetime as dt
import json
import re
import sys

tmp, collection_status, bootstrap_exit, stderr_path = sys.argv[1:5]

# Bound and redact before any of this ever reaches the uploaded artifact
# directory: only a short, sanitized terminal summary is kept, never the
# raw/unbounded SSH or remote command stderr.
SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._-]{10,}"),
    re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"),
    re.compile(r"(?i)(secret|token|password|apikey|api[_-]?key)\s*[=:]\s*\S+"),
    re.compile(r"[A-Za-z][A-Za-z0-9+/]{2,}://[^\s@]+:[^\s@]+@\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),
)
stderr_summary = None
try:
    with open(stderr_path, "rb") as handle:
        raw = handle.read(4096)
except OSError:
    raw = b""
if raw:
    text = raw.decode("utf-8", errors="replace")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    stderr_summary = text[:2048]

bootstrap_exit_value = None
if bootstrap_exit not in ("", "null"):
    try:
        bootstrap_exit_value = int(bootstrap_exit)
    except ValueError:
        bootstrap_exit_value = None

document = {
    "schemaVersion": 1,
    "collectionStatus": collection_status,
    "bootstrapExit": bootstrap_exit_value,
    "collectedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "collectorStderrSummary": stderr_summary,
}
with open(tmp, "w") as handle:
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
write_status "not_started" ""

bootstrap_status=0
"${SSH}" exec \
  "set -euo pipefail; docker exec pantheon-operator-bff-1 python /workspace/scripts/bootstrap_dev_paper_baseline.py --timeout-seconds 420 --poll-seconds 5" \
  || bootstrap_status=$?

if [[ "${bootstrap_status}" -eq 0 ]]; then
  write_status "not_required" "${bootstrap_status}"
  exit 0
fi

# Baseline failed. The lease and heartbeat are still owned by this guarded
# child; collect now, while capture is still possible, then return the
# original failure unchanged. Record that collection is in flight (with the
# already-known bootstrap exit) before running the collector, so a
# collection-phase cancellation/timeout still leaves the real bootstrap
# outcome on disk instead of nothing.
write_status "collecting" "${bootstrap_status}"

collector_args=(--expected-bff-sha "${EXPECTED_BFF_SHA}")
[[ -n "${EXPECTED_FE_SHA:-}" ]] && collector_args+=(--expected-fe-sha "${EXPECTED_FE_SHA}")
[[ -n "${DEV_PAPER_RUN_ID:-}" ]] && collector_args+=(--run-id "${DEV_PAPER_RUN_ID}")
[[ -n "${DEV_PAPER_ATTEMPT:-}" ]] && collector_args+=(--attempt "${DEV_PAPER_ATTEMPT}")
[[ -n "${DEV_PAPER_PHASE:-}" ]] && collector_args+=(--phase "${DEV_PAPER_PHASE}")
collector_args+=(--bootstrap-exit "${bootstrap_status}")
# Every value above was validated against a strict allowlist regex above, so
# it is safe to place directly in the remote command string executed by the
# pinned SSH channel.
collector_invocation="python3 -"
for arg in "${collector_args[@]}"; do
  collector_invocation+=" ${arg}"
done

collection_status="collector_command_failed"
collector_rc=0
timeout --kill-after=5s "${collection_deadline_seconds}s" \
  "${SSH}" exec "${collector_invocation}" \
    <"${DEV_PAPER_DIAGNOSTICS_COLLECTOR}" >"${diagnostic_tmp}" 2>"${collector_stderr_raw}" \
  || collector_rc=$?

if [[ "${collector_rc}" -eq 124 || "${collector_rc}" -eq 137 ]]; then
  collection_status="timeout"
  rm -f "${diagnostic_tmp}"
elif [[ "${collector_rc}" -ne 0 ]]; then
  rm -f "${diagnostic_tmp}"
elif python3 -m json.tool "${diagnostic_tmp}" >/dev/null 2>&1; then
  mv -f "${diagnostic_tmp}" "${diagnostic_file}"
  (cd "${DEV_PAPER_DIAGNOSTICS_DIR}" && sha256sum diagnostics.json >SHA256SUMS)
  collection_status="ok"
else
  collection_status="invalid_json"
  rm -f "${diagnostic_tmp}"
fi

write_status "${collection_status}" "${bootstrap_status}"
exit "${bootstrap_status}"
