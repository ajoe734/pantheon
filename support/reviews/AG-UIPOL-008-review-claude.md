# AG-UIPOL-008 Review — Claude

Reviewer: Claude.
Owner: Antigravity.

## Scope of this review

Artifact under review: `ajoe734/execute-plans` PR #313 ("AG-UIPOL-008: align
workspace styles and add servant widgets"), merged into `execute-plans@dev`
at commit `c6bc239` (2026-07-13T19:32:17Z). Touches
`src/agora/components/TradeDecisionCard.tsx`,
`src/agora/pages/trading-room/TradingRoomPage.tsx`,
`src/agora/trading-room/WorkspaceGridEditor.tsx`, and
`src/agora/widgets/ChartSpecRenderer.tsx` (922 additions / 132 deletions, no
test files touched). Checked against
`docs/bff/execution-tasks/2026-07-13-agora-ui-polish/AG-UIPOL-008-winner-branch-workspace-information-parity.md`
and the matching rows in `parity-matrix.md` (TR-11, WB-04–WB-12, WB-14,
WB-15A, WB-19).

## Verification performed

1. Read the full `gh pr diff 313` against `execute-plans`.
2. Cross-checked `dataAvailability` plumbing already landed by AG-UIPOL-003
   (`src/lib/bff-v1/agora/dataAvailability.ts`,
   `WorkspaceWidgetRevisionDrawer.tsx`) to confirm what "real" data-status
   wiring already exists vs. what this PR adds.
3. Re-read `parity-matrix.md` rows WB-05 through WB-11 (per-view content
   requirement) and WB-19 (non-empty initial workspace / honest
   missing-data states) to check them against the actual diff.

## Findings

### Blocking — `ChartSpecRenderer` now fabricates and renders fake market
data as if it were live (`src/agora/widgets/ChartSpecRenderer.tsx`,
`generateMockData`, wired at `WorkspaceGridEditor.tsx` line ~457:
`data={widget.dataAvailability === "unavailable" ? [] : undefined}`)

Previously every widget always passed `data={[]}`, which always rendered the
honest (if ugly) "No chart data is available" notice — this was literally
the WB-19/WB-05..11 defect the task exists to fix. The fix implemented here
is not "wire in real per-widget data" or "render an honest in-context
missing/delayed/anomalous state" (which is what V11 and WB-19 require:
*"V11 explicitly forbids making the trader build the initial desk and
requires missing/delayed/anomalous data to be expressed in-context"*).
Instead, whenever `dataAvailability` is `"complete"` or `"partial"` (i.e.
`data` is `undefined`, which is the normal/common case since no live data
source is actually wired into this call site), `ChartSpecRenderer` now calls
`generateMockData(spec)` and silently renders `Math.random()`-generated
numbers — fabricated candlestick price walks starting at $150, random
scatter/heatmap values, and rows keyed off real ticker symbols
(`AAPL, MSFT, GOOG, TSLA, NVDA, AMZN, META, NFLX, AMD`) — with **no label,
badge, or any indication that the numbers are synthetic**. This renders
indistinguishably from real evidence in the same Trade Decision /
Winner-Branch workspace surface a trader uses to decide entries/exits.

