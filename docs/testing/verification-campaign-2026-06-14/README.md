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

| 15 | Pagination correctness & cursor robustness | [round-15-plan.md](round-15-plan.md) | [round-15-results.md](round-15-results.md) | PASS (complete, no dup/gap; cursor fuzz no 500); O5 scale note |

| 16 | Broad query-param fuzz (500-hunt + injection) | [round-16-plan.md](round-16-plan.md) | [round-16-results.md](round-16-results.md) | F12: audit from_ts/to_ts 500 on every value (NameError) — fixed; H2 injection PASS |
| 17 | Static undefined-symbol audit | [round-17-plan.md](round-17-plan.md) | [round-17-results.md](round-17-results.md) | PASS (F12 sole instance in 655 files); guard test |
| 18 | Header + parameterized-route query fuzz | [round-18-plan.md](round-18-plan.md) | [round-18-results.md](round-18-results.md) | PASS (0 5xx) |
| 19 | Graceful degradation & signal consistency | [round-19-plan.md](round-19-plan.md) | [round-19-results.md](round-19-results.md) | PASS (F2 generalized: 0 false-green/722 surfaces) |
| 20 | Phase-2 close-out: regression consolidation | [round-20-plan.md](round-20-plan.md) | [round-20-results.md](round-20-results.md) | PASS; [SUMMARY-PHASE2.md](SUMMARY-PHASE2.md) |

**Phase 2 close-out (rounds 11–20):** [SUMMARY-PHASE2.md](SUMMARY-PHASE2.md) — 1
defect fixed (F12), findings F10/F11/F13/O4-O5, 3 regression test files; entire
BFF input surface fuzzed (only 500 anywhere was F12).

### Phase 3 — fleet, edge, security hardening, systemic bug classes (rounds 21–35)

| # | Theme | Plan | Results | Fix |
|---|---|---|---|---|
| 21 | Fleet route-resolution audit | [round-21-plan.md](round-21-plan.md) | [round-21-results.md](round-21-results.md) | PASS (21 services, 0 shadow/dup) |
| 22 | Non-BFF fleet input 500-hunt | [round-22-plan.md](round-22-plan.md) | [round-22-results.md](round-22-results.md) | PASS (21 services, 0 500s) |
| 23 | CORS configuration correctness | [round-23-plan.md](round-23-plan.md) | [round-23-results.md](round-23-results.md) | PASS (exact-match allowlist, no bypass) |
| 24 | BFF security response headers | [round-24-plan.md](round-24-plan.md) | [round-24-results.md](round-24-results.md) | F14: no security headers — fixed (SSE-safe) |
| 25 | Edge security headers (Caddy) | [round-25-plan.md](round-25-plan.md) | [round-25-results.md](round-25-results.md) | F15: FE/edge headers — fixed (caddy validate) |
| 26 | Canonical state cross-consistency | [round-26-plan.md](round-26-plan.md) | [round-26-results.md](round-26-results.md) | PASS |
| 27 | Request body-size limit (DoS) | [round-27-plan.md](round-27-plan.md) | [round-27-results.md](round-27-results.md) | F16: no body limit — fixed (10MB edge) |
| 28 | Complete fleet audit (deferred services) | [round-28-plan.md](round-28-plan.md) | [round-28-results.md](round-28-results.md) | PASS (all 26 services clean) |
| 29 | Error-handling discipline | [round-29-plan.md](round-29-plan.md) | [round-29-results.md](round-29-results.md) | PASS (0 bare except; O6) |
| 30 | Python footguns | [round-30-plan.md](round-30-plan.md) | [round-30-results.md](round-30-results.md) | PASS (0 mutable defaults; O7) |
| 31 | Naive/aware datetime mixing | [round-31-plan.md](round-31-plan.md) | [round-31-results.md](round-31-results.md) | F17: research-analyses sort 500 — fixed |
| 32 | Generalize F17 across read_store | [round-32-plan.md](round-32-plan.md) | [round-32-results.md](round-32-results.md) | F18: 20 more sort-key 500s — fixed + guard |
| 33 | Fleet-wide aware/naive sort audit | [round-33-plan.md](round-33-plan.md) | [round-33-results.md](round-33-results.md) | F19: search retriever sort 500 — fixed |
| 34 | ZeroDivisionError audit | [round-34-plan.md](round-34-plan.md) | [round-34-results.md](round-34-results.md) | PASS (reachable divisions guarded; O8) |
| 35 | Phase-3 close-out: consolidation + summary | [round-35-plan.md](round-35-plan.md) | [round-35-results.md](round-35-results.md) | PASS; [SUMMARY-PHASE3.md](SUMMARY-PHASE3.md) |

**Phase 3 close-out (rounds 21–35):** [SUMMARY-PHASE3.md](SUMMARY-PHASE3.md) — 6
defects fixed (F14–F19), findings O6/O7/O8, 5 regression test files; whole-fleet
route/input audits clean, security/edge hardened, systemic aware/naive sort-key
500 class (22 sites) closed.

## Environment under test

- Dev FE: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- Dev BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- Stub auth (dev): `Authorization: Bearer op-dev:admin:mfa` (any role-bearing
  stub token; dev BFF runs stub auth)
