# MGMT-GAP-010 - Management Console Load And Release Gate Performance

Owner: Gemini2
Reviewer: Codex
Batch: 5
Fleet lane: frontend performance gate
Depends on: `MGMT-GAP-001`, `MGMT-GAP-002`

## Problem

The full re-audit found production readiness can be distorted by page-load
behavior. The management bundle is large, the shell can fan out multiple reads
before the target route is ready, duplicate jobs reads were observed in the load
gap, and `network-idle` is not reliable readiness proof for live management
pages.

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

## Non-Scope

- Do not change product semantics only to satisfy bundle size.
- Do not hide slow endpoint failures behind client-only loading placeholders.
- Do not count localhost CORS failures as hosted production evidence.

## Acceptance

- Management build output records initial bundle and async chunk sizes, with a
  documented budget and fail condition.
- Hosted management probe records route-ready time, key endpoint timings, and
  shell request counts.
- The route harness no longer treats `network-idle` as the only readiness proof.
- Duplicate jobs or shell reads are eliminated or explicitly justified with a
  measured reason.
- Release evidence includes before/after build output, hosted probe JSON,
  Markdown summary, commit SHA, and PR link.
