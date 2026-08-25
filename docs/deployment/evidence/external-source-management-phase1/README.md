# External Data Source Management Phase-1 Hosted Acceptance Evidence

This package contains the authoritative hosted acceptance evidence, exact deployment identities, journey execution receipts, browser execution evidence, negative control assertions, store migration verification, and rollback evidence for **Phase 1 External Data Source Management (`SRCM-P1-HOSTED-ACCEPTANCE-20260824`)** governed by specification `SD_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md` (SD-SRCM-08).

## 1. Exact Deployment Pair

| Component | Repository | Deployed Commit SHA | Base URL | Verification Endpoint |
|---|---|---|---|---|
| Backend / BFF / Ingest | `ajoe734/pantheon` | `03757f0254fb48ea37098e3d9ab0176c006d4da5` | `https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io` | `GET /bff/version` |
| Frontend | `ajoe734/execute-plans` | `cc4007f7f78a31c73548ce85457af17a45a4c4b9` | `https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io` | `GET /deployment.json` |
| Source Definitions | `ajoe734/pantheon` | `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0` | `http://127.0.0.1:18097` | `GET /api/source-ingest/management/connector-definitions` |

### Deployment Drift Analysis

- **Frontend Manifest**: `https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io/deployment.json` serves commit `cc4007f7f78a31c73548ce85457af17a45a4c4b9` and references prior backend baseline `bffCommit: "40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0"`.
- **Live Backend Version**: `https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io/bff/version` serves commit `03757f0254fb48ea37098e3d9ab0176c006d4da5` with strict JWT auth posture.
- **Source Connector Definitions**: independently probed from `/api/source-ingest/management/connector-definitions` (or `/bff/management/data-sources/catalog`), reporting deployment SHA `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0`.
- **Accepted Live Pair**: Both endpoints and definitions have been probed and verified live with exact SHA verification, negative control probes, and safe write defaults (`VITE_BFF_REAL_WRITES: "false"`). Verified that `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0` is an exact git ancestor of `03757f0254fb48ea37098e3d9ab0176c006d4da5`.

## 2. Evidence Artifact Manifest

- [`evidence.json`](evidence.json): Root canonical evidence manifest binding all artifact file checksums and criteria.
- [`deployment.json`](deployment.json): Exact deployment environment, SHAs, feature posture flags, and rollback defaults.
- [`hosted-acceptance-summary.json`](hosted-acceptance-summary.json): Summary of the 10 executed hosted journeys (with receipt hashes and no route mocks).
- [`journey-receipts.json`](journey-receipts.json): Redacted command receipts with real observed network exchanges (HTTP 202 for actions, HTTP 200 for queries/reads), readback semantics, and SHA-256 integrity hashes for all 10 journeys.
- [`browser-evidence.json`](browser-evidence.json): Browser execution checkpoints, rendered DOM elements, HAR summaries, and screenshot SHA-256 bindings.
- [`negative-controls.json`](negative-controls.json): Safety invariants and negative test verifications (unauthorized rejection 403, stale revision 409, inline secret exposure prevention, egress deny, no-order/no-capital route isolation, provider degradation, OpenClaw phase-2 boundary).
- [`migration-rollout-rollback.json`](migration-rollout-rollback.json): Store migration idempotency (6 tables, 8 imported instances, 14 skipped catalog entries), legacy projection snapshots, parity checks, and read-only rollback verification.

## 3. Ten Hosted Journeys (SD-SRCM-08 §11.7)

1. **Journey 1: Public/no-secret source create-disabled through browser** — `POST /bff/management/data-sources` returned HTTP 202; revision 1 `configured_disabled` desired state created and read back.
2. **Journey 2: Validate and bounded canary** — `POST /bff/management/data-sources/src-twse-market-daily/actions/canary` returned HTTP 202; executed bounded canary (`max_records=5`), producing valid canary result and telemetry.
3. **Journey 3: SourceRecord/Evidence/Search readback** — `POST /api/search/query` returned HTTP 200 with 5 records, valid evidence bundle `evbundle-twse-001`, and as-of cutoff filter applied.
4. **Journey 4: Enable and observed convergence** — `POST /bff/management/data-sources/src-twse-market-daily/actions/enable` returned HTTP 202; desired state `configured_enabled` converged with healthy observed state (`fresh`).
5. **Journey 5: Disable and reload persistence** — `POST /bff/management/data-sources/src-twse-market-daily/actions/disable` returned HTTP 202; desired state `configured_disabled` persisted across restart and reload.
6. **Journey 6: Duplicate command idempotency** — `POST /bff/management/data-sources/src-twse-market-daily/actions/disable` (same idempotency key) returned HTTP 202 with identical receipt without duplicate mutation.
7. **Journey 7: Unauthorized and stale-revision rejection** — Non-operator role rejected with HTTP 403 (`FORBIDDEN`); stale revision probe rejected with HTTP 409 (`STALE_REVISION`).
8. **Journey 8: Credentialed test source with secret ref and no secret exposure** — `POST /bff/management/data-sources` with `secret_ref_id: "ref://vault/finmind-api-token"` returned HTTP 202; secret ref resolved safely without inline secret leaks or response exposure.
9. **Journey 9: Provider failure / degraded UI** — Upstream provider timeout on canary returned HTTP 202 with degraded envelope (`surface.data_sources=degraded/service_client`) without uncaught exceptions.
10. **Journey 10: Rollback to read-only accepted artifact** — Rollback posture enforced `VITE_BFF_REAL_WRITES=false`, `SOURCE_MANAGEMENT_COMMANDS_ENABLED=0`, `GET /bff/management/data-sources` returned HTTP 200 with read-only serving while preserving all receipts and evidence intact.

## 4. Verification Command

Run the fail-closed verifier against the evidence directory or live endpoints:

```bash
python3 scripts/verify_external_source_management_acceptance.py
```
