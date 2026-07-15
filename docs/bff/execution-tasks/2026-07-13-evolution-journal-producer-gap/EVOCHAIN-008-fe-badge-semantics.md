# EVOCHAIN-008 — FE data-source badge semantics (live-degraded vs snapshot)

Status: implementation merged and deployed; owner-finalization evidence
complete in Pantheon PR #3522

Owner: Codex · Reviewer: Antigravity

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

- Branch: `task/EVOCHAIN-008` (execute-plans, from `dev`; remote branch
  auto-deleted after merge)
- PR: `ajoe734/execute-plans#298` — "EVOCHAIN-008: distinguish
  live-degraded from snapshot badge"; merged 2026-07-14 05:26:44 UTC
- Final task tip: `af8a8134efb703fd29cda6f31a3033681c862ade`
- Dev merge: `89515d82f087bf10363b3a949868c480f2c15cda`
- Pantheon evidence PR: `ajoe734/pantheon#3522`
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
  - `c9af69c` — finalization refresh after synchronizing with `dev`:
    records the current 14-test focused run, changed-file ESLint result,
    and the push-event trailer false positive caused by an already-merged,
    non-task commit in the raw push range. The subsequent required trailer,
    generated-files, and smoke checks all passed before auto-merge.

## Changed files (execute-plans, PR #298)

- `src/platform/components/TopBar.tsx`
- `src/platform/components/TopBar.test.tsx`
- `src/i18n/locales/en-US.ts` — adds `topbar.dataSource.live_degraded`,
  `topbar.degradedSurfaces` (commit `2f23ce6`)
- `src/i18n/locales/zh-TW.ts` — adds `topbar.dataSource.live_degraded`,
  `topbar.degradedSurfaces` (commit `2f23ce6`)
- `docs/testing/evochain-008-round3-verification.md` — reviewer-fix and
  finalization verification record

## Local verification

Run inside the `execute-plans` checkout:

```sh
npx vitest run src/platform/components/TopBar.test.tsx src/lib/bff-v1/__tests__/shellSummary.test.ts
# 2 files passed; 14 tests passed (TopBar 10 + shellSummary adapter 4)
npx eslint src/platform/components/TopBar.tsx src/platform/components/TopBar.test.tsx   # clean
npx tsc --noEmit -p tsconfig.app.json   # no TopBar-related errors; ~99 pre-existing
                                          # repo-wide errors unrelated to this change
                                          # (matches both prior reviewer notes that a
                                          # full tsc pass is not reproducible at current
                                          # head for unrelated reasons)
```

## Merge, deployment, and hosted acceptance

- Execute-plans PR #298 merged into `dev` as
  `89515d82f087bf10363b3a949868c480f2c15cda`.
- Dev FE deploy run
  [29308549121](https://github.com/ajoe734/execute-plans/actions/runs/29308549121)
  completed successfully. Hosted `deployment.json` readback at
  2026-07-14 05:30 UTC reported:
  - `commit` / `sourceRef`: `89515d82f087bf10363b3a949868c480f2c15cda`
  - `sourceBranch`: `dev`
  - `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`
  - `VITE_BFF_REAL_WRITES=false`,
    `VITE_BFF_ALLOW_DEV_STUB_WRITES=false`, and
    `VITE_BFF_EMBEDDED_BEARER_TOKEN=false`
- Hosted render audit passed:

  ```sh
  PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io npm run audit:render
  # ✓ all 78 pages clean
  ```

- Hosted badge contract proof loaded the deployed Evolution Journal page
  from that FE host and supplied a controlled, production-shaped
  shell-summary response containing only recognized live sources:
  `shell_summary=degraded/bff_composed`,
  `pending_approvals=degraded/service_store`,
  `open_alerts=degraded/bff_cheap_count`, and
  `running_jobs=ok/service_store`. The deployed bundle rendered
  `LIVE（部分降級）`; its tooltip was
  `降級中的 surface：shell_summary, pending_approvals, open_alerts`.
  Screenshot:
  [`EVOCHAIN-008-hosted-live-degraded-badge.png`](./EVOCHAIN-008-hosted-live-degraded-badge.png).

This screenshot is deliberately recorded as a controlled hosted-bundle
contract proof, not as a claim that the dev BFF naturally reported that
exact state at capture time. The contemporaneous live BFF readback had
`running_jobs=unavailable/missing`; the deployed UI therefore correctly
classified the natural response as snapshot-backed. Together, the two
observations prove both sides of the requested semantic boundary without
misrepresenting runtime provenance.

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
   on the Evolution Journal page" — met after merge and deploy:
   `audit:render` passed 78/78 routes, deployment manifest readback pinned
   the hosted FE to merge `89515d82`, and the controlled production-shaped
   hosted-browser proof rendered the new zh-TW badge plus the named surface
   tooltip. The natural BFF snapshot case was separately read back and
   remained honestly classified as snapshot.

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
- PR #298 merged, `dev` redeployed, the owner ran `audit:render` against
  the hosted FE, and the badge-specific screenshot was produced before
  `ai-status.sh done EVOCHAIN-008`.
- The evidence remains owned by EVOCHAIN-008 rather than being inferred
  from EVOCHAIN-011's later packet-wide closeout.

## Residual risk

- At capture time, the natural dev shell summary still reported
  `running_jobs=unavailable/missing`, so the natural badge remained
  `SNAPSHOT DATA`. That is the correct safety behavior and is not an
  EVOCHAIN-008 regression. A naturally occurring all-live-source degraded
  state can be archived later as additional operational evidence, but is
  not substituted for or misrepresented by the controlled contract proof.
- The broader Evolution Journal producer/degraded-state closure remains in
  the later EVOCHAIN packet lanes; this task closes only the TopBar badge
  provenance semantics and its deployed evidence.
