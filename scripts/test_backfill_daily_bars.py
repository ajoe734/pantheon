from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from scripts import backfill_daily_bars as bf
from services.source_ingestion import daily_bars_store as dbs
from services.source_ingestion.daily_bars_store import DailyBarsStore
from services.source_ingestion.security_listing import ListingState

TODAY = "2026-11-17"


def _state(today=TODAY):
    st = ListingState(last_success_date=today)

    def add(sym, cfi, warrant, last_seen=today):
        st.allowlist[sym] = {"venue": "TWSE", "category": "x", "cfi": cfi, "is_warrant": warrant,
                             "first_seen": "2026-01-01", "last_seen": last_seen}

    for sym in ("2330", "2317", "1101"):
        add(sym, "ESVUFR", False)
    add("030001", "RWSCCA", True)  # listed warrant: carried
    add("030002", "RWSCCA", True, last_seen="2026-11-10")  # expired warrant: not carried
    st.ordinary_share_registry["1234"] = {"venue": "TWSE", "first_seen": "2020-01-01"}  # delisted share: carried
    return st


def _bf(root, state, **kw):
    kw.setdefault("today", TODAY)
    return bf.backfill(root, state, **kw)


def _line(symbol, trade_date, close, run="r", key="trade_date", **extra):
    body = {"symbol": symbol, "close": close, "open": close, "high": close, "low": close, "volume": 1000.0, **extra}
    if trade_date is not None:
        body[key] = trade_date
    return json.dumps({"source_id": f"{run}-{symbol}", "metadata": {"market": "TW", "normalized_row": body}})


def _write(root, write_date, run, lines):
    d = root / "normalized" / "tw_price_daily" / f"date={write_date}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{run}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build(root):
    # write date 11-15 holds trade dates 11-12 and 11-13 (partition name is NOT the trade date)
    _write(root, "2026-11-15", "run-a", [
        _line("2330", "2026-11-12", 100.0), _line("2317", "2026-11-12", 50.0),
        _line("2330", "2026-11-13", 101.0), _line("030002", "2026-11-13", 1.0),
        _line("030001", "2026-11-13", 2.0), _line("1234", "2026-11-12", 7.0),
        _line("1101", None, 9.0),  # no trade date: counted, not written
    ])
    # a later re-ingest of 11-13 with a corrected 2330 close: the newest copy wins
    _write(root, "2026-11-16", "run-b", [_line("2330", "2026-11-13", 111.0, run="b"), _line("2317", "2026-11-13", 51.0, run="b")])


def _bytes(root):
    out = {}
    for p in sorted((root / "daily_bars").rglob("*")):
        if p.is_file() and p.name != ".lock":
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_dry_run_reports_and_writes_nothing(tmp_path):
    _build(tmp_path)
    report = _bf(tmp_path, _state())
    assert not (tmp_path / "daily_bars").exists()
    assert report["source"]["rows_without_trade_date"] == 1
    d13 = report["days"]["2026-11-13"]
    assert (d13["rows"], d13["unique"], d13["duplicates"]) == (5, 4, 1)
    assert d13["classes"]["delete"] == 1  # expired warrant
    assert d13["expected_rows"] == 3  # 2330, 2317, listed warrant 030001


def test_trade_date_from_row_and_newest_wins_and_classes(tmp_path):
    _build(tmp_path)
    report = _bf(tmp_path, _state(), apply=True)
    assert sorted(report["days"]) == ["2026-11-12", "2026-11-13"]  # never 11-15 / 11-16 (write dates)
    assert report["mismatches"] == []
    rows = DailyBarsStore(tmp_path)._read_day_rows(DailyBarsStore(tmp_path).read_manifest("2026-11-13"))
    by_symbol = {k[1]: v for k, v in rows.items()}
    assert by_symbol["2330"]["close"] == 111.0
    assert set(by_symbol) == {"2330", "2317", "030001"}  # expired 030002 not carried
    d12 = DailyBarsStore(tmp_path)._read_day_rows(DailyBarsStore(tmp_path).read_manifest("2026-11-12"))
    assert {k[1] for k in d12} == {"2330", "2317", "1234"}  # delisted share carried
    assert report["classes"]["keep_delisted_share"] == 1


def test_second_run_writes_nothing_bytes_and_digests(tmp_path):
    _build(tmp_path)
    first = _bf(tmp_path, _state(), apply=True)
    before = _bytes(tmp_path)
    second = _bf(tmp_path, _state(), apply=True)
    assert _bytes(tmp_path) == before
    assert all(not d["written"] for d in second["days"].values())
    assert {k: v["content_digest"] for k, v in first["days"].items()} == {k: v["content_digest"] for k, v in second["days"].items()}


