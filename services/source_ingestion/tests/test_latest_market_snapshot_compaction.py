from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import threading
from typing import Any

import pytest

from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.requirement_state import (
    COLLECTOR_CLOSE_KEY,
    DAILY_SNAPSHOT_DATASET,
    LatestMarketSnapshotStore,
    MARKET_SNAPSHOT_COMPACT_MAX_BYTES,
    MARKET_SNAPSHOT_COMPACT_RATIO_THRESHOLD,
    MINUTE_SNAPSHOT_DATASET,
    MarketSnapshotStateError,
)
from services.source_ingestion.tests.frozen_source_codec import (
    BEE_REF,
    NEW70A_REF,
    frozen_source_codec,
)


def _daily_record(
    symbol: str,
    *,
    day_index: int,
    close: float,
    source_id: str | None = None,
    connector_id: str = "twse-daily",
    provisional: bool = False,
    calendar_evidence: dict[str, Any] | None = None,
) -> SourceRecord:
    effective_source_id = source_id or (
        "collector-tw-twse-price-history" if provisional else "official-daily-src"
    )
    trade_dt = datetime(2026, 10, 1, 5, 30, tzinfo=timezone.utc) + timedelta(days=day_index)
    event_time = trade_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    trade_date = trade_dt.strftime("%Y-%m-%d")
    metadata: dict[str, Any] = {
        "symbol": symbol,
        "event_time": event_time,
        "close": close,
        "market": "TW",
        "dataset": DAILY_SNAPSHOT_DATASET,
    }
    if calendar_evidence is not None:
        metadata["calendar_evidence"] = calendar_evidence
    if provisional:
        metadata[COLLECTOR_CLOSE_KEY] = {
            "collector_source_id": effective_source_id,
            "exchange_endpoint": "https://www.twse.com.tw/exchangeReport/MI_INDEX",
            "bundle_sha256": f"{day_index:064x}",
            "trade_date": trade_date,
            "provisional": True,
            "official": False,
        }
    return SourceRecord(
        source_id=effective_source_id,
        connector_id=connector_id,
        source_type="market",
        title=f"{symbol} close {trade_date}",
        content_ref=f"mem://source/{symbol}/{trade_date}",
        metadata=metadata,
        created_at=event_time,
    )


def _intraday_record(
    symbol: str,
    *,
    step_index: int,
    close: float,
    dataset: str = MINUTE_SNAPSHOT_DATASET,
) -> SourceRecord:
    bar_dt = datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc) + timedelta(minutes=step_index)
    event_time = bar_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return SourceRecord(
        source_id=f"intraday-{symbol}-{step_index}",
        connector_id="tw-intraday-collector",
        source_type="market",
        title=f"{symbol} 1m {event_time}",
        content_ref=f"mem://intraday/{symbol}/{step_index}",
        metadata={
            "symbol": symbol,
            "event_time": event_time,
            "close": close,
            "market": "TW",
            "dataset": dataset,
        },
        created_at=event_time,
    )


