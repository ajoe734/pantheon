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
    assert (
        drift["environment"]["RECONCILIATION_DRIFT_STORE_BACKEND"]
        == "${RECONCILIATION_DRIFT_STORE_BACKEND:-postgres}"
    )
    assert (
        drift["environment"]["DATABASE_URL"]
        == "${DATABASE_URL:-postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon}"
    )
    assert (
        drift["environment"]["RECONCILIATION_DRIFT_STORE_DSN"]
        == "${RECONCILIATION_DRIFT_STORE_DSN:-}"
    )
    assert drift["environment"]["PANTHEON_TELEMETRY_API_URL"] == "http://telemetry:8083"
    assert drift["environment"]["PANTHEON_LINEAGE_READ_URL"] == "http://lineage-read:8094"
    assert drift["environment"]["PANTHEON_RUNTIME_MANAGER_URL"] == "http://runtime-manager:8081"
    assert drift["environment"]["PANTHEON_INCIDENTS_API_URL"] == "http://incidents:8090"
    assert "reconciliation-drift-data:/data/reconciliation-drift" in drift["volumes"]
    assert drift["ports"] == ["${RECONCILIATION_DRIFT_PORT:-18102}:8102"]
    assert drift["depends_on"]["telemetry"]["condition"] == "service_healthy"
    assert drift["depends_on"]["lineage-read"]["condition"] == "service_healthy"
    assert drift["depends_on"]["runtime-manager"]["condition"] == "service_healthy"
    assert drift["depends_on"]["incidents"]["condition"] == "service_healthy"
    assert "healthcheck" in drift
    assert "timeout=20" in drift["healthcheck"]["test"][-1]
    assert drift["healthcheck"]["interval"] == "15s"
    assert drift["healthcheck"]["timeout"] == "25s"
    assert drift["healthcheck"]["start_period"] == "30s"

    consumer = services["reconciliation-drift-consumer"]
    assert consumer["profiles"] == ["root", "workers"]
    assert consumer["build"]["dockerfile"] == "services/reconciliation-drift/Dockerfile"
    assert consumer["command"] == ["python", "services/reconciliation-drift/consumer.py"]
    assert consumer["restart"] == "unless-stopped"
    assert consumer["environment"]["RECONCILIATION_DRIFT_URL"] == "http://reconciliation-drift-svc:8102"
    assert consumer["environment"]["PANTHEON_TELEMETRY_API_URL"] == "http://telemetry:8083"
    assert "RECONCILIATION_DRIFT_CONSUMER_INPUT" not in consumer["environment"]
    assert (
        consumer["environment"]["RECONCILIATION_DRIFT_CONSUMER_WORKER_STATE_PATH"]
        == "/data/reconciliation-drift/consumer-worker-state.json"
    )
    assert (
        consumer["environment"]["RECONCILIATION_DRIFT_CONSUMER_INTERVAL_SECONDS"]
        == "${RECONCILIATION_DRIFT_CONSUMER_INTERVAL_SECONDS:-60}"
    )
    assert (
        consumer["environment"]["RECONCILIATION_DRIFT_CONSUMER_MAX_TICKS"]
        == "${RECONCILIATION_DRIFT_CONSUMER_MAX_TICKS:-0}"
    )
    assert "reconciliation-drift-data:/data/reconciliation-drift" in consumer["volumes"]
    assert consumer["depends_on"]["reconciliation-drift-svc"]["condition"] == "service_healthy"
    assert consumer["depends_on"]["telemetry"]["condition"] == "service_healthy"
    assert consumer["environment"]["RECONCILIATION_DRIFT_CONSUMER_HEALTH_FILE"] == (
        "${RECONCILIATION_DRIFT_CONSUMER_HEALTH_FILE:-"
        "/tmp/reconciliation-drift-consumer-health.json}"
    )
    assert consumer["environment"][
        "RECONCILIATION_DRIFT_CONSUMER_HEALTH_MAX_AGE_SECONDS"
    ] == "${RECONCILIATION_DRIFT_CONSUMER_HEALTH_MAX_AGE_SECONDS:-300}"
    assert consumer["healthcheck"]["test"] == [
        "CMD",
        "python",
        "services/reconciliation-drift/consumer.py",
        "healthcheck",
    ]
    assert consumer["healthcheck"]["start_period"] == "300s"

    scheduler = services["reconciliation-drift-scheduler"]
    assert scheduler["profiles"] == ["root", "workers"]
    assert scheduler["build"]["dockerfile"] == "services/reconciliation-drift/Dockerfile"
    assert scheduler["command"] == ["python", "services/reconciliation-drift/scheduler_worker.py"]
    assert scheduler["restart"] == "unless-stopped"
    assert scheduler["environment"]["RECONCILIATION_DRIFT_URL"] == "http://reconciliation-drift-svc:8102"
    assert (
        scheduler["environment"]["RECONCILIATION_DRIFT_SCHEDULER_INTERVAL_SECONDS"]
        == "${RECONCILIATION_DRIFT_SCHEDULER_INTERVAL_SECONDS:-300}"
    )
    assert (
        scheduler["environment"]["RECONCILIATION_DRIFT_SCHEDULER_MAX_TICKS"]
        == "${RECONCILIATION_DRIFT_SCHEDULER_MAX_TICKS:-0}"
    )
    assert scheduler["depends_on"]["reconciliation-drift-svc"]["condition"] == "service_healthy"
    assert (
        scheduler["environment"]["RECONCILIATION_DRIFT_SCHEDULER_MAX_ATTEMPTS"]
        == "${RECONCILIATION_DRIFT_SCHEDULER_MAX_ATTEMPTS:-3}"
    )
    assert scheduler["environment"]["RECONCILIATION_DRIFT_SCHEDULER_HEALTH_FILE"] == (
        "${RECONCILIATION_DRIFT_SCHEDULER_HEALTH_FILE:-"
        "/tmp/reconciliation-drift-scheduler-health.json}"
    )
    assert scheduler["environment"][
        "RECONCILIATION_DRIFT_SCHEDULER_HEALTH_MAX_AGE_SECONDS"
    ] == "${RECONCILIATION_DRIFT_SCHEDULER_HEALTH_MAX_AGE_SECONDS:-900}"
    assert scheduler["healthcheck"]["test"] == [
        "CMD",
        "python",
        "services/reconciliation-drift/scheduler_worker.py",
        "healthcheck",
    ]
    assert scheduler["healthcheck"]["start_period"] == "300s"

    listener = services["reconciliation-drift-incident-listener"]
    assert listener["profiles"] == ["root", "workers"]
    assert listener["build"]["dockerfile"] == "services/reconciliation-drift/Dockerfile"
    assert listener["command"] == ["python", "services/reconciliation-drift/incident_listener.py"]
    assert listener["restart"] == "unless-stopped"
    assert listener["environment"]["RECONCILIATION_DRIFT_URL"] == "http://reconciliation-drift-svc:8102"
    assert listener["environment"]["PANTHEON_INCIDENTS_API_URL"] == "http://incidents:8090"
    assert (
        listener["environment"]["RECONCILIATION_DRIFT_INCIDENT_LISTENER_INTERVAL_SECONDS"]
        == "${RECONCILIATION_DRIFT_INCIDENT_LISTENER_INTERVAL_SECONDS:-15}"
    )
    assert (
        listener["environment"]["RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_TICKS"]
        == "${RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_TICKS:-0}"
    )
    assert listener["depends_on"]["reconciliation-drift-svc"]["condition"] == "service_healthy"
    assert listener["depends_on"]["incidents"]["condition"] == "service_healthy"
    assert (
        listener["environment"]["RECONCILIATION_DRIFT_INCIDENT_LISTENER_STATE_PATH"]
        == "/data/reconciliation-drift/incident-listener-state.json"
    )
    assert "reconciliation-drift-data:/data/reconciliation-drift" in listener["volumes"]
    assert listener["environment"][
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_FILE"
    ] == (
        "${RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_FILE:-"
        "/tmp/reconciliation-drift-incident-listener-health.json}"
    )
    assert listener["environment"][
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_MAX_AGE_SECONDS"
    ] == "${RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_MAX_AGE_SECONDS:-300}"
    assert listener["healthcheck"]["test"] == [
        "CMD",
        "python",
        "services/reconciliation-drift/incident_listener.py",
        "healthcheck",
    ]
    assert listener["healthcheck"]["start_period"] == "300s"

    smoke = services["smoke-stack"]
    assert smoke["environment"]["RECONCILIATION_DRIFT_URL"] == "http://reconciliation-drift-svc:8102"
    assert smoke["depends_on"]["reconciliation-drift-svc"]["condition"] == "service_healthy"
    assert "reconciliation-drift-data" in compose["volumes"]


