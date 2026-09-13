# Research and Jobs source, identity, and action contract

Task: `BFF-RESEARCH-JOBS-CONTRACT-DECISION-001` (D-JOBS). Owner: Antigravity.
Independent reviewer: Codex. Date: 2026-09-13.
Status: design selected; independent review pending. This document implements
V2 §02.9's design prerequisite, not U10A/U10B source delivery or hosted acceptance.

## 1. Decision and evidence boundary

Fixed decision: **Establish Management Jobs as a typed read composition and
single `JobAction` dispatch seam across five asynchronous backend sources plus
one read-only diagnostic source, while maintaining strict domain aggregate
separation**. Do not build a universal "JobStore", a second distributed
scheduler, a shared worker pool, or an in-memory execution overlay. Domain
owners retain exclusive write authority, state persistence, and worker
lifecycle management for their respective job types.

Management UI and BFF observe and interact with jobs via standard contracts:
- Read composition: `GET /bff/jobs`, `GET /bff/jobs/{job_id}`, `GET /bff/jobs/{job_id}/logs`, and `GET /bff/sse/jobs/{jobId}/progress`.
- Action dispatch: `POST /bff/jobs/{job_id}/actions/{action_id}` routed via `CommandAdapterService` and `JobCommandAdapter` to the qualified domain owner.
- Aggregate boundaries are strictly preserved: **`ResearchTicket != Experiment != OrchestratorRun != Job`**. They represent fundamentally distinct business entities, lifecycles, and storage models.

Crucial distinction between execution phases:
- **Observed capabilities**: What exists in backend services today (e.g. gateway has `/cancel` updating row/events without worker kill; policy-learning has `/reject` updating row without worker kill; training-session and source-ingestion have NO cancel endpoint).
- **U10A (Read Plumbing & Minimal Write Wiring)**: Connects `ResearchWriteOwner` to read surface, deletes in-memory `_experiments` dictionary, implements typed read projection (`JobReadPort`), decouples tickets from jobs, un-fakes `EvolutionCommandAdapter`, and ensures unimplemented actions fail closed with `ActionUnavailableError` (never fake 200/202).
- **U10B (Real Action Execution Closure)**: Bounded strictly to the 13 paths declared in `dispatch-map.json` (`services/research/main.py`, `services/research/write_owner.py`, and BFF command adapters). U10B implements real cancellation and cancellation fencing for domain owners within its scope (`services/research/`).
- **Unresolved Composition Obligations**: Because U10B's 13-path scope excludes `services/research-worker-gateway/`, `services/training-session/`, `services/source_ingestion/`, and `services/policy-learning/`, any missing backend owner operations (such as gateway worker termination/fence, trainer preview cancel, source-ingest extraction cancel, policy learning worker stop, or cross-service governance promotion) are **explicitly preserved as missing obligations** for follow-up domain tasks. U10B must not silently broaden its grants or pretend these missing backend capabilities are implemented.

This decision governs the contract and boundary prerequisites for U10A and U10B.
It does not authorize cross-host HA, live trading promotions, production capital grants,
or repository/shell execution. If future operational requirements necessitate
modifications to capital limits, live worker execution authority, or cross-tenant
job scheduling, an explicit operator decision must be obtained before implementation.

Source baseline: `b9143d5eb05cbf65394fc3e2e65531279f3f52c0`. Paths below are
relative to the repository root; `BFF/` refers to `services/control-plane/bff/`.
The accompanying evidence manifest records exact source blobs and repeatable probes.

| Evidence | Finding and contract implication |
| --- | --- |
| `BFF/jobs/router.py:58–164` | `create_jobs_router` exposes list, get, logs, and action routes. Currently relies on `read_store.list_jobs_bff` and `get_job_bff`, while `job_action` delegates to `_evol_exp_bff_action_command`. |
| `BFF/ports/read_surface_ports.py:1069–1074` | `get_job_bff` directly calls `research_knowledge_source.get_research_ticket(job_id)` and `list_jobs_bff` calls `list_research_tickets`. **`ResearchTicket`s are masquerading as Jobs**, completely conflating tickets with operational jobs. |
| `BFF/ports/research_knowledge_source.py:2170–2230` | `DefaultResearchKnowledgeSourcePort` maintains an in-memory `self._experiments: Dict[str, Dict[str, Any]]`. `create_research_experiment` and `cancel_research_experiment` mutate this dictionary in memory, bypassing persistent storage. |
| `BFF/command_adapters/evolution_adapter.py:28–38, 186–208` | `EvolutionCommandAdapter._HANDLED_COMMANDS` includes `ExperimentAction` and `JobAction`. `_execute_experiment_or_job` returns hardcoded `status="executed"` with fake success and invented `research_job_authority`, executing zero domain effects. |
| `BFF/command_adapters/registry.py:29–54` | `_DEFAULT_ADAPTERS` evaluates `EvolutionCommandAdapter` before any potential dedicated job or experiment adapter. First-match routing swallows `JobAction` and `ExperimentAction` into the fake execution body. |
| `BFF/events/router.py:511–518` | `GET /bff/sse/jobs/{jobId}/progress` explicitly documents `"Subscription is channel-based; job filtering remains client-side."` Job progress events flood subscribers without backend `jobId` filtering. |
| `BFF/research/routes/experiments.py:111–368, 453–550` | Two competing experiment API families coexist: `/bff/experiments*` (with subrouter `/bff/research-experiments*`) and `/api/v1/experiments*` (with `/launch` and `/cancel`), using divergent models and calling un-synchronized ports. |
| `services/research/write_owner.py:48–85, 200–546` | Canonical persistent `ResearchWriteOwner` exists using `PostgresJsonOwnerStore` over `research.research_tickets`, `research.research_experiments`, and `research.research_notes`. It provides atomic, durable mutations but is disconnected from the main BFF read surface. |
| `services/research-worker-gateway/main.py:406–574`; `store.py:100–186` | Worker gateway generates native `wjob-{date}-{seq}` IDs (main.py:422). `cancel_job` (main.py:559-574) only mutates status row and appends event; has no worker stop or cancellation fence. `DispatchJobBody` and list/get routes contain no tenant filtering or token auth. |
| `services/research/main.py:182–202, 745–1180`; `store.py:99–235` | Research orchestrator generates native `rtask-{date}-{seq}` and `rrun-{date}-{seq}` IDs. Dispatches runs via `POST /api/research-orchestrator/tasks/{task_id}/runs` and exposes `GET /api/research-orchestrator/tasks`, `GET /api/research-orchestrator/runs`; stores artifacts, but has no run cancel endpoint on `main.py`. |
| `services/training-session/main.py:499–522, 1800–1835`; `preview_eval_worker.py` | Training session generates native `pvjob-{digest[:16]}` IDs. Exposes `GET /api/training/preview-jobs`, `GET .../preview-jobs/{job_id}`, and `POST .../sessions/{session_id}/preview-jobs`. Inbound authority middleware authenticates requests; list/get strictly enforces tenant records (`_tenant_records`, `_require_tenant_record`). Has NO cancel operation/endpoint anywhere in the service. |
| `services/source_ingestion/main.py`; `connectors/base.py:634`; `pipeline.py` | Ingestion service generates native `ingest-{uuid}` or `run-{connector_id}` IDs. Has NO cancel operation/endpoint anywhere in the service. |
| `services/policy-learning/main.py:189–196, 551–564`; `store.py` | Policy learning generates native `plj-{date}-{seq}` IDs. `reject_job` only mutates status row; has no worker stop; promotion returns 409 (requires Governance). |
| `services/openclaw-gateway-adapter/main.py:458–490, 3569–3610`; `tool_workflow_bridge.py:1005` | OpenClaw workflow jobs triggered via `POST /api/openclaw-adapter/workflows/trigger` and queried via `GET /api/openclaw-adapter/workflows/jobs/{job_id}` (calling upstream OpenClaw `POST /api/workflows/trigger` and `GET /api/jobs/{job_id}`). Native `job_id`/`id` preserved. Strictly read-only for product BFF; no write actions; no development-supervisor tasks. |

