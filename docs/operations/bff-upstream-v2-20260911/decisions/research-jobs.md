# Research and Jobs source, identity, and action contract

Task: `BFF-RESEARCH-JOBS-CONTRACT-DECISION-001` (D-JOBS). Owner: Antigravity.
Independent reviewer: Codex. Date: 2026-09-13.
Status: design selected; independent review pending. This document implements
V2 §02.9's design prerequisite, not U10A/U10B source delivery or hosted acceptance.

## 1. Decision and evidence boundary

Fixed decision: **Establish Management Jobs as a typed read composition and
single `JobAction` dispatch seam across five asynchronous backend sources plus
one read-only external diagnostic source, while maintaining strict domain aggregate
separation**. Do not build a universal "JobStore", a second distributed
scheduler, a shared worker pool, or an in-memory execution overlay. Domain
owners retain exclusive write authority, state persistence, and worker
lifecycle management for their respective job types.

Management UI and BFF observe and interact with jobs via standard contracts:
- Read composition: `GET /bff/jobs`, `GET /bff/jobs/{job_id}`, `GET /bff/jobs/{job_id}/logs`, and `GET /bff/sse/jobs/{jobId}/progress`.
- Action dispatch: `POST /bff/jobs/{job_id}/actions/{action_id}` routed via `CommandAdapterService` and `JobCommandAdapter` to the qualified domain owner.
- Aggregate boundaries are strictly preserved: **`ResearchTicket != Experiment != OrchestratorRun != Job`**. They represent fundamentally distinct business entities, lifecycles, and storage models.

Crucial distinction between execution phases:
- **Observed capabilities**: What exists in backend services today (e.g. gateway has `/cancel` updating row/events without worker kill; policy-learning has `/reject` updating row without worker kill; training-session and source-ingestion have NO cancel endpoint; orchestrator tasks/runs persist to disk JSON files without run cancel endpoint or archive mutation).
- **U10A (Read Plumbing & Minimal Write Wiring)**: Connects `ResearchWriteOwner` to read surface, deletes in-memory `_experiments` dictionary, implements typed read projection (`JobReadPort`), decouples tickets from jobs, un-fakes `EvolutionCommandAdapter`, and ensures unimplemented actions fail closed with `ActionUnavailableError` (never fake 200/202).
- **U10B (Real Action Execution Closure)**: Bounded strictly to the 13 paths declared in `dispatch-map.json` (`services/research/main.py`, `services/research/write_owner.py`, and BFF command adapters). U10B implements real cancellation and cancellation fencing for domain owners within its scope (`services/research/`).
- **Unresolved Composition Obligations**: Because U10B's 13-path scope excludes `services/research-worker-gateway/`, `services/training-session/`, `services/source_ingestion/`, and `services/policy-learning/`, any missing backend owner operations (such as gateway worker termination/fence, trainer preview cancel, source-ingest extraction cancel, policy learning worker stop, or cross-service governance promotion) are **explicitly preserved as missing obligations** for follow-up domain tasks. U10B must not silently broaden its grants or pretend these missing backend capabilities are implemented.

### Operator decision items and authority boundary

Operator decisions are **explicitly required** (`operator_decision_required_for_selected_scope=true`):
1. **Retention and Archival Schedules**: Neither U10A nor U10B may invent retention periods or archival purge authorities for job stores (`worker_jobs.json`, `research_runs.json`, `training_session.authority_records`, `source_management_store.py`, `policy_jobs.json`). Defining retention periods (e.g. 30-day cold storage vs permanent record) and archival mutator authority requires explicit operator policy decisions.
2. **Promotion to Paper Canary / Live Trading**: Promotion of research experiment candidates, strategy seeds, or policy-learning model weights across stage boundaries requires formal Governance review proposals and signed operator tokens. Autonomous promotion to capital-affecting or live execution without operator approval is strictly prohibited.
3. **No Universal JobStore or Universal Scheduler**: Replacing domain-owned stores with a monolithic database or scheduler is rejected; any alteration of this architecture requires operator decision.
4. **OpenClaw Writable Operations Excluded**: OpenClaw product BFF routes remain strictly read-only diagnostics; granting shell, repo-write, or supervisor-task materialization capabilities through product routes is prohibited.

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
| `BFF/research/routes/experiments.py:60–306, 453–550` | `read_store.py` no longer exists in repository. Routes in `experiments.py` fall back to empty logs `[]`, empty metrics `{}`, and empty artifacts `[]` when `read_store` or `research_knowledge_source` methods are missing, rather than hardcoded synthetic data. |
| `services/research/write_owner.py:48–85, 200–546` | Canonical persistent `ResearchWriteOwner` exists using `PostgresJsonOwnerStore` over `research.research_tickets`, `research.research_experiments`, and `research.research_notes`. It provides atomic mutations (`create_research_experiment`, `cancel_research_experiment`, `get_research_experiment`, `list_research_experiments`). Method name is `create_research_experiment`, not `create_experiment`. |
| `services/research-worker-gateway/main.py:406–574`; `store.py:100–186` | Worker gateway generates native `wjob-{date}-{seq}` IDs (main.py:422). Stored in `worker_jobs.json` + `research_worker_gateway.worker_events`. `cancel_job` (main.py:559-574) only mutates status row and appends event; has no worker stop or cancellation fence. `DispatchJobBody` and list/get routes contain no tenant filtering or token auth. |
| `services/research/main.py:182–202, 745–1180`; `store.py:99–235` | Research orchestrator generates native `rtask-{date}-{seq}` and `rrun-{date}-{seq}` IDs. `ResearchOrchestratorStore` uses JSON files (`research_tasks.json`, `research_runs.json`, `research_artifacts.json`, `research_proposals.json`) and optional Postgres events only (`store.py:100-139`), NOT Postgres storage for tasks/runs. Currently has NO run cancel endpoint on `main.py`. |
| `services/training-session/main.py:499–522, 1800–1835`; `store.py:179–208, 483–500, 831–847` | Training session generates native `pvjob-{digest[:16]}` IDs. Employs dual backend: `TrainingSessionStore` or authoritative `PostgresTrainingSessionStore` (`training_session.authority_records` JSONB table for all mutable records including `preview_job` with transaction-scoped advisory locking). Inbound authority middleware authenticates requests; list/get enforces `_tenant_records` and `_require_tenant_record`. Has NO cancel endpoint or abort mechanism for `preview_eval_worker.py`. |
| `services/source_ingestion/main.py`; `connectors/base.py:634`; `pipeline.py` | Ingestion service generates native `ingest-{uuid}` or `run-{connector_id}` IDs. Backed by `SourceManagementStore` and pipeline checkpoints. Has NO cancel endpoint anywhere in the service. |
| `services/policy-learning/main.py:189–196, 551–564`; `store.py` | Policy learning generates native `plj-{date}-{seq}` IDs. Stored in `policy_jobs.json` + `policy_learning.events`. `reject_job` only mutates status row; has no worker stop; direct promotion returns 409 (requires Governance). |
| `services/openclaw-gateway-adapter/main.py:458–490, 3569–3610`; `tool_workflow_bridge.py:1005` | OpenClaw workflow jobs triggered via `POST /api/openclaw-adapter/workflows/trigger` and queried via `GET /api/openclaw-adapter/workflows/jobs/{job_id}` (calling upstream OpenClaw `POST /api/workflows/trigger` and `GET /api/jobs/{job_id}`). Upstream owns state; adapter stores invocation audit log only. Strictly read-only for product BFF; all write actions fail closed; no development-supervisor tasks. |