def test_compaction_triggers_past_ratio_threshold_and_preserves_withdrawals(
    tmp_path: Path,
) -> None:
    path = tmp_path / "latest-market-snapshots.jsonl"
    store = LatestMarketSnapshotStore(path)
    assert store.compact_ratio_threshold == MARKET_SNAPSHOT_COMPACT_RATIO_THRESHOLD == 4
    assert store.compact_max_bytes == MARKET_SNAPSHOT_COMPACT_MAX_BYTES == 32 * 1024 * 1024

    # Seed two keys across daily and 1m datasets, then withdraw the 1m key so its latest
    # journal line is a withdrawal tombstone (`"withdrawn": true`).
    store.append_normalized_records(
        [_intraday_record("2317.TWSE", step_index=0, close=180.0)],
        ingest_run_id="run-intraday-0",
        observed_at="2026-11-15T01:05:00Z",
        dataset=MINUTE_SNAPSHOT_DATASET,
    )
    assert store.withdraw_sessions(
        "2317.TWSE",
        MINUTE_SNAPSHOT_DATASET,
        ["2026-11-15"],
        observed_at="2026-11-15T01:06:00Z",
    )

    line_counts: list[int] = []
    # Repeatedly append new trading days to 2330.TWSE until the journal exceeds 4x the
    # live content size (2 live keys: 2330.TWSE daily + 2317.TWSE 1m withdrawal tombstone).
    for day_idx in range(1, 14):
        store.append_normalized_records(
            [
                _daily_record(
                    "2330.TWSE",
                    day_index=day_idx,
                    close=1000.0 + day_idx,
                    calendar_evidence={
                        "exchange": "TWSE",
                        "calendar_version": "2026.1",
                        "source_hash": "b" * 64,
                    },
                )
            ],
            ingest_run_id=f"run-daily-{day_idx}",
            observed_at=f"2026-11-15T02:{day_idx:02d}:00Z",
        )
        raw_lines = [
            line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        line_counts.append(len(raw_lines))

    # The journal grew past multiple lines before compacting down to the 2 latest key lines.
    assert max(line_counts) > 4
    assert min(line_counts[3:]) == 2

    # Force a final compaction and verify exact 1-line-per-key output including the withdrawal tombstone.
    store.compact(force=True)
    final_lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(final_lines) == 2
    envelopes = [json.loads(line) for line in final_lines]
    by_key = {
        (env["state"]["symbol"], env["state"].get("dataset", DAILY_SNAPSHOT_DATASET)): env
        for env in envelopes
    }
    assert set(by_key) == {
        ("2317.TWSE", MINUTE_SNAPSHOT_DATASET),
        ("2330.TWSE", DAILY_SNAPSHOT_DATASET),
    }
    assert by_key[("2317.TWSE", MINUTE_SNAPSHOT_DATASET)]["state"]["withdrawn"] is True
    assert "points" not in by_key[("2317.TWSE", MINUTE_SNAPSHOT_DATASET)]["state"]
    assert by_key[("2330.TWSE", DAILY_SNAPSHOT_DATASET)]["state"]["points"][-1]["close"] == 1013.0

    reloaded = LatestMarketSnapshotStore(path)
    assert reloaded.get("2317.TWSE", dataset=MINUTE_SNAPSHOT_DATASET) is None
    snap_2330 = reloaded.get("2330.TWSE")
    assert snap_2330 is not None
    assert len(snap_2330.points) == 13
    assert snap_2330.points[-1].close == 1013.0


def test_compaction_triggers_on_absolute_max_bytes_threshold(tmp_path: Path) -> None:
    path = tmp_path / "latest-market-snapshots.jsonl"
    # Set a high ratio threshold so only the absolute byte threshold triggers compaction.
    store = LatestMarketSnapshotStore(
        path,
        compact_ratio_threshold=1000,
        compact_max_bytes=1200,
    )
    store.append_normalized_records(
        [_daily_record("2330.TWSE", day_index=1, close=1001.0)],
        ingest_run_id="run-1",
        observed_at="2026-11-15T03:01:00Z",
    )
    first_size = path.stat().st_size
    assert first_size < 1200

    # Steady-pull updates for the same trading session append new lines of ~722 bytes each;
    # once total bytes exceed 1200, compaction rewrites the file back to 1 line (<1200 bytes).
    for step in (2, 3):
        store.append_normalized_records(
            [_daily_record("2330.TWSE", day_index=1, close=1000.0 + step)],
            ingest_run_id=f"run-{step}",
            observed_at=f"2026-11-15T03:0{step}:00Z",
        )
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert path.stat().st_size == first_size < 1200


def test_compaction_preserves_checksum_lineage_snapshot_id_and_get_results(
    tmp_path: Path,
) -> None:
    path = tmp_path / "latest-market-snapshots.jsonl"
    # Disable auto-compaction while building a multi-record journal with collector lineage,
    # official reconciliation, calendar evidence, intraday bars, and a withdrawal tombstone.
    store = LatestMarketSnapshotStore(path, compact_ratio_threshold=100)
    calendar_ev = {
        "exchange": "TWSE",
        "calendar_version": "2026.11",
        "source_hash": "c" * 64,
    }
    store.append_normalized_records(
        [
            _daily_record(
                "2330.TWSE",
                day_index=1,
                close=995.0,
                source_id="collector-tw-twse-price-history",
                provisional=True,
            ),
            _daily_record(
                "2454.TWSE",
                day_index=1,
                close=1250.0,
                calendar_evidence=calendar_ev,
            ),
        ],
        ingest_run_id="run-provisional",
        observed_at="2026-11-15T04:00:00Z",
    )
    # Reconcile 2330.TWSE day 1 with an official close that differs from the provisional close,
    # and add day 2 provisional close.
    store.append_normalized_records(
        [
            _daily_record(
                "2330.TWSE",
                day_index=1,
                close=1000.0,
                source_id="official-twse-mi-index",
                calendar_evidence=calendar_ev,
            ),
            _daily_record(
                "2330.TWSE",
                day_index=2,
                close=1015.0,
                source_id="collector-tw-twse-price-history",
                provisional=True,
            ),
        ],
        ingest_run_id="run-official-reconcile",
        observed_at="2026-11-15T04:05:00Z",
    )
    store.append_normalized_records(
        [
            _intraday_record("2330.TWSE", step_index=1, close=1012.0),
            _intraday_record("2317.TWSE", step_index=1, close=190.0),
        ],
        ingest_run_id="run-intraday",
        observed_at="2026-11-15T04:10:00Z",
        dataset=MINUTE_SNAPSHOT_DATASET,
    )
    assert store.withdraw_sessions(
        "2317.TWSE",
        MINUTE_SNAPSHOT_DATASET,
        ["2026-11-15"],
        observed_at="2026-11-15T04:12:00Z",
    )

    lines_before = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines_before) == 6

    before_2330_daily = store.get("2330.TWSE", dataset=DAILY_SNAPSHOT_DATASET)
    before_2454_daily = store.get("2454.TWSE", dataset=DAILY_SNAPSHOT_DATASET)
    before_2330_1m = store.get("2330.TWSE", dataset=MINUTE_SNAPSHOT_DATASET)
    before_2317_1m = store.get("2317.TWSE", dataset=MINUTE_SNAPSHOT_DATASET)
    assert before_2330_daily is not None
    assert before_2454_daily is not None
    assert before_2330_1m is not None
    assert before_2317_1m is None

    # Compact and verify every live snapshot and its serialized envelope (including checksum,
    # collector_close lineage, close_reconciliation, calendar_evidence, and snapshot_id).
    assert store.compact(force=True) is True

    lines_after = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines_after) == 4
    # Every line in the compacted journal must be byte-for-byte identical to the latest line
    # for that (symbol, dataset) key in the pre-compaction journal.
    assert set(lines_after).issubset(set(lines_before))

    reloaded = LatestMarketSnapshotStore(path)
    after_2330_daily = reloaded.get("2330.TWSE", dataset=DAILY_SNAPSHOT_DATASET)
    after_2454_daily = reloaded.get("2454.TWSE", dataset=DAILY_SNAPSHOT_DATASET)
    after_2330_1m = reloaded.get("2330.TWSE", dataset=MINUTE_SNAPSHOT_DATASET)
    after_2317_1m = reloaded.get("2317.TWSE", dataset=MINUTE_SNAPSHOT_DATASET)

    assert after_2330_daily == before_2330_daily
    assert after_2330_daily is not None
    assert after_2330_daily.snapshot_id == before_2330_daily.snapshot_id
    assert after_2330_daily.to_public_dict() == before_2330_daily.to_public_dict()
    assert after_2330_daily.close_reconciliation == before_2330_daily.close_reconciliation
    assert len(after_2330_daily.close_reconciliation) == 1
    assert after_2330_daily.points[-1].collector_close is not None
    assert after_2330_daily.calendar_evidence == calendar_ev

    assert after_2454_daily == before_2454_daily
    assert after_2454_daily is not None
    assert after_2454_daily.snapshot_id == before_2454_daily.snapshot_id

    assert after_2330_1m == before_2330_1m
    assert after_2330_1m is not None
    assert after_2330_1m.snapshot_id == before_2330_1m.snapshot_id

    assert after_2317_1m is None


