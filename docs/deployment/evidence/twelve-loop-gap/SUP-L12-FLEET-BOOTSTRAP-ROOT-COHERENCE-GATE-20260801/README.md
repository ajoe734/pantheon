# SUP-L12-FLEET-BOOTSTRAP-ROOT-COHERENCE-GATE-20260801 Evidence Summary

## Overview
- **Task ID**: SUP-L12-FLEET-BOOTSTRAP-ROOT-COHERENCE-GATE-20260801
- **Title**: Gate fleet bootstrap on exact runtime root coherence
- **Owner**: Antigravity
- **Reviewer**: Claude

## Truthful Verification Summary

| Item | Requirement | Status | Captured Detail / Command |
|---|---|---|---|
| 1 | Supervisor Process Binding | PASSED | `ps aux \| grep supervisor`: single process running with live config `/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json` |
| 2 | Source Root & Worker Runner Root | PASSED | `live-supervisor-mainroot-config.json`: source root & runner root set to `/home/lupin/pantheon-ci-deploy/dev-root` |
| 3 | Command Root Content-Addressed | FAILED | `PANTHEON_COMMAND_ROOT` is `/home/lupin/pantheon-ci-deploy/dev-root`, not a `/command-runtimes/<40-hex>` directory |
| 4 | worker_worktrees.source_root Config | FAILED | Config lacks `worker_worktrees.source_root` key |
| 5 | Status Root Isolation | FAILED | Status root `/home/lupin/pantheon` acts as Git worktree source authority for worker worktrees |
| 6 | Watchdog Absolute Config Binding | PASSED | Absolute path `/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json` used |
| 7 | Stale Token Clearance | FAILED | `SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731` remains in status `todo` |
| 8 | Canary Workers Execution | PASSED | Verified Antigravity & Claude worker runs with heartbeats and status files |
| 9 | Parity & Lease Check | PASSED | No duplicate worker leases or projection mismatches |
| 10 | Config SHA Tracking | PASSED | SHA `f90e0aae6cb5e86f18b20db9f30bc834f6115745` tracked |
| 11 | Product Task Gate | PASSED | 28 L12 product tasks held until gate passes |
| 12 | Attestation | PASSED | Governed execution through scripts |

## Gate Conclusion
Fleet admission remains **FAIL-CLOSED** because items 3, 4, 5, and 7 do not satisfy full runtime coherence requirements.
