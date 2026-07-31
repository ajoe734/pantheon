# Evidence: SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731

- **Task**: `SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731`
- **Owner**: Antigravity
- **Reviewer**: Human/Ops
- **Promoted Target Commit**: `012dab969455e7146f2437159d7d38fc5904a195` (contains merged PR #4430 agent alias guard)
- **Live Config**: `/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json`
- **Live Config SHA256**: `728a6d90aea962a5375ae66014b4d21638f1f5376c45c5ea1e0221ee5d9979cc` (verified unchanged before and after)

## Incident Chronology & Failure Recovery
1. **Initial Failed Promotion Attempt**:
   - Supervisor launched PID `3497098` directly from disposable task worktree `/tmp/pantheon-worker-worktrees/pantheon/sup-agent-alias-guard-live-promotion-20260731`.
   - Worktree HEAD drifted to `ac4fc7fe0e03cbd125389a830efde2873aedb73e`.
   - After worker cleanup, supervisor process cwd became a deleted path (`/tmp/pantheon-worker-worktrees/pantheon/sup-agent-alias-guard-live-promotion-20260731 (deleted)`).
   - Evidence recorded a false claim `active_worker_leases_preserved=0` while the Antigravity worker runner/child were running.
2. **Human/Ops Live Rescue**:
   - Human/Ops executed admission-lock rollback at `2026-07-31T23:26:14Z` to stable commit `cbb36ff1fe385f3bc2690124ff22d8edc0056896` / PID `3509070`.
   - Live config SHA256 remained intact (`728a6d90aea962a5375ae66014b4d21638f1f5376c45c5ea1e0221ee5d9979cc`).
3. **Automatic Rollback Disclaimer & Contract Status**:
   - Automatic rollback was **not** exercised during postcheck failure. The first failure required Human/Ops live rescue.
   - Future postcheck failure automatic rollback validation is marked as an **unmet acceptance item / contract gap** requiring a dedicated fail-closed command contract.

## Corrected Runtime Promotion Verification Summary

1. **Persistent Immutable Command-Runtime Root Proof**:
   - Path: `/home/lupin/pantheon-ci-deploy/command-runtimes/012dab969455e7146f2437159d7d38fc5904a195`
   - HEAD Commit: `012dab969455e7146f2437159d7d38fc5904a195`
   - Git Tree Clean: `git status -sb` shows `## master` (only untracked task-brief present, no staged or modified code diffs).
   - Origin: `https://github.com/ajoe734/pantheon.git` (fetch ref `refs/heads/dev`).

2. **Preflight Discovery & Worker Lease Preservation**:
   - Discovered incumbent supervisor PID `3509070` running from `/home/lupin/pantheon-ci-deploy/dev-root` (`cbb36ff1fe385f3bc2690124ff22d8edc0056896`).
   - Verified active worker lease `antigravity1-1-20260731T232741Z-eb5284e9` for task `SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731` was preserved and running.

3. **Governed Handoff Swap**:
   - Executed `docs/deployment/evidence/supervisor/SUP-COMMAND-RUNTIME-REFRESH-001/handoff/swap-supervisor.sh /home/lupin/pantheon-ci-deploy/command-runtimes/012dab969455e7146f2437159d7d38fc5904a195 alias-guard-live-promotion-20260731-v2`.
   - Recorded intentional restart intent for PID `3509070` targeting `012dab969455e7146f2437159d7d38fc5904a195`.
   - Acquired runtime admission lock (`/home/lupin/pantheon/.orchestrator/runtime-admission.lock`) prior to sending `SIGTERM` to PID `3509070`.
   - Confirmed incumbent supervisor cleanly exited.
   - Launched replacement supervisor process PID `3523046` from persistent target root `/home/lupin/pantheon-ci-deploy/command-runtimes/012dab969455e7146f2437159d7d38fc5904a195`.

4. **Post-Promotion Readback**:
   - Replacement supervisor process live PID: `3523046`.
   - Command line: `/usr/bin/python3.12 -u .orchestrator/supervisor.py --config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json --verbose`
   - Verified cwd of PID `3523046` via `/proc/3523046/cwd`: `/home/lupin/pantheon-ci-deploy/command-runtimes/012dab969455e7146f2437159d7d38fc5904a195` (persistent immutable root outside task worktrees).
   - Completed 5+ UTC supervisor loops:
     - `2026-07-31T23:28:33Z`
     - `2026-07-31T23:29:07Z`
     - `2026-07-31T23:29:42Z`
     - `2026-07-31T23:30:16Z`
     - `2026-07-31T23:30:50Z`
   - Live config SHA256 re-verified: `728a6d90aea962a5375ae66014b4d21638f1f5376c45c5ea1e0221ee5d9979cc` (unmodified before and after).
   - Active worker lease count = 1 (`antigravity1-1-20260731T232741Z-eb5284e9` running).
   - Duplicate task count: `0` (scanned `.orchestrator/state.json` for `SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731`).
   - Projection status: `state.json` v2 schema operational.
   - Antigravity provider readiness: `supported=True`, `can_auto_deliver=True`, primary model selected is `gemini-3.6-flash-low`.
   - Rollback root availability: `/home/lupin/pantheon-ci-deploy/dev-root` (`cbb36ff1fe385f3bc2690124ff22d8edc0056896`) verified available.
   - Status: `in_progress` (awaiting fresh Human/Ops independent review of updated PR #4431 head).
