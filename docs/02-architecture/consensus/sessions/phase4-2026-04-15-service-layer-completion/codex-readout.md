# Codex Readout

## Lane

- Agent: Codex
- Capability focus: Ground the phase 3-5 service-layer plan in repo evidence and turn it into execution-ready slices.

## Canonical Sources Read

- L0: `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/README.md`
- L1: `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/planning-session.json`
- L2: `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md`, `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/consensus-packet.md`, `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/LLM_READOUT_TEMPLATE.md`

## Working Interpretation

- Architecture summary: this should be treated as a service-boundary and packaging wave, not a domain-model invention wave. The repo already contains deployable HTTP apps for router, persona, BFF, feedback, web chat, and a protected internal command API; it also contains library/service classes for governance, runtime binding, telemetry ingest, and lineage read that are not yet exposed as deployable HTTP services. The cleanest target is one write/control path (`runtime-control`), explicit data services for governance/runtime/evidence, and a read-only BFF on top.
- Delivery order: lock the service baseline first (ports, volumes, env contract, compose profiles), then package the runtime-control API because BFF operator commands already depend on it. After that, expose governance/evolution/runtime data APIs plus telemetry/lineage APIs, then Dockerize and rewire BFF/feedback, and only then finalize the single-VM compose stack.
- Ownership boundaries: `runtime-control` owns side-effectful operator commands and runtime-binding mutation; `governance-api` owns `ApprovalDecision`, `CapitalPool`, `PersonaCapitalBinding`, `DeploymentPlan`, and `EvolutionDecision` read/write paths; `telemetry-ingest` owns event intake, buffering, retry, and DLQ; `lineage-read` owns read-only projection queries; `bff` remains a read aggregator and command submitter, not a state writer; `cron` stays a workflow runner and should not become a parallel truth source.

## Risks / Contradictions

- Risk 1: the current root compose file is not phase 3-5 complete and cannot be expanded naively. It only boots LEAN, Redis, router, persona, selected research/learning workers, and MLflow, while both router and BFF currently want port `8001`. A single-VM compose plan needs an explicit port map before implementation.
- Risk 2: BFF can look operational even when the service layer is not real. Its read path prefers JSON snapshots and then falls back to seeded in-process data, so Dockerizing BFF before the canonical backend services exist would hide missing integration behind defaults.
- Risk 3: the operator command plane is only partially converged. Deployment approval, pause, rollback, and kill-switch already dispatch to the protected internal API, but evolution approval/action still short-circuit locally inside BFF. That boundary has to be settled before task materialization.

## Suggested Task Slices

- Slice 1: `SVC-BASELINE` — define the shared runtime contract for phase 3-5 services: port allocation, env names, bind-mounted data directories, health/smoke expectations, and compose profiles such as `core`, `ops`, and optional `research`.
- Slice 2: `SVC-RUNTIME-CONTROL` — package `services/control_plane/internal_api.py` as the runtime-control service on `:5001`, persist command state, and either add the missing evolution endpoints there or explicitly route them into governance APIs.
- Slice 3: `SVC-GOVERNANCE-API` — wrap existing governance/runtime domain modules (`ApprovalDecisionStore`, `CapitalPoolStore`, `StagePlanner`, `DeploymentSagaOrchestrator`, `EvolutionController`, `RuntimeBindingStore`) into a deployable FastAPI service family with file-backed stores and stable read/write routes.
- Slice 4: `SVC-EVIDENCE` — build `telemetry-ingest` and `lineage-read` FastAPI wrappers with Dockerfiles, health endpoints, storage mounts, and smoke coverage.
- Slice 5: `SVC-SURFACES` — Dockerize BFF and trader-feedback, then rewire BFF from snapshot-seed mode to service clients. Keep `web` and `cron` out of the default single-VM critical path unless reviewers agree they belong in the first deployment profile.

## Phase 2-Phase 6 Residual Gap Alignment

