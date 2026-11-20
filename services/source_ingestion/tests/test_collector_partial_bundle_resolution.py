"""COLLECTOR-HISTORY-PARTIAL-BUNDLE-RESOLUTION-20261120: a producer-partial price bundle is
resolved only by a complete bundle of the same trade date or by a both-venue confirmed closure."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from services.source_ingestion.connectors.collector_bundle import (
    CollectorBundleAdapter, compute_bundle_sha256, read_market_closures,
)
from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport, _TWSE_ROW

TWSE = "collector-tw-twse-price-history"
TPEX = "collector-tw-tpex-price-history"
_FIX = Path(__file__).resolve().parents[3] / "deploy/collector/tests/fixtures"


def bundle(source, run_id, trade_date, *, finished, rows=2, status="complete", reason=None):
    exchange = "TPEX" if source == TPEX else "TWSE"
    fixture = "tpex_daily_quotes_20261110.json" if exchange == "TPEX" else "twse_mi_index_20261110.json"
    table = json.loads((_FIX / fixture).read_text())
    table = table.get("tables", [table])[0]
    date = finished[:10]
    base = f"bundles/{source}/{date}/{run_id}"
    files, items, lines = {}, [], []
    for n in range(rows):
        symbol = f"{10000 + n}"
        normalized = {**_TWSE_ROW, "exchange": exchange, "symbol": symbol, "trade_date": trade_date}
        data = list(table["data"][0]); data[0] = symbol
        body = json.dumps({"date": trade_date.replace("-", ""), "fields": table["fields"], "data": [data],
                           "record": normalized}, ensure_ascii=False, sort_keys=True).encode()
        sha = hashlib.sha256(body).hexdigest()
        item_id = f"{source}-{run_id}-{symbol}"
        url = ("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes" if exchange == "TPEX"
               else "https://www.twse.com.tw/exchangeReport/MI_INDEX")
        lines.append({"id": item_id, "url": url, "fetched_at": finished, **normalized})
        items.append({"item_id": item_id, "url": url, "http_status": 200, "content_sha256": sha,
                      "bytes": len(body), "raw_path": f"raw/{sha}.json"})
        files[f"{base}/raw/{sha}.json"] = body
    manifest = {"schema_version": "collector_bundle.v1", "source_id": source, "run_id": run_id,
                "collector_version": "1.0.0", "started_at": finished, "finished_at": finished,
                "fetch_policy_hash": "fixture", "robots_decision": "allowed", "terms_ref": "https://example.test/terms",
                "attribution": exchange, "status": status, "items": items,
                "bundle_sha256": compute_bundle_sha256([m["content_sha256"] for m in items])}
    if reason:
        manifest["reason"] = reason
    files[f"{base}/manifest.json"] = json.dumps(manifest).encode()
    files[f"{base}/items.jsonl"] = "\n".join(json.dumps(r) for r in lines).encode()
    return files, {"source_id": source, "date": date, "run_id": run_id, "finished_at": finished}


def build(tmp_path, specs, *, source=TWSE, calendar=True, cadence=None):
    files, entries = {}, []
    for spec in specs:
        f, e = bundle(*spec[:3], **spec[3])
        files.update(f); entries.append(e)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    root = tmp_path / "store"
    if calendar:
        write_calendar(root, ["2026-01-01", "2026-02-17"])
    return CollectorBundleAdapter(connector_id=source, source_id=source, source_type="market",
                                  dataset="tw_price_daily", attribution_required=True, max_records=100,
                                  **({"cadence_seconds": cadence} if cadence else {}),
                                  transport=FakeTransport(files), raw_root=str(root))


def write_calendar(root, closed_days):
    part = root / "normalized" / "tw_trading_holidays" / "date=2026-01-01"
    part.mkdir(parents=True, exist_ok=True)
    endpoints = {"TWSE": "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?",
                 "TPEx": "https://www.tpex.org.tw/www/zh-tw/bulletin/tradingDate?"}
    rows = [{"metadata": {"normalized_row": {"dataset": "tw_trading_holidays", "venue": v, "api_endpoint": ep + "x=1",
                                             "date": d, "closed": True}}}
            for d in closed_days for v, ep in endpoints.items()]
    (part / "run.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


NO_DATA = {"status": "partial", "reason": "no_data_for_date", "rows": 0}


def closure_specs(day="2026-07-10", peer_status=NO_DATA, next_day="2026-07-13"):
    return [
        (TWSE, f"backfill-{day}", day, {"finished": "2026-11-11T09:33:18Z", **NO_DATA}),
        (TPEX, f"backfill-{day}", day, {"finished": "2026-11-11T09:30:00Z", **peer_status}),
        (TWSE, f"backfill-{next_day}", next_day, {"finished": "2026-11-11T09:40:00Z"}),
    ]


def test_confirmed_closure_is_skipped_and_recorded(tmp_path):
    adapter = build(tmp_path, closure_specs())
    result = adapter.pull(cursor="2026-11-11T09:00:00Z#backfill-2026-07-09")
    assert result.next_cursor.endswith("#backfill-2026-07-13") and result.complete
    assert {r.metadata["normalized_row"]["trade_date"] for r in result.records} == {"2026-07-13"}
    assert len(result.records) == 2
    [rec] = read_market_closures(tmp_path / "store" / "state")
    assert rec["trade_date"] == "2026-07-10" and rec["kind"] == "unscheduled_market_closure"
    assert rec["bundles"] == [f"{TPEX}:backfill-2026-07-10", f"{TWSE}:backfill-2026-07-10"]
    adapter.pull(cursor="2026-11-11T09:00:00Z#backfill-2026-07-09")  # replay does not duplicate
    assert len(read_market_closures(tmp_path / "store" / "state")) == 1
    assert (tmp_path / "store" / "state" / "market_closures.jsonl").stat().st_size < 600


def stalls(result):
    return result.next_cursor.endswith("|items=0") and not result.complete and not result.records


def test_resolved_resume_cursor_state_moves_past_the_partial_day(tmp_path):
    adapter = build(tmp_path, closure_specs())
    first = adapter.pull(cursor="2026-11-11T09:00:00Z#backfill-2026-07-09")
    adapter.save_resume_cursor(first)
    again = adapter.pull(cursor=first.next_cursor)
    assert not again.records and again.no_new_data is not None


@pytest.mark.parametrize("variant", ["peer_other_reason", "peer_missing", "peer_has_rows", "own_other_reason",
                                      "weekend", "calendar_closed", "calendar_absent"])
def test_unconfirmed_partial_still_stops_the_sweep(tmp_path, variant):
    day = "2026-07-10"
    specs = closure_specs(day)
    calendar = True
    if variant == "peer_other_reason":
        specs[1] = (TPEX, f"backfill-{day}", day, {"finished": "2026-11-11T09:30:00Z", "status": "partial", "reason": "fetch_failed", "rows": 0})
    elif variant == "peer_missing":
        specs.pop(1)
    elif variant == "peer_has_rows":
        specs[1] = (TPEX, f"backfill-{day}", day, {"finished": "2026-11-11T09:30:00Z"})
    elif variant == "own_other_reason":
        specs[0] = (TWSE, f"backfill-{day}", day, {"finished": "2026-11-11T09:33:18Z", "status": "partial", "reason": "parse_zero", "rows": 0})
    elif variant == "weekend":
        specs = closure_specs("2026-07-11", next_day="2026-07-13")
    elif variant == "calendar_absent":
        calendar = False
    adapter = build(tmp_path, specs, calendar=calendar)
    if variant == "calendar_closed":
        write_calendar(tmp_path / "store", ["2026-07-10"])
    result = adapter.pull(cursor="2026-11-11T09:00:00Z#backfill-2026-07-09")
    assert stalls(result) and not result.complete
    assert read_market_closures(tmp_path / "store" / "state") == []


def test_non_price_history_source_never_resolves(tmp_path):
    t86 = "collector-tw-twse-t86"
    specs = [(t86, *spec[1:]) if spec[0] == TWSE else spec for spec in closure_specs()]
    adapter = build(tmp_path, specs, source=t86)
    result = adapter.pull(cursor="2026-11-11T09:00:00Z#backfill-2026-07-09")
    assert not result.complete


def test_superseded_partial_is_acknowledged_and_rows_come_once(tmp_path):
    day = "2026-11-13"
    specs = [
        (TPEX, f"close-{day}-1440", day, {"finished": "2026-11-13T06:40:00Z", "status": "partial", "reason": "late_data", "rows": 1}),
        (TPEX, f"close-{day}-1530", day, {"finished": "2026-11-13T07:30:00Z", "rows": 3}),
    ]
    adapter = build(tmp_path, specs, source=TPEX)
    result = adapter.pull(cursor="2026-11-13T06:00:00Z#close-2026-11-12-1530")
    assert result.complete and result.next_cursor.endswith("#close-2026-11-13-1530")
    assert len(result.records) == 3
    assert len({r.record_id if hasattr(r, "record_id") else r.metadata["normalized_row"]["symbol"] for r in result.records}) == 3
    assert any(x["resolution"].startswith("superseded:") for x in result.observed["resolved_partial_bundles"])


def test_partial_without_later_complete_stays_pending(tmp_path):
    day = "2026-11-13"
    specs = [(TPEX, f"close-{day}-1440", day, {"finished": "2026-11-13T06:40:00Z", "status": "partial", "reason": "late_data", "rows": 1}),
             (TPEX, "close-2026-11-16-1530", "2026-11-16", {"finished": "2026-11-16T07:30:00Z", "rows": 2})]
    adapter = build(tmp_path, specs, source=TPEX)
    result = adapter.pull(cursor="2026-11-13T06:00:00Z#close-2026-11-12-1530")
    assert not result.complete and result.next_cursor.endswith("|items=1")
    assert not any(r.metadata["normalized_row"]["trade_date"] == "2026-11-16" for r in result.records)


def test_confirmed_closure_resolves_under_priority_cadence(tmp_path):
    adapter = build(tmp_path, closure_specs(), cadence=600)
    assert adapter._priority_latest_enabled()
    result = adapter.pull(cursor="2026-11-11T09:00:00Z#backfill-2026-07-09")
    assert not stalls(result)
    assert result.next_cursor.endswith("#backfill-2026-07-13") and result.complete
    [rec] = read_market_closures(tmp_path / "store" / "state")
    assert rec["trade_date"] == "2026-07-10"


def test_superseded_partial_resolves_under_priority_cadence(tmp_path):
    day = "2026-11-13"
    specs = [
        (TPEX, f"close-{day}-1440", day, {"finished": "2026-11-13T06:40:00Z", "status": "partial", "reason": "late_data", "rows": 1}),
        (TPEX, f"close-{day}-1530", day, {"finished": "2026-11-13T07:30:00Z", "rows": 3}),
    ]
    adapter = build(tmp_path, specs, source=TPEX, cadence=600)
    result = adapter.pull(cursor="2026-11-13T06:00:00Z#close-2026-11-12-1530")
    assert result.complete and result.next_cursor.endswith("#close-2026-11-13-1530")
    assert len(result.records) == 3
    assert any(x["resolution"].startswith("superseded:") for x in result.observed["resolved_partial_bundles"])


def test_unconfirmed_partial_stops_under_priority_cadence(tmp_path):
    specs = closure_specs()
    specs.pop(1)
    adapter = build(tmp_path, specs, cadence=600)
    result = adapter.pull(cursor="2026-11-11T09:00:00Z#backfill-2026-07-09")
    # Priority-latest may deliver the newest session; the partial day is never resolved or recorded.
    assert "resolved_partial_bundles" not in result.observed
    assert read_market_closures(tmp_path / "store" / "state") == []
    assert not result.next_cursor.endswith("#backfill-2026-07-10")
