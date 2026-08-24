# Lifecycle projector target-dev cutover & retirement runbook

Task: `LIFECYCLE-PROJ-RETIRE-001` (predecessor: `LIFECYCLE-PROJ-CUTOVER-001`)

Target: `pantheon-lupin-dev` in `pantheon-lupin-dev-20260719`

Reader backend: `PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND=postgres` (sole canonical reader)
Last updated: 2026-08-22

> [!NOTE]
> **Operational Status & Phasing**:
> - **Retirement Scope (`LIFECYCLE-PROJ-RETIRE-001`)**: Decommissions legacy JSON readers and writers from application/BFF code, enforces fail-closed rejection of `reader=json`, and provides the dry-run and HMAC-SHA256-signed retirement tool (`scripts/lifecycle_projector_legacy_retire.py`) for legacy JSON file pruning following the required 7-day post-cutover target-dev soak period.
> - **Historical Cutover Reference (Sections 1–7)**: Sections 1–7 document the one-time baseline import and shadow cutover executed during `LIFECYCLE-PROJ-CUTOVER-001` on 2026-08-22 when the reader was transitioned from `json` to `postgres`.

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
- Compose defaults are `LIFECYCLE_PROJECTOR_WRITER_BACKEND=shadow` and
  `PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND=postgres`. Legacy JSON writers and
  readers are retired and fail closed.
- The service key `loop-run-projector-scheduler` runs the bounded relational
  worker writing to PostgreSQL (`trade_journey_projections`,
  `trade_journey_controller_state`, etc.).
- Legacy JSON bundle files are retired and quarantined/pruned under `LIFECYCLE-PROJ-RETIRE-001`
  following completion of the 7-day target-dev soak. PostgreSQL is the sole canonical storage and reader backend.
- `telemetry_events` remains the normal backfill authority. Before an exact
  root deploy, prove that the selected deploy path will not prune or truncate
  it. Capture lifecycle-row count and retained high watermark before and after
  deployment.
- Historical target-dev cutover recovery context: Human/Ops determined on 2026-08-22
  during predecessor cutover (`LIFECYCLE-PROJ-CUTOVER-001`) that the reader had never
  left JSON, the relational writer had stayed disabled, the relational projection was empty,
  no source backup or snapshot existed, and the three-file legacy JSON bundle was intact.
  The cutover imported that exact folded baseline to establish PostgreSQL-only operation.
  Post-cutover, a full 7-day soak on target-dev with zero freshness/capacity/parity/security
  violations and explicit Human/Ops approval are required before operational file deletion
  under `LIFECYCLE-PROJ-RETIRE-001`.
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

## 2. Merge and exact default-safe deployment (Historical Cutover Phase)

*Note: During historical cutover (`LIFECYCLE-PROJ-CUTOVER-001`), the pre-cutover deployment verified initial `json` baseline before shadow migration. In the current post-retirement state (`LIFECYCLE-PROJ-RETIRE-001`), all deployments run exclusively with `postgres` reader and `shadow` writer.*

Independent review must approve the exact PR head. Merge the PR to `dev`, then
deploy that merge SHA through the existing nonprod workflow under the shared
lease.

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

Required (during historical cutover): hosted BFF source SHA equals the merged SHA; both container image IDs
are recorded; the migration and compose checksums are recorded; reader backend posture matches the active cutover stage.

## 3. Additive migration and reviewed fresh legacy baseline (Historical Cutover Phase)

*Note: Historical baseline migration was executed once to populate PostgreSQL from the intact JSON bundle before retiring JSON readers.*

Apply only the additive migration. Do not run a down migration or truncate the
canonical source or projection schema. During initial cutover, before importing,
prove the relational writer is `disabled`, all relational projection tables
and controllers are empty, and size/mtime remain unchanged across the checksum
pass.

The reviewed immutable bundle identity is:

```text
trade_journey_events.json  2102154569 bytes  sha256:774e6c3b8871704e6d19cfc2db9783b7c7b97702129f97208d50ea929321c7bb
loop_runs.json               317514745 bytes  sha256:6ba7cde66e7d7b6c1a4bd6352e7fd2a6918f7a7b0f651b76e7c0b204598baacc
controller_state.json       3170778131 bytes  sha256:c138b59620b6765c3e81294ff346d33790a5d19205ce1299ba8fabae2c08cab0
```

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
    --legacy-controller-state /data/bff/lifecycle-projection/controller_state.json \
    --expected-legacy-sha256 c138b59620b6765c3e81294ff346d33790a5d19205ce1299ba8fabae2c08cab0 \
    --legacy-checkpoint 7100730 \
    --legacy-controller-deployment-sha 97945de7c5193baa9832f6c02674714d889577b9 \
    --snapshot-path /data/bff/lifecycle-projection/cutover-legacy-baseline.snapshot.json \
    --evidence-out /data/bff/lifecycle-projection/cutover-migrate-result.json
