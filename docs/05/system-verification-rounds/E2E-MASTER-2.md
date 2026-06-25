# E2E Business-Flow Verification Campaign — Second Series (R11–R20)

Second 10-round campaign, extending E2E-R1..R10 ([E2E-MASTER.md](E2E-MASTER.md)).
Same loop each round: plan → live-verify against deployed dev → archive →
CI-gated verifier (or a code fix) → commit/push/merge.

## How to run the live checks

```
BFF_BASE=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
BFF_TOKEN=op-dev:admin:mfa \
  scripts/run_e2e_verifiers.sh
```

All `scripts/test_verify_e2e_*.py` are gated in CI via the single
`e2e-verifier-suite` glob step in `run-acceptance.sh` (added this round — no more
per-round wiring). The action-catalog (R11) and persistent-dedup (R16) tests run
in the BFF / lean-runtime suites.

## Rounds

| # | Flow / target | Verifier / change | Live finding | PR |
|---|------|-------------------|--------------|----|
| R11 | operator command safety | `test_action_catalog_safety_invariants.py` | CRITICAL+destructive actions stay gated (confirm/two-man/approval) — holds | #1640 |
| R12 | sentinel finding attribution | `verify_e2e_sentinel_integrity.py` + **live BFF outage fix** | findings well-formed+attributable; restored BFF stuck in `Created` (502) | #1641 |
| R13 | BFF surface-status | `verify_e2e_surface_status_consistency.py` | agora/journal serves 3 items but reports source missing (contradiction) | #1642 |
| R14 | telemetry pipeline | `verify_e2e_telemetry_pipeline_health.py` | buffer drained, 0 rejected, pressure normal — healthy | #1644 |
| R15 | deployment lifecycle | `verify_e2e_deployment_lifecycle.py` | 15/15 plans current_stage=none while binding active (never advances) | #1645 |
| R16 | signal dedup durability | **code fix** in pending_signal_store + signal_consumer | persisted dedup across restarts (fixes R6) | #1646 |
| R17 | auth boundary | `verify_e2e_auth_boundary.py` | 11/11 protected endpoints reject missing/empty auth — no bypass | #1647 |
| R18 | runtime-surface fields | `verify_e2e_runtime_state_coherence.py` | stage/status/binding agree across both runtime surfaces — holds | #1648 |
| R19 | telemetry coverage | `verify_e2e_telemetry_coverage.py` | 16/16 active runtimes report a summary — none blind | #1649 |
| R20 | consolidation | glob CI wiring + extended runner + this index | one-step CI gate for all verifier tests | — |

## Cross-cutting conclusions (R11–R20)

1. **Safety controls are intact and now regression-gated:** operator command
   confirmation/two-man/approval (R11), auth boundary (R17), telemetry ingest
   validation (R7) and pipeline health (R14). One durability fix shipped:
   cross-restart signal dedup (R16, fixes R6); one earlier safety bug fixed (R4).
2. **The right-half telemetry path is solid:** real fills flow (R2), pipeline
   healthy (R14), 100% runtime coverage (R19), runtime surfaces coherent (R18).
3. **The remaining defects are read-model / lifecycle bookkeeping, not safety:**
   surface-status contradiction (R13 agora journal), deployment `current_stage`
   never advancing (R15), persona-health disconnect (R8). These mislead the
   operator view but do not breach safety; all flagged, none faked.
4. **One live outage resolved mid-campaign** (R12): operator-bff stuck in docker
   `Created` serving 502, restored with `docker start`.

## Standing follow-ups

- Advance deployment `current_stage` on activation (R15); reconcile the
  agora_journal surface-status with its data (R13); reflect active-fleet personas
  in persona-health (R8); the first-series upstream data gaps (E2E-MASTER.md).
