"""Comprehensive unit and integration tests for Operations, OpenClaw, Workflows/Catalog, and Consultation domain ports.

Validates:
- ACG-02-006: Workflow templates, hook registry, and automation/governance catalog port reads & router integration
- ACG-02-007: OpenClaw operations snapshots, broker adapter readiness, and Research OSS preactivation with truthful error propagation
- ACG-02-008: Consultation sessions, transcripts, memos, requests, participants, outcome, and evidence reads directly backed by ConsultationServiceClient / ConsultationStore
"""
from __future__ import annotations

import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.consultation.auth as consult_auth_mod
from services.consultation.store import build_consultation_store
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.console_gap.workflows_hooks import create_workflows_hooks_router
from services.control_plane.bff.auth import policy
from services.control_plane.bff.auth.test_browser_session import (  # noqa: F401
    BASE, CREDENTIALS, ORIGIN, app_factory, login,
)
from services.control_plane.bff.ports.operations_consultation import (
    CompositeOperationsConsultationPort,
    ConsultationReaderPort,
    DomainConsultationPort,
    DomainOpenClawOperationsPort,
    DomainWorkflowCatalogPort,
    InMemoryOperationsConsultationPort,
    OpenClawOperationsReaderPort,
    OperationsConsultationPort,
    WorkflowHookCatalogReaderPort,
    _redact_consult_memo_review_payload,
    create_in_memory_operations_consultation_port,
    create_operations_consultation_port,
)
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
from services.consultation.models import (
    ConsultAuditEvent,
    ConsultEvidenceAttachment,
    ConsultGateHandoff,
    ConsultMemo,
    ConsultParticipant,
    ConsultPriority,
    ConsultRequest,
    ConsultRequestStatus,
    ConsultRequestType,
    ConsultTranscript,
    MemoStatus,
    TranscriptEvent,
)
from services.consultation.store import ConsultationStore


# =====================================================================
# Fixtures & Test Setup
# =====================================================================

