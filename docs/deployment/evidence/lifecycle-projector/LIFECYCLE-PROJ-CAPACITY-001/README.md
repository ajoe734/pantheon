# Evidence: LIFECYCLE-PROJ-CAPACITY-001

This directory holds the reproducible PostgreSQL capacity proof for the
relational lifecycle projector. It is not a reader-cutover record and does
not authorize production load testing.

## What is measured

`services/trade_journey/lifecycle_projector_capacity.py` drives only
`RelationalLifecycleProjector` backed by `ProjectionStore`. It has no legacy
snapshot-writer or local controller-state path. Each run:

- generates the deterministic `lifecycle-capacity-v1` corpus: 1,000,000
  canonical events and 150,000 completed loop runs, with a 500-row batch;
- measures per-batch latency, peak/steady RSS, backlog age, and the 500k to
  1M RSS slope;
- catches up a further 100,000 events on the same durable controller and
  fails above the 30-minute limit;
- executes the actual BFF PostgreSQL read repository for journey list, detail,
  timeline, loop list, and loop detail at page size 200, then records p95s;
- captures PostgreSQL `EXPLAIN (FORMAT JSON)` for the bounded BFF list,
  detail, timeline, and loop queries; and
- runs restart, SIGKILL-after-commit-before-acknowledgement, DB disconnect,
  real PostgreSQL deadlock, rollback, second-writer conflict, duplicate,
  out-of-order, conflicting-duplicate, and quarantine cases.

All of those paths use a fresh `lifecycle_capacity_*` PostgreSQL schema.
The harness rejects the shared `trade_journey_projection` schema, rejects an
already-existing capacity schema, and proves `DROP SCHEMA ... CASCADE` in a
`finally` block for both the benchmark and every fault scenario.

## Preconditions

Run only on a quiet dev host with no E2E task containers or task networks.
The normal product stack may remain up; the harness uses an isolated schema
and never starts the default projector service. Verify the source identity
before a run:

```bash
git status --porcelain=v1 --untracked-files=all
git rev-parse HEAD
docker ps --format '{{.Names}}\t{{.Status}}'
docker network ls --format '{{.Name}}'
```

The CLI reads Git directly and refuses a dirty tree. Its emitted evidence
binds the exact commit, clean-tree status hash, corpus configuration checksum,
and the output checksum sidecar. In a container image without `.git`, provide
an exact 40-character `GIT_SHA` and `LIFECYCLE_CAPACITY_GIT_DIRTY=clean`.

## Canonical command

From a clean checkout of the exact commit being measured:

```bash
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
LIFECYCLE_PROJECTOR_PROJECTION_DSN='postgresql://pantheon_app:pantheon_app@127.0.0.1:15432/pantheon' \
"$PANTHEON_PY" -m services.trade_journey.lifecycle_projector_capacity \
  --events 1000000 --loop-runs 150000 --batch-size 500 \
  --catch-up-events 100000 --read-repeats 10 \
  --repository-root "$PWD" \
  --output /absolute/path/to/docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-CAPACITY-001/evidence.json
```

The compose job is opt-in and bounded at 4 GiB:

```bash
GIT_SHA="$(git rev-parse HEAD)" \
LIFECYCLE_CAPACITY_GIT_DIRTY=clean \
docker compose -p pantheon -f docker-compose.yml \
  --profile lifecycle-capacity-benchmark run --rm \
  lifecycle-projector-capacity-benchmark
```

It generates its own capacity-only schema unless
`LIFECYCLE_PROJECTOR_CAPACITY_SCHEMA` supplies a fresh
`lifecycle_capacity_*` name. The job cannot point at the default projection
schema.

## Evidence contract

The generated `evidence.json` is the raw, redacted evidence manifest. It must
contain:

- `git` — exact commit, clean status, and tree-status SHA-256;
- `corpus` — deterministic corpus configuration and SHA-256;
- `capacity.samples` — RSS, backlog, checkpoint, and latency samples for
  every batch, plus derived p95 and RSS gates;
- `catch_up`, `bff_reads`, and `bff_explain` — measured p95s and indexed plans
  for page size 200;
- `fault_matrix` — RPO=0 and no-duplicate-stage outcomes for each named
  scenario; and
- `teardown.schema_dropped: true`.

`evidence.json.sha256` binds the byte-identical raw evidence. Any non-empty
`gate_failures` list is a failed capacity proof and must not be relaxed or
reported as pass.

## Focused verification

```bash
TEST_DATABASE_URL='postgresql://pantheon_app:pantheon_app@127.0.0.1:15432/pantheon' \
  "$PANTHEON_PY" -m pytest -q \
  services/trade_journey/test_lifecycle_projector_capacity.py \
  services/trade_journey/test_lifecycle_projector_compose.py
```

The focused test suite creates an independent PostgreSQL schema for every
scenario and asserts teardown. A pre-existing deployment-script assertion in
`test_lifecycle_projector_compose.py` is unrelated to this task and must be
reported separately if it fails.