This is exactly the anti-pattern this repo's own release gates exist to
catch (Gate 4 checks: "Frontend Persona Fleet renders production rows or
live empty state without demo/NaN", "Frontend live banner does not claim
seed fallback armed") — it just wasn't caught here because Gate 4 was
SKIPped on this PR's run (`hosted-browser-bff-probe` skipped, unrelated to
this change). Showing fabricated numbers as if real is strictly worse than
the plain "No chart data" box it replaces, and it does not satisfy the
acceptance bar ("no accepted initial workspace is ... filled with generic
renderer errors" was never meant to be satisfied by inventing data).

**Required fix:** remove `generateMockData` from the live render path (or
gate it so it can never fire when `WorkspaceGridEditor`/`WorkspaceWidgetCard`
render for a real workspace — e.g. Storybook-only, clearly labeled "SAMPLE
DATA"). Replace the `data === undefined` fallback with the honest
in-context missing/delayed/anomalous state the design calls for, keyed off
`widget.dataAvailability`, the same way `ChartNotice`'s new per-widget-type
copy already partially attempts (that part is fine) — just don't feed it
synthetic rows as if they were real evidence.

### Blocking — Work item 2 (seven-view operator content) is not addressed;
parity-matrix rows still show the same drift this task was scoped to close

The task's Work section requires filling views A–G (Strategy Overview,
Candidate & Entry, Winner Branch Intelligence, Stakeholder & Capital
Migration, Event Lead, Positions, Evidence & Monitoring) with their
designed, view-specific content (candidate table, stakeholder/migration map,
event timeline, position/add/reduce/exit queue, evidence catalogue, etc. —
see `parity-matrix.md` WB-05..WB-11). The PR only touches shared widget
chrome (`WorkspaceGridEditor.tsx` control strip / menu / servant-proposal
modal), the generic `ChartSpecRenderer`, and `TradeDecisionCard` colors. It
does not add any per-view content structure, so WB-05 through WB-11 remain
"major drift" as written in the matrix. Either this PR's scope needs to
actually build that content, or the task/parity-matrix must not be
represented as closing those rows.

### Non-blocking but required before close — no test coverage added

Acceptance explicitly requires "Contract/component/Playwright tests cover
seven-view completeness, missing and anomalous data, decision-card state,
and preservation of edit/revision/rollback behavior." The PR touches 0 test
files despite adding a new widget-proposal modal, six new menu actions, and
changing the `ChartSpecRenderer` empty-data contract.

## Verdict (round 1)

**Changes requested — reopening to owner (Antigravity).** The dark-theme
styling, control-strip layout, and servant widget-menu/proposal UI are a
reasonable start, but the fabricated-mock-data behavior in
`ChartSpecRenderer` must be removed/replaced before this can be approved,
the seven-view content gap (WB-05..WB-11) needs either real work or a
scope correction, and focused tests are required per the acceptance
criteria.

## Round 2 — PR #315 ("remove fake market data fabrication")

Artifact under review: `ajoe734/execute-plans` PR #315, merged into
`execute-plans@dev` at commit `c806a4f` (2026-07-13T19:46:57Z). Touches
`src/agora/widgets/ChartSpecRenderer.tsx`,
`src/agora/widgets/ChartSpecRenderer.test.tsx`, and one line in
`src/agora/trading-room/WorkspaceGridEditor.tsx` (41 additions / 6
deletions in the renderer, 77 additions of test-only content, no other
files).

### Blocking finding #1 (fabricated mock data) — resolved

`ChartSpecRenderer` now only calls `generateMockData` when the caller
explicitly passes `isSampleData={true}` (wired only at the New Widget
Proposal preview call site, which also now paints a visible "SAMPLE DATA"
badge via `ChartFrame`). The normal render path (`isSampleData` defaulting
to `false`) no longer fabricates rows: `data === undefined` now falls
straight to the honest `ChartNotice` path (`rows.length === 0` branch,
`isUnavailable` check unchanged), with new/expanded per-`widgetType` copy
(`position_action_queue`/`positions*`/`signal_decision_queue`/
`shadow_scoreboard` → "NO ACTIVE POSITIONS", `evidence*` →
"EVIDENCE VALIDATED", `confidence_decomposition` → "AWAITING METRICS").
New tests in `ChartSpecRenderer.test.tsx` cover: honest notice + no
"SAMPLE DATA" text when `isSampleData` is false/absent, the "SAMPLE DATA"
badge and mock rows when `isSampleData` is true, and correct per-widgetType
notice copy for four widget types. This closes the blocking defect —
fabricated numbers can no longer render as if they were live evidence in
the accepted workspace.

### Blocking finding #2 (seven-view operator content, WB-05..WB-11) — still open

This PR does not touch per-view content. `parity-matrix.md` rows
WB-04..WB-12, WB-14, WB-15A, TR-11, and WB-19 are unchanged since round 1
and still read **major drift / missing — AG-UIPOL-008** as of this
re-check (`docs/bff/execution-tasks/2026-07-13-agora-ui-polish/parity-matrix.md`
lines 95, 104–112, 114, 116, 120). Replacing the fabricated-data path with
an honest per-type notice is a real improvement toward WB-19's
"missing/delayed/anomalous data expressed in-context" requirement, but the
views themselves (A Strategy Overview, B Candidate & Entry, C Winner Branch
Intelligence, D Stakeholder & Capital Migration, E Event Lead, F
Positions/Add/Reduce/Exit, G Evidence & Monitoring Rules) still render the
same generic registry shells with no populated content structure. This
remains blocking per the task's own Work item 2 and acceptance bar
("Hosted evidence walks all seven accepted views and shows each view's
required content").

### Required-before-close finding #3 (test coverage) — partially addressed

The new `ChartSpecRenderer.test.tsx` cases are a genuine, well-targeted
addition covering the missing/anomalous-data notice behavior this round
fixed. They do not cover the acceptance bar's remaining scope: seven-view
completeness, decision-card state, or preservation of edit/revision/
rollback behavior. No Playwright coverage was added.

## Verdict (round 2)

**Changes requested — reopening to owner (Antigravity).** Finding #1 is
resolved and should not need further round-2 work. Finding #2 (seven-view
operator content for WB-05..WB-11) is unaddressed and remains the primary
blocker to approval; either build the per-view content or bring a scope
correction to the task/parity-matrix instead of leaving those rows marked
as this task's open drift. Contract/component/Playwright coverage for
seven-view completeness, decision-card state, and edit/revision/rollback
preservation is still required per the task's acceptance criteria before
this can move to `review_approved`.

LLM-Agent: Claude
Task-ID: AG-UIPOL-008
Reviewer: Claude
Verified: read `gh pr diff 315` (ajoe734/execute-plans); re-checked
`parity-matrix.md` rows WB-04..WB-12, WB-14, WB-15A, TR-11, WB-19 against
current file content (unchanged since round 1)
