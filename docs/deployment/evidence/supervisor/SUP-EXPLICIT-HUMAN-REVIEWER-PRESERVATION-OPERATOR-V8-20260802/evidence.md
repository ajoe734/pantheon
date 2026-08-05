# SUP-EXPLICIT-HUMAN-REVIEWER-PRESERVATION-OPERATOR-V8-20260802 evidence

Task: Preserve explicit Human/Ops reviewer assignments through fallback

Owner: Codex2 · Reviewer: Human/Ops · Status: **ready for independent exact-head review**

## Observed failure

PR #4508 head `7b268b468ca9cd619481fb8bc6a95ac663ac9cd4` had an explicit Human/Ops review gate. During availability fallback, canonical assignment was rewritten to owner `Human/Ops`, reviewer `Codex2`, and blocked status; the GitHub review bridge then recorded a Codex2 reopen and failed the `Pantheon canonical review gate` context. This is the prohibited lifecycle reproduced by the task regressions.

The root cause was assignment planning treating every reviewer as an auto-dispatchable worker. Human/Ops is an external governance actor and correctly fails ordinary worker eligibility, but the planner interpreted that as permission to choose a worker reviewer. Helper claim also preferred the previous owner over the explicit reviewer, so it could silently weaken the same gate while changing execution ownership.

## Delivered contract

- An explicit `reviewer: Human/Ops` is a protected external governance gate, not an unavailable worker lane.
- Mainline normalization, helper claim, and owner-failure recovery may still choose a viable execution owner, but the reviewer remains Human/Ops.
- A purported Human/Ops reviewer-worker failure fails closed and cannot enter automatic reviewer fallback.
- A `review_approved` task with a complete exact-head PR binding keeps both approved owner and Human/Ops reviewer unchanged during normalization. An unbound finalize task retains existing owner recovery while keeping the human gate.
- Supervisor persistence rejects attempts to overwrite an incumbent Human/Ops reviewer even if a caller bypasses the shared pair planner. Intentional operator reassignment remains available through governed `ai-status`; that command surface is unchanged.
- Owner/reviewer/status compare-and-swap remains under the canonical task-state lock. The concurrent regression proves that two stale owner plans apply at most once and leave Human/Ops intact.
- Non-Human/Ops fallback graphs, configured agent identity, Codex/Codex2 account and quota separation, and fallback ordering are unchanged.

## Scope boundaries

The task changes only `.orchestrator/supervisor.py`, `.orchestrator/test_supervisor.py`, and this evidence directory. The supervisor-generated task brief remains untracked context and is not part of the delivery range. No config, canonical JSON, ai-status semantics, provider/account/quota policy, live runtime, services, deployment, product tasks, or PR #4508 source changed.

## Owner verification

- Task-specific reviewer-preservation regression: 9 passed.
- Relevant assignment/fallback regression: 49 passed.
- Full supervisor regression: 532 tests and 152 subtests passed in 62.75 seconds.
- Supervisor and test `py_compile` passed.
- Evidence JSON parse, commit trailer check, and `git diff --check` passed on the task range.

The initial repo-root pytest collection attempt is not acceptance evidence because this legacy test imports `supervisor` as a top-level module. All authoritative pytest results use the established `.orchestrator/` working directory with the checkout-scoped interpreter.

## Review, merge order, and rollback

Human/Ops must independently inspect the exact corrective PR head, rerun focused checks, validate declared scope, and bind approval to GitHub `headRefOid`. No approval is claimed by this owner-authored evidence.

This corrective PR must merge before PR #4508 is rebased and reapproved. PR #4508 must then be revalidated without unrelated code changes. This task is source-only and authorizes no live rollout or restart. Rollback is a revert of the corrective merge commit; no state or configuration migration is required.