def test_compaction_crash_before_replace_leaves_intact_journal_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "latest-market-snapshots.jsonl"
    store = LatestMarketSnapshotStore(path)

    # Append 4 updates for the same trading session (each line ~480 bytes; 4 lines = 4.0x,
    # still <= 4x threshold).
    for pull_idx in range(1, 5):
        store.append_normalized_records(
            [_daily_record("2330.TWSE", day_index=1, close=1000.0 + pull_idx)],
            ingest_run_id=f"run-{pull_idx}",
            observed_at=f"2026-11-15T05:0{pull_idx}:00Z",
        )
    before_bytes = path.read_bytes()
    before_lines = [
        line for line in before_bytes.decode("utf-8").splitlines() if line.strip()
    ]
    assert len(before_lines) == 4
    expected_before_5 = store.get("2330.TWSE")
    assert expected_before_5 is not None

    # Simulate an OS failure right at os.replace when the 5th steady-pull append exceeds 4x
    # and triggers compaction.
    def _boom_replace(src: os.PathLike[str] | str, dst: os.PathLike[str] | str) -> None:
        raise OSError("simulated crash before atomic replace")

    monkeypatch.setattr(os, "replace", _boom_replace)

    with pytest.raises(MarketSnapshotStateError, match="market snapshot append failed"):
        store.append_normalized_records(
            [_daily_record("2330.TWSE", day_index=1, close=1005.0)],
            ingest_run_id="run-5",
            observed_at="2026-11-15T05:05:00Z",
        )

    # No temporary compaction file remains on disk, and the journal is cleanly readable
    # with the latest 5th record already fsynced in the pre-compaction file.
    temp_files = list(tmp_path.glob("*.tmp"))
    assert temp_files == []

    recovered = LatestMarketSnapshotStore(path)
    snap = recovered.get("2330.TWSE")
    assert snap is not None
    assert len(snap.points) == 1
    assert snap.points[-1].close == 1005.0

    # Also verify a failure during explicit compact() before os.replace leaves the file
    # byte-identical to its pre-compact state.
    pre_explicit_bytes = path.read_bytes()
    with pytest.raises(MarketSnapshotStateError, match="market snapshot compaction failed"):
        recovered.compact(force=True)
    assert path.read_bytes() == pre_explicit_bytes
    assert list(tmp_path.glob("*.tmp")) == []


