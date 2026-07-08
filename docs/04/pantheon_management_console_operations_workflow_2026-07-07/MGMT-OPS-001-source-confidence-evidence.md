# MGMT-OPS-001 — Source Confidence Evidence

Status: implementation evidence for the Wave 0 read-model contract

Owner: Codex2

Reviewer: Codex

Original implementation commit: `9e6850539cb36495feafa91fa0b173da7928bd63`
by Claude2. Codex2 took ownership after supervisor redispatch and rebased the
task branch on `origin/dev`.

## What was implemented

- `services/control-plane/bff/operations_read_model.py`: the shared identity
  (`persona_id`, `persona_label`, `stage`, `runtime_ids`, `paper_ledger_ids`,
  `capital_pool_ids`, `sleeve_ids`, `strategy_ids`, `artifact_ids`,
  `broker_ids`, `period`, `as_of`), source-status
  (`source_name`, `source_status`, `source_freshness`, `source_row_count`,
  `source_error`, `coverage_ratio`), and data-confidence
  (`formal`/`partial`/`fallback`/`degraded`/`unavailable`) contract, plus the
  pure helpers (`sanitize_metric`, `build_operations_identity`,
  `classify_confidence`) other MGMT-OPS-* tasks can reuse without pulling in
  `main.py`.
- `GET /bff/management/operations-read-model/{persona_id}` in
  `services/control-plane/bff/main.py`: composes the contract from existing
  performance-attribution, portfolio-holdings, capital-pool, and
  persona-fleet sources for one persona, and returns one `data_confidence`
  verdict with explicit `diagnostics` for every missing or unresolved join.
  The route publishes `OperationsReadModelEnvelope` as its OpenAPI
  `response_model`, so downstream frontend client/type generation can consume
  the shared contract instead of an untyped `{}` response schema.
- `services/control-plane/bff/test_bff_mgmt_ops_001_operations_read_model_contract.py`:
  unit coverage for the contract module plus BFF contract tests for all five
  confidence states (formal, partial, fallback, degraded, unavailable) and
  the unknown-persona 404 path. It also asserts that OpenAPI publishes the
  operations read-model envelope and nested component schemas.

## Focus persona finding: `persona-20260528-04688755`

Confirmed by `test_focus_persona_represents_missing_attribution_as_fallback_not_nan`:

- The persona-fleet composer (`_persona_fleet_slim_list_payload`) resolves
  this persona to `runtime_binding_id = "runtime-crypto-paper"` and a
  `performance_summary` of `{"pnl": 48000.0, "sharpe": 1.76,
  "max_drawdown": 0.064}` — these are exactly the numbers the 2026-07-07
  hosted-frontend audit captured for this persona in the source plan
  (`MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md`, "Performance
  Attribution").
- `_pm12_performance_attribution_sources()` (the source behind
  `/bff/management/performance-attribution`) calls
  `read_store.list_runtime_bindings()` **without**
  `include_market_persona_defaults=True`. The market-persona-default runtime
  that the fleet view resolves this persona to is therefore invisible to the
  attribution view, so `_pm12_performance_attribution_facts()` never
  produces a row for `persona_id == "persona-20260528-04688755"`.
- Root cause detail: the same runtime id (`runtime-crypto-paper`) that the
  fleet composer assigns to `persona-20260528-04688755` is, in the
  attribution/bindings view, actually owned by a *different* persona id
  (`persona-crypto`, one of the seeded default market personas). This is a
  genuine identity mismatch between the two composition paths, not just a
  missing-data gap — the two views can disagree about which persona a given
  runtime belongs to.
- The new read model represents this as `data_confidence: "fallback"` with
  diagnostics `MISSING_ATTRIBUTION_MATCH`, `MISSING_HOLDINGS_MATCH`, and
  `FORMAL_ATTRIBUTION_MISSING_USING_FLEET_FALLBACK`, and reports the
  persona-fleet summary numbers as the fallback `performance` block — never
  as `nan`, and never as a silently dropped persona.

## What is not yet fixed

This task locks and implements the shared *contract*; it does not change the
underlying attribution/runtime-binding resolution logic that causes the
mismatch above, and it does not yet wire Persona Fleet, Portfolio Book,
Persona League, or Human Review pages to consume this read model — those are
Wave 1 (`MGMT-OPS-002` through `MGMT-OPS-006`) per
`docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/INDEX.md`.
`sleeve_ids` and `artifact_ids` remain empty in every response today: no
current BFF source produces sleeve identity, and only the (unused here)
runtime-binding detail path produces artifact ids. Both fields are part of
the contract so a later task can populate them without a breaking schema
change.

## Verification

```sh
python3 -m pytest services/control-plane/bff/test_bff_mgmt_ops_001_operations_read_model_contract.py -q
```

Result before Codex2 redispatch: 10 passed (confirmed deterministic across
repeated runs; the initial
"formal" case was rewritten to use isolated monkeypatched sources after
finding that `ReadSurfaceStore.list_capital_pools()` without
`include_market_persona_defaults=True` depends on ambient canonical-service
availability and is not deterministic across process/environment — the
degraded/partial/unavailable cases already used this isolated-source
convention, matching `test_bff_pm12_portfolio_book_contract.py`).

Also re-ran unaffected existing suites to confirm no regression:

```sh
python3 -m pytest \
  services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py \
  services/control-plane/bff/test_persona_league_detail_not_found.py \
  services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py \
  services/control-plane/bff/test_no_undefined_call_symbols.py \
  services/control-plane/bff/test_route_resolution_no_shadowing.py \
  services/control-plane/bff/test_management_list_contract_guardrail.py \
  services/control-plane/bff/test_security_headers.py \
  -q
```

Result: 100 passed. `test_management_list_response_guardrail.py` fails both
before and after this change (pre-existing `duplicate_casing` issues on
`/bff/management/persona-fleet`, confirmed via `git stash`) — unrelated to
this task and not touched by it.

Codex2 rebase/owner validation after merging latest `origin/dev`:

```sh
python3 -m pytest \
  services/control-plane/bff/test_bff_mgmt_ops_001_operations_read_model_contract.py \
  services/control-plane/bff/test_no_undefined_call_symbols.py \
  services/control-plane/bff/test_route_resolution_no_shadowing.py \
  -q
```

Result: 16 passed. This includes the typed OpenAPI envelope regression for
`GET /bff/management/operations-read-model/{persona_id}`.