@pytest.fixture
def temp_consultation_dir():
    temp_dir = tempfile.mkdtemp(prefix="pantheon_consult_test_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_identity():
    return {"subject": "operator-1", "roles": ["operator", "viewer"]}


# =====================================================================
# 1. Workflow & Hook Catalog Port Tests (ACG-02-006)
# =====================================================================

class TestWorkflowHookCatalogPort:
    def test_protocol_conformance(self):
        port = DomainWorkflowCatalogPort()
        assert isinstance(port, WorkflowHookCatalogReaderPort)

    def test_empty_dataset_sources(self):
        port = DomainWorkflowCatalogPort()
        assert port.dataset_source("workflow_templates") == "missing"
        assert port.dataset_source("hook_registry") == "missing"
        assert port.list_workflow_templates() == []
        assert port.list_hook_registry() == []
        assert port.list_governance_permissions() == []
        assert port.list_memory_governance_rules() == []
        assert port.list_consult_rules() == []
        assert port.list_route_policies() == []
        assert port.list_alpha_factory_cards() == []
        assert port.list_skills() == []
        assert port.list_tools() == []
        assert port.list_mcp_servers() == []
        assert port.list_mcp_tools() == []

    def test_in_memory_catalog_records_and_sorting(self):
        datasets = {
            "workflow_templates": [
                {"workflow_id": "wf-beta", "name": "Beta Workflow"},
                {"workflow_id": "wf-alpha", "name": "Alpha Workflow"},
            ],
            "hook_registry": [
                {"hook_id": "hook-2", "event": "on_trade"},
                {"hook_id": "hook-1", "event": "on_signal"},
            ],
            "alpha_factory_cards": [
                {"id": "card-1", "lane": "momentum", "score": 0.9},
                {"id": "card-2", "lane": "mean_reversion", "score": 0.8},
                {"id": "card-3", "lane": "momentum", "score": 0.85},
            ],
            "skills": [{"id": "skill-1", "name": "analyze_market"}],
            "tools": [{"id": "tool-1", "name": "fetch_orderbook"}],
            "mcp_servers": [{"id": "srv-1", "url": "http://localhost:8000"}],
            "mcp_tools": [{"id": "mcp-t1", "server_id": "srv-1"}],
        }
        port = DomainWorkflowCatalogPort(datasets=datasets)
        assert port.dataset_source("workflow_templates") == "in_memory"
        assert port.dataset_source("hook_registry") == "in_memory"

        # Check sorting by ID/key
        workflows = port.list_workflow_templates()
        assert [w["workflow_id"] for w in workflows] == ["wf-alpha", "wf-beta"]

        hooks = port.list_hook_registry()
        assert [h["hook_id"] for h in hooks] == ["hook-1", "hook-2"]

        # Check alpha factory cards with lane filtering and pagination
        momentum_cards = port.list_alpha_factory_cards(lane="momentum", page=1, page_size=10)
        assert len(momentum_cards) == 2
        assert {c["id"] for c in momentum_cards} == {"card-1", "card-3"}

        page1 = port.list_alpha_factory_cards(page=1, page_size=2)
        assert len(page1) == 2
        page2 = port.list_alpha_factory_cards(page=2, page_size=2)
        assert len(page2) == 1

        assert len(port.list_skills()) == 1
        assert len(port.list_tools()) == 1
        assert len(port.list_mcp_servers()) == 1
        assert len(port.list_mcp_tools()) == 1

    def test_router_integration_with_domain_port(self, mock_identity):
        port = DomainWorkflowCatalogPort(
            datasets={
                "workflow_templates": [
                    {"workflow_id": "wf-1", "name": "Nightly Strategy Review"},
                    {"workflow_id": "wf-2", "name": "Daily Risk Audit"},
                ],
                "hook_registry": [
                    {"hook_id": "hook-1", "name": "On Capital Breach"},
                ],
            }
        )
        router = create_workflows_hooks_router(
            workflow_hook_port=port,
            extract_identity=lambda auth: mock_identity,
            require_read_role=lambda ident: None,
            snapshot_now=lambda: "2026-08-28T06:00:00Z",
        )
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # GET /bff/workflows
        resp_wf = client.get("/bff/workflows?page_size=10")
        assert resp_wf.status_code == 200
        data_wf = resp_wf.json()
        assert data_wf["meta"]["status"] == "ok"
        assert data_wf["page_info"]["total"] == 2
        assert len(data_wf["items"]) == 2
        assert data_wf["items"][0]["workflow_id"] == "wf-1"

        # GET /bff/hooks
        resp_hook = client.get("/bff/hooks?page_size=10")
        assert resp_hook.status_code == 200
        data_hook = resp_hook.json()
        assert data_hook["meta"]["status"] == "ok"
        assert data_hook["page_info"]["total"] == 1
        assert data_hook["items"][0]["hook_id"] == "hook-1"


# =====================================================================
# 2. OpenClaw Operations Port Tests (ACG-02-007)
# =====================================================================

class TestOpenClawOperationsPort:
    def test_protocol_conformance(self):
        port = DomainOpenClawOperationsPort()
        assert isinstance(port, OpenClawOperationsReaderPort)

    def test_openclaw_ops_snapshot_success(self):
        mock_client = MagicMock(spec=OpenClawOpsClient)
        mock_client.configured = True
        mock_client.get_capabilities.return_value = {
            "adapter_version": "v1.2.0",
            "activation_state": "offline_activation_ready",
            "session_lifecycle_state": "active",
            "fail_closed": True,
            "supported_session_types": ["consult", "training"],
            "activation_gates": {
                "broker_execution": "OPENCLAW_LIVE_ADAPTER_ENABLED",
                "paper_adapter": "OPENCLAW_PAPER_ADAPTER_ENABLED",
                "live_adapter": "OPENCLAW_LIVE_ADAPTER_ENABLED",
                "capital_binding": "OPENCLAW_CAPITAL_BINDING_ENABLED",
            },
            "upstream": {"status": "ok", "error_code": None},
        }
        mock_client.get_upstream_status.return_value = {
            "upstream_url": "http://openclaw.internal:8080",
            "reachable": True,
            "details": {"http_status": 200, "probe": "ping"},
        }
        mock_client.list_lifecycle_sessions.return_value = {
            "sessions": [
                {
                    "session_id": "sess-1",
                    "agent_id": "agent-alpha",
                    "session_type": "consult",
                    "state": "active",
                    "operator_id": "op-1",
                    "created_at": "2026-08-28T05:00:00Z",
                    "audit_log": [{"event": "init"}],
                    "context_bundle": {"market": "us_equities"},
                }
            ]
        }
        mock_client.get_tool_policy.return_value = {
            "policy_version": "v2",
            "enforcement": "strict",
        }
        mock_client.list_invocation_audit.return_value = {
            "entries": [
                {
                    "at": "2026-08-28T05:10:00Z",
                    "request_type": "tool",
                    "trace_id": "tr-1",
                    "operator_id": "op-1",
                    "session_id": "sess-1",
                    "tool_name": "fetch_quote",
                    "policy_decision": "allow",
                    "policy_class": "read_only",
                    "outcome": "success",
                }
            ]
        }

        port = DomainOpenClawOperationsPort(client=mock_client)
        snapshot = port.get_openclaw_ops_snapshot()

        assert snapshot["overall_status"] == "ok"
        assert snapshot["surface"] == "openclaw_ops"
        assert snapshot["activation"]["adapter_version"] == "v1.2.0"
        assert snapshot["upstream"]["reachable"] is True
        assert snapshot["session_lifecycle"]["count"] == 1
        assert snapshot["session_lifecycle"]["sessions"][0]["session_id"] == "sess-1"
        assert snapshot["tool_workflow"]["audit"]["count"] == 1
        assert snapshot["tool_workflow"]["bridge_posture"]["policy_state"] == "adapter_enforcing"
        assert snapshot["allowedActions"]["canCreateSession"] is True
        assert snapshot["allowedActions"]["canInvokeTool"] is False

    def test_openclaw_truthful_error_propagation(self):
        mock_client = MagicMock(spec=OpenClawOpsClient)
        mock_client.configured = False
        mock_client.get_capabilities.side_effect = OpenClawOpsClientError(
            "OpenClaw gateway unreachable",
            status_code=503,
            error_code="gateway_unreachable",
            payload={"endpoint": "/capabilities"},
        )
        mock_client.get_upstream_status.side_effect = OpenClawOpsClientError(
            "OpenClaw upstream connection refused",
            status_code=503,
            error_code="upstream_down",
        )
        mock_client.list_lifecycle_sessions.side_effect = OpenClawOpsClientError(
            "Lifecycle sessions unavailable",
            status_code=503,
            error_code="sessions_unavailable",
        )
        mock_client.get_tool_policy.side_effect = OpenClawOpsClientError(
            "Tool policy unavailable",
            status_code=503,
            error_code="policy_unavailable",
        )
        mock_client.list_invocation_audit.side_effect = OpenClawOpsClientError(
            "Audit log unavailable",
            status_code=503,
            error_code="audit_unavailable",
        )

        port = DomainOpenClawOperationsPort(client=mock_client)
        snapshot = port.get_openclaw_ops_snapshot()

        # Truthful error surface: overall_status must report unavailable, degradation reasons must be recorded
        assert snapshot["overall_status"] == "unavailable"
        assert snapshot["service_status"]["openclaw_capabilities"]["status"] == "unavailable"
        assert snapshot["service_status"]["openclaw_capabilities"]["reason"] == "gateway_unreachable"
        assert snapshot["service_status"]["openclaw_upstream_status"]["status"] == "unavailable"
        assert snapshot["upstream"]["reachable"] is False
        assert snapshot["allowedActions"]["canCreateSession"] is False
        assert len(snapshot["degradation"]["reasons"]) > 0

    def test_openclaw_broker_adapter_readiness_fail_closed(self):
        mock_client = MagicMock(spec=OpenClawOpsClient)
        mock_client.get_broker_capabilities.return_value = {
            "sandbox_adapter_state": "activation_ready",
            "sandbox_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
            "paper_adapter_state": "gated",
            "paper_adapter_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
            "broker_sidecar_configured": False,
            "runtime_manager_configured": False,
        }
        port = DomainOpenClawOperationsPort(client=mock_client)
        readiness = port.get_openclaw_broker_adapter_readiness()

        assert readiness["surface"] == "openclaw_broker_adapter_readiness"
        assert readiness["overall_status"] == "ok"
        assert readiness["sandbox_adapter_state"] == "activation_ready"
        assert readiness["paper_adapter_state"] == "gated"
        assert readiness["canary_adapter_state"] == "fail_closed"
        assert readiness["live_adapter_state"] == "fail_closed"
        assert readiness["live_execution_enabled"] is False
        assert readiness["canary_execution_enabled"] is False
        assert readiness["is_real_capital"] is False
        assert readiness["is_real_order"] is False
        assert readiness["live_gate_reason"] == "fail_closed_explicit_gate_required"

    def test_research_oss_preactivation_snapshot_offline_gates(self):
        port = DomainOpenClawOperationsPort(service_specs={})
        snapshot = port.get_research_oss_preactivation_snapshot()
        assert snapshot["surface"] == "research_oss_activation_ready"
        assert snapshot["production_activation"] == "disabled"
        assert snapshot["activated"] is False
        assert len(snapshot["backend_inventory"]) == 7
        assert all(b["production_activation"] == "disabled" for b in snapshot["backend_inventory"])


# =====================================================================
# 3. Consultation Port Tests (ACG-02-008)
# =====================================================================

class TestConsultationPort:
    def test_protocol_conformance(self, temp_consultation_dir):
        store = ConsultationStore(temp_consultation_dir)
        port = DomainConsultationPort(store=store)
        assert isinstance(port, ConsultationReaderPort)

    def test_consult_memo_redaction(self):
        raw_memo = {
            "memo_id": "memo-101",
            "title": "Strategy Review Memo",
            "findings": [
                {
                    "issue": "Model risk",
                    "policy_internals": "secret_matrix_weights",
                    "recommendation": "Adjust parameters",
                }
            ],
            "secret_credentials": "API_KEY_12345",
            "capabilityMap": {"internal_score": 99},
            "summary": "Reviewed policy_internals and verified memory_trace behavior.",
        }
        redacted = _redact_consult_memo_review_payload(raw_memo)
        assert "secret_credentials" not in redacted
        assert "capabilityMap" not in redacted
        assert "policy_internals" not in redacted["findings"][0]
        assert "[redacted]" in redacted["summary"]
        assert "policy_internals" not in redacted["summary"]
        assert "memory_trace" not in redacted["summary"]

    def test_consult_requests_lifecycle_with_store(self, temp_consultation_dir):
        store = ConsultationStore(temp_consultation_dir)
        port = DomainConsultationPort(store=store)

        # Create request
        created = port.create_consult_request(
            from_persona_id="persona-risk",
            target_type="strategy",
            target_ref="strat-1",
            task="Perform redteam review of execution risk",
            context_refs=[{"type": "strategy_spec", "id": "strat-1"}],
            priority="urgent",
            consultation_type="redteam",
            actor_id="operator-1",
            created_at="2026-08-28T06:00:00Z",
        )
        assert created["request_id"].startswith("cr-")
        req_id = created["request_id"]
        assert created["status"] == "created"
        assert created["priority"] == "urgent"
        assert created["allowedActions"]["canCancel"] is True

        # Get request
        fetched = port.get_consult_request(req_id)
        assert fetched is not None
        assert fetched["request_id"] == req_id
        assert fetched["from_persona_id"] == "persona-risk"
        assert fetched["target_ref"] == "strat-1"

        # List requests
        reqs = port.list_consult_requests(statuses=["created"], target_type="strategy")
        assert len(reqs) == 1
        assert reqs[0]["request_id"] == req_id

        # Cancel request
        canceled = port.cancel_consult_request(req_id, actor_id="operator-1", canceled_at="2026-08-28T06:05:00Z")
        assert canceled is not None
        assert canceled["status"] == "canceled"
        assert canceled["allowedActions"]["canCancel"] is False

        # Attempt to cancel again returns None (already canceled)
        assert port.cancel_consult_request(req_id, actor_id="operator-1") is None

    def test_consult_sessions_transcripts_and_outcomes_with_store(self, temp_consultation_dir):
        store = ConsultationStore(temp_consultation_dir)

        # Insert a request with linked session and consultation metadata
        request = ConsultRequest(
            request_id="cr-20260828-abc12345",
            request_type=ConsultRequestType.STRATEGY_REVIEW,
            requested_by={"actor_type": "operator", "actor_id": "op-1"},
            from_persona_id="persona-analyst",
            target_type="strategy",
            target_id="strat-alpha",
            task="Verify alpha signal decay",
            consultation_type="strategy_review",
            context_refs=["strategy_spec:strat-alpha"],
            priority=ConsultPriority.HIGH,
            status=ConsultRequestStatus.PUBLISHED,
            trace_id="tr-cr-20260828-abc12345",
            linked_session_id="sess-root-1",
            completed_at="2026-08-28T06:30:00Z",
            metadata={
                "consultation": {
                    "consultation_type": "strategy_review",
                    "requester_session_id": "sess-root-1",
                    "responder_session_ids": ["sess-resp-1"],
                    "committee_session_ids": ["sess-comm-1"],
                    "outcome": "approved",
                    "evidence_refs": [{"id": "ev-1", "type": "chart"}],
                    "committee_participants": [
                        {
                            "session_id": "sess-comm-1",
                            "persona_id": "persona-reviewer",
                            "status": "active",
                            "role": "committee_participant",
                        }
                    ],
                }
            },
            created_at="2026-08-28T06:00:00Z",
        )
        store.put_request(request)

        # Insert transcript events
        transcript = ConsultTranscript(
            transcript_id="tr-sess-root-1",
            request_id="cr-20260828-abc12345",
            session_id="sess-root-1",
            events=[
                TranscriptEvent(
                    event_id="ev-1",
                    session_id="sess-root-1",
                    sequence_no=1,
                    event_type="message",
                    actor={"actor_type": "persona", "actor_id": "persona-analyst"},
                    content={"format": "json", "text": "Initial proposal"},
                    event_time="2026-08-28T06:01:00Z",
                ),
                TranscriptEvent(
                    event_id="ev-2",
                    session_id="sess-root-1",
                    sequence_no=2,
                    event_type="response",
                    actor={"actor_type": "persona", "actor_id": "persona-reviewer"},
                    content={"format": "json", "text": "Proposal evaluated"},
                    event_time="2026-08-28T06:02:00Z",
                ),
            ],
            created_at="2026-08-28T06:00:00Z",
        )
        store.put_transcript(transcript)

        # Insert memo
        memo = ConsultMemo(
            memo_id="memo-20260828-001",
            request_id="cr-20260828-abc12345",
            memo_type="risk_review",
            status=MemoStatus.PUBLISHED,
            author_type="persona",
            author_ref="persona-reviewer",
            target_type="strategy",
            target_id="strat-alpha",
            summary="Approved strategy deployment.",
            findings=[
                {
                    "severity": "info",
                    "category": "risk_check",
                    "claim": "None",
                    "recommendation": "Deploy with 5% capital cap",
                    "evidence_refs": ["ev-1"],
                }
            ],
            recommendation="approve",
            trace_id="tr-memo-20260828-001",
            published_at="2026-08-28T06:30:00Z",
            created_at="2026-08-28T06:20:00Z",
        )
        store.put_memo(memo)

        port = DomainConsultationPort(store=store)

        # Test session reads
        sessions = port.list_consultations_for_persona(persona_id="persona-analyst")
        assert sessions is not None
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess-root-1"
        assert sessions[0]["status"] == "completed"

        session = port.get_consultation("sess-root-1")
        assert session is not None
        assert session["session_id"] == "sess-root-1"

        # Test participants
        participants = port.get_consultation_participants("sess-root-1")
        assert participants is not None
        assert len(participants) == 2
        roles = {p["session_id"]: p["consultation_role"] for p in participants}
        assert roles["sess-root-1"] == "requester"
        assert roles["sess-comm-1"] == "committee_participant"

        # Test outcome & evidence
        outcome = port.get_consultation_outcome("sess-root-1")
        assert outcome is not None
        assert outcome["metadata"]["consultation"]["outcome"] == "approved"

        evidence = port.get_consultation_evidence("sess-root-1")
        assert evidence is not None
        assert len(evidence) == 1
        assert evidence[0]["id"] == "ev-1"

        # Test transcript
        trans = port.get_consult_transcript("sess-root-1", page_size=10)
        assert trans is not None
        assert trans["surface_state"] == "ok"
        assert trans["total_events"] == 2
        assert len(trans["events"]) == 2
        assert trans["events"][0]["sequence_no"] == 1
        assert trans["events"][1]["sequence_no"] == 2

        # Test memo
        memos = port.list_consult_memos(statuses=["published"])
        assert len(memos) == 1
        assert memos[0]["memo_id"] == "memo-20260828-001"

        memo_detail = port.get_consult_memo("memo-20260828-001")
        assert memo_detail is not None
        assert memo_detail["memo_id"] == "memo-20260828-001"
        assert memo_detail["linked_session_id"] == "sess-root-1"
        assert memo_detail["recommendations"] == ["Deploy with 5% capital cap"]

    def test_transcript_gap_detection(self, temp_consultation_dir):
        store = ConsultationStore(temp_consultation_dir)
        request = ConsultRequest(
            request_id="cr-gap-test",
            request_type=ConsultRequestType.STRATEGY_REVIEW,
            requested_by={"actor_type": "operator", "actor_id": "op-1"},
            from_persona_id="persona-1",
            target_type="strategy",
            target_id="strat-1",
            task="Gap test",
            consultation_type="strategy_review",
            context_refs=[],
            priority=ConsultPriority.NORMAL,
            status=ConsultRequestStatus.IN_PROGRESS,
            trace_id="tr-cr-gap-test",
            linked_session_id="sess-gap-1",
            created_at="2026-08-28T06:00:00Z",
        )
        store.put_request(request)

        # Sequence numbers 1 and 3 (missing sequence 2)
        transcript = ConsultTranscript(
            transcript_id="tr-sess-gap-1",
            request_id="cr-gap-test",
            session_id="sess-gap-1",
            events=[
                TranscriptEvent(
                    event_id="ev-1",
                    session_id="sess-gap-1",
                    sequence_no=1,
                    event_type="message",
                    actor={"actor_type": "persona", "actor_id": "persona-1"},
                    content={"format": "json", "text": "Event 1"},
                    event_time="2026-08-28T06:01:00Z",
                ),
                TranscriptEvent(
                    event_id="ev-3",
                    session_id="sess-gap-1",
                    sequence_no=3,
                    event_type="message",
                    actor={"actor_type": "persona", "actor_id": "persona-1"},
                    content={"format": "json", "text": "Event 3"},
                    event_time="2026-08-28T06:03:00Z",
                ),
            ],
            created_at="2026-08-28T06:00:00Z",
        )
        store.put_transcript(transcript)

        port = DomainConsultationPort(store=store)
        trans = port.get_consult_transcript("sess-gap-1")
        assert trans is not None
        assert trans["surface_state"] == "degraded"


# =====================================================================
# 4. Composite & In-Memory Ports Tests
# =====================================================================

class TestCompositeAndInMemoryPorts:
    def test_composite_port_delegation(self):
        workflow_port = DomainWorkflowCatalogPort(
            datasets={"skills": [{"id": "s1"}]}
        )
        openclaw_port = DomainOpenClawOperationsPort()
        consultation_port = DomainConsultationPort()

        composite = CompositeOperationsConsultationPort(
            workflow_port=workflow_port,
            openclaw_port=openclaw_port,
            consultation_port=consultation_port,
        )
        assert isinstance(composite, OperationsConsultationPort)
        assert len(composite.list_skills()) == 1
        assert composite.get_openclaw_broker_adapter_readiness()["surface"] == "openclaw_broker_adapter_readiness"
        assert composite.dataset_source("workflow_templates") == "missing"

    def test_in_memory_port_factory_and_crud(self):
        in_mem = create_in_memory_operations_consultation_port(
            skills=[{"id": "s-1", "name": "quant"}],
            consult_requests=[
                {"request_id": "cr-mem-1", "status": "created", "target_type": "strategy"}
            ],
        )
        assert isinstance(in_mem, OperationsConsultationPort)
        assert len(in_mem.list_skills()) == 1
        assert len(in_mem.list_consult_requests()) == 1

        created = in_mem.create_consult_request(
            from_persona_id="persona-1",
            target_type="plan",
            target_ref="plan-1",
            task="Test in-memory create",
            context_refs=[],
            priority="normal",
            consultation_type="strategy_review",
            actor_id="operator-1",
        )
        assert created["request_id"].startswith("cr-")
        assert in_mem.get_consult_request(created["request_id"]) is not None

        canceled = in_mem.cancel_consult_request(created["request_id"], actor_id="operator-1")
        assert canceled is not None
        assert canceled["status"] == "canceled"

    def test_create_operations_consultation_port_factory(self, temp_consultation_dir):
        store = ConsultationStore(temp_consultation_dir)
        port = create_operations_consultation_port(
            consultation_store=store,
        )
        assert isinstance(port, OperationsConsultationPort)
        assert port.dataset_source("consult_requests") == "service_store"


# =====================================================================
# 5. Consultation HTTP Owner Readback & Truthful Error Tests
# =====================================================================

class TestConsultationOwnerHttpReadback:
    @pytest.fixture
    def consultation_server(self, temp_consultation_dir, monkeypatch):
        import importlib
        consult_main_mod = importlib.import_module("services.consultation.main")
        consult_app = getattr(consult_main_mod, "app")
        test_store = build_consultation_store(temp_consultation_dir)
        monkeypatch.setattr(consult_main_mod, "store", test_store)
        monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
        monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", "pantheon-local-consultation-service")
        monkeypatch.setenv("CONSULTATION_SERVICE_ACTOR", "operator-bff")
        monkeypatch.setenv("CONSULTATION_DATA_DIR", temp_consultation_dir)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        base_url = f"http://127.0.0.1:{port}"
        monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", base_url)

        server = uvicorn.Server(uvicorn.Config(app=consult_app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            time.sleep(0.01)

        yield {
            "base_url": base_url,
            "port": port,
            "store": test_store,
            "server": server,
        }
        server.should_exit = True
        thread.join(timeout=2)

    @staticmethod
    def _unit_synthetic_jwt(tenant_id: str) -> str:
        """Low-level unsigned helper for unit-only testing."""
        body = base64.urlsafe_b64encode(
            json.dumps({"tenant_id": tenant_id, "allowed_tenants": [tenant_id]}).encode()
        ).decode().rstrip("=")
        return f"header.{body}.signature"

    @pytest.fixture
    def bff_client(self, app_factory, consultation_server, monkeypatch):
        monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_OPERATOR_ALLOWED_TENANTS", "tenant-dev,tenant-other,tenant-empty")
        monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID", "test-viewer")
        monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET", "test-viewer-password")
        monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_TENANT_ID", "tenant-other")
        monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_ALLOWED_TENANTS", "tenant-other")

        bff_app = app_factory()
        bff_app.add_middleware(OwnerReadContextMiddleware)
        port_instance = DomainConsultationPort()
        bff_app.include_router(
            create_governance_router(
                get_read_store=lambda: port_instance,
                extract_identity=policy.extract_identity,
                require_read_role=policy.require_read_role,
                require_operator_role=policy.require_operator_role,
                bff_error=policy.bff_error,
            )
        )
        return TestClient(bff_app, base_url=BASE)

    def test_unsigned_synthetic_jwt_rejected_by_strict_bff(self, consultation_server, bff_client):
        token = self._unit_synthetic_jwt("tenant-dev")
        resp = bff_client.get(
            "/api/v1/consult/requests",
            headers={"Authorization": f"Bearer {token}", "x-tenant-id": "tenant-dev"},
        )
        assert resp.status_code == 401

    def test_http_owner_draft_list_and_detail_with_verified_tenant(self, consultation_server, bff_client):
        # 1. Seed labelled DRAFT request into canonical owner store
        req = ConsultRequest(
            request_id="cr-test-draft-001",
            request_type=ConsultRequestType.STRATEGY_REVIEW,
            requested_by={"actor_type": "operator", "actor_id": "operator-dev"},
            from_persona_id="persona-analyst",
            target_type="strategy",
            target_id="strat-alpha-01",
            task="Perform draft strategy readback verification",
            consultation_type="strategy_review",
            priority=ConsultPriority.NORMAL,
            status=ConsultRequestStatus.DRAFT,
            tenant_id="tenant-dev",
            created_at="2026-10-17T00:00:00Z",
            trace_id="tr-test-draft-001",
        )
        consultation_server["store"].put_request(req)

        # 2. Genuine dev-login authentication
        login_res = bff_client.post("/bff/auth/dev-login", json=CREDENTIALS)
        assert login_res.status_code == 200, login_res.text
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "x-tenant-id": "tenant-dev"}

        # List through BFF reads the labelled DRAFT
        resp = bff_client.get("/api/v1/consult/requests", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["page_info"]["total"] == 1
        assert len(data["data"]) == 1
        assert data["data"][0]["request_id"] == "cr-test-draft-001"
        assert data["data"][0]["status"] == "created"
        assert data["meta"]["surfaces"]["consult_request_list"]["source"] == "service_client"

        # Detail through BFF reads the labelled DRAFT
        resp_detail = bff_client.get("/api/v1/consult/requests/cr-test-draft-001", headers=headers)
        assert resp_detail.status_code == 200, resp_detail.text
        detail = resp_detail.json()
        assert detail["request_id"] == "cr-test-draft-001"
        assert detail["status"] == "created"
        assert detail["allowedActions"]["canCancel"] is True

        # Also verify genuine cookie session authentication
        login_cookie = login(bff_client)
        assert login_cookie.status_code == 200
        resp_cookie = bff_client.get("/api/v1/consult/requests", headers={"x-tenant-id": "tenant-dev"})
        assert resp_cookie.status_code == 200
        assert resp_cookie.json()["page_info"]["total"] == 1

    def test_http_owner_overlapping_tenant_isolation_and_empty_list(self, consultation_server, bff_client):
        # Seed draft in tenant-dev
        req_dev = ConsultRequest(
            request_id="cr-tenant-dev-only",
            request_type=ConsultRequestType.STRATEGY_REVIEW,
            requested_by={"actor_type": "operator", "actor_id": "op-dev"},
            from_persona_id="persona-dev",
            target_type="strategy",
            target_id="strat-dev",
            task="Dev tenant request",
            consultation_type="strategy_review",
            priority=ConsultPriority.NORMAL,
            status=ConsultRequestStatus.DRAFT,
            tenant_id="tenant-dev",
            created_at="2026-10-17T00:00:00Z",
            trace_id="tr-tenant-dev",
        )
        consultation_server["store"].put_request(req_dev)

        # Seed draft in tenant-other
        req_other = ConsultRequest(
            request_id="cr-tenant-other-only",
            request_type=ConsultRequestType.STRATEGY_REVIEW,
            requested_by={"actor_type": "operator", "actor_id": "op-other"},
            from_persona_id="persona-other",
            target_type="strategy",
            target_id="strat-other",
            task="Other tenant request",
            consultation_type="strategy_review",
            priority=ConsultPriority.NORMAL,
            status=ConsultRequestStatus.DRAFT,
            tenant_id="tenant-other",
            created_at="2026-10-17T00:00:00Z",
            trace_id="tr-tenant-other",
        )
        consultation_server["store"].put_request(req_other)

        # Genuine authentication for both tenant scopes
        res_dev = bff_client.post("/bff/auth/dev-login", json=CREDENTIALS)
        assert res_dev.status_code == 200
        token_dev = res_dev.json()["access_token"]
        headers_dev = {"Authorization": f"Bearer {token_dev}", "x-tenant-id": "tenant-dev"}

        res_other = bff_client.post("/bff/auth/dev-login", json={
            "grant_type": "client_credentials",
            "client_id": "test-viewer",
            "client_secret": "test-viewer-password",
        })
        assert res_other.status_code == 200
        token_other = res_other.json()["access_token"]
        headers_other = {"Authorization": f"Bearer {token_other}", "x-tenant-id": "tenant-other"}

        # Explicit synchronization and observed overlap
        real_list_requests = consultation_server["store"].list_requests
        overlap_observed = []
        active_count = 0
        active_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def synchronized_list_requests(*args, **kwargs):
            nonlocal active_count
            with active_lock:
                active_count += 1
                overlap_observed.append(active_count)
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass
            try:
                return real_list_requests(*args, **kwargs)
            finally:
                with active_lock:
                    active_count -= 1

        consultation_server["store"].list_requests = synchronized_list_requests

        results = {}

        def request_worker(name, headers):
            resp = bff_client.get("/api/v1/consult/requests", headers=headers)
            results[name] = resp

        t1 = threading.Thread(target=request_worker, args=("dev", headers_dev))
        t2 = threading.Thread(target=request_worker, args=("other", headers_other))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Restore original method
        consultation_server["store"].list_requests = real_list_requests

        # Verify actual overlap was observed (both requests active simultaneously)
        assert max(overlap_observed) >= 2

        # Verify tenant-dev response contains ONLY tenant-dev items
        resp_dev = results["dev"]
        assert resp_dev.status_code == 200
        dev_ids = [item["request_id"] for item in resp_dev.json()["data"]]
        assert dev_ids == ["cr-tenant-dev-only"]

        # Verify tenant-other response contains ONLY tenant-other items
        resp_other = results["other"]
        assert resp_other.status_code == 200
        other_ids = [item["request_id"] for item in resp_other.json()["data"]]
        assert other_ids == ["cr-tenant-other-only"]

        # Detail isolation: tenant-dev cannot access tenant-other item (404)
        assert bff_client.get("/api/v1/consult/requests/cr-tenant-other-only", headers=headers_dev).status_code == 404
        assert bff_client.get("/api/v1/consult/requests/cr-tenant-dev-only", headers=headers_dev).status_code == 200

        # Detail isolation: tenant-other cannot access tenant-dev item (404)
        assert bff_client.get("/api/v1/consult/requests/cr-tenant-dev-only", headers=headers_other).status_code == 404
        assert bff_client.get("/api/v1/consult/requests/cr-tenant-other-only", headers=headers_other).status_code == 200

        # Genuinely empty list for third tenant with no records
        headers_empty = {"Authorization": f"Bearer {token_dev}", "x-tenant-id": "tenant-empty"}
        resp_empty = bff_client.get("/api/v1/consult/requests", headers=headers_empty)
        assert resp_empty.status_code == 200
        assert resp_empty.json()["page_info"]["total"] == 0
        assert resp_empty.json()["data"] == []

    def test_http_owner_genuine_missing_id_returns_404(self, consultation_server, bff_client):
        res = bff_client.post("/bff/auth/dev-login", json=CREDENTIALS)
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "x-tenant-id": "tenant-dev"}
        resp = bff_client.get("/api/v1/consult/requests/cr-does-not-exist", headers=headers)
        assert resp.status_code == 404

    def test_http_owner_wrong_machine_auth_visible_error(self, consultation_server, bff_client, monkeypatch):
        # Set wrong machine token on BFF client side without modifying server authenticate
        monkeypatch.setenv("BFF_CONSULTATION_SERVICE_TOKEN", "wrong-machine-token")

        res = bff_client.post("/bff/auth/dev-login", json=CREDENTIALS)
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "x-tenant-id": "tenant-dev"}

        resp_list = bff_client.get("/api/v1/consult/requests", headers=headers)
        assert resp_list.status_code == 403
        assert resp_list.json()["detail"]["error"]["code"] == "DOWNSTREAM_AUTH_ERROR"

        resp_detail = bff_client.get("/api/v1/consult/requests/cr-test-01", headers=headers)
        assert resp_detail.status_code == 403
        assert resp_detail.json()["detail"]["error"]["code"] == "DOWNSTREAM_AUTH_ERROR"

    def test_http_owner_missing_machine_auth_visible_error(self, consultation_server, bff_client, monkeypatch):
        # Set missing machine token on BFF client side without modifying server authenticate
        monkeypatch.setenv("BFF_CONSULTATION_SERVICE_TOKEN", "")

        res = bff_client.post("/bff/auth/dev-login", json=CREDENTIALS)
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "x-tenant-id": "tenant-dev"}

        resp_list = bff_client.get("/api/v1/consult/requests", headers=headers)
        assert resp_list.status_code in (401, 403)
        assert resp_list.json()["detail"]["error"]["code"] == "DOWNSTREAM_AUTH_ERROR"

        resp_detail = bff_client.get("/api/v1/consult/requests/cr-test-01", headers=headers)
        assert resp_detail.status_code in (401, 403)
        assert resp_detail.json()["detail"]["error"]["code"] == "DOWNSTREAM_AUTH_ERROR"

    def test_http_owner_foreign_and_body_tenant_negatives(self, consultation_server, bff_client):
        res = bff_client.post("/bff/auth/dev-login", json=CREDENTIALS)
        token = res.json()["access_token"]

        # Foreign tenant in header
        resp_foreign_header = bff_client.get(
            "/api/v1/consult/requests",
            headers={"Authorization": f"Bearer {token}", "x-tenant-id": "foreign-tenant"},
        )
        assert resp_foreign_header.status_code == 403

        # Foreign tenant in body
        port_instance = DomainConsultationPort()
        from services.control_plane.bff.core.owner_reads import authorization, selected_tenant
        auth_tok = authorization.set(f"Bearer {token}")
        sel_tok = selected_tenant.set("tenant-dev")
        try:
            from services.control_plane.bff.command_adapters.base import ActionUnavailableError
            with pytest.raises(ActionUnavailableError) as exc_info:
                port_instance.create_consult_request(
                    from_persona_id="p1",
                    target_type="strategy",
                    target_ref="s1",
                    task="task",
                    context_refs=[],
                    priority="normal",
                    consultation_type="strategy_review",
                    actor_id="op-1",
                    tenant_id="foreign-tenant",
                )
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail["error"]["code"] == "TENANT_MISMATCH"
        finally:
            selected_tenant.reset(sel_tok)
            authorization.reset(auth_tok)

    def test_http_owner_outage_visible_error_without_fallback(self, monkeypatch, bff_client):
        # Point to unreachable port (outage)
        monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", "http://127.0.0.1:1")

        res = bff_client.post("/bff/auth/dev-login", json=CREDENTIALS)
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "x-tenant-id": "tenant-dev"}

        resp_list = bff_client.get("/api/v1/consult/requests", headers=headers)
        assert resp_list.status_code == 503
        assert resp_list.json()["detail"]["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

        resp_detail = bff_client.get("/api/v1/consult/requests/cr-outage-01", headers=headers)
        assert resp_detail.status_code == 503
        assert resp_detail.json()["detail"]["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    def test_http_owner_malformed_json_visible_error_without_fallback(self, monkeypatch, bff_client):
        class MalformedHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"MALFORMED_NON_JSON{{{")
            def log_message(self, *args): pass

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        server = HTTPServer(("127.0.0.1", port), MalformedHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", f"http://127.0.0.1:{port}")

        res = bff_client.post("/bff/auth/dev-login", json=CREDENTIALS)
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "x-tenant-id": "tenant-dev"}

        try:
            resp_list = bff_client.get("/api/v1/consult/requests", headers=headers)
            assert resp_list.status_code == 503
            assert resp_list.json()["detail"]["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        finally:
            server.shutdown()