---

## 2. Investigation and qualification of the six candidate job/run sources

The system discovery identified six distinct job and run mechanisms across the platform.
The table below qualifies each source, establishing its canonical domain owner,
storage mechanism, native ID scheme, Management projection mapping, auth and tenant
enforcement, existing gaps, observed capabilities, and justified admission decision.

| Candidate Source | Underlying Domain Owner & Store | Native ID Scheme | Management Projection Scheme | Auth & Tenant Enforcement & Gaps | Observed Capabilities vs Missing Obligations | Management Admission Status | Responsible Scope |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **1. Research worker jobs** | `services/research-worker-gateway/` (`ResearchWorkerGatewayStore` file `worker_jobs.json` + optional `PostgresWorkerEventStore` `research_worker_gateway.worker_events`) | `wjob-{YYYYMMDD}-{index:03d}` (main.py:422) | `job-worker-{wjob_id}` | **Enforcement**: None in routing.<br>**Gaps**: `DispatchJobBody` has no `tenant_id`; list/get/cancel routes have no tenant filter and no token auth dependency. | **Observed**: Dispatch queues/runs; cancel (main.py:559-574) only sets status `canceled` & appends event.<br>**Missing**: No worker process kill; no cancellation fence; no attempt lineage retry; no archive/promote. | **Conditionally Admitted (Read & Safe Stub Dispatch)**; Excluded from U10B backend action closure. | Read: U10A.<br>Worker stop & fence: **GW-STOP-FENCE-001** (Gateway owner task, outside U10B). |
| **2. Research orchestrator runs** | `services/research/` (`ResearchOrchestratorStore`, storing tasks/runs in `research_tasks.json`/`research_runs.json` and optional Postgres events, store.py:100-139) | `rrun-{YYYYMMDD}-{index:03d}` under `rtask-{YYYYMMDD}-{index:03d}` (main.py:759, 856) | `job-orchestrator-{rrun_id}` (linked to parent `rtask_id`) | **Enforcement**: Task headers carry optional tenant ID in admission contract.<br>**Gaps**: List/get routes (`GET /api/research-orchestrator/tasks`, `GET .../runs`) lack tenant query filtering; unauthenticated FastAPI endpoints. | **Observed**: Dispatches runs via `POST /api/research-orchestrator/tasks/{task_id}/runs`, tracks stage/status, records artifacts & events.<br>**Missing**: No run cancel endpoint on `main.py`; no late-completion fence; archive lacks owner mutation/authority; promote lacks artifact/approval/target-stage/readback mapping. | **Admitted (Qualified)**; In scope for U10A read and U10B backend action execution. | Read: U10A.<br>Run cancel & fence: **U10B** (`services/research/main.py` is in U10B scope).<br>Archive/Promote: Follow-up orchestrator governance tasks + Operator Decision. |
| **3. Trainer preview jobs** | `services/training-session/` (Dual backend: `TrainingSessionStore` or authoritative `PostgresTrainingSessionStore` on `training_session.authority_records` with advisory lock, store.py:179-208, 483-500, 831-847) | `pvjob-{digest[:16]}` or `pvjob-{uuid[:12]}` (main.py:170-174, 1843) | `job-trainer-{pvjob_id}` | **Enforcement**: `main.py:499-522` enforces inbound authority middleware on `/api/training/*` via `inbound_authority.py`; `list_preview_jobs` (1806) filters via `_tenant_records`; `get_preview_job` (1823) and `queue_preview_job` (1836) enforce `_require_tenant_record` (404 on mismatch).<br>**Gaps/Exceptions**: Test token exceptions (`token_kind == 'test-disabled'`). No cancel endpoint or worker thread interrupt exists. | **Observed**: Exposes `GET /api/training/preview-jobs`, `GET .../preview-jobs/{job_id}`, `POST .../sessions/{session_id}/preview-jobs`; triggers eval previews via worker; records metrics in `authority_records`.<br>**Missing**: **NO cancel endpoint or operation exists** in `training-session`; eval thread runs to completion without abort. | **Conditionally Admitted for Read Projection Only**; Excluded from Management Job Actions. | Read: U10A.<br>Cancel operation: **TS-CANCEL-001** (Training session owner task, outside U10B). |
| **4. Source-ingest runs/jobs** | `services/source_ingestion/` (`SourceManagementStore`, `pipeline.py`, `connectors/base.py`) | `ingest-{uuid4().hex[:12]}` or `run-{connector_id}` (connectors/base.py:634) | `job-ingest-{ingest_run_id}` | **Enforcement**: Controller token verified on connector mutation via `load_controller_token()`.<br>**Gaps**: `/api/source-ingest/jobs` has no tenant filter and no auth dependency. | **Observed**: Processes ingest batches, records receipts, frontier replay.<br>**Missing**: **NO cancel endpoint exists**; no graceful extraction worker halt. | **Conditionally Admitted for Read Projection Only**; Excluded from Management Job Actions. | Read: U10A.<br>Cancel operation: **SI-CANCEL-001** (Source ingestion owner task, outside U10B). |
| **5. Policy-learning jobs** | `services/policy-learning/` (`PolicyLearningStore` file `policy_jobs.json` + `policy_learning.events`, `scheduler_worker.py`) | `plj-{YYYYMMDD}-{index:03d}` (main.py:189-196) | `job-policy-{plj_id}` | **Enforcement**: Tenant scope helper for claims/DLQ.<br>**Gaps**: `list_jobs` has no tenant filter; unauthenticated endpoints; reject is row-only. | **Observed**: `reject_job` (main.py:551-564) updates row status `rejected` & appends event; promote returns 409.<br>**Missing**: Reject does not halt running worker; promote requires Governance. | **Conditionally Admitted for Read Projection Only**; Excluded from Direct JobAction Dispatch. | Read: U10A.<br>Worker stop: **PL-CANCEL-001** (Policy learning owner task, outside U10B). |
| **6. OpenClaw workflow jobs** | `services/openclaw-gateway-adapter/` (`tool_workflow_bridge.py`, `main.py:3569-3610`) + upstream OpenClaw service (`main.py:458-490`) | Upstream OpenClaw `job_id` or `id` (`tool_workflow_bridge.py:1005`) | `job-openclaw-{job_id}` | **Enforcement**: `trigger_workflow` requires `X-Operator-Id` header (401 if missing), maps operator role, control mode, confirm tokens, and passes upstream client credentials.<br>**Gaps**: `GET .../workflows/jobs/{job_id}` lacks auth middleware; no tenant isolation upstream; no `list_jobs` endpoint on adapter. | **Observed**: Upstream workflow trigger (`POST /api/workflows/trigger`) and single job status query (`GET /api/jobs/{job_id}`).<br>**Missing**: All write actions (cancel, retry, archive, promote) from product BFF are unsupported; no local job persistence. | **Admitted Strictly as Read-Only Detail Observation**; Excluded from all Job Actions. | Read: U10A (read-only when `job_id` is known).<br>Write actions: Permanently excluded per repository architecture. |

---

### Detailed investigation and source persistence qualification

