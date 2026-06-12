# Watchdog Flock Branch Retirement

Status date: 2026-06-11

## Summary

`task/watchdog-flock-liveness` is retired as a working branch. The original
watchdog liveness fix was merged to `dev` through PR #1044 on 2026-06-05.

After that merge, the same branch was accidentally used as a base for later
work. Those later pull requests merged into `task/watchdog-flock-liveness`
instead of `dev`, which made the branch look active even though its original
purpose was complete.

Do not open new pull requests against `task/watchdog-flock-liveness`. New
runtime, supervisor, provider, or auto-worker work must start from current
`origin/dev` or from a clean task worktree based on `origin/dev`.

## Preserved Ref

The branch tip is preserved before remote cleanup:

- retired task branch: `task/watchdog-flock-liveness`
- preserved ref: `archive/task-watchdog-flock-liveness-20260611`
- preserved commit: `de2a64d5535c6b8520f7c0ffa6c5a640ced9c403`

This archive ref keeps the misplaced branch history reachable after the retired
task branch is deleted.

## PR History

| PR | Base | Head | Result | Note |
|---|---|---|---|---|
| #1044 | `dev` | `task/watchdog-flock-liveness` | merged 2026-06-05 | Original watchdog flock liveness fix. |
| #1048 | `task/watchdog-flock-liveness` | `codex/runtime-telemetry-hardening-plan` | merged 2026-06-06 | Merged to the retired task branch, not `dev`. |
| #1050 | `task/watchdog-flock-liveness` | `codex/provider-config-guardrails` | merged 2026-06-06 | Merged to the retired task branch, not `dev`. |
| #1054 | `task/watchdog-flock-liveness` | `codex/worker-commit-index-refresh` | merged 2026-06-06 | Merged to the retired task branch, not `dev`. |
| #1102 | `task/watchdog-flock-liveness` | `task/antigravity-cli-migration` | merged 2026-06-06 | Merged to the retired task branch, not `dev`. |

## Cherry Audit

`git cherry -v origin/dev origin/task/watchdog-flock-liveness` classified the
branch commits this way at cleanup time:

| Commit | Patch state vs `origin/dev` | Disposition |
|---|---|---|
| `9a8585b1` `OPS-RUNTIME-TELEMETRY: coerce Postgres timestamps` | equivalent patch found | No branch action needed. |
| `1fc6865f` `OPS-SUPERVISOR-PERSIST: install watchdog persistence` | equivalent patch found | No branch action needed. |
| `c92ce3e9` `OPS-WORKTREE-INDEX: refresh isolated commit index` | equivalent patch found | No branch action needed. |
| `1e85c203` `OPS-RTEL-PLAN: materialize runtime telemetry hardening` | not equivalent | Do not replay generated status files. Use as historical planning context only. |
| `e40e093a` `OPS-AUTOWORKER-GUARD: preflight provider config` | not equivalent | If still needed, reimplement from current `origin/dev` with fresh tests. |
| `370950b3` `OPS-ANTIGRAVITY-CLI-MIGRATION: migrate gemini workers to agy` | not equivalent | If still needed, reimplement from current `origin/dev` with fresh tests. |
| `de2a64d5` `ASST-SKILL-006: state closeout - task archived as done` | not equivalent | Do not replay generated status/current-work archive edits. |

The non-equivalent commits are not safe to cherry-pick blindly because current
`dev` has moved hundreds of commits ahead and has newer supervisor, archive,
assistant skill, runtime telemetry, and auto-integrator state.

## Cleanup Rule

1. Keep this retirement note on `dev`.
2. Keep `archive/task-watchdog-flock-liveness-20260611` as the history anchor.
3. Delete the remote `task/watchdog-flock-liveness` branch after this note is
   merged.
4. Leave any dirty local checkout alone until runtime state has been explicitly
   moved or discarded by the operator. A dirty live checkout is not a safe place
   to fast-forward, rebase, or delete local state.

## Follow-up Policy

If any of the non-equivalent commits are still operationally useful, create a
new task from current `origin/dev`, copy only the relevant behavior, run the
focused tests, and send it through the normal branch, PR, checks, and merge
flow. The retired branch itself must not be used as a merge base.
