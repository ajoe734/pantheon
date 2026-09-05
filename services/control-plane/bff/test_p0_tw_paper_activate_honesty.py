"""Regression tests for P0-TW-PAPER-ACTIVATE-001 console honesty."""

from services.control_plane.bff.main import _build_persona_health_items, _trading_performance_delta


def test_trading_performance_delta_is_unavailable_without_return_schema():
    assert _trading_performance_delta() is None


def test_build_persona_health_items_binds_telemetry_and_seed_flags():
    items = _build_persona_health_items("2026-07-26T00:00:00Z", include_market_persona_defaults=True)
    assert isinstance(items, list)
    tw = next((item for item in items if item.get("persona_id") == "persona-tw-equity"), None)
    assert tw is not None
    assert tw.get("has_trading_telemetry") is False
    assert tw.get("seed_row") is True
    assert tw.get("perf_delta") is None
