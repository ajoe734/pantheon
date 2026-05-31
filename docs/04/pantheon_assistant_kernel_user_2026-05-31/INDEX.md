# Pantheon Assistant Kernel/User Mode — Supplemental Index

Date archived: 2026-05-31
Source: operator request for an LLM helper that can see frontend context and backend operating state, initially with expanded debug authority and later narrowed to product user mode.
Document tier: L2/L3 supplemental SA/SD bundle.
Conflict rule: This bundle does not override L1 canonical architecture, SD-11 BFF/Console Integration, SD-12 Cross-Cutting Foundations, BFF API contracts, or repository workflow policy. If conflict exists, follow the canonical files.

## Documents

| File | Role | Focus |
|---|---|---|
| [SA_assistant_kernel_user_mode.md](SA_assistant_kernel_user_mode.md) | Supplemental System Analysis | Product/system framing, current-state assessment, kernel-mode rationale, risk register, mode ladder, delivery milestones |
| [SD_assistant_kernel_user_mode.md](SD_assistant_kernel_user_mode.md) | Supplemental System Design | Concrete backend/frontend architecture, APIs, data contracts, command broker, Codex/Claude CLI bridge, rollout and validation plan |

## Executive Decision

Pantheon should implement the assistant as an internal, governed debug assistant first, then narrow it into a product-facing user assistant.

The first version may use **kernel mode** for developer/operator collaboration:

- can inspect frontend route state and selected entities;
- can read curated BFF backend state;
- can read sanitized logs and service health;
- can run bounded diagnostic commands through a command broker;
- can propose or, with explicit mode and policy, apply repository fixes through the normal branch/commit/PR flow.

The product version must run in **user mode**:

- no shell;
- no repository write access;
- no raw logs;
- no provider account/session exposure to the browser;
- only BFF-curated context packs and read-only backend summaries;
- all actions routed through existing BFF command, approval, idempotency, and audit surfaces.

## Mode Ladder

| Mode | Audience | Capability | Default State |
|---|---|---|---|
| `observe` | operators and developers | Frontend context plus curated BFF read surfaces | Allowed in both kernel and user mode |
| `debug` | developers/operators | Observe plus sanitized logs, health probes, targeted test commands, code search | Kernel mode only |
| `repair` | developers/operators | Debug plus repo edits, service restarts in dev/staging, validation commands | Kernel mode only, audited and time-boxed |
| `user` | product users/operators | Observe-only answer generation and guided next-step suggestions | Product default |

## Current Repo Evidence Used

The analysis in this bundle is based on the following existing surfaces:

- `execute-plans` Ask Personas page is still mock-response driven and must be wired to BFF before it can be a real assistant.
- Pantheon BFF already has `POST /bff/agora/ask`, but it currently records the user message and command receipt rather than calling an LLM.
- Pantheon BFF already has `POST /bff/management/nl/ask`, but it currently synthesizes deterministic management summaries rather than calling an LLM.
- Pantheon BFF already exposes useful backend observability surfaces: `/bff/v5/control-room`, `/bff/v5/execution/persona-health`, `/bff/v5/execution/strategy-health`, `/bff/jobs`, `/bff/alerts`, `/bff/audit`, and `/bff/events/stream`.
- The existing orchestrator already demonstrates local Codex and Claude CLI invocation patterns, but those worker settings are too broad for a product assistant and must not be copied directly.

## Provider Decision

Provider auth must stay server-side.

The browser must never receive ChatGPT, Codex, Claude, Anthropic, OpenAI, or CLI session credentials. The assistant should call a local `llm-bridge` service that runs under a dedicated OS service user with a pre-authenticated CLI home directory.

Preferred provider order for first implementation:

1. Codex CLI POC, because the local machine already has a ChatGPT-authenticated Codex CLI path.
2. Claude Code CLI as an alternate provider once a dedicated service-user login is confirmed.

Both providers are used in non-interactive CLI mode. This avoids API keys for the requested first phase, while preserving an internal boundary between product/BFF code and provider sessions.

Reference docs, checked on 2026-05-31:

- OpenAI Codex authentication: https://developers.openai.com/codex/auth
- OpenAI Codex non-interactive mode: https://developers.openai.com/codex/noninteractive
- Claude Code getting started: https://docs.anthropic.com/en/docs/claude-code/getting-started
- Claude Code CLI reference: https://docs.anthropic.com/en/docs/claude-code/cli-reference

## Delivery Sequence

| Milestone | Name | Outcome |
|---|---|---|
| M0 | Planning bundle | This SA/SD bundle lands and becomes the implementation guide |
| M1 | Kernel context pack | BFF can create a backend/frontend context pack for an assistant session |
| M2 | Kernel debug gateway | Internal assistant can call Codex/Claude CLI and run bounded diagnostics through a command broker |
| M3 | Ask UI live wiring | `execute-plans` Ask Personas / management helper calls BFF and streams assistant responses |
| M4 | Repair flow | Kernel assistant can propose and optionally apply fixes only through repo workflow and explicit approvals |
| M5 | User-mode contraction | Product assistant is restricted to curated context packs and read-only guidance |
| M6 | Production readiness | Audits, redaction, mode gates, fallback, and operational runbooks are complete |

## Non-Goals

- Do not expose provider account login to the frontend.
- Do not let the assistant directly connect to databases, secret stores, broker APIs, or live capital paths.
- Do not reuse autonomous worker bypass settings for an operator-facing assistant.
- Do not treat a CLI-login-backed assistant as a public multi-tenant SaaS model-serving backend.

## Immediate Next Implementation Tasks

| Task ID | Title | Repo | Notes |
|---|---|---|---|
| ASST-KERNEL-001 | Implement assistant context-pack schema and BFF route | `pantheon` | Read-only; compose current UI context plus allowlisted backend surfaces |
| ASST-KERNEL-002 | Implement `llm-bridge` provider adapter for Codex CLI | `pantheon` | Dedicated `CODEX_HOME`, timeout, redaction, audit |
| ASST-KERNEL-003 | Implement command broker observe/debug allowlists | `pantheon` | No destructive commands; no secret dump; record every command |
| ASST-KERNEL-004 | Wire `/bff/agora/ask` to assistant session lifecycle | `pantheon` | Preserve command receipt and transcript |
| ASST-FE-001 | Replace Ask Personas mock response with BFF call and SSE | `execute-plans` | Add typed POST path and stream handling |
| ASST-USER-001 | Add user-mode policy that disables shell/log/repo capabilities | both | Product mode must be read-only and BFF-curated |

## Acceptance Summary

This bundle is accepted when:

1. Kernel-mode and user-mode boundaries are explicit.
2. Backend visibility is routed through BFF context packs, not direct LLM access.
3. Codex/Claude account-login CLI use is isolated behind a server-side bridge.
4. High-risk capabilities are brokered, audited, time-boxed, and mode-gated.
5. There is a concrete implementation sequence from current mock helper to real assistant.
6. There is a clear path to contract the assistant from kernel mode to user mode without rebuilding the feature.
