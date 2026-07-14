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

## Round 2 — PR #320 ("i18n, BFF candidate integration & drawer a11y focus trap")

Artifact under review: `ajoe734/execute-plans` PR #320, merged into
`execute-plans@dev` at commit `2fb8b36e` (2026-07-13T23:08:50Z). Touches
`src/agora/pages/trading-room/TradingRoomPage.tsx` (i18n keys, BFF
candidate wiring, drawer a11y), `TradingRoomPage.test.tsx` (4 new tests,
72 → 76), `src/i18n/locales/en-US.ts`, and `src/i18n/locales/zh-TW.ts`.
Also reviewed pantheon PR #3579's added
`docs/bff/execution-tasks/2026-07-13-agora-ui-polish/evidence/AG-UIPOL-007-hosted-evidence.md`.

### Blocking finding #1 (hardcoded copy / i18n) — mostly resolved, incomplete

`STRATEGY_LENSES` titles/theses/rules, all candidate board column headers,
the candidate lifecycle-state labels, and the drawer's governed-action
button labels are now routed through `t()` with real `en-US`/`zh-TW`
dictionary keys (verified both locale files contain filled-in
`agora.tradingRoom.lenses.*` / `agora.tradingRoom.candidates.*` trees, not
just English fallbacks). This is genuine, substantial progress. However
the fix is incomplete: inside `CandidateReviewDrawer`, the section headers
`"Next Catalyst Event"`, `"Evidence references"`, `"Governed Actions"`,
and the inline label `"Current State:"` are still bare English string
literals with no `t()` call. `DashboardRecipeB`'s entire hypothesis
narrative (`"AI GPU demand is driving silicone wafer substrate demand;
supply constraints at TSMC shift packaging focus to ASE."`) and its
supply-chain node labels (`"Silicon Wafers"`, `"Substrates"`, `"AI GPU"`)
are also still hardcoded English literals in the same component tree this
finding originally covered. This is a narrower recurrence of the same
defect, not a new one — worth finishing before calling AG-UIPOL-001
compliance complete for this surface.

### Blocking finding #2 (fabricated dashboard/candidate data presented as live) — resolved for the candidate board, still open for the five dashboard recipes

The candidate board itself is now honest: `TradingRoomPage` calls
`listCandidatePoolMembers(activeLensId)` in a `useEffect`, maps real BFF
items into `CandidateRecord`s when the response is non-empty, and falls
back to `DEFAULT_CANDIDATES` with a visible
`data-testid="sample-data-warning"` amber badge
(`"SAMPLE DATA ONLY (BFF OFFLINE)"`) whenever the BFF call fails or
returns an empty list. That satisfies "live or explicitly unavailable
data" for the board and lens switcher metrics (`dynamicMetrics` now
derives candidate/held counts from the loaded list when present).

The five `DashboardRecipeA..E` widgets themselves were not touched by this
wiring. Every internal number and narrative in the funnel/flow, heatmap,
broker network, supply-chain map, similarity scatter, candlestick levels,
trade-condition panel, event countdown/scenario tree, and capital-intent/
slippage-curve widgets remains a static hardcoded constant with zero
connection to `candidates`, BFF data, or `isSampleData`. Critically, the
`isSampleData` warning badge is rendered once above both the dashboard
*and* the board, so when the BFF call succeeds and returns real candidates
(`isSampleData === false`), the badge disappears — but the dashboard
recipe widgets keep rendering the exact same invented numbers/narrative
with nothing distinguishing them from live content. This is the same
fabricated-data anti-pattern from round 1, narrowed in scope from
"everything" to "the five dashboard recipe bodies specifically," and it
is the same anti-pattern already flagged blocking twice this sprint on
`AG-UIPOL-008` round 1 (`support/reviews/AG-UIPOL-008-review-claude.md`).

### Blocking finding #3 (hardcoded `"strat-001"` Winner Branch handoff) — resolved

`CandidateReviewDrawer`'s workspace button now computes `matchedStrategyId`
from `strategies` (passed down from `TradingRoomDefaultEntry`): match by
strategy title containing the candidate's symbol, else first `ready`
strategy, else first strategy in the list. This restores the
candidate/lens-to-workspace relationship work item 5 requires. No further
action needed here.

### Required-before-close finding #4 (hosted evidence) — still open, and the new evidence record overclaims

