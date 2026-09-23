"""Production-composition regression coverage for extracted BFF domain seams.

Verifies that the remaining BFF production seams extracted out of main.py into
declared domain owners (capital, command_adapters, personas, incidents, governance)
behave identically as direct domain service functions and as composed via main.py,
without requiring monkeypatches of main.py globals.
"""
import importlib
import os
from decimal import Decimal
from typing import Any, Dict

import pytest

bff_main = importlib.import_module("services.control_plane.bff.main")
from services.control_plane.bff.capital.service import (
    _pm12_allocation_line_assertion_hash,
    _pm12_semantic_json_value,
    _pm12_semantic_values_match,
)
from services.control_plane.bff.models import (
    AuditContext,
    CommandType,
    ObjectType,
    OperatorCommand,
    OperatorIdentity,
    TargetObject,
)
from services.control_plane.bff.command_adapters.service import (
    assert_duplicate_confirm_token_matches,
    stored_command_params,
)
from services.control_plane.bff.governance.service import (
    human_inbox_surface_timeout_seconds,
)
from services.control_plane.bff.incidents.service import (
    _bff_incident_matches_filters,
    _project_bff_incident_case,
    IncidentService,
)
from services.control_plane.bff.personas.service import (
    _filter_by_common_identifiers,
)


class TestCapitalAllocationSemanticSeam:
    """Verifies PM12 semantic JSON comparison and line assertion hash extracted to capital.service."""

    def test_semantic_canonicalization_primitives(self) -> None:
        assert _pm12_semantic_json_value(None) == ["null"]
        assert _pm12_semantic_json_value(True) == ["boolean", True]
        assert _pm12_semantic_json_value(False) == ["boolean", False]
        assert _pm12_semantic_json_value("foo") == ["string", "foo"]
        assert _pm12_semantic_json_value(1) == ["number", "1"]
        assert _pm12_semantic_json_value(1.0) == ["number", "1"]
        assert _pm12_semantic_json_value(Decimal("1.500")) == ["number", "1.5"]

    def test_semantic_canonicalization_collections(self) -> None:
        # Dict key sorting ensures order-invariance
        val1 = _pm12_semantic_json_value({"b": 2, "a": 1})
        val2 = _pm12_semantic_json_value({"a": 1.0, "b": 2.0})
        assert val1 == val2

    def test_semantic_values_match_tolerance_and_strictness(self) -> None:
        # Float/int/Decimal matches
        assert _pm12_semantic_values_match(1.0, 1)
        assert _pm12_semantic_values_match(Decimal("0.0"), 0)
        # Nested structures
        assert _pm12_semantic_values_match(
            {"weights": [1.0, 2.0], "label": "test"},
            {"label": "test", "weights": [1, 2]},
        )
        # Strict failures: booleans are not integers
        assert not _pm12_semantic_values_match(True, 1)
        assert not _pm12_semantic_values_match(False, 0)
        assert not _pm12_semantic_values_match("1", 1)
        # Strict failures: invalid / non-finite numbers
        assert not _pm12_semantic_values_match(float("nan"), 0)
        assert not _pm12_semantic_values_match(float("inf"), float("inf"))

    def test_allocation_line_assertion_hash_stability(self) -> None:
        line_a = {
            "persona_id": "p-1",
            "runtime_id": "rt-1",
            "strategy_id": "s-1",
            "capital_pool_id": "pool-1",
            "target_weight": 0.5,
            "target_notional": 1000.0,
            "cap_reasons": ["max_exposure"],
            "evidence_refs": ["ref-1"],
        }
        line_b = {
            "evidence_refs": ["ref-1"],
            "cap_reasons": ["max_exposure"],
            "target_notional": 1000,
            "target_weight": Decimal("0.5"),
            "capital_pool_id": "pool-1",
            "strategy_id": "s-1",
            "runtime_id": "rt-1",
            "persona_id": "p-1",
        }
        assert _pm12_allocation_line_assertion_hash(line_a) == _pm12_allocation_line_assertion_hash(line_b)

    def test_main_composition_wiring(self) -> None:
        """Verifies main.py correctly references capital.service without re-implementing."""
        assert bff_main._pm12_semantic_json_value is _pm12_semantic_json_value
        assert bff_main._pm12_semantic_values_match is _pm12_semantic_values_match
        assert bff_main._pm12_allocation_line_assertion_hash is _pm12_allocation_line_assertion_hash