---

## 2. Investigation and qualification of the six candidate job/run sources

The system discovery identified six distinct job and run mechanisms across the platform.
The table below qualifies each source, establishing its canonical domain owner,
storage mechanism, native ID scheme, Management projection mapping, auth and tenant
enforcement, existing gaps, observed capabilities, and justified admission decision.

| Candidate Source | Underlying Domain Owner & Store | Native ID Scheme | Management Projection Scheme | Auth & Tenant Enforcement & Gaps | Observed Capabilities vs Missing Obligations | Management Admission Status | Responsible Scope |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **1. Research worker jobs** | `services/research-worker-gateway/` (`ResearchWorkerGatewayStore` + `PostgresWorkerEventStore`) | `wjob-{YYYYMMDD}-{index:03d}` (main.py:422) | `job-worker-{wjob_id}` | **Enforcement**: None in routing.<br>**Gaps**: `DispatchJobBody` has no `tenant_id`; list/get/cancel routes have no tenant filter and no token auth dependency. | **Observed**: Dispatch queues/runs; cancel (main.py:559-574) only sets status `canceled` & appends event.<br>**Missing**: No worker process kill; no cancellation fence; no attempt lineage retry. | **Conditionally Admitted (Read & Safe Stub Dispatch)**; Excluded from U10B backend action closure. | Read: U10A.<br>Worker stop & fence: **GW-STOP-FENCE-001** (Gateway owner task, outside U10B). |
| **2. Research orchestrator runs** | `services/research/` (`ResearchOrchestratorStore`, storing `ExperimentTask` & `ExperimentRun`) | `rrun-{YYYYMMDD}-{index:03d}` under `rtask-{YYYYMMDD}-{index:03d}` (main.py:759, 856) | `job-orchestrator-{rrun_id}` (linked to parent `rtask_id`) | **Enforcement**: Task headers carry optional tenant ID in admission contract.<br>**Gaps**: List/get routes (`GET /api/research-orchestrator/tasks`, `GET .../runs`) lack tenant query filtering; unauthenticated FastAPI endpoints. | **Observed**: Dispatches runs via `POST /api/research-orchestrator/tasks/{task_id}/runs`, tracks stage/status, records artifacts & events.<br>**Missing**: No run cancel endpoint on `main.py`; no late-completion fence. | **Admitted (Qualified)**; Fully in scope for U10A read and U10B backend action execution. | Read: U10A.<br>Run cancel & fence: **U10B** (`services/research/main.py` is in U10B scope). |
| **3. Trainer preview jobs** | `services/training-session/` (`TrainingSessionStore`, `preview_eval_worker.py`) | `pvjob-{digest[:16]}` or `pvjob-{uuid[:12]}` (main.py:170-174, 1843) | `job-trainer-{pvjob_id}` | **Enforcement**: `main.py:499-522` enforces inbound authority middleware on `/api/training/*` via `inbound_authority.py`; `list_preview_jobs` (1806) filters via `_tenant_records`; `get_preview_job` (1823) and `queue_preview_job` (1836) enforce `_require_tenant_record` (404 on mismatch).<br>**Gaps/Exceptions**: Test token exceptions (`token_kind == 'test-disabled'`). No cancel endpoint or worker thread interrupt exists. | **Observed**: Exposes `GET /api/training/preview-jobs`, `GET .../preview-jobs/{job_id}`, `POST .../sessions/{session_id}/preview-jobs`; triggers eval previews via worker; records metrics.<br>**Missing**: **NO cancel endpoint or operation exists** in `training-session`. | **Conditionally Admitted for Read Projection Only**; Excluded from Management Job Actions. | Read: U10A.<br>Cancel operation: **TS-CANCEL-001** (Training session owner task, outside U10B). |
| **4. Source-ingest runs/jobs** | `services/source_ingestion/` (`source_management_store.py`, `pipeline.py`, `connectors/base.py`) | `ingest-{uuid4().hex[:12]}` or `run-{connector_id}` (connectors/base.py:634) | `job-ingest-{ingest_run_id}` | **Enforcement**: Controller token verified on connector mutation via `load_controller_token()`.<br>**Gaps**: `/api/source-ingest/jobs` has no tenant filter and no auth dependency. | **Observed**: Processes ingest batches, records receipts, frontier replay.<br>**Missing**: **NO cancel endpoint exists**; no graceful extraction worker halt. | **Conditionally Admitted for Read Projection Only**; Excluded from Management Job Actions. | Read: U10A.<br>Cancel operation: **SI-CANCEL-001** (Source ingestion owner task, outside U10B). |
| **5. Policy-learning jobs** | `services/policy-learning/` (`PolicyLearningStore`, `scheduler_worker.py`) | `plj-{YYYYMMDD}-{index:03d}` (main.py:189-196) | `job-policy-{plj_id}` | **Enforcement**: Tenant scope helper for claims/DLQ.<br>**Gaps**: `list_jobs` has no tenant filter; unauthenticated endpoints; reject is row-only. | **Observed**: `reject_job` (main.py:551-564) updates row status `rejected` & appends event; promote returns 409.<br>**Missing**: Reject does not halt running worker; promote requires Governance. | **Conditionally Admitted for Read Projection Only**; Excluded from Direct JobAction Dispatch. | Read: U10A.<br>Worker stop: **PL-CANCEL-001** (Policy learning owner task, outside U10B). |
| **6. OpenClaw workflow jobs** | `services/openclaw-gateway-adapter/` (`tool_workflow_bridge.py`, `main.py:3569-3610`) + upstream OpenClaw service (`main.py:458-490`) | Upstream OpenClaw `job_id` or `id` (`tool_workflow_bridge.py:1005`) | `job-openclaw-{job_id}` | **Enforcement**: `trigger_workflow` requires `X-Operator-Id` header (401 if missing), maps operator role, control mode, confirm tokens, and passes upstream client credentials.<br>**Gaps**: `GET .../workflows/jobs/{job_id}` lacks auth middleware; no tenant isolation upstream; no `list_jobs` endpoint on adapter. | **Observed**: Upstream workflow trigger (`POST /api/workflows/trigger`) and single job status query (`GET /api/jobs/{job_id}`).<br>**Missing**: All write actions (cancel, retry, archive, promote) from product BFF are unsupported; no local job persistence. | **Admitted Strictly as Read-Only Detail Observation**; Excluded from all Job Actions. | Read: U10A (read-only when `job_id` is known).<br>Write actions: Permanently excluded per repository architecture. |

