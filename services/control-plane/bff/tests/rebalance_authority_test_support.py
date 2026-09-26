from __future__ import annotations

__test__ = False

import copy
import hashlib
import importlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Iterator, Optional
from urllib.error import HTTPError
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from services.control_plane.bff import command_executor
from services.control_plane.bff.management_ai_store import ManagementAiAttachmentStore
from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.auth.policy import (
    bff_error,
    require_operator_role,
    require_read_role,
)


def extract_identity(*args: Any, **kwargs: Any) -> Any:
    """Late-bound identity extraction.

    Routers mounted by these harnesses resolve the auth policy's
    ``extract_identity`` at request time, so a test that installs a stricter
    identity extractor on ``auth.policy`` reaches the mounted app the same way
    it reached the composition root's module-level indirection.
    """
    return auth_policy.extract_identity(*args, **kwargs)
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.command_adapters.router import (
    create_action_command_router,
    create_command_adapters_router,
)
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.tests.management_projection_test_doubles import PplFixtureBuilder
from services.control_plane.bff.models import ErrorCode, utc_now
from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports


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
        self._ppl_ranking_snapshots = state.get("ranking_snapshots", {})
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
        existing = self._ppl_ranking_snapshots.get(snapshot_id)
        if existing is not None and existing.get("content_digest") != record.get("content_digest"):
            raise ValueError("ranking snapshot id already has different content")
        stored = copy.deepcopy({**record, "ranking_snapshot_id": snapshot_id})
        self._ppl_ranking_snapshots[snapshot_id] = stored
        ranking = {**stored, "id": snapshot_id, "ranking_id": snapshot_id}
        self._replace(self._rankings, ranking, "ranking_id", "id")
        return copy.deepcopy(stored)

    def get_ranking_snapshot(self, snapshot_id: Optional[str]) -> Optional[Dict[str, Any]]:
        record = self._ppl_ranking_snapshots.get(str(snapshot_id or ""))
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
        record = self._ppl_ranking_snapshots[snapshot_id]
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
                "ranking_snapshots": self._ppl_ranking_snapshots,
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


_PM12_ALLOCATION_LINE_DIGEST_FIELDS = (
    "ranking_snapshot_id",
    "allocation_evaluation_id",
    "allocation_policy_version",
    "persona_id",
    "stage",
    "capital_scope",
    "capital_pool_id",
    "capital_sleeve_id",
    "current_weight",
    "target_weight",
    "delta",
    "cap_reasons",
    "evidence_refs",
)


