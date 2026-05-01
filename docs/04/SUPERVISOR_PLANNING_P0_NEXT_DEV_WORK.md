# Pantheon P0 Supervisor Planning — Next Development Work

Date: 2026-05-01
Source: `docs/04/pantheon_sa/*` and `docs/04/pantheon_p0_sd/*`
Planning session: `phase6-2026-05-01-pantheon-p0-paper-loop`
Status: accepted planning seed, materialized into `ai-status.json`

## Consensus

The docs converge on one main point:

```text
Pantheon has real foundation now, but the operating loop is not proven until
DeploymentPlan -> RuntimeBinding -> paper runtime -> TelemetryEvent -> projection/reconciliation
is executable, auditable, and tested.
```

The current official execution bridge for P0 must be:

```text
path: pantheon/lean
repo-root submodule path: lean
remote: ajoe734/pantheon-lean.git
not target: ajoe734/lean-platform unless migration_only + ADR override
```

Live/canary remain out of P0 activation. The current live role must stay health-only and fail-closed.

## Hard Invariants

1. P0 execution work targets `pantheon/lean`, not `lean-platform`.
2. `paper/canary/live` are deployment stages, not artifact states.
3. Every deployment-managed runtime has a `RuntimeBinding`.
4. Paper telemetry includes runtime identity when a binding exists.
5. No broker secret appears in frontend, artifact payload, launch manifest, telemetry, or OpenClaw memory.
6. BFF/front are not canonical runtime truth.
7. OpenClaw/LLM may research and review, but cannot directly operate broker/runtime.
8. Live broker execution remains disabled until a later activation plan passes human approval, risk guard, rollback, kill switch, and broker entitlement checks.

## P0 Wave Order

| Wave | Goal | Why first |
|---|---|---|
| 0 | Repo authority and safety guardrails | Prevent work from landing in the wrong repo or enabling live accidentally |
| 1 | Runtime contract and context propagation | Make `DeploymentPlan -> RuntimeBinding -> runtime_bootstrap` testable |
| 2 | Paper telemetry and projection | Prove runtime facts return to Pantheon with identity |
| 3 | Paper loop smoke and reconciliation | Convert schemas into a minimum operating loop |
| 4 | Front/BFF honesty cleanup | Prevent operators from reading demo/mock/health-only as production truth |

## P0 Execution Tasks

