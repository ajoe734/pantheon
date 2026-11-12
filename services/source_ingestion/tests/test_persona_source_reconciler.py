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


@pytest.fixture(autouse=True)
def isolated_finmind_configuration(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FINMIND_API_TOKEN", "local-test-token")


# FinMind never supplies tw_price_daily (TWSE/TPEx official stays OHLCV truth);
# FinMind-routed tests use a FinMind-only dataset instead.
_FINMIND_ONLY_DATASET = "tw_day_trading"


def _persona(
    *,
    connector_candidates: list[str] | None = None,
    source_class: str = "live_pull",
    dataset: str | None = None,
) -> dict:
    if dataset is None:
        dataset = _FINMIND_ONLY_DATASET if connector_candidates is None else "tw_price_daily"
    return {
        "persona_id": "persona-alpha",
        "name": "Alpha Momentum",
        "mandate": "Trade TW daily momentum",
        "lifecycle_state": "research_only",
        "created_at": "2026-06-01T00:00:00Z",
        "required_data_sources": [
            {
                "dataset": dataset,
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


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("token", [None, "", "   ", "local-test-token", "expired-test-token"])
def test_actual_persona_daily_requirement_prefers_public_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: bool, token: str | None,
) -> None:
    from services.control_plane.bff.personas.service import _persona_create_required_data_sources
    from services.source_ingestion.connectors.finmind_taiwan import FinMindTaiwanDatasetAdapter

    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.delenv("FINMIND_API_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("FINMIND_API_TOKEN", token)
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)
    persona = _persona()
    # Broker live_push provisioning is a separate requirement/owner.
    persona["required_data_sources"] = _persona_create_required_data_sources({"market": "TW"})[:1]
    if registered:
        provider = FinMindTaiwanDatasetAdapter()
        connector_store.upsert_config(provider.connector(), provider.fetch_config())
        reconciler.reconcile_persona(persona)

    first = reconciler.reconcile_persona(persona)
    second = reconciler.reconcile_persona(persona)

    assert first.actions[0].connector_id == "tw-twse-tpex-official-market"
    assert second.summary["satisfied"] == 1
    config = connector_store.get_config(first.actions[0].connector_id)
    assert config.connector.auth_type.value == "none"
    assert config.fetch["request"]["dataset"] == "tw_price_daily"
    assert schedule_store.get_schedule(first.actions[0].connector_id).enabled
    assert schedule_store.get_schedule("tw-finmind-datasets") is None
    assert not any((tmp_path / name).exists() for name in ("latest_market_snapshots.jsonl", "latest-market-snapshots.jsonl"))
    gates = first.actions[0].details["policy_gate_results"]
    assert gates["require_source_health_ok"]["authority"] == "terminal_actual_readback"


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("fallback", [False, True])
def test_missing_finmind_credential_respects_explicit_candidate_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: bool, fallback: bool,
) -> None:
    from services.source_ingestion.connectors.finmind_taiwan import FinMindTaiwanDatasetAdapter

    monkeypatch.delenv("FINMIND_API_TOKEN", raising=False)
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)
    if registered:
        provider = FinMindTaiwanDatasetAdapter()
        connector_store.upsert_config(provider.connector(), provider.fetch_config())
    candidates = ["tw-finmind-datasets"]
    if fallback:
        candidates.append("tw-twse-tpex-official-market")
    result = reconciler.reconcile_persona(
        _persona(connector_candidates=candidates, dataset="tw_institutional_flow")
    )
    action = result.actions[0]
    assert action.connector_id == ("tw-twse-tpex-official-market" if fallback else None)
    assert action.status == ("mutated" if fallback else "unsupported")
    assert schedule_store.get_schedule("tw-finmind-datasets") is None


@pytest.mark.parametrize("candidate", ["tw-finmind-datasets", "tw-twse-tpex-official-market"])
@pytest.mark.parametrize("market,dataset", [("TW", "unavailable_dataset"), ("US", "tw_price_daily")])
def test_registered_provider_cannot_bypass_dataset_or_market_support(
    tmp_path: Path, candidate: str, market: str, dataset: str,
) -> None:
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)
    provider = reconciler.provider_factories[candidate](candidate)
    connector_store.upsert_config(provider.connector(), provider.fetch_config())
    persona = _persona(connector_candidates=[candidate])
    persona["required_data_sources"][0].update(market=market, dataset=dataset)

    assert reconciler.reconcile_persona(persona).summary["unsupported"] == 1
    assert schedule_store.list_schedules() == []


