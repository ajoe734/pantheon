# P2-001 Schema Review — Claude (LEAN Execution Plane Perspective)

**Reviewer:** Claude  
**Review date:** 2026-04-01  
**Schema file reviewed:** `services/research/schema.json` (current version, post-Codex alignment)  
**Status:** REQUESTING CHANGES — 2 critical issues must be resolved before schema can be LOCKED

---

## TL;DR

The current schema has two issues that will cause **silent runtime errors** in LEAN if shipped as-is. Both are fixable with small additions. The schema also adds two genuinely good features (`order_type`/`limit_price` and `insight_type`/`insight_direction`) that Gemini's original draft lacked.

---

## Critical Issues (must fix before LOCK)

### C-1: `quantity` has no unit — will cause execution errors

**Severity: Critical**

`quantity` is a bare `number` with no type discriminator. A worker emitting portfolio weight `0.05` and a worker emitting share count `100` produce identical JSON except for value magnitude. LEAN cannot distinguish them:

```json
// ML model output — weight-based, 5% of portfolio
{ "quantity": 0.05 }

// Quant strategy output — absolute shares
{ "quantity": 100 }
```

LEAN's execution methods are fundamentally different per interpretation:

| interpretation | LEAN call | result on 0.05 |
|---|---|---|
| weight | `self.SetHoldings(symbol, 0.05)` | 5% portfolio — correct |
| shares | `self.MarketOrder(symbol, round(0.05))` | 0 shares — silently does nothing |
| notional_usd | `self.MarketOrder(symbol, round(0.05 / price))` | 0 shares — same |

**Fix:** Add `quantity_type` as a required sibling field:

```json
"quantity_type": {
  "type": "string",
  "enum": ["weight", "shares", "notional_usd"],
  "description": "How to interpret quantity. weight: fraction of portfolio (0,1]; shares: absolute integer count; notional_usd: USD dollar amount."
}
```

Add to `required`: `["version", "signal_id", "strategy_id", "timestamp", "symbol", "action", "quantity", "quantity_type"]`

---

### C-2: `SELL` direction is ambiguous — LEAN will guess wrong half the time

**Severity: Critical**

`direction` was removed from the schema. With `action=SELL` and no direction signal, LEAN faces an irresolvable ambiguity:

- **Short-sell** → `self.SetHoldings(symbol, -weight)` (go short)
- **Close long** → `self.Liquidate(symbol)` (exit existing long)

These are opposite operations. Executing a short-sell when the intent was to close a long compounds the wrong position.

`metadata.insight_direction` partially addresses this (`Down` ≈ short intent) but:
1. `metadata` is optional — execution plane can't rely on it
2. The combination `action=SELL + insight_direction=Down` requires the executor to join two fields with undocumented semantics

**Fix (Option A — minimal):** Add `direction` back as a required field when `action=SELL`:

```json
"if": {
  "properties": { "action": { "enum": ["SELL"] } },
  "required": ["action"]
},
"then": {
  "required": ["direction"]
},
"properties": {
  "direction": {
    "type": "string",
    "enum": ["short", "flat"],
    "description": "short = open/add short position; flat = close existing long. Required when action=SELL."
  }
}
```

**Fix (Option B — lighter):** Rename `EXIT` to `CLOSE_LONG` and keep `SELL` as short-only:

```json
"action": { "enum": ["BUY", "SELL", "HOLD", "CLOSE_LONG", "CLOSE_SHORT", "CLOSE_ALL"] }
```

Recommendation: **Option A**. Minimal change, keeps the current enum, adds a clearly-scoped required field.

---

## Advisory Issues (document or fix before production)

### A-1: `symbol` flat string needs a normalization spec

The format `"AAPL.US"` is parseable but not self-describing. Edge cases:

| symbol string | ambiguity |
|---|---|
| `2330.TW` | Taiwan Stock Exchange — does LEAN support Market.TW? |
| `BTCUSDT` | no `.` separator — binance format vs coinbase `BTC-USD` |
| `AAPL 231215C00180000` | option contract — what market? |

**Minimum required:** A normalization spec document listing the exact format rule (`{TICKER}.{MARKET_CODE}`) and an exhaustive mapping of `MARKET_CODE` → `(LEAN Market, LEAN SecurityType)`. This can live in `signal_schema_v1.md` §3.3. Execution plane will build the parser from this spec.

