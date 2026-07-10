# MGMT-OPS-009 - Claude Review

Status: approved for owner closeout

Reviewer: Claude

Reviewed on: 2026-07-10

## Scope Reviewed

- `execute-plans` PR `#235`: `MGMT-OPS-009: fix Persona Fleet mutation links and Evolution Journal fallbacks`
  - `src/management/pages/oversight/personaFleetLinks.ts`
  - `src/management/pages/oversight/_core.tsx`
  - `src/management/pages/oversight/_core.test.ts`
- `docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/MGMT-OPS-009-persona-fleet-evolution-links.md`
- `docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10/PERSONA_FLEET_MUTATION_EVOLUTION_GAP.md`

## Delivery Evidence

- Task delivery PR: `ajoe734/execute-plans#235` (base `dev`, head `task/MGMT-OPS-009`, `MERGEABLE`, `integration-gate` running at review time)
- `evolutionJournalFocus.ts` was intentionally left unchanged: `_core.tsx` now
  sanitizes `persona`/`mutation_review` query params to `""` before they reach
  `filterEvolutionJournalRowsForFocus`, so the nan/undefined guard lives at the
  call site and the shared filter helper did not need to change.

## Reviewer Findings

No blocking review findings.

`personaFleetMutationHref` now prefers a valid `mutation_entry_id` /
`evolution_entry_id` (rejecting `nan`, `undefined`, `null`, empty strings, and
`YYYY-MM-DD`-shaped values), falls back to a cleaned canonical/evolution href,
and only emits a `persona=&source=fleet_summary` link when the fallback context
is actually useful (`lastMutationKind === "fleet_summary"` or a real non-`nan`
timestamp). Rows with no useful signal correctly return `null` instead of a
misleading link.

`fallbackEvolutionEntryFromFleet` renames the synthetic card from "mutation
summary" to "Persona Fleet status summary", stops writing the date into
`action_type` (`action_type: undefined`) so it renders under the `landed`
field instead of `Action`, and builds its `id` from a sanitized `asOf` value
instead of a raw `nan`-prone string.

`EvolutionJournalPage` strips `nan`/`undefined` out of the `persona` and
`mutation_review` query params before use, and replaces the old
`focusedFmt`/`focusMissingFmt` i18n calls with an explicit `focusText` that
distinguishes a formal match count (`N 筆正式演化項目`) from a fallback state
(`fleet summary fallback · 無正式 mutation id`), matching the target-contract
banner text in the gap doc verbatim.

Verified against the task's specific acceptance list:

- Fleet `最近 MUTATION` stays linked for formal and useful-fallback rows, and
  is unlinked only when there is truly no useful data.
- Evolution Journal shows the exact formal entry when a formal id is present
  (fallback synthesis is skipped whenever `mutationFocus` is set).
- Fallback content is labeled "Persona Fleet status summary" and states no
  formal mutation id is available.
- No code path can emit `mutation=nan`, `mutation: nan`, or `source=nan` -
  confirmed by the new `_core.test.ts` cases (formal id, fallback-only,
  invalid-id suppression, no-data/no-link).
- Date values only ever populate `occurred_at`/`landed`, never `action_type`.

Non-blocking note for the owner: the new focus-banner and cell-fallback text
(`已聚焦 Persona: ...`, `無正式 mutation`, `無資料`, `無匹配項目`) is now
hardcoded Traditional Chinese instead of going through the `t()` i18n helper
that the rest of this file uses (and that `en-US.ts` previously provided a
translation for via `mgmt.evolution.focusedFmt`/`focusMissingFmt`). This
matches the literal wording required by the gap doc's target-contract
examples, but English-locale users will now see Chinese text in this banner.
Worth a follow-up i18n pass; not required by this task's acceptance list.

Hosted click-map / screenshot evidence for the Persona Fleet -> Evolution
Journal path is explicitly out of scope here; that is
`MGMT-OPS-010-hosted-click-map-regression.md` (depends on this task, reviewed
by Codex).

## Verification

```sh
cd execute-plans && npm ci --prefer-offline --no-audit --no-fund
npx vitest run src/management/pages/oversight/_core.test.ts
npx tsc --noEmit -p tsconfig.json
npx vitest run
```

Result: `_core.test.ts` 31 passed (including the 4 new MGMT-OPS-009 cases and
the updated fallback-link expectation); `tsc --noEmit` clean; full suite
`1189 passed (1189)`.

## Closeout Direction

MGMT-OPS-009 may move from `review` to `review_approved`. Owner closeout still
needs to wait for `ajoe734/execute-plans#235`'s `integration-gate` check and
merge into `dev` before running
`AI_NAME=Antigravity ./scripts/ai-status.sh done MGMT-OPS-009 ...`.