```

Re-run the migration command once. The second run must resume from the durable
snapshot without new derived mutation or data loss. The bounded result must
report `import_complete=true`, `live_controller_seeded=true`,
`live_controller_mode=recovery`, `live_controller_status=repair_only`, and
`accepted_live=false`. The importer streams one folded aggregate at a time;
it must not load the multi-GiB file into memory.

Before the first aggregate import or controller seed, the importer streams the
`controller` member from that same checksummed file and requires exact matches
for controller ID, reviewed checkpoint, and reviewed deployment SHA. It also
requires integer-zero backlog and quarantine count, boolean `accepted_live=true`,
and null `last_error`; missing, string-coerced, or mismatched values fail closed
without a projection transaction or snapshot write.

Capture the migration controller separately from the live controller. Its ID is
`canonical-lifecycle-projector-migrate`; it never grants live-read authority.
The seeded live controller exists only to bridge the accepted pre-truncation
checkpoint. Shadow polling, not the baseline import, is what may later change
it to `live/ready/accepted_live=true`.

## 4. Pre-switch parity, capacity, and security gates

Run parity while the final JSON bundle is still the accepted reader and before
new relational live events can diverge from that frozen recovery snapshot.

```bash
LIFECYCLE_PROJECTION_DSN='postgresql://pantheon_app:<redacted>@postgres:5432/pantheon' \
docker compose -p pantheon -f docker-compose.yml exec -T \
  -e LIFECYCLE_PROJECTION_DSN loop-run-projector-scheduler \
  python -m scripts.lifecycle_projector_parity \
    --legacy-controller-state /data/bff/lifecycle-projection/controller_state.json \
    --expected-legacy-sha256 c138b59620b6765c3e81294ff346d33790a5d19205ce1299ba8fabae2c08cab0 \
    --legacy-checkpoint 7100730 \
    --out /data/bff/lifecycle-projection/pre-switch-parity.json

docker cp pantheon-loop-run-projector-scheduler-1:/data/bff/lifecycle-projection/pre-switch-parity.json \
  "${CUTOVER_EVIDENCE_ROOT}/pre-switch-parity.json"
```

Required:

- streaming parity revalidates the checksummed controller's exact ID and
  checkpoint plus the same safe backlog/quarantine/live/error fields before it
  reads PostgreSQL, and records the source controller deployment SHA;
- stage, journey, loop, identity, and quarantine category hashes recorded;
- source and PostgreSQL row counts match for every category, using the bounded
  streaming multiset digest rather than loading or sorting the full bundle;
- `unexplained_mismatch_count=0` and backlog zero;
- `LIFECYCLE-PROJ-CAPACITY-001` reviewed evidence still passes its stated
  resource, fault, and latency thresholds;
- scoped reader/page-token and live-sensitive masking tests pass;
- migration/runtime credentials are identified separately and no source
  `UPDATE`/`DELETE` authority is granted to the runtime worker.

## 5. Shadow activation with reader unchanged (Historical Cutover Phase)

*Note: During historical cutover, shadow writer was activated while the reader remained on JSON until parity verification passed.*

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
and `bff-data` volume still exist. (During initial cutover, BFF posture temporarily reported `trade_journey_reader_backend=json` before Section 6 switch; post-retirement, BFF requires `trade_journey_reader_backend=postgres`.)

## 6. Authorized paper canary and all-dev read switch (Historical Cutover Phase)

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

## 8. Postgres-only deployment rollback and forward recovery

Because legacy JSON readers and writers are fully retired, rollback is performed
at the image and code deployment level (rolling back to the prior stable container
image or git SHA) rather than switching back to retired JSON files:

```bash
# Recreate container services with the previous validated image / commit SHA
docker compose -p pantheon -f docker-compose.yml up -d \
  --force-recreate --no-deps operator-bff loop-run-projector-scheduler
