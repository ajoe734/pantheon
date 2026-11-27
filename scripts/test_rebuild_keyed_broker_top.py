"""Tests for scripts/rebuild_keyed_broker_top.py (SOURCE-BROKER-TOP-KEYED-SIDE-20261127)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Sequence

import pytest

from scripts import rebuild_keyed_broker_top as rkb
from services.source_ingestion.connectors.finmind_taiwan import FinMindTaiwanBrokerDailyReportAdapter
from services.source_ingestion.daily_bars_store import (
    KEYED_DATASETS,
    DailyBarsStore,
    DatasetKeyDeclaration,
    KeyedDatasetStore,
    keyed_dataset_dir,
    keyed_dataset_store,
)
from services.source_ingestion.market_data_storage import _append_jsonl, _compact_keyed_record

HOSTED_SYMBOLS = ("0050", "2317", "2330")
HOSTED_NORMALIZED_SESSIONS = (
    "2026-11-11",
    "2026-11-12",
    "2026-11-13",
    "2026-11-16",
    "2026-11-17",
    "2026-11-18",
    "2026-11-19",
    "2026-11-20",
    "2026-11-23",
    "2026-11-24",
    "2026-11-25",
    "2026-11-26",
)
HOSTED_KEYED_SESSIONS = HOSTED_NORMALIZED_SESSIONS[3:]  # 2026-11-16..2026-11-26


def _load_taiwan_reference():
    ref_path = Path(__file__).resolve().parents[1] / "services" / "data-plane" / "taiwan_reference.py"
    spec = importlib.util.spec_from_file_location("taiwan_reference", ref_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _legacy_three_field_declaration() -> DatasetKeyDeclaration:
    cur = KEYED_DATASETS["tw_broker_top"]
    return DatasetKeyDeclaration(
        dataset="tw_broker_top",
        key_fields=("symbol", "trade_date", "rank"),
        partition_field="trade_date",
        key_aliases=(cur.key_aliases[0], cur.key_aliases[1], cur.key_aliases[3]),
        optional_key_fields=(),
        retention_days=cur.retention_days,
        day_budget_bytes=cur.day_budget_bytes,
        readers=cur.readers,
    )


def _session_payload(symbol: str, trade_date: str, per_side: int = 20) -> dict:
    rows = []
    for idx in range(1, per_side + 1):
        rows.append({
            "stock_id": symbol,
            "date": trade_date,
            "securities_trader_id": f"B{idx:03d}",
            "securities_trader": f"BuyBroker-{symbol}-{idx:02d}",
            "price": 100.0 + idx,
            "buy": 40_000 + (per_side - idx + 1) * 1_000,
            "sell": 5_000,
        })
        rows.append({
            "stock_id": symbol,
            "date": trade_date,
            "securities_trader_id": f"S{idx:03d}",
            "securities_trader": f"SellBroker-{symbol}-{idx:02d}",
            "price": 100.0 + idx,
            "buy": 5_000,
            "sell": 40_000 + (per_side - idx + 1) * 1_000,
        })
    return {"data": rows}


def _populate_hosted_like_store(
    root: Path,
    *,
    normalized_dates: Sequence[str] = HOSTED_NORMALIZED_SESSIONS,
    keyed_dates: Sequence[str] = HOSTED_KEYED_SESSIONS,
    symbols: Sequence[str] = HOSTED_SYMBOLS,
) -> None:
    """Create the exact pre-fix hosted shape: normalized holds 20 buy + 20 sell per symbol;
    keyed holds only 20 sell per symbol written under the old 3-field key (symbol, trade_date, rank)."""
    adapter = FinMindTaiwanBrokerDailyReportAdapter(max_rank=20)
    legacy_store = KeyedDatasetStore(root, _legacy_three_field_declaration())
    keyed_set = set(keyed_dates)
    for trade_date in normalized_dates:
        run_id = f"ingest-{trade_date.replace('-', '')}"
        records = []
        for symbol in symbols:
            records.extend(
                adapter.records_from_daily_report_payload(
                    _session_payload(symbol, trade_date, 20),
                    symbol=symbol,
                    trade_date=trade_date,
                    available_time=f"{trade_date}T10:30:00Z",
                )
            )
        norm_path = root / "normalized" / "tw_broker_top" / f"date={trade_date}" / f"{run_id}.jsonl"
        _append_jsonl(
            norm_path,
            [
                {
                    "source_id": r.source_id,
                    "connector_id": r.connector_id,
                    "dataset": "tw_broker_top",
                    "content_ref": r.content_ref,
                    "metadata": dict(r.metadata),
                }
                for r in records
            ],
        )
        if trade_date in keyed_set:
            legacy_store.write_records(
                [_compact_keyed_record(r, "tw_broker_top") for r in records],
                ingest_run_id=run_id,
            )


def test_dry_run_reports_dropped_buy_side_and_changes_nothing_on_disk(tmp_path):
    _populate_hosted_like_store(tmp_path)
    assert rkb.keyed_broker_top_needs_repair(tmp_path) is True
    before_files = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    report = rkb.rebuild_keyed_broker_top(tmp_path, apply=False)
    assert report["apply"] is False
    assert report["key_fields"] == ["symbol", "trade_date", "side", "rank"]
    assert report["partition_count"] == len(HOSTED_NORMALIZED_SESSIONS)
    for session in HOSTED_KEYED_SESSIONS:
        part = report["partitions"][session]
        assert part["needs_repair"] is True
        assert part["before"]["key_fields"] == ["symbol", "trade_date", "rank"]
        assert part["before"]["buy_rows"] == 0 and part["before"]["sell_rows"] == 60
        assert part["normalized"]["buy_rows"] == 60 and part["normalized"]["sell_rows"] == 60
    after_files = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after_files == before_files


def test_apply_repairs_all_partitions_with_20_buy_and_20_sell_per_symbol_and_is_idempotent(tmp_path):
    _populate_hosted_like_store(tmp_path)
    report = rkb.rebuild_keyed_broker_top(tmp_path, apply=True)
    assert report["apply"] is True
    assert report["mismatches"] == []
    assert report["partitions_written"] == list(HOSTED_NORMALIZED_SESSIONS)
    assert report["partitions_unchanged"] == []
    assert rkb.keyed_broker_top_needs_repair(tmp_path) is False

    store = KeyedDatasetStore(tmp_path, KEYED_DATASETS["tw_broker_top"])
    ref_mod = _load_taiwan_reference()
    for session in HOSTED_KEYED_SESSIONS:
        part = report["partitions"][session]["after"]
        assert part["verified"] is True
        assert part["key_fields"] == ["symbol", "trade_date", "side", "rank"]
        assert part["row_count"] == 120 and part["buy_rows"] == 60 and part["sell_rows"] == 60
        for sym in HOSTED_SYMBOLS:
            assert part["per_symbol"][sym] == {"buy": 20, "sell": 20, "total": 40}
            read_rows = ref_mod.read_tw_broker_top_rows(tmp_path, trade_date=session, symbol=sym)
            assert len(read_rows) == 40
            assert sum(1 for r in read_rows if r["side"] == "buy") == 20
            assert sum(1 for r in read_rows if r["side"] == "sell") == 20
        assert store.verify_partition(session)["verified"] is True

    # Second run is completely idempotent: 0 partitions written, mtimes and generations unchanged
    part_files = sorted(keyed_dataset_dir(tmp_path, "tw_broker_top").rglob("part-*.jsonl.gz"))
    mtimes_before = {p: p.stat().st_mtime_ns for p in part_files}
    generations_before = {s: store.read_manifest(s)["generation"] for s in HOSTED_NORMALIZED_SESSIONS}

    second = rkb.rebuild_keyed_broker_top(tmp_path, apply=True)
    assert second["partitions_written"] == []
    assert second["partitions_unchanged"] == list(HOSTED_NORMALIZED_SESSIONS)
    assert {p: p.stat().st_mtime_ns for p in part_files} == mtimes_before
    assert {s: store.read_manifest(s)["generation"] for s in HOSTED_NORMALIZED_SESSIONS} == generations_before

    if_needed = rkb.repair_keyed_broker_top_if_needed(tmp_path)
    assert if_needed["repaired"] is False


def test_automatic_repair_runs_through_existing_deploy_and_refresh_paths(tmp_path):
    """Acceptance 4: without any manual CLI write, starting DailyBarsStore (deploy startup) or opening
    keyed_dataset_store(root, 'tw_broker_top') (refresh path) repairs the 2026-11-16..latest partitions."""
    deploy_root = tmp_path / "deploy_path"
    _populate_hosted_like_store(deploy_root, normalized_dates=("2026-11-16", "2026-11-26"),
                                keyed_dates=("2026-11-16", "2026-11-26"))
    assert rkb.keyed_broker_top_needs_repair(deploy_root) is True
    bars_store = DailyBarsStore(deploy_root)
    assert bars_store.last_broker_top_keyed_repair is not None
    assert bars_store.last_broker_top_keyed_repair["repaired"] is True
    assert rkb.keyed_broker_top_needs_repair(deploy_root) is False
    for session in ("2026-11-16", "2026-11-26"):
        rows = KeyedDatasetStore(deploy_root, KEYED_DATASETS["tw_broker_top"]).records(session)
        assert len(rows) == 120
        assert sum(1 for r in rows if r["metadata"]["side"] == "buy") == 60
        assert sum(1 for r in rows if r["metadata"]["side"] == "sell") == 60

    refresh_root = tmp_path / "refresh_path"
    _populate_hosted_like_store(refresh_root, normalized_dates=("2026-11-16", "2026-11-26"),
                                keyed_dates=("2026-11-16", "2026-11-26"))
    assert rkb.keyed_broker_top_needs_repair(refresh_root) is True
    store = keyed_dataset_store(refresh_root, "tw_broker_top")
    assert store is not None
    assert rkb.keyed_broker_top_needs_repair(refresh_root) is False
    for session in ("2026-11-16", "2026-11-26"):
        rows = store.records(session)
        assert len(rows) == 120
        assert sum(1 for r in rows if r["metadata"]["side"] == "buy") == 60
        assert sum(1 for r in rows if r["metadata"]["side"] == "sell") == 60


def test_cli_report_and_if_needed_flags(tmp_path):
    _populate_hosted_like_store(tmp_path, normalized_dates=("2026-11-16", "2026-11-17"),
                                keyed_dates=("2026-11-16", "2026-11-17"))
    report_path = tmp_path / "report.json"
    assert rkb.main(["--root", str(tmp_path), "--since", "2026-11-16", "--report", str(report_path)]) == 0
    dry = json.loads(report_path.read_text(encoding="utf-8"))
    assert dry["apply"] is False and dry["partition_count"] == 2
    assert rkb.keyed_broker_top_needs_repair(tmp_path) is True

    assert rkb.main(["--root", str(tmp_path), "--if-needed", "--report", str(report_path)]) == 0
    applied = json.loads(report_path.read_text(encoding="utf-8"))
    assert applied["repaired"] is True and applied["partitions_written"] == ["2026-11-16", "2026-11-17"]
    assert rkb.keyed_broker_top_needs_repair(tmp_path) is False

    assert rkb.main(["--root", str(tmp_path), "--if-needed", "--report", str(report_path)]) == 0
    noop = json.loads(report_path.read_text(encoding="utf-8"))
    assert noop["repaired"] is False
