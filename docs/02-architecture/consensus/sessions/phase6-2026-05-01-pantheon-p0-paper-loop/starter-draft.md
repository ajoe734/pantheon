# Starter Draft

Current rule: only `Codex` edits this file directly.

## Shared Draft

- Objective: converge `docs/04` SA/SD findings into the next execution wave for Pantheon P0.
- Scope boundary: prove the paper-only operating loop and repo authority; do not activate live/canary or full broker SDK work in P0.
- Accepted current bridge: `pantheon/lean` submodule, remote `ajoe734/pantheon-lean.git`; `lean-platform` is not the P0 execution target.
- Proposed wave order:
  1. Repo authority, CI guardrails, and live fail-closed protection.
  2. Runtime contract: `DeploymentPlan -> RuntimeBinding -> RuntimeBootstrapRequest -> PantheonRuntimeContext`.
  3. `PantheonAlgoBase` context/event attachment and paper telemetry producer.
  4. Telemetry ingest/projection, paper loop smoke, and basic reconciliation.
  5. BFF/front honesty cleanup: read/command split, demo guard, source mode, runtime identity.
- Proposed task slices:
  - See `docs/04/SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK.md`.
  - See `execution-materialization.md`.
- Open disagreements:
  - Whether BFF read/command split is strict P0 or first P1. Current starter recommendation: P0, because command drift affects runtime safety.
  - Whether basic reconciliation belongs in first P0 materialization. Current starter recommendation: include a minimal record writer after telemetry projection, with no automatic evolution action.
  - Whether `lean-platform` should be archived or kept as migration candidate. Current starter recommendation: migration candidate only until ADR revision.
