# AG-DYNUI-PROD-005 Review — Claude2

Reviewer: Claude2 (reassigned from Codex2, who was quota_terminal
dispatch-paused past the 30-minute reviewer-reassignment threshold).
Owner: Claude.

## Scope of this review

Implementation PR `ajoe734/execute-plans` #176 ("AG-DYNUI-PROD-005:
wire workshop route handoff", merge commit `eaad3fa90d7c55a4476ed8dcda0063457933a1cc`,
merged into `execute-plans` `dev` 2026-07-04T13:46:33Z) already merged
into `pantheon` `dev` via PR #2983 (merge commit `8556643b8`,
2026-07-04T16:02:43Z) as closeout-evidence-only (no pantheon backend
change required). This review independently re-verifies the owner's
claims in
`docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-005-dynamic-workflow-closeout.md`
rather than taking them at face value.

## Independent verification performed

1. **Dependency gate** — confirmed via `python3 scripts/ai_status.py show`
   that `AG-DYNUI-PROD-002`, `AG-DYNUI-PROD-003`, `AG-DYNUI-PROD-004`
   are archived `terminal_status: done`, `terminal_outcome: completed`.

2. **Backend tests** — ran directly (not just trusted the closeout
   note): `python3 -m pytest bff/agora/trading_room/test_trading_room.py -q`
   from `services/control-plane/` → **45 passed**. Confirmed test names
   cover the claimed surface: `test_decision_requires_idempotency_key`,
   `test_workspace_layout_requires_etag_and_supports_remove_restore`,
   `test_workspace_view_and_widget_mutations_are_registry_validated`,
   `test_workspace_rejects_servant_direct_patch_and_code_injection`,
   `test_workspace_cross_user_read_is_forbidden`,
   `test_widget_revision_proposal_apply_preserves_before_after_and_records_version`,
   `test_widget_revision_keep_original_adds_copy_and_rollback_creates_new_version`.

3. **Frontend wiring** — fetched the real `ajoe734/execute-plans` `dev`
   branch content directly via `gh api` (not the stale vendored
   `execute-plans/` copy tracked inside this `pantheon` worktree, which
   predates the standalone-repo split and does not contain the
   `Workspace*` components at all — this is a pre-existing repo-layout
   quirk, not a defect introduced by this task).
   - `src/agora/pages/trading-room/TradingRoomPage.tsx` imports and
     mounts `WorkspaceProposalPreview` and `WorkspaceGridEditor` from
     `@/agora/trading-room/*`.
   - `src/agora/trading-room/WorkspaceGridEditor.tsx` imports and
     mounts `WorkspaceWidgetRevisionDrawer`, calls
     `listTradingRoomWorkspaceVersions`/`rollbackTradingRoomWorkspaceVersion`,
     and renders a `workspace-version-history` section with rollback —
     so version history/rollback/widget-revision are present, just
     nested one level inside the grid editor rather than directly in
     the page (initially looked like a gap until traced one level
     deeper).
   - `src/routes/agora.tsx` wires `onAddToTradingRoom` from
     `AgoraStrategyWorkshopRoute` to navigate to `/agora/trading-room`,
     matching PR #176's actual diff (`agora.tsx`,
     `StrategyWorkshopPage.test.tsx`, `agora.test.tsx`).
   - `src/lib/bff-v1/agora/tradingRoom.ts` sends `If-Match` and
     `Idempotency-Key` headers on writes.
   - Frontend test file `TradingRoomPage.test.tsx` on `dev` has 56
     `it(`/`test(` blocks, matching the closeout's "56 passed" claim.

4. **Hosted evidence** — read
   `docs/deployment/evidence/ag-dynui-prod-005/20260704T155514Z/hosted-browser-bff-probe-2026-07-04.md`
   and `README.md`. Probe against `/agora/trading-room` on the hosted
   dev FE: `pass=true`, required BFF path `/bff/agora/trading-room`
   returns `200`, zero old-BFF-host hits, zero failed requests, zero
   console errors. Consistent with the closeout note's explicit scoping
   (deploy-freshness + BFF-host-wiring only; full authenticated
   screenshot walkthrough is correctly deferred to `AG-DYNUI-PROD-006`,
   which depends on this task).

## Findings

No blocking findings. The scope clarifications in the closeout doc
(Dashboard-family components out of scope; `/bff/agora/trading-room/stream`
intentional SSE stub; full hosted E2E screenshots owned by
`AG-DYNUI-PROD-006`) are accurate and consistent with what PR #176
actually changed and what already existed from
`AG-DYNUI-PROD-002/003/004`.

## Verdict

**Approved.** Returning to owner (Claude) for finalization per
`.orchestrator/skills/task-closeout-finalization.md`.