#### 2.1 Research worker jobs (`services/research-worker-gateway`)
- **Native Store & Persistence**: `ResearchWorkerGatewayStore` (`worker_jobs.json` in local directory) and `PostgresWorkerEventStore` (`research_worker_gateway.worker_events`). Configured via `RESEARCH_WORKER_GATEWAY_DATA_DIR` and `DATABASE_URL`.
- **Concurrency & Durability Limitations**: Single-instance file read/write (`_write_json`) susceptible to file race conditions if multiple gateway processes run concurrently. No distributed locks. Worker processes spawned via `subprocess.Popen` without PID tracking or cancellation fencing. Durability relies on local filesystem flush.
- **Native ID Scheme**: `_next_id("wjob", timestamp, existing)` generates formatted IDs `wjob-{YYYYMMDD}-{index:03d}` (main.py:422). Output records use `wgout-{YYYYMMDD}-{index:03d}` (main.py:439). Events use `wgevt-{YYYYMMDD}-{index:03d}` (main.py:196).
- **Management Projection**: Projected into the unified Job model as `job-worker-{wjob_id}`.
- **Auth and Tenant Enforcement vs Gaps**:
  - `DispatchJobBody` (main.py:312-324) declares `worker`, `requested_mode`, `dispatch_mode`, `objective`, `task_id`, `run_id`, `input_refs`, `parameters`, `actor_id`, `idempotency_key`, `requested_at`. It **contains no `tenant_id` field**.
  - `list_jobs` (main.py:508-524) accepts query parameters `worker`, `status`, `task_id`, `run_id`, but **contains no tenant parameter or filtering**.
  - `get_job`, `get_job_status`, and `cancel_job` (main.py:527-574) have no authentication dependencies.
- **Owner Tests**: `services/research-worker-gateway/test_main.py`, `test_store.py`.

#### 2.2 Research orchestrator runs (`services/research`)
- **Native Store & Persistence**: `ResearchOrchestratorStore` (`store.py:100-139`) uses file-backed JSON stores: `research_tasks.json`, `research_runs.json`, `research_artifacts.json`, `research_proposals.json` in `data_dir` (configured via `RESEARCH_ORCHESTRATOR_DATA_DIR`), and optional `PostgresResearchEventStore` for events only (`research_events.jsonl` or `research.orchestrator_events`). **`ExperimentTask` and `ExperimentRun` are stored in JSON files on disk, NOT Postgres tables**.
- **Concurrency & Durability Limitations**: File-backed JSON maps (`_write_map`) with file overwrite; no row-level locking or optimistic concurrency. Multi-instance writes risk overwriting runs/tasks. No process cancellation or late completion fence currently exists on `main.py`.
- **Native ID Scheme**: Tasks: `rtask-{YYYYMMDD}-{index:03d}`; Runs: `rrun-{YYYYMMDD}-{index:03d}`; Artifacts: `rart-{YYYYMMDD}-{index:03d}`; Events: `revt-{YYYYMMDD}-{index:03d}`.
- **Management Projection**: Projected into the unified Job model as `job-orchestrator-{rrun_id}` under `rtask_id`.
- **Auth and Tenant Enforcement vs Gaps**:
  - Admission contracts allow tenant headers on task creation, but `list_runs` and `get_run` in `ResearchOrchestratorStore` do not enforce tenant filtering.
  - Endpoints lack FastAPI authentication dependencies.
- **Owner Tests**: `services/research/test_store.py`, `test_main.py`.

#### 2.3 Trainer preview jobs (`services/training-session`)
- **Native Store & Persistence**: Dual backend configured via `build_training_session_store(data_dir)` (`store.py:831-847`):
  - In-memory/File: `TrainingSessionStore(data_dir)` when `TRAINING_SESSION_EVENT_STORE_BACKEND=jsonl`.
  - Authoritative HA Store: `PostgresTrainingSessionStore` (`store.py:179-208, 483-500`) when `TRAINING_SESSION_EVENT_STORE_BACKEND=postgres`. Stores `session`, `controls`, `preview`, `preview_job`, `replay`, `functional_health` in JSONB table `training_session.authority_records` (`records_table`) and events in `training_session.teaching_events` (`events_table`).
- **Concurrency & Durability Limitations**:
  - In Postgres mode: Authoritative with transaction-scoped advisory locks for read/decide/write mutations (`store.py:185-190`), ACID durability, prevents concurrent admission races across instances.
  - In JSONL mode: Local file only, no cross-process locking.
  - Operational limitation: `preview_eval_worker.py` dispatches background worker threads without abort or in-flight cancellation handles.
- **Native ID Scheme**: `_preview_job_id` (main.py:170-174, 1843) generates `pvjob-{digest[:16]}` (deterministic with idempotency key) or `pvjob-{uuid[:12]}`.
- **Management Projection**: Projected into the unified Job model as `job-trainer-{pvjob_id}`.
- **Auth and Tenant Enforcement vs Gaps**:
  - `services/training-session/main.py:499-522` enforces inbound authority middleware (`enforce_training_inbound_authority`) across all `/api/training/*` endpoints using `inbound_authority.py` (`authenticate_training_request`).
  - `list_preview_jobs` at line 1806 strictly filters records using `_tenant_records(store.list_preview_jobs())` matching `_request_authority().tenant_id`.
  - `get_preview_job` at line 1823 enforces `_require_tenant_record(job, "preview job not found")` (HTTP 404 on mismatch).
  - `queue_preview_job` at line 1836 also enforces `_require_tenant_record(session, "training session not found")`.
- **Owner Tests**: `services/training-session/test_store.py`, `test_main.py`.

#### 2.4 Source-ingest runs/jobs (`services/source_ingestion`)
- **Native Store & Persistence**: `SourceManagementStore` (`source_management_store.py`), `pipeline.py`, and `connectors/base.py`. Checkpoint watermarks and frontier items persisted per connector.
- **Concurrency & Durability Limitations**: Sequential execution per connector; checkpoint watermarks updated after verified batch extractions. No graceful extraction worker/thread cancellation endpoint; process abort leaves partial frontier batches for replay.
- **Native ID Scheme**: `ingest-{uuid4().hex[:12]}` (connectors/base.py:634) or connector-based `run-{connector_id}` / `ingest-{connector_id}`, stored as `ingest_run_id`.
- **Management Projection**: Projected into the unified Job model as `job-ingest-{ingest_run_id}`.
- **Auth and Tenant Enforcement vs Gaps**:
  - Mutation endpoints verify controller authorization via `load_controller_token()` and `_fence_managed_connector_mutation`.
  - `GET /api/source-ingest/jobs` and `GET /api/source-ingest/jobs/{ingest_run_id}` do not enforce tenant filtering or authentication.
- **Owner Tests**: `services/source_ingestion/tests/test_pipeline.py`, `test_connectors.py`.

