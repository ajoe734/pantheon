"""SOURCE-BROKER-WHOLE-MARKET-DAILY-20261127: whole-market daily tw_broker_top from one whole-day file."""
from __future__ import annotations

import gzip
import importlib.util
import io
import json
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from services.source_ingestion import finmind_broker_backfill as fbb
from services.source_ingestion import daily_bars_store as dbs
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.connectors.finmind_taiwan import (
    FinMindLiveFetcher,
    FinMindQuotaError,
    FinMindTaiwanBrokerDailyReportAdapter,
)

DAY = "2026-11-26"
TOKEN = "tok-secret-abc"


def _session(n_symbols: int = 30, branches: int = 25, levels: int = 2, day: str = DAY) -> list[dict]:
    rows = []
    for s in range(n_symbols):
        for b in range(branches):
            for lvl in range(levels):
                buy = (s * 7 + b * 13 + lvl) % 50 * 10
                sell = (s * 11 + b * 5 + lvl * 3) % 50 * 10
                rows.append({"date": day, "stock_id": f"{1101 + s}", "securities_trader_id": f"9{b:03d}",
                             "securities_trader": f"券商{b}-分公司", "price": 100.0 + lvl, "buy": buy, "sell": sell})
    return rows


def _parquet(rows: list[dict]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buf)
    return buf.getvalue()


class Fetcher(FinMindLiveFetcher):
    def __init__(self, body: bytes | None = None, *, token: bool = True, exc: Exception | None = None) -> None:
        super().__init__()
        self.body, self.has_token, self.exc = body, token, exc
        self.calls: list[tuple[str, str]] = []

    def resolve_token(self):
        return TOKEN if self.has_token else None

    def credential_status(self):
        return {"available": self.has_token, "secret_ref_id": self.secret_ref_id}

    def fetch_storage_objects_raw(self, dataset, date):
        self.calls.append((dataset, date))
        if self.exc is not None:
            raise self.exc
        return self.body, {"http_status": 200}


@pytest.fixture(autouse=True)
def _small_market(monkeypatch):
    monkeypatch.setattr(fbb, "MIN_STORED_SYMBOLS", 10)


def _run(tmp_path: Path, fetcher: Fetcher, expected: set[str] | None = None, **kw):
    return fbb.run_whole_market_session(market_data_root=tmp_path, trade_date=DAY, fetcher=fetcher,
                                        expected_symbols=expected, run_id="run-1", **kw)


def test_one_request_stores_top20_both_sides_for_every_symbol_compactly(tmp_path: Path) -> None:
    rows = _session()
    fetcher = Fetcher(_parquet(rows))
    receipt = _run(tmp_path, fetcher, expected={f"{1101 + s}" for s in range(30)})

    assert fetcher.calls == [("TaiwanStockTradingDailyReport", DAY)]
    assert receipt["requests_made"] == 1 <= fbb.FINMIND_PLAN_HOURLY_CAP
    assert receipt["status"] == "complete" and receipt["fresh"] is True and receipt["fallback_required"] is False
    assert receipt["stored_symbol_count"] == 30
    assert receipt["rows"] == receipt["partition_row_count"] == receipt["rows_by_side"]["buy"] + receipt["rows_by_side"]["sell"]
    assert receipt["bytes_compressed"] > 0 and receipt["bytes_uncompressed"] > receipt["bytes_compressed"]
    assert receipt["license_scope"] == "vendor_research" and receipt["redistribution_allowed"] is False
    assert receipt["branch_detail_written"] is False

    store = dbs.keyed_dataset_store(tmp_path, "tw_broker_top")
    records = store.records(DAY)
    assert len(records) == receipt["rows"]
    per_symbol: dict[str, set[str]] = {}
    for rec in records:
        meta = rec["metadata"]
        row = meta["normalized_row"]
        per_symbol.setdefault(row["symbol"], set()).add(row["side"])
        assert 1 <= row["rank"] <= 20
        # Compact: no raw price-level rows, no duplicated JSON body.
        assert "raw_rows" not in json.dumps(rec, ensure_ascii=False) and "body" not in meta
        assert meta["license_scope"] == "vendor_research" and meta["redistribution_allowed"] is False
        assert row["price_levels"] >= 1
    assert all(sides == {"buy", "sell"} for sides in per_symbol.values()) and len(per_symbol) == 30
    assert all(len(json.dumps(r, ensure_ascii=False)) < 800 for r in records)

    # Nothing else is written: no tw_price_daily, no branch detail, no per-row evidence; one receipt.
    files = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file() and p.name != ".lock"}
    assert not any("tw_price_daily" in f or "tw_broker_branch_detail" in f or "normalized" in f for f in files)
    assert [f for f in files if f.startswith("receipts/")] == [f"receipts/tw_broker_whole_market/date={DAY}.json"]
    assert TOKEN not in "".join(Path(tmp_path, f).read_bytes().decode("latin-1") for f in files)