### A-2: `limit_price` dependency not enforced in schema

The description says "Required if order_type is LIMIT" but JSON Schema doesn't enforce it. Workers can emit `order_type=LIMIT` without `limit_price` and the schema will not catch it. Add:

```json
"if": {
  "properties": { "order_type": { "const": "LIMIT" } },
  "required": ["order_type"]
},
"then": { "required": ["limit_price"] }
```

### A-3: `version` format is ambiguous

`"1.0"` vs `"1.0.0"` — the spec document uses semver but the schema accepts any string. The major version check that execution plane needs to implement (`if major != 1: reject`) requires parsing the version string. Document the exact format: either `"MAJOR.MINOR"` or `"MAJOR.MINOR.PATCH"`, consistently.

Recommendation: use `"1.0.0"` (semver) and add a pattern: `"^\\d+\\.\\d+\\.\\d+$"` to the schema.

### A-4: `confidence` buried in optional metadata

`metadata.confidence_score` is optional. The execution plane's confidence-scaled position sizing (documented in `signal_schema_v1.md §3.4`) relies on this value. When absent, the executor needs a documented fallback. Recommend: document the default as `1.0` (full confidence, no scaling) and state this in `signal_schema_v1.md`.

### A-5: `source_worker` removed — P4-001 traceability gap

The control-plane tools (QlibTool, VectorbtTool, etc.) need `source_worker` to:
- Route signals to the correct tool class for replay/re-run
- Tag audit log entries with the producing system

Without it, the control plane has no way to know which worker produced a signal at read time. Recommend adding as optional field:

```json
"source_worker": {
  "type": "string",
  "enum": ["qlib", "vectorbt", "finrl", "quantlib"],
  "description": "Optional. Identifies the research worker that produced this signal. Used by control-plane tools for routing and audit."
}
```

### A-6: `quantity: minimum: 0` permits zero-quantity orders

`quantity: 0` is structurally valid but logically ambiguous — it could mean "flat/no trade" or be an accidental zero. Use `exclusiveMinimum: 0`, or explicitly document that `action=HOLD` is the correct way to signal "no trade" and quantity must always be positive.

---

## What's Good — Keep These

| Feature | Why it's good |
|---|---|
| `order_type` + `limit_price` | Practical necessity; enables LIMIT orders for slippage control |
| `metadata.insight_type` + `insight_direction` | Maps directly to LEAN's Alpha Insight framework (`InsightType.Price`, `InsightDirection.Up/Down/Flat`). Enables future LEAN Alpha model integration |
| `run_id` as optional | Correct — most signals are not FinRL rebalances |
| Flat `symbol` string | Simpler for workers to produce; acceptable if normalization spec is published |

---

## Summary of Required Changes

| ID | Severity | Change |
|----|----------|--------|
| C-1 | Critical | Add required `quantity_type` enum field alongside `quantity` |
| C-2 | Critical | Add `direction` field required when `action=SELL` (via `if/then`) |
| A-2 | Advisory | Enforce `limit_price` required when `order_type=LIMIT` via `if/then` |
| A-3 | Advisory | Constrain `version` to semver pattern `^\d+\.\d+\.\d+$` |
| A-1 | Advisory | Publish symbol normalization spec (format + LEAN mapping table) |
| A-4 | Advisory | Document `confidence` default when absent from metadata |
| A-5 | Advisory | Add optional `source_worker` field |
| A-6 | Advisory | Change `quantity: minimum: 0` → `exclusiveMinimum: 0` |

Schema can be LOCKED once C-1 and C-2 are resolved. Advisory items can ship as v1.0.1.

---

## LEAN Consumer Implementation Notes (for P3-001)

Once C-1 and C-2 are resolved, I will implement the following in `services/execution/lean-runtime/`:

1. `signal_consumer.py` — polls SignalStore, validates schema version, calls executor
2. Symbol parser: `"AAPL.US"` → `Symbol.Create(ticker, SecurityType, Market)` (uses normalization spec from A-1)
3. Quantity dispatch: branch on `quantity_type` → `SetHoldings` / `MarketOrder` / notional conversion
4. Expiry check: executor owns TTL since `expiry_ts` was removed from schema (1-bar fallback)
5. FinRL rebalance buffer: group by `run_id`, timeout after configurable window
6. Conflict resolver: last-write-wins by `timestamp`, tie-break by `metadata.confidence_score`