#### 2.5 Policy-learning jobs (`services/policy-learning`)
- **Native Store & Persistence**: `PolicyLearningStore` (file-backed `policy_jobs.json` + `policy_learning.events`) and `scheduler_worker.py`.
- **Concurrency & Durability Limitations**: Local file persistence without cross-process locks. Reject handler updates JSON row status to `rejected` and appends event, but background scheduler thread (`scheduler_worker.py`) does not terminate active training processes.
- **Native ID Scheme**: `_next_job_id` (main.py:189-196) generates `plj-{YYYYMMDD}-{index:03d}`. Events use `plevt-{YYYYMMDD}-{seq:03d}`.
- **Management Projection**: Projected into the unified Job model as `job-policy-{plj_id}`.
- **Auth and Tenant Enforcement vs Gaps**:
  - Candidate claims and DLQ records have tenant scope; helper `_resolve_tenant_scope` exists.
  - `list_jobs`, `get_job`, `propose_job`, and `reject_job` lack token authentication dependencies and tenant query filtering.
- **Owner Tests**: `services/policy-learning/tests/test_store.py`, `test_main.py`.

#### 2.6 OpenClaw workflow jobs (`services/openclaw-gateway-adapter`)
- **Native Store & Persistence**: Upstream OpenClaw service owns workflow execution and state persistence. Adapter `services/openclaw-gateway-adapter/` provides the integration bridge (`tool_workflow_bridge.py:1005`, `main.py:3569-3610`) and upstream client `OpenClawClient` (`main.py:458-490`). The adapter maintains local invocation audit records (`_audit` store in `tool_workflow_bridge.py`), but maintains NO local persistent job store for workflow jobs.
- **Native ID Scheme**: Upstream OpenClaw `job_id` or `id`, preserved by `tool_workflow_bridge.py:1005` from the upstream workflow trigger response.
- **Management Projection**: Projected into the unified Job model as `job-openclaw-{job_id}`.
- **Auth and Tenant Enforcement vs Gaps**:
  - `POST /api/openclaw-adapter/workflows/trigger` (main.py:3569) enforces required `X-Operator-Id` header (HTTP 401 if missing), parses operator role, control mode, confirmation tokens, and delegates to `_BRIDGE.trigger_workflow(...)`.
  - `GET /api/openclaw-adapter/workflows/jobs/{job_id}` (main.py:3604) queries upstream `GET /api/jobs/{job_id}` via `OpenClawClient.get_job(job_id)`.
  - Gaps: `GET .../workflows/jobs/{job_id}` lacks explicit token authentication middleware; upstream OpenClaw workflow engine has no native multi-tenant fencing. No `list_jobs` endpoint on adapter.
- **Architecture Boundary**:
  - Product BFF is strictly read-only for OpenClaw workflow jobs (detail observation only via `GET /bff/jobs/{job_id}` when `job_id` is known).
  - All write actions (cancel, retry, archive, promote) from product BFF are unsupported and fail closed (HTTP 400).
  - Product BFF and OpenClaw adapter never run development-supervisor tasks, dev-doc generation, worktree preparation, or repository-writing workflows per `AGENTS.md` and `docs/02-architecture/development-tooling-product-boundary.md`.
- **Owner Tests**: `services/openclaw-gateway-adapter/test_tool_workflow_bridge.py`.

---

### Strict distinction of domain aggregates and Experiment obligations

The system must not conflate these four distinct aggregates:
1. **`ResearchTicket`**: Business research request or bug report owned by `ResearchWriteOwner` (`research.research_tickets`). Identifiers: `ticket-xxx`. States: `open`, `in_progress`, `closed`, `archived`. Actions: `canEdit`, `canClose`, `canArchive`. Tickets are tracked metadata, **not asynchronous execution jobs**.
2. **`Experiment` (`ResearchExperiment`)**: Parametric research experiment specification owned by `ResearchWriteOwner` (`research.research_experiments`). Identifiers: `exp-YYYYMMDD-xxx`.
   - In accordance with accepted design **Section 6.1** (`docs/operations/bff-upstream-v2-20260911/02_RESEARCH_AND_DESIGN.md`), Experiments must NOT be treated as `canCancel`-only aggregates. They possess four distinct operational obligations:
     - **`invalidated`**: Explicit owner mutation marking an experiment run/results invalid (e.g. following evaluation failure, corrupted dataset, or model collapse). Mutates status to `invalidated` in `research.research_experiments` with reason and audit timestamp.
     - **`attached_to_review`**: Links the experiment definition, artifact hash, and telemetry metrics to an operator / Governance review packet (e.g. replication bridge proposal).
     - **`archived`**: Retains experiment specification and results under an archived state (`is_archived=True`), excluding it from default active queries while preserving audit trail.
     - **`retry`**: Re-queues an experiment run or parameter variant under the same lineage with incremented attempt counter referencing the parent experiment ID.
     - **`cancel`**: In-flight abort for experiments currently in `queued` or `running` state.
3. **`OrchestratorRun`**: Specific execution attempt of an experiment task, owned by `ResearchOrchestratorStore` (`research_runs.json`, `main.py:745-1180`). Identifiers: `rrun-{date}-{seq}` under `rtask-{date}-{seq}`. Contains execution artifacts, hardware telemetry, and stage logs.
4. **`Job`**: Unified Management operational projection of asynchronous units of work across qualified domain owners (worker jobs, orchestrator runs, trainer previews, ingestion runs, policy learning jobs). Identifiers: `job-<source>-<native_id>`. States: standard normalized job lifecycle (`pending`, `running`, `completed`, `failed`, `canceled`).

---

## 3. Owner contracts, gaps, and action semantics across all sources

### Detailed action semantics per operation

#### Cancellation
- **Lifecycle Sequence**: `cancel_requested` -> `owner_accepted` -> `worker_stopped` -> `terminal_receipt`.
- **Fencing Requirement**: When cancellation is accepted by the domain owner, the owner must persist a cancellation fence timestamp `t_cancel`. Any subsequent completion payload arriving from a worker after the fence (`t_completion > t_cancel`) must be rejected by the owner with 409 Conflict and recorded as orphaned/discarded, preventing late completion from overriding a canceled state.
- **Verification**: Cancellation is not complete merely because the database row status changed to `canceled`. Acceptance requires verification that the worker process/task actually received the stop signal (`SIGTERM`/`SIGKILL` or thread interrupt) and terminated execution.

#### Retry
- **Eligibility**: Only jobs in eligible terminal states (`failed`, `canceled`, `timeout`) may be retried. Active jobs (`running`, `pending`) must reject retry with 409 Conflict.
- **Attempt Lineage**: Retrying a job must not overwrite the historical record. The domain owner must generate a new attempt record (`attempt_number = N + 1`) linking to `parent_job_id` or `root_job_id`.
- **Idempotency**: Retrying with the same idempotency key must return the existing retry attempt without spawning duplicate workers.

#### Archive
- **Retention Semantics**: Archiving represents a change in visibility and retention policy, **not physical record deletion**.
- **Owner Mutation Requirement**: Archiving requires an actual domain owner mutation endpoint and persistent state update (setting `is_archived=True`, recording `archived_at` and `archived_by`). It is **NOT merely a passive read-time filter**.
- **State Transition**: Archived records are excluded from default lists (unless `include_archived=true`) and marked read-only. Further mutations (cancel, retry) are forbidden (409 Conflict).
- **Retention Period & Authority**: Defining retention duration (e.g., 30/90 days cold storage) and purge authority requires explicit operator policy decisions.

