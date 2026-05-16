# OODA Stage Transition Validation Contract

**Task:** MGMT-OODA-003
**Module:** `stage_transition.py`
**Status:** delivered and review approved

## Purpose

`stage_transition.py` is the reusable validator for `OodaLoopPacket` status
advancement. It keeps stage ordering independent from the packet schema and
JSONL append store, while `OodaLoopPacket.advance()` delegates to it.

## Status Graph

```text
open -> observing -> oriented -> decided -> acted -> evolving -> closed
                                             \-----------------> closed
```

`failed` is allowed from any non-terminal status. `closed` and `failed` are
terminal and reject further events.

## Event Mapping

| Event | Target status |
|---|---|
| `observe` | `observing` |
| `orient` | `oriented` |
| `decide` | `decided` |
| `act` | `acted` |
| `learn` | `evolving` |
| `close` | `closed` |
| `fail` | `failed` |

Same-stage data append events are idempotent for the active matching stage
(`observe` while `observing`, `orient` while `oriented`, and so on). Skipped
stages and regressions are rejected.

## Transition Record Shape

```json
{
  "from_status": "decided",
  "to_status": "acted",
  "event": "act",
  "advanced": true,
  "transitioned_at": "2026-05-15T14:00:00Z"
}
```

These records are shaped for append-only persistence by the OODA JSONL store.

## Safety Invariants

- `closed` packets must include `closed_at`.
- `act.live_capital_side_effects` must be false in `dev`, `paper`, `sandbox`,
  and `canary`.
- `live` remains the only environment where `live_capital_side_effects=true`
  can pass packet-stage invariant validation.
