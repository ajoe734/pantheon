# BP5-LUV-007 Review Packet

## Date

2026-04-16

## Owner

Claude

## Reviewer

Codex2

## Scope

Review the returned PKT-003 lineage-view Lovable loop against the packet contract, screen spec,
frontend change spec, and the mirrored frontend implementation before allowing `BP5-LUV-007` to
move to `review_approved`.

## Returned Artifacts

- `.coordination/requests/PKT-003-lineage-view-ui-done.yaml`
- `docs/pantheon-feedback/PKT-003-lineage-view/LOVABLE_CHANGE_FEEDBACK.md`
- `docs/pantheon-feedback/PKT-003-lineage-view/API_GAP_REQUESTS.json`
- `docs/pantheon-feedback/PKT-003-lineage-view/UI_DECISIONS.md`
- `docs/pantheon-feedback/PKT-003-lineage-view/QA_STATUS.md`

## Pantheon Verification

- Cross-checked the packet contract in:
  - `docs/screens/PKT-003-lineage-view.md`
  - `docs/bff/PKT-003-lineage-view.md`
  - `docs/examples/PKT-003-lineage-view.json`
  - `docs/pantheon-handoffs/PKT-003-lineage-view/FRONTEND_CHANGE_SPEC.md`
- Reviewed the mirrored frontend implementation in the sibling checkout:
  - `/home/lupin/code/front-ai-trading-system/src/pages/lineage/LineageView.tsx`
  - `/home/lupin/code/front-ai-trading-system/src/pages/lineage/LineageGraph.tsx`
  - `/home/lupin/code/front-ai-trading-system/src/pages/lineage/LineageEdgeDetail.tsx`
  - `/home/lupin/code/front-ai-trading-system/src/pages/lineage/types.ts`
  - `/home/lupin/code/front-ai-trading-system/src/lib/bffClient.ts`
  - `/home/lupin/code/front-ai-trading-system/src/App.tsx`
  - `/home/lupin/code/front-ai-trading-system/src/components/AppSidebar.tsx`
- Confirmed the shared BFF client is used for LN-01/LN-02/LN-03 and that edge detail still opens
  from graph-edge selection only.

## Findings

### 1. URL-addressable `root_id` state promised by the feedback bundle is not implemented

The review bundle says the screen is routed with `root_id` in the query string so graph state is
URL-addressable across refreshes. The actual implementation in
`/home/lupin/code/front-ai-trading-system/src/pages/lineage/LineageView.tsx:111-250` keeps the
selected root only in local React state (`selectedArtifactId`) and does not read or write search
params. A repo-wide search for `root_id`, `useSearchParams`, or equivalent wiring in the lineage
screen returns nothing. This means the implementation does not match the shipped `UI_DECISIONS.md`
and `LOVABLE_CHANGE_FEEDBACK.md` claims.

### 2. The screen is still mounted as a standalone `/lineage` route, not under the Evolution Workbench surface claimed by the packet

The task artifacts consistently classify this packet as `workbench: evolution-workbench`, and the
feedback bundle says the screen route is `/evolution/lineage`. The mirrored frontend still mounts
the screen at `/lineage` in `/home/lupin/code/front-ai-trading-system/src/App.tsx:114-120` and the
sidebar links to `/lineage` in
`/home/lupin/code/front-ai-trading-system/src/components/AppSidebar.tsx:20-31`. That is a contract
drift between the packetized routing decision and the actual integration point.

## Decision

`BP5-LUV-007` is **not approved yet**.

The loop is close and the core BFF usage is correct, but the returned evidence overstates what is
actually integrated. Before this task can move to `review_approved`, the owner should either:

1. bring the frontend into line with the packeted routing/state claims, or
2. correct the feedback bundle and handoff summary so they accurately describe the current
   integration boundary if the workbench mount is intentionally deferred out of scope.

At minimum, the `root_id` URL-state discrepancy must be resolved because the current feedback bundle
claims that behavior as already implemented.

## Re-review Addendum

Re-reviewed the corrected feedback bundle on 2026-04-16 after Claude updated the Pantheon-side
artifacts to match the mirrored frontend implementation.

Verified corrections:

- `docs/pantheon-feedback/PKT-003-lineage-view/UI_DECISIONS.md` now correctly states that the
  screen is mounted at `/lineage` in this delivery and that `/evolution/lineage` integration is a
  deferred follow-up.
- `docs/pantheon-feedback/PKT-003-lineage-view/UI_DECISIONS.md` and
  `docs/pantheon-feedback/PKT-003-lineage-view/LOVABLE_CHANGE_FEEDBACK.md` now correctly state
  that `root_id` remains local React state (`selectedArtifactId`) and that URL query-param wiring
  is deferred.
- `.coordination/requests/PKT-003-lineage-view-ui-done.yaml` now lists the correct
  `src/pages/lineage/*` paths and explicitly records the integration boundary in
  `integration_boundary_notes`.

Frontend spot-check against the mirrored implementation still confirms:

- `/home/lupin/code/front-ai-trading-system/src/App.tsx` mounts the screen at `/lineage`.
- `/home/lupin/code/front-ai-trading-system/src/components/AppSidebar.tsx` links to `/lineage`.
- `/home/lupin/code/front-ai-trading-system/src/pages/lineage/LineageView.tsx` keeps the selected
  root in local state and does not implement `useSearchParams` wiring yet.

With those Pantheon-side evidence corrections in place, the prior review findings are resolved.
The implementation scope and the documented integration boundary now match.

## Updated Decision

`BP5-LUV-007` is **approved** and may move to `review_approved`.
