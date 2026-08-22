# Lifecycle projector target-dev cutover runbook

Task: `LIFECYCLE-PROJ-CUTOVER-001`

Target: `pantheon-lupin-dev` in `pantheon-lupin-dev-20260719`

Reader rollback boundary: `PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND`
Last updated: 2026-08-21

This is a paper-only target-dev procedure. It does not authorize production,
live capital, broker actions, legacy data retirement, or a destructive schema
reset. A step is accepted only after its command and redacted output have been
captured. Do not infer a later gate from an earlier pass.

## Safety and identity invariants

- Use a merged 40-character `dev` SHA. Candidate-branch deployment is not
  accepted cutover evidence.
- Hold the shared dev environment lease for every deploy, migration, container
  recreation, rollback, and forward-recovery command. The repository workflow
  supplies and verifies the lease; do not bypass it with an unguarded SSH
  session.
- Build and deploy with `PANTHEON_CANARY_EXECUTION_ENABLED=false`,
  `PANTHEON_LIVE_BROKER_ENABLED=false`, and `BROKER_PAPER_ENABLED=true`.
- Keep the compose defaults `LIFECYCLE_PROJECTOR_WRITER_BACKEND=disabled` and
  `PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND=json`. Activation is explicit.
- The service key `loop-run-projector-scheduler` becomes the bounded relational
  worker when its backend is `shadow`; it is not a second writer. The stopped
  JSON implementation is not restarted alongside it.
- Preserve the `bff-data` volume and its final JSON generation. JSON is a
  recovery reader only after cutover and must report stale truth honestly.
- `telemetry_events` is the only backfill authority. Before an exact root
  deploy, prove that the selected deploy path will not prune or truncate that
  table before the backfill commits. Capture the lifecycle-row count and
  retained high watermark both before and after deployment. If a non-empty
  legacy bundle remains but the canonical lifecycle source window is empty,
  stop: do not backfill from derived JSON and do not clear legacy state to
  manufacture parity.
- Never record a DSN, credential, access token, raw payload, or page-token
  secret in evidence. Record only allowlisted controller/config fields and
  checksums.

## Evidence workspace

Use a directory outside the managed deploy worktree for runtime outputs. The
managed deploy checkout must remain clean.

```bash
export CUTOVER_TASK=LIFECYCLE-PROJ-CUTOVER-001
export CUTOVER_EVIDENCE_ROOT=/var/tmp/pantheon-evidence/${CUTOVER_TASK}
install -d -m 0700 "${CUTOVER_EVIDENCE_ROOT}"
```

Every JSON artifact is checksummed after it is written. `evidence.json` may
refer only to files whose checksum is in the final manifest.

## 1. Local real-PostgreSQL admission

Use a real disposable PostgreSQL database, not mocks or a skipped database
fixture.

```bash
TEST_DATABASE_URL='<redacted-test-dsn>' \
  .venv-pantheon/bin/python -m pytest -q \
  services/trade_journey/test_projection_migration.py -rs

TEST_DATABASE_URL='<redacted-test-dsn>' \
  .venv-pantheon/bin/python -m pytest -q \
  services/trade_journey/test_projection_store.py::test_projection_store_mixed_batch_filters_duplicate_owned_mutations \
  services/trade_journey/test_projection_store.py::test_projection_store_contiguous_checkpoint_advancement -rs
```

Required: zero skip; duplicate ordinal 9 and new ordinal 10 are in one fetched
batch and one database transaction; receipts/stages/journey/loop/controller do
not receive duplicate-owned mutations; checkpoint equals source high watermark;
backlog and unexplained mismatch are zero.

## 2. Merge and exact default-safe deployment

Independent review must approve the exact PR head. Merge the PR to `dev`, then
deploy that merge SHA through the existing nonprod workflow under the shared
lease. The initial deployment deliberately retains the JSON reader and disabled
relational writer.

Before activation, capture these identities:

```bash
git rev-parse HEAD
docker compose -p pantheon -f docker-compose.yml ps
docker inspect --format '{{.Image}}' pantheon-operator-bff-1
docker inspect --format '{{.Image}}' pantheon-loop-run-projector-scheduler-1
sha256sum services/trade_journey/migrations/001_create_trade_journey_projection_schema.sql
sha256sum docker-compose.yml
curl -fsS http://127.0.0.1:18001/bff/version
```

