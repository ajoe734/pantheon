# RW-03 Analyze Review Packet

## Date

2026-04-21

## Reviewer

Codex

## Findings

None.

## Verified

- The immutable RW-03 publication chain is now replayable from Git history:
  `origin/pkt-004-detail-fix` resolves to
  `e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f`, and that publish commit contains
  the canonical request pair plus `docs/pantheon-feedback/RW-03-analyze/*`.
  The reviewed implementation commit
  `ef9b4d7b4f69ac829ea097fff0bef889d42e46dc` contains
  `src/pages/research/ResearchAnalyze.tsx`,
  `src/pages/research/ResearchAnalyzeDetail.tsx`,
  `src/pages/research/types.ts`, `src/lib/bffClient.ts`, `src/App.tsx`,
  `src/components/AppSidebar.tsx`, `src/components/WorkbenchBreadcrumb.tsx`,
  and `src/pages/health/Health.tsx`, and the canonical request pair in
  `e9f93f5...` truthfully points back to that implementation commit.
- Pantheon's RW-03 contract slice still passes in the current workspace:
  `python3 -m pytest -q services/control-plane/bff/test_rw03_analyze_contract.py`
  returned `4 passed`.
- The active runtime on `http://127.0.0.1:18001` remains truthful for RW-03:
  `GET /openapi.json` advertises both published analysis routes, operator-auth
  list/detail probes return degraded contract-shaped payloads, the list probe
  preserves backend `page_info.next_page_token = 2` plus
  `metric_group_refs = [performance, drawdown, signal_quality]`, the detail
  probe preserves backend `metric_groups = performance, drawdown,
  signal_quality` plus `links.linked_experiment_detail =
  /research/experiments/exp-20260419-012`, and a missing-detail probe returns
  `404 OBJECT_NOT_FOUND` for `does-not-exist`.
- The reviewed front implementation remains contract-aligned:
  `ResearchAnalyze.tsx` forwards only `ticket_id`, `experiment_id`, `status`,
  `date_range`, `page_token`, and `page_size` through `rw03AnalyzeApi.list()`,
  renders backend-owned summary rows and `metric_group_refs[]` without local
  grouping, and drills down through `links.workbench_detail` plus
  `links.linked_ticket_detail` only. `ResearchAnalyzeDetail.tsx` reads only
  `rw03AnalyzeApi.detail()`, renders `summary`, `metric_groups[]`, and
  `comparative_summary` exactly as returned, preserves backend ordering, and
  distinguishes `404 OBJECT_NOT_FOUND` from route-not-live `404`s.
- The current front router keeps the owner-link surface truthful:
  `App.tsx`, `AppSidebar.tsx`, and `WorkbenchBreadcrumb.tsx` expose
  `/research/analyze` and `/research/analyze/:analysis_id`, and the sibling
  app still mounts `/research/experiments/:experiment_id`, which matches the
  live `linked_experiment_detail` returned by the active BFF.
- Sibling front validation passed again for the reviewed RW-03 slice:
  targeted `npx eslint` on the reviewed files exited cleanly, and
  `npm run build` succeeded on 2026-04-21 with only the existing non-blocking
  Browserslist age notice and Vite chunk-size warning.

## Decision

Approved. `RW-03-analyze` is ready for `review_approved`.

The prior publication-truth blocker is resolved: the front branch now exposes a
Git-visible RW-03 implementation commit plus a publish commit that contains the
canonical request pair and feedback bundle pinned back to that implementation
tree. The live Pantheon runtime advertises the RW-03 route family, degraded and
OBJECT_NOT_FOUND behavior match the published contract, and the sibling front
repo still validates cleanly. No Pantheon API gap remains in this loop.

## Residual Risk

- The active shared runtime still does not expose a direct stale RW-03 envelope
  on `http://127.0.0.1:18001`; current stale evidence remains service-backed
  through the auxiliary probe recorded in
  `.coordination/requests/RW-03-analyze-needs-runtime.yaml`.
- No deployed browser session was exercised against `/research/analyze` or
  `/research/analyze/:analysis_id`, so deployed-environment QA and
  `linked_experiment_detail` navigation confirmation remain non-blocking
  follow-up.
