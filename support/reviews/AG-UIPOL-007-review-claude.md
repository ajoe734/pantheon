# AG-UIPOL-007 Review — Claude

Reviewer: Claude.
Owner: Antigravity.

## Scope of this review

Artifact under review: `ajoe734/execute-plans` PR #319 ("AG-UIPOL-007:
implement strategy lenses monitoring & review drawer"), merged into
`execute-plans@dev` at commit `26cef514` (2026-07-13T22:35:34Z). Touches only
`src/agora/pages/trading-room/TradingRoomPage.tsx` (757 additions / 34
deletions) and `src/agora/pages/trading-room/TradingRoomPage.test.tsx` (4 new
`it()` cases). Checked against
`docs/bff/execution-tasks/2026-07-13-agora-ui-polish/AG-UIPOL-007-trading-room-lens-candidate-parity.md`
and `parity-matrix.md` rows TR-01–TR-09.

## Verification performed

1. Read the full `gh pr diff 319` against `execute-plans`.
2. Checked out the shared `execute-plans` worktree at the task branch tip
   (`5d37b58`, same tree as the PR) and ran
   `npx vitest run src/agora/pages/trading-room/TradingRoomPage.test.tsx`:
   72/72 tests pass — the "100% green" claim in the task brief holds for the
   tests that exist.
3. Ran `npx tsx scripts/check-i18n.ts` (informational-only heuristic lint)
   and grepped the diff directly for hardcoded copy and API/data wiring.
4. Searched `docs/bff/execution-tasks/2026-07-13-agora-ui-polish/evidence/`
   and the `execute-plans` working tree for any AG-UIPOL-007 hosted
   screenshot/evidence artifact — found none.

## Findings

### Blocking — every new string is a hardcoded literal, not routed through
i18n; this reintroduces the exact defect AG-UIPOL-001/this packet exists to
fix