#### Promote
- **Governance Gate**: Promotion (e.g., promoting a research orchestrator candidate, policy-learning model, or strategy seed to candidate/paper/live status) **requires explicit Governance review and signed evidence**.
- **Required Promotion Mapping**:
  1. **Artifact Verification**: Validated candidate weights or strategy code artifact digest and benchmark metrics.
  2. **Approval Gate**: Formal two-person approval or operator authorization token.
  3. **Target Stage**: Explicit target stage binding (`strategy_seed_registry`, `model_registry`, `paper_canary`).
  4. **Registry Readback**: Verified readback confirmation from the destination registry confirming registration before returning promotion success.
- **No Direct Live Elevation**: Direct promotion calls without matching Governance authorization fail closed (HTTP 409/403). Autonomous promotion to paper canary or live trading without operator authorization is prohibited.

---

### Settle every source x {cancel, retry, archive, promote}

The following matrix settles the exact contract, responsible owner, current/proposed API and store, test proof, and operator decision status for every admitted source and action:

| Source | Action | Current Behavior & Gap | Target Contract & Proposed API | Responsible Domain Owner & Scope | Owner Test File | Operator Decision Status |
| --- | --- | --- | --- | --- | --- | --- |
| **research_worker_gateway** | **Cancel** | `POST /api/research-worker-gateway/jobs/{id}/cancel` updates row status in `worker_jobs.json` and appends event to `worker_events`. Does NOT kill subprocess or set late fence. | Add subprocess kill (`SIGTERM`/`SIGKILL`) in `_execute_worker` and cancellation fence in `cancel_job`. | Gateway owner (`GW-STOP-FENCE-001`, outside U10B). | `services/research-worker-gateway/test_main.py` | No (in-scope for GW task) |
| **research_worker_gateway** | **Retry** | No retry API. New dispatch via `POST .../jobs` with idempotency key loses attempt lineage. | Add `POST /api/research-worker-gateway/jobs/{id}/retry` with `attempt_number=N+1` and `parent_job_id`. | Gateway owner (follow-up task). | `services/research-worker-gateway/test_store.py` | No (standard domain retry) |
| **research_worker_gateway** | **Archive** | No archive API or retention policy. `worker_jobs.json` grows unboundedly. | Add `POST /api/research-worker-gateway/jobs/{id}/archive` with `is_archived=True`. Exclude from default list. | Gateway owner (follow-up task). | `services/research-worker-gateway/test_store.py` | **Operator Decision Required** (retention period & archival authority) |
| **research_worker_gateway** | **Promote** | Not supported. Worker jobs are raw execution compute. | Unsupported. Returns HTTP 400 Bad Request. | Permanently excluded from promotion. | N/A | No (permanently unsupported) |
| **research_orchestrator** | **Cancel** | `services/research/main.py` has NO run cancel endpoint. | Implement `POST /api/research-orchestrator/runs/{run_id}/cancel`, persist `canceled` in `research_runs.json`, and set cancellation fence timestamp. | In scope for **U10B** (`services/research/main.py`, `write_owner.py`). | `services/control-plane/bff/tests/test_research_jobs_action_receipts.py` | No (in-scope for U10B) |
| **research_orchestrator** | **Retry** | Dispatches new run under task via `POST .../tasks/{task_id}/runs`, but lacks explicit `attempt_number` and `parent_run_id` tracking. | Implement explicit run retry linking `parent_run_id` and incrementing `attempt_number` under `rtask`. | In scope for **U10B**. | `services/control-plane/bff/tests/test_research_jobs_action_receipts.py` | No (in-scope for U10B) |
| **research_orchestrator** | **Archive** | No archive mutation endpoint, retention period, or authority exists. | Add `POST /api/research-orchestrator/runs/{run_id}/archive` setting `is_archived=True` in `research_runs.json`. | Research Orchestrator owner (follow-up task). | `services/research/test_store.py` | **Operator Decision Required** (artifact retention period & archival authority) |
| **research_orchestrator** | **Promote** | Proposal handoff exists (`POST .../tasks/{id}/proposals`), but lacks artifact hash verification, operator approval, target stage binding, and registry readback. | Enforce: (1) candidate artifact hash check, (2) signed approval token, (3) target stage binding, (4) registry readback verification. | Research Orchestrator owner + Governance. | `services/research/test_store.py` | **Operator Decision Required** (elevation to paper canary / live) |
| **training_session** | **Cancel** | `services/training-session/main.py` has NO cancel endpoint. `preview_eval_worker.py` runs without abort handle. | Implement `POST /api/training/preview-jobs/{job_id}/cancel`, interrupt worker thread, mutate record with advisory lock. | Training Session owner (`TS-CANCEL-001`, outside U10B). | `services/training-session/test_main.py` | No (in-scope for TS task) |
| **training_session** | **Retry** | Can re-queue preview job via `POST .../sessions/{id}/preview-jobs`, but lacks attempt linkage. | Add `attempt_number` to `preview_job` record in `authority_records` linking previous `job_id`. | Training Session owner (follow-up task). | `services/training-session/test_store.py` | No (standard domain retry) |
| **training_session** | **Archive** | Preview jobs tied to session lifecycle; no independent preview job archive endpoint. | Add session/preview archival endpoint with `is_archived=True`. | Training Session owner (follow-up task). | `services/training-session/test_store.py` | **Operator Decision Required** (evaluation dataset & replay log retention) |
| **training_session** | **Promote** | Direct promotion returns 409 Conflict. | Requires signed Governance review proposal, candidate weights verification, model stage binding, and Model Registry readback. | Training Session owner + Governance. | `services/training-session/test_main.py` | **Operator Decision Required** (elevation to paper canary / live) |
| **source_ingestion** | **Cancel** | `services/source_ingestion` has NO cancel endpoint. Ingest workers cannot be interrupted. | Implement `POST /api/source-ingest/jobs/{id}/cancel`, graceful connector extraction interrupt, late-receipt fence. | Source Ingestion owner (`SI-CANCEL-001`, outside U10B). | `services/source_ingestion/tests/test_pipeline.py` | No (in-scope for SI task) |
| **source_ingestion** | **Retry** | Replay via `POST /api/source-ingest/frontier/{frontier_id}/replay`. | Add `POST /api/source-ingest/jobs/{id}/retry` resuming from last verified watermark with attempt count. | Source Ingestion owner (follow-up task). | `services/source_ingestion/tests/test_frontier.py` | No (standard domain retry) |
| **source_ingestion** | **Archive** | Connectors can be deactivated; raw ingest payload retention is unmanaged. | Add connector run archive endpoint; exclude from active frontier list. | Source Ingestion owner (follow-up task). | `services/source_ingestion/tests/test_pipeline.py` | **Operator Decision Required** (raw market data & document retention schedule) |
| **source_ingestion** | **Promote** | Seed extraction promotion to Strategy Seed Store. | Schema validation of candidate seed, write to `strategy_seeds`, and readback verification. | Source Ingestion owner (follow-up task). | `services/source_ingestion/tests/test_connectors.py` | **Operator Decision Required** (if seed alters live execution feeds) |
| **policy_learning** | **Cancel** | `POST /api/policy-learning/jobs/{id}/reject` mutates row status to `rejected`, but does NOT halt worker processes. | Add actual worker process kill/stop and cancellation fence in `reject_job`. | Policy Learning owner (`PL-CANCEL-001`, outside U10B). | `services/policy-learning/tests/test_main.py` | No (in-scope for PL task) |
| **policy_learning** | **Retry** | DLQ replay or new job submission. | Add `POST /api/policy-learning/jobs/{id}/retry` incrementing attempt count and referencing parent job ID. | Policy Learning owner (follow-up task). | `services/policy-learning/tests/test_store.py` | No (standard domain retry) |
| **policy_learning** | **Archive** | Policy learning jobs stored without archiving mechanism. | Add policy job archive endpoint with `is_archived=True`. | Policy Learning owner (follow-up task). | `services/policy-learning/tests/test_store.py` | **Operator Decision Required** (model checkpoint & replay buffer retention) |
| **policy_learning** | **Promote** | Direct promotion returns 409 Conflict (requires Governance). | Governance promotion gate: signed authorization, metric threshold check, stage binding, and readback. | Policy Learning owner + Governance (`GOV-PROMOTE-001`). | `services/policy-learning/tests/test_main.py` | **Operator Decision Required** (live trading capital allocation) |
| **openclaw_gateway_adapter** | **All Actions** | Upstream OpenClaw owns execution. Product BFF is strictly read-only diagnostics (`GET .../workflows/jobs/{id}`). | All write actions (cancel, retry, archive, promote) are unsupported and return HTTP 400 Bad Request. | Permanently excluded per repository architecture. | `services/openclaw-gateway-adapter/test_tool_workflow_bridge.py` | No (permanently read-only) |

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
|   - Experiment obligations: invalidated / attached_to_review / archived / retry|
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
| **`TS-CANCEL-001`** | `training-session` | `services/training-session/main.py` | Implement `POST /api/training/preview-jobs/{job_id}/cancel` endpoint, cancel preview evaluation worker thread in `preview_eval_worker.py`, and update session state via transaction-locked mutator. |
| **`SI-CANCEL-001`** | `source-ingestion` | `services/source_ingestion/routers/ingest_operations.py` | Implement `POST /api/source-ingest/jobs/{id}/cancel` endpoint, graceful connector extraction thread interruption, and cancellation fence. |
| **`PL-CANCEL-001`** | `policy-learning` | `services/policy-learning/main.py` | Update `POST /api/policy-learning/jobs/{id}/reject` to halt active training/imitation worker processes rather than performing a row-only update. |
| **`GOV-PROMOTE-001`** | `governance` | `services/control-plane/bff/` & `services/governance/` | Cross-service Governance promotion gate verification requiring signed operator authorization and target registry readback before candidate elevation. |

