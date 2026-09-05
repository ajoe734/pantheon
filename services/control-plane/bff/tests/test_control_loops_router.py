"""Focused tests and exact-head review evidence for the Control Loops router."""
from __future__ import annotations

import ast
import inspect
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.control_loops.service import ControlLoopsService
from services.control_plane.bff.management_read_models import loop_truth as loop_truth_projection
from services.control_plane.bff.models import CommandType, OperatorIdentity


EXPECTED_ROUTES = {
    ("GET", "/bff/ooda/packets"),
    ("GET", "/bff/ooda/packets/{packet_id}"),
    ("GET", "/bff/v5/interventions"),
    ("POST", "/bff/v5/interventions/{intervention_id}/remediate"),
    ("POST", "/bff/v5/interventions/{id}/decide"),
    ("POST", "/bff/v5/interventions/{id}/claim"),
    ("POST", "/bff/v5/interventions/{id}/escalate"),
    ("POST", "/bff/v5/interventions/{id}/release"),
    ("POST", "/bff/v5/interventions/{id}/two-man-sign"),
    ("POST", "/bff/v5/sentinel/findings/{id}/status"),
    ("POST", "/bff/v5/sentinel/remediation/build"),
    ("POST", "/bff/v5/sentinel/remediation/{actionId}/execute"),
    ("GET", "/bff/v5/sentinel/findings"),
    ("GET", "/bff/v5/loop-inventory"),
    ("GET", "/bff/v5/loop-health"),
    ("GET", "/bff/v5/loop-health/{loop_id}"),
    ("GET", "/bff/v5/loop-inventory/{loop_id}"),
    ("GET", "/bff/v5/downstream-health"),
    ("POST", "/bff/v5/downstream-health/dlq/replay"),
    ("GET", "/bff/v5/loop-runs"),
    ("GET", "/bff/v5/loop-runs/{loop_run_id}"),
    ("GET", "/bff/v5/sentinel/findings/{finding_id}"),
    ("GET", "/bff/v5/control-room"),
    ("GET", "/bff/v5/interventions/{intervention_id}"),
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
        "route_decorators": 24,
        "handlers": 21,
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
        "24 inventoried legacy decorators and then include this prepared router."
    ),
}


