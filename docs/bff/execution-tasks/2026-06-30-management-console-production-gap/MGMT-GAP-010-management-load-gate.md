# MGMT-GAP-010 - Management Console Load And Release Gate Performance

Owner: Gemini2
Reviewer: Codex
Batch: 5
Fleet lane: frontend performance gate
Depends on: `MGMT-GAP-001`, `MGMT-GAP-002`

Execution packet:
`docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md`

## Problem

The full re-audit found production readiness can be distorted by page-load
behavior. The management bundle is large, the shell can fan out multiple reads
before the target route is ready, duplicate jobs reads were observed in the load
gap, and `network-idle` is not reliable readiness proof for live management
pages.

The supplemental route/control re-audit build also produced release-risk
signals: generated CSS had a minify warning, realtime modules were imported both
statically and dynamically, and the main `index` asset was about 5.5 MB before
gzip with several large chunks still over the bundler warning threshold.

This task materializes the separate load-gap plan into an execution gate.

## Scope

Implement the follow-up from
`docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md`:

- code split management route families and defer non-critical route modules;
- defer shell fanout until the target route and authenticated session are known;
- aggregate shell counts instead of issuing duplicate heavyweight reads;
- remove duplicate jobs/request patterns identified by the load audit;
- make hosted probes use deterministic route-ready markers instead of
  `network-idle`;
- add bundle/load regression thresholds to the management release gate.
- gate build warnings that can hide broken generated CSS or ineffective
  code-splitting, including static/dynamic import conflicts for realtime code.

`MGMT-GAP-010` is the umbrella gate. Production execution is split into
`MGMT-LOAD-001` through `MGMT-LOAD-007`; the parent task closes only after
`MGMT-LOAD-007` archives reviewer-approved closeout evidence.

## Non-Scope

- Do not change product semantics only to satisfy bundle size.
- Do not hide slow endpoint failures behind client-only loading placeholders.
- Do not count localhost CORS failures as hosted production evidence.

## Acceptance

- Management build output records initial bundle and async chunk sizes, with a
  documented budget and fail condition.
- Build output records and fails or explicitly waives CSS minify warnings,
  route-splitting import conflicts, and large chunk warnings.
- Hosted management probe records route-ready time, key endpoint timings, and
  shell request counts.
- The route harness no longer treats `network-idle` as the only readiness proof.
- Duplicate jobs or shell reads are eliminated or explicitly justified with a
  measured reason.
- Release evidence includes before/after build output, hosted probe JSON,
  Markdown summary, commit SHA, and PR link.

## MGMT-LOAD-007 Parent Gate Handoff

Closeout archive:
`docs/04/pantheon_management_console_load_gap_2026-07-01/archive/MGMT-LOAD-007-closeout-2026-07-01.md`.

`MGMT-LOAD-001` through `MGMT-LOAD-006` are terminal `done` in the live task
archive. The merged load work includes route-ready probes, shell-summary and
jobs canonicalization, frontend shell fanout reduction, route code splitting,
BFF read-concurrency isolation, and `scripts/aggregate-release-gate.mjs`.

Current production-green status: blocked. The latest release gate manifest,
`docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.json`,
has `result.pass:false` because it intentionally aggregates the archived
pre-fix `MGMT-LOAD-001` route-timing, request-waterfall, and BFF-fanout
evidence. That is correct fail-closed behavior, not a false green.

Measured improvement evidence:

- Hosted route split probe after execute-plans PR `#134`: first row/empty-state
  p75 931 ms and p95 1203 ms on `/management/evidence`.
- Local BFF concurrency reproduction after Pantheon PR `#2682`: `/health` p95
  189 ms and Evidence p95 425 ms under synthetic concurrent slow reads.
- Bundle budget after execute-plans PR `#138`: initial management JS gzip
  269474 bytes and Evidence route chunk gzip 13345 bytes, both under budget.

Remaining required proof before reviewer-approved production-green closeout:
run fresh hosted route-load and BFF-fanout probes against the merged dev FE/BFF
pair, then regenerate the release gate artifact with `result.pass:true`.
