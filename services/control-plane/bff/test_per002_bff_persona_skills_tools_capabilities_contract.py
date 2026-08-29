"""
PER-002 contract tests: persona-scoped skills/tools/capabilities read API.

Endpoints covered:
  GET /bff/personas/{persona_id}/skills
  GET /bff/personas/{persona_id}/tools
  GET /bff/personas/{persona_id}/capabilities

Acceptance criteria:
  1. Skills list: 200, data+items+page_info envelope, skill entries for effective_skills
  2. Tools list: 200, data+items+page_info envelope, tool entries for effective_tools
  3. Capabilities surface: 200, data.personaId + effectiveSkills + effectiveTools + effectiveWorkflows
  4. 404 for unknown persona_id on all three routes
  5. 401 when no authorization header on all three routes
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from ports import ReadSurfacePorts, create_in_memory_read_surface_ports, create_read_surface_ports

OPERATOR_AUTH = "Bearer per002-op:operator"
HEADERS = {"Authorization": OPERATOR_AUTH}

# persona-alpha has a capability snapshot (cap-001) in the local fallback fixture
PERSONA_ID = "persona-alpha"
UNKNOWN_PERSONA_ID = "persona-does-not-exist-per002"


@contextmanager
def _bff_client(*, fallback: bool = True) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_skill_registry = dict(bff_main._SKILL_REGISTRY)
    original_tool_registry = dict(bff_main._TOOL_REGISTRY)
    original_persona_overlay = dict(bff_main._PERSONA_BFF_OVERLAY)
    try:
        if fallback:
            bff_main.read_store = create_in_memory_read_surface_ports(
                persona_capital_runtime_kwargs={
                    "personas": [
                        {
                            "id": "persona-alpha",
                            "persona_id": "persona-alpha",
                            "name": "Alpha Trader",
                            "lifecycle_state": "paper",
                            "status": "active",
                        }
                    ]
                },
                capability_snapshots={
                    "cap-001": {
                        "snapshot_id": "cap-001",
                        "persona_id": "persona-alpha",
                        "effective_skills": ["risk_review", "incident_triage"],
                        "effective_tools": ["signal_read", "artifact_load", "telemetry_query"],
                        "effective_workflows": ["promotion_review", "incident_response"],
                        "restrictions": ["no_live_trade_without_approval"],
                        "created_at": "2026-04-11T07:55:00Z",
                        "generated_at": "2026-04-11T07:55:00Z",
                    },
                    "persona-alpha": {
                        "snapshot_id": "cap-001",
                        "persona_id": "persona-alpha",
                        "effective_skills": ["risk_review", "incident_triage"],
                        "effective_tools": ["signal_read", "artifact_load", "telemetry_query"],
                        "effective_workflows": ["promotion_review", "incident_response"],
                        "restrictions": ["no_live_trade_without_approval"],
                        "created_at": "2026-04-11T07:55:00Z",
                        "generated_at": "2026-04-11T07:55:00Z",
                    },
                },
            )
        else:
            bff_main.read_store = create_read_surface_ports()
        bff_main._SKILL_REGISTRY.clear()
        bff_main._TOOL_REGISTRY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()
        yield TestClient(bff_main.app)
    finally:
        bff_main.read_store = original_store
        bff_main._SKILL_REGISTRY.clear()
        bff_main._SKILL_REGISTRY.update(original_skill_registry)
        bff_main._TOOL_REGISTRY.clear()
        bff_main._TOOL_REGISTRY.update(original_tool_registry)
        bff_main._PERSONA_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.update(original_persona_overlay)


# ---------------------------------------------------------------------------
# Skills endpoint
# ---------------------------------------------------------------------------

def test_persona_skills_200_envelope() -> None:
    """Skills list returns 200 with data/items/page_info envelope."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/skills", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert "items" in body
    assert "page_info" in body
    assert "meta" in body
    assert body["page_info"]["next_page_token"] is None


def test_persona_skills_entries_from_capability_snapshot() -> None:
    """Skills list entries match effective_skills from capability snapshot (cap-001)."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/skills", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"]
    # cap-001 has effective_skills: ["risk_review", "incident_triage"]
    assert len(items) == 2, f"Expected 2 effective skills, got {len(items)}: {items}"
    skill_ids = {str(s.get("skill_id") or s.get("id") or "") for s in items}
    assert "risk_review" in skill_ids, f"risk_review missing from {skill_ids}"
    assert "incident_triage" in skill_ids, f"incident_triage missing from {skill_ids}"


def test_persona_skills_each_entry_has_id_and_name() -> None:
    """Each skill entry has skill_id/id and name fields."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/skills", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    for item in resp.json()["items"]:
        assert item.get("skill_id") or item.get("id"), f"Missing id in skill entry: {item}"
        assert item.get("name"), f"Missing name in skill entry: {item}"


