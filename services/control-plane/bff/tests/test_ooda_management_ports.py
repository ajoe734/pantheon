"""Tests for OODA management domain ports and Management read models.

Validates:
1. OodaPacketsPort with existing OodaLoopStore / OodaJsonlAppendStore and mock providers
2. InterventionsPort and SynthesisConflictLogsPort
3. ManagementReviewQueuePort explicit compositions and allowedActions derivation
4. OodaManagementDomainPort combined interface
5. Five Management read models with narrow injected dependencies
6. FastAPI router integration for the 5 Management read model endpoints
"""

import pytest
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import os
import sys
from pathlib import Path

BFF_DIR = Path(__file__).resolve().parent.parent
OODA_DIR = BFF_DIR.parent / "ooda"
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))
if str(OODA_DIR) not in sys.path:
    sys.path.insert(0, str(OODA_DIR))

from domain_ports.ooda_management import (
    InterventionsPort,
    ManagementReviewQueuePort,
    OodaManagementDomainPort,
    OodaPacketsPort,
    SynthesisConflictLogsPort,
)
from management_read_models.router import (
    create_management_read_models_router,
    get_activity_read_model,
    get_formula_jobs_read_model,
    get_paper_telemetry_read_model,
    get_postmortem_detail_read_model,
    get_postmortems_read_model,
)
from ooda_loop_packet import (
    LoopEnvironment,
    LoopStatus,
    LoopType,
    OodaLoopPacket,
    OodaLoopStore,
)


def _sample_utc_now() -> str:
    return "2026-08-28T01:00:00Z"


def _sample_identity(auth: Optional[str] = None) -> Dict[str, Any]:
    return {"user_id": "operator-1", "roles": ["operator", "viewer"]}


def _sample_require_read_role(identity: Dict[str, Any]) -> None:
    pass


def _sample_snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    return {
        "snapshot_at": snapshot_at,
        "served_by": "test-bff",
    }


# ---------------------------------------------------------------------------
# 1. OodaPacketsPort Tests
# ---------------------------------------------------------------------------

class TestOodaPacketsPort:
    def test_ooda_packets_from_ooda_loop_store(self, tmp_path):
        store_path = str(tmp_path / "ooda.jsonl")
        store = OodaLoopStore(store_path)

        packet1 = OodaLoopPacket.create(
            loop_type=LoopType.PAPER_STRATEGY,
            environment=LoopEnvironment.PAPER,
            strategy_id="strat-alpha",
        )
        packet1.created_at = "2026-08-28T00:00:00Z"
        packet1.updated_at = "2026-08-28T00:05:00Z"
        store.add(packet1)

        packet2 = OodaLoopPacket.create(
            loop_type=LoopType.REBALANCE,
            environment=LoopEnvironment.DEV,
            capital_pool_id="pool-1",
        )
        packet2.created_at = "2026-08-28T00:10:00Z"
        packet2.updated_at = "2026-08-28T00:15:00Z"
        packet2.act.runtime_binding_id = "rt-binding-99"
        store.add(packet2)

        port = OodaPacketsPort(store=store)
        surface_status = port.get_surface_status()
        assert surface_status["status"] == "ok"
        assert surface_status["source"] == "store"

        # List all
        packets = port.list_ooda_packets()
        assert len(packets) == 2
        # Reverse chronological by updated_at
        assert packets[0]["packet_id"] == packet2.packet_id
        assert packets[1]["packet_id"] == packet1.packet_id

        # Filter by strategy_id
        strat_packets = port.list_ooda_packets_for_strategy("strat-alpha")
        assert len(strat_packets) == 1
        assert strat_packets[0]["packet_id"] == packet1.packet_id

        # Filter by runtime_id
        rt_packets = port.list_ooda_packets_for_runtime("rt-binding-99")
        assert len(rt_packets) == 1
        assert rt_packets[0]["packet_id"] == packet2.packet_id

        # Get single packet
        found = port.get_ooda_packet(packet1.packet_id)
        assert found is not None
        assert found["strategy_id"] == "strat-alpha"

        # Non-existent packet
        assert port.get_ooda_packet("ooda-nonexistent") is None
        assert port.get_ooda_packet(None) is None

    def test_ooda_packets_from_records_provider(self):
        records = [
            {
                "packet_id": "ooda-rec-1",
                "status": "observing",
                "stage": "observe",
                "strategy_id": "strat-1",
                "evolution_program_id": "evo-prog-1",
                "created_at": "2026-08-28T00:00:00Z",
                "updated_at": "2026-08-28T00:01:00Z",
            },
            {
                "packet_id": "ooda-rec-2",
                "status": "closed",
                "stage": "learn",
                "strategy_id": "strat-2",
                "runtime_id": "rt-2",
                "created_at": "2026-08-28T00:02:00Z",
                "updated_at": "2026-08-28T00:03:00Z",
            },
        ]
        port = OodaPacketsPort(records_provider=lambda: records)
        assert port.get_surface_status()["status"] == "ok"

        # Filter by status and stage
        obs = port.list_ooda_packets(status="observing")
        assert len(obs) == 1
        assert obs[0]["packet_id"] == "ooda-rec-1"

        # Filter by evolution program
        evo = port.list_ooda_packets_for_evolution_program("evo-prog-1")
        assert len(evo) == 1
        assert evo[0]["packet_id"] == "ooda-rec-1"

    def test_ooda_packets_unavailable_state(self):
        port = OodaPacketsPort()
        status = port.get_surface_status()
        assert status["status"] == "unavailable"
        assert port.list_ooda_packets() == []
        assert port.get_ooda_packet("any") is None


