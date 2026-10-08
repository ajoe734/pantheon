"""Focused tests and exact-head review evidence for the Control Loops router."""
from __future__ import annotations

import ast
import inspect
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


from control_loops.router import create_control_loops_router
from control_loops.service import ControlLoopsService
from management_read_models import loop_truth as loop_truth_projection
from models import OperatorIdentity


EXPECTED_ROUTES = {
    ("GET", "/bff/ooda/packets"),
    ("GET", "/bff/ooda/packets/{packet_id}"),
    ("POST", "/bff/v5/interventions/{id}/two-man-sign"),
    ("GET", "/bff/v5/loop-inventory"),
    ("GET", "/bff/v5/loop-health"),
    ("GET", "/bff/v5/loop-health/{loop_id}"),
    ("GET", "/bff/v5/loop-inventory/{loop_id}"),
    ("GET", "/bff/v5/downstream-health"),
    ("POST", "/bff/v5/downstream-health/dlq/replay"),
    ("GET", "/bff/v5/loop-runs"),
    ("GET", "/bff/v5/loop-runs/{loop_run_id}"),
    ("GET", "/bff/v5/control-room"),
}

READ_HEADERS = {"Authorization": "Bearer reader:viewer:mfa::tenant-a"}
OPERATOR_HEADERS = {
    "Authorization": "Bearer operator:operator,approver,admin:mfa::tenant-a",
    "Idempotency-Key": "control-loops-test-command",
}

REVIEW_EVIDENCE = {
    "task_id": "OPGAP-BE-CONTROL-LOOPS-V2-20260830",
    "owner": "Codex",
    "reviewer": "Antigravity2",
    "owned_layer": "prepared Control Loops domain router and thin read-port adapter",
    "not_changed": [
        "services/control-plane/bff/main.py",
        "services/control-plane/bff/loop_inventory.py",
        "services/control-plane/bff/management_read_models/loop_truth.py",
        "execute-plans",
    ],
    "acceptance": {
        "route_decorators": 12,
        "handlers": 12,
        "reverse_main_import": False,
        "reusable_loop_contracts_preserved": True,
        "local_command_ledger": False,
        "runtime_owners_before_assembly": 1,
        "runtime_owners_after_assembly": 1,
    },
    "verification": [
        ".venv-pantheon/bin/python -m pytest services/control-plane/bff/tests/test_control_loops_router.py -q",
        ".venv-pantheon/bin/python -m py_compile services/control-plane/bff/control_loops/router.py services/control-plane/bff/control_loops/service.py services/control-plane/bff/tests/test_control_loops_router.py",
        "git diff --check",
    ],
    "broader_regression": {
        "result": "95 passed, 6 pre-existing main.py characterization failures",
        "unchanged_paths": [
            "services/control-plane/bff/main.py",
            "services/control-plane/bff/test_v5_interventions.py",
            "services/control-plane/bff/tests/test_bff_path_dedupe.py",
        ],
    },
    "assembly_handoff": (
        "main.py remains the sole current runtime owner; Main Assembly must remove the "
        "12 inventoried legacy decorators and then include this prepared router."
    ),
}


