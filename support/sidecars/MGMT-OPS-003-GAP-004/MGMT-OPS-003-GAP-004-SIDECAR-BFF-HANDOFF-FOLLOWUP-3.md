# MGMT-OPS-003-GAP-004 BFF / Frontend Handoff Follow-up 3

Task: `MGMT-OPS-003-GAP-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`  
Parent: `MGMT-OPS-003-GAP-004`  
Owner: Codex  
Reviewer: Codex2  
Helper kind: support-only `bff_handoff_packet`

## Purpose and Boundary

This packet turns the existing BFF handoff and follow-up 2 into a compact,
reviewer-ready collection plan for the parent's independent hosted closeout.
It does not alter canonical truth, route contracts, runtime or registry data,
governance, frontend code, or the parent's verdict. A route returning `200`, a
unit test, or a historical hosted count is not evidence that the operator
journey currently passes.

## BFF Query Gap Disposition

There is no new route-design request in this sidecar. The actionable gap is
hosted proof that `execute-plans` emits and round-trips the deployed query
contract without hiding complete-set incidents or strengthening confidence.

| Operator control | Required BFF key | Evidence needed from the hosted page | Reject when |
|---|---|---|---|
| Stage | `deployment_stage` | URL, request, `meta.filters`, reload, clear | frontend sends `stage` or loses state |
| Broker | `broker_id` | same five observations | missing broker rows disappear |
| Runtime | `runtime_id` | same observations plus linked-page context | frontend sends `runtime` or drops context |
| Source status | `source_status` | degraded/stale/unavailable values preserved | UI upgrades the source label |
| Stale telemetry | `stale_telemetry` | boolean `true`, reload, then cleared state | string/alias behavior is unproven |
| Risk state | `risk_state` | response value preserved in row and review link | UI derives a stronger classification |

Holdings and positions also accept `capital_pool_id`, `persona_id`, `status`,
`q`, `page_token`, and `page_size`. The reviewer must compare captures only
when the query set and served BFF identity match. `data.items` is the current
page; it must not replace `page_info.total` or summary totals. Incidents are
computed for the complete filtered holding set, so an incident outside the
current page must remain visible and actionable.

## Operator Journey Capture Ledger

The parent evidence bundle should contain one ledger row for every step below.
Each row records the served frontend and BFF identities, timestamp, browser
URL, outbound request, authenticated response artifact, screenshot, console
exception count, failed required-request count, lazy-chunk failure count, and
fallback/seed-data count.

1. Cold-open Portfolio Book in strict live mode on desktop and capture core,
   holdings, positions, and attribution responses from one coherent session.
2. Exercise the six required filters individually, then a combined probe and
   a paginated probe where `page_size < page_info.total`.
3. Compare visible totals, source coverage, degraded rows, missing bindings,
   incidents, stages, risk state, and confidence with the authenticated
   responses. Record differences; do not reconcile them client-side.
4. Select a degraded or missing-binding incident and follow response-provided
   links through Persona Fleet and Performance Attribution to Human Review.
   Preserve persona, runtime, pool, holding, period, risk, and source issues.
5. Repeat the normal and degraded paths at mobile width, including cold load,
   reload, filter clear, lazy navigation, and link-context checks.

All four failure counters must be explicit, including zero. Desktop and mobile
evidence must refer to the same delivered commit family; stale or mock-only
screenshots fail closed.

## Parent Review Decision Table

| Parent acceptance surface | Minimum direct evidence | Sidecar disposition |
|---|---|---|
| Delivery identity | PR/head/merge SHAs, checks, deployment run, served-SHA ancestry for both repos | waiting on parent/dependency evidence |
| Contract-to-UI parity | authenticated payloads and UI-to-API comparison for every matrix row | use the query ledger above |
| Runtime truth | raw runtime, binding, deployment, pool, broker/ledger, and telemetry samples | unresolved rows stay visible and owned |
| Hosted browser behavior | desktop/mobile normal and degraded captures plus four explicit failure counts | no screenshot-only approval |
| Governed navigation | response links and preserved entity/source context to Human Review | reconstructed or lost context fails |
| Consolidation handoff | behavior-preserving note for `MGMT-PERF-IA-003` | parent owns absorption |

The parent remains the only lane that may issue `APPROVE` or
`REQUEST_CHANGES`. This packet supplies a collection and comparison aid; it is
not a signed reviewer checklist and does not close any hosted-gap matrix row.

## Composition Handoff

- `MGMT-OPS-003-GAP-001` should provide the real `execute-plans` implementation
  identity and hosted filter/render evidence.
- `MGMT-OPS-003-GAP-002` should provide raw runtime-data evidence and keep
  unresolved records quarantined and visible rather than deleting them.
- `MGMT-OPS-003-GAP-003` should provide the coherent authenticated desktop and
  mobile capture ledger.
- `MGMT-OPS-003-GAP-004` owner/reviewer should rerun the probes independently,
  sign `REVIEWER_CHECKLIST.md`, and decide the verdict fail closed.
- `MGMT-PERF-IA-003` should preserve exact query keys, complete-set incident
  visibility, confidence semantics, and response-provided navigation context.

## Sources Checked

- `support/sidecars/MGMT-OPS-003-GAP-004/MGMT-OPS-003-GAP-004-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/MGMT-OPS-003-GAP-004/MGMT-OPS-003-GAP-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/MGMT_OPS_003_HOSTED_GAP.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-004-review-closeout.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/REVIEWER_CHECKLIST.md`
- `services/control-plane/bff/main.py` Portfolio Book holdings and positions route signatures
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`
