# RW-04 Experiment Launch BFF Contract

## Status

**Routes live** — all four experiment launch routes (`POST /api/v1/experiments/launch`, `GET /api/v1/experiments`, `GET /api/v1/experiments/{experiment_id}`, `POST /api/v1/experiments/{experiment_id}/cancel`) are confirmed live as of 2026-04-20T12:45:00Z and returning the published field shape. Frontend handoff bundle published at `docs/pantheon-handoffs/RW-04-experiment-launch/`. UI implementation may proceed.

Task: `RW-04-EXPERIMENT-001`

## Purpose

Provide one canonical experiment surface for the Research Workbench so researchers can launch experiments, monitor async progress, inspect durable run history, and cancel only when the backend explicitly authorizes that action.

## Routes

### Launch experiment

- `POST /api/v1/experiments/launch`

Required request body:

- `ticket_id` — canonical research ticket anchor from RW-01
- `experiment_name` — operator-visible run label
- `strategy_selector`
  - `strategy_id`
  - `variant_id` — nullable when launching the default strategy variant
- `parameter_set` — backend-validated map of launch parameters; keys and allowed ranges remain backend-owned
- `run_config`
  - `dataset_ref`
  - `time_range.start_at`
  - `time_range.end_at`
  - `execution_mode` — `"paper"` | `"backtest"` | `"simulation"`
  - `priority` — `"normal"` | `"high"`
  - `requested_by`
- `launch_context.analysis_refs[]` — nullable array of RW-03 analysis ids used as launch context

Required response fields:

- `experiment_id`
- `ticket_id`
- `status` — must return `"queued"` for a newly accepted launch
- `queued_at`
- `allowedActions.canCancel`
- `links.self`
- `links.workbench_detail`

### List experiment runs

- `GET /api/v1/experiments`

Supported query params:

- `ticket_id`
- `status` — `"queued"` | `"running"` | `"completed"` | `"failed"` | `"canceled"`
- `page_token`
- `page_size` — default `20`, maximum `100`

Required response fields:

- `data[]`
  - `experiment_id`
  - `ticket_id`
  - `experiment_name`
  - `status`
  - `queued_at`
  - `started_at` — nullable
  - `completed_at` — nullable
  - `artifact_ids[]`
  - `allowedActions.canCancel`
  - `links.self`
  - `links.workbench_detail`
- `page_info.next_page_token`
- `page_info.total`
- `meta.snapshot_at`
- `meta.surfaces.experiment_history` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`

### Get experiment detail

- `GET /api/v1/experiments/{experiment_id}`

Required response fields:

- `experiment_id`
- `ticket_id`
- `experiment_name`
- `status`
- `queued_at`
- `started_at` — nullable
- `completed_at` — nullable
- `progress`
  - `percent` — nullable integer from `0` to `100`
  - `phase` — nullable backend-authored phase label
  - `message` — nullable backend-authored progress copy
- `strategy_selector`
  - `strategy_id`
  - `variant_id` — nullable
- `parameter_set`
- `run_config`
  - `dataset_ref`
  - `time_range.start_at`
  - `time_range.end_at`
  - `execution_mode`
  - `priority`
  - `requested_by`
- `launch_context.analysis_refs[]`
- `validation_warnings[]`
  - `code`
  - `message`
- `artifact_ids[]`
- `failure`
  - `reason_code` — nullable
  - `message` — nullable
- `allowedActions.canCancel`
- `links.self`
- `links.workbench_detail`
- `links.linked_ticket_detail`
- `meta.snapshot_at`
- `meta.surfaces.experiment_status` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`

### Cancel experiment

- `POST /api/v1/experiments/{experiment_id}/cancel`

Required request body:

- `reason` — operator-authored cancellation note

Required response fields:

- `experiment_id`
- `status` — must return `"canceled"` after the cancel command is durably accepted
- `completed_at`
- `allowedActions.canCancel` — must be `false`

## ExperimentRun Objects

Canonical lifecycle:

- `queued` — launch accepted and waiting for execution
- `running` — experiment is actively executing
- `completed` — experiment finished successfully and any produced `artifact_ids[]` are durable
- `failed` — experiment terminated unsuccessfully; `failure.*` may be populated
- `canceled` — run was terminated by explicit backend-authorized cancel action

Canonical transition graph:

- `queued -> running`
- `queued -> canceled` — a queued run may be canceled before execution begins
- `running -> completed`
- `running -> failed`
- `running -> canceled`

Illegal transitions the backend must reject:

- `queued` must not transition directly to `completed` or `failed`
- `running` must not transition back to `queued`
- terminal states `completed`, `failed`, and `canceled` must not transition to any other state
- repeated cancel commands against a terminal run must be rejected rather than returning a second synthetic state change

Terminal-state cancel invariants:

- `allowedActions.canCancel` may be `true` only while the run is in a backend-cancelable non-terminal state (`queued` or `running`)
- `allowedActions.canCancel` must be `false` for every terminal run summary and terminal run detail payload
- `allowedActions.canCancel` must be `false` in the cancel command response because durable acceptance moves the run into terminal `canceled`
- the frontend must treat terminal `completed`, `failed`, and `canceled` as non-cancelable even if a stale client cache previously showed `canCancel: true`; the next authoritative payload wins

Canonical read models:

```typescript
interface ExperimentLaunchResponse {
  experiment_id: string;
  ticket_id: string;
  status: "queued";
  queued_at: string;
  allowedActions: {
    canCancel: boolean;
  };
  links: {
    self: string;
    workbench_detail: string;
  };
}

interface ExperimentHistoryResponse {
  data: ExperimentRunSummary[];
  page_info: {
    next_page_token: string | null;
    total: number;
  };
  meta: {
    snapshot_at: string;
    surfaces: {
      experiment_history: "fresh" | "stale" | "degraded" | "unavailable";
    };
  };
}

interface ExperimentRunSummary {
  experiment_id: string;
  ticket_id: string;
  experiment_name: string;
  status: "queued" | "running" | "completed" | "failed" | "canceled";
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  artifact_ids: string[];
  allowedActions: {
    canCancel: boolean;
  };
  links: {
    self: string;
    workbench_detail: string;
  };
}

interface ExperimentRunDetail {
  experiment_id: string;
  ticket_id: string;
  experiment_name: string;
  status: "queued" | "running" | "completed" | "failed" | "canceled";
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  progress: {
    percent: number | null;
    phase: string | null;
    message: string | null;
  };
  strategy_selector: {
    strategy_id: string;
    variant_id: string | null;
  };
  parameter_set: Record<string, unknown>;
  run_config: {
    dataset_ref: string;
    time_range: {
      start_at: string;
      end_at: string;
    };
    execution_mode: "paper" | "backtest" | "simulation";
    priority: "normal" | "high";
    requested_by: string;
  };
  launch_context: {
    analysis_refs: string[] | null;
  };
  validation_warnings: Array<{
    code: string;
    message: string;
  }>;
  artifact_ids: string[];
  failure: {
    reason_code: string | null;
    message: string | null;
  };
  allowedActions: {
    canCancel: boolean;
  };
  links: {
    self: string;
    workbench_detail: string;
    linked_ticket_detail: string;
  };
  meta: {
    snapshot_at: string;
    surfaces: {
      experiment_status: "fresh" | "stale" | "degraded" | "unavailable";
    };
  };
}
```

Required invariants:

- `experiment_id` is the canonical identity of an experiment run. The frontend must not derive run identity from `ticket_id + queued_at`.
- The lifecycle is backend-owned. The frontend must not infer `"completed"` from elapsed time, missing heartbeats, or the presence of artifacts alone.
- Legal lifecycle paths are `queued -> running -> completed | failed | canceled` and `queued -> canceled` (direct cancel before execution begins). Any other state transition is a backend contract violation.
- `allowedActions.canCancel` is the only authority signal for rendering the cancel CTA. Status alone is insufficient because backend policy may disable cancellation for specific runs.
- `allowedActions.canCancel` must be `false` for all terminal runs (`completed`, `failed`, `canceled`) across launch follow-up payloads, detail payloads, history payloads, and cancel responses.
- `artifact_ids[]` is a durable output ledger, not a locally inferred list from downstream artifact APIs.
- The history route must return persisted run records. It must not reconstruct history by scraping live worker state or omitting terminal runs that are no longer active.
- `validation_warnings[]` are backend-authored launch caveats. The frontend must render them as provided and must not invent warning taxonomies from parameter names.
- `progress.percent`, `progress.phase`, and `progress.message` are optional because some runtimes may not report fine-grained progress even while the run is active.
- `failure.*` is only populated for terminal failure semantics and must remain `null` for non-failed runs.

## Async Status Delivery

- `GET /api/v1/experiments/{experiment_id}` is the canonical polling route for async status.
- The BFF may additionally expose SSE transport using the shared substrate from `PKT-005-sse-substrate`, but the event payload must map to the same `ExperimentRunDetail` field vocabulary.
- Whether polling or SSE is used, the backend remains the authority for lifecycle transitions, progress copy, and cancellation eligibility.
- Transport updates must preserve the same legal transition graph. SSE events and polling snapshots must not emit out-of-order or impossible transitions such as `queued -> completed` or `canceled -> running`.

## Filter Semantics

- `ticket_id` scopes run history to one research ticket lineage.
- `status` filters the experiment lifecycle only; it must not proxy ticket lifecycle or artifact version state.
- Pagination remains backend-owned through `page_token` and `page_size`.

## Degradation Rules

- When `meta.surfaces.experiment_history = "stale"`, the UI may keep the history list visible with the shared non-dismissable staleness banner.
- When `meta.surfaces.experiment_history = "degraded"`, the UI may render available rows but must not present a missing row set as authoritative evidence that no runs exist.
- When `meta.surfaces.experiment_status = "stale"`, the UI may show the last returned detail payload with the shared degradation banner, but it must not imply that the run is still progressing.
- When `meta.surfaces.experiment_status = "unavailable"`, suppress authoritative status copy and disable cancel affordances.

## Write Authority

- Launch request: `POST /api/v1/experiments/launch`
- Cancel request: `POST /api/v1/experiments/{experiment_id}/cancel`

The BFF must not expose client-side authority to mutate history rows, artifact lineage, or analysis context outside these command routes.

## Non-Goals

- The frontend must not synthesize fake progress bars, local async state machines, or inferred terminal states.
- The frontend must not decide cancel eligibility from status, route timing, or optimistic assumptions.
- This packet does not define artifact registry or comparison semantics. Those remain RW-05 scope.

## Relationship to Upstream and Downstream Modules

- RW-04 depends on RW-01 for stable `ticket_id` identity and lineage.
- RW-04 may reference RW-03 `analysis_refs[]` as launch context, but it must not redefine RW-03 metric aggregation or comparison vocabulary.
- RW-05 depends on RW-04 to emit stable `experiment_id` and durable `artifact_ids[]` so artifact provenance can be resolved without scraping runtime state.

## Example Payload

- `docs/examples/RW-04-experiment-launch.json`
