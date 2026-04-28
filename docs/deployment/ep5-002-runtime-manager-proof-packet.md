# EP5-002 Runtime-Manager Proof Packet Prep

Status: prepared packet boundary only

Scope: runtime-manager-originated live/canary proof packet scaffold,
checklist, validator expectations, lifecycle schema, IBKR manifest, and
closeout template.

This document does not authorize a live order. The prep path only writes local
packet files and validates archived evidence. The later execution task remains
blocked on explicit human approval for the exact account, instrument, quantity,
price, session, and rollback plan.

## Packet Boundary

EP5-002 proof must show that runtime-manager is the origin of the governed
live/canary lifecycle. A direct broker API harness can capture broker facts, but
it is not sufficient by itself unless the packet also archives the
runtime-manager command envelope and lifecycle excerpt.

Required packet files:

- `runtime-manager-command-envelope.dry-run.json`
- `ibkr-packet-manifest.json`
- `runtime-manager-lifecycle.schema.json`
- `operator-checklist.md`
- `validator-expectations.md`
- `closeout-template.md`
- `live-order-submit.request.json`
- `live-order-submit.response.json`
- `live-order-cancel.request.json`
- `live-order-cancel.response.json`
- `telemetry-event-trace.response.json`
- `runtime-manager-event-excerpt.json`
- `tws-open-order-transcript.md` or `tws-open-order-screenshot.{png,jpg,jpeg}`
- `operator-note.md`

## Dry-Run Init

This command creates only a pending capture packet. It does not submit, modify,
or cancel broker orders.

```bash
python3 scripts/validate_ep5_live_order_cancel.py init \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --account U19859952 \
  --limit-price '<operator-set far-from-market value>' \
  --runtime-binding-id '<live-runtime-binding-id>' \
  --deployment-plan-id '<ep5-live-deployment-plan-id>' \
  --operator-id '<operator-id>'
```

The initialized packet intentionally fails validation until all placeholder
broker, runtime, telemetry, TWS, and operator evidence is replaced with real
captured facts.

## Validator Expectations

The validator checks that:

- the runtime-manager command envelope names `runtime-manager` as origin
- explicit human approval is required in the envelope
- the IBKR manifest pins the minimal guardrails: `AAPL`, quantity `1`, `LMT`,
  `DAY`, `outside_rth=false`
- broker acknowledgement and cancel/no-fill or fill disposition refer to the
  same order identity
- telemetry trace refs include runtime binding and deployment plan ids
- runtime-manager lifecycle evidence includes submit and cancel/fill events
- TWS evidence and operator note match the broker order identity
- no `<placeholder>` values remain

Run validation after capture:

```bash
python3 scripts/validate_ep5_live_order_cancel.py validate \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --output-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp>/validation
```

## Closeout

The closeout must record one final disposition:

- `canceled`
- `filled`
- `partially_filled`
- `otherwise_resolved`

If the order fills unexpectedly, the packet must preserve the fill facts and
the operator follow-up. Do not relabel a fill as a cancel-path success.