@pytest.mark.parametrize("public_only", [False, True])
def test_explicit_finmind_preference_with_configured_key_obeys_policy(
    tmp_path: Path, public_only: bool,
) -> None:
    reconciler, connector_store, _ = _reconciler(tmp_path)
    persona = _persona(
        connector_candidates=["tw-finmind-datasets", "tw-twse-tpex-official-market"],
        dataset="tw_institutional_flow",
    )
    if public_only:
        persona["required_data_sources"][0]["policy_gates"].append("public-source-only")
    result = reconciler.reconcile_persona(persona)
    expected = "tw-twse-tpex-official-market" if public_only else "tw-finmind-datasets"
    assert result.actions[0].connector_id == expected
    assert result.summary["mutated"] == 1
    assert connector_store.get_config(expected).fetch["request"]["dataset"] == (
        "tw_institutional_flow" if public_only else "TaiwanStockInstitutionalInvestorsBuySell"
    )


@pytest.mark.parametrize("public_only", [False, True])
def test_keyed_finmind_preference_never_takes_tw_price_daily(tmp_path: Path, public_only: bool) -> None:
    """Official TWSE/TPEx stays daily OHLCV truth; FinMind price is reference-only."""
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)
    persona = _persona(connector_candidates=["tw-finmind-datasets", "tw-twse-tpex-official-market"])
    if public_only:
        persona["required_data_sources"][0]["policy_gates"].append("public-source-only")
    result = reconciler.reconcile_persona(persona)
    assert result.actions[0].connector_id == "tw-twse-tpex-official-market"
    assert connector_store.get_config("tw-finmind-datasets") is None
    assert schedule_store.get_schedule("tw-finmind-datasets") is None
    # A sole FinMind candidate for tw_price_daily is unsupported, not routed.
    sole = _persona(connector_candidates=["tw-finmind-datasets"])
    assert reconciler.reconcile_persona(sole).summary["unsupported"] == 1
    assert connector_store.get_config("tw-finmind-datasets") is None


@pytest.mark.parametrize("http_status", [200, 401, 403])
def test_explicit_keyed_selection_does_not_claim_credentials_or_data_are_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_status: int,
) -> None:
    import json
    from unittest.mock import MagicMock
    from urllib.error import HTTPError
    from services.source_ingestion.connectors import finmind_taiwan as finmind

    token = "usable-test-token" if http_status == 200 else "expired-test-token"
    monkeypatch.setenv("FINMIND_API_TOKEN", token)
    reconciler, connector_store, _ = _reconciler(tmp_path)
    result = reconciler.reconcile_persona(_persona())
    config = connector_store.get_config(result.actions[0].connector_id)
    assert config.connector.connector_id == "tw-finmind-datasets"
    fetcher = finmind.FinMindLiveFetcher(secret_ref_id=config.connector.secret_ref_id)
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = http_status
    response.headers = {}
    response.read.return_value = json.dumps({"status": 200, "data": [
        {"date": "2026-10-02", "stock_id": "2330", "close": 955.0},
    ]}).encode()

    def local_provider(request, **kwargs):
        assert request.get_header("Authorization") == f"Bearer {token}"
        if http_status != 200:
            raise HTTPError(request.full_url, http_status, "Expired credential", {}, None)
        return response

    monkeypatch.setattr(finmind, "open_external_url", local_provider)
    if http_status == 200:
        payload, _ = fetcher.fetch_dataset(config.fetch["request"]["dataset"], symbol="2330")
        records = finmind.FinMindTaiwanDatasetAdapter().records_from_data_payload(
            config.fetch["request"]["dataset"], payload,
        )
        assert records[0].metadata["raw_row"]["close"] == 955.0
        assert "normalized_row" not in records[0].metadata
        assert records[0].metadata["redistribution_allowed"] is False
        assert records[0].metadata["provider"] == "FinMind"
    else:
        with pytest.raises(finmind.FinMindCredentialError, match=f"HTTP {http_status}"):
            fetcher.fetch_dataset(config.fetch["request"]["dataset"], symbol="2330")
    assert not any((tmp_path / name).exists() for name in ("latest_market_snapshots.jsonl", "latest-market-snapshots.jsonl"))
    assert token not in json.dumps(result.to_dict())