# ---------------------------------------------------------------------------
# 2. InterventionsPort Tests
# ---------------------------------------------------------------------------

class TestInterventionsPort:
    def test_interventions_filtering_and_retrieval(self):
        data = [
            {
                "intervention_id": "int-1",
                "kind": "circuit_breaker",
                "status": "triggered",
                "triggered_at": "2026-08-28T00:01:00Z",
            },
            {
                "intervention_id": "int-2",
                "kind": "manual_override",
                "status": "resolved",
                "triggered_at": "2026-08-28T00:02:00Z",
            },
        ]
        port = InterventionsPort(records_provider=lambda: data)
        assert port.get_surface_status()["status"] == "ok"

        # List all sorted by triggered_at desc
        all_ints = port.list_interventions()
        assert len(all_ints) == 2
        assert all_ints[0]["intervention_id"] == "int-2"

        # Filter by kind
        cb = port.list_interventions(kind="circuit_breaker")
        assert len(cb) == 1
        assert cb[0]["intervention_id"] == "int-1"

        # Filter by status
        res = port.list_interventions(status="resolved")
        assert len(res) == 1
        assert res[0]["intervention_id"] == "int-2"

        # Get intervention
        assert port.get_intervention("int-1")["kind"] == "circuit_breaker"
        assert port.get_intervention("non-existent") is None
        assert port.get_intervention(None) is None


# ---------------------------------------------------------------------------
# 3. SynthesisConflictLogsPort Tests
# ---------------------------------------------------------------------------

