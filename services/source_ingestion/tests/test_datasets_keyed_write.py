"""SOURCE-DATASETS-KEYED-WRITE-20261119: every 6A dataset is written once per key through the keyed writer."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pyarrow")

from services.source_ingestion.connectors import SourceConnector, SourceRecord
from services.source_ingestion.daily_bars_store import (
    KEYED_DATASETS,
    DailyBudgetError,
    KeyedDatasetStore,
    keyed_dataset_dir,
)
from services.source_ingestion.market_data_storage import (
    MarketDataStorageWriter,
    _compact_keyed_record,
    feature_files_enabled,
    keyed_dataset_reader_mode,
    keyed_datasets_write_mode,
    normalized_dataset_base,
)

DAY = "2026-11-13"


def _connector(**meta):
    return SourceConnector(connector_id="c1", source_type="market", provider="t", license_scope="internal", metadata=meta)


def _result(records, run_id="run-1"):
    return SimpleNamespace(records=records, run=SimpleNamespace(ingest_run_id=run_id))


# one sample normalized row per dataset: (row builder taking i and a value)
def _sample(dataset: str, i: int, value: float = 1.0) -> dict:
    base = {"dataset": dataset, "date": DAY, "event_time": f"{DAY}T00:00:00Z", "value": value,
            "available_time": "2026-11-13T10:00:00Z", "ingest_time": f"t{i}"}
    extra = {
        "tw_index_daily": {"index_code": f"IDX{i:02d}"}, "tw_index_ohlc_daily": {"index_code": f"IDX{i:02d}"},
        "tw_market_breadth_daily": {"market": f"M{i}"},
        "tw_institutional_flow": {"symbol": f"{1000 + i // 3}", "investor_class": ("foreign", "trust", "dealer")[i % 3]},
        "tw_margin_short_balance": {"symbol": f"{1000 + i}"}, "tw_broker_top": {"symbol": f"{1000 + i // 15}", "rank": i % 15 + 1},
        "tw_broker_branch_detail": {"symbol": f"{1000 + i // 45}", "broker_id": f"B{i // 3}", "branch_id": f"BR{i % 3}"},
        "taifex_ssf_contracts": {"contract_id": f"ssf:C{i}:202611"}, "taifex_tx_daily": {"contract_id": f"TX{i}"},
        "tw_macro_series": {"series_code": f"M{i:03d}", "date": "2026-10"},
        "tw_community_daily": {"symbol": f"{1000 + i // 2}", "platform": ("ptt", "dcard")[i % 2]},
        "collector_news": {"url": f"https://example.test/n/{i}"}, "collector_social_posts": {"url": f"https://example.test/p/{i}"},
    }[dataset]
    return {**base, **extra}


def _rec(dataset: str, i: int, value: float = 1.0, run: str = "run-1") -> SourceRecord:
    row = _sample(dataset, i, value)
    return SourceRecord(source_id=f"s-{dataset}-{i}-{run}", connector_id="c1", source_type="market", title="t",
                        content_ref=f"ref-{run}-{i}",
                        metadata={"dataset": dataset, "normalized_dataset": dataset, "normalized_row": row,
                                  "event_time": row["event_time"], "available_time": row["available_time"]})


def _keyed_files(root: Path, dataset: str) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in keyed_dataset_dir(root, dataset).rglob("part-*"))


def _keyed_rows(root: Path, dataset: str) -> list[dict]:
    store = KeyedDatasetStore(root, KEYED_DATASETS[dataset])
    return [r for part in store.partitions() for r in store.records(part)]


@pytest.mark.parametrize("dataset", sorted(KEYED_DATASETS))
def test_each_dataset_one_row_per_key_replace_and_unchanged_rerun_writes_nothing(tmp_path, dataset):
    decl = KEYED_DATASETS[dataset]
    assert decl.key_aliases and decl.day_budget_bytes and decl.partition_field in decl.key_fields
    writer = MarketDataStorageWriter(tmp_path)
    n = 6
    writer.write_run(result=_result([_rec(dataset, i) for i in range(n)], "run-1"), connector=_connector())
    files = _keyed_files(tmp_path, dataset)
    assert len(_keyed_rows(tmp_path, dataset)) == n and len(files) == 1
    mtimes = {f: (tmp_path / f).stat().st_mtime_ns for f in files}
    manifests = sorted((keyed_dataset_dir(tmp_path, dataset) / "_manifest").glob("date=*.json"))
    before = [m.read_bytes() for m in manifests]
    # a re-run (new run id, new volatile fields, same content) writes nothing
    writer.write_run(result=_result([_rec(dataset, i, run="run-2") for i in range(n)], "run-2"), connector=_connector())
    assert _keyed_files(tmp_path, dataset) == files
    assert {f: (tmp_path / f).stat().st_mtime_ns for f in files} == mtimes
    assert [m.read_bytes() for m in manifests] == before
    assert writer.keyed_counters["partitions_unchanged"] == 1
    # a changed value for one key replaces it; the row count stays n and nothing is appended after the old copy
    writer.write_run(result=_result([_rec(dataset, 2, value=9.0, run="run-3")], "run-3"), connector=_connector())
    rows = _keyed_rows(tmp_path, dataset)
    assert len(rows) == n
    assert sorted(r["metadata"]["normalized_row"]["value"] for r in rows) == [1.0] * (n - 1) + [9.0]
    assert len(_keyed_files(tmp_path, dataset)) == 1  # superseded generation removed
    assert writer.keyed_errors == {}


def test_record_without_a_key_part_is_rejected_and_counted_not_filed(tmp_path):
    rec = _rec("tw_margin_short_balance", 1)
    rec.metadata["normalized_row"].pop("symbol")
    rec.metadata["normalized_row"].pop("date"); rec.metadata["normalized_row"].pop("event_time")
    rec.metadata.pop("event_time")
    writer = MarketDataStorageWriter(tmp_path)
    writer.write_run(result=_result([rec]), connector=_connector())
    assert writer.keyed_counters["rejected_no_key"] == 1 and _keyed_rows(tmp_path, "tw_margin_short_balance") == []


def test_settings_default_and_unknown_values_fail_closed(monkeypatch):
    for name in ("PANTHEON_KEYED_DATASETS_WRITE", "PANTHEON_KEYED_DATASET_READER", "PANTHEON_FEATURE_FILES"):
        monkeypatch.delenv(name, raising=False)
    assert (keyed_datasets_write_mode(), keyed_dataset_reader_mode(), feature_files_enabled()) == ("dual", "run_files", True)
    for name, bad in (("PANTHEON_KEYED_DATASETS_WRITE", "both"), ("PANTHEON_KEYED_DATASET_READER", "x"), ("PANTHEON_FEATURE_FILES", "maybe")):
        monkeypatch.setenv(name, bad)
    with pytest.raises(ValueError):
        keyed_datasets_write_mode()
    with pytest.raises(ValueError):
        keyed_dataset_reader_mode()
    with pytest.raises(ValueError):
        feature_files_enabled()


def test_write_modes_run_files_dual_keyed_only(tmp_path, monkeypatch):
    ds = "tw_index_daily"
    for mode, run_files, keyed in (("run_files", True, False), ("dual", True, True), ("keyed_only", False, True)):
        root = tmp_path / mode
        monkeypatch.setenv("PANTHEON_KEYED_DATASETS_WRITE", mode)
        MarketDataStorageWriter(root).write_run(result=_result([_rec(ds, 1)]), connector=_connector())
        assert (root / "normalized" / ds).is_dir() is run_files
        assert keyed_dataset_dir(root, ds).is_dir() is keyed


def test_features_written_by_default_and_old_write_stoppable(tmp_path, monkeypatch):
    conn = _connector(feature_targets=["returns"])
    monkeypatch.delenv("PANTHEON_FEATURE_FILES", raising=False)
    manifest = MarketDataStorageWriter(tmp_path / "on").write_run(result=_result([_rec("tw_index_daily", 1)]), connector=conn)
    assert (tmp_path / "on" / "features" / "returns").is_dir() and manifest.feature_refs
    monkeypatch.setenv("PANTHEON_FEATURE_FILES", "off")
    manifest = MarketDataStorageWriter(tmp_path / "off").write_run(result=_result([_rec("tw_index_daily", 1)]), connector=conn)
    assert not (tmp_path / "off" / "features").exists() and not manifest.feature_refs


def test_connector_storage_targets_name_no_features_path():
    from services.source_ingestion.active_universe import DEFAULT_SOURCE_UPDATE_RULES
    from services.source_ingestion.connector_definitions import get_connector_definition

    for rule in DEFAULT_SOURCE_UPDATE_RULES:
        assert not [t for t in (rule.metadata or {}).get("storage_targets", ()) if str(t).startswith("features/")]
    defn = get_connector_definition("tw-twse-tpex-official-market")
    assert defn is not None and not [t for t in defn.metadata["storage_targets"] if t.startswith("features/")]


# -- readers: same values before and after, both settings ---------------------------------------
def test_ssf_reader_returns_the_same_rows_from_run_files_and_from_keyed_store(tmp_path, monkeypatch):
    from services.execution.lean_runtime import instrument_candidates as ic
    from services.source_ingestion.connectors.taiwan_official import taifex_ssf_normalized_rows

    fx = Path(__file__).resolve().parents[2] / "execution" / "lean_runtime" / "fixtures" / "taifex_20261106"
    load = lambda n: json.loads((fx / f"{n}.json").read_text(encoding="utf-8"))  # noqa: E731
    payload = {k: load(k) for k in ("SingleStockFuturesMargining", "SingleStockFuturesETFMargining",
                                    "DailyMarketReportFut", "FinalSettlementPriceSSF")}
    payload["stockLists"] = (fx / "stockLists.html").read_text(encoding="utf-8")
    rows = [dict(r) for r in taifex_ssf_normalized_rows(payload)]
    recs = [SourceRecord(source_id=f"s{i}", connector_id="c1", source_type="market", title="t", content_ref="r",
                         metadata={"dataset": "taifex_ssf_contracts", "normalized_row": r}) for i, r in enumerate(rows)]
    MarketDataStorageWriter(tmp_path).write_run(result=_result(recs), connector=_connector())
    monkeypatch.setenv("PANTHEON_KEYED_DATASET_READER", "run_files")
    old = ic.read_normalized_ssf_rows(tmp_path)
    monkeypatch.setenv("PANTHEON_KEYED_DATASET_READER", "keyed")
    new = ic.read_normalized_ssf_rows(tmp_path)
    key = lambda r: (r["contract_id"], r["date"])  # noqa: E731
    assert old and sorted(new, key=key) == sorted(old, key=key)
    assert len(_keyed_rows(tmp_path, "taifex_ssf_contracts")) == len(rows) == 30


def test_regime_observations_equal_from_run_files_and_keyed_store(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    from services.research.regime.features import load_governed_observations

    rows = [{"dataset": "tw_index_daily", "index_code": "TAIEX", "date": f"2026-11-{d:02d}", "event_time": f"2026-11-{d:02d}T00:00:00Z",
             "close": 20000.0 + d, "available_time": f"2026-11-{d:02d}T10:00:00Z"} for d in range(2, 14)]
    recs = [SourceRecord(source_id=f"i{d}", connector_id="c1", source_type="market", title="t", content_ref="r",
                         metadata={"dataset": "tw_index_daily", "normalized_row": r, "event_time": r["event_time"]})
            for d, r in enumerate(rows)]
    MarketDataStorageWriter(tmp_path).write_run(result=_result(recs), connector=_connector())
    monkeypatch.setenv("PANTHEON_KEYED_DATASET_READER", "run_files")
    old, old_notes = load_governed_observations(tmp_path)
    monkeypatch.setenv("PANTHEON_KEYED_DATASET_READER", "keyed")
    new, new_notes = load_governed_observations(tmp_path)
    ident = lambda o: json.dumps(o, sort_keys=True, default=str)  # noqa: E731
    assert old_notes["tw_index_daily"]["rows"] == new_notes["tw_index_daily"]["rows"] == len(rows)
    assert sorted(map(ident, old)) == sorted(map(ident, new))


def test_candidate_pool_feed_partitions_follow_the_reader_setting(tmp_path, monkeypatch):
    from services.source_ingestion.candidate_pool_market_feed import _partitions

    MarketDataStorageWriter(tmp_path).write_run(result=_result([_rec("tw_margin_short_balance", 1)]), connector=_connector())
    monkeypatch.setenv("PANTHEON_KEYED_DATASET_READER", "run_files")
    assert [p.name for p in _partitions(tmp_path, "tw_margin_short_balance")] == ["run-1.jsonl"]
    monkeypatch.setenv("PANTHEON_KEYED_DATASET_READER", "keyed")
    assert [p.name for p in _partitions(tmp_path, "tw_margin_short_balance")] == ["part-g1.jsonl.gz"]
    assert normalized_dataset_base(tmp_path, "tw_price_daily") == tmp_path / "normalized" / "tw_price_daily"  # undeclared


# -- rollback: the previous reader still reads what the new writer wrote ---------------------------
def test_previous_reader_reads_run_files_unchanged_after_dual_write_and_keyed_tree_is_additive(tmp_path):
    MarketDataStorageWriter(tmp_path).write_run(result=_result([_rec("tw_index_daily", i) for i in range(3)]), connector=_connector())
    old_file = tmp_path / "normalized" / "tw_index_daily" / f"date={DAY}" / "run-1.jsonl"
    lines = [json.loads(x) for x in old_file.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3 and all("normalized_row" in x["metadata"] for x in lines)  # format unchanged
    with gzip.open(next(keyed_dataset_dir(tmp_path, "tw_index_daily").rglob("part-*.jsonl.gz")), "rt", encoding="utf-8") as h:
        stored = [json.loads(x) for x in h]
    assert all({"source_id", "connector_id", "dataset", "metadata"} <= set(x) for x in stored)  # new reader reads same shape
    # and the keyed store written by the new writer is read by the new reader even when old files are gone
    old_file.unlink()
    assert len(_keyed_rows(tmp_path, "tw_index_daily")) == 3


# -- growth budget, measured on hosted-shaped volume, enforced on bytes ----------------------------
HOSTED_ROWS = {  # section 6A row counts
    "tw_index_daily": 40, "tw_index_ohlc_daily": 40, "tw_market_breadth_daily": 2, "tw_institutional_flow": 1_900 * 3,
    "tw_margin_short_balance": 1_900, "tw_broker_top": 200 * 15, "tw_broker_branch_detail": 200 * 15 * 3,
    "taifex_ssf_contracts": 600, "taifex_tx_daily": 4, "tw_macro_series": 100, "tw_community_daily": 2_000 * 2,
    "collector_news": 45, "collector_social_posts": 30,
}


def _hosted_records(dataset: str, n: int) -> list[dict]:
    out = []
    for i in range(n):
        row = _sample(dataset, i, float(i % 997) + 0.5)
        row.update({"a": i % 977, "b": 300 + i % 97, "c": (i % 211) - 100})
        if dataset.startswith("collector_"):
            row["body"] = f"{i:04d} " + "x" * 3_500  # about 20 MB per day raw upper bound in the design
        rec = SourceRecord(source_id=f"s{i}", connector_id="c1", source_type="market", title="t", content_ref=f"r{i}",
                           metadata={"dataset": dataset, "normalized_row": row, "event_time": row["event_time"],
                                     "available_time": row["available_time"]})
        out.append(_compact_keyed_record(rec, dataset))  # the shape the writer stores
    return out


@pytest.mark.parametrize("dataset", sorted(HOSTED_ROWS))
def test_hosted_shaped_day_is_within_the_section_6a_budget(tmp_path, dataset):
    store = KeyedDatasetStore(tmp_path, KEYED_DATASETS[dataset])
    for start in range(0, HOSTED_ROWS[dataset], 500):  # collector batches of about 500 records
        store.write_records(_hosted_records(dataset, HOSTED_ROWS[dataset])[start:start + 500], ingest_run_id="r")
    report = store.budget_report()
    assert report["max_day_bytes"] <= KEYED_DATASETS[dataset].day_budget_bytes, report
    assert report["alarms"] == [] and len(_keyed_rows(tmp_path, dataset)) == HOSTED_ROWS[dataset]
    print(f"BUDGET {dataset} rows={HOSTED_ROWS[dataset]} day_bytes={report['max_day_bytes']} limit={report['day_budget_bytes']}")


def test_byte_limit_is_enforced_and_nothing_is_published_when_exceeded(tmp_path):
    from dataclasses import replace

    decl = replace(KEYED_DATASETS["tw_margin_short_balance"], day_budget_bytes=2_000)
    store = KeyedDatasetStore(tmp_path, decl)
    with pytest.raises(DailyBudgetError):
        store.write_records(_hosted_records("tw_margin_short_balance", 1_900), ingest_run_id="r")
    assert store.partitions() == [] and _keyed_files(tmp_path, "tw_margin_short_balance") == []


def test_retention_is_declared_next_to_the_key_as_in_section_6a():
    assert {d: KEYED_DATASETS[d].retention_days for d in KEYED_DATASETS} == {
        "tw_index_daily": 2555, "tw_index_ohlc_daily": 2555, "tw_market_breadth_daily": 2555, "tw_institutional_flow": 2555,
        "tw_margin_short_balance": 2555, "tw_broker_top": 750, "tw_broker_branch_detail": 250, "taifex_ssf_contracts": 750,
        "taifex_tx_daily": 2555, "tw_macro_series": None, "tw_community_daily": 180, "collector_news": 365,
        "collector_social_posts": 365}


def test_crash_before_manifest_rename_leaves_the_previous_set_readable(tmp_path):
    decl = KEYED_DATASETS["tw_margin_short_balance"]
    KeyedDatasetStore(tmp_path, decl).write_records(_hosted_records("tw_margin_short_balance", 5), ingest_run_id="r1")

    def boom(step):
        if step == "before_manifest_rename":
            raise RuntimeError("killed")

    changed = _hosted_records("tw_margin_short_balance", 5)
    changed[0]["metadata"]["normalized_row"]["a"] = -1
    with pytest.raises(RuntimeError):
        KeyedDatasetStore(tmp_path, decl, fault=boom).write_records(changed, ingest_run_id="r2")
    rows = _keyed_rows(tmp_path, "tw_margin_short_balance")
    assert len(rows) == 5 and all(r["metadata"]["normalized_row"]["a"] != -1 for r in rows)
