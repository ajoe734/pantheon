# Evidence Summary: LIFECYCLE-PROJ-CAPACITY-001

- Task ID: LIFECYCLE-PROJ-CAPACITY-001
- Title: Prove bounded lifecycle projection capacity and failure behavior
- Owner: Claude2
- Reviewer: Codex2
- Status: in_progress (harness implemented and unit-tested; full-scale gate run pending a quiet host window)

## What this delivers now

`services/trade_journey/lifecycle_projector_capacity.py` is a deterministic
capacity/fault harness for `LifecycleProjector` (`services/trade_journey/
lifecycle_projector.py`, delivered by `LIFECYCLE-PROJ-REDUCER-001`):

- a synthetic corpus generator (`generate_corpus_batches`) that produces an
  exact `(total_events, total_loop_runs)` corpus in monotonic `ingested_seq`
  order, batched the same way `PostgresLifecycleSource.fetch_after` delivers
  committed rows;
- an RSS/latency/backlog sampler (`run_capacity_benchmark`) that drives the
  real `LifecycleProjector.project_records` transaction path batch-by-batch
  and records the samples the section 14 gates
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
  runs the exact same code path at any scale, including the full
  1,000,000-event / 150,000-loop-run corpus, and exits non-zero with the
  specific violated gate(s) if any threshold is not met;
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

At dispatch (2026-08-21T12:08Z) the 12-vCPU dev host reported load average
55.44/47.01/36.73, and the `l12currentruntimee2e` docker-compose stack (an
in-progress `PFG-L12-RUNTIME-E2E-20260820` product E2E run) plus other
task-owned stacks were concurrently up on the same host. Launching a
million-event, RSS-ceiling-sensitive benchmark concurrently with that load
would produce contaminated, non-reproducible capacity numbers and could
itself starve the concurrent E2E run — exactly the outcome the task's own
admission-guard note (recorded in `ai-status.json` before this session
started) warns against.

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
  --output docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-CAPACITY-001/full-scale-report.json
```

The CLI's own gate check (`CapacityReport.gate_failures()`) is the pass/fail
source of truth; a non-empty `gate_failures` list in the emitted JSON is a
failed run, not a threshold to relax.

The remaining section 14 items not covered by this harness at all —
BFF read-latency/query-plan proof and cross-tenant isolation probes against a
real Postgres/BFF deployment — still need a dedicated pass against the
`LIFECYCLE-PROJ-BFF-001` reader once the capacity corpus exists in a real
database, and are tracked as follow-up work for this same task rather than a
separate one.

## Validation Commands and Results

1. Focused capacity harness tests:
   ```bash
   PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
   "$PANTHEON_PY" -m pytest -q services/trade_journey/test_lifecycle_projector_capacity.py
   ```
   Result: PASS (15 passed)

2. Compose contract test:
   ```bash
   "$PANTHEON_PY" -m pytest -q services/trade_journey/test_lifecycle_projector_compose.py
   ```
   Result: 7 passed, 1 pre-existing unrelated failure
   (`test_bff_only_deploy_rebuilds_its_lifecycle_projector_only` — fails
   identically with none of this task's files present; `scripts/
   deploy_nonprod_vm.sh` has already drifted from that assertion on `dev`
   independent of this task, and fixing it is out of this task's declared
   scope/artifacts).

3. Full `services/trade_journey` suite:
   ```bash
   "$PANTHEON_PY" -m pytest -q services/trade_journey
   ```
   Result: 149 passed, 19 skipped, 1 pre-existing unrelated failure (same as
   above).

4. Manual small-scale end-to-end smoke of the CLI:
   ```bash
   "$PANTHEON_PY" -m services.trade_journey.lifecycle_projector_capacity \
     --events 2000 --loop-runs 300 --batch-size 100 --fault-journey-count 2
   ```
   Result: all section 14 checks pass at this scale (as expected — the
   thresholds are far above small-scale numbers); all six fault scenarios
   pass.

## What is not yet true

This task cannot move to `done` yet: the section 14 acceptance gates require
the actual measured 1,000,000-event / 150,000-loop-run run and the BFF
read-latency/query-plan/isolation proof against a real deployment, neither of
which has been executed. Marking those gates passed without the measurement
would be fabricated evidence, not evidence.
