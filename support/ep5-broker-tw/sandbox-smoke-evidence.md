# EP5-BROKER-TW-002 Sandbox Smoke Evidence

Task: `EP5-BROKER-TW-002`
Owner: `Codex2`
Reviewer: `Claude`
Generated: `2026-05-12T14:34:09Z`

## Scope

This packet adds the broker-side Shioaji sandbox smoke harness and wires its
summary into the EP5 human-gate packet flow.

Live execution and capital binding stayed fail-closed throughout:

- no registry admission was performed
- no paper, canary, live, or capital binding transition was performed
- live broker execution was checked through `SHIOAJI_LIVE_DISABLED`
- no raw Shioaji API key or secret was written to repo artifacts

## Environment Boundary

This background worker did not have the Shioaji SDK installed and did not have
`BROKER_SHIOAJI_API_KEY` / `BROKER_SHIOAJI_SECRET_KEY` configured. The archived
run is therefore explicitly marked as `run_mode=mock_api_replay`.

The harness defaults to the real Shioaji simulation SDK path when run without
`--mock-api`. A true external Shioaji simulation-account proof still requires
running the same command without `--mock-api` in an operator environment that
has the SDK and sandbox credentials installed.

## Evidence Bundle

Root:

```text
docs/deployment/evidence/ep5-broker-tw-002/20260512T143341Z/
```

Broker smoke:

```text
docs/deployment/evidence/ep5-broker-tw-002/20260512T143341Z/sandbox-smoke/summary.json
```

Human-gate packet:

```text
docs/deployment/evidence/ep5-broker-tw-002/20260512T143341Z/human-gate/human-gate-packet.json
```

Recorded smoke identifiers:

| Field | Value |
|---|---|
| `status` | `passed` |
| `run_mode` | `mock_api_replay` |
| `pantheon_order_id` | `28487a9809f14006bc1dd9d4751067ad` |
| `shioaji_trade_id` | `mock-shioaji-trade-001` |
| `reconciliation.status` | `passed` |
| `live_gate.response.error_code` | `SHIOAJI_LIVE_DISABLED` |

## Commands Run

Focused tests:

```bash
python3 -m pytest services/broker/shioaji/test_adapter.py services/broker/shioaji/test_sandbox_smoke.py -q
```

Result: `35 passed in 12.20s`

```bash
python3 -m pytest scripts/test_run_ep5_canary_readiness.py scripts/test_run_broker_sandbox_order_smoke.py -q
```

Result: `14 passed in 8.11s`

Compile check:

```bash
python3 -m py_compile services/broker/shioaji/sandbox_smoke.py scripts/run_ep5_canary_readiness.py
```

Result: pass

Broker smoke replay:

```bash
BROKER_SHIOAJI_SANDBOX_ENABLED=1 \
python3 services/broker/shioaji/sandbox_smoke.py \
  --mock-api \
  --symbol 2330 \
  --qty 1 \
  --side buy \
  --order-type limit \
  --limit-price 950 \
  --output-dir docs/deployment/evidence/ep5-broker-tw-002/20260512T143341Z/sandbox-smoke
```

Result:

```json
{"status": "passed", "run_mode": "mock_api_replay", "output_dir": "docs/deployment/evidence/ep5-broker-tw-002/20260512T143341Z/sandbox-smoke", "order_id": "28487a9809f14006bc1dd9d4751067ad", "shioaji_trade_id": "mock-shioaji-trade-001"}
```

Human-gate packet emission:

```bash
python3 scripts/run_ep5_canary_readiness.py emit-human-gate-packet \
  --checklist-json docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z/operator-checklist.json \
  --datasource-summary-json docs/deployment/evidence/ep5-human-gate-input/20260424T185046Z/datasource-smoke/summary.json \
  --plan-json docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z/canary-deployment-plan.json \
  --drill-summary-json docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z/rollback-drill-summary.json \
  --broker-smoke-summary-json docs/deployment/evidence/ep5-broker-tw-002/20260512T143341Z/sandbox-smoke/summary.json \
  --dual-vm-evidence-dir docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z \
  --event-trace-status packetized \
  --event-trace-note "Replay-clean event-trace projection evidence remains packetized from the archived EP5 closeout bundle; this EP5-BROKER-TW-002 packet adds Shioaji broker sandbox smoke evidence only." \
  --output-dir docs/deployment/evidence/ep5-broker-tw-002/20260512T143341Z/human-gate
```

Result: `{"status": "ready_for_review", "output_dir": "docs/deployment/evidence/ep5-broker-tw-002/20260512T143341Z/human-gate"}`

## Reviewer Notes

The code path now exists for both:

- real Shioaji simulation SDK execution: run `sandbox_smoke.py` without
  `--mock-api` after installing `shioaji` and configuring sandbox credentials
- repo-safe local/CI replay: run with `--mock-api`

The archived evidence proves adapter lifecycle behavior, request/response
shape, order id capture, status transitions, reconciliation, and live-disabled
guarding in mock replay. It does not prove that Sinopac accepted the order in an
external Shioaji simulation account.
