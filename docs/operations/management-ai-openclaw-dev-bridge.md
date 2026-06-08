# Management AI OpenClaw Dev Bridge Runbook

Date: 2026-06-08

This is the canonical runbook for Management AI development that needs
OpenClaw-backed VM file access, SA/SD generation, and downstream supervisor or
auto-worker execution.

## Source Of Truth

- Backend/BFF repo: `ajoe734/pantheon`
- Frontend repo: `ajoe734/execute-plans`
- Active dev FE: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- Active dev BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`

Do not use `front-ai-trading-system` for current frontend development. Do not
use Lovable publish state as the dev frontend host or acceptance source. Lovable
may only be historical evidence or an external reference.

Do not ask the operator to press Lovable publish or reconnect Lovable before
working on Management AI dev capability. The active frontend is
`execute-plans`, and the active host is Pantheon-owned.

Current verified dev deployment, 2026-06-08:

- `pantheon@22b89367a56cdbb4fb8a7345fc7c4ad1d293a118` on `dev` for BFF and
  OpenClaw adapter repair-worktree preparation.
- `execute-plans@8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf` on `main` for the
  Management AI frontend control dialog and `openclaw.repair` forwarding.
- Dev FE document root: `/var/www/pantheon-dev-fe/`.

Current known gate: provider readiness and route availability can be healthy
while control mode is configured but inactive. A positive VM-write claim still
requires an authorized operator/admin activation in `kernel_repair`, a
successful `POST /bff/assistant/repair-worktrees/prepare`, and forwarding the
returned `openclaw.repair` metadata to the Management AI ask request.

## Expected Route Family

Management AI should use Pantheon BFF assistant routes:

- `GET /bff/assistant/mode`
- `GET /bff/assistant/orchestrator/status`
- `POST /bff/assistant/dev-docs/generate`
- `GET /bff/assistant/dev-docs/{packetId}`
- `POST /bff/assistant/dev-bridge/task-packet`
- `POST /bff/assistant/repair-worktrees/prepare`
- `GET /bff/assistant/tools`
- `POST /bff/assistant/tools/preview`
- `POST /bff/assistant/tools/validate`
- `POST /bff/assistant/tools/execute`

The frontend SA/SD action should call `/bff/assistant/dev-docs/generate` with
archive enabled and task-packet emission enabled when the operator asks for a
downstream implementation packet.

The `/bff/assistant/tools/*` route family is not the OpenClaw VM file-system
tool surface. It is the governed Pantheon action surface for BFF-owned preview,
validation, and execution contracts. Do not use it as proof that Management AI
can read, write, search, or debug VM files.

## OpenClaw File Access Boundary

Management AI reaches OpenClaw through Pantheon BFF conversation routes,
primarily `POST /bff/management/nl/ask`. The BFF forwards the request to the
OpenClaw gateway adapter/Codex provider when the assistant provider is healthy.

The two control-mode behaviors are intentionally different:

- `kernel_debug`: read-only provider execution for status, logs, repository
  context, and debugging assistance.
- `kernel_repair`: write-capable provider execution, but only inside a clean
  repair task worktree.

Do not expose direct browser-to-OpenClaw calls. Do not mount or write to the
shared live checkout for repair work. Do not treat the status-root read mount as
a repair workspace.

For `kernel_repair`, the frontend must first call
`POST /bff/assistant/repair-worktrees/prepare`. The BFF verifies active
`kernel_repair` control mode and delegates to
`POST /api/openclaw-adapter/assistant/repair-worktrees/prepare`. The adapter
clones or reuses a clean task worktree from the configured repo source, checks
out the requested task branch, validates scope, and returns `openclaw.repair`
metadata that matches the provider contract:

- `repo_key`
- `task_id`
- `task_worktree`
- `declared_scope`
- `expected_branch`
- `remote`
- `merge_target`

The worktree must exist under `PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT` and
must be the git repo root. Before provider execution it must be clean, on
`expected_branch`, and limited to repo-relative `declared_scope` entries.
`declared_scope` must not be empty and must not be `.`. Use `repoKey:
execute-plans` with merge target `main` for frontend work and `repoKey:
pantheon` with merge target `dev` for backend/BFF work.

As of 2026-06-08, dev BFF control mode can be configured independently from
repair-worktree provisioning. If Management AI chat does not first prepare the
worktree through the governed BFF route and then send the returned
`openclaw.repair` metadata to `/bff/management/nl/ask`, VM write capability is
not complete. Do not tell downstream agents to implement code through
Management AI until that preparation path succeeds.

## Readiness Criteria

A route returning `200` or provider readiness alone does not prove the system is
ready. Check all of these before telling another agent or operator it is done:

- `GET /bff/assistant/orchestrator/status` reports provider readiness available
  and ready for the configured provider/runtime.
- `GET /bff/assistant/mode` reports `kernel_enabled: true`.
- `GET /bff/assistant/mode` reports control mode configured and activatable for
  an authorized operator/admin session.
- The frontend build targets the dev BFF with `VITE_BFF_MODE=live`,
  `VITE_BFF_FALLBACK=strict`, and safe write defaults unless explicitly
  approved.
- The browser-loaded bundle contains the current dev BFF URL and does not
  contain obsolete BFF URLs.
- `/bff/assistant/tools/*` is understood as governed BFF action tooling, not VM
  file tooling.
- `POST /bff/assistant/repair-worktrees/prepare` is not `404`; unauthenticated
  or inactive-control probes should fail closed with `401`, `403`, or `409`.
- `POST /bff/assistant/dev-docs/generate` is not `404`; unauthenticated probes
  should fail closed with `401` or `403`.
- For repair/write claims, the prepare route returns a clean task worktree
  under the configured repair root and Management AI sends that valid
  `openclaw.repair` metadata to `/bff/management/nl/ask`.
- A generated task packet reaches `.orchestrator/assistant-dev-packets/pending/`
  or is otherwise handed to the configured bridge inbox.
- The supervisor drains the packet into
  `.orchestrator/assistant-dev-packets/processed/` and writes a receipt.
- The downstream task record exists under `ai-task-archive/tasks/`.

## Current Work Order For Agents

When the user asks for Management AI frontend or OpenClaw repair changes:

1. For frontend code, work in `ajoe734/execute-plans` from `main`, merge by PR,
   build with the dev BFF URL, and deploy to the Pantheon-owned dev FE host.
2. For backend/BFF code, work in `ajoe734/pantheon` from `dev`, merge by PR,
   rebuild/restart the relevant dev VM services, then re-smoke the FE host.
3. For write-capable Management AI work, use `repoKey: execute-plans` with
   merge target `main` for frontend changes and `repoKey: pantheon` with merge
   target `dev` for backend/BFF changes.
4. Do not dispatch downstream implementation agents from a SA/SD packet until
   the packet has reached the assistant dev bridge inbox and the supervisor has
   produced an archive task record.
5. Do not switch to Lovable or `front-ai-trading-system` when any of the above
   steps fails. Diagnose the failing Pantheon-owned route, build, control mode,
   or supervisor bridge instead.

## Kernel/Control-Mode Failure Pattern

If `GET /bff/assistant/orchestrator/status` shows `providerReadiness.ready:
true` but `GET /bff/assistant/mode` shows `kernel_enabled: false`, do not patch
the frontend and do not fall back to Lovable. The blocker is the running dev BFF
configuration.

The expected fix path is:

1. Perform the smallest live repair only if the operator needs immediate dev
   recovery.
2. Put the exact config/code change through a clean branch, commit, PR, checks,
   merge, and redeploy.
3. Recheck `/bff/assistant/mode` and `/bff/assistant/orchestrator/status` after
   the restart.
4. Only then rerun browser and SA/SD bridge smoke.

The relevant env gate is `PANTHEON_ASSISTANT_KERNEL_ENABLED`. Keep auth,
passphrase, MFA, and capability requirements governed; do not bypass them in
frontend code.

## Supervisor Bridge

The supervisor owns packet draining. The configured dev inbox should point at:

```text
/home/lupin/code/pantheon/.orchestrator/assistant-dev-packets
```

Expected directories:

- `pending/` for packets waiting to be drained.
- `processed/` for accepted packets.
- `failed/` for rejected packets.
- `receipts/` for drain receipts.

The supervisor loop should call the assistant dev bridge drain function and
record processed task ids. A completed bridge smoke must have both a receipt and
an `ai-task-archive/tasks/*.json` task record.

## Agent Guardrails

- Do not claim completion from a local-only frontend build.
- Do not claim completion from a Lovable publish.
- Do not ask the user to press Lovable publish for Pantheon dev delivery.
- Do not use Lovable connector state as proof that the dev FE is deployed.
- Do not claim completion from provider readiness if kernel/control mode is
  disabled.
- Do not claim VM write capability from `/bff/assistant/tools/*`.
- Do not claim repair capability unless
  `POST /bff/assistant/repair-worktrees/prepare` succeeds, the request includes
  the returned `openclaw.repair` metadata, and the repair worktree is clean.
- Do not commit runtime state from the live worktree.
- Do not change broker, paper, canary, live, or capital-binding behavior while
  repairing Management AI dev file access.
