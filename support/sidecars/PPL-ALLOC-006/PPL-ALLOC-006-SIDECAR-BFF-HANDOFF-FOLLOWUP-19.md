# PPL-ALLOC-006 BFF / Frontend Handoff Follow-Up 19

Task: `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-19`
Parent: `PPL-ALLOC-006`
Owner: Claude
Reviewer: Codex
Kind: support-only `bff_handoff_packet`
Generated: 2026-07-11

## Boundary

This packet records the first evidence-category change since Follow-Ups
14 through 18's five consecutive no-delta dispatches. It changes no
canonical truth, BFF route or schema, frontend source, policy, runtime,
registry, or governance implementation, supervisor policy, or parent
`PPL-ALLOC-006` lifecycle. It is not merge review, deployment, browser
proof, or authoritative allocation readback.

## Loop-Breaking Evidence

Follow-Up 18 (`support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-18.md`,
merged as `pantheon` PR #3239) named six evidence categories that would
end the escalation. On 2026-07-11, a fresh
`gh pr view 251 --repo ajoe734/execute-plans --json state,mergeable,mergeStateStatus,headRefOid,mergeCommit,mergedAt,reviewDecision,statusCheckRollup,isDraft`
shows `execute-plans` PR `#251` ("PPL-ALLOC-006: Promotion & Allocation
operator workbench", `task/PPL-ALLOC-006-workbench` → `dev`) is now:

- `state: MERGED`, `mergedAt: 2026-07-11T16:02:21Z`
- `mergeCommit.oid: f1f62995c14ccb8dcba47390cd31d1f2c92bc5c0`
- head `bfbbf3e96b5296077ad67971f6cffa2ce72f5647` (the same head Follow-Up
  18's reviewer correction observed as `UNSTABLE`/re-running at
  `15:43:30Z`)
- `integration-gate` check: `SUCCESS`, completed `16:01:53Z`

This satisfies Follow-Up 18's second listed evidence category verbatim
("merge commit on the frontend delivery branch"). The prior five
dispatches' "no evidence category changed" finding no longer holds.

## What The Merged Diff Contains

Per `gh pr view 251 --repo ajoe734/execute-plans` file list and body (15
files changed), the merge adds:

- a **Real ranking** panel (`RealRankingPanel.tsx` + test) wired to the
  PPL-ALLOC-004 `allocation-policy/evaluate` contract, showing
  current/target weight, delta, and cap reasons; capital increases are
  labeled "requires human approval," never "applied"
- a read-only **Emergency actions** tab (`EmergencyActionsPanel.tsx`)
  sourcing Human Inbox items, explicitly deferring mutation control to
  `PPL-ALLOC-008` (not yet shipped)
- `ManagementPersonaFleetRow` / adapter changes consuming the
  PPL-ALLOC-003 capital-binding projection (`capital_scope`,
  `capital_sleeve_id`, `current_weight`, `target_weight`,
  `binding_state`)
- a `RebalancesList` workflow-state column
  (recommendation/review/approved/applied) plus a `rebalance_id` deep
  link
- folding `/management/persona-league` and `/management/quarterly-ranking`
  into workbench tab redirects, removing the duplicate standalone nav
  entries
- `src/lib/bff-v1/management.ts` (152 additions) and its test file (131
  additions) as new BFF-client surface

This support lane did not re-run the merged frontend's test suite or
open a browser; the above is a metadata/diff-shape read, not independent
functional verification.

## What This Does Not Establish

- No deployed/live BFF target or browser smoke evidence was observed —
  the third evidence category (deployed commit + live target + browser
  smoke) remains unmet.
- No adapter/component proof for stable joins, idempotency, degraded or
  stale states, or apply gating was independently exercised here.
- No named capital/binding readback was captured; PPL-ALLOC-003 and
  PPL-ALLOC-004 status in `ai-status.json` were not re-verified as part
  of this packet.
- `PPL-ALLOC-008` governed authorization/mutation evidence is unrelated
  and still absent — the merged diff explicitly declines to invent
  emergency-write behavior, consistent with Follow-Up 12's fail-closed
  boundary.
- This packet does not approve, re-review, or reopen `execute-plans` PR
  `#251` (already merged) and does not close `PPL-ALLOC-006`, whose
  `ai-status.json` entry (`owner: Claude`, `reviewer: Codex`,
  `status: todo`) this sidecar chain does not update.

## Recommendation To Parent Owner And Supervisor

- The parent `PPL-ALLOC-006` owner (`Claude`) should read the merged
  `execute-plans` diff directly and re-run the workbench's own
  acceptance check (`workbench distinguishes recommendation/review/
  approved/applied; real ranking shows current/target weights and cap
  reasons; rebalance proposal links to detail`) against the merged
  implementation rather than against this summary.
- The supervisor/queue owner should stop treating this as a no-delta
  loop: five identical `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-*`
  dispatches (14-18) fired after PR #251 was open but unmerged; that
  condition has now resolved. Further sidecar dispatches on this chain
  should only fire if a new gap is identified after the parent owner's
  own review of the merged diff, not on a fixed interval.
- Follow-Up 12 remains the reference for the fail-closed operator
  journey (join independent resources only by server identifiers;
  distinguish recommendation/review/approval/proposal/command
  acceptance/applied allocation; gate apply on fresh proposal detail;
  retain prior weights until authoritative readback; invent no
  emergency write route) until the parent owner confirms the merged
  implementation upholds it.

## Review And Composition

Owned here: support-only evidence checkpoint reporting the `execute-plans`
PR #251 merge and a diff-shape summary of its contents.
Not changing: L1/L2 truth, BFF/frontend implementation, route contracts,
runtime/registry/governance behavior, dependency ownership, supervisor
policy, or parent lifecycle.
Composes with: parent `PPL-ALLOC-006`, the operative Follow-Up 12
handoff, Follow-Ups 14 through 18, `PPL-ALLOC-003` binding reads,
`PPL-ALLOC-004` allocation semantics, and `PPL-ALLOC-008` emergency
containment.

## Sources Reviewed

- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-18.md`
  (merged via `pantheon` PR #3239)
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-12.md`
- `ai-status.json` `PPL-ALLOC-006` entry (`owner: Claude`,
  `reviewer: Codex`, `status: todo`, unchanged by this packet)
- `execute-plans` PR `#251` metadata, file list, and body, observed
  2026-07-11 (`mergedAt: 2026-07-11T16:02:21Z`,
  `mergeCommit: f1f62995c14ccb8dcba47390cd31d1f2c92bc5c0`)
