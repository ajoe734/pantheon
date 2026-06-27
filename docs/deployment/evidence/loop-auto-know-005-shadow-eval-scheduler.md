# LOOP-AUTO-KNOW-005: Shadow Eval Scheduler — Evidence

**Task ID:** LOOP-AUTO-KNOW-005  
**Title:** Add human imitation and shadow evaluation scheduler  
**Owner:** Claude  
**Reviewer:** Codex  
**Date:** 2026-06-27  
**Loop ID:** `human_imitation_shadow_evaluation`  
**Maturity:** `api-only` → `scheduled`

---

## Deliverables

### 1. `services/policy-learning/scheduler_worker.py`

New supervised scheduler worker that posts `/api/policy-learning/shadow-eval-tick`
on a configurable interval (`SHADOW_EVAL_SCHEDULER_INTERVAL_SECONDS`, default 3600s).

Key env vars:
- `POLICY_LEARNING_API_URL` — policy-learning service URL (default `http://policy-learning-svc:8100`)
- `SHADOW_EVAL_SCHEDULER_INTERVAL_SECONDS` — tick interval in seconds (default 3600)
- `SHADOW_EVAL_SCHEDULER_MAX_TICKS` — stop after N ticks; 0 = run forever
- `SHADOW_EVAL_TYPE` — evaluation mode: `shadow` (default) or `imitation`
- `SHADOW_EVAL_MAX_DATASETS` — max dataset refs per tick (unbounded if unset)

Worker outputs last success candidate count and last failure detail to stdout as JSON.

### 2. `services/policy-learning/main.py` — new endpoints

**`POST /api/policy-learning/shadow-eval-tick`**  
Idempotent by `(tick_id, dataset_ref.id)` — duplicate ticks for the same dataset
are skipped, not re-created. Returns:
```json
{
  "status": "ok",
  "tick_id": "shadow-tick-20260627",
  "eval_type": "shadow",
  "candidate_count": 2,
  "skipped_count": 0,
  "skipped_ids": [],
  "candidate_ids": ["sic-20260627-001", "sic-20260627-002"],
  "production_training": "fail_closed",
  "ticked_at": "2026-06-27T07:00:00Z"
}
```

**`GET /api/policy-learning/candidates`**  
List `ShadowImitationCandidate` records. Filterable by `tick_id`, `eval_type`, `status`.

**`GET /api/policy-learning/candidates/{candidate_id}`**  
Get a single candidate.

### 3. `services/policy-learning/store.py` — candidate persistence

Added `list_candidates()`, `get_candidate()`, `put_candidate()` to `PolicyLearningStore`.
Candidates stored in `shadow_imitation_candidates.json` under the data dir.

### 4. `docker-compose.yml` — new scheduler service

Service `policy-learning-shadow-eval-scheduler` added under profile
`policy-learning-shadow-eval-scheduler`. Depends on `policy-learning-svc` being healthy.

### 5. `services/policy-learning/tests/test_policy_learning_shadow_eval_scheduler.py`

12 unit tests covering:
- Scheduler env var configuration
- `run_tick()` success, HTTP error, URLError paths
- Empty dataset tick
- Tick with dataset refs → candidates persisted with correct gate fields
- Idempotency: same tick_id + dataset_id → skipped on second call
- Different tick_ids → separate candidate records
- `max_datasets` cap
- Candidates remain `fail_closed` / `proposed` (never auto-activated)
- 404 on unknown candidate
- Filter by `eval_type`

---

## Acceptance Verification

| Criterion | Status |
|---|---|
| Trace datasets run imitation or shadow eval on schedule | ✅ Scheduler worker with configurable interval |
| Candidates require experiment approval and deployment gates | ✅ `experiment_approval_gate: required`, `status: proposed` |
| Production training remains fail-closed until explicitly activated | ✅ `production_training: fail_closed` on all candidates and tick responses |

---

## Test Run

```
python3 -m pytest services/policy-learning/tests/test_policy_learning_shadow_eval_scheduler.py -v
# 12 passed in 2.22s

python3 -m pytest services/policy-learning/tests/ services/research-worker-gateway/tests/ -v
# 51 passed in 11.98s
```

---

## Non-goals (enforced)

- No live-capital execution
- No approval gate bypass
- No seed fixture as live proof
- No production training activation — candidates stay `proposed` until explicit gate open
