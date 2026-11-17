"""SOURCE-WARRANT-LISTING-EXPIRY-20261117: listing parser, allowlist state, fail-closed, filter, writer guard."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_ingestion.connectors import SourceConnector, SourceRecord
from services.source_ingestion.market_data_storage import MarketDataStorageWriter
from services.source_ingestion.security_listing import (
    ListingFilter, ListingParseError, ListingState, ListingUnavailable, bare_symbol, parse_isin_page, refresh_listing,
)

FIX = Path(__file__).parent / "fixtures" / "security_listing"
TWSE = (FIX / "twse_isin_strmode2_excerpt.html").read_text(encoding="utf-8")
TPEX = (FIX / "tpex_isin_strmode4_excerpt.html").read_text(encoding="utf-8")


def _state(date="2026-11-16", extra_twse=(), extra_tpex=()):
    s = ListingState()
    s.apply_listing([parse_isin_page(TWSE, venue="TWSE"), parse_isin_page(TPEX, venue="TPEX")], listing_date=date)
    return s


def test_provenance_records_source_and_hash():
    prov = json.loads((FIX / "provenance.json").read_text(encoding="utf-8"))
    for name in ("twse_isin_strmode2_excerpt.html", "tpex_isin_strmode4_excerpt.html"):
        assert prov[name]["source_url"].startswith("https://isin.twse.com.tw/isin/C_public.jsp?strMode=")
        assert prov[name]["page_update_date"]


def test_parse_warrant_iff_header_and_rw_cfi():
    tw = parse_isin_page(TWSE, venue="TWSE")
    by = {r.symbol: r for r in tw.rows}
    assert by["1101"].is_warrant is False and by["1101"].cfi == "ESVUFR"
    w = [r for r in tw.rows if r.is_warrant]
    assert w and all(r.cfi.startswith("RW") and "權證" in r.category for r in w)
    assert all(not r.cfi.startswith("RW") for r in tw.rows if not r.is_warrant)
    tp = parse_isin_page(TPEX, venue="TPEX")
    assert any(r.is_warrant for r in tp.rows) and any(not r.is_warrant for r in tp.rows)
    assert tw.page_date == "2026-11-17"


def test_parse_fails_on_column_change():
    with pytest.raises(ListingParseError, match="column header changed"):
        parse_isin_page(TWSE.replace("CFICode", "CFI"), venue="TWSE")


def test_parse_fails_on_header_cfi_disagreement():
    bad = TWSE.replace("RWSCCA", "ESVUFR", 1)
    with pytest.raises(ListingParseError, match="disagree"):
        parse_isin_page(bad, venue="TWSE")
    bad2 = TWSE.replace("ESVUFR", "RWSCCA", 1)
    with pytest.raises(ListingParseError, match="disagree"):
        parse_isin_page(bad2, venue="TWSE")


def test_parse_fails_without_date_or_rows():
    with pytest.raises(ListingParseError):
        parse_isin_page(TWSE.replace("最近更新日期", "x"), venue="TWSE")
    with pytest.raises(ListingParseError):
        parse_isin_page("<html>最近更新日期:2026/11/17</html>", venue="TWSE")


def test_allowlist_rows_and_registry():
    s = _state()
    row = s.allowlist["1101"]
    assert set(row) >= {"venue", "category", "cfi", "first_seen", "last_seen"}
    assert "1101" in s.ordinary_share_registry
    warrant = next(sym for sym, r in s.allowlist.items() if r["is_warrant"])
    assert warrant not in s.ordinary_share_registry
    assert s.is_listed(warrant) and s.is_warrant(warrant)


def test_apply_listing_requires_both_venues():
    s = ListingState()
    with pytest.raises(ListingParseError):
        s.apply_listing([parse_isin_page(TWSE, venue="TWSE")], listing_date="2026-11-16")


def test_expiry_registry_only_gains_and_persist(tmp_path):
    s = _state("2026-11-13")
    warrant = next(sym for sym, r in s.allowlist.items() if r["is_warrant"] and r["venue"] == "TWSE")
    # next day's listing no longer lists that warrant and the share 1101
    tw = parse_isin_page(TWSE, venue="TWSE")
    kept = tuple(r for r in tw.rows if r.symbol not in (warrant, "1101"))
    tw2 = type(tw)(venue="TWSE", page_date=tw.page_date, rows=kept)
    s.apply_listing([tw2, parse_isin_page(TPEX, venue="TPEX")], listing_date="2026-11-16")
    assert s.expired_warrants() == {warrant}
    assert not s.is_listed(warrant) and not s.is_listed("1101")
    assert "1101" in s.ordinary_share_registry  # gained once, never lost
    assert "1101" not in s.expired_warrants()  # a delisted share is not an expired warrant
    s.save(tmp_path / "s.json")
    assert ListingState.load(tmp_path / "s.json").expired_warrants() == {warrant}


def test_fail_closed_status_and_no_expiry_while_unreadable():
    s = _state("2026-11-16")  # Monday
    assert s.status("2026-11-16") == "fresh"
    assert s.status("2026-11-18") == "stale_ok"
    assert s.status("2026-11-19") == "stale_ok"  # 3 trading days
    assert s.status("2026-11-20") == "listing_unavailable"  # 4
    with pytest.raises(ListingUnavailable, match="listing_unavailable"):
        s.require_collectable("2026-11-20")
    assert ListingState().status("2026-11-16") == "listing_unavailable"
    # while stale nothing is expired, even if rows look old
    s.allowlist["030001"]["last_seen"] = "2026-11-01"
    s.allowlist["030001"]["is_warrant"] = True
    assert "030001" in s.expired_warrants("2026-11-16")
    assert s.expired_warrants("2026-11-18") == frozenset()


def test_refresh_failure_keeps_previous_allowlist(tmp_path):
    s = _state("2026-11-13")
    before = json.dumps(s.to_dict(), sort_keys=True)
    pages = {"strMode=2": TWSE.replace("CFICode", "X"), "strMode=4": TPEX}
    res = refresh_listing(s, lambda u: next(v for k, v in pages.items() if k in u), listing_date="2026-11-16",
                          state_path=tmp_path / "s.json")
    assert res["ok"] is False and "column header changed" in res["reason"]
    assert json.dumps(s.to_dict(), sort_keys=True) == before and not (tmp_path / "s.json").exists()

    def boom(_u):
        raise OSError("down")
    assert refresh_listing(s, boom, listing_date="2026-11-16")["ok"] is False
    ok = refresh_listing(s, lambda u: TWSE if "strMode=2" in u else TPEX, listing_date="2026-11-16",
                         state_path=tmp_path / "s.json")
    assert ok["ok"] and ok["listing_date"] == "2026-11-16" and (tmp_path / "s.json").exists()


def test_bare_symbol():
    assert bare_symbol("2330.TWSE") == "2330" and bare_symbol("6488.TPEX") == "6488" and bare_symbol("030001") == "030001"


def _rec(symbol, date="2026-11-13"):
    return SourceRecord(source_id=f"s-{symbol}", connector_id="c", source_type="market", title="t", content_ref=f"r-{symbol}",
                        metadata={"symbol": symbol, "close": 10.0, "dataset": "tw_price_daily", "event_time": f"{date}T05:30:00Z"})


def test_batch_of_500_listed_warrants_stored_and_unlisted_skipped(tmp_path):
    pytest.importorskip("pyarrow")
    s = _state()
    listed_w = [sym for sym, r in s.allowlist.items() if r["is_warrant"]]
    assert listed_w
    listed = (listed_w * 500)[:1] + ["1101"]
    unlisted = [f"07{i:04d}" for i in range(498)]  # six-character codes in no listing
    f = ListingFilter.from_state(s)
    conn = SourceConnector(connector_id="c", source_type="market", provider="t", license_scope="internal", metadata={"market": "TW"})
    res = SimpleNamespace(records=[_rec(x) for x in listed + unlisted], run=SimpleNamespace(ingest_run_id="r1"))
    m = MarketDataStorageWriter(tmp_path, listing_filter=f).write_run(result=res, connector=conn).to_dict()
    assert m["summary"]["normalized_row_count"] == 2
    assert m["summary"]["skipped_not_listed_count"] == 498
    assert f.summary()["skipped_not_listed_count"] == 498


def test_filter_disabled_is_previous_behaviour(tmp_path):
    f = ListingFilter.from_state(_state(), enabled=False)
    assert f.admits("999999") and f.skipped_not_listed_count == 0
    conn = SourceConnector(connector_id="c", source_type="market", provider="t", license_scope="internal", metadata={"market": "TW"})
    res = SimpleNamespace(records=[_rec("999999")], run=SimpleNamespace(ingest_run_id="r1"))
    m = MarketDataStorageWriter(tmp_path, listing_filter=f).write_run(result=res, connector=conn).to_dict()
    assert m["summary"]["normalized_row_count"] == 1 and m["summary"]["skipped_not_listed_count"] == 0


def test_hosted_shaped_state_bytes_budget(tmp_path):
    """Design 6 row: <= 2 MB at ~22,500 rows (20,200 warrants). Fails on bytes."""
    s = ListingState(last_success_date="2026-11-16")
    for i in range(20200):
        s.allowlist[f"{i + 30000:06d}"] = {"venue": "TWSE", "category": "上市認購(售)權證", "cfi": "RWSCCA",
                                           "is_warrant": True, "first_seen": "2026-10-05", "last_seen": "2026-11-16"}
    for i in range(2300):
        s.allowlist[f"{1000 + i}"] = {"venue": "TWSE", "category": "股票", "cfi": "ESVUFR", "is_warrant": False,
                                     "first_seen": "2026-10-05", "last_seen": "2026-11-16"}
    s.save(tmp_path / "s.json")
    size = (tmp_path / "s.json").stat().st_size
    print("listing_state_bytes", size)
    assert size <= 2 * 1024 * 1024, size


def test_live_shaped_state_bytes_budget_and_roundtrip(tmp_path):
    """Live listing 2026-11-16: 57,531 rows (41,965 TWSE + 13,143 TPEx warrants, 2,014 ordinary shares)."""
    s = ListingState(last_success_date="2026-11-16")
    for i in range(41965):
        s.allowlist[f"{i + 30000:06d}"] = {"venue": "TWSE", "category": "上市認購(售)權證", "cfi": "RWSCCA",
                                           "is_warrant": True, "first_seen": "2026-10-05", "last_seen": "2026-11-16"}
    for i in range(13143):
        s.allowlist[f"7{i:05d}"] = {"venue": "TPEx", "category": "上櫃認購(售)權證", "cfi": "RWSCCA",
                                    "is_warrant": True, "first_seen": "2026-10-05", "last_seen": "2026-11-16"}
    for i in range(2014):
        s.allowlist[f"{1000 + i}"] = {"venue": "TWSE", "category": "股票", "cfi": "ESVUFR", "is_warrant": False,
                                     "first_seen": "2026-10-05", "last_seen": "2026-11-16"}
        s.ordinary_share_registry[f"{1000 + i}"] = {"venue": "TWSE", "first_seen": "2026-10-05"}
    path = tmp_path / "live.json"
    s.save(path)
    size = path.stat().st_size
    print("live_shaped_listing_state_bytes", size)
    assert size <= 2 * 1024 * 1024, size
    back = ListingState.load(path)
    assert back.allowlist == s.allowlist and back.ordinary_share_registry == s.ordinary_share_registry


def test_v1_state_file_still_loads(tmp_path):
    import json as _json
    v1 = {"schema_version": "security_listing.v1", "last_success_date": "2026-11-16", "categories": ["股票"],
          "listing_allowlist": {"2330": ["TWSE", 0, "ESVUFR", "2026-10-05", "2026-11-16"]},
          "ordinary_share_registry": {"2330": ["TWSE", "2026-10-05"]}}
    (tmp_path / "v1.json").write_text(_json.dumps(v1), encoding="utf-8")
    st = ListingState.load(tmp_path / "v1.json")
    assert st.allowlist["2330"]["last_seen"] == "2026-11-16" and st.ordinary_share_registry["2330"]["venue"] == "TWSE"