class TestSynthesisConflictLogsPort:
    def test_synthesis_conflict_logs_matching(self):
        logs = [
            {
                "log_id": "slog-1",
                "capital_pool_id": "pool-a",
                "scope_ref": "scope-1",
                "proposal_ids": ["prop-100", "prop-101"],
                "sponsor_persona_id": "persona-alpha",
                "synthesis_method": "weighted_consensus",
                "committee_ref": "comm-1",
                "timestamp": "2026-08-28T00:01:00Z",
            },
            {
                "log_id": "slog-2",
                "capital_pool_id": "pool-b",
                "scope_ref": "scope-2",
                "weighting_inputs": {"prop-200": 0.6, "prop-201": 0.4},
                "vetoed_proposals": [{"proposal_id": "prop-202", "reason": "risk"}],
                "sponsor_persona_id": "persona-beta",
                "synthesis_method": "veto_override",
                "timestamp": "2026-08-28T00:02:00Z",
            },
        ]
        port = SynthesisConflictLogsPort(records_provider=lambda: logs)

        # Match by proposal_id in proposal_ids list
        p100_logs = port.list_synthesis_conflict_logs(proposal_id="prop-100")
        assert len(p100_logs) == 1
        assert p100_logs[0]["log_id"] == "slog-1"

        # Match by proposal_id in weighting_inputs keys
        p200_logs = port.list_synthesis_conflict_logs(proposal_id="prop-200")
        assert len(p200_logs) == 1
        assert p200_logs[0]["log_id"] == "slog-2"

        # Match by proposal_id in vetoed_proposals
        p202_logs = port.list_synthesis_conflict_logs(proposal_id="prop-202")
        assert len(p202_logs) == 1
        assert p202_logs[0]["log_id"] == "slog-2"

        # Filter by capital_pool_id
        pool_a_logs = port.list_synthesis_conflict_logs(capital_pool_id="pool-a")
        assert len(pool_a_logs) == 1
        assert pool_a_logs[0]["log_id"] == "slog-1"

        # Get log
        assert port.get_synthesis_conflict_log("slog-1") is not None
        assert port.get_synthesis_conflict_log("unknown") is None


# ---------------------------------------------------------------------------
# 4. ManagementReviewQueuePort Tests (Explicit Compositions)
# ---------------------------------------------------------------------------

class TestManagementReviewQueuePort:
    def test_governance_review_queue_composition(self):
        plans = [
            {
                "plan_id": "plan-1",
                "status": "pending_review",
                "target_stage": "paper",
                "created_at": "2026-08-28T00:00:00Z",
                "created_by": "user-dev",
                "approval_decision_id": "dec-1",
            },
            {
                "plan_id": "plan-2",
                "status": "active",  # Not reviewable
                "target_stage": "live",
            },
        ]
        decisions = [
            {
                "decision_id": "dec-1",
                "risk_level": "medium",
                "outcome": "approved",
                "state": "reviewed",
                "decided_at": "2026-08-28T00:01:00Z",
                "reviewer": "claude",
            },
            {
                "decision_id": "dec-2",
                "risk_level": "high",
                "state": "under_review",
                "outcome": "pending",
                "target_type": "RiskPolicyChange",
                "target_id": "pol-9",
                "target_version": "2.0.0",
                "created_at": "2026-08-28T00:02:00Z",
                "created_by": "risk-service",
            },
        ]
        evo_decisions = [
            {
                "decision_id": "evo-1",
                "status": "proposed",
                "decision_state": "under_review",
                "risk_level": "low",
                "created_at": "2026-08-28T00:03:00Z",
                "created_by_id": "evo-runner",
                "rationale": "Alpha evolution candidate.",
            }
        ]

        port = ManagementReviewQueuePort(
            deployment_plans_reader=lambda: plans,
            approval_decisions_reader=lambda: decisions,
            evolution_decisions_reader=lambda: evo_decisions,
        )

        items = port.list_governance_review_queue_items()
        assert len(items) == 3

        # Item 1: DeploymentPlan (plan-1)
        plan_item = next(i for i in items if i["item_type"] == "DeploymentPlan")
        assert plan_item["item_id"] == "review-plan-1"
        assert plan_item["risk_level"] == "medium"
        assert plan_item["allowedActions"]["canPromoteToPaper"] is True
        assert plan_item["review_summary"]["reviewer"] == "claude"

        # Item 2: EvolutionDecision (evo-1)
        evo_item = next(i for i in items if i["item_type"] == "EvolutionDecision")
        assert evo_item["item_id"] == "review-evo-1"
        assert evo_item["allowedActions"]["canApprove"] is True
        assert "Alpha evolution candidate" in evo_item["review_summary"]["riskSummary"]

        # Item 3: ApprovalDecision (dec-2, unlinked)
        app_item = next(i for i in items if i["item_type"] == "ApprovalDecision")
        assert app_item["item_id"] == "review-dec-2"
        assert app_item["risk_level"] == "high"
        assert app_item["allowedActions"]["canApprove"] is True

        # Filter by item_types
        only_plans = port.list_governance_review_queue_items(item_types=["DeploymentPlan"])
        assert len(only_plans) == 1
        assert only_plans[0]["item_id"] == "review-plan-1"

    def test_approval_queue_items_composition(self):
        decisions = [
            {
                "decision_id": "dec-pending-1",
                "target_type": "StrategyPromotion",
                "risk_level": "medium",
                "created_at": "2026-08-28T00:00:00Z",
                "actor_id": "governance-admin",
                "decision_state": "under_review",
                "rationale": "Strategy passes backtest criteria.",
                "evidence_refs": ["ev-1", "ev-2"],
                "target_id": "strat-42",
                "target_version": "1.1.0",
            },
            {
                "decision_id": "dec-approved-already",
                "decision_state": "approved",
                "outcome": "approved",  # Should be excluded
            },
        ]
        port = ManagementReviewQueuePort(
            approval_decisions_reader=lambda: decisions,
        )

        app_queue = port.list_approval_queue_items()
        assert len(app_queue) == 1
        item = app_queue[0]
        assert item["decision_id"] == "dec-pending-1"
        assert item["decision_type"] == "StrategyPromotion"
        assert item["allowedActions"]["canApprove"] is True
        assert item["decision_context"]["required_approvals"] == 1
        assert item["decision_context"]["governance_chain"]["target_id"] == "strat-42"


