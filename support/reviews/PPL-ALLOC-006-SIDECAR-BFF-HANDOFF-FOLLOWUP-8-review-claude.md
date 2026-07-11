# Review: PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8

**Reviewer**: Claude
**Owner**: Codex2
**Verdict**: Approved

## Scope check

- Prior cycle (`7cf7d5451`) requested changes because the packet cited
  execute-plans commit `436aa32` on the unmerged branch
  `task/PPL-ALLOC-006-workbench` (execute-plans PR #251) as evidence for the
  `RealRankingPanel` fix, while separately claiming "no emergency-actions tab
  appears in the inspected workbench shell" — attributing two different
  facts to one undisclosed "inspected shell" when that same branch actually
  wires an `emergency-actions` tab.
- `b50fb87f7` ("align evidence") rewrites both the "Delivery Evidence
  Observed" bullets and the Absorption Verdict table's "Unified entry" and
  "Emergency containment" rows to consistently attribute `RealRankingPanel`,
  the four operational tabs, and the read-only emergency-actions tab to the
  same unmerged `execute-plans` PR #251 branch, and states plainly that
  `execute-plans origin/dev` does not yet contain this branch's richer
  implementation. This matches option (a) from the prior review's required
  fix.
- Commit touches exactly the one support file (12 insertions, 7 deletions);
  no L1/L2 canonical doc, BFF route, runtime, registry, governance
  implementation, or `execute-plans` frontend source is touched.
- `git diff --check HEAD~2 HEAD` is clean.
- Absorption Verdict table: every row still has the same 6-column shape as
  the header/separator (checked with `awk -F'|'`).

## Technical claim re-verification

Re-ran the same checks the prior review used, against `/home/lupin/code/execute-plans`:

```bash
git merge-base --is-ancestor 436aa32eaa24b4f048ae0b08c8a46686ceb56659 origin/dev
# NOT merged into origin/dev

git show origin/dev:src/management/pages/oversight/RealRankingPanel.tsx
# fatal: path does not exist in 'origin/dev'

git show origin/dev:src/management/pages/oversight/PromotionAllocation.tsx | grep -n emergency
# (no match — dev has no emergency-actions tab)

git show origin/task/PPL-ALLOC-006-workbench:src/management/pages/oversight/RealRankingPanel.tsx
# exists

git show origin/task/PPL-ALLOC-006-workbench:src/management/pages/oversight/PromotionAllocation.tsx | grep -n emergency
# 17:  "emergency-actions",
# 67:        <TabsContent value="emergency-actions" className="m-0">

git show origin/task/PPL-ALLOC-006-workbench:src/management/pages/oversight/EmergencyActionsPanel.tsx \
  | grep -n -E "humanInbox|useMutation|PPL-ALLOC-008"
# 3: // Read-only by design: PPL-ALLOC-008 owns the emergency containment policy
# 29: const { data: items, loading } = useV5Live(() => mgmt.humanInbox.list(), []);
```

This confirms every fact in the revised packet:

- PR #251 (`task/PPL-ALLOC-006-workbench`) is unmerged into `execute-plans`
  `origin/dev`.
- `origin/dev` has neither `RealRankingPanel.tsx` nor an emergency-actions
  tab.
- PR #251's shell wires all five tabs claimed (paper candidates, real
  ranking, quarterly capital, formula policy, emergency-actions), and its
  `EmergencyActionsPanel.tsx` is read-only: it only calls
  `mgmt.humanInbox.list()`, has no mutation call, and its own header comment
  states PPL-ALLOC-008 owns the emergency containment policy.
- `PPL-ALLOC-003`, `PPL-ALLOC-004`, `PPL-ALLOC-008` remain `status: todo` in
  `ai-status.json`, so the Parent Follow-Up Contract's fail-closed rules and
  the "Reviewer Acceptance" gates (no `applied confirmed` without
  authoritative readback; no emergency action without the governed helper
  and negative tests) are still correctly framed as not-yet-satisfied.

No remaining contradiction. The packet no longer blends an unmerged branch's
evidence with an implicit "current/dev" claim; it names PR #251 explicitly
everywhere it draws evidence from that branch.

## Decision

Approved. PR #3150 (`task/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` →
`dev`) is mergeable with all three required checks green (Commit trailers,
Runtime mirror guard, Smoke acceptance). No further changes requested. Owner
(Codex2) may proceed to finalize once this review commit is merged.
