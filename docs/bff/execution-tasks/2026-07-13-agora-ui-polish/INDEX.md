# Agora UI Polish - 2026-07-13

Status: execution packet for fleet supervision.

Trigger: operator review of the hosted dev Agora shell (2026-07-13
screenshots of /agora/trading-room, /agora/strategy-workshop,
/agora/strategy-performance) found the shipped UI does not meet operator
quality: mixed-language copy, duplicated availability badges, nested double
scrollbars, ghost headers, and contradictory readiness/completeness state.
These defects were never covered by AG-DYNUI-* (workflow correctness) or
AG-GAP-* (backend truth) waves, and AG-GAP-010 baselined visual parity on
current screenshots because the design zip is lost — so gates pass while the
UI reads as unfinished.

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

Design parity proper remains blocked on a real design source (the
AI Trading Desk Design zip is declared lost; owner has been asked whether a
copy or Figma source exists). Do not close any AG-UIPOL task by claiming
design parity — these tasks fix objective defects only.

## Supervisor Instructions

1. Owner Codex, reviewer Codex2 (current dispatchable lanes); prefer Claude
   ownership if that lane recovers.
2. FE changes only via `ajoe734/execute-plans@dev`; block nested-checkout edits.
3. Every task requires post-deploy hosted screenshots of the affected tab.
4. i18n locale default: zh-TW primary with en fallback unless the owner
   overrides in AG-UIPOL-001 review.
