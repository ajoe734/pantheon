# Kill-Switch Demo Harness Runbook

Status: EP5-008-V2 operational runbook
Last updated: 2026-05-20

## Purpose

Use this runbook to produce EP5 kill-switch demo evidence without live broker
side effects. The harness:

1. creates a local canary RuntimeBinding in an ephemeral Runtime Manager store;
2. executes the Runtime Manager kill-switch fast path;
3. validates the Runtime Manager response with the Part B5 kill-switch demo
   evidence collector;
4. emits an EP5 proof packet with `proof.kill_switch_demo_completed = true`.

The harness does not call broker APIs, does not enable production routing, and
sets `live_capital_side_effects = false` in both the harness output and the EP5
proof packet.

## Command

From the repo root:

```bash
python3 -m services.governance.ep5_proof.kill_switch_harness \
  --harness-id ks-demo-ep5-008 \
  --proof-id ep5-proof-ks-demo-001 \
  --promotion-readiness-packet-id prp-ks-demo-001 \
  --run-id canary-run-ks-demo-001 \
  --output support/evidence/EP5-008-V2/kill-switch-demo.json
```

Optional controls:

- `--mode validate_only` keeps the proof route in validate-only mode.
- `--mode sandbox` uses the sandbox-safe order route.
- `--action-type pause` runs the default operator emergency stop demo.
- `--action-type risk_off` runs the risk-off demo path.
- `--store-path <path>` writes the local Runtime Manager demo store to a chosen
  file for debugging. Omit it for the default ephemeral store.

Do not pass live order-route values. The harness accepts only `validate_only`
and `sandbox`.

## Expected Output

The JSON output must contain:

- `status = "passed"`
- `kill_switch_demo_completed = true`
- `live_capital_side_effects = false`
- `kill_switch_demo_evidence.passed = true`
- `runtime_manager_response.telemetry_ack.ack_status = "acknowledged"`
- `proof_packet.proof.kill_switch_demo_completed = true`
- `proof_packet.proof.live_capital_side_effects = false`
- `promotion_readiness_packet.can_proceed = true`

The output is the task evidence artifact for EP5-008-V2 when saved under
`support/evidence/EP5-008-V2/`.

## Failure Handling

Treat any of these as blocking:

- telemetry ack is `fail_closed`;
- the evidence collector reports blocking reasons;
- the proof packet has `proof.kill_switch_demo_completed = false`;
- any output field reports `live_capital_side_effects = true`;
- the order route is not `validate_only` or `sandbox`.

Re-run only after the Runtime Manager response includes command, audit,
binding-action, safe-mode, and acknowledged telemetry evidence.

## Local Verification

Run the focused tests:

```bash
python3 -m pytest tests/governance/test_kill_switch_harness.py -q
```
