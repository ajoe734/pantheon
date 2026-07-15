# AG-UIPOL-011: Narrow responsive task parity

Status: completed. The frontend implementation merged through
`execute-plans` PR #344 at
`cbc6877630e0af087cd4d119da6024d816e4e495`, with drawer gates hardened in
PR #345 and PR #346. Hosted acceptance has been completed and verified against
the dev deployment.

Priority: 6 — cross-surface behavioral gate after desktop IA ownership is clear.

## Matrix coverage

`parity-matrix.md` rows G-06, PF-07, and SRV-03. It also verifies the narrow
form of every surface changed by AG-UIPOL-006–010.

## Design authority

- BASE §4.2
- V4 Screen 10
- V6 §16F

The recovered source contains no pixel-authoritative phone artboard or mobile
CSS. This task therefore enforces the documented behavior: prioritize
reminders, decisions, task progress, and risk; stack cards intentionally;
collapse controls; and use a full-width drawer.

## Scope

Define and enforce one narrow interaction contract across the three Agora tabs
and all drawers. Remove extreme traversal, horizontal clipping, and focus/
containment failures without inventing a separate mobile product.

Primary repo: `ajoe734/execute-plans@dev`.

## Work

1. Establish tested breakpoints and a shared narrow shell: contained body,
   scrollable tabs where needed, one scroll owner, sticky decision/task
   controls, safe-area handling, and keyboard/focus restoration.
2. Trading Room: collapse lens/workspace navigation, surface pending decisions
   and risk first, stack cards by operator priority, and avoid rendering every
   diagnostic before the active task.
3. Strategy Workshop: replace the desktop three-column squeeze with an
   intentional conversation/rail selector; keep composer and next question
   reachable without traversing the entire card history.
4. Performance: prioritize exceptions, intervention, and selected-strategy
   outcome; replace the clipped minimum-width desktop table with a narrow
   comparison/detail pattern.
5. Drawers: full viewport width, trapped focus while open, inert/contained
   background, visible close/apply actions, and restored trigger focus.
6. Add screenshot-height and horizontal-overflow budgets so regressions like
   the audited workflow pages reaching 16,951 physical pixels fail before
   deploy.

## Non-goals

- A native app or a second mobile-only information architecture.
- Replacing the desktop content work owned by AG-UIPOL-006–010.
- Pixel matching an artboard that does not exist.

## Implementation record

The shipped contract uses a shared `900px` implementation breakpoint. That
number is an application contract, not recovered pixel truth: the source only
authorizes the behavioral priorities described above.

- The shell owns vertical scrolling, contains horizontal overflow, preserves
  safe-area space, and turns the desktop Servant rail into a modal sheet on
  narrow screens.
- Trading Room puts current task, decision, and risk context before diagnostic
  depth; lens navigation collapses; candidates become cards; Candidate Review
  and Servant use focus-contained full-width overlays.
- Strategy Workshop switches between conversation and readiness/next-question
  panes instead of squeezing the three-column desktop layout.
- Performance switches between decisions, selected outcome, and strategy cards
  rather than clipping the desktop comparison table.
- Winner Branch workspace grid coordinates remain desktop-authoritative. The
  narrow projection is a deterministic vertical card order, with proposal,
  edit, widget-library, revision, history, and action controls kept reachable.

Local delivery validation before merge:

- TypeScript: `npx tsc --noEmit`.
- Production build: live BFF mode, strict fallback, real writes and dev-stub
  writes disabled.
- Vitest: 151 files and 1,387 tests passed; the five focused Agora suites passed
  137 tests.
- Local Playwright: `e2e/29-persona-interaction.spec.ts`, 10 tests passed
  against the production preview.
- Task-scoped ESLint: zero errors and one existing Fast Refresh warning.

The repository-wide lint and contract commands still expose two unrelated
base-branch defects: two `no-explicit-any` errors in `e2e/evochain009.spec.ts`
and a pre-existing `openapi/agora_v1_3.openapi.yaml` bundle hash mismatch.
Neither file is changed by AG-UIPOL-011; the task-scoped and required branch
gates remain green.

## Acceptance

- Hosted evidence at 390x844, 768px, 1280px, and 2560px covers the three tabs,
  workspace proposal/edit/revision/history, Candidate Drawer, and Servant.
- No page body horizontal overflow; one intentional vertical scroll owner per
  state; no unreachable action beneath a fixed overlay.
- Narrow first viewport exposes current task/decision/risk rather than source
  diagnostics or thousands of pixels of preceding cards.
- Drawer focus, Escape/close, background inertness, and trigger restoration
  pass automated accessibility tests.
- Responsive Playwright tests pin screenshots to a hosted deploy SHA and assert
  containment/height budgets using live, not intercepted, Agora data.