`STRATEGY_LENSES` (lens titles/theses in both English and zh-TW, e.g.
`"籌碼大戶部位建立"` / `"找出大戶開始暗中建立部位，但價格尚未反應的標的。"`,
and the `rules` label/value pairs), all five `DashboardRecipeA..E` widget
titles and copy (`"Candidate Funnel & Flow"`, `"Broker Branch x Date
Heatmap"`, `"Expectation Gap Scenario Tree"`, …), the candidate board's
column headers (`"Rank"`, `"Symbol"`, `"AI Score"`, `"Accum. Days"`, …), the
entire `CandidateReviewDrawer` (`"Candidate Status"`, `"AI Fit Score"`,
`"僕人選出理由 (Why Selected)"`, `"疑慮與反方論點 (Concerns)"`, and every
governed-action button label — `"納入監控"`, `"送影子追蹤"`, `"深入研究"`,
`"暫放觀察"`, `"剔除候選 (Exclude)"`, `"開啟 Winner Branch 工作區"` — and
every `DEFAULT_CANDIDATES` reason/concern/next-event string, are literal
string constants inside `TradingRoomPage.tsx`. None of it uses the
`useTranslation()`/`t()` hook that the surrounding file already uses for
older copy, and no keys were added to `src/i18n/locales/en-US` or `zh-TW`.
`AG-UIPOL-001`'s acceptance bar is "Single locale policy: move all operator
copy to FE i18n, BFF returns keys," and the packet's own trigger section
names "mixed-language copy" as the first defect this whole 2026-07-13
Agora UI polish effort exists to eliminate. This PR ships a large new
surface that is simultaneously English- and zh-TW-hardcoded in the same
component tree, which is a regression against that policy, not progress
toward TR-01/TR-02/TR-04 parity.

### Blocking — dashboards and candidates are invented static fixtures
presented as live data, not "live or explicitly unavailable data"

`STRATEGY_LENSES` metrics (`candidates: 38, held: 9, nearTrigger: 3, …`) and
`DEFAULT_CANDIDATES` (AAPL score 94, `"Significant accumulation from major
institutional broker branches (Morgan Stanley, Goldman Sachs) over the last
7 days…"`, TSM at 92nd IV percentile, etc.) are hardcoded constant arrays.
No fetch, query, or BFF contract call is wired anywhere in this diff — the
five dashboards and the candidate board render invented numbers and
invented institutional-flow narratives with no sample/mock label, in the
same Trading Room surface an operator uses to decide entries/exits. The
acceptance bar requires "Hosted desktop evidence shows all five … lens
dashboards with live or explicitly unavailable data" — this satisfies
neither state; it fabricates a third state indistinguishable from real
evidence. This is the same seeded/fabricated-data anti-pattern already
flagged as a round-1 blocking finding on `AG-UIPOL-008`
(`support/reviews/AG-UIPOL-008-review-claude.md`, same reviewer, same
sprint) for `generateMockData()` — the recurrence here is a new instance of
the same anti-pattern the release gates in this repo exist to forbid, not a
one-off.

### Blocking — Winner Branch handoff is hardcoded to one strategy id,
breaking work item 5

`CandidateReviewDrawer`'s `"開啟 Winner Branch 工作區"` button calls
`onStrategySelect("strat-001")` unconditionally — every candidate's drawer
(AAPL, TSM, NFLX, AMD, …, spanning five different lenses) routes to the same
hardcoded `"strat-001"` regardless of which candidate or lens was open. The
task's work item 5 requires the handoff to "preserve … and make the
selected lens/strategy relationship explicit"; instead the relationship is
severed — the button is decorative for every candidate except whichever one
actually maps to `strat-001`.

### Required before close — no hosted evidence exists

Acceptance requires "Hosted desktop evidence" for all five dashboards and
"Hosted narrow evidence … for the switcher, board, and drawer." No
screenshot, deployment probe, or hosted-evidence markdown for AG-UIPOL-007
exists in `docs/bff/execution-tasks/2026-07-13-agora-ui-polish/evidence/`
or anywhere else in either repo, and INDEX.md's Supervisor Instruction #3
("Every task requires post-deploy hosted screenshots of the affected tab")
and the packet's Production-Level Rule (hosted browser proof against the
dev URL) are both unmet. Compare `AG-UIPOL-002`/`004`/`006`, each of which
recorded a "record hosted … evidence/acceptance" commit before close.

### Required before close — test coverage falls well short of the
acceptance bar, and the drawer has no keyboard/focus behavior to test

Acceptance requires "Component/contract tests cover every recipe,
lens-specific columns, empty and delayed data, drawer keyboard/focus
behavior, and candidate state changes." Only 4 tests were added: lens
switcher render, switching to lens D only (Recipes A/B/C/E are never
rendered in any test), opening the drawer plus one field check, and one
state transition. Not covered: any candidate-board column for lenses
A/B/C/E, the empty-candidates state (`"No candidates in this state…"`),
delayed/loading data, or drawer keyboard/focus behavior. The last one is
missing at the implementation level, not just the test level — the drawer
has no `onKeyDown`/Escape handler, no focus trap, and no `role="dialog"`/
`aria-modal` at all, so there is nothing for a keyboard/focus test to
assert against yet.

## Verdict

**Changes requested — reopening to owner (Antigravity).** Three blocking
findings (hardcoded copy regressing AG-UIPOL-001's i18n policy, fabricated
data presented as live, and the hardcoded `"strat-001"` handoff) plus two
required-before-close gaps (no hosted evidence, insufficient test/behavior
coverage against the acceptance bar) must be addressed before this can move
to `review_approved`. The task brief's "All targeted tests and full test
suites are 100% green" claim is accurate as far as it goes (verified: 72/72
pass) but green tests do not by themselves satisfy the acceptance criteria
above.

LLM-Agent: Claude
Task-ID: AG-UIPOL-007
Reviewer: Claude
Verified: read `gh pr diff 319` (ajoe734/execute-plans); ran
`npx vitest run src/agora/pages/trading-room/TradingRoomPage.test.tsx`
(72/72 pass) in the shared execute-plans worktree at commit `5d37b58`;
searched for AG-UIPOL-007 hosted evidence in both repos (none found)
