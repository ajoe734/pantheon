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

Privacy: records with `owner_scope=user_private` or `visibility_scope=private` are returned (and counted in `summary.population`/`page_info`) only to the caller whose user id equals the record's `agora_user_id`; other callers never see them. The caller identity is resolved with the shared Agora user scope (`user_id`, `userId`, `sub`, `operator_id`, `operatorId`); if it cannot be resolved no private records are returned.

## Envelope / counts
`page_info`: `{next_page_token, total, canonical_total, filtered_total, catalog_default_total, page_size}`; `total`/`filtered_total` count the filtered authorized population, not the page.
`data.summary.population`: `{authorized_total, active_total, archived_total, by_purpose{}, by_data_scope{}}` over the whole tenant population (independent of filters).
`data.summary.filtered_archived_total`: `integer >= 0 | null`. Number of authorized retired records matching the current `purpose`, `data_scope`, `q`, `state`, `health`, `deployment_stage` and `market_scope` filters, independent of the `archived` view (in `archived=exclude` it still counts the retired records those other filters match) and of `page_token`/`page_size`. Computed from cheap cohort rows only; retired list/detail DTOs are never projected for it. Tenant and user-private authorization apply. If the retired cohort cannot be read, or the authoritative persona owner read is failed/unavailable (`meta.surfaces.personas.status=unavailable`, e.g. a failed `PersonaFleetPort`), the value is `null` (unavailable), never a fabricated `0`; a healthy empty owner yields `0`. Source availability is taken from the request's cached owner read (no extra owner call). Global `population.archived_total` is unchanged and unfiltered.
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

## Stage profile & owner-call budget (review round 4; fixtures only)
Reproduce: the `profile_fleet.py` script below (head) and the same script against `git archive c74e52e48` (base). Numbers and stage split are in `evidence.json` -> `stage_profile`.
At 1000 archived: detail projection 445.6 -> 1.4 ms (page-bounded), end-to-end `trading/exclude/20` 1264 -> 828 ms, mocked owner transport calls 31 -> 29 and identical at 100 vs 1000 archived.
Remaining cost: the in-process cohort/league step still scales with archive size (~800 ms at 1000); not claimed as eliminated.
Budget tests count real owner method calls via a proxy store (`test_fleet_owner_transport_calls_are_independent_of_archive_size`, `test_fleet_owner_failure_keeps_fixed_call_budget`); an owner failure raises rather than producing healthy-looking metrics.

### profile_fleet.py (save anywhere in repo root and run with `.venv-pantheon/bin/python`)
```python
"""Reproducible persona-fleet stage profile (fixtures only, no hosted calls).

Run from repo root:  .venv-pantheon/bin/python docs/deployment/evidence/PERSONA-FLEET-BFF-20261029/profile_fleet.py
Works on frozen base (c74e52e48) and head; omitted query params keep base-compatible behavior.
"""
import cProfile, importlib, json, os, pstats, sys, time
os.environ.update(PANTHEON_BFF_AUTH_STUB="true", PANTHEON_BFF_AUTH_MODE="permissive", RANKING_STORE_BOOTSTRAP="0")
sys.path.insert(0, "services/control-plane/bff")
t = importlib.import_module("test_pathreon_market_persona_fleet_contract")

STAGES = {
    "owner_reads": ("list_persona_league", "list_bindings", "list_runtime_bindings", "list_capital_pools",
                    "list_incidents", "list_evolution_decisions", "list_telemetry_summaries", "list_personas"),
    "cohort_projection": ("_project_persona_fleet_cohort_row", "_project_persona_fleet_item", "_persona_fleet_slim_list_payload"),
    "detail_projection": ("_project_persona_fleet_list_row",),
    "serialization": ("jsonable_encoder", "render", "dumps"),
}

def seed(n):
    if hasattr(t, "_seed_fleet_classification_store"):
        return t._seed_fleet_classification_store(n)
    raise SystemExit("seed helper missing on this revision")

def run(n, params):
    store = seed(n)
    calls = {}
    class P:
        def __getattr__(self, name):
            a = getattr(store, name)
            if not callable(a): return a
            def w(*x, **k):
                calls[name] = calls.get(name, 0) + 1
                return a(*x, **k)
            return w
    with t._client_with_store(P()) as c:
        c.get("/bff/management/persona-fleet", headers=t.HEADERS, params=params)  # warm
        calls.clear()
        pr = cProfile.Profile(); s0 = time.perf_counter(); pr.enable()
        r = c.get("/bff/management/persona-fleet", headers=t.HEADERS, params=params)
        pr.disable(); wall = (time.perf_counter() - s0) * 1000
    st = pstats.Stats(pr).stats
    stage = {k: 0.0 for k in STAGES}
    for (f, _l, fn), (_cc, _nc, _tt, ct, _c) in st.items():
        for k, names in STAGES.items():
            if fn in names: stage[k] += ct * 1000
    return {"archived": n, "params": params, "status": r.status_code, "wall_ms": round(wall, 1),
            "stage_cum_ms": {k: round(v, 1) for k, v in stage.items()},
            "owner_calls": dict(sorted(calls.items())), "owner_call_total": sum(calls.values())}

out = []
for n in (100, 1000):
    for params in ({}, {"purpose": "trading", "archived": "exclude", "page_size": 20}):
        try: out.append(run(n, params))
        except Exception as e: out.append({"archived": n, "params": params, "error": repr(e)[:200]})
print(json.dumps(out, indent=1))
```

## Health-monitor I/O and directory reads (PERSONA-FLEET-HEALTH-IO-20261030)

No response-shape change. Internally, the downstream health monitor's durable
SQLite calls on async paths (`_probe_all`, probe-result handling, error-rate
emission, Loop 12 state gather) now run via `asyncio.to_thread`, so a held WAL
writer no longer blocks request handlers such as `/bff/version`. A recorded
request outcome uses one connection/transaction, and every store connection is
closed on exit; WAL and `synchronous=FULL` are unchanged. The fleet read path
(`reuse_request_read=True`) shares one persona-directory list per request;
default owner APIs still return independent copies, with no cross-request or
cross-tenant cache.
