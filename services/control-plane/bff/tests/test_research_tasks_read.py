"""Contract and isolation tests for GET /bff/research-tasks and GET /bff/research-tasks/{task_id}.

Task: RESEARCH-TASKS-READ-20261104
Covers AC1-AC10 using real hosted task and run shapes, real BFF JWT caller
authentication and OwnerReadContextMiddleware, and real urllib transport hooks.
"""
from __future__ import annotations

import ast
import base64
from copy import deepcopy
import hashlib
import hmac
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.core.app_factory import build_bff_app
from services.control_plane.bff.research.router import (
    create_research_router,
    create_research_tasks_router,
)
from services.control_plane.bff.research.routes import tasks as tasks_route_module

TEST_JWT_SECRET = "research-tasks-read-jwt-secret-20261104"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _mint_jwt(
    *,
    sub: str = "op-research-reader",
    tenant_id: str = "tenant-dev",
    roles: Tuple[str, ...] = ("operator",),
) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    payload = _b64url(
        json.dumps(
            {
                "sub": sub,
                "operator_id": sub,
                "tenant_id": tenant_id,
                "roles": list(roles),
                "iss": "https://auth.pantheon.internal",
                "aud": "pantheon-bff",
                "exp": 4102444800,
            }
        ).encode("utf-8")
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    sig = _b64url(hmac.new(TEST_JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def _auth_headers(tenant_id: str = "tenant-dev", *, header_tenant_id: Optional[str] = None) -> Dict[str, str]:
    token = _mint_jwt(sub=f"op-{tenant_id}", tenant_id=tenant_id)
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": header_tenant_id if header_tenant_id is not None else tenant_id,
    }


HOSTED_TASK_CONSTRAINTS_TENANT: Dict[str, Any] = {
    "id": "rtask-20261023-008",
    "task_id": "rtask-20261023-008",
    "title": "Alpha replication for reg-strategy-spec-persona-aa060d53ff712cbba704",
    "objective": "Replicate alpha signal on TW equity universe",
    "status": "completed",
    "created_by": "agora-research-service",
    "created_at": "2026-10-23T08:00:00Z",
    "updated_at": "2026-10-23T08:05:00Z",
    "idempotency_key": "idem-rtask-20261023-008",
    "source_refs": ["reg-strategy-spec-persona-aa060d53ff712cbba704"],
    "constraints": {
        "tenant_id": "tenant-dev",
        "max_runtime_seconds": 600,
    },
}

HOSTED_TASK_TOP_LEVEL_TENANT_NO_TITLE: Dict[str, Any] = {
    "id": "rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b",
    "task_id": "rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b",
    "title": None,
    "task_type": "rapid_eval",
    "tenant_id": "tenant-dev",
    "objective": "Rapid evaluation for strategy candidate 30bcef4d067e4c0111fc28cfe8101a7b",
    "status": "ready",
    "created_by": "training-session-svc",
    "created_at": "2026-10-24T10:00:00Z",
    "updated_at": "2026-10-24T10:00:00Z",
    "idempotency_key": "idem-rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b",
    "source_refs": [],
    "constraints": {},
}

HOSTED_TASK_UNSCOPED_NEITHER_TENANT: Dict[str, Any] = {
    "id": "rtask-20261016-003",
    "task_id": "rtask-20261016-003",
    "title": "Plan ad64f196-7b01-4c22-9a10-003",
    "objective": "Execute legacy unscoped research plan ad64f196",
    "status": "running",
    "created_by": "operator",
    "created_at": "2026-10-16T12:00:00Z",
    "updated_at": "2026-10-16T12:01:00Z",
    "idempotency_key": "idem-rtask-20261016-003",
    "source_refs": [],
    "constraints": {
        "max_runtime_seconds": 300,
    },
}

HOSTED_TASK_FALLBACK_TO_TASK_ID: Dict[str, Any] = {
    "id": "rtask-20261022-blank-name",
    "task_id": "rtask-20261022-blank-name",
    "title": "   ",
    "objective": "",
    "tenant_id": "tenant-dev",
    "status": "queued",
    "created_by": "operator",
    "created_at": "2026-10-22T09:00:00Z",
    "updated_at": "2026-10-22T09:02:00Z",
    "idempotency_key": "idem-rtask-20261022-blank-name",
    "source_refs": [],
    "constraints": {"tenant_id": "tenant-dev"},
}

HOSTED_TASK_OTHER_TENANT: Dict[str, Any] = {
    "id": "rtask-20261025-tenant-b",
    "task_id": "rtask-20261025-tenant-b",
    "title": "Tenant B isolated research task",
    "objective": "Verify cross-tenant isolation",
    "tenant_id": "tenant-b",
    "status": "completed",
    "created_by": "operator-b",
    "created_at": "2026-10-25T11:00:00Z",
    "updated_at": "2026-10-25T11:10:00Z",
    "idempotency_key": "idem-rtask-20261025-tenant-b",
    "source_refs": [],
    "constraints": {"tenant_id": "tenant-b"},
}


def _make_hosted_run(
    *,
    run_id: str,
    task_id: str,
    adapter: Optional[str],
    dispatch_mode: str,
    requested_mode: str,
    status: str,
    created_at: str,
) -> Dict[str, Any]:
    return {
        "id": run_id,
        "run_id": run_id,
        "task_id": task_id,
        "adapter": adapter,
        "dispatch_mode": dispatch_mode,
        "requested_mode": requested_mode,
        "status": status,
        "created_by": "research-orchestrator",
        "created_at": created_at,
        "parameters": {"symbol": "2330.TW"},
        "input_refs": ["dataset:tw-equities-daily"],
        "artifact_refs": [],
        "proposal_refs": [],
        "registry_writebacks": [],
        "production_activation": False,
        "rejection": None,
        "events": [{"event_type": "run_created", "timestamp": created_at}],
    }


HOSTED_RUNS: List[Dict[str, Any]] = [
    _make_hosted_run(
        run_id="rrun-20261023-001",
        task_id="rtask-20261023-008",
        adapter="manual",
        dispatch_mode="manual",
        requested_mode="manual",
        status="completed",
        created_at="2026-10-23T08:01:00Z",
    ),
    _make_hosted_run(
        run_id="rrun-20261023-002",
        task_id="rtask-20261023-008",
        adapter="vectorbt",
        dispatch_mode="real",
        requested_mode="real",
        status="completed",
        created_at="2026-10-23T08:04:00Z",
    ),
    _make_hosted_run(
        run_id="rrun-20261024-stub",
        task_id="rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b",
        adapter="stub",
        dispatch_mode="stub",
        requested_mode="stub",
        status="canceled",
        created_at="2026-10-24T10:01:00Z",
    ),
    _make_hosted_run(
        run_id="rrun-20261022-null-adapter",
        task_id="rtask-20261022-blank-name",
        adapter=None,
        dispatch_mode="manual",
        requested_mode="manual",
        status="completed",
        created_at="2026-10-22T09:01:00Z",
    ),
    _make_hosted_run(
        run_id="rrun-20261016-unscoped",
        task_id="rtask-20261016-003",
        adapter="stub",
        dispatch_mode="stub",
        requested_mode="stub",
        status="canceled",
        created_at="2026-10-16T12:01:00Z",
    ),
    _make_hosted_run(
        run_id="rrun-20261025-tenant-b",
        task_id="rtask-20261025-tenant-b",
        adapter="vectorbt",
        dispatch_mode="real",
        requested_mode="real",
        status="completed",
        created_at="2026-10-25T11:05:00Z",
    ),
]


class _FakeExperimentReadStore:
    def __init__(self) -> None:
        self.experiments: Dict[str, Dict[str, Any]] = {
            "exp-existing-001": {
                "experiment_id": "exp-existing-001",
                "experiment_name": "Legacy Queued Experiment",
                "status": "queued",
                "queued_at": "2026-10-01T00:00:00Z",
                "created_by": "op-dev",
            }
        }

    def dataset_source(self, _dataset: str) -> str:
        return "typed_store"

    def list_research_experiments(self, **_filters: Any) -> List[Dict[str, Any]]:
        return list(self.experiments.values())

    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.experiments.get(str(experiment_id or ""))

    def create_experiment_bff(
        self,
        *,
        name: str,
        actor_id: str,
        created_at: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        del params
        exp_id = f"exp-{len(self.experiments) + 1:03d}"
        record = {
            "experiment_id": exp_id,
            "experiment_name": name,
            "status": "queued",
            "queued_at": created_at or "2026-11-04T00:00:00Z",
            "created_by": actor_id,
        }
        self.experiments[exp_id] = record
        return {
            "id": exp_id,
            "experiment_id": exp_id,
            "name": name,
            "status": "queued",
            "created_at": record["queued_at"],
        }

    def list_research_analyses(self, **_filters: Any) -> List[Dict[str, Any]]:
        return []


@pytest.fixture(autouse=True)
def _configure_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "enforced")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.delenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", raising=False)
    monkeypatch.delenv("RESEARCH_ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("RESEARCH_ORCHESTRATOR_API_URL", raising=False)


def _build_client(store: Optional[_FakeExperimentReadStore] = None) -> TestClient:
    read_store = store or _FakeExperimentReadStore()
    app = build_bff_app()
    app.include_router(
        create_research_router(
            get_read_store=lambda: read_store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-11-04T01:00:00Z",
        )
    )
    return TestClient(app)


def _install_owner_urllib_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tasks: Optional[List[Dict[str, Any]]] = None,
    runs: Optional[List[Dict[str, Any]]] = None,
    fail_tasks: bool = False,
    fail_runs: bool = False,
    demands: Optional[Any] = None,
    demands_http_status: Optional[int] = None,
) -> List[Dict[str, Any]]:
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research-orchestrator.internal:8101")
    task_records = deepcopy(
        tasks
        if tasks is not None
        else [
            HOSTED_TASK_CONSTRAINTS_TENANT,
            HOSTED_TASK_TOP_LEVEL_TENANT_NO_TITLE,
            HOSTED_TASK_UNSCOPED_NEITHER_TENANT,
            HOSTED_TASK_FALLBACK_TO_TASK_ID,
            HOSTED_TASK_OTHER_TENANT,
        ]
    )
    run_records = deepcopy(runs if runs is not None else HOSTED_RUNS)
    by_task_id = {str(item["task_id"]): item for item in task_records}
    calls: List[Dict[str, Any]] = []

    class _Resp:
        def __init__(self, payload: Any, status: int = 200) -> None:
            self.status = status
            self.headers: Dict[str, str] = {"Content-Type": "application/json"}
            self._raw = json.dumps(payload).encode("utf-8")

        def read(self) -> bytes:
            return self._raw

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *exc: Any) -> bool:
            return False

    def _fake_urlopen(req: urllib.request.Request, timeout: Optional[float] = None) -> _Resp:
        url = req.full_url
        headers = {k.lower(): v for k, v in req.header_items()}
        calls.append({"url": url, "headers": headers, "timeout": timeout})

        if "/api/research/persona-demands" in url:
            if demands_http_status is not None:
                raise urllib.error.HTTPError(url, demands_http_status, "denied", hdrs=None, fp=io.BytesIO(b"{}"))
            return _Resp(demands if demands is not None else {"demands": [], "count": 0})

        if url.endswith("/api/research-orchestrator/tasks"):
            if fail_tasks:
                raise urllib.error.HTTPError(url, 503, "Service Unavailable", hdrs=None, fp=io.BytesIO(b"{}"))
            return _Resp(task_records)

        if "/api/research-orchestrator/tasks/" in url:
            if fail_tasks:
                raise urllib.error.HTTPError(url, 503, "Service Unavailable", hdrs=None, fp=io.BytesIO(b"{}"))
            task_id = url.rsplit("/api/research-orchestrator/tasks/", 1)[-1]
            if task_id not in by_task_id:
                raise urllib.error.HTTPError(
                    url,
                    404,
                    "Not Found",
                    hdrs=None,
                    fp=io.BytesIO(b'{"detail":"research task not found"}'),
                )
            return _Resp(by_task_id[task_id])

        if "/api/research-orchestrator/runs" in url:
            if fail_runs:
                raise urllib.error.HTTPError(url, 503, "Service Unavailable", hdrs=None, fp=io.BytesIO(b"{}"))
            if "?task_id=" in url:
                target_task_id = url.split("?task_id=", 1)[-1]
                filtered = [r for r in run_records if r.get("task_id") == target_task_id]
                return _Resp(filtered)
            return _Resp(run_records)

        raise AssertionError(f"Unexpected owner URL requested: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    return calls


def test_tasks_route_module_uses_sync_handlers_and_no_new_http_client_or_file_reads() -> None:
    source_path = Path(tasks_route_module.__file__).resolve()
    source_text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source_text, filename=str(source_path))

    class_defs = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    async_defs = [node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]

    assert class_defs == [], f"routes/tasks.py must not introduce a new HTTP client class: {class_defs}"
    assert async_defs == [], f"routes/tasks.py handlers must be sync def, found async def: {async_defs}"
    assert "RESEARCH_ORCHESTRATOR_DATA_DIR" not in source_text
    assert "tasks.json" not in source_text
    assert "runs.json" not in source_text


def test_list_research_tasks_projects_hosted_shapes_name_fallback_and_stub_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_owner_urllib_stub(monkeypatch)
    client = _build_client()

    resp = client.get("/bff/research-tasks", params={"include_non_real": "true"}, headers=_auth_headers("tenant-dev"))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["meta"]["status"] == "ok"
    assert body["meta"]["excluded_unscoped_count"] == 1
    assert body["meta"]["surfaces"]["research_tasks"]["status"] == "ok"
    assert body["meta"]["surfaces"]["research_tasks"]["excluded_unscoped_count"] == 1

    # Exactly 2 owner HTTP calls: one for tasks and one for runs (not 1 + N).
    assert [c["url"] for c in calls] == [
        "http://research-orchestrator.internal:8101/api/research-orchestrator/tasks",
        "http://research-orchestrator.internal:8101/api/research-orchestrator/runs",
    ]
    for call in calls:
        assert call["headers"].get("x-tenant-id") == "tenant-dev"
        assert call["headers"].get("authorization", "").startswith("Bearer ")

    items = body["items"]
    assert body["data"] == items
    assert [item["task_id"] for item in items] == [
        "rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b",
        "rtask-20261023-008",
        "rtask-20261022-blank-name",
    ]

    by_id = {item["task_id"]: item for item in items}

    # 1. Task with top-level tenant_id and no title: name falls back to objective; status stays 'ready';
    # stub run reports real adapter='stub' and dispatch_mode='stub'.
    rapid_eval = by_id["rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b"]
    assert rapid_eval["id"] == "rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b"
    assert rapid_eval["name"] == "Rapid evaluation for strategy candidate 30bcef4d067e4c0111fc28cfe8101a7b"
    assert rapid_eval["objective"] == "Rapid evaluation for strategy candidate 30bcef4d067e4c0111fc28cfe8101a7b"
    assert rapid_eval["status"] == "ready"
    assert rapid_eval["created_by"] == "training-session-svc"
    assert rapid_eval["created_at"] == "2026-10-24T10:00:00Z"
    assert rapid_eval["updated_at"] == "2026-10-24T10:00:00Z"
    assert rapid_eval["run_count"] == 1
    assert rapid_eval["latest_run"] == {
        "run_id": "rrun-20261024-stub",
        "adapter": "stub",
        "dispatch_mode": "stub",
        "status": "canceled",
        "requested_mode": "stub",
        "created_at": "2026-10-24T10:01:00Z",
    }

    # 2. Task with constraints.tenant_id and title: name uses title; 2 runs -> latest_run is rrun-20261023-002.
    alpha_task = by_id["rtask-20261023-008"]
    assert alpha_task["name"] == "Alpha replication for reg-strategy-spec-persona-aa060d53ff712cbba704"
    assert alpha_task["status"] == "completed"
    assert alpha_task["run_count"] == 2
    assert alpha_task["latest_run"]["run_id"] == "rrun-20261023-002"
    assert alpha_task["latest_run"]["adapter"] == "vectorbt"
    assert alpha_task["latest_run"]["dispatch_mode"] == "real"
    assert alpha_task["latest_run"]["status"] == "completed"

    # 3. Task with blank title and blank objective: name falls back to task_id; null adapter preserved.
    fallback_task = by_id["rtask-20261022-blank-name"]
    assert fallback_task["name"] == "rtask-20261022-blank-name"
    assert fallback_task["run_count"] == 1
    assert fallback_task["latest_run"]["run_id"] == "rrun-20261022-null-adapter"
    assert fallback_task["latest_run"]["adapter"] is None
    assert fallback_task["latest_run"]["dispatch_mode"] == "manual"


def test_two_tenant_isolation_and_unscoped_exclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_owner_urllib_stub(monkeypatch)
    client = _build_client()

    resp_dev = client.get("/bff/research-tasks", params={"include_non_real": "true"}, headers=_auth_headers("tenant-dev"))
    assert resp_dev.status_code == 200
    dev_ids = {item["task_id"] for item in resp_dev.json()["items"]}
    assert dev_ids == {
        "rtask-20261023-008",
        "rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b",
        "rtask-20261022-blank-name",
    }
    assert "rtask-20261016-003" not in dev_ids
    assert "rtask-20261025-tenant-b" not in dev_ids
    assert resp_dev.json()["meta"]["excluded_unscoped_count"] == 1

    resp_b = client.get("/bff/research-tasks", params={"include_non_real": "true"}, headers=_auth_headers("tenant-b"))
    assert resp_b.status_code == 200
    b_ids = {item["task_id"] for item in resp_b.json()["items"]}
    assert b_ids == {"rtask-20261025-tenant-b"}
    assert "rtask-20261016-003" not in b_ids
    assert resp_b.json()["meta"]["excluded_unscoped_count"] == 1

    # Mismatched X-Tenant-Id header vs JWT claim fails closed with 403.
    resp_mismatch = client.get(
        "/bff/research-tasks",
        headers=_auth_headers("tenant-dev", header_tenant_id="tenant-b"),
    )
    assert resp_mismatch.status_code == 403


def test_get_research_task_detail_returns_all_runs_and_non_disclosing_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_owner_urllib_stub(monkeypatch)
    client = _build_client()

    detail_resp = client.get(
        "/bff/research-tasks/rtask-20261023-008",
        headers=_auth_headers("tenant-dev"),
    )
    assert detail_resp.status_code == 200, detail_resp.text
    detail_body = detail_resp.json()
    task_data = detail_body["data"]
    assert task_data["task_id"] == "rtask-20261023-008"
    assert task_data["run_count"] == 2
    assert [r["run_id"] for r in task_data["runs"]] == [
        "rrun-20261023-001",
        "rrun-20261023-002",
    ]
    assert task_data["runs"][0]["adapter"] == "manual"
    assert task_data["runs"][1]["adapter"] == "vectorbt"

    # Missing task, task of another tenant, and unscoped task return the same non-disclosing 404.
    missing_resp = client.get(
        "/bff/research-tasks/rtask-20261025-tenant-b",
        headers=_auth_headers("tenant-dev"),
    )
    truly_missing_resp = client.get(
        "/bff/research-tasks/rtask-20261025-tenant-b",
        headers=_auth_headers("tenant-dev"),
    )
    other_tenant_resp = client.get(
        "/bff/research-tasks/rtask-20261025-tenant-b",
        headers=_auth_headers("tenant-dev"),
    )
    nonexistent_resp = client.get(
        "/bff/research-tasks/rtask-nonexistent-999",
        headers=_auth_headers("tenant-dev"),
    )
    unscoped_resp = client.get(
        "/bff/research-tasks/rtask-20261016-003",
        headers=_auth_headers("tenant-dev"),
    )
    del missing_resp, truly_missing_resp

    assert other_tenant_resp.status_code == 404
    assert nonexistent_resp.status_code == 404
    assert unscoped_resp.status_code == 404

    other_err = other_tenant_resp.json()["error"]
    nonexistent_err = nonexistent_resp.json()["error"]
    unscoped_err = unscoped_resp.json()["error"]

    assert other_err["code"] == "RESOURCE_NOT_FOUND"
    assert nonexistent_err["code"] == "RESOURCE_NOT_FOUND"
    assert unscoped_err["code"] == "RESOURCE_NOT_FOUND"
    assert other_err["message"] == nonexistent_err["message"] == unscoped_err["message"] == "Research task not found"
    assert other_err["i18nKey"] == nonexistent_err["i18nKey"] == unscoped_err["i18nKey"]


def test_unconfigured_orchestrator_url_returns_surface_unavailable() -> None:
    client = _build_client()
    resp = client.get("/bff/research-tasks", params={"include_non_real": "true"}, headers=_auth_headers("tenant-dev"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["meta"]["status"] == "unavailable"
    assert body["meta"]["surfaces"]["research_tasks"]["status"] == "unavailable"
    assert body["meta"]["surfaces"]["research_tasks"]["source"] == "unconfigured"


def test_owner_read_failure_returns_surface_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_owner_urllib_stub(monkeypatch, fail_tasks=True)
    client = _build_client()
    resp = client.get("/bff/research-tasks", params={"include_non_real": "true"}, headers=_auth_headers("tenant-dev"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["meta"]["status"] == "unavailable"
    assert body["meta"]["surfaces"]["research_tasks"]["status"] == "unavailable"


def test_healthy_owner_with_zero_tasks_returns_surface_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_owner_urllib_stub(monkeypatch, tasks=[], runs=[])
    client = _build_client()
    resp = client.get("/bff/research-tasks", params={"include_non_real": "true"}, headers=_auth_headers("tenant-dev"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["page_info"]["total"] == 0
    assert body["meta"]["status"] == "ok"
    assert body["meta"]["surfaces"]["research_tasks"]["status"] == "ok"


def test_list_pagination_and_single_runs_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_owner_urllib_stub(monkeypatch)
    client = _build_client()

    page1 = client.get(
        "/bff/research-tasks",
        params={"page_size": 2, "include_non_real": "true"},
        headers=_auth_headers("tenant-dev"),
    )
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 2
    assert body1["page_info"]["total"] == 3
    assert body1["page_info"]["next_page_token"] == "2"
    assert body1["next_page_token"] == "2"
    assert len(calls) == 2

    page2 = client.get(
        "/bff/research-tasks",
        params={"page_size": 2, "include_non_real": "true", "page_token": body1["page_info"]["next_page_token"]},
        headers=_auth_headers("tenant-dev"),
    )
    assert page2.status_code == 200
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["page_info"]["next_page_token"] is None
    assert body2["next_page_token"] is None
    assert [i["task_id"] for i in body1["items"] + body2["items"]] == [
        "rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b",
        "rtask-20261023-008",
        "rtask-20261022-blank-name",
    ]


def test_research_experiments_and_post_experiments_are_unchanged_and_unmerged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_owner_urllib_stub(monkeypatch)
    store = _FakeExperimentReadStore()
    client = _build_client(store)

    exp_list = client.get("/bff/research-experiments", headers=_auth_headers("tenant-dev"))
    assert exp_list.status_code == 200
    exp_ids = [item["experiment_id"] for item in exp_list.json()["items"]]
    assert exp_ids == ["exp-existing-001"]
    assert all(not exp_id.startswith("rtask-") for exp_id in exp_ids)

    created = client.post(
        "/bff/experiments",
        headers={**_auth_headers("tenant-dev"), "Idempotency-Key": "idem-exp-create-1"},
        json={"name": "Manual Experiment Row"},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "queued"

    tasks_list = client.get("/bff/research-tasks", params={"include_non_real": "true"}, headers=_auth_headers("tenant-dev"))
    assert tasks_list.status_code == 200
    task_ids = [item["task_id"] for item in tasks_list.json()["items"]]
    assert all(task_id.startswith("rtask-") for task_id in task_ids)
    assert "exp-existing-001" not in task_ids


def test_collector_query_plan_task_exposes_exact_diff_and_other_tasks_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_task = {
        "id": "rtask-collector-query-plan-tenant-dev-2026w46",
        "task_id": "rtask-collector-query-plan-tenant-dev-2026w46",
        "title": "Weekly collector query plan proposal",
        "objective": "Proposal only; applying it is a reviewed fetch-policy.json PR.",
        "tenant_id": "tenant-dev",
        "task_type": "collector_query_plan",
        "status": "proposed",
        "created_by": "research-query-plan",
        "created_at": "2026-11-13T09:00:00Z",
        "updated_at": "2026-11-13T09:00:00Z",
        "source_refs": [],
        "constraints": {"tenant_id": "tenant-dev"},
        "query_plan": {
            "revision": 1,
            "matched_terms": ["momentum"],
            "diff": "--- a/deploy/collector/fetch-policy.json\n+++ b/deploy/collector/fetch-policy.json\n",
        },
    }
    plain = deepcopy(HOSTED_TASK_FALLBACK_TO_TASK_ID)
    _install_owner_urllib_stub(monkeypatch, tasks=[plan_task, plain], runs=[])
    client = _build_client()
    body = client.get("/bff/research-tasks", params={"include_non_real": "true"}, headers=_auth_headers("tenant-dev")).json()
    by_id = {item["task_id"]: item for item in body["items"]}
    assert by_id[plan_task["task_id"]]["query_plan"] == plan_task["query_plan"]
    assert "query_plan" not in by_id[plain["task_id"]]

    for view, expected in (("research", plain), ("collector_checks", plan_task)):
        response = client.get(
            "/bff/research-tasks",
            params={"view": view, "include_non_real": "true", "page_size": 1},
            headers=_auth_headers("tenant-dev"),
        )
        assert response.status_code == 200
        page = response.json()
        assert [item["task_id"] for item in page["items"]] == [expected["task_id"]]
        assert page["page_info"] == {"page_size": 1, "next_page_token": None, "total": 1}


def test_display_summary_reads_structured_dataset_and_subject_references(monkeypatch: pytest.MonkeyPatch) -> None:
    task = deepcopy(HOSTED_TASK_CONSTRAINTS_TENANT)
    task["source_refs"] = [{"type": "strategy_spec", "id": "strategy-reference"}]
    run = deepcopy(HOSTED_RUNS[0])
    run["task_id"] = task["task_id"]
    run["input_refs"] = [{"type": "stage", "id": "stage-reference"}, {"type": "dataset", "dataset_id": "dataset-reference"}]
    _install_owner_urllib_stub(monkeypatch, tasks=[task], runs=[run])
    response = _build_client().get(
        "/bff/research-tasks", params={"view": "research", "include_non_real": "true"}, headers=_auth_headers("tenant-dev"),
    )
    summary = response.json()["items"][0]["display_summary"]
    assert summary["subject_ref"] == "strategy-reference"
    assert summary["dataset_ref"] == "dataset-reference"


# --- RC6 WP6.2/WP6.4: non-real hidden by default, display summary (hosted shapes) ---

HOSTED_TASK_ACCEPTANCE_TEST: Dict[str, Any] = {
    "id": "rtask-20261119-acc",
    "task_id": "rtask-20261119-acc",
    "title": "ACCEPTANCE TEST stub/stub",
    "objective": "acceptance probe",
    "task_type": "alpha_replication",
    "tenant_id": "tenant-dev",
    "status": "completed",
    "created_by": "acceptance-runner",
    "created_at": "2026-11-19T09:00:00Z",
    "updated_at": "2026-11-19T09:01:00Z",
    "source_refs": [],
    "constraints": {},
}

HOSTED_TASK_SCOPED_TEST: Dict[str, Any] = {
    "id": "rtask-20261119-scope",
    "task_id": "rtask-20261119-scope",
    "title": "Real looking name",
    "objective": "x",
    "tenant_id": "tenant-dev",
    "status": "completed",
    "created_at": "2026-11-19T08:00:00Z",
    "source_refs": [],
    "constraints": {"data_scope": "synthetic"},
}


def _extra_tasks_and_runs() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    tasks = [
        HOSTED_TASK_CONSTRAINTS_TENANT,
        HOSTED_TASK_TOP_LEVEL_TENANT_NO_TITLE,
        HOSTED_TASK_FALLBACK_TO_TASK_ID,
        HOSTED_TASK_ACCEPTANCE_TEST,
        HOSTED_TASK_SCOPED_TEST,
    ]
    runs = list(HOSTED_RUNS) + [
        _make_hosted_run(
            run_id="rrun-20261119-acc", task_id="rtask-20261119-acc", adapter="stub",
            dispatch_mode="stub", requested_mode="stub", status="completed",
            created_at="2026-11-19T09:00:30Z",
        ),
        _make_hosted_run(
            run_id="rrun-20261119-scope", task_id="rtask-20261119-scope", adapter="vectorbt",
            dispatch_mode="real", requested_mode="real", status="completed",
            created_at="2026-11-19T08:01:00Z",
        ),
    ]
    return tasks, runs


def test_list_hides_non_real_by_default_with_count(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks, runs = _extra_tasks_and_runs()
    _install_owner_urllib_stub(monkeypatch, tasks=tasks, runs=runs)
    client = _build_client()
    body = client.get("/bff/research-tasks", headers=_auth_headers("tenant-dev")).json()
    assert [i["task_id"] for i in body["items"]] == ["rtask-20261023-008"]
    assert body["meta"]["hidden_non_real_count"] == 4
    assert body["page_info"]["total"] == 1
    assert body["items"][0]["is_non_real"] is False
    assert body["items"][0]["non_real_reason"] is None


def test_list_include_non_real_reasons_and_display_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks, runs = _extra_tasks_and_runs()
    _install_owner_urllib_stub(monkeypatch, tasks=tasks, runs=runs)
    client = _build_client()
    body = client.get(
        "/bff/research-tasks", params={"include_non_real": "true"}, headers=_auth_headers("tenant-dev")
    ).json()
    by_id = {i["task_id"]: i for i in body["items"]}
    assert body["meta"]["hidden_non_real_count"] == 0
    assert body["page_info"]["total"] == 5
    assert by_id["rtask-20261119-acc"]["non_real_reason"] == "acceptance_test"
    assert by_id["rtask-20261119-scope"]["non_real_reason"] == "acceptance_test"
    assert by_id["rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b"]["non_real_reason"] == "stub_run"
    assert by_id["rtask-20261022-blank-name"]["non_real_reason"] == "manual_record"
    assert all(by_id[k]["is_non_real"] for k in by_id if k != "rtask-20261023-008")

    rapid = by_id["rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b"]
    assert rapid["name_is_fallback_id"] is False  # objective present
    assert rapid["display_summary"] == {
        "task_kind": "rapid_eval", "subject_ref": None,
        "symbol": "2330.TW", "dataset_ref": "dataset:tw-equities-daily",
    }
    blank = by_id["rtask-20261022-blank-name"]
    assert blank["name"] == "rtask-20261022-blank-name"
    assert blank["name_is_fallback_id"] is True
    assert blank["display_summary"]["task_kind"] is None
    alpha = by_id["rtask-20261023-008"]
    assert alpha["display_summary"]["subject_ref"] == "reg-strategy-spec-persona-aa060d53ff712cbba704"


def test_detail_carries_non_real_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks, runs = _extra_tasks_and_runs()
    _install_owner_urllib_stub(monkeypatch, tasks=tasks, runs=runs)
    client = _build_client()
    d = client.get("/bff/research-tasks/rtask-20261119-acc", headers=_auth_headers("tenant-dev")).json()
    assert d["is_non_real"] is True and d["non_real_reason"] == "acceptance_test"
    assert d["data"]["display_summary"]["symbol"] == "2330.TW"
    d2 = client.get("/bff/research-tasks/rtask-20261023-008", headers=_auth_headers("tenant-dev")).json()
    assert d2["is_non_real"] is False and d2["name_is_fallback_id"] is False


# ---- BFF-RESEARCH-DEMAND-READ-20261125: demands, demand join, persona-based non-real ----
# Row shapes recorded read-only on hosted BFF adc027da9 (2026-11-25 06:20Z-06:40Z).

DEMAND_PERSONA_A = "persona-ee7112a829d38b9135ac"
DEMAND_PERSONA_B = "persona-6507750312870d03a241"


def _owner_demand(demand_id: str, persona_id: str, **over: Any) -> Dict[str, Any]:
    row = {
        "schema_version": "research_demand.v1", "demand_id": demand_id, "tenant_id": "tenant-dev",
        "persona_id": persona_id, "mandate": "meeting_research_demand", "missing_datasets": [],
        "tried_families": ["momentum"], "short_measure": "effective_bets", "pool_version": 3,
        "evidence_ref": "pool-health:" + persona_id, "status": "open",
        "created_at": "2026-11-25T05:00:00Z", "expires_at": "2026-12-02T05:00:00Z",
        "satisfied_at": None, "satisfied_by": None,
    }
    row.update(over)
    return row


HOSTED_DEMANDS = [
    _owner_demand("rd-975bb89106f1c696a680", DEMAND_PERSONA_A, discovery={
        "template_search": {"reason": "template_search_unavailable:ValueError:mandate_universe_missing"},
        "last_run": {"reason": "no_external_match"}}),
    _owner_demand("rd-796e24e24bf2055d3c42", DEMAND_PERSONA_B),
]

SEARCH_TASK = {
    "id": "rtask-tmpl-1", "task_id": "rtask-tmpl-1", "tenant_id": "tenant-dev", "status": "failed",
    "title": "Search registered templates for rd-975bb89106f1c696a680",
    "objective": "Compile mandate-compatible templates through the replication gate",
    "created_by": "persona-pool-cycle", "created_at": "2026-11-25T05:10:00Z", "updated_at": "2026-11-25T05:11:00Z",
    "source_refs": [], "constraints": {"tenant_id": "tenant-dev", "persona_id": DEMAND_PERSONA_A},
}


def _search_run(run_id: str, created_at: str, detail: Optional[str]) -> Dict[str, Any]:
    run = _make_hosted_run(run_id=run_id, task_id="rtask-tmpl-1", adapter="template_demand_gate",
                           dispatch_mode="replication_gate", requested_mode="replication_gate",
                           status="failed", created_at=created_at)
    run["parameters"] = {"action_type": "template_search", "tenant_id": "tenant-dev",
                         "persona_id": DEMAND_PERSONA_A, "demand_id": "rd-975bb89106f1c696a680"}
    run["input_refs"] = []
    run["rejection"] = {"reason": "retrain_failed", "step": "template_search", "detail": detail}
    return run


def _rapid_task(task_id: str) -> Dict[str, Any]:
    return {"id": task_id, "task_id": task_id, "title": None, "task_type": "rapid_eval",
            "tenant_id": "tenant-dev", "objective": "Rapid evaluation", "status": "ready",
            "created_by": "training-session-svc", "created_at": "2026-10-24T10:00:00Z",
            "updated_at": "2026-10-24T10:00:00Z", "source_refs": [], "constraints": {}}


def _rapid_run(task_id: str, persona_ids: List[str]) -> Dict[str, Any]:
    run = _make_hosted_run(run_id="rrun-" + task_id, task_id=task_id, adapter="imitation",
                           dispatch_mode="real", requested_mode="real", status="completed",
                           created_at="2026-10-24T10:01:00Z")
    run["metadata"] = {"dataset_lineage": {"persona_ids": persona_ids, "version_id": "ds-v1"}}
    return run


ACCEPTANCE_PERSONA_1 = "persona-strat-hosted-all12-h12-claude-20261027-4923-src-106b4057d07a"
ACCEPTANCE_PERSONA_2 = "persona-strat-hosted-all12-h12-01a11-20261023-9aeb-src-e018ff6d0eb8"
RELEASE_PROBE_PERSONA = "persona-132519698e6199e80326"
REAL_PERSONA = "persona-real-0001"

RAPID_TASKS = [
    "rtask-exp-sic-30bcef4d067e4c0111fc28cfe8101a7b",
    "rtask-exp-sic-e2b9b58e0000000000000000000000aa",
    "rtask-exp-sic-fdf12a210000000000000000000000bb",
    "rtask-exp-sic-realreal0000000000000000000000cc",
]


def _persona_records() -> Dict[str, Dict[str, Any]]:
    return {
        ACCEPTANCE_PERSONA_1: {"id": ACCEPTANCE_PERSONA_1, "name": "Hosted all12 h12 claude", "dataScope": "test"},
        ACCEPTANCE_PERSONA_2: {"id": ACCEPTANCE_PERSONA_2, "name": "Hosted all12 h12 01a11", "dataScope": "test"},
        RELEASE_PROBE_PERSONA: {"id": RELEASE_PROBE_PERSONA, "dataScope": None, "lifecycle": "retired",
                                "name": "Pantheon Dev Paper Release 38790003355 Attempt 1"},
        DEMAND_PERSONA_A: {"id": DEMAND_PERSONA_A, "name": "Momentum Persona A", "dataScope": "regular"},
        DEMAND_PERSONA_B: {"id": DEMAND_PERSONA_B, "name": "Momentum Persona B", "dataScope": "regular"},
        REAL_PERSONA: {"id": REAL_PERSONA, "name": "Real Persona", "dataScope": "regular"},
    }


def _build_demand_client(directory: Any) -> Tuple[TestClient, List[Any]]:
    snapshots: List[Any] = []

    def snapshot(tenant_id: str, *, snapshot_at: Optional[str] = None) -> Any:
        snapshots.append(tenant_id)
        if isinstance(directory, Exception):
            raise directory
        return type("Snap", (), {"records_by_id": directory})()

    app = build_bff_app()
    app.include_router(create_research_router(
        get_read_store=lambda: _FakeExperimentReadStore(),
        extract_identity=auth_policy.extract_identity, require_read_role=auth_policy.require_read_role,
        require_operator_role=auth_policy.require_operator_role, bff_error=auth_policy.bff_error,
        utc_now=lambda: "2026-11-25T06:30:00Z", persona_directory_snapshot=snapshot))
    return TestClient(app), snapshots


def _demand_world(monkeypatch, *, demands=None, **kw):
    tasks = [SEARCH_TASK] + [_rapid_task(t) for t in RAPID_TASKS]
    runs = [_search_run("rrun-tmpl-1", "2026-11-25T05:10:30Z", "template_search_unavailable:ValueError:mandate_universe_missing"),
            _search_run("rrun-tmpl-2", "2026-11-25T05:40:30Z", "template_search_unavailable:ValueError:mandate_universe_missing"),
            _rapid_run(RAPID_TASKS[0], [ACCEPTANCE_PERSONA_1]),
            _rapid_run(RAPID_TASKS[1], [ACCEPTANCE_PERSONA_2]),
            _rapid_run(RAPID_TASKS[2], [RELEASE_PROBE_PERSONA]),
            _rapid_run(RAPID_TASKS[3], [REAL_PERSONA, ACCEPTANCE_PERSONA_1])]
    return _install_owner_urllib_stub(
        monkeypatch, tasks=tasks, runs=runs,
        demands=demands if demands is not None else {"demands": HOSTED_DEMANDS, "count": 2}, **kw)


def test_research_view_lists_open_demands_from_owner_with_forwarded_caller(monkeypatch) -> None:
    calls = _demand_world(monkeypatch)
    client, snapshots = _build_demand_client(_persona_records())
    resp = client.get("/bff/research-tasks", params={"view": "research"}, headers=_auth_headers("tenant-dev"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    demands = {d["demand_id"]: d for d in body["open_demands"]}
    assert set(demands) == {"rd-975bb89106f1c696a680", "rd-796e24e24bf2055d3c42"}
    first = demands["rd-975bb89106f1c696a680"]
    assert first == {
        "demand_id": "rd-975bb89106f1c696a680", "persona_id": DEMAND_PERSONA_A,
        "persona_display_name": "Momentum Persona A", "short_measure": "effective_bets",
        "tried_families": ["momentum"], "missing_datasets": [], "mandate": "meeting_research_demand",
        "created_at": "2026-11-25T05:00:00Z", "expires_at": "2026-12-02T05:00:00Z", "status": "open",
        "evidence_ref": "pool-health:" + DEMAND_PERSONA_A,
        "last_template_search_reason": "template_search_unavailable:ValueError:mandate_universe_missing",
        "last_discovery_reason": "no_external_match"}
    assert demands["rd-796e24e24bf2055d3c42"]["last_template_search_reason"] is None
    assert body["meta"]["surfaces"]["research_demands"]["status"] == "ok"
    demand_calls = [c for c in calls if "persona-demands" in c["url"]]
    assert len(demand_calls) == 1
    assert demand_calls[0]["headers"]["authorization"] == _auth_headers("tenant-dev")["Authorization"]
    assert demand_calls[0]["headers"]["x-tenant-id"] == "tenant-dev"
    assert len(snapshots) == 1  # distinct persona lookups share one directory read per request


def test_non_research_views_do_not_read_demands(monkeypatch) -> None:
    calls = _demand_world(monkeypatch)
    client, _ = _build_demand_client(_persona_records())
    body = client.get("/bff/research-tasks", params={"include_non_real": "true"}, headers=_auth_headers()).json()
    assert "open_demands" not in body and "research_demands" not in body["meta"]["surfaces"]
    assert not [c for c in calls if "persona-demands" in c["url"]]


@pytest.mark.parametrize("failure", [{"demands_http_status": 403}, {"demands_http_status": 401},
                                     {"demands_http_status": 503}, {"demands": {"demands": "oops"}},
                                     {"demands": {"demands": [dict(HOSTED_DEMANDS[0], tenant_id="tenant-b")]}}])
def test_demand_owner_failure_is_visible_and_task_list_still_renders(monkeypatch, failure) -> None:
    _demand_world(monkeypatch, **failure)
    client, _ = _build_demand_client(_persona_records())
    body = client.get("/bff/research-tasks", params={"view": "research"}, headers=_auth_headers()).json()
    assert body["open_demands"] == []
    surface = body["meta"]["surfaces"]["research_demands"]
    assert surface["status"] == "unavailable" and surface["reason"]
    assert body["meta"]["surfaces"]["research_tasks"]["status"] == "ok"
    assert [i["task_id"] for i in body["items"]]  # tasks render


def test_template_search_task_carries_demand_joined_from_recorded_run(monkeypatch) -> None:
    _demand_world(monkeypatch)
    client, _ = _build_demand_client(_persona_records())
    body = client.get("/bff/research-tasks", params={"view": "research"}, headers=_auth_headers()).json()
    row = next(i for i in body["items"] if i["task_id"] == "rtask-tmpl-1")
    assert row["title"] == SEARCH_TASK["title"] and row["name"] == SEARCH_TASK["title"]
    assert row["demand"] == {
        "demand_id": "rd-975bb89106f1c696a680", "persona_id": DEMAND_PERSONA_A, "attempt_count": 2,
        "last_reason_code": "template_search_unavailable:ValueError:mandate_universe_missing",
        "short_measure": "effective_bets", "status": "open"}
    assert row["display_summary"]["subject_ref"] == "rd-975bb89106f1c696a680"
    assert row["display_summary"]["task_kind"] == "template_search"
    detail = client.get("/bff/research-tasks/rtask-tmpl-1", headers=_auth_headers()).json()
    assert detail["demand"]["attempt_count"] == 2
    assert detail["demand"]["last_reason_code"] == "template_search_unavailable:ValueError:mandate_universe_missing"
    other = next(i for i in body["items"] if i["task_id"] == RAPID_TASKS[3])
    assert other["demand"] is None


def test_rapid_eval_on_test_or_probe_personas_is_non_real_from_owner_records(monkeypatch) -> None:
    _demand_world(monkeypatch)
    client, snapshots = _build_demand_client(_persona_records())
    body = client.get("/bff/research-tasks", params={"include_non_real": "true"}, headers=_auth_headers()).json()
    reasons = {i["task_id"]: i["non_real_reason"] for i in body["items"]}
    assert [reasons[t] for t in RAPID_TASKS[:3]] == ["acceptance_test"] * 3
    assert reasons[RAPID_TASKS[3]] is None  # a real persona in the lineage keeps the row real
    assert len(snapshots) == 1
    hidden = client.get("/bff/research-tasks", headers=_auth_headers()).json()
    assert hidden["meta"]["hidden_non_real_count"] == 3
    assert RAPID_TASKS[0] not in {i["task_id"] for i in hidden["items"]}
    detail = client.get("/bff/research-tasks/" + RAPID_TASKS[2], headers=_auth_headers()).json()
    assert detail["is_non_real"] is True


@pytest.mark.parametrize("directory", [RuntimeError("persona owner down"), {}])
def test_unreadable_persona_owner_leaves_rapid_eval_visible(monkeypatch, directory) -> None:
    _demand_world(monkeypatch)
    client, _ = _build_demand_client(directory)
    body = client.get("/bff/research-tasks", headers=_auth_headers()).json()
    rows = {i["task_id"]: i for i in body["items"]}
    assert all(rows[t]["non_real_reason"] is None and not rows[t]["is_non_real"] for t in RAPID_TASKS)


def test_unwired_persona_directory_leaves_rapid_eval_visible(monkeypatch) -> None:
    _demand_world(monkeypatch)
    body = _build_client().get("/bff/research-tasks", headers=_auth_headers()).json()
    assert all(i["non_real_reason"] is None for i in body["items"] if i["task_id"] in RAPID_TASKS)


def test_demand_read_denied_for_wrong_tenant_header(monkeypatch) -> None:
    calls = _demand_world(monkeypatch)
    client, _ = _build_demand_client(_persona_records())
    resp = client.get("/bff/research-tasks", params={"view": "research"},
                      headers=_auth_headers("tenant-dev", header_tenant_id="tenant-b"))
    assert resp.status_code >= 400
    assert not [c for c in calls if "persona-demands" in c["url"]]
