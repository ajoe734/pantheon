# FE-SECRET-BOUNDARY-002 — Review (Claude)

- PR: ajoe734/execute-plans#329 (`task/FE-SECRET-BOUNDARY-002` -> `dev`), head `d89e9f680104650cf96f2715feab346dbc4ee5ea`
- Reviewer: Claude / Owner: Codex2

## Verdict: APPROVED with two documented pre-existing exceptions

## What changed
Relands execute-plans PR #311 (public/hosted auth boundary) after resolving the
5-file conflict that had frozen dev FE deploy. Core boundary additions:
- `src/config/publicBuildAuth.ts`: restricts `VITE_BFF_DEV_BEARER_TOKEN` compiled
  into public JS to exactly the `pantheon-dev-browser:viewer` identity; rejects
  operator/admin tokens at build/runtime.
- `src/config/publicSupabase.ts`: validates `VITE_SUPABASE_URL` /
  `VITE_SUPABASE_PUBLISHABLE_KEY`, rejecting `sb_secret_*` / service-role /
  embedded-credential values, accepting only publishable/anon keys.
- `src/lib/bff-v1/headers.ts`: wires the new validator into the browser auth
  provider's dev-bearer-token path.
- Removed committed `.env` (real Supabase publishable key + BFF URL were
  checked in); `.env.example` updated with the corrected guidance.
- Companion e2e/script updates for the auth boundary and dev-login token
  export flow.

## CI verification
- PR run 29316430470 (head `509be01e1a1a`): integration-gate FAILURE, but the
  prior 14 auth-boundary startup failures are gone, Gate 1 (lint) now PASSES,
  and F13 Agora flips FAIL -> PASS vs the dev baseline.
- Compared against same-base dev baseline run 29316287011 (`b6a5bc9311941cf7`):
  - `F01 Startup / Session Bootstrap`: FAIL on both runs, identical shape
    (8 matching specs, 6 runnable passed on both). Confirmed via job logs:
    smoke credential returns viewer capabilities
    `[metric.read, strategy.view, persona.view]` on both task and dev-baseline
    runs — pre-existing credential/environment behavior, not something this
    diff touches (no RBAC/capability config files in the diff).
  - `Gate 6 overlay focus handling`: FAIL on both runs, identical shape
    (10 focus specs failing on both). Trade Journeys assertions expect English
    strings while the rendered shell is zh-TW — a locale/content mismatch, not
    an auth-boundary regression. No i18n/locale files are touched by this
    diff.
  - Net effect of this PR vs baseline: Gate 1 and F13 move from FAIL to PASS;
    all other gate deltas are neutral or improved. No new failure category is
    introduced.
- Diff reviewed directly (`gh pr diff 329`): confirms the boundary logic only
  narrows what a public build may embed (viewer-only dev token, publishable/
  anon Supabase key only, URL credential-embedding rejected); does not touch
  locale rendering or backend RBAC/capability wiring, consistent with the
  claim that residual failures are unrelated pre-existing base/environment
  issues.

## Exceptions granted (owner: Codex, expiry: next FE-SECRET-BOUNDARY-* or
FE auth/i18n follow-up that specifically targets these)
1. `F01 Startup / Session Bootstrap` — smoke credential capability set is a
   pre-existing dev-BFF credential/environment condition, reproduced
   identically on `dev` baseline `b6a5bc9`.
2. `Gate 6 overlay focus handling` (Trade Journeys, 10 specs) — pre-existing
   locale mismatch (spec asserts English, shell renders zh-TW), reproduced
   identically on `dev` baseline `b6a5bc9`.

Both exceptions are scoped to this specific reproduction; if either
divergence changes shape (different spec count, different failure signature)
on a future run, the exception no longer applies and must be re-justified.

## Next
Owner (Codex2) to finalize per `.orchestrator/skills/task-closeout-finalization.md`
once PR #329 merges into `dev`.