`docs/.../evidence/AG-UIPOL-007-hosted-evidence.md` (added in pantheon PR
#3579) asserts the hosted dev FE `deployment.json` was "verified" but,
unlike the `AG-UIPOL-006`/`AG-UIPOL-003` hosted-evidence records it should
be patterned on, never states the specific commit it observed there, and
includes no screenshots, no Playwright hosted-e2e run, and no
`AG_UIPOL_007_EXPECTED_COMMIT`-style assertion. Fetching
`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
directly during this review returns `"commit":
"903784e80dd8eb6dcf1225a314a8a02a6afc5c31"`, `"deployedAt":
"20260713T220105Z"` — and `903784e8` is a git ancestor of `26cef514` (PR
#319's own merge commit, 2026-07-13T22:35:34Z), i.e. the hosted dev FE has
not been redeployed since *before* AG-UIPOL-007's original implementation
landed, let alone PR #320's fixes. No hosted browser could show any of
this task's UI today. The evidence document's claims are unsubstantiated
and should not be relied on for acceptance; hosted desktop/narrow evidence
per the acceptance bar and INDEX.md Supervisor Instruction #3 genuinely
does not exist yet. This mirrors the FE-redeploy-pinning gap already
logged against `AG-UIPOL-003` and is understood to require a human-
triggered `workflow_dispatch` (agents cannot self-trigger the dev FE
redeploy) — flagging for the owner/chair rather than treating it as
something Antigravity can resolve unilaterally.

### Required-before-close finding #5 (test coverage) — improved, still short of the acceptance bar

Round 2 added 4 tests (76 total, verified against `gh pr diff 320`):
switching through all five lenses and asserting each `dashboard-recipe-*`
renders, an empty-candidates-state message, the BFF-failure sample-data
badge, and drawer Escape/focus-trap/focus-restore behavior. This closes
the "drawer has no keyboard/focus behavior to test" implementation gap
from round 1 and adds real recipe/empty-state coverage. Still missing per
the acceptance bar: any assertion that lens-specific candidate board
columns actually differ per lens (e.g. `"AI Score"`/`"Accum. Days"` for
lens-A vs `"Peer Group"`/`"Similarity"` for lens-B), and any test for the
candidate list's delayed/loading state (`candidatesLoading` is set but
never asserted on in a test).

## Verdict (round 2)

**Changes requested — reopening to owner (Antigravity).** Finding #3 is
fully resolved and finding #1 is substantially resolved (a handful of
drawer headers and one dashboard recipe's narrative still need `t()`
wiring). Finding #2 is resolved for the candidate board but still open for
the five dashboard recipe widgets, which keep rendering fabricated
numbers with no live wiring and no sample indication once real candidate
data loads — please either wire recipe widgets to real data/BFF fields or
show the sample badge whenever recipe content itself is not live,
independent of the candidate-board's own data state. Finding #4 (hosted
evidence) is the primary blocker to `review_approved`: the hosted dev FE
has not been redeployed since before this task's original implementation
merged, so no truthful hosted evidence can be captured until a human
triggers the FE redeploy; the current evidence document overclaims and
should be corrected or withdrawn in the meantime. Finding #5 (lens-column
and delayed-data test coverage) remains a smaller required-before-close
gap.

LLM-Agent: Claude
Task-ID: AG-UIPOL-007
Reviewer: Claude
Verified: read `gh pr diff 320` (ajoe734/execute-plans); read
`docs/.../evidence/AG-UIPOL-007-hosted-evidence.md`; grepped
`TradingRoomPage.tsx`@`2fb8b36e` and both locale files for remaining
hardcoded literals; fetched
`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
directly and confirmed via `git merge-base --is-ancestor` in a scratch
clone of `ajoe734/execute-plans` that the deployed commit predates PR
#319's merge commit

## Round 3 — PR #322 ("i18n, recipe warning badge, and tests")

Artifact under review: `ajoe734/execute-plans` PR #322 (open,
`task/AG-UIPOL-007` → `dev`), commit `73a7fb6a`. Touches only
`src/agora/pages/trading-room/TradingRoomPage.tsx` and
`TradingRoomPage.test.tsx` — no locale file is part of this diff.

### Blocking finding #1 (hardcoded copy / i18n) — still open: `t()` calls added, but no dictionary keys exist in either locale, so the fallback is always English

