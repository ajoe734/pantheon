# TEST-FULLSUITE-RUNBOOK-CI-MATRIX Review

Task: `TEST-FULLSUITE-RUNBOOK-CI-MATRIX`
Owner: Codex
Reviewer: Claude
Decision: Approved
Reviewed at: 2026-04-30T14:58:00Z

## Scope Check

Approved. The runbook defines a repeatable full-suite matrix across root pytest
collection/execution, direct smoke scripts, compose config, full compose smoke,
containerized activation-ready profiles, and production activation gate
reporting.

The document keeps default commands safe. It does not enable Qlib, TRL, RLlib,
FinRL, W&B online sync, paper execution, canary execution, or live execution by
default. Explicit activation-ready rows are limited to local fixtures or
read/report-only gate evaluation.

## Verification

- `python3 scripts/ci_stage0.py validate` passed.
- `python3 -m py_compile scripts/smoke_honest_stack.py scripts/smoke_openclaw_activation_ready_e2e.py scripts/smoke_oss_activation_ready_matrix.py scripts/smoke_dormant_oss_matrix.py scripts/run_research_activation_gates.py` passed.
- `python3 -m pytest -q scripts/test_ci_stage0.py scripts/test_smoke_openclaw_activation_ready_e2e.py scripts/test_smoke_oss_activation_ready_matrix.py` passed with `12` tests.
- `git diff --check -- docs/testing/full-suite-runbook.md docs/testing/pytest-harness.md` passed.

## Notes

The runbook is linked from `docs/testing/pytest-harness.md`, satisfying the
documentation linkage requirement without changing CI workflow behavior in this
task.