---

### Detailed investigation per candidate source

#### 2.1 Research worker jobs (`services/research-worker-gateway`)
- **Native Store & State**: `ResearchWorkerGatewayStore` (`worker_jobs.json`) and `PostgresWorkerEventStore` (`research_worker_gateway.worker_events`).
- **Native ID Scheme**: `_next_id("wjob", timestamp, existing)` generates formatted IDs `wjob-{YYYYMMDD}-{index:03d}` (main.py:422). Output records use `wgout-{YYYYMMDD}-{index:03d}` (main.py:439). Events use `wgevt-{YYYYMMDD}-{index:03d}` (main.py:196). The matrix previously misstated these as UUIDs; they are sequential date-stamped strings.
- **Management Projection**: Projected into the unified Job model as `job-worker-{wjob_id}` (or `job-gateway-{wjob_id}`).
- **Auth and Tenant Enforcement vs Gaps**:
  - `DispatchJobBody` (main.py:312-324) declares `worker`, `requested_mode`, `dispatch_mode`, `objective`, `task_id`, `run_id`, `input_refs`, `parameters`, `actor_id`, `idempotency_key`, `requested_at`. It **contains no `tenant_id` field**.
  - `list_jobs` (main.py:508-524) accepts query parameters `worker`, `status`, `task_id`, `run_id`, but **contains no tenant parameter or filtering**.
  - `get_job`, `get_job_status`, and `cancel_job` (main.py:527-574) have no authentication dependencies (no `Depends(...)`, no token header check).
  - **Gap**: Zero tenant isolation exists in the gateway service, and endpoints are unauthenticated.
- **Observed Capabilities vs Required Owner Effects**:
  - `POST /api/research-worker-gateway/jobs/{job_id}/cancel` (main.py:559-574):
    ```python
    job["status"] = "canceled"
    job["updated_at"] = timestamp
    job["cancel_reason"] = body.reason
    job["events"] = events
    store.put_job(job)
    store.append_event(events[-1])
    ```
    This is **purely a row-level metadata update**. The handler does not track worker PIDs, does not send `SIGTERM` or `SIGKILL` to running subprocesses spawned by `_execute_worker`, and does not establish a cancellation fence timestamp. If a background worker subsequently writes output or completes, the gateway has no guard to reject the late completion.
  - Retry: No retry endpoint exists. Clients may only submit a new dispatch with an idempotency key, which does not link attempt lineage (`parent_job_id`, `attempt_number`).
- **Admission Decision**: **Conditionally Admitted for Read Projection and Safe Stub Dispatch; Excluded from U10B Action Closure**. Because `services/research-worker-gateway/` is outside U10B's 13-path scope, implementing true worker process termination and late-completion fencing cannot be done in U10B without silently broadening grants. This requirement is explicitly preserved as **`GW-STOP-FENCE-001`**.

#### 2.2 Research orchestrator runs (`services/research`)
- **Native Store & State**: `ResearchOrchestratorStore` managing `ExperimentTask` (`rtask-{date}-{seq}`) and `ExperimentRun` (`rrun-{date}-{seq}`) with stage tracking, artifact storage, and Postgres outbox (main.py:182-202, 759, 856). Exposes `GET /api/research-orchestrator/tasks` (main.py:745), `POST /api/research-orchestrator/tasks/{task_id}/runs` (main.py:784), `GET /api/research-orchestrator/runs` (main.py:1060), and `GET /api/research-orchestrator/runs/{run_id}` (main.py:1070).
- **Native ID Scheme**: Tasks: `rtask-{YYYYMMDD}-{index:03d}`; Runs: `rrun-{YYYYMMDD}-{index:03d}`; Artifacts: `rart-{YYYYMMDD}-{index:03d}`; Events: `revt-{YYYYMMDD}-{index:03d}`.
- **Management Projection**: Projected into the unified Job model as `job-orchestrator-{rrun_id}` under `rtask_id`.
- **Auth and Tenant Enforcement vs Gaps**:
  - Admission contracts allow tenant headers on task creation, but `list_runs` and `get_run` in `ResearchOrchestratorStore` do not enforce tenant filtering.
  - Endpoints lack FastAPI authentication dependencies.
- **Observed Capabilities vs Required Owner Effects**:
  - Orchestrator dispatches runs via `POST /api/research-orchestrator/tasks/{task_id}/runs` and tracks multi-stage experiment runs (`pending`, `running`, `completed`, `failed`).
  - **Missing**: `services/research/main.py` currently has NO run cancellation endpoint. A client cannot request `POST /api/research-orchestrator/runs/{run_id}/cancel`.
- **Admission Decision**: **Admitted (Qualified)**. Crucially, `services/research/main.py` and `services/research/write_owner.py` **ARE within U10B's exact 13-path scope**. Therefore, U10B is the responsible task that will implement run cancellation, state persistence, and late-completion fencing directly within `services/research/`.

#### 2.3 Trainer preview jobs (`services/training-session`)
- **Native Store & State**: `TrainingSessionStore` + `preview_eval_worker.py`. Exposes `GET /api/training/preview-jobs` (main.py:1800), `GET /api/training/preview-jobs/{job_id}` (main.py:1818), and `POST /api/training/sessions/{session_id}/preview-jobs` (main.py:1826).
- **Native ID Scheme**: `_preview_job_id` (main.py:170-174, 1843) generates `pvjob-{digest[:16]}` (deterministic with idempotency key) or `pvjob-{uuid[:12]}`.
- **Management Projection**: Projected into the unified Job model as `job-trainer-{pvjob_id}`.
- **Auth and Tenant Enforcement vs Gaps**:
  - **Real Enforcement**: `services/training-session/main.py:499-522` enforces inbound authority middleware (`enforce_training_inbound_authority`) across all `/api/training/*` endpoints using `inbound_authority.py` (`authenticate_training_request`), extracting and verifying `Authorization`, `X-Tenant-Id`, `X-MFA-Token`, and `X-Pantheon-Service`. `list_preview_jobs` at line 1806 strictly filters records using `_tenant_records(store.list_preview_jobs())` matching `_request_authority().tenant_id`. `get_preview_job` at line 1823 enforces `_require_tenant_record(job, "preview job not found")`, throwing HTTP 404 on tenant mismatch to prevent ID enumeration. `queue_preview_job` at line 1836 also enforces `_require_tenant_record(session, "training session not found")`.
  - **Configuration and Test Exceptions**: In local tests and test suites where `inbound_authority` is configured with test tokens (e.g. `authority.token_kind == "test-disabled"` in test fixtures), token validation is relaxed to permit declared actors or test tenant defaults.
  - **Remaining Gaps**: Preview jobs have NO cancel endpoint or cancellation handler in `training-session/main.py`. Evaluation worker threads (`preview_eval_worker.py`) once dispatched run to completion without in-flight abort mechanisms.
- **Observed Capabilities vs Required Owner Effects**:
  - Spawns preview evaluation runs against evaluation datasets via `POST /api/training/sessions/{session_id}/preview-jobs`, calculating candidate metrics.
  - **Missing**: `training-session/main.py` has **NO cancel endpoint or cancellation handler whatsoever**.