class MockReadStore:
    def __init__(self) -> None:
        self.sources = {
            "ooda_packets": "service_store",
            "loop_runs": "service_store",
            "incidents": "service_store",
        }
        self.ooda_packets = [
            {
                "packet_id": "ooda-2",
                "status": "closed",
                "stage": "learn",
                "strategy_id": "strategy-2",
            },
            {
                "packet_id": "ooda-1",
                "status": "open",
                "stage": "observe",
                "strategy_id": "strategy-1",
                "environment": "paper",
                "act": {"live_capital_side_effects": False},
            },
        ]
        self.incidents = [{"id": "inc-1", "status": "open"}]
        self.loop_runs = [
            {"id": "loop-run-1", "loop_run_id": "loop-run-1", "status": "running"},
            {"id": "loop-run-2", "loop_run_id": "loop-run-2", "status": "completed"},
        ]

    def dataset_source(self, dataset: str) -> str:
        return self.sources.get(dataset, "missing")

    def list_ooda_packets(self, **filters: Any) -> List[Dict[str, Any]]:
        records = list(self.ooda_packets)
        for key, value in filters.items():
            if value is not None:
                records = [record for record in records if record.get(key) == value]
        return records

    def get_ooda_packet(self, packet_id: str) -> Optional[Dict[str, Any]]:
        return next((item for item in self.ooda_packets if item["packet_id"] == packet_id), None)

    def list_incidents(self) -> List[Dict[str, Any]]:
        return list(self.incidents)

    def list_loop_runs(self) -> tuple[bool, List[Dict[str, Any]]]:
        return True, list(self.loop_runs)

    def get_loop_run(self, loop_run_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        return True, next(
            (item for item in self.loop_runs if item["loop_run_id"] == loop_run_id),
            None,
        )

    def loop_run_projection_metadata(self) -> Dict[str, Any]:
        return {
            "schema_version": "pantheon.loop-run-projection.v1",
            "generation": 7,
            "controller": {
                "accepted_live": True,
                "status": "ready",
                "mode": "live",
                "truth_level": "canonical_live",
            },
        }

    def trade_journey_projection_reader(self) -> None:
        return None


class MockLoopTruth:
    @staticmethod
    async def fetch_controller_store_health_records(
        tenant_id: str,
        environment: str,
    ) -> tuple[bool, List[Dict[str, Any]]]:
        assert tenant_id in {"tenant-a", "tenant-dev"}
        # Controller records are keyed by the deployment environment, not the
        # authorized trading stage (BFF-LOOP-HEALTH-CONTROLLER-ENVIRONMENT-20261008).
        assert environment == "dev"
        return False, []

    project_canonical_loop_health = staticmethod(
        loop_truth_projection.project_canonical_loop_health
    )
    project_canonical_loop_health_entry = staticmethod(
        loop_truth_projection.project_canonical_loop_health_entry
    )


class MockDownstreamHealthMonitor:
    def __init__(self) -> None:
        self.replays: List[Dict[str, Any]] = []

    def get_state(self) -> Dict[str, Any]:
        return {"overall_ok": True, "targets": {"telemetry": {"ok": True}}}

    def replay_dead_letters(self, **kwargs: Any) -> Dict[str, Any]:
        self.replays.append(kwargs)
        return {"replayed": 2, **kwargs}


class MockCommandOwners:
    """Test double for the existing canonical command admission owners."""

    def __init__(self) -> None:
        self.receipts: Dict[str, Dict[str, Any]] = {}
        self.sem_calls: List[Dict[str, Any]] = []

    @staticmethod
    def _key(kwargs: Dict[str, Any]) -> str:
        return str(kwargs.get("idempotency_key") or kwargs.get("x_idempotency_key") or "generated")

    def _receipt(self, command: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        key = self._key(kwargs)
        command_value = getattr(command, "value", str(command))
        replayed = key in self.receipts
        if not replayed:
            self.receipts[key] = {
                "status": "accepted",
                "data": {
                    "command": command_value,
                    "commandId": f"cmd-{key}",
                    "command_id": f"cmd-{key}",
                },
                "meta": {"durable": True, "liveCapitalSideEffects": False},
            }
        response = {
            **self.receipts[key],
            "data": dict(self.receipts[key]["data"]),
            "meta": dict(self.receipts[key]["meta"]),
        }
        response["meta"]["idempotency"] = {"key": key, "replayed": replayed}
        return response

    def submit_sem(self, **kwargs: Any) -> Dict[str, Any]:
        self.sem_calls.append(dict(kwargs))
        return self._receipt(kwargs["command_type"], kwargs)



def _extract_identity(authorization: Optional[str]) -> OperatorIdentity:
    token = str(authorization or "")
    is_operator = "operator" in token
    return OperatorIdentity(
        operator_id="operator-1" if is_operator else "reader-1",
        roles=["operator", "approver", "admin", "viewer"] if is_operator else ["viewer"],
        mfa_verified="mfa" in token,
        claims={
            "tenant_id": "tenant-a",
            "allowed_tenants": ["tenant-a"],
            "allowed_environments": ["paper", "broker_sandbox"],
        },
    )


def _client() -> tuple[TestClient, MockDownstreamHealthMonitor]:
    monitor = MockDownstreamHealthMonitor()
    commands = MockCommandOwners()
    service = ControlLoopsService(
        read_store=MockReadStore(),
        loop_truth_adapter=MockLoopTruth,
        downstream_health_monitor=monitor,
        deployed_environment="dev",
    )
    app = FastAPI()
    app.state.command_owners = commands
    app.include_router(
        create_control_loops_router(
            service=service,
            extract_identity=_extract_identity,
            submit_sem_command=commands.submit_sem,
        )
    )
    return TestClient(app), monitor


def _ast_decorated_routes(path: Path, owner: str) -> Counter[tuple[str, str, str]]:
    """Inventory literal FastAPI decorators without importing the composition root."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    routes: Counter[tuple[str, str, str]] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not decorator.args:
                continue
            route_path = decorator.args[0]
            if isinstance(route_path, ast.Constant) and isinstance(route_path.value, str):
                routes[(method, route_path.value, owner)] += 1
    return routes


def test_router_registers_exact_12_catalogued_decorators() -> None:
    router = create_control_loops_router()
    actual = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }
    assert len(router.routes) == 12
    assert actual == EXPECTED_ROUTES


def test_ast_route_inventory_proves_single_owner_across_assembly_handoff() -> None:
    bff_root = Path(__file__).resolve().parents[1]
    prepared = _ast_decorated_routes(
        bff_root / "control_loops" / "router.py", "control_loops.router"
    )
    legacy = _ast_decorated_routes(bff_root / "main.py", "main.py")

    prepared_pairs = Counter(
        {(method, path): count for (method, path, _owner), count in prepared.items()}
    )
    legacy_pairs = Counter(
        {(method, path): count for (method, path, _owner), count in legacy.items()}
    )
    assert prepared_pairs == Counter({route: 1 for route in EXPECTED_ROUTES})
    assert {route: legacy_pairs[route] for route in EXPECTED_ROUTES} == {
        route: 0 for route in EXPECTED_ROUTES
    }

    # Assembly handoff completed: control_loops.router is mounted via core/app_factory.py,
    # and main.py removed all 12 legacy decorators so there is single ownership.
    app_factory_source = (bff_root / "core" / "app_factory.py").read_text(encoding="utf-8")
    assert "create_control_loops_router" in app_factory_source


def test_review_evidence_manifest_matches_task_acceptance() -> None:
    assert REVIEW_EVIDENCE["task_id"] == "OPGAP-BE-CONTROL-LOOPS-V2-20260830"
    assert REVIEW_EVIDENCE["reviewer"] == "Antigravity2"
    assert REVIEW_EVIDENCE["acceptance"] == {
        "route_decorators": 12,
        "handlers": 12,
        "reverse_main_import": False,
        "reusable_loop_contracts_preserved": True,
        "local_command_ledger": False,
        "runtime_owners_before_assembly": 1,
        "runtime_owners_after_assembly": 1,
    }


def test_router_has_no_reverse_dependency_on_main() -> None:
    import control_loops.router as router_module
    import control_loops.service as service_module

    for module in (router_module, service_module):
        source = inspect.getsource(module)
        assert "import main" not in source
        assert "from main" not in source


def test_service_has_no_shadow_command_authority() -> None:
    import control_loops.service as service_module

    source = inspect.getsource(service_module)
    for forbidden in (
        "submit_typed_command",
        "_idempotency_receipts",
        "_FINAL_CONTRACT_IDEMPOTENCY",
        "command_store",
        "prepared_domain_router",
    ):
        assert forbidden not in source


def test_uncomposed_write_route_fails_closed() -> None:
    service = ControlLoopsService(read_store=MockReadStore())
    app = FastAPI()
    app.include_router(
        create_control_loops_router(service=service, extract_identity=_extract_identity)
    )
    response = TestClient(app).post(
        "/bff/v5/interventions/sig-1/two-man-sign",
        headers=OPERATOR_HEADERS,
        json={"twoManSignatureId": "sig-1", "command": "X", "target": {"type": "Runtime", "id": "r1"}},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["details"]["precondition_failed"] == (
        "submit_sem_command"
    )


def test_ooda_list_detail_pagination_and_fail_closed_flag(monkeypatch) -> None:
    client, _ = _client()
    listed = client.get("/bff/ooda/packets?page_size=1", headers=READ_HEADERS)
    assert listed.status_code == 200, listed.text
    assert [item["packet_id"] for item in listed.json()["items"]] == ["ooda-2"]
    assert listed.json()["page_info"] == {"next_page_token": "1", "total": 2}
    assert listed.json()["meta"]["surfaces"]["ooda_packets"]["source"] == "service_store"

    detail = client.get("/bff/ooda/packets/ooda-1", headers=READ_HEADERS)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["packet_id"] == "ooda-1"

    monkeypatch.setenv("PANTHEON_OODA_PACKET_ENABLED", "false")
    disabled = client.get("/bff/ooda/packets", headers=READ_HEADERS)
    assert disabled.status_code == 503








def test_loop_inventory_and_health_preserve_reusable_truth_contracts() -> None:
    client, _ = _client()
    inventory = client.get("/bff/v5/loop-inventory", headers=READ_HEADERS)
    assert inventory.status_code == 200, inventory.text
    assert inventory.json()["meta"]["catalog"]["inventory_counts"]["canonical_loop_count"] == 12
    assert inventory.json()["meta"]["surfaces"]["loop_inventory"]["truth_level"] == "registry_metadata"

    health = client.get("/bff/v5/loop-health", headers=READ_HEADERS)
    assert health.status_code == 200, health.text
    assert len(health.json()["items"]) == 12
    assert health.json()["meta"]["scope"] == {
        "tenant_id": "tenant-a",
        "environment": "paper",
        "controller_environment": "dev",
        "source": "authenticated_identity_and_deployment_scope",
    }
    assert health.json()["meta"]["surfaces"]["loop_health"]["status"] == "degraded"

    detail = client.get("/bff/v5/loop-health/source_ingestion", headers=READ_HEADERS)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["loop_id"] == "source_ingestion"
    assert detail.json()["data"]["controller_health"]["current_record_accepted"] is False


def test_loop_runs_control_room_and_downstream_replay() -> None:
    client, monitor = _client()
    running = client.get("/bff/v5/loop-runs?status=running", headers=READ_HEADERS)
    assert running.status_code == 200, running.text
    assert [item["loop_run_id"] for item in running.json()["items"]] == ["loop-run-1"]
    assert running.json()["meta"]["surfaces"]["loop_runs"]["truth_status"] == "formal"

    detail = client.get("/bff/v5/loop-runs/loop-run-2", headers=READ_HEADERS)
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "completed"

    room = client.get("/bff/v5/control-room", headers=READ_HEADERS)
    assert room.status_code == 200, room.text
    assert room.json()["ooda_status"]["total_packet_count"] == 2
    assert room.json()["meta"]["surfaces"]["control_room"]["status"] == "ok"

    health = client.get("/bff/v5/downstream-health", headers=READ_HEADERS)
    assert health.json()["data"]["overall_ok"] is True

    replay = client.post(
        "/bff/v5/downstream-health/dlq/replay",
        headers=OPERATOR_HEADERS,
        json={
            "approval_ref": "approval-dlq-1",
            "reason": "redrive after downstream recovery",
            "channel": "telemetry",
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["replayed"] == 2
    assert monitor.replays[0]["actor_id"] == "operator-1"


def test_openapi_exposes_control_loop_filter_and_scope_parameters() -> None:
    client, _ = _client()
    spec = client.get("/openapi.json").json()
    health_params = {
        parameter["name"]
        for parameter in spec["paths"]["/bff/v5/loop-health"]["get"]["parameters"]
    }
    assert {"authorization", "X-Tenant-Id", "environment"}.issubset(health_params)


class MockTradeJourneyProjectionReader:
    def __init__(self) -> None:
        self.recorded_calls: List[Dict[str, Any]] = []

    def get_loop_run(
        self,
        *,
        tenant_id: str,
        environment: str,
        loop_run_id: str,
    ) -> Dict[str, Any]:
        self.recorded_calls.append(
            {
                "method": "get_loop_run",
                "tenant_id": tenant_id,
                "environment": environment,
                "loop_run_id": loop_run_id,
            }
        )
        return {
            "id": loop_run_id,
            "loop_run_id": loop_run_id,
            "tenant_id": tenant_id,
            "environment": environment,
            "journey_id": "journey-dev-1",
            "status": "completed_with_variance",
            "source": "postgres_lifecycle_projection",
            "freshness_lineage": {"accepted_live": True, "mode": "live"},
        }

    def controller_freshness(
        self,
        *,
        tenant_id: str,
        environment: str,
    ) -> Dict[str, Any]:
        return {
            "accepted_live": True,
            "status": "ready",
            "mode": "live",
            "truth_level": "canonical_live",
            "deployment_sha": "test-sha",
            "generation": 1,
            "checkpoint": 10,
        }

    def page_loop_runs(
        self,
        *,
        tenant_id: str,
        environment: str,
        statuses: Sequence[str],
        page_size: int,
        page_token: Optional[str],
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        self.recorded_calls.append(
            {
                "method": "page_loop_runs",
                "tenant_id": tenant_id,
                "environment": environment,
            }
        )
        return (
            [
                {
                    "id": "loop-run-dev",
                    "loop_run_id": "loop-run-dev",
                    "tenant_id": tenant_id,
                    "environment": environment,
                    "journey_id": "journey-dev-1",
                    "status": "completed_with_variance",
                    "source": "postgres_lifecycle_projection",
                }
            ],
            None,
        )


def _dev_login_client(
    *,
    deployed_environment: str = "dev",
    served_stages: Optional[Sequence[str]] = None,
    projection_reader: Optional[Any] = None,
) -> TestClient:
    read_store = MockReadStore()
    if projection_reader is not None:
        read_store.trade_journey_projection_reader = lambda: projection_reader
    service = ControlLoopsService(
        read_store=read_store,
        loop_truth_adapter=MockLoopTruth,
        deployed_environment=deployed_environment,
        served_stages=served_stages,
    )

    def extract_dev_login_identity(authorization: Optional[str]) -> OperatorIdentity:
        token = str(authorization or "")
        return OperatorIdentity(
            operator_id="operator_a",
            roles=["operator", "viewer"],
            mfa_verified=True,
            claims={
                "tenant_id": "tenant-dev",
                "allowed_tenants": ["tenant-dev"],
            },
        )

    app = FastAPI()
    app.include_router(
        create_control_loops_router(
            service=service,
            extract_identity=extract_dev_login_identity,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_dev_login_operator_reads_paper_loop_run_and_refuses_live() -> None:
    projection = MockTradeJourneyProjectionReader()
    client = _dev_login_client(projection_reader=projection)
    headers = {"Authorization": "Bearer dev-login-token"}

    resp = client.get(
        "/bff/v5/loop-runs/loop-run-dev?tenant_id=tenant-dev&environment=paper",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["loop_run_id"] == "loop-run-dev"
    assert resp.json()["data"]["environment"] == "paper"

    denied_live = client.get(
        "/bff/v5/loop-runs/loop-run-dev?tenant_id=tenant-dev&environment=live",
        headers=headers,
    )
    assert denied_live.status_code == 403, denied_live.text
    assert (
        denied_live.json()["detail"]["error"]["details"]["precondition_failed"]
        == "environment_scope"
    )

    denied_canary = client.get(
        "/bff/v5/loop-runs/loop-run-dev?tenant_id=tenant-dev&environment=canary",
        headers=headers,
    )
    assert denied_canary.status_code == 403
    assert (
        denied_canary.json()["detail"]["error"]["details"]["precondition_failed"]
        == "environment_scope"
    )

    denied_dev = client.get(
        "/bff/v5/loop-runs/loop-run-dev?tenant_id=tenant-dev&environment=dev",
        headers=headers,
    )
    assert denied_dev.status_code == 403
    assert (
        denied_dev.json()["detail"]["error"]["details"]["precondition_failed"]
        == "environment_scope"
    )

    resp_sandbox = client.get(
        "/bff/v5/loop-runs/loop-run-dev?tenant_id=tenant-dev&environment=broker_sandbox",
        headers=headers,
    )
    assert resp_sandbox.status_code == 200, resp_sandbox.text
    assert resp_sandbox.json()["data"]["environment"] == "broker_sandbox"

    resp_default = client.get(
        "/bff/v5/loop-runs/loop-run-dev?tenant_id=tenant-dev",
        headers=headers,
    )
    assert resp_default.status_code == 200, resp_default.text
    assert resp_default.json()["data"]["environment"] == "paper"

    list_paper = client.get(
        "/bff/v5/loop-runs?tenant_id=tenant-dev&environment=paper",
        headers=headers,
    )
    assert list_paper.status_code == 200, list_paper.text

    detail_surface = resp.json()["meta"]["surfaces"]["loop_run_detail"]
    list_surface = list_paper.json()["meta"]["surfaces"]["loop_runs"]
    for surface in (detail_surface, list_surface):
        assert surface["projection_schema_version"] == "pantheon.trade-journey-projection.v1"
        assert surface["projection_mode"] == "live"
        assert surface["source"] == "postgres_lifecycle_projection"
        assert surface["status"] == "ok"
        assert surface["truth_status"] == "formal"
        assert surface["accepted_live"] is True

    list_live = client.get(
        "/bff/v5/loop-runs?tenant_id=tenant-dev&environment=live",
        headers=headers,
    )
    assert list_live.status_code == 403
    assert (
        list_live.json()["detail"]["error"]["details"]["precondition_failed"]
        == "environment_scope"
    )


def test_identity_with_allowed_environments_retains_declared_stages_and_wildcard() -> None:
    projection = MockTradeJourneyProjectionReader()
    read_store = MockReadStore()
    read_store.trade_journey_projection_reader = lambda: projection
    service = ControlLoopsService(
        read_store=read_store,
        deployed_environment="dev",
    )

    def _client_with_env_claims(allowed_environments: List[str]) -> TestClient:
        def extractor(_auth: Optional[str]) -> OperatorIdentity:
            return OperatorIdentity(
                operator_id="operator_restricted",
                roles=["operator", "viewer"],
                mfa_verified=True,
                claims={
                    "tenant_id": "tenant-dev",
                    "allowed_tenants": ["tenant-dev"],
                    "allowed_environments": allowed_environments,
                },
            )

        app = FastAPI()
        app.include_router(
            create_control_loops_router(
                service=service,
                extract_identity=extractor,
            )
        )
        return TestClient(app, raise_server_exceptions=False)

    client_sandbox = _client_with_env_claims(["broker_sandbox"])
    resp_paper = client_sandbox.get(
        "/bff/v5/loop-runs/loop-run-dev?tenant_id=tenant-dev&environment=paper"
    )
    assert resp_paper.status_code == 403
    assert (
        resp_paper.json()["detail"]["error"]["details"]["precondition_failed"]
        == "environment_scope"
    )
    resp_sb = client_sandbox.get(
        "/bff/v5/loop-runs/loop-run-dev?tenant_id=tenant-dev&environment=broker_sandbox"
    )
    assert resp_sb.status_code == 200

    client_wildcard = _client_with_env_claims(["*"])
    assert (
        client_wildcard.get(
            "/bff/v5/loop-runs/loop-run-dev?tenant_id=tenant-dev&environment=paper"
        ).status_code
        == 200
    )
    assert (
        client_wildcard.get(
            "/bff/v5/loop-runs/loop-run-dev?tenant_id=tenant-dev&environment=broker_sandbox"
        ).status_code
        == 200
    )
    denied_live = client_wildcard.get(
        "/bff/v5/loop-runs/loop-run-dev?tenant_id=tenant-dev&environment=live"
    )
    assert denied_live.status_code == 403
    assert (
        denied_live.json()["detail"]["error"]["details"]["precondition_failed"]
        == "environment_scope"
    )


def test_non_dev_deployment_serves_only_explicitly_configured_stages_or_fails_closed() -> None:
    service_unconfigured = ControlLoopsService(deployed_environment="prod")
    identity = OperatorIdentity(
        operator_id="op-prod",
        roles=["operator", "viewer"],
        mfa_verified=True,
        claims={"tenant_id": "tenant-prod", "allowed_tenants": ["tenant-prod"]},
    )
    for env in ("live", "paper", "broker_sandbox", "canary", None, ""):
        with pytest.raises(Exception) as exc_info:
            service_unconfigured.authenticated_loop_truth_scope(
                identity,
                requested_tenant="tenant-prod",
                requested_environment=env,
            )
        assert getattr(exc_info.value, "status_code", None) == 403

    service_configured = ControlLoopsService(
        deployed_environment="prod",
        served_stages=("live",),
    )
    tenant, stage = service_configured.authenticated_loop_truth_scope(
        identity,
        requested_tenant="tenant-prod",
        requested_environment="live",
    )
    assert (tenant, stage) == ("tenant-prod", "live")

    tenant_default, stage_default = service_configured.authenticated_loop_truth_scope(
        identity,
        requested_tenant="tenant-prod",
        requested_environment=None,
    )
    assert (tenant_default, stage_default) == ("tenant-prod", "live")

    with pytest.raises(Exception) as exc_info:
        service_configured.authenticated_loop_truth_scope(
            identity,
            requested_tenant="tenant-prod",
            requested_environment="paper",
        )
    assert getattr(exc_info.value, "status_code", None) == 403


def test_all_loop_truth_callers_share_stage_scope_authorization() -> None:
    import asyncio

    projection = MockTradeJourneyProjectionReader()
    read_store = MockReadStore()
    read_store.trade_journey_projection_reader = lambda: projection
    service = ControlLoopsService(
        read_store=read_store,
        loop_truth_adapter=MockLoopTruth,
        deployed_environment="dev",
    )
    identity = OperatorIdentity(
        operator_id="op_shared",
        roles=["operator", "viewer"],
        mfa_verified=True,
        claims={"tenant_id": "tenant-dev", "allowed_tenants": ["tenant-dev"]},
    )

    # 1. loop_health (~line 563)
    health = asyncio.run(
        service.loop_health(
            identity,
            requested_tenant="tenant-dev",
            requested_environment="paper",
        )
    )
    assert health["meta"]["scope"]["environment"] == "paper"
    with pytest.raises(Exception):
        asyncio.run(
            service.loop_health(
                identity,
                requested_tenant="tenant-dev",
                requested_environment="live",
            )
        )

    # 2. loop_health_detail (~line 597)
    detail = asyncio.run(
        service.loop_health_detail(
            "source_ingestion",
            identity,
            requested_tenant="tenant-dev",
            requested_environment="paper",
        )
    )
    assert detail["meta"]["scope"]["environment"] == "paper"
    with pytest.raises(Exception):
        asyncio.run(
            service.loop_health_detail(
                "source_ingestion",
                identity,
                requested_tenant="tenant-dev",
                requested_environment="live",
            )
        )

    # 3. list_loop_runs (~line 679)
    runs = asyncio.run(
        service.list_loop_runs(
            identity,
            status=None,
            tenant_id="tenant-dev",
            environment="paper",
            page_token=None,
            page_size=10,
        )
    )
    assert len(runs["items"]) == 1
    with pytest.raises(Exception):
        asyncio.run(
            service.list_loop_runs(
                identity,
                status=None,
                tenant_id="tenant-dev",
                environment="live",
                page_token=None,
                page_size=10,
            )
        )

    # 4. get_loop_run (~line 761)
    run = asyncio.run(
        service.get_loop_run(
            "loop-run-dev",
            identity,
            tenant_id="tenant-dev",
            environment="paper",
        )
    )
    assert run["data"]["environment"] == "paper"
    with pytest.raises(Exception):
        asyncio.run(
            service.get_loop_run(
                "loop-run-dev",
                identity,
                tenant_id="tenant-dev",
                environment="live",
            )
        )



def test_loop_health_reads_controller_records_on_the_deployment_environment() -> None:
    """BFF-LOOP-HEALTH-CONTROLLER-ENVIRONMENT-20261008.

    Every LoopControllerWriter records ``environment`` as the deployment
    environment (PANTHEON_ENV, ``dev`` on the dev VM).  Loop-health authorizes
    the requested trading stage but must query the controller store on the
    deployment environment, otherwise no controller record is ever returned.
    """
    import asyncio

    queried: List[tuple[str, str]] = []
    dev_record = {
        "loop_id": "bff_health_monitoring",
        "tenant_id": "tenant-dev",
        "environment": "dev",
        "_health_source": "controller_store",
    }

    class _StoreKeyedLoopTruth:
        @staticmethod
        async def fetch_controller_store_health_records(
            tenant_id: str,
            environment: str,
        ) -> tuple[bool, List[Dict[str, Any]]]:
            queried.append((tenant_id, environment))
            if (tenant_id, environment) == ("tenant-dev", "dev"):
                return True, [dict(dev_record)]
            return False, []

        project_canonical_loop_health = staticmethod(
            loop_truth_projection.project_canonical_loop_health
        )
        project_canonical_loop_health_entry = staticmethod(
            loop_truth_projection.project_canonical_loop_health_entry
        )

    service = ControlLoopsService(
        loop_truth_adapter=_StoreKeyedLoopTruth,
        deployed_environment="dev",
    )
    identity = OperatorIdentity(
        operator_id="op_controller_env",
        roles=["operator", "viewer"],
        mfa_verified=True,
        claims={"tenant_id": "tenant-dev", "allowed_tenants": ["tenant-dev"]},
    )

    health = asyncio.run(
        service.loop_health(
            identity,
            requested_tenant="tenant-dev",
            requested_environment="paper",
        )
    )
    assert queried == [("tenant-dev", "dev")]
    assert health["meta"]["scope"]["environment"] == "paper"
    assert health["meta"]["scope"]["controller_environment"] == "dev"
    assert health["meta"]["coverage"]["raw_health_record_count"] == 1
    assert health["meta"]["surfaces"]["loop_health"]["source"] == "controller_store"

    detail = asyncio.run(
        service.loop_health_detail(
            "bff_health_monitoring",
            identity,
            requested_tenant="tenant-dev",
            requested_environment="paper",
        )
    )
    assert queried[-1] == ("tenant-dev", "dev")
    assert detail["meta"]["scope"]["controller_environment"] == "dev"
    assert detail["meta"]["coverage"]["raw_health_record_count"] == 1

    # Stage authorization from authenticated_loop_truth_scope is unchanged.
    with pytest.raises(Exception):
        asyncio.run(
            service.loop_health(
                identity,
                requested_tenant="tenant-dev",
                requested_environment="live",
            )
        )
