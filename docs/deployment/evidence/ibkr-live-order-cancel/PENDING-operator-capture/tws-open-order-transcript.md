# TWS Open Order Transcript

- observed_at: 2026-04-26T13:27:18Z
- symbol: AAPL
- order_id: 1
- perm_id: 204599504
- state: PreSubmitted
- order_type: LMT
- limit_price: 120.0
- quantity: 1
- filled: 0.0
- remaining: 1.0
- operator: ajoe7341113
- evidence_basis: operator reported VM2 TWS visible; IBKR Mobile screenshot showed
  the AAPL limit order with 0 filled; API submit response captured matching
  order_id, perm_id, and PreSubmitted state.

Follow-up readback on 2026-04-27T05:16:14Z reported the order absent from open
orders with no matching executions. See
`read-only-verify-20260427T051345Z/summary.json`.
