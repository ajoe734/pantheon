# Management List Contract Audit - 2026-07-02

| Field | Value |
|---|---|
| Scope | Static audit of `services/control-plane/bff/main.py` Management list/table/board contracts |
| Tool | `scripts/audit_management_list_contract.py` |
| Baseline | `docs/architecture/management-list-contract-baseline.json` |
| Result | 187 existing contract smells after the first six remediation slices: 49 P0, 138 P1 |

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
findings, `MGMT-LIST-CONTRACT-003` retired nine board-pack findings, and
`MGMT-LIST-CONTRACT-004` retired the Portfolio Book core/pools findings, and
`MGMT-LIST-CONTRACT-005` retired the Portfolio Book exposure/holdings/positions
findings, and `MGMT-LIST-CONTRACT-006` retired PM12 analytics table envelope
findings:

| Category | Count | Severity | Meaning |
|---|---:|---|---|
| `duplicate-envelope` | 19 | P0 | Response returns `data` plus top-level list aliases such as `items`, `rows`, `rankings`, `pools` |
| `duplicate-list-alias` | 17 | P0 | Same list value is returned under multiple semantic names |
| `source-record-in-list-dto` | 10 | P0 | Raw source record/document fields appear in list DTO helpers |
| `embedded-aggregate-payload` | 3 | P0 | List/board payload embeds related aggregate collections |
| `board-pack-full-child-payloads` | 0 | P0 | Board pack nests complete child endpoint responses |
| `camel-snake-duplicate` | 130 | P1 | DTOs return both casing variants for the same fields |
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
| Persona League Family | League, rankings, movers, tiers, heatmap, quarterly ranking, and recommendations repeat aliases and carry casing duplicates | Split list rows from ranking/detail payloads and keep one casing |
| Performance And Cost Attribution | Rows are built from runtime telemetry before slicing and returned as `items`, `rows`, and sometimes `attributions` | Filter and page before row expansion; remove aliases |
| Human Inbox And Governance Ledger | Inbox items and ledger helpers expose source records or detail-grade context in list flows | Move raw source/debug payloads to detail endpoints |
| NL/AI Management Surfaces | Conversation/audit payloads use duplicate list envelopes and many casing duplicates | Apply the same list envelope and casing standard |
| Frontend Consumption Pattern | Current management client patterns tolerate multiple response shapes and filter visible rows client-side | Frontend must request server filters/page and adapt one canonical envelope only |

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
5. Normalize Persona League, Quarterly Ranking, and Cost Attribution families.
6. Move raw `sourceRecord` and detail-grade helper data out of Human Inbox,
   Sentinel/Governance helpers, and list DTOs.
7. Remove camel/snake duplicates from migrated endpoints and delete retired
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
