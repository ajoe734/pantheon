# Management List Contract Audit - 2026-07-02

| Field | Value |
|---|---|
| Scope | Static audit of `services/control-plane/bff/main.py` Management list/table/board contracts |
| Tool | `scripts/audit_management_list_contract.py` |
| Baseline | `docs/architecture/management-list-contract-baseline.json` |
| Result | 159 existing contract smells after the first nine remediation slices: 22 P0, 137 P1 |

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
Attribution envelope aliases, and `MGMT-LIST-CONTRACT-009` retired NL/AI
Management, Evolution Journal, and Persona Intent duplicate envelopes:

| Category | Count | Severity | Meaning |
|---|---:|---|---|
| `duplicate-envelope` | 5 | P0 | Response returns `data` plus top-level list aliases such as `items`, `rows`, `rankings`, `pools` |
| `duplicate-list-alias` | 4 | P0 | Same list value is returned under multiple semantic names |
| `source-record-in-list-dto` | 10 | P0 | Raw source record/document fields appear in list DTO helpers |
| `embedded-aggregate-payload` | 3 | P0 | List/board payload embeds related aggregate collections |
| `board-pack-full-child-payloads` | 0 | P0 | Board pack nests complete child endpoint responses |
| `camel-snake-duplicate` | 129 | P1 | DTOs return both casing variants for the same fields |
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
| Human Inbox And Governance Ledger | Inbox items and ledger helpers expose source records or detail-grade context in list flows | Move raw source/debug payloads to detail endpoints |
| NL/AI Management Surfaces | Remediated in `MGMT-LIST-CONTRACT-009`: AI audit, conversation list/detail, Evolution Journal, Persona Intent, Python tests, and typed client adapters no longer expose top-level list aliases | Continue removing row-level casing duplicates in Management AI helper payloads |
| Remaining P0 Cluster | Human Inbox, Governance Ledger, HIQ Backlog, Intervention Stream, Sentinel Pulse, and Evidence degraded helpers still expose source records, duplicate envelopes, or embedded child aggregates | Move source/debug payloads to detail endpoints and collapse list envelopes to `data.items` |
| Frontend Consumption Pattern | Remediated contracts now reject top-level aliases in focused tests; remaining legacy management client patterns still tolerate multiple shapes on unmigrated routes | Frontend must request server filters/page and adapt one canonical envelope only |

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
8. Normalize remaining duplicate envelopes in Human Inbox, Governance Ledger,
   HIQ Backlog, Intervention Stream, and Evidence degraded responses.
9. Move raw `sourceRecord` and detail-grade helper data out of Human Inbox,
   Sentinel/Governance helpers, and list DTOs.
10. Remove camel/snake duplicates from migrated endpoints and delete retired
   fingerprints from the baseline.

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
