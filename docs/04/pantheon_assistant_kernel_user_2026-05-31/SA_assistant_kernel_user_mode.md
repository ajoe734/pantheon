# Pantheon Assistant Kernel/User Mode — Supplemental SA

Date: 2026-05-31
Audience: Pantheon / execute-plans development team, operators, runtime maintainers, and reviewers.
Scope: Assistant capability that can answer questions from the frontend while also observing backend operating state. The initial delivery intentionally supports internal kernel-mode debugging; the final product mode must be user-mode only.
Baseline repositories: `pantheon@origin/dev`, `execute-plans@main` as inspected on 2026-05-31.
Document tier: L2/L3 supplemental System Analysis.
Conflict rule: This document does not override `TARGET_ARCHITECTURE.md`, SD-11, SD-12, BFF contracts, live-ops rules, or repository workflow rules.

---

## 0. Executive Summary

Pantheon needs a small helper that can collaborate on debugging, not merely answer generic frontend questions. The right architecture is not "put a model in the browser." The right architecture is:

```text
execute-plans assistant UI
-> Pantheon BFF assistant/session routes
-> assistant context pack
-> assistant debug gateway
-> Codex or Claude CLI bridge
-> audited response / command / transcript
```

The first phase can deliberately run in **kernel mode** for trusted internal development and operations. Kernel mode may inspect backend state, read sanitized logs, run bounded diagnostics, and help produce fixes. It must still be session-scoped, audited, and brokered.

The product phase must contract into **user mode**. User mode sees only BFF-curated read surfaces and cannot run shell commands, read raw logs, mutate files, restart services, or touch provider credentials.

The core design goal is therefore not "maximum access forever." It is **temporary, governed expansion followed by deliberate contraction**.

---

## 1. Problem Statement

The current helper concept is too narrow if it only sees frontend text. Operators and developers often ask questions whose answer lives across multiple layers:

- What page is the user on?
- Which entity is selected?
- Is BFF live or falling back?
- Did a job fail?
- Are alerts active?
- Did audit record the action?
- Is a runtime degraded?
- Did an SSE event arrive?
- Is the code path wired or still mock?

An assistant that cannot inspect backend state will hallucinate or give generic advice. An assistant that can freely inspect the machine can leak secrets or cause damage. Pantheon needs a middle path: **BFF-curated visibility plus brokered kernel tools**.

---

## 2. Definitions

| Term | Definition |
|---|---|
| Assistant | The product/helper capability exposed through execute-plans and backed by Pantheon services. |
| Kernel mode | Internal debug/ops mode with expanded visibility and brokered diagnostics. Not product default. |
| User mode | Product-safe mode with BFF-curated read-only context and no shell/repo/log authority. |
| Context pack | A structured, sanitized snapshot of frontend context and backend read surfaces assembled by BFF. |
| Debug gateway | Internal service that mediates kernel-mode sessions, provider calls, command execution, audit, redaction, and timeout policy. |
| Command broker | Narrow executor that allows only approved diagnostic/repair commands and records every invocation. |
| LLM bridge | Provider adapter that invokes Codex CLI or Claude Code CLI under a service account. |
| Provider session | Account-login CLI state such as `CODEX_HOME` or `CLAUDE_CONFIG_DIR`; never exposed to the browser. |

---

## 3. Current State Assessment

### 3.1 Frontend Helper

The `execute-plans` Ask Personas page currently generates local mock responses. It does not yet call `POST /bff/agora/ask` and does not consume assistant SSE deltas.

Implication: the frontend must be rewired before any provider work becomes visible to users.

### 3.2 Existing BFF Assistant-Like Routes

Pantheon BFF already has two useful entry points:

| Route | Current behavior | Gap |
|---|---|---|
| `POST /bff/agora/ask` | Creates/persists an Agora ask session/message command receipt | Does not call LLM or stream generated assistant content |
| `POST /bff/management/nl/ask` | Builds deterministic management answer from BFF surfaces | Does not call LLM; no provider bridge |

These are good integration points. They should not be bypassed by a new frontend-only provider client.

### 3.3 Existing Backend Observability Surfaces

Pantheon BFF already exposes enough state to form an initial context pack:

