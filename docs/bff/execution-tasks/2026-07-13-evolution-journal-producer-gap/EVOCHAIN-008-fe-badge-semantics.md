# EVOCHAIN-008 — FE data-source badge semantics (live-degraded vs snapshot)

Status: implemented, PR #298 (`ajoe734/execute-plans`) open for
re-review after round-3 fixes for round-2 changes-required

Owner: Claude · Reviewer: Codex

Gap spec: `docs/04/pantheon_evolution_journal_producer_gap_2026-07-13/EVOLUTION_JOURNAL_PRODUCER_GAP.md`
(§ "Read-side honesty (EVOCHAIN-007, -008, -009)", root cause 6)

Execution packet: `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md`

## Scope

`ajoe734/execute-plans` — `src/platform/components/TopBar.tsx`, its test
(`TopBar.test.tsx`), and the locale strings it introduced
(`src/i18n/locales/en-US.ts`, `zh-TW.ts` — `topbar.dataSource.live_degraded`
and `topbar.degradedSurfaces`, both added by this task, not pre-existing;
see § "Changed files" below for the correction to an earlier version of
this record).

TopBar's data-source badge previously collapsed every non-"ok" surface
into a single "SNAPSHOT DATA" label, even when the underlying surface was
sourced from a live pathway (`bff_composed`/`service_client`) that was
merely stale/degraded. `SNAPSHOT DATA` must be reserved for surfaces
genuinely backed by a snapshot (`local_snapshot`/`missing`/`unverifiable`,
or an unrecognized/unspecified source); a degraded-but-live surface must
badge as `LIVE (PARTIALLY DEGRADED)` / `LIVE（部分降級）` and name the
affected surface(s).

## Branch / PR

- Branch: `task/EVOCHAIN-008` (execute-plans, from `dev`)
- PR: `ajoe734/execute-plans#298` — "EVOCHAIN-008: distinguish
  live-degraded from snapshot badge"
- Commits:
  - `2f23ce6` — initial three-state classification (live / live_degraded /
    snapshot) across `classifyEnvelopeSource`, `classifySurfaceValue`,
    `classifyShellSummarySurfaces`. **Adds** `topbar.dataSource.live_degraded`
    and `topbar.degradedSurfaces` to both `en-US.ts` and `zh-TW.ts` (an
    earlier version of this evidence record incorrectly said these keys
    pre-existed and were unchanged by this task; the round-2 re-review
    correctly flagged that as internally inconsistent with the PR diff).
  - `a690879` — round-2 fixes for first-round reviewer changes-required
    (GitHub review `PRR_kwDOSVbR4s8AAAABF0i-1A`, 2026-07-13):
    1. `hydrateFromFullLists()` badged a fully-live full-list recovery as
       `SNAPSHOT DATA` (`setSource("degraded")` unconditionally after
       `classifyListSource` returned `"live"`). Now badges
       `LIVE (PARTIALLY DEGRADED)` and names `shell_summary` as the
       degraded surface that triggered the fallback.
    2. `classifyEnvelopeSource()` short-circuited to `"snapshot"` for any
       envelope-level `meta.staleness`/`meta.degradation` flag before
       examining per-surface `source`. Now resolves each surface's own
       source under the envelope-wide flag.
    3. `classifyShellSummarySurfaces()` skipped every `status: "ok"`
       surface before checking its source, so an inconsistent
       `status: ok, source: local_snapshot` payload rendered as live. An
       explicit snapshot source now dominates a co-reported `"ok"` status.
  - `c9e6e0b` — round-3 fixes for round-2 re-review changes-required
    (GitHub review `PRR_kwDOSVbR4s8AAAABF2cE0A`, 2026-07-13):
    1. **P1** — `classifyShellSummarySurfaces()` treated every degraded
       source outside the `bff_composed`/`service_client` allowlist as
       snapshot, so the real production shell-summary shape (child
       surfaces sourced from `service_store`/`bff_cheap_count` — see
       `services/control-plane/bff/test_mgmt_load_002_shell_summary.py`
       in this repo) still set `sawSnapshot=true` even though no surface
       was snapshot-backed. `LIVE_SURFACE_SOURCES` now also includes
       `service_store` and `bff_cheap_count`.
    2. **P1** — `shellSummaryStatus(summary) === "ok"` at the TopBar
       effect call site (previously `TopBar.tsx:133-144`) bypassed
       `classifyShellSummarySurfaces()` entirely, so
       `shell_summary: {status: "ok", source: "local_snapshot"}` rendered
       live. The call site now always resolves through the classifier
       (which itself still returns `"live"` for the ordinary healthy
       case, so behavior is unchanged when no surface reports an
       inconsistent snapshot source).
    3. **P2** — the degraded full-list fallback
       (`hydrateFromFullLists`, non-live branch) returned
       `clearCounts(listSource, listDegradedSurfaces)` without including
       `shell_summary`, the surface whose unavailability triggered the
       fallback in the first place. `shell_summary` is now always
       included in that tooltip.
    - Extended `TopBar.test.tsx` with 4 new/updated cases: the production
      multi-surface shell-summary shape, the primary-surface
      `status: ok` / `source: local_snapshot` bypass, and the
      full-list-fallback tooltip naming `shell_summary` alongside the
      list envelopes' own degraded surfaces.

