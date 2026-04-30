from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_compose_wires_openclaw_gateway_adapter_without_broker_activation() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    adapter = services["openclaw-gateway-adapter"]
    assert adapter["build"]["dockerfile"] == "services/openclaw-gateway-adapter/Dockerfile"
    assert adapter["environment"]["PORT"] == "8104"
    assert adapter["environment"]["OPENCLAW_GATEWAY_URL"] == "http://openclaw-gateway:18789"
    assert adapter["environment"]["OPENCLAW_UPSTREAM_RETRIES"] == "${OPENCLAW_UPSTREAM_RETRIES:-1}"
    assert adapter["environment"]["OPENCLAW_PRODUCTION_BROKER_ENABLED"] == "false"
    assert adapter["environment"]["OPENCLAW_PAPER_ADAPTER_ENABLED"] == "false"
    assert adapter["environment"]["OPENCLAW_LIVE_ADAPTER_ENABLED"] == "false"
    assert adapter["environment"]["OPENCLAW_CAPITAL_BINDING_ENABLED"] == "false"
    assert adapter["ports"] == ["${OPENCLAW_GATEWAY_ADAPTER_PORT:-18104}:8104"]

    healthcheck = " ".join(adapter["healthcheck"]["test"])
    assert "/livez" in healthcheck
    assert "/readyz" not in healthcheck

    init = services["openclaw-data-init"]
    assert init["profiles"] == ["openclaw"]
    assert "chown -R 1000:1000 /home/node/.openclaw" in " ".join(init["command"])

    upstream = services["openclaw-gateway"]
    assert upstream["profiles"] == ["openclaw"]
    assert upstream["depends_on"]["openclaw-data-init"]["condition"] == "service_completed_successfully"
    assert "/healthz" in " ".join(upstream["healthcheck"]["test"])
    assert "profiles" not in adapter

    smoke = services["smoke-stack"]
    assert smoke["environment"]["OPENCLAW_GATEWAY_ADAPTER_URL"] == "http://openclaw-gateway-adapter:8104"
    assert smoke["depends_on"]["openclaw-gateway-adapter"]["condition"] == "service_healthy"


def test_honest_stack_smoke_checks_openclaw_adapter_degraded_boundary() -> None:
    smoke = (ROOT / "scripts/smoke_honest_stack.py").read_text(encoding="utf-8")

    assert 'OPENCLAW_GATEWAY_ADAPTER_URL = os.getenv("OPENCLAW_GATEWAY_ADAPTER_URL", "http://127.0.0.1:8104")' in smoke
    assert '_wait_for_health("openclaw-gateway-adapter", f"{OPENCLAW_GATEWAY_ADAPTER_URL}/livez")' in smoke
    assert "/api/openclaw-adapter/capabilities" in smoke
    assert "/api/openclaw-adapter/sessions" in smoke
    assert "CAPABILITY_DENIED" in smoke
