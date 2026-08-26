from __future__ import annotations

from pathlib import Path

import yaml

from scripts.smoke_source_search_bounded import _run_scoped_connector_ids


def _env_map(service: dict) -> dict:
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return env
    result = {}
    for item in env:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def test_bounded_smoke_builds_disjoint_run_scoped_connector_ids() -> None:
    first = _run_scoped_connector_ids("first-run")
    second = _run_scoped_connector_ids("second-run")

    assert set(first) == {"static", "feed", "replay", "scheduled"}
    assert all(connector_id.endswith("-first-run") for connector_id in first.values())
    assert set(first.values()).isdisjoint(second.values())


def test_root_compose_wires_source_ingest_service_boundary() -> None:
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]

    source_ingest = services["source-ingest"]
    source_ingest_env = _env_map(source_ingest)
    assert source_ingest["build"]["dockerfile"] == "services/source_ingestion/Dockerfile"
    assert source_ingest_env["PORT"] == "8097"
    assert source_ingest_env["SOURCE_INGEST_DATA_DIR"] == "/data/source-ingest"
    assert source_ingest_env["SOURCE_INGEST_CONNECTOR_STORE_PATH"] == "/data/source-ingest/connector_config.jsonl"
    assert source_ingest_env["SOURCE_INGEST_EVIDENCE_STORE_PATH"] == "/data/source-ingest/source_evidence.jsonl"
    assert source_ingest_env["SOURCE_INGEST_CONTROLLER_STATE_PATH"] == "/data/source-ingest/controller_state.json"
    assert source_ingest_env["SOURCE_INGEST_REQUIREMENT_STATE_PATH"] == "/data/source-ingest/requirement_snapshots.jsonl"
    assert source_ingest_env["SOURCE_INGEST_CONTROLLER_TOKEN_FILE"] == "/data/source-ingest/controller_token"
    assert source_ingest_env["SOURCE_INGEST_MAX_RECORDS"] == "${SOURCE_INGEST_MAX_RECORDS:-100}"
    assert source_ingest_env["SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"] == "${SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY:-2}"
    assert source_ingest_env["SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS"] == "${SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS:-2}"
    assert source_ingest_env["SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS"] == "${SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS:-60}"
    assert source_ingest_env["SOURCE_INGEST_FRONTIER_RUNNING_TIMEOUT_SECONDS"] == "${SOURCE_INGEST_FRONTIER_RUNNING_TIMEOUT_SECONDS:-300}"
    assert source_ingest_env["SOURCE_INGEST_DEFAULT_STALE_THRESHOLD_SECONDS"] == "${SOURCE_INGEST_DEFAULT_STALE_THRESHOLD_SECONDS:-86400}"
    assert source_ingest_env["SEARCH_INGEST_NOTIFY_URL"] == "${SEARCH_INGEST_NOTIFY_URL:-http://search-svc:8098}"
    assert source_ingest_env["PANTHEON_SOURCE_SEARCH_POSTURE"] == "${PANTHEON_SOURCE_SEARCH_POSTURE:-dev}"
    assert source_ingest_env["PANTHEON_S3_ENDPOINT"] == "${PANTHEON_S3_ENDPOINT:-http://minio:9000}"
    assert source_ingest_env["PANTHEON_ARTIFACT_BUCKET"] == "${PANTHEON_ARTIFACT_BUCKET:-pantheon-artifacts}"
    assert source_ingest_env["SOURCE_INGEST_EVIDENCE_BACKEND"] == "${SOURCE_INGEST_EVIDENCE_BACKEND:-postgres}"
    assert source_ingest_env["PANTHEON_EXTERNAL_EGRESS"] == "${PANTHEON_EXTERNAL_EGRESS:-deny}"
    assert "source-ingest-data:/data/source-ingest" in source_ingest["volumes"]
    assert "${SOURCE_INGEST_PORT:-18097}:8097" in source_ingest["ports"]
    healthcheck = " ".join(source_ingest["healthcheck"]["test"])
    assert "os.environ.get('PORT','8097')" in healthcheck
    assert "/readyz" in healthcheck

    migration = services["source-ingest-controller-migrate"]
    assert migration["command"] == ["bash", "scripts/db_migrate.sh"]
    assert migration["restart"] == "no"
    assert migration["depends_on"]["postgres"]["condition"] == "service_healthy"

    controller = services["source-ingest-scheduler"]
    controller_env = _env_map(controller)
    # Default-on owner is a non-restarting internal reconcile-only one-shot.
    # The explicit deployment profile overrides mode and allowlists for a
    # bounded provider pull without creating a continuous external connection.
    assert "profiles" not in controller
    assert controller["restart"] == "${SOURCE_INGEST_CONTROLLER_RESTART_POLICY:-no}"
    assert controller["command"] == ["python", "-m", "services.source_ingestion.controller_worker"]
    assert controller_env["SOURCE_INGEST_API_URL"] == "http://source-ingest:8097"
    assert controller_env["SOURCE_INGEST_CONTROLLER_MODE"] == "${SOURCE_INGEST_CONTROLLER_MODE:-reconcile_only}"
    assert controller_env["SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL"] == "${SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL:-scheduled_tick}"
    assert controller_env["SOURCE_INGEST_CONTROLLER_STATE_PATH"] == "/data/source-ingest/controller_state.json"
    assert controller_env["SOURCE_INGEST_CONTROLLER_TOKEN_FILE"] == "/data/source-ingest/controller_token"
    assert controller_env["SOURCE_INGEST_CONTROLLER_TIMEOUT_SECONDS"] == "${SOURCE_INGEST_CONTROLLER_TIMEOUT_SECONDS:-30}"
    assert controller_env["PANTHEON_TENANT_ID"] == "${PANTHEON_TENANT_ID:-${PANTHEON_BFF_TENANT_ID:-default}}"
    assert "SOURCE_INGEST_CONTROLLER_LEASE_SECONDS" not in controller_env
    assert controller_env["SOURCE_INGEST_CONTROLLER_MAX_TICKS"] == "${SOURCE_INGEST_CONTROLLER_MAX_TICKS:-1}"
    assert controller_env["SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS"] == "${SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS:-}"
    assert controller_env["SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS"] == "${SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS:-}"
    assert controller_env["SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"] == "${SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY:-1}"
    assert controller_env["DATABASE_URL"].startswith("${DATABASE_URL:-postgresql://")
    assert "source-ingest-data:/data/source-ingest" in controller["volumes"]
    assert controller["depends_on"]["source-ingest"]["condition"] == "service_healthy"
    assert controller["depends_on"]["source-ingest-controller-migrate"]["condition"] == "service_completed_successfully"
    assert "controller_healthcheck" in " ".join(controller["healthcheck"]["test"])

    projector = services["source-ingest-agora-projector"]
    projector_env = _env_map(projector)
    assert projector["profiles"] == ["source-ingest-scheduler"]
    assert projector["restart"] == "no"
    assert projector["command"] == ["python", "scripts/project_market_data_to_bff_agora_surfaces.py"]
    assert projector["depends_on"]["source-ingest-scheduler"]["condition"] == "service_completed_successfully"
    assert projector_env["SOURCE_INGEST_URL"] == "http://source-ingest:8097"
    assert projector_env["AGORA_MARKET_STALE_THRESHOLD_SECONDS"] == "${AGORA_MARKET_STALE_THRESHOLD_SECONDS:-86400}"
    assert "bff-data:/data/bff" in projector["volumes"]

    distillation = services["strategy-distillation-worker"]
    distillation_env = _env_map(distillation)
    assert distillation["restart"] == "unless-stopped"
    assert distillation["command"] == [
        "python",
        "-m",
        "services.source_ingestion.distillation_controller",
    ]
    # Bounded source refreshes export SOURCE_INGEST_CONTROLLER_MAX_TICKS=1.
    # The always-on distillation controller must remain independently
    # configurable and default to an unbounded loop.
    assert distillation_env["SOURCE_INGEST_CONTROLLER_MAX_TICKS"] == (
        "${STRATEGY_DISTILLATION_CONTROLLER_MAX_TICKS:-0}"
    )
    assert "source-ingest-data:/data/source-ingest" in distillation["volumes"]
    assert distillation["depends_on"]["source-ingest"]["condition"] == "service_healthy"
    assert distillation["depends_on"]["registry"]["condition"] == "service_healthy"

    smoke_env = _env_map(services["smoke-stack"])
    assert smoke_env["SOURCE_INGEST_URL"] == "http://source-ingest:8097"
    assert "source-ingest" in services["smoke-stack"]["depends_on"]
    assert "source-ingest-data" in compose["volumes"]
    assert _env_map(services["search-svc"])["SEARCH_EVIDENCE_BACKEND"] == "${SEARCH_EVIDENCE_BACKEND:-postgres}"

    source_search_smoke = services["source-search-bounded-smoke"]
    source_search_smoke_env = _env_map(source_search_smoke)
    assert source_search_smoke["profiles"] == ["source-search-bounded"]
    assert "run --rm --use-aliases source-search-bounded-smoke" in compose_path.read_text(encoding="utf-8")
    assert source_search_smoke["command"] == ["python", "scripts/smoke_source_search_bounded.py"]
    assert source_search_smoke_env["SOURCE_INGEST_URL"] == "http://source-ingest:8097"
    assert source_search_smoke_env["SEARCH_URL"] == "http://search-svc:8098"
    assert source_search_smoke_env["SOURCE_INGEST_EXTERNAL_FEED_HOST"] == "source-search-bounded-smoke"
    assert source_search_smoke_env["PYTHONUNBUFFERED"] == "1"
    assert source_search_smoke_env["SOURCE_SEARCH_SMOKE_TIMEOUT_SECONDS"] == (
        "${SOURCE_SEARCH_SMOKE_TIMEOUT_SECONDS:-180}"
    )
    assert source_search_smoke_env["SOURCE_SEARCH_SMOKE_REQUEST_TIMEOUT_SECONDS"] == (
        "${SOURCE_SEARCH_SMOKE_REQUEST_TIMEOUT_SECONDS:-15}"
    )
    assert source_search_smoke["depends_on"]["source-ingest"]["condition"] == "service_healthy"
    assert source_search_smoke["depends_on"]["search-svc"]["condition"] == "service_healthy"

    bff_env = _env_map(services["operator-bff"])
    assert bff_env["PANTHEON_SOURCE_INGEST_API_URL"] == "http://source-ingest:8097"
    assert "source-ingest" in services["operator-bff"]["depends_on"]

    smoke = (compose_path.parent / "scripts/smoke_honest_stack.py").read_text(encoding="utf-8")
    assert 'SOURCE_INGEST_EXTERNAL_FEED_HOST = os.getenv("SOURCE_INGEST_EXTERNAL_FEED_HOST", "smoke-stack")' in smoke
    assert 'source_search_token = f"source-smoke-{suffix}"' in smoke
    assert '"mode": "external_feed"' in smoke
    assert '"allowed_url_prefixes": [source_feed_url.rsplit("/", 1)[0] + "/"]' in smoke
    assert '"max_records": 10' in smoke
    assert '"keywords": ["compose", "smoke", "momentum", "volatility", source_search_token]' in smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/connectors"' in smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/jobs"' in smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/run-scheduled"' in smoke
    assert 'f"{SEARCH_URL}/api/search/query"' in smoke

    bounded_smoke = (compose_path.parent / "scripts/smoke_source_search_bounded.py").read_text(encoding="utf-8")
    assert "suffix = uuid.uuid4().hex" in bounded_smoke
    assert "connector_ids = _run_scoped_connector_ids(suffix)" in bounded_smoke
    assert '"SOURCE_SEARCH_SMOKE_TIMEOUT_SECONDS"' in bounded_smoke
    assert '"SOURCE_SEARCH_SMOKE_REQUEST_TIMEOUT_SECONDS"' in bounded_smoke
    assert "last_successful_checkpoint=" in bounded_smoke
    assert '"connector_id": "conn-bounded-' not in bounded_smoke
    assert '"mode": "static_records"' in bounded_smoke
    assert '"mode": "external_feed"' in bounded_smoke
    assert '"allowed_url_prefixes": [feed_url.rsplit("/", 1)[0] + "/"]' in bounded_smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/dlq/replay"' in bounded_smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/run-scheduled"' in bounded_smoke
    assert '"exclusive_connector_ids": [connector_ids["scheduled"]]' in bounded_smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/audit"' in bounded_smoke
    assert 'f"{SEARCH_URL}/api/search/index/refresh"' in bounded_smoke
    assert 'f"{SEARCH_URL}/api/search/index/source-completions/{feed_run_id}"' in bounded_smoke

    prod_env = (compose_path.parent / "env/prod-control.env.example").read_text(encoding="utf-8")
    assert "PANTHEON_SOURCE_SEARCH_POSTURE=production" in prod_env
    assert "SOURCE_INGEST_EVIDENCE_BACKEND=postgres" in prod_env
    assert "PANTHEON_S3_ENDPOINT=http://minio:9000" in prod_env

    prod_smoke = (compose_path.parent / "scripts/smoke_source_search_prod_posture.py").read_text(encoding="utf-8")
    assert "posture_alert_count" in prod_smoke
    assert '"SOURCE_INGEST_EVIDENCE_BACKEND": "postgres"' in prod_smoke