## Changed files (execute-plans, PR #298)

- `src/platform/components/TopBar.tsx`
- `src/platform/components/TopBar.test.tsx`
- `src/i18n/locales/en-US.ts` — adds `topbar.dataSource.live_degraded`,
  `topbar.degradedSurfaces` (commit `2f23ce6`)
- `src/i18n/locales/zh-TW.ts` — adds `topbar.dataSource.live_degraded`,
  `topbar.degradedSurfaces` (commit `2f23ce6`)

## Local verification

Run inside the `execute-plans` checkout:

```sh
npx vitest run src/platform/components/TopBar.test.tsx           # 14 passed
npx vitest run src/lib/bff-v1/__tests__/shellSummary.test.ts      # 4 passed
npx eslint src/platform/components/TopBar.tsx src/platform/components/TopBar.test.tsx   # clean
npx tsc --noEmit -p tsconfig.app.json   # no TopBar-related errors; ~99 pre-existing
                                          # repo-wide errors unrelated to this change
                                          # (matches both prior reviewer notes that a
                                          # full tsc pass is not reproducible at current
                                          # head for unrelated reasons)
```

## EVOCHAIN-008 acceptance checklist (`ai-status.json`)

1. "degraded live-composed responses render a live-degraded badge naming
   the degraded surfaces" — met; unit-verified (`TopBar.test.tsx`, cases
   for `bff_composed`/`service_client` degraded surfaces, the production
   multi-surface shape with `service_store`/`bff_cheap_count`, the
   full-list fallback recovery, and envelope-level degradation with a
   live source).
2. "SNAPSHOT DATA appears only when data is actually served from a
   snapshot source" — met; unit-verified (`local_snapshot` case, the
   explicit-snapshot-source-dominates-status case for both a non-primary
   surface and the primary `shell_summary` surface itself, and the
   production-shaped degraded payload that must NOT render
   `SNAPSHOT DATA`).
3. "zh-TW and en-US locales updated consistently" — met. Corrects an
   earlier version of this record: the `live_degraded` /
   `degradedSurfaces` locale keys were **added by this task** in commit
   `2f23ce6` (not pre-existing before it), identically in both
   `src/i18n/locales/en-US.ts` and `zh-TW.ts`; no locale drift between
   the two files. Not modified further by the round-2 (`a690879`) or
   round-3 (`c9e6e0b`) fix commits, which only touched classification
   logic and tests.
4. "npm run audit:render passes and hosted evidence shows the new badge
   on the Evolution Journal page" — **not yet satisfied, and cannot be
   honestly satisfied before this PR merges**: `audit:render` and any
   hosted screenshot both require a running deploy that already serves
   this fix, and PR #298 is still open pending reviewer re-approval.

## EVOCHAIN-008 ↔ EVOCHAIN-011 acceptance/dependency resolution

Per `INDEX.md`, `EVOCHAIN-011` (dev deploy + closeout, wave 3,
Codex2/Human-Ops) depends on `EVOCHAIN-008` among others, and the
packet's global acceptance requires hosted/live curl evidence archived
for deploy tasks. The round-2 re-review correctly flagged that this
dependency does not let `EVOCHAIN-008` silently satisfy or waive its own
per-task acceptance item 4 by pointing at `EVOCHAIN-011` — `EVOCHAIN-011`
depending on `EVOCHAIN-008` means the reverse (`EVOCHAIN-011` inheriting
`EVOCHAIN-008`'s open item) is not a valid substitute for `EVOCHAIN-008`
closing honestly.

Resolution adopted here (no formal re-slice of `INDEX.md`/`ai-status.json`
acceptance, since that requires a planning-mode consensus step this task
is not the right lane to unilaterally trigger): acceptance item 4 remains
an explicit **EVOCHAIN-008 owner-finalization requirement**, not a
delegated/waived item folded silently into `EVOCHAIN-011`'s later,
broader packet-level check. Concretely:

- `EVOCHAIN-008` may not move to `done` on local unit verification alone.
- Once PR #298 merges and `dev` redeploys, the `EVOCHAIN-008` owner must
  run `npm run audit:render` against the hosted dev FE base URL and
  capture a screenshot of a genuinely live-degraded Evolution Journal /
  shell badge before running `ai-status.sh done EVOCHAIN-008`.
- If `EVOCHAIN-011` closeout is reached first without that screenshot
  having been produced, `EVOCHAIN-008`'s owner must be re-engaged before
  `EVOCHAIN-011` closeout is accepted — `EVOCHAIN-011`'s own hosted
  proof step does not automatically produce or archive the
  badge-specific screenshot this item asks for.

## Residual risk

- **Hosted evidence for acceptance item 4 is outstanding and is retained
  as an EVOCHAIN-008 owner-finalization requirement** (see resolution
  above), not delegated away. Expiry: at `EVOCHAIN-008` owner finalization,
  after PR #298 merges and dev redeploys.
- `EVOCHAIN-008` remains blocked from `done` on two things: PR #298
  merging (owner cannot self-merge; awaiting reviewer re-approval on the
  round-3 fixes above), and the hosted audit/badge screenshot once
  merged.
