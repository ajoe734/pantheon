# Master System Verification Program (10-round, deepening + broadening)

Goal: verify every direction in the system-verification inventory, archive each
round's plan + results, fix gaps via the normal dev workflow (worktree -> PR ->
CI -> merge -> deploy), then draft a deeper/broader plan. Repeat x10.

## Verification inventory (7 directions)
- A. Left half / real strategy pipeline (research->distill->experiment->approved
  artifact->deploy->bind). Biggest gap: everything has run on rescue placeholder
  bindings (artifacts don't exist).
- B. Governance/safety state machines: paper->canary->live promotion (#12),
  evolution lifecycle (#13), kill-switch/safe-mode (#14), rollback/position (#15),
  delivery closure (#16). None exercised live.
- C. Real broker/market integration (Shioaji TW / US sandbox; real market data;
  P0 activation lift). Currently broker paper+live disabled, mock adapter, synthetic data.
- D. Cross-cutting non-functional: security (21 dependabot vulns, real OIDC/JWT
  strict path), data consistency/persistence, saga/cross-service, concurrency/idempotency,
  HA/resilience, performance/backpressure, disaster recovery/alerting.
- E. Operational/deployment integrity: image drift, reconciler durability, CI gate
  coverage, config drift.
- F. Frontend & e2e (operator console renders live data; Playwright/integration suites).
- G. Test quality / multi-env (stub vs real behaviour; staging/prod parity).

## Verified before this program (prior sessions, PRs #1523-#1536)
Control plane/BFF (443 routes, fail-closed), ~125 stale contract tests fixed,
9 service suites (~600 tests) green, execution half proven (paper fills, fail-closed
broker), right half closed+deployed+verified (fill projection + synthetic market data
+ BFF trade visibility).

## Rounds
| # | Direction | Focus | Status |
|---|-----------|-------|--------|
| V1 | E | deploy-drift detectability + audit tool + git-SHA image labels | shipped |
| V2 | D | dependency CVE triage + mlflow 3.10.1->3.11.1 bump | shipped (#1539) |
| V3 | G | service-suite CI verify gate (9 suites, 607 tests green) | shipped (#1544) |
| V4 | E | canonical-doc integrity gate (34 canonical_files + 74 map refs) | shipped (#1546) |
| V5 | D | secret-leak scan gate (allowlisted; no real secrets) | shipped (#1548) |
| V6 | D | dependency-hygiene audit (158/176, 90% unpinned) | shipped (#1551) |
| V7 | E | OpenAPI structural quality gate (447 paths/497 ops clean) | shipped (#1552) |
| V8 | E | container health/restart/non-root hardening audit | shipped (#1554) |
| V9 | E/G | live not-found error-path probe; found+fixed deployed 500 (deploy drift) | shipped (#1557) |
| V10 | A/E | live loop-liveness/OODA-observability probe (capstone) | shipped |

## Consolidation (V1-V10)

**Tooling shipped** - the program leaves behind a reusable verification harness:
`audit_deploy_drift.sh` (V1), `audit_canonical_docs.py` (V4), `audit_secret_leak.sh`
(V5), `audit_dependency_hygiene.py` (V6), `audit_openapi_quality.py` (V7),
`probe_bff_notfound_paths.py` (V9), `probe_loop_liveness.py` (V10), plus a
`run-acceptance.sh verify` mode + `verify-suites` CI job (V3). Static audits
(drift/docs/secrets/deps/openapi/containers) plus runtime probes (not-found
error paths, loop liveness) now cover both "is the code/config sane" and "does
the deployed thing actually behave".

**Concrete defects found + fixed** - V2 stale mlflow pin; V9 a *live* HTTP 500 on
`/bff/persona-league/{id}` caused by deploy drift (stale `ErrorCode.OBJECT_NOT_FOUND`
in the running container while the repo was already fixed) - caught by an
error-log scan, cleared by redeploy.

**Honest capstone finding (V10)** - infrastructure is up (BFF surfaces 200,
personas registered with OODA stages, runtime bindings active) but the **v5
loop-run ledger is empty (0 runs, 0 OODA packets)** and two surfaces are degraded
(`strategy_health` and `ooda_control_room_status` = `unavailable`). So the loops
are **not demonstrably live via the v5 ledger**: normal operation is provisioned
but not yet *proven* through the canonical loop/OODA telemetry. This is the single
most important open thread for any follow-on campaign - distinct from the P0
build-side work that populates these projections.

**Non-duplication discipline** - each round checked `.orchestrator/task-briefs/`
and existing scripts first; V4 pivoted twice and V9's code fix/guard was *dropped*
on discovering `VERIFY-SYS-CAMPAIGN-R3` already owned it (V9's distinct value became
the deploy-drift detection + runtime probe).
