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
