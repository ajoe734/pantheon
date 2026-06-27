# LOOP-AUTO-KNOW-002: Alpha Replication Queue and Revalidation Worker — Evidence Notes

Task: LOOP-AUTO-KNOW-002
Owner: Claude2
Reviewer: Claude
Status: implementation complete; awaiting review

## Scope

Added `services/research/alpha_replication/` with:

- `queue.py` — `AlphaReplicationQueue`: thread-safe, file-backed JSONL queue that
  accepts StrategySpec payloads in `approved` or `review` lifecycle state, rejects
  all other states, and is idempotent by `(strategy_id, spec_version)`.

- `revalidation_worker.py` — `AlphaRevalidationWorker`: scheduled worker that drains
  the queue into stub `ExperimentRun`-shaped records. Dispatch mode defaults to `stub`
  (controlled by `PANTHEON_ALPHA_REVALIDATION_DISPATCH_MODE`). Production/paper/canary/
  live modes raise `ValueError` at construction time — the gateway boundary remains
  fail-closed. Worker exposes `last_success_at`, `last_failure_at`, `run_count`, and
  `error_count` via `get_metrics()`.

- `__init__.py` — public exports.

## Acceptance Criteria Mapping

| Criterion | Evidence |
|---|---|
| Reviewed strategy specs enter replication queue | `AlphaReplicationQueue.enqueue()` accepts `lifecycle_state in {"approved","review"}` and is idempotent by `(strategy_id, spec_version)`. Verified by `TestAlphaReplicationQueueEnqueue`. |
| Scheduled revalidation produces ExperimentRun records | `AlphaRevalidationWorker.run_once()` creates stub ExperimentRun-shaped records in `alpha_revalidation_runs.jsonl`. Verified by `TestAlphaRevalidationWorkerRunOnce`. |
| Production adapters remain fail-closed | `AlphaRevalidationWorker.__init__` raises `ValueError` for any dispatch mode outside `{stub, handoff_only, manual}`. Verified by `TestAlphaRevalidationWorkerSafetyBoundary`. |

## Test Run

```
python3 -m pytest services/research/alpha_replication/test_queue.py \
  services/research/alpha_replication/test_revalidation_worker.py -v
46 passed in 3.62s
```

Command run: 2026-06-27 from task/LOOP-AUTO-KNOW-002 branch.

## Non-Goals (enforced)

- No live-capital execution
- No approval gate bypass
- No panel-only closure
- No seed fixture as live proof
- No registry writes (only stub dispatch records)

## Dispatch Boundary

All revalidation runs are created with `production_activation: "disabled"`.
The `AlphaRevalidationWorker` refuses construction when `dispatch_mode` is outside the
safe set, making the fail-closed invariant an instantiation constraint, not a runtime check.
