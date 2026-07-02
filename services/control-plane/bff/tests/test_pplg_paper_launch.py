from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore


ROOT = Path(__file__).resolve().parents[4]
SCHEMA_PATH = ROOT / "services/control-plane/specs/persona_paper_live.schema.json"
HEADERS = {"Authorization": "Bearer op-pplg:operator,reviewer,admin:mfa"}
LAUNCH_STEPS = [
    "persona_identity_created",
    "policy_snapshots_created",
    "paper_capital_pool_ready",
    "paper_binding_active",
    "paper_deployment_plan_created",
    "paper_approval_recorded",
    "paper_runtime_binding_created",
    "paper_runtime_started",
    "telemetry_heartbeat_verified",
]


def _validator_for(def_name: str) -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(
        {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": f"#/$defs/{def_name}",
        }
    )


def _payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "name": "Taiwan Paper Alpha",
        "mandate": "Paper trade TW equities with governed data sources.",
        "strategy_family": ["stat_arb"],
        "market_scope": ["TW"],
        "source_scope": ["twse", "finra"],
        "risk_profile_id": "risk-paper-default",
        "paper_capital_pool": {
            "mode": "create_from_template",
            "capital_scope": "paper",
            "capital_pool_id": None,
            "template_id": "paper-default",
        },
        "paper_budget": 100000,
        "artifact_id": "artifact-paper-alpha-v1",
        "operator_note": "launch test",
    }
    payload.update(overrides)
    return payload


def _headers(idempotency_key: str, trace_id: str = "trace-pplg-paper-launch") -> Dict[str, str]:
    return {
        **HEADERS,
        "Idempotency-Key": idempotency_key,
        "X-Trace-Id": trace_id,
    }


def _fresh_client() -> Iterator[Tuple[TestClient, ReadSurfaceStore]]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_overlay = dict(bff_main._PERSONA_BFF_OVERLAY)
        try:
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=True,
            )
            bff_main.read_store = store
            bff_main._PERSONA_BFF_OVERLAY.clear()
            bff_main._STRATEGY_BFF_OVERLAY.clear()
            bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
            bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            yield TestClient(bff_main.app, raise_server_exceptions=False), store
        finally:
            bff_main.read_store = original_store
            bff_main._PERSONA_BFF_OVERLAY.clear()
            bff_main._PERSONA_BFF_OVERLAY.update(original_overlay)


def test_paper_launch_creates_complete_paper_runtime_and_fleet_row() -> None:
    for client, store in _fresh_client():
        response = client.post(
            "/bff/management/personas/paper-launch",
            json=_payload(persona_id="persona-paper-tw-alpha"),
            headers=_headers("idem-paper-launch-happy"),
        )

        assert response.status_code == 201, response.text
        body = response.json()
        data = body["data"]
        _validator_for("PaperPersonaLaunch").validate(data)

        assert data["status"] == "paper_running"
        assert data["failed_step"] is None
        assert data["retryable"] is False
        assert data["completed_steps"] == LAUNCH_STEPS
        assert body["meta"]["trace_id"] == "trace-pplg-paper-launch"
        assert body["meta"]["idempotency"]["replayed"] is False
        assert [event["audit_context"]["step"] for event in body["meta"]["audit_events"]] == LAUNCH_STEPS

        for field in (
            "persona_id",
            "capital_pool_id",
            "binding_id",
            "deployment_plan_id",
            "approval_decision_id",
            "runtime_binding_id",
            "runtime_id",
        ):
            assert data[field]

        persona = store.get_persona(data["persona_id"])
        assert persona is not None
        assert persona["lifecycle_state"] == "paper_running"
        assert persona["metadata"]["setup_status"] == "paper_runtime_active"

        pool = store.get_capital_pool(data["capital_pool_id"])
        assert pool is not None
        assert pool["capital_scope"] == "paper"
        assert pool["live_capital_enabled"] is False
        assert pool["cash"] == 100000

        binding = store.get_binding(data["binding_id"])
        assert binding is not None
        assert binding["allowed_deployment_scope"] == "paper"
        assert binding["deployment_modes"] == ["paper"]

        runtime = store.get_runtime_binding(data["runtime_binding_id"])
        assert runtime is not None
        assert runtime["status"] == "active"
        assert runtime["deployment_stage"] == "paper"
        assert runtime["capital_pool_id"] == data["capital_pool_id"]
        assert runtime["persona_capital_binding_id"] == data["binding_id"]
        assert runtime["metadata"]["live_write_enabled"] is False
        assert runtime["metadata"]["fail_closed"] is True

        readiness_response = client.get(
            f"/bff/management/personas/{data['persona_id']}/readiness",
            headers=HEADERS,
        )
        assert readiness_response.status_code == 200, readiness_response.text
        readiness = readiness_response.json()["data"]
        _validator_for("PersonaReadinessProjection").validate(readiness)
        assert readiness["setup_status"] == "paper_runtime_active"
        assert readiness["competition_track"] == "paper_challenger"
        assert readiness["capital_scope"] == "paper"
        assert readiness["repair"]["retryable"] is False

        fleet_response = client.get(
            "/bff/management/persona-fleet?competition_track=paper_challenger",
            headers=HEADERS,
        )
        assert fleet_response.status_code == 200, fleet_response.text
        rows = {
            item["persona_id"]: item
            for item in fleet_response.json()["items"]
        }
        row = rows[data["persona_id"]]
        assert row["competitionTrack"] == "paper_challenger"
        assert row["capitalScope"] == "paper"
        assert row["readinessProjection"]["setup_status"] == "paper_runtime_active"
        assert row["rowAction"]["startupWizardVisible"] is False
        assert "啟動精靈" not in row["rowAction"]["label"]


