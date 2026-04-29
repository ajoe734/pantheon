from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_compose_wires_research_worker_gateway_without_production_activation() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    gateway = services["research-worker-gateway-svc"]
    assert gateway["build"]["dockerfile"] == "services/research-worker-gateway/Dockerfile"
    assert gateway["environment"]["PORT"] == "8103"
    assert gateway["environment"]["RESEARCH_WORKER_GATEWAY_DATA_DIR"] == "/data/research-worker-gateway"
    assert gateway["environment"]["RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS"] == "false"
    assert gateway["environment"]["RESEARCH_WORKER_GATEWAY_MAX_ACTIVE_JOBS"] == "${RESEARCH_WORKER_GATEWAY_MAX_ACTIVE_JOBS:-4}"
    assert gateway["environment"]["RESEARCH_ORCHESTRATOR_URL"] == "http://research-orchestrator-svc:8101"
    assert "research-worker-gateway-data:/data/research-worker-gateway" in gateway["volumes"]
    assert gateway["depends_on"]["research-orchestrator-svc"]["condition"] == "service_healthy"
    assert gateway["ports"] == ["${RESEARCH_WORKER_GATEWAY_PORT:-18103}:8103"]
    assert "healthcheck" in gateway

    smoke = services["smoke-stack"]
    assert smoke["environment"]["RESEARCH_WORKER_GATEWAY_URL"] == "http://research-worker-gateway-svc:8103"
    assert smoke["depends_on"]["research-worker-gateway-svc"]["condition"] == "service_healthy"
    assert "research-worker-gateway-data" in compose["volumes"]


def test_honest_stack_smoke_waits_for_research_worker_gateway() -> None:
    smoke = (ROOT / "scripts/smoke_honest_stack.py").read_text(encoding="utf-8")

    assert 'RESEARCH_WORKER_GATEWAY_URL = os.getenv("RESEARCH_WORKER_GATEWAY_URL", "http://127.0.0.1:8103")' in smoke
    assert '_wait_for_health("research-worker-gateway-svc", f"{RESEARCH_WORKER_GATEWAY_URL}/readyz")' in smoke
