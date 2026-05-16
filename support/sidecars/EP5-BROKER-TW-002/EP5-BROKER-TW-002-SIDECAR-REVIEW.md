# EP5-BROKER-TW-002 Sidecar Acceptance Review

**Sidecar kind:** `review_packet`
**Sidecar task:** `EP5-BROKER-TW-002-SIDECAR-ACCEPTANCE`
**Helper parent:** `EP5-BROKER-TW-002`
**Parent owner:** `Codex2`
**Parent reviewer:** `Claude`
**Acceptance packet prepared by:** `Gemini`
**Review owner:** `Codex`
**Review date:** `2026-05-16`
**Status:** `followup_required`

> Scope constraint: support artifact only. This review does not modify
> canonical truth, runtime implementation, registry state, deployment state, or
> governance semantics. It records whether the redirected sidecar packet can be
> incorporated into parent closeout as-is.

## Decision

Follow-up is required before the sidecar acceptance packet can be incorporated
into parent closeout as an approved readiness surface.

The archived broker smoke evidence itself checks out for the mock replay path:
it records a passed Shioaji sandbox smoke, matched reconciliation fields,
submitted-to-cancelled status transitions, `SHIOAJI_LIVE_DISABLED`, and no real
capital use. However, the current readiness script no longer treats the cited
inputs as a complete human-gate packet. Re-running the current script against
the cited archived broker smoke summary returns `status: incomplete`.

## Verified Passing Surface

Archived smoke bundle:

```text
docs/deployment/evidence/ep5-broker-tw-002/20260512T143341Z/sandbox-smoke/
```

Confirmed fields:

| Surface | Current read |
|---|---|
| Smoke `task_id` | `EP5-BROKER-TW-002` |
| Smoke status | `passed` |
| Run mode | `mock_api_replay` |
| Provider | `Shioaji` |
| Pantheon order id | `28487a9809f14006bc1dd9d4751067ad` |
| Shioaji trade id | `mock-shioaji-trade-001` |
| Reconciliation | `passed`; all diff rows are `match` |
| Live gate | `rejected` with `SHIOAJI_LIVE_DISABLED` |
| Real capital | `real_capital_used: false`; `production_live_order_submitted: false` |

The broker-smoke part of the sidecar is therefore acceptable as repo-safe mock
replay evidence for adapter lifecycle shape, request/response capture, order id
capture, status transitions, reconciliation, and live-disabled guarding.

## Blocking Follow-ups

1. Current `scripts/run_ep5_canary_readiness.py` requires a
   `shioaji_sandbox_evidence_packet` before the human-gate packet can be
   `ready_for_review`. The cited 2026-05-12 human-gate output predates that
   field and does not contain `bundle.shioaji_sandbox_evidence_packet`.
2. Re-running the current readiness command with the sidecar's cited broker
   smoke summary and without `--shioaji-evidence-packet-json` returns
   `status: incomplete`; the generated packet records
   `shioaji_sandbox_evidence_packet_consumed: fail`.
3. The same re-run also records `canary_activation_gate_refs_present: fail`
   because the cited canary plan is missing the current promotion-gate refs:
   `promotion_gate_decision_id`, `human_gate_packet_ref`,
   `broker_sandbox_smoke_ref`, `risk_owner_approval_ref`,
   `operator_approval_ref`, `persona_capital_binding_id`,
   `allowed_deployment_scope`, `capital_scale_pct`, and `gross_scale_pct`.
4. The evidence remains `mock_api_replay`. It must not be described as an
   external Shioaji simulation-account proof until an operator environment with
   the Shioaji SDK and sandbox credentials runs the non-`--mock-api` path.

## Commands Run

Focused broker tests:

```bash
python3 -m pytest services/broker/shioaji/test_adapter.py services/broker/shioaji/test_sandbox_smoke.py -q
```

Result: `48 passed in 14.15s`

Focused readiness tests:

```bash
python3 -m pytest scripts/test_run_ep5_canary_readiness.py -q
```

Result: `12 passed in 3.78s`

Current readiness replay against the sidecar-cited broker smoke summary:

```bash
python3 scripts/run_ep5_canary_readiness.py emit-human-gate-packet --checklist-json docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z/operator-checklist.json --datasource-summary-json docs/deployment/evidence/ep5-human-gate-input/20260424T185046Z/datasource-smoke/summary.json --plan-json docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z/canary-deployment-plan.json --drill-summary-json docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z/rollback-drill-summary.json --broker-smoke-summary-json docs/deployment/evidence/ep5-broker-tw-002/20260512T143341Z/sandbox-smoke/summary.json --dual-vm-evidence-dir docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z --event-trace-status packetized --event-trace-note "Replay-clean event-trace projection evidence remains packetized from the archived EP5 closeout bundle; this EP5-BROKER-TW-002 packet adds Shioaji broker sandbox smoke evidence only." --output-dir /tmp/pantheon-ep5-sidecar-review-current
```

Result: `{"status": "incomplete", "output_dir": "/tmp/pantheon-ep5-sidecar-review-current"}`

The generated packet specifically records:

- `broker_sandbox_smoke_consumed: pass`
- `shioaji_sandbox_evidence_packet_consumed: fail`
- `canary_activation_gate_refs_present: fail`

## Parent Closeout Guidance

Parent closeout may cite the archived smoke bundle as mock replay evidence for
the Shioaji adapter sandbox smoke path. It should not claim the human-gate
packet is complete under the current readiness script until the missing
Shioaji sandbox evidence packet and current promotion-gate refs are supplied
and the human-gate packet is regenerated to `ready_for_review`.

No L1/L2 canonical document update is required from this sidecar review.
