# MGMT-GAP-006 - Management Production Acceptance Harness

Owner: Gemini2
Reviewer: Codex
Batch: 5
Fleet lane: integration/QA and hosted probes
Depends on: `MGMT-GAP-001`, `MGMT-GAP-002`, `MGMT-GAP-004`, `MGMT-GAP-005`

## Problem

There is no single release gate that proves the management console is live,
canonical, non-mock, and production-level after deployment.

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

The harness should emit a Markdown evidence report and machine-readable JSON
summary suitable for release gates.

## Non-Scope

- Do not require real writes unless explicitly enabled by operator-controlled
  environment variables.

## Acceptance

- Probe runs against the hosted FE/BFF dev hosts.
- Probe covers every visible management nav route and the known hidden aliases.
- Output records deployment commit, BFF health, endpoint calls, failures, and
  residual risks.
- CI/release gate fails when legacy routes render, canonical endpoint calls are
  missing, or mock write success is detected.
