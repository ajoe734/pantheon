from __future__ import annotations

import copy
import importlib
import os
import sys
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional
from urllib.error import HTTPError
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from services.control_plane.bff import command_executor
from services.control_plane.bff import main as bff_main
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports
from services.control_plane.bff.tests.management_projection_test_doubles import PplFixtureBuilder


AUTHORITY_URL = "http://capital-authority.test"
HEADERS = {"Authorization": "Bearer op-2:operator"}
APPROVER_HEADERS = {"Authorization": "Bearer op-approval:approver"}
SECOND_OPERATOR_HEADERS = {"Authorization": "Bearer op-3:operator"}


class MarketPersonaProjectionTestDouble(ReadSurfacePorts):
    """Explicit BFF projection double for seeded, in-memory port records.

    Production BFF reads can request market-persona catalog defaults.  This
    fixture carries only records deliberately seeded by a test, so the flag is
    accepted at the BFF boundary but must not synthesize unseeded catalog data.
    """

    @staticmethod
    def _without_market_persona_defaults(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        compatible_kwargs = dict(kwargs)
        compatible_kwargs.pop("include_market_persona_defaults", None)
        return compatible_kwargs

    def list_personas(self, **kwargs: Any) -> list[Dict[str, Any]]:
        return super().list_personas(**self._without_market_persona_defaults(kwargs))

    def list_capital_pools(self, **kwargs: Any) -> list[Dict[str, Any]]:
        return super().list_capital_pools(**self._without_market_persona_defaults(kwargs))

    def list_bindings(self, **kwargs: Any) -> list[Dict[str, Any]]:
        return super().list_bindings(**self._without_market_persona_defaults(kwargs))

    def list_deployment_plans(self, **kwargs: Any) -> list[Dict[str, Any]]:
        return super().list_deployment_plans(**self._without_market_persona_defaults(kwargs))

    def list_runtime_bindings(self, **kwargs: Any) -> list[Dict[str, Any]]:
        return super().list_runtime_bindings(**self._without_market_persona_defaults(kwargs))

    def list_persona_league(self, **kwargs: Any) -> list[Dict[str, Any]]:
        return super().list_persona_league(**self._without_market_persona_defaults(kwargs))


def create_market_persona_projection_test_double(
    **kwargs: Any,
) -> MarketPersonaProjectionTestDouble:
    """Create the explicit BFF-compatible projection fixture used by this task."""
    ports = create_in_memory_read_surface_ports(**kwargs)
    return MarketPersonaProjectionTestDouble(
        operations_consultation=ports.operations_consultation,
        persona_capital_runtime=ports.persona_capital_runtime,
        ooda_management=ports.ooda_management,
        research_knowledge_source=ports.research_knowledge_source,
        lifecycle_telemetry_governance=ports.lifecycle_telemetry_governance,
        persona_training=ports.persona_training,
    )


class PplProjectionTestDouble(MarketPersonaProjectionTestDouble):
    """Explicit mutable PPL fixture over narrow read ports.

    The double exposes only the named Persona/Capital/Runtime fixture writes
    used by ranking-projection tests.  Reads continue through
    ``ReadSurfacePorts``; it is not a forwarding compatibility facade.
    """

    def __init__(self, *, snapshot: Optional[Dict[str, Any]] = None) -> None:
        state = copy.deepcopy(snapshot or {})
        self._fixture_builder = PplFixtureBuilder()
        self._personas = state.get("personas", [])
        self._capital_pools = state.get("capital_pools", [])
        self._bindings = state.get("bindings", [])
        self._runtime_bindings = state.get("runtime_bindings", [])
        self._rankings = state.get("rankings", [])
        self._rebalances = state.get("rebalances", [])
        self._capital_allocations = state.get("capital_allocations", [])
        self._ranking_snapshots = state.get("ranking_snapshots", {})
        self._allocation_evaluations = state.get("allocation_evaluations", {})
        ports = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={
                "personas": self._personas,
                "capital_pools": self._capital_pools,
                "bindings": self._bindings,
                "runtime_bindings": self._runtime_bindings,
                "rankings": self._rankings,
                "rebalances": self._rebalances,
                "capital_allocations": self._capital_allocations,
            }
        )
        super().__init__(
            operations_consultation=ports.operations_consultation,
            persona_capital_runtime=ports.persona_capital_runtime,
            ooda_management=ports.ooda_management,
            research_knowledge_source=ports.research_knowledge_source,
            lifecycle_telemetry_governance=ports.lifecycle_telemetry_governance,
            persona_training=ports.persona_training,
        )

    @staticmethod
    def _replace(records: list[Dict[str, Any]], record: Dict[str, Any], *keys: str) -> Dict[str, Any]:
        record_id = next((str(record.get(key) or "") for key in keys if record.get(key)), "")
        for index, existing in enumerate(records):
            existing_id = next((str(existing.get(key) or "") for key in keys if existing.get(key)), "")
            if record_id and existing_id == record_id:
                records[index] = copy.deepcopy(record)
                return records[index]
        records.append(copy.deepcopy(record))
        return records[-1]

    def create_persona(
        self,
        *,
        persona_id: str,
        name: str,
        actor_id: str,
        archetype: str = "generalist",
        lifecycle_state: str = "draft",
        risk_level: str = "low",
        mandate: Optional[str] = None,
        strategy_family: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **_: Any,
    ) -> Dict[str, Any]:
        record = self._fixture_builder.add_persona(
            persona_id,
            name=name,
            actor_id=actor_id,
            lifecycle_state=lifecycle_state,
            status=lifecycle_state,
            mandate=mandate or archetype,
            strategy_family=strategy_family or archetype,
            metadata={
                **(metadata or {}),
                "owner": actor_id,
                "archetype": archetype,
                "risk_level": risk_level,
            },
        )
        return self._replace(self._personas, record, "persona_id", "id")

    def create_persona_binding(
        self,
        *,
        binding_id: str,
        persona_id: str,
        capital_pool_id: str,
        actor_id: str,
        role: str = "paper_owner",
        validity: str = "active",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        record = self._fixture_builder.add_binding(
            binding_id,
            persona_id,
            capital_pool_id,
            actor_id=actor_id,
            role=role,
            validity=validity,
            status=validity,
            metadata=metadata or {},
            persona_capital_binding_id=binding_id,
        )
        return self._replace(self._bindings, record, "binding_id", "id")

    def create_runtime_binding(
        self,
        *,
        runtime_id: str,
        name: str,
        persona_id: str,
        binding_id: str,
        deployment_plan_id: str,
        runtime_kind: str,
        actor_id: str,
        params: Optional[Dict[str, Any]] = None,
        state: str = "stopped",
    ) -> Dict[str, Any]:
        clean_params = dict(params or {})
        record = self._fixture_builder.add_runtime_binding(
            runtime_id,
            binding_id,
            name=name,
            persona_id=persona_id,
            deployment_plan_id=deployment_plan_id,
            runtime_kind=runtime_kind,
            deployment_stage=runtime_kind,
            deployment_mode=runtime_kind,
            state=state,
            status=state,
            actor_id=actor_id,
            capital_pool_id=clean_params.get("capital_pool_id"),
            params=clean_params,
            runtime_binding_id=binding_id,
            persona_capital_binding_id=binding_id,
        )
        return self._replace(self._runtime_bindings, record, "runtime_id", "id")

    def add_authoritative_capital_pool(self, record: Dict[str, Any]) -> Dict[str, Any]:
        pool_id = str(record.get("pool_id") or record.get("id") or "")
        typed = self._fixture_builder.add_capital_pool(pool_id)
        typed.update(copy.deepcopy(record))
        return self._replace(self._capital_pools, typed, "pool_id", "id")

    def add_authoritative_binding(self, record: Dict[str, Any]) -> Dict[str, Any]:
        binding_id = str(record.get("binding_id") or record.get("id") or "")
        typed = self._fixture_builder.add_binding(
            binding_id,
            str(record.get("persona_id") or ""),
            str(record.get("capital_pool_id") or ""),
        )
        typed.update(copy.deepcopy(record))
        return self._replace(self._bindings, typed, "binding_id", "id")

    def add_authoritative_rebalance(self, record: Dict[str, Any]) -> Dict[str, Any]:
        rebalance_id = str(record.get("rebalance_id") or record.get("id") or "")
        typed = self._fixture_builder.add_rebalance(rebalance_id)
        typed.update(copy.deepcopy(record))
        return self._replace(self._rebalances, typed, "rebalance_id", "id")

    def put_ranking_snapshot(self, record: Dict[str, Any]) -> Dict[str, Any]:
        snapshot_id = str(record.get("ranking_snapshot_id") or "")
        if not snapshot_id or not record.get("content_digest"):
            raise ValueError("ranking snapshot id and content_digest are required")
        existing = self._ranking_snapshots.get(snapshot_id)
        if existing is not None and existing.get("content_digest") != record.get("content_digest"):
            raise ValueError("ranking snapshot id already has different content")
        stored = copy.deepcopy({**record, "ranking_snapshot_id": snapshot_id})
        self._ranking_snapshots[snapshot_id] = stored
        ranking = {**stored, "id": snapshot_id, "ranking_id": snapshot_id}
        self._replace(self._rankings, ranking, "ranking_id", "id")
        return copy.deepcopy(stored)

    def get_ranking_snapshot(self, snapshot_id: Optional[str]) -> Optional[Dict[str, Any]]:
        record = self._ranking_snapshots.get(str(snapshot_id or ""))
        return copy.deepcopy(record) if record is not None else None

    def put_allocation_evaluation(self, record: Dict[str, Any]) -> Dict[str, Any]:
        evaluation_id = str(record.get("allocation_evaluation_id") or "")
        if not evaluation_id or not record.get("content_digest"):
            raise ValueError("allocation evaluation id and content_digest are required")
        existing = self._allocation_evaluations.get(evaluation_id)
        if existing is not None and existing.get("content_digest") != record.get("content_digest"):
            raise ValueError("allocation evaluation id already has different content")
        stored = copy.deepcopy({**record, "allocation_evaluation_id": evaluation_id})
        self._allocation_evaluations[evaluation_id] = stored
        allocation = {**stored, "id": evaluation_id, "allocation_id": evaluation_id}
        self._replace(self._capital_allocations, allocation, "allocation_id", "id")
        return copy.deepcopy(stored)

    def get_allocation_evaluation(self, evaluation_id: Optional[str]) -> Optional[Dict[str, Any]]:
        record = self._allocation_evaluations.get(str(evaluation_id or ""))
        return copy.deepcopy(record) if record is not None else None

    def get_capability_snapshot_for_persona(self, persona_id: str) -> Optional[Dict[str, Any]]:
        del persona_id
        return None

    def dataset_source(self, dataset: str) -> str:
        if dataset in {"evidence_refs", "ranking_snapshots", "allocation_evaluations"}:
            return "typed_store"
        return super().dataset_source(dataset)

    def tamper_ranking_snapshot_item(
        self, snapshot_id: str, persona_id: str, field: str, value: Any
    ) -> None:
        record = self._ranking_snapshots[snapshot_id]
        item = next(item for item in record.get("items", []) if item.get("persona_id") == persona_id)
        item[field] = value

    def tamper_allocation_evaluation_line(
        self, evaluation_id: str, line_index: int, field: str, value: Any
    ) -> None:
        self._allocation_evaluations[evaluation_id]["lines"][line_index][field] = value

    def clone_for_restart(self) -> "PplProjectionTestDouble":
        clone = PplProjectionTestDouble(
            snapshot={
                "personas": self._personas,
                "capital_pools": self._capital_pools,
                "bindings": self._bindings,
                "runtime_bindings": self._runtime_bindings,
                "rankings": self._rankings,
                "rebalances": self._rebalances,
                "capital_allocations": self._capital_allocations,
                "ranking_snapshots": self._ranking_snapshots,
                "allocation_evaluations": self._allocation_evaluations,
            }
        )
        for name in (
            "get_sessions_for_persona",
            "get_telemetry_summary",
            "list_authoritative_paper_runtime_monitoring_sessions",
            "list_evidence_refs",
        ):
            if name in self.__dict__:
                setattr(clone, name, self.__dict__[name])
        return clone


def _assign_rebalance_lineage(payload: Dict[str, Any]) -> Dict[str, Any]:
    snapshot_id = str(payload.get("ranking_snapshot_id") or "rank-q3")
    policy_version = "persona-real-allocation-v1"
    basis_lines = [
        {
            key: value
            for key, value in line.items()
            if key not in {
                "ranking_snapshot_id",
                "allocation_evaluation_id",
                "allocation_line_digest",
                "allocation_policy_version",
            }
        }
        for line in payload.get("lines") or []
    ]
    evaluation_id = (
        "allocation-evaluation-"
        + bff_main._stable_json_hash(
            {
                "ranking_snapshot_id": snapshot_id,
                "allocation_policy_version": policy_version,
                "lines": basis_lines,
            }
        )[:24]
    )
    payload["ranking_snapshot_id"] = snapshot_id
    payload["allocation_evaluation_id"] = evaluation_id
    payload["allocation_policy_version"] = policy_version
    for line in payload.get("lines") or []:
        line["ranking_snapshot_id"] = snapshot_id
        line["allocation_evaluation_id"] = evaluation_id
        line["allocation_policy_version"] = policy_version
        line.pop("allocation_line_digest", None)
        line["allocation_line_digest"] = bff_main._pm12_allocation_line_digest(
            line
        )
    return payload


def rebalance_payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "capital_pool_id": "pool-real",
        "ranking_snapshot_id": "rank-q3",
        "reason": "quarterly",
        "lines": [
            {
                "persona_id": "p-live",
                "stage": "live_running",
                "capital_scope": "pool",
                "capital_pool_id": "pool-real",
                "capital_sleeve_id": "sleeve-live",
                "current_weight": 0.10,
                "target_weight": 0.12,
                "delta": 0.02,
                "cap_reasons": ["quarterly_increase_cap_25pct"],
                "evidence_refs": ["ev-1"],
            }
        ],
        "simulation": {"status": "passed", "run_id": "sim-q3"},
        "constraints": {"pool_total_max": 1.0, "max_turnover": 0.25},
        "rollback_target": {
            "snapshot_id": "allocation-before-q3",
            "allocation_version": 7,
        },
        "audit_refs": ["audit-ranking-q3", "audit-simulation-q3"],
    }
    payload.update(overrides)
    return _assign_rebalance_lineage(payload)


