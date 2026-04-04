# OpenClaw Signal Schema v1.0

**Status:** aligned to `services/research/schema.json` v1.0 for execution-facing fields  
**Owner:** Gemini  
**Reviewer:** Claude  
**Canonical machine schema:** `services/research/schema.json`  
**Example payloads:** `services/research/signal_example.json`, `services/research/example_payload.json`

---

## 1. Purpose

This schema defines the signal contract that crosses OpenClaw plane boundaries:

```text
Research workers
    -> SignalStore
    -> LEAN execution plane
    -> control-plane routing and audit
```

The machine-readable source of truth is `services/research/schema.json`.
This document explains the same contract in human terms so downstream consumers do not invent a second interpretation.

---

## 2. Core Payload

| Field | Required | Type | Description |
|---|---|---|---|
| `version` | yes | string | Schema version string, for example `1.0` |
| `signal_id` | yes | UUID string | Idempotency key for storage and execution |
| `strategy_id` | yes | string | Strategy or alpha identifier |
| `run_id` | no | string | Optional batch or rebalance grouping id |
| `timestamp` | yes | ISO 8601 UTC | When the signal was generated |
| `symbol` | yes | string | Normalized symbol such as `AAPL.US`, `BTCUSDT`, `2330.TW` |
| `action` | yes | enum | `BUY`, `SELL`, `HOLD`, `EXIT` |
| `direction` | yes | enum | `LONG`, `SHORT` |
| `order_type` | no | enum | `MARKET` or `LIMIT`, default `MARKET` |
| `limit_price` | no | number | Required when `order_type=LIMIT` |
| `quantity` | yes | number >= 0 | Order amount |
| `quantity_type` | yes | enum | `SHARES`, `PERCENT_PORTFOLIO`, `CASH_VALUE` |
| `metadata` | no | object | Audit, confidence, risk, and insight context |

### 2.1 Metadata Fields

The current schema supports these metadata keys:

| Field | Required | Type | Description |
|---|---|---|---|
| `metadata.confidence_score` | no | float [0,1] | Raw model confidence for audit or routing |
| `metadata.risk_parameters.stop_loss_pct` | no | number | Suggested stop-loss percentage |
| `metadata.risk_parameters.take_profit_pct` | no | number | Suggested take-profit percentage |
| `metadata.insight_type` | no | enum | `Price` or `Volatility` |
| `metadata.insight_direction` | no | enum | `Up`, `Down`, `Flat` |

`run_id` is optional because not every worker emits grouped signals, but it becomes important for batch-style execution such as portfolio rebalance flows.

---

## 3. Execution Semantics

### 3.1 `action` + `direction`

`SELL` is ambiguous without a direction field. The pair must be interpreted together:

| action | direction | meaning |
|---|---|---|
| `BUY` | `LONG` | open or add to a long position |
| `SELL` | `SHORT` | open or add to a short position |
| `HOLD` | `LONG` | maintain an existing or intended long posture, no order submission |
| `HOLD` | `SHORT` | maintain an existing or intended short posture, no order submission |
| `EXIT` | `LONG` | close long exposure for the symbol |
| `EXIT` | `SHORT` | close short exposure for the symbol |

### 3.2 `EXIT + direction`

This was the remaining documentation gap from Claude's review and is now the required interpretation:

- `EXIT` does not mean "flatten in an unspecified way"
- `direction` tells the execution plane which side is being closed
- `EXIT + LONG` means sell down or close an existing long
- `EXIT + SHORT` means buy to cover an existing short
- execution consumers must not guess the side from portfolio state when the signal already specifies it

If a producer wants to flatten whichever side is currently open, it must still emit an explicit direction that matches the intended close path.

### 3.3 `quantity_type`

`quantity` has no meaning unless paired with `quantity_type`:

| quantity_type | meaning | LEAN-oriented note |
|---|---|---|
| `SHARES` | absolute shares or units | execution may need integer rounding depending on asset type |
| `PERCENT_PORTFOLIO` | target portfolio fraction | commonly maps to `SetHoldings`-style behavior |
| `CASH_VALUE` | cash notional amount | execution converts notional to order quantity using price |

### 3.4 `order_type`

- `MARKET` means no price constraint
- `LIMIT` requires `limit_price`
- if `order_type=LIMIT` and `limit_price` is missing, producer-side validation should fail before store write

---

## 4. Symbol Contract

The locked execution schema uses a flat `symbol` string, not the older structured object form.

Examples:

- `AAPL.US`
- `MSFT.US`
- `BTCUSDT`
- `2330.TW`

This keeps the transport contract compact and leaves broker- or LEAN-specific symbol resolution to the execution layer.

The older structured form:

```json
{
  "ticker": "AAPL",
  "market": "usa",
  "security_type": "equity"
}
```

is no longer the canonical transport shape for v1.0.

---

## 5. Validation Rules

### 5.1 Producer Side

Research workers must validate payloads against `services/research/schema.json` before writing them to SignalStore.

### 5.2 Store Side

SignalStore should reject duplicate `signal_id` values and preserve the payload shape without transport-layer rewrites.

### 5.3 Consumer Side

Execution and control-plane consumers should:

- check the `version` field
- validate required fields defensively
- reject malformed signals without crashing the runtime

---

## 6. Multi-Signal and Batch Behavior

### 6.1 Rebalance Batches

When a worker emits a rebalance, it may send multiple signals that share the same `run_id`.

Consumer expectation:

- the execution plane can group by `run_id`
- grouped signals represent one coordinated batch, not unrelated trades

### 6.2 Conflicting Signals

If multiple signals target the same symbol and conflict, execution policy should be documented in `P3-001`.
This document does not hard-code the conflict resolver beyond requiring that signals remain individually valid against the schema.

---

## 7. Failure Modes to Handle Downstream

| Failure | Handling expectation |
|---|---|
| Missing required field | reject before store write or skip in consumer with error log |
| Unknown `version` | log and skip incompatible payload |
| Duplicate `signal_id` | reject as idempotent duplicate |
| `LIMIT` without `limit_price` | validation failure |
| `EXIT` with mismatched execution side | log and reject or safely no-op according to runtime policy |
| malformed `symbol` string | log and skip instead of crashing the runtime |

---

## 8. Notes for Downstream Tasks

### For `P3-001`

- parse the flat `symbol` string into the execution-layer symbol model
- branch order construction by `action`, `direction`, `order_type`, and `quantity_type`
- treat `HOLD` as informational, not an order submission
- implement `EXIT + LONG` and `EXIT + SHORT` as explicit close paths

### For `P4-001`

- tools that emit signals should validate against `schema.json`
- control-plane routing may inspect `metadata.confidence_score` and insight fields, but should not rewrite the execution contract in transit

### For `P2-002`

- examples and docs must stay aligned to `schema.json`
- future schema changes should update the machine schema first, then this document and examples in the same change set

---

## 9. Open Questions for Later Versions

- Should `run_id` eventually require a stricter format such as UUID?
- Should the v1.x schema add an explicit source provenance field, or should provenance stay inside metadata and registry records?
- Should the execution layer define a canonical symbol normalization grammar for futures and options in v1.1?
