"""Comprehensive unit and integration tests for Operations, OpenClaw, Workflows/Catalog, and Consultation domain ports.

Validates:
- ACG-02-006: Workflow templates, hook registry, and automation/governance catalog port reads & router integration
- ACG-02-007: OpenClaw operations snapshots, broker adapter readiness, and Research OSS preactivation with truthful error propagation
- ACG-02-008: Consultation sessions, transcripts, memos, requests, participants, outcome, and evidence reads directly backed by ConsultationServiceClient / ConsultationStore
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from console_gap.workflows_hooks import create_workflows_hooks_router
from ports.operations_consultation import (
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
from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
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
