"""Real keyed-store cleanup across 750 stored sessions, rather than 750 calendar days."""

import json
import shutil
from dataclasses import replace
from datetime import date, timedelta

import pytest

pytest.importorskip("pyarrow")

from services.source_ingestion.daily_bars_store import (
    KEYED_DATASETS, DailyBarsStore, DatasetKeyDeclaration, keyed_dataset_store,
)
from services.source_ingestion.pin_source import PinSource
from services.source_ingestion.retention import MODE_ENV, RetentionSweeper

TODAY = "2026-11-27"


def _records(day):
    return [{"symbol": "2330", "trade_date": day, "side": side, "rank": 1, "broker": "A"}
            for side in ("buy", "sell")]


def _sweeper(root, tmp_path, *, mode="delete", pins=(), configured=True):
    evidence = tmp_path / "pins.json"
    evidence.write_text(json.dumps({"trade_dates": list(pins)}))
    return RetentionSweeper(
        daily_bars_store=DailyBarsStore(root), keyed_root=root,
        report_path=tmp_path / "report.json", today=lambda: TODAY,
        pin_source=PinSource([evidence]) if configured else None, environ={MODE_ENV: mode},
    )


@pytest.fixture(scope="module")
def history(tmp_path_factory):
    root = tmp_path_factory.mktemp("broker-session-history")
    days, day = [], date.fromisoformat(TODAY)
    while len(days) < 752:
        # Omit a holiday and some collection gaps: neither should consume a stored session.
        if day.weekday() < 5 and day.isoformat() not in {"2026-10-09", "2025-01-01", "2024-01-01"}:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    days.sort()
    store = keyed_dataset_store(root, "tw_broker_top")
    store.write_records([record for day in days for record in _records(day)], ingest_run_id="r")
    return root, days


@pytest.mark.parametrize("mode", ["dry_run", "delete"])
@pytest.mark.parametrize("pinned", [False, True])
def test_latest_750_sessions_survive_calendar_age_and_old_pins_are_extra(history, tmp_path, mode, pinned):
    template, days = history
    root = tmp_path / "store"
    shutil.copytree(template, root)
    store = keyed_dataset_store(root, "tw_broker_top")
    pins = [days[0]] if pinned else []
    assert (date.fromisoformat(TODAY) - date.fromisoformat(days[2])).days > 750
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = _sweeper(root, tmp_path, mode=mode, pins=pins).run()
    result = report["stores"]["keyed"]
    assert report["status"] == "completed"
    assert result["would_delete_partitions"] == 2 - len(pins)
    assert result["pinned_dates_kept"] == len(pins)
    assert result["would_delete_bytes"] > 0
    assert result["deleted_partitions"] == (2 - len(pins) if mode == "delete" else 0)
    if mode == "dry_run":
        assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
        assert store.partitions() == days
    else:
        assert store.partitions() == sorted(days[2:] + pins)
        assert result["deleted_rows"] == 2 * (2 - len(pins))
        assert result["deleted_bytes"] == result["would_delete_bytes"]
        assert {row["side"] for row in store.records(days[2])} == {"buy", "sell"}
        assert store.read_manifest(days[1]) is None
        # A second sweep with no new session is stable.
        again = _sweeper(root, tmp_path, pins=pins).run()
        assert again["stores"]["keyed"]["deleted_partitions"] == 0


def test_short_history_is_kept_and_calendar_retention_and_no_expiry_stay_unchanged(tmp_path):
    root = tmp_path / "store"
    broker = keyed_dataset_store(root, "tw_broker_top")
    broker.write_records(_records("2020-01-02") + _records(TODAY), ingest_run_id="r")
    community = keyed_dataset_store(root, "tw_community_daily")
    recent = ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", TODAY]
    community.write_records([{"symbol": "2330", "trade_date": d, "platform": "ptt"}
                             for d in ["2020-01-02", *recent]], ingest_run_id="r")
    macro = keyed_dataset_store(root, "tw_macro_series")
    macro.write_records([{"series_code": "cpi", "date": "2019-01", "value": 1}], ingest_run_id="r")
    report = _sweeper(root, tmp_path).run()
    assert report["status"] == "completed"
    assert broker.partitions() == ["2020-01-02", TODAY]
    assert community.partitions() == recent
    assert macro.partitions() == ["2019-01"]
    assert report["stores"]["keyed"]["datasets"].keys() == {"tw_community_daily"}


def test_unresolved_pins_prevent_session_cleanup(history, tmp_path):
    template, days = history
    root = tmp_path / "store"
    shutil.copytree(template, root)
    report = _sweeper(root, tmp_path, configured=False).run()
    assert report["status"] == "blocked"
    assert report["stores"]["keyed"]["deleted_partitions"] == 0
    assert keyed_dataset_store(root, "tw_broker_top").partitions() == days


def test_future_and_invalid_dates_cannot_displace_history(history, tmp_path):
    template, days = history
    root = tmp_path / "store"
    shutil.copytree(template, root)
    store = keyed_dataset_store(root, "tw_broker_top")
    for extra in ["2027-01-04", "9999-99-99"]:
        store.write_records(_records(extra), ingest_run_id="extra")
    report = _sweeper(root, tmp_path).run()
    assert report["status"] == "completed"
    assert store.partitions() == sorted(days[2:] + ["2027-01-04", "9999-99-99"])


def test_session_retention_declaration_and_budget_report(tmp_path):
    decl = KEYED_DATASETS["tw_broker_top"]
    assert decl.retention_sessions == 750 and decl.retention_days is None
    report = keyed_dataset_store(tmp_path, "tw_broker_top").budget_report()
    assert report["retention_sessions"] == 750 and report["retention_days"] is None
    for kwargs in [{"retention_sessions": 0}, {"retention_sessions": 750, "retention_days": 750}]:
        with pytest.raises(ValueError):
            DatasetKeyDeclaration("invalid", ("trade_date",), "trade_date", **kwargs)


def test_session_cleanup_still_refuses_large_deletes(tmp_path, monkeypatch):
    # Use a shorter declaration to exercise the same 20% guard without creating thousands of files.
    monkeypatch.setitem(KEYED_DATASETS, "tw_broker_top", replace(KEYED_DATASETS["tw_broker_top"], retention_sessions=1))
    root = tmp_path / "store"
    store = keyed_dataset_store(root, "tw_broker_top")
    days = ["2026-11-24", "2026-11-25", "2026-11-26", TODAY]
    store.write_records([row for day in days for row in _records(day)], ingest_run_id="r")
    report = _sweeper(root, tmp_path).run()
    assert report["status"] == "partial"
    assert report["stores"]["keyed"]["delete_share"] == 0.75
    assert "refused" in report["stores"]["keyed"]
    assert report["stores"]["keyed"]["deleted_partitions"] == 0
    assert store.partitions() == days
