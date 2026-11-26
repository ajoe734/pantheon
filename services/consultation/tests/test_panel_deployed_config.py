from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _env_map(service: dict) -> dict:
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return env
    result = {}
    for item in env:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def test_root_compose_wires_consultation_panel_addresses() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    consultation = services["consultation-svc"]
    consultation_env = _env_map(consultation)

    # Consult-policy panels need working governance, evolution and memory addresses.
    assert consultation_env["CONSULTATION_HANDOFF_SINK_URL"] == (
        "http://governance:8082/api/governance/consultation-handoffs"
    )
    assert consultation_env["PANTHEON_MEMORY_API_URL"] == "http://memory:8086"
    assert consultation_env["PANTHEON_EVOLUTION_API_URL"] == "http://evolution:8093"
    for dependency in ("governance", "memory"):
        assert consultation["depends_on"][dependency]["condition"] == "service_healthy"
    # evolution transitively depends on consultation-svc (via runtime-manager),
    # so it is an address, not a start-order dependency.
    assert "evolution" in services and "evolution" not in consultation["depends_on"]


def test_consultable_flag_follows_consult_policy_and_fails_closed() -> None:
    sys.path.insert(0, str(ROOT / "services" / "control-plane" / "persona"))
    try:
        from persona_policy_resolver import ConsultPolicy, PersonaPolicyResolver
    finally:
        sys.path.pop(0)

    resolver = PersonaPolicyResolver(
        consult_policies={
            "cp-panel": ConsultPolicy("cp-panel"),
            "cp-off": ConsultPolicy("cp-off", consultable=False),
        }
    )

    def persona(policy_id, status="active"):
        return SimpleNamespace(
            persona_id="p", status=status, tool_profile_id=None,
            route_policy_id=None, consult_policy_id=policy_id,
        )

    assert resolver.resolve(persona("cp-panel")).consultable is True
    assert resolver.resolve(persona("cp-off")).consultable is False
    assert resolver.resolve(persona("cp-unknown")).consultable is False
    assert resolver.resolve(persona("cp-panel", status="suspended")).consultable is False
