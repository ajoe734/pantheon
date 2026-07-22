# Agora UI Polish - 2026-07-13

Status: execution packet for fleet supervision.

Trigger: operator review of the hosted dev Agora shell (2026-07-13
screenshots of /agora/trading-room, /agora/strategy-workshop,
/agora/strategy-performance) found the shipped UI does not meet operator
quality: mixed-language copy, duplicated availability badges, nested double
scrollbars, ghost headers, and contradictory readiness/completeness state.
These defects were never covered by AG-DYNUI-* (workflow correctness) or
AG-GAP-* (backend truth) waves, and AG-GAP-010 baselined visual parity on
current screenshots while the design zip was unavailable — so gates passed
while the UI read as unfinished.

## Design-source recovery update

The original design export was recovered on 2026-07-13 and is now versioned at
`docs/design/agora-trading-desk-design/`. Its checksum, source precedence, V2–
V11 documents, interactive `Agora.dc.html`, and 26 screenshots are indexed in
that directory. AG-UIPOL-005 supersedes the earlier “lost source” parity
assumption with `parity-matrix.md` and deploy-SHA-pinned hosted evidence.

## Production-Level Rule

Same as the 2026-07-12 gap-closure packet: clean branch, tests, PR, green
required checks, merge, deploy, and hosted browser proof (screenshots against
`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`). Frontend changes land
only in `ajoe734/execute-plans@dev`. Fixture screenshots do not count.

## Verified root causes (2026-07-13)

- Mixed language: the BFF workspace-proposal generator hardcodes zh-TW view
  titles/purposes (`services/control-plane/bff/agora/trading_room/router.py:893`
  area, 9 hardcoded zh strings) while FE chrome is English; the FE also
  renders literal zh badges ("部分可用") inside English sentences.
- Badge spam: the generator defaults every widget/view to
  `dataAvailability: "partial"` (`router.py:860,899`), so the proposal page
  renders 12 identical "部分可用 / …may lag research" caption blocks that
  carry no per-widget information.
- Double scrollbar: the standalone Agora shell nests a scrollable container
  inside the scrolling page body (visible custom arrows top-right); the page
  double-scrolls on all three tabs.
- Workshop page: ghost/overlapping "Strategy workshop" heading behind the
  status row; completeness rail shows "Dimension coverage 0%" while the
  completeness card says overall grade "complete"; readiness entries all say
  Ready but their labels render greyed-out/disabled.
- Performance tab: an "Unassigned" strategy row with 6,841 trades and PNL $0
  sits above the real strategy; "$0" and "not reported" are mixed as if both
  mean the same thing.

## Task Graph

| Task | Title | Repo |
|---|---|---|
| AG-UIPOL-001 | Single locale policy: move all operator copy to FE i18n, BFF returns keys | execute-plans + pantheon |
| AG-UIPOL-002 | Fix standalone shell double scrollbar and ghost headers | execute-plans |
| AG-UIPOL-003 | Real per-widget data availability; collapse badge spam into a summary | pantheon + execute-plans |
| AG-UIPOL-004 | Reconcile readiness/completeness display; performance tab honesty | execute-plans |
| AG-UIPOL-005 | Re-verify all shipped surfaces against the recovered design source | pantheon docs/evidence |
| AG-UIPOL-006 (draft) | Shell command, Servant, and layout-control parity | execute-plans + additive pantheon contract work if required |
| AG-UIPOL-007 (delivered) | Multi-lens monitoring and candidate parity | execute-plans; Pantheon closeout evidence |
| AG-UIPOL-008 (draft) | Winner Branch workspace information parity | execute-plans + additive pantheon projection work if required |
| AG-UIPOL-009 (draft) | V10 expert Strategy Workshop parity | execute-plans + pantheon |
| AG-UIPOL-010 (draft) | Performance cockpit parity | execute-plans + additive pantheon projection work if required |
| AG-UIPOL-011 (draft) | Narrow responsive task parity | execute-plans |

The source is no longer blocked. Design parity itself is still not achieved;
AG-UIPOL-005 records the remaining gaps and assigns every major/missing row to
AG-UIPOL-001..004 or a ranked AG-UIPOL-006+ draft. Do not close an individual
task by claiming whole-product parity.

## Supervisor Instructions

1. Owner Codex, reviewer Codex2 (current dispatchable lanes); prefer Claude
   ownership if that lane recovers.
2. FE changes only via `ajoe734/execute-plans@dev`; block nested-checkout edits.
3. Every task requires post-deploy hosted screenshots of the affected tab.
4. i18n locale default: zh-TW primary with en fallback unless the owner
   overrides in AG-UIPOL-001 review.
