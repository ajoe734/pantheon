"""BFF read of the Evolution program-qualification report.

``GET /bff/management/evolution-qualification`` is a read-only projection of
the Evolution owner's ``GET /api/evolution/program-qualification``. The
Evolution payload below has the shape the owner produces for the real hosted
infra-component ``flag_for_review`` decision.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.command_adapters.base import ActionUnavailableError
from services.control_plane.bff.evolution.router import create_evolution_router
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

def _jwt(claims: Dict[str, Any]) -> str:
    def part(value: Dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


CALLER_JWT = _jwt({"sub": "op-1", "tenant_id": "tenant-a"})
HEADERS = {"Authorization": "Bearer op-dev:admin:mfa"}

CRITERIA = [
    "real_threshold_signal",
    "real_target",
    "persistent_breach",
    "fresh_evidence",
    "executable_action",
    "no_open_duplicate",
]


def _criterion(name: str, result: str, reason: str = "") -> Dict[str, Any]:
    return {"criterion": name, "result": result, "observed": None, "threshold": None, "reason": reason}


OWNER_REPORT = {
    "tenant_id": "tenant-a",
    "sweep_id": "daily",
    "policy_version": "2026-11-05.1",
    "evaluated_at": "2026-11-05T12:00:00Z",
    "evaluated": 1,
    "qualified": 0,
    "drafted": 0,
    "counts_by_first_failing_criterion": {"real_threshold_signal": 1},
    "failure_reason": None,
    "items": [
        {
            "decision_id": "evo-sweep-agent-000b205c0ef9-0000",
            "target_id": "infra-component-agent-finding-000b205c0ef9",
            "action_type": "flag_for_review",
            "metric_name": "incident_followup_required",
            "qualified": False,
            "first_failing_criterion": "real_threshold_signal",
            "criteria": [
                _criterion("real_threshold_signal", "failed", "signal_type manual_review is not allowed"),
                *[_criterion(name, "not_evaluated") for name in CRITERIA[1:4]],
                _criterion("executable_action", "failed", "action_type has no research execution path"),
                _criterion("no_open_duplicate", "passed"),
            ],
            "program_id": None,
            "drafted": False,
        }
    ],
    "total": 1,
    "limit": 50,
    "offset": 0,
}


def _client(reader=None) -> TestClient:
    ports = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"evolution_qualification_reader": reader}
    )
    app = FastAPI()
    app.include_router(create_evolution_router(read_surface=ports))
    return TestClient(app)


def test_returns_owner_report_with_every_criterion_per_decision() -> None:
    seen: List[Dict[str, Any]] = []

    def reader(query: Dict[str, Any]) -> Dict[str, Any]:
        seen.append(dict(query))
        return dict(OWNER_REPORT)

    response = _client(reader).get("/bff/management/evolution-qualification", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["policy_version"] == "2026-11-05.1"
    assert (data["evaluated"], data["qualified"], data["drafted"]) == (1, 0, 0)
    assert data["counts_by_first_failing_criterion"] == {"real_threshold_signal": 1}
    item = data["items"][0]
    assert item["first_failing_criterion"] == "real_threshold_signal"
    assert [c["criterion"] for c in item["criteria"]] == CRITERIA
    assert seen == [{"limit": 50, "offset": 0}]


def test_paging_and_qualified_filter_are_forwarded_to_the_owner() -> None:
    seen: List[Dict[str, Any]] = []
    client = _client(lambda query: seen.append(dict(query)) or dict(OWNER_REPORT))
    response = client.get(
        "/bff/management/evolution-qualification",
        params={"page_size": 10, "offset": 20, "qualified": "false"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert seen == [{"limit": 10, "offset": 20, "qualified": "false"}]
    assert client.get(
        "/bff/management/evolution-qualification", params={"page_size": 500}, headers=HEADERS
    ).status_code == 422


def test_requires_an_authenticated_reader() -> None:
    from fastapi import HTTPException

    def strict_identity(authorization: Optional[str] = None) -> Any:
        if not authorization:
            raise HTTPException(status_code=401, detail="authentication required")
        return object()

    ports = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"evolution_qualification_reader": lambda query: dict(OWNER_REPORT)}
    )
    app = FastAPI()
    app.include_router(create_evolution_router(read_surface=ports, extract_identity=strict_identity))
    client = TestClient(app)
    assert client.get("/bff/management/evolution-qualification").status_code == 401
    assert client.get("/bff/management/evolution-qualification", headers=HEADERS).status_code == 200


def test_unconfigured_or_failing_owner_is_a_503_not_an_empty_report() -> None:
    assert _client(None).get("/bff/management/evolution-qualification", headers=HEADERS).status_code == 503

    def failing(query: Dict[str, Any]) -> Dict[str, Any]:
        raise ActionUnavailableError("Evolution owner unavailable", downstream_status=503)

    assert _client(failing).get("/bff/management/evolution-qualification", headers=HEADERS).status_code == 503

    def malformed(query: Dict[str, Any]):  # type: ignore[no-untyped-def]
        return ["not", "an", "object"]

    assert _client(malformed).get("/bff/management/evolution-qualification", headers=HEADERS).status_code == 503


def test_owner_object_read_carries_the_callers_identity_and_query(monkeypatch) -> None:
    from services.control_plane.bff.core import owner_reads

    calls: List[Dict[str, Any]] = []

    def fake_owner_request(url: str, *, caller_token: str, tenant_id: str) -> Any:
        calls.append({"url": url, "caller_token": caller_token, "tenant_id": tenant_id})
        return dict(OWNER_REPORT)

    monkeypatch.setattr(owner_reads, "evolution_owner_request", fake_owner_request)
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", "evolution-service-token")
    auth_token = owner_reads.authorization.set("Bearer " + CALLER_JWT)
    tenant_token = owner_reads.selected_tenant.set("tenant-a")
    try:
        report = owner_reads.read_evolution_object(
            lambda path: "http://evolution:8093" + path,
            "/api/evolution/program-qualification",
            {"limit": 10, "offset": 0},
        )
    finally:
        owner_reads.authorization.reset(auth_token)
        owner_reads.selected_tenant.reset(tenant_token)
    assert report["policy_version"] == "2026-11-05.1"
    assert calls[0]["url"] == "http://evolution:8093/api/evolution/program-qualification?limit=10&offset=0"
    assert calls[0]["caller_token"] == CALLER_JWT
    assert calls[0]["tenant_id"] == "tenant-a"


def test_report_with_excluded_infrastructure_field_reads_through() -> None:
    report = {**OWNER_REPORT, "excluded_infrastructure": 46}
    response = _client(lambda query: dict(report)).get(
        "/bff/management/evolution-qualification", headers=HEADERS
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["evaluated"] == 1
    assert data.get("excluded_infrastructure") == 46


def test_older_report_without_excluded_infrastructure_still_reads() -> None:
    assert "excluded_infrastructure" not in OWNER_REPORT
    response = _client(lambda query: dict(OWNER_REPORT)).get(
        "/bff/management/evolution-qualification", headers=HEADERS
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["evaluated"] == 1


# --- BFF-EVO-JOURNAL-POOL-VERSIONS-20261125: member baselines on the qualification page ---

PB_PERSONA = "persona-ee7112a829d38b9135ac"
PB_MEMBER = "artifact-persona-paper-a81b4fd15944698341aa"
PB_CHECKSUM = "sha256:" + "a" * 64


class _Ident:
    operator_id = "op-1"
    roles = {"viewer"}
    claims = {"tenant_id": "tenant-dev"}


def _pool_version(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"persona_id": PB_PERSONA, "tenant_id": "tenant-dev", "pool_version": 6, "members": members,
            "digest": "sha256:" + "6" * 64, "decision": {"action": "reweight", "status": "refused", "reasons": []}}


def _baseline_client(registry_reader, members=None, tenant="tenant-dev"):
    members = members if members is not None else [
        {"artifact_ref": PB_MEMBER, "checksum": PB_CHECKSUM, "stage": "active", "risk_weight": 1.0, "baseline_ref": None},
        {"artifact_ref": "artifact-removed", "checksum": PB_CHECKSUM, "stage": "removed", "risk_weight": 0.0, "baseline_ref": None},
    ]
    ports = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={
            "evolution_qualification_reader": lambda q: dict(OWNER_REPORT),
            "personas": [
                {"persona_id": PB_PERSONA, "name": "Paper One", "lifecycle_state": "paper_owner", "tenant_id": tenant},
                {"persona_id": "persona-other-tenant", "name": "Other", "lifecycle_state": "paper_owner", "tenant_id": "tenant-x"},
            ],
        },
        persona_pool_versions_reader=lambda pid, tid: {"versions": [_pool_version(members)]},
        registry_entry_reader=registry_reader,
    )
    app = FastAPI()
    app.include_router(create_evolution_router(read_surface=ports, extract_identity=lambda authorization=None: _Ident()))
    return TestClient(app)


def test_member_baselines_pass_the_resolver_reason_through_for_each_live_member() -> None:
    calls: List[Any] = []

    def registry(registry_id: str, tenant_id: str) -> Any:
        calls.append((registry_id, tenant_id))
        return {"registry_id": registry_id, "artifact_state": "candidate", "checksum": PB_CHECKSUM}

    data = _baseline_client(registry).get("/bff/management/evolution-qualification", headers=HEADERS).json()["data"]
    assert calls == [(PB_MEMBER, "tenant-dev")]  # removed member and other tenant never read
    assert data["member_baselines"] == [{
        "persona_id": PB_PERSONA, "persona_name": "Paper One", "artifact_ref": PB_MEMBER, "stage": "active",
        "risk_weight": 1.0, "pool_version": 6, "status": "no_approved_baseline", "reason": "artifact_not_approved",
    }]
    surface = data["member_baselines_surface"]
    assert (surface["status"], surface["not_approved"], surface["approved"]) == ("ok", 1, 0)
    assert surface["reads"]["registry_reads"] == 1 and surface["reads"]["pool_version_reads"] == 1
    assert data["items"][0]["first_failing_criterion"] == "real_threshold_signal"  # report untouched


def test_member_baseline_missing_entry_and_unreadable_entry() -> None:
    missing = _baseline_client(lambda rid, tid: {}).get("/bff/management/evolution-qualification", headers=HEADERS)
    row = missing.json()["data"]["member_baselines"][0]
    assert (row["status"], row["reason"]) == ("no_approved_baseline", "registry_entry_missing")

    def broken(rid: str, tid: str) -> Any:
        raise RuntimeError("registry down")

    unreadable = _baseline_client(broken).get("/bff/management/evolution-qualification", headers=HEADERS).json()["data"]
    row = unreadable["member_baselines"][0]
    assert row["status"] == "unavailable" and row["reason"].startswith("registry_entry_unreadable:")
    assert unreadable["member_baselines_surface"]["unavailable"] == 1
    assert "oos_sharpe" not in row  # no number is written or defaulted


def test_member_baselines_pool_owner_failure_is_unavailable_not_empty_success() -> None:
    ports = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={
            "evolution_qualification_reader": lambda q: dict(OWNER_REPORT),
            "personas": [{"persona_id": PB_PERSONA, "name": "P", "lifecycle_state": "paper_owner", "tenant_id": "tenant-dev"}],
        },
        persona_pool_versions_reader=lambda pid, tid: (_ for _ in ()).throw(RuntimeError("down")),
        registry_entry_reader=lambda rid, tid: {},
    )
    app = FastAPI()
    app.include_router(create_evolution_router(read_surface=ports, extract_identity=lambda authorization=None: _Ident()))
    data = TestClient(app).get("/bff/management/evolution-qualification", headers=HEADERS).json()["data"]
    assert data["member_baselines"] == []
    assert data["member_baselines_surface"]["status"] == "unavailable"
    assert data["evaluated"] == 1