---

## 5. Source-specific API, store, and test acceptance mapping

### 5.1 Source-specific API and store acceptance mapping

The following table maps each admitted source to its native API, backing store,
BFF projection endpoint, supported actions, handling of unimplemented operations,
responsible delivery task, and operator decision status.

| Source ID | Domain Owner & Store Paths | Native API Endpoint(s) | Management BFF Route | Supported Actions in U10A / U10B | Unimplemented Actions & Handling | Responsible Scope & Task ID | Operator Decision Required |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **research_worker_gateway** | `services/research-worker-gateway/` (`ResearchWorkerGatewayStore` file `worker_jobs.json`, `PostgresWorkerEventStore`) | `GET /api/research-worker-gateway/jobs`<br>`GET .../jobs/{id}`<br>`POST .../jobs` | `GET /bff/jobs` (`job-worker-*`)<br>`GET /bff/jobs/{id}` | Read projection in U10A.<br>Safe stub dispatch in U10A. | Cancel: Gateway only does row update. U10A/U10B fail closed with `ActionUnavailableError` until `GW-STOP-FENCE-001` is completed.<br>Archive: Missing retention purge.<br>Promote: Unsupported (400). | Read: U10A.<br>Backend closure: **`GW-STOP-FENCE-001`** (Gateway owner). | **Yes** (worker job retention schedule & archive authority) |
| **research_orchestrator** | `services/research/` (`ResearchOrchestratorStore`, `research_tasks.json`, `research_runs.json`, `main.py:745-1180`) | `GET /api/research-orchestrator/tasks`<br>`POST .../tasks/{task_id}/runs`<br>`GET .../runs`<br>`GET .../runs/{run_id}` | `GET /bff/jobs` (`job-orchestrator-*`)<br>`POST /bff/jobs/{id}/actions/cancel` | Read projection in U10A.<br>True run cancellation & late completion fence in U10B.<br>Attempt lineage retry in U10B. | Archive: Missing owner mutation endpoint and authority.<br>Promote: Lacks artifact/approval/stage/readback mapping; rejected 409. | Read: U10A.<br>Action closure: **U10B** (`services/research/main.py` is in U10B scope).<br>Follow-up archive/promote tasks. | **Yes** (artifact retention schedule & live trading elevation) |
| **training_session** | `services/training-session/` (Dual backend: `TrainingSessionStore` or `PostgresTrainingSessionStore` on `training_session.authority_records`, `main.py:1800-1835`) | `GET /api/training/preview-jobs`<br>`GET .../preview-jobs/{job_id}`<br>`POST .../sessions/{session_id}/preview-jobs` | `GET /bff/jobs` (`job-trainer-*`)<br>`GET /bff/jobs/{id}` | Read projection in U10A. | Cancel: **NO cancel endpoint exists**. Actions fail closed with `ActionUnavailableError` (HTTP 400/503).<br>Archive: Tied to session.<br>Promote: Governance review required (409). | Read: U10A.<br>Cancel closure: **`TS-CANCEL-001`** (Training session owner). | **Yes** (preview dataset retention & model promotion to live) |
| **source_ingestion** | `services/source_ingestion/` (`SourceManagementStore`, `pipeline.py`, `routers/ingest_operations.py`) | `GET /api/source-ingest/jobs`<br>`GET .../jobs/{id}`<br>`POST .../jobs` | `GET /bff/jobs` (`job-ingest-*`)<br>`GET /bff/jobs/{id}` | Read projection in U10A. | Cancel: **NO cancel endpoint exists**. Actions fail closed with `ActionUnavailableError` (HTTP 400/503).<br>Archive: Unmanaged.<br>Promote: Requires seed verification. | Read: U10A.<br>Cancel closure: **`SI-CANCEL-001`** (Source ingestion owner). | **Yes** (raw data retention schedule & live feed promotion) |
| **policy_learning** | `services/policy-learning/` (`PolicyLearningStore` file `policy_jobs.json`, `main.py:189-196, 551-564`) | `GET /api/policy-learning/jobs`<br>`POST .../jobs/{id}/reject` | `GET /bff/jobs` (`job-policy-*`)<br>`GET /bff/jobs/{id}` | Read projection in U10A. | Reject/Cancel: Row-only in gateway; does not stop worker. Actions fail closed with `ActionUnavailableError`. Direct promote rejected (409). | Read: U10A.<br>Worker stop: **`PL-CANCEL-001`** (Policy learning owner). | **Yes** (checkpoint retention & capital allocation approval) |
| **openclaw_gateway_adapter** | `services/openclaw-gateway-adapter/` (`tool_workflow_bridge.py:1005`, `main.py:3569-3610`, upstream `main.py:458-490`) | `POST /api/openclaw-adapter/workflows/trigger`<br>`GET .../workflows/jobs/{job_id}`<br>(upstream: `POST /api/workflows/trigger`, `GET /api/jobs/{job_id}`) | `GET /bff/jobs/{id}` (`job-openclaw-*`, detail only) | Read-only detail observation in U10A (when `job_id` is known; no `list_jobs` route). | All write actions (cancel, retry, archive, promote) are unsupported and return 400. | Read: U10A.<br>Write actions: Permanently excluded. | No (permanently read-only) |

