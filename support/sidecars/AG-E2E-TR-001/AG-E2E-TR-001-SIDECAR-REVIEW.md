# AG-E2E-TR-001 — Review Packet and Evidence Summary

**Sidecar kind:** review_packet
**Sidecar task:** AG-E2E-TR-001-SIDECAR-REVIEW
**Parent task:** AG-E2E-TR-001
**Parent owner:** Claude
**Parent reviewer:** Codex
**Prepared by:** Claude2 (sidecar owner)
**Date:** 2026-06-22
**Parent task status at packet time:** in_progress
**Authority docs:**
- `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/C4_dev_market_data_signal_wiring_plan.md`
- `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/04_trading_room_and_governed_intent.md`
- `services/control-plane/openapi/agora_v1_3.openapi.yaml`
- `services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json`
- `services/control-plane/specs/agora/v4/trading_decision_event.schema.json`
- `services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json`

---

## Purpose

This packet is a support artifact for AG-E2E-TR-001. It does **not** modify L1 canonical truth,
OpenAPI schemas, BFF runtime, or test files. It is intended for:

1. **Codex (parent reviewer):** a pre-review evidence map that shows what building blocks are
   merged and what the E2E test file must prove before the task can be approved.
2. **Claude (parent owner):** confirmation of which acceptance checks are already covered by
   existing tests and which gaps remain to be closed in `test_winner_branch_trading_room_e2e.py`.
3. **Downstream tasks** (any task blocked on AG-E2E-TR-001 being merged): reference for what
   the TR E2E gate will enforce.

---

## Parent Task Scope (AG-E2E-TR-001)

From the dispatch script and C4 design plan:

**Title:** Winner-branch strategy → full trading room E2E
**Scenario:** Winner Branch StrategyVersion → candidate pool → trading room dashboard → entry/exit
events → user decision → paper intent
**Target artifact:** `services/control-plane/tests/agora/test_winner_branch_trading_room_e2e.py`
**Acceptance criteria (from task record):**
- 賣家節點策略可走完交易作戰室全流程 (winner-branch strategy completes full trading room flow)
- 無直接下單 (no direct broker order)
- widget/score 對齊設計 (widgets and A2 score components align with design)
- E2E 綠燈收錄 CI (E2E green in CI)
- 實作與引用 spec/schema 逐欄位一致，無自創欄位/route/enum (implementation matches spec/schema field-for-field)
- 遇疑問須先開 blocker 澄清而非自行實作 (blockers required for ambiguities, no guessing)
- 自行臆測或偏離設計稿一律不通過 (deviation from design plan fails review)

---

## Dependency Status (all done as of 2026-06-22)

