# RW-03 Research Analyze — Lovable Change Feedback

Reviewed the RW-03 Research Analyze implementation in
`ajoe734/front-ai-trading-system` against the published contract, example
payload, current sibling front implementation, and the current Pantheon BFF
runtime.

## Outcome

Pantheon review result: accepted for follow-up handoff.

The Research Analyze list and detail screens are implemented against the
published RW-03 contract shape. The list screen submits only the canonical
query params, renders backend-owned summary rows and `metric_group_refs`
directly, preserves backend pagination through `page_token`, and drills down
through `links.workbench_detail` plus `links.linked_ticket_detail`. The detail
screen renders `summary`, `metric_groups`, and `comparative_summary` exactly as
returned, keeps `OBJECT_NOT_FOUND` separate from route-not-live behavior, and
does not compute local metric grouping or diff logic in the browser. The
previous publication-truth blocker is resolved: implementation commit
`ef9b4d7b4f69ac829ea097fff0bef889d42e46dc` and publication commit
`e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f` are now GitHub-visible on
`origin/pkt-004-detail-fix`.

## Verified Against Pantheon

- `src/pages/research/ResearchAnalyze.tsx` implements the `/research/analyze`
  screen with URL-backed filters that map directly to `ticket_id`,
  `experiment_id`, `status`, `date_range`, `page_token`, and `page_size`.
- The list screen renders backend-owned `summary.headline`,
  `summary.verdict`, `metric_group_refs`, `links.workbench_detail`, and
  `links.linked_ticket_detail` without synthesizing row content or pagination.
- `src/pages/research/ResearchAnalyzeDetail.tsx` implements the
  `/research/analyze/:analysis_id` route, validates the required RW-03 detail
  fields before rendering, and preserves the backend-owned ordering of
  `metric_groups[]` and `comparative_summary.comparisons[]`.
- The detail screen treats `404 OBJECT_NOT_FOUND` as a missing analysis run and
  treats other `404` responses as route-not-live behavior instead of
  reconstructing local detail state.
- `src/App.tsx`, `src/components/AppSidebar.tsx`, and
  `src/components/WorkbenchBreadcrumb.tsx` register and surface the Analyze list
  and detail routes in the Research Workbench shell.
- The current front router also mounts `/research/experiments/:experiment_id`,
  which matches the `links.linked_experiment_detail` owner route returned by
  the live RW-03 detail payload.
- `python3 -m pytest -q services/control-plane/bff/test_rw03_analyze_contract.py`
  passed in the current Pantheon workspace (`4 passed`).
- `npm run build` passed in `front-ai-trading-system`.
- Targeted ESLint passed for the RW-03 surface:
  `src/pages/research/ResearchAnalyze.tsx`,
  `src/pages/research/ResearchAnalyzeDetail.tsx`,
  `src/App.tsx`,
  `src/components/AppSidebar.tsx`,
  `src/components/WorkbenchBreadcrumb.tsx`,
  `src/pages/health/Health.tsx`,
  `src/lib/bffClient.ts`,
  and `src/pages/research/types.ts`.

## Constraint compliance

- No raw `fetch` or `axios` calls were added in the RW-03 screen components.
  Network access remains in the shared BFF client surface.
- The browser does not regroup raw metrics by prefix, substring, or inferred
  vocabulary. It renders backend-owned `metric_group_refs` and `metric_groups`
  only.
- The detail screen does not fetch multiple analysis payloads or compute local
  diffs. `comparative_summary` remains the only comparison surface.
- `meta.surfaces.analysis_results = "stale"` keeps returned data visible under a
  non-dismissable staleness alert.
- `meta.surfaces.analysis_results = "degraded"` keeps returned data visible but
  suppresses authoritative empty-state language.
- `meta.surfaces.analysis_results = "unavailable"` suppresses list rows or
  detail content instead of inventing fallback state.
- Runtime contract validation happens before renderable state is committed. If a
  required RW-03 field is absent, the screen raises a contract-gap alert
  instructing the operator to emit
  `.coordination/requests/RW-03-analyze-bff-gap.yaml`.

## Notes

- Live route publication is now confirmed. On `2026-04-21`, the active
  `pantheon-operator-bff` at `http://127.0.0.1:18001` advertises both
  `GET /api/v1/research/analysis` and
  `GET /api/v1/research/analysis/{analysis_id}` in `openapi.json`.
- Authenticated live probes return contract-shaped degraded list/detail
  payloads, including ordered `metric_groups[]`, backend-authored
  `comparative_summary`, and `links.linked_experiment_detail`. A missing detail
  probe returns `404 OBJECT_NOT_FOUND` as expected.
- Missing-auth and wrong-role probes return `401 INVALID_TOKEN` and
  `403 INSUFFICIENT_ROLE`, respectively.
- A workspace-backed unavailable probe returns list/detail envelopes with
  `meta.surfaces.analysis_results = unavailable`, which matches the
  UI-suppression logic in the RW-03 screens.
- A service-backed HTTP probe on `http://127.0.0.1:18014` returns truthful
  stale RW-03 list/detail envelopes with
  `meta.surfaces.analysis_results = stale`.
- Front publication truth is now replay-clean. Remote branch
  `origin/pkt-004-detail-fix` resolves to
  `e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f`, which contains the canonical
  RW-03 request pair and feedback bundle, while its parent implementation
  commit `ef9b4d7b4f69ac829ea097fff0bef889d42e46dc` immutably contains
  `ResearchAnalyze.tsx`, `ResearchAnalyzeDetail.tsx`, `src/pages/research/types.ts`,
  and the health endpoint listing update.

## Pantheon Follow-up

- Capture a truthful `stale` RW-03 runtime response from the active
  `pantheon-operator-bff` when `http://127.0.0.1:18001` emits one directly;
  stale behavior is already verified on the service-backed auxiliary probe.
- Run deployed browser QA for `/research/analyze` and
  `/research/analyze/:analysis_id`.
- Confirm `links.linked_experiment_detail` resolves to the intended owner route
  in the deployed environment.
