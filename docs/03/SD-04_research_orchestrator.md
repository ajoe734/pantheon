# SD-04 — Research Orchestrator / 研究工廠與 Experiment Backend 設計

版本：v0.1 Codex-ready draft
適用範圍：Research & Learning Plane、Experiment Orchestrator、Rapid Eval Service、Qlib / vectorbt / statsmodels / QuantLib / FinRL backend adapters
前置依賴：SD-00 Architecture Invariants、SD-01 Domain Model & Registry Backbone、SD-03 Source / Knowledge / Evidence Plane

---

## 1. Purpose

本文件定義 Pantheon 的 Research Orchestrator。它的任務不是「提供一個回測工具」，而是將 `StrategySpec` 轉成可重放、可審查、可產生 artifact 的研究任務。

Research Orchestrator 必須支援多 backend，但不能讓 backend 各自擁有資料、artifact 或 deployment truth。所有研究任務必須 pin 到 registry 與 dataset lineage：

```text
StrategySpec
→ backend selection policy
→ ExperimentTask
→ ExperimentRun
→ MetricBundle / ArtifactCandidate
→ Registry / Lineage
→ Promotion Plane
```

研究結果只能產生 `CandidateArtifact` 或 `AllocationPolicyArtifact`，不得直接部署到 execution runtime。
Agora / Trainer / trader feedback 形成的 persona lessons、correction traces、preference
pairs、trader trajectories 與 shadow imitation candidates 也屬於 research / learning plane。
它們可以進 rapid eval、OOS、paper-shadow 或 experiment，但不能直接改 running artifact
或 live LEAN runtime。

---

## 2. Repo ownership

| Repo | Ownership |
|---|---|
| `pantheon` | Primary owner：orchestrator、backend registry、experiment task/run registry、metric normalizer、artifact packager。 |
| `front-ai-trading-system` | UI consumer：Research Workbench、Experiment Console、Run Detail、Rapid Eval Preview。 |
| `pantheon-lean` | 不屬於 research backend；只在後續 SD-08 consume approved deployment plan。 |
| external research libraries | Qlib、vectorbt、statsmodels、QuantLib、FinRL 作為 adapters，不擁有 registry truth。 |

---

## 3. Module paths

### `pantheon`

```text
services/research-orchestrator/
  __init__.py
  models.py
  commands.py
  queries.py
  events.py
  backend_registry.py
  backend_selection.py
  orchestrator.py
  task_queue.py
  metric_normalizer.py
  artifact_packager.py
  rapid_eval.py
  replay.py
  policies.py
  repository.py
  api.py
  tests/

services/research-orchestrator/backends/
  base.py
  qlib_adapter.py
  vectorbt_adapter.py
  statsmodels_adapter.py
  quantlib_adapter.py
  finrl_adapter.py
  mock_adapter.py

services/research-orchestrator/schemas/
  research_backend.schema.json
  experiment_task.schema.json
  experiment_run.schema.json
  metric_bundle.schema.json
  rapid_eval_request.schema.json

docs/sd/04_research_orchestrator.md
docs/contracts/experiment_task.schema.json
docs/contracts/experiment_run.schema.json
docs/contracts/metric_bundle.schema.json
docs/codex/SD-04_task_packets.md
```

### `front-ai-trading-system`

```text
src/pages/research/ExperimentQueue.tsx
src/pages/research/ExperimentRunDetail.tsx
src/pages/research/RapidEvalPanel.tsx
src/types/experiment.ts
src/lib/experimentClient.ts
```

---

## 4. Domain model

### 4.1 `ResearchBackend`

```yaml
ResearchBackend:
  backend_id: string
  backend_type: enum[qlib, vectorbt, statsmodels, quantlib, finrl, custom]
  display_name: string
  status: enum[enabled, disabled, degraded]
  supported_asset_classes: string[]
  supported_frequencies: string[]
  supported_task_types: enum[backtest, factor_eval, regime_model, pricing, rl_training, rapid_eval][]
  required_input_contracts: string[]
  output_contract_version: string
  compute_profile_ref: string | null
  adapter_module: string
```

### 4.2 `BackendSelectionPolicy`

