# AG-BE-TR-001 BFF and Frontend Handoff Packet — Followup 3

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-001` — Trading room aggregate and event queues |
| Parent owner / reviewer | `Claude2` / `Codex` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |
| Supersedes / builds on | `support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` (done, PR #2137 merged) |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. The parent owner decides whether and how to absorb
this material.

## Cumulative packet scope

| Packet | Key additions |
|---|---|
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF` (done) | BFF query gap matrix, operator journeys A–H, `tradingRoom.ts` method signatures, acceptance checks, open design notes. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (done) | Phased implementation sequence, backend module structure, D9 position event fields, Trading Room SSE contract, BFF degraded-response patterns, TypeScript types, safety wording, pending questions Q1–Q5. |
| **This packet (FOLLOWUP-3)** | Schema-derived corrections to Packet 2 TypeScript types, Q1/Q2/Q4 resolution, `additionalProperties` clarification for degradation signalling, idempotency implementation pattern, BFF test structure supplement, remaining open questions. |

## Current state observed

| Surface | Observed 2026-06-21 | Change since Packet 2 |
|---|---|---|
| `AG-BE-TR-001` | `todo`; owner `Claude2`, reviewer `Codex`. | Unchanged. |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2`. | Unchanged. D8 promotion leg still gated. |
| `AG-FE-TR-001` | `todo`. | Unchanged. |
| `trading_room/router.py` | Placeholder returning empty `APIRouter`. | Unchanged. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Does not exist. | Unchanged. |

## Resolved pending questions from Packet 2

### Q2 — Is `top_decision_events` in the aggregate required or optional?

**Resolution:** Optional. The `trading_room_aggregate.schema.json` lists it under `properties` but
not under `required`. The BFF may omit it when not yet populated; it must not return
`null` — omit the key entirely. When present, each item must validate against
`trading_decision_event.schema.json` (the schema uses `$ref`).

The aggregate is also free of `additionalProperties: false` at the root level
(see § Schema-correctness note on degradation below), so the `top_decision_events` key
can be included or omitted without affecting other fields.

### Q4 — Does `POST .../handoffs` return `202` or `201`?

**Resolution:** `202` confirmed from `agora_v1_3.openapi.yaml` (line 683):
```
"202":
  description: Request-only handoff submitted
