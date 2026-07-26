# Task Brief: L12-TEACH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make Persona Teaching authenticated, tenant-safe, and HA
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Review failed after independent validation of merged PR #4149. Required fixes: (1) strict JWTs without an explicit training-service role must be rejected; current probe creates a session with HTTP 201 because inbound_authority hard-codes the privileged default role. Add missing/wrong-role negatives. (2) commit/discard must not accept an arbitrary syntactically valid six-digit X-MFA-Token as verified MFA; current no-MFA-claim JWT plus 123456 reaches the route boundary (HTTP 404). Require claim-bound or otherwise authoritative upstream-verifiable MFA and add a well-formed-but-unverified negative. (3) make Postgres TeachingEvent/session append conflict-safe across workers: current duplicate event_id with different payload returns the second payload as success while Postgres retains the first, and session append has no cross-instance mutation lock. Reject mismatched duplicates or return the durable prior record, serialize sequence/session mutation, and add a two-instance real-Postgres no-lost-update/conflict test. Existing positives independently passed: 123 passed, 1 skipped; real Postgres worker/restart 1 passed; evidence SHA-256 and PR checks passed.

## Summary
為 teaching API/worker 加 inbound authority 與 tenant，將 session/job/replay 移入 authoritative store，讓 functional health 與真正 eval/commit 結果一致。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Reviewer-requested remediation
- Status: addressed by Codex2 and ready for independent Codex re-review.
- Strict JWTs now require an explicit authorized training role; missing and wrong-role negatives return `403 AUTH_FORBIDDEN`.
- Commit and discard now require MFA asserted by the verified JWT/IdP claim; a syntactically valid caller-supplied OTP without that proof returns `401 MFA_NOT_VERIFIED`.
- Postgres event duplicates return the durable prior record only when payloads match, reject mismatches, and session event append is serialized in one session-scoped transaction.
- Anchor: `cc8592176e62907794e3a8943c13d0bd74524bc6`.
- Validation: `129 passed, 1 warning` with both real-Postgres tests enabled; evidence is under `docs/deployment/evidence/twelve-loop-gap/L12-TEACH-001`.
