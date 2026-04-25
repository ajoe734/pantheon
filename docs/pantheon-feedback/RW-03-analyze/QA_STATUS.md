# RW-03 Research Analyze — QA Status

## Status

Pantheon review update prepared. Outcome: `ready-for-review`.

## Checks completed

- `npx eslint src/pages/research/ResearchAnalyze.tsx src/pages/research/ResearchAnalyzeDetail.tsx src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx src/pages/health/Health.tsx src/lib/bffClient.ts src/pages/research/types.ts` passed.
- `npm run build` passed in `front-ai-trading-system`.
- `python3 -m pytest -q services/control-plane/bff/test_rw03_analyze_contract.py` passed with 4 tests in the Pantheon workspace.
- Runtime contract validation coverage was checked in code for the RW-03 list
  response:
  - `data[].analysis_id`
  - `data[].ticket_id`
  - `data[].experiment_id`
  - `data[].status`
  - `data[].run_at`
  - `data[].summary.headline`
  - `data[].summary.verdict`
  - `data[].metric_group_refs`
  - `data[].links.self`
  - `data[].links.workbench_detail`
  - `data[].links.linked_ticket_detail`
  - `page_info.next_page_token`
  - `page_info.total`
  - `meta.snapshot_at`
  - `meta.surfaces.analysis_results`
- Runtime contract validation coverage was checked in code for the RW-03 detail
  response:
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
  - `metric_groups[].metrics[].baseline_value`
  - `metric_groups[].metrics[].delta_value`
  - `metric_groups[].metrics[].delta_display`
  - `comparative_summary.basis`
  - `comparative_summary.baseline_analysis_id`
  - `comparative_summary.focus_metrics`
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
  - `links.linked_experiment_detail`
  - `meta.snapshot_at`
  - `meta.surfaces.analysis_results`
- Live runtime probes against the active Pantheon BFF recorded on `2026-04-21`:
  - `GET http://127.0.0.1:18001/openapi.json` advertises
    `/api/v1/research/analysis` and `/api/v1/research/analysis/{analysis_id}`.
  - `GET http://127.0.0.1:18001/api/v1/research/analysis?page_size=1` with
    `Authorization: Bearer op-rw03:operator` returns `200 OK` with
    `page_info.next_page_token = "1"`, `total = 3`, and
    `meta.surfaces.analysis_results = degraded`.
  - `GET http://127.0.0.1:18001/api/v1/research/analysis/analysis-20260419-007-a`
    with `Authorization: Bearer op-rw03:operator` returns `200 OK` with ordered
    `metric_groups = [performance, drawdown, signal_quality]`,
    backend-authored `comparative_summary`, and
    `links.linked_experiment_detail = /research/experiments/exp-20260419-012`.
  - `GET http://127.0.0.1:18001/api/v1/research/analysis/does-not-exist` with
    `Authorization: Bearer op-rw03:operator` returns `404 OBJECT_NOT_FOUND`.
  - The same list probe without bearer auth returns `401 INVALID_TOKEN`.
  - The same list probe with `Authorization: Bearer op-rw03:viewer` returns
    `403 INSUFFICIENT_ROLE`.
- Workspace-backed unavailable verification recorded:
  - `BFF_READ_SURFACE_STATE=unavailable` list probe returns `200 OK` with
    `data = []`, `total = 0`, and `meta.surfaces.analysis_results = unavailable`.
  - `BFF_READ_SURFACE_STATE=unavailable` detail probe returns `200 OK` with
    the backend-owned run snapshot preserved and
    `meta.surfaces.analysis_results = unavailable`.
- Service-backed stale verification recorded:
  - `PANTHEON_BFF_RESEARCH_ANALYSIS_STORE=<tmp>/research_analyses.json`
    plus the auxiliary probe on `http://127.0.0.1:18014` returns `200 OK`
    stale list/detail envelopes with
    `meta.surfaces.analysis_results = stale`.
- Front publication tuple review completed:
  - `git -C ../front-ai-trading-system ls-remote --heads origin pkt-004-detail-fix`
    now resolves to `e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f`.
  - `git -C ../front-ai-trading-system show --stat --summary ef9b4d7b4f69ac829ea097fff0bef889d42e46dc -- ...`
    confirms the immutable RW-03 implementation commit for
    `ResearchAnalyze.tsx`, `ResearchAnalyzeDetail.tsx`,
    `src/pages/research/types.ts`, and `src/pages/health/Health.tsx`.
  - `git -C ../front-ai-trading-system show --stat --summary e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f -- ...`
    confirms the canonical RW-03 request pair and feedback bundle are
    published and pinned to `ef9b4d7b4f69ac829ea097fff0bef889d42e46dc`.
  - `git -C ../front-ai-trading-system ls-tree -r --name-only e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f -- ...`
    includes the RW-03 request pair, feedback bundle, Analyze pages,
    `src/pages/research/types.ts`, and `src/pages/health/Health.tsx`.
  - `git -C ../front-ai-trading-system status --short -- ...` returns no
    output for the reviewed RW-03 slice.

## Not completed in this cycle

- Direct live capture of `meta.surfaces.analysis_results = stale` from the
  active operator-bff on `http://127.0.0.1:18001`.
- Deployed browser QA against the mounted RW-03 routes.
- Deployed owner-route verification for `links.linked_experiment_detail`.

## Risk note

No publication blocker remains. The RW-03 UI implementation compiles, the live
operator-bff serves authenticated degraded list/detail responses plus
`OBJECT_NOT_FOUND`, the auxiliary unavailable probe matches the published
suppression semantics, and the service-backed auxiliary probe returns truthful
stale envelopes. Direct active-runtime stale capture on `18001` and deployed
browser QA remain non-blocking follow-up.