| Dependency | Status | Key Evidence |
|---|---|---|
| `AG-BE-TR-001` | `done` (archived 2026-06-22T03:22:30Z) | PR #2191; trading room aggregate/event queues; 24/24 unit tests + 18/18 router tests |
| `AG-BE-TR-002` | `done` (archived 2026-06-22T07:42:08Z) | PR #2220 + #2222; governed TradingIntent/handoff; 31/31 tests + 18/18 router |
| `AG-FE-TR-001` | `done` (archived per FE-TR-002 sidecar) | Trading Room page + `tradingRoom.ts` BFF client; contract-backed dashboard recipe loading |
| `AG-FE-TR-002` | `done` (PR #2270 merged 2026-06-22) | CandidateReviewDrawer + TradeDecisionCard; idempotency/If-Match headers wired |
| `AG-XR-OPENAPI-004` | `done` | v1.3 OpenAPI bundle frozen; Trading Room + governed intent routes live |
| `AG-DES-E2E-001` | `done` | 146 acceptance tests (winner-branch E2E + isolation matrix) merged |

---

## Merged Building Blocks

### 1. Backend — Trading Room BFF

**Location:** `services/control-plane/bff/agora/trading_room/`

| File | Purpose |
|---|---|
| `router.py` | v1.3 routes: GET/PUT aggregate, GET/POST decision-events, POST decisions, PUT submit-handoff, DELETE withdraw, SSE stream |
| `store.py` | In-memory TradingRoomStore and TradingIntentStore with upsert/get/filter/paginate |
| `test_trading_room.py` | 31 tests — unit + router smoke covering all event kinds, decision states, safety invariants |

**Verification:**
```
python3 -m pytest services/control-plane/bff/agora/trading_room/test_trading_room.py -v
# Result: 31 passed
```

### 2. Schemas — v4 Trading Room Contracts

| Schema | Key fields |
|---|---|
| `v4/trading_room_aggregate.schema.json` | `user_scope_ref`, `strategies[]`, `readiness_state`, `dashboard_recipe_id`, `queue_summary`, `risk_summary`, `data_cutoff` |
| `v4/trading_decision_event.schema.json` | `event_kind` (entry/add/reduce/exit/review), `confidence`, `probability`, `expected_value`, `rationale[]`, `risk_notes[]`, `evidence_refs[]`, `invalidation`, `no_order_route_proof: "agora_decision_support_only"` |
| `v4/governed_intent_handoff.schema.json` | `handoff_type`, `requested_stage` (shadow/paper/canary/live), `state: "submitted"`, `target_queue`, `action_proposal.non_binding: true`, `no_order_route_proof: "agora_request_only_no_order_route"` |
| `trading_intent.schema.json` | `no_order_route_proof: "agora_intent_record_only"` |

### 3. Winner Branch E2E — Steps 10–11 (already merged in AG-DES-E2E-001)

`services/control-plane/tests/agora/test_winner_branch_e2e_v13.py`

| Step | Class | Tests | Coverage |
|---|---|---|---|
| Step 10 | `TestStep10CandidatePoolAndTradingRoom` | 6 | trading room schema, gate-ready/blocked states, dashboard_recipe_id, candidate pool with scoring |
| Step 11 | `TestStep11DecisionEventAndGovernedIntent` | 17 | decision event schema, governed handoff (paper/canary/live), no_order_route_proof, shadow evaluation, all handoff states request-only |
| Full-flow invariant | `TestFullFlowSequenceInvariant` | 3 | SSE monotonic sequence, no broker order anywhere in flow, all handoffs request-only |

**Verification:**
```
python3 -m pytest services/control-plane/tests/agora/test_winner_branch_e2e_v13.py -v
# Result: 89 passed (full file including Steps 1–11)
```

### 4. OpenAPI Routes (v1.3)

`services/control-plane/openapi/agora_v1_3.openapi.yaml`

| Route | Method | Description |
|---|---|---|
| `/bff/agora/trading-room` | GET/PUT | Aggregate read/refresh |
| `/bff/agora/trading-room/strategies/{strategy_id}` | GET | Per-strategy state |
| `/bff/agora/trading-room/decision-events` | GET/POST | List/create decision events |
| `/bff/agora/trading-room/decision-events/{id}` | GET/PATCH | Read/update event |
| `/bff/agora/trading-room/decision-events/{id}/decisions` | POST | Record trader decision |
| `/bff/agora/trading-room/stream` | GET (SSE) | Live event stream |

---

## Gap Analysis: What AG-E2E-TR-001 Must Still Deliver

The target artifact `services/control-plane/tests/agora/test_winner_branch_trading_room_e2e.py`
does **not exist** as of this packet. The parent task (AG-E2E-TR-001) must create it.

### What the new test file must cover (per C4 and dispatch spec)

Based on the task summary (SD §24.3 step 9-11) and C4 design plan:

| Gap | Required coverage | Source authority |
|---|---|---|
| G1 | Winner-branch StrategyVersion selects into CandidatePool with A2 score components | `v5/candidate_score_result.schema.json`; v1.4 OpenAPI |
| G2 | Candidate added to Trading Room with `readiness_state: "ready"` and `dashboard_recipe_id` from registry | `v4/trading_room_aggregate.schema.json` |
| G3 | Dashboard recipe widgets are all from widget registry (no invented widget types) | `capability_manifest.json`; design-closure/A2 |
| G4 | Entry/exit decision event created with all required fields (confidence, probability, EV, rationale, risk_notes, evidence_refs, invalidation) | `v4/trading_decision_event.schema.json` |
| G5 | Trader approval creates TradingIntent record — not a broker order, RuntimeBinding, or capital binding | `trading_intent.schema.json`; no-order-route invariant |
| G6 | Paper governed handoff is `request_only` and state is `submitted` (not `executed`) | `v4/governed_intent_handoff.schema.json` |
| G7 | Canary/live handoffs remain `promotion_review_request` only | design-closure-round2/04 |
| G8 | A2 score in CandidateReviewDrawer uses component decomposition (raw_score, penalty_score, evidence_confidence, effective_score) | `v5/candidate_score_result.schema.json` |
| G9 | No broker order field (`broker_order_id`, `order_route`, `order_id`, `filled_qty`, `runtime_binding_id`, `capital_binding_id`) appears anywhere in the test flow | iron rule from `test_winner_branch_e2e_v13.py::test_no_broker_order_anywhere_in_flow` |
| G10 | E2E test is self-contained and deterministic (no network, fixed fixture, fixed checksum) | C4 Mode A: deterministic fixture replay |

### Already covered by existing tests (no duplication needed)

| Area | Source | Tests |
|---|---|---|
| Schema validity (trading_room_aggregate, trading_decision_event, governed_intent_handoff) | `test_winner_branch_e2e_v13.py` Steps 10-11 | 23 tests |
| Full flow no-broker-order invariant | `test_winner_branch_e2e_v13.py::TestFullFlowSequenceInvariant` | 3 tests |
| BFF router correctness, store upsert/get/filter, pagination | `test_trading_room.py` | 31 tests |
| Isolation (cross-user, cross-servant) | `test_agora_isolation_matrix.py`, `test_cross_user_isolation.py` | 63 tests |

The new E2E file must focus on **the integrated flow narrative** (steps 9→10→11 as a connected
sequence) rather than re-testing individual schemas or BFF stores.

---

## Iron Rule Confirmation

The following invariants must be explicitly asserted in `test_winner_branch_trading_room_e2e.py`:

1. **No broker order** — no `order_route`, `broker_order_id`, or equivalent in any object in the flow.
2. **No RuntimeBinding write** — no `runtime_binding_id` or `runtime_binding_ref` created by Agora.
3. **No capital binding** — no `capital_binding_id` or `capital_binding_ref` created by Agora.
4. **Handoff state = submitted** — governed intent handoff state must be `submitted`, not `executed` or `converted`.
5. **Action proposal non-binding** — `action_proposal.non_binding: true` on every handoff.
6. **no_order_route_proof constants** — decision event must carry `"agora_decision_support_only"`, handoff `"agora_request_only_no_order_route"`, intent `"agora_intent_record_only"`.

These are enforced in existing tests; the new E2E test must assert them at the **narrative flow level** (end-to-end, not just per-object).

---

## Reviewer Checklist (for Codex when AG-E2E-TR-001 goes to review)

When `test_winner_branch_trading_room_e2e.py` is submitted for review:

- [ ] The file exists at the canonical path `services/control-plane/tests/agora/test_winner_branch_trading_room_e2e.py`
- [ ] All tests pass: `python3 -m pytest services/control-plane/tests/agora/test_winner_branch_trading_room_e2e.py -v`
- [ ] No new schema fields, routes, or enum values were invented (verify against v1.3 OpenAPI and v4/v5 schemas)
- [ ] No `broker_order_id`, `order_route`, `runtime_binding_id`, `capital_binding_id`, or `capital_binding_ref` appear anywhere in test fixtures
- [ ] Governing `no_order_route_proof` constants are asserted explicitly (not just implicitly present in fixture)
- [ ] Paper governed handoff state is `submitted` (not `executed`)
- [ ] Canary/live handoffs produce only `promotion_review_request` type (not direct execution)
- [ ] A2 score component fields (`raw_score`, `penalty_score`, `evidence_confidence`, `effective_score`) are referenced from `v5/candidate_score_result.schema.json` — not invented
- [ ] Dashboard recipe `dashboard_recipe_id` is loaded from existing registry record — not hardcoded
- [ ] Test is self-contained and deterministic: no live network calls, fixed fixture checksums
- [ ] CI can run the test without additional environment setup (Mode A fixture replay)
- [ ] Test does not duplicate schema-level assertions already in Steps 10-11 of `test_winner_branch_e2e_v13.py`

### Quick verification commands

```bash
# Run new E2E test
python3 -m pytest services/control-plane/tests/agora/test_winner_branch_trading_room_e2e.py -v

# Run full agora test suite (regression guard)
python3 -m pytest services/control-plane/tests/agora/ services/control-plane/bff/agora/trading_room/ -v

# Schema no-order-route invariant check
grep -n "no_order_route_proof" services/control-plane/tests/agora/test_winner_branch_trading_room_e2e.py

# Forbidden execution field check
grep -n "broker_order_id\|order_route\|runtime_binding_id\|capital_binding_id" \
    services/control-plane/tests/agora/test_winner_branch_trading_room_e2e.py
# Expected: no matches (or only in FORBIDDEN_EXECUTION_KEYS constant asserting absence)
```

---

## Non-Blocking Follow-Up Notes

These items are **not** blocking review of AG-E2E-TR-001:

| # | Note | Owner |
|---|---|---|
| N1 | Live-mode E2E (Mode B historical replay or Mode C sandbox) is deferred per C4 design — CI uses Mode A only | Deferred by design |
| N2 | Widget registry integration test (asserting dashboard recipe loads from capability_manifest) is a separate acceptance concern; a fixture-level reference is sufficient for this task | Scope boundary |
| N3 | AG-FE-DB-002 is still in retry (cross-repo sync) — it does not block AG-E2E-TR-001 per the dispatch unblock matrix note | AG-FE-DB-002 owner |

---

## Handoff to Reviewer

This packet is ready for Codex to use during review of AG-E2E-TR-001.

When Claude (parent owner) submits `test_winner_branch_trading_room_e2e.py` for review, Codex
should use the reviewer checklist above and the gap analysis to confirm:

1. G1–G10 are covered by the new test file
2. Iron rule invariants are explicitly asserted end-to-end
3. The test is deterministic and CI-safe
4. No design deviation or invented schema was introduced

This sidecar (AG-E2E-TR-001-SIDECAR-REVIEW) will be marked `done` once the handoff to the
parent reviewer (Claude) is complete. The parent task (AG-E2E-TR-001) review/approval lifecycle
is separate and is owned by Claude/Codex.
