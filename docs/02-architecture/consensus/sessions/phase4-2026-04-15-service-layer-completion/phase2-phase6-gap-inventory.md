# Phase 2-Phase 6 Gap Inventory

Last updated: 2026-04-15
Status: planning support snapshot for the active `phase4-2026-04-15-service-layer-completion` session
Scope: inventory the residual work still missing across roadmap phases 2 through 6, even though the canonical baseline tasks for those phases already have archived completion records

## 1. Reading Rule

This inventory separates two ideas that are easy to mix together:

1. `canonical baseline complete`
   This means the phase's named backlog tasks already exist as archived `done` tasks and the repo contains the expected policy, contract, or model artifacts.
2. `delivery complete`
   This means the phase is actually deployable, service-backed, UI-backed, and integrated enough that downstream work no longer depends on hidden placeholders, snapshot fallbacks, or deferred adapters.

For phases 2 through 6, the repo is much closer to the first condition than the second.

## 2. Source Set

- `ROADMAP.md`
- `DEVELOPMENT_WORKBREAKDOWN.md`
- `ai-task-archive/tasks/{CAP-001,CAP-002,RUN-001,EX-002,TEL-001,TEL-002,LIN-001,LIN-002,INC-001,EVO-003,EVO-004,EVO-005,PER-001,APP-001,APP-002,OSS-001,OSS-002,OSS-003}.json`
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/consensus-packet.md`
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/planning-session.json`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/codex-readout.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- `current-work.md`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/command_executor.py`
- `find services -maxdepth 3 -name Dockerfile | sort`

## 3. Executive Summary

All canonical roadmap tasks for phases 2 through 6 already have archived `done` records. That does not mean phases 2 through 6 are operationally complete.

The residual gaps cluster into four cross-phase buckets:

1. `service exposure and deployability`
   The domain models and policies exist, but several phase 2-4 capabilities are still not exposed as deployable HTTP services with Dockerfiles, health checks, compose wiring, and stable port/env contracts.
2. `command-plane convergence`
   Runtime and evolution semantics are documented, but the live operator command path is still split between internal APIs and local BFF placeholders.
3. `surface and packet coverage`
   APP-002-backed surfaces are strongly packetized, but large parts of the Persona, Research, Knowledge, Trainer, Consultation, Governance, and Evolution workbenches remain partial or entirely unpacketized.
4. `OSS criteria vs real integration`
   Phase 6 did a strong job of selecting, pinning, and defining criteria, but several upstream components are still only `criteria-defined`, `version-pinned`, or `adapter-started`.

## 4. Phase Snapshot Table

| Phase | Canonical task baseline | Current call |
|---|---|---|
| Phase 2 | `CAP-001`, `RUN-001`, `EX-002`, `CAP-002` archived `done` | semantics complete; serviceization and command convergence still incomplete |
| Phase 3 | `TEL-001`, `TEL-002`, `LIN-001`, `LIN-002`, `INC-001` archived `done` | schemas and service classes complete; deployable service plane still incomplete |
| Phase 4 | `EVO-003`, `EVO-004`, `EVO-005` archived `done` | governance and fast-path rules complete; evolution command/API boundary still incomplete |
| Phase 5 | `PER-001`, `APP-001`, `APP-002` archived `done` | contract and packet baseline complete; BFF rewiring and workbench expansion still incomplete |
| Phase 6 | `OSS-001`, `OSS-002`, `OSS-003` archived `done` | governance and criteria complete; real upstream integration still partial |

## 5. Phase-by-Phase Residual Gaps

### Phase 2: Capital, Runtime, and Execution Control

Archive-confirmed baseline:

- `CAP-001` locked `capital_pool` and `PersonaCapitalBinding`
- `RUN-001` locked `RuntimeBinding` and runtime-manager write authority
- `EX-002` aligned rollback execution semantics
- `CAP-002` implemented optimizer-side multi-persona synthesis

What is already solid:

- canonical ownership and single-runtime rules exist
- rollback vocabulary is aligned end to end
- synthesis artifacts and conflict-resolution logging exist

Residual gaps:

1. `runtime-control` is still not packaged as the stable deployable service layer that downstream surfaces can rely on in a single-VM stack.
2. Governance/runtime domain objects are not yet exposed through a stable service API family that the BFF can consume without snapshot fallbacks.
3. The evolution command boundary is still split. In `services/control-plane/bff/command_executor.py`, `ApproveEvolutionDecision` and `ExecuteEvolutionAction` still record local placeholder results instead of dispatching to real internal API endpoints.
4. The shared service baseline for ports, env vars, volumes, health checks, and compose profiles is not locked yet; the active phase4 session exists largely because this layer remains open.

Why these still count as phase-2 gaps:

- phase 2 semantics were completed as contracts and models
- phase 2 delivery is still incomplete at the service boundary that phase 3-5 surfaces need to consume

### Phase 3: Telemetry, Lineage, and Incident Backbone

Archive-confirmed baseline:

- `TEL-001` added deployment-stage and runtime-binding telemetry references
- `TEL-002` implemented durable buffering and async ingest
- `LIN-001` normalized lineage edges and derived read-model semantics
- `LIN-002` implemented lineage-read performance work
- `INC-001` defined incident and postmortem records

What is already solid:

- telemetry truth carries the right identifiers
- ingest buffering and DLQ behavior exist
- lineage and incident objects are formally modeled

Residual gaps:

1. `telemetry-ingest` and `lineage-read` still need first-class HTTP entrypoints, Dockerfiles, health endpoints, storage mounts, and compose wiring in the actual service stack.
2. The operator-side incident, alert, and evidence drilldowns are still only partially covered by APP-002 packets and sidecars; the full operator and governance workbench shells remain incomplete.
3. Even where service classes exist, the deployable stack still lacks the packaging layer needed to prove that telemetry and lineage are available as stable network services in the single-VM target.

Repo evidence:

- `find services -maxdepth 3 -name Dockerfile | sort` shows no Dockerfiles for BFF, feedback, runtime-manager, or telemetry service wrappers.
- The active phase4 Codex readout explicitly treats telemetry ingest and lineage read as reusable classes that still need HTTP wrapping and packaging.

### Phase 4: Evolution Governance

Archive-confirmed baseline:

- `EVO-003` adopted `EvolutionDecision`
- `EVO-004` defined freeze/rollback/retrain/redeploy orchestration boundaries
- `EVO-005` implemented the kill-switch and safe-mode fast path

What is already solid:

- the governed object exists
- owner and threshold semantics exist
- emergency fast-path logic exists in runtime-manager semantics

Residual gaps:

1. The live command path is not fully converged: evolution approvals and actions still do not flow through a real internal API endpoint.
2. The service boundary between `runtime-control` and a future `governance-api` remains open in the current phase4 planning session.
3. The Evolution Workbench is only partially packetized: `post-incident`, `evolution center`, and `lineage view` exist, but `inspiration` and `mutation review` remain missing packet families.

Repo evidence:

- `services/control-plane/bff/command_executor.py` still treats evolution actions as local placeholders.
- `starter-draft.md` lists the runtime-control vs governance-api split as an open disagreement.
- `pantheon-console-workbench-backlog.md` explicitly marks inspiration and mutation review as still missing for the Evolution Workbench.

### Phase 5: Persona and Application Surfaces

Archive-confirmed baseline:

- `PER-001` adopted the persona registry/session/runtime contract
- `APP-001` defined governed BFF and consultation surfaces
- `APP-002` defined operator-facing deployment/incident/evolution surfaces

What is already solid:

- persona object boundaries are formalized
- APP-002-backed screens have packet families
- degraded operator path and consultation semantics are documented

Residual gaps:

1. The BFF still reads from canonical snapshots and then falls back to seeded defaults when those files are absent. That means phase 5 surfaces are not yet honestly service-backed.
2. The BFF and trader-feedback services are still not packaged into the deployable service stack; there is still no BFF Dockerfile in the repo.
3. The phase3 workbench backlog shows that only the APP-002-backed slice is strongly packetized. Large parts of the Operator, Governance, Evolution, Persona, Research, Knowledge, Trainer, and Consultation workbenches still have missing packet families or missing backend routes.
4. The Lovable loop is active but incomplete. `current-work.md` currently shows `11` Lovable-ready packets, `9` still waiting for Lovable/front-end, `0` returned `ui-done`, and `2` returned `frontend feedback`.

Repo evidence:

- `services/control-plane/bff/read_store.py` explicitly says the BFF prefers canonical snapshots and otherwise falls back to local seed data.
- `find services -maxdepth 3 -name Dockerfile | sort` shows no BFF Dockerfile.
- `pantheon-console-workbench-backlog.md` marks Research, Knowledge, Trainer, and Consultation as not Lovable-ready and still missing full packet families and backed BFF routes.

### Phase 6: OSS Integration Hardening

Archive-confirmed baseline:

- `OSS-001` selected and pinned OpenClaw
- `OSS-002` regraded DSPy, imitation, and MLflow
- `OSS-003` defined activation criteria for deferred frameworks

What is already solid:

- upstream governance decisions are documented
- deferred frameworks now have explicit criteria instead of placeholder boxes
- the checklist is honest about maturity states

Residual gaps:

1. `OpenClaw` is only `adapter-started`; the real gateway adapter, runtime dependency path, and pinned-image smoke test still need to happen.
2. `DSPy`, `imitation`, and `MLflow` are smoke-tested, but the checklist still calls out missing canonical `integration.md` and `governance.md` normalization work.
3. `TRL`, `Qlib`, `FinRL`, `RLlib`, and `W&B` are still criteria-only or pinned-only, not integrated.
4. `Ray Tune` is version-pinned but still lacks its adapter path and smoke test.

Repo evidence:

- `OSS_INTEGRATION_CHECKLIST.md` explicitly says "Do not treat a component as integrated just because we wrote contracts around it."
- Current checklist states:
  - `OpenClaw = adapter-started`
  - `DSPy = smoke-tested`
  - `TRL = criteria-defined`
  - `Qlib = criteria-defined`
  - `FinRL = criteria-defined`
  - `RLlib = criteria-defined`
  - `Ray Tune = version-pinned`
  - `imitation = smoke-tested`
  - `MLflow = smoke-tested`
  - `W&B = criteria-defined`

## 6. Cross-Phase Priority Order

If we want the next wave to reduce the largest real delivery risk instead of just closing more documents, the next order should be:

1. `SVC-BASELINE`
   Lock ports, env vars, volumes, health checks, and compose profile boundaries.
2. `SVC-RUNTIME-CONTROL`
   Package the command plane that phase 2 and phase 4 already assume exists.
3. `SVC-GOVERNANCE-API`
   Expose approval, deployment, binding, and evolution objects as service-backed APIs.
4. `SVC-EVIDENCE`
   Wrap telemetry ingest and lineage read as real services.
5. `SVC-SURFACES`
   Rewire BFF away from snapshot/default mode, package BFF and feedback, and make phase 5 surfaces honest.
6. `Phase 5 workbench expansion`
   Packetize the backlog that phase3 identified but only partially covered.
7. `Phase 6 real integrations`
   Move OpenClaw and the deferred OSS stack from criteria to executable adapters and smoke tests.

## 7. SVC-SERVICE-DISPOSITION Addendum (2026-04-28, historical; updated 2026-04-29)

`SVC-SERVICE-DISPOSITION` originally deferred the consultation/source-ingest/search boundary for the first single-VM service baseline. That 2026-04-28 negative boundary is now historical only.

After `SVC-CONSULTATION-SERVICE-ACTIVATION`, `SVC-SOURCE-INGEST-SERVICE`, `SVC-SEARCH-SERVICE`, and `SVC-COMPOSE`, current code truth is:

| Component | Current evidence | Disposition for default single-VM compose |
|---|---|---|
| `consultation-svc` | `docker-compose.yml` builds `services/consultation/Dockerfile`, sets `PORT=8096`, mounts `consultation-data`, maps `${CONSULTATION_PORT:-18096}:8096`, and checks `/readyz`. `services/consultation/main.py` also exposes `/health` and consultation APIs under `/api/consult/...`. `runtime-manager` and `operator-bff` point at `PANTHEON_CONSULTATION_API_URL=http://consultation-svc:8096`. | Activated in the default single-VM stack as an explicit HTTP service dependency. |
| `source-ingest` | `docker-compose.yml` builds `services/source_ingestion/Dockerfile`, sets `PORT=8097`, mounts `source-ingest-data`, maps `${SOURCE_INGEST_PORT:-18097}:8097`, and checks `/readyz`. `services/source_ingestion/main.py` exposes `/health`, `POST /api/source-ingest/jobs`, job replay, watermark, DLQ, and audit endpoints. The smoke stack uses `SOURCE_INGEST_URL=http://source-ingest:8097`. | Activated in the default single-VM stack as a bounded job-trigger wrapper. The wrapper accepts already-fetched records in this slice; autonomous external fetching remains later pipeline work. |
| `search-svc` | `docker-compose.yml` builds `services/search/Dockerfile`, sets `PORT=8098`, mounts `search-data`, maps `${SEARCH_PORT:-18098}:8098`, and checks `/readyz`. `services/search/main.py` exposes `/health`, `POST /api/search/query`, and `GET /api/search/snapshots/{request_id}`. `operator-bff` points at `PANTHEON_SEARCH_API_URL=http://search-svc:8098`. | Activated in the default single-VM stack as the governed search HTTP service. |

