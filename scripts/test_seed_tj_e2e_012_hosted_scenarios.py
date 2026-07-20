from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from services.trade_journey.materializer import IDENTIFIER_FIELDS, JourneyMaterializer


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


def test_seed_source_keeps_tls_verification_and_never_logs_credentials() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "ssl.create_default_context()" in source
    assert "CERT_NONE" not in source
    assert "check_hostname = False" not in source
    assert '"client_secret": _required_env' in source
    assert "print(token" not in source
    assert "print(login" not in source


def test_main_authenticates_and_posts_batch_without_printing_secrets(monkeypatch, capsys) -> None:
    for name, value in {
        "BFF_BASE": "https://bff.example.test",
        "TJ_E2E_ALLOWED_BFF_ORIGIN": "https://bff.example.test",
        "GITHUB_REPOSITORY": "ajoe734/pantheon",
        "TJ_E2E_TENANT_ID": seed.TENANT_ID,
        "TJ_E2E_OPERATOR_CLIENT_ID": "operator-a-client",
        "TJ_E2E_OPERATOR_CLIENT_SECRET": "operator-a-secret",
    }.items():
        monkeypatch.setenv(name, value)

    calls = []

    def fake_request(url, *, body, token=None, timeout=30.0):
        calls.append({"url": url, "body": body, "token": token, "timeout": timeout})
        if url.endswith("/bff/auth/dev-login"):
            return 200, {"access_token": "short-lived-token", "meta": {"identity": "operator_a"}}
        return 200, {"status": "ok", "count": len(body)}

    monkeypatch.setattr(seed, "_request_json", fake_request)

    assert seed.main() == 0
    stdout = capsys.readouterr().out
    summary = json.loads(stdout)
    assert summary["result"] == "seeded"
    assert summary["journey_count"] == 13
    assert "operator-a-secret" not in stdout
    assert "short-lived-token" not in stdout
    assert calls[1]["token"] == "short-lived-token"
    assert calls[1]["body"] == seed.build_scenarios()
