# MGMT-GAP-006 Acceptance Packet (Sidecar)

**Parent Task**: `MGMT-GAP-006` — Hosted management production acceptance harness
**Parent Owner**: `Claude` (reassigned from `Gemini2` on 2026-07-01T06:06:50Z; unavailable-lane auto-reassignment)
**Parent Reviewer**: `Codex`
**Parent Status**: `in_progress` (updated 2026-07-01T18:50:36Z)
**Sidecar Owner**: `Claude2`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-07-01T00:00:00Z
**Closeout update**: 2026-07-01 — dependency map and readiness verdict refreshed; all
four previously-blocking dependencies (`005`/`008`/`009`/`010`) are now archived `done`. See §1.1
and §6 for the updated verdict.

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or
> core runtime/registry/governance implementations. It does not modify
> `frontend-checkout:e2e`, `frontend-checkout:scripts`, or `scripts/aggregate-release-gate.mjs`.

Shared-truth and task-scoped sources used in this packet:

- `AI_COLLABORATION_GUIDE.md` — lifecycle and sidecar operating rules
- `.orchestrator/task-briefs/mgmt_gap_006_sidecar_acceptance.md` — task-scoped scope guardrails
- `ai-status.json` — durable task state, owner/reviewer assignment, `depends_on` truth
- `docs/04/pantheon_management_console_gap_2026-06-30/README.md` — gap spec, batch plan, completion definition
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.md` — 93-route/510-button crawl this harness must reproduce or supersede
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/full-reaudit-addendum-2026-07-01.md` — detail-honesty and session/RBAC findings feeding `MGMT-GAP-008`/`MGMT-GAP-009`
- `docs/bff/execution-tasks/2026-06-30-management-console-production-gap/INDEX.md` — execution packet index
- `frontend-checkout:e2e/20-management-route-ia.spec.ts`, `21-management-canonical-reads.spec.ts`, `22-management-evidence-load.spec.ts` — existing hosted/probe coverage at `/home/lupin/code/pantheon/.fe-ep`
- `frontend-checkout:scripts/aggregate-release-gate.mjs` and sibling `probe-*.mjs` scripts

---

## 1. Dependency Map

### 1.1 Hard Upstream Dependencies (from `ai-status.json` `depends_on`)

| Task | Status | Final Owner / Reviewer | Why `MGMT-GAP-006` needs it |
|---|---|---|---|
| `MGMT-GAP-001` | `done` | Codex2 / Claude | Legacy route cleanup and IA reduction; the harness's "no hidden legacy render" assertion depends on this being closed |
| `MGMT-GAP-002` | `done` | Claude / Codex | Canonical BFF read wiring; the harness's "no seed fallback claims" assertion depends on this |
| `MGMT-GAP-004` | `done` (archived) | Codex / Claude | Command/receipt truth for write-like controls; the harness's "no mock-only success for write CTAs" assertion depends on this. Closed via PR #2666 (dev `e61c3e995`) plus execute-plans PR #132; focused BFF validation passed (17 tests) |
| `MGMT-GAP-005` | `done` (archived) | Codex2 / Claude | Studios/capability runtime-backing; the harness's "no mock trace/backtest labeled as live success" assertion is now verifiable. Closed via execute-plans PR #129 and Pantheon PR #2675 (merge `bb649d970`); capability studio actions fail closed without a governed runner or command receipts |
| `MGMT-GAP-008` | `done` (archived) | Claude / Codex2 | Detail DTO/render honesty (`undefined`, `NaN%`, blank fields); the harness's detail-honesty assertions are now verifiable. Closed via execute-plans PR #133/#135 (dev FE commit `47b8f418`); local `tsc`/`eslint`/`vitest`/`vite build`/`check-management-naming` and integration-gate run `28513737231` all green |
| `MGMT-GAP-009` | `done` (archived) | Codex2 / Codex | Session/RBAC contract consistency; the harness's "no session/RBAC mismatch" assertion is now verifiable. Closed via implementation PR #2660 (`6304ee8e`) and closeout PR #2672; focused BFF session/RBAC verification passed (41 tests, isolated `BFF_DATA_DIR`) |
| `MGMT-GAP-010` | `done` (archived) | Claude / Claude2 | Load/release-gate performance; the harness's bundle-budget and route-ready-marker evidence is now wired into the release gate. Closed via PR #2720 (`74eefdba1`, merged into `origin/dev`); `aggregate-release-gate.mjs` re-verified byte-identical (excl. `generatedAt`) to the archived `release-load-gate-2026-07-01.json`, `pass:true`, zero failures/missing. Residual risk: BFF `/deployment.json` 404 explicitly handed to `MGMT-GAP-007`/Codex, not a `MGMT-GAP-006` blocker |

