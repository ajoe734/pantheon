from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_lifecycle_projector_is_default_and_owns_both_read_models():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    projector = services["loop-run-projector-scheduler"]
    environment = projector["environment"]

    assert "profiles" not in projector
    assert projector["restart"] == "${LIFECYCLE_PROJECTOR_RESTART_POLICY:-no}"
    assert projector["mem_limit"] == "${LIFECYCLE_PROJECTOR_MEMORY_LIMIT:-16g}"
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
    assert environment["LIFECYCLE_PROJECTOR_HEALTH_STATE_PATH"] == (
        "/data/bff/lifecycle-projection/health_state.json"
    )
    assert environment["LIFECYCLE_PROJECTOR_POLL_SECONDS"] == "${LIFECYCLE_PROJECTOR_POLL_SECONDS:-1}"
    assert environment["LIFECYCLE_PROJECTOR_GENERATION_RETENTION"] == (
        "${LIFECYCLE_PROJECTOR_GENERATION_RETENTION:-4}"
    )
    assert environment["LIFECYCLE_PROJECTOR_STAGING_MAX_AGE_SECONDS"] == (
        "${LIFECYCLE_PROJECTOR_STAGING_MAX_AGE_SECONDS:-3600}"
    )
    assert environment["LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS"] == (
        "${LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS:-120}"
    )
    assert environment["LIFECYCLE_PROJECTOR_HEALTH_MIN_FREE_BYTES"] == (
        "${LIFECYCLE_PROJECTOR_HEALTH_MIN_FREE_BYTES:-134217728}"
    )
    assert environment["LIFECYCLE_PROJECTOR_HEALTH_MIN_FREE_PERCENT"] == (
        "${LIFECYCLE_PROJECTOR_HEALTH_MIN_FREE_PERCENT:-5}"
    )
    assert environment["GIT_SHA"] == "${GIT_SHA:-unknown}"
    assert "bff-data:/data/bff" in projector["volumes"]
    assert projector["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert projector["depends_on"]["telemetry"]["condition"] == "service_healthy"
    assert "operator-bff" not in projector["depends_on"]
    healthcheck = projector["healthcheck"]["test"]
    assert healthcheck == [
        "CMD",
        "python",
        "-m",
        "services.trade_journey.lifecycle_projector",
        "healthcheck",
    ]

    bff = services["operator-bff"]
    bff_environment = bff["environment"]
    assert bff_environment["PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE"] == (
        "/data/bff/lifecycle-projection/current/trade_journey_events.json"
    )
    assert bff_environment["PANTHEON_BFF_LOOP_RUN_STORE"] == (
        "/data/bff/lifecycle-projection/current/loop_runs.json"
    )
    assert bff_environment["LIFECYCLE_PROJECTOR_STATE_PATH"] == (
        "/data/bff/lifecycle-projection/controller_state.json"
    )
    assert bff_environment["LIFECYCLE_PROJECTOR_HEALTH_STATE_PATH"] == (
        "/data/bff/lifecycle-projection/health_state.json"
    )
    assert bff_environment["LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS"] == (
        "${LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS:-120}"
    )
    assert (
        bff["depends_on"]["loop-run-projector-scheduler"]["condition"]
        == "service_started"
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
    environment = producer["environment"]

    assert "profiles" not in producer
    assert producer["restart"] == "unless-stopped"
    assert producer["command"] == [
        "python",
        "-m",
        "services.execution.lean_runtime.paper_signal_producer",
    ]
    assert environment["TELEMETRY_DB_DSN"].startswith(
        "${TELEMETRY_DB_DSN:-postgresql://"
    )


def test_lifecycle_projector_capacity_benchmark_is_profile_gated_and_bounded():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    benchmark = services["lifecycle-projector-capacity-benchmark"]

    # LIFECYCLE-PROJ-CAPACITY-001: the benchmark is a distinct, opt-in job. It
    # must never start with the default stack and must never share a writer
    # role with the canonical projector service.
    assert benchmark["profiles"] == ["lifecycle-capacity-benchmark"]
    assert benchmark["restart"] == "no"
    assert benchmark["mem_limit"] == "${LIFECYCLE_PROJECTOR_CAPACITY_MEMORY_LIMIT:-4g}"
    assert benchmark["build"]["dockerfile"] == "services/telemetry/Dockerfile"
    assert benchmark["command"] == [
        "python",
        "-m",
        "services.trade_journey.lifecycle_projector_capacity",
        "--events",
        "${LIFECYCLE_PROJECTOR_CAPACITY_EVENTS:-1000000}",
        "--loop-runs",
        "${LIFECYCLE_PROJECTOR_CAPACITY_LOOP_RUNS:-150000}",
        "--batch-size",
        "${LIFECYCLE_PROJECTOR_CAPACITY_BATCH_SIZE:-500}",
        "--output",
        "/data/bff/lifecycle-capacity/evidence.json",
    ]
    assert benchmark["environment"]["TELEMETRY_DB_DSN"].startswith(
        "${TELEMETRY_DB_DSN:-postgresql://"
    )
    assert benchmark["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert benchmark["depends_on"]["telemetry"]["condition"] == "service_healthy"

    default_services = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )["services"]
    assert "profiles" not in default_services["loop-run-projector-scheduler"]


def test_hosted_lifecycle_probe_uses_mfa_bound_governed_operator():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/nonprod-deploy.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["deploy-dev"]["steps"]
    probe = next(
        step
        for step in steps
        if step.get("name") == "Dev canonical paper lifecycle hosted probe"
    )

    assert probe["env"]["DEV_BFF_OIDC_CLIENT_ID"] == (
        "${{ vars.DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID "
        "|| 'pantheon-dev-operator-a-v1' }}"
    )
    assert probe["env"]["DEV_BFF_OIDC_CLIENT_SECRET"] == (
        "${{ secrets.DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET }}"
    )
    assert "--expected-login-identity operator_a" in probe["run"]


def test_managed_dev_deploy_forwards_lifecycle_freshness_budget():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/nonprod-deploy.yml").read_text(encoding="utf-8")
    )
    deploy = next(
        step
        for step in workflow["jobs"]["deploy-dev"]["steps"]
        if step.get("name") == "Deploy dev VM stack under lease"
    )
    assert deploy["env"][
        "DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS"
    ] == (
        "${{ vars.DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS || '300' }}"
    )

    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    assert (
        'DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS="'
        '${DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS:-300}"'
    ) in script
    assert (
        'command_prefix+=" '
        "PANTHEON_DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS="
        '$(shell_quote "$DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS")"'
    ) in script

    root_block = script.split("\n  root)", 1)[1].split("\n  bff)", 1)[0]
    bff_block = script.split("\n  bff)", 1)[1].split("\n  exec)", 1)[0]
    compose_override = (
        'LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS="'
        '${PANTHEON_DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS}"'
    )
    assert root_block.count(compose_override) == 2
    assert bff_block.count(compose_override) == 1


def test_bff_only_deploy_rebuilds_its_lifecycle_projector_only():
    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    bff_block = script.split("\n  bff)", 1)[1].split("\n  exec)", 1)[0]

    compose_up = (
        "docker compose -p pantheon -f docker-compose.yml "
        "up -d --build --no-deps operator-bff loop-run-projector-scheduler"
    )
    assert bff_block.count(compose_up) == 1
    assert "runtime-manager" not in compose_up
    assert "paper-fleet" not in compose_up
