# Review: AG-BE-CP-001 — CandidatePool/Member/Discussion/Monitoring records

**Reviewer:** Claude2  
**Date:** 2026-06-22  
**Task commit:** 2b29d871b8ec5b2533eac43dec8f13c7861668b1  
**PR:** #2181 (merged into dev via 0401cca0895f8e2b956d2338ef90160dd5a0d833)

## Verdict: APPROVED

## Scope reviewed

Three files added/modified in the task commit:
- `services/control-plane/bff/agora/research/router.py` (+1290 lines)
- `services/control-plane/bff/agora/research/store.py` (+235 lines, new)
- `services/control-plane/bff/tests/test_agora_candidate_pool.py` (+266 lines, new)
- `services/control-plane/specs/agora/candidate_pool.schema.json` (pre-existing, referenced)

## Acceptance criteria

| Criterion | Status |
|---|---|
| candidate 形狀符合 schema | PASS — pool JSON validates against `candidate_pool.schema.json` in test |
| score 由 A2 recipe 算出且 components 對齊 | PASS — `_score_candidate` iterates recipe `positive_components`+`penalty_components` in order; test asserts component IDs match recipe |
| A2 confidence multiplier (0.60 + 0.40×conf) | PASS — `confidence_multiplier` formula matches recipe's `confidence_policy` |
| data_quality cap (<0.50 → effective_score ≤49, band=needs_research) | PASS — blocker text "data_quality below 0.50" verified in test |
| rejected 保留為 negative example | PASS — `negative_example=True` on reject/park decisions; `lifecycle_state=rejected` is immutable (409 on re-review) |
| §17.3 endpoints 到位 | PASS — GET/POST candidate-pools, /score, /members, /members/{id}/review, /discussions, /monitoring, /monitor, DELETE /monitor all present |
| 無自創欄位/route/enum | PASS — all fields follow schema and recipe definitions; no extra routes observed |
| no_order_route_proof on all responses | PASS — `_CANDIDATE_NO_ORDER_ROUTE_PROOF` set in every response and metadata block |
| ETag/If-Match concurrency control | PASS — lock_version tracked, `_candidate_pool_etag` format verified, 412 on mismatch |
| Idempotency-Key enforcement on mutating endpoints | PASS — all POST endpoints call `_require_candidate_idempotency` |

## Verification commands run

```
python3 -m py_compile services/control-plane/bff/agora/research/router.py \
  services/control-plane/bff/agora/research/store.py
# → SYNTAX OK

python3 -m pytest services/control-plane/bff/tests/test_agora_candidate_pool.py -q
# → 3 passed

python3 -m pytest services/control-plane/bff/tests/test_agora_candidate_pool.py \
  services/control-plane/bff/tests/test_agora_research_run_projection.py \
  services/control-plane/bff/tests/test_agora_router.py -q
# → 23 passed

python3 -m pytest services/control-plane/bff/test_no_undefined_call_symbols.py -q
# → 2 passed
```

## Notes

- Store is in-memory (dev/test). Postgres backend is deferred (env var `AGORA_RESEARCH_PLAN_STORE_BACKEND` reserved for future task).
- `liquidity_capacity` missing → forced band `suppressed`; correct per spec.
- `related_branch_distribution_risk > 0.80` → band forced to `needs_research` / capped at 64.999; correct per spec.
- Monitoring requires `lifecycle_state == "approved"` (i.e. `approve_for_monitoring` decision applied first); correct gate.
- Task artifacts list in `ai-status.json` shows `research.py` but implementation is correctly split into `agora/research/router.py` + `agora/research/store.py`; this is an artifact path discrepancy in the metadata, not a code issue.