# ---------------------------------------------------------------------------
# 5. Combined OodaManagementDomainPort Tests
# ---------------------------------------------------------------------------

class TestOodaManagementDomainPort:
    def test_combined_port_delegations(self):
        port = OodaManagementDomainPort(
            ooda_port=OodaPacketsPort(records_provider=lambda: [{"packet_id": "ooda-1", "created_at": "2026-08-28T00:00:00Z"}]),
            interventions_port=InterventionsPort(records_provider=lambda: [{"intervention_id": "int-1", "triggered_at": "2026-08-28T00:00:00Z"}]),
            synthesis_conflict_logs_port=SynthesisConflictLogsPort(records_provider=lambda: [{"log_id": "log-1", "timestamp": "2026-08-28T00:00:00Z"}]),
            review_queue_port=ManagementReviewQueuePort(),
        )

        assert len(port.list_ooda_packets()) == 1
        assert port.get_ooda_packet("ooda-1")["packet_id"] == "ooda-1"
        assert len(port.list_interventions()) == 1
        assert port.get_intervention("int-1")["intervention_id"] == "int-1"
        assert len(port.list_synthesis_conflict_logs()) == 1
        assert port.get_synthesis_conflict_log("log-1")["log_id"] == "log-1"
        assert port.list_governance_review_queue_items() == []


# ---------------------------------------------------------------------------
# 6. Management Read Models Unit Tests
# ---------------------------------------------------------------------------