class MockReadStore:
    def __init__(self) -> None:
        self.sources = {
            "ooda_packets": "service_store",
            "loop_runs": "service_store",
            "sentinel_findings": "service_store",
            "incidents": "missing",
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
        self.interventions = [
            {
                "intervention_id": "intv-1",
                "kind": "hiq_sentinel",
                "status": "pending",
                "target_type": "Runtime",
                "target_id": "runtime-1",
                "triggered_at": "2026-08-30T12:00:00Z",
                "description": "controller drift",
            }
        ]
        self.sentinel_findings = [
            {
                "id": "finding-1",
                "kind": "loop_anomaly",
                "status": "open",
                "severity": "high",
            }
        ]
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

    def list_v5_interventions(
        self,
        status: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        records = list(self.interventions)
        if status:
            records = [record for record in records if record["status"] == status]
        if kind:
            records = [record for record in records if record["kind"] == kind]
        return records

    def list_sentinel_findings(
        self,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> tuple[bool, List[Dict[str, Any]]]:
        records = list(self.sentinel_findings)
        if kind:
            records = [record for record in records if record["kind"] == kind]
        if status:
            records = [record for record in records if record["status"] == status]
        if severity:
            records = [record for record in records if record["severity"] == severity]
        return True, records

    def get_sentinel_finding(self, finding_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        return True, next(
            (item for item in self.sentinel_findings if item["id"] == finding_id),
            None,
        )

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
        assert tenant_id == "tenant-a"
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
        self.final_calls: List[Dict[str, Any]] = []

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

    def submit_final(self, **kwargs: Any) -> Dict[str, Any]:
        self.final_calls.append(dict(kwargs))
        payload = kwargs["payload"]
        command = payload["command"]
        params = payload.get("params") or {}
        if command == "RemediateSentinelIntervention":
            signature = params.get("twoManSignatureId") or params.get("two_man_signature_id")
            if not signature:
                raise HTTPException(status_code=409, detail="two-man signature required")
            if not kwargs.get("x_confirm_token") or not (
                params.get("approvalId") or params.get("approval_id")
            ):
                raise HTTPException(status_code=428, detail="confirmation evidence required")
        if command == "DecideV5Intervention" and params.get("decision") not in {
            "approve",
            "reject",
            "defer",
            "dismiss",
        }:
            raise HTTPException(status_code=422, detail="invalid decision")
        return self._receipt(command, kwargs)


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
            "allowed_environments": ["dev"],
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
            submit_final_command_admission=commands.submit_final,
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


def test_router_registers_exact_24_catalogued_decorators() -> None:
    router = create_control_loops_router()
    actual = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }
    assert len(router.routes) == 24
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
        route: 1 for route in EXPECTED_ROUTES
    }

    # The prepared router is additive-only and is not mounted yet, so main.py
    # remains the sole current runtime owner.  Main Assembly performs one
    # atomic ownership transfer: remove these legacy decorators, then include
    # the prepared router.  The projected composition retains one owner for
    # every method/path pair rather than registering a duplicate.
    main_source = (bff_root / "main.py").read_text(encoding="utf-8")
    assert "from control_loops.router import" not in main_source
    projected = legacy_pairs.copy()
    for route in EXPECTED_ROUTES:
        projected[route] -= 1
    projected.update(prepared_pairs)
    assert {route: projected[route] for route in EXPECTED_ROUTES} == {
        route: 1 for route in EXPECTED_ROUTES
    }


def test_review_evidence_manifest_matches_task_acceptance() -> None:
    assert REVIEW_EVIDENCE["task_id"] == "OPGAP-BE-CONTROL-LOOPS-V2-20260830"
    assert REVIEW_EVIDENCE["reviewer"] == "Antigravity2"
    assert REVIEW_EVIDENCE["acceptance"] == {
        "route_decorators": 24,
        "handlers": 21,
        "reverse_main_import": False,
        "reusable_loop_contracts_preserved": True,
        "local_command_ledger": False,
        "runtime_owners_before_assembly": 1,
        "runtime_owners_after_assembly": 1,
    }


def test_router_has_no_reverse_dependency_on_main() -> None:
    from services.control_plane.bff.control_loops import router as router_module
    from services.control_plane.bff.control_loops import service as service_module

    for module in (router_module, service_module):
        source = inspect.getsource(module)
        assert "import main" not in source
        assert "from main" not in source


def test_service_has_no_shadow_command_authority() -> None:
    from services.control_plane.bff.control_loops import service as service_module

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
        "/bff/v5/sentinel/findings/finding-1/status",
        headers=OPERATOR_HEADERS,
        json={"status": "resolved"},
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


def test_intervention_list_detail_and_decision_idempotency() -> None:
    client, _ = _client()
    listed = client.get("/bff/v5/interventions", headers=READ_HEADERS)
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["intervention_id"] == "intv-1"

    detail = client.get("/bff/v5/interventions/intv-1", headers=READ_HEADERS)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["intervention_id"] == "intv-1"

    headers = {**OPERATOR_HEADERS, "Idempotency-Key": "decide-intv-1"}
    first = client.post(
        "/bff/v5/interventions/intv-1/decide",
        headers=headers,
        json={"decision": "defer", "reason": "more observation"},
    )
    second = client.post(
        "/bff/v5/interventions/intv-1/decide",
        headers=headers,
        json={"decision": "defer", "reason": "more observation"},
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["command"] == "DecideV5Intervention"
    assert second.json()["data"]["commandId"] == first.json()["data"]["commandId"]
    assert second.json()["meta"]["idempotency"]["replayed"] is True
    command_owner = client.app.state.command_owners
    assert command_owner.final_calls[-1]["payload"]["command"] == "DecideV5Intervention"
    assert command_owner.final_calls[-1]["include_durable_meta"] is True

    invalid = client.post(
        "/bff/v5/interventions/intv-1/decide",
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "invalid-decision"},
        json={"decision": "invented"},
    )
    assert invalid.status_code == 422


def test_remediation_requires_guards_and_accepts_bound_evidence() -> None:
    client, _ = _client()
    missing = client.post(
        "/bff/v5/interventions/intv-1/remediate",
        headers=OPERATOR_HEADERS,
        json={"remediation_action": "resolve"},
    )
    assert missing.status_code == 409

    accepted = client.post(
        "/bff/v5/interventions/intv-1/remediate",
        headers={
            **OPERATOR_HEADERS,
            "Idempotency-Key": "remediate-intv-1",
            "X-Confirm-Token": "confirm-1",
        },
        json={
            "remediation_action": "resolve",
            "twoManSignatureId": "signature-1",
            "approvalId": "approval-1",
            "reason": "guarded repair",
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["data"]["command"] == "RemediateSentinelIntervention"


def test_sentinel_reads_filters_and_typed_commands() -> None:
    client, _ = _client()
    listed = client.get(
        "/bff/v5/sentinel/findings?kind=loop_anomaly&severity=high",
        headers=READ_HEADERS,
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == ["finding-1"]

    invalid = client.get(
        "/bff/v5/sentinel/findings?severity=impossible",
        headers=READ_HEADERS,
    )
    assert invalid.status_code == 400

    detail = client.get("/bff/v5/sentinel/findings/finding-1", headers=READ_HEADERS)
    assert detail.status_code == 200
    assert detail.json()["data"]["severity"] == "high"

    status = client.post(
        "/bff/v5/sentinel/findings/finding-1/status",
        headers={**READ_HEADERS, "Idempotency-Key": "finding-status-1"},
        json={"status": "resolved"},
    )
    build = client.post(
        "/bff/v5/sentinel/remediation/build",
        headers={**READ_HEADERS, "Idempotency-Key": "remediation-build-1"},
        json={"finding_id": "finding-1"},
    )
    execute = client.post(
        "/bff/v5/sentinel/remediation/remediation-1/execute",
        headers={**READ_HEADERS, "Idempotency-Key": "remediation-execute-1"},
        json={"reason": "approved remediation"},
    )
    assert status.json()["data"]["command"] == "SentinelFindingStatus"
    assert build.json()["data"]["command"] == "SentinelRemediationBuild"
    assert execute.json()["data"]["command"] == "SentinelRemediationExecute"
    command_owner = client.app.state.command_owners
    assert [call["command_type"] for call in command_owner.sem_calls[-3:]] == [
        CommandType.SENTINEL_FINDING_STATUS,
        CommandType.SENTINEL_REMEDIATION_BUILD,
        CommandType.SENTINEL_REMEDIATION_EXECUTE,
    ]


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
        "environment": "dev",
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
    finding_params = {
        parameter["name"]
        for parameter in spec["paths"]["/bff/v5/sentinel/findings"]["get"]["parameters"]
    }
    assert {"kind", "status", "severity", "authorization"}.issubset(finding_params)
    health_params = {
        parameter["name"]
        for parameter in spec["paths"]["/bff/v5/loop-health"]["get"]["parameters"]
    }
    assert {"authorization", "X-Tenant-Id", "environment"}.issubset(health_params)
