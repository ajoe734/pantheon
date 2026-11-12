"""K2: partitioned gz-JSONL store, idempotency, quarantine, pruner, disk guard (SD §4)."""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from services.source_ingestion.intraday_store import (
    DiskBudgetExhausted,
    IntradayStore,
    IntradayStoreError,
    project_remaining_bytes,
)

DAY = "2026-11-10"
GB = 1024**3


def _rows(symbol="2330", day=DAY, n=3, close=100.5):
    return [{"symbol": symbol, "session_date": day, "bar_start": f"{day}T09:0{i}:00+08:00",
             "bar_end": f"{day}T09:0{i + 1}:00+08:00", "open": 100.0, "high": 101.0, "low": 99.0,
             "close": close, "volume": 10 + i, "fetched_at": "2026-11-10T08:00:00Z"} for i in range(n)]


def _store(tmp_path, free=1000 * GB, **kw):
    return IntradayStore(tmp_path, usage_fn=lambda p: SimpleNamespace(free=free), **kw)


def test_layout_manifest_and_sorted_shard(tmp_path):
    store = _store(tmp_path)
    res = store.put_symbol_day("2330", DAY, list(reversed(_rows())), raw_envelope={"raw_rows": [{"a": 1}], "response_content_hash": "h"})
    assert res.status == "written" and res.row_count == 3
    shard = tmp_path / "normalized/tw_price_1m" / DAY / "2330.jsonl.gz"
    with gzip.open(shard, "rt") as fh:
        starts = [json.loads(line)["bar_start"] for line in fh]
    assert starts == sorted(starts)
    manifest = json.loads((tmp_path / "normalized/tw_price_1m" / DAY / "manifest.json").read_text())
    assert manifest["symbols"]["2330"]["row_count"] == 3 and manifest["row_count"] == 3
    assert manifest["content_hash"] and manifest["symbols"]["2330"]["content_hash"]
    assert (tmp_path / "raw/tw_price_1m" / DAY / "2330.raw.jsonl.gz").exists()


def test_identical_refetch_is_noop_even_with_new_fetch_stamp(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _rows())
    shard = store.shard_path("2330", DAY)
    manifest_before = store.manifest_path(DAY).read_bytes()
    mtime = shard.stat().st_mtime_ns
    again = [{**r, "fetched_at": "2026-11-11T08:00:00Z"} for r in _rows()]
    res = store.put_symbol_day("2330", DAY, again)
    assert res.status == "unchanged"
    assert shard.stat().st_mtime_ns == mtime and store.manifest_path(DAY).read_bytes() == manifest_before


def test_new_bars_added_existing_untouched(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _rows(n=2))
    res = store.put_symbol_day("2330", DAY, _rows(n=4))
    assert res.status == "added" and res.new_rows == 2 and len(store.read_rows("2330", DAY)) == 4


def test_conflicting_content_quarantines_and_never_overwrites(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _rows())
    res = store.put_symbol_day("2330", DAY, _rows(close=999.0))
    assert res.status == "quarantined"
    q = json.loads(store.quarantine_path("2330", DAY).read_text())
    assert q["reason"] == "conflicting_content_for_existing_key" and q["resolved_at"] is None
    assert store.read_rows("2330", DAY) == []  # invisible to readers
    with gzip.open(store.shard_path("2330", DAY), "rt") as fh:  # original bytes preserved
        assert all(json.loads(l)["close"] == 100.5 for l in fh)
    assert store.put_symbol_day("2330", DAY, _rows()).status == "quarantined"  # stays isolated
    assert store.is_symbol_day_complete("2330", DAY)


def test_conflict_inside_one_batch_quarantines(tmp_path):
    store = _store(tmp_path)
    rows = _rows(n=1) + _rows(n=1, close=5.0)
    assert store.put_symbol_day("2330", DAY, rows).status == "quarantined"
    assert not store.shard_path("2330", DAY).exists()


