# Persona Fleet API contract (PERSONA-FLEET-BFF-20261029)

Endpoint: `GET /bff/management/persona-fleet` (existing; read-only; tenant-scoped).

## New query parameters (all optional, case-insensitive)

| name | values | default when omitted |
|---|---|---|
| `purpose` | `trading`, `advisory_assistant`, `unknown`, `all` | `all` |
| `data_scope` | `regular`, `test`, `unknown`, `all` | `all` |
| `archived` | `exclude`, `only`, `include` | `include` (historical behavior) |

Existing `state`, `health`, `deployment_stage`, `market_scope`, `q`, `page_token`, `page_size` (1-200) are unchanged.
Any other value returns `422 VALIDATION_FAILED` (`precondition_failed: fleet_filter`).
"Archived" means projected lifecycle state `retired`. Recommended frontend call:
`purpose=trading&data_scope=regular|test|unknown&archived=exclude&page_size=20`, and `archived=only` for the archive view.

## Row DTO additions (`data.items[]`)
`persona_purpose` (`trading|advisory_assistant|unknown`), `data_scope` (`regular|test|unknown`),
`classification_source` (`{purpose, data_scope}` provenance tags), `classification_confirmed` (bool; false = needs confirmation), `archived` (bool).

Classification uses canonical metadata only: `persona_class` (`agora_servant` => advisory), agora user scope with
`execution_authority=none`, `data_scope`/`is_test`/`acceptance_run` flags => test, provisioning-coordinator provenance => regular.
Paper capital mode never implies test. Names/ID patterns are never used. Legacy records with no provenance are `unknown` and remain reachable.

Advisory rows: `state="advisory"`, `status`/`health="not_applicable"`, and `league_rank`, `rank`, `score`, `paper_ledger*`,
`legacy_paper_capital_pool_id`, `capital_mode`, `deployment_stage`, `runtime_health`, `stage`, `capital_scope*`, `binding_state`, `capital_binding`, `runtime_binding`, `capital_pool*` are `null` (never fabricated, not an incident).

Privacy: records with `owner_scope=user_private` or `visibility_scope=private` are returned (and counted in `summary.population`/`page_info`) only to the caller whose user id equals the record's `agora_user_id`; other callers never see them.

## Envelope / counts
`page_info`: `{next_page_token, total, canonical_total, filtered_total, catalog_default_total, page_size}`; `total`/`filtered_total` count the filtered authorized population, not the page.
`data.summary.population`: `{authorized_total, active_total, archived_total, by_purpose{}, by_data_scope{}}` over the whole tenant population (independent of filters).
`data.summary.filters` echoes the applied `{purpose, data_scope, archived}`.
Ordering is unchanged (score desc, persona_id asc) and page traversal is deterministic.

Example: `GET ...?purpose=trading&data_scope=regular&archived=exclude&page_size=20`
-> `page_info.total=2`, items `p-draft` (draft, no runtime, valid) and `p-paper`; `population.archived_total=105`.

## Rank policy
Quarterly/rolling ranking consumers drop explicit advisory personas before ranking (`_pm12_quarterly_ranking_items`, rolling league).
Trading ranks are computed over the whole trading cohort, never renumbered per page. Scoring/admission/promotion policy is unchanged.
Affected consumers: persona-fleet, quarterly ranking, drilldown, recommendations, promotion reviews, rolling league.

## Local evidence (fixtures only, no hosted data; single warm TestClient call, in-memory store)
| archived fixtures | `archived=exclude` (5 rows) | omitted (`include`) |
|---|---|---|
| 100 | 149 ms | 52 ms |
| 1000 | 328 ms | 416 ms |

Honest limit: archived rows still pass through the cheap retired projection in the whole-cohort league/ranking step, so
cost is reduced but not independent of archive size locally; per-row enrichment (incidents, telemetry, runtimes) is skipped for retired
and out-of-scope rows, and owner reads are batch/request-cached (no per-persona remote calls). Hosted timings belong to the acceptance task.
Tests: `test_pathreon_market_persona_fleet_contract.py` (filters, >100 archived traversal, servant exclusion, 422). 13 pre-existing
failures in `test_persona_router.py`/`test_persona_archived_lifecycle.py` are identical on the unmodified baseline.
