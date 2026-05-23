# BFF-B5-001 Review — Claude

**Task:** BFF-B5-001 — HumanGate command operations via /bff/v1/commands  
**Reviewer:** Claude  
**Owner:** Codex2  
**Reviewed at:** 2026-05-23  
**Commit:** 972de7ba (merged via PR #476 → dev: db10d26a)

## Verdict: APPROVED

All 5 acceptance criteria are met. The implementation is merged, tests pass, and no regressions were found.

## Evidence

### Tests run locally

```
PYTHONPATH=services/control-plane/bff pytest services/control-plane/bff/tests/test_bff_b5_humangate_commands.py
4 passed in 3.91s

PYTHONPATH=services/control-plane/bff pytest \
  services/control-plane/bff/tests/test_bff_b5_humangate_commands.py \
  services/control-plane/bff/tests/test_bff_b3_human_inbox.py \
  services/control-plane/bff/tests/test_bff_pm12_persona_league.py
17 passed in 8.40s
```

### Acceptance criteria verification

| # | Criterion | Verified |
|---|---|---|
| 1 | `POST /bff/v1/commands` admits all 6 B5 command names | ✅ `test_humangate_command_names_are_admitted_through_bff_v1_commands` — all 5 HumanGate commands + `QuarterlyRankingRecommendationSubmit` admitted with HTTP 202 |
| 2 | Each command returns standard `CommandResponse<T>` with command id, tracking URL, receipt dual-write data, and durable idempotency metadata | ✅ `_accepted_command_id` helper asserts all fields; `liveCapitalSideEffects=False` confirmed in `meta` |
| 3 | Human Inbox decision flow can approve, reject, and request more evidence using inbox item id as HumanGateItem target | ✅ `test_human_inbox_decision_flow_can_submit_decisions_via_command_path` — fetches real inbox item, submits all 3 decisions, verifies `source_type` inferred from `intervention:` prefix |
| 4 | `QuarterlyRankingRecommendationSubmit` records governance intent with `liveCapitalSideEffects=false` | ✅ `test_quarterly_ranking_recommendation_submit_uses_command_response_without_live_mutation` — verifies `recommendation_id`, `recommendation_action_id`, `action_id=submit_recommendation`, and `audit_event=quarterly_ranking.recommendation_submitted` |
| 5 | B5 command names in action catalog and executor dispatch table | ✅ `test_b5_commands_are_in_action_catalog_and_executor_dispatch` — all 6 entries in `action_catalog.py`, all 6 dispatch to `_execute_bff_action_adapter` via `command_executor.py` |

### Code inspection

- **models.py**: `HUMAN_GATE_APPROVE`, `HUMAN_GATE_REJECT`, `HUMAN_GATE_REQUEST_MORE_EVIDENCE`, `HUMAN_GATE_REVOKE`, `HUMAN_GATE_EXTEND_TTL`, `QUARTERLY_RANKING_RECOMMENDATION_SUBMIT` added to `CommandType` enum; `HUMAN_GATE_ITEM` added to `ObjectType`.
- **main.py**: `_normalize_human_gate_command` correctly normalizes `target.id` → `human_gate_item_id`/`itemId`, infers `source_type` from `approval:`/`intervention:` prefix, records `human_gate.{decision}` audit events. `_validate_human_gate_decision` enforces role gates (`approver`/`admin` for approve/reject/revoke/extend_ttl; operator-level for request_more_evidence) and positive TTL for `HumanGateExtendTtl`. `_normalize_quarterly_recommendation_command` normalizes `action_id=submit_recommendation` and sets governance audit event.
- **action_catalog.py**: All 6 B5 command names registered with `entity_type` and `endpoint=/bff/v1/commands`.
- **command_executor.py**: All 6 types mapped to `_execute_bff_action_adapter` (adapter-only, no live capital mutation path).

## No blocking issues

The implementation fully satisfies the spec. The PR is already merged into `dev` with green CI checks. Returning to owner for final closeout.
