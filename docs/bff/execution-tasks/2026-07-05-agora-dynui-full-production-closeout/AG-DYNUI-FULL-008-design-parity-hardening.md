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

**2026-07-12 update (AG-GAP-010):** `AI Trading Desk Design.zip` is now
formally declared lost after a final documented search found no copy of the
file, its `/tmp/ai-trading-desk-design` extraction, or its primary source
documents anywhere on the machine. It is retired as a gate — no future parity
check may block on recovering it. The verifiable replacement baseline is
recorded in
`docs/04/agora_design_pack_dynui_2026-06-28/design-parity-baseline-declaration.md`:
the closure packs' written IA/component specs (this file's parity-matrix
criteria plus the 2026-06-28 design-pack invariants) and the hosted
screenshots from `AG-DYNUI-LIVE-TABS-GATE-011`, pinned to deployed frontend
commit `9d60297e5c200d05214df7f758ee0c20c224db02`. Any future maintenance pass
on this task should diff against that baseline doc, not search for the zip
again.

The available local design pack remains:

- `/home/lupin/code/pantheon/Pantheon_Agora_Design_Closure_Pack_2026-06-20.zip`

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

## 2026-07-06 Supervisor Update

Published design-parity work now includes:

- execute-plans PR #190: AG-DYNUI-FULL-008 dark-surface alignment, merge 705649c430d3b6064cf34aa7d854c3936b4c86af.
- execute-plans PR #195: AG-DYNUI-FULL-009 proposal overflow polish, merge 2dd6cf39157adc5d965b721e9e9ec53fbcfc0dac.
- Follow-up deploy continuity through execute-plans PRs #196, #197, and #198.
- Latest hosted proof directory: /tmp/agora-live-proof-9a4d164d.

Do not create a duplicate implementation task for the above work. New work is allowed only for a concrete regression found by comparing the current deployed SHA against the available design pack and hosted screenshots.

## 2026-07-12 Baseline Declaration (AG-GAP-010)

The referenced file `/home/lupin/code/pantheon/AI Trading Desk Design.zip` is
declared lost; see the "Current Design Source State" update above and
`docs/04/agora_design_pack_dynui_2026-06-28/design-parity-baseline-declaration.md`
for the final search log and the replacement baseline. Do not reopen this
search in a future task.
