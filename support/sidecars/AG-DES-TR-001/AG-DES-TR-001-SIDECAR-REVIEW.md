# AG-DES-TR-001 — Sidecar Review Packet

**Prepared by:** Claude2 (sidecar worker)  
**Date:** 2026-06-21  
**Task:** AG-DES-TR-001-SIDECAR-REVIEW  
**Parent task:** AG-DES-TR-001 — Trading Room aggregate/intent handoff  
**Reviewer:** Claude  
**Status:** Ready for reviewer handoff

---

## 1. What AG-DES-TR-001 Must Deliver

AG-DES-TR-001 is the design/contract task for the Trading Room aggregate and governed intent handoff. It must produce:

| Deliverable | Target path | Status |
|---|---|---|
| Prose contract (D1–D10) | `docs/04/…/design-closure-round2/04_trading_room_and_governed_intent.md` | **PRESENT** |
| `trading_room_aggregate.schema.json` | `services/control-plane/specs/agora/v4/` | **Not yet deployed** (source in design-closure-round2/schemas/) |
| `trading_decision_event.schema.json` | `services/control-plane/specs/agora/v4/` | **Not yet deployed** |
| `governed_intent_handoff.schema.json` | `services/control-plane/specs/agora/v4/` | **Not yet deployed** |
| Trading Room routes in v1.3 OpenAPI | `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Handled by AG-XR-OPENAPI-004 (out of scope for AG-DES-TR-001) |

The v4 schema deployment is the remaining execution step. The design source material is complete.

---

## 2. Design Quality Assessment

### 2.1 Prose Contract (`04_trading_room_and_governed_intent.md`)

| Section | Coverage | Notes |
|---|---|---|
| D1 Boundary | ✓ Pass | Clear statement of what TR may/may not do; no order routing; no capital binding ownership |
| D2 API routes | ✓ Pass | 9 routes covering aggregate, strategy detail, decision queue, decision command, stream, intent get/handoff/withdraw |
| D3 Aggregate | ✓ Pass | User scope, strategies, queue summary, positions, risk summary, snapshot timestamp, data cutoff |
| D4 Decision event semantics | ✓ Pass | All required fields present; confidence vs. probability correctly separated |
| D5 Event lifecycle | ✓ Pass | approaching→triggered→pending_review→decided; approaching/triggered→invalidated; pending_review→expired/superseded |
| D6 Trader decision | ✓ Pass | approve/reject/defer/modify; approve or modify creates TradingIntent, not an order |
| D7 Governed handoff | ✓ Pass | shadow/paper/canary/live semantics; UI wording specified; no direct execution path |
| D8 Candidate review | ✓ Pass | Six candidate decisions; no hard delete of rejected candidates; eligibility conditions stated |
| D9 Position events | ✓ Pass | add/reduce/exit/review projection includes position snapshot, thesis ref, delta |
| D10 Safety errors | ✓ Pass | 7 error codes covering staleness, invalidation, duplication, handoff guard, capability denial |

### 2.2 Schema: `trading_room_aggregate.schema.json`

**Strengths:**
- Required fields match D3 prose (user_scope_ref, strategies, queue_summary, risk_summary, snapshot_at, data_cutoff)
- Per-strategy inline object captures readiness_state, monitoring_state, pending_event_counts
- `top_decision_events` references `trading_decision_event.schema.json` via `$ref`
- `additionalProperties: false` enforced

**Gap — `readiness_state` missing `not_assessed`:**  
Prose section A5 defines five gate states: `not_assessed`, `blocked`, `conditional`, `ready`, `stale`.  
The schema's `readiness_state` enum only lists four: `["blocked", "conditional", "ready", "stale"]`.  
`not_assessed` is required to represent a strategy that has not yet entered any readiness gate evaluation.  
**This must be corrected before deployment.**

### 2.3 Schema: `trading_decision_event.schema.json`

**Strengths:**
- All required D4 fields present: event_kind (entry/add/reduce/exit/review), confidence, probability, expected_value (gross/cost/net/downside + unit + horizon), rationale array with per-claim confidence, risk_notes, evidence_refs, invalidation, suggested_action, no_order_route_proof
- `confidence` and `probability` are distinct objects — correctly enforcing the D4 semantic separation
- `no_order_route_proof` is `const: "agora_decision_support_only"` — locks the no-route invariant
- `suggested_size.non_binding` is `const: true` — enforces non-binding nature at schema level
- Event lifecycle states match D5: approaching/triggered/pending_review/decided/expired/invalidated/superseded
- `decision_state` is a separate field from event `state` — this is intentional; the two fields model different dimensions (event progression vs. trader response)

**Minor observation (non-blocking):**  
`GovernedIntentHandoff.action_proposal.direction` is `type: string` (free-form), while the existing `trading_intent.schema.json` constrains it to `["long", "short", "neutral", "reduce", "exit"]`. The proposal object is a hint field inside the handoff aggregate, so loose typing is acceptable here. Reviewer may choose to tighten this in the final task if desired.

### 2.4 Schema: `governed_intent_handoff.schema.json`

**Strengths:**
- Required fields cover handoff_id, intent_id, requested_stage, handoff_type, state, strategy linkage, requested_by, evidence_refs, no_order_route_proof, created_at
- `requested_stage` aligns with D7 prose: shadow/paper/canary/live
- `handoff_type` correctly maps stages to semantics: shadow_start / paper_validation_request / promotion_review_request
- `no_order_route_proof: "agora_request_only_no_order_route"` is distinct from the TradingIntent v1 token (`"agora_intent_record_only"`) — both are present and correct for their respective schemas
- Governance references (`management_handoff_ref`, `deployment_plan_ref`, `runtime_binding_ref`) are optional — Agora never creates these; it only references them after they are created by Management
- Actor definition includes `agora_servant` and `institutional_persona` types for automated handoffs

---

## 3. OpenAPI Coverage (v1.3 Delta)

Trading Room routes in `08_openapi_v1_3_delta.yaml`:

| Route | Method | operationId |
|---|---|---|
| `/bff/agora/trading-room` | GET | getAgoraTradingRoom |
| `/bff/agora/trading-room/strategies/{strategy_id}` | GET | getAgoraTradingRoomStrategy |
| `/bff/agora/trading-room/decision-events` | GET | listAgoraTradingDecisionEvents (filterable by event_kind, state) |
| `/bff/agora/trading-room/decision-events/{id}` | GET | getAgoraTradingDecisionEvent |
| `/bff/agora/trading-room/decision-events/{id}/decisions` | POST | decideAgoraTradingEvent (approve/reject/defer/modify) |
| `/bff/agora/trading-room/stream` | GET | streamAgoraTradingRoom (SSE) |
| `/bff/agora/trading-intents/{intent_id}` | GET | getAgoraTradingIntent |
| `/bff/agora/trading-intents/{intent_id}/handoffs` | POST | submitAgoraTradingIntentHandoff |
| `/bff/agora/trading-intents/{intent_id}/withdraw` | POST | withdrawAgoraTradingIntent |

All routes defined in D2 prose are present. Decision command uses If-Match + Idempotency-Key for safe replay.

---

## 4. Downstream Unblock Matrix

| Downstream task | Unblocked by TR schemas merging |
|---|---|
| AG-BE-TR-001 | Yes — needs TR aggregate contract |
| AG-BE-TR-002 | Yes — needs governed intent handoff contract |
| AG-FE-TR-001 | Partial — also needs CARD contract (AG-DES-CARD-001) |
| AG-FE-TR-002 | Partial — also needs candidate-decision integration |
| AG-E2E-TR-001 | Partial — also needs E2E matrix (AG-DES-E2E-001) |

---

## 5. Required Action for AG-DES-TR-001 Execution

The parent task `AG-DES-TR-001` must:

1. Correct `readiness_state` in `trading_room_aggregate.schema.json` — add `not_assessed` to the enum.
2. Copy (or produce) the three schemas to `services/control-plane/specs/agora/v4/`:
   - `trading_room_aggregate.schema.json`
   - `trading_decision_event.schema.json`
   - `governed_intent_handoff.schema.json`
3. Verify each schema is valid JSON with correct `$id`, `$schema`, and `additionalProperties: false`.
4. Commit via `worker_commit.py` with the task branch, required trailers, and `Verified` line.
5. Do NOT create `agora_v1_3.openapi.yaml` or `bundle_index.v1_3.json` — those are owned by `AG-XR-OPENAPI-004`.

---

## 6. Sidecar Review Verdict

| Area | Verdict |
|---|---|
| Prose contract completeness | **PASS** — D1–D10 complete and internally consistent |
| Schema correctness | **CONDITIONAL PASS** — see readiness_state gap (§2.2) |
| API route coverage | **PASS** — all D2 routes present in v1.3 delta |
| No-order-route enforcement | **PASS** — both schemas carry const proof tokens |
| Downstream unblock conditions | **PASS** — TR unblocks AG-BE-TR-001 and AG-BE-TR-002 once merged |
| Ready for AG-DES-TR-001 execution? | **YES**, subject to the readiness_state correction |

**Reviewer recommendation:** Approve AG-DES-TR-001 to proceed once the executing worker adds `not_assessed` to `trading_room_aggregate.schema.json`. No other blocking issues identified. The governed handoff model correctly preserves the no-order-route invariant and the Management/DeploymentPlan boundary.

---

## 7. Evidence References

| Document | Path |
|---|---|
| Prose contract | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/04_trading_room_and_governed_intent.md` |
| OpenAPI v1.3 delta | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/08_openapi_v1_3_delta.yaml` |
| TR aggregate schema | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/trading_room_aggregate.schema.json` |
| TR decision event schema | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/trading_decision_event.schema.json` |
| Governed intent handoff schema | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/governed_intent_handoff.schema.json` |
| Round 2 dispatch matrix | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/07_dispatch_unblock_matrix.md` |
| Open gaps inventory | `docs/04/pantheon_agora_cross_repo_2026-06-20/OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` (section D) |
| Existing v4 schemas (VERS-001) | `services/control-plane/specs/agora/v4/` |
| Existing TradingIntent v1 schema | `services/control-plane/specs/agora/trading_intent.schema.json` |
