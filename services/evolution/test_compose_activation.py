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
    assert scheduler["environment"]["EVOLUTION_API_URL"] == "http://evolution:8093"
    assert (
        scheduler["environment"]["EVOLUTION_SCHEDULER_INTERVAL_SECONDS"]
        == "${EVOLUTION_SCHEDULER_INTERVAL_SECONDS:-86400}"
    )
    assert scheduler["depends_on"]["evolution"]["condition"] == "service_healthy"


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
