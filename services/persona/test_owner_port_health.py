"""Unit and compose contract tests for persona owner-port health reporting and degraded cycle heartbeats."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.test_persona_cycle_owner_ports_compose_contract import (
    test_code_read_owner_env_vars_are_wired_and_missing_var_fails_contract,
    test_degraded_cycle_report_names_every_missing_owner_port,
    test_persona_owner_ports_resolve_configured_when_compose_and_issuer_are_applied,
    test_persona_owner_tokens_verify_against_each_target_owner_contract,
    test_pool_publish_and_research_canonical_dataset_authority_wiring,
    test_quarterly_paper_allocation_and_deploy_passthrough_wiring,
)
from services.persona.owner_port_health import (
    REQUIRED_OWNER_PORTS,
    apply_owner_port_health_to_cycle_report,
    evaluate_owner_ports,
    non_healthy_owner_ports,
    sync_owner_port_env,
)
from services.persona.persona_cycle_controller import run_controller

__all__ = [
    "test_code_read_owner_env_vars_are_wired_and_missing_var_fails_contract",
    "test_degraded_cycle_report_names_every_missing_owner_port",
    "test_persona_owner_ports_resolve_configured_when_compose_and_issuer_are_applied",
    "test_persona_owner_tokens_verify_against_each_target_owner_contract",
    "test_pool_publish_and_research_canonical_dataset_authority_wiring",
    "test_quarterly_paper_allocation_and_deploy_passthrough_wiring",
]


def _fully_configured_env(tmp_path: Path) -> tuple[dict[str, str], list[str]]:
    secrets: list[str] = []

    def token_file(name: str, secret_value: str) -> str:
        secrets.append(secret_value)
        path = tmp_path / name
        path.write_text(secret_value, encoding="utf-8")
        path.chmod(0o600)
        return str(path)

    env = {
        "RESEARCH_API_URL": "http://research-orchestrator-svc:8101",
        "PERSONA_RESEARCH_SERVICE_TOKEN_FILE": token_file(
            "PERSONA_RESEARCH_SERVICE_TOKEN", "secret-research-token-001"
        ),
        "MEMORY_API_URL": "http://memory:8086",
        "PERSONA_MEMORY_SERVICE_TOKEN_FILE": token_file(
            "PERSONA_MEMORY_SERVICE_TOKEN", "secret-memory-token-002"
        ),
        "REGISTRY_URL": "http://registry:8087",
        "PERSONA_REGISTRY_SERVICE_TOKEN_FILE": token_file(
            "PERSONA_REGISTRY_SERVICE_TOKEN", "secret-registry-token-003"
        ),
        "EVOLUTION_API_URL": "http://evolution:8093",
        "EVOLUTION_AUTH_TOKEN": "secret-evolution-token-004",
        "PANTHEON_CONSULTATION_API_URL": "http://consultation-svc:8096",
        "CONSULTATION_SERVICE_TOKEN": "secret-consultation-token-005",
        "PANTHEON_GOVERNANCE_API_URL": "http://governance:8082",
        "PERSONA_GOVERNANCE_SERVICE_TOKEN_FILE": token_file(
            "PERSONA_GOVERNANCE_SERVICE_TOKEN", "secret-governance-token-006"
        ),
        "PANTHEON_SEARCH_API_URL": "http://search-svc:8098",
        "PERSONA_SEARCH_SERVICE_TOKEN_FILE": token_file(
            "PERSONA_SEARCH_SERVICE_TOKEN", "secret-search-token-007"
        ),
    }
    secrets.extend(["secret-evolution-token-004", "secret-consultation-token-005"])
    return env, secrets


def test_evaluate_owner_ports_reports_all_seven_configured_without_exposing_secrets(tmp_path: Path) -> None:
    env, secrets = _fully_configured_env(tmp_path)
    ports = evaluate_owner_ports(env)

    assert tuple(ports.keys()) == REQUIRED_OWNER_PORTS
    for name in REQUIRED_OWNER_PORTS:
        assert ports[name]["status"] == "configured"
        assert ports[name]["address_configured"] is True
        assert ports[name]["credential_configured"] is True

    serialized = json.dumps(ports)
    for secret in secrets:
        assert secret not in serialized
    assert non_healthy_owner_ports(ports) == {}


def test_evaluate_owner_ports_distinguishes_unconfigured_and_unreachable(tmp_path: Path) -> None:
    env, secrets = _fully_configured_env(tmp_path)
    # Break research to the legacy non-existent service address and remove memory + search credentials.
    env["RESEARCH_API_URL"] = "http://research-orchestrator:8100"
    del env["PERSONA_MEMORY_SERVICE_TOKEN_FILE"]
    del env["PANTHEON_SEARCH_API_URL"]

    ports = evaluate_owner_ports(
        env,
        probe_fn=lambda url: "consultation-svc:8096" not in url,
    )

    assert ports["research"]["status"] == "unreachable"
    assert ports["memory"]["status"] == "unconfigured"
    assert ports["search"]["status"] == "unconfigured"
    assert ports["consultation"]["status"] == "unreachable"
    assert ports["registry"]["status"] == "configured"
    assert ports["evolution"]["status"] == "configured"
    assert ports["governance"]["status"] == "configured"

    serialized = json.dumps(ports)
    for secret in secrets:
        assert secret not in serialized


def test_cycle_report_records_degraded_heartbeat_naming_every_missing_owner_port(tmp_path: Path) -> None:
    env, _ = _fully_configured_env(tmp_path)
    del env["PERSONA_REGISTRY_SERVICE_TOKEN_FILE"]
    del env["PANTHEON_SEARCH_API_URL"]

    base_report = {
        "status": "ok",
        "personas_seen": 1,
        "tenant_scopes": [{"tenant_id": "tenant-dev", "personas": ["persona-alpha"]}],
        "failures": [],
    }
    updated = apply_owner_port_health_to_cycle_report(base_report, env=env)

    assert updated["status"] == "degraded"
    failure_ports = {item["owner_port"] for item in updated["failures"]}
    assert failure_ports == {"registry", "search"}
    assert all(item.get("persona_id") == "persona-alpha" for item in updated["failures"])
    reasons = [item["reason"] for item in updated["failures"]]
    assert any("registry" in reason for reason in reasons)
    assert any("search" in reason for reason in reasons)


def test_sync_owner_port_env_propagates_file_tokens_and_url_aliases(tmp_path: Path) -> None:
    env, _ = _fully_configured_env(tmp_path)
    sync_owner_port_env(env)

    assert env["PANTHEON_RESEARCH_API_URL"] == "http://research-orchestrator-svc:8101"
    assert env["PANTHEON_RESEARCH_SERVICE_TOKEN"] == "secret-research-token-001"
    assert env["PANTHEON_MEMORY_API_URL"] == "http://memory:8086"
    assert env["PANTHEON_MEMORY_AUTH_TOKEN"] == "secret-memory-token-002"
    assert env["PANTHEON_MEMORY_SERVICE_TOKEN"] == "secret-memory-token-002"
    assert env["PANTHEON_REGISTRY_API_URL"] == "http://registry:8087"
    assert env["PANTHEON_REGISTRY_SERVICE_TOKEN"] == "secret-registry-token-003"
    assert env["PANTHEON_EVOLUTION_API_URL"] == "http://evolution:8093"
    assert env["EVOLUTION_AUTH_TOKEN"] == "secret-evolution-token-004"
    assert env["PANTHEON_CONSULTATION_SERVICE_URL"] == "http://consultation-svc:8096"
    assert env["CONSULTATION_SERVICE_TOKEN"] == "secret-consultation-token-005"
    assert env["PANTHEON_SEARCH_SERVICE_TOKEN"] == "secret-search-token-007"


def test_persona_main_health_and_wrapped_controller_surface_owner_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persona_dir = Path(__file__).resolve().parents[1] / "control-plane" / "persona"
    monkeypatch.syspath_prepend(str(persona_dir))
    import main as persona_main  # type: ignore[import-not-found]

    env, secrets = _fully_configured_env(tmp_path)
    for key in list(env.keys()):
        monkeypatch.setenv(key, env[key])

    healthy = asyncio.run(persona_main.health())
    assert healthy["status"] == "ok"
    assert tuple(healthy["owner_ports"].keys()) == REQUIRED_OWNER_PORTS
    assert all(healthy["owner_ports"][p]["status"] == "configured" for p in REQUIRED_OWNER_PORTS)
    for secret in secrets:
        assert secret not in json.dumps(healthy)

    # Remove search URL and verify /health and /api/persona/health/owner-ports degrade and controller heartbeat records search failure.
    monkeypatch.delenv("PANTHEON_SEARCH_API_URL", raising=False)
    degraded_health = asyncio.run(persona_main.health())
    assert degraded_health["status"] == "degraded"
    assert degraded_health["owner_ports"]["search"]["status"] == "unconfigured"
    dedicated_health = asyncio.run(persona_main.persona_owner_ports_health())
    assert dedicated_health["status"] == "degraded"
    assert dedicated_health["owner_ports"]["search"]["status"] == "unconfigured"

    heartbeats: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    class _StubRegistrar:
        def _get_runtime(self) -> object:
            return object()

        def list_registered_jobs(self) -> list[dict[str, Any]]:
            return [{"job_id": "per_persona_ooda", "tenant_id": "tenant-dev", "personas": ["persona-alpha"]}]

        def record_heartbeat(
            self,
            job_id: str,
            payload: dict[str, Any],
            *,
            downstream_actual_state: dict[str, Any] | None = None,
        ) -> None:
            heartbeats.append((job_id, payload, downstream_actual_state or {}))

    class _StubInnerController:
        def __init__(self) -> None:
            self.registrar = _StubRegistrar()

        def reconcile_once(self) -> dict[str, Any]:
            return {
                "status": "ok",
                "personas_seen": 1,
                "tenant_scopes": {"tenant-dev": ["persona-alpha"]},
                "personas": {"persona-alpha": {"status": "healthy"}},
                "failures": [],
            }

    dataset_dir = tmp_path / "dataset-authority"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONA_POOL_DATASET_AUTHORITY_OUTPUT_ROOT", str(dataset_dir))

    def _two_arg_loader(persona: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        version_path = dataset_dir / "dataset-version-dv-002.json"
        payload = {
            "dataset_version": "dv-002",
            "persona_id": persona.get("persona_id"),
            "candidate_id": candidate.get("candidate_id"),
            "parquet_path": "/data/source-ingest/bars.parquet",
        }
        version_path.write_text(json.dumps(payload), encoding="utf-8")
        return {"rows": []}

    wrapped_loader = persona_main._sync_canonical_dataset_pointer(_two_arg_loader)
    assert wrapped_loader({"persona_id": "persona-alpha"}, {"candidate_id": "cand-1"}) == {"rows": []}
    pointer_file = dataset_dir / "canonical-dataset-version.json"
    assert pointer_file.is_file()
    assert json.loads(pointer_file.read_text(encoding="utf-8"))["dataset_version"] == "dv-002"

    stop_event = asyncio.Event()

    class _FakeLoopControllerWriter:
        def __init__(self, dsn: str, *, tenant_id: str, controller_name: str, lease_duration_seconds: int) -> None:
            self.tenant_id = tenant_id

        async def record_heartbeat(
            self,
            loop_id: str,
            *,
            downstream_actual_state: dict[str, Any],
            payload: dict[str, Any],
            **_kwargs: Any,
        ) -> None:
            heartbeats.append((loop_id, payload, downstream_actual_state))
            stop_event.set()

    import importlib
    loop_control_mod = importlib.import_module("services.loop-control")
    monkeypatch.setattr(loop_control_mod, "LoopControllerWriter", _FakeLoopControllerWriter)
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/pantheon")

    wrapped = persona_main._OwnerPortAwareCycleController(_StubInnerController(), dataset_loader=wrapped_loader)
    asyncio.run(run_controller(wrapped, stop_event, interval_seconds=0.01))

    ooda_heartbeats = [item for item in heartbeats if item[0] == "per_persona_ooda"]
    assert len(ooda_heartbeats) == 1
    _, ooda_payload, ooda_state = ooda_heartbeats[0]
    assert ooda_state.get("status") == "degraded"
    assert any(
        f.get("owner_port") == "search" and f.get("persona_id") == "persona-alpha"
        for f in ooda_payload["failures"]
    )
