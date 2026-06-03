# Existing Architecture Assistant Integration Execution Tasks

Date: 2026-06-03
Sprint: `2026-06-03-pantheon-assistant-existing-architecture`
Source plan: `docs/04/pantheon_assistant_kernel_user_2026-05-31/EXISTING_ARCHITECTURE_INTEGRATION_PLAN_2026-06-03.md`
Scope: supervisor/autoworker task materialization for integrating the Management AI helper with existing Pantheon BFF, OpenClaw adapter, and orchestrator surfaces.

## Guardrails

- Reuse existing Pantheon assistant, OpenClaw adapter, and orchestrator architecture.
- Do not introduce a second assistant gateway.
- Do not expose provider credentials or local provider session paths to FE.
- Do not let Web API shell into the VM.
- Do not treat FE recent turns as canonical conversation history.
- Use branch, commit, push, PR, checks, and merge workflow for repo changes.
- FE changes are planned as a later cross-repo task. This task packet itself does not modify execution-plans.

## Task Wave

| Task ID | Owner | Reviewer | Phase | Purpose |
|---|---|---|---|---|
| ASST-INTEG-001 | Codex | Claude | Durable conversation truth | Unify Management AI durable conversation readback with `/bff/assistant` session/transcript surfaces. |
| ASST-INTEG-002 | Codex2 | Claude2 | Context mesh | Extend existing BFF context composer to combine UI hints, BFF read surfaces, and docs/RAG citations. |
| ASST-INTEG-003 | Codex | Claude | Provider routing | Route real provider calls through existing OpenClaw adapter readiness and invoke contracts with honest degraded behavior. |
| ASST-INTEG-004 | Claude | Codex2 | Governed operation tools | Define and implement initial assistant tool contracts on top of existing BFF action catalog, command executor, and audit receipt flow. |
| ASST-INTEG-005 | Claude2 | Codex | SA/SD generator | Build requirement capture, SA, SD, and execution task packet generation using existing docs locations and source refs. |
| ASST-INTEG-006 | Codex2 | Claude | Dev collaboration bridge | Add signed task packet handoff to existing supervisor/autoworker tooling without direct Web API shell. |
| ASST-INTEG-007 | Gemini | Codex | Orchestrator status readback | Expose task, worker, PR, CI, and deploy status to the assistant from existing orchestrator/GitHub surfaces. |
| ASST-INTEG-008 | Copilot | Claude2 | FE context registry and stale-session UX | Future cross-repo FE task for assistant-readable form registry, BFF 404 resync UX, and SSE degraded clarity. |
| ASST-INTEG-009 | Codex | Claude | Security and mode regression | Regression suite for user-mode contraction, control-mode expiry, tool allowlists, redaction, and provider credential non-exposure. |

## Detailed Acceptance

### ASST-INTEG-001 Durable Conversation Truth

Artifacts:

- `services/control-plane/bff/assistant/transcript_store.py`
- `services/control-plane/bff/assistant/routes.py`
- `services/control-plane/bff/management_ai_store.py`
- `services/control-plane/bff/tests/test_assistant_sessions.py`
- `services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py`

Acceptance:

- `/bff/management/nl/ask` and `/bff/management/ai/conversations/{sessionId}` remain the canonical Management AI ask/readback pair.
- `/bff/assistant/sessions/{sessionId}/transcript` no longer becomes a separate in-memory truth in dev/prod.
- Unknown session ids return 404.
- Idempotency-key replay does not create duplicate user or assistant turns.
- BFF restart preserves conversation history in dev.

### ASST-INTEG-002 Context Mesh

Artifacts:

- `services/control-plane/bff/assistant/context_composer.py`
- `services/control-plane/bff/assistant/models.py`
- `services/control-plane/bff/tests/test_assistant_context_pack.py`
- `docs/04/pantheon_assistant_kernel_user_2026-05-31/EXISTING_ARCHITECTURE_INTEGRATION_PLAN_2026-06-03.md`

Acceptance:

- Context pack contains separate UI, BFF read, and docs/RAG source sections.
- FE context remains hint-only and cannot grant access.
- BFF read sources are RBAC and tenant filtered.
- Source refs are present for docs/API snapshots used in provider context.
- Redaction runs before persistence and provider invocation.

### ASST-INTEG-003 Provider Routing

Artifacts:

- `services/control-plane/bff/openclaw_ops_client.py`
- `services/openclaw-gateway-adapter/main.py`
- `services/openclaw-gateway-adapter/assistant_*`
- `scripts/openclaw-assistant-provider-smoke.sh`
- `services/control-plane/bff/tests/test_management_nl_assistant_provider.py`

