# Execution Materialization Draft

Status: draft planning output for `phase6-2026-04-16-oss-ecosystem-closure`

This file maps the residual Phase 6 / OSS maturity gap into the next executable wave. It does not authorize execution by itself. Human gate approval is still required before materialization.

## Wave A / Advance Activation-Ready Frameworks

1. `OSS-NEXT-001` realize the governed Qlib adapter and first supervised-alpha smoke path
2. `OSS-NEXT-002` realize the TRL activation baseline and governed preference-learning smoke path
3. `OSS-NEXT-008` refresh governed-path smoke and no-regression evidence for `OpenClaw`, `DSPy`, `imitation`, and `MLflow`

Rationale:

- `Qlib` and `TRL` are the closest deferred frameworks to practical activation
- the already-governed path should be refreshed before the next OSS wave claims more maturity than it can prove

## Wave B / Materialize Missing Research Backends

1. `OSS-NEXT-005` vectorbt task materialization
2. `OSS-NEXT-006` statsmodels task materialization
3. `OSS-NEXT-007` QuantLib task materialization

Rationale:

- these are still invisible planning debt
- the next round should stop treating them as named architecture nouns without executable ownership

## Wave C / Conditional Decisions and Optional Backends

1. `OSS-NEXT-003` close the RL path activation gate and choose the first executable RL lane
2. `OSS-NEXT-004` decide W&B backend parity versus explicit defer

Rationale:

- RL and W&B are both conditional rather than unconditional next-wave must-haves
- they need explicit yes/no decisions rather than another cycle of criteria-only drift

## Initial Parallel Roots

The next OSS wave should not serialize everything behind one task. The initial dispatch front should open with:

1. `OSS-NEXT-001`
2. `OSS-NEXT-002`
3. `OSS-NEXT-005`
4. `OSS-NEXT-006`
5. `OSS-NEXT-007`
6. `OSS-NEXT-008`

`OSS-NEXT-003` should wait until the first Qlib and TRL interpretation is on the table. `OSS-NEXT-004` should wait until the governed-path regression refresh confirms the current MLflow-first baseline.

## Materialization Rule

- do not call the OSS ecosystem "fully integrated" while activation-ready rows still lack runnable adapters and smoke evidence
- do not let `vectorbt`, `statsmodels`, and `QuantLib` remain only in maturity documents; they must either become named tasks or be explicitly cut from scope
- RL and W&B must carry an explicit include/defer decision in the session record, not just implicit silence