def test_compacted_journal_rollback_reader_equivalence_across_bee62bc_and_70a5bb8(
    tmp_path: Path,
) -> None:
    # 1. Standard official daily journal compacted -> current, bee62bc, and 70a5bb8 readers
    # must all load the exact same latest-per-key projection and snapshot_id values.
    official_path = tmp_path / "official-compacted.jsonl"
    official_store = LatestMarketSnapshotStore(official_path)
    calendar_ev = {
        "exchange": "TWSE",
        "calendar_version": "2026.11",
        "source_hash": "d" * 64,
    }
    for day_idx in range(1, 7):
        official_store.append_normalized_records(
            [
                _daily_record(
                    "2330.TWSE",
                    day_index=day_idx,
                    close=1000.0 + day_idx,
                    calendar_evidence=calendar_ev,
                ),
                _daily_record(
                    "2317.TWSE",
                    day_index=day_idx,
                    close=200.0 + day_idx,
                    calendar_evidence=calendar_ev,
                ),
            ],
            ingest_run_id=f"official-{day_idx}",
            observed_at=f"2026-11-15T06:0{day_idx}:00Z",
        )
    official_store.compact(force=True)
    compacted_official_lines = [
        line for line in official_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(compacted_official_lines) == 2

    current_official = {
        symbol: official_store.get(symbol).to_public_dict()  # type: ignore[union-attr]
        for symbol in ("2317.TWSE", "2330.TWSE")
    }
    for ref in (BEE_REF, NEW70A_REF):
        historical = frozen_source_codec(ref)
        hist_store = historical.LatestMarketSnapshotStore(official_path)
        hist_projection = {
            symbol: hist_store.get(symbol).to_public_dict()
            for symbol in ("2317.TWSE", "2330.TWSE")
        }
        assert hist_projection == current_official

    # 2. Extended-v1 journal (provisional collector close, close reconciliation, intraday,
    # and withdrawal tombstone) compacted -> current and 70a5bb8 readers must match identically.
    extended_path = tmp_path / "extended-compacted.jsonl"
    extended_store = LatestMarketSnapshotStore(extended_path)
    for day_idx in range(1, 6):
        extended_store.append_normalized_records(
            [
                _daily_record(
                    "2330.TWSE",
                    day_index=day_idx,
                    close=990.0 + day_idx,
                    source_id="collector-tw-twse-price-history",
                    provisional=True,
                ),
                _daily_record(
                    "2330.TWSE",
                    day_index=day_idx,
                    close=1000.0 + day_idx,
                    source_id="official-twse",
                    calendar_evidence=calendar_ev,
                ),
            ],
            ingest_run_id=f"ext-daily-{day_idx}",
            observed_at=f"2026-11-15T07:0{day_idx}:00Z",
        )
        extended_store.append_normalized_records(
            [
                _intraday_record("2330.TWSE", step_index=day_idx, close=1000.0 + day_idx),
                _intraday_record("2317.TWSE", step_index=day_idx, close=200.0 + day_idx),
            ],
            ingest_run_id=f"ext-1m-{day_idx}",
            observed_at=f"2026-11-15T07:1{day_idx}:00Z",
            dataset=MINUTE_SNAPSHOT_DATASET,
        )
    assert extended_store.withdraw_sessions(
        "2317.TWSE",
        MINUTE_SNAPSHOT_DATASET,
        ["2026-11-15"],
        observed_at="2026-11-15T07:30:00Z",
    )
    extended_store.compact(force=True)
    compacted_ext_lines = [
        line for line in extended_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(compacted_ext_lines) == 3

    historical_70a = frozen_source_codec(NEW70A_REF)
    hist_ext_store = historical_70a.LatestMarketSnapshotStore(extended_path)
    assert (
        hist_ext_store.get("2330.TWSE", dataset=DAILY_SNAPSHOT_DATASET).to_public_dict()
        == extended_store.get("2330.TWSE", dataset=DAILY_SNAPSHOT_DATASET).to_public_dict()  # type: ignore[union-attr]
    )
    assert (
        hist_ext_store.get("2330.TWSE", dataset=MINUTE_SNAPSHOT_DATASET).to_public_dict()
        == extended_store.get("2330.TWSE", dataset=MINUTE_SNAPSHOT_DATASET).to_public_dict()  # type: ignore[union-attr]
    )
    assert hist_ext_store.get("2317.TWSE", dataset=MINUTE_SNAPSHOT_DATASET) is None


def test_compaction_waits_for_write_lock_and_handles_concurrent_append_and_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "latest-market-snapshots.jsonl"
    store = LatestMarketSnapshotStore(path, compact_ratio_threshold=100)

    for day_idx in range(1, 5):
        store.append_normalized_records(
            [_daily_record("2330.TWSE", day_index=day_idx, close=1000.0 + day_idx)],
            ingest_run_id=f"seed-{day_idx}",
            observed_at=f"2026-11-15T08:0{day_idx}:00Z",
        )

    # 1. Verify compaction cannot run while an external writer holds LOCK_EX on the journal.
    lock_acquired = threading.Event()
    release_lock = threading.Event()
    compact_finished = threading.Event()

    def _hold_write_lock() -> None:
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                lock_acquired.set()
                release_lock.wait(timeout=5.0)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    holder = threading.Thread(target=_hold_write_lock)
    holder.start()
    assert lock_acquired.wait(timeout=5.0)

    compactor = threading.Thread(
        target=lambda: (store.compact(force=True), compact_finished.set())
    )
    compactor.start()
    # Compaction must remain blocked while LOCK_EX is held.
    assert not compact_finished.wait(timeout=0.15)
    release_lock.set()
    holder.join(timeout=5.0)
    compactor.join(timeout=5.0)
    assert compact_finished.is_set()

    # 2. Verify concurrent append and concurrent readers during compaction:
    # pause compaction right before os.replace so a concurrent writer opens the old path
    # and blocks on LOCK_EX; once os.replace completes, the writer must detect the inode
    # replacement, reopen the compacted file, and append its new key without losing data.
    for day_idx in range(5, 9):
        store.append_normalized_records(
            [_daily_record("2330.TWSE", day_index=day_idx, close=1000.0 + day_idx)],
            ingest_run_id=f"seed-{day_idx}",
            observed_at=f"2026-11-15T08:1{day_idx}:00Z",
        )

    real_replace = os.replace
    entering_replace = threading.Event()
    allow_replace = threading.Event()

    def _coordinated_replace(
        src: os.PathLike[str] | str, dst: os.PathLike[str] | str
    ) -> None:
        entering_replace.set()
        allow_replace.wait(timeout=5.0)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _coordinated_replace)

    writer_store = LatestMarketSnapshotStore(path, compact_ratio_threshold=100)
    reader_errors: list[Exception] = []
    stop_readers = threading.Event()

    def _continuous_reader() -> None:
        while not stop_readers.is_set():
            try:
                snap = LatestMarketSnapshotStore(path).get("2330.TWSE")
                if snap is None or snap.points[-1].close != 1008.0:
                    reader_errors.append(
                        AssertionError(f"unexpected reader state during compaction: {snap}")
                    )
                    return
            except Exception as exc:  # pragma: no cover - captured for assertion
                reader_errors.append(exc)
                return

    reader_thread = threading.Thread(target=_continuous_reader)
    compact_thread = threading.Thread(target=lambda: store.compact(force=True))
    append_thread = threading.Thread(
        target=lambda: writer_store.append_normalized_records(
            [_daily_record("2317.TWSE", day_index=1, close=210.0)],
            ingest_run_id="concurrent-append",
            observed_at="2026-11-15T08:20:00Z",
        )
    )

    reader_thread.start()
    compact_thread.start()
    assert entering_replace.wait(timeout=5.0)
    # Start concurrent append while compaction holds LOCK_EX on the pre-replace inode.
    append_thread.start()
    allow_replace.set()

    compact_thread.join(timeout=5.0)
    append_thread.join(timeout=5.0)
    stop_readers.set()
    reader_thread.join(timeout=5.0)

    assert reader_errors == []
    final_store = LatestMarketSnapshotStore(path)
    snap_2330 = final_store.get("2330.TWSE")
    snap_2317 = final_store.get("2317.TWSE")
    assert snap_2330 is not None and snap_2330.points[-1].close == 1008.0
    assert snap_2317 is not None and snap_2317.points[-1].close == 210.0
    final_lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(final_lines) == 2


def test_compacted_size_for_hosted_key_counts(tmp_path: Path) -> None:
    # Measure compacted journal size for the hosted 2,107-key snapshot population
    # after repeated steady-pull appends that would otherwise grow the journal linearly.
    key_count = 2107
    path = tmp_path / "hosted-key-count.jsonl"
    store = LatestMarketSnapshotStore(path)

    initial_batch = [
        _daily_record(
            f"{1000 + idx}.TWSE" if idx < 1000 else f"{2000 + idx}.TPEX",
            day_index=1,
            close=100.0 + (idx % 500),
            provisional=True,
        )
        for idx in range(key_count)
    ]
    store.append_normalized_records(
        initial_batch,
        ingest_run_id="steady-pull-0",
        observed_at="2026-11-15T02:10:00Z",
    )
    single_pass_bytes = path.stat().st_size
    single_pass_lines = sum(
        1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    assert single_pass_lines == key_count

    # Four more steady-pull passes with updated observed_at timestamps exceed the 4x ratio
    # threshold and compact the journal back down to `key_count` lines.
    for pass_idx in range(1, 5):
        store.append_normalized_records(
            initial_batch,
            ingest_run_id=f"steady-pull-{pass_idx}",
            observed_at=f"2026-11-15T0{2 + pass_idx}:10:00Z",
        )

    compacted_bytes = path.stat().st_size
    compacted_lines = sum(
        1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    assert compacted_lines == key_count
    assert compacted_bytes == single_pass_bytes
    assert compacted_bytes < 5 * 1024 * 1024

