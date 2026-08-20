from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient


def _load_source_main(monkeypatch: Any, tmp_path: Path) -> Any:
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOURCE_INGEST_MAX_RECORDS", "10")
    monkeypatch.setenv("SEARCH_INGEST_NOTIFY_URL", "")
    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.import_module("services.source_ingestion.main")
    return importlib.reload(module)


def _connector() -> dict[str, Any]:
    return {
        "connector_id": "stored-price-test",
        "source_type": "market",
        "provider": "Stored normalized test source",
        "license_scope": "internal",
        "metadata": {"dataset": "us_equity_price_daily"},
    }


def _record(*, source_id: str, event_time: str, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"AAPL.US close {event_time}",
        "content_ref": f"source-test://AAPL.US/{event_time}",
        "metadata": {
            "normalized_row": {
                "schema_version": "us_equity_price_daily.v1",
                "symbol_canonical": "AAPL.US",
                "trade_date": event_time,
                "close": close,
            }
        },
    }


def _ingest_prices(client: TestClient) -> None:
    configured = client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(),
            "fetch": {
                "mode": "static_records",
                "records": [
                    _record(
                        source_id="aapl-2026-08-18",
                        event_time="2026-08-18T20:00:00Z",
                        close=220.0,
                    ),
                    _record(
                        source_id="aapl-2026-08-19",
                        event_time="2026-08-19T20:00:00Z",
                        close=224.5,
                    ),
                    _record(
                        source_id="aapl-2026-08-20",
                        event_time="2026-08-20T20:00:00Z",
                        close=223.0,
                    ),
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text
    ingested = client.post(
        "/api/source-ingest/jobs",
        json={"connector_id": "stored-price-test", "trace_id": "snapshot-contract-test"},
    )
    assert ingested.status_code == 201, ingested.text
    assert ingested.json()["run"]["status"] == "completed"
    assert ingested.json()["evidence_refs"]["market_snapshots"]["updated_snapshot_count"] == 1


def test_latest_market_snapshot_returns_one_stored_normalized_contract(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    module = _load_source_main(monkeypatch, tmp_path)
    client = TestClient(module.app)
    _ingest_prices(client)

    response = client.get("/api/source-ingest/snapshots/latest?symbol=AAPL.US")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "source_ingest_latest_market_snapshot.v1"
    assert body["symbol"] == "AAPL.US"
    assert body["closes"] == [220.0, 224.5, 223.0]
    assert body["event_time"] == "2026-08-20T20:00:00Z"
    assert body["snapshot_id"].startswith("mss-")
    assert body["source_ref"] == f"source-ingest://snapshots/{body['snapshot_id']}"
    assert body["lineage"]["source_ids"] == [
        "aapl-2026-08-18",
        "aapl-2026-08-19",
        "aapl-2026-08-20",
    ]
    assert body["lineage"]["connector_ids"] == ["stored-price-test"]


def test_snapshot_read_performs_no_provider_egress_or_scheduler_work(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    module = _load_source_main(monkeypatch, tmp_path)
    client = TestClient(module.app)
    _ingest_prices(client)

    class ForbiddenScheduler:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"snapshot read must not access scheduler.{name}")

    monkeypatch.setattr(module, "scheduler", ForbiddenScheduler())
    with patch("urllib.request.urlopen", side_effect=AssertionError("snapshot read must not egress")):
        response = client.get("/api/source-ingest/snapshots/latest?symbol=AAPL.US")

    assert response.status_code == 200, response.text
    assert response.json()["closes"][-1] == 223.0


def test_missing_snapshot_has_a_typed_not_found_response(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    module = _load_source_main(monkeypatch, tmp_path)
    response = TestClient(module.app).get(
        "/api/source-ingest/snapshots/latest?symbol=MISSING.US"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "market_snapshot_not_found",
        "symbol": "MISSING.US",
    }
