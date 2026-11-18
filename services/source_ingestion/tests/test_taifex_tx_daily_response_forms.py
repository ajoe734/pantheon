"""tw-taifex-tx-daily reads both forms DailyMarketReportFut is served in (TW-TAIFEX-TX-DAILY-CSV-RESPONSE-20261118)."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from services.source_ingestion.connectors import taiwan_official as tw

FIX = Path(__file__).resolve().parent / "fixtures" / "taifex_tx_daily"
CSV_BYTES = (FIX / "DailyMarketReportFut_csv_recorded.csv").read_bytes()
JSON_ROWS = json.loads((FIX / "DailyMarketReportFut_json_recorded.json").read_text(encoding="utf-8"))["payload"]
PROV = json.loads((FIX / "provenance.json").read_text(encoding="utf-8"))["csv_recorded"]


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200, ctype: str = "application/octet-stream") -> None:
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fetch_with(monkeypatch, body: bytes, **kw):
    calls: list[str] = []

    def fake_open(request, *, caller, timeout):
        calls.append(request.full_url)
        return _Resp(body, **kw)

    monkeypatch.setattr(tw, "open_external_url", fake_open)
    monkeypatch.setattr(tw, "OFFICIAL_FETCH_BACKOFF_SECONDS", 0)
    adapter = tw.TaifexDerivativesChipAdapter(dataset="taifex_tx_daily", contracts=["TX", "MTX"])
    return adapter, calls


def _json_as_text(rows) -> bytes:
    out = io.StringIO()
    inverse = tw._TX_DAILY_ENGLISH_TO_TEXT
    w = csv.DictWriter(out, fieldnames=[inverse[k] for k in rows[0]])
    w.writeheader()
    for r in rows:
        w.writerow({inverse[k]: v for k, v in r.items()})
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def test_recorded_csv_has_provenance_and_is_the_real_text_form() -> None:
    import hashlib

    assert hashlib.sha256(CSV_BYTES).hexdigest() == PROV["sha256"]
    assert len(CSV_BYTES) == PROV["byte_count"] == 181749
    assert CSV_BYTES.startswith(b"\xef\xbb\xbf")
    assert CSV_BYTES.decode("utf-8-sig").splitlines()[0] == PROV["header_line"]
    with pytest.raises(json.JSONDecodeError):  # what the JSON-only adapter hit on 3 attempts
        json.loads(CSV_BYTES.decode("utf-8"))


def test_fetch_reads_the_recorded_text_response_in_one_attempt(monkeypatch) -> None:
    adapter, calls = _fetch_with(monkeypatch, CSV_BYTES)
    rows = adapter.fetch_payload()
    assert len(calls) == 1
    assert len(rows) == 38 and {r["Contract"] for r in rows} == {"TX", "MTX"}
    norm = adapter.normalized_rows_from_payload(rows, dataset="taifex_tx_daily")
    assert {r["date"] for r in norm} == {"2026-11-17"}
    assert {r["trading_session"] for r in norm} == {"regular", "after_hours"}
    assert not any("/" in r["contract_month"] for r in norm)  # calendar spreads skipped as before


def test_row_selection_rule_on_the_recorded_text_response() -> None:
    rows = tw._parse_tx_daily_body(CSV_BYTES)
    assert len(rows) == 2342
    selected = tw.taifex_tx_daily_select_rows(rows, ["TX", "MTX"])
    assert len(selected) == 38  # every TX/MTX row, both sessions, before any cap
    norm = tw.taifex_tx_daily_normalized_rows(rows, contracts=["TX", "MTX"])
    # 38 rows minus the calendar-spread rows ("/" in month); sessions kept apart by TradingSession.
    assert len(norm) == len([r for r in selected if "/" not in r["ContractMonth(Week)"]])
    ids = {r["contract_id"] for r in norm}
    assert "taifex:TX:202611:regular" in ids and "taifex:TX:202611:after_hours" in ids
    tx = next(r for r in norm if r["contract_id"] == "taifex:TX:202611:regular")
    assert tx["last_price"] == 40073.0 and tx["settlement_price"] == 40011.0 and tx["open_interest"] == 19858
    ah = next(r for r in norm if r["contract_id"] == "taifex:TX:202611:after_hours")
    assert ah["settlement_price"] is None and ah["open_interest"] is None  # NULL / - kept, never zero


def test_json_and_text_forms_give_identical_normalized_rows() -> None:
    text_rows = tw._parse_tx_daily_body(_json_as_text(JSON_ROWS))
    json_rows = tw._parse_tx_daily_body(json.dumps(JSON_ROWS).encode("utf-8"))
    a = tw.taifex_tx_daily_normalized_rows(json_rows, available_time="2026-11-14T00:00:00Z")
    b = tw.taifex_tx_daily_normalized_rows(text_rows, available_time="2026-11-14T00:00:00Z")
    strip = lambda rs: [{k: v for k, v in r.items() if k != "ingest_time"} for r in rs]
    assert strip(a) == strip(b) and a


def test_column_mapping_is_explicit_and_complete() -> None:
    header = PROV["header_line"].split(",")
    assert list(tw._TX_DAILY_TEXT_COLUMNS) == header
    assert list(tw._TX_DAILY_TEXT_COLUMNS.values()) == list(JSON_ROWS[0])


@pytest.mark.parametrize("renamed", ["開盤價", "交易時段", "日期", "結算價"])
def test_renamed_or_missing_text_column_fails_closed_naming_it(renamed) -> None:
    body = CSV_BYTES.replace(renamed.encode(), b"X" + renamed.encode(), 1)
    with pytest.raises(tw.SourceEvidenceError) as err:
        tw.taifex_tx_daily_normalized_rows(tw._parse_tx_daily_body(body))
    assert renamed in str(err.value)


@pytest.mark.parametrize(
    "body,ctype",
    [(b"<html><body>Service Unavailable</body></html>", "text/html"), (b"\x00\x01\x02binary", "application/octet-stream"),
     (b"", "text/plain"), (b"a,b\n1,2\n", "text/csv")],
)
def test_unparseable_body_states_status_type_bytes_and_prefix_and_is_not_retried(monkeypatch, body, ctype) -> None:
    adapter, calls = _fetch_with(monkeypatch, body, status=200, ctype=ctype)
    with pytest.raises(tw.SourceEvidenceError) as err:
        adapter.fetch_payload()
    msg = str(err.value)
    assert len(calls) == 1
    assert "HTTP status 200" in msg and f"content-type {ctype}" in msg and f"{len(body)} bytes" in msg
    assert repr(body[:80]) in msg


def test_unparseable_body_prefix_is_capped_at_80_bytes(monkeypatch) -> None:
    body = b"<html>" + b"x" * 500
    adapter, _ = _fetch_with(monkeypatch, body, ctype="text/html")
    with pytest.raises(tw.SourceEvidenceError) as err:
        adapter.fetch_payload()
    assert repr(body[:80]) in str(err.value) and repr(body[:81]) not in str(err.value)