def test_ranking_matches_net_quantity_summed_over_price_levels(tmp_path: Path) -> None:
    rows = [
        {"date": DAY, "stock_id": "2330", "securities_trader_id": "A", "securities_trader": "甲", "buy": 100, "sell": 0},
        {"date": DAY, "stock_id": "2330", "securities_trader_id": "A", "securities_trader": "甲", "buy": 300, "sell": 50},
        {"date": DAY, "stock_id": "2330", "securities_trader_id": "B", "securities_trader": "乙", "buy": 200, "sell": 0},
        {"date": DAY, "stock_id": "2330", "securities_trader_id": "C", "securities_trader": "丙", "buy": 0, "sell": 900},
    ]
    _run(tmp_path, Fetcher(_parquet(rows)), expected={"2330"})
    got = {(r["metadata"]["normalized_row"]["side"], r["metadata"]["normalized_row"]["rank"]):
           r["metadata"]["normalized_row"] for r in dbs.keyed_dataset_store(tmp_path, "tw_broker_top").records(DAY)}
    assert got[("buy", 1)]["broker_id"] == "A" and got[("buy", 1)]["net_qty"] == 350 and got[("buy", 1)]["price_levels"] == 2
    assert got[("buy", 2)]["broker_id"] == "B"
    assert got[("sell", 1)]["broker_id"] == "C" and got[("sell", 1)]["net_qty"] == -900


