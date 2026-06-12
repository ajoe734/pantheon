# Requirement Capture: Smoke Management AI OpenClaw Repair Work

- **Packet ID**: `pkt_68abefcfb82f`
- **Capture ID**: `req_efb101c347fb`
- **Conversation ID**: `mgmt-ai-openclaw-repair-smoke-20260612T011551Z`
- **Generated at**: 2026-06-12T01:15:51Z

## Problem

Management AI can already answer provider-backed questions and generate SA/SD packets, but this smoke needs an auditable proof that a kernel repair conversation can prepare a clean task worktree, pass that repair metadata to OpenClaw/Codex, write only inside the declared scope, and queue the generated DevTaskPacket for the repo-local supervisor.

## Actors

- Operator or admin with MFA and `assistant.kernel.repair`.
- Management AI frontend conversation surface in `execute-plans`.
- Pantheon BFF assistant routes.
- Pantheon BFF Management NL route.
- OpenClaw gateway adapter and Codex provider.
- Repo-local supervisor draining `.orchestrator/assistant-dev-packets`.
- Codex owner and Claude reviewer for this task.

## User Intent

Run a dev-only smoke where Management AI activates `kernel_repair`, prepares an `execute-plans` repair worktree, asks OpenClaw/Codex to write a sentinel under `tmp/management-ai-openclaw-smoke`, generates SA/SD from the conversation, queues the signed task packet, and proves the supervisor processed the packet. The implementation must not introduce a second gateway or grant direct browser shell access.

## Affected Modules

- `execute-plans:management-ai`
- `pantheon:bff-assistant`
- `pantheon:openclaw-dev-bridge`
- `services/control-plane/bff/openclaw_ops_client.py`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/assistant/*`
- `scripts/smoke_management_ai_openclaw_repair_e2e.sh`
- `tmp/management-ai-openclaw-smoke/MGMT-AI-OPENCLAW-REPAIR-SMOKE-20260612T011551Z.md`

## Constraints

- Dev only: `PANTHEON_ASSISTANT_KERNEL_ENABLED=true` is never a staging-live or production default.
- `kernel_repair` requires operator/admin role, MFA, a configured control-mode passphrase, and `assistant.kernel.repair`.
- Repair write metadata must come from `POST /bff/assistant/repair-worktrees/prepare`.
- Provider write work must run in the returned task worktree and declared scope only.
- All mutations route through governed BFF actions with preview, validation, confirmation, idempotency, and receipt semantics where applicable.
- The browser does not receive VM filesystem or shell access.
- The smoke must not commit, push, deploy, trade, or touch broker, capital, live, or runtime-control state.

## Open Questions

- None for this implementation slice.
- Full remote smoke still requires the existing dev control-mode passphrase and is intentionally not stored in repo artifacts.

## Source Turn References

- turn `mgmt-ai-openclaw-repair-smoke-20260612T011551Z:user`: requested the OpenClaw repair smoke, sentinel write, SA/SD generation, and DevTaskPacket queueing.
- turn `mgmt-ai-openclaw-repair-smoke-20260612T011551Z:assistant`: selected the existing BFF assistant routes, Management NL provider path, and OpenClaw adapter path instead of a second gateway.

## Acceptance Summary

- Requirement capture includes problem, actors, intent, modules, constraints, and source refs.
- Source citations link back to the Management AI conversation and BFF context pack surfaces.
- Execution is limited to dev-only kernel repair and scoped task worktrees.

## Source Citations

- `management_nl`: /bff/management/ai/conversations/mgmt-ai-openclaw-repair-smoke-20260612T011551Z - Source conversation used for requirement extraction.
- `assistant_mode`: /bff/assistant/mode - Kernel flag and control-mode posture.
- `repair_worktree_prepare`: /bff/assistant/repair-worktrees/prepare - BFF-governed clean worktree preparation.
- `management_nl_ask`: /bff/management/nl/ask - Provider invocation route that forwards `openclaw.repair` metadata.
- `dev_docs_generate`: /bff/assistant/dev-docs/generate - SA/SD archive and DevTaskPacket queue route.
- `orchestrator_status`: /bff/assistant/orchestrator/status - Supervisor/provider/dev-bridge readback.
- `smoke_script`: scripts/smoke_management_ai_openclaw_repair_e2e.sh - Executable smoke contract.
- `runbook`: docs/deployment/management-ai-dev-kernel-control-mode.md - Dev-only activation and verification runbook.
