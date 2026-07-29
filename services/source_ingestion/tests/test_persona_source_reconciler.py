from __future__ import annotations

import importlib
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.source_ingestion.configured import JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
from services.source_ingestion.connectors.base import SourceConnector, SourceEvidenceError
from services.source_ingestion.connectors.taiwan_official import TaiwanOfficialMarketDatasetAdapter
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


@pytest.fixture()
def persona_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOURCE_INGEST_MAX_RECORDS", "10")
    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.import_module("services.source_ingestion.main")
    module = importlib.reload(module)
    yield TestClient(module.app), module


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


def test_controller_owned_connector_drift_is_repaired(tmp_path: Path) -> None:
    reconciler, connector_store, _ = _reconciler(tmp_path)
    persona = _persona(connector_candidates=[])
    persona["required_data_sources"][0]["connector_candidates"] = []
    reconciler.reconcile_persona(persona)
    connector_id = "tw-twse-tpex-official-market"
    existing = connector_store.get_config(connector_id)
    assert existing is not None
    drifted_fetch = dict(existing.fetch)
    drifted_fetch["request"] = {**dict(drifted_fetch["request"]), "dataset": "tw_day_trading"}
    connector_store.upsert_config(existing.connector, drifted_fetch)

    repaired = reconciler.reconcile_persona(persona)

    assert repaired.actions[0].connector_action == "repaired"
    assert connector_store.get_config(connector_id).fetch["request"]["dataset"] == "tw_price_daily"


def test_operator_owned_connector_drift_fails_closed_as_conflict(tmp_path: Path) -> None:
    reconciler, connector_store, _ = _reconciler(tmp_path)
    provider = TaiwanOfficialMarketDatasetAdapter()
    payload = provider.connector().to_dict()
    payload["metadata"] = {**payload["metadata"], "operator_override": "retain"}
    connector_store.upsert_config(SourceConnector.from_dict(payload), provider.fetch_config())
    persona = _persona(connector_candidates=["tw-twse-tpex-official-market"])

    result = reconciler.reconcile_persona(persona)

    assert result.summary["conflicts"] == 1
    assert result.actions[0].connector_action == "conflict"
    assert result.actions[0].details["existing_owner"] is None


def test_requirement_dataset_is_projected_into_official_provider_request(tmp_path: Path) -> None:
    reconciler, connector_store, _ = _reconciler(tmp_path)
    persona = _persona(connector_candidates=["tw-twse-tpex-official-market"])
    persona["required_data_sources"][0]["dataset"] = "tw_institutional_flow"

    result = reconciler.reconcile_persona(persona)

    assert result.summary["mutated"] == 1
    config = connector_store.get_config("tw-twse-tpex-official-market")
    assert config is not None
    assert config.fetch["request"]["dataset"] == "tw_institutional_flow"
    marker = config.connector.metadata["persona_source_reconciliation"]
    assert marker["desired_state"]["dataset"] == "tw_institutional_flow"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_class", "live_pul"),
        ("cadence", "every_day"),
        ("connector_candidates", "tw-finmind-datasets"),
        ("policy_gates", ["unknown_gate"]),
    ],
)
def test_requirement_schema_and_policy_fail_closed(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)
    persona = _persona()
    persona["required_data_sources"][0][field] = value

    with pytest.raises(SourceEvidenceError):
        reconciler.reconcile_persona(persona)

    assert connector_store.list_configs() == []
    assert schedule_store.list_schedules() == []


@pytest.mark.parametrize(
    "persona",
    [
        {"persona_id": "persona-alpha"},
        {"persona_id": "persona-alpha", "required_data_sources": None},
        {"persona_id": "persona-alpha", "required_data_sources": "tw_price_daily"},
    ],
)
def test_required_data_sources_must_be_explicit_array(
    tmp_path: Path,
    persona: dict[str, object],
) -> None:
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)

    with pytest.raises(SourceEvidenceError, match="required_data_sources"):
        reconciler.reconcile_persona(persona)

    assert connector_store.list_configs() == []
    assert schedule_store.list_schedules() == []


