"""Regression tests for P0-TW-PAPER-ACTIVATE-001 console honesty.

Migrated by BFF-LOOPS-PAPER-V5-PROJECTION-SEAM-CORRECTIVE-001: the execution
persona-health builder is now solely owned by ``PersonaService`` (see
``BFF-LOOPS-PAPER-V5-PROJECTION-OWNERSHIP-DECISION-001``); ``main.py`` no
longer defines a standalone ``_build_persona_health_items``. This exercises
the real instance builder against an explicit empty read fixture instead of
main's module-global read_store.
"""
from __future__ import annotations

from typing import Any, Dict, List

from services.control_plane.bff.personas.service import (
    PersonaService,
    _trading_performance_delta,
    create_persona_registry_write_owner,
)


class _EmptyReadStore:
    def list_persona_league(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def list_incidents(self) -> List[Dict[str, Any]]:
        return []

    def list_evolution_decisions(self) -> List[Dict[str, Any]]:
        return []

    def list_telemetry_summaries(self) -> List[Dict[str, Any]]:
        return []

    def list_personas(self, *, include_market_persona_defaults: bool = False, **kwargs: Any) -> List[Dict[str, Any]]:
        if not include_market_persona_defaults:
            return []
        return [
            {
                "persona_id": "persona-tw-equity",
                "id": "persona-tw-equity",
                "name": "TW Equity",
                "lifecycle_state": "active",
                "metadata": {"is_market_persona_default": True},
            }
        ]

    def get_bindings_for_persona(self, persona_id: str) -> List[Dict[str, Any]]:
        return []

    def list_bindings(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def list_runtime_bindings(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def get_source_connector_registry(self) -> Dict[str, Any]:
        return {"connectors": []}

    def get_source_health_usage_snapshot(self) -> Dict[str, Any]:
        return {"sources": []}

    def list_strategy_specs(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return []


def _service() -> PersonaService:
    return PersonaService(
        write_owner=create_persona_registry_write_owner(),
        ranking_write_owner=object(),
        read_store=_EmptyReadStore(),
        command_store=object(),
    )


def test_trading_performance_delta_is_unavailable_without_return_schema():
    assert _trading_performance_delta() is None


def test_build_persona_health_items_binds_telemetry_and_seed_flags():
    items = _service().build_persona_health_items(
        "2026-07-26T00:00:00Z", include_market_persona_defaults=True
    )
    assert isinstance(items, list)
    tw = next((item for item in items if item.get("persona_id") == "persona-tw-equity"), None)
    assert tw is not None
    assert tw.get("has_trading_telemetry") is False
    assert tw.get("seed_row") is True
    assert tw.get("perf_delta") is None