- **Admission Decision**: **Conditionally Admitted for Read Projection Only; Excluded from Management Job Actions**. `services/training-session` is NOT in U10B's scope. In U10A and U10B, any `JobAction` targeting a `job-trainer-*` ID must fail closed with `ActionUnavailableError` (HTTP 400/503). Implementing a preview cancel operation on the training session service is preserved as an explicit obligation **`TS-CANCEL-001`**.
- **Fail-Closed Read Admission Definition**:
  Because candidate sources vary in their tenant isolation guarantees (e.g., `services/training-session` enforces strict tenant filtering via `_tenant_records`, whereas `services/research-worker-gateway` and `services/source_ingestion` have unauthenticated or un-tenanted endpoints), the Management BFF read surface must enforce a **fail-closed read admission rule**:
  When serving any tenant-scoped read request (`GET /bff/jobs`, `GET /bff/jobs/{job_id}`), any backend source that lacks trustworthy, authenticated tenant identity (such as `research-worker-gateway` whose `DispatchJobBody` and route filters lack tenant fields) must fail closed: it must NOT return unfenced or tenant-ambiguous jobs to a tenant-scoped caller, nor leak jobs across tenant boundaries. Only global/system operator queries or sources with verified tenant context matching the caller's tenant can be admitted into the tenant projection.

#### 2.4 Source-ingest runs/jobs (`services/source_ingestion`)
- **Native Store & State**: `source_management_store.py`, `pipeline.py`, `connectors/base.py`.
- **Native ID Scheme**: `ingest-{uuid4().hex[:12]}` (connectors/base.py:634) or connector-based `run-{connector_id}` / `ingest-{connector_id}`, stored as `ingest_run_id` across pipeline and store.
- **Management Projection**: Projected into the unified Job model as `job-ingest-{ingest_run_id}`.
- **Auth and Tenant Enforcement vs Gaps**:
  - Mutation endpoints verify controller authorization via `load_controller_token()` and `_fence_managed_connector_mutation`.
  - `GET /api/source-ingest/jobs` and `GET /api/source-ingest/jobs/{ingest_run_id}` do not enforce tenant filtering or authentication.
- **Observed Capabilities vs Required Owner Effects**:
  - Ingestion pipeline executes batch extractions, updates watermarks, stores evidence bundles.
  - Frontier items support replay (`POST /api/source-ingest/frontier/{frontier_id}/replay`).
  - **Missing**: Ingestion service has **NO cancel endpoint**. Extraction workers cannot be aborted via API.
- **Admission Decision**: **Conditionally Admitted for Read Projection Only; Excluded from Management Job Actions**. `services/source_ingestion` is NOT in U10B's scope. Action dispatch targeting `job-ingest-*` must fail closed with `ActionUnavailableError`. Implementing ingest cancellation is preserved as **`SI-CANCEL-001`**.

#### 2.5 Policy-learning jobs (`services/policy-learning`)
- **Native Store & State**: `PolicyLearningStore` + `scheduler_worker.py`.
- **Native ID Scheme**: `_next_job_id` (main.py:189-196) generates `plj-{YYYYMMDD}-{index:03d}`. Events use `plevt-{YYYYMMDD}-{seq:03d}`. The matrix previously misstated this as `pl-job-<id>`.
- **Management Projection**: Projected into the unified Job model as `job-policy-{plj_id}`.
- **Auth and Tenant Enforcement vs Gaps**:
  - Candidate claims and DLQ records have tenant scope; helper `_resolve_tenant_scope` exists.
  - `list_jobs`, `get_job`, `propose_job`, and `reject_job` lack token authentication dependencies and tenant query filtering.
- **Observed Capabilities vs Required Owner Effects**:
  - `POST /api/policy-learning/jobs/{job_id}/reject` (main.py:551-564): Mutates status row to `rejected` and appends event to `PolicyLearningStore`. It does NOT stop running background workers or scheduler threads.
  - Direct promotion is explicitly rejected with 409 Conflict because candidate promotion requires Governance mutation review.
- **Admission Decision**: **Conditionally Admitted for Read Projection Only; Excluded from Direct JobAction Dispatch**. `services/policy-learning` is NOT in U10B's scope. Action dispatch targeting `job-policy-*` must fail closed with `ActionUnavailableError`. Worker process stopping is preserved as **`PL-CANCEL-001`**.

#### 2.6 OpenClaw workflow jobs (`services/openclaw-gateway-adapter`)
- **Native Store & State**: Upstream OpenClaw service owns workflow execution and state persistence. Adapter `services/openclaw-gateway-adapter/` provides the integration bridge (`tool_workflow_bridge.py:1005`, `main.py:3569-3610`) and upstream client `OpenClawClient` (`main.py:458-490`). The adapter maintains local invocation audit records (`_audit` store in `tool_workflow_bridge.py`), but maintains NO local persistent job store for workflow jobs.
- **Native ID Scheme**: Upstream OpenClaw `job_id` or `id`, preserved by `tool_workflow_bridge.py:1005` from the upstream workflow trigger response (`job_id = str(result.get("job_id") or result.get("id") or "")`).
- **Management Projection**: Projected into the unified Job model as `job-openclaw-{job_id}`.
- **Auth and Tenant Enforcement vs Gaps**:
  - `POST /api/openclaw-adapter/workflows/trigger` (main.py:3569) enforces required `X-Operator-Id` header (returns HTTP 401 if missing), parses operator role, control mode (`_mode_from_control_mode`), confirmation tokens, and delegates to `_BRIDGE.trigger_workflow(...)`. Upstream client communicates using upstream client authentication.
  - `GET /api/openclaw-adapter/workflows/jobs/{job_id}` (main.py:3604) queries upstream `GET /api/jobs/{job_id}` via `OpenClawClient.get_job(job_id)`.
  - Gaps: `GET .../workflows/jobs/{job_id}` lacks explicit token authentication middleware; upstream OpenClaw workflow engine has no native multi-tenant fencing.
- **Persistence and Read API Gaps**:
  - Workflow execution and state persist only in the upstream OpenClaw service.
  - Read API Gap: Neither `openclaw-gateway-adapter` nor `OpenClawClient` exposes a `list_workflow_jobs` or `list_jobs` endpoint (`OpenClawClient` only implements `get_job(job_id)`). Management BFF cannot enumerate active or historical OpenClaw workflow jobs on `GET /bff/jobs` without an upstream list API or a dedicated local audit tracking index.
- **Read-Only BFF Qualification and Architecture Boundary**:
  - Product BFF is strictly read-only for OpenClaw workflow jobs (detail observation only via `GET /bff/jobs/{job_id}` when `job_id` is known).
  - All write actions (cancel, retry, archive, promote) from product BFF are unsupported and must fail closed (HTTP 400).
  - **No-development-supervisor boundary**: In accordance with `AGENTS.md` and `docs/02-architecture/development-tooling-product-boundary.md`, product BFF and OpenClaw adapter never run development-supervisor tasks, dev-doc generation, worktree preparation, or repository-writing workflows. Do not infer upstream workflows absent from this boundary.
- **Relevant Tests**:
  - `services/openclaw-gateway-adapter/test_tool_workflow_bridge.py` (validates workflow trigger policy, operator identity propagation, audit trail recording, error handling, and verifies no broker/paper/live execution).

---

### Strict distinction of domain aggregates

