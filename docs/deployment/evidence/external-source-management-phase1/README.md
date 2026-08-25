# External Data Source Management Phase-1 Hosted Acceptance Evidence

This package contains the authoritative hosted acceptance evidence, exact deployment identities, journey execution receipts, negative control assertions, store migration verification, and rollback evidence for **Phase 1 External Data Source Management (`SRCM-P1-HOSTED-ACCEPTANCE-20260824`)** governed by specification `SD_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md` (SD-SRCM-08).

## 1. Exact Deployment Pair

| Component | Repository | Deployed Commit SHA | Base URL | Verification Endpoint |
|---|---|---|---|---|
| Backend / BFF / Ingest | `ajoe734/pantheon` | `03757f0254fb48ea37098e3d9ab0176c006d4da5` | `https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io` | `GET /bff/version` |
| Frontend | `ajoe734/execute-plans` | `cc4007f7f78a31c73548ce85457af17a45a4c4b9` | `https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io` | `GET /deployment.json` |

### Deployment Drift Analysis

- **Frontend Manifest**: `https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io/deployment.json` serves commit `cc4007f7f78a31c73548ce85457af17a45a4c4b9` and references prior backend baseline `bffCommit: "40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0"`.
- **Live Backend Version**: `https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io/bff/version` serves commit `03757f0254fb48ea37098e3d9ab0176c006d4da5` with strict JWT auth posture.
- **Accepted Live Pair**: Both endpoints have been probed and verified live with exact SHA verification, negative control probes, and safe write defaults (`VITE_BFF_REAL_WRITES: "false"`).

## 2. Evidence Artifact Manifest

- [`evidence.json`](evidence.json): Root canonical evidence manifest fulfilling all SD-SRCM-08 acceptance requirements.
- [`deployment.json`](deployment.json): Exact deployment environment, SHAs, feature posture flags, and rollback defaults.
- [`hosted-acceptance-summary.json`](hosted-acceptance-summary.json): Summary of the 10 executed hosted journeys (without route mocks).
- [`journey-receipts.json`](journey-receipts.json): Redacted command receipts and execution evidence for all 10 journeys.
- [`negative-controls.json`](negative-controls.json): Safety invariants and negative test verifications (unauthorized rejection, stale revision, secret exposure prevention, egress deny, no-order/no-capital route isolation, provider degradation, OpenClaw phase-2 boundary).
- [`migration-rollout-rollback.json`](migration-rollout-rollback.json): Store migration idempotency, legacy projection snapshots, parity checks, and read-only rollback verification.

## 3. Ten Hosted Journeys (SD-SRCM-08 §11.7)

1. **Journey 1: Public/no-secret source create-disabled through browser** — Revision 1 `configured_disabled` desired state created.
2. **Journey 2: Validate and bounded canary** — Validated configuration and executed bounded canary (`max_records=5`), producing valid canary result and telemetry.
3. **Journey 3: SourceRecord/Evidence/Search readback** — Verified as-of search and evidence lineage from canary ingest.
4. **Journey 4: Enable and observed convergence** — Desired state `configured_enabled` converged with healthy observed state.
5. **Journey 5: Disable and reload persistence** — Desired state `configured_disabled` persisted across restart and reload.
6. **Journey 6: Duplicate command idempotency** — Duplicate command replay with same idempotency key returned identical receipt without side effects.
7. **Journey 7: Unauthorized and stale-revision rejection** — Non-operator role rejected (403); stale revision rejected (409 conflict).
8. **Journey 8: Credentialed test source with secret ref and no secret exposure** — Secret ref `ref://vault/...` resolved safely without inline secret leaks or response exposure.
9. **Journey 9: Provider failure / degraded UI** — Upstream provider timeout gracefully returned degraded envelope (200 with `surface.data_sources=degraded`) without uncaught exceptions.
10. **Journey 10: Rollback to read-only accepted artifact** — Rollback posture enforced `VITE_BFF_REAL_WRITES=false`, `SOURCE_MANAGEMENT_COMMANDS_ENABLED=0`, preserving all receipts and evidence.

## 4. Verification Command

Run the fail-closed verifier against the evidence directory or live endpoints:

```bash
python3 scripts/verify_external_source_management_acceptance.py
```
