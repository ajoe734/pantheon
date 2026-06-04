# Pantheon Management Assistant Existing Architecture Integration Plan

Date: 2026-06-03
Owner: Pantheon BFF / OpenClaw / Orchestrator team
Priority: P0 planning archive
Scope: integrate the Management AI helper with the existing Pantheon BFF assistant surfaces, OpenClaw gateway adapter, and supervisor/autoworker system.

## Executive Decision

Do not design a new assistant gateway.

The Platform Admin helper should become a governed assistant by wiring together the assets already present in Pantheon:

- BFF assistant session, context, transcript, mode, and control-mode routes under `services/control-plane/bff/assistant/`.
- Management AI multi-turn ask and conversation readback under `/bff/management/nl/ask` and `/bff/management/ai/conversations/{sessionId}`.
- OpenClaw gateway adapter provider and tool-policy boundary under `services/openclaw-gateway-adapter/`.
- Existing BFF command/action catalog, audit, idempotency, and management read APIs.
- Existing repo orchestration stack: `scripts/ai_status.py`, `.orchestrator/supervisor.py`, `.orchestrator/worker_runner.py`, `.orchestrator/permission_broker.py`, and `.orchestrator/task-briefs/`.
- Existing SA/SD and handoff document locations under `docs/02-architecture/`, `docs/03/`, `docs/04/`, `docs/05-ui/`, and `docs/pantheon-handoffs/`.

The assistant should feel like it lives inside Platform Admin, but its authority remains the current Platform Admin actor plus RBAC, mode policy, redaction, audit, and governed tools.

## Current Assets To Reuse

| Layer | Existing asset | Integration role |
|---|---|---|
| Platform Admin FE | Floating assistant overlay and context hints from execution-plans | Sends route, form, table, selected-row, local recent-turn, summary, and attachment hints. FE remains a UX accelerator, not source of truth. |
| BFF Management AI | `/bff/management/nl/ask` and `/bff/management/ai/conversations/{sessionId}` | Canonical ask path and persisted management conversation readback. |
| BFF Assistant Core | `/bff/assistant/sessions`, `/context`, `/transcript`, `/mode`, `/control-mode` | Session lifecycle, curated context pack, mode policy, and user/kernel/control posture. |
| BFF Assistant Stores | `assistant/transcript_store.py` plus Management AI SQLite store | Needs one durable conversation/session truth instead of parallel in-memory and SQLite truth. |
| BFF Read Surfaces | Control room, jobs, audit, alerts, events, persona/strategy/payment/platform records | Read tools for context mesh, always filtered by actor RBAC and tenant visibility. |
| BFF Write Surfaces | `action_catalog.py`, `command_executor.py`, command confirmations, audit chain | Governed operation tools using preview, validation, confirmation, execute, receipt. |
| OpenClaw Adapter | Provider readiness, provider invoke, command policy, repair workflow | Provider runtime and bounded tool/channel boundary. BFF never exposes provider keys or local sessions to FE. |
| Orchestrator | `scripts/ai_status.py`, supervisor, worker runner, permission broker, task briefs | Dev collaboration bridge for SA/SD task materialization, assignment, worker dispatch, review, PR, CI, deploy tracking. |
| Docs corpus | Existing SA/SD, design, runbook, handoff, and task docs | RAG/context mesh source with citations for design generation and implementation briefs. |

## Non-Goals

- Do not give the browser an LLM account, API key, OAuth directory path, or provider session.
- Do not let Web API routes call shell directly.
- Do not bypass the existing supervisor/autoworker branch, commit, PR, checks, and merge workflow.
- Do not make user-mode assistant access raw logs, repo writes, docker socket, secret stores, or unfiltered backend state.
- Do not treat FE `conversation.recentTurns` as canonical history.
- Do not rebuild Lovable/FE routes as part of this planning materialization.

## Required Integration Corrections

### 1. Conversation And Session Truth

The current durable Management AI conversation store must become the source of truth for management assistant conversations. The older `/bff/assistant/sessions/{sessionId}/transcript` in-memory store is useful for tests and local fallback, but production/dev BFF must not depend on process memory.

Required changes:

- Persist assistant sessions and turns through the same durable store family as `management_ai_sessions` and `management_ai_turns`.
- Keep `/bff/management/nl/ask` as the ask entrypoint for Management AI, while allowing `/bff/assistant/*` transcript readback to either alias the same session or clearly map to it.
- Preserve idempotency for user and assistant turns.
- Return 404 for unknown sessions. Do not return `turns: []` for missing sessions.
- Make stale/local-only FE sessions explicit so the UI can prompt "start a new server session" instead of silently resyncing empty history.

### 2. Context Mesh

The assistant context pack should be composed from three existing source families:

1. UI context hint from FE: route, visible page, form registry, field values, validators, dirty state, selected row, table filters, attachments.
2. BFF read tools: tenant, partner, pricing, payment, reimbursement, notices, feature flags, adapter registry, audit, jobs, runtime and persona state visible to the current actor.
3. Docs/RAG context: SA, SD, runbooks, design handoffs, execution task packets, and repository workflow policy.

Rules:

- FE-provided context is a hint and snapshot, not authority.
- BFF read tools enforce current actor RBAC and tenant visibility before context enters the provider prompt.
- Context packs carry `source_refs` so generated SA/SD and task briefs can cite the source documents or API snapshots.
- Redaction happens before persistence and before provider invocation.

### 3. Real Provider Through Existing OpenClaw Adapter

The assistant should use the existing OpenClaw gateway adapter as the provider runtime boundary.

Required changes:

- Add provider routing on top of the current adapter contract instead of creating a second provider gateway.
- Support real provider modes already planned by the repo: Codex CLI first, Claude CLI as alternate, and API-key providers only when explicitly configured in BFF/runtime secrets.
- Dev should default to real provider when the secret or service-user credential is present. If absent, return explicit degraded provider status instead of pretending mock completion is real.
- Surface readiness metadata such as provider name, degraded reason, timeout, and trace id. Never surface credentials or local credential paths.

### 4. Governed System Operation Tools

The assistant should not "click the DOM" to commit changes. It can help fill forms, but actual platform mutations go through BFF tools.

Required flow:

1. Preview intended mutation.
2. Validate inputs and RBAC.
3. Request confirmation when risk requires it.
4. Execute through `action_catalog.py` and `command_executor.py` or the existing write endpoint.
5. Return a receipt with audit id, trace id, changed entity ids, and SSE/event refs.

Initial tool coverage should follow current Platform Admin modules: tenant governance, partners, pricing, payments and reimbursements, notices, feature flags, adapter registry, audit export, persona/runtime management, and management AI conversation operations.

### 5. Form And Workflow Assistant

FE should expose an assistant-readable form registry in a later execution task. The registry should include field names, labels, validators, option sets, current values, dirty state, errors, and submit action contract.

The assistant may propose and fill values through a FE-mediated patch. The submit still routes through BFF governed actions.

### 6. SA/SD Document Generator

When an operator says a feature should change, the assistant should first capture requirements and then generate design artifacts using the existing docs tree.

Required output families:

- Requirement capture: problem, actors, user intent, affected modules, constraints, open questions, and source conversation refs.
- SA: current state, roles, flows, data, risk, edge cases, and acceptance scenarios.
- SD: architecture, API contract, DB/migration, UI routes/components, tool/action contract, tests, rollout, rollback.
- Execution task packet: supervisor/autoworker tasks with owners, reviewers, dependencies, artifacts, and acceptance gates.

Archive locations:

- SA/SD and planning bundles: `docs/04/...` or canonical layer-specific locations when appropriate.
- UI-specific specs: `docs/05-ui/...`.
- Architecture excerpts: `docs/02-architecture/...`.
- Worker briefs: `.orchestrator/task-briefs/...`.

Every generated artifact must carry source citations from the conversation, context pack, repo docs, or API snapshots used.

### 7. Dev Collaboration Bridge

The Web API should not shell into the VM. The bridge should emit a signed task packet that the existing orchestrator can consume.

The bridge must reuse:

- `scripts/ai_status.py assign/start/progress/done/approve`.
- `.orchestrator/supervisor.py` dispatch and watchdog behavior.
- `.orchestrator/worker_runner.py` provider launch behavior.
- `.orchestrator/permission_broker.py` approval and command restrictions.
- Branch/worktree/PR policies already documented in AGENTS and repository workflow docs.

Signed task packet minimum shape:

```json
{
  "version": "pantheon.assistant.dev-task.v1",
  "intent": "generate_sa_sd_and_dispatch",
  "actor": {
    "id": "operator-id",
    "roles": ["admin"],
    "capabilities": ["assistant.kernel.debug"]
  },
  "mode": "kernel_debug",
  "sourceConversationId": "mgmt-nl-...",
  "sourceTurnIds": ["turn_..."],
  "documents": [
    {
      "path": "docs/04/...",
      "kind": "SA_SD_PLAN",
      "sourceRefs": ["..."]
    }
  ],
  "tasks": [
    {
      "id": "ASST-INTEG-001",
      "owner": "Codex",
      "reviewer": "Claude",
      "artifacts": ["..."],
      "acceptance": ["..."]
    }
  ],
  "constraints": {
    "allowedRepos": ["pantheon"],
    "requiresBranchPrMerge": true,
    "noDirectShellFromWeb": true
  },
  "signature": {
    "keyId": "assistant-bridge-dev",
    "algorithm": "HMAC-SHA256",
    "value": "..."
  }
}
```

