# Evidence: SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731

- **Task**: `SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731`
- **Owner**: Antigravity
- **Reviewer**: Human/Ops
- **Promoted Target Commit**: `012dab969455e7146f2437159d7d38fc5904a195` (contains merged PR #4430 agent alias guard)
- **Live Config**: `/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json`
- **Live Config SHA256**: `728a6d90aea962a5375ae66014b4d21638f1f5376c45c5ea1e0221ee5d9979cc` (verified unchanged before and after)

## Runtime Promotion Verification Summary

1. **Preflight Discovery**:
   - Single incumbent live supervisor detected at PID `3319508` running from `/home/lupin/pantheon-ci-deploy/dev-root` (`cbb36ff1fe385f3bc2690124ff22d8edc0056896`).
   - Verified target commit `012dab969455e7146f2437159d7d38fc5904a195` exists and contains PR #4430.

2. **Governed Handoff Swap**:
   - Executed `docs/deployment/evidence/supervisor/SUP-COMMAND-RUNTIME-REFRESH-001/handoff/swap-supervisor.sh`.
   - Recorded intentional restart intent for PID `3319508` targeting `012dab969455e7146f2437159d7d38fc5904a195`.
   - Acquired runtime admission lock (`/home/lupin/pantheon/.orchestrator/runtime-admission.lock`) prior to sending `SIGTERM` to PID `3319508`.
   - Confirmed incumbent supervisor cleanly exited.
   - Launched replacement supervisor process from target root `/tmp/pantheon-worker-worktrees/pantheon/sup-agent-alias-guard-live-promotion-20260731` running `012dab969455e7146f2437159d7d38fc5904a195`.

3. **Post-Promotion Readback**:
   - Replacement supervisor process live PID: `3497098`.
   - PID `3497098` verified running `.orchestrator/supervisor.py` with cwd `/tmp/pantheon-worker-worktrees/pantheon/sup-agent-alias-guard-live-promotion-20260731`.
   - Live config SHA256 re-verified: `728a6d90aea962a5375ae66014b4d21638f1f5376c45c5ea1e0221ee5d9979cc`.
   - Active worker leases: 0 (clean state preserved).
   - Antigravity provider readiness: `supported=True`, `can_auto_deliver=True`, primary model selected is `gemini-3.6-flash-low`.