def test_paper_launch_idempotency_replays_and_conflicts() -> None:
    for client, _store in _fresh_client():
        payload = _payload(persona_id="persona-paper-idem")
        first = client.post(
            "/bff/management/personas/paper-launch",
            json=payload,
            headers=_headers("idem-paper-launch-replay"),
        )
        second = client.post(
            "/bff/management/personas/paper-launch",
            json=payload,
            headers=_headers("idem-paper-launch-replay"),
        )
        conflict = client.post(
            "/bff/management/personas/paper-launch",
            json={**payload, "name": "Different Paper Alpha"},
            headers=_headers("idem-paper-launch-replay"),
        )

        assert first.status_code == 201, first.text
        assert second.status_code == 200, second.text
        assert first.json()["data"]["launch_id"] == second.json()["data"]["launch_id"]
        assert second.json()["meta"]["idempotency"]["replayed"] is True
        assert conflict.status_code == 409, conflict.text


def test_paper_launch_step_failure_is_repairable_and_retry_starts_runtime(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_ENABLE_PAPER_LAUNCH_FAILURE_INJECTION", "1")
    for client, store in _fresh_client():
        failed = client.post(
            "/bff/management/personas/paper-launch",
            json=_payload(
                persona_id="persona-paper-repair",
                simulate_failure_step="paper_runtime_started",
            ),
            headers=_headers("idem-paper-launch-fail", trace_id="trace-pplg-fail"),
        )
        assert failed.status_code == 202, failed.text
        failed_data = failed.json()["data"]
        _validator_for("PaperPersonaLaunch").validate(failed_data)
        assert failed_data["status"] == "setup_failed"
        assert failed_data["failed_step"] == "paper_runtime_started"
        assert failed_data["retryable"] is True
        assert failed_data["repair_url"] == "/bff/management/personas/persona-paper-repair/setup/retry"

        persona = store.get_persona("persona-paper-repair")
        assert persona is not None
        assert persona["lifecycle_state"] == "setup_failed"
        assert persona["metadata"]["setup_failed_step"] == "paper_runtime_started"

        readiness = client.get(
            "/bff/management/personas/persona-paper-repair/readiness",
            headers=HEADERS,
        )
        assert readiness.status_code == 200, readiness.text
        readiness_data = readiness.json()["data"]
        assert readiness_data["setup_status"] == "setup_failed"
        assert readiness_data["repair"]["retryable"] is True

        retry = client.post(
            "/bff/management/personas/persona-paper-repair/setup/retry",
            json={},
            headers=_headers("idem-paper-launch-retry", trace_id="trace-pplg-retry"),
        )
        retry_replay = client.post(
            "/bff/management/personas/persona-paper-repair/setup/retry",
            json={},
            headers=_headers("idem-paper-launch-retry", trace_id="trace-pplg-retry"),
        )

        assert retry.status_code == 200, retry.text
        retry_data = retry.json()["data"]
        assert retry_data["status"] == "paper_running"
        assert retry_data["persona_id"] == "persona-paper-repair"
        assert retry_data["failed_step"] is None
        assert retry_data["completed_steps"] == LAUNCH_STEPS
        assert retry_replay.status_code == 200, retry_replay.text
        assert retry_replay.json()["meta"]["idempotency"]["replayed"] is True


def test_paper_launch_rejects_live_capable_pool_before_binding() -> None:
    for client, store in _fresh_client():
        live_pool = store.create_capital_pool(
            pool_id="pool-live-not-paper",
            name="Live Pool",
            actor_id="test",
            risk_policy_ref="risk-live",
            status="ready",
            capital_scope="live",
            live_capital_enabled=True,
            nav=1000000,
            cash=1000000,
            market_scope=["TW"],
        )
        response = client.post(
            "/bff/management/personas/paper-launch",
            json=_payload(
                persona_id="persona-paper-live-rejected",
                paper_capital_pool={
                    "mode": "select_existing",
                    "capital_scope": "paper",
                    "capital_pool_id": live_pool["pool_id"],
                },
            ),
            headers=_headers("idem-paper-launch-live-pool"),
        )

        assert response.status_code == 202, response.text
        data = response.json()["data"]
        assert data["status"] == "setup_failed"
        assert data["failed_step"] == "paper_capital_pool_ready"
        assert data["retryable"] is True
        assert store.get_binding(data["binding_id"]) is None
        assert store.get_runtime_binding(data["runtime_binding_id"]) is None
        assert store.get_capital_pool("pool-live-not-paper")["live_capital_enabled"] is True
