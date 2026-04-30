# Phase 2-Phase 6 Gap Inventory

Last updated: 2026-04-29
Status: planning support snapshot for the active `phase4-2026-04-15-service-layer-completion` session
Scope: inventory the residual work still missing across roadmap phases 2 through 6, even though the canonical baseline tasks for those phases already have archived completion records

## 1. Reading Rule

This inventory separates two ideas that are easy to mix together:

1. `canonical baseline complete`
   This means the phase's named backlog tasks already exist as archived `done` tasks and the repo contains the expected policy, contract, or model artifacts.
2. `delivery complete`
   This means the phase is actually deployable, service-backed, UI-backed, and integrated enough that downstream work no longer depends on hidden placeholders, snapshot fallbacks, or deferred adapters.

For phases 2 through 6, the repo has moved materially since the original 2026-04-15 inventory. Read this file in three bands:

- `current code truth`: service wrappers, Dockerfiles, default compose wiring, health checks, and smoke coverage that exist now
- `current execution tasks`: narrow hardening work already assigned in `ai-status.json`
- `future-deferred`: product-scope or governance-gated work that must not be treated as current implementation scope

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
- `ai-status.json` for active task ownership and lifecycle only
- `docker-compose.yml`
- `scripts/smoke_honest_stack.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/command_executor.py`
- `services/source_ingestion/configured.py`
- `services/search/main.py`
- `services/openclaw-gateway-adapter/main.py`

## 3. Executive Summary

All canonical roadmap tasks for phases 2 through 6 already have archived `done` records. The current single-VM service baseline is also much more complete than the original planning inventory: root compose now contains the control/evidence/surface services, consultation/source/search, safe research/learning boundary wrappers, and the Pantheon-owned OpenClaw gateway adapter facade.

The remaining work now clusters into four different buckets:

1. `current code truth`
   The default stack is deployable as a single-VM test stack with Dockerfiles, health checks, durable volumes, and smoke wiring. This includes `policy-learning-svc`, `research-orchestrator-svc`, and `research-worker-gateway-svc`, but only as safe service-boundary wrappers with production adapters disabled.
2. `command-plane convergence`
   Runtime/evolution command convergence, data ownership migration, and BFF read-path hardening remain separate execution concerns; they are no longer missing-Dockerfile problems.
3. `current hardening tasks`
   Source ingest external fetch is not a production crawler today. The active `SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE` task owns bounded allowlisted HTTP/file feed support. The active `SVC-SEARCH-DURABLE-COMPAT-QUARANTINE` task owns request-document compatibility quarantine.
4. `future-deferred activation`
   BFF HA, OpenClaw paper/live broker sessions, OpenClaw runtime execution, research/learning production adapters, `web`, `cron`, and broader upstream OSS activation remain future-deferred unless explicitly reopened.

## 4. Phase Snapshot Table

| Phase | Canonical task baseline | Current call |
|---|---|---|
| Phase 2 | `CAP-001`, `RUN-001`, `EX-002`, `CAP-002` archived `done` | semantics and default service exposure mostly complete; command convergence and Postgres ownership migration continue |
| Phase 3 | `TEL-001`, `TEL-002`, `LIN-001`, `LIN-002`, `INC-001` archived `done` | evidence services are in default compose; remaining work is hardening, migration, and workbench coverage |
| Phase 4 | `EVO-003`, `EVO-004`, `EVO-005` archived `done` | governance and fast-path rules complete; evolution command/action convergence and packets remain |
| Phase 5 | `PER-001`, `APP-001`, `APP-002` archived `done` | contract and packet baseline complete; read-path hardening, product workbenches, and frontend closure remain |
| Phase 6 | `OSS-001`, `OSS-002`, `OSS-003` archived `done` | safe facades and criteria exist; production adapter/runtime activation remains deferred |

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

