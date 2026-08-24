# PFG-DATA-TELEMETRY-BASELINE-20260822: Canonical Telemetry Baseline Disposition

## Overview

Following the deployment telemetry prune scope repair (**PFG-DATA-TELEMETRY-PRUNE-20260822** / **SD-DATA-01** merged in commit `5517afdda923774c1d5f2c80688c76827dae5f91`), this task establishes the canonical telemetry history baseline disposition per **SD-DATA-02** and **SA-DATA-02**.

## Key Decisions and Invariants

1. **Non-Destructive Baseline Capture**:
   - `scripts/capture_canonical_telemetry_baseline.py` inspects `public.telemetry_events` in PostgreSQL using a deterministic, read-only query hashed with SHA-256 (`cbb98d00313c8d8d2b26b9f2a4440cbecbd1c28fd87fda520746ce1724e33239`).
   - Captures row count, min/max `created_at`, `source_high_watermark` (max `ingested_seq`), deployment git SHA, and known history boundary.

2. **Authoritative Backup Candidate Inventory**:
   - Audited GCP disk snapshots, PostgreSQL database dumps, Docker persistent volumes, legacy derived Lifecycle JSON files, and test fixtures.
   - Concluded honestly that pre-boundary source history prior to the truncate event is **irrecoverable** in source.
   - Post-boundary telemetry repopulated starting from `2026-08-22T11:48:48+00:00` (ingested sequence `7122484`) is active, continuous, and preserved intact.
   - Overall history disposition is recorded as **`partial`**.

3. **Fail-Closed Complete-History Attestation**:
   - A `complete` disposition now requires `recovery_source_attestation`; a URI-shaped string alone is rejected.
   - The attestation binds the exact source identity and independently observed immutable version/digest to the baseline row count, timestamp range, high watermark, canonical query hash, and a zero-missing event-ID comparison.
   - A hash-bound JSONL event manifest is read independently; the validator rejects duplicate IDs and recomputes its unique count, min/max timestamp, high watermark, and sorted event-ID comparison SHA-256 against the baseline.
   - GCS objects are resolved with `gcloud` and bound to generation, metageneration, and object SHA-256 metadata; fully-qualified READY GCP snapshots are bound to immutable resource metadata; local PostgreSQL dumps and source-ledger proofs must exist as regular non-symlink files and are hashed directly.
   - Bare digests, short snapshot names, logical dump URIs, nonexistent sources, mismatched attestation identities/digests, and incomplete event comparisons fail closed.

4. **No Synthetic Source Events (AD-03 / SD-DATA-02 Compliance)**:
   - Prohibits importing or synthesizing secondary `lifecycle_projection.json` data into `public.telemetry_events`.
   - Legacy JSON remains derived read-model evidence only and is never disguised as canonical source truth.

5. **Unblocks `LIFECYCLE-PROJ-CUTOVER-001`**:
   - The migration start checkpoint is unambiguously defined at the post-boundary baseline (`ingested_seq: 7122484`, `min_created_at: 2026-08-22T11:48:48+00:00`).
   - Relational Trade Journey backfill and reader cutover can safely proceed against real canonical source telemetry.

## Verification

```bash
# 1. Run full unit and contract test suite
.venv-pantheon/bin/python3 -m pytest -v scripts/test_capture_canonical_telemetry_baseline.py

# 2. Capture baseline artifact against dev PostgreSQL
.venv-pantheon/bin/python3 scripts/capture_canonical_telemetry_baseline.py   --deployment-sha 5517afdda923774c1d5f2c80688c76827dae5f91   --history-disposition partial   --known-history-start 2026-08-22T11:48:48+00:00   --out docs/deployment/evidence/product-functional-closure/PFG-DATA-TELEMETRY-BASELINE-20260822/baseline.json   --candidate-inventory-out docs/deployment/evidence/product-functional-closure/PFG-DATA-TELEMETRY-BASELINE-20260822/backup_candidates.json

# 3. Validate captured baseline artifact against schema
.venv-pantheon/bin/python3 scripts/capture_canonical_telemetry_baseline.py   --validate-file docs/deployment/evidence/product-functional-closure/PFG-DATA-TELEMETRY-BASELINE-20260822/baseline.json

# 4. Verify evidence checksums from repository root
sha256sum -c docs/deployment/evidence/product-functional-closure/PFG-DATA-TELEMETRY-BASELINE-20260822/evidence.sha256
```