The old SVC-SURFACES negative boundary must no longer be used to omit these services from compose or describe them as missing wrappers. Normal-path dependencies are now explicit network dependencies and must keep the degraded/unavailable semantics from `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` when a downstream service is unhealthy.

The BFF multi-replica/load-balancer topology remains separately deferred as a 2026-04-29 product-scope decision. This service activation update must not be read as reopening BFF HA implementation work.

## 8. Bottom Line

The repo has already completed most of the `semantic baseline` for phases 2 through 6.

What is still missing is the `operational baseline`:

- deployable service wrappers
- real command-plane convergence
- BFF rewiring away from snapshots/defaults
- complete workbench packet families and front-end closure
- real upstream adapter execution beyond criteria and checklists

That is why the active planning effort should not treat phase 2-6 as "already finished". The right interpretation is:

- `phase 2-4`: contracts mostly done, service exposure still incomplete
- `phase 5`: packet baseline partly done, product/workbench delivery still incomplete
- `phase 6`: governance criteria done, executable integrations still incomplete

## 9. SVC-BASELINE Closure Note

Updated: 2026-04-28

The `SVC-BASELINE` execution slice has locked the single-VM baseline contract in `starter-draft.md` under `SVC-BASELINE locked contract (2026-04-28)`.

This inventory remains the historical planning bridge that justified the service-layer wave. For implementation, downstream `SVC-*` tasks should now treat the locked baseline in `starter-draft.md` as the active contract for:

- service port and host-port allocation
- shared env names
- durable volume ownership
- health-check endpoint expectations
- compose profile boundaries
- Dockerfile conventions
- the explicit single-VM BFF HA deferral, now recorded as a 2026-04-29 product-scope defer rather than current execution work because the operator frontend is expected to have low concurrent human usage

The earlier evidence notes about missing Dockerfiles and an unlocked compose baseline should be read as 2026-04-15 planning evidence, not as the current implementation contract.

## 10. SVC-COMPOSE Closure Note

Updated: 2026-04-28

The `SVC-COMPOSE` execution slice assembles the root `docker-compose.yml` as the current single-VM test stack.

Default profile contents:

- local infrastructure: `postgres`, `minio`, `minio-init`, `nats`, and `signal-store`
- control/evidence services: `runtime-manager`, `governance`, `deployment`, `capital`, `evolution`, `telemetry`, `lineage-read`, `incidents`, and `postmortems`
- activated consultation/source/search services: `consultation-svc`, `source-ingest`, and `search-svc`
- operator/application surfaces: `operator-bff`, `persona`, `router`, and `feedback`
- supporting service shells already in the baseline: `evaluation`, `memory`, `registry`, `optimizer-svc`, and `promotion`

Optional profile contents:

- `openclaw-gateway` remains under the `openclaw` profile and proves only gateway reachability for this wave.
- `smoke-stack` remains under the `smoke` profile and runs `scripts/smoke_honest_stack.py` after the default stack is healthy.

Repeatable verification commands:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose --profile smoke run --rm smoke-stack
docker compose down --volumes --remove-orphans
```

The smoke path intentionally runs after the default stack is healthy, waits for every default HTTP service health endpoint through the `smoke` profile's dependency graph, then exercises an integration path across runtime deployment, telemetry ingest, incident/postmortem evidence creation, BFF honest-mode guidance, and BFF SSE replay. It is run as a separate `docker compose run` step because `minio-init` is a successful one-shot initialization service; using `--abort-on-container-exit` on the whole stack would treat that expected exit as a stack stop signal.

The 2026-04-28 `consultation`, `source_ingestion`, and `search` deferral from section 7 is historical. In the current root compose stack, `consultation-svc`, `source-ingest`, and `search-svc` are default services with Dockerfiles, `/readyz` health checks, mounted service-owned volumes, and HTTP entrypoints; downstream docs should cite the 2026-04-29 code-backed state instead of the old omission rationale.