```yaml
BackendSelectionPolicy:
  policy_id: string
  version: string
  rules:
    - match:
        strategy_family: string | null
        asset_class: string | null
        task_type: string | null
        frequency: string | null
      preferred_backend: string
      fallback_backends: string[]
      required_capabilities: string[]
  default_backend: string
  allow_manual_override: boolean
  allowed_override_roles: string[]
```

### 4.3 `ExperimentTask`

```yaml
ExperimentTask:
  task_id: string
  strategy_id: string
  strategy_spec_version: string
  requested_by: actor_ref
  task_type: enum[backtest, factor_eval, regime_model, pricing, rl_training, rapid_eval]
  backend_id: string | null
  backend_selection_policy_id: string
  dataset_version_id: string
  code_version: string
  feature_spec_version: string | null
  label_spec_version: string | null
  cost_assumption_ref: string | null
  risk_assumption_ref: string | null
  priority: enum[low, normal, high]
  status: enum[queued, selecting_backend, ready, running, completed, failed, cancelled]
  idempotency_key: string
  trace_id: string
  created_at: datetime
```

### 4.4 `ExperimentRun`

```yaml
ExperimentRun:
  run_id: string
  task_id: string
  backend_id: string
  runtime_env: enum[dev, sandbox, research]
  status: enum[pending, running, completed, failed, cancelled]
  started_at: datetime | null
  finished_at: datetime | null
  input_manifest_ref: string
  output_manifest_ref: string | null
  metric_bundle_id: string | null
  artifact_refs: string[]
  logs_ref: string | null
  failure_reason: string | null
  trace_id: string
```

### 4.5 `MetricBundle`

```yaml
MetricBundle:
  metric_bundle_id: string
  run_id: string
  strategy_id: string
  schema_version: string
  primary_metrics:
    sharpe: number | null
    sortino: number | null
    max_drawdown: number | null
    annualized_return: number | null
    turnover: number | null
    hit_rate: number | null
    pnl_volatility: number | null
  risk_metrics:
    gross_exposure: number | null
    net_exposure: number | null
    factor_exposures: object | null
    liquidity_score: number | null
    tail_risk_score: number | null
  backend_specific_metrics: object
  warnings: string[]
  evidence_refs: string[]
```

### 4.6 `RapidEvalRequest`

```yaml
RapidEvalRequest:
  rapid_eval_id: string
  persona_id: string | null
  strategy_id: string | null
  patch_ref: string | null
  eval_scope: enum[persona_patch, strategy_patch, feature_patch, risk_patch]
  dataset_version_id: string
  max_runtime_seconds: integer
  requested_by: actor_ref
  status: enum[queued, running, completed, failed]
  result_ref: string | null
```

---

## 5. Commands

| Command | Input | Output | Notes |
|---|---|---|---|
| `RegisterResearchBackend` | `ResearchBackend` | backend_id | Admin-only。 |
| `CreateExperimentTask` | StrategySpec ref + task config | `ExperimentTask` | Must pin `dataset_version_id`。 |
| `SelectResearchBackend` | task_id | backend_id | Policy-driven。 |
| `StartExperimentRun` | task_id | run_id | Queue worker command。 |
| `RecordExperimentResult` | run_id + output manifest | `MetricBundle` + artifact refs | Only adapter worker may call。 |
| `CancelExperimentTask` | task_id + reason | task status | Must emit audit event。 |
| `CreateRapidEvalRequest` | request payload | rapid_eval_id | Used by trainer / preview path。 |
| `PackageCandidateArtifact` | run_id + artifact manifest | artifact_id | Does not approve or deploy。 |
| `ReplayExperimentRun` | run_id | new_run_id | Reuses pinned dataset/code versions unless override approved。 |

---

## 6. Queries

| Query | Output |
|---|---|
| `GetResearchBackends` | backend catalog + status |
| `GetExperimentTask` | task detail |
| `ListExperimentTasks` | queue view |
| `GetExperimentRun` | run detail + metrics + logs |
| `ListExperimentRunsByStrategy` | strategy research history |
| `GetMetricBundle` | normalized metrics |
| `GetRapidEvalResult` | preview result |
| `GetReplayDiff` | comparison between original and replay runs |

---

## 7. Events

All events must use the common event envelope from SD-00.

