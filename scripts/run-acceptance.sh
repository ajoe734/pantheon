#!/usr/bin/env bash
# Pantheon acceptance entry point.
#
# Usage:
#   scripts/run-acceptance.sh smoke              # fast subset, used by wave-ci.yml
#   scripts/run-acceptance.sh full               # full pass, used at wave freeze
#   scripts/run-acceptance.sh wave wave/<id>     # acceptance for a specific wave ref
#
# This is a thin dispatcher so the wave/CI tooling has a stable hook even
# while the underlying acceptance suite continues to evolve. Each mode falls
# back to `scripts/ci_stage0.py` for the Stage-0 baseline gate, then layers
# in additional checks based on what's available in the worktree.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODE="${1:-smoke}"
TARGET_REF="${2:-HEAD}"

PYTHON="${PYTHON:-python3}"

run_step() {
  local name="$1"
  shift
  echo "═══ $name"
  if "$@"; then
    echo "✓ $name"
  else
    local rc=$?
    echo "✗ $name (exit $rc)"
    return $rc
  fi
}

stage0_baseline() {
  if [[ -f scripts/ci_stage0.py ]]; then
    "$PYTHON" scripts/ci_stage0.py run-baseline
  else
    echo "stage0 baseline script not present; skipping"
  fi
}

stage0_validate() {
  if [[ -f scripts/ci_stage0.py ]]; then
    "$PYTHON" scripts/ci_stage0.py validate
  else
    echo "stage0 validate not present; skipping"
  fi
}

trailers_on_range() {
  local rev_range="$1"
  if "$PYTHON" scripts/git/check_commit_trailers.py --range "$rev_range" --skip-merge; then
    :
  else
    return 1
  fi
}

case "$MODE" in
  smoke)
    run_step "stage0-validate" stage0_validate
    run_step "stage0-baseline" stage0_baseline
    ;;
  full)
    run_step "stage0-validate" stage0_validate
    run_step "stage0-baseline" stage0_baseline
    # Layer additional gates here as the suite grows.
    if [[ -f Makefile ]] && grep -q '^acceptance:' Makefile; then
      run_step "make-acceptance" make acceptance
    fi
    if [[ -d tests ]]; then
      run_step "pytest" "$PYTHON" -m pytest -q tests || echo "pytest reported failures"
    fi
    # E2E binding-provenance verifier logic gate (the live run against a deployed
    # BFF is a post-deploy smoke check; this gates the checker's decision logic).
    if [[ -f scripts/test_verify_e2e_binding_provenance.py ]]; then
      run_step "e2e-provenance-verifier" "$PYTHON" -m pytest -q scripts/test_verify_e2e_binding_provenance.py || echo "provenance verifier tests reported failures"
    fi
    if [[ -f scripts/test_verify_e2e_telemetry_drift_consistency.py ]]; then
      run_step "e2e-telemetry-drift-verifier" "$PYTHON" -m pytest -q scripts/test_verify_e2e_telemetry_drift_consistency.py || echo "telemetry-drift verifier tests reported failures"
    fi
    if [[ -f scripts/test_verify_e2e_promotion_governance.py ]]; then
      run_step "e2e-promotion-governance-verifier" "$PYTHON" -m pytest -q scripts/test_verify_e2e_promotion_governance.py || echo "promotion-governance verifier tests reported failures"
    fi
    if [[ -f scripts/test_verify_e2e_capital_integrity.py ]]; then
      run_step "e2e-capital-integrity-verifier" "$PYTHON" -m pytest -q scripts/test_verify_e2e_capital_integrity.py || echo "capital-integrity verifier tests reported failures"
    fi
    if [[ -f scripts/test_verify_e2e_surface_consistency.py ]]; then
      run_step "e2e-surface-consistency-verifier" "$PYTHON" -m pytest -q scripts/test_verify_e2e_surface_consistency.py || echo "surface-consistency verifier tests reported failures"
    fi
    if [[ -f scripts/test_verify_e2e_evolution_loop.py ]]; then
      run_step "e2e-evolution-loop-verifier" "$PYTHON" -m pytest -q scripts/test_verify_e2e_evolution_loop.py || echo "evolution-loop verifier tests reported failures"
    fi
    if [[ -f scripts/test_verify_e2e_sentinel_integrity.py ]]; then
      run_step "e2e-sentinel-integrity-verifier" "$PYTHON" -m pytest -q scripts/test_verify_e2e_sentinel_integrity.py || echo "sentinel-integrity verifier tests reported failures"
    fi
    if [[ -f scripts/test_verify_e2e_telemetry_dlq_health.py ]]; then
      run_step "e2e-telemetry-dlq-verifier" "$PYTHON" -m pytest -q scripts/test_verify_e2e_telemetry_dlq_health.py || echo "telemetry-dlq verifier tests reported failures"
    fi
    ;;
  wave)
    REF="${TARGET_REF:-HEAD}"
    if [[ ! "$REF" =~ ^(wave/|refs/heads/wave/) ]]; then
      echo "wave mode expects a wave/* ref; got: $REF" >&2
      exit 2
    fi
    git fetch --quiet origin "$REF" || true
    RANGE="origin/dev..$REF"
    run_step "trailers-range" trailers_on_range "$RANGE"
    run_step "stage0-baseline" stage0_baseline
    ;;
  *)
    echo "usage: $0 {smoke|full|wave [<ref>]}" >&2
    exit 1
    ;;
esac

echo "✓ acceptance mode='$MODE' complete"
