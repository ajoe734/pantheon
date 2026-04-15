# Starter Draft — Phase 4: Service Layer Completion

Current rule: only `Codex` edits this file directly.

Last updated by: Codex (baton-owner seed for round 1 refresh)

## Shared Draft

- Objective: turn the existing phase 3-5 domain objects and operator surfaces into a coherent single-VM service stack with explicit write/read boundaries, Docker packaging, and a compose topology that can be cross-reviewed before execution work starts.
- Cross-phase reading rule: use `phase2-phase6-gap-inventory.md` as the canonical bridge between already-finished semantic baseline work and still-missing operational baseline work. This round should slice the residual operational gaps, not reopen already accepted L1 object semantics unless the service boundary still depends on them.
- Scope boundary: planning only. This session aligns service boundaries, port/env contracts, delivery order, and task slicing. It does not directly implement the wrappers or Dockerfiles yet. Default scope includes runtime-control, governance/evolution data APIs, telemetry ingest, lineage read, BFF, trader feedback, and the single-VM compose plan. `web` and `cron` stay optional until reviewers agree they belong in the default VM profile.
- Proposed architecture:
  - `runtime-control` is the only side-effectful operator command API. Reuse/package `services/control_plane/internal_api.py` rather than inventing a second command surface during this wave.
  - `governance-api` exposes approval, capital pool, persona binding, deployment, saga, runtime-binding, and evolution objects from the existing domain modules.
  - `telemetry-ingest` owns event intake, buffering, retry, and DLQ. `lineage-read` owns projection/query endpoints only.
  - `bff` remains read-oriented and command-submitting. It should stop treating snapshot/default seed data as the long-term integration path.
  - `feedback` remains a separate ingestion service. `router` and `persona` stay as already-deployable dependencies, not redesign targets.
- Proposed wave order:
  1. Service baseline: finalize port map, env names, volume mounts, and compose profile boundaries.
  2. Runtime-control packaging on `:5001`, because BFF operator commands already depend on that interface.
  3. Governance API plus evidence services (`telemetry-ingest`, `lineage-read`).
  4. BFF and trader-feedback Dockerfiles plus BFF client rewiring.
  5. Single-VM compose assembly and smoke path. Only after that, decide whether `web` / `cron` belong in the default profile or an optional profile.
- Proposed task slices:
  - `SVC-BASELINE`: shared env/volume contract, service port allocation, Dockerfile conventions, and compose profile plan.
  - `SVC-RUNTIME-CONTROL`: package internal API, persist command state, and resolve the missing evolution command boundary.
  - `SVC-GOVERNANCE-API`: expose approval/capital/binding/deployment/evolution read/write APIs around current stores/controllers.
  - `SVC-EVIDENCE`: add HTTP wrappers + Dockerfiles for telemetry ingest and lineage read.
  - `SVC-SURFACES`: Dockerize BFF + trader-feedback and rewire BFF away from snapshot-seed mode.
  - `SVC-COMPOSE`: assemble the single-VM docker-compose file, storage mounts, dependency graph, and smoke commands.
- Residual-gap interpretation by roadmap phase:
  - `Phase 2`: contracts are mostly done; remaining work is service exposure for runtime-control and governance/runtime APIs.
  - `Phase 3`: schemas and service classes exist; remaining work is HTTP wrapping, packaging, and compose wiring for telemetry/lineage/incident evidence paths.
  - `Phase 4`: evolution semantics exist; remaining work is command-plane convergence plus service ownership split between runtime-control and governance-api.
  - `Phase 5`: persona/app surface contracts exist; remaining work is BFF rewiring away from snapshots/defaults and packet expansion for non-APP-002 workbenches.
  - `Phase 6`: OSS governance criteria exist; remaining work is mostly follow-on execution once the service stack is runnable, not part of the first compose-critical slice unless reviewers argue otherwise.
- Current evidence-backed decisions:
  - The repo already has working HTTP apps for router, persona, BFF, feedback, web chat, and a protected internal command API. The largest missing work is packaging and service exposure for governance/runtime/evidence modules.
  - BFF is not yet backed by canonical services; it reads JSON snapshots or seeded defaults for governance/runtime data and posts commands to the protected internal API.
  - Telemetry ingest and lineage read already exist as reusable service classes, so they should be wrapped instead of redesigned.
  - The current root compose file is obsolete for this scope and cannot be expanded naively because router and BFF both bind `8001` in their current local/dev runners.
- Open disagreements:
  - Should `services/control_plane/internal_api.py` become the long-lived `runtime-control` service, or only a temporary adapter in front of a new `runtime-manager` FastAPI app?
  - Where should evolution approval/action endpoints live: `runtime-control`, `governance-api`, or a split between them?
  - Must BFF client rewiring to real services happen in the same wave as Dockerization, or can snapshot mode ship temporarily for an earlier smoke stack?
  - Should `web` and `cron` be part of the default single-VM profile, or remain optional profiles outside the phase 3-5 critical path?

## Execution Slice Seed

The current Codex recommendation is to keep the first materialization wave focused on the six service-layer slices below and treat broader workbench expansion plus phase 6 adapter realization as follow-on work after the stack is runnable:

| Slice | Main closure target | Depends on |
|---|---|---|
| `SVC-BASELINE` | port/env/volume/compose contract across the stack | - |
| `SVC-RUNTIME-CONTROL` | real side-effectful operator command service | `SVC-BASELINE` |
| `SVC-GOVERNANCE-API` | service-backed approval/deployment/binding/evolution APIs | `SVC-BASELINE` |
| `SVC-EVIDENCE` | telemetry-ingest + lineage-read HTTP services | `SVC-BASELINE` |
| `SVC-SURFACES` | BFF + feedback packaging and BFF rewiring off snapshots/defaults | `SVC-RUNTIME-CONTROL`, `SVC-GOVERNANCE-API`, `SVC-EVIDENCE` |
| `SVC-COMPOSE` | single-VM compose assembly and smoke path | `SVC-RUNTIME-CONTROL`, `SVC-GOVERNANCE-API`, `SVC-EVIDENCE`, `SVC-SURFACES` |

## Citations

- [C1] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/planning-session.json`
- [C2] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/README.md`
- [C3] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`
- [R1] `docker-compose.yml:22-100`
- [R2] `services/control-plane/router/Dockerfile:13-15`
- [R3] `services/control-plane/bff/main.py:2124-2126`
- [R4] `services/control-plane/bff/read_store.py:43-175`
- [R5] `services/control-plane/bff/command_executor.py:21-25`
- [R6] `services/control-plane/bff/command_executor.py:64-183`
- [R7] `services/control_plane/internal_api.py:1-114`
- [R8] `services/control-plane/feedback/main.py:180-235`
- [R9] `services/telemetry/ingest_svc.py:1-120`
- [R10] `services/telemetry/lineage_read/service.py:1269-1295`
- [R11] `services/channels/web/main.py:1-68`
- [R12] shell observations from `find services -maxdepth 3 -name Dockerfile | sort` and `rg -n "class (LineageReadService|TelemetryIngestService|RuntimeBindingStore|KillSwitchController|StagePlanner|DeploymentSagaOrchestrator|EvolutionController|ApprovalDecisionStore|CapitalPoolStore|PersonaRegistry)" services/control-plane services/execution services/telemetry`
