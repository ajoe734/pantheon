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
