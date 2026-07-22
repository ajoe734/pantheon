# MGMT-LOAD-006 Sidecar BFF / Frontend Handoff Follow-Up 2

Date: 2026-07-01
Owner: Codex2
Reviewer: Claude
Parent task: `MGMT-LOAD-006`
Helper kind: `bff_handoff_packet`
Scope: support-only follow-up packet. This does not change canonical truth,
BFF runtime code, frontend runtime code, release-gate implementation, route
registry behavior, or governance policy.

## Purpose

This follow-up narrows how the `MGMT-LOAD-006` release gate should consume the
existing load-gap evidence and the earlier sidecar packet. It is meant to be a
handoff checklist for the parent owner and reviewer, not a new source of
architecture truth.

The key risk this follow-up addresses is false closure: a single route timing
pass, a local BFF concurrency reproduction, or a raw request-count number can
look like enough evidence while still leaving the production gate unable to
fail the next regression.

## Sources Read

| Source | Use in this follow-up |
|---|---|
| `docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md` | Budget semantics, operator journey, route-load root cause, and phase-5 gate expectations. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md` | Task sequencing and global acceptance expectations for final closeout evidence. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-001-baseline-route-probes.md` | Baseline route probe contract and SSE-safe readiness requirements. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md` | Frontend startup fanout acceptance: <= 2 non-primary BFF reads and zero duplicate jobs reads before primary content. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-006-release-load-gate.md` | Parent release-gate scope and required artifact classes. |
| `docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-007-load-closeout.md` | Downstream closeout needs: exact artifact paths, deployed commits, residual risks, and parent proof. |
| `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/route-load-baseline-2026-07-01.md` | Baseline browser waterfall and duplicate `/bff/jobs` evidence. |
| `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/mgmt-load-004-route-load-hosted-2026-07-01.md` | Post-route-split hosted timing precedent and the raw request-count ambiguity. |
| `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-local-before-after-2026-07-01.md` | Local BFF read-isolation proof and hosted rerun deferral boundary. |
| `support/sidecars/MGMT-LOAD-006/MGMT-LOAD-006-SIDECAR-BFF-HANDOFF.md` | Existing sidecar packet that this follow-up complements instead of replacing. |

## Gate Absorption Checklist

`MGMT-LOAD-006` should absorb the evidence as a release gate only when the
implementation can answer all of these with machine-readable artifacts:

| Question | Required gate behavior |
|---|---|
| Did the browser probe wait on content milestones instead of `networkidle`? | Emit `usedNetworkidle=false`; fail if readiness is proved only by `networkidle`. |
| Did primary Evidence content render within budget? | Compute percentiles for heading and first row or empty state; fail p75/p95 budget breaches. |
| Did the startup shell stay out of the critical path? | Classify bounded requests before first row; fail if non-primary BFF requests exceed budget. |
| Did duplicate jobs hydration regress? | Count bounded `/bff/jobs` calls before first row; fail any duplicate. |
| Did bundle size regress? | Emit initial management JS and route chunk gzip sizes; fail budget breaches or require a named reviewer exception. |
| Did BFF read fanout stay responsive? | Emit concurrent p95s for `/health`, Evidence, shell-summary, alerts, approvals, and jobs; fail budget breaches. |
| Did degraded/timeout paths stay honest? | Emit degraded metadata per route; fail hanging or silent empty-success behavior. |
| Can downstream closeout link exact evidence? | Emit JSON and Markdown artifact paths that `MGMT-LOAD-007` and `MGMT-GAP-006` can cite without reinterpretation. |

## Pass / Fail Policy

The release gate should treat the following as hard failures:

| Field or condition | Failure threshold |
|---|---|
| `usedNetworkidle` | Any value other than `false`, or absence of the field when the route has SSE. |
| `firstRowOrEmptyVisibleMs.p95` | Greater than 2500 ms on the deployed dev FE route probe. |
| `firstRowOrEmptyVisibleMs.p75` | Greater than 1500 ms on the deployed dev FE route probe. |
| `headingVisibleMs.p95` | Greater than 1500 ms when heading percentiles are emitted. |
| `non_primary_bff_before_first_row` | Greater than 2. |
| `duplicate_startup_requests["/bff/jobs"]` | Greater than 0 before first row or empty state. |
| `initial_management_js_gzip_bytes` | Greater than 800 KB unless the artifact carries an explicit reviewer-approved shared-vendor exception. |
| `evidence_route_chunk_gzip_bytes` | Greater than 150 KB excluding shared vendor cache unless an equivalent reviewed budget is documented. |
| `bffFanout["/health"].p95Ms` | Greater than 200 ms during concurrent management read fanout. |
| `bffFanout["/bff/management/evidence"].p95Ms` | Greater than 750 ms during concurrent management read fanout. |
| `bffFanout["/bff/management/shell-summary"].p95Ms` | Greater than 200 ms under 10 concurrent requests. |
| `bffFanout` coverage | Missing `/health`, Evidence, shell-summary, alerts, approvals, or jobs rows. |

The gate should keep the following as diagnostic fields, not standalone
failure conditions unless a separate budget is defined:

| Field | Why it is diagnostic |
|---|---|
| `total_requests_before_first_row` | The MGMT-LOAD-004 hosted samples report 70 requests before first row; this mixes assets/chunks with API requests and needs classification before enforcement. |
| raw EventSource request count | `/bff/events/stream` is long-lived and must be excluded from bounded request timing. |
| isolated Evidence HTTP latency | Fast isolated Evidence calls do not prove shell startup and concurrent BFF fanout are safe. |
| local-only BFF before/after reproduction | Strong implementation evidence for `MGMT-LOAD-005`, but not a deployed dev-BFF release gate by itself. |

## Request Classification Contract

The JSON artifact should classify every captured startup request with one of
these values so reviewers can separate root causes:

| Classification | Examples | Gate use |
|---|---|---|
| `primary` | `GET /bff/management/evidence` for `/management/evidence` | Required for primary content timing. |
| `non_primary_bff` | `/bff/me`, `/bff/alerts`, `/bff/approvals`, early `/bff/jobs`, early `/health` | Counted before first row; budget <= 2. |
| `deferred_shell` | shell-summary or full-list hydration that starts after first row/empty state | Recorded but not counted against first-row startup budget. |
| `asset` | FE document, CSS, JS chunks, fonts, manifest files | Used for bundle and asset diagnostics. |
| `sse` | `/bff/events/stream` | Excluded from readiness and bounded p95 calculations. |
| `other_api` | Non-BFF API calls that are not the route primary request | Recorded for drift detection; fail only if the budget file names it. |

If `GET /bff/management/shell-summary` starts before first row, it should still
be visible in the waterfall. It may be acceptable as one of the allowed
non-primary BFF requests only if it remains cheap and replaces the earlier full
list fanout. If it starts after first row, classify it as `deferred_shell`.

## Minimum Artifact Shape

The earlier sidecar packet lists the high-level artifact classes. This follow-up
adds the minimum pass/fail fields the parent gate should emit:

```json
{
  "environment": {
    "feBaseUrl": "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io",
    "bffBaseUrl": "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io",
    "routePath": "/management/evidence",
    "primaryApiPath": "/bff/management/evidence",
    "authTokenShape": "op-<id>:admin"
  },
  "readiness": {
    "usedNetworkidle": false,
    "domContentLoadedMs": 0,
    "shellVisibleMs": 0,
    "headingVisibleMs": 0,
    "primaryApiCompleteMs": 0,
    "firstRowOrEmptyVisibleMs": 0
  },
  "percentiles": {
    "headingVisibleMs": {"p75": 0, "p95": 0},
    "firstRowOrEmptyVisibleMs": {"p75": 0, "p95": 0}
  },
  "startupGuards": {
    "nonPrimaryBffBeforeFirstRow": 0,
    "duplicateStartupRequests": {"/bff/jobs": 0},
    "totalRequestsBeforeFirstRow": 0
  },
  "bundleBudgets": {
    "initialManagementJsGzipBytes": 0,
    "evidenceRouteChunkGzipBytes": 0,
    "reviewerException": null
  },
  "bffFanout": {
    "/health": {"count": 0, "p95Ms": 0},
    "/bff/management/evidence": {"count": 0, "p95Ms": 0},
    "/bff/management/shell-summary": {"count": 0, "p95Ms": 0},
    "/bff/alerts": {"count": 0, "p95Ms": 0},
    "/bff/approvals": {"count": 0, "p95Ms": 0},
    "/bff/jobs": {"count": 0, "p95Ms": 0}
  },
  "result": {
    "pass": false,
    "failures": []
  }
}
```

Zero values in this example are placeholders. The real artifact must contain
observed values and a populated `result.failures` array when any hard gate
fails.

## Evidence That Is Not Sufficient Alone

Do not let any of these close `MGMT-LOAD-006` by itself:

| Evidence | Missing piece |
|---|---|
| MGMT-LOAD-004 hosted route timing pass on execute-plans commit `255e60414e0ca36e29c1b2e39f0543d23d2eea80` | It proves route split timing precedent, but the raw 70-request count still needs classification and release-gate enforcement. |
| MGMT-LOAD-005 local BFF before/after reproduction | It proves the read-isolation behavior locally, but a post-merge hosted dev-BFF fanout rerun is still needed for release evidence. |
| Existing baseline route-load JSON and Markdown | They diagnose the gap; they are not the final pass artifact after all child fixes are merged and deployed. |
| Shell-summary endpoint tests alone | They do not prove the frontend stopped first-row startup fanout or duplicate jobs hydration. |
| Passing isolated `/bff/management/evidence` latency | It does not prove `/health` and Evidence stay responsive during shell fanout. |

## Reviewer Focus

Claude should review this sidecar as a support packet only:

- Confirm it does not alter canonical architecture, L1 contract files, runtime
  implementation, or release-gate code.
- Confirm it composes with `MGMT-LOAD-006` by providing gate absorption policy,
  not by approving the parent gate implementation.
- Confirm the parent owner can translate the checklist into release-gate JSON,
  Markdown, and CI/smoke pass-fail behavior.
- Confirm residual hosted evidence remains visible for `MGMT-LOAD-007` and
  `MGMT-GAP-006`.

## Handoff

This follow-up is ready to hand to Claude for support-slice review. The parent
owner should decide whether to copy any of these fields into the `MGMT-LOAD-006`
release-gate implementation or closeout artifacts.
