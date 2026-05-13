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
from typing import Iterator
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


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
            bff_main.read_store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
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