The system must not conflate these four distinct aggregates:
1. **`ResearchTicket`**: Business research request or bug report owned by `ResearchWriteOwner` (`research.research_tickets`). Identifiers: `ticket-xxx`. States: `open`, `in_progress`, `closed`, `archived`. Actions: `canEdit`, `canClose`, `canArchive`. Tickets are tracked metadata, **not asynchronous execution jobs**.
2. **`Experiment` (`ResearchExperiment`)**: Parametric research experiment specification owned by `ResearchWriteOwner` (`research.research_experiments`). Identifiers: `exp-YYYYMMDD-xxx`. States: `queued`, `running`, `completed`, `failed`, `canceled`. Actions: `canCancel`.
3. **`OrchestratorRun`**: Specific execution attempt of an experiment task, owned by `ResearchOrchestratorStore`. Identifiers: `rrun-{date}-{seq}` under `rtask-{date}-{seq}`. Contains execution artifacts, hardware telemetry, and stage logs.
4. **`Job`**: Unified Management operational projection of asynchronous units of work across qualified domain owners (worker jobs, ingestion runs, policy learning jobs, trainer previews). Identifiers: `job-<source>-<native_id>`. States: standard normalized job lifecycle (`pending`, `running`, `completed`, `failed`, `canceled`).

---

## 3. Owner contracts, gaps, and action semantics

### Existing gaps and architectural defects

1. **Fake Execution in `EvolutionCommandAdapter`**:
   `EvolutionCommandAdapter` registers `JobAction` and `ExperimentAction` and handles them in `_execute_experiment_or_job`, returning `status="executed"`, `success=True`, and a synthetic readback `status="completed"`. It never dispatches to any domain backend. U10A must remove these handled entries, and U10B must bind them to real domain execution.
2. **In-Memory Store in Read Port**:
   `DefaultResearchKnowledgeSourcePort` maintains an ephemeral `self._experiments` dictionary. Creating or canceling an experiment modifies this dictionary in the web process memory, which is lost on restart and desynchronized from the Postgres-backed `ResearchWriteOwner`. U10A must wire `ResearchWriteOwner` directly to the canonical read store.
3. **ResearchTicket Masquerading as Job**:
   `ReadSurfacePorts.get_job_bff` and `list_jobs_bff` delegate directly to `get_research_ticket` and `list_research_tickets`. In the UI, tickets appear in the jobs table, displaying ticket priorities as job statuses and hiding real worker jobs. U10A must decouple them.
4. **Unfiltered SSE Job Progress Stream**:
   `GET /bff/sse/jobs/{jobId}/progress` subscribes to the channel without matching `jobId`. Every subscriber receives all job events, requiring client-side filtering. U10A must implement server-side topic/id filtering.
5. **Divergent Experiment Route Sets**:
   `/bff/experiments*` and `/api/v1/experiments*` expose overlapping functionality with different request validation, response envelopes, and backing store methods. U10A must consolidate them to a single canonical experiment router.

### Action semantics per admitted source

#### Cancellation
- **Lifecycle Sequence**: `cancel_requested` -> `owner_accepted` -> `worker_stopped` -> `terminal_receipt`.
- **Fencing Requirement**: When cancellation is accepted by the domain owner, the owner must persist a cancellation fence timestamp. Any subsequent completion payload arriving from a worker after the fence must be rejected and recorded as orphaned/discarded, preventing late completion from overriding a canceled state.
- **Verification**: Cancellation is not complete merely because the database row status changed to `canceled`. Acceptance requires verification that the worker process/task actually received the stop signal and terminated execution.

#### Retry
- **Eligibility**: Only jobs in eligible terminal states (`failed`, `canceled`, `timeout`) may be retried. Active jobs (`running`, `pending`) must reject retry with 409 Conflict.
- **Attempt Lineage**: Retrying a job must not overwrite the historical record. The domain owner must generate a new attempt record (`attempt_number = N + 1`) linking to `parent_job_id` or `root_job_id`.
- **Idempotency**: Retrying with the same idempotency key must return the existing retry attempt without spawning duplicate workers.

#### Archive
- **Retention Semantics**: Archiving represents a change in visibility and retention policy, **not physical record deletion**.
- **State Transition**: Archived records are excluded from default lists (unless `include_archived=true`) and marked read-only. Further mutations (cancel, retry) are forbidden (409 Conflict).

#### Promote
- **Governance Gate**: Promotion (e.g., promoting a research experiment candidate, policy-learning model, or strategy seed to candidate/paper/live status) **requires explicit Governance review and signed evidence**.
- **No Direct Live Elevation**: Neither Research, Jobs, nor Policy-Learning services possess authority to promote directly into capital-affecting or live trading stages. Direct promotion calls without a matching Governance proposal and two-person approval must fail closed (409/403).

---

## 4. U10A vs U10B layering and composition contract

The delivery of the Research and Jobs domain is partitioned into two ordered tasks,
with clear boundaries separating in-scope implementation from unresolved external obligations:

```
+-------------------------------------------------------------------------------+
| D-JOBS (This Task): Contract & Boundary Decision                               |
|   - Fixed source/type/tenant/ID qualification matrix                          |
|   - Native IDs: wjob-*, rrun-*, pvjob-*, ingest-*, plj-*                      |
|   - Distinct aggregates: ResearchTicket != Experiment != OrchestratorRun != Job|
|   - Detailed acceptance mapping & explicit unresolved composition obligations |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
| U10A: Research/Jobs Real Owner & Read Plumbing (BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001)
|   - Connect ResearchWriteOwner to read surface; delete in-memory _experiments |
|   - Implement Jobs read projection (jobs/projection.py, ports/job_read.py)    |
|   - Consolidate experiment routes to single canonical router                  |
|   - Dedicated ExperimentCommandAdapter & JobCommandAdapter in registry        |
|   - Delete fake handled entries from EvolutionCommandAdapter                  |
|   - Fix SSE /bff/sse/jobs/{jobId}/progress to filter by jobId                |
|   - Unimplemented actions fail closed with ActionUnavailableError (no fake 200)|
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
| U10B: True Action Execution Closure (RESEARCH-JOBS-ACTIONS-CLOSURE-CORRECTIVE-001)
|   - Bounded strictly to 13 paths in dispatch-map.json                         |
|   - Real backend execution for Experiment cancel (ResearchWriteOwner)         |
|   - Real backend execution for Orchestrator run cancel & fence (services/research)|
|   - Paired with FE-RESEARCH-JOBS-ACTIONS-CLOSURE-001 in execute-plans         |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
| Unresolved Composition Obligations (Separate Follow-up Domain Tasks)          |
|   - GW-STOP-FENCE-001: Gateway worker process kill & late completion fence    |
|   - TS-CANCEL-001: Training session preview eval cancel endpoint              |
|   - SI-CANCEL-001: Source ingestion extraction cancel endpoint                |
|   - PL-CANCEL-001: Policy learning worker process stop endpoint               |
|   - GOV-PROMOTE-001: Governance-gated candidate promotion verification        |
+-------------------------------------------------------------------------------+
```

### U10A exact source scope (39 paths from `dispatch-map.json`)

