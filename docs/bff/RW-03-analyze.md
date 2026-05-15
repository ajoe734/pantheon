# RW-03 Analyze BFF Contract

## Status

**Contract published** — the Research Analyze read model, metric aggregation panels, and comparative summary payload are now the definitive implementation target for the Pantheon BFF. UI work must not start until Pantheon confirms the routes are live and returning this field shape.

Task: `RW-03-ANALYZE-001`

## Purpose

Provide one canonical analysis surface for the Research Workbench so researchers can browse completed analysis runs, inspect backend-grouped metrics, and compare related runs without grouping raw metrics or computing local diffs in the browser.

## Routes

### List analysis runs

- `GET /api/v1/research/analysis`

Supported query params:

- `ticket_id`
- `experiment_id`
- `status` — `"queued"` | `"running"` | `"completed"` | `"failed"`
- `date_range` — `"24h"` | `"7d"` | `"30d"` | `"90d"`
- `page_token`
- `page_size` — default `20`, maximum `100`

Required response fields:

- `data[]`
  - `analysis_id`
  - `ticket_id`
  - `experiment_id` — nullable when the analysis is ticket-scoped only
  - `status`
  - `run_at`
  - `summary.headline`
  - `summary.verdict`
  - `metric_group_refs[]`
  - `links.self`
  - `links.workbench_detail`
  - `links.linked_ticket_detail`
- `page_info.next_page_token`
- `page_info.total`
- `meta.snapshot_at`
- `meta.surfaces.analysis_results` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`

### Get analysis detail

- `GET /api/v1/research/analysis/{analysis_id}`

Required response fields:

- `analysis_id`
- `ticket_id`
- `experiment_id` — nullable
- `status`
- `run_at`
- `completed_at` — nullable
- `summary.headline`
- `summary.narrative`
- `summary.verdict`
- `summary.next_question`
- `metric_groups[]`
  - `group_key`
  - `label`
  - `description`
  - `metrics[]`
    - `metric_key`
    - `label`
    - `value`
    - `unit`
    - `display_value`
    - `direction` — `"higher_is_better"` | `"lower_is_better"` | `"contextual"`
    - `baseline_value` — nullable
    - `delta_value` — nullable
    - `delta_display` — nullable
- `comparative_summary`
  - `basis` — backend-authored comparison set description
  - `baseline_analysis_id`
  - `focus_metrics[]`
  - `comparisons[]`
    - `analysis_id`
    - `label`
    - `status`
    - `run_at`
    - `delta_highlights[]`
      - `metric_key`
      - `change_label`
      - `direction`
      - `delta_display`
      - `interpretation`
- `links.self`
- `links.workbench_detail`
- `links.linked_ticket_detail`
- `links.linked_experiment_detail` — nullable
- `meta.snapshot_at`
- `meta.surfaces.analysis_results` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`

## ResearchAnalysis Objects

Canonical read model:

```typescript
interface ResearchAnalysisListResponse {
  data: ResearchAnalysisSummary[];
  page_info: {
    next_page_token: string | null;
    total: number;
  };
  meta: {
    snapshot_at: string;
    surfaces: {
      analysis_results: "fresh" | "stale" | "degraded" | "unavailable";
    };
  };
}

interface ResearchAnalysisSummary {
  analysis_id: string;
  ticket_id: string;
  experiment_id: string | null;
  status: "queued" | "running" | "completed" | "failed";
  run_at: string;
  summary: {
    headline: string;
    verdict: string;
  };
  metric_group_refs: string[];
  links: {
    self: string;
    workbench_detail: string;
    linked_ticket_detail: string;
  };
}

interface ResearchAnalysisDetail {
  analysis_id: string;
  ticket_id: string;
  experiment_id: string | null;
  status: "queued" | "running" | "completed" | "failed";
  run_at: string;
  completed_at: string | null;
  summary: {
    headline: string;
    narrative: string;
    verdict: string;
    next_question: string;
  };
  metric_groups: MetricGroup[];
  comparative_summary: ComparativeSummary;
  links: {
    self: string;
    workbench_detail: string;
    linked_ticket_detail: string;
    linked_experiment_detail: string | null;
  };
  meta: {
    snapshot_at: string;
    surfaces: {
      analysis_results: "fresh" | "stale" | "degraded" | "unavailable";
    };
  };
}
```

Required invariants:

- `analysis_id` is the canonical identity of an analysis run. The frontend must not derive row keys from `ticket_id + run_at`.
- `metric_groups[]` is already backend-grouped. The frontend must render the provided grouping and order exactly as returned.
- `metric_group_refs[]` in list payloads is a lightweight summary of the same backend-owned grouping keys shown in detail; the UI must not infer missing groups from raw metric names.
- `comparative_summary` is the only authoritative diff surface. The frontend must not fetch two detail routes and compute its own comparison.
- `baseline_value`, `delta_value`, and `delta_display` are optional because some metrics are informative without a baseline; the absence of delta data must not trigger client-side backfill.
- `linked_experiment_detail` is nullable when an analysis is attached directly to a ticket rather than an experiment run.

## Filter Semantics

- `ticket_id` scopes analysis runs to one research ticket lineage.
- `experiment_id` narrows the list to analyses generated from one experiment run.
- `status` filters the analysis-run lifecycle, not the underlying ticket lifecycle.
- `date_range` is a backend-owned recency window over `run_at`; the frontend must not invent arbitrary timestamp filters outside the published token set.
- Pagination remains backend-owned through `page_token` and `page_size`.

## Metric Aggregation Contract

The metric aggregation layer is part of this packet even though it is not exposed as a standalone route.

Required rules:

- The BFF is the only authority that maps raw metrics into presentation groups such as `performance`, `drawdown`, and `signal_quality`.
- Each `metric_group` must contain ordered metrics that are already labeled for display.
- Comparative deltas must be attached to each metric by the backend when a baseline exists.
- The frontend must not bucket metrics by prefix, substring, or naming convention.

## Degradation Rules

- When `meta.surfaces.analysis_results = "stale"`, the shared degradation substrate from `PKT-005` must keep the current data visible with a non-dismissable staleness banner.
- When `meta.surfaces.analysis_results = "degraded"`, the UI may render available analysis data but must not present empty states as authoritative.
- When `meta.surfaces.analysis_results = "unavailable"`, suppress list and detail content and show the unavailable state instead.

## Non-Goals

- The frontend must not group raw result metrics into display panels.
- The frontend must not compute side-by-side diffs from multiple analysis payloads.
- This packet does not define experiment launch semantics or artifact version comparison. Those remain RW-04 and RW-05 scope.

## Relationship to Upstream and Downstream Modules

- RW-03 depends on RW-01 for stable `ticket_id` identity and lineage.
- RW-03 may reference experiment lineage from RW-04, but the analysis payload shape is fixed independently of the eventual experiment launch UI.
- RW-04 may consume analysis summaries as launch context, but it must not redefine the metric grouping or comparison vocabulary published here.

## Example Payload

- `docs/examples/RW-03-analyze.json`
