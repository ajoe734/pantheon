# LOOP-AUTO-KNOW-005 Review — Claude2

**Task:** Add human imitation and shadow evaluation scheduler  
**Owner:** Claude  
**Reviewer:** Claude2  
**Date:** 2026-06-27  
**Outcome:** Approved

---

## Review Scope

Reviewed commit `949acef7` and all deliverables:

- `services/policy-learning/scheduler_worker.py`
- `services/policy-learning/main.py` (new shadow-eval endpoints)
- `services/policy-learning/store.py` (candidate persistence)
- `docker-compose.yml` (new scheduler service profile)
- `services/policy-learning/tests/test_policy_learning_shadow_eval_scheduler.py`
- `docs/deployment/evidence/loop-auto-know-005-shadow-eval-scheduler.md`

---

## Acceptance Criteria Verification

| Criterion | Verdict | Notes |
|---|---|---|
| Trace datasets run imitation or shadow eval on schedule | ✅ Pass | `scheduler_worker.py` POST to `shadow-eval-tick` on configurable interval (default 3600s) |
| Candidates require experiment approval and deployment gates | ✅ Pass | Every candidate has `experiment_approval_gate: "required"` and `status: proposed` |
| Production training remains fail-closed until explicitly activated | ✅ Pass | `production_training: "fail_closed"` on all candidates and tick responses; gate requires explicit env var |

---

## Implementation Review

### scheduler_worker.py

- Clean design: single `run_tick()` function, pure `main()` loop.
- Configurable via env vars with safe defaults.
- Exposes `last_success_candidate_count` and `last_failure_detail` per tick to stdout as JSON — satisfies operator-visible truth requirement.
- `max_ticks` control allows test/smoke runs without running forever.
- No production adapter calls.

### main.py — shadow-eval-tick

- Idempotency: skips `(tick_id, dataset_ref.id)` pairs already seen for this tick. Duplicate scheduler ticks are safe.
- Default `tick_id` is date-based (`shadow-tick-20260627`), which naturally scopes deduplication to the day.
- Response always carries `production_training: "fail_closed"` — cannot be confused as activated.
- `GET /candidates` supports filtering by `tick_id`, `eval_type`, and `status` — satisfies operator-visible truth projection.

### store.py

- Candidate persistence uses a separate JSON file (`shadow_imitation_candidates.json`) from job store — clean separation.
- Note: Postgres backend covers jobs only; candidates always use JSON file. Acceptable at `scheduled` maturity; Postgres candidate store can be a follow-up at `reconciled` maturity.

### docker-compose.yml

- Scheduler under named profile `policy-learning-shadow-eval-scheduler` — not started by default, must be explicitly activated. Correct fail-closed posture.
- `depends_on: policy-learning-svc: condition: service_healthy` — scheduler waits for API to be ready before ticking.

### Tests

- 12 tests covering scheduler env vars, `run_tick()` success/error paths, empty tick, dataset → candidate creation, idempotency, `max_datasets` cap, fail-closed gate enforcement, eval_type filter.
- Verified: 51 total tests pass including pre-existing research-worker-gateway tests.

---

## Observations (Non-blocking)

1. **Dataset source connection:** The scheduler sends empty `dataset_refs` by default, so it produces 0 candidates per tick unless the caller injects refs. This is acceptable at `scheduled` maturity (the loop mechanism exists; connecting it to LOOP-AUTO-KNOW-004 dataset outputs is a `reconciled`-maturity concern). The evidence doc is clear about this boundary.

2. **Candidate Postgres persistence:** Not implemented. Acceptable given `api-only → scheduled` target — noted above.

---

## Conclusion

Implementation meets all three acceptance criteria. Code is clean, idempotent, fail-closed, and operator-observable. Tests pass. Approved for owner finalization.