```

Required:
- No database migration or data destruction is performed during service recreate.
- PostgreSQL projection tables and controller states remain intact.
- Upon restart, the relational worker verifies schema, connects, and resumes from
  the recorded checkpoint sequence.
- BFF `/readyz` validates `dependencies.lifecycle_projector.reader_backend=postgres`
  and reports healthy status when backlog is 0.

Forward recovery re-deploys the validated target SHA, verifies backlog zero,
and confirms `/readyz` health and authenticated hosted readbacks. Receipt count,
maximum ingested sequence, and canary journey/loop records must be unchanged
or monotonically advanced—never lower or duplicated.

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

## 10. Post-soak legacy JSON retirement (`LIFECYCLE-PROJ-RETIRE-001`)

Following completion of the 7-day target-dev soak with zero parity, capacity,
freshness, or security violations, legacy JSON files are retired and pruned
through an allow-listed, checksummed process.

### Operational invariants:
- **Default compose configuration**: `operator-bff` uses `PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND=postgres` and `loop-run-projector-scheduler` uses `LIFECYCLE_PROJECTOR_WRITER_BACKEND=shadow`. Legacy JSON generator and retention environment variables are removed.
- **Fail-closed readiness**: If any component attempts to select a legacy JSON reader or writer, readiness fails closed immediately with `legacy_reader_retired:json` or an explicit configuration exception.
- **Dry-run inventory first**: Run `scripts/lifecycle_projector_legacy_retire.py --dry-run` to inventory all candidate files and verify SHA-256 checksums before any file mutation.
- **Governed Human/Ops approval record required**: Execution requires `--execute`, `--dry-run-manifest <PATH>`, and `--approval-record <PATH>` pointing to an authoritative Human/Ops approval record located within the immutable governed status root (`$PANTHEON_STATUS_ROOT` / canonical task state identity root). The record binds the exact dry-run inventory digest, root, action, recovery posture, quarantine destination, and an authoritative cryptographic HMAC-SHA256 signature generated by Human/Ops. Caller-controlled directory redirects or forgeable unkeyed hashes cannot bypass governed approval.
- **Production CLI root and key governance**: The production CLI strictly targets `/data/bff/lifecycle-projection`. Custom root overrides, test-mode bypasses, and caller-selected CLI verification keys are strictly prohibited; signature verification keys are resolved strictly from verified authoritative supervisor protected key files (`human-ops-signing.key` or `authority-signing.env`), and caller-controlled environment variable fallback (including unverified task state identities or attacker keys when authoritative supervisor config is absent) fails closed.
- **Quarantine over raw deletion**: In default retirement mode (`--action archive` or `--action quarantine`), obsolete generation directories and root files are moved into a quarantine folder (`/data/bff/lifecycle-projection/quarantine`), preserving recovery capability if needed.

### Step 1: Execute dry-run scan
```bash
python3 scripts/lifecycle_projector_legacy_retire.py \
  --root /data/bff/lifecycle-projection \
  --dry-run \
  --output /var/tmp/pantheon-evidence/LIFECYCLE-PROJ-RETIRE-001/dry-run-manifest.json
```

### Step 2: Human/Ops review & approval record generation
Human/Ops reviews the dry-run manifest and generates an authoritative signed approval record `approval-record.json` inside the governed status root (`$PANTHEON_STATUS_ROOT/docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-RETIRE-001/`):
```json
{
  "schema_version": "pantheon.lifecycle-projector-retirement-approval.v1",
  "task_id": "LIFECYCLE-PROJ-RETIRE-001",
  "actor": "Human/Ops",
  "approved": true,
  "approved_at_utc": "2026-08-22T18:30:00Z",
  "action": "quarantine",
  "recovery_possible": true,
  "root_path": "/data/bff/lifecycle-projection",
  "quarantine_path": "/data/bff/lifecycle-projection/quarantine",
  "inventory_sha256": "<dry-run-inventory-sha256>",
  "signature_sha256": "<human-ops-hmac-sha256-signature>",
  "notes": "Approved by Human/Ops after reviewing exact dry-run inventory digest."
}
```

### Step 3: Governed retirement execution
```bash
python3 scripts/lifecycle_projector_legacy_retire.py \
  --root /data/bff/lifecycle-projection \
  --action quarantine \
  --execute \
  --dry-run-manifest /var/tmp/pantheon-evidence/LIFECYCLE-PROJ-RETIRE-001/dry-run-manifest.json \
  --approval-record "${PANTHEON_STATUS_ROOT}/docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-RETIRE-001/approval-record.json" \
  --output /var/tmp/pantheon-evidence/LIFECYCLE-PROJ-RETIRE-001/retirement-receipt.json
```

### Step 4: Postgres-only health validation
```bash
curl -fsS http://127.0.0.1:18001/readyz
curl -fsS http://127.0.0.1:18001/bff/version
```

Verify that `dependencies.lifecycle_projector.reader_backend` is `postgres`, `status` is `ready`, `backlog` is `0`, and all public BFF endpoints respond normally without legacy JSON fallback.

---

## Residual Risk Register (Post-Retirement)

| Risk Item | Likelihood | Impact | Mitigation & Safeguards |
| :--- | :--- | :--- | :--- |
| **Postgres connection exhaustion / saturation** | Low | Medium | Dedicated connection pooling via `ProjectionStore`; bounded batching (`LIFECYCLE_PROJECTOR_BATCH_SIZE=500`); fail-closed `/readyz` alerts immediately if latency or connection drop occurs. |
| **Telemetry ingestion backlog spike** | Low | Low | Incremental relational projector processes batches with cursor checkpointing; high-watermark delta is continuously monitored in `/readyz` (`backlog == 0` invariant). |
| **Corrupted projection row / quarantine event** | Very Low | Medium | Strict validation on every lifecycle event; anomalous events are recorded in `quarantine` table without blocking subsequent events; `/readyz` flags non-zero quarantine count. |
| **Recovery after node or pod restart** | Low | Low | State is durable in PostgreSQL tables (`trade_journey_projections`, `trade_journey_controller_state`); worker restarts automatically reconnect and resume from checkpoint sequence. |
| **Accidental invocation of legacy reader** | Very Low | Low | Legacy JSON reader paths removed from code and compose; configuring `json` fails closed with 503 degraded status. |