def test_dry_run_with_existing_connector_does_not_mutate_manager_or_stores(tmp_path: Path) -> None:
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)
    provider = TaiwanOfficialMarketDatasetAdapter()
    connector_store.upsert_config(provider.connector(), provider.fetch_config())
    persona = _persona(connector_candidates=[provider.connector().connector_id])
    connector_bytes = (tmp_path / "connector_config.jsonl").read_bytes()

    result = reconciler.reconcile_persona(persona, dry_run=True)

    assert result.dry_run is True
    assert reconciler.manager.get_connector(provider.connector().connector_id) is None
    assert (tmp_path / "connector_config.jsonl").read_bytes() == connector_bytes
    assert schedule_store.list_schedules() == []


def test_multiple_requirements_cannot_overwrite_singleton_connector(tmp_path: Path) -> None:
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)
    first = _persona()
    second = deepcopy(first)
    second["persona_id"] = "persona-beta"

    with pytest.raises(SourceEvidenceError, match="singleton connector"):
        reconciler.reconcile_personas([first, second])

    assert connector_store.list_configs() == []
    assert schedule_store.list_schedules() == []


def test_failed_config_append_never_publishes_in_memory_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, connector_store, schedule_store = _reconciler(tmp_path)
    connector = TaiwanOfficialMarketDatasetAdapter().connector()

    monkeypatch.setattr(connector_store, "_append", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        connector_store.upsert_config(connector, TaiwanOfficialMarketDatasetAdapter().fetch_config())
    assert connector_store.get_config(connector.connector_id) is None

    monkeypatch.setattr(schedule_store, "_append", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        schedule_store.upsert_schedule(connector.connector_id, interval_seconds=60, enabled=True)
    assert schedule_store.get_schedule(connector.connector_id) is None


def test_persona_source_provisioning_api_smoke(persona_api) -> None:
    client, module = persona_api
    endpoint = "/api/source-ingest/persona-source-provisioning/reconcile"
    unauthorized = client.post(endpoint, json={"personas": [], "authoritative_snapshot": True})
    forbidden = client.post(
        endpoint,
        headers={"Authorization": "Bearer wrong-controller-token-that-is-long-enough"},
        json={"personas": [], "authoritative_snapshot": True},
    )
    unauthorized_non_authoritative = client.post(endpoint, json={"persona": _persona()})
    forbidden_non_authoritative = client.post(
        endpoint,
        headers={"Authorization": "Bearer wrong-controller-token-that-is-long-enough"},
        json={"persona": _persona()},
    )
    anonymous_dry_run = client.post(endpoint, json={"persona": _persona(), "dry_run": True})

    assert unauthorized.status_code == 401
    assert forbidden.status_code == 403
    assert unauthorized_non_authoritative.status_code == 401
    assert forbidden_non_authoritative.status_code == 403
    assert anonymous_dry_run.status_code == 200, anonymous_dry_run.text
    assert module.connector_store.list_configs() == []
    assert module.schedule_config_store.list_schedules() == []
    assert module.manager.get_connector("tw-finmind-datasets") is None

    controller_headers = {"Authorization": f"Bearer {module.controller_token}"}
    omitted_personas = client.post(
        endpoint,
        headers=controller_headers,
        json={
            "authoritative_snapshot": True,
            "desired_state_sha256": module._desired_state_digest([]),
        },
    )
    omitted_digest = client.post(
        endpoint,
        headers=controller_headers,
        json={"personas": [], "authoritative_snapshot": True},
    )
    assert omitted_personas.status_code == 400
    assert omitted_digest.status_code == 400
    assert module.requirement_snapshot_store.latest is None

    personas = [_persona()]
    first = client.post(
        endpoint,
        headers=controller_headers,
        json={
            "personas": personas,
            "authoritative_snapshot": True,
            "desired_state_sha256": module._desired_state_digest(personas),
        },
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["summary"]["mutated"] == 1
    assert first_payload["authoritative_snapshot"] is True
    assert len(first_payload["desired_state_sha256"]) == 64
    assert first_payload["pre_readback"]["connector_count"] == 0
    assert first_payload["post_readback"]["connector_count"] == 1

    connector = client.get("/api/source-ingest/connectors/tw-finmind-datasets")
    assert connector.status_code == 200, connector.text
    schedule = client.get("/api/source-ingest/connectors/tw-finmind-datasets/schedule")
    assert schedule.status_code == 200, schedule.text
    assert schedule.json()["schedule"]["interval_seconds"] == 86400

    second = client.post(
        endpoint,
        headers=controller_headers,
        json={
            "personas": [_persona()],
            "authoritative_snapshot": True,
            "desired_state_sha256": first_payload["desired_state_sha256"],
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["summary"]["satisfied"] == 1
    readback = client.get("/api/source-ingest/controller/readback")
    assert readback.status_code == 200, readback.text
    assert readback.json()["connectors"][0]["desired_state"]["dataset"] == "tw_price_daily"


def test_two_reconcile_threads_converge_without_duplicate_connector_or_schedule(persona_api) -> None:
    client, module = persona_api
    endpoint = "/api/source-ingest/persona-source-provisioning/reconcile"
    headers = {"Authorization": f"Bearer {module.controller_token}"}
    personas = [_persona()]
    payload = {
        "personas": personas,
        "authoritative_snapshot": True,
        "desired_state_sha256": module._desired_state_digest(personas),
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _worker: client.post(endpoint, headers=headers, json=payload), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    summaries = [response.json()["summary"] for response in responses]
    assert sorted((summary["mutated"], summary["satisfied"]) for summary in summaries) == [(0, 1), (1, 0)]
    assert _line_count(module.REQUIREMENT_STATE_PATH) == 1
    assert _line_count(module.CONNECTOR_STORE_PATH) == 1
    assert _line_count(module.CONNECTOR_SCHEDULE_CONFIG_PATH) == 1
    assert len(module.connector_store.list_configs()) == 1
    assert len(module.schedule_config_store.list_schedules()) == 1


def test_authoritative_intent_is_durable_before_connector_mutation_and_retryable(
    persona_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, module = persona_api
    endpoint = "/api/source-ingest/persona-source-provisioning/reconcile"
    headers = {"Authorization": f"Bearer {module.controller_token}"}
    original_upsert = module.connector_store.upsert_config
    personas = [_persona()]
    payload = {
        "personas": personas,
        "authoritative_snapshot": True,
        "desired_state_sha256": module._desired_state_digest(personas),
    }

    def fail_config_upsert(*args, **kwargs):
        raise OSError("simulated connector store crash")

    monkeypatch.setattr(module.connector_store, "upsert_config", fail_config_upsert)
    with pytest.raises(OSError, match="simulated connector store crash"):
        client.post(
            endpoint,
            headers=headers,
            json=payload,
        )

    admitted = module.requirement_snapshot_store.latest
    assert admitted is not None
    assert admitted.sequence == 1
    assert admitted.binding_count == 1
    assert module.connector_store.list_configs() == []

    monkeypatch.setattr(module.connector_store, "upsert_config", original_upsert)
    retried = client.post(
        endpoint,
        headers=headers,
        json=payload,
    )

    assert retried.status_code == 200, retried.text
    assert retried.json()["accepted_requirement_snapshot"]["sequence"] == 1
    assert module.requirement_snapshot_store.latest.sequence == 1
    assert module.connector_store.get_config("tw-finmind-datasets") is not None


def test_controller_owned_connector_legacy_mutations_require_controller_token(persona_api) -> None:
    client, module = persona_api
    endpoint = "/api/source-ingest/persona-source-provisioning/reconcile"
    headers = {"Authorization": f"Bearer {module.controller_token}"}
    personas = [_persona()]
    provisioned = client.post(
        endpoint,
        headers=headers,
        json={
            "personas": personas,
            "authoritative_snapshot": True,
            "desired_state_sha256": module._desired_state_digest(personas),
        },
    )
    assert provisioned.status_code == 200, provisioned.text

    schedule_path = "/api/source-ingest/connectors/tw-finmind-datasets/schedule"
    unauthorized_schedule = client.put(schedule_path, json={"interval_seconds": 60, "enabled": True})
    forbidden_schedule = client.put(
        schedule_path,
        headers={"Authorization": "Bearer wrong-controller-token-that-is-long-enough"},
        json={"interval_seconds": 60, "enabled": True},
    )
    unauthorized_lifecycle = client.put(
        "/api/source-ingest/connectors/tw-finmind-datasets/lifecycle",
        json={"status": "disabled", "reason": "unauthorized drift attempt"},
    )
    unauthorized_job = client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "tw-finmind-datasets",
            "trace_id": "unauthorized-managed-job",
            "trigger_type": "manual",
        },
    )
    unauthorized_scheduled_run = client.post("/api/source-ingest/run-scheduled")

    assert unauthorized_schedule.status_code == 401
    assert forbidden_schedule.status_code == 403
    assert unauthorized_lifecycle.status_code == 401
    assert unauthorized_job.status_code == 401
    assert unauthorized_scheduled_run.status_code == 401
    authorized_schedule = client.put(
        schedule_path,
        headers=headers,
        json={"interval_seconds": 60, "enabled": True},
    )
    assert authorized_schedule.status_code == 200, authorized_schedule.text


def test_authoritative_removal_converges_after_retirement_crash_without_log_growth(
    persona_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, module = persona_api
    endpoint = "/api/source-ingest/persona-source-provisioning/reconcile"
    headers = {"Authorization": f"Bearer {module.controller_token}"}
    personas = [_persona()]
    desired_payload = {
        "personas": personas,
        "authoritative_snapshot": True,
        "desired_state_sha256": module._desired_state_digest(personas),
    }
    removal_payload = {
        "personas": [],
        "authoritative_snapshot": True,
        "desired_state_sha256": module._desired_state_digest([]),
    }
    first = client.post(
        endpoint,
        headers=headers,
        json=desired_payload,
    )
    assert first.status_code == 200, first.text
    original_upsert = module.connector_store.upsert_config

    def fail_retirement(connector, fetch):
        if connector.status.value == "disabled":
            raise OSError("simulated retirement crash")
        return original_upsert(connector, fetch)

    monkeypatch.setattr(module.connector_store, "upsert_config", fail_retirement)
    with pytest.raises(OSError, match="simulated retirement crash"):
        client.post(endpoint, headers=headers, json=removal_payload)

    assert module.requirement_snapshot_store.latest.sequence == 2
    assert dict(module.requirement_snapshot_store.latest.bindings) == {}
    assert module.connector_store.get_config("tw-finmind-datasets").connector.status.value == "enabled"

    monkeypatch.setattr(module.connector_store, "upsert_config", original_upsert)
    retried = client.post(endpoint, headers=headers, json=removal_payload)
    assert retried.status_code == 200, retried.text
    assert retried.json()["accepted_requirement_snapshot"]["sequence"] == 2
    assert retried.json()["retirement_actions"][0]["action"] == "disabled_controller_owned"
    assert module.connector_store.get_config("tw-finmind-datasets").connector.status.value == "disabled"
    assert module.schedule_config_store.get_schedule("tw-finmind-datasets").enabled is False

    connector_lines = _line_count(module.CONNECTOR_STORE_PATH)
    schedule_lines = _line_count(module.CONNECTOR_SCHEDULE_CONFIG_PATH)
    duplicate = client.post(endpoint, headers=headers, json=removal_payload)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["accepted_requirement_snapshot"]["sequence"] == 2
    assert duplicate.json()["retirement_actions"][0]["action"] == "already_disabled_controller_owned"
    assert _line_count(module.CONNECTOR_STORE_PATH) == connector_lines
    assert _line_count(module.CONNECTOR_SCHEDULE_CONFIG_PATH) == schedule_lines


def test_unsupported_authoritative_snapshot_is_not_admitted_or_mutated(persona_api) -> None:
    client, module = persona_api
    persona = _persona(source_class="live_push")
    response = client.post(
        "/api/source-ingest/persona-source-provisioning/reconcile",
        headers={"Authorization": f"Bearer {module.controller_token}"},
        json={
            "personas": [persona],
            "authoritative_snapshot": True,
            "desired_state_sha256": module._desired_state_digest([persona]),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["unsupported"] == 1
    assert response.json()["accepted_requirement_snapshot"] is None
    assert module.requirement_snapshot_store.latest is None
    assert module.connector_store.list_configs() == []
    assert module.schedule_config_store.list_schedules() == []
