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
    assert worker["environment"] == {
        "EVOLUTION_API_URL": "http://evolution:8093",
        "EVOLUTION_DISPATCH_ACTOR_ID": (
            "${EVOLUTION_DISPATCH_ACTOR_ID:-evolution-dispatch-worker}"
        ),
        "EVOLUTION_DISPATCH_INTERVAL_SECONDS": (
            "${EVOLUTION_DISPATCH_INTERVAL_SECONDS:-30}"
        ),
        "EVOLUTION_DISPATCH_MAX_TICKS": "${EVOLUTION_DISPATCH_MAX_TICKS:-0}",
        "EVOLUTION_DISPATCH_TIMEOUT_SECONDS": (
            "${EVOLUTION_DISPATCH_TIMEOUT_SECONDS:-10}"
        ),
        "EVOLUTION_DISPATCH_HEALTH_FILE": (
            "${EVOLUTION_DISPATCH_HEALTH_FILE:-/tmp/evolution-dispatch-health.json}"
        ),
    }
    assert worker["depends_on"]["evolution"]["condition"] == "service_healthy"
    assert worker["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "services.evolution.dispatch_worker",
        "healthcheck",
    ]
