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
#                                     caller before the guard was launched
#   DEV_PAPER_DIAGNOSTICS_COLLECTOR  path to collect_dev_paper_diagnostics.py
#   EXPECTED_BFF_SHA                 full 40-hex candidate BFF commit SHA
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

status_file="${DEV_PAPER_DIAGNOSTICS_DIR}/collection-status.json"
diagnostic_file="${DEV_PAPER_DIAGNOSTICS_DIR}/diagnostics.json"
diagnostic_tmp="${diagnostic_file}.tmp"

write_status() {
  local collection_status="$1"
  local bootstrap_exit="$2"
  local tmp="${status_file}.tmp.$$"
  printf '{"schemaVersion":1,"collectionStatus":%s,"bootstrapExit":%s,"collectedAt":"%s"}\n' \
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "${collection_status}")" \
    "${bootstrap_exit}" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${tmp}"
  mv -f "${tmp}" "${status_file}"
}

SSH="${GITHUB_WORKSPACE}/.agora-gate-controller/scripts/dev_vm_ssh.sh"
[[ -f "${SSH}" && ! -L "${SSH}" ]] || { error "dev_vm_ssh.sh is missing or is a symlink"; exit 75; }

bootstrap_status=0
"${SSH}" exec \
  "set -euo pipefail; docker exec pantheon-operator-bff-1 python /workspace/scripts/bootstrap_dev_paper_baseline.py --timeout-seconds 420 --poll-seconds 5" \
  || bootstrap_status=$?

if [[ "${bootstrap_status}" -eq 0 ]]; then
  exit 0
fi

# Baseline failed. The lease and heartbeat are still owned by this guarded
# child; collect now, while capture is still possible, then return the
# original failure unchanged.
collection_status="collector_command_failed"
if "${SSH}" exec "python3 - --expected-bff-sha ${EXPECTED_BFF_SHA}" \
    <"${DEV_PAPER_DIAGNOSTICS_COLLECTOR}" >"${diagnostic_tmp}" 2>"${DEV_PAPER_DIAGNOSTICS_DIR}/collector.stderr"; then
  if python3 -m json.tool "${diagnostic_tmp}" >/dev/null 2>&1; then
    mv -f "${diagnostic_tmp}" "${diagnostic_file}"
    (cd "${DEV_PAPER_DIAGNOSTICS_DIR}" && sha256sum diagnostics.json >SHA256SUMS)
    collection_status="ok"
  else
    collection_status="invalid_json"
    rm -f "${diagnostic_tmp}"
  fi
else
  rm -f "${diagnostic_tmp}"
fi

write_status "${collection_status}" "${bootstrap_status}"
exit "${bootstrap_status}"
