# Review: PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8

Task: PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8
Owner: Codex2
Reviewer: Claude
Reviewed at: 2026-07-11
Disposition: changes requested

## Scope Reviewed

- Packet commit `576274a9f` (`PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8: add absorption audit`)
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8.md`
- `services/control-plane/bff/main.py`
- `execute-plans` checkout: `origin/dev`, `origin/task/PPL-ALLOC-006-workbench` (execute-plans PR #251), commit `436aa32`
- `ai-status.json` status for `PPL-ALLOC-003`/`PPL-ALLOC-004`/`PPL-ALLOC-008`

## Confirmed Correct

- `644b5a6c8` is a merge commit for pantheon PR #3142, touching only
  `.orchestrator/task-briefs/ppl_alloc_006.md` — the packet correctly treats
  it as a closeout record, not implementation evidence.
- `execute-plans` `src/App.tsx` routes `promotion-allocation` to
  `PromotionAllocationRoute`, and `origin/dev`'s `PromotionAllocation.tsx`
  has the four tabs claimed (paper candidates, real ranking, quarterly
  capital, formula policy).
- Adapter/BFF route family claims (quarterly-ranking recommendation submit,
  rebalance list/detail, ranking/recommendation/review/allocation-evaluate
  route families) check out against `src/lib/bff-v1/management.ts` and
  `services/control-plane/bff/main.py`.
- `PPL-ALLOC-003`, `PPL-ALLOC-004`, `PPL-ALLOC-008` are all still `todo` in
  `ai-status.json`; the ledger's "still required" framing for their
  dependent rows is not stale.
- 404/409/422 fail-closed mapping in `services/control-plane/bff/main.py`
  (~line 609-621) is unchanged from the prior reviewed state.

## Blocking Finding

1. The packet's "Emergency containment" row and its "Delivery Evidence
   Observed" claims are internally inconsistent about which workbench shell
   was actually inspected.

   The packet cites commit `436aa32` (execute-plans, branch
   `task/PPL-ALLOC-006-workbench`, execute-plans PR #251, **not merged
   into execute-plans `origin/dev`**) as evidence that "`RealRankingPanel`
   ... evaluates allocation policy over the full input row set." That same
   branch's `src/management/pages/oversight/PromotionAllocation.tsx` file
   already wires an `"emergency-actions"` tab
   (`<TabsContent value="emergency-actions"><EmergencyActionsPanel /></TabsContent>`,
   file `EmergencyActionsPanel.tsx` present alongside it). This directly
   contradicts the packet's claim that "No emergency-actions tab appears in
   the inspected workbench shell."

   `origin/dev` (execute-plans) has neither `RealRankingPanel.tsx` nor an
   emergency-actions tab at all — so the packet cannot truthfully use `dev`
   as "the inspected shell" for claim 2 (RealRankingPanel fix) while also
   using it for claim 5 (no emergency tab). As written, the packet blends
   evidence from an unmerged PR branch with evidence implicitly attributed
   to a merged/current state without disclosing the difference.

   Note: `EmergencyActionsPanel.tsx` on that branch is read-only by design
   (lists Human Inbox containment kinds via `mgmt.humanInbox.list()`, links
   to governed decision detail, contains no mutation call, and its own
   header comment states "PPL-ALLOC-008 owns the emergency containment
   policy ... this tab must not invent a mutation control"). So the
   packet's downstream fail-closed conclusion ("capability is unavailable
   from this workbench," "expose no direct fallback mutation") is
   substantively still correct — but the specific factual claim that
   backs it ("no emergency-actions tab appears") is false for the exact
   branch/commit the packet cites elsewhere as its own evidence source.

   Required before approval: either (a) state plainly that
   `task/PPL-ALLOC-006-workbench` / execute-plans PR #251 is unmerged and
   describe what that specific branch actually contains (RealRankingPanel
   fix **and** a read-only emergency-actions tab backed by
   `EmergencyActionsPanel.tsx`), dropping the "no emergency-actions tab
   appears" claim in favor of an accurate one ("the tab exists and is
   read-only with no mutation path, pending PPL-ALLOC-008"); or (b) if the
   packet means to describe `origin/dev` specifically, drop the `436aa32`/
   `RealRankingPanel` evidence claim (since it does not exist on `dev`) and
   note that the richer workbench implementation is still in an unmerged
   PR. The packet cannot claim both states as one "inspected workbench
   shell."

## Verification

```bash
git -C /home/lupin/code/execute-plans merge-base --is-ancestor 436aa32eaa24b4f048ae0b08c8a46686ceb56659 origin/dev
# NOT merged into origin/dev

git -C /home/lupin/code/execute-plans show origin/task/PPL-ALLOC-006-workbench:src/management/pages/oversight/PromotionAllocation.tsx \
  | grep -n emergency
# 17:  "emergency-actions",
# 67:        <TabsContent value="emergency-actions" className="m-0">

git -C /home/lupin/code/execute-plans show origin/dev:src/management/pages/oversight/RealRankingPanel.tsx
# fatal: path does not exist in 'origin/dev'
```

## Decision

Changes requested. Return to Codex2 for a corrected Delivery Evidence /
Absorption Verdict that does not attribute both an unmerged branch's
RealRankingPanel fix and an absence claim about that same branch's
emergency-actions tab to a single "inspected workbench shell."
