from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from services.source_ingestion.connectors.finmind_taiwan import (
    FinMindTaiwanBrokerDailyReportAdapter,
    _safe_request_endpoint,
)
from services.source_ingestion.runtime import SourceIngestionRuntime
from services.source_ingestion.tests.test_finmind_taiwan_connectors import (
    _BROKER_TOKEN_PATH,
    BROKER_DAILY_REPORT_PAYLOAD,
    _allow_guarded_urls,  # noqa: F401
    _broker_refresh,
    _mock_http_response,
)

REQUIRED = {
    "provider", "dataset", "available_time", "api_endpoint",
    "access_scope", "license_scope", "schema_hash",
}


def _readback(record):
    return SourceIngestionRuntime._source_record_readback(None, record)


def test_broker_record_carries_actual_request_url_into_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINMIND_API_TOKEN", "valid-token")
    seen = []

    def _open(request, *a, **k):
        seen.append(request.full_url)
        return _mock_http_response(BROKER_DAILY_REPORT_PAYLOAD)

    with patch(_BROKER_TOKEN_PATH, side_effect=_open):
        records = _broker_refresh({"symbols": ["2330"], "run_date": "2026-06-08"})
    assert len(seen) == 1
    for record in records:
        assert record.metadata["api_endpoint"] == seen[0]
        assert record.metadata["source_url"] == seen[0]
        provenance = _readback(record)["provenance"]
        assert provenance["api_endpoint"] == seen[0]
        assert provenance.get("provider") == "FinMind"
        assert "valid-token" not in json.dumps(provenance)
        assert not (REQUIRED - {"source_ingest_run_id"} - {k for k, v in provenance.items() if v})


def test_missing_request_url_does_not_invent_api_endpoint() -> None:
    records = FinMindTaiwanBrokerDailyReportAdapter().records_from_daily_report_payload(
        BROKER_DAILY_REPORT_PAYLOAD, symbol="2330", trade_date="2026-06-08"
    )
    assert records
    assert all("api_endpoint" not in r.metadata for r in records)
    assert all(r.metadata["source_url"] for r in records)
    assert "api_endpoint" not in _readback(records[0])["provenance"]


@pytest.mark.parametrize(
    "url",
    [
        "https://api.finmindtrade.com/api/v4/x?token=abc",
        "https://u:p@api.finmindtrade.com/x",
        "ftp://api.finmindtrade.com/x",
        "",
        None,
    ],
)
def test_unsafe_request_url_rejected(url) -> None:
    assert _safe_request_endpoint(url) is None
    records = FinMindTaiwanBrokerDailyReportAdapter().records_from_daily_report_payload(
        BROKER_DAILY_REPORT_PAYLOAD, symbol="2330", trade_date="2026-06-08", source_url=url
    )
    assert all("api_endpoint" not in r.metadata for r in records)
