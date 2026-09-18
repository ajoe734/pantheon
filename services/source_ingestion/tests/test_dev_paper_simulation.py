"""Regression coverage for the dev-only synthetic market-data connector.

DEV-PAPER-MARKET-INPUT-STALENESS-001: this connector replaces a one-off
operator-registered connector whose event_time values were frozen absolute
timestamps and could never pass freshness admission again. These tests pin
the two properties that fix that: (1) records are honestly labeled as
simulation, never real market evidence, and (2) event_time is always
computed relative to "now" at call time, so two calls separated by real time
produce different, non-frozen event_time values.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.source_ingestion.connector_definitions import get_connector_definition
from services.source_ingestion.connectors.dev_paper_simulation import (
    DEV_PAPER_SIMULATION_CONNECTOR_ID,
    DevPaperUsEquitySimulationAdapter,
    is_dev_environment,
)
from services.source_ingestion.provider_adapters import provider_adapter_tokens


def test_records_are_honestly_labeled_simulation_not_real_market_evidence() -> None:
    adapter = DevPaperUsEquitySimulationAdapter(
        clock=lambda: datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    )
    records = adapter.records_from_now(trace_id="trace-1")

    assert records
    connector = adapter.connector()
    assert connector.provider == "Explicit controlled simulation"
    assert connector.metadata["is_real"] is False
    assert connector.metadata["provenance"] == "simulation"
    for record in records:
        assert record.metadata["is_real"] is False
        assert record.metadata["provenance"] == "simulation"
        normalized_row = record.metadata["normalized_row"]
        assert normalized_row["is_real"] is False
        assert normalized_row["provenance"] == "simulation"
        assert normalized_row["symbol"]
        assert normalized_row["close"] > 0


def test_event_time_is_computed_relative_to_now_not_frozen() -> None:
    """Two calls with different injected clocks must not replay the same
    event_time — the defect this connector fixes in the frozen fixture."""

    early_clock = lambda: datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    later_clock = lambda: datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

    early_records = DevPaperUsEquitySimulationAdapter(clock=early_clock).records_from_now()
    later_records = DevPaperUsEquitySimulationAdapter(clock=later_clock).records_from_now()

    early_event_time = early_records[0].metadata["event_time"]
    later_event_time = later_records[0].metadata["event_time"]
    assert early_event_time != later_event_time
    assert later_event_time > early_event_time


def test_event_time_stays_within_freshness_admission_window_when_called_now() -> None:
    """A record generated "now" must always be young enough to pass the flat
    admission rule (age_seconds > max_age_seconds == 86400s), which is the
    whole point of this connector replacing the frozen fixture."""

    adapter = DevPaperUsEquitySimulationAdapter()
    records = adapter.records_from_now()

    event_time = datetime.fromisoformat(
        records[0].metadata["event_time"].replace("Z", "+00:00")
    )
    age_seconds = (datetime.now(timezone.utc) - event_time).total_seconds()
    assert 0 <= age_seconds < 86400


def test_connector_definition_registered_and_enabled_for_us_price_daily() -> None:
    definition = get_connector_definition("dev-paper-us-equity-simulation")
    assert definition is not None
    assert definition.definition_state.value == "supported"
    assert "us_price_daily" in definition.datasets
    assert definition.metadata.get("is_real") is False
    assert definition.metadata.get("provenance") == "simulation"
    assert definition.adapter_token in provider_adapter_tokens()


def test_is_dev_environment_gate() -> None:
    assert is_dev_environment({"PANTHEON_ENV": "dev"}) is True
    assert is_dev_environment({"PANTHEON_ENV": "Dev"}) is True
    assert is_dev_environment({"PANTHEON_ENV": "staging"}) is False
    assert is_dev_environment({"PANTHEON_ENV": "production"}) is False
    assert is_dev_environment({}) is False


def test_connector_id_matches_module_constant() -> None:
    assert DEV_PAPER_SIMULATION_CONNECTOR_ID == "dev-paper-us-equity-simulation"
