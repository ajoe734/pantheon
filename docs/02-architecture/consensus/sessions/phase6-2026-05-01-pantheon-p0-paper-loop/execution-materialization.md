# Execution Materialization

This file bridges the docs/04 P0 planning seed into supervisor-managed execution tasks.

Materialization is blocked until document reconciliation, LLM readouts, consensus, and human gate are accepted.

## P0

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| P0-EXEC-ADR-001 | Codex | Claude | - | 0 | Official `pantheon/lean` bridge ADR and repo mapping |
| P0-CI-BRIDGE-001 | Codex | Codex2 | P0-EXEC-ADR-001 | 0 | Submodule authority and no-wrong-repo target CI |
| P0-BOOT-001 | Codex | Codex2 | P0-CI-BRIDGE-001 | 1 | `DeploymentPlan + RuntimeBinding -> RuntimeBootstrapRequest` |
| P0-CTX-001 | Codex2 | Codex | P0-BOOT-001 | 1 | `PantheonRuntimeContext` model and validation |
| P0-CTX-002 | Codex | Codex2 | P0-CTX-001 | 1 | `runtime_bootstrap.py` launch manifest/env wiring |
| P0-LEAN-CTX-001 | Codex2 | Claude | P0-CTX-001 | 1 | `PantheonAlgoBase` context access and event attachment |
| P0-TEL-001 | Codex | Codex2 | P0-CTX-002,P0-LEAN-CTX-001 | 2 | Paper telemetry emitter and ingest validation |
| P0-TEL-PROJ-001 | Codex | Claude | P0-TEL-001 | 2 | Runtime status projection from paper heartbeat |
| P0-LOOP-001 | Codex | Gemini | P0-TEL-PROJ-001 | 3 | Minimum paper operating loop smoke |
| P0-REC-001 | Codex2 | Codex | P0-LOOP-001 | 3 | Basic paper reconciliation record and incident threshold seed |
| P0-STATE-001 | Codex2 | Claude | - | 1 | Artifact/deployment/runtime invariant tests |
| P0-BFF-CMD-001 | Codex | Claude | P0-STATE-001 | 4 | BFF read/command split and command audit contract |
| P0-FE-DEMO-001 | Copilot | Codex | - | 4 | Front staging/prod demo auth and demo-island guard |
| P0-FE-SOURCE-001 | Copilot | Claude | P0-FE-DEMO-001,P0-TEL-PROJ-001 | 4 | Source mode and runtime identity on critical UI |
| P0-LIVE-GUARD-001 | Codex | Gemini | P0-BOOT-001 | 0 | Live fail-closed and bracket `logged_only` honesty tests |
| P0-CI-BOUNDED-001 | Codex | Copilot | P0-CI-BRIDGE-001 | 0 | Source/search bounded CI and fail-closed adapter CI |
| P0-HEALTH-001 | Codex | Claude | P0-CI-BRIDGE-001 | 0 | Health endpoint cleanup scan |

## P1

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| P1-BRACKET-001 | Codex | Gemini | P0-LIVE-GUARD-001 | 5 | Guarded paper/sim bracket order execution |
| P1-LIVE-PLAN-001 | Claude | Codex | P0-LOOP-001 | 5 | Canary/live activation criteria and runbook |
| P1-SEARCH-001 | Copilot | Codex2 | P0-CI-BOUNDED-001 | 5 | OpenClaw governed SearchGateway integration |
| P1-SOURCE-001 | Copilot | Claude | P1-SEARCH-001 | 6 | News/social/alpha DB connector expansion |
| P1-PERSIST-001 | Codex | Claude | P0-CI-BOUNDED-001 | 5 | Staging/prod Postgres and object store posture guard |
| P1-KILL-001 | Codex2 | Gemini | P0-TEL-PROJ-001 | 6 | KillSwitchBridge secondary path and telemetry ack |
| P1-EVO-001 | Claude | Codex2 | P0-REC-001 | 6 | Postmortem evidence and governed evolution dispatcher baseline |

## P2

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| P2-LIVE-KERNEL-001 | Gemini | Claude | P1-LIVE-PLAN-001,P1-KILL-001 | 7 | Full Lean Launcher + broker SDK production readiness plan |
| P2-BROKER-SANDBOX-ORDER-001 | Codex2 | Codex | P2-LIVE-KERNEL-001 | 7 | Broker paper/sandbox/test-key order API smoke before production live side effects |
| P2-OSS-ACTIVATE-001 | Codex | Copilot | P0-CI-BOUNDED-001 | 7 | Research OSS production data posture and activation; no direct order routing |
