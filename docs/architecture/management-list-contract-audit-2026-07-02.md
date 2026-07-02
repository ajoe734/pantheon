# Management List Contract Audit - 2026-07-02

| Field | Value |
|---|---|
| Scope | Static audit of `services/control-plane/bff/main.py` Management list/table/board contracts |
| Tool | `scripts/audit_management_list_contract.py` |
| Baseline | `docs/architecture/management-list-contract-baseline.json` |
| Result | 65 existing contract smells after `MGMT-LIST-CONTRACT-018`: 0 P0, 65 P1 |

## Trigger

Live measurement of `/bff/management/persona-fleet` showed the real bottleneck:

- Management page HTML/JS reached `DOMContentLoaded` in about 0.2 s.
- `/bff/management/persona-fleet` returned about 16.8 MB JSON.
- Five direct curls took about 2.3-3.4 s each.
- Browser first rows appeared around 5.0 s.
- `/bff/me` alone was about 20-30 ms, but large same-origin JSON transfer
  could delay small requests.
- The same persona rows were duplicated across top-level `items`, `data.items`,
  and `data.persona_fleet`.
- Rows also carried snake_case/camelCase copies and deep connector/source health.
- The frontend displayed only production rows after downloading all rows.

That is not a frontend button problem. It is a list-contract problem.

## Static Audit Summary

The guardrail found these categories in the current source after
`MGMT-LIST-CONTRACT-002` retired the four `/bff/management/persona-fleet`
findings, `MGMT-LIST-CONTRACT-003` retired nine board-pack findings,
`MGMT-LIST-CONTRACT-004` retired the Portfolio Book core/pools findings,
`MGMT-LIST-CONTRACT-005` retired the Portfolio Book exposure/holdings/positions
findings, `MGMT-LIST-CONTRACT-006` retired PM12 analytics table envelope
findings, `MGMT-LIST-CONTRACT-007` retired Persona League and Quarterly
Ranking family envelope aliases, `MGMT-LIST-CONTRACT-008` retired Cost
Attribution envelope aliases, `MGMT-LIST-CONTRACT-009` retired NL/AI
Management, Evolution Journal, and Persona Intent duplicate envelopes, and
`MGMT-LIST-CONTRACT-010` retired the remaining P0 list-contract cluster in
Human Inbox, Evidence Explorer, HIQ Backlog, Intervention Stream, Governance
Ledger, and Sentinel Pulse helpers, `MGMT-LIST-CONTRACT-011` expanded the
guardrail to `_build_management_*` builders and retired newly visible P0
builder smells in Trading Pulse, Sentinel Pulse, Cockpit, Anomalies, EP5
readiness links, and Evidence Explorer casing, `MGMT-LIST-CONTRACT-012`
retired the first Human/Ops wire-casing duplicate cluster, and
`MGMT-LIST-CONTRACT-013` removed the remaining Human Inbox readiness/summary
wire-casing mirrors, `MGMT-LIST-CONTRACT-014` kept Evidence Explorer focused
on snake_case typed/frontend consumers and closed the remaining focused fixture
gaps around public rows, summary counts, facets, and degraded envelopes, and
`MGMT-LIST-CONTRACT-015` removed PM12 quarterly ranking formula/window,
governance evidence, ranking summary, formula summary, and recommendation
summary casing mirrors, `MGMT-LIST-CONTRACT-016` hardened frontend live
transport so live-mode tests must prove they call the configured BFF URL
instead of silently returning mock data, and `MGMT-LIST-CONTRACT-017` removed PM12 quarterly
ranking row, drilldown, recommendation row, and HumanGate command fixture
casing mirrors while slimming drilldown source breakdowns, and
`MGMT-LIST-CONTRACT-018` removed PM12 performance attribution casing mirrors,
moved row DTO projection behind page slicing, and aligned the adjacent PM12
persona-league DTOs and tests to the same snake_case-only wire contract:

| Category | Count | Severity | Meaning |
|---|---:|---|---|
| `duplicate-envelope` | 0 | P0 | Response returns `data` plus top-level list aliases such as `items`, `rows`, `rankings`, `pools` |
| `duplicate-list-alias` | 0 | P0 | Same list value is returned under multiple semantic names |
| `source-record-in-list-dto` | 0 | P0 | Raw source record/document fields appear in list DTO helpers |
| `embedded-aggregate-payload` | 0 | P0 | List/board payload embeds related aggregate collections |
| `board-pack-full-child-payloads` | 0 | P0 | Board pack nests complete child endpoint responses |
| `camel-snake-duplicate` | 61 | P1 | DTOs return both casing variants for the same fields |
| `project-before-page` | 4 | P1 | Endpoint/helper projects broad aggregates before page slicing |
| `heavy-row-helper` | 0 | P1 | Row helper includes detail-grade nested policy/session/memory/source data |

The complete machine-readable list is in
`docs/architecture/management-list-contract-baseline.json`.

## Highest Impact Findings

