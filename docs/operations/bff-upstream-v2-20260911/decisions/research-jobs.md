# Research and Jobs source, identity, and action contract

Task: `BFF-RESEARCH-JOBS-CONTRACT-DECISION-001` (D-JOBS). Owner: Antigravity.
Independent reviewer: Codex. Date: 2026-09-13.
Status: design selected; independent review pending. This document implements
V2 §02.9's design prerequisite, not U10A/U10B source delivery or hosted acceptance.

## 1. Decision and evidence boundary

Fixed decision: **Establish Management Jobs as a typed read composition and
single `JobAction` dispatch seam across five qualified asynchronous backend
sources plus one read-only external diagnostic source, while maintaining strict
domain aggregate separation**. Do not build a universal "JobStore", a second
distributed scheduler, a shared worker pool, or an in-memory execution overlay.
Domain owners retain exclusive write authority, state persistence, and worker
lifecycle management for their respective job types.

Management UI and BFF observe and interact with jobs via standard contracts:
- Read composition: `GET /bff/jobs`, `GET /bff/jobs/{job_id}`, `GET /bff/jobs/{job_id}/logs`, and `GET /bff/sse/jobs/{jobId}/progress`.
- Action dispatch: `POST /bff/jobs/{job_id}/actions/{action_id}` routed via `CommandAdapterService` and `JobCommandAdapter` to the qualified domain owner.
- Aggregate boundaries are strictly preserved: **`ResearchTicket != Experiment != OrchestratorRun != Job`**. They represent fundamentally distinct business entities, lifecycles, and storage models.

This decision governs the contract and boundary prerequisites for U10A (read binding
and minimal write wiring) and U10B (retained action execution closure). It does
not authorize cross-host HA, live trading promotions, production capital grants,
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
| `services/research-worker-gateway/main.py:406–565`; `store.py:100–186` | Real worker gateway provides `POST/GET /api/research-worker-gateway/jobs` and `POST .../cancel` with file/postgres event persistence. It is a genuine job execution backend, not the universal owner of all jobs. |
| `services/research/main.py:745–1180`; `store.py:99–235` | `ResearchOrchestratorStore` manages `ExperimentTask` and `ExperimentRun` with artifact tracking, separate from `ResearchWriteOwner`'s ticket and experiment records. |
| `services/training-session/main.py`; `store.py`; `preview_eval_worker.py` | Training session service manages teaching sessions and preview evaluation runs. Read-only preview generation and evaluation jobs belong to this domain owner. |
| `services/source_ingestion/main.py`; `source_management_store.py` | Ingestion pipeline, active universe connectors, and strategy seed distillation runs are managed by ingestion workers and stores. |
| `services/policy-learning/main.py:405–560, 1368`; `store.py` | Policy-learning manages imitation and shadow evaluation jobs. Promotion endpoint explicitly rejects with 409 because promotion requires Governance mutation review. |
| `services/openclaw-gateway-adapter/main.py`; `AGENTS.md` | External OSS prompt and diagnostic adapter. Per repository architecture rules, OpenClaw is strictly read-only for product BFF (`kernel_debug`); no shell access, repo writes, or supervisor task scheduling. |

---

## 2. Investigation and qualification of the six candidate job/run sources

The system discovery identified six distinct job and run mechanisms across the platform.
The table below qualifies each source, establishing its canonical domain owner,
storage mechanism, aggregate identity, tenant scoping, supported actions, and
admission decision for the Management control plane.