Execution of U10A is bounded strictly to the following 39 paths:
1. `docs/deployment/evidence/BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001/evidence.json`
2. `services/control-plane/bff/action_catalog.py`
3. `services/control-plane/bff/assistant/source_collectors.py`
4. `services/control-plane/bff/command_adapters/evolution_adapter.py`
5. `services/control-plane/bff/command_adapters/experiment_adapter.py`
6. `services/control-plane/bff/command_adapters/job_adapter.py`
7. `services/control-plane/bff/command_adapters/registry.py`
8. `services/control-plane/bff/events/router.py`
9. `services/control-plane/bff/jobs/projection.py`
10. `services/control-plane/bff/jobs/router.py`
11. `services/control-plane/bff/main.py`
12. `services/control-plane/bff/management_read_models/service.py`
13. `services/control-plane/bff/ports/job_read.py`
14. `services/control-plane/bff/ports/read_surface_ports.py`
15. `services/control-plane/bff/ports/research_commands.py`
16. `services/control-plane/bff/ports/research_knowledge_source.py`
17. `services/control-plane/bff/research/CHARACTERIZATION.md`
18. `services/control-plane/bff/research/router.py`
19. `services/control-plane/bff/research/routes/__init__.py`
20. `services/control-plane/bff/research/routes/common.py`
21. `services/control-plane/bff/research/routes/experiments.py`
22. `services/control-plane/bff/research/test_router.py`
23. `services/control-plane/bff/test_bff_consol_010_fixture_pack_c.py`
24. `services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py`
25. `services/control-plane/bff/test_exp002_bff_research_experiments_contract.py`
26. `services/control-plane/bff/test_mgmt_load_002_shell_summary.py`
27. `services/control-plane/bff/test_mgmt_load_005_read_concurrency.py`
28. `services/control-plane/bff/tests/read_store_migration_inventory.json`
29. `services/control-plane/bff/tests/test_assistant_context_pack.py`
30. `services/control-plane/bff/tests/test_assistant_security.py`
31. `services/control-plane/bff/tests/test_bff_b2_002_evolution_jobs_ops.py`
32. `services/control-plane/bff/tests/test_bff_b2_004_research_search.py`
33. `services/control-plane/bff/tests/test_console_data_route_workflows_projection.py`
34. `services/control-plane/bff/tests/test_jobs_source_owner_contract.py`
35. `services/control-plane/bff/tests/test_overlay_retirement.py`
36. `services/control-plane/bff/tests/test_read_surface_caller_migration.py`
37. `services/control-plane/bff/tests/test_research_knowledge_source_ports.py`
38. `services/research/main.py`
39. `services/research/write_owner.py`

### U10B exact source scope (13 paths from `dispatch-map.json`)

Execution of U10B backend is bounded strictly to the following 13 paths:
1. `docs/deployment/evidence/RESEARCH-JOBS-ACTIONS-CLOSURE-CORRECTIVE-001/evidence.json`
2. `services/control-plane/bff/action_catalog.py`
3. `services/control-plane/bff/command_adapters/experiment_adapter.py`
4. `services/control-plane/bff/command_adapters/job_adapter.py`
5. `services/control-plane/bff/command_adapters/registry.py`
6. `services/control-plane/bff/jobs/projection.py`
7. `services/control-plane/bff/jobs/router.py`
8. `services/control-plane/bff/ports/job_read.py`
9. `services/control-plane/bff/ports/research_commands.py`
10. `services/control-plane/bff/research/routes/experiments.py`
11. `services/control-plane/bff/tests/test_research_jobs_action_receipts.py`
12. `services/research/main.py`
13. `services/research/write_owner.py`

### Unresolved composition obligations

Because U10B's scope excludes several backend service repositories, the following
obligations cannot be completed by U10B and must be tracked as explicit external
composition obligations:

| Obligation ID | Responsible Domain Owner | Scope & Target Service | Description & Required Implementation |
| --- | --- | --- | --- |
| **`GW-STOP-FENCE-001`** | `research-worker-gateway` | `services/research-worker-gateway/main.py` | Add actual worker process termination (`SIGTERM`/`SIGKILL` to subprocess) and cancellation fence timestamp in `cancel_job` to reject late worker outputs. |
| **`TS-CANCEL-001`** | `training-session` | `services/training-session/main.py` | Implement `POST /api/training-session/previews/{id}/cancel` endpoint, cancel preview evaluation worker thread, and update session state. |
| **`SI-CANCEL-001`** | `source-ingestion` | `services/source_ingestion/routers/ingest_operations.py` | Implement `POST /api/source-ingest/jobs/{id}/cancel` endpoint, graceful connector extraction thread interruption, and cancellation fence. |
| **`PL-CANCEL-001`** | `policy-learning` | `services/policy-learning/main.py` | Update `POST /api/policy-learning/jobs/{id}/reject` to halt active training/imitation worker processes rather than performing a row-only update. |
| **`GOV-PROMOTE-001`** | `governance` | `services/control-plane/bff/` & `services/governance/` | Cross-service Governance promotion gate verification requiring signed operator authorization before candidate elevation. |

### Operator decision items

The following boundaries must be preserved without unilateral agent expansion:
- **Promotion to Live/Capital**: No autonomous promotion to paper canary or live trading without explicit Operator/Governance approval.
- **Universal Scheduler / Universal Store**: Any proposal to replace domain-owned stores with a single unified job database requires explicit operator architecture sign-off and is rejected in this baseline.
- **OpenClaw Writable Operations**: OpenClaw product routes remain strictly read-only diagnostics; any grant of shell, file-modification, or task-materialization authority requires explicit operator policy authorization.

---

## 5. Source-specific API, store, and test acceptance mapping

### 5.1 Source-specific API and store acceptance mapping

The following table maps each admitted source to its native API, backing store,
BFF projection endpoint, supported actions, handling of unimplemented operations,
and responsible delivery task.

