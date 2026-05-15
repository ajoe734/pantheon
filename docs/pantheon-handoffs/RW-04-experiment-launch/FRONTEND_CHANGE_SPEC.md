# RW-04 Experiment Launch — Frontend Change Spec

## Feature

- Feature ID: `RW-04-experiment-launch`
- Screen ID: `screen-experiment-launch`
- Workbench: Research Workbench
- Packet status: contract-ready — UI implementation may proceed against the live BFF routes
- Task: `RW-04-EXPERIMENT-001`

## Readiness Gate

Pantheon has confirmed **all four** of the following routes are live and returning the published field shape:

1. `POST /api/v1/experiments/launch` — accepts the published launch body and returns `status: "queued"` with `allowedActions.canCancel`.
2. `GET /api/v1/experiments` — returns paginated run history with `meta.surfaces.experiment_history`.
3. `GET /api/v1/experiments/{experiment_id}` — returns the durable run detail with `progress`, `artifact_ids`, `allowedActions.canCancel`, and `meta.surfaces.experiment_status`.
4. `POST /api/v1/experiments/{experiment_id}/cancel` — accepts a cancel request and returns `status: "canceled"` with `allowedActions.canCancel: false`.

Build the production pages against these live surfaces. If any required field is absent or diverges from the synced contract, emit `.coordination/requests/RW-04-experiment-launch-bff-gap.yaml` instead of inventing state or dummy CTAs.

## Summary

Build the **Experiment Launch** screens inside `front-ai-trading-system`. This slice lets a researcher submit a new experiment run, monitor its async lifecycle, inspect durable run history, and cancel a run when the backend authorizes it. All data and CTA authority come from the Pantheon BFF — no client-side state machine, no fake progress, no local cancel inference.

## Files to Create or Modify

```
src/pages/research/ExperimentLaunch.tsx       — new launch form and async status page
src/pages/research/ExperimentHistory.tsx      — new run history list page
src/pages/research/ExperimentDetail.tsx       — new run detail page (or drawer)
src/pages/research/ExperimentTypes.ts         — add experiment-run types
src/lib/bffClient.ts                          — add RW-04 experiment calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Launch experiment

```
POST /api/v1/experiments/launch
```

Request body (all fields required unless noted as nullable):

```typescript
{
  ticket_id: string;
  experiment_name: string;
  strategy_selector: {
    strategy_id: string;
    variant_id: string | null;
  };
  parameter_set: Record<string, unknown>;
  run_config: {
    dataset_ref: string;
    time_range: { start_at: string; end_at: string };
    execution_mode: "paper" | "backtest" | "simulation";
    priority: "normal" | "high";
    requested_by: string;
  };
  launch_context: {
    analysis_refs: string[] | null;
  };
}
```

Expected response shape (see `docs/examples/RW-04-experiment-launch.json` for full example):

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
```

### List experiment runs

```
GET /api/v1/experiments
Query params: ticket_id, status, page_token, page_size (default 20, max 100)
```

Expected response shape:

```typescript
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
  allowedActions: { canCancel: boolean };
  links: { self: string; workbench_detail: string };
}
```

### Get experiment detail

```
GET /api/v1/experiments/{experiment_id}
Path param: experiment_id (required)
```

Expected response shape:

```typescript
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
  strategy_selector: { strategy_id: string; variant_id: string | null };
  parameter_set: Record<string, unknown>;
  run_config: {
    dataset_ref: string;
    time_range: { start_at: string; end_at: string };
    execution_mode: "paper" | "backtest" | "simulation";
    priority: "normal" | "high";
    requested_by: string;
  };
  launch_context: { analysis_refs: string[] | null };
  validation_warnings: Array<{ code: string; message: string }>;
  artifact_ids: string[];
  failure: { reason_code: string | null; message: string | null };
  allowedActions: { canCancel: boolean };
  links: { self: string; workbench_detail: string; linked_ticket_detail: string };
  meta: {
    snapshot_at: string;
    surfaces: {
      experiment_status: "fresh" | "stale" | "degraded" | "unavailable";
    };
  };
}
```

### Cancel experiment

```
POST /api/v1/experiments/{experiment_id}/cancel
Path param: experiment_id (required)
Body: { reason: string }
```

Expected response fields: `experiment_id`, `status: "canceled"`, `completed_at`, `allowedActions.canCancel: false`.

## Component Structure

### `ExperimentLaunch.tsx`