| Candidate Source | Underlying Domain Owner & Store | Target Aggregate & ID Scheme | Auth & Tenant Scoping | Supported Actions & Semantics | Management Admission Status |
| --- | --- | --- | --- | --- | --- |
| **1. Research worker jobs** | `services/research-worker-gateway/` (`ResearchWorkerGatewayStore` + `PostgresWorkerEventStore`) | `Job` (`job-worker-<uuid>`) | Tenant context passed via job payload spec; service-level token auth | **Cancel**: transitions job to `canceled`, appends event, halts worker.<br>**Retry**: new job submission linked to parent job ID.<br>**Archive/Promote**: N/A (worker level). | **Admitted (Qualified)**: primary worker execution backend for compute jobs. |
| **2. Research orchestrator runs** | `services/research/` (`ResearchOrchestratorStore`, storing `ExperimentTask` & `ExperimentRun`) | `OrchestratorRun` (`run-<id>`) under `ExperimentTask` (`task-<id>`) | Tenant scoped in admission contracts; task headers carry tenant ID | **Cancel**: terminates active run, fences late worker completions.<br>**Retry**: new run attempt under existing task lineage.<br>**Archive**: run artifact retention (logical visibility).<br>**Promote**: candidate handoff to Replication Bridge (requires artifact proof). | **Admitted (Qualified)**: backtest, simulation, and multi-stage research runs. |
| **3. Trainer preview jobs** | `services/training-session/` (`TrainingSessionStore`, `preview_eval_worker.py`) | `TrainerPreviewJob` (`eval-preview-<id>`) | Persona-scoped and tenant-scoped via session headers | **Cancel**: stops preview worker evaluation.<br>**Retry**: re-triggers evaluation against dataset.<br>**Archive**: tied to teaching session retention.<br>**Promote**: N/A (candidate promotion routed through Governance). | **Admitted (Qualified)**: interactive and automated model evaluation previews. |
| **4. Source-ingest runs/jobs** | `services/source_ingestion/` (`source_management_store.py`, `pipeline.py`, `distillation_worker.py`) | `SourceIngestRun` (`ingest-run-<id>`) | Tenant scoped via data source registry and connector configs | **Cancel**: gracefully stops connector extraction thread/worker.<br>**Retry**: resumes from last verified checkpoint / watermark.<br>**Archive**: deactivates source connector; preserves lineage.<br>**Promote**: seed materializer promotion to Strategy Seed Store. | **Admitted (Qualified)**: data ingestion, universe synchronization, and distillation. |
| **5. Policy-learning jobs** | `services/policy-learning/` (`PolicyLearningStore`, `scheduler_worker.py`) | `PolicyLearningJob` (`pl-job-<id>`) | Tenant scoped in candidate claims and DLQ records | **Cancel/Reject**: marks job rejected, stops processing.<br>**Retry**: DLQ replay or worker retry with attempt count increment.<br>**Archive**: retention policy on candidate history.<br>**Promote**: **Requires Governance Gate**; direct promotion returns 409. | **Admitted (Qualified)**: imitation learning, shadow evaluation, and model training. |
| **6. OpenClaw workflow jobs** | `services/openclaw-gateway-adapter/` (`assistant_openclaw_provider.py`) | `OpenClawWorkflow` (external session/run ID) | Upstream provider session context; read-only token | **Read-only**: diagnostic observation and token status streaming.<br>**Cancel/Retry/Archive/Promote**: **Rejected / Not Supported** via product BFF. | **Admitted as Read-Only Diagnostic**: no write actions; no supervisor or task creation authority. |

### Strict distinction of domain aggregates

The system must not conflate these four distinct aggregates:
1. **`ResearchTicket`**: Business research request or bug report owned by `ResearchWriteOwner` (`research.research_tickets`). Identifiers: `ticket-xxx`. States: `open`, `in_progress`, `closed`, `archived`. Actions: `canEdit`, `canClose`, `canArchive`. Tickets are tracked metadata, **not asynchronous execution jobs**.
2. **`Experiment` (`ResearchExperiment`)**: Parametric research experiment specification owned by `ResearchWriteOwner` (`research.research_experiments`). Identifiers: `exp-YYYYMMDD-xxx`. States: `queued`, `running`, `completed`, `failed`, `canceled`. Actions: `canCancel`.
3. **`OrchestratorRun`**: Specific execution attempt of an experiment task, owned by `ResearchOrchestratorStore`. Identifiers: `run-xxx` under `task-xxx`. Contains execution artifacts, hardware telemetry, and stage logs.
4. **`Job`**: Unified Management operational projection of asynchronous units of work across qualified domain owners (worker jobs, ingestion runs, policy learning jobs, trainer previews). Identifiers: `job-<source>-<id>`. States: standard normalized job lifecycle (`pending`, `running`, `completed`, `failed`, `canceled`).

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

The delivery of the Research and Jobs domain is partitioned into two ordered tasks:

```
+-------------------------------------------------------------------------------+
| D-JOBS (This Task): Contract & Boundary Decision                               |
|   - Fixed source/type/tenant/ID qualification matrix                          |
|   - Distinct aggregates: ResearchTicket != Experiment != OrchestratorRun != Job|
|   - Per-action semantics: Cancel, Retry, Archive, Promote                      |
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
|   - Real backend execution for Cancel (worker stopped verification + fence)   |
|   - Real backend execution for Retry (attempt lineage + eligible state check)  |
|   - Real backend execution for Archive (visibility projection, no physical del)|
|   - Real backend execution for Promote (Governance authorization check)       |
|   - Paired with FE-RESEARCH-JOBS-ACTIONS-CLOSURE-001 in execute-plans         |
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

### Operator decision items

The following boundaries must be preserved without unilateral agent expansion:
- **Promotion to Live/Capital**: No autonomous promotion to paper canary or live trading without explicit Operator/Governance approval.
- **Universal Scheduler / Universal Store**: Any proposal to replace domain-owned stores with a single unified job database requires explicit operator architecture sign-off and is rejected in this baseline.
- **OpenClaw Writable Operations**: OpenClaw product routes remain strictly read-only diagnostics; any grant of shell, file-modification, or task-materialization authority requires explicit operator policy authorization.

---

## 5. Required implementation acceptance and negative verification

Implementation tasks (U10A and U10B) must satisfy the following acceptance matrix.
These criteria govern subsequent code delivery and are **not** claims of completion
for this design task.

| Risk / Feature Boundary | Required Proof in U10A / U10B |
| --- | --- |
| **Domain Aggregate Separation** | Querying `/bff/jobs` returns operational jobs, never raw `ResearchTicket` records. `ResearchTicket` CRUD remains on ticket endpoints with ticket-specific actions (`canClose`, `canArchive`). `Experiment` records link to ticket IDs without sharing database tables. |
| **Real Storage Readback** | Creating an experiment via the consolidated endpoint writes to Postgres `research.research_experiments` via `ResearchWriteOwner`. Restarting the web process preserves the record. In-memory `_experiments` dictionary is completely removed. |
| **Registry Single-Match** | `find_adapter("JobAction", "job", "cancel")` resolves exclusively to `JobCommandAdapter`. `find_adapter("ExperimentAction", "experiment", "cancel")` resolves exclusively to `ExperimentCommandAdapter`. `EvolutionCommandAdapter` rejects both. No first-match race conditions. |
| **Action Unavailability Honesty** | In U10A, invoking an action not yet implemented in U10B returns `ActionUnavailableError` (HTTP 400/503) with clear remediation guidance. It **never returns fake HTTP 202/200 with status="executed"**. |
| **True Cancellation Semantics (U10B)** | Cancellation verifies worker stoppage. A simulated worker finishing after cancellation receipt has its completion rejected by the owner due to the cancellation fence timestamp. |
| **Retry Lineage & Idempotency (U10B)** | Retrying an eligible failed job produces attempt 2 with identical `parent_job_id`. Concurrent retries with the same idempotency key return the same new attempt. Active jobs reject retry with 409. |
| **SSE Job ID Filtering** | Connecting to `/bff/sse/jobs/{jobId}/progress` streams only events matching the requested `jobId`. Events for other jobs are filtered server-side. |
| **Regression Suites** | Retain 100% of the original business assertions in `test_bff_evolution_experiment_jobs_events_contract.py`, `test_bff_b2_002_evolution_jobs_ops.py`, `test_exp002_bff_research_experiments_contract.py`, and `test_assistant_context_pack.py`. |

### Verification rules
- AST function counts are not passed tests. Verification commands must report collected, passed, failed, and skipped counts.
- No tests may be skipped, silenced, or stubbed with synthetic mock fixtures to bypass real owner requirements.
- Long-running commands must be bounded and executed in terminal batches.

---

## 6. Rollout and delivery boundaries

- D-JOBS delivery consists solely of this contract decision document and its accompanying evidence manifest.
- No runtime source code is modified in this task.
- Delivery follows the standard per-task PR workflow into `dev`, with exact-head independent review by Codex.
- Merge identity will be established by GitHub and the supervisor integration runner upon passing checks.