---

### 5.2 Original inventory cases (`read_store_migration_inventory.json`) mapping

The following mapping binds every historical `ReadSurfaceStore` inventory case for
experiments (lines 12895–12966) and jobs (lines 12972–13022) to its specific target disposition,
target owner, callers, negative cases, and test proof.
Crucially, **`services/control-plane/bff/read_store.py` no longer exists in the codebase**.
In the current baseline, `research/routes/experiments.py` falls back to empty logs `[]`, empty metrics `{}`, and empty artifacts `[]` when methods are missing from `read_store`/`research_knowledge_source`, rather than returning hardcoded synthetic mock data:

| Inventory Case / Method | Location in Historical Store & Inventory Catalog | Current Observation (Problem in Current Baseline) | Target Disposition & Architecture (Proposed Change) | Negative Cases | Responsible Scope & Target Test File |
| --- | --- | --- | --- | --- | --- |
| `list_experiments_bff` | Historical `ReadSurfaceStore` lines 12895–12900 (cataloged in inventory at line 3480, ACG-02-001, KEEP).<br>Historical callers: `main.py` | `read_store.py` deleted. Current caller in `experiments.py:60-62` checks `hasattr(read_store, "list_experiments_bff")` and falls back to empty list if absent. | Wire to `DefaultResearchKnowledgeSourcePort.list_research_experiments` reading directly from PostgreSQL `research.research_experiments` via `ResearchWriteOwner.list_research_experiments`. | DB connection failure returns 500/503; invalid query parameters return 422; unauthorized returns 401. | **U10A**: `test_exp002_bff_research_experiments_contract.py` |
| `get_experiment_bff` | Historical `ReadSurfaceStore` lines 12902–12906 (inventory line 3496, ACG-02-001, KEEP).<br>Historical callers: `main.py`, `research/router.py` | Current route `GET /bff/experiments/{id}` delegates to `_require_experiment` checking read store or port. | Wire to `DefaultResearchKnowledgeSourcePort.get_research_experiment` backed by `ResearchWriteOwner.get_research_experiment`. | Unknown `experiment_id` returns 404; cross-tenant access returns 404/403. | **U10A**: `test_exp002_bff_research_experiments_contract.py` |
| `create_experiment_bff` | Historical `ReadSurfaceStore` lines 12908–12926 (inventory line 3513, ACG-02-001, KEEP).<br>Historical callers: `main.py`, `research/router.py` | `experiments.py:164-196` falls back to `creator` looking for `create_research_experiment`. `DefaultResearchKnowledgeSourcePort` delegates to in-memory `_experiments` dictionary. Note that `ResearchWriteOwner` method is `create_research_experiment` (there is no `create_experiment`). | Delete in-memory `_experiments` dict. Atomic creation in Postgres `research.research_experiments` via `ResearchWriteOwner.create_research_experiment`. | Missing required fields returns 422; duplicate idempotency key returns existing record without duplicate creation; DB failure returns 500/503. | **U10A**: `test_exp002_bff_research_experiments_contract.py` |
| `_project_experiment_bff` | Historical `ReadSurfaceStore` lines 12929–12945 (inventory line 3530, ACG-02-001, KEEP).<br>Historical callers: `read_store.py` internal | Historical helper projected record with static `allowedActions: {"canCancel": true}`. | Standard projection helper normalizing `ResearchExperiment` from `ResearchWriteOwner` into BFF DTO, dynamically computing `allowedActions` based on real lifecycle state and design §6.1 obligations (`canCancel`, `invalidated`, `attached_to_review`, `archived`, `retry`). | Corrupt or missing lifecycle fields default safely; status preserves domain truth. | **U10A**: `test_exp002_bff_research_experiments_contract.py` |
| `get_experiment_logs` | Historical `ReadSurfaceStore` lines 12947–12951 (inventory line 3546, ACG-02-001, KEEP).<br>Historical callers: `main.py`, `research/router.py` | `read_store.py` deleted. Route in `experiments.py:256` checks `getattr(read_store, "get_experiment_logs")` and falls back to empty list `[]` when absent. | Route to domain owner run stage logs (`ResearchOrchestratorStore`) or worker execution logs for experiment runs. | Unknown experiment returns 404; experiment with no associated runs returns empty logs list `[]`. | **U10A**: `test_bff_evolution_experiment_jobs_events_contract.py` |
| `get_experiment_metrics` | Historical `ReadSurfaceStore` lines 12953–12957 (inventory line 3563, ACG-02-001, KEEP).<br>Historical callers: `main.py`, `research/router.py` | `read_store.py` deleted. Route in `experiments.py:278` checks `getattr(read_store, "get_experiment_metrics")` and falls back to empty dict `{}` when absent. | Route to domain owner execution receipts and metrics from `ResearchOrchestratorStore` or `ResearchWriteOwner`. | Unknown experiment returns 404; pending/running experiment returns empty metrics object `{}`. | **U10A**: `test_bff_evolution_experiment_jobs_events_contract.py` |
| `get_experiment_artifacts` | Historical `ReadSurfaceStore` lines 12959–12966 (inventory line 3580, ACG-02-001, KEEP).<br>Historical callers: `main.py`, `research/router.py` | `read_store.py` deleted. Route in `experiments.py:300` checks `getattr(read_store, "get_experiment_artifacts")` and falls back to empty list `[]` when absent. | Route to `ResearchOrchestratorStore.list_artifacts` / `get_artifact` or Postgres `research.research_notes` / artifacts table. | Unknown experiment returns 404; empty artifact set returns empty list `[]`. | **U10A**: `test_bff_evolution_experiment_jobs_events_contract.py` |
| `list_jobs_bff` | Historical `ReadSurfaceStore` lines 12972–12992 (inventory line 3597, ACG-02-005, MERGE).<br>Historical callers: `main.py` | `ReadSurfacePorts.list_jobs_bff` calls `list_research_tickets`; tickets masquerade as jobs in UI. | Merge into typed `JobReadPort` / `JobProjectionService` querying qualified domain owners. Decouple tickets from jobs. | Cross-tenant query fails closed; empty source returns empty list `[]`. | **U10A**: `test_jobs_source_owner_contract.py` |
| `get_job_bff` | Historical `ReadSurfaceStore` lines 12994–13016 (inventory line 3613, ACG-02-005, MERGE).<br>Historical callers: `main.py`, `read_store.py` internal | `ReadSurfacePorts.get_job_bff` calls `get_research_ticket`; returns ticket dictionary as job. | Merge into typed `JobReadPort.get_job_bff`, dispatching to domain owner by ID prefix (`job-worker-*`, `job-orchestrator-*`, etc.). | Unknown `job_id` returns 404; cross-tenant query returns 404/403. | **U10A**: `test_jobs_source_owner_contract.py` |
| `get_job_logs_bff` | Historical `ReadSurfaceStore` lines 13018–13022 (inventory line 3630, ACG-02-001, KEEP).<br>Historical callers: none in production | Hardcoded mock logs returned from memory when present. | Route to domain owner logs endpoint (`/api/research-worker-gateway/jobs/{id}/status`, orchestrator run logs, etc.). | Unknown job returns 404; unstarted job returns empty logs list `[]`. | **U10A**: `test_jobs_source_owner_contract.py` |
| `_experiments` in read port | `ports/research_knowledge_source.py:2170`<br>(ACG-02-003, REMOVE) | Ephemeral `self._experiments` dictionary in web memory; non-durable. | **DELETE completely**. Replace with direct readback from Postgres `research.research_experiments` via `ResearchWriteOwner`. | DB connection error raises 500/503; never falls back to in-memory dictionary. | **U10A**: `test_research_knowledge_source_ports.py` |
| `EvolutionCommandAdapter._HANDLED_COMMANDS` | `command_adapters/evolution_adapter.py:28`<br>(ACG-02-003, REMOVE) | Declares `JobAction` and `ExperimentAction`; returns fake `executed` with zero effects. | **DELETE `JobAction` and `ExperimentAction` from handled list**. `EvolutionCommandAdapter.can_handle` returns `False`. | Unsupported command rejects execution; fails closed without synthetic receipt. | **U10A**: `test_bff_evolution_experiment_jobs_events_contract.py` |
| `_DEFAULT_ADAPTERS` registry order | `command_adapters/registry.py:29-54`<br>(ACG-02-005, MERGE) | `EvolutionCommandAdapter` evaluated first; swallows job/experiment actions into fake body. | Register dedicated `ExperimentCommandAdapter` and `JobCommandAdapter` ahead of `EvolutionCommandAdapter`. | Unknown action type returns 404/400 unhandled adapter error. | **U10A**: `test_jobs_source_owner_contract.py` |
| `GET /bff/sse/jobs/{jobId}/progress` | `events/router.py:511`<br>(ACG-02-005, MERGE) | Client-side filtering only; events flood all connected clients without jobId match. | Implement server-side topic/channel filtering by `jobId`. | Client subscribed to `job-A` never receives events for `job-B`. | **U10A**: `test_bff_evolution_experiment_jobs_events_contract.py` |