1. `runtime-manager`, `governance`, `deployment`, `capital`, and related services are now packaged in the default stack. The remaining risk is not service exposure; it is command/API convergence and durable store ownership.
2. Governance/runtime domain objects are exposed through service APIs in the single-VM baseline, but Postgres ownership migration and cross-service read/write hardening remain active production-readiness work.
3. The evolution command boundary still needs convergence where BFF/runtime/evolution actions cross service boundaries.
4. The shared service baseline for ports, env vars, volumes, health checks, and compose profiles is locked by `starter-draft.md` and root compose; do not cite the earlier unlocked-baseline gap as current truth.

Why these still count as phase-2 gaps:

- phase 2 semantics were completed as contracts and models
- phase 2 delivery risk now sits in command convergence, data ownership, and production hardening rather than absent compose packaging

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

1. `telemetry`, `lineage-read`, `incidents`, and `postmortems` are first-class default compose services with Dockerfiles, health checks, storage mounts, and smoke wiring.
2. The operator-side incident, alert, and evidence drilldowns are still only partially covered by APP-002 packets and sidecars; the full operator and governance workbench shells remain incomplete.
3. The remaining delivery risk is hardening, data ownership migration, and proof depth, not proving that telemetry and lineage can exist as network services.

Repo evidence:

- Root compose builds `services/telemetry/Dockerfile`, `services/lineage-read/Dockerfile`, `services/incidents/Dockerfile`, and `services/postmortems/Dockerfile` and waits on their `/readyz` health checks.
- The earlier "need HTTP wrapping and packaging" note is historical 2026-04-15 planning evidence, not the current implementation contract.

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

1. The live command path still needs convergence across BFF, runtime-manager, governance, and evolution service boundaries.
2. The default stack now includes `evolution`, `governance`, and `runtime-manager`; the remaining split is behavioral ownership and cross-service command semantics, not whether the services exist.
3. The Evolution Workbench is only partially packetized: `post-incident`, `evolution center`, and `lineage view` exist, but `inspiration` and `mutation review` remain missing packet families.

Repo evidence:

- `services/control-plane/bff/command_executor.py` remains the place to inspect runtime/evolution command dispatch behavior.
- `starter-draft.md` now treats the service wrapper baseline as code truth and separates behavioral command convergence from missing-service claims.
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

1. `operator-bff`, `persona`, `router`, and `feedback` are packaged in the default stack. The root compose wires BFF to downstream network services and sets local snapshot fallback off for the default stack.
2. BFF read-path hardening remains live production-readiness work: downstream degradation, auth, query contracts, and service-backed surfaces still need focused tests and frontend closure.
3. The phase3 workbench backlog shows that only the APP-002-backed slice is strongly packetized. Large parts of the Operator, Governance, Evolution, Persona, Research, Knowledge, Trainer, and Consultation workbenches still have missing packet families or missing backend routes.
4. The Lovable loop is a coordination state, not product truth. Use generated status files for current counts and use this inventory only for architectural gap boundaries.

Repo evidence:

- Root compose builds `services/control-plane/bff/Dockerfile` and `services/control-plane/feedback/Dockerfile`, wires service URLs, and depends on service health checks.
- `services/control-plane/bff/read_store.py` remains relevant for fallback behavior, but root compose disables local snapshot fallback in the default profile.
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

1. `openclaw-gateway-adapter` is now a default Pantheon-owned facade around the optional upstream `openclaw-gateway` container. It proves health/capability/degraded semantics, not upstream session execution.
2. OpenClaw session creation remains explicitly deferred: `POST /api/openclaw-adapter/sessions` returns non-retryable `CAPABILITY_DENIED`, and root compose keeps broker/paper activation flags false.
3. `DSPy`, `imitation`, and `MLflow` are smoke-tested, but the checklist still calls out missing canonical `integration.md` and `governance.md` normalization work.
4. `TRL`, `Qlib`, `FinRL`, `RLlib`, `Ray Tune`, and `W&B` remain deferred/criteria-gated unless a separate activation task changes their status.

