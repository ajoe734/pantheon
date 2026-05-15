# RW-03 Analyze Backend Delivery Note

## Status

`loop-complete`

## Summary

Pantheon synced the accepted `RW-03-analyze` `ui-done` handoff and paired
`frontend-feedback` bundle from `ajoe734/front-ai-trading-system` against the
canonical RW-03 contract, example payload, sibling front publication chain,
and the current Pantheon BFF/runtime evidence.

The earlier RW-03 publication blocker is now resolved:

- reviewed UI implementation commit:
  `ef9b4d7b4f69ac829ea097fff0bef889d42e46dc`
- current request-pair publish commit:
  `e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f`
- `git ls-remote --heads origin pkt-004-detail-fix` now resolves to
  `e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f`
- the publish commit contains the canonical request pair, the full
  `docs/pantheon-feedback/RW-03-analyze/*` bundle, and the reviewed Analyze UI
  files while truthfully pinning `source_commit` back to
  `ef9b4d7b4f69ac829ea097fff0bef889d42e46dc`

Pantheon also reconfirmed that the RW-03 route family remains live and
contract-shaped:

- `GET /api/v1/research/analysis`
- `GET /api/v1/research/analysis/{analysis_id}`

`python3 -m pytest -q services/control-plane/bff/test_rw03_analyze_contract.py`
still passes (`4 passed`), and the live OpenAPI document on
`http://127.0.0.1:18001/openapi.json` still advertises both RW-03 routes.

No new Pantheon endpoint, contract expansion, shadow state, or client-side
metric grouping or diff synthesis is authorized or required in this cycle. The
current RW-03 loop is complete apart from deferred browser QA and later direct
stale-envelope capture from the active runtime.

## Delivered Findings

### 1. The request pair and feedback bundle are now replay-clean and Git-visible

Observed in the sibling front repo:

- `git show e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f:.coordination/requests/RW-03-analyze-ui-done.yaml`
  publishes
  `source_commit: ef9b4d7b4f69ac829ea097fff0bef889d42e46dc`
- the matching
  `.coordination/requests/RW-03-analyze-frontend-feedback.yaml`
  publishes the same real `source_commit`
- `git branch -r --contains e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f`
  returns `origin/pkt-004-detail-fix`
- `git branch -r --contains ef9b4d7b4f69ac829ea097fff0bef889d42e46dc`
  returns `origin/pkt-004-detail-fix`
- `git ls-tree -r --name-only e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f -- ...`
  returns the canonical request pair, the
  `docs/pantheon-feedback/RW-03-analyze/*` bundle,
  `src/App.tsx`, `src/components/AppSidebar.tsx`,
  `src/components/WorkbenchBreadcrumb.tsx`, `src/lib/bffClient.ts`,
  `src/pages/health/Health.tsx`, `src/pages/research/ResearchAnalyze.tsx`,
  `src/pages/research/ResearchAnalyzeDetail.tsx`, and
  `src/pages/research/types.ts`

Impact:

- Pantheon can now replay the returned RW-03 cycle from a truthful remote
  branch head
- the closeout record no longer points at the stale `f00791b...`
  publication tuple

### 2. The reviewed UI implementation remains contract-aligned

Observed in the accepted review packet and published source commit:

- `ResearchAnalyze.tsx` sends only the published list query params, preserves
  backend ordering, renders `metric_group_refs[]` as informational chips only,
  and navigates via `links.workbench_detail` plus
  `links.linked_ticket_detail`
- `ResearchAnalyzeDetail.tsx` renders `summary`, `metric_groups[]`, and
  `comparative_summary` from the single detail payload, preserves backend group
  order, and distinguishes `404 OBJECT_NOT_FOUND` from route-not-live behavior
- `src/lib/bffClient.ts` continues to expose `rw03AnalyzeApi`; no component
  added a raw `fetch` path
- the publish tree still mounts `/research/analyze`,
  `/research/analyze/:analysis_id`, and
  `/research/experiments/:experiment_id`
- targeted front verification still records `npx eslint` and `npm run build`
  passing for the reviewed RW-03 slice

Impact:

- the reviewed RW-03 UI behavior remains aligned to the published acceptance
  rules
- the final publish commit closes the replay chain without reopening a UI
  contract divergence

### 3. Pantheon RW-03 routes remain live and contract-shaped