class CapitalBffAuthorityHarness:
    """Run BFF tests against the real, durable Capital service boundary."""

    _ENV_KEYS = (
        "BFF_COMMIT",
        "CAPITAL_AUDIT_BACKEND",
        "CAPITAL_AUTH_DISABLED",
        "CAPITAL_DATA_DIR",
        "CAPITAL_STORE_BACKEND",
        "PANTHEON_BFF_CAPITAL_ALLOCATION_STORE",
        "PANTHEON_BFF_CAPITAL_POOL_STORE",
        "PANTHEON_BFF_CONTAINMENT_STORE",
        "PANTHEON_BFF_PERSONA_REGISTRY_STORE",
        "PANTHEON_BFF_REBALANCE_STORE",
        "PANTHEON_CAPITAL_API_URL",
        "PANTHEON_CAPITAL_SERVICE_URL",
        "PANTHEON_ENV",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_PERSONA_DATA_DIR",
        "PANTHEON_PERSISTENCE_POSTURE",
    )

    def __init__(self, root: Path, *, seed_allocation: bool = True) -> None:
        self.root = Path(root)
        self.seed_allocation = seed_allocation
        self.capital_data_dir = self.root / "capital"
        self.read_path = self.root / "bff-read-surfaces.json"
        self.command_path = self.root / "bff-commands.jsonl"
        self.capital_module: Optional[ModuleType] = None
        self.capital_client: Optional[TestClient] = None
        self.client: Optional[TestClient] = None
        self.read_surface = PplProjectionTestDouble()

    def __enter__(self) -> "CapitalBffAuthorityHarness":
        self.root.mkdir(parents=True, exist_ok=True)
        self.capital_data_dir.mkdir(parents=True, exist_ok=True)
        self._environment = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self._previous_capital_module = sys.modules.get("services.capital.main")
        self._original_read_store = bff_main.read_store
        self._original_command_store = bff_main.command_store
        self._original_post_json = command_executor._post_json
        self._original_get_json = command_executor._get_json
        self._capital_idempotency = dict(bff_main._CAPITAL_BFF_IDEMPOTENCY)
        self._command_auth_context = dict(bff_main._COMMAND_AUTH_CONTEXT)
        self._persona_overlay = dict(bff_main._PERSONA_BFF_OVERLAY)

        for key in self._ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(
            {
                "CAPITAL_AUDIT_BACKEND": "jsonl",
                "CAPITAL_AUTH_DISABLED": "true",
                "CAPITAL_DATA_DIR": str(self.capital_data_dir),
                "CAPITAL_STORE_BACKEND": "json",
                "PANTHEON_CAPITAL_API_URL": AUTHORITY_URL,
                "PANTHEON_ENV": "dev",
                "PANTHEON_GOVERNANCE_DATA_DIR": str(self.capital_data_dir),
                "PANTHEON_PERSISTENCE_POSTURE": "dev",
            }
        )

        sys.modules.pop("services.capital.main", None)
        self.capital_module = importlib.import_module("services.capital.main")
        self.capital_client = TestClient(self.capital_module.app)
        command_executor._post_json = self._post_json
        command_executor._get_json = self._get_json
        self._reset_bff_process_state()

        assert self.client is not None
        response = self.client.post(
            "/bff/capital-pools",
            json={
                "pool_id": "pool-real",
                "name": "Regression Pool",
                "owner_id": "fund-real",
                "owner_type": "fund",
                "risk_policy_ref": "risk-main",
            },
            headers={**HEADERS, "Idempotency-Key": "create-pool-real"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["pool_id"] == "pool-real"
        assert response.json()["status"] == "active"

        response = self.client.post(
            "/api/v1/bindings",
            json={
                "binding_id": "binding-live",
                "persona_id": "p-live",
                "capital_pool_id": "pool-real",
                "capital_sleeve_id": "sleeve-live",
                "role": "live_owner",
                "allowed_deployment_scope": "live",
            },
            headers={**HEADERS, "Idempotency-Key": "create-binding-live"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["binding_id"] == "binding-live"
        assert response.json()["capital_sleeve_id"] == "sleeve-live"
        assert response.json()["status"] == "pending"
        if self.seed_allocation:
            self._seed_authoritative_allocation()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.client is not None:
            self.client.close()
        if self.capital_client is not None:
            self.capital_client.close()

        command_executor._post_json = self._original_post_json
        command_executor._get_json = self._original_get_json
        bff_main.read_store = self._original_read_store
        bff_main.command_store = self._original_command_store
        bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
        bff_main._CAPITAL_BFF_IDEMPOTENCY.update(self._capital_idempotency)
        bff_main._COMMAND_AUTH_CONTEXT.clear()
        bff_main._COMMAND_AUTH_CONTEXT.update(self._command_auth_context)
        bff_main._PERSONA_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.update(self._persona_overlay)

        if self._previous_capital_module is None:
            sys.modules.pop("services.capital.main", None)
        else:
            sys.modules["services.capital.main"] = self._previous_capital_module
        for key, value in self._environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _reset_bff_process_state(self) -> None:
        if self.client is not None:
            self.client.close()
        bff_main.read_store = self.read_surface
        bff_main.command_store = CommandStore(str(self.command_path))
        bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
        bff_main._COMMAND_AUTH_CONTEXT.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()
        self.client = TestClient(bff_main.app)

    def restart(self) -> None:
        """Rebuild both owner and BFF process-local state over the same files."""
        assert self.capital_module is not None
        if self.capital_client is not None:
            self.capital_client.close()
        self.capital_module = importlib.reload(self.capital_module)
        self.capital_client = TestClient(self.capital_module.app)
        self.read_surface = self.read_surface.clone_for_restart()
        self._reset_bff_process_state()

    def create_persona(self, persona_id: str = "p-live") -> Dict[str, Any]:
        return self.read_surface.create_persona(
            persona_id=persona_id,
            name="Contained Live Persona",
            actor_id="operator-test",
            lifecycle_state="live_running",
            risk_level="high",
            mandate="systematic live trading",
            strategy_family="momentum",
        )

    def admit_rebalance_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Test-only admission fixture for the exact server-materialized lines."""
        _assign_rebalance_lineage(payload)
        snapshot_id = str(payload["ranking_snapshot_id"])
        evaluation_id = str(payload["allocation_evaluation_id"])
        policy_version = str(payload["allocation_policy_version"])
        self.read_surface.put_ranking_snapshot({
            "ranking_snapshot_id": snapshot_id,
            "surface": "quarterly",
            "period": "test",
            "formula_version": "pm12-default-v1",
            "content_digest": bff_main._stable_json_hash(
                {
                    "surface": "quarterly",
                    "period": "test",
                    "formula_version": "pm12-default-v1",
                    "items": [],
                }
            ),
            "items": [],
            "evidence_assertion_digests": {},
        })
        lines = [dict(line) for line in payload.get("lines") or []]
        self.read_surface.put_allocation_evaluation({
            "allocation_evaluation_id": evaluation_id,
            "ranking_snapshot_id": snapshot_id,
            "allocation_policy_version": policy_version,
            "content_digest": bff_main._stable_json_hash(
                {
                    "ranking_snapshot_id": snapshot_id,
                    "allocation_evaluation_id": evaluation_id,
                    "allocation_policy_version": policy_version,
                    "lines": lines,
                }
            ),
            "lines": lines,
            "applied": False,
        })
        return payload

    def _seed_authoritative_allocation(self) -> None:
        """Owner-only fixture bootstrap; product apply paths still enter via BFF."""
        assert self.capital_client is not None
        seed_line = {
            "ranking_snapshot_id": "rank-seed",
            "allocation_evaluation_id": "allocation-evaluation-seed",
            "allocation_policy_version": "persona-real-allocation-v1",
            "persona_id": "p-live",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-real",
            "capital_sleeve_id": "sleeve-live",
            "current_weight": 0.0,
            "target_weight": 0.10,
            "delta": 0.10,
            "cap_reasons": [],
            "evidence_refs": [],
        }
        seed_line["allocation_line_digest"] = (
            bff_main._pm12_allocation_line_digest(seed_line)
        )
        created = self.capital_client.post(
            "/api/rebalances",
            json={
                "actor_id": "op-2",
                "actor_role": "operator",
                "idempotency_key": "seed-allocation-proposal",
                "request_hash": "seed-allocation-proposal-v1",
                "rebalance_id": "rb-seed-allocation",
                "capital_pool_id": "pool-real",
                "ranking_snapshot_id": "rank-seed",
                "allocation_evaluation_id": "allocation-evaluation-seed",
                "allocation_policy_version": "persona-real-allocation-v1",
                "reason": "Seed authoritative test baseline",
                "lines": [seed_line],
            },
        )
        assert created.status_code == 201, created.text
        applied = self.capital_client.post(
            "/api/rebalances/rb-seed-allocation/apply",
            json={
                "actor_id": "op-2",
                "actor_role": "operator",
                "idempotency_key": "seed-allocation-apply",
                "request_hash": "seed-allocation-apply-v1",
                "command_id": "cmd-seed-allocation",
                "approval_ref": "approval-seed-allocation",
            },
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["allocation_readback"][0]["current_weight"] == 0.10

    def apply_evidence(
        self,
        rebalance_id: str,
        *,
        suffix: str,
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        """Create restart-safe approval, confirm-token, and two-man evidence."""
        assert self.client is not None
        approval_id = f"approval-{suffix}"
        signature_id = f"tms-{suffix}"
        token_id = f"ct-{suffix}"

        approved = self.client.post(
            f"/bff/rebalances/{rebalance_id}/approve",
            json={"approval_decision_id": approval_id, "memo": "Regression approval"},
            headers={**APPROVER_HEADERS, "Idempotency-Key": f"approve-{suffix}"},
        )
        assert approved.status_code == 201, approved.text

        confirmed = self.client.post(
            "/bff/confirm-tokens",
            json={
                "tokenId": token_id,
                "command": "ApprovedApply",
                "target": {"type": "Rebalance", "id": rebalance_id},
                "operator_id": "op-2",
                "reason": "Confirm authoritative rebalance apply",
            },
            headers={**HEADERS, "Idempotency-Key": f"confirm-{suffix}"},
        )
        assert confirmed.status_code == 201, confirmed.text

        first = self.client.post(
            f"/bff/rebalances/{rebalance_id}/two-man-sign",
            json={"two_man_signature_id": signature_id},
            headers={**HEADERS, "Idempotency-Key": f"sign-first-{suffix}"},
        )
        assert first.status_code == 202, first.text
        assert first.json()["data"]["complete"] is False
        second = self.client.post(
            f"/bff/rebalances/{rebalance_id}/two-man-sign",
            json={"two_man_signature_id": signature_id},
            headers={**SECOND_OPERATOR_HEADERS, "Idempotency-Key": f"sign-second-{suffix}"},
        )
        assert second.status_code == 202, second.text
        assert second.json()["data"]["complete"] is True

        return (
            {
                "approval_decision_id": approval_id,
                "two_man_signature_id": signature_id,
            },
            {**HEADERS, "X-Confirm-Token": token_id},
        )

    def _post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        del auth_token, mfa_token
        assert self.capital_client is not None
        parsed = urlsplit(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        response = self.capital_client.post(path, json=payload)
        if response.status_code >= 400:
            raise HTTPError(
                url,
                response.status_code,
                response.reason_phrase,
                response.headers,
                BytesIO(response.content),
            )
        body = response.json()
        if parsed.path == "/api/capital-pools":
            self.read_surface.add_authoritative_capital_pool(body)
        elif parsed.path == "/api/bindings":
            self.read_surface.add_authoritative_binding(body)
        elif parsed.path == "/api/rebalances":
            self.read_surface.add_authoritative_rebalance(body)
        return body

    def _get_json(
        self,
        url: str,
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Any:
        del auth_token, mfa_token
        assert self.capital_client is not None
        parsed = urlsplit(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        response = self.capital_client.get(path)
        if response.status_code >= 400:
            raise HTTPError(
                url,
                response.status_code,
                response.reason_phrase,
                response.headers,
                BytesIO(response.content),
            )
        if not response.content:
            return None
        return response.json()
