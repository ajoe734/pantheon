# IBKR Minimal Live Order / Cancel Manual

Status: prepared manual broker-capture path only

Scope: smallest possible live order acceptance plus cancel test after the
read-only TWS API proof is complete.

This document does not authorize automated trading. It exists so the operator
can run the smallest possible broker-path validation manually and capture broker
facts for the runtime-manager-originated EP5-002 packet.

The canonical packet prep boundary is:

- [ep5-002-runtime-manager-proof-packet.md](/home/edna/code/pantheon/docs/deployment/ep5-002-runtime-manager-proof-packet.md)

## Preconditions

- live TWS login is active on VM2
- `TWS API` is enabled
- `Read-Only API` is intentionally disabled before the test
- account ref is confirmed: `U19859952`
- Pantheon runtime / telemetry / runtime-manager health checks are green
- operator is present and watching TWS during submission and cancel

## Guardrails

- instrument: `AAPL`
- action: `BUY`
- quantity: `1`
- order type: `LMT`
- tif: `DAY`
- `outsideRth`: `false`
- limit price: set deliberately far enough from the current market to avoid an
  immediate fill
- do not use `MKT`
- do not submit multiple orders
- do not leave the order open after the validation window

## Validation Goal

The goal is not execution quality. The goal is to prove:

1. Pantheon can submit one live order through the authenticated IBKR session.
2. IBKR returns an accepted open-order status.
3. Pantheon can cancel the same order.
4. TWS / telemetry / runtime-manager capture the order and cancel lifecycle.

Current implementation note: the repo now includes
`scripts/run_ibkr_live_order_cancel.py`, a narrow operator-supervised IBKR API
harness that places one live limit order and cancels it immediately. It is not
a general trading tool and it is not a runtime-manager route. Use it only as a
broker-fact capture helper for a packet that separately archives
runtime-manager origin, lifecycle, telemetry, operator, and closeout evidence.

## Manual Payload Shape

Initialize a pending capture packet first. This command only writes template
files; it does not submit an order.

```bash
python3 scripts/validate_ep5_live_order_cancel.py init \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --account U19859952 \
  --limit-price '<operator-set far-from-market value>' \
  --runtime-binding-id '<live-runtime-binding-id>' \
  --deployment-plan-id '<ep5-live-deployment-plan-id>' \
  --operator-id '<operator-id>'
```

Use the smallest payload your order path expects, but preserve these semantic
fields:

```json
{
  "account": "U19859952",
  "symbol": "AAPL",
  "security_type": "STK",
  "exchange": "SMART",
  "currency": "USD",
  "action": "BUY",
  "quantity": 1,
  "order_type": "LMT",
  "limit_price": "<operator-set far-from-market value>",
  "time_in_force": "DAY",
  "outside_rth": false
}
```

## Required Evidence

Capture all of these in one timestamped folder:

1. `runtime-manager-command-envelope.dry-run.json`
2. `ibkr-packet-manifest.json`
3. `runtime-manager-lifecycle.schema.json`
4. `operator-checklist.md`
5. `validator-expectations.md`
6. `closeout-template.md`
7. `live-order-submit.request.json`
8. `live-order-submit.response.json`
9. `tws-open-order-transcript.md` or `tws-open-order-screenshot.{png,jpg,jpeg}`
10. `live-order-cancel.request.json`
11. `live-order-cancel.response.json`
12. `telemetry-event-trace.response.json`
13. `runtime-manager-event-excerpt.json`
14. `operator-note.md` confirming the order was never filled, or if it was filled,
   the exact fill details and follow-up disposition

After the packet is captured, validate it with:

```bash
python3 scripts/validate_ep5_live_order_cancel.py validate \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --output-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp>/validation
```

The validator only checks archived evidence. It does not submit or cancel
orders, and it fails if any `<placeholder>` values remain.

## Stepwise Recording Commands

After each operator-observed fact is known, record it into the packet with the
matching command:

```bash
python3 scripts/validate_ep5_live_order_cancel.py record-request \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --account U19859952 \
  --limit-price '<operator-confirmed-non-marketable-limit>'

python3 scripts/validate_ep5_live_order_cancel.py record-submit \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --order-id '<broker-order-id>' \
  --status '<Submitted|Open|Accepted>' \
  --captured-at '<UTC timestamp>'

python3 scripts/validate_ep5_live_order_cancel.py record-cancel \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --status '<Cancelled|Canceled>' \
  --captured-at '<UTC timestamp>'

python3 scripts/validate_ep5_live_order_cancel.py record-telemetry \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --event-id '<telemetry-event-id>' \
  --runtime-binding-id '<runtime-binding-id>' \
  --deployment-plan-id '<deployment-plan-id>'

python3 scripts/validate_ep5_live_order_cancel.py record-runtime \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --runtime-binding-id '<runtime-binding-id>' \
  --deployment-plan-id '<deployment-plan-id>' \
  --operator-id '<operator-id>' \
  --submitted-at '<UTC timestamp>' \
  --canceled-at '<UTC timestamp>'

python3 scripts/validate_ep5_live_order_cancel.py record-tws \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --state '<Submitted|Open|Accepted>' \
  --operator-id '<operator-id>' \
  --observed-at '<UTC timestamp>'

python3 scripts/validate_ep5_live_order_cancel.py record-operator-note \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --operator-id '<operator-id>' \
  --submitted-at '<UTC timestamp>' \
  --canceled-at '<UTC timestamp>'
```

The broker submit/cancel responses may be captured by the guarded harness:

```bash
/tmp/pantheon-ibapi-venv/bin/python scripts/run_ibkr_live_order_cancel.py \
  --host 10.140.0.5 \
  --port 7496 \
  --client-id 76 \
  --account U19859952 \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --symbol AAPL \
  --quantity 1 \
  --limit-price '<operator-confirmed-non-marketable-limit>' \
  --cancel-after-seconds 2 \
  --i-understand-live-order
```

The harness explicitly sets deprecated IBKR order flags `eTradeOnly=false` and
`firmQuoteOnly=false`; TWS rejects the default `ibapi==9.81.1.post1` shape if
those flags are left at their package defaults.

## Stop Conditions

Abort immediately if any of these occur:

- `Read-Only API` is still enabled
- order type drifts to `MKT`
- quantity is not `1`
- symbol is not the agreed liquid test symbol
- the limit price would cross the current market
- operator cannot watch the order in TWS

## Closeout Criteria

This manual test is considered successful only if:

1. one live order is accepted
2. the same order is canceled
3. no unintended extra order is created
4. evidence is archived under a single timestamped packet

If the order fills before cancel, do not hide it. Record the fill truthfully and
treat the run as a live-fill incident rather than a cancel-path success.
