# BP5-LUV-004 Review Packet

## Date

2026-04-16

## Owner

Claude

## Reviewer

Codex

## Scope

Review the returned PKT-002 incident-detail Lovable loop against the packet
contract, screen spec, example payload, and the mirrored frontend
implementation before allowing `BP5-LUV-004` to move to `review_approved`.

## Returned Artifacts

- `.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
- `docs/pantheon-feedback/PKT-002-incident-detail/LOVABLE_CHANGE_FEEDBACK.md`
- `docs/pantheon-feedback/PKT-002-incident-detail/API_GAP_REQUESTS.json`
- `docs/pantheon-feedback/PKT-002-incident-detail/UI_DECISIONS.md`
- `docs/pantheon-feedback/PKT-002-incident-detail/QA_STATUS.md`

## Pantheon Verification

- Cross-checked the packet contract in:
  - `docs/screens/PKT-002-incident-detail.md`
  - `docs/bff/PKT-002-incident-detail.md`
  - `docs/examples/PKT-002-incident-detail.json`
  - `docs/pantheon-handoffs/PKT-002-incident-detail/FRONTEND_CHANGE_SPEC.md`
- Reviewed the mirrored frontend implementation in the sibling checkout:
  - `/home/edna/code/front-ai-trading-system/src/pages/operator/IncidentDetail.tsx`
  - `/home/edna/code/front-ai-trading-system/src/pages/operator/types.ts`
  - `/home/edna/code/front-ai-trading-system/src/lib/bffClient.ts`
  - `/home/edna/code/front-ai-trading-system/src/App.tsx`
- Confirmed the shared BFF client is used for the composed detail endpoint and
  that the page does not re-fetch individual incident surfaces.
- Re-ran targeted front-end validation successfully:
  - `npx eslint src/pages/operator/IncidentDetail.tsx src/pages/operator/types.ts src/lib/bffClient.ts src/App.tsx`
  - `npm run build`

## Findings

### 1. The Open Action Drawer CTA is still inert and the returned handoff overstates the integration boundary

The detail page renders a plain button labeled "Open Action Drawer" but does not
wire it to any drawer component or navigation path:

- `/home/edna/code/front-ai-trading-system/src/pages/operator/IncidentDetail.tsx:549-555`

The mirrored app also mounts the detail page at `/incidents/:incidentId`, not
the returned `/operator/incident/:incident_id` route:

- `/home/edna/code/front-ai-trading-system/src/App.tsx:128-130`
- `.coordination/requests/PKT-002-incident-detail-ui-done.yaml:52-55`

This blocks approval because the shipped UI does not yet enter the action-drawer
surface from the detail page, and the synced evidence bundle claims that it
already does.

### 2. The staleness banner acceptance item is not met

Pantheon requires a non-dismissable staleness banner whenever `meta.staleness`
is present:

- `docs/bff/PKT-002-incident-detail.md:60-61`
- `.coordination/requests/PKT-002-incident-detail-ui-done.yaml:44-45`

The mirrored frontend only derives the `stale` banner variant when a surface is
already degraded and `served_from` is `cache` or `reconstructed`; otherwise the
banner state falls back to `none`:

- `/home/edna/code/front-ai-trading-system/src/pages/operator/IncidentDetail.tsx:201-205`
- `/home/edna/code/front-ai-trading-system/src/lib/degradationBanner.ts:260-287`

The kill-switch panel itself also has no dedicated staleness banner render path:

- `/home/edna/code/front-ai-trading-system/src/pages/operator/IncidentDetail.tsx:469-517`

### 3. The kill-switch panel does not render `active_commands[]`

The contract requires the kill-switch panel to render `active_commands[]` in the
OK state:

- `docs/screens/PKT-002-incident-detail.md:18`
- `docs/pantheon-handoffs/PKT-002-incident-detail/FRONTEND_CHANGE_SPEC.md:102-105`

The mirrored implementation renders `status`, `last_confirmed_at`, and
`last_triggered_at`, but no `active_commands` list:

- `/home/edna/code/front-ai-trading-system/src/pages/operator/IncidentDetail.tsx:480-505`

### 4. The returned QA/evidence bundle is not rerunnable against the mirrored tree

`QA_STATUS.md` says eslint was run against three component files that do not
exist in the mirrored repo:

- `docs/pantheon-feedback/PKT-002-incident-detail/QA_STATUS.md:9`
- `docs/pantheon-feedback/PKT-002-incident-detail/UI_DECISIONS.md:4-6`

Re-running the exact command from `QA_STATUS.md` fails with:

> No files matching the pattern "src/components/operator/AffectedBindings.tsx" were found.

This means the current Pantheon-side evidence bundle cannot be approved as an
accurate description of the mirrored implementation.

## Decision

`BP5-LUV-004` is **not approved yet**.

The core contract usage is close: the page uses the single composed endpoint,
the prior BFF blocker is resolved, and the mirrored repo still builds. The loop
cannot move to `review_approved` until the owner completes another UI cycle on
the same Pantheon contract and republishes evidence that matches the actual
frontend tree.

Minimum follow-up needed before re-review:

1. Wire the **Open Action Drawer** CTA into the delivered drawer surface, or
   explicitly document and republish a deferred integration boundary if that is
   intentionally out of scope.
2. Implement the required staleness banner / staleness-note behavior for
   `meta.staleness`.
3. Render `data.kill_switch.active_commands[]` in the kill-switch panel.
4. Correct the returned QA and feedback artifacts so the file paths, route
   boundary, and drawer-integration claims match the mirrored frontend.
