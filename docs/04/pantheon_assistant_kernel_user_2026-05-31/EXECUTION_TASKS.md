# Assistant OpenClaw Gateway Execution Tasks

Date: 2026-05-31
Scope: supervisor/autoworker task materialization for the assistant kernel/user plan after the OpenClaw gateway credential-mount adjustment.

## Planning Adjustment

Use the existing `openclaw-gateway-adapter` and OpenClaw gateway container as the provider runtime boundary. Codex/Claude account-login support is delivered by mounting a dedicated service-user `.codex` / `.claude` OAuth directory into the gateway container and invoking the provider CLI inside that container.

This path intentionally avoids API keys and avoids building a new standalone assistant gateway first. It also means the first execution wave must prove CLI binary path/version, mount ownership, auth readiness, and credential refresh behavior before kernel debug is treated as reliable.

## Task Wave

| Task ID | Owner | Reviewer | Purpose |
|---|---|---|---|
| ASST-KERNEL-001 | Codex | Claude | BFF context-pack schema and route |
| ASST-KERNEL-002 | Codex2 | Claude | Redaction before provider and transcript persistence |
| ASST-KERNEL-003 | Claude | Codex | Assistant session and transcript store |
| ASST-OCGW-001 | Gemini | Codex | OpenClaw gateway credential mount compose/env contract |
| ASST-OCGW-002 | Gemini2 | Codex2 | Gateway CLI image install/path/version/readiness probes |
| ASST-OCGW-003 | Codex | Claude | Codex CLI provider inside OpenClaw gateway |
| ASST-OCGW-004 | Claude | Codex | Claude CLI provider inside OpenClaw gateway |
| ASST-OCGW-005 | Gemini | Claude2 | done · Credential refresh smoke/runbook + Claude readiness bugfix |
| ASST-KERNEL-006 | Codex2 | Claude | OpenClaw command broker observe/debug allowlists |
| ASST-KERNEL-007 | Gemini | Codex | Repair-mode worktree workflow guardrails |
| ASST-BFF-001 | Claude | Codex2 | `/bff/agora/ask` assistant-backed flow |
| ASST-BFF-002 | Claude2 | Codex | `/bff/management/nl/ask` provider option |
| ASST-FE-001 | Copilot | Codex2 | Ask Personas BFF/SSE live wiring |
| ASST-FE-002 | Copilot | Claude2 | Assistant mode and provider UI signals |
| ASST-SEC-001 | Codex2 | Claude | Security regression suite |
| ASST-USER-001 | Claude | Codex2 | Product-safe user-mode contraction |

## Hard Acceptance Gates

1. No browser or BFF response exposes provider credentials, session files, or mounted paths beyond sanitized readiness metadata.
2. The mounted OAuth directories are dedicated service-user directories, not a human operator's personal home.
3. The OpenClaw gateway reports provider CLI path, version, auth status, mount mode, and refresh posture.
4. Missing/expired auth degrades provider status and keeps deterministic fallback available.
5. Kernel debug commands go through OpenClaw tool/workflow policy and remain deny-first.
6. Repair mode follows task branch/worktree, validation, commit, PR, checks, and merge workflow.
7. User mode has no shell, raw log, repo, command broker, or provider-session capability.