- Route: `/research/experiments/launch`.
- Renders a form with `experiment_name`, `ticket_id`, `strategy_selector`, `parameter_set`, `run_config`, and optional `launch_context.analysis_refs`.
- All parameter validation rules and allowed ranges are backend-owned. Do not enforce them client-side.
- On submit, calls `POST /api/v1/experiments/launch`. On a `200` response, transitions to the async status view showing the returned `experiment_id` and `status`.

#### Async status view

- After a successful launch, enter a polling or SSE-driven status view.
- Poll `GET /api/v1/experiments/{experiment_id}` to display `status`, `progress.percent`, `progress.phase`, `progress.message`, `started_at`, `completed_at`.
- Do not infer `"completed"` or `"failed"` from elapsed time, missing heartbeats, or the presence of `artifact_ids`.
- `progress.*` fields are all nullable — render them only when non-null.

#### Cancel CTA

**Only render the Cancel CTA when `allowedActions.canCancel === true` AND `meta.surfaces.experiment_status !== "unavailable"`.**

- After submitting cancel, re-fetch `GET /api/v1/experiments/{experiment_id}` to confirm state. Do not optimistically update `status`.
- Do not re-render the Cancel CTA after the BFF confirms `status: "canceled"`.

### `ExperimentHistory.tsx`

- Route: `/research/experiments`.
- Fetches `GET /api/v1/experiments` on mount, supports `ticket_id` and `status` filter params.
- Each row: `experiment_name`, `experiment_id`, `status` badge, `queued_at`, `started_at`, `completed_at`, `artifact_ids` count.
- Row click navigates to the run detail page at `/research/experiments/:experiment_id`.
- Pagination via `page_info.next_page_token`.

### `ExperimentDetail.tsx`

- Route: `/research/experiments/:experiment_id`.
- Fetches `GET /api/v1/experiments/{experiment_id}` on mount.
- Renders all `ExperimentRunDetail` fields as described above.
- Renders `validation_warnings[]` as a labeled list when non-empty.
- Renders `failure.*` fields when `status === "failed"` and `failure` is populated.
- Renders `artifact_ids[]` as an output ledger — do not link to artifact details using speculative URLs.
- Renders Cancel CTA using the same `allowedActions.canCancel` gate as the launch status view.

## Degradation Handling

| `meta.surfaces.experiment_history` | Required behavior |
|---|---|
| `"fresh"` | Normal display |
| `"stale"` | Non-dismissable staleness banner at top; data visible |
| `"degraded"` | Available rows rendered with staleness banner; do not present missing rows as proof that no runs exist |
| `"unavailable"` | Replace list content with unavailable notice |

| `meta.surfaces.experiment_status` | Required behavior |
|---|---|
| `"fresh"` | Normal display |
| `"stale"` | Non-dismissable staleness banner; do not imply the run is still progressing |
| `"degraded"` | Show last-returned detail with degradation banner; disable cancel CTA |
| `"unavailable"` | Replace panel content with unavailable notice; suppress cancel CTA |

## Lifecycle State Machine

The backend owns the lifecycle. The frontend must not implement its own state machine.

Legal transitions (backend-authorized only):
- `queued → running`
- `queued → canceled`
- `running → completed`
- `running → failed`
- `running → canceled`

Terminal states: `completed`, `failed`, `canceled` — must not transition to any other state.

`allowedActions.canCancel` is the only authority signal for rendering the cancel CTA. Do not infer cancellation eligibility from `status` alone.

## State Requirements

Each data panel must handle:

- `loading`: skeleton or spinner
- `empty`: explicit empty copy (no blank panels)
- `stale`: stale banner with available data
- `unavailable`: degradation placeholder
- `error`: error copy with retry option

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- `allowedActions.canCancel` is the sole source of cancel CTA visibility truth — no inference from `status`, elapsed time, or presence of `artifact_ids`.
- Do not synthesize experiment state from ticker data, local timers, or heartbeat absence.
- `artifact_ids[]` is a durable output ledger returned by the BFF — do not construct it from downstream artifact APIs.
- If any required field is absent from the BFF response, write `.coordination/requests/RW-04-experiment-launch-bff-gap.yaml` and stop implementation.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/RW-04-experiment-launch-ui-done.yaml` using `.coordination/requests/RW-04-experiment-launch-ui-done.example.yaml` as the template.

## References

- BFF contract: `docs/bff/RW-04-experiment-launch.md`
- Example payload: `docs/examples/RW-04-experiment-launch.json`
- Contract-ready coordination: `.coordination/responses/RW-04-experiment-launch-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/RW-04-experiment-launch-lovable-ui-task.yaml`
- Packet family: `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`
- Research workbench policy: `docs/bff/RW-04-experiment-launch.md`
