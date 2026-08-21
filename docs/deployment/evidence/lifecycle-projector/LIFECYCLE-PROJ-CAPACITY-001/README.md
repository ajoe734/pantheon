# Evidence Summary: LIFECYCLE-PROJ-CAPACITY-001

- Task ID: LIFECYCLE-PROJ-CAPACITY-001
- Title: Prove bounded lifecycle projection capacity and failure behavior
- Owner: Codex2
- Reviewer: Antigravity2
- Status: in_progress (relational harness and bulk transaction path are under focused verification; canonical scale evidence remains pending a quiet host window)

## What this delivers now

`services/trade_journey/lifecycle_projector_capacity.py` is a deterministic
capacity/fault harness for `RelationalLifecycleProjector`
(`services/trade_journey/lifecycle_projector.py`, delivered by
`LIFECYCLE-PROJ-REDUCER-001`):

- a synthetic corpus generator (`generate_corpus_batches`) that produces an
  exact `(total_events, total_loop_runs)` corpus in monotonic `ingested_seq`
  order, batched the same way `PostgresLifecycleSource.fetch_after` delivers
  committed rows;
- an RSS/latency/backlog sampler (`run_capacity_benchmark`) that drives the
  real `RelationalLifecycleProjector.project_records` PostgreSQL transaction
  path batch-by-batch and records the samples the section 14 gates
  (`docs/04/pantheon_lifecycle_projector_incremental_redesign_2026-08-01/
  archive/LIFECYCLE_PROJECTOR_INCREMENTAL_REDESIGN_PLAN_2026-08-01.md`) are
  computed from: steady/peak RSS, 500k→1M RSS slope, batch-latency p95, and
  backlog-age p95;
- a fault matrix (`run_fault_matrix`) covering SIGKILL mid-publish, DB
  disconnect before apply, injected state-transaction rollback, a
  second-writer conflict, duplicate delivery, and out-of-order delivery —
  reusing the same fork/monkeypatch techniques already proven at fixture
  scale in `test_lifecycle_projector.py`, applied at harness scale instead;
- a `python -m services.trade_journey.lifecycle_projector_capacity` CLI that
  requires an explicit relational DML target and runs the exact same code
  path at any scale, including the full 1,000,000-event / 150,000-loop-run
  corpus; it exits non-zero with the specific violated gate(s) if any
  threshold is not met, rather than falling back to JSON snapshots;
- set-based receipt preflight, receipt claiming, and aggregate hydration. A
  500-row poll uses bounded batch queries instead of one receipt connection
  and one aggregate query per source row/journey. The controller advisory
  lock, exact-duplicate/conflict distinction, transaction rollback, and
  fail-stop behavior remain unchanged.
- a profile-gated, run-once `lifecycle-projector-capacity-benchmark` compose
  service (`docker-compose.yml`, `profiles: ["lifecycle-capacity-benchmark"]`)
  so the full-scale run can be launched under a documented 4 GiB
  `mem_limit` without ever starting alongside the default stack or acting as
  a second projector writer.

`services/trade_journey/test_lifecycle_projector_capacity.py` (15 tests) and
the new compose assertion in `test_lifecycle_projector_compose.py` exercise
all of the above at a small, fast scale (2,000 events / 300 loop runs and a
handful of fault-matrix journeys). They prove the harness is correct; they do
not themselves prove the section 14 scale gates, which require the full
corpus.

## Why the full 1,000,000-event benchmark has not been run yet

The canonical run requires zero E2E containers *and networks* on the host.
At the latest preflight, `pfg-l12-research-e2e-20260820_default` and
`pfg-l12-runtime-e2e-20260820_default` were still present. Running a
million-event, RSS-ceiling-sensitive benchmark until those unrelated
resources have been torn down would produce contaminated, non-reproducible
capacity numbers and could starve another task.

This is not a task dependency block: `LIFECYCLE-PROJ-REDUCER-001` and
`LIFECYCLE-PROJ-BFF-001` are both `done` and this task's own code is ready to
run. It is a host-capacity/environment condition, and the acceptance gates in
section 14 must not be marked passed without the actual measured run.

## Running the full-scale benchmark once the host is quiet

```bash
# from the repository root, after confirming no other resource-heavy task
# stack (product E2E runs, other capacity/perf benchmarks) is active
docker compose -p pantheon -f docker-compose.yml \
  --profile lifecycle-capacity-benchmark run --rm \
  lifecycle-projector-capacity-benchmark

# or directly against a provisioned interpreter, without docker:
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
"$PANTHEON_PY" -m services.trade_journey.lifecycle_projector_capacity \
  --events 1000000 --loop-runs 150000 --batch-size 500 \
  --projection-dsn "$LIFECYCLE_PROJECTOR_PROJECTION_DSN" \
  --projection-schema "$LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA" \
  --output docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-CAPACITY-001/full-scale-report.json
```

The CLI's own gate check (`CapacityReport.gate_failures()`) is the pass/fail
source of truth; a non-empty `gate_failures` list in the emitted JSON is a
failed run, not a threshold to relax.

The remaining section 14 items — BFF read-latency/query-plan proof and
cross-tenant isolation probes against the `LIFECYCLE-PROJ-BFF-001` reader —
must use the same committed relational corpus and appear beside the final raw
metrics, checksums, image/config identity, and teardown record.

## Validation Commands and Results

1. Focused capacity harness tests:
   ```bash
   PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
   "$PANTHEON_PY" -m pytest -q services/trade_journey/test_lifecycle_projector_capacity.py
   ```
   Result: PASS as part of the exact committed focused run below.

2. Compose contract test:
   ```bash
   "$PANTHEON_PY" -m pytest -q services/trade_journey/test_lifecycle_projector_compose.py
   ```
   Result: the capacity benchmark contract passes. The known
   `test_bff_only_deploy_rebuilds_its_lifecycle_projector_only` deployment
   script assertion still fails identically and is outside this task's
   declared artifacts.

3. Full `services/trade_journey` suite:
   ```bash
   "$PANTHEON_PY" -m pytest -q services/trade_journey
   ```
   Result: the exact committed focused relational run was:
   ```bash
   TEST_DATABASE_URL=<isolated dev-Postgres endpoint> "$PANTHEON_PY" -m pytest -q \
     services/trade_journey/test_lifecycle_projector_capacity.py \
     services/trade_journey/test_lifecycle_projector.py \
     services/trade_journey/test_projection_store.py
   ```
   `74 passed, 1 skipped` — this includes real PostgreSQL duplicate/conflict,
   transaction rollback, and second-writer coverage. It does not claim a
   host-capacity p95 measurement.

4. Manual small-scale end-to-end smoke of the CLI:
   ```bash
   "$PANTHEON_PY" -m services.trade_journey.lifecycle_projector_capacity \
     --events 2000 --loop-runs 300 --batch-size 100 --fault-journey-count 2 \
     --projection-dsn "$LIFECYCLE_PROJECTOR_PROJECTION_DSN" \
     --projection-schema "$LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA"
   ```
   Result: not run. The batch=500 p95 smoke and the full run remain subject
   to the host admission gate above; no capacity JSON report or checksum has
   been emitted from this worker while unrelated E2E networks remain.

## What is not yet true

This task cannot move to `done` yet: the section 14 acceptance gates require
the actual measured 1,000,000-event / 150,000-loop-run run and the BFF
read-latency/query-plan/isolation proof against a real deployment, neither of
which has been executed. Marking those gates passed without the measurement
would be fabricated evidence, not evidence.
