from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from services.trade_journey.materializer import IDENTIFIER_FIELDS, JourneyMaterializer
from services.trade_journey.lifecycle_projector import LifecycleProjector
from services.telemetry.ingest_svc import TelemetryIngestService


SCRIPT = Path(__file__).with_name("seed_tj_e2e_012_hosted_scenarios.py")
SPEC = importlib.util.spec_from_file_location("seed_tj_e2e_012_hosted_scenarios", SCRIPT)
assert SPEC and SPEC.loader
seed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed)


def _projection(materializer: JourneyMaterializer, number: int, environment: str = "paper"):
    result = materializer.get(
        f"tj-scenario-{number}",
        tenant_id=seed.TENANT_ID,
        environment=environment,
    )
    assert result is not None
    return result


def test_seed_batch_materializes_all_source_scenarios_and_is_deterministic() -> None:
    events = seed.build_scenarios()
    repeated = seed.build_scenarios()

    assert events == repeated
    assert len(events) == len({event["event_id"] for event in events})
    assert {event["environment"] for event in events} == {"paper", "live"}
    assert len({event["journey_id"] for event in events}) == 13

    materializer = JourneyMaterializer()
    materializer.rebuild(reversed(events))
    assert materializer.rebuild_status == "complete"
    for number in range(1, 13):
        _projection(materializer, number, "live" if number == 10 else "paper")


def test_seed_encodes_negative_causation_late_ordering_and_replay_versions() -> None:
    events = seed.build_scenarios()
    materializer = JourneyMaterializer()
    materializer.rebuild(events)

    happy = _projection(materializer, 1)
    assert set(seed.OBSERVABLE_STAGES) <= set(happy.snapshot["stages"])
    assert all(happy.snapshot["stages"][stage]["status"] == "succeeded" for stage in seed.OBSERVABLE_STAGES)
    assert happy.snapshot["status"] == "completed"
    assert set(_projection(materializer, 2).snapshot["stages"]) == {"promotion_decision"}
    assert _projection(materializer, 3).snapshot["status"] == "blocked"
    assert _projection(materializer, 4).snapshot["status"] == "failed"
    assert _projection(materializer, 5).snapshot["status"] == "partially_filled"
    assert _projection(materializer, 6).snapshot["status"] == "waiting_human"
    assert _projection(materializer, 7).snapshot["status"] == "completed_with_variance"

    live = _projection(materializer, 10, "live").timeline[-1]
    assert {
        "account_id",
        "capital_account_id",
        "order_id",
        "client_order_id",
        "broker_order_id",
        "quantity",
        "price",
    } <= set(live)

    degraded = _projection(materializer, 11)
    assert degraded.snapshot["status"] == "completed"
    assert degraded.timeline[-1]["unavailable_sources"] == ["research_archive"]
    assert {"signal_generation", "trade_decision", "risk_evaluation"} <= set(
        degraded.snapshot["completeness"]["missing_stages"]
    )

    late = _projection(materializer, 8).timeline
    assert datetime.fromisoformat(late[0]["occurred_at"].replace("Z", "+00:00")) < datetime.fromisoformat(
        late[1]["occurred_at"].replace("Z", "+00:00")
    )
    assert datetime.fromisoformat(late[0]["recorded_at"].replace("Z", "+00:00")) > datetime.fromisoformat(
        late[1]["recorded_at"].replace("Z", "+00:00")
    )

    replay_cut = datetime(2026, 7, 12, 12, 1, 30, tzinfo=timezone.utc)
    historical_events = [
        event
        for event in events
        if datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")) <= replay_cut
    ]
    historical = JourneyMaterializer()
    historical.rebuild(historical_events)
    current_version = _projection(materializer, 12).timeline[-1]["persona_version"]
    historical_version = _projection(historical, 12).timeline[-1]["persona_version"]
    assert (historical_version, current_version) == ("persona-v1", "persona-v2")


def test_seed_and_materializer_cover_every_hosted_resolve_identifier() -> None:
    assert {"persona_id", "strategy_id", "fill_id"} <= set(IDENTIFIER_FIELDS)
    materializer = JourneyMaterializer()
    materializer.rebuild(seed.build_scenarios())
    scenario = _projection(materializer, 9)
    event = scenario.timeline[-1]

    for identifier_type in (
        "persona_id",
        "strategy_id",
        "decision_id",
        "client_order_id",
        "broker_order_id",
        "fill_id",
    ):
        assert materializer.resolve(
            identifier_type,
            event[identifier_type],
            tenant_id=seed.TENANT_ID,
            environment="paper",
        )[0] == "tj-scenario-9"

    assert materializer.resolve(
        "decision_id",
        seed.AMBIGUITY_IDENTIFIER,
        tenant_id=seed.TENANT_ID,
        environment="paper",
    ) == ["tj-scenario-9", "tj-scenario-9-ambiguity-peer"]


def _binding() -> dict:
    return {
        "binding_id": "10000000-0000-4000-8000-000000000001",
        "runtime_id": "runtime-tj-e2e-012",
        "capital_pool_id": "pool-tj-e2e-012",
        "artifact_id": "artifact-tj-e2e-012",
        "artifact_version": "1.2.3",
        "plan_id": "plan-tj-e2e-012",
        "persona_capital_binding_id": "pcb-tj-e2e-012",
        "effective_at": "2026-07-20T00:00:00Z",
        "deployment_mode": "paper",
        "status": "active",
    }


