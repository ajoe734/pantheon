# System Verification Campaign — 2026-06-14

An independent, multi-round verification campaign over the live Pantheon dev
system and its supporting code/state planes. Each round produces an archived
**plan** and **results** document; any fixes follow the normal dev workflow
(isolated worktree off `origin/dev` → branch → patch → test → commit → push →
PR → merge `dev`).

The campaign deliberately goes **deeper and broader** each round.

## Operating rules

1. **No duplication.** Before each round, check the dedup ledger and the
   prior-art list below. A round must add a verification angle not already
   covered by existing docs or fleet tasks.
2. **Evidence-first.** Every claim in a results doc is backed by a captured
   probe (curl/test output, file inspection) with UTC timestamps.
3. **Fixes via dev workflow.** Code fixes land on a branch and PR, never edited
   directly in the orchestrator-churned main tree.
4. **Independence.** This campaign verifies; it does not re-implement the
   fleet's in-flight build-gap work (signal producers, market data, real
   artifacts). When verification surfaces a fleet-owned gap, it is recorded as
   a finding, not re-worked here.

## Prior art reviewed (to avoid duplication)

| Area | Existing doc(s) | Date | This campaign's distinct angle |
|---|---|---|---|
| Runtime/reconciliation verification | `docs/deployment/runtime-verification-*` | 2026-04-28 | Live-stack control-plane health on 06-14, post-GCP-migration IPs |
| Cross-repo SD verify | `docs/reviews/2026-04-2x-cross-repo-sd-verify-001-*` | 2026-04-2x | N/A (sidecar acceptance) |
| BFF API gap audits | `docs/04/pantheon_bff_api_gap_*` | 2026-05-2x | Contract-surface liveness, not gap enumeration |
| FE/BE integration blueprint | `docs/testing/Pantheon_FE_BE_Integration_Test_Blueprint_2026-05-10.md` | 2026-05-10 | Live probes against deployed dev, not a test blueprint |
| CI verification | `docs/04/pantheon_sa/SA-18_v2_test_ci_verification_*` | — | Targeted service-suite runs, not CI design |

## Round ledger

| # | Theme | Plan | Results | Fix |
|---|---|---|---|---|
| 1 | Live dev stack reachability & control-plane health | [round-01-plan.md](round-01-plan.md) | [round-01-results.md](round-01-results.md) | OODA card false-green when source missing (PR #1540) |
| 2 | Contract-surface conformance breadth & ghost-route audit | [round-02-plan.md](round-02-plan.md) | [round-02-results.md](round-02-results.md) | `/api/v1/incidents/stream` SSE route un-shadowed (PR #1541) |
| 3 | Parameterized-route robustness (no unhandled 500s) | [round-03-plan.md](round-03-plan.md) | [round-03-results.md](round-03-results.md) | persona-league detail 500→404 + ErrorCode static guard (PR #1542) |
| 4 | Write-surface input robustness & error-envelope consistency | [round-04-plan.md](round-04-plan.md) | [round-04-results.md](round-04-results.md) | PASS (no defect); stub laxity O1/O2 documented (PR #1543) |
| 5 | SSE streaming substrate behavior | [round-05-plan.md](round-05-plan.md) | [round-05-results.md](round-05-results.md) | PASS; F6 deploy-lag for Round 2 fix flagged (PR #1545) |
| 6 | Authorization depth (RBAC) audit of write surface | [round-06-plan.md](round-06-plan.md) | [round-06-results.md](round-06-results.md) | PASS (133/136 write-gated); F7 read-gated creates flagged for product decision (PR #1549) |
| 7 | Test-suite import/collection & execution health | [round-07-plan.md](round-07-plan.md) | [round-07-results.md](round-07-results.md) | PASS (~1,150 tests green, 0 failures) (PR #1550) |
| 8 | Write-method robustness + orchestrator archive integrity | [round-08-plan.md](round-08-plan.md) | [round-08-results.md](round-08-results.md) | F8: archive indexer silently dropped legacy-id files — fixed |
| 9 | Systematic route-resolution audit (shadowing + dup registration) | [round-09-plan.md](round-09-plan.md) | [round-09-results.md](round-09-results.md) | PASS; F9 dup-registration hazard locked by guard test |
| 10 | Undocumented/hidden-route audit (live→documented) | [round-10-plan.md](round-10-plan.md) | [round-10-results.md](round-10-results.md) | PASS; no hidden mutating endpoints |

**Phase 1 close-out (rounds 1–10):** [SUMMARY.md](SUMMARY.md) — 5 defects fixed
(PRs #1540, #1541, #1542, #1553, #1555), 5 findings attributed to owners, 5
regression test files added.

### Phase 2 — semantics, auth, integration, resilience (rounds 11–20)

Targets the gaps Phase 1 did not reach: computed-value correctness (not shape),
real JWT/MFA auth (not stub), cross-service integration, concurrency/idempotency,
persistence, security depth, canonical-state consistency, pagination at scale,
and resilience.

| # | Theme | Plan | Results | Fix |
|---|---|---|---|---|

| 11 | Data/computation correctness (not shape) | [round-11-plan.md](round-11-plan.md) | [round-11-results.md](round-11-results.md) | PASS (aggregation math exact; O4 governance-default note) |
| 12 | Real JWT/MFA auth path (not stub) — attack matrix | [round-12-plan.md](round-12-plan.md) | [round-12-results.md](round-12-results.md) | PASS (fail-closed, alg-pinned); F10 no-exp hardening; attack-matrix test added |
| 13 | Idempotency-Key replay correctness | [round-13-plan.md](round-13-plan.md) | [round-13-results.md](round-13-results.md) | PASS (replay cached, conflict 409, key required) |
| 14 | Concurrency & idempotency durability | [round-14-plan.md](round-14-plan.md) | [round-14-results.md](round-14-results.md) | PASS (no intra-instance race; guard test); F11 per-process idempotency under HA |

## Environment under test

- Dev FE: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- Dev BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- Stub auth (dev): `Authorization: Bearer op-dev:admin:mfa` (any role-bearing
  stub token; dev BFF runs stub auth)