---

### 5.3 Regression test suites business acceptance and baseline gap honesty

All four regression test suites are preserved without skipping or weakening assertions.
Independent foreground execution under timeout 60 reveals the following terminal outcomes:

| Test File | Test Case Count | Business Cases & Invariants | Execution Result | Notes & Baseline Gap Record |
| --- | --- | --- | --- | --- |
| `test_bff_evolution_experiment_jobs_events_contract.py` | 29 tests | Evolution programs (8), Experiments CRUD & actions (8), Jobs CRUD & actions (6), Events & SSE (4). | **29 passed** in isolated execution. | Contract suite passes completely against current baseline fixtures. |
| `test_bff_b2_002_evolution_jobs_ops.py` | 31 tests | `GET /bff/jobs` list envelope, `GET /bff/jobs/{id}` detail + 404, HTTP 401 unauthenticated. | **31 passed** in full suite run. | Preserves 200 envelope shape, items, page_info, and 401/404 handling. |
| `test_exp002_bff_research_experiments_contract.py` | 17 tests | Experiment list/detail envelope, status fields, analysis links, 404, 401. | **17 passed** in full suite run. | Verifies experiment contract shapes and error handling. |
| `test_assistant_context_pack.py` | 5 tests | Assistant context collectors extracting active jobs, research experiments, tickets. | **4 passed, 1 failed** (`test_assistant_context_pack_builds_structured_snapshot`). | **Unchanged baseline gap**: Fails with NameError _REPO_ROOT at bff/main.py:21745 (`NameError: name '_REPO_ROOT' is not defined` in `_assistant_repo_root()`; isolated rerun fails in ~9s). |

**Honest Baseline Gap Record**:
When all four full regression files are executed concurrently under timeout 60:
`81 passed, 1 failed, 0 skipped, exit 1 in ~41s`.
The single failure is `test_assistant_context_pack_builds_structured_snapshot`, caused by an un-scoped variable reference `_REPO_ROOT` inside `_assistant_repo_root()` at `services/control-plane/bff/main.py:21745`. This failure is an **unchanged pre-existing baseline defect** in the assistant context pack and does not stem from this contract decision. In accordance with governance instructions, this gap is recorded honestly; D-JOBS does not broaden into runtime repair.

---

### 5.4 Negative verification, concurrency, and failure modes per source

Implementation tasks (U10A and U10B) and follow-up domain tasks must prove the following negative cases bound to specific per-source backends:

1. **Gateway Concurrency & Durability Failure (`research-worker-gateway`)**:
   - Write conflict or concurrent dispatch to `worker_jobs.json` must be fenced; simulating missing data directory or unwritable JSON file raises 500 error and does not return synthetic success.
   - Tested in: `services/research-worker-gateway/test_store.py`.
2. **Orchestrator Concurrency & Late Completion Race (`services/research`, U10B)**:
   - When run cancellation succeeds via `POST /api/research-orchestrator/runs/{run_id}/cancel`, orchestrator persists cancellation fence timestamp `t_cancel` in `research_runs.json`.
   - A simulated worker completion arriving at `t_completion > t_cancel` is rejected by the owner with 409 Conflict, marked as orphaned/discarded, and does NOT overwrite the `canceled` status.
   - Tested in: `services/control-plane/bff/tests/test_research_jobs_action_receipts.py`.
3. **Training Session Advisory Lock & Durability Failure (`training-session`)**:
   - In Postgres mode (`PostgresTrainingSessionStore`), concurrent preview job mutations acquire transaction-scoped advisory locks on `training_session.authority_records`. A concurrent conflicting update is serialized; connection loss rolls back the transaction.
   - Tested in: `services/training-session/test_store.py`.
4. **Source Ingestion Checkpoint Resume Failure (`source_ingestion`)**:
   - Extraction thread interruption before batch watermark commit ensures uncommitted frontier items remain in queue; connector restart resumes strictly from last verified checkpoint.
   - Tested in: `services/source_ingestion/tests/test_pipeline.py`.
5. **Policy Learning Reject Race (`policy_learning`)**:
   - Calling reject on an already rejected or terminal job returns 409 Conflict; direct promotion without Governance approval returns 409 Conflict.
   - Tested in: `services/policy-learning/tests/test_main.py`.
6. **Permission & Tenant Isolation**:
   - Request without valid `Authorization` header returns HTTP 401 Unauthorized.
   - Request with tenant header attempting to access another tenant's job returns HTTP 403 Forbidden or 404 Not Found.
7. **Action Unavailability Honesty**:
   - Invoking `POST /bff/jobs/{job_id}/actions/cancel` against a source that lacks backend cancel implementation (e.g. `job-trainer-*` or `job-ingest-*`) returns HTTP 400 or 503 `ActionUnavailableError` with `action_id`, `job_id`, and `reason="owner_operation_unsupported"`.
   - It **never returns fake HTTP 202/200 with status="executed"**.

---

## 6. Rollout and delivery boundaries

- D-JOBS delivery consists solely of this contract decision document and its accompanying evidence manifest.
- No runtime source code is modified in this task.
- Delivery follows the standard per-task PR workflow into `dev`, with exact-head independent review by Codex.
- Merge identity will be established by GitHub and the supervisor integration runner upon passing checks.