```yaml
ExperimentTaskCreated:
  task_id: string
  strategy_id: string
  dataset_version_id: string
  trace_id: string

ResearchBackendSelected:
  task_id: string
  backend_id: string
  policy_id: string
  reason: string

ExperimentRunStarted:
  run_id: string
  task_id: string
  backend_id: string

ExperimentRunCompleted:
  run_id: string
  task_id: string
  metric_bundle_id: string
  artifact_refs: string[]

ExperimentRunFailed:
  run_id: string
  task_id: string
  failure_reason: string

CandidateArtifactPackaged:
  artifact_id: string
  run_id: string
  strategy_id: string

RapidEvalCompleted:
  rapid_eval_id: string
  result_ref: string
```

---

## 8. State machines

### 8.1 ExperimentTask state

```text
queued
→ selecting_backend
→ ready
→ running
→ completed
```

Allowed terminal alternatives:

```text
queued / selecting_backend / ready / running → cancelled
running → failed
failed → queued  # only via RetryExperimentTask command
```

### 8.2 ExperimentRun state

```text
pending → running → completed
pending / running → cancelled
running → failed
```

### 8.3 Candidate packaging state

```text
not_packaged → packaging → packaged
packaging → packaging_failed
```

Candidate packaging does **not** imply approval.

---

## 9. Hard invariants

1. Every `ExperimentTask` must reference exactly one `StrategySpec` version.
2. Every `ExperimentTask` must pin `dataset_version_id` before execution.
3. Every `ExperimentRun` must have `code_version` and `input_manifest_ref`.
4. A research backend cannot write directly to promotion approval or execution runtime.
5. A backend adapter cannot fetch arbitrary vendor data outside the approved dataset lineage path.
6. `CandidateArtifact` cannot be created from a failed run.
7. `MetricBundle` must be normalized to Pantheon metric schema before registry storage.
8. Rapid eval output is advisory and cannot be promoted without a normal `ExperimentRun` unless policy explicitly allows a bounded preview artifact type.
9. Experiment replay must preserve original dataset/code references unless an approved override is recorded.
10. All task/run/result changes must emit audit or domain events with `trace_id`.

---

## 10. Policy hooks

| Policy | Purpose |
|---|---|
| `backend_selection_policy` | Selects Qlib / vectorbt / statsmodels / QuantLib / FinRL based on StrategySpec and task type。 |
| `compute_budget_policy` | Limits runtime, memory, GPU, parallelism。 |
| `replay_required_policy` | Requires replay before candidate packaging for specific strategy classes。 |
| `metric_threshold_policy` | Tags results as eligible / ineligible for candidate packaging。 |
| `rapid_eval_policy` | Controls who can run previews and which fields can be patched。 |
| `artifact_packaging_policy` | Determines artifact type and required manifests。 |
| `dataset_access_policy` | Ensures persona/workspace/source entitlement is respected。 |

Policies must be data/config-driven, not hardcoded into backend adapters.

---

## 11. Storage model

Recommended tables / collections:

```text
research_backends
backend_selection_policies
experiment_tasks
experiment_runs
experiment_run_manifests
metric_bundles
rapid_eval_requests
rapid_eval_results
candidate_artifact_links
research_events
research_audit_actions
```

Object storage:

```text
s3://pantheon-research/input-manifests/{task_id}.json
s3://pantheon-research/output-manifests/{run_id}.json
s3://pantheon-research/logs/{run_id}/
s3://pantheon-research/artifacts/{artifact_id}/
```

---

## 12. API endpoints

```text
GET    /api/research/backends
POST   /api/research/backends
GET    /api/research/backends/{backend_id}
POST   /api/experiments/tasks
GET    /api/experiments/tasks
GET    /api/experiments/tasks/{task_id}
POST   /api/experiments/tasks/{task_id}/select-backend
POST   /api/experiments/tasks/{task_id}/cancel
POST   /api/experiments/runs
GET    /api/experiments/runs/{run_id}
POST   /api/experiments/runs/{run_id}/record-result
POST   /api/experiments/runs/{run_id}/replay
GET    /api/experiments/runs/{run_id}/metrics
POST   /api/rapid-eval
GET    /api/rapid-eval/{rapid_eval_id}
POST   /api/artifacts/package-candidate
```