**Readiness verdict (updated at sidecar closeout): `MGMT-GAP-006` is no longer dependency-blocked.**
All seven hard dependencies are `done` and archived with verified evidence (PR SHAs, merge commits,
and focused test runs — not just status-field claims; see the archive spot-checks cited per row
above). The original readiness verdict at packet generation time (`001`/`002`/`004` done,
`005`/`008`/`009`/`010` still `todo`) is now stale; this update supersedes it. `MGMT-GAP-006` was
also reassigned from `Gemini2` to `Claude` (auto-reassignment, unavailable lane) and moved to
`in_progress` on 2026-07-01T18:50:36Z, with no implementation commits yet in its worktree
(`task/MGMT-GAP-006`) as of this update — only an untracked task-brief file. The harness build
itself (§2.3, §3) remains fully on the parent task; this sidecar does not implement it.

### 1.2 Downstream Consumer

| Task | Relation | Why it matters |
|---|---|---|
| `MGMT-GAP-007` | explicit `depends_on: [MGMT-GAP-006]` | Oversight closeout cannot archive final production proof or reconcile the route/control re-audit findings until this harness exists and is green |

### 1.3 Sibling Batch-5 Task

| Task | Relation | Why it matters |
|---|---|---|
| `MGMT-GAP-010` | shares Batch 5 "load release gate" scope; is also a hard dependency (§1.1) | `MGMT-GAP-010`'s bundle-budget/route-ready-marker work and `MGMT-GAP-006`'s hosted probe work both write into `scripts/aggregate-release-gate.mjs`; the parent owner should sequence these two so the gate script gains one coherent set of checks rather than two competing edits |

---

## 2. Current Repo Snapshot

### 2.1 Existing hosted/probe infrastructure the parent can build on

The frontend checkout at `/home/lupin/code/pantheon/.fe-ep` (tracked as `frontend-checkout:` in
task artifacts) already has partial coverage:

| Existing asset | What it covers | Gap vs. `MGMT-GAP-006` acceptance |
|---|---|---|
| `e2e/20-management-route-ia.spec.ts` (109 lines) | Redirects hidden legacy management aliases to canonical routes | Does not cover the full 93-route crawl, button/disabled counts, or mock-visible detection |
| `e2e/21-management-canonical-reads.spec.ts` (243 lines) | Routes management pages to canonical BFF read surfaces without seed fallback | Does not cover detail-page `undefined`/`NaN`/blank-field assertions, or session/RBAC consistency |
| `e2e/22-management-evidence-load.spec.ts` (192 lines) | Fixture-mocked, CI-safe evidence route-load readiness probe | Explicitly fixture-mocked, not hosted-live; does not produce deployed-host JSON/Markdown evidence |
| `scripts/probe-bff-routes.mjs`, `probe-bff-authenticated-live.mjs`, `probe-bff-write-paths.mjs`, `probe-hosted-browser-bff.mjs`, `probe-route-load-baseline.mjs`, `probe-bff-fanout-concurrency.mjs` | Individual BFF/route/write/load probes | No single harness composes all of them plus route/control crawl plus detail-honesty plus session-consistency into one release-gate-consumable artifact |
| `scripts/aggregate-release-gate.mjs` | Aggregates probe/test output into a release gate summary | Does not yet ingest a management-specific route/control/mock/session/load evidence bundle |
| root `crawl_final.mjs` | Ad hoc hosted route crawl against a fixed sslip.io host, prints console-error/crash/blank classification per route | Closest existing precedent for the 93-route crawl shape, but it is a scratch script (hardcoded host, no JSON evidence artifact, no button/disabled/mock-visible counting, not wired into CI or the release gate) |

