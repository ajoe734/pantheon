# RW-03 Research Analyze — UI Decisions

- The canonical list route is `/research/analyze`, and the canonical detail
  route is `/research/analyze/:analysis_id`.
- List filters are stored in the browser URL using the exact backend parameter
  names: `ticket_id`, `experiment_id`, `status`, `date_range`, `page_token`,
  and `page_size`.
- The list screen renders only the backend-owned analysis summary projection:
  `analysis_id`, `ticket_id`, `experiment_id`, `status`, `run_at`,
  `summary.*`, `metric_group_refs[]`, and `links.*`.
- Pagination remains backend-owned through `page_info.next_page_token`. The UI
  keeps a local previous-token history only to revisit prior backend pages; it
  does not synthesize page numbers or offset-based paging.
- The detail screen renders `summary`, `metric_groups[]`, and
  `comparative_summary` exactly as returned. It does not regroup metrics or
  compute local comparison deltas.
- `404 OBJECT_NOT_FOUND` is treated as a missing analysis record. Other `404`
  responses are treated as route-not-live behavior for the current Pantheon
  runtime.
- `meta.surfaces.analysis_results` is the only freshness authority for stale,
  degraded, and unavailable copy. The UI does not infer freshness from empty
  arrays or missing optional fields.
- `links.linked_experiment_detail` is rendered as an optional backend-provided
  drilldown only when present. The current sibling front router mounts
  `/research/experiments/:experiment_id`; deployed-environment owner-route
  validation remains pending.
- Required RW-03 fields are validated before render. Missing required fields
  suppress normal rendering and direct the operator to emit the canonical
  `RW-03-analyze-bff-gap` handoff instead of inferring missing state.