class TestManagementReadModelsUnits:
    def test_get_formula_jobs_read_model(self):
        jobs = [
            {
                "job_id": "job-1",
                "formula_id": "formula-momentum",
                "status": "completed",
                "submitted_at": "2026-08-28T00:00:00Z",
                "metrics": {"sharpe": 1.8},
            },
            {
                "job_id": "job-2",
                "formula_id": "formula-meanrev",
                "status": "running",
                "submitted_at": "2026-08-28T00:05:00Z",
            },
        ]
        res = get_formula_jobs_read_model(
            jobs_reader=lambda: (True, jobs),
            formula_jobs_reader=lambda: (False, []),
            utc_now=_sample_utc_now,
        )
        assert res["source"] == "service"
        assert len(res["items"]) == 2
        assert res["items"][0]["job_id"] == "job-2"  # newer first

        # Filter by status
        res_comp = get_formula_jobs_read_model(
            status="completed",
            jobs_reader=lambda: (True, jobs),
            utc_now=_sample_utc_now,
        )
        assert len(res_comp["items"]) == 1
        assert res_comp["items"][0]["job_id"] == "job-1"

    def test_get_activity_read_model(self):
        act_records = [
            {
                "event_id": "act-1",
                "action_type": "session.start",
                "actor_id": "user-a",
                "timestamp": "2026-08-28T00:00:00Z",
            }
        ]
        tel_records = [
            {
                "id": "tel-1",
                "type": "telemetry.sample",
                "actor_id": "agent-b",
                "timestamp": "2026-08-28T00:01:00Z",
            }
        ]
        res = get_activity_read_model(
            activity_audit_reader=lambda: (True, act_records),
            governance_audit_reader=lambda: (False, []),
            telemetry_events_reader=lambda: ("telemetry", tel_records),
            utc_now=_sample_utc_now,
        )
        assert res["source"] in ("audit", "telemetry")
        assert len(res["items"]) == 2
        assert res["surfaces"]["activity_audit"]["status"] == "ok"
        assert res["surfaces"]["telemetry_events"]["status"] == "ok"

    def test_get_paper_telemetry_read_model(self):
        bindings = [
            {
                "strategy_id": "strat-paper-1",
                "persona_id": "persona-1",
                "status": "active",
                "paper_ledger_id": "ledger-p1",
            }
        ]
        telemetry = [
            {
                "strategy_id": "strat-paper-1",
                "timestamp": "2026-08-28T00:01:00Z",
                "metrics": {"equity": 10500.0, "drawdown_pct": 0.02, "open_positions": 3, "daily_pnl": 500.0},
            }
        ]
        res = get_paper_telemetry_read_model(
            runtime_bindings_reader=lambda: (True, bindings),
            telemetry_events_reader=lambda: ("telemetry", telemetry),
            utc_now=_sample_utc_now,
        )
        assert res["source"] == "service"
        assert len(res["items"]) == 1
        item = res["items"][0]
        assert item["strategy_id"] == "strat-paper-1"
        assert len(item["series"]) == 1
        assert item["series"][0]["equity"] == 10500.0

    def test_get_paper_telemetry_read_model_missing_paper_ledger_id(self):
        # Case 1: Binding without paper_ledger_id, but with binding_id
        bindings_with_binding_id = [
            {
                "strategy_id": "strat-paper-2",
                "persona_id": "persona-2",
                "status": "active",
                "binding_id": "bind-xyz",
            }
        ]
        res1 = get_paper_telemetry_read_model(
            runtime_bindings_reader=lambda: (True, bindings_with_binding_id),
            utc_now=_sample_utc_now,
        )
        assert len(res1["items"]) == 1
        assert res1["items"][0]["paper_ledger_id"] == "ledger-bind-xyz"

        # Case 2: Binding without paper_ledger_id or binding_id, but with id
        bindings_with_id = [
            {
                "strategy_id": "strat-paper-3",
                "persona_id": "persona-3",
                "status": "active",
                "id": "item-id-123",
            }
        ]
        res2 = get_paper_telemetry_read_model(
            runtime_bindings_reader=lambda: (True, bindings_with_id),
            utc_now=_sample_utc_now,
        )
        assert len(res2["items"]) == 1
        assert res2["items"][0]["paper_ledger_id"] == "ledger-item-id-123"

        # Case 3: Minimal binding without paper_ledger_id, binding_id, or id (direct repro of Claude blocker)
        minimal_bindings = [
            {
                "strategy_id": "strat-paper-4",
                "persona_id": "persona-4",
                "status": "active",
            }
        ]
        res3 = get_paper_telemetry_read_model(
            runtime_bindings_reader=lambda: (True, minimal_bindings),
            utc_now=_sample_utc_now,
        )
        assert len(res3["items"]) == 1
        assert res3["items"][0]["strategy_id"] == "strat-paper-4"
        assert res3["items"][0]["paper_ledger_id"] == "ledger-strat-paper-4"


    def test_get_postmortems_and_detail_read_model(self):
        postmortems = [
            {
                "postmortem_id": "pm-100",
                "incident_id": "inc-1",
                "title": "Latency Spike Incident",
                "severity": "high",
                "status": "resolved",
                "created_at": "2026-08-28T00:00:00Z",
                "action_items": ["Upgrade node memory", "Tune garbage collection"],
            }
        ]
        res = get_postmortems_read_model(
            postmortems_reader=lambda: (True, postmortems),
            utc_now=_sample_utc_now,
        )
        assert res["source"] == "store"
        assert len(res["items"]) == 1
        assert res["items"][0]["postmortem_id"] == "pm-100"
        assert len(res["items"][0]["action_items"]) == 2

        # Detail query
        detail = get_postmortem_detail_read_model(
            postmortem_id="pm-100",
            postmortems_reader=lambda: (True, postmortems),
            utc_now=_sample_utc_now,
        )
        assert detail["source"] == "store"
        assert detail["item"]["title"] == "Latency Spike Incident"

        # Missing detail
        missing = get_postmortem_detail_read_model(
            postmortem_id="pm-missing",
            postmortems_reader=lambda: (True, postmortems),
            utc_now=_sample_utc_now,
        )
        assert missing["source"] == "store"
        assert missing["item"] is None