| Area | Evidence | Required fix |
|---|---|---|
| Persona Fleet | Remediated in `MGMT-LIST-CONTRACT-002`: `/bff/management/persona-fleet` now returns `data.items`, `data.summary`, top-level `page_info`, and `meta.related` links only | Keep source/research health detail on detail/health endpoints and require server-side filters such as `deployment_stage` |
| Board Pack | Remediated in `MGMT-LIST-CONTRACT-003`: `_management_board_pack_response` now returns section summaries, counts, status, and hrefs without child endpoint payloads | Keep board-pack summary-only; fetch full child sections from their dedicated routes |
| Portfolio Book Family | Remediated across `MGMT-LIST-CONTRACT-004` and `MGMT-LIST-CONTRACT-005`: core, pools, exposure, holdings, and positions now use one envelope and snake_case rows | Keep the family on `data.items`/`data.summary` and move future row expansion to detail routes |
| PM12 Analytics Tables | Remediated in `MGMT-LIST-CONTRACT-006`: performance attribution, strategy allocation, capital flow, risk radar, incident timeline, and loop throughput now use one list envelope. `MGMT-LIST-CONTRACT-018` removed performance-attribution row/summary/metrics/source-ref casing mirrors and pages before row DTO projection | Continue row-level casing cleanup in Strategy Allocation, Capital Flow, Risk Radar, Incident Timeline, Loop Throughput, and Cost Attribution |
| Persona League Family | Remediated in `MGMT-LIST-CONTRACT-007`: league, rankings, movers, tiers, heatmap, quarterly ranking, recommendations, typed client contracts, and the legacy `/bff/persona-league` helper now use one `data.items`/`data.summary` envelope. `MGMT-LIST-CONTRACT-007B` removed the shadowed legacy `/bff/management/persona-league` decorator. `MGMT-LIST-CONTRACT-018` aligned the PM12 persona-league row, ranking, mover, tier, heatmap, quarterly score-field, typed-contract, and focused-test DTOs to snake_case-only output while keeping detail-grade policy/session/memory data out of list rows. | Keep PM12 on one canonical wire contract; avoid rebuilding duplicate first-level UI pages for every PM12 endpoint |
| Performance And Cost Attribution | Cost Attribution duplicate list aliases were remediated in `MGMT-LIST-CONTRACT-008`; Performance Attribution casing and page-before-projection were remediated in `MGMT-LIST-CONTRACT-018` | Continue Cost Attribution casing and page-before-projection cleanup |
| Human Inbox And Governance Ledger | Remediated in `MGMT-LIST-CONTRACT-010`: Human Inbox and Governance Ledger list contracts now return canonical `data.items`/`data.summary` envelopes and omit raw source records | Keep raw source/debug payloads on detail endpoints only |
| Human/Ops Wire Casing | Remediated across `MGMT-LIST-CONTRACT-012` and `MGMT-LIST-CONTRACT-013`: HIQ Backlog, Intervention Stream, Governance Ledger, and Human Inbox readiness/summary casing mirrors were removed | Continue the same casing cleanup for other Management families |
| NL/AI Management Surfaces | Remediated in `MGMT-LIST-CONTRACT-009`: AI audit, conversation list/detail, Evolution Journal, Persona Intent, Python tests, and typed client adapters no longer expose top-level list aliases | Continue removing row-level casing duplicates in Management AI helper payloads |
| Remaining P0 Cluster | Remediated in `MGMT-LIST-CONTRACT-010`: Evidence Explorer, HIQ Backlog, Intervention Stream, Sentinel Pulse, Human Inbox, and Governance Ledger no longer expose duplicate list envelopes, embedded child aggregates, or raw source records in list DTOs | Keep source/debug payloads on detail endpoints and enforce canonical `data.items` list envelopes |
| Evidence Explorer Wire Casing | Remediated in `MGMT-LIST-CONTRACT-014`: Evidence Explorer rows, summaries, facets, degraded envelopes, typed contracts, and focused frontend fixtures now use snake_case wire keys without camelCase mirrors | Keep temporary frontend fallback reads only at adapter boundaries while formal Management DTOs remain single-casing |
| PM12 Quarterly Ranking Casing | Remediated across `MGMT-LIST-CONTRACT-015` and `MGMT-LIST-CONTRACT-017`: formula/window/governance evidence helpers, ranking rows, drilldown summaries/data, recommendation rows, and focused command fixtures now use snake_case wire keys; drilldown source breakdowns expose counts/summaries instead of detail-grade helper blobs | Continue with remaining PM12 analytics table casing and page-before-projection issues |
| Builder Blind Spot | Remediated in `MGMT-LIST-CONTRACT-011`: `_build_management_*` helpers are now audited, and newly visible Trading Pulse, Sentinel Pulse, Cockpit, Anomalies, EP5 readiness, and Evidence Explorer builder smells were fixed instead of added to the baseline | Keep helper builders under the same list contract as route handlers; no hidden builder aliases |
| Frontend Live Transport | Hardened in `MGMT-LIST-CONTRACT-016`: frontend live reads now honor `VITE_BFF_MODE`, `VITE_BFF_BASE_URL`, and strict fallback settings instead of silently returning mock data | Live-mode tests must prove they actually call the configured BFF URL |
| Frontend Consumption Pattern | Remediated contracts now reject top-level aliases in focused tests; Human Inbox, Evidence, Trading Pulse, and Live Evidence adapters consume canonical `data.items`/`data.summary` shapes | Frontend must request server filters/page and adapt one canonical envelope only |