Acceptance:

- BFF uses existing OpenClaw adapter provider readiness and invoke routes.
- Dev uses real provider when configured.
- Missing provider credentials return explicit degraded provider status.
- Mock fallback is allowed only for local/CI fallback and is clearly labelled.
- Provider credentials and local session paths are never present in FE-visible payloads.

### ASST-INTEG-004 Governed Operation Tools

Artifacts:

- `services/control-plane/bff/action_catalog.py`
- `services/control-plane/bff/command_executor.py`
- `services/control-plane/bff/assistant/tool_*`
- `services/control-plane/bff/tests/test_assistant_security.py`

Acceptance:

- Tools follow preview, validation, confirmation, execute, receipt.
- Low-risk actions can be previewed and executed within RBAC.
- Medium/high-risk actions require reason and confirmation.
- Every execution writes audit receipt and trace id.
- The assistant never submits hidden DOM actions as the authoritative mutation path.

### ASST-INTEG-005 SA/SD Generator

Artifacts:

- `services/control-plane/bff/assistant/dev_docs_*`
- `docs/04/pantheon_assistant_kernel_user_2026-05-31/`
- `docs/02-architecture/`
- `docs/05-ui/`
- `.orchestrator/task-briefs/`

Acceptance:

- Requirement capture can be generated from a Management AI conversation.
- SA output includes current state, roles, flows, data, risk, and acceptance scenarios.
- SD output includes architecture, API, DB/migration, UI, tool/action, tests, rollout, and rollback.
- Generated docs include source citations.
- Generated execution tasks include owner, reviewer, dependencies, artifacts, and acceptance.

### ASST-INTEG-006 Dev Collaboration Bridge

Artifacts:

- `services/control-plane/bff/assistant/dev_bridge_*`
- `scripts/ai_status.py`
- `.orchestrator/supervisor.py`
- `.orchestrator/worker_runner.py`
- `.orchestrator/permission_broker.py`
- `.orchestrator/task-briefs/`

Acceptance:

- BFF emits or stores a signed task packet instead of executing shell directly.
- Packet includes actor, mode, source conversation, source turns, docs, tasks, constraints, and signature.
- Replay protection rejects duplicate signed packets.
- Dispatcher materializes tasks through existing `scripts/ai_status.py` workflow.
- Supervisor/autoworker can pick up generated tasks.

### ASST-INTEG-007 Orchestrator Status Readback

Artifacts:

- `.orchestrator/runtime_state.py`
- `.orchestrator/supervisor.py`
- `scripts/ai_status.py`
- `services/control-plane/bff/assistant/dev_bridge_*`

Acceptance:

- Assistant can read task status, owner, reviewer, blocker, next action, and task brief path.
- Assistant can read worker dispatch state without exposing provider credentials.
- Assistant can report PR, CI, merge, and deploy status when available.
- All status payloads include source refs and snapshot timestamps.

### ASST-INTEG-008 FE Context Registry And Stale-Session UX

Artifacts:

- `execute-plans/src/lib/bff-v1/managementAi.ts`
- `execute-plans/src/management/components/agent/AgentPanelBody.tsx`
- `execute-plans/src/management/**`

Acceptance:

- FE sends assistant-readable route, form, table, filter, selected-row, and validator context.
- BFF 404 on old local-only sessions is shown as stale local session, with clear new-session recovery.
- SSE failure explains whether the source is auth, network, path, or server stream degraded.
- FE still treats BFF conversation readback as source of truth once available.

### ASST-INTEG-009 Security And Mode Regression

Artifacts:

- `services/control-plane/bff/tests/test_assistant_security.py`
- `services/control-plane/bff/assistant/tests/test_user_mode_regression.py`
- `services/openclaw-gateway-adapter/test_*`
- `docs/04/pantheon_assistant_kernel_user_2026-05-31/ASST_KERNEL_007_REPAIR_WORKFLOW.md`

Acceptance:

- User mode cannot access shell, repo write, raw logs, docker, secret store, provider session, or command broker.
- Control/kernel mode requires RBAC, MFA, capability, passphrase, TTL, and idle timeout.
- Passphrase change requires admin plus MFA.
- OpenClaw tool policy remains deny-first.
- Prompt injection attempts cannot expand tools or expose secrets.

## Dispatch

This wave is materialized by:

```bash
python3 scripts/dispatch_assistant_existing_architecture_2026-06-03.py
```

The dispatcher updates sprint metadata, creates the `ASST-INTEG-*` task rows through `scripts/ai_status.py assign`, and lets the existing sync path refresh `ai-status.json`, `current-work.md`, dashboard state, and `.orchestrator/task-briefs/`.
