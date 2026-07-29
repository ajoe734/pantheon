# Task Brief: SUP-L12-FLEET-DISPATCH-HEALTH-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Verify live supervisor and provider-first dispatch health
- Status: review (owner closeout refresh complete; pending Antigravity exact-head approval)
- Owner: Codex
- Reviewer: Antigravity
- Task branch: `task/SUP-L12-FLEET-DISPATCH-HEALTH-20260729`
- Pull request: `#4328`
- Prior approved head: Codex2 independently approved
  `73c679b21971b2a0d6435e3043371883799ec0be`.
- Reviewer transition: the supervisor reassigned the reviewer from Codex2 to
  Antigravity after Codex2 became unavailable. The canonical merge gate
  therefore requires Antigravity to approve the final exact head; ownership
  and reviewer assignment must not change during closeout.
- Base refresh: merged current `origin/dev`
  `31d7eaebc49f027e1c9935915c875e8a0b4c6846` into the task branch as
  `ef804fe6dc9bc33ea3b848f1fcabd496b6322667`.
- Next: Antigravity independently reviews and approves the final exact head;
  the owner then merges PR #4328 and runs governed `done`.

## Summary
Fleet health task to ensure this packet is actually handled by supervisor/auto workers.

## Acceptance
- Prove the live supervisor uses `/home/lupin/pantheon-ci-deploy/dev-root`.
- Prove assistant dev packet `pkt-l12-current-gap-drain-20260729T0107Z`
  reaches a pending, processed, receipt, or archive record.
- Read Antigravity and Claude2 readiness from live worker-runtime facts.
- Record repeated failure loops without editing `.orchestrator/config.json`.

## Evidence
- Review manifest:
  `docs/deployment/evidence/twelve-loop-gap/SUP-L12-FLEET-DISPATCH-HEALTH-20260729/evidence.json`
- Human-readable summary:
  `docs/deployment/evidence/twelve-loop-gap/SUP-L12-FLEET-DISPATCH-HEALTH-20260729/README.md`
- Fleet verdict: acceptance satisfied; live health observed as
  `degraded_but_failover_working`.

## Owner Closeout Verification
- `python3 -m json.tool evidence.json` passed.
- `sha256sum -c evidence.sha256` passed from the evidence directory.
- `git diff --check origin/dev...HEAD` and `git show --check HEAD` passed.
- The task range contains only this brief and the three reviewed evidence
  files; `.orchestrator/config.json` remains absent from the task diff.
- Live supervisor PID `2082839` still runs
  `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/supervisor.py`, with
  cwd/PWD `/home/lupin/pantheon-ci-deploy/dev-root` and status root
  `/home/lupin/pantheon`.
- Governed command root HEAD and `PANTHEON_COMMAND_RUNTIME_SHA` both resolve to
  `a6d56c366f7436574e6d2d241b47564558beac74`.
- The packet receipt remains durable with status `failed`, digest
  `fa8e92a1c424e58e5206ba4c32ef44a60e6c71c22c8a057b548aec490bd652ea`,
  two dispatched task records, two artifact-conflict errors, and the matching
  failed archive path.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
