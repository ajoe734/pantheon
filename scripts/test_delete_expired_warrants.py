from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scripts import delete_expired_warrants as dew
from services.source_ingestion.security_listing import ListingState


def _row(symbol):
    return json.dumps({"source_id": f"s-{symbol}", "metadata": {"symbol": symbol, "close": 1.0}}) + "\n"


def _state(today="2026-11-16"):
    st = ListingState(last_success_date=today)
    st.allowlist["2330"] = {"venue": "TWSE", "category": "股票", "cfi": "ESVUFR", "is_warrant": False,
                            "first_seen": today, "last_seen": today}
    st.allowlist["030001"] = {"venue": "TWSE", "category": "上市認購(售)權證", "cfi": "RWSCCA", "is_warrant": True,
                              "first_seen": today, "last_seen": today}  # listed warrant: kept
    st.allowlist["030002"] = {"venue": "TWSE", "category": "上市認購(售)權證", "cfi": "RWSCCA", "is_warrant": True,
                              "first_seen": "2026-10-01", "last_seen": "2026-11-10"}  # expired
    st.ordinary_share_registry["1234"] = {"venue": "TWSE", "first_seen": "2024-01-01"}  # delisted share
    return st


def _root(tmp_path):
    root = tmp_path / "store"
    n = root / "normalized" / "tw_price_daily" / "date=2026-11-13"
    n.mkdir(parents=True)
    (n / "run.jsonl").write_text("".join(_row(s) for s in
                                         ["2330", "030001", "030002", "700123", "1234", "9999", "00999A"]), encoding="utf-8")
    r = root / "raw" / "c" / "tw_price_daily" / "date=2026-11-13"
    r.mkdir(parents=True)
    with gzip.open(r / "run.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(_row("030002") + _row("2330"))
    return root


def test_dry_run_is_default_and_classifies(tmp_path, capsys):
    root = _root(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    st = tmp_path / "state.json"
    _state().save(st)
    rep = tmp_path / "report.json"
    assert dew.main(["--root", str(root), "--listing-state", str(st), "--today", "2026-11-16", "--report", str(rep)]) == 0
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before  # nothing deleted
    report = json.loads(rep.read_text())
    assert report["delete_symbols"] == ["030002", "700123"]  # expired + in no listing
    assert report["review_symbols"] == ["00999A", "9999"]  # share-shaped: never automatic
    assert "1234" not in report["delete_symbols"] + report["review_symbols"]  # delisted share kept
    assert "030001" not in report["delete_symbols"]  # listed warrant kept
    assert report["stores"]["normalized"]["delete_rows"] == 2 and report["stores"]["raw"]["delete_rows"] == 1
    assert report["digest"]


def test_execute_needs_matching_digest_and_keeps_delisted_share_daily_bars(tmp_path):
    root = _root(tmp_path)
    st = tmp_path / "state.json"
    _state().save(st)
    rep = tmp_path / "report.json"
    base = ["--root", str(root), "--listing-state", str(st), "--today", "2026-11-16", "--report", str(rep)]
    assert dew.main(base + ["--execute"]) == 2  # no digest
    assert dew.main(base + ["--execute", "--confirm-digest", "deadbeef"]) == 2
    digest = json.loads(rep.read_text())["digest"]
    assert dew.main(base + ["--execute", "--confirm-digest", digest]) == 0
    syms = [json.loads(x)["metadata"]["symbol"] for x in
            (root / "normalized/tw_price_daily/date=2026-11-13/run.jsonl").read_text().splitlines()]
    assert syms == ["2330", "030001", "1234", "9999", "00999A"]  # delisted ordinary share 1234 kept
    with gzip.open(root / "raw/c/tw_price_daily/date=2026-11-13/run.jsonl.gz", "rt") as fh:
        assert [json.loads(x)["metadata"]["symbol"] for x in fh] == ["2330"]


def test_daily_bars_of_delisted_ordinary_shares_are_untouched(tmp_path):
    pytest.importorskip("pyarrow")
    from services.source_ingestion.daily_bars_store import DailyBarsStore

    root = _root(tmp_path)
    store = DailyBarsStore(root)
    store.write_day("2026-11-13", [{"market": "TW", "symbol": "1234", "trade_date": "2026-11-13", "open": 1.0,
                                     "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}], ingest_run_id="r1")
    assert (root / "daily_bars").exists()
    digests = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "daily_bars").rglob("*") if p.is_file()} if (root / "daily_bars").exists() else {}
    before = digests()
    st = tmp_path / "state.json"
    _state().save(st)
    rep = tmp_path / "report.json"
    base = ["--root", str(root), "--listing-state", str(st), "--today", "2026-11-16", "--report", str(rep)]
    dew.main(base)
    dew.main(base + ["--execute", "--confirm-digest", json.loads(rep.read_text())["digest"]])
    assert digests() == before


def test_fail_closed_when_listing_not_fresh(tmp_path):
    root = _root(tmp_path)
    st = tmp_path / "state.json"
    _state("2026-11-02").save(st)
    rep = tmp_path / "report.json"
    base = ["--root", str(root), "--listing-state", str(st), "--today", "2026-11-16", "--report", str(rep)]
    assert dew.main(base) == 0
    report = json.loads(rep.read_text())
    assert report["delete_symbols"] == [] and "refused" in report
    assert dew.main(base + ["--execute", "--confirm-digest", report["digest"]]) == 2
    assert len((root / "normalized/tw_price_daily/date=2026-11-13/run.jsonl").read_text().splitlines()) == 7
