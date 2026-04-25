# RW-03 Analyze — Frontend Change Spec

## Feature

- Feature ID: `RW-03-analyze`
- Screen ID: `screen-research-analyze`
- Workbench: Research Workbench
- Packet status: route-live — UI implementation may proceed against the live BFF routes
- Task: `RW-03-ANALYZE-001`

## Summary

Build the Research Workbench analyze surface inside `front-ai-trading-system`. This slice includes the analysis list with filter rail, analysis detail with backend-grouped metric panels, and the backend-authored comparative summary. All metric grouping, delta computation, and comparison data must come from the Pantheon BFF. The frontend must not group raw metrics or compute side-by-side diffs.

## Files to Create or Modify

```text
src/pages/research/ResearchAnalyzeList.tsx      — new analysis list page
src/pages/research/ResearchAnalyzeDetail.tsx    — new analysis detail page
src/pages/research/types.ts                     — add RW-03 analysis types
src/lib/bffClient.ts                            — add RW-03 analysis calls
src/App.tsx                                     — add /research/analyze and /research/analyze/:id routes
```

## Readiness Gate

Pantheon has confirmed the following are live and returning the published field shape:

- `GET /api/v1/research/analysis`
- `GET /api/v1/research/analysis/{analysis_id}`
- `metric_groups[]` is backend-grouped in every detail response
- `comparative_summary` is present and backend-authored in every detail response
- `links.workbench_detail` and `links.linked_ticket_detail` in every list row
- `links.linked_experiment_detail` in every detail response (nullable)

Build the production pages against these live routes. If any required field is absent or diverges from the synced contract, emit `.coordination/requests/RW-03-analyze-bff-gap.yaml` instead of falling back to placeholder or mock state.

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` in component files.

### Analysis list route

```http
GET /api/v1/research/analysis
```

Supported query params:

- `ticket_id`
- `experiment_id`
- `status` — `"queued"` | `"running"` | `"completed"` | `"failed"`
- `date_range` — `"24h"` | `"7d"` | `"30d"` | `"90d"`
- `page_token`
- `page_size`

Required response fields:

- `data[].analysis_id`
- `data[].ticket_id`
- `data[].experiment_id`
- `data[].status`
- `data[].run_at`
- `data[].summary.headline`
- `data[].summary.verdict`
- `data[].metric_group_refs[]`
- `data[].links.self`
- `data[].links.workbench_detail`
- `data[].links.linked_ticket_detail`
- `page_info.next_page_token`
- `page_info.total`
- `meta.snapshot_at`
- `meta.surfaces.analysis_results`

### Analysis detail route

```http
GET /api/v1/research/analysis/{analysis_id}
```

Required response fields:

- `analysis_id`
- `ticket_id`
- `experiment_id`
- `status`
- `run_at`
- `completed_at`
- `summary.headline`
- `summary.narrative`
- `summary.verdict`
- `summary.next_question`
- `metric_groups[].group_key`
- `metric_groups[].label`
- `metric_groups[].description`
- `metric_groups[].metrics[].metric_key`
- `metric_groups[].metrics[].label`
- `metric_groups[].metrics[].value`
- `metric_groups[].metrics[].unit`
- `metric_groups[].metrics[].display_value`
- `metric_groups[].metrics[].direction`
- `metric_groups[].metrics[].baseline_value` (nullable)
- `metric_groups[].metrics[].delta_value` (nullable)
- `metric_groups[].metrics[].delta_display` (nullable)
- `comparative_summary.basis`
- `comparative_summary.baseline_analysis_id`
- `comparative_summary.focus_metrics[]`
- `comparative_summary.comparisons[].analysis_id`
- `comparative_summary.comparisons[].label`
- `comparative_summary.comparisons[].status`
- `comparative_summary.comparisons[].run_at`
- `comparative_summary.comparisons[].delta_highlights[].metric_key`
- `comparative_summary.comparisons[].delta_highlights[].change_label`
- `comparative_summary.comparisons[].delta_highlights[].direction`
- `comparative_summary.comparisons[].delta_highlights[].delta_display`
- `comparative_summary.comparisons[].delta_highlights[].interpretation`
- `links.self`
- `links.workbench_detail`
- `links.linked_ticket_detail`
- `links.linked_experiment_detail` (nullable)
- `meta.snapshot_at`
- `meta.surfaces.analysis_results`

## Component Rules

### `ResearchAnalyzeList.tsx`

- Hosts the filter rail and analysis list.
- Filter params must map exactly to backend query params: `ticket_id`, `experiment_id`, `status`, `date_range`.
- `status` filter vocabulary is limited to: `queued`, `running`, `completed`, `failed`.
- `date_range` vocabulary is limited to: `24h`, `7d`, `30d`, `90d`.
- Row click navigates to `links.workbench_detail`.
- Ticket anchor navigates to `links.linked_ticket_detail`.
- `metric_group_refs[]` renders as informational tag chips; do not expand, reorder, or use for prefetching.
- Backend list order is authoritative. Do not sort client-side.
- If `meta.surfaces.analysis_results` is `degraded` or `unavailable`, render the shared PKT-005 degradation banner.

### `ResearchAnalyzeDetail.tsx`

- Renders summary, metric groups, comparative summary, and navigation links.
- `metric_groups[]` must render in backend-provided order. Do not re-bucket or re-order groups or metrics.
- For each metric row, show `label`, `display_value`, and `delta_display` when present.
- Absence of `baseline_value`, `delta_value`, or `delta_display` must not trigger client-side backfill.
- `direction` field drives any directional indicator (arrow, color) — do not infer direction from delta sign.
- `comparative_summary` renders from the single detail payload. Do not fetch additional analysis detail payloads to build or enrich the comparison.
- `comparative_summary.basis` renders as a section subtitle.
- `delta_highlights[].interpretation` renders as-is; do not synthesize or replace copy.
- `links.linked_experiment_detail` is nullable; only render the experiment link when present.

## Constraints

- Use the existing BFF client only.
- Do not add raw network calls in components.
- Do not group metrics by prefix, substring, or naming convention.
- Do not compute side-by-side diffs from multiple analysis payloads.
- Do not use `metric_group_refs[]` from list payloads to infer missing groups in detail.
- Do not invent drilldown paths; use `links.*` values only.
- If any required field is missing, emit a `bff-gap` handoff instead of mocking.

## Degradation Handling

| State | Handling |
|---|---|
| `meta.surfaces.analysis_results = "stale"` | render non-dismissable staleness banner; keep current data visible |
| `meta.surfaces.analysis_results = "degraded"` | render degradation banner; keep available data visible; do not present empty state as authoritative |
| `meta.surfaces.analysis_results = "unavailable"` | suppress list and detail content; show unavailable notice |

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/RW-03-analyze-ui-done.yaml` using `.coordination/requests/RW-03-analyze-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/RW-03-analyze.md`
- BFF contract: `docs/bff/RW-03-analyze.md`
- Example payload: `docs/examples/RW-03-analyze.json`
- Contract-ready: `.coordination/responses/RW-03-analyze-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/RW-03-analyze-lovable-ui-task.yaml`
- Packet family: `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`
