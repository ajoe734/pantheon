from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_compose_wires_research_orchestrator_without_production_activation() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    research = services["research-orchestrator-svc"]
    assert research["build"]["dockerfile"] == "services/research/Dockerfile"
    assert research["environment"]["PORT"] == "8101"
    assert research["environment"]["RESEARCH_ORCHESTRATOR_DATA_DIR"] == "/data/research-orchestrator"
    assert research["environment"]["RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS"] == "false"
    assert research["environment"]["RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS"] == "${RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS:-8}"
    assert "research-orchestrator-data:/data/research-orchestrator" in research["volumes"]
    assert research["ports"] == ["${RESEARCH_ORCHESTRATOR_PORT:-18101}:8101"]
    assert "healthcheck" in research

    smoke = services["smoke-stack"]
    assert smoke["environment"]["RESEARCH_ORCHESTRATOR_URL"] == "http://research-orchestrator-svc:8101"
    assert smoke["depends_on"]["research-orchestrator-svc"]["condition"] == "service_healthy"
    assert "research-orchestrator-data" in compose["volumes"]