| Surface | Use |
|---|---|
| `/bff/v5/control-room` | Closed-loop overview: loops, interventions, sentinel, OODA status |
| `/bff/v5/execution/persona-health` | Persona execution health |
| `/bff/v5/execution/strategy-health` | Strategy execution health |
| `/bff/jobs` and `/bff/jobs/{id}/logs` | Job state and bounded logs |
| `/bff/alerts` | Active operator alerts |
| `/bff/audit` and `/bff/audit/entities/{type}/{id}` | Governance and action trace |
| `/bff/events/stream` | Realtime state changes and assistant deltas |

### 3.4 Existing Provider Invocation Patterns

Pantheon already has orchestrator adapters for Codex and Claude CLI. They prove feasibility:

- Codex can run in non-interactive `codex exec` mode.
- Claude Code can run in `claude -p` mode with stream-json output.
- Local Codex CLI is installed and authenticated through ChatGPT on the inspected machine.
- Claude Code binary exists locally, but a dedicated service-user auth check still needs to be performed.

The orchestrator's current worker settings are not product-safe. They are designed for autonomous repo workers and may include broad workspace write or bypass behavior. The assistant needs its own gateway and policy.

---

## 4. Goals

1. Give the helper enough backend visibility to debug real frontend/backend integration issues.
2. Keep provider account sessions server-side and isolated.
3. Preserve BFF as the only frontend-facing backend aggregation boundary.
4. Make kernel mode useful for trusted internal debugging.
5. Make user mode safe enough for product/operator usage.
6. Record every assistant session, context pack, provider run, command, answer, and high-risk refusal.
7. Preserve the existing Pantheon branch/commit/PR/checks flow for any repo changes produced by the assistant.

---

## 5. Non-Goals

- No direct browser-to-Codex or browser-to-Claude session.
- No API-key implementation for the first requested account-login path.
- No direct LLM database credentials.
- No raw production secret access.
- No unrestricted root shell.
- No autonomous live trading, live broker, or capital-binding mutation.
- No replacement of SD-11 BFF command facade or SD-12 RBAC/audit/idempotency foundations.

---

## 6. Stakeholders

| Stakeholder | Needs |
|---|---|
| System developer | Fast debugging across frontend, BFF, services, tests, and logs |
| Operator | Clear answer grounded in current backend state |
| Reviewer | Audit trail for assistant actions and generated fixes |
| Security owner | Secret redaction, least privilege, and account-session isolation |
| Product owner | User-mode helper that can explain state without creating operational risk |
| Runtime owner | No assistant bypass around runtime-manager, safe mode, or live gates |

---

## 7. Mode Ladder

### 7.1 Observe

Observe is read-only and can be shared by kernel and user modes.

Capabilities:

- receive frontend route, selected entity, panel, and visible errors;
- read BFF allowlisted surfaces;
- inspect recent SSE events from BFF buffers;
- cite source routes and snapshot timestamps;
- answer with confidence and missing-evidence markers.

### 7.2 Debug

Debug is kernel-only.

Capabilities:

- code search through approved workspaces;
- read configuration except secret files;
- run health probes;
- run `git status`, `rg`, targeted tests, static checks;
- inspect sanitized logs;
- produce diagnosis and proposed fix.

### 7.3 Repair

Repair is kernel-only and must remain explicit.

Capabilities:

- edit repository files in a task branch or clean worktree;
- run validation;
- restart dev/staging services where allowed;
- commit, push, open PR, wait for checks, and merge when policy allows.

Repair must not include:

- destructive git operations without explicit human instruction;
- production DB writes;
- secret dumping;
- live-capital actions;
- unapproved live restarts outside the live repair policy.

### 7.4 User

User mode is the product default.

Capabilities:

- context-pack Q&A;
- source-backed explanations;
- guided next-step suggestions;
- link to relevant UI pages and BFF evidence;
- propose actions that require existing BFF command/approval flows.

No shell, no repo, no raw logs, no provider session access.

---

## 8. Trust Boundaries