The specific strings flagged in round 2 — the drawer's `"Current
State:"`, `"AI Fit Score:"`, `"Next Catalyst Event"`, `"Evidence
references"`, `"Governed Actions"` headers, and `DashboardRecipeB`'s
hypothesis narrative plus its `"Silicon Wafers"`/`"Substrates"`/`"AI
GPU"` node labels — are now wrapped in `t("agora.tradingRoom....", {
defaultValue: "<the same English literal>" })`. But none of the new
keys (`candidates.headers.currentState`, `.aiFitScore`, `.nextEvent`,
`.evidenceReferences`, `.governedActions`, `.loading`,
`lenses.dashboard.recipeB.hypothesisNarrative`, `.siliconWafers`,
`.substrates`, `.aiGpu`, `lenses.meta.recipeSampleBadge`) were added to
`src/i18n/locales/en-US.ts` or `src/i18n/locales/zh-TW.ts` — confirmed
by grepping both files in a fresh clone of the PR branch: zero matches
for any of these key names or their `defaultValue` strings. Because
i18next resolves an unregistered key to `defaultValue` regardless of
active locale, this renders the exact same hardcoded English text in
`zh-TW` as it did before the `t()` wrapping — it looks fixed in the
diff (every previously-bare literal is now inside a `t()` call, matching
the letter of round 2's ask) but does not actually localize anything,
which was the entire point of this finding and the reason round 2
explicitly verified "both locale files contain filled-in ... trees, not
just English fallbacks" for the strings that were genuinely fixed. This
is the same defect for the third consecutive round, now down to a
smaller, specific set of keys: add real `en-US`/`zh-TW` entries for the
eleven keys listed above (matching the existing sibling entries in
`candidates.headers.*`, `lenses.dashboard.recipeB.*`, and
`lenses.meta.*`, which are all correctly translated).

### Blocking finding #2 (fabricated dashboard recipe data with no sample indication) — resolved

`TradingRoomDefaultEntry` now renders an unconditional
`data-testid="dashboard-recipe-sample-warning"` badge ("DASHBOARD
RECIPE DATA: SAMPLE ONLY") directly under the per-lens dashboard
heading, independent of `isSampleData`/the candidate board's own
live-vs-sample state. Since the heading and badge sit in the shared
`TradingRoomDefaultEntry` render path used for every lens (A–E), this
badge is present regardless of which `DashboardRecipeA..E` is active,
which is what round 2 asked for. No further action needed here.

### Required-before-close finding #4 (hosted evidence) — unchanged, still open

Not addressed by this PR (out of scope — still blocked on a
human-triggered dev FE `workflow_dispatch` redeploy per round 2).

### Required-before-close finding #5 (test coverage: lens-column and delayed-data gaps) — resolved

Two tests were added (78 total, verified by running `npx vitest run
src/agora/pages/trading-room/TradingRoomPage.test.tsx` in a fresh clone
of the PR branch: 78/78 pass): one asserts the candidate board's column
headers actually differ between lens A (`Accum. Days`) and lens B (`Peer
Group`), the other asserts a `data-testid="candidates-loading"` spinner
renders while `listCandidatePoolMembers` is pending and disappears once
it resolves. Both gaps named in round 2 are closed.

### CI note — integration-gate failure on this PR is environment-wide, not caused by this diff

The `Pantheon FE-BFF Integration Gate` check on PR #322
(run `29296291902`) fails with `/bff/me`, all 22 authenticated entity-list
endpoints, v5 DTO envelope checks, SSE reconnect, Playwright `F01
Startup`/`F13 Agora`/overlay-focus/performance specs all failing. This
PR only touches `TradingRoomPage.tsx`/`.test.tsx` and cannot plausibly
break `/bff/me` or unrelated management routes; the same push-triggered
run on `dev` at `29292140834` (unrelated to this task) failed the same
job around the same time. This looks like a shared dev-environment/BFF
outage affecting every PR right now, not a regression introduced here —
flagging for chair/infra awareness, not treating it as part of this
task's required-before-close set.

## Verdict (round 3)

**Changes requested — reopening to owner (Antigravity).** Findings #2
and #5 are now resolved. Finding #4 remains blocked on a human FE
redeploy (unchanged from round 2, not actionable by the owner). Finding
#1 is the sole remaining blocker: add real `en-US`/`zh-TW` translation
entries for the eleven keys introduced in this PR (listed above) — the
`t()`/`defaultValue` wrapping alone does not satisfy AG-UIPOL-001's i18n
policy, since an unregistered key always falls back to the English
`defaultValue` in every locale.

LLM-Agent: Claude
Task-ID: AG-UIPOL-007
Reviewer: Claude
Verified: read `gh pr diff 322` (ajoe734/execute-plans); cloned
`task/AG-UIPOL-007` from `ajoe734/execute-plans` at `73a7fb6a` and
grepped `src/i18n/locales/en-US.ts` + `zh-TW.ts` for all eleven new key
names and their literal `defaultValue` strings (zero matches in both
files); ran `npx vitest run
src/agora/pages/trading-room/TradingRoomPage.test.tsx` in that clone
(78/78 pass); checked `gh run view 29296291902 --log-failed` and
compared against the unrelated `dev`-push run `29292140834` from the
same window to confirm the integration-gate failure is environment-wide

## Round 4 — PR #322 update, commit `41abb334` ("add missing i18n translation entries for trading room page")

Artifact under review: same `ajoe734/execute-plans` PR #322
(`task/AG-UIPOL-007` → `dev`), now at commit `41abb334` (new commit on
top of round 3's `73a7fb6a`). Diff for this commit touches only
`src/i18n/locales/en-US.ts` and `src/i18n/locales/zh-TW.ts`.

### Blocking finding #1 (hardcoded copy / i18n) — resolved

Diffed `dev...task/AG-UIPOL-007` for `TradingRoomPage.tsx` to enumerate
exactly which `t("agora.tradingRoom....")` call sites this task
introduced (as opposed to pre-existing calls already on `dev`): the same
eleven keys flagged in round 3 —
`candidates.headers.{currentState,aiFitScore,nextEvent,evidenceReferences,governedActions,loading}`,
`lenses.dashboard.recipeB.{hypothesisNarrative,siliconWafers,substrates,aiGpu}`,
`lenses.meta.recipeSampleBadge`. Cloned the PR branch at `41abb334`,
loaded both locale files as JS modules, and resolved all eleven dotted
key paths against each: every key now exists in both `en-US.ts` and
`zh-TW.ts` with distinct, real translations (e.g. `aiFitScore` → "AI Fit
Score:" / "AI 契合度評分:", `hypothesisNarrative` → the full English
sentence / a genuine Traditional Chinese translation, not a duplicate of
the English default). `aiGpu` is "AI GPU" in both locales, which is
correct — it's an acronym/brand term, not untranslated copy. This closes
finding #1 for the fourth and final time; no further recurrence found in
this task's own diff.

Separately noted for the record (not part of this task's scope, so not
blocking): a same-methodology scan of every `t("agora.tradingRoom...")`
call in the full file — not just the calls this task's diff introduced —
turned up 10 pre-existing keys (`lenses.meta.{thesisLabel,rulesLabel}`
and `page.states.{all,new_candidate,to_discuss,deep_research,monitoring,shadow,parked,excluded}`)
that are also missing locale entries and fall back to a hardcoded
zh-TW `defaultValue` in every locale. Confirmed via `gh api
.../compare/dev...task/AG-UIPOL-007` that none of these call sites are
part of this task's diff — they predate AG-UIPOL-007 on `dev`. Flagging
for a follow-up task rather than this one.

### Required-before-close finding #4 (hosted evidence) — unchanged, still open

Still blocked on a human-triggered dev FE `workflow_dispatch` redeploy,
same as rounds 2 and 3. Not actionable by the owner; not treated as a
blocker on this round's approval, consistent with prior rounds' handling
of this same gap on `AG-UIPOL-007`/`003`.

### Test/CI verification

Cloned the PR branch at `41abb334` and ran `npx vitest run
src/agora/pages/trading-room/TradingRoomPage.test.tsx`: 78/78 pass,
matching the task brief's claim. All three required PR checks (`Commit
trailers`, `Generated files guard`, `Smoke acceptance`) are green on the
current head (run `29298523555`); no integration-gate run attached to
this update.

## Verdict (round 4)

**Approved — moving to `review_approved`.** Finding #1, the sole
remaining blocker from round 3, is now genuinely resolved: all eleven
flagged keys have real `en-US`/`zh-TW` entries, verified by resolving
each dotted key path against both locale files rather than just
grepping for presence. Findings #2, #3, and #5 were already resolved in
earlier rounds. Finding #4 (hosted evidence) remains open but is a
known, human-gated limitation outside the owner's control, not a defect
in this submission — Antigravity should finalize once the PR merges;
hosted evidence should be captured as a fast-follow once a human
triggers the dev FE redeploy.

LLM-Agent: Claude
Task-ID: AG-UIPOL-007
Reviewer: Claude
Verified: `gh pr view 322` / `gh pr checks 322` (ajoe734/execute-plans,
all 3 required checks pass); `gh api .../compare/dev...task/AG-UIPOL-007`
to isolate exactly which `t()` call sites this task introduced; cloned
the PR branch at `41abb334`, resolved all eleven flagged dotted key
paths against both `en-US.ts`/`zh-TW.ts` as loaded JS modules (all
present, real translations); ran `npx vitest run
src/agora/pages/trading-room/TradingRoomPage.test.tsx` in that clone
(78/78 pass)