Required: hosted BFF source SHA equals the merged SHA; both container image IDs
are recorded; the migration and compose checksums are recorded; the reader is
still `json`; no canary claim has been made.

## 3. Additive migration and resumable backfill

Apply only the additive migration. Do not run a down migration or truncate the
canonical source or projection schema.

```bash
docker compose -p pantheon -f docker-compose.yml exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U pantheon_app -d pantheon \
  < services/trade_journey/migrations/001_create_trade_journey_projection_schema.sql

LIFECYCLE_PROJECTION_DSN='postgresql://pantheon_app:<redacted>@postgres:5432/pantheon' \
GIT_SHA="${MERGED_SHA}" \
docker compose -p pantheon -f docker-compose.yml run --rm -T --no-deps \
  -e LIFECYCLE_PROJECTION_DSN -e GIT_SHA \
  loop-run-projector-scheduler \
  python -m scripts.lifecycle_projector_migrate \
    --controller-id canonical-lifecycle-projector \
    --tenant-scope '*' --environment-scope '*' \
    --snapshot-path /data/bff/lifecycle-projection/cutover-migrate.snapshot.json \
    --evidence-out /data/bff/lifecycle-projection/cutover-migrate-result.json
```

Re-run the migration command once. The second run must resume from the durable
snapshot without new derived mutation or data loss.

Capture the migration controller separately from the live controller. Its ID is
`canonical-lifecycle-projector-migrate`; it never grants live-read authority.

## 4. Pre-switch parity, capacity, and security gates

Run parity while the final JSON bundle is still the accepted reader and before
new relational live events can diverge from that frozen recovery snapshot.

```bash
LIFECYCLE_PROJECTION_DSN='postgresql://pantheon_app:<redacted>@postgres:5432/pantheon' \
docker compose -p pantheon -f docker-compose.yml exec -T \
  -e LIFECYCLE_PROJECTION_DSN loop-run-projector-scheduler \
  python -m scripts.lifecycle_projector_parity \
    --legacy-journey-events /data/bff/lifecycle-projection/current/trade_journey_events.json \
    --legacy-loop-runs /data/bff/lifecycle-projection/current/loop_runs.json \
    --legacy-controller-state /data/bff/lifecycle-projection/controller_state.json \
    --out /data/bff/lifecycle-projection/pre-switch-parity.json

docker cp pantheon-loop-run-projector-scheduler-1:/data/bff/lifecycle-projection/pre-switch-parity.json \
  "${CUTOVER_EVIDENCE_ROOT}/pre-switch-parity.json"
```

Required:

- stage, journey, loop, identity, and quarantine category hashes recorded;
- `unexplained_mismatch_count=0` and backlog zero;
- `LIFECYCLE-PROJ-CAPACITY-001` reviewed evidence still passes its stated
  resource, fault, and latency thresholds;
- scoped reader/page-token and live-sensitive masking tests pass;
- migration/runtime credentials are identified separately and no source
  `UPDATE`/`DELETE` authority is granted to the runtime worker.

## 5. Shadow activation with reader unchanged

Supply the projection DSN from the governed secret source. Recreate only the
single lifecycle worker; do not start a legacy JSON writer.

```bash
LIFECYCLE_PROJECTOR_WRITER_BACKEND=shadow \
LIFECYCLE_PROJECTOR_PROJECTION_DSN='postgresql://pantheon_app:<redacted>@postgres:5432/pantheon' \
LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA=trade_journey_projection \
docker compose -p pantheon -f docker-compose.yml up -d \
  --force-recreate --no-deps loop-run-projector-scheduler
```

Poll the allowlisted relational controller fields until all are true:

```text
controller_id=canonical-lifecycle-projector
deployment_sha=<merged SHA>
mode=live
status=ready
accepted_live=true
checkpoint_seq=source_high_watermark
backlog_count=0
unresolved_quarantine_count=0
last_error_message=''
```

Also verify the worker image ID is the recorded exact image and the JSON files
and `bff-data` volume still exist. At this point BFF posture must still report
`trade_journey_reader_backend=json`.

## 6. Authorized paper canary and all-dev read switch

Inject a random secret of at least 16 bytes from the governed secret source.
Do not print it. Recreate only BFF with the single reader backend set to
`postgres`; leave live execution disabled. Initially admit only the governed
operator canary traffic. After the hosted gates pass, reopen normal target-dev
paper reads without another backend change.