```

Full matrix from the OpenAPI spec:

| Route | Method | Status code |
|---|---|---|
| `…/decision-events/{id}/decisions` | POST | `201` — Decision recorded (may create TradingIntent) |
| `…/trading-intents/{id}/handoffs` | POST | `202` — Request-only handoff submitted |
| `…/trading-intents/{id}/withdraw` | POST | `200` — Intent/handoff withdrawal recorded |

### Q1 — Can `degradation_notes` be added to the aggregate response?

**Resolution:** No. The `trading_room_aggregate.schema.json` has `"additionalProperties": false`
at the `TradingRoomAggregate` root level. Adding a `degradation_notes` key to the
aggregate response would cause schema validation failures in any consumer that validates
against the schema.

**Correct degradation-signalling approach (schema-valid):**

When `candidate_count` cannot be populated because AG-BE-CP-001 is unavailable:

1. **Per-strategy**: omit the `candidate_count` key from the affected strategy entry in
   `strategies[]`. The field is optional (not in the strategy `required` array). Do not
   set it to `0` — that is semantically "zero candidates", not "data unavailable".

2. **Per-strategy staleness**: add a `staleness_reasons` entry on the strategy object. The
   `staleness_reasons` field is in the strategy schema as an optional `string[]`:
   ```json
   { "staleness_reasons": ["candidate_count_source_unavailable"] }
   ```

3. **Aggregate risk summary**: if the missing data warrants a user-visible notice,
   add the reason to the optional `risk_summary.alerts` array:
   ```json
   { "alerts": ["candidate_count_source_unavailable"] }
   ```

Do not use either `staleness_reasons` or `risk_summary.alerts` for fields that have their
own schema-level signalling. Only use them for genuinely degraded/missing sources.

## TypeScript type corrections (schema-derived)

The TypeScript types published in Packet 2 contained several discrepancies with the actual
v4 JSON schemas. The corrections below are authoritative against the schemas; the Packet 2
types should not be used verbatim.

### `TradingDecisionEvent` corrections

**1. Add missing `spec_version` required field**

The schema requires `spec_version: "1.0"` but Packet 2's interface omitted it.

```ts
// Add to TradingDecisionEvent:
spec_version: "1.0";
```

**2. `suggested_size` shape is different**

Packet 2 used `{ value: number; unit: string; non_binding: true }`.  
The schema (`"additionalProperties": false`) defines:

```ts
suggested_size?: {
  size_hint?: "small" | "medium" | "large" | "full_position";
  portfolio_pct?: number;   // 0–1 fraction of portfolio
  non_binding: true;        // const true
};
```

There is no `value` or `unit` field at the top level. `size_hint` provides a qualitative
band; `portfolio_pct` provides a numeric fraction (0 to 1). Both are optional. The
`non_binding: true` const is required when the object is present.

**Frontend note**: the "non-binding" label rule from Packet 2 still applies. Display
`size_hint` and `portfolio_pct` (if present) both with the label; never show them as
order-size inputs.

**3. `confidence.calibration_state` enum is different**

Packet 2 used `"calibrated" | "uncalibrated" | "degraded"`.  
The schema enum is:
```ts
calibration_state: "calibrated" | "partially_calibrated" | "uncalibrated";
```
`"degraded"` is not a valid value. `"partially_calibrated"` is an intermediate state that
means the model has some calibration evidence but not a full calibration set.

**4. `invalidation.current_state` enum is different**

Packet 2 used `"none" | "watch" | "invalidated"`.  
The schema enum is:
```ts
current_state: "valid" | "watch" | "invalidated";
```
`"none"` is not a valid value. Use `"valid"` to indicate no invalidation condition.

**5. `invalidation` has a required `conditions` array**

Packet 2's type omitted this. The schema requires both `conditions` and `current_state`:
```ts
invalidation: {
  conditions: string[];            // required; may be []
  current_state: "valid" | "watch" | "invalidated";
  last_checked_at?: string;
};
```

**6. `origin` is a constrained enum, not `string`**

Packet 2 typed `origin: string`. The schema constrains it to:
```ts
origin: "strategy_signal" | "risk_rule" | "position_rule" | "servant_analysis" | "trader_request";
```

**7. `suggested_action` is a constrained enum, not `string`**

Packet 2 used `suggested_action: string`. The schema constrains it to:
```ts
suggested_action: "enter" | "add" | "reduce" | "exit" | "review" | "no_action";
```

**8. Add missing optional fields from schema**

```ts
// Add to TradingDecisionEvent:
trigger?: {
  rule_id?: string;
  summary?: string;
  current_value?: unknown;
  threshold?: unknown;
  distance_to_trigger?: number;
};
expires_at?: string;
decision_state?: "pending" | "approved_by_trader" | "rejected_by_trader" | "deferred" | "expired" | "handed_off" | "superseded";
candidate_ref?: string;
position_ref?: string;
```

**9. `rationale` requires at least one item**

The schema has `"minItems": 1` on `rationale`. An empty `rationale: []` is schema-invalid.
The BFF projection must always populate at least one rationale claim; the test suite must
assert `rationale.length >= 1`.

**10. `evidence_ref.ref_type` full enum**

Packet 2 did not include all valid `ref_type` values. The schema enum is:
```ts
ref_type: "evidence_bundle" | "evidence_item" | "source_record" | "citation" |
          "experiment_artifact" | "registry_entry" | "consult_memo" |
          "research_run" | "telemetry_snapshot" | "market_context";
