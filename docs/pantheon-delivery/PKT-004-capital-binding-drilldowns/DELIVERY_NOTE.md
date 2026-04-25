# PKT-004 Capital / Binding Drilldowns Backend Delivery Note

## Status

`followup-required`

## Summary

Pantheon re-reviewed the returned PKT-004 capital/binding UI against the
published contract, example payload, sibling front checkout, and the current
Pantheon contract test for the four CP routes.

The implementation itself is now in the right place:

- CP-01 to CP-04 use the published capital-pool and binding read routes only
- the current sibling front tree builds successfully
- targeted ESLint passes on the touched PKT-004 files
- Pantheon's targeted PKT-004 contract test passes on the unchanged backend
- no new Pantheon API gap or contract change is required

The loop is still `followup-required` because the front-owned publication is
not replay-clean yet:

1. `ui-done` now points to
   `source_commit: 6c6b4e884c8eb6537b6bf46b59971aaee852ed7d`
2. `frontend-feedback` still points to
   `source_commit: 46a7a947fee0a375007115df100bac1d84e06e82`
3. the older feedback commit omits the four CP page files
4. neither advertised commit contains the referenced
   `docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/` bundle

Under Pantheon's replay contract, the request pair and feedback bundle must be
Git-visible from one truthful front publication commit before this packet can
move forward.

## Verified Pantheon Contract

- `GET /api/v1/capital-pools`
- `GET /api/v1/capital-pools/{pool_id}`
- `GET /api/v1/bindings`
- `GET /api/v1/bindings/{binding_id}`

No new endpoint, no shadow state, and no client-side join expansion is
authorized in this follow-up.

## Verified UI Alignment

- `src/pages/persona/CapitalPoolList.tsx` fetches CP-01 through the shared
  `personaDrilldownApi` and renders explicit loading, empty, error, and
  contract-mismatch states.
- `src/pages/persona/CapitalPoolDetail.tsx` uses the documented detail route
  and pivots to the filtered binding list instead of fabricating a client-side
  join.
- `src/pages/persona/BindingList.tsx` forwards `persona_id` and
  `capital_pool_id` as Pantheon-owned query parameters instead of filtering
  returned rows locally.
- `src/pages/persona/BindingDetail.tsx` links back into the existing Persona
  Management and Capital Pool detail surfaces without inventing new backend
  surface area.
- `src/lib/bffClient.ts` wires the four PKT-004 reads onto the existing shared
  BFF client and keeps filter/query behavior inside the documented route set.

## Verified Acceptance Step

Pantheon reran the next static integration/acceptance checks against the
current sibling front tree and the current Pantheon backend tree:

- sibling front repo:
  - `npm run build`
  - `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/persona/types.ts src/pages/persona/CapitalPoolList.tsx src/pages/persona/CapitalPoolDetail.tsx src/pages/persona/BindingList.tsx src/pages/persona/BindingDetail.tsx`
  - result: passed
- Pantheon backend:
  - `pytest -q services/control-plane/bff/test_pkt004_capital_binding_drilldowns_contract.py`
  - result: `2 passed`

These checks confirm the current implementation slice is contract-aligned and
locally healthy. The blocker is now transport truth, not route behavior.

## Findings Requiring Another Cycle

### 1. The canonical request pair still disagrees on `source_commit`

The current sibling request bodies are committed with different source anchors:

- `ui-done` -> `6c6b4e884c8eb6537b6bf46b59971aaee852ed7d`
- `frontend-feedback` -> `46a7a947fee0a375007115df100bac1d84e06e82`

Pantheon cannot treat that as one replayable handoff.

### 2. The feedback bundle is still not Git-visible from either advertised commit

`frontend-feedback` references:

- `docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/LOVABLE_CHANGE_FEEDBACK.md`
- `docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/API_GAP_REQUESTS.json`
- `docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/UI_DECISIONS.md`
- `docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/QA_STATUS.md`

But `git ls-tree` on both advertised source commits shows that bundle is still
absent from committed front history.

### 3. The stale feedback commit still omits the CP page files

The older advertised feedback commit `46a7a947fee0a375007115df100bac1d84e06e82`
contains only:

- the request pair
- `src/App.tsx`
- `src/lib/bffClient.ts`
- `src/pages/persona/types.ts`

It omits:

- `src/pages/persona/CapitalPoolList.tsx`
- `src/pages/persona/CapitalPoolDetail.tsx`
- `src/pages/persona/BindingList.tsx`
- `src/pages/persona/BindingDetail.tsx`

So the current `frontend-feedback` transport anchor is still untruthful for the
reviewed UI slice.

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Pantheon endpoints: unchanged
- Pantheon API gap: none in this cycle
- Front follow-up required:
  - publish one Git-visible front commit containing the request pair, feedback
    bundle, and claimed PKT-004 UI files
  - point both request bodies at that same truthful `source_commit`
  - redispatch Pantheon review on the unchanged PKT-004 contract

## Verification Performed

- Reviewed the sibling front repo coordination payloads:
  - `../front-ai-trading-system/.coordination/requests/PKT-004-capital-binding-drilldowns-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-004-capital-binding-drilldowns-frontend-feedback.yaml`
- Reviewed the sibling front repo implementation paths:
  - `src/App.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/persona/types.ts`
  - `src/pages/persona/CapitalPoolList.tsx`
  - `src/pages/persona/CapitalPoolDetail.tsx`
  - `src/pages/persona/BindingList.tsx`
  - `src/pages/persona/BindingDetail.tsx`
- Verified the current front transport anchors with Git object lookup:
  - `git show HEAD:.coordination/requests/PKT-004-capital-binding-drilldowns-ui-done.yaml`
  - `git show HEAD:.coordination/requests/PKT-004-capital-binding-drilldowns-frontend-feedback.yaml`
  - `git ls-tree -r --name-only 46a7a947fee0a375007115df100bac1d84e06e82 -- ...`
  - `git ls-tree -r --name-only 6c6b4e884c8eb6537b6bf46b59971aaee852ed7d -- ...`
- Ran sibling front repo validation:
  - `npm run build`
  - `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/persona/types.ts src/pages/persona/CapitalPoolList.tsx src/pages/persona/CapitalPoolDetail.tsx src/pages/persona/BindingList.tsx src/pages/persona/BindingDetail.tsx`
  - result: passed
- Re-checked the Pantheon packet:
  - `docs/bff/PKT-004-capital-binding-drilldowns.md`
  - `docs/examples/PKT-004-capital-binding-drilldowns.json`
  - `docs/screens/PKT-004-capital-binding-drilldowns.md`
  - `docs/pantheon-handoffs/PKT-004-capital-binding-drilldowns/FRONTEND_CHANGE_SPEC.md`
- Ran the current Pantheon contract test:
  - `pytest -q services/control-plane/bff/test_pkt004_capital_binding_drilldowns_contract.py`
  - result: `2 passed`

## Not Completed

- No live browser QA against a running Pantheon deployment was performed in this
  review cycle.
- No new front-repo publication was produced from Pantheon. The remaining work
  is front-owned and transport-related.
