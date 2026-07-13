# EVOCHAIN-008 — FE data-source badge semantics (live-degraded vs snapshot)

Status: implemented, PR #298 (`ajoe734/execute-plans`) open for
re-review after round-2 changes-required fixes

Owner: Claude · Reviewer: Codex

Gap spec: `docs/04/pantheon_evolution_journal_producer_gap_2026-07-13/EVOLUTION_JOURNAL_PRODUCER_GAP.md`
(§ "Read-side honesty (EVOCHAIN-007, -008, -009)", root cause 6)

Execution packet: `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md`

## Scope

`ajoe734/execute-plans` — `src/platform/components/TopBar.tsx` and its
test (`TopBar.test.tsx`); locale strings already existed
(`src/i18n/locales/en-US.ts`, `zh-TW.ts`, `topbar.dataSource.live_degraded`
/ `topbar.degradedSurfaces`) and were not changed by this task.

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
    `classifyShellSummarySurfaces`.
  - `a690879` — round-2 fixes for reviewer changes-required
    (GitHub review `PRR_kwDOSVbR4s8AAAABF0i-1A`, 2026-07-13):
    1. `hydrateFromFullLists()` badged a fully-live full-list recovery as
       `SNAPSHOT DATA` (`setSource("degraded")` unconditionally after
       `classifyListSource` returned `"live"`). Now badges
       `LIVE (PARTIALLY DEGRADED)` and names `shell_summary` as the
       degraded surface that triggered the fallback.
    2. `classifyEnvelopeSource()` short-circuited to `"snapshot"` for any
       envelope-level `meta.staleness`/`meta.degradation` flag before
       examining per-surface `source`, so a degraded envelope whose
       surface named a live source (`bff_composed`/`service_client`)
       still rendered `SNAPSHOT DATA`. It now resolves each surface's own
       source under the envelope-wide flag.
    3. `classifyShellSummarySurfaces()` skipped every `status: "ok"`
       surface before checking its source, so an inconsistent
       `status: ok, source: local_snapshot` payload rendered as live. An
       explicit snapshot source now dominates a co-reported `"ok"`
       status.

## Changed files (execute-plans, PR #298)

- `src/platform/components/TopBar.tsx`
- `src/platform/components/TopBar.test.tsx`

## Local verification

Run inside the `execute-plans` checkout:

```sh
npx vitest run src/platform/components/TopBar.test.tsx   # 8 passed
npx eslint src/platform/components/TopBar.tsx src/platform/components/TopBar.test.tsx   # clean
npx tsc --noEmit -p tsconfig.app.json   # no TopBar-related errors; ~99 pre-existing
                                          # repo-wide errors unrelated to this change
                                          # (matches the round-2 reviewer's note that a
                                          # full tsc pass is not reproducible at current
                                          # head for unrelated reasons)
```

## EVOCHAIN-008 acceptance checklist (`ai-status.json`)

1. "degraded live-composed responses render a live-degraded badge naming
   the degraded surfaces" — met; unit-verified (`TopBar.test.tsx`, cases
   for `bff_composed`/`service_client` degraded surfaces, the full-list
   fallback recovery, and envelope-level degradation with a live source).
2. "SNAPSHOT DATA appears only when data is actually served from a
   snapshot source" — met; unit-verified (`local_snapshot` case, and the
   new explicit-snapshot-source-dominates-status case).
3. "zh-TW and en-US locales updated consistently" — the `live_degraded`
   / `degradedSurfaces` locale keys already existed in both
   `src/i18n/locales/en-US.ts` and `zh-TW.ts` before this task (added
   alongside the original three-state classification, commit `2f23ce6`);
   no locale drift between the two files. Not modified further by the
   round-2 fix commit (`a690879`), which only touched classification
   logic.
4. "npm run audit:render passes and hosted evidence shows the new badge
   on the Evolution Journal page" — **not yet satisfied, and cannot be
   honestly satisfied before this PR merges**: `audit:render` and any
   hosted screenshot both require a running deploy that already serves
   this fix, and PR #298 is still open pending reviewer re-approval.
   Faking this against a local mock-mode preview (no live BFF, no
   degraded surfaces to actually observe) would not be the evidence the
   criterion asks for. This is deliberately deferred, not skipped: once
   PR #298 merges and dev redeploys, `npm run audit:render` against the
   hosted dev FE base URL plus a screenshot of a genuinely
   live-degraded Evolution Journal / shell badge closes this item. That
   redeploy-and-verify step is exactly the work `EVOCHAIN-011` (dev
   deploy + closeout, which depends on `EVOCHAIN-008` per `INDEX.md`)
   already owns — see the dependency resolution below.

## EVOCHAIN-008 ↔ EVOCHAIN-011 acceptance/dependency resolution

Per `INDEX.md`, `EVOCHAIN-011` (dev deploy + closeout) depends on
`EVOCHAIN-008` among others, and the packet's global acceptance requires
hosted/live curl evidence archived for deploy tasks. This does **not**
mean `EVOCHAIN-008` itself must produce hosted evidence of the full
producer chain before it can close:

- `EVOCHAIN-008`'s own scope is FE badge *classification logic* — a pure
  client-side change verified by unit tests against synthetic envelope/
  shell-summary payloads (see the 8 vitest cases above, including the
  three new/updated cases for the round-2 fixes). There is no live
  producer chain to observe yet: the threshold-breach producer
  (`EVOCHAIN-001`), sweep activation (`EVOCHAIN-002`), and the
  freeze/rollback canonical store (`EVOCHAIN-004`/`-005`) are separate,
  independent tasks in this same packet and are not prerequisites of
  `EVOCHAIN-008`.
- The packet-level, end-to-end hosted proof ("real threshold breach ->
  incident -> proposal -> formal journal entry -> ... -> Evolution
  Journal aggregate surface ok (no SNAPSHOT DATA badge)") is explicitly
  owned by `EVOCHAIN-011`'s closeout, which runs after every wave-0/1/2
  task (including `EVOCHAIN-008`) has merged. `EVOCHAIN-008` closing on
  local verification alone is consistent with the dependency graph, not
  a gap in it.
- `EVOCHAIN-008`'s local unit coverage (`TopBar.test.tsx`) is the durable
  record that the badge-classification contract EVOCHAIN-011 will
  observe hosted (`bff_composed`/`service_client` degraded => `LIVE
  (PARTIALLY DEGRADED)`; `local_snapshot`/`missing`/`unverifiable` =>
  `SNAPSHOT DATA`) is implemented correctly ahead of that hosted check.

## Residual risk

- **Hosted evidence for acceptance item 4 is outstanding.** Owner:
  `EVOCHAIN-011` (Codex2 / Human-Ops per `INDEX.md`). Expiry: at
  `EVOCHAIN-011` closeout — that task cannot itself close without dev
  redeployed and live curl/screenshot evidence archived, so this item
  cannot silently expire unresolved. If `EVOCHAIN-011` closeout is
  reached without a screenshot of the live-degraded badge specifically,
  `EVOCHAIN-008`'s owner should be re-engaged before that closeout is
  accepted.
- `EVOCHAIN-008` remains blocked from `done` only on PR #298 merging
  (owner cannot self-merge; awaiting reviewer re-approval per round-2
  fixes above).
