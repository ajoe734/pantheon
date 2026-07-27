from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_daily_sweep_scheduler_is_enabled_by_default_in_root_compose() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    scheduler = compose["services"]["evolution-daily-sweep-scheduler"]

    assert "profiles" not in scheduler
    assert scheduler["build"]["dockerfile"] == "services/evolution/Dockerfile"
    assert scheduler["command"] == ["python", "-m", "services.evolution.scheduler_worker"]
    assert scheduler["restart"] == "unless-stopped"
    assert scheduler["stop_grace_period"] == "30s"
    assert scheduler["environment"]["EVOLUTION_API_URL"] == "http://evolution:8093"
    assert scheduler["environment"]["EVOLUTION_AUTH_MODE"] == (
        "${EVOLUTION_AUTH_MODE:-disabled}"
    )
    assert scheduler["environment"]["EVOLUTION_AUTH_TOKEN"] == (
        "${EVOLUTION_AUTH_TOKEN:-}"
    )
    assert scheduler["environment"]["EVOLUTION_SCHEDULER_TENANT_ID"] == (
        "${EVOLUTION_DEFAULT_TENANT_ID:-pantheon-default}"
    )
    assert (
        scheduler["environment"]["EVOLUTION_SCHEDULER_INTERVAL_SECONDS"]
        == "${EVOLUTION_SCHEDULER_INTERVAL_SECONDS:-86400}"
    )
    assert scheduler["environment"]["EVOLUTION_SCHEDULER_HEALTH_FILE"] == (
        "${EVOLUTION_SCHEDULER_HEALTH_FILE:-/tmp/evolution-scheduler-health.json}"
    )
    assert scheduler["depends_on"]["evolution"]["condition"] == "service_healthy"
    assert scheduler["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "services.evolution.scheduler_worker",
        "healthcheck",
    ]