def test_kill_in_the_middle_then_resume(tmp_path):
    _build(tmp_path)
    calls = {"n": 0}

    def kill(step):
        if step == "before_manifest_rename":
            calls["n"] += 1
            if calls["n"] == 2:  # first date published, second date killed before its manifest
                raise KeyboardInterrupt("killed")

    with pytest.raises(KeyboardInterrupt):
        _bf(tmp_path, _state(), apply=True, store=DailyBarsStore(tmp_path, fault=kill))
    store = DailyBarsStore(tmp_path)
    published = sorted(p.name for p in store.manifest_dir.glob("date=*.json"))
    assert published == ["date=2026-11-12.json"]
    assert store.verify_day("2026-11-12")["verified"]  # every published date is complete
    residue = [p for p in (tmp_path / "daily_bars").rglob("*")
               if p.is_file() and p.name != ".lock" and "date=2026-11-12" not in p.as_posix()]
    assert residue and all("date=2026-11-13" in p.as_posix() for p in residue)  # one trade date at most

    report = _bf(tmp_path, _state(), apply=True)
    assert report["swept"]["temp_files"] >= 1 and report["swept"]["orphan_parts"] >= 1
    assert not [p for p in (tmp_path / "daily_bars").rglob("*") if p.is_file() and ".tmp" in p.name]
    assert report["mismatches"] == [] and len(report["days"]) == 2
    assert DailyBarsStore(tmp_path).verify_day("2026-11-13")["verified"]


def test_mismatch_fails_and_names_the_date(tmp_path, monkeypatch):
    _build(tmp_path)
    real = DailyBarsStore.verify_day

    def bad(self, trade_date):
        out = real(self, trade_date)
        return {**out, "verified": False} if trade_date == "2026-11-13" else out

    monkeypatch.setattr(DailyBarsStore, "verify_day", bad)
    with pytest.raises(bf.BackfillMismatch) as exc:
        _bf(tmp_path, _state(), apply=True)
    assert exc.value.dates == ["2026-11-13"] and "2026-11-13" in str(exc.value)


@pytest.mark.parametrize("last", [None, "2026-11-01"])
def test_unreadable_or_stale_listing_stops_before_any_write(tmp_path, last):
    _build(tmp_path)
    state = _state()
    state.last_success_date = last
    with pytest.raises(bf.BackfillRefused, match="listing is not fresh"):
        _bf(tmp_path, state, apply=True)
    assert not (tmp_path / "daily_bars").exists()


def test_hosted_shaped_backfill_stays_inside_the_byte_limits(tmp_path):
    """3400 symbols per day, 3 trade dates through 2 write dates; day bytes stay under the section 6 bound."""
    state = _state()
    for i in range(3400):
        state.allowlist[f"{2000 + i}"] = {"venue": "TWSE", "category": "x", "cfi": "ESVUFR", "is_warrant": False,
                                          "first_seen": "2026-01-01", "last_seen": TODAY}
    lines = lambda day, c: [_line(f"{2000 + i}", day, c + i % 50) for i in range(3400)]  # noqa: E731
    _write(tmp_path, "2026-11-15", "a", lines("2026-11-11", 10.0) + lines("2026-11-12", 11.0))
    _write(tmp_path, "2026-11-16", "b", lines("2026-11-13", 12.0) + lines("2026-11-12", 11.0))  # duplicates of 11-12
    report = _bf(tmp_path, state, apply=True)
    assert report["days"]["2026-11-12"]["duplicates"] == 3400
    assert all(d["manifest_rows"] == 3400 and d["verified"] for d in report["days"].values())
    assert report["budget"]["daily_bars_max_day_bytes"] <= dbs.DAILY_BARS_DAY_TARGET_BYTES


def test_day_over_the_limit_fails_on_bytes(tmp_path, monkeypatch):
    _build(tmp_path)
    monkeypatch.setattr(bf, "DAILY_BARS_DAY_ALARM_BYTES", 1)
    with pytest.raises(dbs.DailyBarsBudgetExceeded):
        _bf(tmp_path, _state(), apply=True)


def test_cli_dry_run_writes_report(tmp_path):
    _build(tmp_path)
    state_path = tmp_path / "listing.json"
    _state().save(state_path)
    out = tmp_path / "report.json"
    assert bf.main(["--root", str(tmp_path), "--listing-state", str(state_path), "--today", TODAY, "--report", str(out)]) == 0
    assert json.loads(out.read_text())["apply"] is False
    assert bf.main(["--root", str(tmp_path), "--listing-state", str(state_path), "--today", "2026-12-30"]) == 2
