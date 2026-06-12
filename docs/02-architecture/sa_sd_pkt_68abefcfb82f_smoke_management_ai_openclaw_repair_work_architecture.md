# Architecture Note: Management AI OpenClaw Repair Worktree Smoke

- **Packet ID**: `pkt_68abefcfb82f`
- **Conversation ID**: `mgmt-ai-openclaw-repair-smoke-20260612T011551Z`
- **Status**: task-scoped architecture note

## Boundary

The repair smoke stays inside the existing Pantheon BFF and OpenClaw gateway adapter boundary. The browser-facing surface is BFF only. BFF validates control mode and identity, then delegates repair worktree preparation and provider invocation to the Pantheon-owned OpenClaw adapter. OpenClaw/Codex may write only inside the prepared task worktree and declared scope returned by the prepare route.

This architecture intentionally does not add:

- A second gateway.
- Browser shell access.
- Browser VM filesystem access.
- Direct provider credential handling in frontend code.
- Broker, capital, live, deployment, or runtime-control authority.

## Components

- `execute-plans:management-ai`: operator conversation and governed action surface.
- `pantheon:bff-assistant`: control mode, context pack, SA/SD generation, DevTaskPacket signing and queueing.
- `pantheon:bff-management-nl`: provider invocation and repair metadata forwarding.
- `pantheon:openclaw-dev-bridge`: signed packet inbox and supervisor drain path.
- `OpenClaw gateway adapter`: provider runtime and task worktree preparation.

## API Surface

- `GET /bff/assistant/mode`
- `POST /bff/assistant/control-mode/activate`
- `POST /bff/assistant/repair-worktrees/prepare`
- `POST /bff/management/nl/ask`
- `POST /bff/assistant/dev-docs/generate`
- `POST /bff/assistant/dev-bridge/task-packet`
- `GET /bff/assistant/orchestrator/status`

## Execution Contract

1. Control mode must be active as `kernel_repair`.
2. `declaredScope` must contain explicit repo-relative paths, not `.`.
3. BFF forwards only normalized repair metadata to OpenClaw/Codex.
4. Provider status must report `workspaceClass=task_worktree`.
5. The sentinel write must be inside the declared scope.
6. SA/SD docs and task briefs are repo artifacts, not transient chat output.
7. Supervisor acceptance is a processed DevTaskPacket receipt.

## Source Citations

- `management_nl`: /bff/management/ai/conversations/mgmt-ai-openclaw-repair-smoke-20260612T011551Z - Source conversation used for requirement extraction.
- `assistant_mode`: /bff/assistant/mode - Kernel and control-mode status.
- `repair_worktree_prepare`: /bff/assistant/repair-worktrees/prepare - Repair worktree preparation route.
- `management_nl_ask`: /bff/management/nl/ask - Provider invocation and repair metadata forwarding route.
- `dev_docs_generate`: /bff/assistant/dev-docs/generate - SA/SD archive and task packet queue route.
- `orchestrator_status`: /bff/assistant/orchestrator/status - Supervisor/provider/dev-bridge readback.
- `smoke_script`: scripts/smoke_management_ai_openclaw_repair_e2e.sh - End-to-end smoke contract.
