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

## Expected Route Family

Management AI should use Pantheon BFF assistant routes:

- `GET /bff/assistant/mode`
- `GET /bff/assistant/orchestrator/status`
- `POST /bff/assistant/dev-docs/generate`
- `GET /bff/assistant/dev-docs/{packetId}`
- `POST /bff/assistant/dev-bridge/task-packet`
- `GET /bff/assistant/tools`
- `POST /bff/assistant/tools/preview`
- `POST /bff/assistant/tools/validate`
- `POST /bff/assistant/tools/execute`

The frontend SA/SD action should call `/bff/assistant/dev-docs/generate` with
archive enabled and task-packet emission enabled when the operator asks for a
downstream implementation packet.

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
- `POST /bff/assistant/dev-docs/generate` is not `404`; unauthenticated probes
  should fail closed with `401` or `403`.
- A generated task packet reaches `.orchestrator/assistant-dev-packets/pending/`
  or is otherwise handed to the configured bridge inbox.
- The supervisor drains the packet into
  `.orchestrator/assistant-dev-packets/processed/` and writes a receipt.
- The downstream task record exists under `ai-task-archive/tasks/`.

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
- Do not claim completion from provider readiness if kernel/control mode is
  disabled.
- Do not commit runtime state from the live worktree.
- Do not change broker, paper, canary, live, or capital-binding behavior while
  repairing Management AI dev file access.
