# OodaLoopPacket — Contract

**Module:** `services/control-plane/ooda/`
**Schema:** `ooda_loop_packet.schema.json`
**Model:** `ooda_loop_packet.py`
**Task:** MGMT-OODA-001
**Status:** delivered

---

## Purpose

`OodaLoopPacket` is the primary evidence and replay artifact for one complete
Observe → Orient → Decide → Act → Learn/Evolve governance loop in the Pantheon
Management Console.

Every OODA-complete loop must produce a packet that:

- captures all stage evidence references
- can be replayed from Control Room or a detail drawer
- prevents OODA-complete status when evidence is missing

---

## Allowed Loop Types

| Value | Meaning |
|---|---|
| `paper_strategy` | Paper trading strategy execution loop |
| `rebalance` | Portfolio rebalance decision loop |
| `evolution` | Model/strategy evolution loop |
| `incident_response` | Incident-triggered response loop |
| `persona_synthesis` | Multi-persona allocation synthesis loop |

---

## Lifecycle

```
open -> observing -> oriented -> decided -> acted -> evolving -> closed
 \________________________________________/_________\__________/ \-> failed (from any)
```

Only the transitions in `_ALLOWED_TRANSITIONS` are valid. Attempting a disallowed
transition raises `ValueError`.

---

## Stage Bundles

| Bundle | Stage | Key fields |
|---|---|---|
| `observe` | Observe | `source_refs`, `telemetry_refs`, `signal_refs`, `market_refs`, `incident_refs`, `human_feedback_refs` |
| `orient` | Orient | `regime_state_ref`, `universe_selection_ref`, `signal_inference_refs`, `allocation_proposal_refs`, `risk_adjudication_ref`, `persona_proposal_refs` |
| `decide` | Decide | `approval_decision_id`, `deployment_plan_id`, `evolution_decision_id`, `sponsor_persona_id`, `policy_decision_refs` |
| `act` | Act | `runtime_binding_id`, `command_receipt_refs`, `broker_evidence_refs`, `rollback_refs`, `safe_mode_refs`, `live_capital_side_effects` |
| `learn` | Learn/Evolve | `telemetry_refs`, `postmortem_refs`, `evolution_followthrough_refs`, `trainer_refs`, `retrain_refs`, `observation_window` |

---

## Safety Invariant

`act.live_capital_side_effects` **must be `false`** in all environments except
`live`. Validation rejects any packet that violates this rule.

This is a hard contract constraint, not a soft check. Environments:

| Environment | `live_capital_side_effects` allowed |
|---|---|
| `dev` | no |
| `paper` | no |
| `sandbox` | no |
| `canary` | no |
| `live` | yes (requires explicit activation gate) |

---

## Storage

v1: JSONL append-only store via `OodaLoopStore`.

- Each call to `add()` or `update()` appends a snapshot line to the store file.
- The in-memory index holds the latest snapshot per `packet_id`.
- v2 migration target: `ooda.loop_packet` Postgres table.

Default store path:

```text
services/control-plane/ooda/store/ooda_packets.jsonl
```

---

## BFF Routes (owned by MGMT-OODA-004)

```http
GET /bff/ooda/packets
GET /bff/ooda/packets/{packet_id}
GET /bff/strategies/{id}/ooda
GET /bff/runtimes/{id}/ooda
GET /bff/evolution-programs/{id}/ooda
```

---

## Usage Example

```python
from services.control_plane.ooda.ooda_loop_packet import (
    OodaLoopPacket, LoopType, LoopEnvironment, LoopStatus, OodaLoopStore,
)

# Create a paper strategy loop
packet = OodaLoopPacket.create(
    loop_type=LoopType.PAPER_STRATEGY,
    environment=LoopEnvironment.PAPER,
    strategy_id="strat-001",
)

# Observe
packet.add_observe_ref("source_refs", "src-event-abc123")
packet.add_observe_ref("telemetry_refs", "tel-heartbeat-xyz")

# Orient
packet.advance(LoopStatus.ORIENTED)
packet.add_orient_ref("regime_state_ref", "regime-bull-2026q2")

# Decide
packet.advance(LoopStatus.DECIDED)
packet.decide.approval_decision_id = "approval-001"
packet.decide.deployment_plan_id = "dep-plan-001"
packet.updated_at = ...

# Act
packet.advance(LoopStatus.ACTED)
packet.act.runtime_binding_id = "rb-paper-001"
packet.act.command_receipt_refs.append("receipt-deploy-001")

# Learn
packet.advance(LoopStatus.EVOLVING)
packet.learn.telemetry_refs.append("tel-pnl-session-001")
packet.advance(LoopStatus.CLOSED)

# Persist
store = OodaLoopStore("services/control-plane/ooda/store/ooda_packets.jsonl")
store.add(packet)
```

---

## Validation Errors

| Error | Cause |
|---|---|
| `packet_id is required` | Empty `packet_id` |
| `packet_id must start with 'ooda-'` | Prefix missing |
| `Unknown loop_type` | Not in `LoopType` enum |
| `Unknown status` | Not in `LoopStatus` enum |
| `Unknown environment` | Not in `LoopEnvironment` enum |
| `live_capital_side_effects must be False in environment ...` | Non-live env with live side effects |
| `created_at is required` | Empty timestamp |
| `updated_at is required` | Empty timestamp |

---

## Acceptance Criteria (MGMT-OODA-001)

- [x] `OodaLoopPacket` schema defined with all five stage bundles
- [x] `LoopType`, `LoopStatus`, `LoopEnvironment` enums defined
- [x] Stage transition table enforced by `advance()`
- [x] `live_capital_side_effects` safety invariant enforced
- [x] `OodaLoopStore` JSONL append-only store implemented
- [x] `validate_packet()` standalone helper available
- [x] JSON schema (`ooda_loop_packet.schema.json`) aligned with Python model
- [x] Contract documentation (`contract.md`) complete

Downstream tasks:
- `MGMT-OODA-002`: JSONL append store (uses `OodaLoopStore`)
- `MGMT-OODA-003`: stage transition validation (extends `advance()`)
- `MGMT-OODA-004`: BFF read routes
- `MGMT-OODA-005` / `MGMT-OODA-006`: Control Room card and drawer
- `MGMT-OODA-007`: unit/integration tests
