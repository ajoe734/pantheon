# AG-DYNUI-FULL-008 - Design Parity Hardening

Status: ready for fleet execution

Recommended owner: Copilot or Codex

Recommended reviewer: Codex

Do not assign to Claude or Claude2 while their quota is exhausted.

## Goal

Verify that the live Agora Trading Room implementation matches the available
design source closely enough for production use, and fix concrete UI gaps found
by that verification.

This is not a static-page conversion. The live UI must remain BFF-driven and
must keep the dynamic workflow proven by AG-DYNUI-FULL-006.

## Current Design Source State

The user referenced:

- `/home/lupin/code/pantheon/AI%20Trading%20Desk%20Design.zip`

That exact file is not present in the current repository root. The available
local design pack is:

- `/home/lupin/code/pantheon/Pantheon_Agora_Design_Closure_Pack_2026-06-20.zip`

If the missing `AI Trading Desk Design.zip` is later provided, this task must
use it as the higher-priority source. Until then, use the available design
closure pack and record the missing-file constraint explicitly.

## Owned Scope

Primary repository:

- `ajoe734/execute-plans`

Likely paths:

- `src/agora/pages/trading-room/TradingRoomPage.tsx`
- `src/agora/trading-room/WorkspaceProposalPreview.tsx`
- `src/agora/trading-room/WorkspaceGridEditor.tsx`
- `src/agora/trading-room/WorkspaceWidgetRevisionDrawer.tsx`
- `src/agora/widgets/ChartSpecRenderer.tsx`
- `e2e/agora-winner-branch-hosted.spec.ts`

## Do Not Change

- Do not reintroduce mocked Agora data or `page.route` fixtures.
- Do not remove the live workflow gate.
- Do not replace dynamic BFF state with static screenshots.
- Do not relax auth, CORS, ETag, or optimistic concurrency behavior.
- Do not route live order/broker/capital-binding functionality from Agora.

## Required Audit

1. Extract or inspect the available design closure pack.
2. Produce a parity matrix covering at minimum:
   - `/agora/trading-room` proposal preview.
   - accepted workspace shell.
   - grid edit and unsaved/saved state.
   - widget revision drawer.
   - version history and rollback state.
   - desktop and mobile viewports.
3. Compare visual hierarchy, spacing, tab treatment, proposal cards, side panel,
   empty states, overflow behavior, and responsive behavior.
4. Identify concrete UI defects, not vague "looks different" claims.
5. Fix only scoped production defects.

## Minimum Known Risks To Check

- Proposal preview cards may overflow horizontally in narrow desktop/mobile
  captures.
- Badge/pill labels may wrap vertically in dense cards.
- First viewport should clearly show Agora as the page surface, not the old
  white Trading Desk shell.
- Empty states must not hide actionable proposal/workspace controls.
- Dynamic data labels must not overlap or clip.

## Acceptance Criteria

1. A design-parity matrix is committed.
2. Any fixed UI files have focused unit or browser coverage.
3. Hosted live AG-DYNUI-FULL-006 gate still passes on desktop and mobile.
4. New screenshots prove the relevant design states at desktop and mobile
   widths.
5. execute-plans PR checks pass.
6. PR is merged.
7. Dev FE deploy is completed if runtime UI code changed.
8. Hosted `/agora/trading-room` proof is recorded after deploy.

## Suggested Validation

```bash
npm run lint
npm run test
npm run build
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
PANTHEON_BFF_TENANT_ID=pantheon-dev \
npx playwright test e2e/agora-winner-branch-hosted.spec.ts --project=chromium --reporter=list
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
PANTHEON_BFF_TENANT_ID=pantheon-dev \
npx playwright test e2e/agora-winner-branch-hosted.spec.ts --project=mobile-chromium --reporter=list
```
