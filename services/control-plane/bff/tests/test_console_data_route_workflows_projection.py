from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


HEADERS = {"Authorization": "Bearer op-console:operator"}

_ENV_TO_FILE = {
    "PANTHEON_BFF_ROUTE_POLICY_STORE": "route_policies.json",
    "PANTHEON_BFF_WORKFLOW_TEMPLATE_STORE": "workflow_templates.json",
    "PANTHEON_BFF_HOOK_REGISTRY_STORE": "hook_registry.json",
    "PANTHEON_BFF_JOB_STORE": "jobs.json",
}


@contextmanager
def _projected_store_client() -> Iterator[TestClient]:
    original_store = bff_main.read_store
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
            bff_main.read_store = ports
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
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
