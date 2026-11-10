from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from services.execution.lean_runtime.futures_session_inputs import (
    DatasetFinalSettlementSource,
    attach_futures_session_inputs,
    final_settlement_source_from_env,
    open_stock_future_positions,
    read_session_settlement_prices_for_open_futures,
)
from services.execution.lean_runtime.instrument_candidates import (
    lookup_final_settlement_price,
)
from services.execution.lean_runtime.paper_runtime import (
    PaperExecutionAlgorithm,
    PaperRuntimeService,
    RuntimeIdentity,
)
from services.execution.lean_runtime.paper_signal_producer import (
    PaperSignalProducer,
    _runner_strategy,
)
from services.execution.lean_runtime.pending_signal_store import (
    InMemoryPendingSignalStore,
)
from services.execution.lean_runtime.runtime_bootstrap import (
    build_paper_runtime_service,
)
from services.source_ingestion.connectors.taiwan_official import (
    taifex_ssf_normalized_rows,
)
from services.source_ingestion.market_data_storage import MarketDataStorageWriter

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "taifex_20261106"


def _write_ssf_partition(
    root: Path,
    trade_date: str,
    rows: list[dict[str, Any]],
    *,
    run_id: str = "run-1",
) -> Path:
    writer = MarketDataStorageWriter(root)
    partition_dir = writer.root / "normalized" / "taifex_ssf_contracts" / f"date={trade_date}"
    partition_dir.mkdir(parents=True, exist_ok=True)
    path = partition_dir / f"{run_id}.jsonl"
    lines = [
        json.dumps(
            {
                "source_id": "taifex_ssf",
                "connector_id": "taifex_derivatives_chip",
                "dataset": "taifex_ssf_contracts",
                "metadata": {"normalized_row": dict(row)},
            }
        )
        for row in rows
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _captured_20261106_rows() -> list[dict[str, Any]]:
    payload = {
        "SingleStockFuturesMargining": json.loads(
            (FIXTURES / "SingleStockFuturesMargining.json").read_text(encoding="utf-8")
        ),
        "SingleStockFuturesETFMargining": json.loads(
            (FIXTURES / "SingleStockFuturesETFMargining.json").read_text(encoding="utf-8")
        ),
        "DailyMarketReportFut": json.loads(
            (FIXTURES / "DailyMarketReportFut.json").read_text(encoding="utf-8")
        ),
        "FinalSettlementPriceSSF": json.loads(
            (FIXTURES / "FinalSettlementPriceSSF.json").read_text(encoding="utf-8")
        ),
        "stockLists": (FIXTURES / "stockLists.html").read_text(encoding="utf-8"),
    }
    return [dict(row) for row in taifex_ssf_normalized_rows(payload)]


def _srf_row(
    price_date: str,
    settlement_price: float | None,
    *,
    contract_month: str = "202611",
    last_final_settlement: dict[str, Any] | None = None,
    tombstone: bool = False,
) -> dict[str, Any]:
    compact_day = price_date.replace("-", "")
    row: dict[str, Any] = {
        "contract": "SRF",
        "contract_month": contract_month,
        "underlying_code": "0050",
        "underlying_kind": "etf",
        "unit": 1000,
        "multiplier": 1000,
        "settlement_price": settlement_price,
        "price_date": price_date,
        "margin_date": price_date,
        "margin_tier": "fixed",
        "initial_margin": 8700.0,
        "maintenance_margin": 6700.0,
        "clearing_margin": 6400.0,
        "volume": 900,
        "open_interest": 3500,
        "available_time": f"{price_date}T08:00:00Z",
        "ingest_time": f"{price_date}T08:00:05Z",
        "raw_market_row": {"Date": compact_day, "Contract": "SRF"},
        "raw_margin_row": {"Date": compact_day, "Contract": "SRF"},
    }
    if last_final_settlement is not None:
        row["last_final_settlement"] = dict(last_final_settlement)
    if tombstone:
        row["tombstone"] = True
    return row


def _load_reconciliation_drift_main() -> Any:
    import importlib.util
    import sys

    service_dir = Path(__file__).resolve().parents[2] / "reconciliation-drift"
    with patch.dict(
        os.environ,
        {
            "RECONCILIATION_DRIFT_DATA_DIR": tempfile.mkdtemp(),
            "PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083",
            "PANTHEON_LINEAGE_READ_URL": "http://lineage-read:8094",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
        },
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(service_dir))
        try:
            spec = importlib.util.spec_from_file_location(
                "reconciliation_drift_futures_mark_test_main", service_dir / "main.py"
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            sys.path.remove(str(service_dir))


class _StubRuntimeManagerClient:
    def __init__(self, binding: dict[str, Any]) -> None:
        self._binding = dict(binding)

    def list_all(self) -> list[dict[str, Any]]:
        return [dict(self._binding)]

    def get_binding(self, binding_id: str) -> dict[str, Any]:
        return dict(self._binding)

    def find_active_binding_for_runtime(self, runtime_id: str) -> dict[str, Any]:
        return dict(self._binding)


class _StubTelemetryEmitter:
    enabled = True
    last_delivery_lag_ms = 0.0

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.payloads: list[dict[str, Any]] = []

    def build_event(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        *,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        from services.trade_journey.correlation_envelope import propagate_envelope

        event_metrics = dict(metrics)
        stamp_key = "pnl_as_of" if event_type == "pnl_snapshot" else "drawdown_as_of"
        stamp = event_metrics.pop(stamp_key, None)
        payload: dict[str, Any] = {
            "event_id": event_id,
            "event_type": event_type,
            "created_at": created_at,
            "binding_id": (metadata or {}).get("runtime_binding_id"),
            stamp_key: stamp,
            "metrics": event_metrics,
            "metadata": dict(metadata or {}),
        }
        event_metadata = payload["metadata"]
        incoming_envelope = event_metadata.get("correlation_envelope")
        sequence_no = event_metadata.get("sequence_no")
        causal_parent_id = event_metadata.get("causal_parent_id")
        if (
            isinstance(incoming_envelope, dict)
            and isinstance(sequence_no, int)
            and event_id
            and created_at
            and causal_parent_id
        ):
            outgoing_envelope = propagate_envelope(
                incoming_envelope,
                producer="execution.paper_runtime",
                event_id=event_id,
                event_time=created_at,
            )
            payload.update(
                {
                    "aggregate_type": "trade_journey",
                    "aggregate_id": outgoing_envelope["journey_id"],
                    "sequence_no": sequence_no,
                    "causal_parent_id": causal_parent_id,
                    "correlation_envelope": outgoing_envelope,
                }
            )
        return payload

    def emit(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        self.events.append((event_type, dict(metrics), dict(metadata or {})))
        return True

    def emit_payload(self, payload: dict[str, Any]) -> bool:
        self.payloads.append(dict(payload))
        return True

    def emit_heartbeat(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def emit_deploy_started(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def emit_deploy_completed(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": True, "emitted_count": len(self.events) + len(self.payloads)}


class FuturesSessionInputsUnitTests(unittest.TestCase):
    def test_open_stock_future_positions_extracts_active_futures_only(self) -> None:
        book = {
            "positions": [
                {
                    "underlying": "2330",
                    "instrument_id": "eq:2330",
                    "kind": "cash_share",
                    "direction": "LONG",
                    "units": 100,
                },
                {
                    "underlying": "0050.TW",
                    "instrument_id": "ssf:SRF:202611",
                    "kind": "stock_future",
                    "direction": "LONG",
                    "units": 2,
                },
                {
                    "underlying": "2317",
                    "instrument_id": "ssf:DHF:202611",
                    "kind": "stock_future",
                    "quantity": 0,
                },
                {
                    "underlying": "2330",
                    "instrument_id": "ssf:QFF:202611",
                    "kind": "stock_future",
                    "quantity": -1,
                    "status": "closed",
                },
            ]
        }
        positions = open_stock_future_positions(book)
        self.assertEqual(
            positions,
            [
                {
                    "instrument_id": "ssf:SRF:202611",
                    "kind": "stock_future",
                    "contract": "SRF",
                    "contract_month": "202611",
                    "underlying": "0050",
                    "units": 2,
                }
            ],
        )

    def test_read_session_settlement_prices_distinguishes_fresh_stale_missing_and_unset(self) -> None:
        book = {
            "positions": [
                {
                    "underlying": "0050",
                    "instrument_id": "ssf:SRF:202611",
                    "kind": "stock_future",
                    "units": 1,
                }
            ]
        }
        with patch.dict(os.environ, {}, clear=True):
            unset_info = read_session_settlement_prices_for_open_futures(
                book["positions"], "2026-11-06", normalized_root=None
            )
        self.assertEqual(unset_info["session_settlement_prices"], {})
        self.assertEqual(
            unset_info["session_settlement_price_reasons"],
            {"ssf:SRF:202611": "dataset_root_unset"},
        )
        self.assertEqual(unset_info["futures_mark_unavailable_reason"], "dataset_root_unset")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            unreadable_info = read_session_settlement_prices_for_open_futures(
                book["positions"], "2026-11-06", normalized_root=root
            )
            self.assertEqual(
                unreadable_info["session_settlement_price_reasons"],
                {"ssf:SRF:202611": "dataset_unreadable"},
            )

            _write_ssf_partition(root, "2026-11-06", _captured_20261106_rows())
            fresh_info = read_session_settlement_prices_for_open_futures(
                book["positions"], "2026-11-06", normalized_root=root
            )
            self.assertEqual(fresh_info["session_settlement_prices"], {"ssf:SRF:202611": 92.35})
            self.assertEqual(fresh_info["session_settlement_price_reasons"], {})
            self.assertIsNone(fresh_info["futures_mark_unavailable_reason"])

            # Asking for 2026-11-09 when only 2026-11-06 exists must report stale price, never reuse 92.35
            stale_info = read_session_settlement_prices_for_open_futures(
                book["positions"], "2026-11-09", normalized_root=root
            )
            self.assertEqual(stale_info["session_settlement_prices"], {})
            self.assertEqual(
                stale_info["session_settlement_price_reasons"],
                {"ssf:SRF:202611": "settlement_price_stale"},
            )
            self.assertEqual(
                stale_info["futures_mark_unavailable_reason"],
                "ssf:SRF:202611:settlement_price_stale",
            )


class ProducerRuntimeFuturesWiringIntegrationTests(unittest.TestCase):
    POOL = {
        "pool_id": "pool-rb-expr",
        "tenant_id": "default",
        "name": "persona paper pool",
        "owner_id": "default",
        "owner_type": "org",
        "status": "active",
        "currency": "TWD",
        "budget": 100000.0,
        "single_runtime_enforced": True,
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "risk_policy": {
                "risk_policy_id": "rp-pool-rb-expr",
                "version": "1.0",
                "status": "active",
                "allowed_stages": ["paper"],
                "gross_limit": 2.0,
                "net_limit": 2.0,
                "max_leverage": 2.0,
                "allowed_asset_classes": ["equity", "future"],
            },
        },
    }

    def _artifact(self, *, symbol: str = "0050.TWSE", bearish: bool = False):
        import copy
        from services.execution.lean_runtime.test_current_artifact_signal import _artifact
        from services.registry.test_strategy_artifact import _artifact_with_expression_policy

        art = copy.deepcopy(_artifact_with_expression_policy())
        base = _artifact(bearish=bearish)
        for key in ("artifact_id", "strategy_id"):
            art[key] = base[key]
        art["strategy_logic"] = base["strategy_logic"]
        art["parameters"]["symbols"] = [symbol]
        return art

    def _binding(
        self,
        art: dict[str, Any],
        *,
        symbol: str = "0050.TWSE",
        closes: tuple[float, float] = (88.0, 92.5),
        event_time: str = "2026-11-06T00:00:00+08:00",
        observed_at: str = "2026-11-09T09:00:00Z",
    ) -> dict[str, Any]:
        from services.execution.lean_runtime.test_current_artifact_signal import _binding

        binding = _binding(art, binding_id="rb-expr", include_market_input=False)
        binding["runtime_id"] = "runtime-rb-expr"
        binding["capital_pool_id"] = "pool-rb-expr"
        binding["persona_capital_binding_id"] = "pcb-rb-expr"
        binding["status"] = "active"
        binding["market_input"] = {
            "symbol": symbol,
            "closes": list(closes),
            "source_ref": f"source-ingest://normalized/tw-price/{symbol}",
            "observed_at": observed_at,
            "event_time": event_time,
        }
        return binding

    def _runtime_service(
        self,
        store: InMemoryPendingSignalStore,
        binding: dict[str, Any],
        tmpdir: str,
        *,
        normalized_root: Path | None = None,
    ) -> tuple[PaperRuntimeService, _StubTelemetryEmitter]:
        emitter = _StubTelemetryEmitter()
        identity = RuntimeIdentity.from_env(
            {
                "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
                "PANTHEON_RUNTIME_MODE": "paper",
                "PANTHEON_RUNTIME_ID": "runtime-rb-expr",
                "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
                "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
                "PANTHEON_WORKSPACE_REF": "workspace-paper",
                "PANTHEON_AUTH_PROFILE_REF": "auth-profile-paper",
                "PANTHEON_PERSONA_ID": "persona-paper-ops",
                "PANTHEON_SESSION_ID": "session-paper-runtime",
                "PANTHEON_TRACE_ID": "trace-paper-runtime",
                "PANTHEON_REQUEST_ID": "request-paper-runtime",
            }
        )
        env = {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_PERFORMANCE_STATE_PATH": str(Path(tmpdir) / "ledger.json"),
            "PANTHEON_LIFECYCLE_OUTBOX_PATH": str(Path(tmpdir) / "outbox.json"),
        }
        if normalized_root is not None:
            env["PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT"] = str(normalized_root)
        with patch.dict(os.environ, env, clear=False):
            service = build_paper_runtime_service(
                [],
                store=store,
                identity=identity,
                runtime_manager_client=_StubRuntimeManagerClient(binding),
                telemetry_emitter=emitter,
                capital_pool_reader=lambda _pid: self.POOL,
            )
            service._algo._in_process_broker = True
            service._consumer._rebalance_timeout = 1
        return service, emitter

    def _drain_at(self, service: PaperRuntimeService, now_iso: str) -> dict[str, Any]:
        from datetime import datetime

        service._algo.Time = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        return service.drain_once()

    def test_producer_to_runtime_opens_srf_marks_session_and_records_missing_price_reason(
        self,
    ) -> None:
        from services.execution.lean_runtime import expression_inputs

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "market_data_store"
            _write_ssf_partition(root, "2026-11-06", _captured_20261106_rows())

            summary_state: dict[str, Any] = {
                "runtime_id": "runtime-rb-expr",
                "binding_id": "rb-expr",
                "ledger_fill_count": 0,
                "executed_trade_count": 0,
                "positions": None,
            }

            def fetch(url: str, headers: dict[str, str], timeout: float) -> Any:
                if "/api/capital-pools/" in url:
                    return self.POOL
                if "/api/telemetry/runtime-summaries/" in url:
                    return summary_state
                raise AssertionError(url)

            env = {
                "PANTHEON_PAPER_EXPRESSION_LAYER": "on",
                "PANTHEON_CAPITAL_API_URL": "http://capital:8092",
                "DEPLOYMENT_CAPITAL_SERVICE_TOKEN": "capital-reader",
                "PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083",
                "PANTHEON_TELEMETRY_SERVICE_TOKEN": "telemetry-reader",
                "PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT": str(root),
                "PANTHEON_TENANT_ID": "default",
            }
            store = InMemoryPendingSignalStore()
            art = self._artifact(symbol="0050.TWSE")
            binding = self._binding(
                art,
                symbol="0050.TWSE",
                closes=(88.0, 92.5),
                event_time="2026-11-06T00:00:00+08:00",
                observed_at="2026-11-09T09:00:00Z",
            )
            service, _emitter = self._runtime_service(
                store, binding, tmpdir, normalized_root=root
            )

            # 1. Decision on 2026-11-09 opens 2 contracts of ssf:SRF:202611 at 92.35 (from 2026-11-06 partition)
            with patch.dict(os.environ, env, clear=True), patch.object(
                expression_inputs, "_http_get_json", fetch
            ):
                producer = PaperSignalProducer(
                    store_for=lambda _: store, strategy=_runner_strategy()
                )
                self.assertEqual(producer.produce(binding, "2026-11-09T09:00:00Z"), 1)

            [open_sig] = list(store._pending)
            self.assertEqual(open_sig["metadata"]["instrument_id"], "ssf:SRF:202611")
            self.assertEqual(open_sig["metadata"]["contract"], "SRF")
            self.assertEqual(open_sig["metadata"]["contract_month"], "202611")
            self.assertEqual(
                open_sig["metadata"]["price_basis"],
                "futures_daily_settlement_prev_session",
            )
            self.assertEqual(open_sig["metadata"]["price_date"], "2026-11-06")
            self.assertEqual(open_sig["metadata"]["futures_settlement_price"], 92.35)
            self.assertEqual(open_sig["metadata"]["initial_margin_per_unit"], 8700.0)
            self.assertEqual(open_sig["metadata"]["maintenance_margin_per_unit"], 6700.0)
            self.assertEqual(open_sig["metadata"]["trade_date"], "2026-11-09")

            snap_after_open = self._drain_at(service, "2026-11-09T09:00:00Z")
            self.assertEqual(snap_after_open["status"], "ok")
            pos_open = service._algo.get_instrument_position("ssf:SRF:202611")
            self.assertIsNotNone(pos_open)
            self.assertEqual(pos_open["quantity"], 2.0)
            self.assertAlmostEqual(pos_open["average_entry_price"], 92.35, places=4)
            self.assertAlmostEqual(pos_open["initial_margin_reserved"], 17400.0, places=4)
            self.assertAlmostEqual(
                pos_open["maintenance_margin_required"], 13400.0, places=4
            )

            # Publish the open ssf:SRF:202611 position into the telemetry summary
            summary_state = {
                "runtime_id": "runtime-rb-expr",
                "binding_id": "rb-expr",
                "ledger_fill_count": 1,
                "executed_trade_count": 1,
                "ledger_cash": float(service._algo._cash),
                "positions": [
                    {
                        "symbol": "0050.TWSE",
                        "instrument_id": "ssf:SRF:202611",
                        "kind": "stock_future",
                        "quantity": 2.0,
                        "multiplier": 1000.0,
                        "average_price": 92.35,
                        "mark_price": 92.35,
                        "unrealized_pnl": 0.0,
                        "margin_reserved": 17400.0,
                    }
                ],
            }

            # 2. On 2026-11-10, write session partition with daily settlement price 94.00 and emit a HOLD signal
            _write_ssf_partition(root, "2026-11-10", [_srf_row("2026-11-09", 94.0)])
            binding_1110 = self._binding(
                art,
                symbol="0050.TWSE",
                closes=(94.0, 94.0),  # flat view -> HOLD (flat_view / close_leg or hold)
                event_time="2026-11-10T00:00:00+08:00",
                observed_at="2026-11-10T13:50:00Z",
            )
            # Keep strong long view so policy returns already_at_target HOLD
            binding_1110["market_input"]["closes"] = [88.0, 94.0]
            with patch.dict(os.environ, env, clear=True), patch.object(
                expression_inputs, "_http_get_json", fetch
            ):
                producer = PaperSignalProducer(
                    store_for=lambda _: store, strategy=_runner_strategy()
                )
                self.assertEqual(producer.produce(binding_1110, "2026-11-10T13:50:00Z"), 1)

            [hold_sig] = list(store._pending)
            self.assertEqual(hold_sig["action"], "HOLD")
            self.assertEqual(
                hold_sig["metadata"]["expression_decision"]["reason_code"],
                "already_at_target",
            )
            self.assertEqual(hold_sig["metadata"]["trade_date"], "2026-11-09")
            self.assertEqual(hold_sig["metadata"]["as_of_trading_day"], "2026-11-10")
            self.assertEqual(
                hold_sig["metadata"]["session_settlement_prices"],
                {"ssf:SRF:202611": 94.0},
            )
            self.assertEqual(
                hold_sig["metadata"]["session_settlement_price_reasons"], {}
            )

            self._drain_at(service, "2026-11-10T13:50:00Z")
            pos_1110 = service._algo.get_instrument_position("ssf:SRF:202611")
            self.assertAlmostEqual(pos_1110["mark_price"], 94.0, places=4)
            self.assertEqual(pos_1110["mark_as_of"], "2026-11-09")
            # Unrealized PnL = 2 * 1000 * (94.0 - 92.35) = +3300.0
            self.assertAlmostEqual(pos_1110["unrealized_pnl"], 3300.0, places=4)
            self.assertEqual(
                service._algo.futures_mark_diagnostic()["status"], "marked"
            )
            self.assertEqual(
                service._algo.futures_mark_diagnostic()["trade_date"], "2026-11-09"
            )

            # 3. On 2026-11-11, no 2026-11-11 partition exists: producer records stale/missing reason,
            # runtime does NOT mark with a stale or invented price and records diagnosable reason.
            binding_1111 = self._binding(
                art,
                symbol="0050.TWSE",
                closes=(88.0, 94.0),
                event_time="2026-11-11T00:00:00+08:00",
                observed_at="2026-11-11T13:50:00Z",
            )
            with patch.dict(os.environ, env, clear=True), patch.object(
                expression_inputs, "_http_get_json", fetch
            ):
                producer = PaperSignalProducer(
                    store_for=lambda _: store, strategy=_runner_strategy()
                )
                self.assertEqual(producer.produce(binding_1111, "2026-11-11T13:50:00Z"), 1)

            [missing_sig] = list(store._pending)
            self.assertEqual(missing_sig["action"], "HOLD")
            self.assertEqual(missing_sig["metadata"]["trade_date"], "2026-11-10")
            self.assertEqual(missing_sig["metadata"]["session_settlement_prices"], {})
            self.assertEqual(
                missing_sig["metadata"]["session_settlement_price_reasons"],
                {"ssf:SRF:202611": "settlement_price_stale"},
            )
            self.assertIn(
                "settlement_price_stale",
                missing_sig["metadata"]["futures_mark_unavailable_reason"],
            )

            snap_1111 = self._drain_at(service, "2026-11-11T13:50:00Z")
            pos_1111 = service._algo.get_instrument_position("ssf:SRF:202611")
            self.assertEqual(pos_1111["mark_as_of"], "2026-11-09")
            self.assertEqual(
                 service._algo._last_futures_mark_session_date, "2026-11-09"
            )
            diag = snap_1111["paper_state"]["futures_mark_diagnostic"]
            self.assertEqual(diag["status"], "skipped_missing_settlement_price")
            self.assertEqual(diag["trade_date"], "2026-11-10")
            self.assertIn("settlement_price_stale", diag["reason"])
            self.assertEqual(
                diag["missing_contracts"],
                {"ssf:SRF:202611": "settlement_price_stale"},
            )

    def _write_opening_partition(self, root: Path) -> None:
        rows = _captured_20261106_rows()
        for row in rows:
            if row.get("contract") == "SRF" and row.get("contract_month") == "202611":
                row["settlement_price"] = 97.1
        _write_ssf_partition(root, "2026-11-06", rows)

    def _open_srf_via_production_path(self, root: Path, tmpdir: str):
        """Open ssf:SRF:202611 x2 at 97.1 through producer -> runtime_bootstrap."""
        from services.execution.lean_runtime import expression_inputs

        summary_state: dict[str, Any] = {
            "runtime_id": "runtime-rb-expr",
            "binding_id": "rb-expr",
            "ledger_fill_count": 0,
            "executed_trade_count": 0,
            "positions": None,
        }

        def fetch(url: str, headers: dict[str, str], timeout: float) -> Any:
            if "/api/capital-pools/" in url:
                return self.POOL
            if "/api/telemetry/runtime-summaries/" in url:
                return summary_state
            raise AssertionError(url)

        env = {
            "PANTHEON_PAPER_EXPRESSION_LAYER": "on",
            "PANTHEON_CAPITAL_API_URL": "http://capital:8092",
            "DEPLOYMENT_CAPITAL_SERVICE_TOKEN": "capital-reader",
            "PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083",
            "PANTHEON_TELEMETRY_SERVICE_TOKEN": "telemetry-reader",
            "PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT": str(root),
            "PANTHEON_TENANT_ID": "default",
        }
        store = InMemoryPendingSignalStore()
        art = self._artifact(symbol="0050.TWSE")
        binding = self._binding(
            art,
            symbol="0050.TWSE",
            closes=(88.0, 92.5),
            event_time="2026-11-09T00:00:00+08:00",
            observed_at="2026-11-09T09:00:00Z",
        )
        service, emitter = self._runtime_service(
            store, binding, tmpdir, normalized_root=root
        )
        with patch.dict(os.environ, env, clear=True), patch.object(
            expression_inputs, "_http_get_json", fetch
        ):
            producer = PaperSignalProducer(
                store_for=lambda _: store, strategy=_runner_strategy()
            )
            self.assertEqual(producer.produce(binding, "2026-11-09T09:00:00Z"), 1)
        self._drain_at(service, "2026-11-09T09:00:00Z")
        pos = service._algo.get_instrument_position("ssf:SRF:202611")
        self.assertEqual(pos["quantity"], 2.0)
        self.assertAlmostEqual(pos["average_entry_price"], 97.1, places=4)
        self._prod = {
            "store": store,
            "art": art,
            "env": env,
            "fetch": fetch,
            "summary_state": summary_state,
        }
        return service, emitter

    def _produce_hold_at(self, service: PaperRuntimeService, now_iso: str) -> dict[str, Any]:
        """Run the producer for a bar at ``now_iso`` and return the queued signal."""
        from services.execution.lean_runtime import expression_inputs

        prod = self._prod
        store = prod["store"]
        store._pending.clear()
        prod["summary_state"].update(
            {
                "ledger_fill_count": 1,
                "executed_trade_count": 1,
                "ledger_cash": float(service._algo._cash),
                "positions": [
                    {
                        "symbol": "0050.TWSE",
                        "instrument_id": "ssf:SRF:202611",
                        "kind": "stock_future",
                        "quantity": 2.0,
                        "multiplier": 1000.0,
                        "average_price": 97.1,
                        "mark_price": 97.1,
                        "unrealized_pnl": 0.0,
                        "margin_reserved": 17400.0,
                    }
                ],
            }
        )
        day = now_iso[:10]
        binding = self._binding(
            prod["art"],
            symbol="0050.TWSE",
            closes=(88.0, 97.1),
            event_time=f"{day}T00:00:00+08:00",
            observed_at=now_iso,
        )
        with patch.dict(os.environ, prod["env"], clear=True), patch.object(
            expression_inputs, "_http_get_json", prod["fetch"]
        ):
            producer = PaperSignalProducer(
                store_for=lambda _: store, strategy=_runner_strategy()
            )
            self.assertEqual(producer.produce(binding, now_iso), 1)
        [sig] = list(store._pending)
        return sig

    def _hosted_layout(self, root: Path, partitions: dict[str, list[dict[str, Any]]]) -> None:
        base = root / "normalized" / "taifex_ssf_contracts"
        for child in base.iterdir():
            for f in child.iterdir():
                f.unlink()
            child.rmdir()
        for day, rows in partitions.items():
            _write_ssf_partition(root, day, rows)

    def test_producer_attaches_d_minus_1_session_and_runtime_marks_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "market_data_store"
            self._write_opening_partition(root)
            service, _emitter = self._open_srf_via_production_path(root, tmpdir)
            # Hosted layout: partition date=2026-11-10 holds price_date 2026-11-09.
            self._hosted_layout(root, {"2026-11-10": [_srf_row("2026-11-09", 97.1)]})

            sig = self._produce_hold_at(service, "2026-11-10T07:30:00Z")
            meta = sig["metadata"]
            self.assertEqual(meta["session_settlement_prices"], {"ssf:SRF:202611": 97.1})
            self.assertEqual(meta["trade_date"], "2026-11-09")
            self.assertEqual(meta["as_of_trading_day"], "2026-11-10")

            marks: list[tuple[Any, ...]] = []
            real_mark = service._algo.MarkFuturesSession

            def spy(*args: Any, **kwargs: Any) -> Any:
                marks.append(args)
                return real_mark(*args, **kwargs)

            service._algo.MarkFuturesSession = spy
            snap = self._drain_at(service, "2026-11-10T07:30:00Z")
            pos = service._algo.get_instrument_position("ssf:SRF:202611")
            self.assertEqual(pos["mark_price"], 97.1)
            self.assertEqual(pos["mark_as_of"], "2026-11-09")
            self.assertEqual(len(marks), 1)
            self.assertEqual(marks[0][0], "2026-11-09")
            self.assertEqual(service._algo._last_futures_mark_session_date, "2026-11-09")
            self.assertEqual(
                snap["paper_state"]["futures_mark_diagnostic"]["status"], "marked"
            )

            # A second signal for the same session neither marks nor checks again.
            # (the real signal dict re-enqueued under a new identity, as a later
            # bar of the same decision day would carry the same session inputs).
            import copy

            again = copy.deepcopy(sig)
            again["signal_id"] = f"{sig['signal_id']}-later-bar"
            again["timestamp"] = "2026-11-10T08:30:00Z"
            again["metadata"]["decision_id"] = f"{meta['decision_id']}-later-bar"
            self._prod["store"].enqueue(again)
            processed_before = len(service._consumer._processed_signal_ids)
            self._drain_at(service, "2026-11-10T08:30:00Z")
            self.assertEqual(
                len(service._consumer._processed_signal_ids), processed_before + 1
            )
            self.assertEqual(len(marks), 1)

    def test_producer_records_per_contract_reason_and_runtime_never_marks_from_it(self) -> None:
        cases = {
            "older_than_d_minus_1": (
                [_srf_row("2026-11-06", 99.0)],
                "settlement_price_stale",
            ),
            "missing": (
                [_srf_row("2026-11-09", 99.0, contract_month="202612")],
                "settlement_price_missing",
            ),
            "tombstone": (
                [_srf_row("2026-11-09", 99.0, tombstone=True)],
                "contract_tombstoned",
            ),
        }
        for name, (rows, reason) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir) / "market_data_store"
                self._write_opening_partition(root)
                service, _emitter = self._open_srf_via_production_path(root, tmpdir)
                self._hosted_layout(root, {"2026-11-10": rows})

                sig = self._produce_hold_at(service, "2026-11-10T07:30:00Z")
                meta = sig["metadata"]
                self.assertEqual(meta["session_settlement_prices"], {})
                self.assertEqual(
                    meta["session_settlement_price_reasons"], {"ssf:SRF:202611": reason}
                )
                snap = self._drain_at(service, "2026-11-10T07:30:00Z")
                pos = service._algo.get_instrument_position("ssf:SRF:202611")
                self.assertNotEqual(pos["mark_price"], 99.0)
                self.assertIsNone(service._algo._last_futures_mark_session_date)
                diag = snap["paper_state"]["futures_mark_diagnostic"]
                self.assertEqual(diag["status"], "skipped_missing_settlement_price")
                self.assertEqual(diag["missing_contracts"], {"ssf:SRF:202611": reason})

    def test_valuation_mark_stays_fresh_between_host_refreshes(self) -> None:
        def mark_ref(partitions: dict[str, list[dict[str, Any]]], now_iso: str):
            with tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir) / "market_data_store"
                self._write_opening_partition(root)
                service, emitter = self._open_srf_via_production_path(root, tmpdir)
                self._hosted_layout(root, partitions)
                self._value_at(service, now_iso, root)
                status = service._performance_telemetry.get("status")
                refs = (
                    self._perf_events(emitter)
                    .get("pnl_snapshot", {})
                    .get("metadata", {})
                    .get("mark_refs")
                )
                return status, refs

        hosted = {"2026-11-10": [_srf_row("2026-11-09", 97.1)]}
        for now_iso in ("2026-11-10T23:30:00Z", "2026-11-11T06:50:00Z"):
            with self.subTest(now_iso):
                status, refs = mark_ref(hosted, now_iso)
                self.assertEqual(status, "emitted")
                self.assertEqual(refs[0]["as_of"][:10], "2026-11-09")
        # A weekend: Friday 2026-11-13's partition holds Thursday's session.
        friday = {"2026-11-13": [_srf_row("2026-11-12", 97.4)]}
        for now_iso in ("2026-11-14T10:00:00Z", "2026-11-16T06:00:00Z"):
            with self.subTest(now_iso):
                status, refs = mark_ref(friday, now_iso)
                self.assertEqual(status, "emitted")
                self.assertEqual(refs[0]["price"], 97.4)
                self.assertEqual(refs[0]["as_of"][:10], "2026-11-12")
        # A newer partition carrying a newer price_date wins over the older row.
        newer = {
            "2026-11-10": [_srf_row("2026-11-09", 97.1)],
            "2026-11-11": [_srf_row("2026-11-10", 98.2)],
        }
        status, refs = mark_ref(newer, "2026-11-11T08:00:00Z")
        self.assertEqual(status, "emitted")
        self.assertEqual(refs[0]["price"], 98.2)
        self.assertEqual(refs[0]["as_of"][:10], "2026-11-10")
        # A missed host refresh is detected one session later, not never:
        # the 2026-11-09 row is two sessions behind on Thursday 2026-11-12.
        status, _refs = mark_ref(hosted, "2026-11-12T06:50:00Z")
        self.assertEqual(status, "marks_unavailable")
        status, _refs = mark_ref(hosted, "2026-11-14T10:00:00Z")
        self.assertEqual(status, "marks_unavailable")

    @staticmethod
    def _perf_events(emitter: Any) -> dict[str, dict[str, Any]]:
        return {
            p["event_type"]: p
            for p in emitter.payloads
            if p["event_type"] in ("pnl_snapshot", "drawdown_snapshot")
        }

    def _value_at(self, service: PaperRuntimeService, now_iso: str, root: Path) -> None:
        from datetime import datetime

        now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        service._valuation_now = lambda: now
        # docker-compose.yml sets this root on the reconciler container.
        with patch.dict(
            os.environ, {"PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT": str(root)}
        ):
            service._maybe_emit_performance_snapshots()

    def test_open_stock_future_is_valued_at_dataset_daily_settlement_via_production_path(
        self,
    ) -> None:
        from services.execution.lean_runtime.paper_runtime import PaperExecutionAlgorithm

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "market_data_store"
            # The hosted layout: the 2026-11-10 partition holds rows whose
            # price_date is the 2026-11-09 session.
            self._write_opening_partition(root)
            service, emitter = self._open_srf_via_production_path(root, tmpdir)
            fetched: list[str] = []
            service._mark_provider._fetch_json = lambda url, timeout: fetched.append(url)
            _write_ssf_partition(
                root,
                "2026-11-10",
                [
                    _srf_row("2026-11-09", 97.1),
                    _srf_row("2026-11-09", 97.1, contract_month="202612"),
                ],
            )

            self._value_at(service, "2026-11-10T07:13:29Z", root)

            self.assertEqual(fetched, [], "no source-ingest read for an ssf symbol")
            self.assertEqual(service._performance_telemetry.get("status"), "emitted")
            by_type = self._perf_events(emitter)
            self.assertEqual(set(by_type), {"pnl_snapshot", "drawdown_snapshot"})
            pnl = by_type["pnl_snapshot"]
            # Unrealized is quantity * multiplier * mark - average entry
            # = 2 * 1000 * (97.1 - 97.1) = 0, so pnl is exactly the fill's
            # fee 16.0 plus tax 3.884 taken from the cash ledger.
            self.assertAlmostEqual(pnl["metrics"]["pnl"], -19.884, places=3)
            [ref] = pnl["metadata"]["mark_refs"]
            self.assertEqual(ref["symbol"], "ssf:SRF:202611")
            self.assertEqual(ref["price"], 97.1)
            self.assertEqual(ref["as_of"][:10], "2026-11-09")
            self.assertIn("normalized/taifex_ssf_contracts", ref["source_ref"])
            self.assertIn("2026-11-09", ref["source_ref"])
            self.assertIn("drawdown_pct", by_type["drawdown_snapshot"]["metrics"])

            # Acceptance 2: MarkFuturesSession applies the same dataset value.
            info = read_session_settlement_prices_for_open_futures(
                open_stock_future_positions(
                    service._algo.open_stock_future_position_records()
                ),
                "2026-11-09",
                normalized_root=root,
                partition_horizon="2026-11-10",
            )
            self.assertEqual(info["session_settlement_prices"], {"ssf:SRF:202611": 97.1})
            service._algo.MarkFuturesSession("2026-11-09", info["session_settlement_prices"])
            pos = service._algo.get_instrument_position("ssf:SRF:202611")
            self.assertEqual(pos["mark_price"], ref["price"])
            self.assertEqual(pos["mark_as_of"], "2026-11-09")
            self.assertIsInstance(service._algo, PaperExecutionAlgorithm)

    def test_summary_projection_and_drift_see_futures_valuation(self) -> None:
        from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "market_data_store"
            self._write_opening_partition(root)
            service, emitter = self._open_srf_via_production_path(root, tmpdir)
            _write_ssf_partition(root, "2026-11-10", [_srf_row("2026-11-09", 97.1)])
            self._value_at(service, "2026-11-10T07:13:29Z", root)

            store = RuntimeSummaryProjectionStore(path=None)
            summary: dict[str, Any] = {}
            for payload in emitter.payloads:
                # The transport adds the runtime identity the stub emitter omits.
                event = {
                    **payload,
                    "deployment_stage": "paper",
                    "runtime_id": "runtime-rb-expr",
                    "tenant_id": "default",
                    "binding_id": "rb-expr",
                    "metadata": {
                        **payload["metadata"],
                        "engine_bridge_repo": "ajoe734/pantheon-lean.git",
                        "engine_bridge_path": "pantheon/lean",
                        "engine_bridge_commit": "abc1234",
                    },
                }
                if event["event_type"] in ("pnl_snapshot", "drawdown_snapshot"):
                    # Fixed valuation instant, so the freshness check is clock free.
                    event["created_at"] = "2026-11-10T07:13:29Z"
                projected = store.project_event(event)
                summary = projected or summary

            self.assertNotIn("_valuation_failure", summary)
            self.assertAlmostEqual(summary["pnl"], -19.884, places=3)
            self.assertIn("drawdown", summary)
            [row] = [
                r for r in summary["positions"] if r.get("instrument_id") == "ssf:SRF:202611"
            ]
            self.assertEqual(row["mark_price"], 97.1)
            self.assertEqual(row["mark_as_of"][:10], "2026-11-09")
            self.assertIn("taifex_ssf_contracts", row["mark_source_ref"])
            self.assertNotIn("mark_unavailable_reason", row)
            self.assertNotIn("mark_stale", row)

            # Reconciliation drift: the drawdown metric is observed, so an open
            # futures position alone no longer yields a degraded check.
            drift = _load_reconciliation_drift_main()
            observed = drift._resolve_scheduled_metrics(summary)
            self.assertIn("drawdown", observed)
            [check] = drift._drift_checks({"drawdown": 0.1245}, observed, {})
            self.assertNotEqual(check["status"], "degraded")
            self.assertIsNotNone(check["observed"])

    def test_futures_mark_unavailable_for_missing_stale_and_tombstone_rows(self) -> None:
        cases = {
            "missing": ([_srf_row("2026-11-09", 97.1, contract_month="202612")], "settlement_price_missing"),
            "stale": ([_srf_row("2026-11-05", 97.1)], "settlement_price_stale"),
            "tombstone": ([_srf_row("2026-11-09", 97.1, tombstone=True)], "contract_tombstoned"),
        }
        for name, (rows, reason) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir) / "market_data_store"
                self._write_opening_partition(root)
                service, emitter = self._open_srf_via_production_path(root, tmpdir)
                shutil_root = root / "normalized" / "taifex_ssf_contracts"
                for child in shutil_root.iterdir():
                    for f in child.iterdir():
                        f.unlink()
                    child.rmdir()
                _write_ssf_partition(root, "2026-11-10", rows)

                self._value_at(service, "2026-11-10T07:13:29Z", root)

                telemetry = service._performance_telemetry
                self.assertEqual(telemetry["status"], "marks_unavailable")
                self.assertEqual(telemetry["code"], "missing_market_marks")
                self.assertEqual(telemetry["missing_symbols"], ["ssf:SRF:202611"])
                futures = telemetry["mark_source"]["futures_settlement"]
                self.assertEqual(futures["reasons"], {"ssf:SRF:202611": reason})
                self.assertEqual(self._perf_events(emitter), {})

    def test_futures_mark_is_calendar_aware_across_a_weekend_and_not_aged_by_seconds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "market_data_store"
            self._write_opening_partition(root)
            service, emitter = self._open_srf_via_production_path(root, tmpdir)
            # 2026-11-13 is a Friday; its D-1 session is Thursday 2026-11-12.
            _write_ssf_partition(root, "2026-11-16", [_srf_row("2026-11-13", 98.0)])
            # Monday 2026-11-16: expected session is Friday 2026-11-13 (> 48h old).
            self._value_at(service, "2026-11-16T01:00:00Z", root)
            self.assertEqual(service._performance_telemetry.get("status"), "emitted")
            [ref] = self._perf_events(emitter)["pnl_snapshot"]["metadata"]["mark_refs"]
            self.assertEqual(ref["price"], 98.0)
            self.assertEqual(ref["as_of"][:10], "2026-11-13")

    def test_runtime_without_open_futures_never_consults_the_futures_provider(self) -> None:
        class _Forbidden:
            def resolve(self, positions: Any) -> Any:
                raise AssertionError("futures provider consulted for a cash-only book")

        with tempfile.TemporaryDirectory() as tmpdir:
            service, emitter = self._runtime_service(
                InMemoryPendingSignalStore(), self._binding(self._artifact()), tmpdir
            )
            service._futures_mark_provider = _Forbidden()
            service._maybe_emit_performance_snapshots()
            self.assertEqual(service._performance_telemetry["status"], "no_fills")
            self.assertNotIn(
                "futures_settlement", service._performance_telemetry["mark_source"]
            )
            self.assertEqual(self._perf_events(emitter), {})

    def test_runtime_bootstrap_dataset_final_settlement_pending_then_settled_for_202611_srf(
        self,
    ) -> None:
        from services.execution.lean_runtime import expression_inputs

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "market_data_store"
            _write_ssf_partition(root, "2026-11-06", _captured_20261106_rows())

            summary_state: dict[str, Any] = {
                "runtime_id": "runtime-rb-expr",
                "binding_id": "rb-expr",
                "ledger_fill_count": 0,
                "executed_trade_count": 0,
                "positions": None,
            }

            def fetch(url: str, headers: dict[str, str], timeout: float) -> Any:
                if "/api/capital-pools/" in url:
                    return self.POOL
                if "/api/telemetry/runtime-summaries/" in url:
                    return summary_state
                raise AssertionError(url)

            env = {
                "PANTHEON_PAPER_EXPRESSION_LAYER": "on",
                "PANTHEON_CAPITAL_API_URL": "http://capital:8092",
                "DEPLOYMENT_CAPITAL_SERVICE_TOKEN": "capital-reader",
                "PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083",
                "PANTHEON_TELEMETRY_SERVICE_TOKEN": "telemetry-reader",
                "PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT": str(root),
                "PANTHEON_TENANT_ID": "default",
            }
            store = InMemoryPendingSignalStore()
            art = self._artifact(symbol="0050.TWSE")
            binding = self._binding(
                art,
                symbol="0050.TWSE",
                closes=(88.0, 92.5),
                event_time="2026-11-06T00:00:00+08:00",
                observed_at="2026-11-09T09:00:00Z",
            )
            service, _emitter = self._runtime_service(
                store, binding, tmpdir, normalized_root=root
            )
            self.assertIsInstance(
                service._final_settlement_source, DatasetFinalSettlementSource
            )

            # Open 2 contracts of ssf:SRF:202611 on 2026-11-09
            with patch.dict(os.environ, env, clear=True), patch.object(
                expression_inputs, "_http_get_json", fetch
            ):
                producer = PaperSignalProducer(
                    store_for=lambda _: store, strategy=_runner_strategy()
                )
                self.assertEqual(producer.produce(binding, "2026-11-09T09:00:00Z"), 1)
            self._drain_at(service, "2026-11-09T09:00:00Z")
            self.assertEqual(
                service._algo.get_instrument_position("ssf:SRF:202611")["quantity"], 2.0
            )

            summary_state = {
                "runtime_id": "runtime-rb-expr",
                "binding_id": "rb-expr",
                "ledger_fill_count": 1,
                "executed_trade_count": 1,
                "ledger_cash": float(service._algo._cash),
                "positions": [
                    {
                        "symbol": "0050.TWSE",
                        "instrument_id": "ssf:SRF:202611",
                        "kind": "stock_future",
                        "quantity": 2.0,
                        "multiplier": 1000.0,
                        "average_price": 92.35,
                        "mark_price": 92.35,
                        "unrealized_pnl": 0.0,
                        "margin_reserved": 17400.0,
                    }
                ],
            }

            # Decision day 2026-11-19: the D-1 session 2026-11-18 is the last
            # trading day (third Wednesday of 202611) and the 2026-11-19
            # partition carries no 202611 final settlement price yet.
            _write_ssf_partition(
                root,
                "2026-11-19",
                [
                    _srf_row(
                        "2026-11-18",
                        95.10,
                        contract_month="202611",
                        last_final_settlement={
                            "day": "20261021",
                            "contract_month": "202610",
                            "price": 90.0,
                        },
                    )
                ],
            )
            binding_1118 = self._binding(
                art,
                symbol="0050.TWSE",
                closes=(88.0, 95.10),
                event_time="2026-11-19T00:00:00+08:00",
                observed_at="2026-11-19T13:50:00Z",
            )
            with patch.dict(os.environ, env, clear=True), patch.object(
                expression_inputs, "_http_get_json", fetch
            ):
                producer = PaperSignalProducer(
                    store_for=lambda _: store, strategy=_runner_strategy()
                )
                self.assertEqual(producer.produce(binding_1118, "2026-11-19T13:50:00Z"), 1)

            self._drain_at(service, "2026-11-19T13:50:00Z")
            pos_pending = service._algo.get_instrument_position("ssf:SRF:202611")
            self.assertIsNotNone(pos_pending)
            self.assertEqual(pos_pending["status"], "settlement_pending")
            self.assertEqual(pos_pending["quantity"], 2.0)

            # In the later partition 2026-11-20 the dataset publishes
            # FinalSettlementPriceSSF for 202611 (95.25), day 2026-11-18.
            _write_ssf_partition(
                root,
                "2026-11-20",
                [
                    _srf_row(
                        "2026-11-19",
                        95.50,
                        contract_month="202612",
                        last_final_settlement={
                            "day": "20261118",
                            "contract_month": "202611",
                            "price": 95.25,
                        },
                    )
                ],
            )
            binding_1119 = self._binding(
                art,
                symbol="0050.TWSE",
                closes=(88.0, 95.50),
                event_time="2026-11-20T00:00:00+08:00",
                observed_at="2026-11-20T13:50:00Z",
            )
            with patch.dict(os.environ, env, clear=True), patch.object(
                expression_inputs, "_http_get_json", fetch
            ):
                producer = PaperSignalProducer(
                    store_for=lambda _: store, strategy=_runner_strategy()
                )
                self.assertEqual(producer.produce(binding_1119, "2026-11-20T13:50:00Z"), 1)

            snap_settled = self._drain_at(service, "2026-11-20T13:50:00Z")
            pos_settled = service._algo.get_instrument_position("ssf:SRF:202611")
            self.assertIsNotNone(pos_settled)
            self.assertEqual(pos_settled["status"], "closed")
            self.assertAlmostEqual(pos_settled["quantity"], 0.0, places=4)
            self.assertAlmostEqual(
                pos_settled["initial_margin_reserved"], 0.0, places=4
            )
            self.assertAlmostEqual(
                pos_settled["maintenance_margin_required"], 0.0, places=4
            )
            self.assertAlmostEqual(pos_settled["mark_price"], 95.25, places=4)
            reconcil = service._algo.assert_ledger_reconciliation()
            self.assertAlmostEqual(reconcil["margin_reserved"], 0.0, places=4)
            self.assertAlmostEqual(
                reconcil["maintenance_margin_required"], 0.0, places=4
            )
            # Realized PnL = 2 * 1000 * (95.25 - 92.35) = +5800.0
            self.assertAlmostEqual(reconcil["realized_pnl"], 5800.0, places=4)
            settled_events = [
                ev
                for ev in snap_settled["paper_state"]["recent_order_events"]
                if ev["event_type"] == "paper_futures_settled"
            ]
            self.assertEqual(len(settled_events), 1)
            self.assertEqual(
                settled_events[0]["metadata"]["instrument_id"], "ssf:SRF:202611"
            )
            self.assertAlmostEqual(
                settled_events[0]["metadata"]["final_settlement_price"], 95.25, places=4
            )
            self.assertEqual(
                settled_events[0]["metadata"]["settlement_day"], "2026-11-18"
            )

    def test_margin_call_then_forced_liquidation_with_real_srf_margin_figures_via_production_path(
        self,
    ) -> None:
        from services.execution.lean_runtime import expression_inputs

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "market_data_store"
            _write_ssf_partition(root, "2026-11-06", _captured_20261106_rows())

            summary_state: dict[str, Any] = {
                "runtime_id": "runtime-rb-expr",
                "binding_id": "rb-expr",
                "ledger_fill_count": 0,
                "executed_trade_count": 0,
                "positions": None,
            }

            def fetch(url: str, headers: dict[str, str], timeout: float) -> Any:
                if "/api/capital-pools/" in url:
                    return self.POOL
                if "/api/telemetry/runtime-summaries/" in url:
                    return summary_state
                raise AssertionError(url)

            env = {
                "PANTHEON_PAPER_EXPRESSION_LAYER": "on",
                "PANTHEON_CAPITAL_API_URL": "http://capital:8092",
                "DEPLOYMENT_CAPITAL_SERVICE_TOKEN": "capital-reader",
                "PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083",
                "PANTHEON_TELEMETRY_SERVICE_TOKEN": "telemetry-reader",
                "PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT": str(root),
                "PANTHEON_TENANT_ID": "default",
            }
            store = InMemoryPendingSignalStore()
            art = self._artifact(symbol="0050.TWSE")
            binding = self._binding(
                art,
                symbol="0050.TWSE",
                closes=(88.0, 92.5),
                event_time="2026-11-06T00:00:00+08:00",
                observed_at="2026-11-09T09:00:00Z",
            )
            service, _emitter = self._runtime_service(
                store, binding, tmpdir, normalized_root=root
            )

            # 1. Open 2 contracts of ssf:SRF:202611 on 2026-11-09 at 92.35
            # Initial margin = 2 * 8700 = 17,400; Maintenance margin = 2 * 6700 = 13,400
            with patch.dict(os.environ, env, clear=True), patch.object(
                expression_inputs, "_http_get_json", fetch
            ):
                producer = PaperSignalProducer(
                    store_for=lambda _: store, strategy=_runner_strategy()
                )
                self.assertEqual(producer.produce(binding, "2026-11-09T09:00:00Z"), 1)
            self._drain_at(service, "2026-11-09T09:00:00Z")
            pos = service._algo.get_instrument_position("ssf:SRF:202611")
            self.assertEqual(pos["quantity"], 2.0)
            self.assertAlmostEqual(pos["initial_margin_per_contract"], 8700.0, places=4)
            self.assertAlmostEqual(
                pos["maintenance_margin_per_contract"], 6700.0, places=4
            )
            self.assertAlmostEqual(pos["initial_margin_reserved"], 17400.0, places=4)
            self.assertAlmostEqual(
                pos["maintenance_margin_required"], 13400.0, places=4
            )

            summary_state = {
                "runtime_id": "runtime-rb-expr",
                "binding_id": "rb-expr",
                "ledger_fill_count": 1,
                "executed_trade_count": 1,
                "ledger_cash": float(service._algo._cash),
                "positions": [
                    {
                        "symbol": "0050.TWSE",
                        "instrument_id": "ssf:SRF:202611",
                        "kind": "stock_future",
                        "quantity": 2.0,
                        "multiplier": 1000.0,
                        "average_price": 92.35,
                        "mark_price": 92.35,
                        "unrealized_pnl": 0.0,
                        "margin_reserved": 17400.0,
                    }
                ],
            }

            # 2. Session 2026-11-10: SRF drops from 92.35 to 48.00 -> unrealized PnL = 2 * 1000 * (48.00 - 92.35) = -88,700
            # Equity (~11,252.31) < maintenance margin required (13,400.0) -> emits paper_margin_call
            _write_ssf_partition(root, "2026-11-10", [_srf_row("2026-11-09", 48.0)])
            binding_1110 = self._binding(
                art,
                symbol="0050.TWSE",
                closes=(45.0, 48.0),
                event_time="2026-11-10T00:00:00+08:00",
                observed_at="2026-11-10T13:50:00Z",
            )
            with patch.dict(os.environ, env, clear=True), patch.object(
                expression_inputs, "_http_get_json", fetch
            ):
                producer = PaperSignalProducer(
                    store_for=lambda _: store, strategy=_runner_strategy()
                )
                self.assertEqual(producer.produce(binding_1110, "2026-11-10T13:50:00Z"), 1)
            snap_1110 = self._drain_at(service, "2026-11-10T13:50:00Z")
            reconcil_1110 = service._algo.assert_ledger_reconciliation()
            self.assertTrue(reconcil_1110["margin_call_active"])
            self.assertLess(
                reconcil_1110["equity"], reconcil_1110["maintenance_margin_required"]
            )
            self.assertAlmostEqual(
                reconcil_1110["maintenance_margin_required"], 13400.0, places=4
            )
            mc_events = [
                ev
                for ev in snap_1110["paper_state"]["recent_order_events"]
                if ev["event_type"] == "paper_margin_call"
            ]
            self.assertEqual(len(mc_events), 1)
            self.assertEqual(mc_events[0]["metadata"]["trade_date"], "2026-11-09")
            self.assertAlmostEqual(
                mc_events[0]["metadata"]["maintenance_margin_required"],
                13400.0,
                places=4,
            )
            self.assertAlmostEqual(
                mc_events[0]["metadata"]["initial_margin_reserved"], 17400.0, places=4
            )

            # 3. Session 2026-11-11: SRF settles at 47.50 -> equity remains below 13,400 -> emits paper_forced_liquidation
            _write_ssf_partition(root, "2026-11-11", [_srf_row("2026-11-10", 47.5)])
            binding_1111 = self._binding(
                art,
                symbol="0050.TWSE",
                closes=(45.0, 47.5),
                event_time="2026-11-11T00:00:00+08:00",
                observed_at="2026-11-11T13:50:00Z",
            )
            with patch.dict(os.environ, env, clear=True), patch.object(
                expression_inputs, "_http_get_json", fetch
            ):
                producer = PaperSignalProducer(
                    store_for=lambda _: store, strategy=_runner_strategy()
                )
                self.assertEqual(producer.produce(binding_1111, "2026-11-11T13:50:00Z"), 1)
            snap_1111 = self._drain_at(service, "2026-11-11T13:50:00Z")
            reconcil_1111 = service._algo.assert_ledger_reconciliation()
            self.assertFalse(reconcil_1111["margin_call_active"])
            self.assertAlmostEqual(reconcil_1111["margin_reserved"], 0.0, places=4)
            self.assertAlmostEqual(
                reconcil_1111["maintenance_margin_required"], 0.0, places=4
            )
            liq_events = [
                ev
                for ev in snap_1111["paper_state"]["recent_order_events"]
                if ev["event_type"] == "paper_forced_liquidation"
            ]
            self.assertEqual(len(liq_events), 1)
            self.assertEqual(
                liq_events[0]["metadata"]["instrument_id"], "ssf:SRF:202611"
            )
            self.assertEqual(liq_events[0]["metadata"]["trade_date"], "2026-11-10")
            self.assertEqual(liq_events[0]["metadata"]["liquidated_quantity"], -2.0)
            self.assertAlmostEqual(
                liq_events[0]["metadata"]["liquidation_price"], 47.5, places=4
            )


if __name__ == "__main__":
    unittest.main()
