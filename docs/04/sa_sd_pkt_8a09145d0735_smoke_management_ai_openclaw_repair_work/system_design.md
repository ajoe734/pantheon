# SD: Smoke Management AI OpenClaw Repair Work

- **Packet ID**: `pkt_8a09145d0735`
- **SD ID**: `sd_smoke_management_ai_openclaw_repair_work`
- **SA ID**: `sa_smoke_management_ai_openclaw_repair_work`
- **Generated at**: 2026-06-12T00:43:11Z

## Architecture

Use the existing BFF assistant and Management NL routes as the only browser-facing control plane. The frontend asks BFF to activate dev-only control mode, prepare a repair worktree, invoke Management NL with the returned `openclaw.repair` metadata, and generate or queue SA/SD artifacts. BFF delegates provider execution and repair worktree preparation to the Pantheon-owned OpenClaw gateway adapter. No second gateway, direct browser shell, direct VM filesystem route, or broker/capital/runtime mutation path is introduced.

The durable artifact surface is split into:

- `docs/04/sa_sd_pkt_8a09145d0735_smoke_management_ai_openclaw_repair_work/` for requirement capture, SA, and SD.
- `docs/02-architecture/sa_sd_pkt_8a09145d0735_smoke_management_ai_openclaw_repair_work_architecture.md` for the architecture note.
- `docs/05-ui/sa_sd_pkt_8a09145d0735_smoke_management_ai_openclaw_repair_work_ui.md` for the operator UI flow note.
- `.orchestrator/task-briefs/task_c83b0fd8b087.md` for worker dispatch context.

## API Contract

- `GET /bff/assistant/mode`: reports `kernel_enabled`, passphrase configured state, and active control-mode state.
- `POST /bff/assistant/control-mode/activate`: activates `kernel_repair` when actor role, MFA, passphrase, and `assistant.kernel.repair` capability pass.
- `POST /bff/assistant/repair-worktrees/prepare`: validates active `kernel_repair`, normalizes task metadata, and delegates to `POST /api/openclaw-adapter/assistant/repair-worktrees/prepare`.
- `POST /bff/management/nl/ask`: builds a governed context pack and forwards `openclaw.repair` metadata into provider invocation metadata only when provider mode is `kernel_repair`.
- `POST /bff/assistant/dev-docs/generate`: generates, archives, signs, and optionally queues SA/SD DevTaskPackets.
- `GET /bff/assistant/dev-docs/{packetId}`: reads archived SA/SD packet metadata.
- `POST /bff/assistant/dev-bridge/task-packet`: signs or queues an already generated DevTaskPacket.
- `GET /bff/assistant/orchestrator/status`: reports supervisor, provider readiness, OpenClaw tool policy, inbox, pending count, and recent receipts.

All POST requests used by the smoke require stable idempotency where the final-contract route enforces it.

## DB / Migration

- No database migration is required.
- Control-mode state remains in the configured BFF control-mode store.
- Management AI conversation state remains in the existing Management AI conversation store.
- SA/SD, architecture, UI, and task-brief artifacts are repo files.
- DevTaskPacket queue state remains under `.orchestrator/assistant-dev-packets`.

## UI Routes / Components

- The Management AI frontend should expose repair capability only after `GET /bff/assistant/mode` reports `kernel_enabled=true` and control mode can be activated by the current operator.
- The UI must present worktree preparation as a governed BFF action, not as file explorer or shell access.
- The repair prompt should include repo key, task id, expected branch, merge target, declared scope, and the returned task worktree metadata.
- After invocation, the UI should display provider status, workspace class, sentinel path, packet id, queue receipt, and supervisor processed receipt.
- The UI must keep passphrases, provider credentials, broker credentials, and private keys out of frontend environment variables and rendered logs.

## Tool / Action Contract

- Preview: show the action sequence and declared scope before activation or repair preparation.
- Validate: require operator/admin role, MFA, kernel capability, active `kernel_repair`, non-empty repo-relative declared scope, and idempotency key.
- Confirm: require passphrase for control-mode activation and explicit operator intent for write-capable repair.
- Execute: call only the BFF routes listed above; BFF calls only the OpenClaw adapter for worktree/provider operations.
- Receipt: return control activation, repair worktree, provider, archive, queue, and supervisor receipt identifiers.
- Fail closed: unavailable adapter, inactive control mode, missing capability, stale passphrase, dirty worktree, non-task workspace, or missing queue receipt blocks completion.

## Tests

- `pytest services/control-plane/bff/tests/test_req_3e2d36061ef6.py`
  - Proves `OpenClawOpsClient.prepare_assistant_repair_worktree` calls the adapter route with the expected body, headers, and timeout.
  - Proves `/bff/assistant/repair-worktrees/prepare` requires active `kernel_repair` and delegates normalized payload metadata.
  - Proves `/bff/assistant/dev-docs/generate` archives docs into `docs/04`, `docs/02-architecture`, `docs/05-ui`, produces a task brief, and queues a signed DevTaskPacket containing those document paths.
- Existing Management NL provider tests cover `kernel_repair` metadata forwarding and `workspaceClass=task_worktree`.
- Full remote smoke command, when a dev operator supplies the existing passphrase:
  - `PANTHEON_ASSISTANT_CONTROL_PASSPHRASE=<existing-control-mode-passphrase> scripts/smoke_management_ai_openclaw_repair_e2e.sh`

## Rollout

- Keep staging-live and production with `PANTHEON_ASSISTANT_KERNEL_ENABLED=false`.
- Deploy to dev with the existing Management AI kernel overlay.
- Verify `GET /bff/assistant/mode` reports kernel enabled and configured control mode on dev.
- Run the local regression tests before PR.
- Run the full remote smoke only with the existing dev control-mode passphrase.
- Monitor `GET /bff/assistant/orchestrator/status` until the task packet receipt is processed.

## Rollback

- Disable `PANTHEON_ASSISTANT_KERNEL_ENABLED` or deactivate control mode to fail closed.
- Disable provider-backed Management NL if provider behavior regresses.
- Revert the task PR to remove archive-location expansion and the regression test.
- Remove any dev-only sentinel file from the prepared repair worktree if a smoke run created it outside the committed repo.
- Do not alter broker, capital, live, or runtime-control state as part of rollback.

## Source Citations

- `management_nl`: /bff/management/ai/conversations/mgmt-ai-openclaw-repair-smoke-20260612T004311Z - Source conversation used for requirement extraction.
- `assistant_mode`: /bff/assistant/mode - Kernel and control-mode status.
- `repair_worktree_prepare`: /bff/assistant/repair-worktrees/prepare - Repair worktree preparation route.
- `management_nl_ask`: /bff/management/nl/ask - Provider invocation and repair metadata forwarding route.
- `dev_docs_generate`: /bff/assistant/dev-docs/generate - SA/SD archive and task packet queue route.
- `orchestrator_status`: /bff/assistant/orchestrator/status - Supervisor/provider/dev-bridge readback.
- `smoke_script`: scripts/smoke_management_ai_openclaw_repair_e2e.sh - End-to-end smoke contract.
- `runbook`: docs/deployment/management-ai-dev-kernel-control-mode.md - Dev-only activation and smoke procedure.
