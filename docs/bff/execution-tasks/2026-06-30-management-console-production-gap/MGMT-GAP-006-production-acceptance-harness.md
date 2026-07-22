# MGMT-GAP-006 - Management Production Acceptance Harness

Owner: Gemini2
Reviewer: Codex
Batch: 5
Fleet lane: integration/QA and hosted probes
Depends on: `MGMT-GAP-001`, `MGMT-GAP-002`, `MGMT-GAP-004`, `MGMT-GAP-005`, `MGMT-GAP-008`, `MGMT-GAP-009`, `MGMT-GAP-010`

## Problem

There is no single release gate that proves the management console is live,
canonical, non-mock, and production-level after deployment.

The supplemental 2026-07-01 route/control crawl proved that a render-only check
is still too shallow. The harness must cover route/control density, final paths,
mock-visible text, disabled reasons, console-error classification, and
high-density write surfaces.

## Scope

Build or extend a hosted management probe that checks:

- visible management nav routes;
- hidden legacy aliases;
- expected canonical final paths;
- intended BFF endpoint calls per page;
- no old BFF host usage;
- no silent seed fallback in strict live mode;
- no mock-only success after write-like CTA interaction;
- no console CORS/resource errors that block the page.
- the route/control inventory shape from
  `docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.md`:
  53 visible nav routes, representative detail/hidden/alias routes, button and
  disabled-button counts, mock-visible route flags, and direct-render alias
  checks;
- detail-route honesty detectors from `MGMT-GAP-008`: raw `undefined`, `NaN`,
  blank critical headings/owners/updates, seed-id not-found leakage, and alias
  drift;
- session/RBAC detectors from `MGMT-GAP-009`: `/bff/me`, tenant, roles,
  provider auth degraded state, and privileged data rendering under session
  failure;
- load/release detectors from `MGMT-GAP-010`: route-ready milestones, bundle
  warnings, request counts, and endpoint timing.

The harness should emit a Markdown evidence report and machine-readable JSON
summary suitable for release gates.

## Non-Scope

- Do not require real writes unless explicitly enabled by operator-controlled
  environment variables.

## Acceptance

- Probe runs against the hosted FE/BFF dev hosts.
- Probe covers every visible management nav route and the known hidden aliases.
- Probe covers representative detail routes and fails when old detail aliases
  direct-render instead of redirecting or sharing a tested canonical mapper.
- Probe records total buttons, disabled buttons, disabled reasons, and
  high-density write-control hotspots; changes in those counts require review.
- Probe fails when mock/demo/seed text is presented as live production success.
- Output records deployment commit, BFF health, endpoint calls, failures, and
  residual risks.
- CI/release gate fails when legacy routes render, canonical endpoint calls are
  missing, or mock write success is detected.
- Localhost CORS failures are not accepted as hosted evidence; the gate must run
  on the hosted FE origin or explicitly prove the same origin policy.

## MGMT-LOAD-006 Handoff (load/release detectors)

`MGMT-LOAD-006` implements the "load/release detectors from `MGMT-GAP-010`"
named in Scope above as `scripts/aggregate-release-gate.mjs` (this repo,
`ajoe734/pantheon`). Before this harness treats a management release as
production-acceptable, it must require these exact artifact paths (all under
`docs/04/pantheon_management_console_load_gap_2026-07-01/archive/`) and read
`result.pass` from the manifest, not just its presence:

- `release-load-gate-*.json` / `.md` — overall pass/fail manifest (dependency
  pass-eligibility, bundle budget, route-timing/readiness, startup-request
  duplicate/count, BFF fanout latency).
- `release-route-timing-*.json`, `release-request-waterfall-*.json`,
  `release-bff-fanout-*.json`, `release-bundle-*.json` — the underlying
  evidence the manifest aggregated.

As of the 2026-07-01 `MGMT-LOAD-006` closeout, the most recent
`release-load-gate-*.json` reports `pass: false`: the archived
route-timing/waterfall/fanout evidence is the MGMT-LOAD-001 pre-fix baseline
and does not yet reflect the merged MGMT-LOAD-002/003/005 fixes. This harness
must not treat that manifest as production-acceptance evidence until a fresh
hosted probe run (`npm run probe:route-load && npm run probe:bff:fanout` in
`execute-plans`, then re-run `scripts/aggregate-release-gate.mjs`) reports
`pass: true`.

## MGMT-LOAD-007 Handoff

Final load-gap parent closeout:
`docs/04/pantheon_management_console_load_gap_2026-07-01/archive/MGMT-LOAD-007-closeout-2026-07-01.md`.

The current exact load-gate artifact set to consume is:

- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.json`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.md`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-route-timing-2026-07-01.json`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-request-waterfall-2026-07-01.json`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-bff-fanout-2026-07-01.json`
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-bundle-2026-07-01.json`

Acceptance rule: require `release-load-gate-*.json.result.pass == true`.
Presence of the files is not enough. The 2026-07-01 files above are useful
negative evidence and path fixtures, but they are not production-acceptance
evidence because the manifest is `pass:false`.
