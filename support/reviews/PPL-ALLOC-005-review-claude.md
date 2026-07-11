# PPL-ALLOC-005 Review — Frontend Create Paper Persona Flow

Reviewer: Claude
Owner: Codex2
PR reviewed: `ajoe734/execute-plans#248` (head branch `task/PPL-ALLOC-005-v2`, replaces
closed stale-main PR #247)

## Round 1 — reopened

Reviewed `ajoe734/execute-plans#247` (head `f3108af9`). Reopened instead of
approving:

1. **Blocking** — PR targeted `main` (360 commits behind `dev`, the repo's
   actual default/integration branch), so release-gate CI was red on content
   unrelated to this diff (`main`/`dev` divergence in
   `src/lib/bff/__tests__/client.test.ts` and the OpenAPI contract-drift
   check).
2. **Acceptance gap** — `PersonaOnboarding.tsx` only renamed the page copy to
   "repair"; it never read `repair=1`/`failed_step`, never checked whether
   the loaded persona already had a complete paper bundle, so the wizard
   could still be re-run against an already-`paper_running` persona (e.g. via
   `PersonaReadinessCard`'s unconditional link or a stale bookmark),
   producing a duplicate binding/deployment-plan/runtime.

Full round-1 findings and CI-failure root-causing are preserved in commit
`9837bdc06` ("PPL-ALLOC-005: record reviewer reopen"), which carried this
file's prior contents.

## Round 2 — PR #248

### Scope reviewed

- `src/lib/bff-v1/personas.ts` / `personas.test.ts`: unchanged contract from
  round 1 — `createPersona` calls `POST
  /bff/management/personas/create-paper-bundle` and throws
  `PaperPersonaBundleIncompleteError` unless the response is `state:
  "paper_running"` with `paperLedgerId` and `runtimeBindingId`.
- `src/management/components/write/createEntity.ts` / `.test.ts`: same
  removal of the `NOT_IMPLEMENTED` → `writeOverlay` fallback for personas;
  simplified the `createPersona` call site (the removed explicit
  `capitalMode`/`deploymentStage`/`liveCapitalEnabled`/etc. fields are
  already supplied by `buildEntity`'s persona defaulter in
  `src/lib/writeIntents/createDefaults.ts:58-63`, confirmed by the
  `objectContaining` assertion in `createEntity.test.ts` still passing).
- `src/management/components/write/EntityCreateDrawer.tsx`: `onCreateFailed`
  prop, "Create Paper Persona" label for `entity === "persona"`.
- `src/management/pages/ObjectListPage.tsx`: on
  `PaperPersonaBundleIncompleteError` (or a generic BFF error carrying
  `details.persona_id`/`details.failed_step`), navigates to
  `.../:id/onboarding?repair=1&failed_step=...`.
- `src/management/pages/PersonaOnboarding.tsx` (new in this round): exports
  `isCompletePaperBundle()` and `repairStepFor()`; the page now blocks
  re-running the wizard when the loaded persona is already a complete
  running paper bundle (`state === "paper_running"` with both IDs present)
  and the request isn't an explicit `repair=1` + `failed_step` request —
  showing a "Nothing to repair" card with a link back to the persona detail
  page instead. When it is a legitimate repair, `step` now defaults to the
  wizard step mapped from `failed_step` instead of always starting at 1.

### Round-1 findings — verified fixed

1. **Base branch.** `gh pr view 248 --json baseRefName` → `dev`.
   `mergeStateStatus` is `CLEAN`, `mergeable` is `MERGEABLE`. The
   `integration-gate` check (`Pantheon FE-BFF Integration Gate`,
   run `29139993234`) is green (`gh pr checks 248`).
2. **Repair-only guard.** `isCompletePaperBundle` + the `isExplicitRepair`
   check in `PersonaOnboarding()` now refuse to start the wizard against an
   already-complete bundle regardless of entry point (readiness card link,
   bookmark, etc.), which was the concrete duplicate-resource scenario from
   round 1. `PersonaOnboarding.test.ts` unit-tests both helpers directly
   (completeness detection across state/id combinations; failed-step →
   wizard-step mapping, including the `null`/unknown-step fallback to step
   1).

### New checks this round

- Confirmed the BFF's `create-paper-bundle` handler
  (`services/control-plane/bff/main.py:40117`, delegating to
  `bff_create_persona`) is synchronous/atomic — persona, binding, deployment
  plan, and runtime binding are created in one call with no partial
  `setup_incomplete` code path today. The frontend's defensive
  `PaperPersonaBundleIncompleteError` check and the `details.persona_id`
  generic-error branch in `ObjectListPage` are therefore forward-looking (for
  the saga/outbox-based creation path tracked separately under
  `LOOP-AUTO-DEP-*`) rather than exercised by the current backend, but they
  match this task's declared artifact scope
  (`execute-plans:src/management/pages`, `execute-plans:src/lib/bff-v1`) and
  do not regress current behavior — not a blocker.
- Ran the full `execute-plans` Vitest suite locally (clone of
  `task/PPL-ALLOC-005-v2`, `npm ci` + `npx vitest run`): 1205/1206 passing.
  The one failure, `useV5Live.test.tsx > serves a fresh cache hit without
  calling the loader again`, is in a file with zero diff against `dev`
  (`git diff origin/dev -- src/management/pages/v5/useV5Live.test.tsx
  src/management/pages/v5/useV5Live.ts` is empty) and passed on 4/4 reruns in
  isolation — pre-existing timing flake, unrelated to this change.
- Ran `npm run lint`: 0 errors, only pre-existing warnings (v3-deprecation,
  fast-refresh, exhaustive-deps); the only warnings touching this diff are
  two pre-existing-pattern fast-refresh notices on the two newly-exported
  helper functions in `PersonaOnboarding.tsx` (non-blocking style warning,
  not an error).
- Ran the three task-scoped test files directly: `personas.test.ts` (3),
  `createEntity.test.ts` (3), `PersonaOnboarding.test.ts` (2) — 8/8 passing,
  matching the PR body's validation section.

## Verification performed

- `gh pr view 248 --repo ajoe734/execute-plans --json baseRefName,mergeStateStatus,mergeable,statusCheckRollup`
- `gh pr diff 248 --repo ajoe734/execute-plans` — full 471-line diff read.
- `gh pr checks 248 --repo ajoe734/execute-plans` — `integration-gate` pass.
- Local clone of `task/PPL-ALLOC-005-v2`: `npm ci`, `npx vitest run` (full
  suite + focused task files + isolated reruns of the one unrelated
  failure), `npm run lint`.
- Read `services/control-plane/bff/main.py` (`bff_create_persona`,
  `bff_create_paper_persona_bundle`, `_persona_create_paper_refs`) to confirm
  the create-paper-bundle contract shape and its atomic (non-partial)
  behavior.
- Read `src/lib/writeIntents/createDefaults.ts` to confirm the persona
  defaulter already supplies the paper-mode fields `createEntity.ts` stopped
  passing explicitly.

## Verdict

**Approved.** Both round-1 blockers are fixed: PR targets `dev` with a green
integration gate, and `PersonaOnboarding` is now actually repair-only
(guarded, not just relabeled). No new blocking issues found.
