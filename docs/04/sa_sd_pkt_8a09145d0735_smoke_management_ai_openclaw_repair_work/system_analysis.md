# SA: Smoke Management AI OpenClaw Repair Work

- **Packet ID**: `pkt_8a09145d0735`
- **SA ID**: `sa_smoke_management_ai_openclaw_repair_work`
- **Capture ID**: `req_3e2d36061ef6`
- **Generated at**: 2026-06-12T00:43:11Z

## Current State

The BFF already exposes the control-mode surface, Management NL provider route, SA/SD generator, signed DevTaskPacket bridge, and OpenClaw adapter client. The missing closure for this smoke is a task-scoped proof that these pieces compose into one repair workflow: control mode enables `kernel_repair`, BFF prepares a clean task worktree through the adapter, Management NL forwards the returned repair metadata to OpenClaw/Codex, generated SA/SD artifacts are archived, and the DevTaskPacket is queued for supervisor drain.

## Roles

- Operator or admin: activates dev-only kernel repair mode and starts the smoke.
- BFF assistant routes: enforce control mode, skill authorization, archive writes, and task packet queueing.
- BFF Management NL route: builds the context pack and invokes the configured assistant provider.
- OpenClaw gateway adapter: owns provider execution and repair worktree preparation.
- Codex provider: writes only inside the declared task worktree scope.
- Supervisor: drains signed DevTaskPackets from `.orchestrator/assistant-dev-packets`.
- Codex task owner: implements and finalizes this repo task.
- Claude reviewer: reviews the task artifacts and tests.

## Flows

- Operator activates `kernel_repair` through `POST /bff/assistant/control-mode/activate`.
- Frontend or smoke script calls `POST /bff/assistant/repair-worktrees/prepare` with repo key, task id, expected branch, merge target, and declared scope.
- BFF validates active repair control mode and delegates the normalized payload to `POST /api/openclaw-adapter/assistant/repair-worktrees/prepare`.
- Management NL calls `POST /bff/management/nl/ask` with `openclaw.repair` metadata from the prepare response.
- BFF context composer includes Management NL, orchestrator, repo status, and BFF read surfaces in the provider context pack.
- OpenClaw/Codex runs in the task worktree and returns provider status with `workspaceClass=task_worktree`.
- BFF calls `POST /bff/assistant/dev-docs/generate` with `archive=true` and `queueTaskPacket=true`.
- Supervisor reports the queued packet as processed through `GET /bff/assistant/orchestrator/status`.

## Data

- Control-mode activation: actor, mode, capabilities, TTL, idle TTL, management session id.
- Repair metadata: `repo_key`, `task_id`, `task_worktree`, `declared_scope`, `expected_branch`, `remote`, and `merge_target`.
- Provider context pack: Management NL conversation, UI snapshot, orchestrator status, repo status, and source refs.
- Sentinel proof: repo-relative path under `tmp/management-ai-openclaw-smoke`.
- SA/SD archive: docs under `docs/04`, architecture note under `docs/02-architecture`, UI flow note under `docs/05-ui`, and task brief under `.orchestrator/task-briefs`.
- DevTaskPacket receipt: packet id, queue path, signature, documents, tasks, and supervisor processed status.

## Risk

- If BFF accepts repair metadata without active `kernel_repair`, Management AI could imply write authority outside the intended dev gate.
- If `declared_scope` is too broad or unvalidated, provider writes could escape the intended repo paths.
- If Management NL drops the repair metadata, provider readiness may look healthy while actual task-worktree writes are not proven.
- If generated SA/SD docs omit source citations, downstream workers cannot audit why the task exists.
- If the task packet queues into a different status root from the supervisor drain root, the smoke may pass generation but fail dispatch.
- If frontend wording suggests shell access, the operator UI would misrepresent the governed BFF action model.

## Edge Cases

- Control mode active as `kernel_debug` instead of `kernel_repair`.
- Missing or expired control-mode passphrase.
- Adapter unavailable or returning a non-clean worktree.
- Provider completes but reports a non-task workspace class.
- Sentinel file missing or content mismatch after provider completion.
- DevTaskPacket queued but not drained before the polling deadline.
- Idempotency key replay with a changed payload.

## Acceptance Scenarios

- Requirement capture includes problem, actors, intent, modules, constraints, and source refs.
- SA includes current state, roles, flows, data, risk, and acceptance scenarios.
- SD includes architecture, API, DB/migration, UI, tool/action, tests, rollout, and rollback.
- Generated docs include source citations from the conversation and context pack.
- Execution tasks include owner, reviewer, dependencies, artifacts, and acceptance.
- Artifacts land in `docs/04/`, `docs/02-architecture/`, `docs/05-ui/`, and `.orchestrator/task-briefs/`.
- Regression tests prove the adapter prepare contract, BFF prepare route delegation, archive locations, and queued DevTaskPacket document set.

## Source Citations

- `management_nl`: /bff/management/ai/conversations/mgmt-ai-openclaw-repair-smoke-20260612T004311Z - Source conversation used for requirement extraction.
- `assistant_mode`: /bff/assistant/mode - Kernel and control-mode status.
- `repair_worktree_prepare`: /bff/assistant/repair-worktrees/prepare - Repair worktree preparation route.
- `management_nl_ask`: /bff/management/nl/ask - Provider invocation and repair metadata forwarding route.
- `dev_docs_generate`: /bff/assistant/dev-docs/generate - SA/SD archive and task packet queue route.
- `orchestrator_status`: /bff/assistant/orchestrator/status - Supervisor/provider/dev-bridge readback.
- `smoke_script`: scripts/smoke_management_ai_openclaw_repair_e2e.sh - End-to-end smoke contract.
- `runbook`: docs/deployment/management-ai-dev-kernel-control-mode.md - Dev-only activation and smoke procedure.