def test_persona_skills_404_unknown_persona() -> None:
    """Skills route returns 404 for unknown persona."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{UNKNOWN_PERSONA_ID}/skills", headers=HEADERS)
    assert resp.status_code == 404, resp.text


def test_persona_skills_401_no_auth() -> None:
    """Skills route returns 401 without authorization."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/skills")
    assert resp.status_code == 401, resp.text


def test_persona_skills_meta_surface() -> None:
    """Skills meta includes snapshot_at."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/skills", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    meta = resp.json()["meta"]
    assert "snapshot_at" in meta


# ---------------------------------------------------------------------------
# Tools endpoint
# ---------------------------------------------------------------------------

def test_persona_tools_200_envelope() -> None:
    """Tools list returns 200 with data/items/page_info envelope."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/tools", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert "items" in body
    assert "page_info" in body
    assert "meta" in body
    assert body["page_info"]["next_page_token"] is None


def test_persona_tools_entries_from_capability_snapshot() -> None:
    """Tools list entries match effective_tools from capability snapshot (cap-001)."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/tools", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"]
    # cap-001 has effective_tools: ["signal_read", "artifact_load", "telemetry_query"]
    assert len(items) == 3, f"Expected 3 effective tools, got {len(items)}: {items}"
    tool_ids = {str(t.get("tool_id") or t.get("id") or "") for t in items}
    assert "signal_read" in tool_ids, f"signal_read missing from {tool_ids}"
    assert "artifact_load" in tool_ids, f"artifact_load missing from {tool_ids}"
    assert "telemetry_query" in tool_ids, f"telemetry_query missing from {tool_ids}"


def test_persona_tools_each_entry_has_id_and_name() -> None:
    """Each tool entry has tool_id/id and name fields."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/tools", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    for item in resp.json()["items"]:
        assert item.get("tool_id") or item.get("id"), f"Missing id in tool entry: {item}"
        assert item.get("name"), f"Missing name in tool entry: {item}"


def test_persona_tools_404_unknown_persona() -> None:
    """Tools route returns 404 for unknown persona."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{UNKNOWN_PERSONA_ID}/tools", headers=HEADERS)
    assert resp.status_code == 404, resp.text


def test_persona_tools_401_no_auth() -> None:
    """Tools route returns 401 without authorization."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/tools")
    assert resp.status_code == 401, resp.text


def test_persona_tools_meta_surface() -> None:
    """Tools meta includes snapshot_at."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/tools", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    meta = resp.json()["meta"]
    assert "snapshot_at" in meta


# ---------------------------------------------------------------------------
# Capabilities surface endpoint
# ---------------------------------------------------------------------------

def test_persona_capabilities_200_envelope() -> None:
    """Capabilities returns 200 with data and meta envelope."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/capabilities", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert "meta" in body


def test_persona_capabilities_data_fields() -> None:
    """Capabilities data includes personaId, effectiveSkills, effectiveTools, effectiveWorkflows."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/capabilities", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["personaId"] == PERSONA_ID
    assert isinstance(data["effectiveSkills"], list)
    assert isinstance(data["effectiveTools"], list)
    assert isinstance(data["effectiveWorkflows"], list)
    assert isinstance(data["restrictions"], list)
    assert isinstance(data["sourceRefs"], list)


def test_persona_capabilities_matches_snapshot() -> None:
    """Capabilities data matches the cap-001 fixture for persona-alpha."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/capabilities", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # cap-001 fixture values
    assert "risk_review" in data["effectiveSkills"]
    assert "incident_triage" in data["effectiveSkills"]
    assert "signal_read" in data["effectiveTools"]
    assert "artifact_load" in data["effectiveTools"]
    assert "telemetry_query" in data["effectiveTools"]
    assert "promotion_review" in data["effectiveWorkflows"]
    assert "no_live_trade_without_approval" in data["restrictions"]


def test_persona_capabilities_404_unknown_persona() -> None:
    """Capabilities route returns 404 for unknown persona."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{UNKNOWN_PERSONA_ID}/capabilities", headers=HEADERS)
    assert resp.status_code == 404, resp.text


def test_persona_capabilities_401_no_auth() -> None:
    """Capabilities route returns 401 without authorization."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/capabilities")
    assert resp.status_code == 401, resp.text


def test_persona_capabilities_meta_snapshot_at() -> None:
    """Capabilities meta includes snapshot_at."""
    with _bff_client() as client:
        resp = client.get(f"/bff/personas/{PERSONA_ID}/capabilities", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert "snapshot_at" in resp.json()["meta"]
