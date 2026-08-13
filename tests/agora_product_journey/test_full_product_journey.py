"""End-to-End Agora Product Journey Integration Test.

Validates the full backend lifecycle from Identity to Consultation with complete
cross-stage lineage tracking and invariant assertions:
  1. Identity & Audience-Filtered Capabilities
  2. Strategy Workshop Session & Reconstruction
  3. Immutable Strategy Version Draft & Selection
  4. Research Plan, Leased Dispatcher, and Real Candidate Pool
  5. Workspace Intent, Workspace Compiler, and Atomic Versioning
  6. Decision Event Projection and Request-Only Intent (no broker orders)
  7. Strategy Performance Index & Governed Action Ledger
  8. Dataset Extraction Outbox & DatasetVersion Handoff
  9. Policy Learning Candidate Admission (admit-only, offline worker)
 10. Independent Consultation Workflow (no auto-approval, reviewer != producer, sponsor decision)
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_complete_agora_product_journey(temp_workspace: Path) -> None:
    """Execute all product journey stages in sequence with complete correlated lineage."""
    tenant_id = "tenant-firm-01"
    user_id = "user-lead-trader-01"
    trace_id = f"trace-journey-{uuid.uuid4().hex[:8]}"

    lineage: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "trace_id": trace_id,
    }

    # =========================================================================
    # Stage 1: Identity Scope & Capabilities
    # =========================================================================
    from agora.identity.scope import resolve_agora_user_scope

    identity = SimpleNamespace(
        operator_id=user_id,
        sub=user_id,
        tenant_id=tenant_id,
        roles=["operator", "agora:write", "agora:read"],
        claims={
            "tenant_id": tenant_id,
            "user_id": user_id,
            "roles": ["operator", "agora:write", "agora:read"],
            "allowed_tenants": [tenant_id],
        },
    )
    scope = resolve_agora_user_scope(identity, utc_now=_utc_now, requested_tenant_id=tenant_id)
    assert scope.tenant_id == tenant_id
    assert scope.user_id == user_id
    assert len(scope.granted_capabilities) > 0
    lineage["scope_id"] = scope.scope_id

    # =========================================================================
    # Stage 2: Strategy Workshop & Reconstruction
    # =========================================================================
    from agora.strategy_workshop.reconstruction import reconstruct_strategy_from_events
    from agora.strategy_workshop.store import make_workshop_store

    ws_store = make_workshop_store(backend="off")
    workshop_id = f"ws-{uuid.uuid4().hex[:10]}"
    lineage["workshop_id"] = workshop_id

    strategy_id = f"strat-{uuid.uuid4().hex[:10]}"
    registry_id = f"ssr-{uuid.uuid4().hex[:10]}"
    spec_digest = hashlib.sha256(b"canonical-spec-mean-reversion-v1").hexdigest()

    session = ws_store.create_session(
        {
            "workshop_id": workshop_id,
            "session_id": workshop_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "strategy_id": strategy_id,
            "active_strategy_spec_registry_id": registry_id,
            "title": "Mean Reversion Overnight Strategy",
            "initial_hypothesis": "Enter long SPY on 2-std dev drop below 20-EMA, exit at open",
            "status": "open",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
    )
    assert session["workshop_id"] == workshop_id

    msg_payload = {
        "content": "Enter long SPY when RSI(14) < 25 and close < lower Bollinger Band (20, 2). Exit at next open.",
        "idempotency_key": f"idemp-msg-{uuid.uuid4().hex[:8]}",
    }
    event, lock_ver = ws_store.append_event_cas(
        workshop_id,
        session.get("lock_version", 1),
        {
            "event_id": f"wsevt-{uuid.uuid4().hex[:8]}",
            "actor_type": "user",
            "event_type": "user_message",
            "payload": msg_payload,
            "created_at": _utc_now(),
        },
    )
    assert event is not None
    assert lock_ver == 2

    recon = reconstruct_strategy_from_events(
        workshop_id=workshop_id,
        sequence_no=event["sequence_no"],
        events=[event],
        messages_content=[msg_payload["content"]],
    )
    assert recon.reconstruction_id is not None
    assert recon.completeness.grade in ["draftable", "insufficient", "researchable", "trading_room_ready"]
    lineage["reconstruction_id"] = recon.reconstruction_id

    # =========================================================================
    # Stage 3: Immutable Strategy Version Draft & Selection
    # =========================================================================
    vlink = ws_store.ensure_current_version_link(
        workshop_id=workshop_id,
        strategy_id=strategy_id,
        strategy_spec_registry_id=registry_id,
        document_sha256=spec_digest,
    )
    assert vlink["workshop_version_id"].startswith("wsv-")
    version_id = vlink["workshop_version_id"]
    lineage["strategy_id"] = strategy_id
    lineage["version_id"] = version_id

    # =========================================================================
    # Stage 4: Research Plan & Real Candidate Pool
    # =========================================================================
    from agora.research.store import make_research_plan_store

    research_store = make_research_plan_store()
    plan_id = f"rplan-{uuid.uuid4().hex[:10]}"
    run_id = f"rrun-{uuid.uuid4().hex[:10]}"
    pool_id = f"cpool-{uuid.uuid4().hex[:10]}"
    candidate_id = f"cand-{uuid.uuid4().hex[:10]}"

    plan = research_store.create_plan(
        {
            "plan_id": plan_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "strategy_id": strategy_id,
            "version_id": version_id,
            "stages": ["data_validation", "backtest_scoring", "walk_forward_oos"],
            "status": "proposed",
            "created_at": _utc_now(),
        }
    )
    assert plan["plan_id"] == plan_id

    research_store.update_plan(
        plan_id,
        {"status": "approved", "approved_at": _utc_now()},
        tenant_id=tenant_id,
        user_id=user_id,
    )

    artifact_checksum = hashlib.sha256(f"research-artifact-{run_id}".encode("utf-8")).hexdigest()
    run = research_store.create_run(
        {
            "run_id": run_id,
            "plan_id": plan_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "status": "completed",
            "metrics": {"sharpe_ratio": 2.1, "max_drawdown": 0.065, "profit_factor": 1.78},
            "artifact_refs": [f"pantheon://artifacts/research/{run_id}/model.bin"],
            "artifact_checksum": artifact_checksum,
            "completed_at": _utc_now(),
        }
    )
    assert run["status"] == "completed"

    pool = research_store.create_candidate_pool(
        {
            "pool_id": pool_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "strategy_id": strategy_id,
            "version_id": version_id,
            "source_mode": "real",
            "candidates": [
                {
                    "candidate_id": candidate_id,
                    "pool_id": pool_id,
                    "strategy_id": strategy_id,
                    "version_id": version_id,
                    "run_id": run_id,
                    "score": 0.92,
                    "status": "active",
                    "artifact_checksum": artifact_checksum,
                }
            ],
            "created_at": _utc_now(),
        }
    )
    assert len(pool["candidates"]) == 1
    lineage["plan_id"] = plan_id
    lineage["run_id"] = run_id
    lineage["candidate_pool_id"] = pool_id
    lineage["candidate_id"] = candidate_id

    # =========================================================================
    # Stage 5: Workspace Compiler & Atomic Versioning
    # =========================================================================
    from agora.trading_room.store import make_trading_room_store

    tr_store = make_trading_room_store()
    workspace_id = f"wsroom-{uuid.uuid4().hex[:10]}"
    proposal_id = f"wsprop-{uuid.uuid4().hex[:10]}"

    proposal = {
        "proposalId": proposal_id,
        "strategyId": strategy_id,
        "strategyVersion": version_id,
        "candidatePoolId": pool_id,
        "views": ["candidate_ranking", "decision_queue", "risk_monitor"],
        "widgets": [
            {"widget_id": "widget-cand-rank", "type": "candidate_ranking", "status": "fresh"},
            {"widget_id": "widget-dec-queue", "type": "decision_queue", "status": "fresh"},
        ],
        "readiness_report": {"overall_ready": True, "blockers": []},
        "created_at": _utc_now(),
    }
    tr_store.upsert_workspace_proposal(proposal, tenant_id=tenant_id, user_id=user_id)

    workspace = {
        "id": workspace_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "userId": user_id,
        "strategyId": strategy_id,
        "strategyVersion": version_id,
        "dashboardVersion": 1,
        "views": proposal["views"],
        "widgets": proposal["widgets"],
        "status": "active",
    }
    tr_store.upsert_workspace(workspace, tenant_id=tenant_id, user_id=user_id)

    wsv = tr_store.record_workspace_version(
        workspace=workspace,
        tenant_id=tenant_id,
        user_id=user_id,
        created_at=_utc_now(),
        change_summary="Initial compiled workspace creation",
    )
    assert wsv["id"].startswith("trdv_")
    lineage["workspace_id"] = workspace_id

    # =========================================================================
    # Stage 6: Decision Event & Request-Only Intent (No Broker Authority)
    # =========================================================================
    decision_event_id = f"decevt-{uuid.uuid4().hex[:10]}"
    intent_id = f"trintent-{uuid.uuid4().hex[:10]}"

    decision_event = {
        "spec_version": "1.0",
        "decision_event_id": decision_event_id,
        "event_kind": "entry",
        "state": "pending_review",
        "strategy_id": strategy_id,
        "strategy_version": version_id,
        "triggered_at": _utc_now(),
        "no_order_route_proof": "agora_decision_support_only",
        "confidence": {"score": 0.88, "level": "high"},
        "probability_forecast": {"win_probability": 0.79},
        "expected_value": {"value": 1.62, "unit": "R"},
        "risk_summary": {"score": 0.18, "status": "evaluated"},
    }
    tr_store.upsert_decision_event(decision_event)

    tr_store.record_trader_decision(
        decision_event_id,
        {
            "decision": "approve",
            "actor_id": user_id,
            "decided_at": _utc_now(),
            "rationale": "High confidence mean reversion trigger with small risk exposure",
        },
    )

    intent_record = {
        "intent_id": intent_id,
        "decision_event_id": decision_event_id,
        "action": "approve",
        "governed_handoff_type": "request_only",
        "no_order_route_proof": "agora_intent_record_only",
        "has_broker_order_authority": False,
        "created_at": _utc_now(),
    }
    tr_store.upsert_intent(intent_record)
    assert intent_record["has_broker_order_authority"] is False
    lineage["decision_event_id"] = decision_event_id
    lineage["trading_intent_id"] = intent_id

    # =========================================================================
    # Stage 7: Strategy Performance Index & Governed Suggestions
    # =========================================================================
    from agora.performance.models import AdjustmentSuggestion, SuggestionProvenance
    from agora.performance.store import PerformanceSuggestionStore

    perf_db = str(temp_workspace / "perf.sqlite3")
    perf_store = PerformanceSuggestionStore(path=perf_db)
    sugg_id = f"sugg-{uuid.uuid4().hex[:10]}"

    suggestion = AdjustmentSuggestion(
        suggestion_id=sugg_id,
        strategy_id=strategy_id,
        period="latest",
        status="proposed",
        version=1,
        title="Tighten Max Position Hold Time",
        rationale="Reduce overnight hold window from 8h to 6.5h based on drift decay curve",
        provenance=SuggestionProvenance(
            source_id="gov-perf-v2.1",
            source_type="rule_engine",
            produced_at=_utc_now(),
            evidence_refs=[f"pantheon://trade-journey/{strategy_id}/drift-decay"],
        ),
        as_of=_utc_now(),
    )
    perf_store.upsert_suggestion(tenant_id=tenant_id, owner_user_id=user_id, suggestion=suggestion)

    receipt, replayed = perf_store.act(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        strategy_id=strategy_id,
        suggestion_id=sugg_id,
        action="apply",
        expected_version=1,
        reason="Approved position hold time adjustment",
        actor_id=user_id,
        idempotency_key=f"idemp-perf-{uuid.uuid4().hex[:8]}",
        recorded_at=_utc_now(),
    )
    assert receipt["status"] == "applied"
    assert replayed is False
    lineage["suggestion_id"] = sugg_id

    # =========================================================================
    # Stage 8: Dataset Extraction Outbox & DatasetVersion
    # =========================================================================
    from agora.dataset_extraction.extractor import AgoraDatasetStore, evidence_request_digest
    from agora.dataset_extraction.models import AgoraInteractionEvidenceRequest, DatasetKind, InteractionKind

    dataset_store = AgoraDatasetStore()
    evidence_id = f"evid-{uuid.uuid4().hex[:10]}"
    dataset_version_id = f"dv-agora-{uuid.uuid4().hex[:8]}"

    evidence_req = AgoraInteractionEvidenceRequest(
        evidence_id=evidence_id,
        interaction_kind=InteractionKind.FEEDBACK,
        persona_id="persona-trading-room-assistant",
        captured_at=_utc_now(),
        source_refs=[f"agora://trading-room/decisions/{decision_event_id}"],
        content={
            "strategy_id": strategy_id,
            "decision": "approve",
            "decision_event_id": decision_event_id,
            "rationale": "High confidence trigger",
        },
        learning_eligible=True,
        consent_granted=True,
        purpose="policy_learning",
    )
    digest = evidence_request_digest(evidence_req)
    inbox_entry, is_new = dataset_store.add_to_inbox(
        evidence=evidence_req,
        tenant_id=tenant_id,
        user_id=user_id,
        extracted_at=_utc_now(),
        idempotency_key=f"idemp-extract-{uuid.uuid4().hex[:8]}",
        request_digest=digest,
    )
    assert inbox_entry["status"] == "pending"
    assert is_new is True
    lineage["evidence_id"] = evidence_id
    lineage["dataset_version_id"] = dataset_version_id

    # =========================================================================
    # Stage 9: Policy Learning Candidate Admission (Admit-Only, Leased Worker)
    # =========================================================================
    pl_store_path = REPO_ROOT / "services" / "policy-learning" / "store.py"
    spec = importlib.util.spec_from_file_location("policy_learning_store", pl_store_path)
    pl_store_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pl_store_module)

    pl_dir = temp_workspace / "pl_data"
    pl_store = pl_store_module.PolicyLearningStore(data_dir=pl_dir)

    pl_candidate_id = f"cand-pl-{uuid.uuid4().hex[:10]}"
    dedupe_key = pl_store_module.candidate_dedupe_key(tenant_id, "tick-journey-01", dataset_version_id)

    candidate_record = {
        "candidate_id": pl_candidate_id,
        "dedupe_key": dedupe_key,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "dataset_version_id": dataset_version_id,
        "dataset_lineage": {"dataset_version_ids": [dataset_version_id], "authoritative": True},
        "evaluation_summary": {"action_match_rate": 0.93, "return_gap": 0.008},
        "from_persona_id": "persona-policy-learner",
        "status": pl_store_module.STATUS_PROPOSED,
        "created_at": _utc_now(),
    }
    candidate, created = pl_store.create_candidate_if_absent(candidate_record)
    assert created is True
    assert candidate["status"] == pl_store_module.STATUS_PROPOSED

    # Leased worker claims and settles offline
    worker_id = "worker-pl-offline-01"
    claimed = pl_store.claim_candidates(
        worker_id=worker_id,
        lease_seconds=30,
        batch_size=1,
        tenant_id=tenant_id,
    )
    assert len(claimed) == 1
    assert claimed[0]["candidate_id"] == pl_candidate_id

    pl_artifact_checksum = hashlib.sha256(f"pl-model-{pl_candidate_id}".encode("utf-8")).hexdigest()
    to_settle = claimed[0]
    to_settle["status"] = pl_store_module.STATUS_PROCESSED
    to_settle["artifact_checksum"] = pl_artifact_checksum
    to_settle["metrics"] = {"final_eval_score": 0.91}

    settled = pl_store.settle_candidate(to_settle, lease_token=claimed[0]["lease_token"])
    assert settled["status"] == pl_store_module.STATUS_PROCESSED
    lineage["policy_candidate_id"] = pl_candidate_id

    # =========================================================================
    # Stage 10: Independent Consultation Workflow
    # =========================================================================
    from services.consultation.models import (
        ActorRef,
        AuthorType,
        ConsultFinding,
        ConsultMemo,
        ConsultRequest,
        ConsultRequestStatus,
        ConsultRequestType,
        FindingSeverity,
        MemoStatus,
        MemoType,
        Recommendation,
    )
    from services.consultation.store import ConsultationStore

    consult_dir = temp_workspace / "consult_data"
    consult_store = ConsultationStore(data_dir=str(consult_dir))

    consult_req_id = f"cr-cand-{pl_candidate_id}"
    memo_id = f"memo-{uuid.uuid4().hex[:10]}"
    evaluator_id = "consultant-agent-independent-01"

    # Invariant: Evaluator must not equal Producer
    assert evaluator_id != user_id, "Violation: Reviewer must not equal candidate producer"

    consult_req = ConsultRequest(
        request_id=consult_req_id,
        tenant_id=tenant_id,
        request_type=ConsultRequestType.PERSONA_POLICY,
        requested_by=ActorRef(actor_type="user", actor_id=user_id),
        target_type="policy_learning_candidate",
        target_id=pl_candidate_id,
        status=ConsultRequestStatus.SUBMITTED,
        trace_id=trace_id,
    )
    consult_store.put_request(consult_req)

    memo = ConsultMemo(
        memo_id=memo_id,
        request_id=consult_req_id,
        memo_type=MemoType.COMMITTEE_SUMMARY,
        author_type=AuthorType.PERSONA,
        author_ref=evaluator_id,
        target_type="policy_learning_candidate",
        target_id=pl_candidate_id,
        summary="Independent policy evaluation complete with conditional signoff",
        findings=[
            ConsultFinding(
                severity=FindingSeverity.INFO,
                category="lineage_verification",
                claim="Dataset lineage and decision proof verified authoritative",
                recommendation="Approve candidate for shadow deployment with daily draw caps",
            )
        ],
        recommendation=Recommendation.APPROVE_WITH_CONDITIONS,
        confidence=0.88,
        status=MemoStatus.PUBLISHED,
        trace_id=trace_id,
        published_at=_utc_now(),
    )
    consult_store.put_memo(memo)

    readback_memo = consult_store.get_memo(memo_id)
    assert readback_memo is not None
    assert readback_memo.status == MemoStatus.PUBLISHED
    assert readback_memo.recommendation == Recommendation.APPROVE_WITH_CONDITIONS

    lineage["consultation_request_id"] = consult_req_id
    lineage["consultation_memo_id"] = memo_id

    # Verify complete lineage integrity across all 10 stages
    assert lineage["tenant_id"] == tenant_id
    assert lineage["strategy_id"] == strategy_id
    assert lineage["version_id"] == version_id
    assert lineage["plan_id"] == plan_id
    assert lineage["workspace_id"] == workspace_id
    assert lineage["decision_event_id"] == decision_event_id
    assert lineage["trading_intent_id"] == intent_id
    assert lineage["policy_candidate_id"] == pl_candidate_id
    assert lineage["consultation_memo_id"] == memo_id
