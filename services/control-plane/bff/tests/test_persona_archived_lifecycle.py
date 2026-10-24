"""BFF-PERSONA-ARCHIVED-LIFECYCLE-20261024: an archived Persona owner is terminal.

The Persona owner keeps the governance ``lifecycle_state`` and the
administrative ``status`` (active/suspended/archived) apart, and archiving
leaves ``lifecycle_state`` untouched. These tests drive the owner functions in
``personas/service.py`` against the canonical PM12-eligible fixture with the
owner ``status`` flipped to ``archived`` and prove the Persona is projected as
the single terminal state (``retired``) that the existing fleet, league and
ranking terminal fast paths recognise.
"""
from __future__ import annotations

import contextvars
from typing import Any, Dict, List

import pytest

from services.control_plane.bff import test_bff_promotion_review_governance as gov_test
from services.control_plane.bff.personas import service as persona_service_module
from services.control_plane.bff.tests.conftest import make_composed_persona_service

_TENANT_ID = gov_test._PM12_ELIGIBLE_TENANT_ID
_ARCHIVED_ID = "persona-us-equity"
_ACTIVE_ID = "persona-crypto-perp"
_SNAPSHOT_AT = "2026-01-15T00:00:00Z"
_ARCHIVED_RUNTIME_ID = "runtime-us-equity-paper"
_ACTIVE_RUNTIME_ID = "runtime-crypto-paper"

# BFF lifecycle projection before this task; every value must keep its mapping.
_PRE_EXISTING_LIFECYCLE_MAP = {
    "draft": "draft",
    "candidate": "review",
    "review": "review",
    "approved": "approved",
    "active": "deployed",
    "deployed": "deployed",
    "paused": "paused",
    "retired": "retired",
    "paper": "paper_running",
    "paper_running": "paper_running",
    "canary": "canary_running",
    "canary_running": "canary_running",
    "canary_authorized_not_started": "canary_authorized_not_started",
    "live": "live_running",
    "live_running": "live_running",
    "needs_human_approval": "needs_human_approval",
    "rollback_required": "rollback_required",
    "stopped": "stopped",
    "failed": "failed",
    "provisioning": "provisioning",
    "provisioning_failed": "failed",
}


