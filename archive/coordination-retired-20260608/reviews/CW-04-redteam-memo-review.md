# CW-04 Red-team Memo Review Packet

## Date

2026-04-23

## Reviewer

Codex

## Findings

1. No blocking findings remain. The prior replay blocker is resolved:
   `origin/pkt-004-detail-fix` now resolves to
   `675f1cc59be537455e776113be9ad8a45fa44208`, and that publish commit
   republishes `.coordination/requests/CW-04-redteam-memo-ui-done.yaml`,
   `.coordination/requests/CW-04-redteam-memo-frontend-feedback.yaml`, and
   `docs/pantheon-feedback/CW-04-redteam-memo/*` with truthful
   `source_commit: c94f63082eae1667ed919353d62c85180d7bafba`. The targeted
   sibling front working tree is clean for the CW-04 paths, so the request
   pair is now replay-clean from Git.

## Verified Positives

- The reviewed UI transport commit `c94f63082eae1667ed919353d62c85180d7bafba`
  contains the claimed CW-04 route wiring and screens in `src/App.tsx`,
  `src/components/AppSidebar.tsx`,
  `src/components/WorkbenchBreadcrumb.tsx`, `src/lib/bffClient.ts`,
  `src/pages/consultation/types.ts`,
  `src/pages/consultation/RedTeamMemoList.tsx`, and
  `src/pages/consultation/RedTeamMemoDetail.tsx`.
- The current UI remains aligned with the live CW-04 read boundary. It reads
  only `GET /api/v1/consult/memos` and
  `GET /api/v1/consult/memos/{memo_id}` through `consultMemoApi`, sends only
  the published list query params, keeps row navigation on backend
  `route_href`, uses only `evidence_refs[].link` for evidence navigation, and
  renders `session_to_memo_mapping` verbatim.
- The strict detail validator still expects the full published detail shape,
  and Pantheon's degraded detail branch still keeps that full envelope while
  forcing `allowedActions.canInitiateGovernanceReview` to `false`; only the
  unavailable branch suppresses memo content.
- Pantheon's governance CTA remains fully backend-owned in the current
  implementation. `_cw04_allowed_actions()` and `_cw04_memo_projection()` in
  `services/control-plane/bff/main.py` still force the CTA off outside
  `state = ok` and compute the ok-state gate from backend-owned lifecycle,
  authority, target, suppression, withdrawal, active review, and supported
  target type checks.
- The targeted metadata diff from
  `c94f63082eae1667ed919353d62c85180d7bafba` to
  `675f1cc59be537455e776113be9ad8a45fa44208` over the CW-04 paths only touches
  the two request files plus
  `docs/pantheon-feedback/CW-04-redteam-memo/API_GAP_REQUESTS.json` and
  `docs/pantheon-feedback/CW-04-redteam-memo/LOVABLE_CHANGE_FEEDBACK.md`, so
  the accepted UI transport snapshot remains the reviewed CW-04 source commit.
- `python3 -m pytest services/control-plane/bff/test_cw04_redteam_memo_contract.py -q`
  passed with `7 passed`.

## Decision

APPROVED. `CW-04-redteam-memo` is loop-complete for the current closeout
scope.

The returned request pair is now replay-clean, the frontend implementation
stays within the published read boundary, degraded detail handling remains
contract-correct, and Pantheon's CW-04 contract slice is still green.
Deployed-environment browser QA remains deferred and non-blocking outside this
closeout.

## Verification

- `git -C ../front-ai-trading-system rev-parse origin/pkt-004-detail-fix`
- `git -C ../front-ai-trading-system show --stat --summary --oneline c94f63082eae1667ed919353d62c85180d7bafba`
- `git -C ../front-ai-trading-system show --stat --summary --oneline 675f1cc59be537455e776113be9ad8a45fa44208`
- `git -C ../front-ai-trading-system show 675f1cc59be537455e776113be9ad8a45fa44208:.coordination/requests/CW-04-redteam-memo-ui-done.yaml`
- `git -C ../front-ai-trading-system show 675f1cc59be537455e776113be9ad8a45fa44208:.coordination/requests/CW-04-redteam-memo-frontend-feedback.yaml`
- `git -C ../front-ai-trading-system ls-tree -r --name-only 675f1cc59be537455e776113be9ad8a45fa44208 -- .coordination/requests/CW-04-redteam-memo-ui-done.yaml .coordination/requests/CW-04-redteam-memo-frontend-feedback.yaml docs/pantheon-feedback/CW-04-redteam-memo/LOVABLE_CHANGE_FEEDBACK.md docs/pantheon-feedback/CW-04-redteam-memo/API_GAP_REQUESTS.json docs/pantheon-feedback/CW-04-redteam-memo/UI_DECISIONS.md docs/pantheon-feedback/CW-04-redteam-memo/QA_STATUS.md`
- `git -C ../front-ai-trading-system diff --name-only c94f63082eae1667ed919353d62c85180d7bafba..675f1cc59be537455e776113be9ad8a45fa44208 -- .coordination/requests/CW-04-redteam-memo-ui-done.yaml .coordination/requests/CW-04-redteam-memo-frontend-feedback.yaml docs/pantheon-feedback/CW-04-redteam-memo`
- `git -C ../front-ai-trading-system status --short -- src/pages/consultation/RedTeamMemoList.tsx src/pages/consultation/RedTeamMemoDetail.tsx .coordination/requests/CW-04-redteam-memo-ui-done.yaml .coordination/requests/CW-04-redteam-memo-frontend-feedback.yaml docs/pantheon-feedback/CW-04-redteam-memo`
- `python3 -m pytest services/control-plane/bff/test_cw04_redteam_memo_contract.py -q`
