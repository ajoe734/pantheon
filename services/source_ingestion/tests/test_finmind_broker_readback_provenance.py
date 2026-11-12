"""FinMind broker request provenance: live handler -> adapter -> persist -> terminal readback."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.knowledge.evidence import EvidenceBundleBuilder, InMemoryEvidenceRepository
from services.source_ingestion.connectors.finmind_taiwan import (
    FinMindFetchError,
    FinMindTaiwanBrokerDailyReportAdapter,
    _safe_request_endpoint,
)
from services.source_ingestion.controller_worker import ControllerTickError
from services.source_ingestion.pipeline import persist_source_evidence_refs
from services.source_ingestion.runtime import SourceIngestionRuntime
from services.source_ingestion.tests.test_controller_worker import (
    _actual_readback,
    _reconcile,
    _schedule,
    _validate_terminal_readback,
)
from services.source_ingestion.tests.test_finmind_taiwan_connectors import (
    _BROKER_TOKEN_PATH,
    BROKER_DAILY_REPORT_PAYLOAD,
    _allow_guarded_urls,  # noqa: F401
    _broker_refresh,
    _mock_http_response,
)

RUN_ID = "ingest-broker-readback-test"
BROKER_CONNECTOR_ID = "finmind-taiwan-broker-daily-report"
TOKEN = "dummy-secret-token"


def _persist(records):
    repository = InMemoryEvidenceRepository()
    result = SimpleNamespace(
        records=list(records),
        run=SimpleNamespace(
            ingest_run_id=RUN_ID, connector_id=BROKER_CONNECTOR_ID, trigger_type="manual", trace_id="trace-broker"
        ),
    )
    persist_source_evidence_refs(
        SimpleNamespace(get_connector=lambda _: None),
        repository,
        EvidenceBundleBuilder(repository),
        None,
        result,
    )
    return repository.list_source_records()


def _terminal(source_record, latest_run_id: str = RUN_ID):
    """Build the controller readback from the persisted record and run the real validator."""
    readback = SourceIngestionRuntime._source_record_readback(None, source_record)
    actual = _actual_readback()
    item = actual["connectors"][0]
    item["connector_id"] = item["connector"]["connector_id"] = item["schedule"]["connector_id"] = BROKER_CONNECTOR_ID
    item["desired_state"]["dataset"] = "tw_broker_top"
    item["freshness"]["last_ingest_run_id"] = latest_run_id
    item["freshness"]["latest_run"]["ingest_run_id"] = latest_run_id
    item["source_health"]["source_id"] = BROKER_CONNECTOR_ID
    item["source_health"]["metadata"]["last_ingest_run_id"] = latest_run_id
    readback["connector_id"] = BROKER_CONNECTOR_ID
    readback["provenance"]["available_time"] = actual["captured_at"]
    item["latest_source_record"] = readback
    reconcile = _reconcile()
    reconcile["results"][0]["actions"][0].update(dataset="tw_broker_top", connector_id=BROKER_CONNECTOR_ID)
    _validate_terminal_readback(reconcile=reconcile, schedule=_schedule(), actual=actual)
    return readback


def _live_records(monkeypatch):
    monkeypatch.setenv("FINMIND_API_TOKEN", TOKEN)
    seen = []

    def _open(request, *a, **k):
        seen.append(request.full_url)
        return _mock_http_response(BROKER_DAILY_REPORT_PAYLOAD)

    with patch(_BROKER_TOKEN_PATH, side_effect=_open):
        records = _broker_refresh({"symbols": ["2330"], "run_date": "2026-06-08"})
    assert len(seen) == 1
    return seen[0], records


def test_live_request_url_survives_persist_and_terminal_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    request_url, records = _live_records(monkeypatch)
    persisted = _persist(records)
    assert persisted
    for record in persisted:
        readback = _terminal(record)
        provenance = readback["provenance"]
        assert provenance["api_endpoint"] == request_url
        assert provenance["source_ingest_run_id"] == RUN_ID
        assert provenance["provider"] == "FinMind"
        assert provenance["dataset"] == "tw_broker_top"
        assert record.metadata["source_url"] == request_url
        assert TOKEN not in json.dumps(readback, default=str)


def test_missing_request_url_fails_terminal_readback_honestly() -> None:
    records = FinMindTaiwanBrokerDailyReportAdapter().records_from_daily_report_payload(
        BROKER_DAILY_REPORT_PAYLOAD, symbol="2330", trade_date="2026-06-08"
    )
    assert records and all("api_endpoint" not in r.metadata for r in records)
    persisted = _persist(records)
    assert all(r.metadata["source_url"] for r in persisted)
    with pytest.raises(ControllerTickError, match=r"provenance_missing\[api_endpoint\]"):
        _terminal(persisted[0])


def test_contradictory_run_identity_fails_terminal_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    _, records = _live_records(monkeypatch)
    record = _persist(records)[0]
    with pytest.raises(ControllerTickError, match="record_run_mismatch"):
        _terminal(record, latest_run_id="some-other-run")


@pytest.mark.parametrize(
    "url",
    [
        f"https://api.finmindtrade.com/api/v4/data?dataset=x&token={TOKEN}",
        f"https://u:{TOKEN}@api.finmindtrade.com/api/v4/data",
        "ftp://api.finmindtrade.com/x",
    ],
)
def test_unsafe_request_url_rejected_before_any_record(url: str) -> None:
    assert _safe_request_endpoint(url) is None
    with pytest.raises(FinMindFetchError) as raised:
        FinMindTaiwanBrokerDailyReportAdapter().records_from_daily_report_payload(
            BROKER_DAILY_REPORT_PAYLOAD, symbol="2330", trade_date="2026-06-08", source_url=url
        )
    assert TOKEN not in str(raised.value)
