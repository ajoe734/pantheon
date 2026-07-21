# PKT-004 Capital / Binding Drilldowns Review Packet

## Date

2026-04-17

## Reviewer

Codex

## Findings

### 1. High: the returned front handoff is still not replay-clean

Pantheon's current review target is split across two different front-repo
transport anchors:

- `.coordination/requests/PKT-004-capital-binding-drilldowns-ui-done.yaml`
  now points to
  `source_commit: 6c6b4e884c8eb6537b6bf46b59971aaee852ed7d`
- `.coordination/requests/PKT-004-capital-binding-drilldowns-frontend-feedback.yaml`
  still points to
  `source_commit: 46a7a947fee0a375007115df100bac1d84e06e82`

That pair is not replayable under Pantheon's `payload_path + source_commit`
rule:

- `git ls-tree -r --name-only 46a7a947fee0a375007115df100bac1d84e06e82`
  contains only:
  - `.coordination/requests/PKT-004-capital-binding-drilldowns-ui-done.yaml`
  - `.coordination/requests/PKT-004-capital-binding-drilldowns-frontend-feedback.yaml`
  - `src/App.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/persona/types.ts`
- that older commit omits all four capital/binding page files claimed by the
  packet:
  - `src/pages/persona/CapitalPoolList.tsx`
  - `src/pages/persona/CapitalPoolDetail.tsx`
  - `src/pages/persona/BindingList.tsx`
  - `src/pages/persona/BindingDetail.tsx`
- `git ls-tree -r --name-only 6c6b4e884c8eb6537b6bf46b59971aaee852ed7d`
  does contain the four page files plus the request pair
- but neither advertised commit contains the published feedback bundle paths
  referenced by `frontend-feedback`:
  - `docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/UI_DECISIONS.md`
  - `docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/QA_STATUS.md`

Impact:

- the Git-visible transport tuple is still internally inconsistent
- the feedback bundle is still not replayable from a committed front source
- Pantheon cannot mark the PKT-004 capital-binding loop accepted or closed yet

## Verified Positives

- The reviewed UI implementation itself is aligned to the published contract
  and example payload:
  - `GET /api/v1/capital-pools`
  - `GET /api/v1/capital-pools/{pool_id}`
  - `GET /api/v1/bindings`
  - `GET /api/v1/bindings/{binding_id}`
- The sibling front repo current tree passes static verification for the
  touched PKT-004 slice:
  - `npm run build`
  - `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/persona/types.ts src/pages/persona/CapitalPoolList.tsx src/pages/persona/CapitalPoolDetail.tsx src/pages/persona/BindingList.tsx src/pages/persona/BindingDetail.tsx`
- Pantheon's current backend contract check passes on the unchanged PKT-004
  route set:
  - `pytest -q services/control-plane/bff/test_pkt004_capital_binding_drilldowns_contract.py`
    -> `2 passed`
- No new Pantheon BFF gap was found in this re-review. The remaining blocker is
  front-owned publication truth, not endpoint behavior.

## Decision

`PKT-004-capital-binding-drilldowns` remains **followup-required**.

The implementation is contract-correct and the current static checks are green,
but the front-owned request pair and feedback bundle still do not resolve to a
single replayable Git-visible source commit.

## Required Follow-up

1. Publish one front-repo commit that contains all of the following together:
   - the PKT-004 capital/binding request pair
   - the `docs/pantheon-feedback/PKT-004-capital-binding-drilldowns/` bundle
   - the claimed UI files for CP-01 to CP-04
2. Update both request bodies so
   `ui-done.source_commit` and `frontend-feedback.source_commit` point to that
   same truthful publication commit.
3. Redispatch Pantheon review on the unchanged PKT-004 contract after the
   replay-clean publication exists.

## Residual Risk

- Live browser QA against a running Pantheon deployment is still pending, but it
  is no longer the primary blocker in this review cycle.
- Pantheon should re-check the next front publication against the exact commit
  advertised in both request payloads rather than the sibling working tree.

## 2026-04-19 Closeout Addendum

Pantheon re-verified the PKT-004 packet after the front repo published a
single replay-clean transport bundle.

- The request pair is now Git-visible at
  `c9c1e20726bfc1d35f3ddcbb4f7552859f1d8f5d`.
- Both request bodies now point `source_commit` at
  `77ab876e05dbb206f4fd4abc39051df86f6127c2`, whose tree contains the capital
  and binding drilldown UI plus the returned feedback bundle.
- Pantheon's contract slice remains green:
  `python3 -m pytest services/control-plane/bff/test_pkt004_capital_binding_drilldowns_contract.py -q`
  -> `2 passed`.

## Final Decision

**APPROVED.**

Residual live-browser QA remains non-blocking.
