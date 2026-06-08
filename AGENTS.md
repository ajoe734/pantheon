# Codex Repository Work Rules

These rules apply to Codex work in this repository. They are Codex's own
responsibility. Do not treat CI, hooks, reviewers, or the user as the first line
of enforcement for this workflow.

## Completion Definition For Repo Changes

When Codex modifies repository files, the work is not complete until Codex has
finished the repository development flow:

- inspect `git status -sb`, current branch, and remote before editing;
- use a clean task branch or clean worktree when the current checkout is dirty,
  detached, on a default branch, or shared with live workers;
- keep unrelated user, worker, generated, and runtime-state changes out of the
  commit;
- run relevant local validation;
- stage only the intended files;
- commit with the repository's required subject and trailers;
- push the branch;
- open a PR;
- wait for required repository checks when they are visible;
- merge the PR when the user asked for completed delivery and policy allows it;
- report the PR number, merge commit, and validation in the final answer.

If any step is blocked, Codex must say exactly which step is blocked and why.
Never present local-only edits, a restarted process, or passing local tests as a
completed repository change.

## Live Repair Rule

For urgent supervisor, worker, auth, or runtime repair, Codex may perform the
smallest live rescue first. Codex must explicitly label that action as temporary
live repair, then put the exact code or configuration change through branch,
commit, push, PR, checks, and merge in the same turn.

This is not optional process paperwork. It is part of the engineering task.

## Frontend Repository And Dev Hosting

The active frontend system is `execute-plans`, not `front-ai-trading-system`.
Use repository `ajoe734/execute-plans` and local checkout
`/home/lupin/code/execute-plans` or a clean task worktree created from it. Do
not create, revive, mirror to, or assign new work to `front-ai-trading-system`;
that name is legacy-only and must not be used for current development.

As of 2026-06-08 the `execute-plans` remote has `main` as its frontend delivery
base; there is no remote `dev` branch. If a frontend dev branch is later
created, update this file and `docs/frontend/execute-plans-dev-hosting.md`
before routing work to it.

Do not treat Lovable publish status as the dev frontend host or as the release
truth for Pantheon dev. Lovable URLs may remain historical evidence or an
external reference, but current dev frontend delivery must be served by the
Pantheon dev environment from an `execute-plans` commit. The intended dev host
is Pantheon-owned, for example
`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`, with the BFF target
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`.

When deploying or validating the dev frontend, ensure the frontend build uses
`VITE_BFF_MODE=live`, `VITE_BFF_BASE_URL` pointing at the dev BFF,
`VITE_BFF_FALLBACK=strict`, and safe write defaults unless the operator
explicitly enables real writes. Also ensure the dev BFF
`PANTHEON_BFF_CORS_ORIGINS` includes the Pantheon-owned frontend origin before
browser smoke tests. See `docs/frontend/execute-plans-dev-hosting.md`.

## Management AI / OpenClaw Dev Work

Management AI frontend work must start from `execute-plans` and call Pantheon
BFF assistant routes. Do not route new Management AI development through
Lovable, and do not use `front-ai-trading-system` as a source checkout.

For SA/SD generation and downstream agent work, the expected route family is:

- `POST /bff/assistant/dev-docs/generate`
- `GET /bff/assistant/dev-docs/{packetId}`
- `POST /bff/assistant/dev-bridge/task-packet`
- `POST /bff/assistant/repair-worktrees/prepare`
- `GET /bff/assistant/orchestrator/status`
- `GET|POST /bff/assistant/tools/*` for governed UI/ops action
  preview/validation/execute only

Do not confuse `GET|POST /bff/assistant/tools/*` with VM file-system access.
Those routes expose Pantheon-governed action contracts such as preview,
validation, and execute for BFF-owned operations. They are not shell, repo file
read, or repo file write tools.

OpenClaw-backed VM inspection and debugging is reached through Management AI
conversation routes, primarily `POST /bff/management/nl/ask`, with Pantheon BFF
calling the OpenClaw gateway adapter. In active `kernel_debug` mode, provider
work must remain read-only. In active `kernel_repair` mode, provider write work
must run in a clean repair task worktree; never point repair at the shared live
checkout.

Any frontend or BFF change that claims Management AI can write VM files must
first call `POST /bff/assistant/repair-worktrees/prepare` while control mode is
active in `kernel_repair`, then send the OpenClaw repair metadata returned by
that route to `POST /bff/management/nl/ask`:

- `repo_key` (`execute-plans` for frontend work, `pantheon` for backend/BFF
  work)
- `task_id`
- `task_worktree`
- `declared_scope`
- `expected_branch`
- `remote`
- `merge_target`

The BFF prepare route delegates to the OpenClaw adapter route
`POST /api/openclaw-adapter/assistant/repair-worktrees/prepare`. The adapter
must clone or reuse a clean task worktree under
`PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT`, make that directory the git repo
root, check out `expected_branch`, and validate that `declared_scope` contains
only repo-relative paths. Do not use `.` as a blanket write scope.

Provider readiness is necessary but not sufficient. Before claiming that
Management AI can read/write VM files or collaborate on debugging through
OpenClaw, verify `GET /bff/assistant/mode` reports `kernel_enabled: true` and
that control mode can be activated by an authorized operator/admin session. If
`providerReadiness.ready` is true but `kernel_enabled` is false, the blocker is
dev BFF configuration, not frontend hosting.

As of 2026-06-08, if the frontend can activate control mode but cannot prepare
a repair worktree through `POST /bff/assistant/repair-worktrees/prepare`, treat
VM write capability as not complete. Do not ask other agents to implement code
through Management AI until that prepare call succeeds and its
`openclaw.repair` metadata is forwarded with the conversation request.

The supervisor handoff path is the assistant dev bridge inbox under
`.orchestrator/assistant-dev-packets/`. The supervisor must drain pending task
packets into `ai-task-archive/tasks/` before a SA/SD packet is considered
accepted by downstream workers. See
`docs/operations/management-ai-openclaw-dev-bridge.md` for the full validation
runbook.