def test_the_keyed_reader_answers_any_symbol_with_both_sides(tmp_path: Path) -> None:
    _run(tmp_path, Fetcher(_parquet(_session())), expected=None)
    spec = importlib.util.spec_from_file_location(
        "taiwan_reference_wm", Path(__file__).resolve().parents[2] / "data-plane" / "taiwan_reference.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = mod.read_tw_broker_top_rows(tmp_path, trade_date=DAY, symbol="1117")
    assert {r["side"] for r in rows} == {"buy", "sell"}
    assert all(r["symbol"] == "1117" and r["source_dataset"] == "TaiwanStockTradingDailyReport" for r in rows)


def test_rerun_of_a_complete_session_makes_no_request(tmp_path: Path) -> None:
    body = _parquet(_session())
    first = _run(tmp_path, Fetcher(body), expected={f"{1101 + s}" for s in range(30)})
    again = Fetcher(body)
    second = _run(tmp_path, again, expected={f"{1101 + s}" for s in range(30)})
    assert first["status"] == "complete"
    assert second["status"] == "already_complete" and second["requests_made"] == 0 and again.calls == []


@pytest.mark.parametrize("one_sided_symbols", [1, 1000])
def test_missing_sell_side_is_partial_even_with_all_1000_symbols_and_retried(
    tmp_path: Path, monkeypatch, one_sided_symbols: int,
) -> None:
    monkeypatch.setattr(fbb, "MIN_STORED_SYMBOLS", 1000)
    expected = {str(1101 + i) for i in range(1000)}
    rows = [{"date": DAY, "stock_id": str(1101 + i), "securities_trader_id": side,
             "securities_trader": side, "buy": 100 if side == "buy" else 0,
             "sell": 100 if side == "sell" else 0}
            for i in range(1000) for side in ("buy", "sell")
            if side == "buy" or i >= one_sided_symbols]
    body = _parquet(rows)
    first = _run(tmp_path, Fetcher(body), expected=expected)
    assert first["status"] == "partial" and first["fresh"] is False
    assert first["stored_symbol_count"] == 1000
    assert first["rows_by_side"] == {"buy": 1000, "sell": 1000 - one_sided_symbols}
    assert first["coverage"]["partition_verified"] is True
    assert first["coverage"]["single_sided_count"] == one_sided_symbols
    assert first["coverage"]["missing_count"] == one_sided_symbols
    again = Fetcher(body)
    assert _run(tmp_path, again, expected=expected)["status"] == "partial"
    assert len(again.calls) == 1


def test_coverage_uses_persisted_sides_and_rejects_legacy_complete_receipt(tmp_path: Path, monkeypatch) -> None:
    original_write = dbs.KeyedDatasetStore.write_records

    def lose_sell_side(store, records, **kwargs):
        return original_write(store, [r for r in records if r["metadata"]["normalized_row"]["side"] == "buy"], **kwargs)

    monkeypatch.setattr(dbs.KeyedDatasetStore, "write_records", lose_sell_side)
    expected = {str(1101 + i) for i in range(30)}
    body = _parquet(_session())
    first = _run(tmp_path, Fetcher(body), expected=expected)
    # The generated parquet has both sides, but only buy rows reached the valid keyed partition.
    assert first["status"] == "partial" and first["fresh"] is False
    assert first["rows_by_side"]["sell"] == 0 and first["coverage"]["single_sided_count"] == 30
    assert first["rows"] > first["partition_row_count"]
    assert dbs.keyed_dataset_store(tmp_path, "tw_broker_top").verify_partition(DAY)["verified"] is True
    # Simulate a complete receipt produced by the previous buggy writer. Digest verification alone
    # must not cause a skip; a repaired download fills the missing persisted sell keys.
    fbb._write_receipt(tmp_path, {**first, "trade_date": DAY, "status": "complete", "fresh": True})
    monkeypatch.setattr(dbs.KeyedDatasetStore, "write_records", original_write)
    again = Fetcher(body)
    repaired = _run(tmp_path, again, expected=expected)
    assert len(again.calls) == 1 and repaired["status"] == "complete"
    assert repaired["coverage"]["symbols_with_both_sides"] == 30
    assert repaired["rows_by_side"]["sell"] > 0


def test_persisted_partition_must_verify_before_freshness(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dbs.KeyedDatasetStore, "verify_partition",
                        lambda *args: {"verified": False, "reason": "manifest_mismatch", "keys": set()})
    result = _run(tmp_path, Fetcher(_parquet(_session())), expected={str(1101 + i) for i in range(30)})
    assert result["status"] == "partial" and result["fresh"] is False
    assert result["coverage"]["partition_verified"] is False
    assert "manifest_mismatch" in result["reason"]


def test_complete_receipt_revalidates_current_expected_symbols(tmp_path: Path) -> None:
    expected = {str(1101 + i) for i in range(30)}
    body = _parquet(_session())
    assert _run(tmp_path, Fetcher(body), expected=expected)["status"] == "complete"
    again = Fetcher(body)
    result = _run(tmp_path, again, expected=expected | {"9999"})
    assert len(again.calls) == 1 and result["status"] == "partial" and result["fresh"] is False
    assert result["coverage"]["missing_count"] == 1


def test_coverage_outside_tolerance_is_partial_and_never_fresh(tmp_path: Path) -> None:
    expected = {f"{1101 + s}" for s in range(40)}  # 10 of 40 traded symbols are absent from the file
    receipt = _run(tmp_path, Fetcher(_parquet(_session())), expected=expected)
    assert receipt["status"] == "partial" and receipt["fresh"] is False and receipt["fallback_required"] is False
    assert receipt["coverage"]["missing_count"] == 10 and receipt["coverage"]["expected_symbols"] == 40
    # The partial receipt is not accepted as complete by a rerun: the rerun fetches again.
    again = Fetcher(_parquet(_session()))
    assert _run(tmp_path, again, expected=expected)["status"] == "partial" and len(again.calls) == 1


def test_coverage_within_tolerance_and_minimum_symbols(tmp_path: Path, monkeypatch) -> None:
    expected = {f"{1101 + s}" for s in range(30)} | {"9999"}  # 1 of 31 missing (3.2 %) > 2 %
    assert _run(tmp_path, Fetcher(_parquet(_session())), expected=expected)["status"] == "partial"
    monkeypatch.setattr(fbb, "COVERAGE_MAX_MISSING_RATIO", 0.05)
    assert _run(tmp_path / "b", Fetcher(_parquet(_session())), expected=expected)["status"] == "complete"
    monkeypatch.setattr(fbb, "MIN_STORED_SYMBOLS", 1000)
    low = _run(tmp_path / "c", Fetcher(_parquet(_session())), expected=expected)
    assert low["status"] == "partial" and "30 stored" in low["reason"]


def test_expected_symbols_come_from_daily_bars_traded_shares(tmp_path: Path) -> None:
    bars = dbs.DailyBarsStore(tmp_path)
    recs = [SourceRecord(source_id=f"s-{sym}", connector_id="c", source_type="market", title="t", content_ref=f"r-{sym}",
                         metadata={"symbol": sym, "close": 10.0, "volume": vol, "market": "TW", "dataset": "tw_price_daily",
                                   "trade_date": DAY})
            for sym, vol in (("1101.TW", 1000), ("1102", 500), ("1103", 0), ("030001", 900), ("00878", 900))]
    bars.write_records(recs, ingest_run_id="r")
    assert fbb.traded_share_symbols(tmp_path, DAY) == {"1101", "1102"}
    assert fbb.traded_share_symbols(tmp_path, "2026-11-25") is None
    receipt = _run(tmp_path, Fetcher(_parquet(_session())))
    assert receipt["coverage"]["expected_symbols"] == 2 and receipt["status"] == "complete"


def test_no_daily_bars_means_coverage_unverified_partial(tmp_path: Path) -> None:
    receipt = _run(tmp_path, Fetcher(_parquet(_session())))
    assert receipt["status"] == "partial" and receipt["reason"].startswith("coverage_unverified")


@pytest.mark.parametrize("exc,code", [
    (FinMindQuotaError("FinMind API quota exceeded (HTTP 402)"), "quota_exhausted"),
])
def test_provider_refusal_requires_fallback_and_records_reason(tmp_path: Path, exc, code) -> None:
    receipt = _run(tmp_path, Fetcher(exc=exc))
    assert receipt["status"] == "provider_refused" and receipt["fallback_required"] is True
    assert receipt["provider_reason_code"] == code and receipt["requests_made"] == 1
    assert not (tmp_path / "keyed").exists()


def test_plan_refusal_json_body_keeps_provider_message_without_token(tmp_path: Path) -> None:
    body = json.dumps({"status": 400, "msg": f"Your level is register, need sponsorpro ({TOKEN})"}).encode()
    receipt = _run(tmp_path, Fetcher(body))
    assert receipt["status"] == "provider_refused" and receipt["fallback_required"] is True
    assert "sponsorpro" in receipt["reason"] and TOKEN not in json.dumps(receipt)
    stored = (tmp_path / "receipts/tw_broker_whole_market" / f"date={DAY}.json").read_text()
    assert TOKEN not in stored and json.loads(stored)["status"] == "provider_refused"


def test_no_credential_makes_no_request(tmp_path: Path) -> None:
    fetcher = Fetcher(token=False)
    receipt = _run(tmp_path, fetcher)
    assert receipt["status"] == "credential_unavailable" and receipt["fallback_required"] is True
    assert fetcher.calls == [] and receipt["requests_made"] == 0


def test_empty_whole_day_body_is_no_data_and_falls_back(tmp_path: Path) -> None:
    receipt = _run(tmp_path, Fetcher(json.dumps({"status": 200, "data": []}).encode()))
    assert receipt["status"] == "no_data" and receipt["fallback_required"] is True and receipt["fresh"] is False


def test_over_budget_session_publishes_nothing(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import replace

    monkeypatch.setitem(dbs.KEYED_DATASETS, "tw_broker_top",
                        replace(dbs.KEYED_DATASETS["tw_broker_top"], day_budget_bytes=500))
    receipt = _run(tmp_path, Fetcher(_parquet(_session())), expected=None)
    assert receipt["status"] == "failed" and "budget" in receipt["reason"]
    assert dbs.keyed_dataset_store(tmp_path, "tw_broker_top").records(DAY) == []


def test_declared_day_budget_holds_a_hosted_shaped_whole_market_session(tmp_path: Path) -> None:
    """2,600 symbols x 20 buy + 20 sell through the real writer stays inside the declared 6 MB budget."""
    decl = dbs.KEYED_DATASETS["tw_broker_top"]
    assert decl.day_budget_bytes == 6_000_000 and decl.retention_sessions == 750 and decl.retention_days is None
    assert decl.key_fields == ("symbol", "trade_date", "side", "rank")
    adapter = FinMindTaiwanBrokerDailyReportAdapter()
    rows = [{"symbol": f"{1000 + i // 40}", "date": DAY, "side": ("buy", "sell")[i % 2], "rank": (i // 2) % 20 + 1,
             "broker": f"券商{(i * 31) % 997:03d}-分公司", "broker_id": f"{9000 + (i * 31) % 997}",
             "buy_qty": 1000 + i % 9000, "sell_qty": 100 + i % 900, "net_qty": 900, "price_levels": 1 + i % 7,
             "available_time": "2026-11-26T10:05:00+00:00"} for i in range(2600 * 40)]
    store = dbs.keyed_dataset_store(tmp_path, "tw_broker_top")
    store.write_records(adapter.compact_keyed_records(rows, run_id="r"))
    size = store.read_manifest(DAY)["bytes"]
    assert size <= decl.day_budget_bytes
    with gzip.open(next((tmp_path / "keyed/tw_broker_top").rglob("*.jsonl.gz")), "rt", encoding="utf-8") as fh:
        assert sum(1 for _ in fh) == 2600 * 40


def test_receipts_are_pruned_past_the_retention(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(fbb, "RECEIPT_RETENTION_SESSIONS", 2)
    for day in ("2026-11-24", "2026-11-25", "2026-11-26"):
        fbb.run_whole_market_session(market_data_root=tmp_path, trade_date=day, fetcher=Fetcher(_parquet(_session(day=day))),
                                     expected_symbols=None)
    names = sorted(p.name for p in (tmp_path / "receipts/tw_broker_whole_market").glob("*.json"))
    assert names == ["date=2026-11-25.json", "date=2026-11-26.json"]


def test_per_symbol_records_no_longer_embed_raw_price_level_rows() -> None:
    rows = _session(n_symbols=1, branches=3, levels=4)
    recs = FinMindTaiwanBrokerDailyReportAdapter().records_from_daily_report_payload(rows, symbol="1101", trade_date=DAY)
    assert recs
    for rec in recs:
        assert "raw_rows" not in rec.metadata and "raw_rows" not in rec.metadata["body"]
        assert rec.metadata["price_levels"] == 4


def test_daily_cli_prints_one_receipt_without_token(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(fbb, "FinMindLiveFetcher", lambda: Fetcher(_parquet(_session())))
    rc = fbb.main(["daily", "--market-data-root", str(tmp_path), "--trade-date", DAY])
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0 and len(out) == 1
    receipt = json.loads(out[0])
    assert receipt["status"] == "partial" and receipt["requests_made"] == 1 and TOKEN not in out[0]