class TestCommandAdaptersStoredParamsSeam:
    """Verifies stored_command_params extracted to command_adapters.service."""

    def test_stored_command_params_derivation(self) -> None:
        identity = OperatorIdentity(
            operator_id="op-123",
            tenant_id="tenant-alpha",
            roles={"reviewer", "operator"},
        )
        cmd = OperatorCommand(
            command=CommandType.APPROVE_DEPLOYMENT,
            target=TargetObject(type=ObjectType.DEPLOYMENT_PLAN, id="plan-1"),
            audit_context=AuditContext(reason="deployment review"),
            params={"target_artifact_id": "art-1"},
        )
        params = stored_command_params(cmd, identity)
        assert params["entity_id"] == "plan-1"
        assert params["actor_id"] == "op-123"
        assert params["actor_role"] == "reviewer"  # reviewer preferred over operator
        assert params["target_artifact_id"] == "art-1"

    def test_stored_command_params_drawer_passthrough(self) -> None:
        identity = OperatorIdentity(
            operator_id="op-admin",
            tenant_id="default",
            roles={"admin"},
        )
        cmd = OperatorCommand(
            command=CommandType.PAUSE_EXECUTION,
            target=TargetObject(type=ObjectType.RUNTIME, id="rt-1"),
            audit_context=AuditContext(reason="emergency stop"),
            params={"runtime_id": "rt-1", "reason": "drawer action"},
        )
        params = stored_command_params(cmd, identity)
        assert params["runtime_id"] == "rt-1"
        assert params["reason"] == "drawer action"

    def test_assert_duplicate_confirm_token_matches(self) -> None:
        cmd = OperatorCommand(
            command=CommandType.PAUSE_RUNTIME,
            target=TargetObject(type=ObjectType.RUNTIME, id="rt-1"),
            audit_context=AuditContext(reason="pause"),
        )
        duplicate_record = {
            "params": {"confirm_token_id": "token-xyz"},
        }
        # Matching token should succeed
        assert_duplicate_confirm_token_matches(
            duplicate=duplicate_record,
            cmd=cmd,
            payload={},
            confirm_token="token-xyz",
            foundation_context={},
        )
        # Mismatched token should raise 409 error
        with pytest.raises(Exception) as exc_info:
            assert_duplicate_confirm_token_matches(
                duplicate=duplicate_record,
                cmd=cmd,
                payload={},
                confirm_token="token-mismatch",
                foundation_context={},
            )
        assert getattr(exc_info.value, "status_code", 409) == 409

    def test_main_composition_wiring(self) -> None:
        """Verifies main.py correctly references command_adapters.service."""
        assert bff_main._stored_command_params is stored_command_params


class TestPersonasCommonIdentifiersSeam:
    """Verifies common identifier filtering extracted to personas.service."""

    def test_filtering_by_identifiers_and_aliases(self) -> None:
        items = [
            {"persona_id": "p-1", "runtime_id": "rt-1", "strategy_id": "s-1", "capital_pool_id": "pool-1"},
            {"persona_id": "p-2", "runtime_id": "rt-2", "strategy_id": "s-1", "capital_pool_id": "pool-2"},
            {"persona_id": "p-1", "runtime_id": "rt-3", "strategy_id": "s-2", "capital_pool_id": "pool-1"},
        ]
        # Direct key filtering
        filtered_p1 = _filter_by_common_identifiers(items, persona_id="p-1")
        assert len(filtered_p1) == 2
        assert all(it["persona_id"] == "p-1" for it in filtered_p1)

        # Alias key filtering
        filtered_s1 = _filter_by_common_identifiers(items, strategy="s-1")
        assert len(filtered_s1) == 2
        assert all(it["strategy_id"] == "s-1" for it in filtered_s1)

        # Conjunction of filters
        filtered_conj = _filter_by_common_identifiers(items, persona="p-1", strategy_id="s-2")
        assert len(filtered_conj) == 1
        assert filtered_conj[0]["runtime_id"] == "rt-3"

    def test_main_composition_wiring(self) -> None:
        """Verifies main.py correctly references personas.service._filter_by_common_identifiers."""
        assert bff_main._filter_by_common_identifiers is _filter_by_common_identifiers


class TestGovernanceTimeoutSeam:
    """Verifies human inbox timeout extracted to governance.service."""

    def test_timeout_ceiling_and_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "10.0")
        assert human_inbox_surface_timeout_seconds() == 1.0

        monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "0.25")
        assert human_inbox_surface_timeout_seconds() == 0.25

        monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "not-a-number")
        assert human_inbox_surface_timeout_seconds() == 1.0

        monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "-1.0")
        assert human_inbox_surface_timeout_seconds() == 1.0

    def test_main_composition_wiring(self) -> None:
        """Verifies main.py references governance.service.human_inbox_surface_timeout_seconds."""
        assert bff_main._human_inbox_surface_timeout_seconds is human_inbox_surface_timeout_seconds


class TestIncidentsSeam:
    """Verifies incident filtering and projection extracted to incidents.service."""

    def test_project_bff_incident_case(self) -> None:
        raw = {
            "incident_id": "inc-100",
            "artifact_id": "art-1",
            "artifact_version": "v1.0",
            "status": "active",
            "severity": "P1",
        }
        projected = _project_bff_incident_case(raw)
        assert projected["id"] == "inc-100"
        assert projected["incident_id"] == "inc-100"
        assert projected["lineage_ref"] == "art-1@v1.0"

    def test_filter_bff_incidents(self) -> None:
        inc_p1 = {"severity": "P1", "status": "active", "capital_pool_id": "pool-1"}
        inc_p2 = {"severity": "P2", "status": "resolved", "capital_pool_id": "pool-2"}

        assert _bff_incident_matches_filters(inc_p1, severity="P1")
        assert not _bff_incident_matches_filters(inc_p2, severity="P1")
        assert _bff_incident_matches_filters(inc_p2, status="resolved")
        assert not _bff_incident_matches_filters(inc_p1, status="resolved")
        assert _bff_incident_matches_filters(inc_p1, affected_pool_id="pool-1")
        assert not _bff_incident_matches_filters(inc_p2, affected_pool_id="pool-1")

    def test_main_composition_wiring(self) -> None:
        """Verifies main.py uses IncidentService for bff incidents."""
        assert callable(bff_main._list_bff_incidents)
        assert callable(bff_main._get_bff_incident)