Repo evidence:

- `OSS_INTEGRATION_CHECKLIST.md` explicitly says "Do not treat a component as integrated just because we wrote contracts around it."
- Current checklist/status interpretation:
  - `OpenClaw = facade boundary active; runtime/paper/live execution deferred`
  - `DSPy = smoke-tested`
  - `TRL = criteria-defined`
  - `Qlib = criteria-defined`
  - `FinRL = criteria-defined`
  - `RLlib = criteria-defined`
  - `Ray Tune = version-pinned`
  - `imitation = smoke-tested`
  - `MLflow = smoke-tested`
  - `W&B = criteria-defined`

## 6. Current Priority Order

The old cross-phase order closed the missing service-wrapper baseline. The current order should reduce remaining production-readiness risk:

1. `SVC-DOCS-FUTURE-STATE-TRUTH-SYNC`
   Keep planning docs aligned with code truth, active tasks, and future-deferred boundaries.
2. `SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE`
   Add bounded allowlisted HTTP/file feed support without claiming arbitrary crawling or live web scraping.
3. `SVC-SEARCH-DURABLE-COMPAT-QUARANTINE`
   Keep durable evidence/index search as the normal path and isolate request-document mode behind explicit compatibility paths.
4. `SVC-DATA-OWNERSHIP-MIGRATION-MAP`
   Map JSONL service stores to Postgres ownership migration slices before store pilots. The execution artifact is `svc-data-ownership-migration-map.md`.
5. BFF security/read-path hardening
   Complete optional OIDC/JWKS validation and keep degraded downstream semantics explicit.
6. Workbench/front-end closure
   Packetize missing surfaces and close Lovable/front-end feedback loops.
7. Future activation lanes
   Reopen OpenClaw runtime/paper/live execution, research/learning production adapters, BFF HA, `web`, or `cron` only through explicit governance-scoped tasks.

## 7. SVC-SERVICE-DISPOSITION Addendum (2026-04-28, historical; updated 2026-04-29; pipeline updated 2026-04-29)

`SVC-SERVICE-DISPOSITION` originally deferred the consultation/source-ingest/search boundary for the first single-VM service baseline. That 2026-04-28 negative boundary is now historical only.

After `SVC-CONSULTATION-SERVICE-ACTIVATION`, `SVC-SOURCE-INGEST-SERVICE`, `SVC-SEARCH-SERVICE`, `SVC-COMPOSE`, `SVC-SOURCE-INGEST-AUTONOMOUS-PIPELINE`, and `SVC-SEARCH-AUTONOMOUS-INDEX-PIPELINE`, current code truth is:

| Component | Current evidence | Disposition for default single-VM compose |
|---|---|---|
| `consultation-svc` | `docker-compose.yml` builds `services/consultation/Dockerfile`, sets `PORT=8096`, mounts `consultation-data`, maps `${CONSULTATION_PORT:-18096}:8096`, and checks `/readyz`. `services/consultation/main.py` also exposes `/health` and consultation APIs under `/api/consult/...`. `runtime-manager` and `operator-bff` point at `PANTHEON_CONSULTATION_API_URL=http://consultation-svc:8096`. | Activated in the default single-VM stack as an explicit HTTP service dependency. |
| `source-ingest` | `docker-compose.yml` builds `services/source_ingestion/Dockerfile`, sets `PORT=8097`, mounts `source-ingest-data`, maps `${SOURCE_INGEST_PORT:-18097}:8097`, and checks `/readyz`. `services/source_ingestion/main.py` exposes `/health`, connector config, job trigger, watermark, DLQ inspection/replay, source record, evidence bundle, and audit endpoints. `services/source_ingestion/configured.py` accepts configured `static_records` and bounded `external_feed` fetch modes. The smoke stack configures an allowlisted HTTP feed connector, triggers a job by `connector_id` alone, verifies DLQ routing on configured failure, and replays from DLQ. | Activated in the default single-VM stack as governed source ingest with inline records, configured static-record replay, and bounded allowlisted HTTP/file JSON feeds. It applies the governed ingest lifecycle, watermark advancement, DLQ routing, and operator-approved replay. It is not a production crawler and does not claim arbitrary external web scraping. |
| `search-svc` | `docker-compose.yml` builds `services/search/Dockerfile`, sets `PORT=8098`, mounts `search-data`, maps `${SEARCH_PORT:-18098}:8098`, and checks `/readyz`. `services/search/main.py` exposes `/health`, `POST /api/search/index/reload`, `GET /api/search/index/status`, `POST /api/search/query`, explicit `POST /api/search/query/request-documents-compat`, and `GET /api/search/snapshots/{request_id}`. `operator-bff` points at `PANTHEON_SEARCH_API_URL=http://search-svc:8098`. | Activated in the default single-VM stack as the governed search HTTP service with durable evidence/index path. No-document queries use the durable JSONL evidence index seeded by `source-ingest` (`adapter_state=durable`). Request-document mode is compatibility-only and must be explicitly allowed by the compat flag or compat route; `SVC-SEARCH-DURABLE-COMPAT-QUARANTINE` owns this quarantine boundary. |

The old SVC-SURFACES negative boundary must no longer be used to omit these services from compose or describe them as missing wrappers. Normal-path dependencies are now explicit network dependencies and must keep the degraded/unavailable semantics from `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` when a downstream service is unhealthy.

The BFF multi-replica/load-balancer topology remains separately deferred as a 2026-04-29 product-scope decision. This service activation update must not be read as reopening BFF HA implementation work.

## 8. Bottom Line

The repo has already completed most of the `semantic baseline` for phases 2 through 6.

What remains is not a generic missing-wrapper baseline. The current split is:

- `code truth`: the root single-VM stack has deployable wrappers, health checks, volumes, smoke wiring, safe research/learning service boundaries, and an OpenClaw adapter facade
- `current tasks`: source external fetch baseline, search compatibility quarantine, data ownership migration, BFF auth/read-path hardening, and related production-readiness work
- `future-deferred`: BFF HA, OpenClaw runtime/paper/live execution, research/learning production adapters, `web`, `cron`, and broader upstream OSS activation

That is why downstream work should not use the old "phase 2-6 are missing deployable services" framing. The right interpretation is:

- `phase 2-4`: contracts and default service exposure mostly done; command convergence and data ownership remain
- `phase 5`: packet baseline and BFF packaging exist; product/workbench delivery and frontend closure remain
- `phase 6`: safe facades and criteria exist; production adapter/runtime activation remains deferred

## 9. SVC-BASELINE Closure Note

Updated: 2026-04-29

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

Updated: 2026-04-29

The `SVC-COMPOSE` execution slice assembles the root `docker-compose.yml` as the current single-VM test stack.

Default profile contents:

- local infrastructure: `postgres`, `minio`, `minio-init`, `nats`, and `signal-store`
- control/evidence services: `runtime-manager`, `governance`, `deployment`, `capital`, `evolution`, `telemetry`, `lineage-read`, `incidents`, and `postmortems`
- activated consultation/source/search/training services: `consultation-svc`, `source-ingest`, `search-svc`, and `training-session-svc`
- safe research/learning service boundaries: `policy-learning-svc`, `research-orchestrator-svc`, and `research-worker-gateway-svc`
- reconciliation and OpenClaw boundary facades: `reconciliation-drift-svc` and `openclaw-gateway-adapter`
- operator/application surfaces: `operator-bff`, `persona`, `router`, and `feedback`
- supporting service shells already in the baseline: `evaluation`, `memory`, `registry`, `optimizer-svc`, and `promotion`

Optional profile contents:

- `openclaw-gateway` remains under the `openclaw` profile and proves only upstream gateway reachability; it does not activate OpenClaw runtime sessions, broker execution, or paper/live adapters.
- `smoke-stack` remains under the `smoke` profile and runs `scripts/smoke_honest_stack.py` after the default stack is healthy.

Repeatable verification commands:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose --profile smoke run --rm smoke-stack
docker compose down --volumes --remove-orphans
```

The smoke path intentionally runs after the default stack is healthy, waits for every default HTTP service health endpoint through the `smoke` profile's dependency graph, then exercises an integration path across runtime deployment, telemetry ingest, incident/postmortem evidence creation, source ingest, durable search, policy-learning and research-orchestrator rejection paths, OpenClaw adapter degraded semantics, BFF honest-mode guidance, and BFF SSE replay. It is run as a separate `docker compose run` step because `minio-init` is a successful one-shot initialization service; using `--abort-on-container-exit` on the whole stack would treat that expected exit as a stack stop signal.

The 2026-04-28 `consultation`, `source_ingestion`, and `search` deferral from section 7 is historical. In the current root compose stack, `consultation-svc`, `source-ingest`, and `search-svc` are default services with Dockerfiles, `/readyz` health checks, mounted service-owned volumes, and HTTP entrypoints. Downstream docs should cite the 2026-04-29 code-backed state, while still preserving the current limits: source configured fetch supports static-record replay and bounded allowlisted HTTP/file JSON feeds only, and request-document search remains compatibility-only.

## 11. Research And Learning Boundary Audit

Updated: 2026-04-29

`research-orchestrator-svc`, `policy-learning-svc`, and `research-worker-gateway-svc` now exist as default single-VM service boundaries, but this is not production activation of the deferred research/learning stack.

Current code truth:

- `research-orchestrator-svc` exposes task/run lifecycle, artifact handoff, and proposal handoff APIs. Default dispatch remains `stub`; Qlib, TRL, RL/RLlib, FinRL, W&B, and paper/canary/live requests are rejected while `RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS=false`.
- `policy-learning-svc` records non-production policy-learning proposals and operator rejection state. Qlib, TRL, RL, W&B, and paper/canary/live requests are rejected by the service boundary.
- `research-worker-gateway-svc` exposes safe `stub`, `handoff_only`, and `manual` worker dispatch records only. Qlib, TRL, RL/RLlib, FinRL, Ray Tune, VectorBT, Statsmodels, QuantLib, W&B, paper/canary/live, LEAN execution, SignalStore/live-trading paths, registry writes, governance writes, and EP5/production-learning activation are rejected.
- Root compose sets `POLICY_LEARNING_ENABLE_PRODUCTION_ADAPTERS=false`, `RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS=false`, and `RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS=false`.
- Regression coverage lives in `services/research/tests/`, `services/policy-learning/tests/`, and `services/research-worker-gateway/tests/`, including compose-default checks and rejection-path checks for production adapters and production modes.

Disposition:

These services should be described as service-boundary wrappers with safe replay and manual handoff paths. They must not be cited as evidence that the EP5 human gate, Qlib/TRL production adapters, RL production lane, W&B integration reopen, or paper/canary/live deployment activation is complete. Those remain future activation work requiring separate governance, adapter, evidence, and smoke-test tasks.

## 12. OpenClaw Gateway Adapter Boundary

Updated: 2026-04-29

`openclaw-gateway-adapter` now exists as a Pantheon-owned default service boundary around the optional upstream `openclaw-gateway` container.

Current code truth:

- Root compose builds `services/openclaw-gateway-adapter/Dockerfile` as `openclaw-gateway-adapter` on port `8104`, published as `${OPENCLAW_GATEWAY_ADAPTER_PORT:-18104}`.
- The upstream `openclaw-gateway` image remains optional under the `openclaw` profile and its compose healthcheck uses `/readyz`. The adapter healthcheck uses `/livez`, so the Pantheon adapter process can be healthy while upstream OpenClaw is absent.
- The adapter exposes `/healthz`, `/livez`, `/readyz`, `/metrics`, `/api/openclaw-adapter/upstream/status`, `/api/openclaw-adapter/capabilities`, and deferred session metadata routes under `/api/openclaw-adapter/sessions`.
- `/readyz` degrades when the optional upstream gateway is absent or unhealthy. Capability metadata remains readable in degraded mode.
- Session creation returns a non-retryable `CAPABILITY_DENIED` deferral. `OPENCLAW_PRODUCTION_BROKER_ENABLED=false` and `OPENCLAW_PAPER_ADAPTER_ENABLED=false` are locked in compose.
- `scripts/smoke_honest_stack.py` now covers the adapter liveness facade, readiness/degraded semantics, capability metadata, and denied session creation path.

Disposition:

This closes the service-boundary gap for a controlled OpenClaw adapter facade only. It must not be cited as evidence that upstream OpenClaw runtime session execution, paper execution, production adapters, broker execution, or EP5 activation is complete.

## 13. Code-Backed References

- `docker-compose.yml:81-220` — consultation, source-ingest, search, training-session, policy-learning, and research-orchestrator default services.
- `docker-compose.yml:257-278` — OpenClaw adapter facade default service and disabled broker/paper env gates.
- `docker-compose.yml:280-337` — runtime-manager and governance default services.
- `docker-compose.yml:720-841` — deployment, evolution, lineage-read, reconciliation-drift, and research-worker-gateway services.
- `docker-compose.yml:843-927` — smoke profile environment and dependency graph.
- `services/source_ingestion/configured.py:120-166` — configured fetch accepts `static_records` and bounded allowlisted `external_feed` JSON inputs.
- `services/search/main.py:278-326` — durable search path and explicit request-document compatibility path.
- `services/openclaw-gateway-adapter/main.py:180-220` — deferred OpenClaw sessions and `CAPABILITY_DENIED` session creation.
- `scripts/smoke_honest_stack.py:135-180` and `scripts/smoke_honest_stack.py:381-613` — smoke coverage for OpenClaw degraded semantics, source ingest, search, policy-learning, and research-orchestrator.

## 14. SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER Closeout Note

Updated: 2026-04-30

The `SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER` execution slice closes the autonomous baseline gap that section 7 left for source-ingest and search: scheduled connector execution beyond caller-triggered `external_feed`, and a materialized index refresh path independent of query-time durable reload.

Delivered scope (commit `7c4a924`):

- Source-ingest: `ConnectorScheduleConfig` and `JsonlConnectorScheduleStore` (append-only with replay) back `PUT/GET /api/source-ingest/connectors/{id}/schedule` and `POST /api/source-ingest/run-scheduled`. The scheduled-run endpoint is watermark-driven and only executes connectors whose due time has elapsed.
- Search: `JsonlMaterializedIndexStore` (append-only with last-state replay) backs `POST/GET /api/search/index/materialize`, separating materialization from `POST /api/search/index/reload`'s query-time path.
- Existing watermark advancement, DLQ routing, replay, bounded-fetch guards, and the request-document compatibility quarantine remain enforced — this slice did not loosen any of those boundaries.
- `scripts/smoke_honest_stack.py` exercises the scheduled-run and materialize endpoints; 14 focused tests cover schedule append/replay and materialized index append/replay.

Verification (review-approved by Codex2):

- `python3 -m pytest services/source_ingestion/ services/search/` → 64 passed
- `docker compose config --quiet` → exit 0

Disposition:

This closes the autonomous-baseline gap only. It does not promote source-ingest into a production crawler, does not remove the bounded-fetch allowlist, and does not change the request-document quarantine. Downstream production-crawler or richer indexer work remains outside this slice.
