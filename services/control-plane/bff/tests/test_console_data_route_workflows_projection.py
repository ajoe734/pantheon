from __future__ import annotations

import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.console_gap.route_policies import create_route_policies_router
from services.control_plane.bff.console_gap.workflows_hooks import create_workflows_hooks_router
from services.control_plane.bff.jobs.router import create_jobs_router
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


HEADERS = {"Authorization": "Bearer op-console:operator"}


def _extract_identity(authorization: Optional[str] = None) -> Any:
    class _Identity:
        operator_id = "op-console"
        roles = ["operator"]

    return _Identity()


def _require_read_role(_identity: Any) -> None:
    return None


def _utc_now() -> str:
    return "2026-06-15T00:00:00Z"


def _page_slice(items: List[Any], page_token: Optional[str], page_size: int):
    start = int(page_token) if page_token else 0
    page_items = list(items[start : start + page_size])
    next_token = str(start + page_size) if start + page_size < len(items) else None
    return page_items, next_token


def _dataset_surface_status(dataset: str, *, snapshot_at: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    return {"status": "ok", "source": "service_store", "dataset": dataset, "snapshot_at": snapshot_at}


def _read_surface_meta(
    dataset: str,
    surface_key: str,
    *,
    snapshot_at: Optional[str] = None,
    total: Optional[int] = None,
    surface: Optional[Dict[str, Any]] = None,
    **_: Any,
) -> Dict[str, Any]:
    surf = surface or _dataset_surface_status(dataset, snapshot_at=snapshot_at)
    meta: Dict[str, Any] = {"snapshot_at": snapshot_at, "surfaces": {surface_key: surf}}
    if total is not None:
        meta["total"] = total
    return meta


def _raise_if_read_surface_unavailable(surface: Dict[str, Any], *, label: str) -> None:
    return None


def _bff_error(status_code: int, code: Any, message: str, *args: Any, **kwargs: Any) -> Exception:
    return Exception(f"{status_code}: {message}")


def _reject_body_idempotency_key(_payload: Dict[str, Any]) -> None:
    return None


def _resolve_final_idempotency_key(primary: Optional[str], alternate: Optional[str]) -> str:
    return primary or alternate or str(uuid.uuid4())

_ENV_TO_FILE = {
    "PANTHEON_BFF_ROUTE_POLICY_STORE": "route_policies.json",
    "PANTHEON_BFF_WORKFLOW_TEMPLATE_STORE": "workflow_templates.json",
    "PANTHEON_BFF_HOOK_REGISTRY_STORE": "hook_registry.json",
    "PANTHEON_BFF_JOB_STORE": "jobs.json",
}


def _client(ports) -> TestClient:
    app = FastAPI()
    common = {"extract_identity": _extract_identity, "require_read_role": _require_read_role}
    app.include_router(create_route_policies_router(read_surface=ports, **common))
    app.include_router(
        create_workflows_hooks_router(
            workflow_hook_port=ports,
            snapshot_now=_utc_now,
            **common,
        )
    )
    app.include_router(
        create_jobs_router(
            read_surface=ports,
            bff_error=_bff_error,
            utc_now=_utc_now,
            page_slice=_page_slice,
            read_surface_meta=_read_surface_meta,
            dataset_surface_status=_dataset_surface_status,
            raise_if_read_surface_unavailable=_raise_if_read_surface_unavailable,
            reject_body_idempotency_key=_reject_body_idempotency_key,
            resolve_final_idempotency_key=_resolve_final_idempotency_key,
            submit_job_action=lambda *a, **k: {},
            **common,
        )
    )
    return TestClient(app)


@contextmanager
def _projected_store_client() -> Iterator[TestClient]:
    original_env = {key: os.environ.get(key) for key in _ENV_TO_FILE}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        payloads = {
            "route_policies.json": {
                "control-plane.default": {
                    "id": "control-plane.default",
                    "policy_id": "control-plane.default",
                    "route_policy_id": "control-plane.default",
                    "allowed_workflows": ["trade", "monitor", "governance"],
                    "allowed_skills": ["status-summary"],
                    "producer": "persona-agent",
                    "source": "services/control-plane/persona/main.py::_DEFAULT_POLICY_RESOLVER",
                }
            },
            "workflow_templates.json": {
                "pantheon.review": {
                    "id": "pantheon.review",
                    "template_id": "pantheon.review",
                    "workflow_id": "pantheon.review",
                    "name": "pantheon.review",
                    "schedule": "15 7 * * 1-5",
                    "status": "registered",
                    "producer": "control-plane-cron",
                }
            },
            "hook_registry.json": {
                "cron.pantheon.review": {
                    "id": "cron.pantheon.review",
                    "hook_id": "cron.pantheon.review",
                    "cron_id": "cron.pantheon.review",
                    "workflow_id": "pantheon.review",
                    "kind": "cron",
                    "schedule": "15 7 * * 1-5",
                    "status": "registered",
                    "producer": "control-plane-cron",
                }
            },
            "jobs.json": {
                "research_orchestrator:rrun-console-001": {
                    "id": "research_orchestrator:rrun-console-001",
                    "job_id": "research_orchestrator:rrun-console-001",
                    "producer": "research_orchestrator",
                    "producer_job_id": "rrun-console-001",
                    "job_type": "research_orchestrator",
                    "status": "completed",
                    "created_at": "2026-06-15T00:00:00Z",
                    "summary": "real research run projection",
                    "source_ref": "research_orchestrator:rrun-console-001",
                }
            },
        }
        try:
            for env_name, filename in _ENV_TO_FILE.items():
                path = root / filename
                path.write_text(json.dumps(payloads[filename]), encoding="utf-8")
                os.environ[env_name] = str(path)
            ports = create_in_memory_read_surface_ports(
                operations_consultation_kwargs={
                    "route_policies": list(payloads["route_policies.json"].values()),
                    "workflow_templates": list(payloads["workflow_templates.json"].values()),
                    "hook_registry": list(payloads["hook_registry.json"].values()),
                },
            )
            ports.list_jobs_bff = lambda **_kwargs: list(payloads["jobs.json"].values())
            ports.dataset_source = lambda _dataset: "service_store"
            yield _client(ports)
        finally:
            for env_name, value in original_env.items():
                if value is None:
                    os.environ.pop(env_name, None)
                else:
                    os.environ[env_name] = value


def test_console_data_route_workflows_projected_stores_are_ok() -> None:
    with _projected_store_client() as client:
        expectations = (
            ("/bff/route-policies", "route_policies", "policy_id", "control-plane.default"),
            ("/bff/workflows", "workflow_templates", "workflow_id", "pantheon.review"),
            ("/bff/hooks", "hook_registry", "hook_id", "cron.pantheon.review"),
            ("/bff/jobs", "job_list", "job_id", "research_orchestrator:rrun-console-001"),
        )
        for path, surface_key, id_key, expected_id in expectations:
            response = client.get(path, headers=HEADERS)
            assert response.status_code == 200, response.text
            body = response.json()
            data = body.get("data")
            items = body.get("items")
            if items is None and isinstance(data, dict):
                items = data.get("items")
            assert items
            assert items[0][id_key] == expected_id
            assert body["page_info"]["total"] == 1
            surface = body["meta"]["surfaces"][surface_key]
            assert surface["status"] == "ok"
            assert surface["source"] == "service_store"
