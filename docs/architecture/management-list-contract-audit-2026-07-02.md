# Management List Contract Audit - 2026-07-02

| Field | Value |
|---|---|
| Scope | Static audit of `services/control-plane/bff/main.py` Management list/table/board contracts |
| Tool | `scripts/audit_management_list_contract.py` |
| Baseline | `docs/architecture/management-list-contract-baseline.json` |
| Result | 113 existing contract smells after the active remediation set: 0 P0, 113 P1 |

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
wire-casing mirrors:

| Category | Count | Severity | Meaning |
|---|---:|---|---|
| `duplicate-envelope` | 0 | P0 | Response returns `data` plus top-level list aliases such as `items`, `rows`, `rankings`, `pools` |
| `duplicate-list-alias` | 0 | P0 | Same list value is returned under multiple semantic names |
| `source-record-in-list-dto` | 0 | P0 | Raw source record/document fields appear in list DTO helpers |
| `embedded-aggregate-payload` | 0 | P0 | List/board payload embeds related aggregate collections |
| `board-pack-full-child-payloads` | 0 | P0 | Board pack nests complete child endpoint responses |
| `camel-snake-duplicate` | 105 | P1 | DTOs return both casing variants for the same fields |
| `project-before-page` | 5 | P1 | Endpoint/helper projects broad aggregates before page slicing |
| `heavy-row-helper` | 3 | P1 | Row helper includes detail-grade nested policy/session/memory/source data |

The complete machine-readable list is in
`docs/architecture/management-list-contract-baseline.json`.

## Highest Impact Findings

| Area | Evidence | Required fix |
|---|---|---|
| Persona Fleet | Remediated in `MGMT-LIST-CONTRACT-002`: `/bff/management/persona-fleet` now returns `data.items`, `data.summary`, top-level `page_info`, and `meta.related` links only | Keep source/research health detail on detail/health endpoints and require server-side filters such as `deployment_stage` |
| Board Pack | Remediated in `MGMT-LIST-CONTRACT-003`: `_management_board_pack_response` now returns section summaries, counts, status, and hrefs without child endpoint payloads | Keep board-pack summary-only; fetch full child sections from their dedicated routes |
| Portfolio Book Family | Remediated across `MGMT-LIST-CONTRACT-004` and `MGMT-LIST-CONTRACT-005`: core, pools, exposure, holdings, and positions now use one envelope and snake_case rows | Keep the family on `data.items`/`data.summary` and move future row expansion to detail routes |
| PM12 Analytics Tables | Remediated in `MGMT-LIST-CONTRACT-006`: performance attribution, strategy allocation, capital flow, risk radar, incident timeline, and loop throughput now use one list envelope | Continue removing row-level casing duplicates and project/page order issues in follow-up slices |
| Persona League Family | Remediated in `MGMT-LIST-CONTRACT-007`: league, rankings, movers, tiers, heatmap, quarterly ranking, recommendations, typed client contracts, and the legacy `/bff/persona-league` helper now use one `data.items`/`data.summary` envelope. `MGMT-LIST-CONTRACT-007B` removed the shadowed legacy `/bff/management/persona-league` decorator. | Continue removing row-level casing duplicates in a follow-up slice |
| Performance And Cost Attribution | Cost Attribution duplicate list aliases were remediated in `MGMT-LIST-CONTRACT-008`; remaining risk is row projection before page slicing and casing duplication | Filter and page before row expansion; continue removing row-level casing duplicates |
| Human Inbox And Governance Ledger | Remediated in `MGMT-LIST-CONTRACT-010`: Human Inbox and Governance Ledger list contracts now return canonical `data.items`/`data.summary` envelopes and omit raw source records | Keep raw source/debug payloads on detail endpoints only |
| Human/Ops Wire Casing | Remediated across `MGMT-LIST-CONTRACT-012` and `MGMT-LIST-CONTRACT-013`: HIQ Backlog, Intervention Stream, Governance Ledger, and Human Inbox readiness/summary casing mirrors were removed | Continue the same casing cleanup for other Management families |
| NL/AI Management Surfaces | Remediated in `MGMT-LIST-CONTRACT-009`: AI audit, conversation list/detail, Evolution Journal, Persona Intent, Python tests, and typed client adapters no longer expose top-level list aliases | Continue removing row-level casing duplicates in Management AI helper payloads |
| Builder Blind Spot | Remediated in `MGMT-LIST-CONTRACT-011`: `_build_management_*` helpers are now audited, and newly visible Trading Pulse, Sentinel Pulse, Cockpit, Anomalies, EP5 readiness, and Evidence Explorer builder smells were fixed instead of added to the baseline | Keep helper builders under the same list contract as route handlers; no hidden builder aliases |
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
12. Remove camel/snake duplicates from the remaining migrated endpoints and
    delete retired fingerprints from the baseline.
13. Fix remaining project-before-page and heavy-row-helper findings so large
    list endpoints filter and page before detail-grade projection.

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