| Source ID | Domain Owner & Store Paths | Native API Endpoint(s) | Management BFF Route | Supported Actions in U10A / U10B | Unimplemented Actions & Handling | Responsible Scope & Task ID |
| --- | --- | --- | --- | --- | --- | --- |
| **research_worker_gateway** | `services/research-worker-gateway/` (`ResearchWorkerGatewayStore`, `PostgresWorkerEventStore`) | `GET /api/research-worker-gateway/jobs`<br>`GET .../jobs/{id}`<br>`POST .../jobs` | `GET /bff/jobs` (`job-worker-*`)<br>`GET /bff/jobs/{id}` | Read projection in U10A.<br>Safe stub dispatch in U10A. | Cancel: Gateway only does row update. U10A/U10B fail closed with `ActionUnavailableError` until `GW-STOP-FENCE-001` is completed. | Read: U10A.<br>Backend closure: **`GW-STOP-FENCE-001`** (Gateway owner). |
| **research_orchestrator** | `services/research/` (`ResearchOrchestratorStore`, `store.py:99-235`, `main.py:745-1180`) | `GET /api/research-orchestrator/tasks`<br>`POST .../tasks/{task_id}/runs`<br>`GET .../runs`<br>`GET .../runs/{run_id}` | `GET /bff/jobs` (`job-orchestrator-*`)<br>`POST /bff/jobs/{id}/actions/cancel` | Read projection in U10A.<br>True run cancellation & late completion fence in U10B. | Retry: Linking attempt lineage under task is implemented in U10B.<br>Archive: Retention projection in U10B. | Read: U10A.<br>Action closure: **U10B** (`services/research/main.py` is in U10B scope). |
| **training_session** | `services/training-session/` (`TrainingSessionStore`, `main.py:1800-1835`, `preview_eval_worker.py`) | `GET /api/training/preview-jobs`<br>`GET .../preview-jobs/{job_id}`<br>`POST .../sessions/{session_id}/preview-jobs` | `GET /bff/jobs` (`job-trainer-*`)<br>`GET /bff/jobs/{id}` | Read projection in U10A. | Cancel & Retry: **NO cancel endpoint exists**. Actions fail closed with `ActionUnavailableError` (HTTP 400/503). | Read: U10A.<br>Cancel closure: **`TS-CANCEL-001`** (Training session owner). |
| **source_ingestion** | `services/source_ingestion/` (`source_management_store.py`, `pipeline.py`, `routers/ingest_operations.py`) | `GET /api/source-ingest/jobs`<br>`GET .../jobs/{id}`<br>`POST .../jobs` | `GET /bff/jobs` (`job-ingest-*`)<br>`GET /bff/jobs/{id}` | Read projection in U10A. | Cancel: **NO cancel endpoint exists**. Actions fail closed with `ActionUnavailableError` (HTTP 400/503). | Read: U10A.<br>Cancel closure: **`SI-CANCEL-001`** (Source ingestion owner). |
| **policy_learning** | `services/policy-learning/` (`PolicyLearningStore`, `main.py:189-196, 551-564`) | `GET /api/policy-learning/jobs`<br>`POST .../jobs/{id}/reject` | `GET /bff/jobs` (`job-policy-*`)<br>`GET /bff/jobs/{id}` | Read projection in U10A. | Reject/Cancel: Row-only in gateway. Actions fail closed with `ActionUnavailableError`. Direct promote rejected (409). | Read: U10A.<br>Worker stop: **`PL-CANCEL-001`** (Policy learning owner). |
| **openclaw_gateway_adapter** | `services/openclaw-gateway-adapter/` (`tool_workflow_bridge.py:1005`, `main.py:3569-3610`, upstream `main.py:458-490`) | `POST /api/openclaw-adapter/workflows/trigger`<br>`GET .../workflows/jobs/{job_id}`<br>(upstream: `POST /api/workflows/trigger`, `GET /api/jobs/{job_id}`) | `GET /bff/jobs/{id}` (`job-openclaw-*`, detail only) | Read-only detail observation in U10A (when `job_id` is known; no `list_jobs` route). | All write actions (cancel, retry, archive, promote) are unsupported and return 400. | Read: U10A.<br>Write actions: Permanently excluded. |

---

### 5.2 Original inventory cases (`read_store_migration_inventory.json`) mapping

The following mapping binds every historical ReadSurfaceStore inventory case for
jobs and experiments to its specific target disposition, target owner, callers, negative cases,
and test proof, distinguishing current observations from proposed architecture changes:

| Inventory Case / Method | Location & Production Callers | Current Observation (Problem in Baseline) | Target Disposition & Architecture (Proposed Change) | Negative Cases | Responsible Scope & Target Test File |
| --- | --- | --- | --- | --- | --- |
| `list_jobs_bff` | line 3597 (ACG-02-005, MERGE)<br>Callers: `main.py` | Calls `list_research_tickets`; tickets masquerade as jobs in UI. | Merge into typed `JobReadPort` / `JobProjectionService` querying qualified domain owners. | Cross-tenant query fails closed; empty source returns empty list `[]`. | **U10A**: `test_jobs_source_owner_contract.py` |
| `get_job_bff` | line 3613 (ACG-02-005, MERGE)<br>Callers: `main.py` | Calls `get_research_ticket`; returns ticket dictionary as job. | Merge into typed `JobReadPort.get_job_bff`, dispatching to domain owner by ID prefix. | Unknown `job_id` returns 404; cross-tenant query returns 404/403. | **U10A**: `test_jobs_source_owner_contract.py` |
| `get_job_logs_bff` | line 3630 (ACG-02-001, KEEP)<br>Callers: `main.py` | Hardcoded mock logs returned from memory. | Route to domain owner logs endpoint (`/api/research-worker-gateway/jobs/{id}/status`, etc.). | Unknown job returns 404; unstarted job returns empty logs list. | **U10A**: `test_jobs_source_owner_contract.py` |
| `_experiments` in read port | `research_knowledge_source.py:2170`<br>(ACG-02-003, REMOVE) | Ephemeral `self._experiments` dictionary in web memory; lost on restart. | **DELETE completely**. Replace with direct readback from Postgres `research.research_experiments` via `ResearchWriteOwner`. | DB connection error raises 500/503; never falls back to in-memory dictionary. | **U10A**: `test_research_knowledge_source_ports.py` |
| `EvolutionCommandAdapter._HANDLED_COMMANDS` | `evolution_adapter.py:28`<br>(ACG-02-003, REMOVE) | Declares `JobAction` and `ExperimentAction`; returns fake `executed` with zero effects. | **DELETE `JobAction` and `ExperimentAction` from handled list**. `EvolutionCommandAdapter.can_handle` returns `False`. | Unsupported command rejects execution; fails closed without synthetic receipt. | **U10A**: `test_bff_evolution_experiment_jobs_events_contract.py` |
| `_DEFAULT_ADAPTERS` registry order | `registry.py:29-54`<br>(ACG-02-005, MERGE) | `EvolutionCommandAdapter` evaluated first; swallows job/experiment actions. | Register dedicated `ExperimentCommandAdapter` and `JobCommandAdapter` ahead of `EvolutionCommandAdapter`. | Unknown action type returns 404/400 unhandled adapter error. | **U10A**: `test_jobs_source_owner_contract.py` |
| `GET /bff/sse/jobs/{jobId}/progress` | `events/router.py:511`<br>(ACG-02-005, MERGE) | Client-side filtering only; events flood all connected clients. | Implement server-side topic/channel filtering by `jobId`. | Client subscribed to `job-A` never receives events for `job-B`. | **U10A**: `test_bff_evolution_experiment_jobs_events_contract.py` |
| `list_experiments_bff` | line 3480 (ACG-02-001, KEEP)<br>Callers: `services/control-plane/bff/main.py` | Delegates to in-memory `_experiments` dataset via `_get_dataset("research_experiments")`. | Merge into `DefaultResearchKnowledgeSourcePort.list_research_experiments` reading from Postgres `research.research_experiments` via `ResearchWriteOwner`. | DB connection failure returns 500/503; invalid query parameters return 422; unauthorized returns 401. | **U10A**: `test_exp002_bff_research_experiments_contract.py` |
| `get_experiment_bff` | line 3496 (ACG-02-001, KEEP)<br>Callers: `main.py`, `research/router.py` | Reads directly from in-memory dictionary in `ReadSurfaceStore`. | Merge into `DefaultResearchKnowledgeSourcePort.get_research_experiment` backed by `ResearchWriteOwner`. | Unknown `experiment_id` returns 404; cross-tenant access returns 404/403. | **U10A**: `test_exp002_bff_research_experiments_contract.py` |
| `create_experiment_bff` | line 3513 (ACG-02-001, KEEP)<br>Callers: `main.py`, `research/router.py` | Mutates in-memory `_experiments` dictionary in web memory; non-durable. | Atomic creation in Postgres `research.research_experiments` via `ResearchWriteOwner.create_experiment`. | Missing required fields returns 422; duplicate idempotency key returns existing record without duplicate creation; DB failure returns 500/503. | **U10A**: `test_exp002_bff_research_experiments_contract.py` |
| `_project_experiment_bff` | line 3530 (ACG-02-001, KEEP)<br>Callers: `read_store.py (internal)` | Projects internal record with hardcoded `allowedActions` (`canCancel: true`). | Standard projection helper normalizing `ResearchExperiment` from `ResearchWriteOwner` into BFF DTO, dynamically computing `allowedActions` based on real lifecycle state (`canCancel` only for `queued`/`running`). | Corrupt or missing lifecycle fields default safely; status preserves domain truth. | **U10A**: `test_exp002_bff_research_experiments_contract.py` |
| `get_experiment_logs` | line 3546 (ACG-02-001, KEEP)<br>Callers: `main.py`, `research/router.py` | Returns hardcoded synthetic mock log entries from memory. | Route to domain owner run stage logs (`ResearchOrchestratorStore`) or worker execution logs for experiment runs. | Unknown experiment returns 404; experiment with no associated runs returns empty logs list `[]`. | **U10A**: `test_bff_evolution_experiment_jobs_events_contract.py` |
| `get_experiment_metrics` | line 3563 (ACG-02-001, KEEP)<br>Callers: `main.py`, `research/router.py` | Returns synthetic dictionary of metric floats from in-memory fixture. | Route to domain owner execution receipts and metrics from `ResearchOrchestratorStore` or `ResearchWriteOwner`. | Unknown experiment returns 404; pending/running experiment returns empty metrics object `{}`. | **U10A**: `test_bff_evolution_experiment_jobs_events_contract.py` |
| `get_experiment_artifacts` | line 3580 (ACG-02-001, KEEP)<br>Callers: `main.py`, `research/router.py` | Returns synthetic hardcoded artifact list from in-memory dictionary. | Route to `ResearchOrchestratorStore.list_artifacts` / `get_artifact` or Postgres `research.research_notes` / artifacts table. | Unknown experiment returns 404; empty artifact set returns empty list `[]`. | **U10A**: `test_bff_evolution_experiment_jobs_events_contract.py` |