class _FleetReadPorts(gov_test.PromotionReviewTestReadPorts):
    """Canonical PM12 fixture plus the Fleet-only collections it lacks."""

    def __init__(self) -> None:
        super().__init__(allow_fallback=True)
        self.telemetry_summary_reads: List[str] = []

    def list_evolution_decisions(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def list_telemetry_summaries(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        # Force the Fleet onto its per-runtime telemetry lookup so the test
        # can observe which runtimes it reads.
        raise RuntimeError("bulk telemetry read unavailable")

    def get_telemetry_summary(self, runtime_id: str) -> Any:
        self.telemetry_summary_reads.append(str(runtime_id))
        return super().get_telemetry_summary(runtime_id)


def _archived_ports() -> _FleetReadPorts:
    ports = _FleetReadPorts()
    ports._data["personas"][_ARCHIVED_ID]["status"] = "archived"
    return ports


def _run_with_persona_service(ports: Any, fn):
    def _run():
        persona_service_module._current_persona_service.set(
            make_composed_persona_service(read_store=ports)
        )
        return fn()

    return contextvars.copy_context().run(_run)


def _league_rows() -> List[Dict[str, Any]]:
    return persona_service_module._pm12_persona_league_rows(tenant_id=_TENANT_ID)


def _fleet_items() -> List[Dict[str, Any]]:
    payload = persona_service_module._persona_fleet_slim_list_payload(
        snapshot_at=_SNAPSHOT_AT,
        tenant_id=_TENANT_ID,
        state=None,
        health=None,
        deployment_stage=None,
        market_scope=None,
        q=None,
        page_token=None,
        page_size=50,
    )
    return payload["data"]["items"]


def test_existing_lifecycle_values_keep_their_mapping() -> None:
    lifecycle_map = persona_service_module._STRATEGY_BFF_LIFECYCLE_MAP
    for owner_value, projected in _PRE_EXISTING_LIFECYCLE_MAP.items():
        assert lifecycle_map[owner_value] == projected, owner_value
    # BFF-LIFECYCLE-UNMAPPED-VALUES-20261024 maps the remaining owner values;
    # test_persona_lifecycle_owner_vocabulary.py guards them.
    assert set(lifecycle_map) - set(_PRE_EXISTING_LIFECYCLE_MAP) == {
        "archived",
        "rejected",
        "superseded",
        "research_only",
        "consultable",
        "paper_owner",
        "live_owner",
        "frozen",
        "suspended",
    }


def test_archived_lifecycle_value_normalizes_to_the_terminal_state() -> None:
    assert persona_service_module._normalize_lifecycle_state("archived") == "retired"
    assert persona_service_module._normalize_lifecycle_state(" Archived ") == "retired"


def test_archived_owner_status_outranks_the_governance_lifecycle_state() -> None:
    project = persona_service_module._persona_record_projected_state
    assert project({"lifecycle_state": "paper_running", "status": "archived"}) == "retired"
    assert project({"lifecycle_state": "paper_running", "status": "active"}) == "paper_running"
    # A suspended owner is non-running (BFF-LIFECYCLE-UNMAPPED-VALUES-20261024).
    assert project({"lifecycle_state": "paper_running", "status": "suspended"}) == "suspended"


def test_league_projects_archived_persona_as_terminal_and_skips_enrichment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = _archived_ports()
    route_policy_reads: List[str] = []
    original_route_policy = ports.get_route_policy_for_persona
    monkeypatch.setattr(
        ports,
        "get_route_policy_for_persona",
        lambda pid: route_policy_reads.append(str(pid)) or original_route_policy(pid),
    )
    enrich_calls: Dict[str, Dict[str, Any]] = {}
    original_enrich = persona_service_module._enrich_persona_item_with_bindings

    def _spy_enrich(item: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        enrich_calls[str(item.get("persona_id") or item.get("id"))] = kwargs
        return original_enrich(item, **kwargs)

    monkeypatch.setattr(persona_service_module, "_enrich_persona_item_with_bindings", _spy_enrich)

    rows = {row["persona_id"]: row for row in _run_with_persona_service(ports, _league_rows)}

    assert rows[_ARCHIVED_ID]["state"] == "retired"
    assert rows[_ARCHIVED_ID]["stage"] == "retired"
    assert rows[_ACTIVE_ID]["state"] == "paper_running"
    # The retired fast path hands enrichment no downstream records.
    archived_enrich = enrich_calls[_ARCHIVED_ID]
    assert archived_enrich["bindings"] == []
    assert archived_enrich["runtimes"] == []
    assert archived_enrich["league_entry"] == {}
    assert archived_enrich["persona_sessions"] == []
    assert enrich_calls[_ACTIVE_ID]["runtimes"]
    assert _ARCHIVED_ID not in route_policy_reads
    assert _ACTIVE_ID in route_policy_reads


def test_archived_persona_is_not_ranked_eligible() -> None:
    ports = _archived_ports()

    def _rank():
        rows = _league_rows()
        quarter_window = persona_service_module._pm12_quarter_window("2026-Q1", _SNAPSHOT_AT)
        return persona_service_module._pm12_quarterly_ranking_items(rows, quarter_window=quarter_window)

    items = {item["persona_id"]: item for item in _run_with_persona_service(ports, _rank)}

    assert items[_ARCHIVED_ID]["eligible"] is False
    assert "Inactive lifecycle state: retired" in items[_ARCHIVED_ID]["exclusion_reasons"]
    assert items[_ACTIVE_ID]["eligible"] is True


def test_fleet_projects_archived_persona_as_terminal_without_incident_or_telemetry_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = _archived_ports()
    incident_lookups: List[str] = []
    original_incidents = persona_service_module._persona_fleet_active_incidents_for_row

    def _spy_incidents(**kwargs: Any) -> List[Dict[str, Any]]:
        incident_lookups.append(str(kwargs.get("persona_id")))
        return original_incidents(**kwargs)

    monkeypatch.setattr(persona_service_module, "_persona_fleet_active_incidents_for_row", _spy_incidents)

    items = {item["persona_id"]: item for item in _run_with_persona_service(ports, _fleet_items)}

    assert items[_ARCHIVED_ID]["state"] == "retired"
    assert items[_ACTIVE_ID]["state"] == "paper_running"
    assert incident_lookups == [_ACTIVE_ID]
    assert _ARCHIVED_RUNTIME_ID not in ports.telemetry_summary_reads
    assert _ACTIVE_RUNTIME_ID in ports.telemetry_summary_reads


def test_fleet_primary_runtime_prefers_paused_runtime_over_retired_runtime() -> None:
    ports = _FleetReadPorts()
    runtimes = ports._data["runtime_bindings"]
    retired_runtime_id = _ACTIVE_RUNTIME_ID
    paused_runtime_id = f"{_ACTIVE_RUNTIME_ID}-paused"
    runtimes[retired_runtime_id]["status"] = "retired"
    runtimes[paused_runtime_id] = {
        **runtimes[retired_runtime_id],
        "id": paused_runtime_id,
        "runtime_id": paused_runtime_id,
        "status": "paused",
    }

    items = {item["persona_id"]: item for item in _run_with_persona_service(ports, _fleet_items)}

    assert items[_ACTIVE_ID]["runtime_id"] == paused_runtime_id
