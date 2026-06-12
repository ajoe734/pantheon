# UI Flow Note: Management AI OpenClaw Repair Worktree Smoke

- **Packet ID**: `pkt_8a09145d0735`
- **Conversation ID**: `mgmt-ai-openclaw-repair-smoke-20260612T004311Z`
- **Status**: task-scoped UI note

## Operator Flow

The Management AI UI should present repair work as a governed dev action sequence, not as a file browser, terminal, or direct VM write capability.

1. Read `GET /bff/assistant/mode`.
2. Show repair controls only when kernel mode is enabled and the operator has the required role, MFA, and kernel capability.
3. Activate `kernel_repair` through `POST /bff/assistant/control-mode/activate`.
4. Preview the repair worktree request with repo key, task id, expected branch, merge target, and declared scope.
5. Call `POST /bff/assistant/repair-worktrees/prepare`.
6. Send returned `openclaw.repair` metadata with `POST /bff/management/nl/ask`.
7. Display provider status, workspace class, sentinel path, and concise answer.
8. Call `POST /bff/assistant/dev-docs/generate` with `queueTaskPacket=true`.
9. Poll `GET /bff/assistant/orchestrator/status` until the packet receipt is processed.

## Required UI States

- Kernel disabled: show repair unavailable and route operator to dev BFF configuration.
- Control mode inactive: show activation action, never provider write action.
- Prepare failed: show BFF error details and do not call Management NL provider.
- Provider completed in task worktree: show sentinel path and workspace class.
- Provider degraded or non-task workspace: mark smoke incomplete.
- Task packet queued: show packet id and queue path.
- Task packet processed: show supervisor receipt status.

## Copy Constraints

- Do not claim the browser can read or write arbitrary VM files.
- Do not expose the control-mode passphrase or provider credentials.
- Do not label provider readiness alone as write capability.
- Do not describe `GET|POST /bff/assistant/tools/*` as filesystem access.
- Do not present broker, capital, live, deploy, or runtime-control actions as part of this smoke.

## Source Citations

- `management_nl`: /bff/management/ai/conversations/mgmt-ai-openclaw-repair-smoke-20260612T004311Z - Source conversation used for requirement extraction.
- `assistant_mode`: /bff/assistant/mode - Kernel and control-mode status.
- `repair_worktree_prepare`: /bff/assistant/repair-worktrees/prepare - Repair worktree preparation route.
- `management_nl_ask`: /bff/management/nl/ask - Provider invocation and repair metadata forwarding route.
- `dev_docs_generate`: /bff/assistant/dev-docs/generate - SA/SD archive and task packet queue route.
- `orchestrator_status`: /bff/assistant/orchestrator/status - Supervisor/provider/dev-bridge readback.
- `runbook`: docs/deployment/management-ai-dev-kernel-control-mode.md - Dev-only activation and smoke procedure.
