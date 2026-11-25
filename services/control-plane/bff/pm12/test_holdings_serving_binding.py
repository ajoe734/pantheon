"""Focused unit tests for PM12 holdings serving-binding selection.

Task: BFF-PM12-HOLDINGS-SERVING-BINDING-20261125
Covers:
1. Active + retired binding on the same runtime projects 1 holdings row bound
   to the active serving binding (across both owner-list orders, retirement
   timestamps, telemetry rollover _retired_binding_ids, and plan binding_ids).
2. Multi-persona distinct runtimes (each with active + retired bindings)
   project 1 holdings row per runtime without duplicating rows across retired
   bindings or double-counting pool telemetry rollups.
3. Genuinely terminal / retired-only runtimes carrying an unclosed residual
   position remain visible in holdings (never silently hidden), whereas
   terminal runtimes omitted from Telemetry produce no synthetic row or
   incident.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from services.control_plane.bff.pm12 import service as pm12_service
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore


SNAPSHOT_NOW = "2026-11-20T09:00:00Z"


def _make_read_store(
    *,
    runtime_bindings: List[Dict[str, Any]],
    telemetry_by_runtime_id: Dict[str, Dict[str, Any]],
    deployment_plans: Optional[List[Dict[str, Any]]] = None,
    bindings: Optional[List[Dict[str, Any]]] = None,
    capital_pools: Optional[List[Dict[str, Any]]] = None,
    use_get_only: bool = False,
) -> SimpleNamespace:
    get_calls: List[str] = []

    def _get_summary(runtime_id: str) -> Optional[Dict[str, Any]]:
        get_calls.append(runtime_id)
        return telemetry_by_runtime_id.get(runtime_id)

    attrs: Dict[str, Any] = {
        "list_runtime_bindings": lambda: list(runtime_bindings),
        "list_deployment_plans": lambda: list(deployment_plans or []),
        "list_bindings": lambda: list(bindings or []),
        "list_capital_pools": lambda **_: list(capital_pools or []),
        "get_telemetry_summary": _get_summary,
        "dataset_source": lambda _ds: "canonical",
        "get_calls": get_calls,
    }
    if not use_get_only:
        attrs["list_telemetry_summaries"] = lambda: list(telemetry_by_runtime_id.values())
    return SimpleNamespace(**attrs)


def _project_equity_position(
    store: RuntimeSummaryProjectionStore,
    *,
    runtime_id: str,
    binding_id: str,
    symbol: str = "2330.TWSE",
    instrument_id: str = "eq:2330",
    quantity: float = 10.0,
    fill_price: float = 2300.0,
    mark_price: float = 2320.0,
    fee: float = 32.0,
    pnl: float = 168.0,
) -> Dict[str, Any]:
    scope = {
        "tenant_id": "pantheon-dev",
        "environment": "paper",
        "deployment_stage": "paper",
        "runtime_id": runtime_id,
        "binding_id": binding_id,
    }
    store.project_event(
        {
            **scope,
            "event_id": f"evt-{runtime_id}-fill-1",
            "event_type": "paper_fill_simulated",
            "created_at": "2026-11-19T23:46:17Z",
            "metrics": {"fill_quantity": quantity, "fill_price": fill_price},
            "metadata": {
                "symbol": symbol,
                "instrument_id": instrument_id,
                "kind": "cash_share",
                "multiplier": 1.0,
                "currency": "TWD",
                "broker": "shioaji_paper",
                "fee": fee,
                "tax": 0.0,
                "ledger_cash": 100000.0 - quantity * fill_price - fee,
                "ledger_fill_count": 1,
            },
        }
    )
    return store.project_event(
        {
            **scope,
            "event_id": f"evt-{runtime_id}-pnl-1",
            "event_type": "pnl_snapshot",
            "created_at": "2026-11-20T06:00:00Z",
            "pnl_as_of": "2026-11-20T00:00:00Z",
            "metrics": {"pnl": pnl, "fill_count": 1},
            "metadata": {
                "mark_refs": [
                    {
                        "symbol": symbol,
                        "price": mark_price,
                        "as_of": "2026-11-20T00:00:00Z",
                        "source_ref": f"source-ingest://snapshots/mss-{symbol}",
                        "observed_at": "2026-11-20T06:00:00Z",
                        "lineage": {
                            "source_ids": [f"tw-official:tw_price_daily:TWSE:{symbol}:test"],
                            "connector_ids": ["tw-twse-tpex-official-market"],
                        },
                    }
                ]
            },
        }
    )


def test_active_and_retired_binding_on_same_runtime_projects_single_holding_row() -> None:
    """AC 1 & 3: Active + retired binding on the same runtime produces 1 row bound to the active serving binding."""
    runtime_id = "rt-544fc8c0a8f9"
    rb_retired_id = "rb-retired-001"
    rb_active_id = "rb-active-002"

    projection_store = RuntimeSummaryProjectionStore(path=None)
    # Telemetry summary still references the retired binding_id before the next fill;
    # serving-binding selection must still pick the active serving binding.
    summary = _project_equity_position(
        projection_store,
        runtime_id=runtime_id,
        binding_id=rb_retired_id,
    )

    retired_binding = {
        "id": rb_retired_id,
        "binding_id": rb_retired_id,
        "runtime_id": runtime_id,
        "capital_pool_id": "pool-retired",
        "plan_id": "plan-shared",
        "persona_capital_binding_id": "pcb-retired",
        "persona_id": "persona-retired",
        "strategy_id": "strat-retired",
        "paper_ledger_id": "ledger-retired",
        "status": "retired",
        "retired_at": "2026-11-18T07:00:00Z",
        "deployment_stage": "paper",
        "deployment_mode": "paper",
    }
    active_binding = {
        "id": rb_active_id,
        "binding_id": rb_active_id,
        "runtime_id": runtime_id,
        "capital_pool_id": "pool-serving",
        "plan_id": "plan-shared",
        "persona_capital_binding_id": "pcb-serving",
        "persona_id": "persona-serving",
        "strategy_id": "strat-serving",
        "paper_ledger_id": "ledger-serving",
        "status": "active",
        "effective_at": "2026-11-19T00:00:00Z",
        "deployment_stage": "paper",
        "deployment_mode": "paper",
    }

    for order in ([active_binding, retired_binding], [retired_binding, active_binding]):
        read_store = _make_read_store(
            runtime_bindings=order,
            telemetry_by_runtime_id={runtime_id: summary},
            use_get_only=True,
        )
        body = pm12_service._pm12_portfolio_book_holdings_response(
            read_store,
            utc_now_fn=lambda: SNAPSHOT_NOW,
        )
        items = body["data"]["items"]
        assert len(items) == 1
        # get_telemetry_summary is queried once per unique runtime_id, not once per binding.
        assert read_store.get_calls == [runtime_id]

        item = items[0]
        assert item["holding_id"] == f"{runtime_id}:2330.TWSE"
        assert item["runtime_id"] == runtime_id
        assert item["persona_id"] == "persona-serving"
        assert item["strategy_id"] == "strat-serving"
        assert item["capital_pool_id"] == "pool-serving"
        assert item["capital_scope"] == {
            "stage": "paper",
            "scope_kind": "paper_ledger",
            "scope_id": "ledger-serving",
        }
        assert item["quantity"] == 10.0
        assert item["average_price"] == 2300.0
        assert item["mark_price"] == 2320.0
        assert item["market_value"] == 23200.0
        assert item["unrealized_pnl"] == 200.0
        assert item["realized_pnl"] == -32.0
        assert item["total_pnl"] == 168.0
        assert item["currency"] == "TWD"
        assert item["broker_id"] == "shioaji_paper"

        summary_block = body["data"]["summary"]
        assert summary_block["holding_count"] == 1
        assert summary_block["source_row_count"] == 1
        assert summary_block["runtime_count"] == 1
        assert summary_block["telemetry_runtime_count"] == 1
        assert summary_block["incident_count"] == 0


def test_retired_timestamp_and_plan_binding_fallback_prefer_active_serving_binding() -> None:
    """AC 1: A historical binding with retired_at set (or telemetry _retired_binding_ids) loses to the active binding."""
    runtime_id = "rt-timestamp-retired"
    stale_status_retired = {
        "id": "rb-old-stamp",
        "binding_id": "rb-old-stamp",
        "runtime_id": runtime_id,
        "plan_id": "plan-1",
        "status": "active",
        "retired_at": "2026-11-18T07:00:00Z",
        "deployment_stage": "paper",
    }
    current_serving = {
        "id": "rb-new-serving",
        "binding_id": "rb-new-serving",
        "runtime_id": runtime_id,
        "plan_id": "plan-1",
        "status": "active",
        "effective_at": "2026-11-19T00:00:00Z",
        "deployment_stage": "paper",
    }
    plan = {
        "id": "plan-1",
        "plan_id": "plan-1",
        "capital_pool_id": "pool-1",
        "binding_ids": ["pcb-old-retired", "pcb-new-active"],
        "deployment_stage": "paper",
    }
    persona_bindings = [
        {
            "id": "pcb-old-retired",
            "binding_id": "pcb-old-retired",
            "persona_id": "persona-old",
            "strategy_id": "strat-old",
            "capital_pool_id": "pool-1",
            "paper_ledger_id": "ledger-old",
            "status": "retired",
        },
        {
            "id": "pcb-new-active",
            "binding_id": "pcb-new-active",
            "persona_id": "persona-new",
            "strategy_id": "strat-new",
            "capital_pool_id": "pool-1",
            "paper_ledger_id": "ledger-new",
            "status": "active",
        },
    ]
    telemetry = {
        "runtime_id": runtime_id,
        "binding_id": "rb-old-stamp",
        "_retired_binding_ids": ["rb-old-stamp"],
        "positions": [
            {
                "symbol": "2317.TWSE",
                "quantity": 5.0,
                "average_price": 210.0,
                "mark_price": 215.0,
                "mark_as_of": "2026-11-20T00:00:00Z",
                "currency": "TWD",
                "broker_id": "shioaji_paper",
            }
        ],
        "position_count": 1,
    }
    read_store = _make_read_store(
        runtime_bindings=[stale_status_retired, current_serving],
        telemetry_by_runtime_id={runtime_id: telemetry},
        deployment_plans=[plan],
        bindings=persona_bindings,
        capital_pools=[{"id": "pool-1", "pool_id": "pool-1", "status": "active"}],
    )
    body = pm12_service._pm12_portfolio_book_holdings_response(
        read_store,
        utc_now_fn=lambda: SNAPSHOT_NOW,
    )
    (item,) = body["data"]["items"]
    assert item["holding_id"] == f"{runtime_id}:2317.TWSE"
    assert item["persona_id"] == "persona-new"
    assert item["strategy_id"] == "strat-new"
    assert item["capital_scope"]["scope_id"] == "ledger-new"


def test_multi_persona_distinct_runtimes_project_one_row_per_serving_binding() -> None:
    """AC 1 & 3: Multi-persona distinct runtimes (each with active + retired bindings) produce 1 row per runtime."""
    rt_persona_a = "rt-persona-a-2330"
    rt_persona_b = "rt-persona-b-0050"
    future_id = "ssf:SRF:202611"

    projection_store = RuntimeSummaryProjectionStore(path=None)
    summary_a = _project_equity_position(
        projection_store,
        runtime_id=rt_persona_a,
        binding_id="rb-a-active",
        symbol="2330.TWSE",
        instrument_id="eq:2330",
        quantity=10.0,
        fill_price=2300.0,
        mark_price=2320.0,
    )

    scope_b = {
        "tenant_id": "pantheon-dev",
        "environment": "paper",
        "deployment_stage": "paper",
        "runtime_id": rt_persona_b,
        "binding_id": "rb-b-active",
    }
    projection_store.project_event(
        {
            **scope_b,
            "event_id": "evt-b-fill-1",
            "event_type": "paper_fill_simulated",
            "created_at": "2026-11-19T23:46:17Z",
            "metrics": {"fill_quantity": 2.0, "fill_price": 97.1},
            "metadata": {
                "symbol": "0050.TWSE",
                "instrument_id": future_id,
                "kind": "stock_future",
                "multiplier": 1000.0,
                "contract_month": "202611",
                "initial_margin_reserved": 17400.0,
                "currency": "TWD",
                "broker": "shioaji_paper",
                "fee": 16.0,
                "tax": 3.884,
                "ledger_cash": 99980.116,
                "ledger_fill_count": 1,
            },
        }
    )
    summary_b = projection_store.project_event(
        {
            **scope_b,
            "event_id": "evt-b-pnl-1",
            "event_type": "pnl_snapshot",
            "created_at": "2026-11-20T06:00:00Z",
            "pnl_as_of": "2026-11-20T00:00:00Z",
            "metrics": {"pnl": -19.884, "fill_count": 1},
            "metadata": {
                "mark_refs": [
                    {
                        "symbol": future_id,
                        "price": 97.1,
                        "as_of": "2026-11-20T00:00:00Z",
                        "source_ref": "source-ingest://snapshots/mss-0050-20261120",
                        "observed_at": "2026-11-20T06:00:00Z",
                        "lineage": {
                            "source_ids": ["tw-official:tw_price_daily:TWSE:0050:test"],
                            "connector_ids": ["tw-twse-tpex-official-market"],
                        },
                    }
                ]
            },
        }
    )

    bindings_list = [
        {
            "id": "rb-a-retired",
            "binding_id": "rb-a-retired",
            "runtime_id": rt_persona_a,
            "capital_pool_id": "pool-a",
            "persona_id": "persona-a-old",
            "strategy_id": "strat-a-old",
            "paper_ledger_id": "ledger-a-old",
            "status": "retired",
            "deployment_stage": "paper",
        },
        {
            "id": "rb-a-active",
            "binding_id": "rb-a-active",
            "runtime_id": rt_persona_a,
            "capital_pool_id": "pool-a",
            "persona_id": "persona-a",
            "strategy_id": "strat-a",
            "paper_ledger_id": "ledger-a",
            "status": "active",
            "deployment_stage": "paper",
        },
        {
            "id": "rb-b-active",
            "binding_id": "rb-b-active",
            "runtime_id": rt_persona_b,
            "capital_pool_id": "pool-b",
            "persona_id": "persona-b",
            "strategy_id": "strat-b",
            "paper_ledger_id": "ledger-b",
            "status": "active",
            "deployment_stage": "paper",
        },
        {
            "id": "rb-b-retired",
            "binding_id": "rb-b-retired",
            "runtime_id": rt_persona_b,
            "capital_pool_id": "pool-b",
            "persona_id": "persona-b-old",
            "strategy_id": "strat-b-old",
            "paper_ledger_id": "ledger-b-old",
            "status": "retired",
            "deployment_stage": "paper",
        },
    ]
    pools = [
        {"id": "pool-a", "pool_id": "pool-a", "name": "Pool A", "status": "active", "currency": "TWD"},
        {"id": "pool-b", "pool_id": "pool-b", "name": "Pool B", "status": "active", "currency": "TWD"},
    ]

    read_store = _make_read_store(
        runtime_bindings=bindings_list,
        telemetry_by_runtime_id={
            rt_persona_a: summary_a,
            rt_persona_b: summary_b,
        },
        capital_pools=pools,
    )
    body = pm12_service._pm12_portfolio_book_holdings_response(
        read_store,
        utc_now_fn=lambda: SNAPSHOT_NOW,
    )
    items = body["data"]["items"]
    assert len(items) == 2
    by_runtime = {item["runtime_id"]: item for item in items}
    assert set(by_runtime) == {rt_persona_a, rt_persona_b}

    item_a = by_runtime[rt_persona_a]
    assert item_a["holding_id"] == f"{rt_persona_a}:2330.TWSE"
    assert item_a["persona_id"] == "persona-a"
    assert item_a["strategy_id"] == "strat-a"
    assert item_a["capital_pool_id"] == "pool-a"
    assert item_a["capital_scope"]["scope_id"] == "ledger-a"

    item_b = by_runtime[rt_persona_b]
    assert item_b["holding_id"] == f"{rt_persona_b}:{future_id}"
    assert item_b["persona_id"] == "persona-b"
    assert item_b["strategy_id"] == "strat-b"
    assert item_b["capital_pool_id"] == "pool-b"
    assert item_b["capital_scope"]["scope_id"] == "ledger-b"
    assert item_b["instrument_kind"] == "stock_future"
    assert item_b["multiplier"] == 1000.0

    # Pool telemetry rollup must also deduplicate shared runtime_id per pool.
    pool_sources = pm12_service._management_portfolio_book_pool_sources(read_store)
    pool_entries = {entry["pool_id"]: entry for entry in pool_sources["entries"]}
    assert pool_entries["pool-a"]["telemetry"]["runtime_count"] == 1
    assert pool_entries["pool-a"]["telemetry"]["total_pnl"] == 168.0
    assert pool_entries["pool-b"]["telemetry"]["runtime_count"] == 1
    assert pool_entries["pool-b"]["telemetry"]["total_pnl"] == -19.884


def test_retired_only_runtime_residual_position_remains_visible_while_empty_terminal_omitted() -> None:
    """AC 2 & 3: Genuinely terminal/retired-only runtimes with an unclosed residual position remain visible."""
    rt_residual = "rt-retired-residual"
    rt_empty_terminal = "rt-retired-empty"

    residual_older = {
        "id": "rb-residual-older",
        "binding_id": "rb-residual-older",
        "runtime_id": rt_residual,
        "capital_pool_id": "pool-legacy-old",
        "persona_capital_binding_id": "pcb-legacy-old",
        "persona_id": "persona-legacy-old",
        "strategy_id": "strat-legacy-old",
        "paper_ledger_id": "ledger-legacy-old",
        "status": "retired",
        "retired_at": "2026-11-10T00:00:00Z",
        "deployment_stage": "paper",
    }
    residual_matching = {
        "id": "rb-residual-latest",
        "binding_id": "rb-residual-latest",
        "runtime_id": rt_residual,
        "capital_pool_id": "pool-legacy-latest",
        "persona_capital_binding_id": "pcb-legacy-latest",
        "persona_id": "persona-legacy-latest",
        "strategy_id": "strat-legacy-latest",
        "paper_ledger_id": "ledger-legacy-latest",
        "status": "retired",
        "retired_at": "2026-11-18T00:00:00Z",
        "deployment_stage": "paper",
    }
    empty_terminal = {
        "id": "rb-empty-terminal",
        "binding_id": "rb-empty-terminal",
        "runtime_id": rt_empty_terminal,
        "capital_pool_id": "pool-legacy-latest",
        "persona_capital_binding_id": "pcb-empty",
        "persona_id": "persona-empty",
        "strategy_id": "strat-empty",
        "status": "retired",
        "retired_at": "2026-11-18T00:00:00Z",
        "deployment_stage": "paper",
    }

    residual_telemetry = {
        "runtime_id": rt_residual,
        "binding_id": "rb-residual-latest",
        "positions": [
            {
                "symbol": "2317.TWSE",
                "quantity": 3.0,
                "average_price": 210.0,
                "currency": "TWD",
                "broker_id": "shioaji_paper",
            }
        ],
        "position_count": 1,
    }

    for order in (
        [residual_older, residual_matching, empty_terminal],
        [residual_matching, residual_older, empty_terminal],
    ):
        read_store = _make_read_store(
            runtime_bindings=order,
            telemetry_by_runtime_id={rt_residual: residual_telemetry},
        )
        body = pm12_service._pm12_portfolio_book_holdings_response(
            read_store,
            utc_now_fn=lambda: SNAPSHOT_NOW,
        )
        items = body["data"]["items"]
        assert len(items) == 1
        item = items[0]
        assert item["holding_id"] == f"{rt_residual}:2317.TWSE"
        assert item["runtime_id"] == rt_residual
        assert item["symbol"] == "2317.TWSE"
        assert item["quantity"] == 3.0
        assert item["persona_id"] == "persona-legacy-latest"
        assert item["strategy_id"] == "strat-legacy-latest"
        assert item["capital_pool_id"] == "pool-legacy-latest"
        assert item["capital_scope"]["scope_id"] == "ledger-legacy-latest"

        summary_block = body["data"]["summary"]
        assert summary_block["holding_count"] == 1
        assert summary_block["runtime_count"] == 1
        assert summary_block["telemetry_runtime_count"] == 1
        assert summary_block["incident_count"] == 0