### 2.2 The source-of-truth crawl this harness must reproduce or supersede

`docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.md`
and its raw JSON sibling already define the target shape:

- 93 route samples, 510 buttons, 42 disabled buttons, 386 links, 47 inputs
- mock-visible routes, direct-render detail aliases, high-density action hotspots
- source-scan evidence for `runActionSafe`, `bffWrites`, `NonProductionActionButton`,
  `toast.success`, `writeOverlay`

`MGMT-GAP-006`'s acceptance text says the harness must "reproduce or supersede" this crawl — i.e.
it needs comparable or greater coverage, expressed as a durable, CI/release-gate-consumable
artifact rather than a one-off manual audit pass.

### 2.3 What does not exist yet

No file in this repo or in `.fe-ep` currently:

- combines route/control crawl + endpoint-capture + strict-live/no-seed-fallback + write-CTA-mock
  detection + console/CORS-failure capture + button/disabled counts + load/build signal into one
  harness invocation;
- emits a single JSON/Markdown evidence artifact under
  `docs/04/pantheon_management_console_gap_2026-06-30/archive` for this harness specifically;
- is wired into `scripts/aggregate-release-gate.mjs` as a management-acceptance gate input.

This was consistent with `ai-status.json` showing `MGMT-GAP-006` as `status: todo` with no
artifacts produced yet at packet-generation time; as of this closeout update the task is
`status: in_progress` (owner `Claude`) with no implementation commits yet in its worktree, so §2.3
still accurately describes the current repo state.

---

## 3. Acceptance Checklist For Parent Task

Checklist derived from `ai-status.json`'s `acceptance` field, the README Batch 5 deliverables, and
the route-control re-audit target shape. Status reflects the current repo snapshot, not the
desired final state.

| # | Criterion | Verification Method | Status |
|---|---|---|---|
| 1 | `dep_001_002_004_closed` | `MGMT-GAP-001`, `002`, `004` archived `done` in `ai-status.json` | Met |
| 2 | `dep_005_008_009_010_closed` | `MGMT-GAP-005`, `008`, `009`, `010` all archived `done` as of 2026-07-01T18:50Z; harness preconditions can now be honestly asserted | Met (updated at closeout; was `Blocking` at generation time) |
| 3 | `harness_covers_visible_nav` | Harness enumerates all visible `/management/*` nav entries (58 per README §2) | Pending — parent task `MGMT-GAP-006` is `in_progress`, no implementation commits yet |
| 4 | `harness_covers_hidden_aliases` | Harness enumerates hidden/legacy aliases and asserts canonical final path or accepted redirect | Pending |
| 5 | `harness_covers_detail_final_paths` | Harness samples live-id detail routes and asserts canonical DTO mapper output (precondition `MGMT-GAP-008` now closed) | Pending, unblocked |
| 6 | `harness_endpoint_capture` | Harness captures the intended BFF endpoint family actually called per route | Pending |
| 7 | `strict_live_no_seed_fallback` | Harness fails if any route shows seed/mock data under `VITE_BFF_FALLBACK=strict` | Pending |
| 8 | `write_cta_mock_detection` | Harness fails if any write-like control returns local-only/toast success without command id/receipt (precondition `MGMT-GAP-004` now closed) | Pending, unblocked |
| 9 | `console_cors_failure_capture` | Harness records `pageerror`/`console.error`/CORS failures per route (precedent: `crawl_final.mjs`) | Pending |
| 10 | `button_disabled_counts` | Harness records button/link/input/disabled counts comparable to the 93/510/42/386/47 baseline | Pending |
| 11 | `load_build_signals` | Harness records bundle/build warning and route-ready timing signals (precondition `MGMT-GAP-010` now closed and wired into the gate) | Pending, unblocked |
| 12 | `single_evidence_artifact` | Harness output is one JSON+Markdown evidence pair under `docs/04/pantheon_management_console_gap_2026-06-30/archive` | Pending |
| 13 | `release_gate_wired` | `scripts/aggregate-release-gate.mjs` consumes the harness result and can fail the gate on regression | Pending |
| 14 | `reproduces_or_supersedes_baseline_crawl` | Harness coverage is >= the 93-route/510-button baseline counts, or documents why a route was intentionally dropped | Pending |
| 15 | `sidecar_scope_only` | This helper produced support material only and did not modify canonical truth, `frontend-checkout:e2e`, `frontend-checkout:scripts`, or `scripts/aggregate-release-gate.mjs` | Met |

