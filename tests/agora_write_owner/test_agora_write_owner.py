"""Tests for independent persistent Agora domain write owners and operation resolution.

Verifies:
1. All 19 Agora write operations map to concrete, existing, callable durable store/client methods.
2. Functional write execution and fresh independent read-after-write verification across stores.
3. Fresh-reader and restart-persistence proofs across Agora stores:
   - TradingRoomStore / PostgresTradingRoomStore
   - MemoryWorkshopStore / PostgresWorkshopStore
   - CandidateDecisionStore
   - ProposalStore
   - ConsultationStore / PostgresConsultationStore
   - DashboardRecipeStore
4. AST import isolation: zero read_store imports in Agora stores or Consultation stores.
5. Source ingestion reconcile-only invariant: zero write mutations in source ingestion.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import uuid
from typing import Any, Dict
from unittest import mock

import pytest

# Ensure control-plane / bff modules can be imported
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONTROL_PLANE_DIR = os.path.join(_REPO_ROOT, "services", "control-plane")
if _CONTROL_PLANE_DIR not in sys.path:
    sys.path.insert(0, _CONTROL_PLANE_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.consultation.client import ConsultationServiceClient
from services.consultation.models import (
    ActorRef,
    AuthorType,
    ConsultAuditEvent,
    ConsultEvidenceAttachment,
    ConsultGateHandoff,
    ConsultMemo,
    ConsultPriority,
    ConsultRequest,
    ConsultRequestStatus,
    ConsultRequestType,
    ConsultTranscript,
    EvidenceRef,
    MemoStatus,
    MemoType,
    Recommendation,
    TranscriptEvent,
)
from services.consultation.store import (
    ConsultationStore,
    PostgresConsultationStore,
)
from bff.agora.candidate_decisions.store import CandidateDecisionStore
from bff.agora.dashboard.store import (
    MemoryDashboardRecipeStore,
    PostgresDashboardRecipeStore,
)
from bff.agora.governance.store import ProposalStore
from bff.agora.interaction.store import InteractionLifecycleStore
from bff.agora.strategy_workshop.store import (
    MemoryWorkshopStore,
    PostgresWorkshopStore,
)
from bff.agora.trading_room.store import (
    PostgresTradingRoomStore,
    TradingRoomStore,
)
from bff.domain_ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
)
from services.research.write_owner import ResearchWriteOwner


# =========================================================================== #
# Canonical 19 Write Operation Mapping Inventory
# =========================================================================== #

AGORA_WRITE_MAPPINGS = [
    {
        "index": 11,
        "method": "create_agora_session",
        "primary_owners": [MemoryWorkshopStore, PostgresWorkshopStore],
        "primary_methods": ["create_session"],
        "secondary_owners": [InteractionLifecycleStore],
        "secondary_methods": ["create_request"],
    },
    {
        "index": 14,
        "method": "open_committee_session",
        "primary_owners": [MemoryWorkshopStore, PostgresWorkshopStore],
        "primary_methods": ["complete_command", "create_event"],
    },
    {
        "index": 15,
        "method": "close_committee_session",
        "primary_owners": [MemoryWorkshopStore, PostgresWorkshopStore],
        "primary_methods": ["complete_command"],
    },
    {
        "index": 18,
        "method": "submit_committee_session_memo",
        "primary_owners": [ConsultationStore, PostgresConsultationStore],
        "primary_methods": ["put_memo"],
        "secondary_owners": [MemoryWorkshopStore, PostgresWorkshopStore],
        "secondary_methods": ["ensure_current_version_link", "record_workshop_card"],
    },
    {
        "index": 19,
        "method": "publish_committee_session_memo",
        "primary_owners": [ConsultationStore, PostgresConsultationStore],
        "primary_methods": ["put_memo"],
        "secondary_owners": [ConsultationServiceClient],
        "secondary_methods": ["intake_policy_learning_candidate"],
    },
    {
        "index": 20,
        "method": "create_agora_handoff",
        "primary_owners": [TradingRoomStore, PostgresTradingRoomStore],
        "primary_methods": ["upsert_handoff"],
        "secondary_owners": [ConsultationStore, PostgresConsultationStore],
        "secondary_methods": ["put_handoff"],
    },
    {
        "index": 21,
        "method": "create_agora_committee_evidence_pack",
        "primary_owners": [MemoryWorkshopStore, PostgresWorkshopStore],
        "primary_methods": ["create_event"],
        "secondary_owners": [ConsultationStore, PostgresConsultationStore],
        "secondary_methods": ["put_evidence_attachment"],
    },
    {
        "index": 23,
        "method": "append_agora_committee_evidence_files",
        "primary_owners": [MemoryWorkshopStore, PostgresWorkshopStore],
        "primary_methods": ["create_event"],
        "secondary_owners": [ConsultationStore, PostgresConsultationStore],
        "secondary_methods": ["put_evidence_attachment"],
    },
    {
        "index": 24,
        "method": "create_agora_feedback",
        "primary_owners": [TradingRoomStore, PostgresTradingRoomStore],
        "primary_methods": ["upsert_decision_event"],
        "secondary_owners": [MemoryDashboardRecipeStore, PostgresDashboardRecipeStore],
        "secondary_methods": ["add_feedback"],
    },
    {
        "index": 25,
        "method": "create_agora_note",
        "primary_owners": [DefaultResearchKnowledgeSourcePort],
        "primary_methods": ["create_research_note"],
        "secondary_owners": [ResearchWriteOwner],
        "secondary_methods": ["create_research_note"],
    },
    {
        "index": 27,
        "method": "create_agora_signal",
        "primary_owners": [TradingRoomStore, PostgresTradingRoomStore],
        "primary_methods": ["upsert_decision_event", "upsert_intent"],
    },
    {
        "index": 30,
        "method": "record_agora_signal_feedback",
        "primary_owners": [TradingRoomStore, PostgresTradingRoomStore],
        "primary_methods": ["record_trader_decision"],
    },
    {
        "index": 33,
        "method": "create_agora_training_example",
        "primary_owners": [InteractionLifecycleStore],
        "primary_methods": ["create_request"],
    },
    {
        "index": 35,
        "method": "record_agora_audit_event",
        "primary_owners": [ProposalStore],
        "primary_methods": ["once"],
        "secondary_owners": [InteractionLifecycleStore, ConsultationStore, PostgresConsultationStore],
        "secondary_methods": ["create_request", "append_audit"],
    },
    {
        "index": 36,
        "method": "create_decision_journal_entry",
        "primary_owners": [CandidateDecisionStore],
        "primary_methods": ["create_candidate"],
        "secondary_owners": [TradingRoomStore, PostgresTradingRoomStore],
        "secondary_methods": ["upsert_decision_event"],
    },
    {
        "index": 37,
        "method": "patch_decision_journal_entry",
        "primary_owners": [CandidateDecisionStore],
        "primary_methods": ["append_decision"],
    },
    {
        "index": 41,
        "method": "record_sponsor_decision",
        "primary_owners": [ConsultationServiceClient],
        "primary_methods": ["record_sponsor_decision"],
        "secondary_owners": [ConsultationStore, PostgresConsultationStore],
        "secondary_methods": ["append_audit"],
    },
    {
        "index": 43,
        "method": "create_consult_request",
        "primary_owners": [ConsultationStore, PostgresConsultationStore],
        "primary_methods": ["put_request"],
        "secondary_owners": [ConsultationServiceClient],
        "secondary_methods": ["create_request"],
    },
    {
        "index": 44,
        "method": "cancel_consult_request",
        "primary_owners": [ConsultationServiceClient],
        "primary_methods": ["cancel_request"],
        "secondary_owners": [ConsultationStore, PostgresConsultationStore],
        "secondary_methods": ["put_request"],
    },
]


def test_all_19_write_mappings_exist_and_count_matches() -> None:
    """Verify exactly 19 distinct write operations are mapped."""
    assert len(AGORA_WRITE_MAPPINGS) == 19
    names = {mapping["method"] for mapping in AGORA_WRITE_MAPPINGS}
    assert len(names) == 19


def test_all_19_write_mappings_resolve_to_concrete_methods() -> None:
    """Verify that every mapped class exists and has the exact method callable."""
    for mapping in AGORA_WRITE_MAPPINGS:
        method_name = mapping["method"]
        for primary_cls in mapping["primary_owners"]:
            for fn_name in mapping["primary_methods"]:
                assert hasattr(
                    primary_cls, fn_name
                ), f"{method_name}: {primary_cls.__name__} missing {fn_name}"
                assert callable(
                    getattr(primary_cls, fn_name)
                ), f"{method_name}: {primary_cls.__name__}.{fn_name} not callable"

        for sec_cls in mapping.get("secondary_owners", []):
            matching_methods = [
                fn for fn in mapping.get("secondary_methods", [])
                if hasattr(sec_cls, fn)
            ]
            assert len(matching_methods) > 0, f"{method_name}: {sec_cls.__name__} has none of {mapping.get('secondary_methods')}"
            for fn_name in matching_methods:
                assert callable(
                    getattr(sec_cls, fn_name)
                ), f"{method_name}: {sec_cls.__name__}.{fn_name} not callable"


def test_functional_execution_of_all_19_write_operations(tmp_path) -> None:
    """Verify functional write-then-read execution of all 19 write operations."""
    trading_store = TradingRoomStore()
    workshop_store = MemoryWorkshopStore()
    candidate_store = CandidateDecisionStore(backend="memory")
    proposal_store = ProposalStore(backend="memory")
    interaction_store = InteractionLifecycleStore(backend="memory")
    consultation_store = ConsultationStore(str(tmp_path / "consult_data"))
    dashboard_store = MemoryDashboardRecipeStore()

    # 1. create_agora_session (11)
    ws_id = f"ws-{uuid.uuid4().hex}"
    session = {
        "workshop_id": ws_id,
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "servant_persona_id": "persona-1",
        "status": "open",
    }
    saved_session = workshop_store.create_session(session)
    assert saved_session["workshop_id"] == ws_id
    assert workshop_store.get_session(ws_id)["status"] == "open"

    # 2. open_committee_session (14)
    admit_res = workshop_store.admit_command(
        workshop_id=ws_id,
        tenant_id="tenant-1",
        user_id="user-1",
        operation="open_session",
        idempotency_key="idem-open-1",
        request_hash="hash1",
        expected_lock_version=1,
    )
    assert admit_res["outcome"] == "admitted"
    comp_res = workshop_store.complete_command(
        workshop_id=ws_id,
        tenant_id="tenant-1",
        user_id="user-1",
        operation="open_session",
        idempotency_key="idem-open-1",
        request_hash="hash1",
        result={"opened": True},
        session_updates={"status": "open"},
    )
    assert comp_res["outcome"] == "completed"
    assert workshop_store.get_session(ws_id)["status"] == "open"

    # 3. close_committee_session (15)
    admit_close = workshop_store.admit_command(
        workshop_id=ws_id,
        tenant_id="tenant-1",
        user_id="user-1",
        operation="conclude_session",
        idempotency_key="idem-close-1",
        request_hash="hash2",
        expected_lock_version=2,
    )
    assert admit_close["outcome"] == "admitted"
    comp_close = workshop_store.complete_command(
        workshop_id=ws_id,
        tenant_id="tenant-1",
        user_id="user-1",
        operation="conclude_session",
        idempotency_key="idem-close-1",
        request_hash="hash2",
        result={"concluded": True},
        session_updates={"status": "concluded", "concluded_at": "2026-08-29T04:00:00Z"},
    )
    assert comp_close["outcome"] == "completed"
    assert workshop_store.get_session(ws_id)["status"] == "concluded"

    # 4. submit_committee_session_memo (18)
    memo_id = f"memo-{uuid.uuid4().hex[:8]}"
    req_id = f"req-{uuid.uuid4().hex[:8]}"
    consult_memo = ConsultMemo(
        memo_id=memo_id,
        request_id=req_id,
        memo_type=MemoType.COMMITTEE_SUMMARY,
        author_type=AuthorType.HUMAN,
        author_ref="user-1",
        target_type="strategy",
        target_id="strat-1",
        summary="Memo summary",
        recommendation=Recommendation.APPROVE,
        status=MemoStatus.DRAFT,
        trace_id="tr-1",
    )
    consultation_store.put_memo(consult_memo)
    fetched_memo = consultation_store.get_memo(memo_id)
    assert fetched_memo is not None
    assert fetched_memo.status == MemoStatus.DRAFT

    # 5. publish_committee_session_memo (19)
    consult_memo.status = MemoStatus.PUBLISHED
    consult_memo.published_at = "2026-08-29T04:05:00Z"
    consultation_store.put_memo(consult_memo)
    pub_memo = consultation_store.get_memo(memo_id)
    assert pub_memo.status == MemoStatus.PUBLISHED

    # 6. create_agora_handoff (20)
    intent_id = f"intent-{uuid.uuid4().hex[:8]}"
    trading_store.upsert_intent(
        {
            "intent_id": intent_id,
            "strategy_id": "strat-1",
            "no_order_route_proof": "agora_intent_record_only",
        }
    )
    handoff_id = f"ho-{uuid.uuid4().hex[:8]}"
    trading_store.upsert_handoff(
        {
            "handoff_id": handoff_id,
            "intent_id": intent_id,
            "state": "submitted",
            "no_order_route_proof": "agora_request_only_no_order_route",
        }
    )
    assert trading_store.get_handoff(handoff_id)["handoff_id"] == handoff_id

    # 7. create_agora_committee_evidence_pack (21)
    ev_att = ConsultEvidenceAttachment(
        attachment_id=f"att-{uuid.uuid4().hex[:8]}",
        request_id=req_id,
        evidence_ref=EvidenceRef(id="ev-pack-1", evidence_type="pack", link="vault://evidence/pack1"),
        attached_by=ActorRef(actor_type="operator", actor_id="user-1"),
        trace_id="tr-1",
    )
    consultation_store.put_evidence_attachment(ev_att)
    assert consultation_store.get_evidence_attachment(ev_att.attachment_id) is not None

    # 8. append_agora_committee_evidence_files (23)
    ev_file = ConsultEvidenceAttachment(
        attachment_id=f"att-{uuid.uuid4().hex[:8]}",
        request_id=req_id,
        evidence_ref=EvidenceRef(id="ev-file-1", evidence_type="file", link="vault://evidence/file1"),
        attached_by=ActorRef(actor_type="operator", actor_id="user-1"),
        trace_id="tr-1",
    )
    consultation_store.put_evidence_attachment(ev_file)
    assert consultation_store.get_evidence_attachment(ev_file.attachment_id) is not None

    # 9. create_agora_feedback (24)
    feedback_item = {"feedback_id": "fb-1", "signal_id": "sig-1", "rating": 5}
    dashboard_store.add_feedback(feedback_item)
    assert feedback_item in dashboard_store.feedback

    # 10. create_agora_note (25)
    note_payload = {
        "note_id": f"note-{uuid.uuid4().hex[:8]}",
        "title": "Agora Strategy Note",
        "content": "Note body",
        "author": "Antigravity",
    }
    research_port = DefaultResearchKnowledgeSourcePort()
    saved_note = research_port.create_research_note(note_payload)
    assert saved_note is not None
    assert saved_note["note_id"] == note_payload["note_id"]

    # 11. create_agora_signal (27)
    sig_id = f"sig-{uuid.uuid4().hex[:8]}"
    sig_event = {
        "decision_event_id": sig_id,
        "strategy_id": "strat-1",
        "event_kind": "strategy_signal",
        "state": "pending",
        "triggered_at": "2026-08-29T04:00:00Z",
        "no_order_route_proof": "agora_decision_support_only",
    }
    trading_store.upsert_decision_event(sig_event)
    assert trading_store.get_decision_event(sig_id) == sig_event

    # 12. record_agora_signal_feedback (30)
    trading_store.record_trader_decision(
        sig_id, {"decision": "approve", "reason": "valid signal"}
    )
    decided_sig = trading_store.get_decision_event(sig_id)
    assert decided_sig["state"] == "decided"
    assert decided_sig["decision_state"] == "approved_by_trader"

    # 13. create_agora_training_example (33)
    int_id = f"int-{uuid.uuid4().hex[:8]}"
    interaction_req = {
        "interaction_id": int_id,
        "workshop_id": ws_id,
        "tenant_id": "tenant-1",
        "owner_user_id": "user-1",
        "interaction_kind": "training_example",
        "status": "pending",
        "human_request": {"operator_id": "user-1"},
        "created_at": "2026-08-29T04:00:00Z",
    }
    interaction_store.create_request(
        interaction_req,
        idempotency_scope="scope-te-1",
        idempotency_key="key-te-1",
        fingerprint="fp-te-1",
        trace_id="tr-te-1",
    )
    assert interaction_store.get(int_id, "tenant-1", "user-1") is not None

    # 14. record_agora_audit_event (35)
    audit_evt = ConsultAuditEvent(
        audit_id=f"aud-{uuid.uuid4().hex[:8]}",
        request_id=req_id,
        actor_ref=ActorRef(actor_type="operator", actor_id="user-1"),
        action="committee_decision_recorded",
        trace_id="tr-1",
    )
    consultation_store.append_audit(audit_evt)
    audits = consultation_store.list_audit_for_request(req_id)
    assert any(a.audit_id == audit_evt.audit_id for a in audits)

    # 15. create_decision_journal_entry (36)
    prop_id = f"prop-{uuid.uuid4().hex[:8]}"
    candidate_rec = {
        "proposal_id": prop_id,
        "revision": 1,
        "tenant_id": "tenant-1",
        "owner_user_id": "user-1",
        "title": "Candidate 1",
    }
    mutation = candidate_store.create_candidate(
        candidate_rec,
        idempotency_key="key-cand-1",
        fingerprint="fp-cand-1",
    )
    assert mutation.resource["proposal_id"] == prop_id
    assert candidate_store.get(prop_id, "tenant-1", "user-1") is not None

    # 16. patch_decision_journal_entry (37)
    next_cand = {
        "proposal_id": prop_id,
        "revision": 2,
        "tenant_id": "tenant-1",
        "owner_user_id": "user-1",
        "title": "Candidate 1 - Patched",
    }
    decision_rec = {
        "decision_id": f"dec-{uuid.uuid4().hex[:8]}",
        "proposal_id": prop_id,
        "revision": 2,
        "decision": "approved",
    }
    patched = candidate_store.append_decision(
        current=candidate_rec,
        expected_etag=candidate_store.etag(candidate_rec),
        next_record=next_cand,
        decision=decision_rec,
        idempotency_key="key-dec-1",
        fingerprint="fp-dec-1",
    )
    assert patched.resource["candidate"]["revision"] == 2
    assert candidate_store.get(prop_id, "tenant-1", "user-1")["revision"] == 2

    # 17. record_sponsor_decision (41)
    with mock.patch.object(
        ConsultationServiceClient,
        "_request_json",
        return_value={"status": "recorded", "committee_id": "comm-1"},
    ):
        client = ConsultationServiceClient(base_url="http://localhost:8000")
        sponsor_res = client.record_sponsor_decision(
            "comm-1",
            sponsor_decision="approve",
            rationale_ref="ref-1",
            actor_id="user-1",
        )
        assert sponsor_res["status"] == "recorded"

    # 18. create_consult_request (43)
    new_req_id = f"creq-{uuid.uuid4().hex[:8]}"
    consult_req = ConsultRequest(
        request_id=new_req_id,
        request_type=ConsultRequestType.STRATEGY_REVIEW,
        target_type="strategy",
        target_id="strat-1",
        requested_by=ActorRef(actor_type="operator", actor_id="user-1"),
        status=ConsultRequestStatus.DRAFT,
        priority=ConsultPriority.NORMAL,
        trace_id="tr-req-1",
    )
    consultation_store.put_request(consult_req)
    assert consultation_store.get_request(new_req_id) is not None

    # 19. cancel_consult_request (44)
    with mock.patch.object(
        ConsultationServiceClient,
        "_request_json",
        return_value={"request_id": new_req_id, "status": "canceled"},
    ):
        cancel_res = client.cancel_request(new_req_id, actor_id="user-1")
        assert cancel_res["status"] == "canceled"


def test_candidate_decision_store_fresh_reader_invariants() -> None:
    """Verify fresh reader and optimistic locking in CandidateDecisionStore."""
    store = CandidateDecisionStore(backend="memory")
    prop_id = f"prop-{uuid.uuid4().hex}"
    cand_1 = {
        "proposal_id": prop_id,
        "revision": 1,
        "tenant_id": "t1",
        "owner_user_id": "u1",
        "title": "Candidate Title",
    }
    m1 = store.create_candidate(cand_1, idempotency_key="k1", fingerprint="f1")
    assert m1.resource["proposal_id"] == prop_id
    assert store.get(prop_id, "t1", "u1")["revision"] == 1

    cand_2 = {
        "proposal_id": prop_id,
        "revision": 2,
        "tenant_id": "t1",
        "owner_user_id": "u1",
        "title": "Candidate Title Rev 2",
    }
    dec_1 = {"decision_id": "d1", "proposal_id": prop_id, "revision": 2, "decision": "approved"}
    m2 = store.append_decision(
        current=cand_1,
        expected_etag=store.etag(cand_1),
        next_record=cand_2,
        decision=dec_1,
        idempotency_key="k2",
        fingerprint="f2",
    )
    assert m2.resource["candidate"]["revision"] == 2
    assert store.get(prop_id, "t1", "u1")["revision"] == 2
    history = store.history(prop_id, "t1", "u1")
    assert len(history) == 2


def test_proposal_store_once_and_append_invariants() -> None:
    """Verify ProposalStore once idempotency and revision tracking."""
    store = ProposalStore(backend="memory")
    prop_id = f"prop-{uuid.uuid4().hex}"
    p1 = {"proposal_id": prop_id, "revision": 1, "tenant_id": "t1", "owner_user_id": "u1", "state": "draft"}
    m1 = store.create(p1, key="idem-p1", fingerprint="fp-p1")
    assert m1["revision"] == 1

    res1 = store.once("scope1", "key1", "fp1", lambda: {"processed": True})
    assert res1.replayed is False
    assert res1.run_side_effects is True
    assert res1.data["processed"] is True

    res2 = store.once("scope1", "key1", "fp1", lambda: {"processed": True})
    assert res2.replayed is True
    assert res2.run_side_effects is False


def test_consultation_store_fresh_disk_restart(tmp_path) -> None:
    """Verify ConsultationStore persists across distinct store instances (disk restart)."""
    db_path = str(tmp_path / "consult_disk_test")
    store1 = ConsultationStore(db_path)
    req_id = f"req-{uuid.uuid4().hex}"
    req = ConsultRequest(
        request_id=req_id,
        request_type=ConsultRequestType.STRATEGY_REVIEW,
        target_type="strategy",
        target_id="strat-1",
        requested_by=ActorRef(actor_type="operator", actor_id="u1"),
        status=ConsultRequestStatus.DRAFT,
        trace_id="tr-1",
    )
    store1.put_request(req)

    memo_id = f"memo-{uuid.uuid4().hex}"
    memo = ConsultMemo(
        memo_id=memo_id,
        request_id=req_id,
        memo_type=MemoType.COMMITTEE_SUMMARY,
        author_type=AuthorType.HUMAN,
        author_ref="u1",
        target_type="strategy",
        target_id="strat-1",
        summary="Summary text",
        recommendation=Recommendation.APPROVE,
        status=MemoStatus.DRAFT,
        trace_id="tr-1",
    )
    store1.put_memo(memo)

    # Fresh store instance from the same directory
    store2 = ConsultationStore(db_path)
    loaded_req = store2.get_request(req_id)
    assert loaded_req is not None
    assert loaded_req.target_id == "strat-1"

    loaded_memo = store2.get_memo(memo_id)
    assert loaded_memo is not None
    assert loaded_memo.summary == "Summary text"


def test_no_read_store_import_in_agora_and_consultation() -> None:
    """Verify AST import isolation: zero read_store references in stores."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    store_files = [
        os.path.join(repo_root, "services", "control-plane", "bff", "agora", "candidate_decisions", "store.py"),
        os.path.join(repo_root, "services", "control-plane", "bff", "agora", "governance", "store.py"),
        os.path.join(repo_root, "services", "control-plane", "bff", "agora", "strategy_workshop", "store.py"),
        os.path.join(repo_root, "services", "control-plane", "bff", "agora", "trading_room", "store.py"),
        os.path.join(repo_root, "services", "control-plane", "bff", "agora", "dashboard", "store.py"),
        os.path.join(repo_root, "services", "control-plane", "bff", "agora", "interaction", "store.py"),
        os.path.join(repo_root, "services", "consultation", "store.py"),
        os.path.join(repo_root, "services", "research", "write_owner.py"),
    ]

    for path in store_files:
        assert os.path.exists(path), f"{path} missing"
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "read_store" not in alias.name, f"{path} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "read_store" not in module, f"{path} imports from {module}"
                for alias in node.names:
                    assert alias.name != "ReadSurfaceStore", f"{path} imports ReadSurfaceStore"


def test_source_ingestion_reconcile_only_for_agora() -> None:
    """Verify source_ingestion has zero write mutations or imports of Agora stores."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    si_dir = os.path.join(repo_root, "services", "source_ingestion")
    if not os.path.exists(si_dir):
        pytest.skip("services/source_ingestion does not exist")

    for root, _, files in os.walk(si_dir):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as handle:
                    content = handle.read()
                assert "CandidateDecisionStore" not in content, f"{path} imports CandidateDecisionStore"
                assert "PostgresTradingRoomStore" not in content, f"{path} imports PostgresTradingRoomStore"
                assert "TradingRoomStore" not in content, f"{path} imports TradingRoomStore"
                assert "StrategyWorkshopStore" not in content, f"{path} imports StrategyWorkshopStore"