| Boundary | Rule |
|---|---|
| Browser -> BFF | Browser sends user question and UI context only |
| BFF -> Context pack | BFF chooses and sanitizes backend sources |
| Context pack -> LLM | LLM receives only structured context, not secrets or raw machine access |
| LLM -> Command broker | LLM can request commands; broker decides allow/deny |
| Command broker -> OS | Only allowlisted commands under service user and workspace policy |
| Provider session -> System | Provider login lives in service-user home with restricted permissions |
| Assistant -> Live systems | No live side effect except through existing Pantheon authority paths |

---

## 9. Account-Login Provider Position

The requested "not API key, use account login" path is feasible only as a local/server-side CLI bridge:

```text
dedicated OS user
-> pre-authenticated CLI home
-> non-interactive CLI invocation
-> gateway captures output
```

This is appropriate for internal tools on controlled infrastructure. It is brittle for public product serving because account sessions can expire, quotas can be user-plan dependent, and provider terms or intended use may differ from API usage. The architecture therefore keeps this behind an internal bridge and leaves room to swap to official API/service auth later if needed.

---

## 10. Context Pack Requirements

Every assistant answer should be grounded in a context pack.

Minimum context:

- `question`;
- `actor`;
- `mode`;
- `frontend.route`;
- `frontend.selected_entity`;
- `backend.snapshot_at`;
- BFF source payloads;
- recent SSE events;
- relevant audit refs;
- staleness metadata;
- redaction summary;
- source citations.

The assistant must be allowed to say "I cannot see that source" when the pack lacks evidence. It must not pretend to know backend state.

---

## 11. Risk Register

| Risk | Severity | Mitigation |
|---|---|---|
| Provider account session leaks | Critical | Service-user home permissions, no browser exposure, no log echo of env/session files |
| Prompt injection through logs or UI text | High | Treat logs/user text as untrusted data; delimit context; instruct model not to follow embedded commands |
| Destructive command execution | Critical | Command broker denylist plus allowlist, no root, no direct shell for user mode |
| Secret leakage from logs | Critical | Redaction before LLM, block `.env`, token, cookie, key material |
| Kernel mode becomes permanent product default | High | Feature flag, TTL, RBAC, explicit downgrade milestone |
| Stale backend context | Medium | Snapshot timestamps, staleness markers, confidence scoring |
| Repo changes bypass workflow | High | Repair mode must use clean task branch/worktree and PR flow |
| Live side effects | Critical | Deny live broker/capital/runtime mutations except existing authority paths |
| Provider quota/session expiry | Medium | Degraded fallback, provider status health, manual re-login runbook |

---

## 12. Delivery Milestones

| Milestone | Scope | Acceptance |
|---|---|---|
| M1 Context pack | BFF builds structured assistant context from UI context and backend read surfaces | Context pack has sources, staleness, redaction summary, tests |
| M2 Kernel bridge | Debug gateway invokes Codex CLI with account session and returns streamed answer | Provider status, timeout, audit, fallback |
| M3 Command broker | Observe/debug command allowlist works | Commands recorded; denylist tested |
| M4 Frontend wiring | Ask Personas and/or management helper calls BFF and streams response | No mock response in live mode |
| M5 Repair workflow | Kernel repair can create scoped repo changes through normal workflow | Branch, commit, PR, checks, merge policy |
| M6 User mode | Same assistant runs with read-only BFF context only | Shell/log/repo capabilities disabled |

---

## 13. Open Questions

1. Which environment should host the first dedicated service user: local dev VM, staging VM, or both?
2. Should Claude Code be enabled in M2 or deferred until Codex POC is stable?
3. Which roles can start kernel sessions: admin, developer, operator, or a new `assistant.kernel` capability?
4. What is the default TTL for kernel mode: 30 minutes or 60 minutes?
5. Which service restarts are safe in repair mode, and which always require manual approval?

---

## 14. Analysis Conclusion

The assistant should be built as a mode-aware operational companion:

1. Kernel mode first, because the immediate need is collaborative debugging.
2. BFF-curated context always, because backend visibility must be explainable and auditable.
3. CLI bridge behind a service account, because account-login providers cannot safely live in the browser.
4. User mode later, because the same context-pack architecture can be narrowed without rewriting the assistant.

The important architectural move is to separate **what the assistant can reason about** from **what the assistant can do**. It can reason over rich backend context early; it should only do actions through brokered, audited, mode-gated paths.
