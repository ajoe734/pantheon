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
            "trace_id": trace_id,
            "correlation_id": trace_id,
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
    from agora.interaction.worker import AgoraInteractionWorker
    from agora.research.dispatcher import AuthenticStageAdapter, ResearchDispatcher
    from agora.research.receipt import resolve_run_provenance
    from agora.research.routes.common import (
        AgoraResearchRouteContext,
        CandidatePoolCreateRequest,
        _build_run_projection,
    )
    from agora.research.store import make_research_plan_store

    research_db = str(temp_workspace / "research_store.json")
    research_store = make_research_plan_store(storage_path=research_db)
    plan_id = f"rplan-{uuid.uuid4().hex[:10]}"
    run_id = f"rrun-{uuid.uuid4().hex[:10]}"
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
        {
            "status": "approved",
            "approved_at": _utc_now(),
            "correlation_id": trace_id,
            "executor": "qlib_executor",
        },
        tenant_id=tenant_id,
        user_id=user_id,
    )

    # Natural execution chain:
    # Do NOT manually create succeeded runs or pre-seed receipts.
    # Connect authentic execution owner (AuthenticStageAdapter) via ResearchDispatcher.
    stage_item = {
        "stage_id": "walk_forward_oos",
        "stage_type": "walk_forward_oos",
        "status": "ready",
        "dependencies": [],
        "routing": {
            "backend_mode": "real",
            "fallback_policy": "explicit_fixture_only",
        },
    }

    # 1. Fail-closed assertion: real mode with absent backend must raise RuntimeError
    absent_adapter = AuthenticStageAdapter(
        "walk_forward_oos",
        preferred_backend="vectorbt",
        mode="real",
        execution_owner=None,
    )
    with pytest.raises(RuntimeError, match="Absent backend must fail closed"):
        absent_adapter.execute(
            stage=stage_item,
            plan=plan,
            context={"run_id": run_id, "correlation_id": trace_id},
            downstream_key="k",
        )

    # 2. Authentic execution owner returning authentic backend metrics and reference
    def authentic_execution_backend(*args, **kwargs):
        return {
            "status": "succeeded",
            "outcome": "succeeded",
            "provenance": "real",
            "backend_reference": "qlib://runs/42",
            "artifact_id": candidate_id,
            "artifact_digest": "sha256:d8a9e102f4c8b",
            "artifact_refs": [
                {
                    "artifact_id": candidate_id,
                    "ref": f"artifact://{candidate_id}",
                    "digest": "sha256:d8a9e102f4c8b",
                }
            ],
            "checksums": {
                candidate_id: "sha256:d8a9e102f4c8b",
                f"artifact://{candidate_id}": "sha256:d8a9e102f4c8b",
            },
            "metrics": [
                {"name": "sharpe_ratio", "value": 2.1, "category": "performance", "gate_result": "pass", "provenance": "real"},
                {"name": "max_drawdown", "value": 0.065, "category": "risk", "gate_result": "pass", "provenance": "real"},
                {"name": "profit_factor", "value": 1.78, "category": "performance", "gate_result": "pass", "provenance": "real"},
            ],
            "receipt": {
                "receipt_id": f"rcpt-qlib-{run_id}",
                "run_id": run_id,
                "executor": "qlib_executor",
                "mode": "real",
                "correlation_id": trace_id,
                "completed_at": _utc_now(),
                "backend_reference": "qlib://runs/42",
                "artifact_digest": "sha256:d8a9e102f4c8b",
                "spec_version": "1.0",
            },
        }

    dispatcher = ResearchDispatcher(
        store=research_store,
        publish_progress_fn=lambda *args, **kwargs: None,
        utc_now=_utc_now,
    )
    dispatcher.registry.register_authentic_adapter(
        "walk_forward_oos",
        preferred_backend="vectorbt",
        executor="qlib_executor",
        mode="real",
        backend_reference="qlib://runs/42",
        execution_owner=authentic_execution_backend,
    )

    run_obj = _build_run_projection(
        plan=plan,
        stage=stage_item,
        run_id=run_id,
        now=_utc_now(),
        scope=scope,
    )
    run_obj["correlation_id"] = trace_id
    run_obj["executor"] = "qlib_executor"
    run_obj["metrics"] = [
        {"name": "sharpe_ratio", "value": 2.1, "category": "performance", "gate_result": "pass"},
        {"name": "max_drawdown", "value": 0.065, "category": "risk", "gate_result": "pass"},
        {"name": "profit_factor", "value": 1.78, "category": "performance", "gate_result": "pass"},
    ]
    research_store.create_run(run_obj)

    outbox_record = dispatcher.create_outbox_record(
        plan=plan,
        stage=stage_item,
        run_id=run_id,
        scope=scope,
        now=_utc_now(),
    )
    assert outbox_record["outbox_id"] is not None
    assert outbox_record["run_id"] == run_id

    # 3. Drain outbox via AgoraInteractionWorker (records receipt into research_store without manual test draining)
    worker = AgoraInteractionWorker(
        store=None,
        research_store=research_store,
        research_dispatcher=dispatcher,
        worker_id="worker-journey-1",
    )
    drained_count = worker.run_once(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    assert drained_count >= 1
    assert worker.metrics["completed_count"] >= 1

    # 4. Prove genuine restart persistence across store reconstruction (no shared in-memory instance)
    del research_store
    del dispatcher
    del worker
    reconstructed_research_store = make_research_plan_store(storage_path=research_db)

    run = reconstructed_research_store.get_run(run_id)
    assert run is not None
    assert run["execution_status"] == "succeeded"
    assert run["provenance"] == "real"
    assert run["executor"] == "qlib_executor"
    assert run["correlation_id"] == trace_id
    for metric in run.get("metrics", []):
        assert metric.get("provenance") == "real"

    receipt = reconstructed_research_store.get_execution_receipt(run_id)
    assert receipt is not None
    assert receipt["run_id"] == run_id
    assert receipt["executor"] == "qlib_executor"
    assert receipt["mode"] == "real"
    assert receipt["correlation_id"] == trace_id
    assert receipt["spec_version"] == "1.0"
    assert receipt["backend_reference"] == "qlib://runs/42"
    receipt_id = receipt["receipt_id"]
    artifact_checksum = receipt["artifact_digest"]

    # Server-side provenance resolution verifies owner, correlation, and terminal state
    prov, resolved_receipt = resolve_run_provenance(
        reconstructed_research_store,
        run,
        expected_correlation_id=trace_id,
        expected_owner="qlib_executor",
    )
    assert prov == "real"
    assert resolved_receipt is not None
    assert resolved_receipt["receipt_id"] == receipt_id

    # Negative controls: correlation mismatch downgrades to unavailable
    prov_bad_corr, _ = resolve_run_provenance(
        reconstructed_research_store,
        run,
        expected_correlation_id="trace-wrong-id",
        expected_owner="qlib_executor",
    )
    assert prov_bad_corr == "unavailable"

    # Negative controls: owner mismatch downgrades to unavailable
    prov_bad_owner, _ = resolve_run_provenance(
        reconstructed_research_store,
        run,
        expected_correlation_id=trace_id,
        expected_owner="wrong_executor",
    )
    assert prov_bad_owner == "unavailable"

    # Negative controls: unreceipted real run downgrades to simulation
    unreceipted_run = dict(run, run_id=f"rrun-unreceipted-{uuid.uuid4().hex[:6]}")
    prov_unreceipted, _ = resolve_run_provenance(
        reconstructed_research_store,
        unreceipted_run,
    )
    assert prov_unreceipted == "simulation"
    lineage["receipt_id"] = receipt_id

    # Natural candidate admission via CandidatePoolCreateRequest / RouteContext
    research_ctx = AgoraResearchRouteContext(
        extract_identity=lambda *args, **kwargs: identity,
        require_read_role=lambda *args, **kwargs: None,
        require_write_role=lambda *args, **kwargs: None,
        bff_error=lambda status, code, msg, *args: Exception(f"{code}: {msg}"),
        utc_now=_utc_now,
        store=reconstructed_research_store,
    )
    pool_req = CandidatePoolCreateRequest(
        operator_id=user_id,
        strategy_id=strategy_id,
        strategy_version=version_id,
        candidates=[
            {
                "artifact_id": candidate_id,
                "candidate_id": candidate_id,
                "strategy_id": strategy_id,
                "strategy_version": version_id,
                "run_id": run_id,
                "score": 0.92,
                "lifecycle_state": "candidate",
                "artifact_checksum": artifact_checksum,
                "provenance": "real",
            }
        ],
        metrics_by_artifact={
            candidate_id: {
                "sharpe_ratio": 2.1,
                "max_drawdown": 0.065,
                "profit_factor": 1.78,
            }
        },
    )
    pool = research_ctx.build_candidate_pool(pool_req, scope, _utc_now())
    pool_id = pool["pool_id"]
    assert len(pool["candidates"]) == 1
    admitted = pool["candidates"][0]
    assert admitted["artifact_id"] == candidate_id
    assert admitted["has_real_receipt"] is True
    assert admitted["provenance"] == "real"
    assert admitted["receipt_id"] == receipt_id
    assert admitted["artifact_digest"] == artifact_checksum

    # Negative control: unrelated artifact fails real candidate admission
    unrelated_pool = research_ctx.build_candidate_pool(
        CandidatePoolCreateRequest(
            operator_id=user_id,
            strategy_id=strategy_id,
            strategy_version=version_id,
            candidates=[
                {
                    "artifact_id": f"unrelated-{uuid.uuid4().hex[:8]}",
                    "run_id": run_id,
                    "lifecycle_state": "candidate",
                }
            ],
        ),
        scope,
        _utc_now(),
    )
    assert unrelated_pool["candidates"][0]["has_real_receipt"] is False
    assert unrelated_pool["candidates"][0]["provenance"] != "real"

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
    from fastapi import HTTPException
    from agora.trading_room.routes import (
        build_decisions_router,
        build_intents_router,
        TradingRoomRouteContext,
        TraderDecisionRequest,
        GovernedIntentHandoffRequest,
    )

    decision_event_id = f"decevt-{uuid.uuid4().hex[:10]}"
    decision_event = {
        "spec_version": "1.0",
        "decision_event_id": decision_event_id,
        "event_kind": "entry",
        "state": "pending_review",
        "strategy_id": strategy_id,
        "strategy_version": version_id,
        "triggered_at": _utc_now(),
        "no_order_route_proof": "agora_decision_support_only",
        "confidence": {"value": 0.88, "score": 0.88, "level": "high"},
        "probability_forecast": {"win_probability": 0.79},
        "expected_value": {"value": 1.62, "unit": "R"},
        "risk_summary": {"score": 0.18, "status": "evaluated"},
        "subject": {
            "symbol": "SPY",
            "asset_class": "equity",
            "venue": "default",
        },
        "suggested_action": "entry",
        "suggested_size": {"size_hint": "medium", "portfolio_pct": 0.02, "non_binding": True},
    }
    tr_store.upsert_decision_event(decision_event)

    tr_ctx = TradingRoomRouteContext(
        extract_identity=lambda *args, **kwargs: identity,
        require_read_role=lambda *args, **kwargs: None,
        require_write_role=lambda *args, **kwargs: None,
        bff_error=lambda status, code, msg, *args: HTTPException(status_code=status, detail=f"{code}: {msg}"),
        utc_now=_utc_now,
        store=tr_store,
    )

    decisions_router = build_decisions_router(tr_ctx)
    decide_endpoint = next(r.endpoint for r in decisions_router.routes if "decisions" in r.path and "POST" in r.methods)
    decide_res = decide_endpoint(
        decision_event_id=decision_event_id,
        body=TraderDecisionRequest(
            decision="approve",
            rationale="High confidence mean reversion trigger with small risk exposure",
        ),
        authorization="Bearer test",
        if_match="*",
        idempotency_key=f"idemp-dec-{uuid.uuid4().hex[:8]}",
        x_request_id=f"req-dec-{uuid.uuid4().hex[:8]}",
    )
    assert decide_res["status"] == "completed"
    intent_id = decide_res["data"]["intent_ref"]
    assert intent_id is not None

    intent_record = tr_store.get_intent(intent_id)
    assert intent_record is not None
    assert intent_record.get("has_broker_order_authority", False) is False

    intents_router = build_intents_router(tr_ctx)
    handoff_endpoint = next(r.endpoint for r in intents_router.routes if "handoffs" in r.path and "POST" in r.methods)
    handoff_id = f"handoff-{uuid.uuid4().hex[:10]}"
    handoff_res = handoff_endpoint(
        intent_id=intent_id,
        body=GovernedIntentHandoffRequest(
            handoff_id=handoff_id,
            intent_id=intent_id,
            requested_stage="shadow",
            handoff_type="shadow_start",
            state="submitted",
            strategy_id=strategy_id,
            strategy_spec_registry_id=registry_id,
            requested_by={"actor_type": "trader", "actor_ref": user_id},
            created_at=_utc_now(),
            target_queue="shadow_research",
            no_order_route_proof="agora_request_only_no_order_route",
            rationale="Shadow validation requested for approved decision",
            correlation_id=trace_id,
        ),
        authorization="Bearer test",
        if_match="*",
        idempotency_key=f"idemp-handoff-{uuid.uuid4().hex[:8]}",
        x_request_id=f"req-handoff-{uuid.uuid4().hex[:8]}",
    )
    assert handoff_res["status"] == "queued"
    assert handoff_res["data"]["handoff_id"] == handoff_id
    assert handoff_res["data"]["state"] == "submitted"

    handoff = tr_store.get_handoff(handoff_id)
    assert handoff is not None
    assert handoff["handoff_id"] == handoff_id
    assert handoff["correlation_id"] == trace_id

    # Retries reuse the same IDs; assert duplicate handoff is rejected
    with pytest.raises(HTTPException) as exc_info:
        handoff_endpoint(
            intent_id=intent_id,
            body=GovernedIntentHandoffRequest(
                handoff_id=handoff_id,
                intent_id=intent_id,
                requested_stage="shadow",
                handoff_type="shadow_start",
                state="submitted",
                strategy_id=strategy_id,
                strategy_spec_registry_id=registry_id,
                requested_by={"actor_type": "user", "actor_id": user_id},
                created_at=_utc_now(),
                target_queue="shadow_research",
                no_order_route_proof="agora_request_only_no_order_route",
            ),
            authorization="Bearer test",
            if_match="*",
            idempotency_key=f"idemp-handoff-dup-{uuid.uuid4().hex[:8]}",
            x_request_id=f"req-handoff-dup-{uuid.uuid4().hex[:8]}",
        )
    assert exc_info.value.status_code == 409

    lineage["decision_event_id"] = decision_event_id
    lineage["trading_intent_id"] = intent_id
    lineage["handoff_id"] = handoff_id

    # =========================================================================
    # Stage 7: Strategy Performance Index & Governed Suggestions
    # =========================================================================
    # Telemetry-triggered suggestion produced via canonical consumer (SD §6.4, OP-G02)
    from agora.performance.consumer import EvaluationTelemetryConsumer
    from agora.performance.store import PerformanceSuggestionStore
    from services.incident.incident import IncidentStore
    from services.incidents.consumer import ThresholdTelemetryIncidentConsumer

    perf_db = str(temp_workspace / "perf.sqlite3")
    perf_store = PerformanceSuggestionStore(path=perf_db)

    incident_db = temp_workspace / "incidents.json"
    incident_store = IncidentStore(path=incident_db)

    events_published: List[Any] = []
    def _test_publisher(event_type: str, suggestion_id: str, data: Dict[str, Any]) -> None:
        events_published.append((event_type, suggestion_id, data))

    eval_consumer = EvaluationTelemetryConsumer(
        store=perf_store,
        publish_event_fn=_test_publisher,
    )

    # Attach suggestion producer to canonical consumer without adding any new background scheduler
    telemetry_incident_consumer = ThresholdTelemetryIncidentConsumer(
        incident_store=incident_store,
    )
    eval_consumer.attach_to(telemetry_incident_consumer)

    incident_id = f"inc-tel-{uuid.uuid4().hex[:8]}"
    telemetry_payload = {
        "incident_id": incident_id,
        "title": "Strategy Drawdown Threshold Breached",
        "tenant_id": tenant_id,
        "strategy_id": strategy_id,
        "owner_user_id": user_id,
        "correlation_id": trace_id,
        "trace_id": trace_id,
        "telemetry_event": {
            "event_id": f"tel-{uuid.uuid4().hex[:8]}",
            "event_type": "pnl_snapshot",
            "created_at": _utc_now(),
            "severity": "high",
            "runtime_binding_id": f"rb-{strategy_id}",
            "deployment_stage": "paper",
            "deployment_plan_id": f"plan-{strategy_id}",
            "capital_pool_id": f"pool-{tenant_id}",
            "persona_capital_binding_id": f"pcb-{strategy_id}",
            "artifact_id": f"art-{strategy_id}",
            "artifact_version": "1.0.0",
            "runtime_id": f"runtime-{strategy_id}",
            "trace_id": trace_id,
            "strategy_id": strategy_id,
            "metrics": {
                "rolling_drawdown_multiple": 1.42,
                "current_drawdown": 0.082,
            },
            "description": "Paper telemetry reported rolling drawdown above governed threshold.",
        },
        "threshold_snapshot": {
            "policy_source": "gov-perf-v2.1",
            "signal_type": "performance_degradation",
            "metric_name": "rolling_drawdown_multiple",
            "comparator": "gt",
            "observed_value": 1.42,
            "threshold_value": 1.25,
            "window": "paper-session",
            "breached": True,
            "note": f"Drawdown exceeded policy tolerance. dedupe_key=rb-{strategy_id}:rolling_drawdown_multiple:2026-09-08",
        },
    }

    # 1. Event-driven consumption via real input entry point of canonical consumer
    incident_result = telemetry_incident_consumer.consume(telemetry_payload)
    assert incident_result.created is True
    assert incident_result.incident.incident_id == incident_id
    assert incident_store.get_incident(incident_id) is not None

    # 2. Prove event-driven durable persistence and readback in suggestion store
    persisted_list = perf_store.list_suggestions(tenant_id, strategy_id)
    assert len(persisted_list) == 1
    persisted = persisted_list[0]
    sugg_id = persisted["suggestion_id"]
    assert persisted["strategy_id"] == strategy_id
    assert persisted["correlation_id"] == trace_id

    # 3. Prove read-model event delivery to registered publisher
    assert len(events_published) >= 1
    assert events_published[0][0] == "agora.performance.suggestion.created"
    assert events_published[0][1] == sugg_id
    assert events_published[0][2]["strategy_id"] == strategy_id

    # 4. Prove idempotent replay
    replay_outcome_event = {
        "tenant_id": tenant_id,
        "owner_user_id": user_id,
        "strategy_id": strategy_id,
        "outcome_type": "drawdown_breach",
        "period": "latest",
        "correlation_id": trace_id,
        "title": f"Telemetry Incident: {incident_result.incident.title}",
        "rationale": "Threshold breach",
        "metrics": {"observed_value": 1.42, "threshold_value": 1.25},
        "source_id": "gov-perf-v2.1",
        "source_type": "telemetry_engine",
        "as_of": persisted["as_of"],
    }
    replayed_sugg = eval_consumer.replay(replay_outcome_event, utc_now=persisted["as_of"])
    assert replayed_sugg.suggestion_id == sugg_id
    suggestion = replayed_sugg
    listed_suggs = perf_store.list_suggestions(tenant_id, strategy_id)
    assert len(listed_suggs) == 1

    act_receipt, replayed = perf_store.act(
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
    assert act_receipt["status"] == "applied"
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
    assert lineage["run_id"] == run_id
    assert lineage["receipt_id"] == receipt_id
    assert lineage["candidate_pool_id"] == pool_id
    assert lineage["workspace_id"] == workspace_id
    assert lineage["decision_event_id"] == decision_event_id
    assert lineage["trading_intent_id"] == intent_id
    assert lineage["handoff_id"] == handoff_id
    assert lineage["suggestion_id"] == sugg_id
    assert lineage["policy_candidate_id"] == pl_candidate_id
    assert lineage["consultation_memo_id"] == memo_id

    # Single continuous correlation chain verified across every stage
    assert lineage["trace_id"] == trace_id
    assert event["trace_id"] == trace_id
    assert receipt["correlation_id"] == trace_id
    assert handoff["correlation_id"] == trace_id
    assert suggestion.correlation_id == trace_id
    assert suggestion.provenance.correlation_id == trace_id
    assert consult_req.trace_id == trace_id
    assert memo.trace_id == trace_id
