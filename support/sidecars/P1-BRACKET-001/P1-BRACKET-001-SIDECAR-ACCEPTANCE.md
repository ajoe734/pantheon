# P1-BRACKET-001 Sidecar Acceptance Packet

Task ID: `P1-BRACKET-001-SIDECAR-ACCEPTANCE`  
Parent task: `P1-BRACKET-001`  
Helper kind: `acceptance_packet`  
Owner: `Codex2`  
Reviewer: `Codex`  
Scope: support artifact only; this packet does not change L1 canonical truth, core contracts, runtime code, registry code, or governance implementation.

## Parent Task Snapshot

`P1-BRACKET-001` is the Wave 5 parent task for guarded paper/sim bracket order execution. Its current execution-board acceptance surfaces are:

1. Paper/sim bracket order path is guarded.
2. `logged_only` and `submitted_to_broker` semantics remain distinct.
3. Live broker submission remains fail-closed without an activation guard.

Current execution-board parent owner is `Codex`; current parent reviewer is `Claude`. The execution materialization record originally routed the parent reviewer to `Gemini`, so the parent owner should re-check `ai-status.json` before final parent handoff if reviewer routing changes again.

## Dependency Map

### Direct Dependency

`P1-BRACKET-001` depends on `P0-LIVE-GUARD-001`, which is complete. That dependency established the safety floor the parent task must preserve:

- Live role stays health-only / not activated for broker actions unless an explicit production activation guard passes.
- Bracket risk parameters are audit evidence when unguarded and are not silently treated as broker-submitted orders.
- `bracket_order_logged` telemetry must expose the difference between `broker_submission_status=logged_only` with `submitted_to_broker=false` and any later guarded simulated submission.

### Constraint On Parent Implementation

The parent task may add a guarded paper/sim bracket path, but it must not weaken the P0 safety posture:

- Guarded execution is allowed only for paper/sim runtime stages, never live.
- The guard must be explicit, not inferred from risk parameters alone.
- A blocked guard must produce a logged-only bracket event rather than an attempted child-order submission.
- A live stage must remain logged-only even if a bracket execution flag is accidentally enabled.
- Any paper/sim child-order path must use simulated/runtime-local submission semantics; the parent must not claim production broker readiness.

### Parent Absorption Checks

Before absorbing this sidecar into the parent, the parent owner/reviewer should verify:

- `P0-LIVE-GUARD-001` remains done and its evidence still distinguishes live fail-closed from paper/sim behavior.
- Any parent runtime changes are limited to guarded paper/sim bracket execution and do not modify live activation criteria.
- Telemetry, runtime snapshots, and tests keep `logged_only` and `submitted_to_broker` as distinct states.
- Documentation updates, if any, describe Wave 5 as activation-ready support, not production live broker enablement.

## Acceptance Checklist For Parent Review

### Guarded Paper/Sim Bracket Order Path

- [ ] Paper/sim bracket execution is gated by both runtime stage and an explicit bracket execution enablement flag.
- [ ] Signals with stop-loss or take-profit risk parameters do not create bracket child orders unless the guard passes.
- [ ] The allowed stages are paper/sim only; unknown, staging, canary, and live stages do not pass the bracket execution guard.
- [ ] A guarded paper/sim entry signal can create deterministic stop-loss and take-profit child-leg payloads from entry quantity and price.
- [ ] Non-entry actions, invalid prices, and zero/invalid quantities remain logged-only.
- [ ] Paper runtime state exposes open bracket child orders only after a guarded paper/sim submission path succeeds.

### `logged_only` Versus `submitted_to_broker`

- [ ] `broker_submission_status=logged_only` always carries `submitted_to_broker=false`.
- [ ] Guarded paper/sim simulated submission uses a separate status, `submitted_to_broker`, and carries `submitted_to_broker=true`.
- [ ] Telemetry action names remain distinct, for example `bracket_logged_only` versus `bracket_submitted_to_broker`.
- [ ] Parent tests assert both the default logged-only path and the guarded paper/sim submitted path.
- [ ] Runtime snapshot or recent event output preserves enough metadata to explain guard stage, guard reason, child legs, and submission result.

### Live Fail-Closed Behavior

- [ ] Live stage bracket execution remains blocked even if a bracket execution flag is set.
- [ ] Live bracket signals produce a logged-only audit event and no bracket child-order submission.
- [ ] Runtime bootstrap or health surfaces continue to report live bracket submission as not allowed until a separate activation guard exists.
- [ ] No parent change introduces a direct live broker submission path, broker SDK call, or production-live readiness claim.

## Suggested Verification Commands

These are focused commands the parent owner can run after implementing or reconciling parent changes:

```bash
python3 -m unittest services.execution.lean_runtime.test_executor
python3 -m unittest services.execution.lean_runtime.test_paper_runtime
python3 -m unittest services.execution.lean_runtime.test_runtime_bootstrap
python3 -m unittest services.telemetry.test_paper_runtime_ingest_contract
rg -n "bracket_order_logged|submitted_to_broker|logged_only|bracket_order_submission_allowed" services/execution services/telemetry docs/04
```

Sidecar-only verification used while preparing this packet:

```bash
sed -n '1,260p' .orchestrator/task-briefs/p1_bracket_001_sidecar_acceptance.md
sed -n '440,590p' ai-status.json
sed -n '1,240p' support/sidecars/P1-BRACKET-001/P1-BRACKET-001-SIDECAR-ACCEPTANCE-REVIEW.md
rg -n "bracket|logged_only|submitted_to_broker|fail-closed|activation guard|P1-BRACKET-001|P0-LIVE-GUARD-001" docs/04/pantheon_sa/SA-20_v2_risk_register_corrected.md docs/04/pantheon_p0_sd/SD-P0-02_DeploymentPlan_to_RuntimeBootstrap_Contract.md services/execution/lean_runtime services -g '*.py'
git status --short
```

## Handoff

Ready for sidecar review by `Codex`.

Reviewer focus:

- Confirm this packet remains support-only and does not promote canonical truth.
- Confirm the dependency map ties `P0-LIVE-GUARD-001` to the parent bracket execution guardrails.
- Confirm the checklist is concrete enough for the parent owner to use when deciding whether to absorb the sidecar into `P1-BRACKET-001`.

Parent owner decision remains with `Codex`. This packet should be treated as acceptance support, not as proof that the parent runtime implementation is complete.
