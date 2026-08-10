# SUP-L12-FLEET-BOOTSTRAP-ROOT-COHERENCE-GATE-20260801 Evidence Summary

## Overview
- **Task ID**: SUP-L12-FLEET-BOOTSTRAP-ROOT-COHERENCE-GATE-20260801
- **Title**: Gate fleet bootstrap on exact runtime root coherence
- **Owner**: Antigravity
- **Reviewer**: Claude

## Truthful Verification Summary

| Item | Requirement | Status | Captured Detail / Command |
|---|---|---|---|
| 1 | Supervisor Process Binding | PASSED | `ps aux \| grep supervisor`: single process running bound to `/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json` |
| 2 | Source Root & Worker Runner Root | PASSED | `live-supervisor-mainroot-config.json`: source root & runner root set to `/home/lupin/pantheon-ci-deploy/dev-root` (ancestor of `origin/dev`) |
| 3 | Command Root Content-Addressed | FAILED | `PANTHEON_COMMAND_ROOT` is `/home/lupin/pantheon-ci-deploy/dev-root`, not a `/command-runtimes/<40-hex>` directory |
| 4 | worker_worktrees.source_root Config | FAILED | Config lacks `worker_worktrees.source_root` key; 0 of 3272 status records contain `workspace_source_root` |
| 5 | Status Root Isolation | FAILED | Status root `/home/lupin/pantheon` acts as Git worktree source authority for worker worktrees |
| 6 | Watchdog Absolute Config Binding | FAILED | `supervisor_watchdog.py:79, :870` fall back to command without `--config`, defaulting in `supervisor.py:623` to `.orchestrator/config.json` |
| 7 | Stale Token Clearance | FAILED | `SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731` remains in status `todo` (owner `Claude`); no recovery action attempted |
| 8 | Canary Workers Execution | FAILED | `ls -la` alone cannot prove canary lifecycle execution without process reconciliation; logs show worker exit 143 / exit 1 terminations |
| 9 | Parity & Lease Check | FAILED | `ls -la` alone cannot prove projection checkpoint parity across state stores |
| 10 | Config SHA Tracking | FAILED | Previously recorded `f90e0aae` is command-root Git commit sha, not config digest; live config sha256 is `bde28509c694ffa2fcdce35c3e8bb9041ced69b1bb0a027b8091b0981309c2cd` |
| 11 | Product Task Gate | PASSED | 28 L12 product tasks held until gate passes |
| 12 | Attestation | PASSED | Governed execution through scripts |

## Gate Conclusion
Fleet admission remains **FAIL-CLOSED** because items 3, 4, 5, 6, 7, 8, 9, and 10 fail runtime coherence and proof requirements.
