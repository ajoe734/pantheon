# Task Brief: L12-TEACH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make Persona Teaching authenticated, tenant-safe, and HA
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Reviewer fixes are merged in PR #4166 and owner-revalidated; ready for independent Codex re-review.

## Summary
為 teaching API/worker 加 inbound authority 與 tenant，將 session/job/replay 移入 authoritative store，讓 functional health 與真正 eval/commit 結果一致。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Reviewer-requested remediation
- Status: addressed by Codex2, merged to `dev` in PR #4166 (`022bb35f4cd93c82571fcaf2799905a7043efcd2`), and ready for independent Codex re-review.
- Strict JWTs now require an explicit authorized training role; missing and wrong-role negatives return `403 AUTH_FORBIDDEN`.
- Commit and discard now require MFA asserted by the verified JWT/IdP claim; a syntactically valid caller-supplied OTP without that proof returns `401 MFA_NOT_VERIFIED`.
- Postgres event duplicates return the durable prior record only when payloads match, reject mismatches, and session event append is serialized in one session-scoped transaction.
- Anchor: `cc8592176e62907794e3a8943c13d0bd74524bc6`.
- Owner revalidation: `129 passed, 1 warning` with both real-Postgres tests enabled; evidence is under `docs/deployment/evidence/twelve-loop-gap/L12-TEACH-001`.