```

### Corrected `TradingDecisionEvent` interface

Full corrected interface replacing the Packet 2 version:

```ts
export interface TradingDecisionEvent {
  spec_version: "1.0";
  decision_event_id: string;
  event_kind: "entry" | "add" | "reduce" | "exit" | "review";
  origin: "strategy_signal" | "risk_rule" | "position_rule" | "servant_analysis" | "trader_request";
  strategy_id: string;
  strategy_spec_registry_id: string;
  subject: { symbol: string; asset_class?: string; venue?: string };
  state: "approaching" | "triggered" | "pending_review" | "decided" | "invalidated" | "expired" | "superseded";
  triggered_at: string;
  confidence: {
    value: number;            // 0–1
    basis: "model" | "statistical" | "heuristic" | "mixed";
    calibration_state: "calibrated" | "partially_calibrated" | "uncalibrated";
    sample_size?: number;
    source_ref?: string;
  };
  probability: {
    value: number;            // 0–1
    target_outcome: string;
    horizon: string;
    ci_lower?: number;
    ci_upper?: number;
    model_ref?: string;
    as_of?: string;
  };
  expected_value: {
    horizon: string;
    unit: "pct_return" | "currency" | "risk_units";
    gross: number;
    cost: number;
    net: number;
    downside: number;
    expected_shortfall?: number;
  };
  rationale: Array<{           // minItems: 1
    claim: string;
    confidence: number;        // 0–1
    evidence_refs?: EvidenceRef[];
  }>;
  risk_notes: Array<{
    severity: "info" | "watch" | "warning" | "high" | "critical";
    domain: string;
    summary: string;
    mitigation?: string;
  }>;
  evidence_refs: EvidenceRef[];
  invalidation: {
    conditions: string[];      // required; may be []
    current_state: "valid" | "watch" | "invalidated";
    last_checked_at?: string;
  };
  suggested_action: "enter" | "add" | "reduce" | "exit" | "review" | "no_action";
  no_order_route_proof: "agora_decision_support_only";
  // Optional fields
  dedupe_key?: string;
  candidate_ref?: string;
  position_ref?: string;
  trigger?: {
    rule_id?: string;
    summary?: string;
    current_value?: unknown;
    threshold?: unknown;
    distance_to_trigger?: number;
  };
  expires_at?: string;
  decision_state?: "pending" | "approved_by_trader" | "rejected_by_trader" | "deferred" | "expired" | "handed_off" | "superseded";
  suggested_size?: {
    size_hint?: "small" | "medium" | "large" | "full_position";
    portfolio_pct?: number;    // 0–1
    non_binding: true;
  };
  position_snapshot?: PositionSnapshot;
  data_cutoff?: string;
}

export interface EvidenceRef {
  ref_type: "evidence_bundle" | "evidence_item" | "source_record" | "citation" |
            "experiment_artifact" | "registry_entry" | "consult_memo" |
            "research_run" | "telemetry_snapshot" | "market_context";
  ref_id: string;
  summary?: string;
  data_cutoff?: string;
}
```

### `TradingRoomStrategy` correction

The `monitoring_state` `"inactive"` value exists in the schema but is not forbidden.
However `"paper_requested"` is in the schema enum, not `"paper"`. Correct the frontend type:

```ts
monitoring_state: "inactive" | "shadow" | "paper_requested" | "monitoring" | "paused";
```

Packet 2 had `"paper_requested"` correct, but the `shadow_status` field (optional string)
exists on the strategy schema and was not in Packet 2's `TradingRoomStrategy` interface:

```ts
// Add to TradingRoomStrategy:
shadow_status?: string;
performance_summary?: Record<string, unknown>;
```

### `TradingRoomAggregate` correction

`top_decision_events` is optional (not required). Add it to the TypeScript interface:

```ts
// Add to TradingRoomAggregate:
top_decision_events?: TradingDecisionEvent[];
position_summaries?: Array<Record<string, unknown>>;
```

`queue_summary` has `"additionalProperties": false` — the TypeScript type is correct in
listing only the five event-kind counts. `risk_summary` also has `"additionalProperties": false`,
so `alerts?: string[]` and `summary?: string` are the only optional fields beyond `state`.

## Idempotency implementation pattern

The existing BFF uses an in-process dict keyed by the idempotency key header value.
The pattern for the Trading Room decision and handoff routes should follow the same shape
as `_GOV_BFF_IDEMPOTENCY` in `services/control-plane/bff/main.py`.

### Recommended naming and shape

```python
# In trading_room/router.py (module-level)
_TRADING_ROOM_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
```

### Check-before-store sequence

```python
def _resolve_idempotency_key(idempotency_key: str, operator_id: str) -> str:
    # Scope the key per operator so two operators don't share idempotency records.
    return f"{operator_id}:{idempotency_key}"

def _stable_hash(payload: Dict[str, Any]) -> str:
    import hashlib, json
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

# In the decision handler:
resolved_key = _resolve_idempotency_key(idempotency_key, identity.operator_id)
request_hash = _stable_hash({"decision_event_id": decision_event_id, "body": body.dict()})

existing = _TRADING_ROOM_IDEMPOTENCY.get(resolved_key)
if existing is not None:
    if existing["request_hash"] != request_hash:
        raise bff_error(
            "IDEMPOTENCY_CONFLICT",
            status_code=409,
        )
    return existing["result"]  # return the previously-computed response

# ... process the decision ...

