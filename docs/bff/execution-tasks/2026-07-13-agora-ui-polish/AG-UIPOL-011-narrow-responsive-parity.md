# AG-UIPOL-011: Narrow responsive task parity

Status: draft follow-up from AG-UIPOL-005. Not yet dispatched.

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
