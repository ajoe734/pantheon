from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

import services.telemetry.main as _main
from services.telemetry.ingest_svc import TelemetryIngestService
from services.telemetry.main import _DEFAULT_SCHEMA_PATH, _build_service, startup


def test_build_service_fails_when_configured_schema_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing_path = tmp_path / "nonexistent.schema.json"
    monkeypatch.setenv("TELEMETRY_SCHEMA_PATH", str(missing_path))
    with pytest.raises(FileNotFoundError, match="Configured telemetry schema file does not exist"):
        _build_service()


def test_startup_fails_when_configured_schema_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing_path = tmp_path / "nonexistent.schema.json"
    monkeypatch.setenv("TELEMETRY_SCHEMA_PATH", str(missing_path))
    try:
        with pytest.raises(FileNotFoundError, match="Configured telemetry schema file does not exist"):
            startup()
    finally:
        _main.shutdown()


def test_build_service_succeeds_and_validates_with_default_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEMETRY_SCHEMA_PATH", raising=False)
    assert Path(_DEFAULT_SCHEMA_PATH).is_file()
    svc = _build_service()
    assert svc._schema is not None

    valid_event = {
        "event_id": str(uuid.uuid4()),
        "event_type": "heartbeat",
        "created_at": "2026-10-06T12:00:00Z",
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": "test-binding",
        "runtime_id": "test-runtime",
        "capital_pool_id": "test-pool",
        "artifact_id": "test-art",
        "artifact_version": "1.0.0",
        "plan_id": "test-plan",
        "persona_capital_binding_id": "test-pcb",
        "target": {"strategy_id": "test-strat"},
        "metrics": {"heartbeat": 1},
    }
    is_valid, err = svc._validate_event(valid_event)
    assert is_valid is True
    assert err is None

    invalid_event = {
        "event_id": "bad-event",
        "event_type": "order_submitted",
    }
    is_valid, err = svc._validate_event(invalid_event)
    assert is_valid is False
    assert err is not None


def test_telemetry_ingest_service_missing_schema_rejects_events(tmp_path: Path) -> None:
    missing_path = tmp_path / "nonexistent.schema.json"
    svc = TelemetryIngestService(schema_path=str(missing_path))
    for event_type in ("order_submitted", "trade_journal_entry"):
        valid, err = svc._validate_event({"event_type": event_type})
        assert valid is False
        assert err == "Telemetry schema is unavailable"


def test_telemetry_ingest_service_no_schema_passes_through() -> None:
    svc = TelemetryIngestService(schema_path=None)
    valid, err = svc._validate_event({"event_type": "order_submitted"})
    assert valid is True
    assert err is None
