# RW-04 Experiment Launch Backend Delivery Note

## Status

`loop-complete`

## Summary

Pantheon synced the accepted `RW-04-experiment-launch` `ui-done` handoff and
paired `frontend-feedback` bundle from `ajoe734/front-ai-trading-system`
against the canonical experiment-launch contract, example payload, the sibling
front publication chain, and the current Pantheon BFF/runtime evidence.

The earlier RW-04 publication blocker is now resolved:

- reviewed UI transport commit:
  `f672af2c0019618ce05cf07c7ed50c65897e9fbb`
- current request-pair publish commit:
  `f00791b217e5550d80c1add72a8560b42bc3a056`
- `git ls-remote --heads origin pkt-004-detail-fix` now resolves to
  `f00791b217e5550d80c1add72a8560b42bc3a056`
- the publish commit republishes the canonical request pair with
  `source_commit: f672af2c0019618ce05cf07c7ed50c65897e9fbb`

Pantheon also reconfirmed that the RW-04 route family remains live and
contract-shaped:

- `POST /api/v1/experiments/launch`
- `GET /api/v1/experiments`
- `GET /api/v1/experiments/{experiment_id}`
- `POST /api/v1/experiments/{experiment_id}/cancel`

`python3 -m pytest -q services/control-plane/bff/test_rw04_experiment_launch_contract.py`
still passes (`21 passed`), and the live OpenAPI document on
`http://127.0.0.1:18001/openapi.json` still advertises the full
`/api/v1/experiments*` route family.

No new Pantheon endpoint, contract expansion, or client-side shadow state is
authorized or required in this cycle. The current RW-04 loop is complete apart
from deferred browser QA.

## Delivered Findings

### 1. The request pair is now replay-clean and Git-visible

Observed in the sibling front repo:

- `git show f00791b217e5550d80c1add72a8560b42bc3a056:.coordination/requests/RW-04-experiment-launch-ui-done.yaml`
  publishes
  `source_commit: f672af2c0019618ce05cf07c7ed50c65897e9fbb`
- the matching
  `.coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml`
  publishes the same real `source_commit`
- `git branch -r --contains f00791b217e5550d80c1add72a8560b42bc3a056`
  returns `origin/pkt-004-detail-fix`
- `git ls-tree -r --name-only f672af2c0019618ce05cf07c7ed50c65897e9fbb -- ...`
  returns the canonical request pair, the
  `docs/pantheon-feedback/RW-04-experiment-launch/*` bundle,
  `src/App.tsx`, `src/components/AppSidebar.tsx`,
  `src/components/WorkbenchBreadcrumb.tsx`, `src/lib/bffClient.ts`, and the
  six experiment files

Impact:

- Pantheon can now replay the returned RW-04 cycle from a truthful remote
  branch head
- the closeout record no longer points at the stale `6e17dd8`/`147297b`
  intermediate publication tuple

### 2. The reviewed UI transport commit remains contract-aligned

Observed in the accepted review packet:

- `ExperimentLaunch.tsx` continues to poll durable status through
  `GET /api/v1/experiments/{experiment_id}` and preserves a retry path after an
  initial detail-read failure
- `ExperimentRunHelpers.ts` only classifies route-not-live for
  `404 NOT_FOUND` and `404 ROUTE_NOT_FOUND`, preserving
  `404 OBJECT_NOT_FOUND` for missing experiment ids
- `ExperimentHistory.tsx` clears pagination history when `ticket_id` or
  `status` changes
- `ExperimentRunView.tsx` still gates cancel visibility on
  `allowedActions.canCancel` plus degradation semantics only
- the accepted front verification still records targeted eslint,
  `npx tsc --noEmit`, and `npm run build` passing on 2026-04-21

Impact:

- the reviewed RW-04 UI behavior remains aligned to the published acceptance
  rules
- the final publish commit only replay-cleans the request pair; it does not
  introduce a new UI divergence

### 3. Pantheon RW-04 routes remain live and contract-shaped

Observed in the current Pantheon workspace/runtime:

- `python3 -m pytest -q services/control-plane/bff/test_rw04_experiment_launch_contract.py`
  returned `21 passed`
- the live OpenAPI document on `http://127.0.0.1:18001/openapi.json` still
  lists:
  - `POST /api/v1/experiments/launch`
  - `GET /api/v1/experiments`
  - `GET /api/v1/experiments/{experiment_id}`
  - `POST /api/v1/experiments/{experiment_id}/cancel`
- the accepted review packet already captured live HTTP proof for queued,
  running, completed, failed, canceled, degraded, unavailable, and
  `OBJECT_NOT_FOUND` behavior on the published route family

Impact:

- no additional Pantheon runtime or contract follow-up remains for the current
  RW-04 packet scope

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Pantheon runtime route family: still live and verified
- Pantheon delivery completed:
  - re-confirmed the replay-clean `f672af2c -> f00791b` front publication chain
  - re-ran the targeted RW-04 contract slice in the current workspace
  - re-confirmed live OpenAPI route publication for the full
    `/api/v1/experiments*` family
- Front follow-up still required:
  - none for the current packet scope
- Current loop outcome: `loop-complete`

## Verification Performed

- Reviewed Pantheon-visible request artifacts:
  - `.coordination/requests/RW-04-experiment-launch-ui-done.yaml`
  - `.coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml`
- Reviewed the accepted Pantheon review packet:
  - `.coordination/reviews/RW-04-experiment-launch-review.md`
- Re-checked the canonical packet:
  - `docs/bff/RW-04-experiment-launch.md`
  - `docs/examples/RW-04-experiment-launch.json`
  - `docs/pantheon-handoffs/RW-04-experiment-launch/FRONTEND_CHANGE_SPEC.md`
- Verified the remote-visible request-pair publish commit:
  - `git -C ../front-ai-trading-system ls-remote --heads origin pkt-004-detail-fix`
  - `git -C ../front-ai-trading-system branch -r --contains f00791b217e5550d80c1add72a8560b42bc3a056`
  - `git -C ../front-ai-trading-system show f00791b217e5550d80c1add72a8560b42bc3a056:.coordination/requests/RW-04-experiment-launch-ui-done.yaml`
  - `git -C ../front-ai-trading-system show f00791b217e5550d80c1add72a8560b42bc3a056:.coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml`
- Verified the reviewed UI transport commit contents:
  - `git -C ../front-ai-trading-system ls-tree -r --name-only f672af2c0019618ce05cf07c7ed50c65897e9fbb -- .coordination/requests/RW-04-experiment-launch-ui-done.yaml .coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml docs/pantheon-feedback/RW-04-experiment-launch src/pages/research/ExperimentLaunch.tsx src/pages/research/ExperimentDetail.tsx src/pages/research/ExperimentHistory.tsx src/pages/research/ExperimentRunHelpers.ts src/pages/research/ExperimentRunView.tsx src/pages/research/ExperimentTypes.ts src/lib/bffClient.ts src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx`
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest -q services/control-plane/bff/test_rw04_experiment_launch_contract.py`
  - Result: `21 passed`
- Re-checked live OpenAPI publication:
  - `curl -s http://127.0.0.1:18001/openapi.json | tr ',' '\n' | rg '"/api/v1/experiments(/launch|/\{experiment_id\}|/\{experiment_id\}/cancel)?"'`
- Accepted review evidence retained:
  - sibling front targeted eslint, `npx tsc --noEmit`, and `npm run build`
    passed on 2026-04-21 for the reviewed RW-04 slice

## Not Completed

- No deployed browser QA against a shared Pantheon environment was performed in
  this closeout sync