```bash
PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND=postgres \
PANTHEON_BFF_TRADE_JOURNEY_PROJECTION_DSN='postgresql://pantheon_app:<redacted>@postgres:5432/pantheon' \
PANTHEON_BFF_TRADE_JOURNEY_PROJECTION_SCHEMA=trade_journey_projection \
PANTHEON_BFF_TRADE_JOURNEY_PAGE_TOKEN_SECRET='<governed-secret>' \
docker compose -p pantheon -f docker-compose.yml up -d \
  --force-recreate --no-deps operator-bff
```

Required before normal paper reads:

- `/bff/version` reports exact merged SHA, strict auth, and reader backend
  `postgres` with schema `trade_journey_projection`;
- `/readyz` reads the relational controller and passes exact SHA, checkpoint,
  backlog, quarantine, live mode, and freshness gates;
- authenticated list/detail/timeline/graph/evidence/loop responses identify
  the Postgres projection and never fall back to JSON.

## 7. Real paper lifecycle and negative probes

Capture a post-deploy telemetry baseline, emit one normal paper lifecycle using
the existing producer/reconciliation path, then run the read-only hosted proof.
The projector container automatically selects its relational DSN.

```bash
bash scripts/run_loop_prod_tel_002_hosted_probe.sh \
  --expected-sha "${MERGED_SHA}" \
  --container-output /tmp/lifecycle-cutover-source.json \
  --remote-output "${CUTOVER_EVIDENCE_ROOT}/hosted-source.json"

DEV_BFF_OIDC_CLIENT_ID='<governed-operator-a-id>' \
DEV_BFF_OIDC_CLIENT_SECRET='<governed-secret>' \
python3 services/trade_journey/hosted_bff_readback.py \
  --source "${CUTOVER_EVIDENCE_ROOT}/hosted-source.json" \
  --output "${CUTOVER_EVIDENCE_ROOT}/hosted-bff-readback.json" \
  --expected-sha "${MERGED_SHA}" \
  --expected-login-identity operator_a \
  --base-url https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io
```

The proof must correlate exact event IDs, source offsets, stable identity,
terminal status, controller identity, and monotonic projection generations.
Negative results must be: unauthenticated/arbitrary bearer `401`, cross-tenant
and paper-to-live detail `404`, and stale/scope-conflicting page token `400`.
The evidence artifact persists no response payloads, token, credentials, DSN,
or raw lifecycle payload.

## 8. BFF-only rollback and forward recovery

Record the relational controller and receipt counts before rollback. Roll back
only the reader and recreate only BFF:

```bash
PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND=json \
docker compose -p pantheon -f docker-compose.yml up -d \
  --force-recreate --no-deps operator-bff
```

Required: the relational worker remains stopped only if diagnosis requires it;
no source/projection rows are deleted; the preserved JSON bundle is readable;
if its controller is old, `/readyz` and response metadata label it stale rather
than manufacturing freshness.

Forward recovery reuses the same schema and controller, waits for backlog zero,
then recreates only BFF with `postgres` and the same governed DSN/schema/secret.
Re-run the authenticated hosted readback. Receipt count, maximum ingested
sequence, and the canary journey/loop must be unchanged or monotonically
advanced—never lower or duplicated.

## 9. Actual 24-hour observation

The observation starts only after forward recovery and all target-dev paper
reads are accepted. Record concrete UTC start/end timestamps at least 24 hours
apart. A planned duration or a short sample is not a pass.

At least hourly, capture allowlisted data:

- exact BFF SHA/image ID and Postgres reader posture;
- controller checkpoint/high watermark/backlog/quarantine/error/last poll;
- projector container state, restart count, memory and CPU;
- BFF 5xx and lifecycle read latency/error counts;
- one authenticated readback of the retained paper canary identity.

Any wrong SHA/backend, stale controller, restart/OOM, backlog, quarantine,
unexplained parity drift, protected-scope failure, or hosted read failure ends
the observation as failed and triggers the BFF-only rollback. After 24 actual
hours, checksum all redacted samples and publish start/end/duration plus zero
violation counts in `evidence.json`.

## Closeout

The task remains `in_progress` until the exact merged deployment, shadow gate,
authorized canary, all-dev switch, real lifecycle, negatives, rollback/forward,
and actual 24-hour observation have all executed. Independent review must bind
to the exact evidence checksums. Legacy retirement is a later task and must not
be performed here.
