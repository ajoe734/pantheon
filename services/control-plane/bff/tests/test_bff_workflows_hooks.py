from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import extract_identity as _auth_extract_identity
from services.control_plane.bff.console_gap.workflows_hooks import create_workflows_hooks_router
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.operations_consultation import create_operations_consultation_port


OPERATOR_HEADERS = {"Authorization": "Bearer op-bffgap:operator"}
NO_AUTH_HEADERS: dict = {}

_WORKFLOW_ENV = "PANTHEON_BFF_WORKFLOW_TEMPLATE_STORE"
_HOOK_ENV = "PANTHEON_BFF_HOOK_REGISTRY_STORE"


def _extract_identity(auth: Optional[str]) -> Any:
    return _auth_extract_identity(auth)


def _require_read_role(identity: Any) -> None:
    roles = getattr(identity, "roles", [])
    if not any(r in {"operator", "admin", "analyst", "viewer", "approver", "reviewer"} for r in roles):
        raise HTTPException(status_code=403, detail="Forbidden")


class _WorkflowsHooksTestStore:
    def __init__(self, td: str) -> None:
        self.ports = create_read_surface_ports(
            operations_consultation=create_operations_consultation_port()
        )
        self.td = td

    def dataset_source(self, dataset: str) -> str:
        if dataset == "workflow_templates":
            env_path = os.environ.get(_WORKFLOW_ENV)
            if env_path and Path(env_path).exists():
                return "service_store"
            return "missing"
        if dataset == "hook_registry":
            env_path = os.environ.get(_HOOK_ENV)
            if env_path and Path(env_path).exists():
                return "service_store"
            return "missing"
        return "missing"

    def list_workflow_templates(self) -> list[dict[str, Any]]:
        env_path = os.environ.get(_WORKFLOW_ENV)
        if env_path and Path(env_path).exists():
            try:
                data = json.loads(Path(env_path).read_text(encoding="utf-8"))
                return data if isinstance(data, list) else list(data.values())
            except Exception:
                return []
        return []

    def list_hook_registry(self) -> list[dict[str, Any]]:
        env_path = os.environ.get(_HOOK_ENV)
        if env_path and Path(env_path).exists():
            try:
                data = json.loads(Path(env_path).read_text(encoding="utf-8"))
                return data if isinstance(data, list) else list(data.values())
            except Exception:
                return []
        return []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.ports, name)


@contextmanager
def _fresh_client(
    td: str,
    *,
    workflows: list[dict[str, Any]] | None = None,
    hooks: list[dict[str, Any]] | None = None,
) -> Iterator[TestClient]:
    original_env = {
        _WORKFLOW_ENV: os.environ.get(_WORKFLOW_ENV),
        _HOOK_ENV: os.environ.get(_HOOK_ENV),
    }
    try:
        for key in (_WORKFLOW_ENV, _HOOK_ENV):
            os.environ.pop(key, None)
        if workflows is not None:
            workflow_path = Path(td) / "workflow_templates.json"
            workflow_path.write_text(json.dumps(workflows), encoding="utf-8")
            os.environ[_WORKFLOW_ENV] = str(workflow_path)
        if hooks is not None:
            hook_path = Path(td) / "hook_registry.json"
            hook_path.write_text(json.dumps(hooks), encoding="utf-8")
            os.environ[_HOOK_ENV] = str(hook_path)
        store = _WorkflowsHooksTestStore(td)
        app = FastAPI()
        app.include_router(
            create_workflows_hooks_router(
                read_store_provider=lambda: store,
                extract_identity=_extract_identity,
                require_read_role=_require_read_role,
            )
        )
        yield TestClient(app)
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_bff_workflows_returns_canonical_list_envelope() -> None:
    workflows = [
        {
            "workflow_id": "pantheon.review",
            "name": "Candidate review",
            "description": "Package candidate review evidence.",
            "schedule": "15 7 * * 1-5",
            "execution_context": "paper",
            "approval_required": True,
        }
    ]
    with tempfile.TemporaryDirectory() as td, _fresh_client(td, workflows=workflows) as client:
        response = client.get("/bff/workflows", headers=OPERATOR_HEADERS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == body["items"]
    assert body["data"][0]["workflow_id"] == "pantheon.review"
    assert body["page_info"]["total"] == 1
    assert body["page_info"]["page_size"] == 100
    assert body["page_info"]["returned"] == 1
    assert body["page_info"]["has_more"] is False
    assert body["meta"]["surfaces"]["workflow_templates"]["status"] == "ok"
    assert body["meta"]["surfaces"]["workflow_templates"]["source"] == "service_store"


def test_bff_hooks_returns_canonical_list_envelope() -> None:
    hooks = [
        {
            "hook_id": "cron.pantheon.deploy",
            "workflow_id": "pantheon.deploy",
            "kind": "cron",
            "schedule": "*/15 * * * *",
            "status": "registered",
        }
    ]
    with tempfile.TemporaryDirectory() as td, _fresh_client(td, hooks=hooks) as client:
        response = client.get("/bff/hooks", headers=OPERATOR_HEADERS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == body["items"]
    assert body["data"][0]["hook_id"] == "cron.pantheon.deploy"
    assert body["page_info"]["total"] == 1
    assert body["page_info"]["page_size"] == 100
    assert body["page_info"]["returned"] == 1
    assert body["page_info"]["has_more"] is False
    assert body["meta"]["surfaces"]["hook_registry"]["status"] == "ok"
    assert body["meta"]["surfaces"]["hook_registry"]["source"] == "service_store"


def test_bff_workflows_hooks_missing_store_returns_degraded_envelope() -> None:
    with tempfile.TemporaryDirectory() as td, _fresh_client(td) as client:
        for path, surface_key in (
            ("/bff/workflows", "workflow_templates"),
            ("/bff/hooks", "hook_registry"),
        ):
            response = client.get(path, headers=OPERATOR_HEADERS)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["data"] == []
            assert body["items"] == []
            assert body["page_info"]["total"] == 0
            assert body["page_info"]["page_size"] == 100
            assert body["page_info"]["returned"] == 0
            assert body["page_info"]["has_more"] is False
            surface = body["meta"]["surfaces"][surface_key]
            assert surface["status"] == "unavailable"
            assert surface["source"] == "missing"
            assert body["meta"]["degradation"]["reason"]


def test_bff_workflows_hooks_require_read_auth() -> None:
    with tempfile.TemporaryDirectory() as td, _fresh_client(td) as client:
        assert client.get("/bff/workflows", headers=NO_AUTH_HEADERS).status_code == 401
        assert client.get("/bff/hooks", headers=NO_AUTH_HEADERS).status_code == 401


def test_bff_workflows_hooks_are_in_openapi() -> None:
    with tempfile.TemporaryDirectory() as td, _fresh_client(td) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200, response.text
    paths = response.json()["paths"]
    assert "/bff/workflows" in paths
    assert "/bff/hooks" in paths