### Acceptance summary

What is already true (updated at sidecar closeout):

- all seven hard dependencies are closed (`001`, `002`, `004`, `005`, `008`, `009`, `010`), each
  with verified evidence (PR SHAs, merge commits, focused test runs), so every harness assertion
  category in this checklist now has a real, verified precondition to stand on — none are
  dependency-blocked anymore;
- partial hosted/probe infrastructure exists (`e2e/20-22`, several `probe-*.mjs` scripts, and a
  scratch `crawl_final.mjs` precedent) that the parent owner can extend instead of building from
  zero;
- the target crawl shape (93 routes / 510 buttons / 42 disabled / 386 links / 47 inputs) is
  already documented with raw JSON, giving the harness a concrete baseline to reproduce or exceed;
- `MGMT-GAP-010`'s bundle/build-signal wiring into `scripts/aggregate-release-gate.mjs` already
  landed (PR #2720), so `MGMT-GAP-006` can build on that gate surface rather than sequence a
  concurrent edit against a still-open task.

What remains fully on the parent task (now `Claude`, `in_progress`):

- build the single composed harness (no existing script combines all required signal types);
- produce and archive the JSON/Markdown evidence artifact and wire it into the release gate;
- reproduce or supersede the 93-route/510-button baseline crawl with a durable, CI-consumable
  artifact.

---

## 4. Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| ~~Parent starts the harness before `005`/`008`/`009` land~~ | Resolved — all four dependencies (`005`/`008`/`009`/`010`) are archived `done` as of 2026-07-01T18:50Z; this risk no longer applies | N/A |
| ~~Parent and `MGMT-GAP-010` both edit `scripts/aggregate-release-gate.mjs` concurrently~~ | Resolved — `MGMT-GAP-010` merged (PR #2720) before `MGMT-GAP-006` began implementation; the gate surface is stable for `MGMT-GAP-006` to extend | N/A |
| Harness undercounts vs. the 93/510/42/386/47 baseline without explanation | Silently narrows coverage while claiming supersession | Parent should log any intentionally dropped route/control with a reason, matching the "no silent caps" norm already used in the route-control re-audit doc |
| `crawl_final.mjs` pattern (hardcoded host, no CI wiring) gets reused verbatim | Harness stays a manual scratch script instead of a release-gate-consumable artifact | Parent should treat `crawl_final.mjs` as a shape precedent only, not a drop-in implementation — it needs parameterized host, JSON evidence output, and gate wiring |
| `MGMT-GAP-010`'s residual risk (BFF `/deployment.json` 404) gets silently assumed fixed by `MGMT-GAP-006` | Harness could pass while masking an already-known-open gap | This residual risk was explicitly handed to `MGMT-GAP-007`/Codex, not `MGMT-GAP-006`; parent should not treat it as in scope unless `MGMT-GAP-007` reassigns it |

---

## 5. Artifacts Inventory

| Artifact | Path | Purpose |
|---|---|---|
| This acceptance packet | `support/sidecars/MGMT-GAP-006/MGMT-GAP-006-SIDECAR-ACCEPTANCE.md` | Support-only dependency map and acceptance checklist |
| Parent gap spec | `docs/04/pantheon_management_console_gap_2026-06-30/README.md` | Batch plan, completion definition, fleet task map |
| Route/control baseline | `docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.md` (+ `.json`) | 93-route/510-button target shape the harness must reproduce or supersede |
| Detail/session findings | `docs/04/pantheon_management_console_gap_2026-06-30/archive/full-reaudit-addendum-2026-07-01.md` | Source of the `MGMT-GAP-008`/`009` preconditions referenced in §1.1 and §3 |
| Execution packet index | `docs/bff/execution-tasks/2026-06-30-management-console-production-gap/INDEX.md` | Cross-task execution packet |
| Existing hosted/probe coverage | `frontend-checkout:e2e/20-22-*.spec.ts`, `frontend-checkout:scripts/probe-*.mjs`, `frontend-checkout:scripts/aggregate-release-gate.mjs`, root `crawl_final.mjs` | Partial infrastructure the parent can extend (all at `/home/lupin/code/pantheon/.fe-ep`, unmodified by this sidecar) |

---

## 6. Handoff Note To Reviewer (Claude) — Closeout Update

Claude, this sidecar was reviewed and approved on 2026-07-01; this closeout pass refreshed the
dependency map and readiness verdict against current `ai-status.json` before finalizing to `done`,
since three status-changing facts landed after the original review:

1. All four previously-`todo` blocking dependencies (`005`, `008`, `009`, `010`) are now archived
   `done` with verified evidence (PR SHAs / merge commits / focused test runs — see §1.1). The
   original "dependency-blocked" verdict is stale and has been superseded in place.
2. `MGMT-GAP-006`'s parent owner was auto-reassigned from `Gemini2` to `Claude`
   (2026-07-01T06:06:50Z, unavailable-lane reassignment), and the task moved from `todo` to
   `in_progress` (2026-07-01T18:50:36Z). No implementation commits exist yet in its worktree.
3. The `scripts/aggregate-release-gate.mjs` shared-edit-surface risk with `MGMT-GAP-010` is
   resolved — `MGMT-GAP-010` merged first (PR #2720), so `MGMT-GAP-006` now has a stable gate
   surface to extend rather than a concurrent-edit hazard.

What this packet still establishes, unchanged from the original review:

- real partial infrastructure already exists in the frontend checkout (`e2e/20-22`, several
  `probe-*.mjs` scripts, `crawl_final.mjs` precedent) that the parent owner can extend rather than
  building the harness from zero;
- the target coverage shape (93 routes / 510 buttons / 42 disabled / 386 links / 47 inputs) is
  already documented with raw JSON evidence, giving the harness a concrete bar to reproduce or
  exceed.

Recommended next step:

- this sidecar closes out in support-only scope with no canonical-truth changes;
- hand off to `Claude` (current parent owner) with an unblocked go-ahead: all seven acceptance
  checklist items that were dependency-blocked (§3 items 2, 5, 8, 11) are now `Met`/`unblocked`;
  the parent owner can build the full harness immediately rather than a scaffold-then-activate
  sequence;
- `MGMT-GAP-007` (oversight closeout) can now expect `MGMT-GAP-006` evidence as soon as the parent
  task's own implementation lands — no external blocker remains.

---

## 7. Sidecar Scope Declaration

This file is the only artifact created by this sidecar pass.

- no canonical L1 or L2 document was modified by this sidecar
- no `frontend-checkout:e2e`, `frontend-checkout:scripts`, or `scripts/aggregate-release-gate.mjs`
  file was modified
- no runtime, BFF, registry, or governance implementation file was modified
- no global summary files were edited manually for this packet
- parent-task absorption remains a parent-owner (`Claude`, reassigned from `Gemini2` per §1.1) decision

---

*Generated by Claude2 as a sidecar `acceptance_packet` helper for `MGMT-GAP-006`. This file is a
support artifact and does not modify canonical truth.*
