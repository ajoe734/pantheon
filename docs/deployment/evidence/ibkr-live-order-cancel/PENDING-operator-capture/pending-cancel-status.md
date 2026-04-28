# Live Order Cancel Disposition

Status: validated manual live broker packet

Earlier readbacks through `2026-04-26T13:36:06Z` showed one open order:

- account: `U19859952`
- order_id: `1`
- perm_id: `204599504`
- symbol: `AAPL`
- action: `BUY`
- quantity: `1`
- order_type: `LMT`
- limit_price: `120`
- status: `PendingCancel`
- filled: `0.0`
- remaining: `1.0`

As of `2026-04-26T13:50:00Z`, the read-only monitor in
`read-only-monitor-20260426T1350Z/summary.json` reports the order as absent
from open orders:

- status: `terminal_or_absent`
- order_id: `1`
- open_order_count: `0`
- statuses: `[]`
- absent: `true`

The same probe set includes TWS/server connectivity warnings (`2110` and data
farm disconnects), while `session-health.json` still proves an authenticated
socket session via `nextValidId` and managed account `U19859952`. Treat the
absence result as useful readback evidence, not as final cancel/no-fill proof
by itself.

As of `2026-04-27T05:16:14Z`, the refreshed read-only verification packet in
`read-only-verify-20260427T051345Z/summary.json` reports:

- authenticated IBKR session: `ok`
- account ref present: `U19859952`
- open_order_count: `0`
- fill_status: `no_matching_executions`
- matching_execution_count: `0`
- matching_shares: `0.0`

This is stronger broker readback than the earlier April 26 probe because the
same packet includes authenticated session, open-order, and execution evidence.
It still does not fill the Pantheon runtime-manager / telemetry live refs gap.

As of `2026-04-27T09:30:43Z`, the packet also includes non-placeholder
runtime/telemetry archive refs for the manual live broker lifecycle:

- runtime_binding_id: `rb-ep5-002-ibkr-live-manual-20260426T132701Z`
- deployment_plan_id: `plan-ep5-002-ibkr-live-manual-20260426`
- telemetry event: `evt-ep5-002-ibkr-live-order-1-disposition-20260427T051614Z`

These close the packet-level runtime/telemetry archive gap for the manual
harness route. They do not claim the deployable runtime-manager service
originated the live order; `runtime-manager-event-excerpt.json` records that
boundary explicitly.

As of `2026-04-27T10:27:17Z`, operator `ajoe7341113` confirmed in TWS/IBKR UI
that permId `204599504` / order_id `1` is canceled or absent and not filled.
`operator-note.md` records this final closeout.

Cancel attempts already issued:

- initial cancel from `scripts/run_ibkr_live_order_cancel.py`
- same-client cancel retry from client id `79`
- global cancel request from client id `83`

IBKR returned `10148`: `OrderId 1 that needs to be cancelled cannot be cancelled, state: PendingCancel.`

Latest evidence:

- `read-only-monitor-20260426T1350Z/summary.json`
- `read-only-monitor-20260426T1350Z/session-health.json`
- `read-only-probe-20260426T134911Z/open-orders.json`
- `read-only-verify-20260427T051345Z/summary.json`
- `read-only-verify-20260427T051345Z/session-health-retry.json`
- `read-only-verify-20260427T051345Z/open-orders-retry.json`
- `read-only-verify-20260427T051345Z/executions.json`
- `post-pending-cancel-monitor-1.json`
- `post-user-tws-cannot-delete-open-orders-probe.json`
- `global-cancel-request.json`
- `cancel-retry-client79.json`

The packet was validated at `2026-04-27T10:27:30Z`; see
`validation-latest/summary.json`.
