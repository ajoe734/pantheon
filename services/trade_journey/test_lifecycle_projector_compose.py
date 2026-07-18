from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_lifecycle_projector_is_default_and_owns_both_read_models():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    projector = services["loop-run-projector-scheduler"]
    environment = projector["environment"]

    assert "profiles" not in projector
    assert projector["restart"] == "unless-stopped"
    assert projector["build"]["dockerfile"] == "services/telemetry/Dockerfile"
    assert projector["command"] == [
        "python",
        "-m",
        "services.trade_journey.lifecycle_projector",
        "run",
    ]
    assert environment["TELEMETRY_DB_DSN"].startswith(
        "${TELEMETRY_DB_DSN:-postgresql://"
    )
    assert environment["LIFECYCLE_PROJECTION_ROOT"] == "/data/bff/lifecycle-projection"
    assert (
        environment["LIFECYCLE_PROJECTOR_STATE_PATH"]
        == "/data/bff/lifecycle-projection/controller_state.json"
    )
    assert environment["LIFECYCLE_PROJECTOR_POLL_SECONDS"] == "${LIFECYCLE_PROJECTOR_POLL_SECONDS:-1}"
    assert environment["GIT_SHA"] == "${GIT_SHA:-unknown}"
    assert "bff-data:/data/bff" in projector["volumes"]
    assert projector["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert projector["depends_on"]["telemetry"]["condition"] == "service_healthy"
    assert "operator-bff" not in projector["depends_on"]
    healthcheck = projector["healthcheck"]["test"]
    assert healthcheck[0] == "CMD-SHELL"
    assert "controller_state.json" in healthcheck[1]
    assert "last_error" in healthcheck[1]
    assert "age < 600" in healthcheck[1]

    bff = services["operator-bff"]
    bff_environment = bff["environment"]
    assert bff_environment["PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE"].endswith(
        ":-/data/bff/lifecycle-projection/current/trade_journey_events.json}"
    )
    assert bff_environment["PANTHEON_BFF_LOOP_RUN_STORE"].endswith(
        ":-/data/bff/lifecycle-projection/current/loop_runs.json}"
    )
    assert (
        bff["depends_on"]["loop-run-projector-scheduler"]["condition"]
        == "service_healthy"
    )


def test_legacy_snapshot_projector_can_only_write_backfill_truth():
    projector = (ROOT / "scripts/paper_loop_run_projector.py").read_text(
        encoding="utf-8"
    )
    scheduler = (ROOT / "scripts/loop_run_projector_scheduler.py").read_text(
        encoding="utf-8"
    )
    assert '"projection_mode": "backfill"' in projector
    assert '"accepted_live": False' in projector
    assert '"truth_level": "legacy_snapshot_backfill"' in projector
    assert "PANTHEON_BFF_LOOP_RUN_BACKFILL_STORE" in projector
    assert '"/data/bff/loop_runs_backfill.json"' in projector
    assert "services.trade_journey.lifecycle_projector" in scheduler


def test_default_paper_signal_producer_uses_package_safe_module_entrypoint():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    producer = compose["services"]["paper-signal-producer"]

    assert "profiles" not in producer
    assert producer["restart"] == "unless-stopped"
    assert producer["command"] == [
        "python",
        "-m",
        "services.execution.lean_runtime.paper_signal_producer",
    ]
