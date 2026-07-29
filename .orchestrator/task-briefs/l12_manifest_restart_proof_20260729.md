# Task Brief: L12-MANIFEST-RESTART-PROOF-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: L12 manifest isolated restart proof workstream
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: PR #4345 merged as `9394c9ac6bcc6ece9a8f7f1412fce2ac51ef58c8`; Human/Ops may reconcile the already-merged, review-approved row to done because the direct Codex owner closeout is blocked by active worker lease enforcement.

## Closeout Handoff
- Claude2 approved PR #4338 at exact head `801852aeb59991526d37904491ceda62410ec701`; all recorded CI and the canonical review gate passed.
- The supervisor reassigned owner closeout from Codex2 to Codex after Codex2 repeatedly failed to resolve the remote task ref. The reviewer remains Claude2.
- Codex preserved the reviewed restart evidence byte-for-byte, anchored the owner metadata handoff, and refreshed the branch through `origin/dev` commit `50a1c5af513d43e0e97f8174cccc0325e0d19ece`.
- Because Pantheon review binds the exact PR head, the refreshed head requires a narrow delta review before merge; the isolated proof itself is unchanged.

## Final Reconcile Evidence
- Repository: `ajoe734/pantheon`
- Delivery PR: #4345
- Delivery commit: `9394c9ac6bcc6ece9a8f7f1412fce2ac51ef58c8`
- Reviewed head: `62057d7fa513309bb5fb04ee2b1c164816ebad0f`
- Review file: `docs/deployment/evidence/twelve-loop-gap/L12-MANIFEST-RESTART-PROOF-20260729/evidence.json`
- Validation: GitHub checks passed, `python3 -m json.tool evidence.json` passed, and `sha256sum -c evidence.sha256` passed 5/5 from the restart-proof evidence directory.
- Boundary: no `.orchestrator/config.json`, Compose, deploy script, live-capital, or hosted deployment switch is changed by this reconcile brief.

## Summary
補 isolated/non-shared PID1 crash restart proof，或取得明確 governed waiver。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
