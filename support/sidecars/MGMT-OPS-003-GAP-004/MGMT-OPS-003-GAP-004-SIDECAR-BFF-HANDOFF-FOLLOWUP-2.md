# MGMT-OPS-003-GAP-004 BFF / Frontend Handoff Follow-up 2

Task: `MGMT-OPS-003-GAP-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`  
Parent: `MGMT-OPS-003-GAP-004`  
Owner: Codex  
Reviewer: Codex2  
Kind: support-only `bff_handoff_packet`

## Scope Boundary

This follow-up resolves review ambiguity in the earlier sidecar packet. It is
not a new contract and does not change BFF routes, frontend code, runtime
truth, canonical documents, or the parent's verdict. The deployed OpenAPI
document and authenticated responses remain authoritative.

## Exact Query-Key Handoff

Both `GET /bff/management/portfolio-book/holdings` and
`GET /bff/management/portfolio-book/positions` currently declare the same
query surface:

`capital_pool_id`, `persona_id`, `runtime_id`, `deployment_stage`, `broker_id`,
`status`, `source_status`, `stale_telemetry`, `risk_state`, `q`, `page_token`,
and `page_size`.

The six required operator filters therefore map as follows:

| Operator control | Request query key | Review caution |
|---|---|---|
| Stage | `deployment_stage` | Do not send the UI/URL alias `stage` to these routes. |
| Broker | `broker_id` | Do not send `broker`; missing identity must remain visible. |
| Runtime | `runtime_id` | Do not send `runtime`; preserve this value in linked-page context. |
| Source status | `source_status` | Preserve degraded, stale, and unavailable values verbatim. |
| Stale telemetry | `stale_telemetry` | Send a boolean value and prove both URL reload and clear/reset behavior. |
| Risk state | `risk_state` | Do not derive or strengthen this classification client-side. |

Review artifacts that use `stage`, `broker`, or `runtime` as human-readable
labels are not proof that the BFF received a valid filter. Browser/network
evidence must show the exact request keys above. Comma-separated values are
accepted for the identifier and categorical filters; the reviewer should
still test the serialization actually emitted by the deployed frontend.

## Response and Pagination Invariants

- Compare the UI against one authenticated capture set from the same served
  BFF identity. Do not combine summary, rows, or incidents from different
  filter states or timestamps.
- `data.summary.holding_count`, `page_info.total`, and
  `meta.summary/source_coverage` describe the complete filtered set, while
  `data.items` is the current page. A `page_size`-limited row count must not be
  presented as the filtered total.
- `meta.incidents` is computed from the complete filtered holding set before
  page slicing. An incident may therefore refer to a row outside the current
  page; the UI must not hide it or claim the current page is incident-free.
- `meta.filters` echoes the resolved filters. For each filter probe, record the
  browser URL, outbound request, echoed filter, total, incident count, visible
  rows, reload result, and clear/reset result.
- Positions delegate to the holdings composition with the same filters. A
  holdings capture and a positions capture are comparable only when their
  query sets and deployed BFF identity match.

## Operator Journey Evidence Table

The parent reviewer should require one row per step rather than accepting a
general screenshot claim:

| Step | Required evidence | Fail-closed condition |
|---|---|---|
| Open Portfolio Book | served frontend/BFF identities plus authenticated core, holdings, positions, and attribution captures | identity or capture set is missing/mismatched |
| Apply each of six filters | URL, exact outbound query key/value, echoed `meta.filters`, visible result, reload, clear/reset | alias reaches BFF, state disappears, or rows/incidents are hidden |
| Inspect degraded incident | incident kind/severity/source issues and affected row/context | degraded row is omitted or shown as covered/formal |
| Follow Persona Fleet link | preserved persona/runtime/pool and live-data banner/state | focus is lost or seed/non-production data silently replaces live truth |
| Follow Performance Attribution link | preserved entity/period/source context and confidence label | degraded input becomes formal attribution |
| Reach Human Review | preserved holding/persona/runtime/pool/risk/source-issue context | review action is absent or context is reconstructed inaccurately |
| Repeat desktop/mobile cold load | screenshots, console exceptions, failed required requests, lazy-chunk failures, fallback/seed counts | any required count is omitted rather than explicitly recorded, including zero |

## Reviewer Probe Matrix

For holdings and positions, capture at minimum:

1. no-filter baseline;
2. one probe for each exact required query key;
3. `stale_telemetry=true` and its cleared state;
4. a combined filter probe proving filters compose;
5. a paginated probe where `page_size` is smaller than `page_info.total`;
6. a degraded/missing-binding probe showing the row and incident remain
   actionable.

For every probe, a `200` alone is insufficient. Assert that `meta.filters`
echoes the intended value and that UI counts, confidence, labels, incidents,
and links agree with the response. Historical counts such as 14 degraded rows
or 27 incidents are regression references only, never expected constants.

## Composition Handoff

- Frontend and hosted-E2E owners should use this exact-key and pagination
  matrix when producing current hosted evidence.
- Runtime owners retain responsibility for repairing or explicitly
  quarantining source gaps; this packet does not authorize deleting records.
- The `MGMT-OPS-003-GAP-004` owner/reviewer must independently rerun the
  probes and use the signed reviewer checklist for the verdict.
- `MGMT-PERF-IA-003` should preserve the same exact query keys, fail-closed
  confidence, complete-set incident visibility, and link context during later
  consolidation.

## Sources Checked

- `support/sidecars/MGMT-OPS-003-GAP-004/MGMT-OPS-003-GAP-004-SIDECAR-BFF-HANDOFF.md`
- `services/control-plane/bff/main.py` holdings and positions route signatures
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/REVIEWER_CHECKLIST.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-004-review-closeout.md`
- `docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/MGMT_OPS_003_HOSTED_GAP.md`