# ---------------------------------------------------------------------------
# 7. FastAPI Router Integration Tests
# ---------------------------------------------------------------------------

class TestManagementReadModelsRouterIntegration:
    @pytest.fixture
    def test_client(self):
        app = FastAPI()

        jobs_data = [
            {
                "job_id": "job-live-1",
                "formula_id": "form-1",
                "status": "completed",
                "submitted_at": "2026-08-28T00:00:00Z",
            }
        ]
        postmortems_data = [
            {
                "postmortem_id": "pm-live-1",
                "incident_id": "inc-42",
                "title": "Feed Desync Incident",
                "severity": "critical",
                "status": "under_review",
                "created_at": "2026-08-28T00:00:00Z",
            }
        ]

        router = create_management_read_models_router(
            extract_identity=_sample_identity,
            require_read_role=_sample_require_read_role,
            snapshot_meta=_sample_snapshot_meta,
            utc_now=_sample_utc_now,
            jobs_reader=lambda: (True, jobs_data),
            postmortems_reader=lambda: (True, postmortems_data),
        )
        app.include_router(router)
        return TestClient(app)

    def test_endpoint_formula_jobs(self, test_client):
        resp = test_client.get("/bff/management/formula-jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["id"] == "management-formula-jobs"
        assert len(data["data"]["items"]) == 1
        assert data["data"]["items"][0]["job_id"] == "job-live-1"
        assert data["meta"]["surfaces"]["formula_jobs"]["status"] == "ok"

    def test_endpoint_postmortems_list_and_detail(self, test_client):
        resp = test_client.get("/bff/management/postmortems")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]["items"]) == 1
        assert data["data"]["items"][0]["postmortem_id"] == "pm-live-1"

        # Detail endpoint - success
        resp_detail = test_client.get("/bff/management/postmortems/pm-live-1")
        assert resp_detail.status_code == 200
        assert resp_detail.json()["data"]["title"] == "Feed Desync Incident"

        # Detail endpoint - 404
        resp_404 = test_client.get("/bff/management/postmortems/pm-nonexistent")
        assert resp_404.status_code == 404
        assert "not found" in resp_404.json()["detail"].lower()