---

## 13. Integration points

| Integration | Direction | Contract |
|---|---|---|
| SD-01 Registry | read/write | StrategySpec, ExperimentRun, CandidateArtifact, Lineage。 |
| SD-03 Source/Evidence | read | DatasetVersion, evidence bundle, source refs。 |
| SD-05 Consultation | emit/read | Run summaries may feed consult request context。 |
| SD-07 Promotion | emit | CandidateArtifactPackaged triggers review eligibility。 |
| `front-ai-trading-system` | read/command | Queue, run detail, rapid eval UI。 |
| Qlib / vectorbt / statsmodels / QuantLib / FinRL | adapter | Must implement `ResearchBackendAdapter` interface。 |

---

## 14. Tests

### Unit tests

- backend selection chooses correct backend by policy.
- backend selection falls back when preferred backend disabled.
- `CreateExperimentTask` rejects missing dataset version.
- `RecordExperimentResult` rejects missing input/output manifest.
- metric normalizer maps backend metrics to Pantheon schema.
- candidate packaging rejects failed run.

### Contract tests

- each adapter implements `run(task: ExperimentTask) -> ExperimentRunResult`.
- output manifest must include artifact hashes.
- every event uses common event envelope.

### Integration tests

- StrategySpec → ExperimentTask → mock backend run → MetricBundle → CandidateArtifact.
- RapidEvalRequest returns preview within bounded time.
- replay run produces comparable metric bundle.

---

## 15. Definition of Done

1. `services/research-orchestrator` exists with models, commands, policies, repository, API, and tests.
2. At least one real backend adapter and one mock adapter are implemented.
3. Experiment task/run lifecycle is persisted and event-emitting.
4. Candidate artifact packaging produces registry-compatible output, not approval.
5. Dataset version, code version, and input manifest are mandatory.
6. Frontend can list experiment queue and inspect a run.
7. All tests pass for backend selection, run lifecycle, and packaging invariants.

---

## 16. Codex task packets

### PTH-SD04-001 — Implement research orchestrator domain models

```text
Repo: ajoe734/pantheon
Target paths:
  services/research-orchestrator/models.py
  services/research-orchestrator/events.py
  services/research-orchestrator/schemas/*.json
Goal:
  Implement ResearchBackend, ExperimentTask, ExperimentRun, MetricBundle, RapidEvalRequest.
Acceptance tests:
  - model validation rejects missing dataset_version_id
  - ExperimentRun cannot be completed without output_manifest_ref
  - every model supports trace_id
Non-goals:
  - do not implement Qlib/vectorbt logic yet
```

### PTH-SD04-002 — Implement backend selection service

```text
Repo: ajoe734/pantheon
Target paths:
  services/research-orchestrator/backend_registry.py
  services/research-orchestrator/backend_selection.py
  services/research-orchestrator/policies.py
  services/research-orchestrator/tests/test_backend_selection.py
Goal:
  Select backend using BackendSelectionPolicy.
Acceptance tests:
  - preferred backend selected when enabled
  - fallback selected when preferred disabled
  - manual override requires allowed role
```

### PTH-SD04-003 — Implement experiment lifecycle API

```text
Repo: ajoe734/pantheon
Target paths:
  services/research-orchestrator/api.py
  services/research-orchestrator/orchestrator.py
  services/research-orchestrator/repository.py
  services/research-orchestrator/tests/test_experiment_lifecycle.py
Goal:
  Create tasks, start runs, record results, and emit events.
Acceptance tests:
  - StrategySpec ref + dataset version creates queued task
  - run start transitions task to running
  - result recording stores MetricBundle and emits ExperimentRunCompleted
```

### PTH-SD04-004 — Implement candidate artifact packager

```text
Repo: ajoe734/pantheon
Target paths:
  services/research-orchestrator/artifact_packager.py
  services/research-orchestrator/tests/test_artifact_packager.py
Goal:
  Package completed experiment outputs into CandidateArtifact-compatible manifest.
Acceptance tests:
  - rejects failed run
  - writes artifact manifest with hashes and lineage refs
  - emits CandidateArtifactPackaged
Non-goals:
  - do not approve or deploy artifacts
```