- I refreshed this readout against `phase2-phase6-gap-inventory.md`. That inventory changes the interpretation of the session in one useful way: phases 2 through 6 are no longer blocked on semantic baseline work, but they are still blocked on operational baseline work.
- The first materialization wave should therefore stay narrow and execution-oriented. The six `SVC-*` slices above should be treated as the compose-critical bridge from already-finished contracts into deployable services.
- I do **not** recommend materializing phase 5 workbench expansion or phase 6 real OSS adapter realization into the same first wave. Those should stay downstream until the service stack is runnable and the BFF is off snapshot/default mode.
- The main remaining ambiguity for reviewers is not whether additional backlog exists; it is whether `runtime-control` vs `governance-api` should be split as two services in wave 1, or whether a temporary runtime-control-heavy cut is acceptable for the first composeable stack.

## Citations

- [C1] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/planning-session.json`: this session is explicitly about completing the phase 3-5 service layer, aligning architecture/delivery order/task slicing, and it assigns the baton/starter draft to `Codex`.
- [C2] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/README.md`: every lane writes an independent readout, and only `Codex` edits `starter-draft.md`.
- [R1] `docker-compose.yml:22-100`: the current compose file defines `lean`, `signal-store`, `control-plane-router`, `control-plane-persona`, `dspy-worker`, `qlib-worker`, `finrl-worker`, `imitation-worker`, and `mlflow-server`; it does not yet define BFF, feedback, runtime-control, governance, telemetry, or lineage services.
- [R2] `services/control-plane/router/Dockerfile:13-15` and `services/control-plane/bff/main.py:2124-2126`: router exposes `8001`, and BFF's local runner also binds `8001`, so a naive single-VM compose would create a port collision.
- [R3] `services/control-plane/bff/read_store.py:43-175`: BFF reads governance/runtime state from env-addressed JSON snapshot files and falls back to `_default_read_data()` when those files are absent.
- [R4] `services/control-plane/bff/command_executor.py:21-25`, `services/control-plane/bff/command_executor.py:64-183`, and `services/control_plane/internal_api.py:1-114`: BFF already depends on `PANTHEON_INTERNAL_API_URL` defaulting to `http://localhost:5001`; deployment/pause/rollback/kill-switch dispatch there, but evolution approval/action still remain local placeholders. The protected internal API already wraps runtime-manager modules.
- [R5] `services/control-plane/feedback/main.py:180-235`: trader feedback is already a real FastAPI app with `/health`, `POST /trader-feedback`, and `GET /trader-feedback`, so its remaining gap is packaging and compose wiring.
- [R6] `services/telemetry/ingest_svc.py:1-120` and `services/telemetry/lineage_read/service.py:1269-1295`: telemetry ingest and lineage read already exist as reusable service classes, but there is no HTTP entrypoint next to them today.
- [R7] shell observation: `find services -maxdepth 3 -name Dockerfile | sort` returned only `services/control-plane/persona/Dockerfile`, `services/control-plane/router/Dockerfile`, `services/learning/*/Dockerfile`, and `services/research/*/Dockerfile`; there is still no Dockerfile under `services/control-plane/bff`, `services/control-plane/feedback`, `services/control-plane/cron`, `services/execution/runtime-manager`, or `services/telemetry`.
- [R8] `services/channels/web/main.py:1-68`: the current web channel is only a thin proxy to router `http://localhost:8001`, so it should not drive the first service-layer wave.
- [R9] shell observation: `rg -n "class (LineageReadService|TelemetryIngestService|RuntimeBindingStore|KillSwitchController|StagePlanner|DeploymentSagaOrchestrator|EvolutionController|ApprovalDecisionStore|CapitalPoolStore|PersonaRegistry)" services/control-plane services/execution services/telemetry` finds the domain/service classes already in-repo, which means this wave is mostly about HTTP wrapping, packaging, and integration wiring.
- [R10] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`: the cross-phase inventory confirms that phase 2-6 canonical tasks are archived done, but the remaining delivery gaps still cluster into service exposure, command-plane convergence, surface packet coverage, and real OSS integration execution.
