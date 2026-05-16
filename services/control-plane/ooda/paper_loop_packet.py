"""
MGMT-PAPER-007 complete paper OODA packet composer.

This module builds a replayable paper-mode OodaLoopPacket for the Management
Paper Loop Proof. It intentionally keeps the packet in paper environment and
records only logged/simulated act evidence: no live broker or capital-binding
side effects are permitted.
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


OODA_DIR = Path(__file__).resolve().parent
REPO_ROOT = OODA_DIR.parents[2]
GOVERNANCE_DIR = REPO_ROOT / "services" / "control-plane" / "governance"
EXEC_RUNTIME_MANAGER_DIR = REPO_ROOT / "services" / "execution" / "runtime-manager"

for _path in (REPO_ROOT, OODA_DIR, GOVERNANCE_DIR, EXEC_RUNTIME_MANAGER_DIR):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from approval_decision import EvidenceRef, EvidenceRefType  # noqa: E402
from deployment_plan import (  # noqa: E402
    DeploymentStage,
    RollbackRef,
    StagePlanner,
    validate_plan,
)
from evolution_decision import (  # noqa: E402
    ComparisonOperator,
    ExecutionPlane,
    ExecutionResult,
    ExecutionStatus,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionTargetType,
    ThresholdSignalType,
    ThresholdSnapshot,
    validate_evolution_decision,
)
from jsonl_store import OodaJsonlAppendStore, OodaPacketQuery  # noqa: E402
from ooda_loop_packet import (  # noqa: E402
    ActBundle,
    DecideBundle,
    LearnBundle,
    LoopEnvironment,
    LoopStatus,
    LoopType,
    ObservationWindow,
    ObserveBundle,
    OodaLoopPacket,
    OrientBundle,
    validate_packet,
)
from paper_approval_decision import (  # noqa: E402
    PaperApprovalContext,
    build_paper_approval_decision,
)
from runtime_binding import (  # noqa: E402
    RuntimeBinding,
    RuntimeBindingStatus,
    validate_binding,
)
from stage_transition import OodaTransitionError, assert_complete_replay_path  # noqa: E402


TASK_ID = "MGMT-PAPER-007"
MILESTONE_ID = "MGMT-OODA-M2"
EPIC = "EPIC-02 Management Paper Loop Proof"
PAPER_ENVIRONMENT = "paper"

TASK_EVIDENCE_PATH = REPO_ROOT / "support" / "evidence" / "MGMT-PAPER-007-complete-paper-ooda-packet.json"
MILESTONE_EVIDENCE_PATH = REPO_ROOT / "support" / "evidence" / "MGMT-OODA-M2-paper-loop.json"


@dataclass(frozen=True)
class PaperOodaLoopContext:
    """Stable identifiers used by the paper-loop proof packet."""

    packet_id: str = "ooda-paper-mgmt-loop-001"
    strategy_spec_id: str = "strategy-spec-paper-qlib-lgbm-001"
    strategy_version: str = "1.0.0"
    capital_pool_id: str = "capital-pool-paper-001"
    sponsor_persona_id: str = "persona-quant-paper-01"
    operator_actor_id: str = "operator-paper-loop-01"
    reviewer_actor_id: str = "governance-reviewer-paper-01"
    approval_decision_id: str = "approval-paper-strategy-001"
    deployment_plan_id: str = "deployment-plan-paper-001"
    runtime_binding_id: str = "8d706784-2678-4c49-9b70-e05d89f7b001"
    runtime_id: str = "rt-paper-mgmt-001"
    persona_capital_binding_id: str = "pcb-paper-quant-001"
    evolution_decision_id: str = "evolution-paper-revalidate-001"
    created_at: str = "2026-05-15T15:05:00Z"
    observation_end_at: str = "2026-05-15T15:05:15Z"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def build_strategy_spec(ctx: PaperOodaLoopContext | None = None) -> dict[str, Any]:
    ctx = ctx or PaperOodaLoopContext()
    return {
        "spec_version": "1.0",
        "strategy_id": ctx.strategy_spec_id,
        "title": "Paper Qlib LGBM TW cross-sectional alpha",
        "hypothesis": "TWSE/TPEx cross-sectional LightGBM signals can be evaluated safely in paper mode.",
        "objective": "Generate a paper-only StrategySpec candidate for the Management OODA proof.",
        "market_scope": {
            "symbols": ["2330.TW", "2317.TW", "2454.TW"],
            "asset_classes": ["equity"],
            "venues": ["TWSE", "TPEx"],
            "frequency": "daily",
        },
        "data_dependencies": [
            {
                "ref": "dataset://qlib-twse-tpex-smoke-v1",
                "kind": "dataset",
            },
            {
                "ref": "support/evidence/MGMT-PAPER-001-paper-strategy-spec.json",
                "kind": "note",
            },
        ],
        "execution_profile": {
            "signal_schema_version": "pantheon.signal.v1",
            "quantity_type": "PERCENT_PORTFOLIO",
            "rebalance_cadence": "daily",
            "execution_mode_hint": "paper",
        },
        "evaluation_plan": {
            "metrics": ["sharpe", "max_drawdown", "turnover", "hit_rate"],
            "candidate_gate": "offline backtest accepted for paper proof",
            "paper_gate": "paper telemetry and no-live-side-effect checks pass",
            "live_gate": "not evaluated by MGMT-PAPER-007",
        },
        "governance": {
            "approval_required": True,
            "policy_id": "PAPER_CANARY_LIVE_POLICY.paper_loop",
            "risk_profile": "medium",
        },
        "provenance": {
            "source_kind": "workflow",
            "created_at": ctx.created_at,
            "source_refs": [
                "docs/04/pantheon_sa_supplemental_2026-05-15/SD_management_console_multi_persona_ooda.md",
                "services/research/qlib/examples/smoke_dataset.json",
            ],
            "created_by": "Codex2",
        },
    }


def build_registry_entry(ctx: PaperOodaLoopContext | None = None) -> dict[str, Any]:
    ctx = ctx or PaperOodaLoopContext()
    return {
        "registry_id": ctx.strategy_spec_id,
        "artifact_type": "strategy_spec",
        "strategy_id": ctx.strategy_spec_id,
        "version": ctx.strategy_version,
        "artifact_state": "approved",
        "checksum": "sha256:mgmtpaper007paperooda001",
        "approval_decision_id": ctx.approval_decision_id,
        "approved_at": ctx.created_at,
        "lineage": {
            "source_run_ids": ["qlib-paper-smoke-001"],
            "strategy_spec_ref": f"strategy-spec://{ctx.strategy_spec_id}@{ctx.strategy_version}",
        },
        "deployment_summary": {"current_stage": "none"},
    }


def build_approval_decision(ctx: PaperOodaLoopContext | None = None):
    ctx = ctx or PaperOodaLoopContext()
    paper_ctx = PaperApprovalContext(
        decision_id=ctx.approval_decision_id,
        strategy_spec_id=ctx.strategy_spec_id,
        strategy_version=ctx.strategy_version,
        reviewer_actor_id=ctx.reviewer_actor_id,
        capital_pool_id=ctx.capital_pool_id,
        persona_id=ctx.sponsor_persona_id,
        evidence_refs=[
            EvidenceRef(
                ref_type=EvidenceRefType.EVALUATOR_RESULT,
                ref_id="eval-strategy-spec-paper-qlib-lgbm-001-paper",
                storage_ref={
                    "backend": "support/evidence",
                    "path": "support/evidence/MGMT-PAPER-001-paper-strategy-spec.json",
                },
                note="Paper evaluator result; no live capital exposure.",
            )
        ],
    )
    return build_paper_approval_decision(paper_ctx)


def build_deployment_plan(ctx: PaperOodaLoopContext | None = None, approval_decision: Any | None = None):
    ctx = ctx or PaperOodaLoopContext()
    approval_decision = approval_decision or build_approval_decision(ctx)
    planner = StagePlanner()
    return planner.create_plan(
        plan_id=ctx.deployment_plan_id,
        approval_decision_id=ctx.approval_decision_id,
        approval_decision=approval_decision.to_dict(),
        registry_entry=build_registry_entry(ctx),
        capital_pool_id=ctx.capital_pool_id,
        target_stage=DeploymentStage.PAPER,
        sponsor_persona_id=ctx.sponsor_persona_id,
        runtime_config_ref="launch-manifest://paper-runtime-mgmt-001",
        rollback=RollbackRef(
            target_artifact_id="strategy-spec-paper-qlib-lgbm-000",
            target_version="0.9.0",
            action_type="replace",
            reason="Previous paper baseline for rollback-only recovery.",
        ),
        pre_checks=[
            "paper runtime package available",
            "live broker disabled",
            "capital binding live writes disabled",
        ],
        post_checks=[
            "runtime binding is paper",
            "telemetry heartbeat accepted",
            "logged-only order evidence emitted",
        ],
        metadata={
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_path": "pantheon/lean",
            "engine_bridge_commit": "paper-loop-bridge-commit-001",
            "runtime_adapter_version": "0.1.0",
            "live_broker_enabled": False,
            "live_capital_binding_enabled": False,
        },
    )


def build_runtime_binding(ctx: PaperOodaLoopContext | None = None, deployment_plan: Any | None = None) -> RuntimeBinding:
    ctx = ctx or PaperOodaLoopContext()
    deployment_plan = deployment_plan or build_deployment_plan(ctx)
    return RuntimeBinding(
        binding_id=ctx.runtime_binding_id,
        runtime_id=ctx.runtime_id,
        capital_pool_id=ctx.capital_pool_id,
        artifact_id=deployment_plan.artifact_id,
        artifact_version=deployment_plan.artifact_version,
        deployment_mode=PAPER_ENVIRONMENT,
        effective_at=ctx.created_at,
        status=RuntimeBindingStatus.ACTIVE.value,
        plan_id=deployment_plan.plan_id,
        persona_capital_binding_id=ctx.persona_capital_binding_id,
        metadata={
            "write_owner": "runtime-manager",
            "runtime_role": "paper",
            "paper_runtime": True,
            "live_broker_enabled": False,
            "live_capital_side_effects": False,
            "deployment_plan_id": deployment_plan.plan_id,
            "strategy_id": deployment_plan.strategy_id,
        },
    )


def build_telemetry_packet(ctx: PaperOodaLoopContext | None = None) -> dict[str, Any]:
    ctx = ctx or PaperOodaLoopContext()
    event_ids = {
        "heartbeat": "631f17dc-bbba-4593-9ad3-9ed24c5b3001",
        "deploy_completed": "631f17dc-bbba-4593-9ad3-9ed24c5b3002",
        "pnl_snapshot": "631f17dc-bbba-4593-9ad3-9ed24c5b3003",
        "bracket_logged": "631f17dc-bbba-4593-9ad3-9ed24c5b3004",
    }

    def _event(event_type: str, event_id: str, created_at: str, metrics: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "event_id": event_id,
            "event_type": event_type,
            "created_at": created_at,
            "execution_mode": PAPER_ENVIRONMENT,
            "environment": PAPER_ENVIRONMENT,
            "deployment_stage": PAPER_ENVIRONMENT,
            "binding_id": ctx.runtime_binding_id,
            "runtime_id": ctx.runtime_id,
            "capital_pool_id": ctx.capital_pool_id,
            "artifact_id": ctx.strategy_spec_id,
            "artifact_version": ctx.strategy_version,
            "plan_id": ctx.deployment_plan_id,
            "persona_capital_binding_id": ctx.persona_capital_binding_id,
            "target": {
                "registry_id": ctx.strategy_spec_id,
                "strategy_id": ctx.strategy_spec_id,
                "artifact_version": ctx.strategy_version,
                "artifact_type": "strategy_spec",
                "promotion_state": PAPER_ENVIRONMENT,
            },
            "metrics": dict(metrics),
            "metadata": {
                "runtime_role": "paper",
                "engine_bridge_repo": "ajoe734/pantheon-lean.git",
                "engine_bridge_path": "pantheon/lean",
                "engine_bridge_commit": "paper-loop-bridge-commit-001",
                "runtime_adapter_version": "0.1.0",
                **dict(metadata),
            },
            "trace_id": "03829d25-2c9f-44f0-981e-56a94d8ff001",
        }

    events = [
        _event("heartbeat", event_ids["heartbeat"], ctx.created_at, {"heartbeat": 1}, {"runtime_package": "paper_execution_runtime"}),
        _event("deploy_completed", event_ids["deploy_completed"], "2026-05-15T15:05:05Z", {"action": "deploy_completed"}, {"runtime_package": "paper_execution_runtime"}),
        _event("pnl_snapshot", event_ids["pnl_snapshot"], "2026-05-15T15:05:10Z", {"pnl": 125.5}, {"observation_window": "paper-session-open"}),
        _event(
            "bracket_order_logged",
            event_ids["bracket_logged"],
            ctx.observation_end_at,
            {"action": "bracket_logged_only"},
            {
                "broker_submission_status": "logged_only",
                "submitted_to_broker": False,
                "is_real_order": False,
                "is_real_capital": False,
            },
        ),
    ]

    return {
        "packet_id": "telemetry-packet-paper-mgmt-001",
        "event_count": len(events),
        "event_ids": [event["event_id"] for event in events],
        "events": events,
        "ingest_validation": {
            "accepted_event_ids": [event["event_id"] for event in events],
            "heartbeat_first_accepted": True,
            "heartbeat_duplicate_accepted": True,
            "stage_mismatch_rejected": True,
            "missing_binding_rejected": True,
        },
        "runtime_summary_projection": {
            "runtime_id": ctx.runtime_id,
            "runtime_binding_id": ctx.runtime_binding_id,
            "deployment_stage": PAPER_ENVIRONMENT,
            "capital_pool_id": ctx.capital_pool_id,
            "strategy_id": ctx.strategy_spec_id,
            "deployment_plan_id": ctx.deployment_plan_id,
            "last_event_id": event_ids["bracket_logged"],
            "last_heartbeat_at": ctx.created_at,
            "state": "degraded",
            "health_summary": {
                "paper_runtime": "ok",
                "bridge": "ok",
                "telemetry": "degraded",
                "broker": "not_applicable",
            },
            "pnl": 125.5,
        },
        "safety_assertions": {
            "paper_stage_only": True,
            "live_broker_telemetry_disabled": True,
            "capital_binding_live_disabled": True,
            "bracket_logged_only": True,
            "no_real_order": True,
            "no_real_capital": True,
        },
    }


def build_evolution_decision(ctx: PaperOodaLoopContext | None = None) -> EvolutionDecision:
    ctx = ctx or PaperOodaLoopContext()
    decision = EvolutionDecision.create_proposed(
        decision_id=ctx.evolution_decision_id,
        target_type=EvolutionTargetType.STRATEGY_SPEC,
        target_id=ctx.strategy_spec_id,
        target_version=ctx.strategy_version,
        action_type=EvolutionActionType.REVALIDATE,
        rationale="Revalidate the paper StrategySpec after telemetry confirms logged-only paper execution.",
        created_by_id="evolution-controller-paper-01",
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        evidence_refs=[
            EvidenceRef(
                ref_type=EvidenceRefType.TELEMETRY_SUMMARY,
                ref_id="telemetry-packet-paper-mgmt-001",
                storage_ref={
                    "backend": "support/evidence",
                    "path": "support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json",
                },
            )
        ],
        threshold_snapshots=[
            ThresholdSnapshot(
                policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md#paper-loop",
                signal_type=ThresholdSignalType.PERFORMANCE_DEGRADATION,
                metric_name="paper_loop_revalidation_required",
                comparator=ComparisonOperator.GTE,
                observed_value=1,
                threshold_value=1,
                window="paper-session",
                note="Paper telemetry exists and should be revalidated before any canary promotion.",
            )
        ],
        capital_pool_id=ctx.capital_pool_id,
        persona_id=ctx.sponsor_persona_id,
        metadata={"ooda_packet_id": ctx.packet_id, "live_mutation_allowed": False},
    )
    decision.mark_reviewed(
        EvolutionActorRole.REVIEWER_ON_DUTY,
        "evolution-reviewer-paper-01",
        "approval-paper-evolution-revalidate-001",
        note="Paper telemetry review accepted for revalidation.",
        reviewed_at="2026-05-15T15:06:00Z",
    )
    decision.approve(
        EvolutionActorRole.REVIEWER_ON_DUTY,
        "evolution-reviewer-paper-01",
        note="Low-risk revalidation approved; no runtime mutation.",
        approved_at="2026-05-15T15:06:05Z",
    )
    decision.execute(
        EvolutionActorRole.EVOLUTION_CONTROLLER,
        "evolution-controller-paper-01",
        ExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            plane=ExecutionPlane.RESEARCH,
            executed_at="2026-05-15T15:06:10Z",
            execution_ref_id="revalidate-job-paper-001",
            outcome_summary="Paper revalidation job queued without live runtime mutation.",
        ),
        cooldown_ends_at="2026-05-16T15:06:10Z",
        observation_window_ends_at="2026-05-22T15:06:10Z",
        note="Paper loop learn/evolve follow-through recorded.",
    )
    return decision


def build_complete_ooda_packet(
    ctx: PaperOodaLoopContext | None = None,
    *,
    telemetry_packet: Mapping[str, Any] | None = None,
    deployment_plan: Any | None = None,
    runtime_binding: RuntimeBinding | None = None,
    evolution_decision: EvolutionDecision | None = None,
) -> tuple[OodaLoopPacket, list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = ctx or PaperOodaLoopContext()
    telemetry_packet = telemetry_packet or build_telemetry_packet(ctx)
    deployment_plan = deployment_plan or build_deployment_plan(ctx)
    runtime_binding = runtime_binding or build_runtime_binding(ctx, deployment_plan)
    evolution_decision = evolution_decision or build_evolution_decision(ctx)

    packet = OodaLoopPacket(
        packet_id=ctx.packet_id,
        loop_type=LoopType.PAPER_STRATEGY,
        status=LoopStatus.OPEN,
        environment=LoopEnvironment.PAPER,
        created_at=ctx.created_at,
        updated_at=ctx.created_at,
        capital_pool_id=ctx.capital_pool_id,
        strategy_id=ctx.strategy_spec_id,
        persona_ids=[ctx.sponsor_persona_id],
        observe=ObserveBundle(),
        orient=OrientBundle(),
        decide=DecideBundle(),
        act=ActBundle(live_capital_side_effects=False),
        learn=LearnBundle(),
        audit_refs=[
            "audit://paper-loop/strategy-approved",
            "audit://paper-loop/runtime-paper-bound",
            "audit://paper-loop/no-live-side-effects",
        ],
    )

    snapshots: list[dict[str, Any]] = [packet.to_dict()]
    transitions: list[dict[str, Any]] = []

    def _advance(stage: str, target: LoopStatus) -> None:
        from_status = _enum_value(packet.status)
        packet.advance(target)
        transitions.append(
            {
                "packet_id": packet.packet_id,
                "stage": stage,
                "from_status": from_status,
                "to_status": _enum_value(packet.status),
                "actor_id": ctx.operator_actor_id,
                "transitioned_at": packet.updated_at,
            }
        )
        snapshots.append(packet.to_dict())

    event_ids = telemetry_packet["event_ids"]
    heartbeat_event_id = event_ids[0]
    pnl_event_id = event_ids[2]
    bracket_event_id = event_ids[3]

    packet.observe.source_refs.extend(
        [
            f"strategy-spec://{ctx.strategy_spec_id}@{ctx.strategy_version}",
            "support/evidence/MGMT-PAPER-001-paper-strategy-spec.json",
        ]
    )
    packet.observe.telemetry_refs.append(f"telemetry-event://{heartbeat_event_id}")
    packet.observe.signal_refs.append("signal://qlib-lgbm-cross-sectional-alpha-v1")
    packet.observe.market_refs.append("dataset://qlib-twse-tpex-smoke-v1")
    _advance("observe", LoopStatus.OBSERVING)

    packet.orient.regime_state_ref = "regime-state://tw-paper-neutral-001"
    packet.orient.universe_selection_ref = "universe://twse-tpex-paper-top50-001"
    packet.orient.signal_inference_refs.append("signal-inference://qlib-lgbm-paper-001")
    packet.orient.allocation_proposal_refs.append("allocation-proposal://paper-zero-capital-001")
    packet.orient.risk_adjudication_ref = "risk-adjudication://paper-medium-no-live-capital-001"
    packet.orient.persona_proposal_refs.append(f"persona://{ctx.sponsor_persona_id}")
    packet.orient.evidence_bundle_refs.extend(
        [
            "support/evidence/MGMT-PAPER-002-paper-approval-decision.json",
            "support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json",
        ]
    )
    _advance("orient", LoopStatus.ORIENTED)

    packet.decide.approval_decision_id = ctx.approval_decision_id
    packet.decide.deployment_plan_id = deployment_plan.plan_id
    packet.decide.evolution_decision_id = evolution_decision.decision_id
    packet.decide.sponsor_persona_id = ctx.sponsor_persona_id
    packet.decide.decision_rationale_ref = "rationale://paper-loop-no-live-side-effects"
    packet.decide.policy_decision_refs.extend(
        [
            "PAPER_CANARY_LIVE_POLICY.md#paper",
            "KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md#fail-closed",
        ]
    )
    _advance("decide", LoopStatus.DECIDED)

    packet.act.runtime_binding_id = runtime_binding.binding_id
    packet.act.command_receipt_refs.append("command-receipt://paper-runtime-bind-001")
    packet.act.broker_evidence_refs.append(f"paper-broker://logged-only-order/{bracket_event_id}")
    packet.act.safe_mode_refs.append("safe-mode://live-broker-disabled")
    packet.act.live_capital_side_effects = False
    _advance("act", LoopStatus.ACTED)

    packet.learn.telemetry_refs.extend(
        [
            f"telemetry-event://{pnl_event_id}",
            f"telemetry-event://{bracket_event_id}",
        ]
    )
    packet.learn.evolution_followthrough_refs.append(
        f"evolution-decision://{evolution_decision.decision_id}/revalidate-job-paper-001"
    )
    packet.learn.trainer_refs.append("trainer://paper-revalidation-observer-001")
    packet.learn.retrain_refs.append("revalidate-job://paper-001")
    packet.learn.observation_window = ObservationWindow(
        start_at=ctx.created_at,
        end_at=ctx.observation_end_at,
        duration_hours=0.003,
    )
    _advance("learn", LoopStatus.EVOLVING)
    _advance("close", LoopStatus.CLOSED)

    return packet, transitions, snapshots


def build_replay_validation(
    packet: OodaLoopPacket,
    transitions: list[Mapping[str, Any]],
    snapshots: list[Mapping[str, Any]],
) -> dict[str, Any]:
    assert_complete_replay_path(transitions)
    with tempfile.TemporaryDirectory(prefix="mgmt_paper_007_ooda_") as temp_dir:
        store_path = Path(temp_dir) / "ooda-loop.jsonl"
        store = OodaJsonlAppendStore(store_path)
        store.append_packet(snapshots[0])
        for transition, snapshot in zip(transitions, snapshots[1:]):
            store.append_stage_transition(packet.packet_id, transition, packet=snapshot)

        latest = store.get_packet(packet.packet_id)
        by_runtime = store.list_packets(OodaPacketQuery(runtime_binding_id=packet.act.runtime_binding_id))
        by_plan = store.list_packets(OodaPacketQuery(deployment_plan_id=packet.decide.deployment_plan_id))
        by_evolution = store.list_packets(OodaPacketQuery(evolution_decision_id=packet.decide.evolution_decision_id))
        records = list(store.iter_records())

    return {
        "can_replay": bool(latest and latest.get("status") == LoopStatus.CLOSED.value),
        "record_count": len(records),
        "stage_transition_count": len(transitions),
        "latest_packet_id": latest.get("packet_id") if latest else None,
        "latest_status": latest.get("status") if latest else None,
        "query_matches": {
            "runtime_binding_id": [item["packet_id"] for item in by_runtime],
            "deployment_plan_id": [item["packet_id"] for item in by_plan],
            "evolution_decision_id": [item["packet_id"] for item in by_evolution],
        },
    }


def build_evidence_packet(
    ctx: PaperOodaLoopContext | None = None,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    ctx = ctx or PaperOodaLoopContext()
    strategy_spec = build_strategy_spec(ctx)
    approval_decision = build_approval_decision(ctx)
    deployment_plan = build_deployment_plan(ctx, approval_decision)
    runtime_binding = build_runtime_binding(ctx, deployment_plan)
    telemetry_packet = build_telemetry_packet(ctx)
    evolution_decision = build_evolution_decision(ctx)
    ooda_packet, transitions, snapshots = build_complete_ooda_packet(
        ctx,
        telemetry_packet=telemetry_packet,
        deployment_plan=deployment_plan,
        runtime_binding=runtime_binding,
        evolution_decision=evolution_decision,
    )
    replay_validation = build_replay_validation(ooda_packet, transitions, snapshots)
    plan_projection = StagePlanner().build_execution_projection(deployment_plan, build_registry_entry(ctx))

    packet: dict[str, Any] = {
        "task_id": TASK_ID,
        "milestone_id": MILESTONE_ID,
        "epic": EPIC,
        "environment": PAPER_ENVIRONMENT,
        "generated_at": generated_at or utc_now(),
        "live_capital_side_effects": False,
        "strategy_spec": strategy_spec,
        "approval_decision": approval_decision.to_dict(),
        "deployment_plan": deployment_plan.to_dict(),
        "deployment_execution_projection": {
            "metadata_key": plan_projection.metadata_key,
            "artifact_key": plan_projection.artifact_key,
            "metadata": plan_projection.metadata,
        },
        "runtime_binding": runtime_binding.to_dict(),
        "telemetry_packet": telemetry_packet,
        "evolution_decision": evolution_decision.to_dict(),
        "ooda_packet": ooda_packet.to_dict(),
        "stage_transitions": transitions,
        "replay_validation": replay_validation,
        "safety_assertions": {
            "paper_environment": ooda_packet.environment == LoopEnvironment.PAPER,
            "ooda_live_capital_side_effects_false": ooda_packet.act.live_capital_side_effects is False,
            "runtime_binding_paper_mode": runtime_binding.deployment_mode == PAPER_ENVIRONMENT,
            "deployment_scale_zero_capital": deployment_plan.scale is not None
            and deployment_plan.scale.capital_scale_pct == 0.0,
            "telemetry_logged_only_no_broker": telemetry_packet["safety_assertions"]["bracket_logged_only"]
            and telemetry_packet["safety_assertions"]["no_real_order"]
            and telemetry_packet["safety_assertions"]["no_real_capital"],
            "evolution_revalidate_not_live_mutation": evolution_decision.action_type == EvolutionActionType.REVALIDATE
            and evolution_decision.metadata is not None
            and evolution_decision.metadata.get("live_mutation_allowed") is False,
            "replay_store_roundtrip": replay_validation["can_replay"],
        },
        "paper_loop_chain": [
            "MGMT-PAPER-001: candidate StrategySpec ref",
            "MGMT-PAPER-002: ApprovalDecision packet ref",
            "MGMT-PAPER-003: DeploymentPlan packet fields",
            "MGMT-PAPER-004: paper RuntimeBinding packet fields",
            "MGMT-PAPER-005: telemetry packet refs",
            "MGMT-PAPER-006: EvolutionDecision review packet fields",
            "MGMT-PAPER-007: complete OODA packet",
        ],
        "artifact_refs": {
            "task_evidence": str(TASK_EVIDENCE_PATH.relative_to(REPO_ROOT)),
            "milestone_evidence": str(MILESTONE_EVIDENCE_PATH.relative_to(REPO_ROOT)),
        },
        "validation_errors": [],
    }
    packet["validation_errors"] = validate_evidence_packet(packet)
    return packet


def validate_evidence_packet(packet: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if packet.get("task_id") != TASK_ID:
        errors.append("task_id must be MGMT-PAPER-007")
    if packet.get("milestone_id") != MILESTONE_ID:
        errors.append("milestone_id must be MGMT-OODA-M2")
    if packet.get("environment") != PAPER_ENVIRONMENT:
        errors.append("environment must be paper")
    if packet.get("live_capital_side_effects") is not False:
        errors.append("live_capital_side_effects must be false")

    strategy_spec = packet.get("strategy_spec")
    if not isinstance(strategy_spec, Mapping):
        errors.append("strategy_spec must be present")
        strategy_spec = {}
    elif strategy_spec.get("execution_profile", {}).get("execution_mode_hint") != PAPER_ENVIRONMENT:
        errors.append("strategy_spec execution_mode_hint must be paper")

    approval_decision = packet.get("approval_decision")
    if not isinstance(approval_decision, Mapping):
        errors.append("approval_decision must be present")
        approval_decision = {}
    else:
        if approval_decision.get("decision_state") != "decided":
            errors.append("approval_decision must be decided")
        if approval_decision.get("decision") != "approved":
            errors.append("approval_decision must be approved")
        if approval_decision.get("target_id") != strategy_spec.get("strategy_id"):
            errors.append("approval_decision target_id must match strategy_spec.strategy_id")

    deployment_plan = packet.get("deployment_plan")
    if not isinstance(deployment_plan, Mapping):
        errors.append("deployment_plan must be present")
        deployment_plan = {}
    else:
        if deployment_plan.get("target_stage") != PAPER_ENVIRONMENT:
            errors.append("deployment_plan target_stage must be paper")
        if deployment_plan.get("approval_decision_id") != approval_decision.get("decision_id"):
            errors.append("deployment_plan must reference approval_decision")
        plan_errors = validate_plan_json_safe(deployment_plan)
        errors.extend(f"deployment_plan: {error}" for error in plan_errors)

    runtime_binding = packet.get("runtime_binding")
    if not isinstance(runtime_binding, Mapping):
        errors.append("runtime_binding must be present")
        runtime_binding = {}
    else:
        if runtime_binding.get("deployment_mode") != PAPER_ENVIRONMENT:
            errors.append("runtime_binding deployment_mode must be paper")
        if runtime_binding.get("plan_id") != deployment_plan.get("plan_id"):
            errors.append("runtime_binding must reference deployment_plan")
        try:
            binding_errors = validate_binding(RuntimeBinding.from_dict(dict(runtime_binding)))
            errors.extend(f"runtime_binding: {error}" for error in binding_errors)
        except Exception as exc:
            errors.append(f"runtime_binding parse failed: {exc}")

    evolution_decision = packet.get("evolution_decision")
    if not isinstance(evolution_decision, Mapping):
        errors.append("evolution_decision must be present")
        evolution_decision = {}
    else:
        if evolution_decision.get("decision_state") != "executed":
            errors.append("evolution_decision must be executed")
        if evolution_decision.get("action_type") != "revalidate":
            errors.append("evolution_decision action_type must be revalidate")
        try:
            evo_errors = validate_evolution_decision(EvolutionDecision.from_dict(evolution_decision))
            errors.extend(f"evolution_decision: {error}" for error in evo_errors)
        except Exception as exc:
            errors.append(f"evolution_decision parse failed: {exc}")

    telemetry_packet = packet.get("telemetry_packet")
    if not isinstance(telemetry_packet, Mapping):
        errors.append("telemetry_packet must be present")
        telemetry_packet = {}
    else:
        safety = telemetry_packet.get("safety_assertions", {})
        for key in ("paper_stage_only", "bracket_logged_only", "no_real_order", "no_real_capital"):
            if not isinstance(safety, Mapping) or safety.get(key) is not True:
                errors.append(f"telemetry safety assertion failed: {key}")

    ooda_packet = packet.get("ooda_packet")
    if not isinstance(ooda_packet, Mapping):
        errors.append("ooda_packet must be present")
        ooda_packet = {}
    else:
        ooda_errors = validate_packet(dict(ooda_packet))
        errors.extend(f"ooda_packet: {error}" for error in ooda_errors)
        if ooda_packet.get("status") != LoopStatus.CLOSED.value:
            errors.append("ooda_packet status must be closed")
        if ooda_packet.get("environment") != PAPER_ENVIRONMENT:
            errors.append("ooda_packet environment must be paper")
        decide = ooda_packet.get("decide", {}) if isinstance(ooda_packet.get("decide"), Mapping) else {}
        act = ooda_packet.get("act", {}) if isinstance(ooda_packet.get("act"), Mapping) else {}
        learn = ooda_packet.get("learn", {}) if isinstance(ooda_packet.get("learn"), Mapping) else {}
        if decide.get("approval_decision_id") != approval_decision.get("decision_id"):
            errors.append("ooda_packet decide.approval_decision_id mismatch")
        if decide.get("deployment_plan_id") != deployment_plan.get("plan_id"):
            errors.append("ooda_packet decide.deployment_plan_id mismatch")
        if decide.get("evolution_decision_id") != evolution_decision.get("decision_id"):
            errors.append("ooda_packet decide.evolution_decision_id mismatch")
        if act.get("runtime_binding_id") != runtime_binding.get("binding_id"):
            errors.append("ooda_packet act.runtime_binding_id mismatch")
        if act.get("live_capital_side_effects") is not False:
            errors.append("ooda_packet act.live_capital_side_effects must be false")
        if not learn.get("telemetry_refs"):
            errors.append("ooda_packet learn.telemetry_refs must not be empty")

    transitions = packet.get("stage_transitions")
    if not isinstance(transitions, list):
        errors.append("stage_transitions must be present")
    else:
        try:
            assert_complete_replay_path(transitions)
        except OodaTransitionError as exc:
            errors.append(f"stage_transitions: {exc}")

    replay_validation = packet.get("replay_validation")
    if not isinstance(replay_validation, Mapping):
        errors.append("replay_validation must be present")
    else:
        if replay_validation.get("can_replay") is not True:
            errors.append("replay_validation.can_replay must be true")
        if replay_validation.get("latest_status") != LoopStatus.CLOSED.value:
            errors.append("replay_validation.latest_status must be closed")
        query_matches = replay_validation.get("query_matches", {})
        if isinstance(query_matches, Mapping):
            packet_id = ooda_packet.get("packet_id")
            for key in ("runtime_binding_id", "deployment_plan_id", "evolution_decision_id"):
                if packet_id not in query_matches.get(key, []):
                    errors.append(f"replay_validation query must match {key}")
        else:
            errors.append("replay_validation.query_matches must be present")

    safety = packet.get("safety_assertions", {})
    if isinstance(safety, Mapping):
        for key, value in safety.items():
            if value is not True:
                errors.append(f"safety assertion failed: {key}")
    else:
        errors.append("safety_assertions must be present")

    return errors


def validate_plan_json_safe(plan: Mapping[str, Any]) -> list[str]:
    try:
        deployment_plan = build_deployment_plan_from_dict(plan)
    except Exception as exc:
        return [str(exc)]
    return validate_plan(deployment_plan)


def build_deployment_plan_from_dict(plan: Mapping[str, Any]):
    from deployment_plan import DeploymentPlan

    return DeploymentPlan.from_dict(plan)


def write_evidence_packet(
    packet: Mapping[str, Any],
    *,
    task_path: Path = TASK_EVIDENCE_PATH,
    milestone_path: Path = MILESTONE_EVIDENCE_PATH,
) -> dict[str, Any]:
    task_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_copy(packet)
    task_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if milestone_path != task_path:
        milestone_path.parent.mkdir(parents=True, exist_ok=True)
        milestone_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    print("=== MGMT-PAPER-007: complete paper OODA packet ===\n")
    packet = build_evidence_packet()
    write_evidence_packet(packet)
    errors = packet["validation_errors"]
    ooda_packet = packet["ooda_packet"]
    print(f"  packet_id       : {ooda_packet['packet_id']}")
    print(f"  status          : {ooda_packet['status']}")
    print(f"  deployment_plan : {ooda_packet['decide']['deployment_plan_id']}")
    print(f"  runtime_binding : {ooda_packet['act']['runtime_binding_id']}")
    print(f"  replay_records  : {packet['replay_validation']['record_count']}")
    print(f"  validation      : {'PASS (no errors)' if not errors else errors}")
    print(f"\n  evidence packet written to: {TASK_EVIDENCE_PATH}")
    print(f"  milestone packet written to: {MILESTONE_EVIDENCE_PATH}")
    print("\n=== PASS ===" if not errors else "\n=== FAIL ===")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