Observed in the current Pantheon workspace/runtime:

- `python3 -m pytest -q services/control-plane/bff/test_rw03_analyze_contract.py`
  returned `4 passed`
- the live OpenAPI document on `http://127.0.0.1:18001/openapi.json` still
  lists:
  - `GET /api/v1/research/analysis`
  - `GET /api/v1/research/analysis/{analysis_id}`
- authenticated live HTTP list and detail probes on `18001` returned `200` with
  degraded contract-shaped payloads, backend-owned metric grouping, and
  backend-authored `comparative_summary`
- authenticated missing-detail probe on `18001` returned `404 OBJECT_NOT_FOUND`
- missing auth returned `401 INVALID_TOKEN` and viewer auth returned
  `403 INSUFFICIENT_ROLE`
- auxiliary workspace-backed probes on `18013` and `18014` still provide
  truthful `unavailable` and `stale` RW-03 envelopes

Impact:

- no additional Pantheon runtime or contract follow-up remains for the current
  RW-03 packet scope

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Pantheon runtime route family: still live and verified
- Pantheon delivery completed:
  - re-confirmed the replay-clean `ef9b4d7 -> e9f93f5` front publication chain
  - re-ran the targeted RW-03 contract slice in the current workspace
  - re-confirmed live OpenAPI route publication for the RW-03 route family
  - retained live and auxiliary HTTP proof for degraded, unavailable, stale,
    and `OBJECT_NOT_FOUND` behavior
- Front follow-up needed:
  - none for the current packet scope
- Current loop outcome: `loop-complete`

## Verification Performed

- Reviewed Pantheon-visible request artifacts:
  - `.coordination/requests/RW-03-analyze-ui-done.yaml`
  - `.coordination/requests/RW-03-analyze-frontend-feedback.yaml`
  - `.coordination/requests/RW-03-analyze-needs-runtime.yaml`
- Reviewed the accepted Pantheon review packet:
  - `.coordination/reviews/RW-03-analyze-review.md`
  - `.coordination/reviews/RW-03-analyze-approval.md`
- Re-checked the canonical packet:
  - `docs/bff/RW-03-analyze.md`
  - `docs/examples/RW-03-analyze.json`
  - `docs/screens/RW-03-analyze.md`
  - `docs/pantheon-handoffs/RW-03-analyze/FRONTEND_CHANGE_SPEC.md`
- Verified the remote-visible request-pair publish commit:
  - `git -C ../front-ai-trading-system ls-remote --heads origin pkt-004-detail-fix`
  - `git -C ../front-ai-trading-system branch -r --contains e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f`
  - `git -C ../front-ai-trading-system show e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f:.coordination/requests/RW-03-analyze-ui-done.yaml`
  - `git -C ../front-ai-trading-system show e9f93f5fab05ede7fb56bddb33e171dfee0f9f6f:.coordination/requests/RW-03-analyze-frontend-feedback.yaml`
- Verified the reviewed UI implementation commit contents:
  - `git -C ../front-ai-trading-system ls-tree -r --name-only ef9b4d7b4f69ac829ea097fff0bef889d42e46dc -- .coordination/requests/RW-03-analyze-ui-done.yaml .coordination/requests/RW-03-analyze-frontend-feedback.yaml docs/pantheon-feedback/RW-03-analyze src/pages/research/ResearchAnalyze.tsx src/pages/research/ResearchAnalyzeDetail.tsx src/pages/research/types.ts src/pages/health/Health.tsx`
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest -q services/control-plane/bff/test_rw03_analyze_contract.py`
  - Result: `4 passed`
- Re-checked live OpenAPI publication and auth-gated behavior:
  - `curl -s http://127.0.0.1:18001/openapi.json | jq -r '.paths | keys[]' | rg '^/api/v1/research/analysis'`
  - operator-auth list/detail probes on `18001`
  - missing-auth and viewer-auth probes on `18001`
  - missing-detail probe on `18001`
- Accepted review evidence retained:
  - sibling front targeted eslint and `npm run build` passed on the published
    branch for the reviewed RW-03 slice

## Not Completed

- No deployed browser QA against a shared Pantheon environment was performed in
  this closeout sync
- The active operator-bff on `18001` did not expose a direct stale RW-03
  envelope during this closeout; stale behavior remains verified through the
  accepted auxiliary service-backed probe
