"""Tests for BFF-CONSOL-010 canonical fixture pack C.

Acceptance criteria verified:
- alerts/incidents/approvals/audit/jobs/channels/skills/tools/mcp families are non-empty
- alerts link to incidents
- approvals link to deployment or v5 intervention records
- audit contains an immutable append-only record sample
- jobs expose non-generic detail and logs
- channels align with the BFF SSE channel catalog
- authenticated live smoke routes return at least one record
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest import mock

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import ReadSurfacePorts


HEADERS = {"Authorization": "Bearer op-2:operator"}
FIXTURE_PATH = Path(__file__).resolve().parent / "data" / "fixtures_pack_c.json"

SERVICE_ENV_BLANKS = {
    "PANTHEON_DEPLOYMENT_API_URL": "",
    "PANTHEON_DEPLOYMENT_SERVICE_URL": "",
    "PANTHEON_GOVERNANCE_APPROVAL_API_URL": "",
    "PANTHEON_GOVERNANCE_SERVICE_URL": "",
    "PANTHEON_CAPITAL_API_URL": "",
    "PANTHEON_CAPITAL_SERVICE_URL": "",
    "PANTHEON_RUNTIME_MANAGER_URL": "",
    "PANTHEON_INTERNAL_API_URL": "",
    "PANTHEON_PERSONA_API_URL": "",
    "PANTHEON_PERSONA_SERVICE_URL": "",
    "PANTHEON_LINEAGE_READ_URL": "",
    "PANTHEON_LINEAGE_API_URL": "",
}


class FixturePackCTestReadPorts(ReadSurfacePorts):
    def __init__(self, *, allow_local_snapshot_fallback: bool = True) -> None:
        super().__init__()
        self._allow_fallback = allow_local_snapshot_fallback
        self._data: dict[str, Any] = {}
        if allow_local_snapshot_fallback and FIXTURE_PATH.exists():
            payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
            self._data = payload.get("datasets", {})

    def dataset_source(self, dataset: str) -> str:
        return "local_snapshot" if self._allow_fallback else "missing"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        src = self.dataset_source(dataset)
        status = "unavailable" if src == "missing" else "ok"
        return {"status": status, "source": src, "snapshot_at": snapshot_at}

    def _get_dataset(self, name: str) -> dict[str, Any] | list[Any]:
        return self._data.get(name, {})

    def list_alerts(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("alerts")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_alert(self, alert_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("alerts")
        if isinstance(ds, dict):
            return ds.get(str(alert_id or ""))
        return next((a for a in ds if a.get("id") == alert_id or a.get("alert_id") == alert_id), None)

    def list_incidents(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("incidents")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_incident(self, incident_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("incidents")
        if isinstance(ds, dict):
            return ds.get(str(incident_id or ""))
        return next((i for i in ds if i.get("id") == incident_id or i.get("incident_id") == incident_id), None)

    def list_approval_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("approval_decisions")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_approvals(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_approval_decisions(**kwargs)

    def list_approval_queue_items(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("approval_queue_items")
        if ds:
            return list(ds.values()) if isinstance(ds, dict) else list(ds)
        return self.list_approval_decisions(**kwargs)

    def get_approval_decision(self, approval_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("approval_decisions")
        raw = None
        if isinstance(ds, dict):
            raw = ds.get(str(approval_id or ""))
        else:
            raw = next((a for a in ds if a.get("id") == approval_id or a.get("approval_id") == approval_id or a.get("decision_id") == approval_id), None)
        if raw:
            d = dict(raw)
            target_type = d.get("target_type")
            target_id = d.get("target_id")
            if str(target_type or "") == "DeploymentPlan" and target_id:
                d["deployment_ref"] = {
                    "plan_id": target_id,
                    "href": f"/bff/deployments/{target_id}",
                }
            return d
        return None

    def get_approval(self, approval_id: str | None) -> dict[str, Any] | None:
        return self.get_approval_decision(approval_id)

    def list_governance_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("governance_audit_events")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_jobs(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("jobs")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_jobs_bff(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_jobs(**kwargs)

    def get_job(self, job_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("jobs")
        if isinstance(ds, dict):
            return ds.get(str(job_id or ""))
        return next((j for j in ds if j.get("id") == job_id or j.get("job_id") == job_id), None)

    def get_job_bff(self, job_id: str | None) -> dict[str, Any] | None:
        return self.get_job(job_id)

    def get_job_logs(self, job_id: str | None) -> list[dict[str, Any]]:
        job = self.get_job(job_id)
        if job and isinstance(job.get("logs"), list):
            return job["logs"]
        return [{"message": "job initialized", "timestamp": "2026-05-13T03:40:00Z"}]

    def list_channels(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("channels")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_skills(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("skills")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_skill(self, skill_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("skills")
        if isinstance(ds, dict):
            return ds.get(str(skill_id or ""))
        return next((s for s in ds if s.get("id") == skill_id or s.get("skill_id") == skill_id), None)

    def list_tools(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("tools")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_tool(self, tool_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("tools")
        if isinstance(ds, dict):
            return ds.get(str(tool_id or ""))
        return next((t for t in ds if t.get("id") == tool_id or t.get("tool_id") == tool_id), None)

    def list_mcp_servers(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("mcp_servers")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_mcp_server(self, server_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("mcp_servers")
        if isinstance(ds, dict):
            return ds.get(str(server_id or ""))
        return next((s for s in ds if s.get("id") == server_id or s.get("server_id") == server_id), None)

    def list_mcp_tools(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("mcp_tools")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_mcp_tool(self, tool_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("mcp_tools")
        if isinstance(ds, dict):
            return ds.get(str(tool_id or ""))
        return next((t for t in ds if t.get("id") == tool_id or t.get("tool_id") == tool_id), None)


@contextmanager
def _fresh_pack_c_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_jobs = dict(bff_main._GOV_BFF_JOB_OVERLAY)
        original_mcp_servers = dict(bff_main._MCP_SERVER_REGISTRY)
        original_mcp_tools = dict(bff_main._MCP_TOOL_REGISTRY)
        original_tools = dict(bff_main._TOOL_REGISTRY)
        original_skills = dict(bff_main._SKILL_REGISTRY)
        try:
            bff_main.read_store = FixturePackCTestReadPorts(
                allow_local_snapshot_fallback=True,
            )
            bff_main._GOV_BFF_JOB_OVERLAY.clear()
            bff_main._MCP_SERVER_REGISTRY.clear()
            bff_main._MCP_TOOL_REGISTRY.clear()
            bff_main._TOOL_REGISTRY.clear()
            bff_main._SKILL_REGISTRY.clear()
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.read_store = original_store
            bff_main._GOV_BFF_JOB_OVERLAY.clear()
            bff_main._GOV_BFF_JOB_OVERLAY.update(original_jobs)
            bff_main._MCP_SERVER_REGISTRY.clear()
            bff_main._MCP_SERVER_REGISTRY.update(original_mcp_servers)
            bff_main._MCP_TOOL_REGISTRY.clear()
            bff_main._MCP_TOOL_REGISTRY.update(original_mcp_tools)
            bff_main._TOOL_REGISTRY.clear()
            bff_main._TOOL_REGISTRY.update(original_tools)
            bff_main._SKILL_REGISTRY.clear()
            bff_main._SKILL_REGISTRY.update(original_skills)


def _payload_records(payload: dict) -> list[dict]:
    for key in ("data", "items", "alerts", "events"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def test_fixture_pack_c_declares_all_required_families() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["policy"]["paper_canary_truth_impact"] == "none"
    for family in (
        "alerts",
        "incidents",
        "approvals",
        "audit",
        "jobs",
        "channels",
        "skills",
        "tools",
        "mcp",
    ):
        assert payload["families"][family], f"Family {family!r} must be non-empty"


def test_fixture_pack_c_alerts_approvals_and_audit_linkages() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    datasets = payload["datasets"]

    incidents = datasets["incidents"]
    for alert_id, alert in datasets["alerts"].items():
        linked_id = alert.get("linked_incident_id") or (alert.get("target_ref") or {}).get("target_id")
        assert linked_id in incidents, f"{alert_id}: alert must link to a Pack C incident"

    deployments = datasets["deployment_plans"]
    interventions = datasets.get("v5_interventions", {})
    for approval_id, approval in datasets["approval_decisions"].items():
        target_id = approval.get("target_id")
        assert target_id in deployments or target_id in interventions, (
            f"{approval_id}: approval must link to deployment or v5 intervention"
        )

    immutable = [
        event
        for event in datasets["governance_audit_events"]
        if event.get("immutable_record") is True and event.get("append_only") is True
    ]
    assert immutable, "Pack C audit must include at least one immutable append-only record"


def test_fixture_pack_c_channels_align_with_sse_catalog() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    channels = payload["datasets"]["channels"]
    for channel_id, channel in channels.items():
        assert channel_id in bff_main.SSE_CHANNEL_CATALOG
        assert channel["sse_topic"] == f"bff.{channel_id}"


def test_pack_c_live_smoke_routes_return_non_empty_records() -> None:
    with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
        with _fresh_pack_c_client() as client:
            routes = (
                "/bff/alerts",
                "/bff/incidents",
                "/bff/approvals",
                "/bff/audit",
                "/bff/jobs",
                "/bff/channels",
                "/bff/skills",
                "/bff/tools",
                "/bff/mcp-servers",
                "/bff/mcp-tools",
            )
            failures = []
            for path in routes:
                response = client.get(path, headers=HEADERS)
                if response.status_code != 200:
                    failures.append((path, response.status_code, response.text[:240]))
                    continue
                records = _payload_records(response.json())
                if not records:
                    failures.append((path, "empty", response.text[:240]))

            assert not failures


def test_pack_c_live_jobs_fixture_has_detail_and_logs() -> None:
    with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
        with _fresh_pack_c_client() as client:
            job_id = "job-pack-c-tool-import-001"

            detail = client.get(f"/bff/jobs/{job_id}", headers=HEADERS)
            assert detail.status_code == 200, detail.text
            data = detail.json()["data"]
            assert data["job_id"] == job_id
            assert data["name"] != "undefined"
            assert data.get("progress")

            logs = client.get(f"/bff/jobs/{job_id}/logs", headers=HEADERS)
            assert logs.status_code == 200, logs.text
            assert logs.json()["logs"]


def test_pack_c_live_detail_routes_use_fixture_records() -> None:
    with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
        with _fresh_pack_c_client() as client:
            checks = (
                ("/bff/incidents/inc-pack-c-001", "incident_id", "inc-pack-c-001"),
                ("/bff/approvals/approval-pack-c-deploy", "id", "approval-pack-c-deploy"),
                ("/bff/tools/tool-pack-c-risk-snapshot", "tool_id", "tool-pack-c-risk-snapshot"),
                (
                    "/bff/skills/skill-pack-c-incident-summarizer",
                    "skill_id",
                    "skill-pack-c-incident-summarizer",
                ),
                (
                    "/bff/mcp-servers/mcp-server-pack-c-research",
                    "server_id",
                    "mcp-server-pack-c-research",
                ),
                (
                    "/bff/mcp-tools/mcp-tool-pack-c-risk-snapshot",
                    "tool_id",
                    "mcp-tool-pack-c-risk-snapshot",
                ),
            )
            failures = []
            for path, key, expected in checks:
                response = client.get(path, headers=HEADERS)
                if response.status_code != 200:
                    failures.append((path, response.status_code, response.text[:240]))
                    continue
                payload = response.json()
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                if data.get(key) != expected:
                    failures.append((path, key, data))

            assert not failures

            approval = client.get("/bff/approvals/approval-pack-c-deploy", headers=HEADERS)
            assert approval.status_code == 200, approval.text
            approval_data = approval.json()["data"]
            assert approval_data["target_type"] == "DeploymentPlan"
            assert approval_data["target_id"] == "plan-pack-c-paper-001"
            assert approval_data["deployment_ref"] == {
                "plan_id": "plan-pack-c-paper-001",
                "href": "/bff/deployments/plan-pack-c-paper-001",
            }
