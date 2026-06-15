#!/usr/bin/env bash
# Run all BFF-based E2E business-flow verifiers against a deployed stack.
# One command to check every e2e invariant the E2E-R1..R10 campaign established.
#
# Usage:
#   BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa scripts/run_e2e_verifiers.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${BFF_BASE:?set BFF_BASE}"
: "${BFF_TOKEN:=op-dev:admin:mfa}"
export BFF_BASE BFF_TOKEN

VERIFIERS=(
  "verify_e2e_binding_provenance.py:R1 provenance integrity"
  "verify_e2e_telemetry_drift_consistency.py:R2 telemetry<->drift"
  "verify_e2e_promotion_governance.py:R3 promotion governance"
  "verify_e2e_capital_integrity.py:R5 capital invariants"
  "verify_e2e_surface_consistency.py:R8 surface consistency"
  "verify_e2e_evolution_loop.py:R9 evolution loop"
  "verify_e2e_sentinel_integrity.py:R12 sentinel integrity"
  "verify_e2e_surface_status_consistency.py:R13 surface-status consistency"
  "verify_e2e_deployment_lifecycle.py:R15 deployment lifecycle"
  "verify_e2e_auth_boundary.py:R17 auth boundary"
  "verify_e2e_runtime_state_coherence.py:R18 runtime-state coherence"
  "verify_e2e_telemetry_coverage.py:R19 telemetry coverage"
)

pass=0; fail=0; missing=0
for entry in "${VERIFIERS[@]}"; do
  script="${entry%%:*}"; label="${entry#*:}"
  path="$HERE/$script"
  if [[ ! -f "$path" ]]; then
    echo "SKIP  $label ($script not present)"; missing=$((missing+1)); continue
  fi
  if python3 "$path" >/tmp/e2e_v.out 2>&1; then
    echo "PASS  $label"; pass=$((pass+1))
  else
    echo "FAIL  $label"; fail=$((fail+1))
    sed 's/^/        /' /tmp/e2e_v.out | tail -6
  fi
done
echo "----"
echo "e2e verifiers: $pass passed, $fail failed, $missing missing"
# telemetry DLQ health is internal-only (reads the telemetry service DLQ); run on the VM:
echo "(note: R7 telemetry-DLQ health runs on the telemetry host - see e2e-r7 doc)"
[[ $fail -eq 0 ]]
