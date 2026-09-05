"""Tests for BFF-CONSOL-009: Canonical fixture pack B smoke coverage.

Acceptance criteria verified:
- 6 families each have ≥1 non-empty fixture
- v5 intervention links to governed remediation skeleton
- agora session is active and has an sse_topic
- research ticket links to analysis via experiment
- runtime fixture is paper-canary with fail_closed=true
- authenticated live smoke: /bff routes return data_count ≥1
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from typing import Any

from ports import ReadSurfacePorts

HEADERS = {"Authorization": "Bearer op-2:operator"}
FIXTURE_PATH = Path(__file__).resolve().parent / "data" / "fixtures_pack_b.json"

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


class FixturePackBTestReadPorts(ReadSurfacePorts):
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

    def list_evolution_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("evolution_decisions")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_evolution_decision(self, decision_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("evolution_decisions")
        if isinstance(ds, dict):
            return ds.get(str(decision_id or ""))
        return next((d for d in ds if d.get("id") == decision_id or d.get("decision_id") == decision_id), None)

    def list_v5_interventions(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("v5_interventions")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_v5_intervention(self, intv_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("v5_interventions")
        if isinstance(ds, dict):
            return ds.get(str(intv_id or ""))
        return next((i for i in ds if i.get("id") == intv_id or i.get("intervention_id") == intv_id), None)

    def list_agora_signals(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("agora_signals")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_agora_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("agora_sessions")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_agora_session(self, session_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("agora_sessions")
        if isinstance(ds, dict):
            return ds.get(str(session_id or ""))
        return next((s for s in ds if s.get("sessionId") == session_id or s.get("session_id") == session_id or s.get("id") == session_id), None)

    def list_research_tickets(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("research_tickets")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_research_ticket(self, ticket_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("research_tickets")
        if isinstance(ds, dict):
            return ds.get(str(ticket_id or ""))
        return next((t for t in ds if t.get("id") == ticket_id or t.get("ticket_id") == ticket_id), None)

    def list_deployment_plans(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("deployment_plans") or self._get_dataset("runtime_bindings")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_deployment_plan(self, plan_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("deployment_plans") or self._get_dataset("runtime_bindings")
        if isinstance(ds, dict):
            return ds.get(str(plan_id or ""))
        return next((p for p in ds if p.get("id") == plan_id or p.get("plan_id") == plan_id), None)

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("runtime_bindings")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)


def _fresh_pack_b_client(td: str) -> TestClient:
    bff_main.read_store = FixturePackBTestReadPorts(
        allow_local_snapshot_fallback=True,
    )
    bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._PERSONA_BFF_OVERLAY.clear()
    return TestClient(bff_main.app)


def _list_payload_count(payload: dict) -> int:
    rows = payload.get("data")
    if rows is None:
        rows = payload.get("items")
    assert isinstance(rows, list), f"Expected list, got: {type(rows)}"
    return len(rows)


def test_fixture_pack_b_declares_all_required_families() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["policy"]["paper_canary_truth_impact"] == "none"
    for family in ("evolution", "research", "artifacts", "v5_interventions", "agora", "runtimes"):
        assert payload["families"][family], f"Family '{family}' must be non-empty"


def test_fixture_pack_b_v5_intervention_has_remediation_skeleton() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    interventions = payload["datasets"]["v5_interventions"]
    assert interventions, "v5_interventions must have ≥1 entry"
    for intv_id, intv in interventions.items():
        assert intv.get("kind"), f"{intv_id}: missing kind"
        assert intv.get("status"), f"{intv_id}: missing status"
        assert intv.get("triggered_at"), f"{intv_id}: missing triggered_at"
        assert "remediation_skeleton" in intv, f"{intv_id}: missing governed remediation_skeleton"
        skel = intv["remediation_skeleton"]
        assert skel.get("two_man_rule_enforced") is True, f"{intv_id}: two_man_rule_enforced must be true"
        assert skel.get("remediation_actions_available"), f"{intv_id}: remediation_actions_available must be non-empty"


def test_fixture_pack_b_agora_session_is_active_with_sse_topic() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sessions = payload["datasets"]["agora_sessions"]
    assert sessions, "agora_sessions must have ≥1 entry"
    active_sessions = [s for s in sessions.values() if s.get("status") == "active"]
    assert active_sessions, "At least one agora session must have status=active"
    for s in active_sessions:
        assert s.get("sse_topic"), f"Active agora session {s.get('sessionId')} must have sse_topic"
        assert s.get("messages"), f"Active agora session {s.get('sessionId')} must have ≥1 message"


def test_fixture_pack_b_research_ticket_links_analysis() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    tickets = payload["datasets"]["research_tickets"]
    analyses = payload["datasets"]["research_analyses"]
    assert tickets, "research_tickets must have ≥1 entry"
    assert analyses, "research_analyses must have ≥1 entry"
    for ticket in tickets.values():
        assert ticket.get("linked_experiments"), f"Ticket {ticket.get('ticket_id')} must have linked_experiments"
    for analysis in analyses.values():
        assert analysis.get("ticket_id"), f"Analysis {analysis.get('analysis_id')} must reference a ticket_id"
        assert analysis.get("experiment_id"), f"Analysis {analysis.get('analysis_id')} must reference an experiment_id"


def test_fixture_pack_b_runtime_is_fail_closed_paper_canary() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    runtimes = payload["datasets"]["runtime_bindings"]
    assert runtimes, "runtime_bindings must have ≥1 entry"
    for rt_id, rt in runtimes.items():
        assert rt.get("fail_closed") is True, f"{rt_id}: fail_closed must be true"
        assert rt.get("live_write_enabled") is False, f"{rt_id}: live_write_enabled must be false"
        assert rt.get("deployment_stage") == "paper", f"{rt_id}: deployment_stage must be paper"


def test_pack_b_evolution_live_list_returns_non_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
                client = _fresh_pack_b_client(td)
                resp = client.get("/api/v1/evolution-decisions", headers=HEADERS)
                assert resp.status_code == 200, f"/api/v1/evolution-decisions: {resp.text}"
                data = resp.json()
                items = data.get("data") or data.get("items") or []
                assert len(items) >= 1, "evolution-decisions must return ≥1 record"
        finally:
            bff_main.read_store = original


def test_pack_b_v5_interventions_live_list_returns_non_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
                client = _fresh_pack_b_client(td)
                # Seed the intervention from fixture pack B into the in-memory store
                payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
                interventions = payload["datasets"]["v5_interventions"]
                original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
                bff_main._V5_INTERVENTIONS_STORE.extend(interventions.values())
                try:
                    resp = client.get("/bff/v5/interventions", headers=HEADERS)
                    assert resp.status_code == 200, f"/bff/v5/interventions: {resp.text}"
                    body = resp.json()
                    assert body["count"] >= 1, "v5/interventions must return ≥1 record"
                    assert any(
                        item["intervention_id"] == "intv-pack-b-001"
                        for item in body["items"]
                    )
                finally:
                    bff_main._V5_INTERVENTIONS_STORE.clear()
                    bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)
        finally:
            bff_main.read_store = original


def test_pack_b_agora_signals_live_list_returns_non_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
                client = _fresh_pack_b_client(td)
                resp = client.get("/bff/agora/signals", headers=HEADERS)
                assert resp.status_code == 200, f"/bff/agora/signals: {resp.text}"
                data = resp.json()
                items = data.get("data") or data.get("items") or []
                assert len(items) >= 1, "agora/signals must return ≥1 record"
        finally:
            bff_main.read_store = original


def test_pack_b_agora_sessions_live_list_returns_non_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
                client = _fresh_pack_b_client(td)
                resp = client.get("/bff/agora/sessions", headers=HEADERS)
                assert resp.status_code == 200, f"/bff/agora/sessions: {resp.text}"
                data = resp.json()
                items = data.get("data") or data.get("items") or []
                assert len(items) >= 1, "agora/sessions must return ≥1 record"
        finally:
            bff_main.read_store = original


def test_pack_b_research_tickets_live_list_returns_non_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
                client = _fresh_pack_b_client(td)
                resp = client.get("/api/v1/research/tickets", headers=HEADERS)
                assert resp.status_code == 200, f"/api/v1/research/tickets: {resp.text}"
                data = resp.json()
                items = data.get("data") or data.get("items") or []
                assert len(items) >= 1, "research/tickets must return ≥1 record"
        finally:
            bff_main.read_store = original


def test_pack_b_runtime_bindings_live_list_returns_non_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            with mock.patch.dict(os.environ, SERVICE_ENV_BLANKS, clear=False):
                client = _fresh_pack_b_client(td)
                resp = client.get("/bff/deployments", headers=HEADERS)
                assert resp.status_code == 200, f"/bff/deployments: {resp.text}"
                data = resp.json()
                items = data.get("data") or data.get("items") or []
                assert len(items) >= 1, "deployments (runtime-linked) must return ≥1 record"
        finally:
            bff_main.read_store = original