def _stable_json_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _pm12_allocation_line_digest(line: Dict[str, Any]) -> str:
    basis = {
        field: line.get(field)
        for field in _PM12_ALLOCATION_LINE_DIGEST_FIELDS
    }
    basis["capital_scope"] = line.get("capital_scope") or "pool"
    basis["cap_reasons"] = list(line.get("cap_reasons") or [])
    basis["evidence_refs"] = list(line.get("evidence_refs") or [])
    return _stable_json_hash(basis)


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
        + _stable_json_hash(
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
        line["allocation_line_digest"] = _pm12_allocation_line_digest(
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


class PplRankingProjectionHarness:
    """Isolated BFF app for the PPL ranking / capital projection suites.

    Mounts the same already-extracted production router factories the
    composition root mounts for the persona-league, quarterly-ranking,
    promotion-review, capital and command-adapter surfaces, with the read
    surface, command store and idempotency stores injected explicitly
    instead of reached through ``main.py`` module globals.
    """

    def __init__(
        self,
        *,
        read_surface: Optional[PplProjectionTestDouble] = None,
        command_path: Optional[str] = None,
    ) -> None:
        self.read_surface = read_surface if read_surface is not None else PplProjectionTestDouble()
        if command_path is None:
            self._command_dir = tempfile.TemporaryDirectory()
            command_path = os.path.join(self._command_dir.name, "commands.jsonl")
        self.command_path = command_path
        self.command_store = CommandStore(command_path)
        self.final_idempotency: Dict[str, Dict[str, Any]] = {}
        self.gov_idempotency: Dict[str, Dict[str, Any]] = {}
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        from services.control_plane.bff.command_adapters.service import CommandAdapterService
        from services.control_plane.bff.core.errors import register_error_handlers
        from services.control_plane.bff.management_read_models.router import (
            create_management_router,
        )
        from services.control_plane.bff.personas import (
            PersonaService,
            create_personas_router,
        )
        from services.control_plane.bff.personas.service import (
            create_persona_registry_write_owner,
        )

        app = FastAPI()
        register_error_handlers(app)

        self.persona_service = PersonaService(
            write_owner=create_persona_registry_write_owner(),
            read_store=self.read_surface,
            ranking_write_owner=self.read_surface,
            command_store=self.command_store,
        )
        app.include_router(
            create_personas_router(
                service=self.persona_service,
                extract_identity_fn=extract_identity,
                require_read_role_fn=require_read_role,
                require_operator_role_fn=require_operator_role,
                bff_error_fn=bff_error,
                utc_now_fn=utc_now,
            )
        )
        app.include_router(
            create_capital_router(
                read_surface=lambda: self.read_surface,
                extract_identity=extract_identity,
                require_read_role=require_read_role,
                require_operator_role=require_operator_role,
                bff_error=bff_error,
                utc_now=utc_now,
            )
        )
        self.command_adapter_service = CommandAdapterService(
            command_store=lambda: self.command_store,
            read_surface=lambda: self.read_surface,
            extract_identity=extract_identity,
            require_operator_role=require_operator_role,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=utc_now,
            final_contract_idempotency=self.final_idempotency,
            gov_bff_idempotency=self.gov_idempotency,
        )
        app.include_router(
            create_command_adapters_router(service=self.command_adapter_service)
        )
        app.include_router(
            create_management_router(
                get_read_store=lambda: self.read_surface,
                extract_identity=extract_identity,
                require_read_role=require_read_role,
                bff_error=bff_error,
                utc_now=utc_now,
            )
        )

        @app.post("/api/v1/bindings", status_code=201)
        async def _create_binding(payload: Dict[str, Any] = Body(...)):
            return command_executor.create_capital_binding(payload)

        return app

    def client(self) -> TestClient:
        return TestClient(self.app, raise_server_exceptions=False)

    def set_read_surface(self, read_surface: PplProjectionTestDouble) -> PplProjectionTestDouble:
        """Swap the injected read projection the mounted routers resolve."""
        self.read_surface = read_surface
        self.persona_service._read_store = read_surface
        self.persona_service._ranking_write_owner = read_surface
        return read_surface

    def restart(
        self, read_surface: Optional[PplProjectionTestDouble] = None
    ) -> "PplRankingProjectionHarness":
        """Rebuild process-local BFF state over the same durable files."""
        return PplRankingProjectionHarness(
            read_surface=(
                read_surface
                if read_surface is not None
                else self.read_surface.clone_for_restart()
            ),
            command_path=self.command_path,
        )


def _build_authority_harness_app(
    read_surface: ReadSurfacePorts,
    command_store: CommandStore,
) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "ERROR", "message": str(detail)}},
        )

    app.include_router(
        create_capital_router(
            read_surface=read_surface,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )
    app.include_router(
        create_command_adapters_router(
            command_store=command_store,
            read_surface=read_surface,
            extract_identity=extract_identity,
            require_operator_role=require_operator_role,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )
    app.include_router(
        create_action_command_router(
            command_store=command_store,
            extract_identity=extract_identity,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )

    @app.post("/api/v1/bindings", status_code=201)
    async def _create_binding(payload: Dict[str, Any] = Body(...)):
        return command_executor.create_capital_binding(payload)

    @app.get("/api/v1/bindings")
    async def _list_bindings():
        return {"data": read_surface.list_bindings(), "meta": {}}

    @app.get("/api/v1/bindings/{binding_id}")
    async def _get_binding(binding_id: str):
        for b in read_surface.list_bindings():
            if b.get("binding_id") == binding_id or b.get("id") == binding_id:
                return {"data": b, "meta": {}}
        raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Binding not found")

    return app


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
        self.command_store: Optional[CommandStore] = None
        self.capital_module: Optional[ModuleType] = None
        self.capital_client: Optional[TestClient] = None
        self.client: Optional[TestClient] = None
        self.read_surface = PplProjectionTestDouble()

    def __enter__(self) -> "CapitalBffAuthorityHarness":
        self.root.mkdir(parents=True, exist_ok=True)
        self.capital_data_dir.mkdir(parents=True, exist_ok=True)
        self._environment = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self._previous_capital_module = sys.modules.get("services.capital.main")
        self._original_post_json = command_executor._post_json
        self._original_get_json = command_executor._get_json

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
        self.command_store = CommandStore(str(self.command_path))
        app = _build_authority_harness_app(self.read_surface, self.command_store)
        self.client = TestClient(app)

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
            "content_digest": _stable_json_hash(
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
            "content_digest": _stable_json_hash(
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
            _pm12_allocation_line_digest(seed_line)
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


def get_management_nl_module() -> ModuleType:
    """Real-seam accessor for BFF management NL composition state.

    Returns ``services.control_plane.bff.assistant.management_service``
    directly instead of dynamically loading ``main.py``. main.py's own
    module proxy (``_BffMainModule.__getattr__``/``__setattr__`` at the
    bottom of main.py) already delegates every management-NL-related
    attribute (``read_store``, ``OpenClawOpsClient``, ``_MGMT_AI_*``,
    ``_MANAGEMENT_NL_USE_CASE``, ``bff_management_nl_ask`` and friends) onto
    this exact module, because BFF-MGMT-NL-HELPER-EXTRACTION-001 extracted
    all 37 management NL helpers' real implementations into
    ``management_service.py`` and retired main.py's own copies. Reading or
    writing state here therefore has the identical runtime effect as poking
    main.py's proxy, with no import of main.py at all (AC3).
    """
    from services.control_plane.bff.assistant import management_service

    return management_service


async def _seam_bff_management_ai_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
) -> Any:
    """Real-seam route handler for GET /bff/management/ai/conversations,
    reproducing main.py's own thin wrapper (main.py line ~7064) purely from
    already-extracted assistant.management_service functions -- no main.py
    business logic, only glue/parameter-passing, same as main.py's own
    handler body."""
    from services.control_plane.bff.assistant import management_service as ms

    identity = ms._extract_identity(authorization)
    ms._require_read_role(identity)
    caller_tenant_id = ms._mgmt_nl_caller_tenant(
        identity,
        requested_tenant=x_tenant_id or x_pantheon_tenant,
    )
    return ms.management_ai_list_conversations(
        identity=identity,
        caller_tenant_id=caller_tenant_id,
        limit=limit,
    )


async def _seam_bff_management_ai_conversation(
    session_id: str,
    trace_id: Optional[str] = None,
    limit: int = Query(default=500, ge=1, le=1000),
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
) -> Any:
    """Real-seam route handler for GET /bff/management/ai/conversations/
    {session_id}, reproducing main.py's own thin wrapper (main.py line
    ~7085) purely from already-extracted assistant.management_service
    functions -- no main.py business logic, only glue/parameter-passing."""
    from services.control_plane.bff.assistant import management_service as ms

    identity = ms._extract_identity(authorization)
    ms._require_read_role(identity)
    clean_session_id = str(session_id or "").strip()
    caller_tenant_id = ms._mgmt_nl_caller_tenant(
        identity,
        requested_tenant=x_tenant_id or x_pantheon_tenant,
    )
    return ms.management_ai_get_conversation(
        session_id=clean_session_id,
        identity=identity,
        caller_tenant_id=caller_tenant_id,
        trace_id=trace_id,
        limit=limit,
        audit_href_fn=lambda s_id, t_id: ms._management_ai_audit_href(session_id=s_id, trace_id=t_id),
    )


async def _seam_bff_management_ai_attachment(
    attachment_id: str,
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
) -> Any:
    """Real-seam route handler for GET /bff/management/ai/attachments/
    {attachment_id}, reproducing main.py's own thin wrapper (main.py line
    ~7111) purely from already-extracted assistant.management_service
    functions -- no main.py business logic, only glue/parameter-passing."""
    from fastapi import Response

    from services.control_plane.bff.assistant import management_service as ms

    identity = ms._extract_identity(authorization)
    ms._require_read_role(identity)
    caller_tenant_id = ms._mgmt_nl_caller_tenant(
        identity,
        requested_tenant=x_tenant_id or x_pantheon_tenant,
    )
    content, mime_type, filename = ms.management_ai_get_attachment(
        attachment_id=attachment_id,
        identity=identity,
        caller_tenant_id=caller_tenant_id,
    )
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


async def _seam_bff_management_ai_audit(
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    message_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
    authorization: Optional[str] = Header(default=None),
) -> Any:
    """Real-seam route handler for GET /bff/management/ai/audit, reproducing
    main.py's own thin wrapper (main.py line ~7007) purely from already-
    extracted functions -- no main.py business logic, only glue. The
    camelCase-pruning helper is a generic, stateless utility that also lives
    verbatim in personas/service.py (not main.py-specific business logic)."""
    from services.control_plane.bff.assistant import management_service as ms
    from services.control_plane.bff.personas.service import _management_prune_camel_aliases

    identity = ms._extract_identity(authorization)
    ms._require_read_role(identity)
    events = ms._management_ai_list_audit_events(
        session_id=session_id,
        trace_id=trace_id,
        message_id=message_id,
        event_type=event_type,
        limit=limit,
    )
    canonical_events = _management_prune_camel_aliases(events)
    return {
        "data": {
            "id": "management_ai_audit",
            "items": canonical_events,
            "summary": {
                "total_events": len(canonical_events),
                "returned_items": len(canonical_events),
            },
        },
        "page_info": {
            "next_page_token": None,
            "total": len(canonical_events),
            "page_size": limit,
        },
        "meta": {
            "count": len(canonical_events),
            "filters": {
                "session_id": session_id,
                "trace_id": trace_id,
                "message_id": message_id,
                "event_type": event_type,
            },
        },
    }


_SEAM_APP: Optional[FastAPI] = None


def get_management_nl_app() -> FastAPI:
    """Build (once, then cache) the real, fully composed BFF app via the
    ``compose_bff_app`` seam (AC3) -- the same 37-router composition main.py
    itself assembles, but reachable without importing main.py. All routes
    used by the management NL suites (``/bff/management/nl/ask`` and
    ``/bff/management/nl/ask/stream``) are mounted by this composer straight
    from ``assistant.management_service`` (see ``core/app_factory.py``'s
    ``_dep``/``mount_bff_routers``), so this is main.py's real production
    composition, not a stand-in."""
    global _SEAM_APP
    if _SEAM_APP is None:
        from services.control_plane.bff.core.app_factory import compose_bff_app
        from services.control_plane.bff.assistant.management_service import (
            get_management_ai_conversation_store,
        )

        # core/app_factory.py's standalone dependency resolver falls back to a
        # no-op stub for "_management_ai_conversation_store" when main.py is
        # not loaded, even though a real seam exists
        # (assistant.management_service.get_management_ai_conversation_store,
        # the exact function main.py's own `_management_ai_conversation_store`
        # wrapper delegates to). mount_bff_routers/_dep() honors an explicit
        # keyword override before falling back to that stub, so pass the real
        # seam through explicitly rather than accepting the stub.
        _SEAM_APP = compose_bff_app(
            _management_ai_conversation_store=get_management_ai_conversation_store,
            bff_management_ai_conversations=_seam_bff_management_ai_conversations,
            bff_management_ai_conversation=_seam_bff_management_ai_conversation,
            bff_management_ai_attachment=_seam_bff_management_ai_attachment,
            bff_management_ai_audit=_seam_bff_management_ai_audit,
        )
    return _SEAM_APP


def get_management_nl_read_store() -> Any:
    """Retrieve the current active read_store for the management NL seam."""
    management_service = get_management_nl_module()
    return management_service.get_read_store()


def set_management_nl_read_store(store: Any) -> None:
    """Set the active read_store on the management NL seam and persona service.

    Also syncs main.py's own read_store proxy and its _management_ai_
    context_service, but only if main.py happens to already be imported in
    this process (via sys.modules, never forcing an import) -- some test
    files elsewhere in this suite import main.py directly for collaborators
    with no extracted seam, and without this sync a test's own cleanup call
    to this function would restore the seam's read_store but leave main.py's
    proxy/context-service pointed at stale test data, leaking state into
    whichever real-main-backed test runs next.
    """
    import sys

    management_service = get_management_nl_module()
    import services.control_plane.bff.personas.service as personas_service
    management_service.set_read_store(store)
    setattr(personas_service, "read_store", store)
    context_svc = getattr(management_service, "_management_ai_context_service", None)
    if context_svc is not None:
        context_svc._get_read_store = (lambda: store) if store is not None else None

    real_main = sys.modules.get("services.control_plane.bff.main")
    if real_main is not None:
        setattr(real_main, "read_store", store)
        real_main_context_svc = getattr(real_main, "_management_ai_context_service", None)
        if real_main_context_svc is not None:
            real_main_context_svc._get_read_store = (lambda: store) if store is not None else None


def get_management_nl_sse_buffer(channel: str = "ask") -> list:
    """Read events from the management NL SSE buffer safely."""
    management_service = get_management_nl_module()
    return list(management_service._sse_buffers.get(channel, []))


def clear_management_nl_sse_buffer(channel: str = "ask") -> None:
    """Clear events in the management NL SSE buffer safely."""
    management_service = get_management_nl_module()
    if channel in management_service._sse_buffers:
        management_service._sse_buffers[channel].clear()


@contextmanager
def bound_management_nl_store(read_surface: Any) -> Iterator[Any]:
    """Context manager to scope active read_store on the management NL seam and personas."""
    management_service = get_management_nl_module()
    import services.control_plane.bff.personas.service as personas_service

    old_main_store = management_service.get_read_store()
    old_persona_store = getattr(personas_service, "read_store", None)
    context_svc = getattr(management_service, "_management_ai_context_service", None)
    old_context_fn = getattr(context_svc, "_get_read_store", None) if context_svc is not None else None
    try:
        management_service.set_read_store(read_surface)
        setattr(personas_service, "read_store", read_surface)
        if context_svc is not None:
            context_svc._get_read_store = (lambda: read_surface) if read_surface is not None else None
        yield read_surface
    finally:
        management_service.set_read_store(old_main_store)
        setattr(personas_service, "read_store", old_persona_store)
        if context_svc is not None:
            context_svc._get_read_store = old_context_fn


@contextmanager
def management_nl_test_client(
    read_surface: Any = None,
    *,
    raise_server_exceptions: bool = False,
    reset_conversation_store: bool = True,
) -> Iterator[TestClient]:
    """Provide a TestClient wired to the real seam-composed management NL app
    (``compose_bff_app``) with clean store/SSE state -- no main.py import."""
    management_service = get_management_nl_module()
    import services.control_plane.bff.personas.service as personas_service

    old_main_store = management_service.get_read_store()
    old_persona_store = getattr(personas_service, "read_store", None)
    context_svc = getattr(management_service, "_management_ai_context_service", None)
    old_context_fn = getattr(context_svc, "_get_read_store", None) if context_svc is not None else None
    store = read_surface if read_surface is not None else old_main_store

    if reset_conversation_store:
        management_service._MGMT_AI_CONVERSATION_STORE = management_service.ManagementAiConversationStore(
            storage_path="off",
            attachment_store=ManagementAiAttachmentStore(storage_path="off"),
        )
    if "ask" in management_service._sse_buffers:
        management_service._sse_buffers["ask"].clear()

    try:
        management_service.set_read_store(store)
        setattr(personas_service, "read_store", store)
        if context_svc is not None:
            context_svc._get_read_store = (lambda: store) if store is not None else None
        client = TestClient(get_management_nl_app(), raise_server_exceptions=raise_server_exceptions)
        yield client
    finally:
        management_service.set_read_store(old_main_store)
        setattr(personas_service, "read_store", old_persona_store)
        if context_svc is not None:
            context_svc._get_read_store = old_context_fn
        if "ask" in management_service._sse_buffers:
            management_service._sse_buffers["ask"].clear()
