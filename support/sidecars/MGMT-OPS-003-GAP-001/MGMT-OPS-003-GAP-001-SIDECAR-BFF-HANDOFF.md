# MGMT-OPS-003-GAP-001 BFF / Frontend Handoff

Status: support packet for parent owner review

Parent task: `MGMT-OPS-003-GAP-001`

Sidecar task: `MGMT-OPS-003-GAP-001-SIDECAR-BFF-HANDOFF`

Owned layer: BFF query inventory, frontend consumption notes, and operator
journey handoff

Not changing: BFF runtime, canonical contracts, governance truth, or the
`execute-plans` implementation

Intended consumer: the parent owner implementing Portfolio Book in
`ajoe734/execute-plans`

## Outcome

The live BFF contract already supplies the six required filter dimensions,
row-level source diagnostics, incidents, capital-scope identity, coverage
counters, and governed cross-page links. The frontend closure should consume
these values without deriving stronger confidence from aggregate totals or
silently replacing missing identifiers.

The principal collection endpoint is:

```text
GET /bff/management/portfolio-book/holdings
```

`GET /bff/management/portfolio-book/positions` accepts the same query shape
and is composed from the holdings response. Portfolio Book should use one
canonical URL/filter state and pass the applicable values unchanged to these
requests.

## Query Handoff

| Operator control | BFF query parameter | Encoding / behavior |
|---|---|---|
| Capital stage | `deployment_stage` | Comma-separated values; preserve `paper`, `canary`, `live`, and any unknown returned value as distinct text states. |
| Broker | `broker_id` | Comma-separated identifiers; an absent broker identity is data quality truth, not a frontend-generated broker option. |
| Runtime | `runtime_id` | Comma-separated identifiers. |
| Source status | `source_status` | Comma-separated values; do not coerce degraded, stale, or unavailable into an OK/covered state. |
| Stale telemetry | `stale_telemetry` | Boolean `true` or `false`; omission means no stale filter. |
| Risk state | `risk_state` | Comma-separated values returned by the BFF. |

Additional context supported by the endpoint is `capital_pool_id`,
`persona_id`, `status`, `q`, `page_token`, and `page_size`. The response echoes
the effective values in `meta.filters`; the UI can compare this echo with the
URL state when testing refresh persistence. Filters apply before pagination,
so a filter change must clear the current `page_token`.

Recommended URL ownership is one stable query key per BFF parameter. On load,
parse URL state, issue the request, and render controls from that same state.
On change, update the URL and request together. Do not maintain a second,
unobservable filter state that diverges after reload or browser navigation.

## Response-To-UI Mapping

| BFF location | Frontend use | Fail-closed rule |
|---|---|---|
| `data.summary.source_coverage` | Coverage cards for source rows, runtimes, telemetry runtimes, stale rows, missing bindings, and degraded sources | Missing counters are unavailable, not zero. |
| `data.summary.incident_count` | Portfolio-level incident count | Cross-check against the complete `meta.incidents` collection for the current filtered result, not only the paginated rows. |
| `data.summary.source_status_counts`, `risk_state_counts`, `by_stage`, `by_broker` | Filter/result summaries | Preserve unknown keys; do not collapse them into a healthy bucket. |
| `data.items[]` identity and ownership fields | Holding table identity, persona, runtime, pool, strategy, artifact, and broker context | Render missing identity explicitly; never synthesize an owner. |
| `data.items[].capital_scope` | Accessible paper-ledger, canary-sleeve, live-capital-pool, or unknown label | Text/icon semantics are required; color alone is insufficient. Unknown must not inherit paper or live styling. |
| `data.items[].source_status`, `source_issues`, `telemetry_stale`, `risk_state` | Row reliability and issue details | Any degraded, stale, unavailable, or missing-source state blocks formal/fully-covered language. |
| `data.items[].links` | Persona Fleet, Performance Attribution, and Human Review actions | Prefer BFF-provided links because they preserve supported identifiers and target context. Hide only a missing individual link, not the affected row. |
| `meta.incidents[]` | Incident list with severity/kind, source issues, affected holding context, and Human Review action | Incidents remain visible and actionable even when their holding is outside the current page. |
| `meta.surfaces` | Page/section degraded or unavailable banners | A degraded composed surface may still contain useful rows; show both the banner and rows. |
| `page_info.next_page_token`, `page_info.total` | Server pagination | Do not infer completeness from `data.items.length`. |

The contract test proves that a missing-telemetry holding remains in the table,
has `source_status=degraded`, `risk_state=degraded_source`, an explicit
`MISSING_TELEMETRY` incident, and paper-ledger capital scope. The related
attribution response is only `partial` and its PnL is null. This is the minimum
reference behavior for the frontend's degraded fixture.

## Operator Journey

1. The operator lands on Portfolio Book and immediately sees coverage and
   incident counters alongside explicit capital-scope segmentation.
2. A degraded surface banner explains source availability without removing
   holdings that can still be inspected.
3. The operator filters by stage, broker, runtime, source status, stale
   telemetry, or risk state. The URL changes and the BFF echo in
   `meta.filters` matches after request completion and reload.
4. Each affected row exposes source status, risk state, and source issues. The
   incident view exposes kind/severity and keeps the row actionable.
5. The operator follows BFF-provided links to Persona Fleet or Performance
   Attribution for diagnosis, or Human Review for governed action. Returning
   through browser history restores the Portfolio Book URL and filter state.
6. Empty filtered results distinguish "no matching rows" from source
   unavailable. Partial/degraded/stale responses retain their warning and must
   not show formal attribution or fully covered copy.

## Frontend Acceptance Fixture

The parent implementation should include a response fixture matching the
hosted observation of 14 holdings, 14 degraded holdings/incidents, 10 missing
bindings, 6 runtimes, and 2 telemetry runtimes. At minimum it should prove:

- all 14 incidents are discoverable even with pagination;
- all six filter parameters affect the request and survive reload;
- paper, canary, live, and unknown scopes have distinct accessible labels;
- missing/degraded/stale/unavailable data never produces formal or covered
  success copy;
- a missing BFF link removes only that action, not its row or incident;
- BFF-provided Human Review links are used without dropping target context;
- loading, empty-match, partial, degraded, stale, unavailable, request-failure,
  and malformed/unknown-enum states have explicit behavior.

## Parent Owner Decisions

These are implementation choices for `execute-plans`, not requests to change
the Pantheon BFF contract:

1. Choose whether incidents render as an always-visible panel or a count plus
   accessible drawer. Either choice must keep all filtered incidents
   discoverable independently of row pagination.
2. Choose the URL serialization for multi-select values while sending the BFF
   comma-separated parameter form.
3. Confirm whether the existing frontend client preserves BFF-supplied relative
   links verbatim or needs a single route-normalization helper.
4. Treat newly returned enum values as explicit unknown states until product
   copy is added; do not map them to a healthy default.

## Verification Sources

- `services/control-plane/bff/main.py`: holdings and positions route query,
  filtering, summary, incident, surface, and pagination behavior.
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`: stage;
  broker/source/stale/risk filtering; degraded holding; capital scope; links;
  and non-formal attribution evidence.
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-frontend-monitor.md`:
  parent delivery and acceptance boundary.
- `docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/MGMT_OPS_003_HOSTED_GAP.md`:
  hosted counts and plan-to-live failure matrix.

This packet is advisory support material. The parent owner decides whether and
how to compose it into the main frontend delivery.