def test_public_only_policy_rejects_a_sole_keyed_selection(tmp_path: Path) -> None:
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)
    persona = _persona()
    persona["required_data_sources"][0]["policy_gates"].append("public-source-only")
    result = reconciler.reconcile_persona(persona)
    assert result.actions[0].connector_action == "policy_gate_failed"
    assert result.actions[0].details["failed_policy_gates"] == ["public-source-only"]
    assert connector_store.list_configs() == []
    assert schedule_store.list_schedules() == []


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
    assert module.runtime.connector_store.list_configs() == []
    assert module.runtime.schedule_config_store.list_schedules() == []
    assert module.runtime.manager.get_connector("tw-finmind-datasets") is None

    controller_headers = {"Authorization": f"Bearer {module.runtime.controller_token}"}
    omitted_personas = client.post(
        endpoint,
        headers=controller_headers,
        json={
            "authoritative_snapshot": True,
            "desired_state_sha256": module.runtime._desired_state_digest([]),
        },
    )
    omitted_digest = client.post(
        endpoint,
        headers=controller_headers,
        json={"personas": [], "authoritative_snapshot": True},
    )
    assert omitted_personas.status_code == 400
    assert omitted_digest.status_code == 400
    assert module.runtime.requirement_snapshot_store.latest is None

    personas = [_persona()]
    first = client.post(
        endpoint,
        headers=controller_headers,
        json={
            "personas": personas,
            "authoritative_snapshot": True,
            "desired_state_sha256": module.runtime._desired_state_digest(personas),
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
    assert readback.json()["connectors"][0]["desired_state"]["dataset"] == _FINMIND_ONLY_DATASET


def test_two_reconcile_threads_converge_without_duplicate_connector_or_schedule(persona_api) -> None:
    client, module = persona_api
    endpoint = "/api/source-ingest/persona-source-provisioning/reconcile"
    headers = {"Authorization": f"Bearer {module.runtime.controller_token}"}
    personas = [_persona()]
    payload = {
        "personas": personas,
        "authoritative_snapshot": True,
        "desired_state_sha256": module.runtime._desired_state_digest(personas),
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _worker: client.post(endpoint, headers=headers, json=payload), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    summaries = [response.json()["summary"] for response in responses]
    assert sorted((summary["mutated"], summary["satisfied"]) for summary in summaries) == [(0, 1), (1, 0)]
    assert _line_count(module.runtime.REQUIREMENT_STATE_PATH) == 1
    assert _line_count(module.runtime.CONNECTOR_STORE_PATH) == 1
    assert _line_count(module.runtime.CONNECTOR_SCHEDULE_CONFIG_PATH) == 1
    assert len(module.runtime.connector_store.list_configs()) == 1
    assert len(module.runtime.schedule_config_store.list_schedules()) == 1


def test_authoritative_intent_is_durable_before_connector_mutation_and_retryable(
    persona_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, module = persona_api
    endpoint = "/api/source-ingest/persona-source-provisioning/reconcile"
    headers = {"Authorization": f"Bearer {module.runtime.controller_token}"}
    original_upsert = module.runtime.connector_store.upsert_config
    personas = [_persona()]
    payload = {
        "personas": personas,
        "authoritative_snapshot": True,
        "desired_state_sha256": module.runtime._desired_state_digest(personas),
    }

    def fail_config_upsert(*args, **kwargs):
        raise OSError("simulated connector store crash")

    monkeypatch.setattr(module.runtime.connector_store, "upsert_config", fail_config_upsert)
    with pytest.raises(OSError, match="simulated connector store crash"):
        client.post(
            endpoint,
            headers=headers,
            json=payload,
        )

    admitted = module.runtime.requirement_snapshot_store.latest
    assert admitted is not None
    assert admitted.sequence == 1
    assert admitted.binding_count == 1
    assert module.runtime.connector_store.list_configs() == []

    monkeypatch.setattr(module.runtime.connector_store, "upsert_config", original_upsert)
    retried = client.post(
        endpoint,
        headers=headers,
        json=payload,
    )

    assert retried.status_code == 200, retried.text
    assert retried.json()["accepted_requirement_snapshot"]["sequence"] == 1
    assert module.runtime.requirement_snapshot_store.latest.sequence == 1
    assert module.runtime.connector_store.get_config("tw-finmind-datasets") is not None


def test_controller_owned_connector_legacy_mutations_require_controller_token(persona_api) -> None:
    client, module = persona_api
    endpoint = "/api/source-ingest/persona-source-provisioning/reconcile"
    headers = {"Authorization": f"Bearer {module.runtime.controller_token}"}
    personas = [_persona()]
    provisioned = client.post(
        endpoint,
        headers=headers,
        json={
            "personas": personas,
            "authoritative_snapshot": True,
            "desired_state_sha256": module.runtime._desired_state_digest(personas),
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
    headers = {"Authorization": f"Bearer {module.runtime.controller_token}"}
    personas = [_persona()]
    desired_payload = {
        "personas": personas,
        "authoritative_snapshot": True,
        "desired_state_sha256": module.runtime._desired_state_digest(personas),
    }
    removal_payload = {
        "personas": [],
        "authoritative_snapshot": True,
        "desired_state_sha256": module.runtime._desired_state_digest([]),
    }
    first = client.post(
        endpoint,
        headers=headers,
        json=desired_payload,
    )
    assert first.status_code == 200, first.text
    original_upsert = module.runtime.connector_store.upsert_config

    def fail_retirement(connector, fetch):
        if connector.status.value == "disabled":
            raise OSError("simulated retirement crash")
        return original_upsert(connector, fetch)

    monkeypatch.setattr(module.runtime.connector_store, "upsert_config", fail_retirement)
    with pytest.raises(OSError, match="simulated retirement crash"):
        client.post(endpoint, headers=headers, json=removal_payload)

    assert module.runtime.requirement_snapshot_store.latest.sequence == 2
    assert dict(module.runtime.requirement_snapshot_store.latest.bindings) == {}
    assert module.runtime.connector_store.get_config("tw-finmind-datasets").connector.status.value == "enabled"

    monkeypatch.setattr(module.runtime.connector_store, "upsert_config", original_upsert)
    retried = client.post(endpoint, headers=headers, json=removal_payload)
    assert retried.status_code == 200, retried.text
    assert retried.json()["accepted_requirement_snapshot"]["sequence"] == 2
    assert retried.json()["retirement_actions"][0]["action"] == "disabled_controller_owned"
    assert module.runtime.connector_store.get_config("tw-finmind-datasets").connector.status.value == "disabled"
    assert module.runtime.schedule_config_store.get_schedule("tw-finmind-datasets").enabled is False

    connector_lines = _line_count(module.runtime.CONNECTOR_STORE_PATH)
    schedule_lines = _line_count(module.runtime.CONNECTOR_SCHEDULE_CONFIG_PATH)
    duplicate = client.post(endpoint, headers=headers, json=removal_payload)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["accepted_requirement_snapshot"]["sequence"] == 2
    assert duplicate.json()["retirement_actions"][0]["action"] == "already_disabled_controller_owned"
    assert _line_count(module.runtime.CONNECTOR_STORE_PATH) == connector_lines
    assert _line_count(module.runtime.CONNECTOR_SCHEDULE_CONFIG_PATH) == schedule_lines


def test_unsupported_authoritative_snapshot_is_not_admitted_or_mutated(persona_api) -> None:
    client, module = persona_api
    persona = _persona(source_class="live_push")
    response = client.post(
        "/api/source-ingest/persona-source-provisioning/reconcile",
        headers={"Authorization": f"Bearer {module.runtime.controller_token}"},
        json={
            "personas": [persona],
            "authoritative_snapshot": True,
            "desired_state_sha256": module.runtime._desired_state_digest([persona]),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["unsupported"] == 1
    assert response.json()["accepted_requirement_snapshot"] is None
    assert module.runtime.requirement_snapshot_store.latest is None
    assert module.runtime.connector_store.list_configs() == []
    assert module.runtime.schedule_config_store.list_schedules() == []


def _us_persona() -> dict:
    return {
        "persona_id": "persona-dev-paper-us",
        "name": "Dev Paper US Baseline",
        "mandate": "Paper-only dev baseline SPY momentum",
        "lifecycle_state": "research_only",
        "created_at": "2026-09-18T00:00:00Z",
        "required_data_sources": [
            {
                "dataset": "us_price_daily",
                "market": "US",
                "cadence": "daily",
                "source_class": "live_pull",
                "connector_candidates": ["dev-paper-us-equity-simulation"],
                "policy_gates": ["require_connector_approved", "require_schedule_active"],
            }
        ],
    }


@pytest.mark.parametrize("pantheon_env", [None, "dev"])
def test_us_price_daily_requirement_is_unsupported_in_every_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pantheon_env: str | None
) -> None:
    """No connector serves a US price_daily requirement in any environment:
    it stays unsupported rather than silently binding paper personas to
    simulated data."""

    if pantheon_env is None:
        monkeypatch.delenv("PANTHEON_ENV", raising=False)
    else:
        monkeypatch.setenv("PANTHEON_ENV", pantheon_env)
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)

    result = reconciler.reconcile_persona(_us_persona())

    assert result.summary["unsupported"] == 1
    action = result.actions[0]
    assert action.connector_id is None
    assert connector_store.get_config("dev-paper-us-equity-simulation") is None
    assert schedule_store.get_schedule("dev-paper-us-equity-simulation") is None



# --- COLLECTOR-HOSTED-ADMISSION-20261112: Collector sources provisioned from their ConnectorDefinition ---


def _collector_persona(*definition_ids: str) -> dict:
    from services.source_ingestion.connector_definitions import get_connector_definition

    requirements = []
    for definition_id in definition_ids:
        collector = get_connector_definition(definition_id).metadata["collector"]
        requirements.append(
            {
                "dataset": collector["dataset"],
                "market": "TW",
                "cadence": "daily",
                "source_class": "live_pull",
                "connector_candidates": [definition_id],
                "policy_gates": ["require_connector_approved", "require_schedule_active", "no_live_capital"],
            }
        )
    return {
        "persona_id": "persona-collector",
        "name": "Collector intake",
        "mandate": "Admit Collector sources",
        "lifecycle_state": "research_only",
        "created_at": "2026-06-01T00:00:00Z",
        "required_data_sources": requirements,
    }


_SOCIAL = (
    "collector-tw-threads",
    "collector-tw-instagram-popular",
    "collector-tw-cmoney-forum",
    "collector-tw-ptt-stock",
    "collector-youtube-finance",
)


def test_collector_requirements_provision_one_connector_per_source_from_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.source_ingestion.connector_definitions import get_connector_definition
    from services.source_ingestion.connectors.collector_bundle import COLLECTOR_BUNDLE_ADAPTER_TOKEN

    monkeypatch.setenv("PANTHEON_COLLECTOR_HOST", "Collector.Example.Test")
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)
    ids = (*_SOCIAL, "collector-news-rss", "collector-tw-macro-ndc", "collector-tw-twse-price-history", "strategy-seed-paper-corpus")

    result = reconciler.reconcile_persona(_collector_persona(*ids))

    assert result.summary["mutated"] == len(ids)
    assert [a.connector_id for a in result.actions] == list(ids)  # five social platforms stay five connectors
    assert len({a.idempotency_key for a in result.actions}) == len(ids)
    for definition_id in ids:
        definition = get_connector_definition(definition_id)
        collector = definition.metadata["collector"]
        config = connector_store.get_config(definition_id)
        assert config.connector.metadata["definition_id"] == definition_id
        assert config.connector.metadata["persona_source_reconciliation"]["managed_by"]
        assert config.fetch["mode"] == "provider_owned_adapter"
        assert config.fetch["adapter"] == COLLECTOR_BUNDLE_ADAPTER_TOKEN
        assert config.fetch["max_records"] == definition.default_limits["max_records"]
        assert config.fetch["adapter_config"]["host"] == "collector.example.test"
        assert config.fetch["adapter_config"]["source_id"] == collector["source_id"]
        assert config.fetch["adapter_config"]["source_type"] == collector["source_type"]
        assert config.fetch["allow_empty"] is False  # an empty Collector pull is never masked
        schedule = schedule_store.get_schedule(definition_id)
        assert schedule.enabled is True and schedule.interval_seconds == collector["cadence_seconds"]
    for definition_id in _SOCIAL:
        assert connector_store.get_config(definition_id).fetch["max_records"] == 50  # test volume (O-6)

    second = reconciler.reconcile_persona(_collector_persona(*ids))
    assert second.summary["satisfied"] == len(ids)
    assert {a.connector_action for a in second.actions} == {"verified"}


def test_collector_requirements_are_not_provisioned_without_collector_host_and_say_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PANTHEON_COLLECTOR_HOST", raising=False)
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)

    results = reconciler.reconcile_personas([_collector_persona("collector-tw-ptt-stock", "collector-gdelt")])

    for action in results[0].actions:
        assert action.status == "skipped"
        assert action.connector_action == "collector_host_not_configured"
        assert "PANTHEON_COLLECTOR_HOST" in action.details["reason"]
    assert connector_store.get_config("collector-tw-ptt-stock") is None
    assert schedule_store.get_schedule("collector-tw-ptt-stock") is None
    assert not (tmp_path / "connector_config.jsonl").exists() or _line_count(tmp_path / "connector_config.jsonl") == 0


def test_collector_requirement_with_unsupported_dataset_is_not_provisioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PANTHEON_COLLECTOR_HOST", "collector.example.test")
    reconciler, connector_store, _ = _reconciler(tmp_path)
    persona = _collector_persona("collector-tw-ptt-stock")
    persona["required_data_sources"][0]["dataset"] = "tw_price_daily"

    result = reconciler.reconcile_persona(persona)

    assert result.actions[0].status == "unsupported"
    assert connector_store.get_config("collector-tw-ptt-stock") is None


def test_collector_requirements_leave_official_price_requirement_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PANTHEON_COLLECTOR_HOST", "collector.example.test")
    reconciler, connector_store, schedule_store = _reconciler(tmp_path)
    official = _persona(connector_candidates=["tw-twse-tpex-official-market"], dataset="tw_price_daily")
    official["required_data_sources"].extend(_collector_persona("collector-tw-twse-price-history")["required_data_sources"])
    official["required_data_sources"][-1]["dataset"] = "tw_price_daily"

    result = reconciler.reconcile_persona(official)

    by_connector = {a.connector_id: a for a in result.actions}
    assert set(by_connector) == {"tw-twse-tpex-official-market", "collector-tw-twse-price-history"}
    assert by_connector["tw-twse-tpex-official-market"].status == "mutated"
    assert schedule_store.get_schedule("tw-twse-tpex-official-market").interval_seconds == 86400
    assert connector_store.get_config("tw-twse-tpex-official-market").fetch["allow_empty"] is True
