# PPL-ALLOC-005 Review — Frontend Create Paper Persona Flow

Reviewer: Claude
Owner: Codex
PR reviewed: `ajoe734/execute-plans#247` (head `f3108af92d809de75987de1d13f75e00566d3095`)

## Scope reviewed

- `src/lib/bff-v1/personas.ts` / `personas.test.ts`: `createPersona` now calls
  `POST /bff/management/personas/create-paper-bundle` and throws
  `PaperPersonaBundleIncompleteError` unless the response is
  `state: "paper_running"` with `paperLedgerId` and `runtimeBindingId`.
- `src/management/components/write/createEntity.ts` / `.test.ts`: removed the
  `NOT_IMPLEMENTED` → `writeOverlay` degraded-fallback path for personas, so a
  failed/incomplete bundle can no longer be silently reported as a local
  "success".
- `src/management/components/write/EntityCreateDrawer.tsx`: added
  `onCreateFailed`, relabeled the create button/title to "Create Paper
  Persona" for `entity === "persona"`.
- `src/management/pages/ObjectListPage.tsx`: on `PaperPersonaBundleIncompleteError`
  (or an error with a `details.persona_id`/`failed_step`), navigates to
  `.../:id/onboarding?repair=1&failed_step=...`.
- `src/management/pages/PersonaOnboarding.tsx`: header/comment text renamed
  from "Persona Onboarding Wizard" to "Paper Persona Setup Repair".

## Findings

### 1. Blocking — PR targets the wrong base branch; CI is red

`gh pr view 247 --repo ajoe734/execute-plans --json baseRefName` reports
`base = main`. `main` is a stale branch (1791 commits) that diverged from
`dev` (2151 commits, the repo's actual `default_branch` and the base of
every other recently merged PR, e.g. #244–#246) 360 commits back at
`e5163ad4`. `mergeStateStatus` is `UNSTABLE`.

Effect: the release-gate run (`actions/runs/29139413010`) is failing on
gates unrelated to this diff:

- Gate 1 `npm run test`: `src/lib/bff/__tests__/client.test.ts` fails 4
  cases (`paths.oodaPackets is not a function`, `paths.oodaPacket is not a
  function`, `paths.evolutionMutationReview is not a function`). That test
  file's `dev` copy is 220 lines with no `oodaPackets`/`evolutionReviews`
  suites at all; the PR head's copy is 401 lines and includes those suites
  — content this task never touched, inherited only because the branch's
  history doesn't line up with the branch it's being merged toward.
- Gate 2 `contract:drift`: `Pantheon Agora bundle is not reproducible`
  (openapi hash mismatch) — same root cause.
- Gate 7 release decision: FAIL (aggregates the above).

The PR body's validation section ("npm test ... 6 passed") only reports the
two new task-scoped test files run locally; it does not reflect the full
suite CI actually runs, so the red gate was not caught before this went to
review.

Required change: retarget PR #247 to `dev` (or close and reopen a branch cut
from current `dev` tip) and get the `integration-gate` check green before
this returns to review.

### 2. Acceptance gap — `PersonaOnboarding` is not actually repair-only

Acceptance criterion: *"PersonaOnboarding is repair-only."* Spec
(`PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md` §route table): *"Rename/copy as
setup repair; use only for incomplete bundles or failed creation steps."*

The PR only renames the page title/description
(`src/management/pages/PersonaOnboarding.tsx`); it does not gate usage:

- The component never reads the `repair=1` / `failed_step` query params that
  `ObjectListPage`'s `onCreateFailed` handler now sends — it still always
  starts at step 1 and requires the operator to re-run every step manually.
- There is no check against the already-loaded `persona` (fetched via
  `getPersona(id)` in the same file) to detect a persona that is already
  `paper_running` with a valid `paperLedgerId`/`runtimeBindingId` and short-
  circuit/redirect instead of letting the wizard re-run
  `AdvanceLifecycle` → binding → deployment-plan → approval → `StartRuntime`
  against an already-complete bundle.
- `src/management/components/persona/PersonaReadinessCard.tsx` still links
  to the same route unconditionally as a generic "open wizard" action,
  so the route remains reachable as a general-purpose flow, not only for
  repair.

Concrete failure scenario: an operator opens
`/management/personas/<already-complete-id>/onboarding` (e.g. via the
readiness card, or a stale bookmark) and can re-run the full 5-step wizard
against a persona that already has a paper ledger, deployment plan, and
running runtime binding — creating a second binding/deployment-plan/runtime
for the same persona. That's the exact scenario "repair-only" was meant to
prevent.

`PersonaReadinessCard.tsx` is outside this task's declared artifact scope
(`execute-plans:src/management/pages`, `execute-plans:src/lib/bff-v1`), so
the fix does not need to touch it, but `PersonaOnboarding.tsx` (in scope)
should at minimum refuse/redirect when the loaded persona is already a
complete paper bundle and no `repair=1`/`failed_step` param is present.

## Verification performed

- `gh pr diff 247 --repo ajoe734/execute-plans` — full diff read.
- `gh pr checks 247` / `gh run view 29139413010 --log-failed` — confirmed
  Gate 1/2/7 failures and root-caused them to the `main` vs `dev`
  divergence (`git merge-base origin/main origin/dev` → `e5163ad4`,
  360 commits back).
- Read `services/control-plane/bff/main.py` `bff_create_persona` /
  `bff_create_paper_persona_bundle` (already merged via PPL-ALLOC-002) to
  confirm the `paperLedgerId`/`runtimeBindingId` camelCase field names the
  new `createPersona` client checks match the BFF's actual response shape.
- Read `src/management/pages/PersonaOnboarding.tsx` and
  `src/App.tsx` / `registry.tsx` route wiring to confirm there is no other
  guard preventing non-repair use of the onboarding route.

## Verdict

Reopened, not approved. Required before re-review:

1. Retarget/rebase PR #247 onto `dev` and get `integration-gate` green.
2. Add an actual repair-only guard to `PersonaOnboarding.tsx` (redirect or
   block re-running the wizard when the loaded persona already has a
   complete paper bundle and the request isn't an explicit repair of a
   named `failed_step`).
