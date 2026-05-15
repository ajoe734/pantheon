# EP5-002 Live Order / Cancel Evidence Packet

Status: validated manual live broker packet

This folder contains the validated EP5-002 manual live order/cancel evidence
packet for the guarded IBKR harness route. Validation passed at
`2026-04-27T10:27:30Z`.

Proof boundary: this is a manual live broker packet with archived
runtime/telemetry lineage refs. It does not claim the deployable
runtime-manager service originated the live order.

Required files:

- live-order-submit.request.json
- live-order-submit.response.json
- live-order-cancel.request.json
- live-order-cancel.response.json
- telemetry-event-trace.response.json
- runtime-manager-event-excerpt.json
- tws-open-order-transcript.md or tws-open-order-screenshot.{png,jpg,jpeg}
- operator-note.md

Validation output:

- `validation-latest/summary.json`
- `validation-latest/ep5-live-order-cancel-validation.json`
