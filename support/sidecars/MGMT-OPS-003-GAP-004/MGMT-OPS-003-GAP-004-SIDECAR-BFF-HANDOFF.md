# MGMT-OPS-003-GAP-004 Sidecar BFF / Frontend Handoff

Task: `MGMT-OPS-003-GAP-004-SIDECAR-BFF-HANDOFF`
Parent: `MGMT-OPS-003-GAP-004`
Owner: Codex
Reviewer: Codex2
Kind: support-only `bff_handoff_packet`

## Boundary

This packet maps the existing Portfolio Book BFF surface to the frontend and
the parent's independent closeout. It does not change canonical policy, the
BFF contract, runtime truth, frontend code, or the parent verdict. Hosted
counts quoted by source artifacts are evidence snapshots, not contract
defaults.

## BFF Query Handoff

The implementation source of truth remains the deployed BFF OpenAPI contract.
The current repository route signatures expose the following read surfaces:

| Operator need | Route | Query key | Frontend URL state | Closeout assertion |
|---|---|---|---|---|
| Portfolio summary | `GET /bff/management/portfolio-book` | common entity filters | preserve supported entity context | summary values agree with the same authenticated capture |
| Holdings and incidents | `GET /bff/management/portfolio-book/holdings` | see below | round-trip all active filters | visible rows, summary counts, incidents, and confidence agree with response |
| Positions | `GET /bff/management/portfolio-book/positions` | same filter set as holdings | retain filters when switching views | position result is not compared with an independently filtered capture |
| Performance attribution | relevant Performance Attribution BFF link returned in row payload | preserve returned context | follow the BFF-provided link | degraded input never becomes formal attribution |

Required filter mapping for holdings and positions:

| UI filter | BFF query key | Notes |
|---|---|---|
| Stage | `deployment_stage` | Render paper, canary, live, and unknown explicitly; unknown must not inherit another stage's treatment. |
| Broker | `broker_id` | Missing broker identity remains visible as degraded/quarantined truth. |
| Runtime | `runtime_id` | Preserve the selected runtime in cross-page navigation. |
| Source status | `source_status` | Do not translate degraded/stale/unavailable into a success label. |
| Stale telemetry | `stale_telemetry` | Boolean query; URL serialization must survive reload. |
| Risk state | `risk_state` | Treat as source truth, not a client-invented classification. |

The routes also accept entity/search/pagination context including
`capital_pool_id`, `persona_id`, `status`, `q`, `page_token`, and `page_size`.
The frontend must use the names advertised by the deployed OpenAPI document
and must not silently substitute camel-case aliases. Pagination must not hide
incidents or make the displayed total look healthier than the response.

## Response Fields The UI Must Preserve

- Summary counters: holding/source rows, telemetry coverage, degraded rows,
  stale rows, missing bindings, and incident totals when present.
- Row truth: persona, runtime, pool, broker, deployment stage, capital scope,
  `source_status`, `source_issues`, `risk_state`, and telemetry staleness.
- Incident truth: kind, severity, affected row/context, source issues, and the
  Human Review action/link supplied by the response.
- Confidence truth: partial, degraded, stale, unavailable, or fallback data
  must never be presented as formal attribution or fully covered.
- Navigation truth: prefer response-provided Persona Fleet, Performance
  Attribution, and Human Review links. Preserve persona, runtime, pool,
  holding, period, risk, and source context carried by those links.

Contract tests demonstrate the fail-closed case: a runtime without telemetry
remains a visible degraded holding, produces a `degraded_source` incident with
`MISSING_TELEMETRY`, and carries a Human Review link. The frontend must not
drop that row merely because no position telemetry exists.

## Operator Journey For Parent Review

1. Open Portfolio Book in strict live mode and capture the authenticated
   summary, holdings, positions, and attribution responses used by the page.
2. Compare visible counters and every degraded/missing-binding row with that
   single capture set; record discrepancies rather than reconciling them away.
3. Exercise stage, broker, runtime, source-status, stale-telemetry, and
   risk-state filters. For each, verify request query, visible result, URL,
   reload persistence, and clear/reset behavior.
4. Select a degraded incident and follow the supplied links through Persona
   Fleet and Performance Attribution to Human Review. Confirm entity and
   source context survives each transition.
5. Repeat normal and degraded paths at desktop and mobile widths; cold-load
   and reload lazy routes.
6. Record frontend/BFF served commit identities, console exception count,
   failed required request count, and fallback/seed-data count.

## Fail-Closed Evidence Bundle

The parent reviewer should reject the handoff unless the evidence bundle
contains all of the following for the same deployed frontend/BFF identities:

- frontend and Pantheon PR numbers, head and merge SHAs, merge targets, checks,
  deployment runs, and ancestry from served commits;
- authenticated core, holdings, positions, and attribution payload captures;
- desktop and mobile screenshots covering normal and degraded states;
- a per-filter request/URL/reload result table;
- UI-to-API comparisons for counts, labels, incidents, stages, and confidence;
- explicit console, required-network-failure, lazy-chunk-failure, and
  fallback/seed-data counts, including zeroes;
- raw samples of runtime, binding, pool, deployment, broker/ledger, and
  telemetry records for unresolved rows;
- residual risks with owner and follow-up task, none of which may violate a
  parent acceptance criterion.

Historical snapshots such as 14 degraded holdings/10 missing bindings or the
later 27-incident capture are useful regression references only. The parent
verdict must use a fresh, internally consistent hosted capture.

## Composition Handoff

- `MGMT-OPS-003-GAP-001` / frontend owner: consume the query and response
  mapping without creating a Pantheon-local frontend mirror.
- `MGMT-OPS-003-GAP-002` / runtime owner: keep unresolved records visible and
  quarantined; never improve counters by deletion or client filtering.
- `MGMT-OPS-003-GAP-003` / hosted E2E owner: exercise the operator journey
  against the exact captured responses and deployed identities.
- `MGMT-OPS-003-GAP-004` / parent owner and reviewer: use this packet as a
  checklist aid only; the signed `REVIEWER_CHECKLIST.md` and direct hosted
  evidence determine `APPROVE` or `REQUEST_CHANGES`.
- `MGMT-PERF-IA-003`: preserve these query, confidence, incident, capital-scope,
  and context-link behaviors during Performance Center consolidation.

## Source References

- `docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/MGMT_OPS_003_HOSTED_GAP.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-frontend-monitor.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-002-runtime-data-quality.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-003-hosted-workflow-e2e.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-004-review-closeout.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/REVIEWER_CHECKLIST.md`
- `services/control-plane/bff/main.py` Portfolio Book route signatures
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`
