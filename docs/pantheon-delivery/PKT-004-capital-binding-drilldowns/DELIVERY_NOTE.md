# PKT-004 Capital / Binding Drilldowns Backend Delivery Note

## Status

`followup-required`

## Summary

Pantheon refreshed the PKT-004 delivery bundle after aligning the CP-03 binding
list route with the published `persona_id` contract. The BFF now accepts
`persona_id` on `GET /api/v1/bindings`, applies that filter in the read store
before returning rows, and preserves canonical `validity` values during binding
projection so combined CP-03 filters behave consistently.

The overall feature is still `followup-required`, but the remaining blockers are
front-owned:

1. the advertised `source_commit` still does not contain the four CP page files
   named in the packet `changed_files`, so the UI transport is not replayable
2. the published QA note is stale because targeted ESLint fails on
   `src/auth/AuthProvider.tsx`

## Verified Pantheon Contract

- `GET /api/v1/capital-pools`
- `GET /api/v1/capital-pools/{pool_id}`
- `GET /api/v1/bindings`
- `GET /api/v1/bindings/{binding_id}`

No new endpoint, no shadow state, and no client-side join expansion is
authorized in this follow-up.

## Verified UI Alignment

- `BindingList` keeps Pantheon-owned filter state in the URL query string and
  forwards `persona_id` and `capital_pool_id` through `personaDrilldownApi`
  instead of filtering rows client-side.
- `CapitalPoolDetail` renders embedded `bindings[]` only when Pantheon returns
  them and otherwise pivots to the filtered binding list instead of fabricating
  a join locally.
- `BindingDetail` links to the existing Persona Management and capital-pool
  drilldowns without inventing new backend surface area.
- Explicit loading, empty, error, contract-mismatch, permission, and staleness
  states are present across the reviewed Capital/Binding pages in the sibling
  working tree.
- `AuthProvider` now writes and clears `pantheon_operator_token` during session
  bootstrap, sign-in, and sign-out, which is the right integration direction
  for the shared BFF client.

## Verified BFF Behavior

Pantheon ran a targeted local smoke against the live route code with the seeded
read store:

- `GET /api/v1/capital-pools` returned `200`
- `GET /api/v1/capital-pools?status=active` returned `200`
- `GET /api/v1/capital-pools/pool-main` returned `200`
- `GET /api/v1/bindings` returned `200`
- `GET /api/v1/bindings?capital_pool_id=pool-main` returned `200`
- `GET /api/v1/bindings?persona_id=persona-alpha&capital_pool_id=pool-main&validity=active`
  returned `200` with `binding-042`
- `GET /api/v1/bindings?persona_id=persona-does-not-exist` returned `200` with
  an empty `data[]`
- `GET /api/v1/bindings/binding-042` returned `200`
- `viewer` access to `GET /api/v1/capital-pools` returned
  `403 INSUFFICIENT_ROLE`
- missing auth on `GET /api/v1/capital-pools` returned `401 INVALID_TOKEN`

The list/detail routes, response envelopes, `meta.staleness`, read RBAC, and
the published CP-03 filter rail are now behaving as published in the local BFF
implementation.

## Findings Requiring Another Cycle

### 1. The advertised transport commit still omits the four CP page files

The current sibling request pair is still advertising
`source_commit: 46a7a947fee0a375007115df100bac1d84e06e82`.

But that advertised source commit still does not contain the files that make up
the reviewed UI slice:

- `src/pages/persona/CapitalPoolList.tsx`
- `src/pages/persona/CapitalPoolDetail.tsx`
- `src/pages/persona/BindingList.tsx`
- `src/pages/persona/BindingDetail.tsx`

`git ls-tree -r --name-only 46a7a947fee0a375007115df100bac1d84e06e82` shows
only these tracked PKT-004 paths from the claimed UI bundle:

- `src/App.tsx`
- `src/auth/AuthProvider.tsx`
- `src/lib/bffClient.ts`
- `src/pages/persona/types.ts`

