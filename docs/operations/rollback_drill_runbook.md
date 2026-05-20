# Rollback Drill Harness Runbook

Status: EP5-007-V2 operational runbook
Last updated: 2026-05-20

## Purpose

Use this runbook to produce EP5 rollback drill evidence without live broker
side effects. The harness:

1. creates a local canary RuntimeBinding in an ephemeral Runtime Manager store;
2. validates the rollback packet with the Part B6 rollback dry-run evidence
   runner;
3. executes the Runtime Manager rollback path against the local store;
4. emits an EP5 proof packet with `proof.rollback_drill_completed = true`.

The harness does not call broker APIs, does not enable production routing, and
sets `live_capital_side_effects = false` in both the harness output and the EP5
proof packet.

## Command

From the repo root:

```bash
python3 -m services.governance.ep5_proof.rollback_drill_harness \
  --harness-id rollback-drill-ep5-007 \
  --proof-id ep5-proof-rollback-drill-001 \
  --promotion-readiness-packet-id prp-rollback-drill-001 \
  --run-id canary-run-rollback-drill-001 \
  --output support/evidence/EP5-007-V2/rollback-drill.json
```

Optional controls:

- `--mode validate_only` keeps the proof route in validate-only mode.
- `--mode sandbox` uses the sandbox-safe order route.
- `--action-type replace` runs the direct replacement rollback strategy.
- `--action-type pause_then_replace` runs the default pause/drain/replace
  strategy.
- `--action-type liquidate_then_replace` runs the flatten-before-replacement
  strategy.
- `--replacement-start-paused` starts a liquidate replacement in paused mode.
- `--store-path <path>` writes the local Runtime Manager drill store to a chosen
  file for debugging. Omit it for the default ephemeral store.

Do not pass live order-route values. The harness accepts only `validate_only`
and `sandbox`.

## Expected Output

The JSON output must contain:

- `status = "passed"`
- `rollback_drill_completed = true`
- `live_capital_side_effects = false`
- `rollback_drill_evidence.passed = true`
- `rollback_drill_evidence.dry_run = true`
- `runtime_manager_response.old_binding.status = "retired"`
- `runtime_manager_response.new_binding.rollback_parent` matching the original
  binding id
- `proof_packet.proof.rollback_drill_completed = true`
- `proof_packet.proof.live_capital_side_effects = false`
- `promotion_readiness_packet.can_proceed = true`

The output is the task evidence artifact for EP5-007-V2 when saved under
`support/evidence/EP5-007-V2/`.

## Failure Handling

Treat any of these as blocking:

- the rollback dry-run evidence reports blocking reasons;
- the Runtime Manager response does not retire the original binding;
- the replacement binding lacks `rollback_parent` or `rollback_action_type`;
- the proof packet has `proof.rollback_drill_completed = false`;
- any output field reports `live_capital_side_effects = true`;
- the order route is not `validate_only` or `sandbox`;
- raw broker secret material is present in the drill input.

Re-run only after the rollback packet includes DeploymentPlan, current binding,
replacement artifact, operator, broker subaccount reference, loader-check, and
position-lineage evidence.

## Local Verification

Run the focused tests:

```bash
python3 -m pytest tests/governance/test_rollback_drill_harness.py -q
```
