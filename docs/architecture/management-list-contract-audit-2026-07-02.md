# Management List Contract Audit - 2026-07-02

| Field | Value |
|---|---|
| Scope | Static audit of `services/control-plane/bff/main.py` Management list/table/board contracts |
| Tool | `scripts/audit_management_list_contract.py` |
| Baseline | `docs/architecture/management-list-contract-baseline.json` |
| Result | 233 existing contract smells: 79 P0, 154 P1 |

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

The guardrail found these categories in the current source:

| Category | Count | Severity | Meaning |
|---|---:|---|---|
| `duplicate-envelope` | 32 | P0 | Response returns `data` plus top-level list aliases such as `items`, `rows`, `rankings`, `pools` |
| `duplicate-list-alias` | 30 | P0 | Same list value is returned under multiple semantic names |
| `source-record-in-list-dto` | 10 | P0 | Raw source record/document fields appear in list DTO helpers |
| `embedded-aggregate-payload` | 6 | P0 | List/board payload embeds related aggregate collections |
| `board-pack-full-child-payloads` | 1 | P0 | Board pack nests complete child endpoint responses |
| `camel-snake-duplicate` | 145 | P1 | DTOs return both casing variants for the same fields |
| `project-before-page` | 6 | P1 | Endpoint/helper projects broad aggregates before page slicing |
| `heavy-row-helper` | 3 | P1 | Row helper includes detail-grade nested policy/session/memory/source data |

The complete machine-readable list is in
`docs/architecture/management-list-contract-baseline.json`.

## Highest Impact Findings

| Area | Evidence | Required fix |
|---|---|---|
| Persona Fleet | `/bff/management/persona-fleet` returns `data.items`, `data.persona_fleet`, top-level `items`, and embeds `persona_league`, `capital_pools`, `runtime_bindings`, `human_inbox` | Create a slim list DTO and move capital/runtime/league/human inbox detail to detail or section endpoints |
| Board Pack | `_management_board_pack_response` calls child list endpoints, then embeds full payloads under `portfolioBook`, `personaLeague`, `performanceAttribution`, etc. | Return section summaries, counts, status, and hrefs only |
| Portfolio Book Family | Summary, pools, exposure, holdings, and positions repeat lists under `data`, top-level aliases, and domain aliases | Normalize one envelope, one list field, server filters, and slim rows |
| Persona League Family | League, rankings, movers, tiers, heatmap, quarterly ranking, and recommendations repeat aliases and carry casing duplicates | Split list rows from ranking/detail payloads and keep one casing |
| Performance And Cost Attribution | Rows are built from runtime telemetry before slicing and returned as `items`, `rows`, and sometimes `attributions` | Filter and page before row expansion; remove aliases |
| Human Inbox And Governance Ledger | Inbox items and ledger helpers expose source records or detail-grade context in list flows | Move raw source/debug payloads to detail endpoints |
| NL/AI Management Surfaces | Conversation/audit payloads use duplicate list envelopes and many casing duplicates | Apply the same list envelope and casing standard |
| Frontend Consumption Pattern | Current management client patterns tolerate multiple response shapes and filter visible rows client-side | Frontend must request server filters/page and adapt one canonical envelope only |

## Remediation Order

1. Fix `/bff/management/persona-fleet` first because it has proven live impact:
   slim list rows, remove duplicate row copies, add production/server filters,
   and move connector/source health to detail.
2. Fix board-pack next because it multiplies child payload mistakes.
3. Normalize Portfolio Book, Persona League, Quarterly Ranking, Performance
   Attribution, and Cost Attribution families.
4. Move raw `sourceRecord` and detail-grade helper data out of Human Inbox,
   Sentinel/Governance helpers, and list DTOs.
5. Remove camel/snake duplicates from migrated endpoints and delete retired
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