def test_threshold_sweep_producer_forwards_metric_max_age_env_in_root_compose() -> None:
    """Compose must forward EVOCHAIN_THRESHOLD_SWEEP_METRIC_MAX_AGE_SECONDS,
    which the worker actually reads (threshold_sweep_worker.py `main()`), or
    an operator-set override in the host environment silently never reaches
    the container (round-7 review point 7)."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    producer = compose["services"]["evolution-threshold-sweep-producer"]

    assert (
        producer["environment"]["EVOCHAIN_THRESHOLD_SWEEP_METRIC_MAX_AGE_SECONDS"]
        == "${EVOCHAIN_THRESHOLD_SWEEP_METRIC_MAX_AGE_SECONDS:-172800}"
    )


def test_threshold_sweep_producer_compose_shape_matches_acceptance_criteria() -> None:
    """Full compose-shape assertion for the EVOCHAIN-001 acceptance criterion
    ("compose service ships with EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS
    default 86400 and its own logs"), not just the metric-max-age env forward
    already covered above (round-8 review: "add complete compose acceptance
    assertions"). "Its own logs" means its own container process (a distinct
    `command`/service entry, not aliased onto another service's process),
    which stdout-logs per-tick JSON via `main()` under `docker compose logs`."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    producer = compose["services"]["evolution-threshold-sweep-producer"]

    assert "profiles" not in producer  # default-on, not opt-in
    assert producer["build"]["dockerfile"] == "services/evolution/Dockerfile"
    assert producer["command"] == ["python", "-m", "services.evolution.threshold_sweep_worker"]
    assert producer["restart"] == "unless-stopped"
    assert producer["stop_grace_period"] == "30s"

    assert producer["environment"]["EVOCHAIN_TELEMETRY_API_URL"] == "http://telemetry:8083"
    assert producer["environment"]["EVOCHAIN_INCIDENTS_API_URL"] == "http://incidents:8090"
    assert (
        producer["environment"]["EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS"]
        == "${EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS:-86400}"
    )
    assert producer["environment"]["EVOCHAIN_THRESHOLD_SWEEP_HEALTH_FILE"] == (
        "${EVOCHAIN_THRESHOLD_SWEEP_HEALTH_FILE:-/tmp/evolution-threshold-sweep-health.json}"
    )

    # Config/baselines are bind-mounted read-only so an operator can retune
    # threshold values by editing the host files and restarting this one
    # service, no image rebuild; state is on a named volume so the WAL
    # (and its quarantine tombstones) survive a container restart.
    assert "./services/evolution/config:/workspace/services/evolution/config:ro" in producer["volumes"]
    assert "evolution-data:/data/evolution" in producer["volumes"]
    assert (
        producer["environment"]["EVOCHAIN_THRESHOLD_SWEEP_STATE_PATH"]
        == "/data/evolution/threshold_sweep_state.json"
    )

    assert producer["depends_on"]["telemetry"]["condition"] == "service_healthy"
    assert producer["depends_on"]["incidents"]["condition"] == "service_healthy"
    assert producer["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "services.evolution.threshold_sweep_worker",
        "healthcheck",
    ]

    # This is its own service/process, not folded into evolution-daily-sweep
    # -scheduler's command or cadence — a distinct compose log stream.
    scheduler = compose["services"]["evolution-daily-sweep-scheduler"]
    assert producer["command"] != scheduler["command"]


def test_dispatch_worker_is_enabled_by_default_in_root_compose() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    worker = compose["services"]["evolution-dispatch-worker"]

    assert "profiles" not in worker
    assert worker["build"]["dockerfile"] == "services/evolution/Dockerfile"
    assert worker["command"] == [
        "python",
        "-m",
        "services.evolution.dispatch_worker",
    ]
    assert worker["restart"] == "unless-stopped"
    assert worker["stop_grace_period"] == "30s"
    environment = worker["environment"]
    assert environment["EVOLUTION_API_URL"] == "http://evolution:8093"
    assert environment["EVOLUTION_DATA_DIR"] == "/data/evolution"
    assert environment["EVOLUTION_STORE_BACKEND"] == "${EVOLUTION_STORE_BACKEND:-json}"
    assert environment["EVOLUTION_STORE_DSN"] == "${EVOLUTION_STORE_DSN:-}"
    assert environment["PANTHEON_PERSISTENCE_POSTURE"] == (
        "${PANTHEON_PERSISTENCE_POSTURE:-dev}"
    )
    assert environment["EVOLUTION_AUTH_MODE"] == "${EVOLUTION_AUTH_MODE:-disabled}"
    assert environment["EVOLUTION_AUTH_TOKEN"] == "${EVOLUTION_AUTH_TOKEN:-}"
    assert environment["EVOLUTION_DISPATCH_ACTOR_ID"] == (
        "${EVOLUTION_DISPATCH_ACTOR_ID:-evolution-dispatch-worker}"
    )
    assert environment["EVOLUTION_DISPATCH_INTERVAL_SECONDS"] == (
        "${EVOLUTION_DISPATCH_INTERVAL_SECONDS:-30}"
    )
    assert environment["EVOLUTION_DISPATCH_MAX_TICKS"] == (
        "${EVOLUTION_DISPATCH_MAX_TICKS:-0}"
    )
    assert environment["EVOLUTION_DISPATCH_TIMEOUT_SECONDS"] == (
        "${EVOLUTION_DISPATCH_TIMEOUT_SECONDS:-10}"
    )
    assert environment["EVOLUTION_DISPATCH_HEALTH_FILE"] == (
        "${EVOLUTION_DISPATCH_HEALTH_FILE:-/tmp/evolution-dispatch-health.json}"
    )
    assert "evolution-data:/data/evolution" in worker["volumes"]
    api = compose["services"]["evolution"]
    assert api["stop_grace_period"] == "30s"
    assert "evolution-data:/data/evolution" in api["volumes"]
    assert api["environment"]["EVOLUTION_DATA_DIR"] == environment["EVOLUTION_DATA_DIR"]
    assert api["environment"]["EVOLUTION_STORE_BACKEND"] == environment[
        "EVOLUTION_STORE_BACKEND"
    ]
    assert api["environment"]["EVOLUTION_STORE_DSN"] == environment[
        "EVOLUTION_STORE_DSN"
    ]
    assert api["environment"]["PANTHEON_PERSISTENCE_POSTURE"] == environment[
        "PANTHEON_PERSISTENCE_POSTURE"
    ]
    assert worker["depends_on"]["evolution"]["condition"] == "service_healthy"
    assert worker["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "services.evolution.dispatch_worker",
        "healthcheck",
    ]