Impact:

- the packet is still not replayable from its own advertised source commit
- the reviewed UI state lives partly outside the Git-visible tree
- the next front-owned cycle must publish the four CP page files together with
  the request pair in a single replayable commit

### 2. The published QA evidence no longer matches the checked-in tree

The sibling `QA_STATUS.md` claims targeted ESLint passed, but the current tree
fails the exact PKT-004 lint slice:

- `/home/edna/code/front-ai-trading-system/src/auth/AuthProvider.tsx:72`
  `no-useless-catch`

Impact:

- Pantheon cannot treat the published QA bundle as authoritative evidence for
  this cycle
- the next front-owned cycle must republish QA evidence after restoring green
  targeted ESLint

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Pantheon endpoints: unchanged
- Pantheon API gap: CP-03 `persona_id` filtering aligned to the published
  contract
- Front follow-up required:
  - publish the four CP page files from the same replayable commit referenced by
    `ui-done` and `frontend-feedback`
  - restore green targeted ESLint and republish matching QA evidence
- Pantheon follow-up completed in this cycle:
  - `GET /api/v1/bindings` now accepts `persona_id` and forwards it into the
    BFF read store
  - `services/control-plane/bff/read_store.py` now filters bindings by
    `persona_id` before returning rows
  - canonical binding projection now preserves `validity`, keeping combined
    CP-03 filtering aligned across canonical and fallback reads

## Verification Performed

- Reviewed the current sibling front repo coordination payloads:
  - `../front-ai-trading-system/.coordination/requests/PKT-004-capital-binding-drilldowns-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-004-capital-binding-drilldowns-frontend-feedback.yaml`
- Reviewed the sibling front repo support bundle:
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/QA_STATUS.md`
- Re-checked the Pantheon packet:
  - `docs/bff/PKT-004-capital-binding-drilldowns.md`
  - `docs/examples/PKT-004-capital-binding-drilldowns.json`
  - `docs/screens/PKT-004-capital-binding-drilldowns.md`
  - `docs/pantheon-handoffs/PKT-004-capital-binding-drilldowns/FRONTEND_CHANGE_SPEC.md`
- Reviewed the sibling front repo implementation paths:
  - `src/App.tsx`
  - `src/auth/AuthProvider.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/persona/types.ts`
  - `src/pages/persona/CapitalPoolList.tsx`
  - `src/pages/persona/CapitalPoolDetail.tsx`
  - `src/pages/persona/BindingList.tsx`
  - `src/pages/persona/BindingDetail.tsx`
- Ran sibling front repo validation:
  - `npm run build`
  - `npx eslint src/App.tsx src/auth/AuthProvider.tsx src/lib/bffClient.ts src/pages/persona/types.ts src/pages/persona/CapitalPoolList.tsx src/pages/persona/CapitalPoolDetail.tsx src/pages/persona/BindingList.tsx src/pages/persona/BindingDetail.tsx`
  - Result: build passed, ESLint failed on `src/auth/AuthProvider.tsx:72`
- Refreshed the Pantheon BFF contract alignment:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/read_store.py`
  - `services/control-plane/bff/test_pkt004_capital_binding_drilldowns_contract.py`
  - `services/control-plane/bff/BFF_API_CONTRACT.md`
  - `services/control-plane/bff/APP_001C_QUERY_CONTRACT_OUTLINE.md`
- Ran targeted Pantheon BFF verification:
  - `pytest -q services/control-plane/bff/test_pkt004_capital_binding_drilldowns_contract.py`
  - `pytest -q services/control-plane/bff/test_w4_remaining_catalog.py services/control-plane/bff/test_pkt004_capital_binding_drilldowns_contract.py`
  - Result: all targeted CP-03 and existing catalog smoke checks passed

## Not Completed

- No live browser QA against a running Pantheon deployment was performed in
  this review cycle
- No new sibling front repo replay commit or refreshed QA bundle was produced
  in this backend-owned cycle
