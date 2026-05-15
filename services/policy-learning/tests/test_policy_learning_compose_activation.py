from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_compose_wires_policy_learning_service_without_production_activation() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    policy_learning = services["policy-learning-svc"]
    assert policy_learning["build"]["dockerfile"] == "services/policy-learning/Dockerfile"
    assert policy_learning["environment"]["PORT"] == "8100"
    assert policy_learning["environment"]["POLICY_LEARNING_DATA_DIR"] == "/data/policy-learning"
    assert policy_learning["environment"]["POLICY_LEARNING_ENABLE_PRODUCTION_ADAPTERS"] == "false"
    assert "policy-learning-data:/data/policy-learning" in policy_learning["volumes"]
    assert policy_learning["ports"] == ["${POLICY_LEARNING_PORT:-18100}:8100"]
    assert "healthcheck" in policy_learning

    smoke = services["smoke-stack"]
    assert smoke["environment"]["POLICY_LEARNING_URL"] == "http://policy-learning-svc:8100"
    assert smoke["depends_on"]["policy-learning-svc"]["condition"] == "service_healthy"
    assert "policy-learning-data" in compose["volumes"]
