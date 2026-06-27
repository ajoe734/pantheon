from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from services.source_ingestion.configured import JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
from services.source_ingestion.ingest_manager import IngestManager
from services.source_ingestion.persona_source_reconciler import SourceProvisioningReconciler


def _persona(*, connector_candidates: list[str] | None = None, source_class: str = "live_pull") -> dict:
    return {
        "persona_id": "persona-alpha",
        "name": "Alpha Momentum",
        "mandate": "Trade TW daily momentum",
        "lifecycle_state": "research_only",
        "created_at": "2026-06-01T00:00:00Z",
        "required_data_sources": [
            {
                "dataset": "tw_price_daily",
                "market": "TW",
                "cadence": "daily",
                "source_class": source_class,
                "connector_candidates": connector_candidates or ["tw-finmind-datasets"],
                "policy_gates": ["require_connector_approved", "require_schedule_active"],
            }
        ],
    }


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def _reconciler(tmp_path: Path) -> tuple[SourceProvisioningReconciler, JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore]:
    connector_store = JsonlConfiguredConnectorStore(tmp_path / "connector_config.jsonl")
    schedule_store = JsonlConnectorScheduleStore(tmp_path / "connector_schedule.jsonl")
    return (
        SourceProvisioningReconciler(
            manager=IngestManager(),
            connector_store=connector_store,
            schedule_store=schedule_store,
        ),
        connector_store,
        schedule_store,
    )


def test_tw_price_daily_requirement_creates_connector_and_schedule(tmp_path: Path) -> None:
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)

    result = reconciler.reconcile_persona(_persona())

    assert result.summary["mutated"] == 1
    action = result.actions[0]
    assert action.connector_id == "tw-finmind-datasets"
    assert action.connector_action == "created"
    assert action.schedule_action == "created"
    config = connector_store.get_config("tw-finmind-datasets")
    assert config is not None
    assert config.connector.source_type.value == "market"
    assert config.fetch["mode"] == "provider_owned_adapter"
    schedule = schedule_store.get_schedule("tw-finmind-datasets")
    assert schedule is not None
    assert schedule.enabled is True
    assert schedule.interval_seconds == 86400


def test_duplicate_reconcile_tick_does_not_append_duplicate_records(tmp_path: Path) -> None:
    reconciler, _, _ = _reconciler(tmp_path)
    connector_path = tmp_path / "connector_config.jsonl"
    schedule_path = tmp_path / "connector_schedule.jsonl"

    first = reconciler.reconcile_persona(_persona())
    assert first.summary["mutated"] == 1
    first_connector_lines = _line_count(connector_path)
    first_schedule_lines = _line_count(schedule_path)

    second = reconciler.reconcile_persona(_persona())

    assert second.summary["satisfied"] == 1
    assert second.actions[0].connector_action == "verified"
    assert second.actions[0].schedule_action == "verified"
    assert _line_count(connector_path) == first_connector_lines
    assert _line_count(schedule_path) == first_schedule_lines


def test_missing_schedule_is_repaired_on_next_reconcile(tmp_path: Path) -> None:
    reconciler, connector_store, _ = _reconciler(tmp_path)
    reconciler.reconcile_persona(_persona())

    repaired_schedule_store = JsonlConnectorScheduleStore(tmp_path / "recreated_connector_schedule.jsonl")
    repaired = SourceProvisioningReconciler(
        manager=IngestManager(),
        connector_store=connector_store,
        schedule_store=repaired_schedule_store,
    ).reconcile_persona(_persona())

    assert repaired.actions[0].connector_action == "verified"
    assert repaired.actions[0].schedule_action == "created"
    assert repaired_schedule_store.get_schedule("tw-finmind-datasets") is not None


def test_missing_connector_is_repaired_on_next_reconcile(tmp_path: Path) -> None:
    _, _, schedule_store = _reconciler(tmp_path)

    repaired_connector_store = JsonlConfiguredConnectorStore(tmp_path / "recreated_connector_config.jsonl")
    repaired = SourceProvisioningReconciler(
        manager=IngestManager(),
        connector_store=repaired_connector_store,
        schedule_store=schedule_store,
    ).reconcile_persona(_persona())

    assert repaired.actions[0].connector_action == "created"
    assert repaired.actions[0].schedule_action == "created"
    assert repaired_connector_store.get_config("tw-finmind-datasets") is not None


def test_seed_only_requirement_is_not_provisioned(tmp_path: Path) -> None:
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)

    result = reconciler.reconcile_persona(_persona(source_class="seed_only"))

    assert result.summary["skipped"] == 1
    assert connector_store.list_configs() == []
    assert schedule_store.list_schedules() == []


def test_default_tw_price_daily_candidate_uses_official_connector(tmp_path: Path) -> None:
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)
    persona = _persona(connector_candidates=[])
    persona["required_data_sources"][0]["connector_candidates"] = []

    result = reconciler.reconcile_persona(persona)

    assert result.actions[0].connector_id == "tw-twse-tpex-official-market"
    assert connector_store.get_config("tw-twse-tpex-official-market") is not None
    assert schedule_store.get_schedule("tw-twse-tpex-official-market").interval_seconds == 86400


def test_persona_source_provisioning_api_smoke() -> None:
    tempdir = tempfile.mkdtemp(prefix="source_ingest_persona_reconcile_")
    env_backup = {
        "SOURCE_INGEST_DATA_DIR": os.environ.get("SOURCE_INGEST_DATA_DIR"),
        "SOURCE_INGEST_MAX_RECORDS": os.environ.get("SOURCE_INGEST_MAX_RECORDS"),
    }
    os.environ["SOURCE_INGEST_DATA_DIR"] = tempdir
    os.environ["SOURCE_INGEST_MAX_RECORDS"] = "10"
    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.import_module("services.source_ingestion.main")
    module = importlib.reload(module)

    try:
        client = TestClient(module.app)
        first = client.post(
            "/api/source-ingest/persona-source-provisioning/reconcile",
            json={"persona": _persona()},
        )
        assert first.status_code == 200, first.text
        assert first.json()["summary"]["mutated"] == 1

        connector = client.get("/api/source-ingest/connectors/tw-finmind-datasets")
        assert connector.status_code == 200, connector.text
        schedule = client.get("/api/source-ingest/connectors/tw-finmind-datasets/schedule")
        assert schedule.status_code == 200, schedule.text
        assert schedule.json()["schedule"]["interval_seconds"] == 86400

        second = client.post(
            "/api/source-ingest/persona-source-provisioning/reconcile",
            json={"persona": _persona()},
        )
        assert second.status_code == 200, second.text
        assert second.json()["summary"]["satisfied"] == 1
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
