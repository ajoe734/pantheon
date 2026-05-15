# RW-03 Analyze

## Classification

- Workbench: Research Workbench
- Screen ID: `screen-research-analyze`
- Feature ID: `RW-03-analyze`
- Packet status: **route-live** — BFF routes `GET /api/v1/research/analysis` and `GET /api/v1/research/analysis/{analysis_id}` are confirmed live; UI work is unblocked
- Task: `RW-03-ANALYZE-001`

## Contract Note

The Research Analyze routes are confirmed live. UI implementation may proceed against the published analysis list/detail shapes and backend-owned metric aggregation and comparative summary.

The UI must not group raw result metrics into display panels, and it must not compute side-by-side diffs by fetching multiple detail payloads. All grouping and comparison data is backend-owned.

## User Goal

Let a researcher browse completed analysis runs, inspect backend-grouped metric panels, and review the backend-authored comparative summary without any client-side metric bucketing or delta computation.

## Routes

Primary routes:

- `/research/analyze` — analysis list
- `/research/analyze/:analysis_id` — analysis detail

## Readiness Gate

Pantheon has confirmed the live routes return:

1. `GET /api/v1/research/analysis` with `ticket_id`, `experiment_id`, `status`, `date_range`, `page_token`, `page_size`, and `meta.surfaces.analysis_results`
2. `GET /api/v1/research/analysis/{analysis_id}` with `metric_groups[]` (backend-grouped), `comparative_summary` (backend-authored), and all required `links.*` fields
3. The published `ResearchAnalysisSummary` and `ResearchAnalysisDetail` shapes

If any required field is absent from the live payload, emit a `bff-gap` handoff instead of rendering with invented data.

## Page Sections

### 1. Analysis List (`/research/analyze`)

- Fetches from `GET /api/v1/research/analysis`.
- Supports filter params: `ticket_id`, `experiment_id`, `status`, `date_range`.
- Renders each row from `ResearchAnalysisSummary`:
  - `analysis_id`
  - `ticket_id`
  - `summary.headline`
  - `summary.verdict`
  - `status`
  - `run_at`
  - `metric_group_refs[]` — render as tag chips only; do not expand or reorder
- Row click navigates to `links.workbench_detail`.
- Ticket anchor navigates to `links.linked_ticket_detail`.
- Backend ordering must be preserved. No client-side sort or re-rank.

### 2. Pagination Rail

- Uses `page_info.next_page_token` from the BFF response.
- Next-page requests must repeat the active filter values exactly.

### 3. Analysis Detail (`/research/analyze/:analysis_id`)

- Fetches from `GET /api/v1/research/analysis/{analysis_id}`.
- Renders summary section:
  - `summary.headline`
  - `summary.narrative`
  - `summary.verdict`
  - `summary.next_question`
- Renders `metric_groups[]` exactly as returned:
  - Each group is a panel with its backend-assigned `label` and `description`.
  - Each metric row shows `label`, `display_value`, `delta_display` (when present).
  - Direction indicator follows `direction` field — do not infer direction from delta sign.
  - Must not reorder or re-bucket metrics; backend group order is authoritative.
- Renders `comparative_summary` panel:
  - Shows `basis` as a section subtitle.
  - For each comparison, renders `label`, `run_at`, and `delta_highlights[]`.
  - `delta_highlights[].interpretation` must be rendered as-is; do not synthesize copy.
  - Must not fetch additional analysis detail payloads to build the comparison.

### 4. Staleness / Degradation Banner

- Uses the shared PKT-005 degradation substrate.
- Reads `meta.surfaces.analysis_results` in every route response.

## Degradation Handling

| Surface state | Required behavior |
|---|---|
| `meta.surfaces.analysis_results = "fresh"` | normal list and detail rendering |
| `meta.surfaces.analysis_results = "stale"` | non-dismissable staleness banner; keep data visible |
| `meta.surfaces.analysis_results = "degraded"` | degradation banner; keep available data visible; do not present empty state as authoritative |
| `meta.surfaces.analysis_results = "unavailable"` | suppress list and detail content; show unavailable notice |

## Constraints

- Use the Pantheon BFF only. No local data, no demo dataset, no client-side metric assembly.
- `metric_groups[]` must be rendered in backend-supplied order only.
- `comparative_summary` is the only authoritative diff surface; do not compute diffs client-side.
- `metric_group_refs[]` in list rows is informational — render as-is; do not use to pre-fetch groups.
- `baseline_value`, `delta_value`, and `delta_display` absence must not trigger client-side backfill.
- Drilldowns must use BFF-provided `links.*` values only.
- If any required field is missing, emit a `bff-gap` handoff instead of rendering with invented state.

## Acceptance

- List page fetches only from `GET /api/v1/research/analysis` with published filter params.
- Detail page fetches only from `GET /api/v1/research/analysis/{analysis_id}`.
- `metric_groups[]` renders in backend-provided group order without client-side bucketing.
- `comparative_summary` is rendered from the single detail payload without additional fetches.
- Degradation behavior follows `meta.surfaces.analysis_results` rules from PKT-005.

## References

- BFF contract: `docs/bff/RW-03-analyze.md`
- Example payload: `docs/examples/RW-03-analyze.json`
- Frontend change spec: `docs/pantheon-handoffs/RW-03-analyze/FRONTEND_CHANGE_SPEC.md`
- Packet family: `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`