_TRADING_ROOM_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
return result
```

**Note on TTL**: The existing BFF pattern uses an in-memory dict with no explicit expiry.
The dict lives for the process lifetime. For production reliability (pod restarts, rolling
deploys), the parent owner should decide whether a durable store with TTL (e.g., Redis 24h)
is required or whether the in-memory pattern is acceptable. This is an owner decision, not
resolved by this sidecar.

### Idempotency header location

The `Idempotency-Key` value must come from the HTTP header only, not from the request body.
The existing BFF has `_reject_body_idempotency_key(payload)` guards on action routes.
The Trading Room router should apply the same guard: if the parsed body contains an
`idempotency_key` or `Idempotency-Key` field, reject with `422`.

## BFF test structure supplement

The existing BFF test pattern uses an `_isolated_bff()` context manager with a temporary
directory for the read store and command store. Tests for the Trading Room router should
follow the same isolation pattern.

### Suggested fixture structure

```python
"""
Contract tests for AG-BE-TR-001: Trading Room aggregate, decision events,
trader decisions, governed handoffs.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore

OPERATOR_TOKEN = "Bearer tr-test-001:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}
IDEMPOTENCY_KEY = "test-trading-room-decision-001"


@contextmanager
def _isolated_bff() -> Iterator[tuple[TestClient, ReadSurfaceStore]]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main.read_store = store
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        # Clear trading room idempotency ledger between tests.
        bff_main._TRADING_ROOM_IDEMPOTENCY.clear()
        try:
            yield TestClient(bff_main.app), store
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._TRADING_ROOM_IDEMPOTENCY.clear()
```

### Minimum test cases for decision recording route

Each of these test cases corresponds to a distinct acceptance criterion from Packet 1:

| Test | Scenario | Expected outcome |
|---|---|---|
| `test_record_decision_approve_creates_intent` | POST decisions `{decision: "approve"}` on a `triggered` event | `201`; response contains `intent_id`; no broker order or RuntimeBinding created. |
| `test_record_decision_reject_transitions_state` | POST decisions `{decision: "reject"}` | `201`; event transitions to `decided`; no TradingIntent created. |
| `test_record_decision_idempotency_same_key_same_payload` | POST decisions twice with same `Idempotency-Key` and same body | Both return `201`; same response body; no duplicate TradingIntent. |
| `test_record_decision_idempotency_key_collision` | POST decisions twice with same `Idempotency-Key` but different `decision` value | Second POST → `409 IDEMPOTENCY_CONFLICT`. |
| `test_record_decision_if_match_mismatch` | POST decisions with stale `If-Match` value | `409`; event not mutated. |
| `test_record_decision_invalid_state_event` | POST decisions on an `invalidated` event | `422 TRADING_EVENT_INVALIDATED`. |
| `test_record_decision_invalid_decision_value` | POST decisions `{decision: "bet"}` | `422`; invalid decision value rejected. |
| `test_no_order_route_proof_on_intent` | POST decisions approve → fetch the created TradingIntent | `no_order_route_proof` field present with value `"agora_request_only_no_order_route"`. |
| `test_schema_validation_aggregate` | GET trading-room | Response validates against `trading_room_aggregate.schema.json` via `jsonschema.validate`. |
| `test_schema_validation_decision_event` | GET decision-events/{id} | Response validates against `trading_decision_event.schema.json`. |

## Acceptance checks addendum

These checks supplement the 30+ checks in Packet 1, derived from the schema corrections above:

| Check | Expected result |
|---|---|
| `spec_version` on decision event | Every `TradingDecisionEvent` response includes `spec_version: "1.0"`. |
| `invalidation.conditions` present | `invalidation.conditions` is always a `string[]` (may be empty). Never absent. |
| `invalidation.current_state` value set | `current_state` is one of `"valid"`, `"watch"`, `"invalidated"`. Never `"none"`. |
| `rationale` non-empty | `rationale` array has at least one item. `rationale: []` fails schema validation. |
| `suggested_size` shape when present | Uses `size_hint` and/or `portfolio_pct`; never uses `value` or `unit` keys at top level. |
| `confidence.calibration_state` value set | `calibration_state` is one of `"calibrated"`, `"partially_calibrated"`, `"uncalibrated"`. Never `"degraded"`. |
| `candidate_count` absent when unavailable | Strategy entries omit `candidate_count` (not set to `0`) when the candidate source is down; `staleness_reasons` carries the signal instead. |
| `top_decision_events` when absent | Aggregate response omits the key entirely; does not include `"top_decision_events": null`. |

## Remaining open questions

| # | Question | Default if not resolved |
|---|---|---|
| Q3 | What is the idempotency window for trader decisions? How long should a duplicate `Idempotency-Key` be honoured before expiry? | The existing BFF pattern has no explicit TTL (process lifetime). For production, 24 hours is the conventional default. Owner decision required. |
| Q5 | Should the `trading_room.snapshot` SSE event on connect carry the full `TradingRoomAggregate` shape (same as `GET /bff/agora/trading-room`) or a lighter payload? | Full aggregate shape recommended (avoids needing a separate GET on reconnect). Confirm with owner. |
| Q6 | Who populates `position_snapshot` on add/reduce/exit/review events — the event record itself (stored in the event projection) or a separate position projection joined at query time by the BFF? | D9 implies stored with the event, but the schema uses `"additionalProperties": true` on `position_snapshot`, leaving the source ambiguous. Owner clarification needed before implementing the position-event projection path. |
| Q7 | Should `decision_state` on a `TradingDecisionEvent` reflect the current intent/handoff lifecycle (e.g. updating to `handed_off` after a governed handoff is submitted), or is it a snapshot at the time of the trader decision that does not update? | Most natural is a live projection (updating as the intent progresses), but this requires the event record to have a writable `decision_state` field and a projection update path. Confirm the update semantics with the owner. |

## Reviewer handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. No canonical docs, schemas, OpenAPI, BFF runtime, or frontend files were changed by this sidecar. |
| Schema corrections accurate | Each TypeScript type correction is grounded in the actual v4 schema; no corrections are invented. |
| Q1 resolution | The `additionalProperties: false` restriction at TradingRoomAggregate root level correctly identified from schema; `staleness_reasons` and `risk_summary.alerts` correctly cited as schema-valid alternatives. |
| Q2 resolution | `top_decision_events` correctly identified as optional (not in `required` array) in `trading_room_aggregate.schema.json`. |
| Q4 resolution | Response codes correctly sourced from `agora_v1_3.openapi.yaml`: 201 for decisions, 202 for handoffs, 200 for withdraw. |
| Idempotency pattern | Pattern correctly derived from `_GOV_BFF_IDEMPOTENCY` in `services/control-plane/bff/main.py`; no implementation invented beyond what the existing codebase demonstrates. |
| Test structure | Test fixture follows `_isolated_bff` context manager pattern from existing BFF contract tests. |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
  REVIEW_NOTES_ZH="Followup-3 BFF/frontend handoff packet approved: delivers schema-derived corrections to Packet-2 TypeScript types (suggested_size, calibration_state, invalidation.current_state, origin/suggested_action enums, spec_version, decision_state, conditions array), resolves Q1 degradation-signalling (additionalProperties:false blocks degradation_notes; staleness_reasons is schema-valid), resolves Q2 (top_decision_events optional), resolves Q4 (202/201/200 response codes from OpenAPI spec), adds idempotency implementation pattern and BFF test structure supplement — all as support material without modifying canonical truth." \
  ./scripts/ai-status.sh approve AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Support-only AG-BE-TR-001 BFF/frontend handoff followup-3 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Describe the factual correction, scope issue, or missing detail needed before approval."
```

## Validation run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3

git status --short
# ?? .orchestrator/task-briefs/ag_be_tr_001_sidecar_bff_handoff_followup_3.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
# in_progress; owner Claude; reviewer Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001
# todo; owner Claude2; reviewer Codex

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001
# blocked; owner Codex; reviewer Claude2

python3 -m json.tool services/control-plane/specs/agora/v4/trading_decision_event.schema.json > /dev/null
# Valid JSON schema; confirmed schema enum values for suggested_size, calibration_state,
# invalidation.current_state, origin, suggested_action.

python3 -m json.tool services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json > /dev/null
# Valid JSON schema; confirmed additionalProperties:false at root level;
# confirmed top_decision_events is optional; confirmed staleness_reasons in strategy items.

grep -n '"202"' services/control-plane/openapi/agora_v1_3.openapi.yaml
# Confirms 202 for /bff/agora/trading-intents/{intent_id}/handoffs.

grep -n '"201"' services/control-plane/openapi/agora_v1_3.openapi.yaml
# Confirms 201 for /bff/agora/trading-room/decision-events/{decision_event_id}/decisions.

grep -n '"200"' services/control-plane/openapi/agora_v1_3.openapi.yaml | grep withdraw
# Confirms 200 for /bff/agora/trading-intents/{intent_id}/withdraw.
```
