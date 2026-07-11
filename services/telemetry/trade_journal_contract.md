# Persona Trade Journal Service Contract (PTJ-001)

Status: canonical contract
Task-ID: PTJ-001
Owner: Antigravity
Reviewer: Codex2

## 1. Boundary & Truth Ownership

To maintain system-wide consistency and avoid dual-write synchronization conflicts:
- **Order, Fill, Position Truth**: Emitted by `Runtime Manager` / `Lean` execution telemetry. Telemetry stores these events canonically. BFF is strictly read-only and must never act as a shadow transaction or state database for orders/fills.
- **Valuation, P&L, Slippage Truth**: Realized/unrealized P&L, holding period return, fees, and slippage are computed by the valuation/attribution service.
- **Decision Lineage Truth**: Rationale, catalyst, risk bounds, universe selection, and intents belong to the `Registry Core` and `Decision Journal` / OODA loop lineage.
- **Reflection & memory Governance Truth**: The `Persona` service owns the lifecycle of reflections and lesson candidates.

```
       [Registry Core] (Rationale/OODA)
             │
             ▼
[Telemetry / Runtime Manager] (Execution: order/fill/position)
             │
             ▼
      [BFF Projection] (TradeEpisodeProjection Read-Model)
             │
             ▼
       [Persona Svc] (PersonaTradeReflection & memory Governance)
```

---

## 2. Episode Identity & Lifecycle State Machine

A `trade_episode_id` represents a Persona's unique financial exposure trace for a single instrument and strategy direction, from initial entry intent until exposure returns to zero or is reversed.

### State Transitions
```
proposed → approved → submitted → partially_filled → open
   │
   └─→ rejected/cancelled/aborted
open → reducing → closed → reflection_pending → reflected
open/reducing → force_closed → reflection_pending
reflection_pending → reflection_failed → reflection_pending (audited retry)
```

### Identity Rules
1. **Scale-in & Scale-out**: Partial fills, add-ons, and scale-outs on the same strategy thesis and direction belong to the *same* episode.
2. **Reversals**: If a position reverses (e.g. long to short), the current episode MUST be closed and a new episode created. Shared P&L across a reversal is forbidden.
3. **Aborted Intents**: Intentions that are rejected or cancelled without any fills are marked as `aborted` episodes to facilitate decision quality reflections, but carry zero execution quantities or P&L.
4. **Interventions**: Risk liquidations, manual exits, or kill-switch actions must mark the exit actor (e.g., `exit_actor="risk_system"`) and the cause.
5. **Duplicate / Late Arrivals**: Events that arrive out-of-order or late are integrated using event sequence and canonical timestamps. Each `TradeJournalEvent` carries a required `sequence_number` (integer ≥ 0) metadata field. Replay/projection processors use this to detect duplicates, sequence late arrivals, and perform idempotent updates. `TradeEpisodeProjection` tracks the state by recording the `last_event_sequence` of the last successfully processed event.

---

## 3. Data Integration & Migration Strategy

Historical or legacy execution logs might lack proper correlation IDs (`trade_episode_id` or `trace_id`).
- **Unresolved Joins**: Any trade execution event or attribution mismatch that cannot be joined to a decision with 100% confidence must be marked as `unresolved`. Mismatches must NEVER be silently merged using heuristic proximity or timestamp estimation.
- **Degraded Coverage State**: The projection's `coverage.state` enum (`complete`, `partial`, `degraded`, `unavailable`) allows the system to inform the operator when data-plane or telemetry records are missing.
- **Incremental Cutover**: All new execution instances under v2 runtime bindings must inject the `trade_episode_id` header in telemetry capture payloads.

---

## 4. Contract Schemas

Four versioned JSON schemas lock down these boundaries:
1. `services/telemetry/trade_episode_projection.schema.json`
2. `services/persona/persona_trade_reflection.schema.json`
3. `services/persona/trade_lesson_candidate.schema.json`
4. `services/telemetry/trade_journal_event.schema.json`

---

## 5. Verification

Contract conformance is verified by unit and schema integration tests:
- `services/telemetry/test_trade_journal_contracts.py`
- `services/persona/test_trade_reflection_contracts.py`