def test_empty_day_and_non_trading_day_are_resumable_states(tmp_path):
    store = _store(tmp_path)
    assert store.put_symbol_day("2330", DAY, []).status == "provider_empty"
    assert store.mark_non_trading_day("2317", DAY).status == "non_trading_day"
    assert store.is_symbol_day_complete("2330", DAY) and store.is_symbol_day_complete("2317", DAY)
    assert store.read_rows("2330", DAY) == []


def test_rejects_path_escape_and_foreign_rows(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(IntradayStoreError):
        store.put_symbol_day("../x", DAY, _rows())
    with pytest.raises(IntradayStoreError):
        store.put_symbol_day("2330", DAY, _rows(symbol="2317"))


def test_disk_guard_requires_ten_x_and_20gb_and_deletes_nothing(tmp_path):
    other = tmp_path / "normalized" / "tw_price_daily" / "2026-01-01"
    other.mkdir(parents=True)
    (other / "keep.json").write_text("{}")
    projected = project_remaining_bytes(98_000)
    assert 10 * projected < 20 * GB  # floor is the binding limit at full-backfill scale
    _store(tmp_path, free=20 * GB).check_disk_budget(98_000)
    with pytest.raises(DiskBudgetExhausted) as exc:
        _store(tmp_path, free=20 * GB - 1).check_disk_budget(98_000)
    assert exc.value.reason == "disk_budget_exhausted"
    big = 10 * project_remaining_bytes(10_000_000_000)
    with pytest.raises(DiskBudgetExhausted):
        _store(tmp_path, free=big - 1).check_disk_budget(10_000_000_000)
    assert (other / "keep.json").exists()


def test_prune_deletes_only_this_dataset_old_days_and_logs(tmp_path):
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    store = _store(tmp_path, clock=lambda: now)
    old, new = "2024-11-01", "2026-11-10"
    for d in (old, new):
        store.put_symbol_day("2330", d, _rows(day=d), raw_envelope={"raw_rows": [], "response_content_hash": "h"})
    daily = tmp_path / "normalized/tw_price_daily/2020-01-01"
    daily.mkdir(parents=True)
    (daily / "x.json").write_text("{}")
    unmanifested = tmp_path / "normalized/tw_price_1m/2023-01-02"
    unmanifested.mkdir(parents=True)
    out = store.prune(today=date(2026, 11, 12))
    assert [d["session_date"] for d in out["deleted"]] == [old]
    assert not (tmp_path / "normalized/tw_price_1m" / old).exists()
    assert not (tmp_path / "raw/tw_price_1m" / old).exists()
    assert (tmp_path / "normalized/tw_price_1m" / new).exists()
    assert unmanifested.exists() and out["skipped"][0]["reason"] == "no_manifest"
    assert (daily / "x.json").exists()
    log = [json.loads(l) for l in (tmp_path / "_logs/tw_price_1m_prune.jsonl").read_text().splitlines()]
    assert {e["kind"] for e in log} == {"normalized", "raw"} and all(e["manifest_content_hash"] for e in log)


def test_quarantine_kept_until_resolved_then_expires_after_90_days(tmp_path):
    t = {"now": datetime(2026, 11, 12, tzinfo=timezone.utc)}
    store = _store(tmp_path, clock=lambda: t["now"])
    store.put_symbol_day("2330", DAY, _rows())
    store.put_symbol_day("2330", DAY, _rows(close=9.0))
    store.prune(today=date(2030, 1, 1))  # unresolved: never deleted, not even by age
    assert store.quarantine_path("2330", DAY).exists()
    with pytest.raises(IntradayStoreError):
        store.resolve_quarantine("2330", DAY, resolution="")
    store.resolve_quarantine("2330", DAY, resolution="refetched_clean")
    t["now"] = datetime(2027, 2, 9, tzinfo=timezone.utc)  # +89 days
    store.prune(today=date(2027, 2, 9))
    assert store.quarantine_path("2330", DAY).exists()
    t["now"] = datetime(2027, 2, 11, tzinfo=timezone.utc)  # +91 days
    out = store.prune(today=date(2027, 2, 11))
    assert not store.quarantine_path("2330", DAY).exists()
    assert out["deleted"][0]["kind"] == "resolved_quarantine"
