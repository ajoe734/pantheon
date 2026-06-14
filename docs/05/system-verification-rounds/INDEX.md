# System Verification Rounds — Index

Goal: iteratively verify **all** Pantheon system flows, fix every gap found, ship the
fix through the canonical dev workflow (commit -> PR -> merge dev -> publish where no human
gate blocks), archive each round, then run the next round against a **different path**.
Target: 100 rounds.

## Definition of "normal operation" (acceptance basis)

Pantheon normal operation is NOT "services return 200". It is: the multi-persona OODA
**closed loops** can each actually run, under the safety invariants, and be proven by
telemetry/lineage. The canonical loop inventory (the acceptance checklist):

11 main loops (`LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` Section 6):
1 Source Ingestion; 2 Strategy Distillation; 3 Alpha Replication; 4 Persona Teaching;
5 Human Imitation; 6 Consultation; 7 Promotion/Deployment; 8 Capital Pool Execution
(LEAN, the only true continuous loop); 9 Telemetry/Reconciliation; 10 Evolution;
11 BFF Health Monitoring.

Plus 5 governance/safety state-machine loops layered on top:
12 paper->canary->live promotion ladder (`PAPER_CANARY_LIVE_POLICY.md`);
13 Evolution decision lifecycle (`EVOLUTION_REVIEW_AND_THRESHOLDS.md`);
14 Kill-switch + Safe-mode (`KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`);
15 Rollback + Position reconciliation (`ROLLBACK_AND_POSITION_SEMANTICS.md`);
16 Delivery closure loop-state (`DELIVERY_CLOSURE_AND_LOOP_STATES.md`).

Each loop is tracked as a 3-state cell: **design has / API has / actually runs**.

## Live dev surface under test

- dev BFF: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io (operator-bff v0.2.0)
- dev FE:  https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
- (pre-migration IP 34.81.75.241 is dead; see Round 001)

## Rounds

| # | Path tested | Gap found | Fix | Status |
|---|-------------|-----------|-----|--------|
| 001 | Loop #7 / #11 - FE<->BFF deployment-config & integration-probe integrity | Test/probe/CI/env defaults still pointed at dead pre-migration BFF IP 34.81.75.241 | Repoint 10 operational files to live 35.201.239.38 | shipped -> dev |
| 002 | Loops #1-#16 — parallel contract-coverage audit (4 sub-agents) vs live openapi+probe | Confirmed 404 feature gaps (allocation/deployment-policies/kill-switch-activate); systemic 422-before-401 on ~81 GET endpoints; 1 sub-agent claim (canary enum) FALSIFIED | Audit archived; substantive gaps escalated (design decisions, not auto-fixed); no 5xx, uniform fail-closed | shipped -> dev |
| 003 | Loop #4 Persona Teaching - run BFF contract suite | trainer/sessions 422-before-401 (fail-closed ordering); 8 pre-existing RED tests (stale `["detail"]["error"]` envelope) | Fixed auth ordering + 2 regression tests; corrected 9 assertions; trn002 8-fail -> 27 pass | shipped -> dev |
| 004 | BFF contract suite at scale (33 files) | 70 tests RED on dev (not in CI gate); layer-1 stale error-envelope path; layer-2 stale error-CODE constants | Swept 81 envelope assertions (provably regression-free, 70->63 fail / +7 green); error-code drift escalated to R005 | shipped -> dev |
| 005 | BFF contract error-CODE drift (38 files) | tests assert 9 removed error-code names (INVALID_TOKEN/OBJECT_NOT_FOUND/INSUFFICIENT_ROLE/...); code returns canonical catalog | Verified mapping from failure diffs; 73 literal fixes; 77->36 fail / +41 green, zero regression; layer-3 escalated to R006 | shipped -> dev |
| 006 | BFF residual two-line envelope unwrap (10 files) | `detail = resp.json()["detail"]` form missed by R004; KeyErrors on canonical `{"error"}` root | Precise `.json()["detail"]`->`.json()` (26 fixes); 29->6 fail / +23 green, zero regression; legit nested detail preserved; v5 confirm-token (6) escalated as fixture drift (code correct) | shipped -> dev |