---

### 5.3 Regression test suites business acceptance and scope mapping

All four regression test suites are preserved without skipping or weakening assertions.
The primary contract suite `test_bff_evolution_experiment_jobs_events_contract.py` has been executed
and verified with 29 passed tests. The other three suites have been verified via pytest collection
(31 + 17 + 5 = 53 collected tests, distinct from execution):

| Test File | Test Case Count & Verification Mode | Business Cases & Invariants | Baseline Behavior | U10A Acceptance Obligation | U10B Acceptance Obligation |
| --- | --- | --- | --- | --- | --- |
| `test_bff_evolution_experiment_jobs_events_contract.py` | **29 tests** (Executed & Passed exit 0) | **Evolution** (8 tests): program list, get, patch, runs, candidates, actions.<br>**Experiments** (8 tests): list, get, 404, logs, metrics, artifacts, action, idempotency.<br>**Jobs** (6 tests): list empty, list with seed, get detail, get 404, get logs, action, action 404.<br>**Events** (4 tests): list, filter, degraded, stream. | `_seed_job` seeds in-memory dataset; `EvolutionCommandAdapter` returns fake 202 `executed` for `JobAction`. | `_seed_job` replaced with typed domain projection. `test_jobs_action` fails closed with `ActionUnavailableError` if backend owner action is not yet wired. | In U10B, `test_jobs_action` returns real domain execution receipt for research orchestrator runs. |
| `test_bff_b2_002_evolution_jobs_ops.py` | **31 tests** (Collected via `pytest --collect-only`) | `GET /bff/jobs` list + envelope (200, items, page_info, meta.surfaces).<br>`GET /bff/jobs/{id}` detail + 404 for unknown id.<br>HTTP 401 unauthenticated for all endpoints. | `_EvolutionJobsOpsTestStore` returns local snapshot fixtures for jobs. | Replaces mock store with `JobReadPort` test fixture; verifies 200 envelope and 401/404 handling. | Unchanged; read facade remains stable. |
| `test_exp002_bff_research_experiments_contract.py` | **17 tests** (Collected via `pytest --collect-only`) | Experiment list/detail envelope, status-specific fields (`running`, `completed`, `failed`), analysis links injection, 404 on missing, 401 unauthenticated. | Reads from in-memory `read_store._get_dataset("research_experiments")`. | Replaces in-memory store with `ResearchWriteOwner` readback; preserves analysis link injection. | Verifies `allowedActions.canCancel` dynamically matches `ResearchWriteOwner` state. |
| `test_assistant_context_pack.py` | **5 tests** (Collected via `pytest --collect-only`) | Assistant context collectors extracting active jobs, research experiments, and ticket summaries. | Collectors call `read_store.list_jobs_bff` (which returns tickets). | Collectors call `JobReadPort.list_jobs_bff` and receive real operational jobs; ticket queries go to ticket collector. | Preserves clean separation between ticket context and job context. |

---

### 5.4 Negative verification, concurrency, and failure modes

Implementation tasks (U10A and U10B) must prove the following negative cases:

1. **Persistence & Database Failure**:
   - Dropping Postgres connection or simulating write conflict in `ResearchWriteOwner` raises database error, rolls back transaction, and returns HTTP 500/503. It **never falls back to an in-memory dictionary**.
2. **Concurrency & Late Completion Race (U10B)**:
   - When a cancellation request succeeds, `services/research` persists a cancellation fence timestamp `t_cancel`.
   - A simulated worker completion arriving at `t_completion > t_cancel` is rejected by the owner with 409 Conflict, marked as orphaned, and does NOT overwrite the `canceled` status.
3. **Concurrent Retry Idempotency (U10B)**:
   - Sending two concurrent retry requests with the same `Idempotency-Key` executes exactly one domain retry, creates attempt `N+1`, and returns the identical attempt record for both requests without spawning duplicate runs.
   - Retrying an active job (`running`, `pending`) is rejected with HTTP 409 Conflict.
4. **Permission & Tenant Isolation**:
   - Request without valid `Authorization` header returns HTTP 401 Unauthorized.
   - Request with tenant header attempting to access another tenant's job returns HTTP 403 Forbidden or 404 Not Found.
5. **Action Unavailability Honesty**:
   - Invoking `POST /bff/jobs/{job_id}/actions/cancel` against a source that lacks backend cancel implementation (e.g. `job-trainer-*` or `job-ingest-*`) returns HTTP 400 or 503 `ActionUnavailableError` with `action_id`, `job_id`, and `reason="owner_operation_unsupported"`.
   - It **never returns fake HTTP 202/200 with status="executed"**.

---

## 6. Rollout and delivery boundaries

- D-JOBS delivery consists solely of this contract decision document and its accompanying evidence manifest.
- No runtime source code is modified in this task.
- Delivery follows the standard per-task PR workflow into `dev`, with exact-head independent review by Codex.
- Merge identity will be established by GitHub and the supervisor integration runner upon passing checks.