## Remediation Order

1. Done in `MGMT-LIST-CONTRACT-002`: `/bff/management/persona-fleet` now uses
   slim list rows, removed duplicate row copies, added server filters, and moved
   connector/source health out of the list contract.
2. Done in `MGMT-LIST-CONTRACT-003`: board-pack now returns summary-only
   sections and href-only related links instead of complete child endpoint
   payloads.
3. Done across `MGMT-LIST-CONTRACT-004` and `MGMT-LIST-CONTRACT-005`:
   Portfolio Book core, pools, exposure, holdings, and positions now use one
   list envelope and snake_case rows.
4. Done in `MGMT-LIST-CONTRACT-006`: PM12 analytics tables now use one
   `data.items`/`data.summary` envelope without top-level `items`/`rows`
   aliases.
5. Done in `MGMT-LIST-CONTRACT-007`: Persona League and Quarterly Ranking
   family endpoints now return one `data.items`/`data.summary` envelope; related
   collections stay inside `data.related` or explicitly named nested fields, not
   top-level aliases. `MGMT-LIST-CONTRACT-007B` also removed the shadowed legacy
   `/bff/management/persona-league` decorator so that management route has one
   registered owner.
6. Done in `MGMT-LIST-CONTRACT-008`: Cost Attribution now returns one
   `data.items`/`data.summary` envelope without top-level `items`/`rows`/
   `attributions` aliases.
7. Done in `MGMT-LIST-CONTRACT-009`: NL/AI Management audit/conversation,
   Evolution Journal, and Persona Intent now use one `data.items`/
   `data.summary` envelope; conversation detail no longer mirrors turns at the
   top level.
8. Done in `MGMT-LIST-CONTRACT-010`: Human Inbox, Evidence Explorer, HIQ
   Backlog, Intervention Stream, Governance Ledger, and Sentinel Pulse helpers
   no longer expose duplicate list envelopes, embedded child aggregates, or raw
   source records in list DTOs.
9. Done in `MGMT-LIST-CONTRACT-011`: the static guard now scans
   `_build_management_*` builders; Trading Pulse, Trading Pulse Rankings,
   Sentinel Pulse, Cockpit, Anomalies, EP5 readiness links, and Evidence
   Explorer no longer expose newly visible duplicate envelopes, raw source
   records, embedded alias pairs, or camel/snake copies in their migrated list
   DTOs.
10. Done in `MGMT-LIST-CONTRACT-012`: HIQ Backlog, Intervention Stream, and
    Governance Ledger list rows/summaries now use snake_case wire keys without
    camelCase mirrors.
11. Done in `MGMT-LIST-CONTRACT-013`: Human Inbox readiness blocker rows and
    Human Inbox summary fields now avoid camelCase wire mirrors.
12. Done in `MGMT-LIST-CONTRACT-014`: Evidence Explorer public rows, summary
    counts, facets, degraded timeout envelope, typed contracts, and focused
    frontend fixtures now use snake_case wire keys without camelCase mirrors.
13. Done in `MGMT-LIST-CONTRACT-015`: PM12 quarterly ranking formula/window,
    governance evidence, formula summaries, ranking summaries, and
    recommendation outer summaries now use snake_case wire keys without
    camelCase mirrors.
14. Done in `MGMT-LIST-CONTRACT-016`: frontend live transport now honors live
    mode/base URL/strict fallback settings instead of silently returning mock
    data.
15. Done in `MGMT-LIST-CONTRACT-017`: PM12 quarterly ranking row, drilldown,
    recommendation row, governance payload, typed contracts, and HumanGate
    command fixture reads now use snake_case wire keys; drilldown source
    breakdowns now expose lightweight summaries/counts instead of nested
    capability/session/memory helper payloads.
16. Done in `MGMT-LIST-CONTRACT-018`: PM12 performance attribution metrics,
    rows, source refs, summaries, typed contracts, and focused tests now use
    snake_case wire keys; row DTO projection now happens after page slicing.
    The adjacent PM12 persona-league DTOs and focused tests now also assert
    snake_case-only league rows, ranking rows, movers, tiers, heatmap cells, and
    quarterly score fields without detail-grade row helper payloads.
17. Remove camel/snake duplicates from the remaining migrated endpoints:
    Management AI/NL, Strategy Allocation, Capital Flow, Risk Radar, Incident
    Timeline, Loop Throughput, and Cost Attribution.
18. Fix remaining project-before-page findings so Human Inbox, Cost
    Attribution, Portfolio Exposure, and Portfolio Holdings filter and page
    before detail-grade projection.

## Enforcement

New development must run:

```bash
python3 scripts/audit_management_list_contract.py \
  --baseline docs/architecture/management-list-contract-baseline.json \
  --fail-on-new
```

This baseline intentionally allows current debt so the repo can keep moving, but
it fails any new duplicate envelope, list alias, embedded aggregate, source
record leak, or casing duplication introduced after this audit.