def test_seed_wraps_source_events_in_deterministic_binding_valid_telemetry() -> None:
    fixtures = seed.build_telemetry_fixtures(_binding())
    repeated = seed.build_telemetry_fixtures(_binding())

    assert fixtures == repeated
    assert len(fixtures) == len(seed.build_scenarios())
    assert {event["event_type"] for event in fixtures} == {seed.FIXTURE_EVENT_TYPE}
    assert {event["binding_id"] for event in fixtures} == {_binding()["binding_id"]}
    assert {event["metadata"]["fixture_scope"] for event in fixtures} == {"dev-only"}
    assert {event["correlation_envelope"]["tenant_id"] for event in fixtures} == {
        seed.TENANT_ID
    }


def test_fixture_ingest_is_default_closed_and_dev_gate_still_runs_evidence_validation(
    monkeypatch,
) -> None:
    fixture = seed.build_telemetry_fixtures(_binding())[0]
    service = TelemetryIngestService(schema={})

    monkeypatch.delenv("PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED", raising=False)
    accepted, reason, _ = service._validate_evidence_contract(fixture)
    assert accepted is False
    assert "disabled" in str(reason)

    monkeypatch.setenv("PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED", "true")
    accepted, reason, _ = service._validate_evidence_contract(fixture)
    assert accepted is True
    assert reason is None


def test_canonical_fixture_projector_rebuilds_all_scenarios(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixtures = seed.build_telemetry_fixtures(_binding())
    rows = [
        {
            "ingested_seq": position,
            "ingested_at": f"2026-07-20T00:05:{position:02d}Z",
            "event_id": fixture["event_id"],
            "event_type": fixture["event_type"],
            "created_at": fixture["created_at"],
            "payload": fixture,
        }
        for position, fixture in enumerate(fixtures, start=1)
    ]
    monkeypatch.setenv("PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED", "true")
    projector = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="a" * 40,
    )
    result = projector.project_records(
        rows,
        mode="live",
        source_high_watermark=len(rows),
    )

    assert result.accepted == len(rows)
    assert result.quarantined == 0
    projected = json.loads(
        (tmp_path / "current" / "trade_journey_events.json").read_text(
            encoding="utf-8"
        )
    )
    materializer = JourneyMaterializer()
    materializer.rebuild(projected["events"])
    for number in range(1, 13):
        _projection(materializer, number, "live" if number == 10 else "paper")
    assert set(_projection(materializer, 2).snapshot["stages"]) == {"promotion_decision"}
    assert _projection(materializer, 5).timeline[-1]["fill_id"] == "fill-scenario-5"
    assert _projection(materializer, 11).timeline[-1]["source_unavailable"] is True


def test_seed_source_is_loopback_only_and_never_uses_or_logs_credentials() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "CERT_NONE" not in source
    assert "check_hostname = False" not in source
    assert "ALLOWED_TELEMETRY_ORIGINS" in source
    assert "http://127.0.0.1:18083" in source
    assert "client_secret" not in source
    assert "print(token" not in source
    assert "print(login" not in source


def test_main_posts_canonical_loopback_batch_without_printing_payloads(monkeypatch, capsys) -> None:
    for name, value in {
        "TJ_E2E_TELEMETRY_BASE": "http://127.0.0.1:18083",
        "TJ_E2E_RUNTIME_MANAGER_BASE": "http://127.0.0.1:18081",
        "TJ_E2E_SEED_BFF_BASE": "http://127.0.0.1:18001",
        "TJ_E2E_EXPECTED_BFF_SHA": "a" * 40,
        "GITHUB_REPOSITORY": "ajoe734/pantheon",
        "TJ_E2E_TENANT_ID": seed.TENANT_ID,
    }.items():
        monkeypatch.setenv(name, value)

    calls = []

    def fake_read(url, *, token=None, timeout=30.0):
        calls.append({"url": url, "token": token, "timeout": timeout})
        if url.endswith("/bff/version"):
            return 200, {"source_commit_sha": "a" * 40}
        return 200, {"bindings": [_binding()]}

    def fake_request(url, *, body, token=None, timeout=30.0):
        calls.append({"url": url, "body": body, "token": token, "timeout": timeout})
        return 202, {"ingested": len(body["events"]), "rejected": 0}

    monkeypatch.setattr(seed, "_read_json", fake_read)
    monkeypatch.setattr(seed, "_request_json", fake_request)
    monkeypatch.setattr(seed.time, "sleep", lambda _seconds: None)

    assert seed.main() == 0
    stdout = capsys.readouterr().out
    summary = json.loads(stdout)
    assert summary["result"] == "seeded"
    assert summary["journey_count"] == 13
    assert summary["telemetry_event_count"] == len(seed.build_scenarios())
    assert "fixture_payload" not in stdout
    write = calls[-1]
    assert write["url"].endswith("/api/telemetry/ingest/batch")
    assert write["token"] is None
    assert write["body"]["events"] == seed.build_telemetry_fixtures(_binding())
