"""Every TAIFEX OpenAPI dataset is read in both served forms (TW-TAIFEX-OPENAPI-ALL-DATASETS-BOTH-FORMS-20261118)."""
from __future__ import annotations

import csv
import hashlib
import inspect
import io
import json
import re
from pathlib import Path

import pytest

from services.source_ingestion.connectors import taiwan_official as tw

FIX = Path(__file__).resolve().parent / "fixtures" / "taifex_openapi_forms"
CSV_BYTES = (FIX / "institutional_text_recorded.csv").read_bytes()
JSON_DERIVED = (FIX / "institutional_json_derived.json").read_bytes()
PCR_BYTES = (FIX / "put_call_ratio_json_recorded.json").read_bytes()
PROV = json.loads((FIX / "provenance.json").read_text(encoding="utf-8"))
INST = "MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"
KW = dict(source_dataset="x", api_endpoint="https://openapi.taifex.com.tw/v1/x", available_time="2026-11-17T16:30:00Z")


def _norm(dataset: str, rows) -> list[dict]:
    adapter = tw.TaifexDerivativesChipAdapter(dataset=dataset)
    out = adapter.normalized_rows_from_payload(rows, dataset=dataset, **KW)
    # ingest_time is the read clock; everything else must be identical
    return [{k: v for k, v in r.items() if k != "ingest_time"} for r in out]


def test_recorded_fixtures_match_provenance() -> None:
    for name, data in (("institutional_text_recorded.csv", CSV_BYTES), ("put_call_ratio_json_recorded.json", PCR_BYTES),
                       ("institutional_json_derived.json", JSON_DERIVED)):
        assert hashlib.sha256(data).hexdigest() == PROV[name]["sha256"]
    assert PROV["institutional_json_derived.json"]["derived"] is True
    assert PROV["institutional_text_recorded.csv"]["derived"] is False
    assert CSV_BYTES.startswith(b"\xef\xbb\xbf")
    with pytest.raises(json.JSONDecodeError):  # what the JSON-only reader hit
        json.loads(CSV_BYTES.decode("utf-8"))


def test_institutional_text_and_json_forms_normalize_to_identical_rows() -> None:
    text_rows = tw._parse_taifex_futures_chip_body(CSV_BYTES)
    json_rows = tw._parse_taifex_futures_chip_body(JSON_DERIVED)
    assert len(text_rows) == len(json_rows) == 66
    a = _norm("taifex_futures_chip", text_rows)
    b = _norm("taifex_futures_chip", json_rows)
    assert a == b and len(a) == 66
    # same identity: every product keeps its own contract, no collapse onto TX
    ids = {(r["date"], r["contract"], r["participant_group"]) for r in a}
    assert len(ids) == 66
    foreign_tx = next(r for r in a if r["contract"] == "TX" and r["participant_group"] == "外資及陸資")
    assert (foreign_tx["long_volume"], foreign_tx["short_volume"], foreign_tx["net_volume"]) == (153931, 155075, -1144)
    assert (foreign_tx["long_open_interest"], foreign_tx["short_open_interest"], foreign_tx["net_open_interest"]) == (17052, 114948, -97896)


def test_put_call_ratio_recorded_json_goes_through_the_shared_reader() -> None:
    rows = tw._parse_taifex_options_chip_body(PCR_BYTES)
    assert list(rows) == json.loads(PCR_BYTES.decode("utf-8"))
    out = _norm("taifex_options_chip", rows)
    assert out == _norm("taifex_options_chip", json.loads(PCR_BYTES))
    assert out[0]["date"] == "2026-11-17" and out[0]["put_volume"] == 218602 and out[0]["call_open_interest"] == 193859


def test_put_call_ratio_in_text_form_equals_json_form() -> None:
    rows = json.loads(PCR_BYTES)
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    text = b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")
    assert _norm("taifex_options_chip", tw._parse_taifex_options_chip_body(text)) == _norm("taifex_options_chip", rows)


