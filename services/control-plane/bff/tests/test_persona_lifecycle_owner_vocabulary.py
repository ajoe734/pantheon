"""BFF-LIFECYCLE-UNMAPPED-VALUES-20261024: every owner lifecycle value is mapped.

``_normalize_lifecycle_state`` projects owner lifecycle values through the one
``_STRATEGY_BFF_LIFECYCLE_MAP`` and still falls back to ``draft`` for a value it
does not know. The guard below enumerates each owner vocabulary from its source
(``PersonaLifecycleState``, the Persona registry schema ``status`` enum and
``StrategyLifecycleState``) so a new owner value fails here instead of silently
projecting as ``draft``.

The projection tests drive the owner functions in ``personas/service.py``
against the canonical PM12-eligible fixture and prove a frozen or suspended
Persona with an active runtime and binding is never shown as running in the
fleet, league or persona health projections.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from services.control_plane.bff.personas import service as persona_service_module
from services.control_plane.bff.tests.test_persona_archived_lifecycle import (
    _FleetReadPorts,
    _fleet_items,
    _league_rows,
    _run_with_persona_service,
)
from services.control_plane.persona import persona_registry
from services.control_plane.persona.persona_registry import PersonaLifecycleState
from services.research.strategy_spec.models import StrategyLifecycleState

_PERSONA_ID = "persona-crypto-perp"
_RUNNING_ID = "persona-us-equity"
_SNAPSHOT_AT = "2026-01-15T00:00:00Z"
_RUNNING_STAGES = {"paper_running", "canary_running", "live_running"}


def _persona_admin_statuses() -> List[str]:
    schema_path = Path(persona_registry.__file__).with_name("persona_registry.schema.json")
    schema = json.loads(schema_path.read_text())
    return list(schema["properties"]["status"]["enum"])


_OWNER_VOCABULARIES = {
    "PersonaLifecycleState": [state.value for state in PersonaLifecycleState],
    "persona_registry.schema.json status": _persona_admin_statuses(),
    "StrategyLifecycleState": [state.value for state in StrategyLifecycleState],
}


@pytest.mark.parametrize("vocabulary", sorted(_OWNER_VOCABULARIES))
def test_every_owner_lifecycle_value_is_explicitly_mapped(vocabulary: str) -> None:
    values = _OWNER_VOCABULARIES[vocabulary]
    assert values, vocabulary
    lifecycle_map = persona_service_module._STRATEGY_BFF_LIFECYCLE_MAP
    unmapped = [value for value in values if value not in lifecycle_map]
    assert unmapped == [], f"{vocabulary} values fall back to 'draft': {unmapped}"


def test_owner_lifecycle_values_project_as_decided() -> None:
    normalize = persona_service_module._normalize_lifecycle_state
    assert normalize("frozen") == "frozen"
    assert normalize("suspended") == "suspended"
    assert normalize("rejected") == "retired"
    assert normalize("superseded") == "retired"
    for stage in ("research_only", "consultable", "paper_owner", "live_owner"):
        assert normalize(stage) == stage
    for value in ("frozen", "suspended", "rejected", "superseded"):
        assert normalize(value) in persona_service_module._PERSONA_NON_RUNNING_PROJECTED_STATES
    # The unknown-value default is unchanged.
    assert normalize("not-an-owner-value") == "draft"


def test_persona_record_projection_honours_frozen_and_suspended() -> None:
    project = persona_service_module._persona_record_projected_state
    assert project({"lifecycle_state": "frozen", "status": "active"}) == "frozen"
    assert project({"lifecycle_state": "paper_running", "status": "suspended"}) == "suspended"
    assert project({"lifecycle_state": "frozen", "status": "suspended"}) == "suspended"
    # Retired and archived stay terminal whatever the other field says.
    assert project({"lifecycle_state": "retired", "status": "suspended"}) == "retired"
    assert project({"lifecycle_state": "frozen", "status": "archived"}) == "retired"


class _HealthReadPorts(_FleetReadPorts):
    """The persona health projection reads telemetry in bulk only."""

    def list_telemetry_summaries(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return []


def _ports_with_owner(ports: _FleetReadPorts, **owner_fields: str) -> _FleetReadPorts:
    ports._data["personas"][_PERSONA_ID].update(owner_fields)
    return ports


def _health_items() -> List[Dict[str, Any]]:
    return persona_service_module._build_persona_health_items_impl(_SNAPSHOT_AT)


_NON_RUNNING_OWNERS = [
    pytest.param({"lifecycle_state": "frozen", "status": "active"}, "frozen", id="frozen"),
    pytest.param({"status": "suspended"}, "suspended", id="suspended"),
]


@pytest.mark.parametrize(("owner_fields", "expected"), _NON_RUNNING_OWNERS)
def test_league_projects_non_running_persona_with_active_runtime(
    owner_fields: Dict[str, str], expected: str
) -> None:
    ports = _ports_with_owner(_FleetReadPorts(), **owner_fields)
    assert ports._data["runtime_bindings"]["runtime-crypto-paper"]["status"] == "running"

    rows = {row["persona_id"]: row for row in _run_with_persona_service(ports, _league_rows)}

    assert rows[_PERSONA_ID]["state"] == expected
    assert rows[_PERSONA_ID]["stage"] == expected
    assert rows[_PERSONA_ID]["health_summary"]["health"] == "degraded"
    assert rows[_RUNNING_ID]["stage"] == "paper_running"


@pytest.mark.parametrize(("owner_fields", "expected"), _NON_RUNNING_OWNERS)
def test_fleet_projects_non_running_persona_with_active_runtime(
    owner_fields: Dict[str, str], expected: str
) -> None:
    ports = _ports_with_owner(_FleetReadPorts(), **owner_fields)

    items = {item["persona_id"]: item for item in _run_with_persona_service(ports, _fleet_items)}

    assert items[_PERSONA_ID]["state"] == expected
    assert items[_PERSONA_ID]["state"] not in _RUNNING_STAGES
    assert items[_RUNNING_ID]["state"] == "paper_running"


@pytest.mark.parametrize(("owner_fields", "expected"), _NON_RUNNING_OWNERS)
def test_persona_health_projects_non_running_persona_with_active_runtime(
    owner_fields: Dict[str, str], expected: str
) -> None:
    ports = _ports_with_owner(_HealthReadPorts(), **owner_fields)

    items = {item["persona_id"]: item for item in _run_with_persona_service(ports, _health_items)}

    assert items[_PERSONA_ID]["state"] == expected
    assert items[_RUNNING_ID]["state"] not in {"frozen", "suspended", "retired"}
