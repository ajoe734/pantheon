"""Focused durable-source substrate checks for LOOP-PROD-TEL-002."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_db_migration_and_bootstrap_install_the_same_ingestion_cursor() -> None:
    for relative_path in ("scripts/db_migrate.sh", "scripts/bootstrap.sh"):
        ddl = (ROOT / relative_path).read_text(encoding="utf-8")

        assert "CREATE SEQUENCE IF NOT EXISTS telemetry_events_ingested_seq_seq AS BIGINT" in ddl
        assert re.search(
            r"ingested_seq\s+BIGINT\s+NOT NULL\s+DEFAULT\s+nextval",
            ddl,
            re.IGNORECASE,
        )
        assert re.search(
            r"ingested_at\s+TIMESTAMPTZ\s+NOT NULL\s+DEFAULT\s+clock_timestamp\(\)",
            ddl,
            re.IGNORECASE,
        )
        assert "ADD COLUMN IF NOT EXISTS ingested_seq BIGINT" in ddl
        assert "ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ" in ddl
        assert "WHERE ingested_seq IS NULL" in ddl
        assert "WHERE ingested_at IS NULL" in ddl
        assert "OWNED BY telemetry_events.ingested_seq" in ddl
        assert "idx_telemetry_events_ingested_seq" in ddl
        assert "idx_telemetry_events_ingested_at" in ddl
        assert "idx_telemetry_events_event_type_ingested_seq" in ddl


def test_ingestion_cursor_is_unique_and_commit_timestamp_is_queryable() -> None:
    migration = (ROOT / "scripts/db_migrate.sh").read_text(encoding="utf-8")

    assert re.search(
        r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+idx_telemetry_events_ingested_seq",
        migration,
        re.IGNORECASE,
    )
    assert re.search(
        r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+idx_telemetry_events_ingested_at",
        migration,
        re.IGNORECASE,
    )
    assert re.search(
        r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+idx_telemetry_events_event_type_ingested_seq"
        r"\s+ON\s+telemetry_events\s*\(event_type,\s*ingested_seq\)",
        migration,
        re.IGNORECASE,
    )


def test_compose_gives_telemetry_cold_start_budget_for_large_dlq_spill() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    telemetry = compose["services"]["telemetry"]

    assert (
        telemetry["environment"]["TELEMETRY_STARTUP_TIMEOUT_SECONDS"]
        == "${TELEMETRY_STARTUP_TIMEOUT_SECONDS:-240}"
    )
    healthcheck = telemetry["healthcheck"]
    assert "timeout=20" in healthcheck["test"][-1]
    assert healthcheck["interval"] == "15s"
    assert healthcheck["timeout"] == "25s"
    assert healthcheck["retries"] == 20
    assert healthcheck["start_period"] == "300s"