def test_text_response_with_missing_column_fails_closed_naming_dataset_and_column() -> None:
    lines = CSV_BYTES.decode("utf-8-sig").splitlines()
    header = lines[0].split(",")
    drop = header.index("多空未平倉口數淨額")
    body = "\n".join(",".join(c for i, c in enumerate(l.split(",")) if i != drop) for l in lines).encode("utf-8")
    with pytest.raises(tw.SourceEvidenceError) as exc:
        tw._parse_taifex_futures_chip_body(body)
    assert INST in str(exc.value) and "format changed" in str(exc.value) and "多空未平倉口數淨額" in str(exc.value)


def test_renamed_column_is_reported_not_guessed() -> None:
    body = CSV_BYTES.replace("商品名稱".encode(), "商品全名".encode())
    with pytest.raises(tw.SourceEvidenceError, match="商品名稱"):
        tw._parse_taifex_futures_chip_body(body)


@pytest.mark.parametrize("body", [b"<html><body>Service Unavailable</body></html>", b"", b"   \n", b"\xff\xfe\x00bad"])
def test_body_that_is_neither_form_names_dataset_and_first_bytes(body: bytes) -> None:
    with pytest.raises(tw.SourceEvidenceError) as exc:
        tw._parse_taifex_options_chip_body(body)
    msg = str(exc.value)
    assert "PutCallRatio" in msg
    assert "first bytes" in msg or "format changed" in msg
    if isinstance(exc.value, tw.OfficialBodyNotParseable):
        assert "first bytes" in msg


def test_no_empty_success_for_a_json_array_of_nothing() -> None:
    with pytest.raises(tw.OfficialBodyNotParseable):
        tw._parse_taifex_futures_chip_body(b"[]")


def _endpoint_names_in_source() -> set[str]:
    src = inspect.getsource(tw)
    names = set(re.findall(r"\{TAIFEX_OPENAPI_BASE_URL\}/([A-Za-z0-9_]+)", src))
    names |= set(re.findall(r"openapi\.taifex\.com\.tw/v1/([A-Za-z0-9_]+)", src))
    return names


def test_every_requestable_taifex_endpoint_has_a_both_forms_reader() -> None:
    requestable = set(_endpoint_names_in_source())
    for ds in ("taifex_options_chip", "taifex_futures_chip", tw.TAIFEX_TX_DAILY_DATASET):
        requestable.add(tw._taifex_endpoint(ds).rsplit("/", 1)[-1])
    requestable |= {u.rsplit("/", 1)[-1] for u in tw.TAIFEX_SSF_OPENAPI_ENDPOINTS.values()}
    assert requestable == set(tw.TAIFEX_OPENAPI_BODY_READERS), (
        "a TAIFEX endpoint was added without a reader that covers both served forms"
    )


@pytest.mark.parametrize("name", sorted(tw.TAIFEX_OPENAPI_BODY_READERS))
def test_every_reader_reads_the_json_array_and_the_text_form(name: str) -> None:
    reader = tw.TAIFEX_OPENAPI_BODY_READERS[name]
    if name == INST:
        json_body, text_body = JSON_DERIVED, CSV_BYTES
    elif name == "PutCallRatio":
        rows = json.loads(PCR_BYTES)
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
        json_body, text_body = PCR_BYTES, b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")
    elif name == "DailyMarketReportFut":
        eng = list(tw._TX_DAILY_ENGLISH_TO_TEXT)
        row = {k: "1" for k in eng}
        row.update(Date="20261117", Contract="TX")
        json_body = json.dumps([row]).encode()
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow([tw._TX_DAILY_ENGLISH_TO_TEXT[k] for k in eng])
        w.writerow([row[k] for k in eng])
        text_body = b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")
    else:  # SSF tables: any rows, both forms
        json_body = json.dumps([{"Contract": "CAF", "Value": "1"}]).encode()
        text_body = b"\xef\xbb\xbfContract,Value\nCAF,1\n"
    a, b = reader(json_body), reader(text_body)
    assert len(a) == len(b) >= 1