| Task ID | Owner | Reviewer | Depends On | Summary | Acceptance |
|---|---|---|---|---|---|
| P0-EXEC-ADR-001 | Codex | Claude | - | Land ADR/repo mapping policy for `pantheon/lean` | `.gitmodules`, docs, and task packets name `pantheon/lean`; `lean-platform` is not-current-runtime or migration-only |
| P0-CI-BRIDGE-001 | Codex | Codex2 | P0-EXEC-ADR-001 | Add submodule authority and no-wrong-repo CI | CI reports bridge path/remote/commit and fails P0 `lean-platform` target without ADR override |
| P0-BOOT-001 | Codex | Codex2 | P0-CI-BRIDGE-001 | Implement `RuntimeBootstrapRequest` materializer | Request contains deployment plan, runtime binding, artifact, capital, bridge identity, and no secrets |
| P0-CTX-001 | Codex2 | Codex | P0-BOOT-001 | Add `PantheonRuntimeContext` model and validation | Manifest/env source modes work; required fields enforced; stage mismatch and secrets rejected |
| P0-CTX-002 | Codex | Codex2 | P0-CTX-001 | Wire `runtime_bootstrap.py` to launch manifest/env context | Paper role receives context; staging/prod missing binding fail closed; live still health-only |
| P0-LEAN-CTX-001 | Codex2 | Claude | P0-CTX-001 | Add `PantheonAlgoBase` context/event attachment | `get_pantheon_context()` and event emit attach binding/plan/artifact/stage metadata |
| P0-TEL-001 | Codex | Codex2 | P0-CTX-002,P0-LEAN-CTX-001 | Add paper telemetry emitter and ingest tests | Paper heartbeat validates as `TelemetryEvent`, dedupes by `event_id`, rejects stage mismatch |
| P0-TEL-PROJ-001 | Codex | Claude | P0-TEL-001 | Runtime status projection from paper telemetry | BFF/runtime summary shows non-mock last heartbeat with bridge identity |
| P0-LOOP-001 | Codex | Gemini | P0-TEL-PROJ-001 | Minimum paper operating loop smoke | Seed/approved artifact -> DeploymentPlan -> RuntimeBinding -> paper heartbeat -> projection, no live broker, no `lean-platform` |
| P0-REC-001 | Codex2 | Codex | P0-LOOP-001 | Basic paper reconciliation record | One paper run creates a `ReconciliationRecord`; threshold breach can open an `IncidentCase`; evolution stays proposed only |
| P0-STATE-001 | Codex2 | Claude | - | Add artifact/deployment/runtime invariant tests | `paper/live/canary` cannot be artifact states; deployment requires approved artifact and binding |
| P0-BFF-CMD-001 | Codex | Claude | P0-STATE-001 | Split BFF read/command contract | Read API remains GET-only; command API requires actor, trace, idempotency, RBAC/policy, audit |
| P0-FE-DEMO-001 | Copilot | Codex | - | Front staging/prod demo auth and demo-island guard | staging/prod bundle has no `@/demo/api`, no demo token path, forbidden routes fail CI |
| P0-FE-SOURCE-001 | Copilot | Claude | P0-FE-DEMO-001,P0-TEL-PROJ-001 | Add source mode and runtime identity to critical UI | Runtime/deployment/governance/evolution surfaces show source mode, bridge repo/commit, binding, plan, artifact |
| P0-LIVE-GUARD-001 | Codex | Gemini | P0-BOOT-001 | Live fail-closed and bracket honesty tests | live role is `health_only/not_activated`; no broker connect/order; bracket event is `logged_only`, not submitted |
| P0-CI-BOUNDED-001 | Codex | Copilot | P0-CI-BRIDGE-001 | Source/search bounded and OSS/OpenClaw fail-closed CI | static records, guarded external feed, DLQ/frontier/audit replay smoke pass; no unrestricted crawler; adapters fail closed |
| P0-HEALTH-001 | Codex | Claude | P0-CI-BRIDGE-001 | Health endpoint cleanup scan | `__health__` occurrences reported, then forbidden after cleanup; services use `/healthz`, `/livez`, `/readyz`, `/metrics` |

## P0 Execution Packet Invariant

Every P0 execution packet that touches LEAN runtime, runtime bootstrap, paper telemetry, bridge CI, live guard, or paper loop smoke must inherit this target block:

```yaml
official_execution_target:
  repo: pantheon
  canonical_path: pantheon/lean
  gitmodules_path: lean
  remote: https://github.com/ajoe734/pantheon-lean.git
  runtime_mount: /workspace/lean
not_current_runtime:
  - repo: ajoe734/lean-platform
    allowed_only_when: migration_only_with_ADR_override
```

## P1 Backlog

| Task | Acceptance |
|---|---|
| Guarded bracket order execution | Sim/paper broker bracket orders can be submitted/tracked; live still activation-gated |
| Canary/live activation plan | Activation requires risk pass, rollback target, broker entitlement, human approval, kill switch, stage-aware credentials |
| OpenClaw governed search | OpenClaw only receives EvidenceBundle/citation pack through SearchGateway with ACL/license/available_time filters |
| Source connector expansion | Add at least one of news/social/alpha DB with `SourceRecord`, `EvidenceBundle`, entitlement, and PIT semantics |
| Production persistence rollout | staging/prod fail fast without Postgres/object store; dev JSON/JSONL remains dev-only |
| KillSwitchBridge | Secondary path writes audit, changes CapitalPool state, sends runtime command, and receives telemetry ack |
| Postmortem/evolution dispatcher baseline | Incident evidence is collected; approved evolution can dispatch only through governed command path |

## Supervisor Materialization Rule

This plan stays in planning mode until:

1. `document-reconciliation.md` is marked completed or not needed.
2. LLM readouts are submitted or explicitly waived.
3. `consensus-packet.md` is accepted.
4. Human gate is approved.

These gates are now satisfied for `phase6-2026-05-01-pantheon-p0-paper-loop`; the P0 tasks above were materialized into `ai-status.json` on 2026-05-01.
