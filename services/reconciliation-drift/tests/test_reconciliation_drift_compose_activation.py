from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_compose_wires_reconciliation_drift_as_derived_read_model() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    drift = services["reconciliation-drift-svc"]
    assert drift["build"]["dockerfile"] == "services/reconciliation-drift/Dockerfile"
    assert drift["environment"]["PORT"] == "8102"
    assert drift["environment"]["RECONCILIATION_DRIFT_DATA_DIR"] == "/data/reconciliation-drift"
    assert drift["environment"]["PANTHEON_TELEMETRY_API_URL"] == "http://telemetry:8083"
    assert drift["environment"]["PANTHEON_LINEAGE_READ_URL"] == "http://lineage-read:8094"
    assert drift["environment"]["PANTHEON_RUNTIME_MANAGER_URL"] == "http://runtime-manager:8081"
    assert "reconciliation-drift-data:/data/reconciliation-drift" in drift["volumes"]
    assert drift["ports"] == ["${RECONCILIATION_DRIFT_PORT:-18102}:8102"]
    assert drift["depends_on"]["telemetry"]["condition"] == "service_healthy"
    assert drift["depends_on"]["lineage-read"]["condition"] == "service_healthy"
    assert drift["depends_on"]["runtime-manager"]["condition"] == "service_healthy"
    assert "healthcheck" in drift

    smoke = services["smoke-stack"]
    assert smoke["environment"]["RECONCILIATION_DRIFT_URL"] == "http://reconciliation-drift-svc:8102"
    assert smoke["depends_on"]["reconciliation-drift-svc"]["condition"] == "service_healthy"
    assert "reconciliation-drift-data" in compose["volumes"]