The first implementation can materialize task packets through a repo-local dispatcher script, then later expose the same behavior behind BFF once signing, replay protection, and audit are complete.

### 8. Mode And Security Boundary

Default assistant mode remains user mode. Control/kernel mode must require RBAC, MFA, capability, passphrase, TTL, idle timeout, audit, and visible degraded state when missing.

User mode:

- No shell.
- No repo write.
- No raw logs.
- No docker/socket access.
- No provider credential exposure.
- Only curated BFF reads and governed actions.

Kernel/debug/repair mode:

- Time-boxed and idle-expiring.
- Uses OpenClaw tool policy and repair workflow guardrails.
- Uses branch/commit/PR/checks/merge for repository changes.
- Writes audit receipts and source refs.

## Delivery Sequence

| Phase | Outcome |
|---|---|
| M1 Durable assistant truth | Management AI and `/bff/assistant` session/transcript surfaces share durable server-side truth. |
| M2 Context mesh | Context packs combine UI hints, BFF read tools, and docs/RAG citations with redaction and RBAC. |
| M3 Provider routing | Real provider invocation uses existing OpenClaw adapter readiness and invoke contracts with honest degraded fallback. |
| M4 Governed tools | Assistant can preview, validate, confirm, execute, and receipt low-risk Platform Admin operations through BFF actions. |
| M5 SA/SD generator | Assistant can turn a feature-change conversation into archived SA/SD docs and source-cited task packets. |
| M6 Orchestrator bridge | Signed task packets dispatch supervisor/autoworker tasks and provide PR/CI/deploy status readback. |
| M7 User-mode contraction | Product-safe user mode is regression-tested against shell, raw log, repo, secret, and provider-session leaks. |

## End-To-End Target Scenario

1. Operator opens Platform Admin and asks the floating helper: "這個功能要改成 X".
2. FE sends session id, local recent turns, UI snapshot, form registry, and attachments as hints.
3. BFF loads complete server-side conversation history and creates a redacted context pack from allowed FE, BFF, and docs sources.
4. Provider runs through OpenClaw adapter. The helper asks clarification or drafts a requirement capture.
5. Operator asks it to produce SA/SD and execution tasks.
6. BFF or a repo-local dispatcher archives SA/SD docs with source citations.
7. Dev collaboration bridge creates signed task packets and materializes tasks through `scripts/ai_status.py`.
8. Supervisor dispatches owners and reviewers. Auto workers work in branches/worktrees and produce PRs.
9. Assistant reads task, PR, CI, and deploy status from orchestrator/GitHub surfaces and reports progress in the same conversation.

## Acceptance Gates

1. A long Management AI conversation survives browser refresh, BFF restart, and FE local storage loss.
2. `/bff/assistant/*` transcript/session behavior is not a separate in-memory truth in dev/prod.
3. Context packs include UI, BFF read, and docs/RAG source refs while respecting RBAC and redaction.
4. Provider status is honest: real provider when configured, explicit degraded when not, never fake mock success in dev acceptance.
5. System operation tools execute only through governed BFF actions and produce receipts.
6. SA/SD generation produces archived documents with citations and an execution task packet.
7. Dev collaboration dispatch uses existing supervisor/autoworker infrastructure and never shells directly from Web API.
8. User-mode regression proves no shell, repo write, raw log, docker, secret, or provider-session access.

## M4 Delivery Record (ASST-INTEG-004)

Milestone M4 (Governed tools) delivered via PR #841 and PR #870.

**Delivered artifacts:**
- `services/control-plane/bff/assistant/tool_contracts.py`: governed tool contract layer with
  preview → validate → confirm → execute → receipt flow
- `services/control-plane/bff/command_executor.py`: AUDIT_EXPORT executor added to dispatch table
- `services/control-plane/bff/assistant/routes.py`: governed tool HTTP endpoints with
  `payload.get("confirmed") is True` gate
- `services/control-plane/bff/tests/test_assistant_security.py`: 28 tests covering allowlist
  denial, RBAC, confirmation gate (including non-bool regression tests), and receipt shape

**Verified:** `python3 -m pytest services/control-plane/bff/tests/test_assistant_security.py -q`
→ 28 passed. Strict confirmed gate: `confirmed is not True` rejects `confirmed='false'`,
`confirmed=1`, and all truthy non-True values at both HTTP route and contract boundary.

**Reviewer:** Codex2. Review approval recorded 2026-06-04T04:39:38Z.
**Closeout:** Owner Claude finalized task ASST-INTEG-004 on 2026-06-04. PR #1021 carries the delivery record into dev.
