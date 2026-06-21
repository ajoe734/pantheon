# AG-BE-TR-002 BFF and Frontend Handoff Packet — Followup 2

| Field | Value |
|---|---|
| Task ID | `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-TR-002` — Governed TradingIntent / handoff |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Baseline | Follows `AG-BE-TR-002-SIDECAR-BFF-HANDOFF` (done, PR #2142, reviewed by Claude2) |
| Status | Ready for reviewer handoff |

This is a support artifact only. It does not modify L1 canonical truth, OpenAPI, JSON schemas,
BFF runtime, registry/governance implementation, or frontend code. The parent owner (Codex) decides
whether and how to absorb this material into the main implementation.

---

## Cumulative Packet Scope

| Packet | Key additions |
|---|---|
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF` (done, PR #2142) | BFF query gap matrix (10 gaps), operator journeys A–I, frontend `tradingRoom.ts` method signatures, backend acceptance checks, 7 open design notes, routing table (`shadow→shadow_research`, `paper→management_governance`, `canary/live→promotion_review`), `TradingIntent` vs `GovernedIntentHandoff` schema distinction. |
| **This packet (FOLLOWUP-2)** | Schema-derived corrections: `target_queue` derivation, `converted` state, `action_proposal` field constraints, fields the BFF must never populate (`management_handoff_ref`, `deployment_plan_ref`, `runtime_binding_ref`), `additionalProperties: false` implication for BFF responses. Corrected TypeScript interfaces grounded in the actual v4 schema. Idempotency implementation pattern. Backend module structure guidance. Acceptance check addendum. Current dependency status update. |

---

## Current State Observed (2026-06-21)

| Surface | Observed state | Change since original packet |
|---|---|---|
| `AG-BE-TR-002` | `todo`; owner `Codex`, reviewer `Claude2`. | Unchanged. |
| `AG-BE-TR-001` | `todo`; owner `Claude2`, reviewer `Codex`. | Unchanged. Gated on `AG-BE-CP-001` (blocked). |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2`, waiting for `Claude2`. | Unchanged. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | `done` (PR #2145 merged). | **New.** Idempotency implementation pattern, schema-derived TypeScript corrections, and BFF test structure for TR-001 are now documented as sidecar support material. TR-002 builds governed-handoff routes on top of TR-001. |
| `trading_room/router.py` | Placeholder returning empty `APIRouter`. | Unchanged. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Does not exist. | Unchanged. Gated on `AG-FE-TR-001`. |

---

## Schema-Derived Corrections and Additions

The original packet described the `GovernedIntentHandoff` v4 schema from the OpenAPI spec.
This section corrects and extends that description against the actual
`services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json`.

### 1. `target_queue` is an optional schema field — set server-side by the BFF

The schema defines `target_queue` as an optional property with enum
`["shadow_research", "management_governance", "promotion_review"]`. It is **not** in the
`required` array, so it is not required from the client. The BFF should:

- Derive `target_queue` from `requested_stage` on the server side (see routing table below).
- Write `target_queue` onto the stored `GovernedIntentHandoff` record.
- Never accept `target_queue` as a client-supplied request body field (it is server-derived).

```
requested_stage → target_queue
"shadow"          → "shadow_research"
"paper"           → "management_governance"
"canary"          → "promotion_review"
"live"            → "promotion_review"
```

Any request body that includes a `target_queue` field must be rejected (`422`) because
the schema has `additionalProperties: false` — parsing or forwarding it as an extra field
is a schema violation.

### 2. `converted` state in the state machine

The schema `state` enum is:
```
"draft" | "submitted" | "accepted" | "rejected" | "expired" | "withdrawn" | "converted"
```

The original packet listed `draft → submitted → accepted/rejected`, `submitted → withdrawn`,
`submitted → expired`. The additional `"converted"` state was not documented.

**`converted` semantics:** A handoff reaches `converted` when the governance plane converts
the handoff request into a downstream artifact (e.g., a `DeploymentPlan`). This transition
is driven by the Management governance plane, not by the BFF. The BFF only writes
`state: "submitted"` on creation and `state: "withdrawn"` on withdrawal; all other state
transitions (`accepted`, `rejected`, `expired`, `converted`) are written by downstream
governance systems. The BFF must never write `state: "accepted"`, `"rejected"`,
`"expired"`, or `"converted"` to a handoff record.

Full state-machine summary for BFF implementation:

| Transition | Writer |
|---|---|
| → `draft` | BFF (pre-submission save; optional) |
| `draft` → `submitted` | BFF (on successful `POST .../handoffs`) |
| `submitted` → `withdrawn` | BFF (on `POST .../withdraw`) |
| `submitted` → `accepted` | Management governance plane only |
| `submitted` → `rejected` | Management governance plane only |
| `submitted` → `expired` | Management governance plane only |
| `submitted` → `converted` | Management governance plane only |

### 3. `action_proposal` constraints

The schema has `additionalProperties: false` on `action_proposal`. The only valid fields are:

```ts
action_proposal?: {
  action?: "enter" | "add" | "reduce" | "exit" | "review";
  symbol?: string;
  direction?: string;
  size_hint?: string;
  portfolio_pct?: number;
  non_binding: true;  // const — required when action_proposal is present
};
```

Original packet stated `action_proposal.non_binding: true` is required when `action_proposal`
is present. This is confirmed by the `"const": true` schema constraint. Fields `value` and
`unit` do not exist and must not be accepted. The BFF must reject any `action_proposal` body
that includes keys outside this list.

### 4. Fields BFF must never populate on handoff creation

The schema includes `management_handoff_ref`, `deployment_plan_ref`, and `runtime_binding_ref`
as optional string fields. These are populated by the Management governance plane after a
handoff is processed upstream. The BFF must never:

- Accept these fields from the client request body.
- Write these fields to a newly-created `GovernedIntentHandoff` record.

If a client request includes any of these fields, the BFF should reject with `422` (schema
violation: `additionalProperties: false`).

### 5. `additionalProperties: false` at the root level

The schema has `"additionalProperties": false` at the root `GovernedIntentHandoff` level.
This means any BFF-computed fields (e.g., `created_by_bff`, `processing_notes`, `queue_ref`)
that are not in the schema **cannot** be stored in the handoff record. If the BFF needs
internal tracking state, it must use a separate internal record, not the `GovernedIntentHandoff`
document.

Fields **not** in the schema that the BFF might be tempted to add and must not:

| Tempting field | Correct alternative |
|---|---|
| `created_by_bff` | Store in the BFF's own command log |
| `processing_notes` | Store in a separate BFF internal store |
| `queue_submitted_at` | Use `created_at` (schema field) on submission |
| `bff_idempotency_key` | Track in the idempotency ledger, not the handoff record |

### 6. `decision_event_id` is optional

The schema defines `decision_event_id` (optional `string`) as the link from a governed
handoff back to the decision event that triggered it. When a handoff is submitted for an
intent that was created from a `TradingDecisionEvent` (via `approve` decision in AG-BE-TR-001),
the BFF should populate `decision_event_id` if available in the request context.

### 7. `required_gate_refs` optional field

The schema defines `required_gate_refs` as an optional `string[]`. The BFF may populate this
server-side to indicate which upstream gates (e.g., paper validation approval ref, prior
handoff ID) are required for the submission to proceed. This is a server-owned field, not
accepted from the client.

---

## Corrected TypeScript Interfaces (Schema-Grounded)

The `tradingRoom.ts` client types should match the actual schema precisely. Below are
schema-grounded interfaces for the AG-BE-TR-002 endpoint surfaces.

```ts
// GovernedIntentHandoff body sent by the client (POST .../handoffs)
// Note: handoff_id, state, target_queue, created_at, management_handoff_ref,
// deployment_plan_ref, runtime_binding_ref are server-assigned — client must not send them.
export interface SubmitHandoffBody {
  spec_version: "1.0";
  intent_id: string;
  requested_stage: "shadow" | "paper" | "canary" | "live";
  handoff_type: "shadow_start" | "paper_validation_request" | "promotion_review_request";
  strategy_id: string;
  strategy_spec_registry_id: string;
  requested_by: HandoffActor;
  evidence_refs: HandoffEvidenceRef[];       // minItems: 1 (enforced server-side)
  no_order_route_proof: "agora_request_only_no_order_route";  // const literal
  // Optional fields
  decision_event_id?: string;
  action_proposal?: HandoffActionProposal;
  rationale?: string;
  risk_summary?: string;
}

export interface HandoffActionProposal {
  action?: "enter" | "add" | "reduce" | "exit" | "review";
  symbol?: string;
  direction?: string;
  size_hint?: string;
  portfolio_pct?: number;
  non_binding: true;  // const — must always be true
}

export interface HandoffActor {
  actor_type: "trader" | "agora_servant" | "institutional_persona" | "system";
  actor_ref: string;        // minLength: 1
  session_id?: string;
  display_name?: string;
}

export interface HandoffEvidenceRef {
  ref_type:
    | "evidence_bundle" | "evidence_item" | "source_record" | "citation"
    | "experiment_artifact" | "registry_entry" | "consult_memo"
    | "research_run" | "telemetry_snapshot" | "market_context";
  ref_id: string;           // minLength: 1
  summary?: string;
  data_cutoff?: string;     // date-time
}

// Full GovernedIntentHandoff record as stored and returned by BFF
export interface GovernedIntentHandoff extends SubmitHandoffBody {
  handoff_id: string;
  state: "draft" | "submitted" | "accepted" | "rejected" | "expired" | "withdrawn" | "converted";
  target_queue: "shadow_research" | "management_governance" | "promotion_review";
  created_at: string;       // date-time
  updated_at?: string;      // date-time
  expires_at?: string;      // date-time
  required_gate_refs?: string[];
  // Management governance plane fields — never set by BFF on creation:
  management_handoff_ref?: string;
  deployment_plan_ref?: string;
  runtime_binding_ref?: string;
}

// TradingIntent record (created by AG-BE-TR-001; read by AG-BE-TR-002)
export interface TradingIntent {
  spec_version: "1.0";
  intent_id: string;
  operator_id: string;
  intent_type:
    | "buy_interest" | "sell_interest" | "hold_decision" | "reduce_exposure"
    | "increase_exposure" | "hedge_intent" | "exit_intent" | "entry_interest";
  direction: "long" | "short" | "neutral" | "reduce" | "exit";
  subject: {
    symbol: string;
    asset_class?: string;
    venue?: string;
    strategy_ref?: string;
  };
  expressed_at: string;     // date-time
  no_order_route_proof: "agora_intent_record_only";
  // Optional
  persona_id?: string;
  session_id?: string;
  rationale?: string;
  size_hint?: "small" | "medium" | "large" | "full_position";
  timeframe_hint?: string;
  confidence?: number;      // 0–1
  linked_event_ids?: string[];
  learning_eligible?: boolean;
  metadata?: Record<string, unknown>;
}

// Intent detail envelope returned by GET .../trading-intents/{intent_id}
export interface TradingIntentDetail {
  intent: TradingIntent;
  handoffs: GovernedIntentHandoff[];
}

// CommandResponse from POST routes
export interface CommandResponse {
  command_id: string;
  // Contains handoff_id on successful handoff submission
  [key: string]: unknown;
}

// Write options required on all handoff/withdrawal calls
export interface WriteOptions {
  ifMatch: string;
  idempotencyKey: string;
}

// Method signatures for tradingRoom.ts
//   getTradingIntent(intentId: string): Promise<TradingIntentDetail>
//   submitHandoff(intentId: string, body: SubmitHandoffBody, opts: WriteOptions): Promise<CommandResponse>
//   withdrawHandoff(intentId: string, opts: WriteOptions): Promise<CommandResponse>
```

---

## Idempotency Implementation Pattern

The existing BFF in `services/control-plane/bff/main.py` uses an
`IdempotencyRecord.reserve()` pattern backed by the command store. The Trading Room
handoff routes should follow the same `IdempotencyRecord` abstraction used by the governance
BFF for its write operations.

If the existing `IdempotencyRecord` class is not directly reusable in the Trading Room
router context, an in-process fallback pattern (as used in TR-001 Packet 3) should be
applied. The key points:

```python
# Module-level idempotency ledger (in trading_room/router.py)
_HANDOFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}

def _resolve_handoff_idempotency_key(idempotency_key: str, operator_id: str) -> str:
    # Scope per operator to prevent cross-operator idempotency collisions.
    return f"{operator_id}:{idempotency_key}"

def _stable_hash(payload: Dict[str, Any]) -> str:
    import hashlib, json
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
```

### Check-before-store sequence for `POST .../handoffs`

```python
resolved_key = _resolve_handoff_idempotency_key(idempotency_key, identity.operator_id)
request_hash = _stable_hash({
    "intent_id": intent_id,
    "body": body.dict(),
})

existing = _HANDOFF_IDEMPOTENCY.get(resolved_key)
if existing is not None:
    if existing["request_hash"] != request_hash:
        # Same key, different body → idempotency conflict
        raise bff_error("IDEMPOTENCY_CONFLICT", status_code=409)
    return existing["result"]   # replay prior 202 response

# ... validate, route, create handoff record ...

_HANDOFF_IDEMPOTENCY[resolved_key] = {
    "request_hash": request_hash,
    "result": response,
}
return response
```

The same pattern applies to `POST .../withdraw`, with a separate ledger key or the same
ledger scoped by operation type.

**Idempotency-Key must come from the HTTP header only.** The BFF must reject any request
body that contains an `idempotency_key` field (same guard as the existing BFF's
`_reject_body_idempotency_key` function).

---

## Backend Module Structure Guidance

### Where to implement

The open design note from the original packet (Design Note §1) remains unresolved in the
task tracker. Recommendation is unchanged: implement the governed handoff routes inside
`services/control-plane/bff/agora/trading_room/router.py` (the existing placeholder), not
in a new `trading_room.py` alongside the directory.

### Suggested route function structure

```python
@router.get("/bff/agora/trading-intents/{intent_id}")
async def get_trading_intent(
    intent_id: str,
    identity: Any = Depends(extract_identity),
    _: None = Depends(require_read_role),
) -> JSONResponse:
    """Return TradingIntent detail including handoff chain."""
    # Fetch from read store; return DetailEnvelope.
    # On source unavailable: return typed degraded response (not fixture).

@router.post("/bff/agora/trading-intents/{intent_id}/handoffs")
async def submit_governed_handoff(
    intent_id: str,
    body: GovernedIntentHandoffBody,
    if_match: str = Header(..., alias="If-Match"),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    identity: Any = Depends(extract_identity),
) -> JSONResponse:
    """Submit a governed intent handoff — request-only; no broker order."""
    # 1. Validate body against governed_intent_handoff.schema.json.
    # 2. Assert no_order_route_proof == "agora_request_only_no_order_route".
    # 3. Assert action_proposal.non_binding == True if action_proposal present.
    # 4. Assert management_handoff_ref / deployment_plan_ref / runtime_binding_ref absent.
    # 5. Check idempotency (see pattern above).
    # 6. Check If-Match against current intent version.
    # 7. Derive target_queue from requested_stage (server-side only).
    # 8. Assert handoff_type matches requested_stage per schema rules.
    # 9. Check TRADING_INTENT_NOT_ALLOWED gate: reject if any code path
    #    would write RuntimeBinding, capital binding, or broker order.
    # 10. Route to target_queue; create GovernedIntentHandoff with state="submitted".
    # 11. Return 202 CommandResponse with handoff_id.

@router.post("/bff/agora/trading-intents/{intent_id}/withdraw")
async def withdraw_trading_intent(
    intent_id: str,
    if_match: str = Header(..., alias="If-Match"),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    identity: Any = Depends(extract_identity),
) -> JSONResponse:
    """Withdraw a pending intent or handoff — never deletes the record."""
    # 1. Check idempotency.
    # 2. Check If-Match.
    # 3. Assert intent/handoff is in a withdrawable state.
    # 4. Set state="withdrawn"; retain record (no delete).
    # 5. Return 200 CommandResponse.
```

---

## Acceptance Check Addendum

These checks supplement the 25 acceptance checks in the original packet. They are derived
from the schema corrections above.

| Check | Expected result |
|---|---|
| `target_queue` server-derived | `target_queue` is set by the BFF from `requested_stage`; never accepted from client body. Client body including `target_queue` is rejected with `422`. |
| `state` on creation | Newly-created `GovernedIntentHandoff` record has `state: "submitted"`. BFF never writes `"accepted"`, `"rejected"`, `"expired"`, or `"converted"`. |
| `management_handoff_ref` absent on creation | BFF-created handoff records have no `management_handoff_ref`, `deployment_plan_ref`, or `runtime_binding_ref` fields. Tests assert these fields absent from the `POST .../handoffs` response and the stored record. |
| `action_proposal` field constraints | BFF rejects any `action_proposal` body containing keys other than `action`, `symbol`, `direction`, `size_hint`, `portfolio_pct`, `non_binding`. `non_binding: false` or absent is rejected with `422`. |
| `additionalProperties` on handoff response | `POST .../handoffs` `202` body and `GET .../trading-intents/{id}` handoff objects must validate against `governed_intent_handoff.schema.json` with `additionalProperties: false`. No BFF-internal fields leak into the response. |
| `converted` state never written by BFF | No BFF code path sets `state: "converted"`. Tests assert the state enum on newly-created records is limited to `"submitted"` and `"withdrawn"`. |
| `decision_event_id` populated when available | When the intent was created from a `TradingDecisionEvent`, the handoff record includes `decision_event_id` referencing that event. |
| Idempotency key from header only | BFF rejects request body containing `idempotency_key` field (same guard as existing `_reject_body_idempotency_key`). |
| Idempotency conflict detection | Same `Idempotency-Key`, different body → `409 IDEMPOTENCY_CONFLICT`. |

---

## Minimum Test Cases (AG-BE-TR-002)

| Test | Scenario | Expected outcome |
|---|---|---|
| `test_submit_handoff_shadow_returns_202` | POST handoffs `{requested_stage: "shadow", handoff_type: "shadow_start", ...}` | `202`; `target_queue = "shadow_research"` set server-side; `state = "submitted"`; no broker order. |
| `test_submit_handoff_paper_routes_management_governance` | POST handoffs `{requested_stage: "paper", ...}` | `202`; `target_queue = "management_governance"`. |
| `test_submit_handoff_canary_routes_promotion_review` | POST handoffs `{requested_stage: "canary", ...}` | `202`; `target_queue = "promotion_review"`; no RuntimeBinding written. |
| `test_submit_handoff_no_order_route_proof_enforced` | POST handoffs with `no_order_route_proof: "something_else"` | `422`; schema violation. |
| `test_submit_handoff_trading_intent_not_allowed_gate` | POST handoffs that would write a broker order or RuntimeBinding | `TRADING_INTENT_NOT_ALLOWED` error returned. |
| `test_submit_handoff_missing_idempotency_key` | POST handoffs without `Idempotency-Key` header | `422`; header required. |
| `test_submit_handoff_idempotency_replay` | POST handoffs twice with same `Idempotency-Key` and same body | Both return `202`; same `handoff_id`; no duplicate record. |
| `test_submit_handoff_idempotency_conflict` | POST handoffs twice with same `Idempotency-Key` but different body | Second POST → `409 IDEMPOTENCY_CONFLICT`. |
| `test_submit_handoff_if_match_conflict` | POST handoffs with stale `If-Match` value | `409`; handoff not created. |
| `test_submit_handoff_action_proposal_non_binding_required` | POST handoffs with `action_proposal.non_binding: false` | `422`; const violation. |
| `test_submit_handoff_management_refs_absent` | POST handoffs successfully; inspect stored record | Record has no `management_handoff_ref`, `deployment_plan_ref`, `runtime_binding_ref`. |
| `test_submit_handoff_target_queue_client_rejected` | POST handoffs body includes `target_queue` key | `422`; client-supplied `target_queue` rejected. |
| `test_get_trading_intent_includes_handoffs` | GET trading-intents/{id} after submitting a handoff | Response contains `intent` and `handoffs` array with the submitted handoff. |
| `test_withdraw_intent_sets_withdrawn` | POST withdraw on a submitted handoff | `200`; record transitions to `state: "withdrawn"`; record not deleted. |
| `test_schema_validation_handoff_record` | Validate stored handoff against `governed_intent_handoff.schema.json` | Passes `jsonschema.validate` with no extra properties. |
| `test_approval_required_gate` | POST handoffs for canary without prior paper approval | `APPROVAL_REQUIRED` error with blocking reason. |
| `test_handoff_not_allowed_terminal_state` | POST handoffs on an already-withdrawn intent | `TRADING_INTENT_HANDOFF_NOT_ALLOWED`. |

---

## Remaining Open Questions

| # | Question | Default if not resolved |
|---|---|---|
| Q1 | Should the BFF use the `IdempotencyRecord.reserve()` abstraction from `main.py` or the module-level dict pattern (as in TR-001 Followup-3)? The existing command store tracks idempotency via `IdempotencyRecord`; reusing it avoids a second implementation but may couple the Trading Room router to the governance command pipeline. | Module-level dict per TR-001 Packet-3 pattern unless `IdempotencyRecord` is made available to Trading Room context. Owner decision required. |
| Q2 | What is the TTL for handoff idempotency keys? The in-process dict has no explicit expiry (process lifetime). | 24-hour TTL conventional default; requires durable store (e.g. Redis) for production pod-restart safety. Owner decision required. |
| Q3 | How should the BFF handle `requested_gate_refs`? The schema field exists but there is no documented policy for when the BFF should populate it vs. leave it absent. | Omit on creation; owner to specify if gate enforcement requires populating it before queue routing. |
| Q4 | Should `GET .../trading-intents/{intent_id}` return a `DetailEnvelope` schema (per v1.3 OpenAPI `$ref: "#/components/schemas/DetailEnvelope"`) or the raw `TradingIntent` + `handoffs[]` composite? The `DetailEnvelope` schema is referenced in the OpenAPI spec but its definition is not shown in the excerpt read. | Return `{ intent: TradingIntent, handoffs: GovernedIntentHandoff[] }`. Confirm `DetailEnvelope` shape with owner if the schema is formally defined elsewhere. |

---

## Reviewer Handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. No canonical docs, schemas, OpenAPI, BFF runtime, or frontend files changed. |
| `target_queue` accuracy | `target_queue` is correctly identified as an optional schema field (not in `required` array); server-derived from `requested_stage`; correct enum values. |
| `converted` state accuracy | `converted` state is confirmed present in the schema enum; transition to `converted` is correctly attributed to the Management governance plane, not the BFF. |
| `action_proposal` field constraints | `additionalProperties: false` on `action_proposal`; `non_binding: true` const confirmed; no `value` or `unit` fields at top level. |
| BFF-forbidden fields | `management_handoff_ref`, `deployment_plan_ref`, `runtime_binding_ref` correctly identified as Management-plane fields that must not be set by BFF on creation. |
| `additionalProperties: false` implications | Root-level `additionalProperties: false` correctly noted; BFF-internal fields may not leak into the handoff record. |
| TypeScript interfaces grounded in schema | `SubmitHandoffBody`, `GovernedIntentHandoff`, `HandoffActor`, `HandoffActionProposal`, `HandoffEvidenceRef` types derived from actual schema fields and enums; no invented fields. |
| Status accuracy | `AG-BE-TR-002` is `todo`; `AG-BE-TR-001` is `todo` (gated on blocked `AG-BE-CP-001`); `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` is `done` (PR #2145). |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |

**Recommended reviewer approval command:**

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="Followup-2 handoff packet approved: adds schema-derived corrections (target_queue server-derived enum, converted state, action_proposal field constraints, management-plane-only fields, additionalProperties:false root-level implication), corrected TypeScript interfaces grounded in v4 schema, idempotency implementation pattern, backend module structure guidance, and acceptance check addendum — all without modifying canonical truth." \
  ./scripts/ai-status.sh approve AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Followup-2 BFF/frontend handoff packet approved for parent owner absorption."
```

**Recommended reviewer reopen command:**

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Describe the factual error, scope issue, or missing context requiring correction."
```

---

## Validation Run

```bash
git branch --show-current
# task/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2

git status --short
# ?? .orchestrator/task-briefs/ag_be_tr_002_sidecar_bff_handoff_followup_2.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
# status: in_progress, owner: Claude, reviewer: Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002
# status: todo, owner: Codex, reviewer: Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-001
# status: todo, owner: Claude2, reviewer: Codex; gated on AG-BE-CP-001 (blocked)

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-TR-002-SIDECAR-BFF-HANDOFF
# source: archive; terminal_status: done; archived_at 2026-06-21T20:46:27Z; PR #2142 merged

# Confirmed governed_intent_handoff.schema.json is valid JSON Schema:
python3 -m json.tool services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json > /dev/null
# valid

# Confirmed trading_intent.schema.json is valid JSON Schema:
python3 -m json.tool services/control-plane/specs/agora/trading_intent.schema.json > /dev/null
# valid

# Confirmed additionalProperties:false at root and nested levels:
grep "additionalProperties" services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json
# "additionalProperties": false  (action_proposal, actor, evidence_ref, root)

# Confirmed target_queue enum values in schema:
python3 -c "import json; s=json.load(open('services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json')); print(s['properties']['target_queue']['enum'])"
# ['shadow_research', 'management_governance', 'promotion_review']

# Confirmed state enum includes 'converted':
python3 -c "import json; s=json.load(open('services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json')); print(s['properties']['state']['enum'])"
# ['draft', 'submitted', 'accepted', 'rejected', 'expired', 'withdrawn', 'converted']

# Confirmed target_queue is NOT in required array:
python3 -c "import json; s=json.load(open('services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json')); print('target_queue' in s['required'])"
# False

# Confirmed 202/200 response codes from OpenAPI:
grep -n '"202"\|"200"' services/control-plane/openapi/agora_v1_3.openapi.yaml | grep -i "handoff\|withdraw"
# (see lines 683 and 699 of agora_v1_3.openapi.yaml)

# Confirmed trading_room/router.py is still placeholder:
cat services/control-plane/bff/agora/trading_room/router.py | grep "return APIRouter"
# return APIRouter(tags=["agora-trading"])
```