def test_reconciliation_drift_compose_environment_builds_postgres_store_and_fails_closed_without_dsn(
    tmp_path: Path,
) -> None:
    """Acceptance 1, 2, 3: Dev compose environment selects Postgres store and fails closed without DSN."""
    import importlib.util
    import re
    from unittest import mock
    import pytest

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    drift = compose["services"]["reconciliation-drift-svc"]
    env_spec = drift["environment"]

    pattern = re.compile(r"^\$\{[^:]+:-([^}]+)\}$")
    backend_default = pattern.match(env_spec["RECONCILIATION_DRIFT_STORE_BACKEND"]).group(1)
    db_url_default = pattern.match(env_spec["DATABASE_URL"]).group(1)

    assert backend_default == "postgres"
    assert db_url_default == "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon"

    spec = importlib.util.spec_from_file_location(
        "reconciliation_drift_store_compose_test",
        ROOT / "services" / "reconciliation-drift" / "store.py",
    )
    assert spec and spec.loader
    store_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(store_mod)

    # 1. Proves the service builds the Postgres store from the dev compose environment
    with mock.patch.dict(
        "os.environ",
        {
            "RECONCILIATION_DRIFT_STORE_BACKEND": backend_default,
            "DATABASE_URL": db_url_default,
            "RECONCILIATION_DRIFT_STORE_BOOTSTRAP": "0",
        },
        clear=True,
    ):
        store = store_mod.build_reconciliation_drift_store(tmp_path)
        assert isinstance(store, store_mod.PostgresReconciliationDriftStore)
        assert getattr(store, "backend", None) == "postgres"

    # 2. Proves it fails closed instead of silently falling back to JSON when the DSN is missing
    with mock.patch.dict(
        "os.environ",
        {
            "RECONCILIATION_DRIFT_STORE_BACKEND": backend_default,
        },
        clear=True,
    ):
        with pytest.raises(
            ValueError,
            match="RECONCILIATION_DRIFT_STORE_DSN or DATABASE_URL is required for Postgres reconciliation store",
        ):
            store_mod.build_reconciliation_drift_store(tmp_path)

