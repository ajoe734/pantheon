"""Unit, contract, and BFF readback tests for historical paper journey cause repair."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BFF_ROOT = REPO_ROOT / "services" / "control-plane"
for path_entry in (REPO_ROOT, BFF_ROOT):
    if str(path_entry) not in sys.path:
        sys.path.insert(0, str(path_entry))

from bff.trade_journeys import (  # noqa: E402
    JourneyProjection,
    _detail,
    _list_row,
    _reconciliation_analysis,
    _timeline_event,
)
import scripts.repair_paper_journey_causes as repair_cli  # noqa: E402
from services.trade_journey.cause_repair import (  # noqa: E402
    BEFORE_IMAGE_FILENAME,
    CURSOR_FILENAME,
    CandidateStageRow,
    InMemoryCauseRepairBackend,
    cleanup_temp_evidence,
    evaluation_storage_record_id,
    run_cause_repair,
    run_cause_rollback,
)
from services.trade_journey.lifecycle_projector import (  # noqa: E402
     main as projector_main,
)


NOW = datetime(2026, 11, 21, 16, 0, 0, tzinfo=timezone.utc)


def _envelope(
    *,
    journey_id: str,
    event_id: str,
    tenant_id: str = "tenant-dev",
    environment: str = "paper",
    producer: str = "execution.paper_runtime",
    event_time: str = "2026-11-19T23:46:17Z",
    causation_event_id: str | None = None,
) -> dict[str, Any]:
    env: dict[str, Any] = {
        "schema_version": "trade-journey-envelope/1",
        "tenant_id": tenant_id,
        "environment": environment,
        "journey_id": journey_id,
        "correlation_id": "49291e64-0e7b-58cc-9f64-0a591a2e2291",
        "trace_id": "a72e45df-044c-5ae4-94be-c45d05ec4c2c",
        "event_id": event_id,
        "producer": producer,
        "event_time": event_time,
        "received_at": event_time,
        "producer_revision": 1,
    }
    if causation_event_id:
        env["causation_event_id"] = causation_event_id
    return env


def _base_telemetry_payload(
    *,
    journey_id: str,
    event_id: str,
    event_type: str,
    sequence_no: int = 4,
    tenant_id: str = "tenant-dev",
    environment: str = "paper",
    created_at: str = "2026-11-19T23:46:17Z",
    producer: str = "execution.paper_runtime",
    causation_event_id: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "created_at": created_at,
        "tenant_id": tenant_id,
        "environment": environment,
        "deployment_stage": environment,
        "execution_mode": "paper",
        "run_id": f"run-{journey_id}",
        "loop_run_id": f"lr-run-{journey_id}",
        "signal_id": f"sig-{journey_id}",
        "strategy_id": "strategy-persona-039ad01f8bcd2b4aa466",
        "runtime_id": "rt-49c1c3d1",
        "binding_id": "rb-7f3c1be55a224fdaa36bb7514e0aa536",
        "capital_pool_id": "pool-persona-paper-039ad01f8bcd2b4aa466",
        "persona_id": "persona-6507750312870d03a241",
        "persona_capital_binding_id": "pcb-persona-paper-039ad01f8bcd2b4aa466",
        "artifact_id": "artifact-persona-pool-039ad01f8bcd2b4aa466-v2",
        "artifact_version": "1",
        "plan_id": "plan-persona-paper-039ad01f8bcd2b4aa466",
        "trace_id": "a72e45df-044c-5ae4-94be-c45d05ec4c2c",
        "authority_refs": {"persona_id": "persona-6507750312870d03a241"},
        "target": {"strategy_id": "strategy-persona-039ad01f8bcd2b4aa466"},
        "correlation_envelope": _envelope(
            journey_id=journey_id,
            event_id=event_id,
            tenant_id=tenant_id,
            environment=environment,
            producer=producer,
            event_time=created_at,
            causation_event_id=causation_event_id,
        ),
        "metadata": {
            "tenant_id": tenant_id,
            "environment": environment,
            "journey_id": journey_id,
            "run_id": f"run-{journey_id}",
            "signal_id": f"sig-{journey_id}",
            "persona_id": "persona-6507750312870d03a241",
            "sequence_no": sequence_no,
            "source_mode": "live",
        },
    }


def _build_2317_buy_refusal_fixture() -> tuple[CandidateStageRow, dict[str, dict[str, Any]]]:
    journey_id = "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd"
    event_id = "91d55953-b575-4fe8-8d39-6e0cade58d59"
    payload = _base_telemetry_payload(
        journey_id=journey_id,
        event_id=event_id,
        event_type="order_rejection",
        sequence_no=4,
    )
    payload.update(
        {
            "symbol": "2317.TWSE",
            "side": "buy",
            "price": 221.0,
            "quantity": 0.0,
        }
    )
    payload["metadata"].update(
        {
            "reason_code": "long_cash",
            "reject_reason": "insufficient_cash",
            "risk_reason": "insufficient_cash",
            "reason": "insufficient_cash",
            "broker_submission_status": "insufficient_cash",
            "required": 100034.3461,
            "available": 100000.0,
            "limit": 100000.0,
            "requested_quantity": 452.0,
            "submitted_to_broker": False,
            "risk_status": "rejected",
            "order_status": "rejected",
            "risk_gate_evaluated": True,
        }
    )
    # Pre-repair projected contract_fields on hosted dev had reason_code="long_cash" and no reason_detail
    stage_row = CandidateStageRow(
        tenant_id="tenant-dev",
        environment="paper",
        journey_id=journey_id,
        source_event_id=event_id,
        stage_name="order_submission",
        stage_status="failed",
        stage_ordinal=1,
        source_ingested_seq=17392241,
        event_sequence=4,
        occurred_at_iso="2026-11-19T23:46:17Z",
        recorded_at_iso="2026-11-19T23:46:17Z",
        contract_fields={
            "event_id": f"{event_id}:order_submission",
            "canonical_event_id": event_id,
            "event_type": "order_rejection",
            "journey_id": journey_id,
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "stage": "order_submission",
            "stage_status": "failed",
            "occurred_at": "2026-11-19T23:46:17Z",
            "recorded_at": "2026-11-19T23:46:17Z",
            "symbol": "2317.TWSE",
            "side": "buy",
            "price": 221.0,
            "quantity": 0.0,
            "reason_code": "long_cash",
            "persona_id": "persona-6507750312870d03a241",
            "binding_id": "rb-7f3c1be55a224fdaa36bb7514e0aa536",
            "correlation_envelope": payload["correlation_envelope"],
        },
        evidence_references=[],
        projection_revision=3,
        fingerprint="fp-2317-buy-refusal",
        journey_status="failed",
        journey_projection_revision=3,
    )
    telemetry = {
        event_id: {
            "event_id": event_id,
            "event_type": "order_rejection",
            "created_at": "2026-11-19T23:46:17Z",
            "payload": payload,
        }
    }
    return stage_row, telemetry


def _build_2317_sell_reconciliation_fixture() -> tuple[
    CandidateStageRow,
    dict[str, dict[str, Any]],
    dict[tuple[str, str], dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    journey_id = "tj_57418ba7-ef37-5015-9b5e-b6423872da89"
    recon_event_id = "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14"
    fill_event_id = "000600fb-6496-47d9-a550-1fa23449c1e2"
    snap_event_id = "8d6761c5-5ae8-5799-87bb-10302186595a"
    eval_id = (
        "rdeval-sched-scheduled-tenant-dev-2026-11-16T-"
        "rb-7f3c1be55a224fdaa36bb-a39cdf74492b52bb"
    )
    next_healthy_eval_id = (
        "rdeval-sched-scheduled-tenant-dev-2026-11-16T-"
        "rb-7f3c1be55a224fdaa36bb-48bb476d7e5e5f59"
    )

    fill_payload = _base_telemetry_payload(
        journey_id=journey_id,
        event_id=fill_event_id,
        event_type="paper_fill_simulated",
        sequence_no=5,
        created_at="2026-11-16T06:52:09Z",
    )
    fill_payload.update(
        {
            "symbol": "2317.TWSE",
            "side": "sell",
            "metrics": {"fill_quantity": -1.0, "fill_price": 218.5},
        }
    )
    fill_payload["metadata"].update(
        {
            "symbol": "2317.TWSE",
            "side": "sell",
            "fill_id": f"paper-fill-{fill_event_id}",
            "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
            "order_id": "paper-order-c2e78e1b-d65b-4a34-b9d8-b4642129cb80",
            "client_order_id": "sig-6946b513a0bb1ce4650793a4f5069b37",
            "submitted_to_broker": True,
        }
    )

    snap_payload = _base_telemetry_payload(
        journey_id=journey_id,
        event_id=snap_event_id,
        event_type="position_snapshot",
        sequence_no=6,
        created_at="2026-11-16T06:52:09Z",
        causation_event_id=fill_event_id,
    )
    snap_payload.update(
        {
            "symbol": "2317.TWSE",
            "side": "sell",
            "position_qty": 0.0,
            "metrics": {
                "position_qty": 0.0,
                "price": 218.5,
                "cash": 100217.5331375,
            },
        }
    )
    snap_payload["metadata"].update(
        {
            "symbol": "2317.TWSE",
            "side": "sell",
            "fill_id": f"paper-fill-{fill_event_id}",
            "source_fill_event_id": fill_event_id,
            "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
            "order_id": "paper-order-c2e78e1b-d65b-4a34-b9d8-b4642129cb80",
            "client_order_id": "sig-6946b513a0bb1ce4650793a4f5069b37",
            "submitted_to_broker": True,
            "ledger_committed": True,
            "causal_parent_id": fill_event_id,
        }
    )

    recon_payload = _base_telemetry_payload(
        journey_id=journey_id,
        event_id=recon_event_id,
        event_type="reconciliation_failed",
        sequence_no=7,
        created_at="2026-11-16T06:58:28Z",
        producer="reconciliation-drift.scheduled",
        causation_event_id=snap_event_id,
    )
    recon_payload.update({"symbol": "2317.TWSE", "side": "sell"})
    recon_payload["metadata"].update(
        {
            "symbol": "2317.TWSE",
            "side": "sell",
            "fill_id": f"paper-fill-{fill_event_id}",
            "source_fill_event_id": fill_event_id,
            "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
            "order_id": "paper-order-c2e78e1b-d65b-4a34-b9d8-b4642129cb80",
            "client_order_id": "sig-6946b513a0bb1ce4650793a4f5069b37",
            "submitted_to_broker": True,
            "ledger_committed": True,
            "causal_parent_id": snap_event_id,
            "reconciliation_status": "warning",
            "reconciliation_evaluation_id": eval_id,
            "reconciliation_tick_id": "scheduled:tenant-dev:2026-11-16T06:55:00Z:PT300S",
        }
    )

    # Pre-repair projected reconciliation stage row on hosted dev lacked reason_code, reason_detail, quantity, and price
    recon_stage_row = CandidateStageRow(
        tenant_id="tenant-dev",
        environment="paper",
        journey_id=journey_id,
        source_event_id=recon_event_id,
        stage_name="reconciliation",
        stage_status="failed",
        stage_ordinal=1,
        source_ingested_seq=17309638,
        event_sequence=7,
        occurred_at_iso="2026-11-16T06:58:28Z",
        recorded_at_iso="2026-11-16T06:58:28Z",
        contract_fields={
            "event_id": f"{recon_event_id}:reconciliation",
            "canonical_event_id": recon_event_id,
            "event_type": "reconciliation_failed",
            "journey_id": journey_id,
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "stage": "reconciliation",
            "stage_status": "failed",
            "occurred_at": "2026-11-16T06:58:28Z",
            "recorded_at": "2026-11-16T06:58:28Z",
            "symbol": "2317.TWSE",
            "side": "sell",
            "fill_id": f"paper-fill-{fill_event_id}",
            "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
            "order_id": "paper-order-c2e78e1b-d65b-4a34-b9d8-b4642129cb80",
            "client_order_id": "sig-6946b513a0bb1ce4650793a4f5069b37",
            "submitted_to_broker": True,
            "persona_id": "persona-6507750312870d03a241",
            "binding_id": "rb-7f3c1be55a224fdaa36bb7514e0aa536",
            "correlation_envelope": recon_payload["correlation_envelope"],
        },
        evidence_references=[],
        projection_revision=6,
        fingerprint="fp-2317-sell-recon",
        journey_status="completed_with_variance",
        journey_projection_revision=6,
    )

    companion_stages = {
        (journey_id, "fill_management"): {
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "journey_id": journey_id,
            "stage_name": "fill_management",
            "source_event_id": fill_event_id,
            "stage_status": "succeeded",
            "contract_fields": {
                "event_id": f"{fill_event_id}:fill_management",
                "canonical_event_id": fill_event_id,
                "event_type": "paper_fill_simulated",
                "stage": "fill_management",
                "stage_status": "succeeded",
                "symbol": "2317.TWSE",
                "side": "sell",
                "quantity": 1.0,
                "price": 218.5,
                "fill_id": f"paper-fill-{fill_event_id}",
                "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
            },
        },
        (journey_id, "ledger_booking"): {
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "journey_id": journey_id,
            "stage_name": "ledger_booking",
            "source_event_id": snap_event_id,
            "stage_status": "succeeded",
            "contract_fields": {
                "event_id": f"{snap_event_id}:ledger_booking",
                "canonical_event_id": snap_event_id,
                "event_type": "position_snapshot",
                "stage": "ledger_booking",
                "stage_status": "succeeded",
                "symbol": "2317.TWSE",
                "side": "sell",
                "quantity": 0.0,  # Post-fill position_qty=0.0 projected on historical ledger_booking row
                "fill_id": f"paper-fill-{fill_event_id}",
                "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
            },
        },
    }

    telemetry = {
        recon_event_id: {
            "event_id": recon_event_id,
            "event_type": "reconciliation_failed",
            "created_at": "2026-11-16T06:58:28Z",
            "payload": recon_payload,
        },
        fill_event_id: {
            "event_id": fill_event_id,
            "event_type": "paper_fill_simulated",
            "created_at": "2026-11-16T06:52:09Z",
            "payload": fill_payload,
        },
        snap_event_id: {
            "event_id": snap_event_id,
            "event_type": "position_snapshot",
            "created_at": "2026-11-16T06:52:09Z",
            "payload": snap_payload,
        },
    }

    evaluations = {
        eval_id: {
            "id": eval_id,
            "evaluation_id": eval_id,
            "tenant_id": "tenant-dev",
            "binding_id": "rb-7f3c1be55a224fdaa36bb7514e0aa536",
            "runtime_id": "rt-49c1c3d1",
            "status": "warning",
            "evaluated_at": "2026-11-16T06:58:28Z",
            "drift_checks": [],
            "reconciliation_checks": [
                {
                    "check": "authoritative_actual_identity",
                    "status": "ok",
                    "detail": "runtime telemetry identity must match active runtime binding",
                    "observed": True,
                    "threshold": True,
                },
                {
                    "check": "runtime_state",
                    "status": "warning",
                    "detail": "observed runtime state must remain active",
                    "observed": "recovering",
                    "threshold": "active",
                },
                {
                    "check": "runtime_health_summary",
                    "status": "warning",
                    "detail": "runtime telemetry health summary must remain ok",
                    "observed": "degraded",
                    "threshold": "ok",
                },
                {
                    "check": "queue_lag_ms",
                    "status": "ok",
                    "detail": "runtime queue lag must stay within threshold",
                    "observed": 168.0,
                    "threshold": 1500.0,
                },
                {
                    "check": "event_delivery_lag_ms",
                    "status": "ok",
                    "detail": "event delivery lag must stay within threshold",
                    "observed": 76.0,
                    "threshold": 1500.0,
                },
                {
                    "check": "actual_metrics_presence",
                    "status": "ok",
                    "detail": "paper execution telemetry must provide financial metrics",
                    "observed": True,
                    "threshold": True,
                },
            ],
        },
        # Next scheduled tick 5 minutes later was runtime-healthy (status="ok")
        next_healthy_eval_id: {
            "id": next_healthy_eval_id,
            "evaluation_id": next_healthy_eval_id,
            "tenant_id": "tenant-dev",
            "binding_id": "rb-7f3c1be55a224fdaa36bb7514e0aa536",
            "runtime_id": "rt-49c1c3d1",
            "status": "ok",
            "evaluated_at": "2026-11-16T07:03:47Z",
            "drift_checks": [],
            "reconciliation_checks": [
                {"check": "authoritative_actual_identity", "status": "ok", "observed": True, "threshold": True},
                {"check": "runtime_state", "status": "ok", "observed": "active", "threshold": "active"},
                {"check": "runtime_health_summary", "status": "ok", "observed": "ok", "threshold": "ok"},
            ],
        },
    }
    return recon_stage_row, telemetry, companion_stages, evaluations


def test_2317_buy_refusal_recovery_and_bff_readback(tmp_path: Path) -> None:
    stage_row, telemetry = _build_2317_buy_refusal_fixture()
    backend = InMemoryCauseRepairBackend(
        stages=[stage_row],
        telemetry_events=telemetry,
    )

    dry_report = run_cause_repair(backend, apply=False, work_dir=tmp_path)
    assert dry_report.mode == "dry_run"
    assert dry_report.scanned_rows == 1
    assert dry_report.recoverable_refusals == 1
    assert dry_report.health_only_evaluations == 0
    assert dry_report.proven_variances == 0
    assert dry_report.irrecoverable_records == 0
    assert dry_report.needs_update_rows == 1
    assert dry_report.updated_rows == 0

    apply_report = run_cause_repair(backend, apply=True, work_dir=tmp_path)
    assert apply_report.mode == "apply"
    assert apply_report.updated_rows == 1
    assert apply_report.before_image_records == 1
    assert apply_report.before_image_bytes > 0

    repaired_stage = backend.stages[
        (
            "tenant-dev",
            "paper",
            "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
            "91d55953-b575-4fe8-8d39-6e0cade58d59",
            "order_submission",
        )
    ]
    cf = repaired_stage.contract_fields
    assert cf["source_event_id"] == "91d55953-b575-4fe8-8d39-6e0cade58d59"
    assert cf["reason_code"] == "insufficient_cash"
    assert cf["expression_reason_code"] == "long_cash"
    assert cf["submitted_to_broker"] is False
    assert cf["order_status"] == "not_submitted"
    assert cf["risk_status"] == "rejected"
    assert cf["risk_gate_evaluated"] is True
    assert cf["reason_detail"] == {
        "required": 100034.3461,
        "available": 100000.0,
        "limit": 100000.0,
        "requested_quantity": 452.0,
    }
    assert "missing_evidence" not in cf

    # Verify BFF list, detail, and timeline readback
    projection = JourneyProjection(
        journey_id="tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
        tenant_id="tenant-dev",
        environment="paper",
        timeline=[
            {
                **cf,
                "event_id": repaired_stage.source_event_id,
                "stage": "order_submission",
                "stage_status": "failed",
                "status": "failed",
                "occurred_at": repaired_stage.occurred_at_iso,
                "recorded_at": repaired_stage.recorded_at_iso,
                "event_sequence": 4,
            }
        ],
        snapshot={
            "journey_id": "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "status": "failed",
            "stages": {
                "signal_generation": {"status": "succeeded", "revision": 1},
                "trade_decision": {"status": "succeeded", "revision": 2},
                "order_submission": {"status": "failed", "revision": 4},
            },
            "revision": repaired_stage.projection_revision,
            "created_at": "2026-11-19T23:46:15Z",
            "updated_at": "2026-11-19T23:46:17Z",
            "identifiers": {},
            "completeness": {"missing_stages": ["risk_evaluation"], "complete": False},
        },
        graph_edges=[],
        diagnostics=[],
    )
    list_row = _list_row(projection, now=NOW)
    assert list_row["quantity"] == 452.0
    assert list_row["filled_quantity"] is None
    assert list_row["current_stage"] == "risk_evaluation"
    assert list_row["outcome"] == {
        "kind": "rejected",
        "rejected_stage": "risk_evaluation",
        "reason_code": "insufficient_cash",
        "reason_detail": {
            "required": 100034.3461,
            "available": 100000.0,
            "limit": 100000.0,
            "requested_quantity": 452.0,
        },
        "expression_reason_code": "long_cash",
        "submitted_to_broker": False,
        "order_status": "not_submitted",
        "source_event_id": "91d55953-b575-4fe8-8d39-6e0cade58d59",
    }
    detail_row = _detail(projection, now=NOW)
    assert detail_row["stages"]["risk_evaluation"]["status"] == "rejected"
    assert detail_row["stages"]["risk_evaluation"]["reason_code"] == "insufficient_cash"
    assert detail_row["stages"]["order_submission"]["status"] == "not_submitted"
    assert detail_row["stages"]["order_submission"]["submitted_to_broker"] is False
    timeline_row = _timeline_event(projection.timeline[0])
    assert timeline_row["stage"] == "risk_evaluation"
    assert timeline_row["reason_code"] == "insufficient_cash"
    assert timeline_row["submitted_to_broker"] is False


def test_2317_sell_restores_fill_and_health_warning_as_unverified_accounting(
    tmp_path: Path,
) -> None:
    recon_stage, telemetry, companions, evaluations = (
        _build_2317_sell_reconciliation_fixture()
    )
    backend = InMemoryCauseRepairBackend(
        stages=[recon_stage],
        telemetry_events=telemetry,
        companion_stages=companions,
        drift_evaluations=evaluations,
    )

    dry_report = run_cause_repair(backend, apply=False, work_dir=tmp_path)
    assert dry_report.scanned_rows == 1
    assert dry_report.health_only_evaluations == 1
    assert dry_report.recoverable_refusals == 0
    assert dry_report.proven_variances == 0
    assert dry_report.irrecoverable_records == 0
    assert dry_report.needs_update_rows == 1

    apply_report = run_cause_repair(backend, apply=True, work_dir=tmp_path)
    assert apply_report.updated_rows == 1

    repaired_stage = backend.stages[
        (
            "tenant-dev",
            "paper",
            "tj_57418ba7-ef37-5015-9b5e-b6423872da89",
            "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14",
            "reconciliation",
        )
    ]
    cf = repaired_stage.contract_fields
    assert cf["source_event_id"] == "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14"
    assert cf["source_fill_event_id"] == "000600fb-6496-47d9-a550-1fa23449c1e2"
    assert cf["snapshot_event_id"] == "8d6761c5-5ae8-5799-87bb-10302186595a"
    assert (
        cf["reconciliation_evaluation_id"]
        == "rdeval-sched-scheduled-tenant-dev-2026-11-16T-rb-7f3c1be55a224fdaa36bb-a39cdf74492b52bb"
    )
    assert cf["quantity"] == 1.0
    assert cf["fill_quantity"] == 1.0
    assert cf["price"] == 218.5
    assert cf["fill_price"] == 218.5
    assert cf["reason_code"] == "reconciliation_evidence_incomplete"
    checks_by_name = {item["check"]: item for item in cf["reason_detail"]}
    assert checks_by_name["runtime_state"]["status"] == "warning"
    assert checks_by_name["runtime_state"]["observed"] == "recovering"
    assert checks_by_name["runtime_health_summary"]["status"] == "warning"
    assert checks_by_name["runtime_health_summary"]["observed"] == "degraded"
    assert checks_by_name["position_change"]["status"] == "unverified"
    assert checks_by_name["position_change"]["expected"] == -1.0
    assert checks_by_name["position_change"]["actual"] is None
    assert checks_by_name["cash_change"]["status"] == "unverified"

    # Build projection in the exact reverse stage_ordinal order used by page_journeys
    # (reconciliation -> ledger_booking(quantity=0.0) -> fill_management(quantity=1.0))
    timeline_desc = [
        {
            **cf,
            "event_id": repaired_stage.source_event_id,
            "stage": "reconciliation",
            "stage_status": "failed",
            "status": "failed",
            "occurred_at": repaired_stage.occurred_at_iso,
            "recorded_at": repaired_stage.recorded_at_iso,
            "event_sequence": 7,
        },
        {
            **companions[("tj_57418ba7-ef37-5015-9b5e-b6423872da89", "ledger_booking")][
                "contract_fields"
            ],
            "event_id": "8d6761c5-5ae8-5799-87bb-10302186595a",
            "stage": "ledger_booking",
            "stage_status": "succeeded",
            "status": "succeeded",
            "occurred_at": "2026-11-16T06:52:09Z",
            "recorded_at": "2026-11-16T06:52:09Z",
            "event_sequence": 6,
        },
        {
            **companions[("tj_57418ba7-ef37-5015-9b5e-b6423872da89", "fill_management")][
                "contract_fields"
            ],
            "event_id": "000600fb-6496-47d9-a550-1fa23449c1e2",
            "stage": "fill_management",
            "stage_status": "succeeded",
            "status": "succeeded",
            "occurred_at": "2026-11-16T06:52:09Z",
            "recorded_at": "2026-11-16T06:52:09Z",
            "event_sequence": 5,
        },
    ]
    projection = JourneyProjection(
        journey_id="tj_57418ba7-ef37-5015-9b5e-b6423872da89",
        tenant_id="tenant-dev",
        environment="paper",
        timeline=timeline_desc,
        snapshot={
            "journey_id": "tj_57418ba7-ef37-5015-9b5e-b6423872da89",
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "status": "completed_with_variance",
            "stages": {
                "signal_generation": {"status": "succeeded", "revision": 1},
                "trade_decision": {"status": "succeeded", "revision": 2},
                "order_submission": {"status": "succeeded", "revision": 3},
                "broker_acknowledgement": {"status": "succeeded", "revision": 4},
                "fill_management": {"status": "succeeded", "revision": 5},
                "ledger_booking": {"status": "succeeded", "revision": 6},
                "reconciliation": {"status": "failed", "revision": 7},
            },
            "revision": repaired_stage.projection_revision,
            "created_at": "2026-11-16T06:52:08Z",
            "updated_at": "2026-11-16T06:58:28Z",
            "identifiers": {},
            "completeness": {"missing_stages": [], "complete": True},
        },
        graph_edges=[],
        diagnostics=[],
    )
    list_row = _list_row(projection, now=NOW)
    assert list_row["quantity"] == 1.0
    assert list_row["filled_quantity"] == 1.0
    assert list_row["price"] == 218.5
    outcome = list_row["outcome"]
    assert outcome["kind"] == "variance"
    assert outcome["classification"] == "unverified_evidence"
    assert outcome["accounting_variance"] is False
    assert outcome["comparison_completed"] is False
    assert outcome["reason_code"] == "reconciliation_evidence_incomplete"
    assert outcome["discrepancies"] == []
    assert outcome["source_event_id"] == "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14"
    assert "position_quantity_before" in outcome["missing_evidence"]
    assert "cash_before" in outcome["missing_evidence"]
    assert "fee" in outcome["missing_evidence"]
    assert "tax" in outcome["missing_evidence"]


def test_generalized_classifications_preserve_proven_variances_and_irrecoverable_unknowns(
    tmp_path: Path,
) -> None:
    buy_stage, buy_tel = _build_2317_buy_refusal_fixture()
    sell_stage, sell_tel, sell_comp, sell_evals = (
        _build_2317_sell_reconciliation_fixture()
    )

    # 3. Proven accounting variance (complete booking evidence with -45.0 cash discrepancy)
    var_jid = "tj_proven-variance-001"
    var_eid = "evt-recon-variance-001"
    var_fill_eid = "evt-fill-variance-001"
    var_snap_eid = "evt-snap-variance-001"
    var_eval_id = "rdeval-sched-proven-variance-001"

    var_fill_pay = _base_telemetry_payload(
        journey_id=var_jid,
        event_id=var_fill_eid,
        event_type="paper_fill_simulated",
        sequence_no=5,
    )
    var_fill_pay["metrics"] = {"fill_quantity": 1.0, "fill_price": 218.5}
    var_fill_pay["metadata"].update(
        {
            "side": "sell",
            "symbol": "2317.TWSE",
            "instrument_id": "2317.TWSE",
            "kind": "cash_share",
            "multiplier": 1.0,
            "fill_quantity": 1.0,
            "fill_price": 218.5,
            "fee": 0.3113625,
            "tax": 0.6555,
            "position_quantity_before": 1.0,
            "position_quantity_after": 0.0,
            "cash_before": 100000.0,
            "cash_after": 100172.5331375,  # Expected 100217.5331375 -> delta -45.0
            "broker_order_id": "ord-proven-001",
            "submitted_to_broker": True,
        }
    )
    var_snap_pay = _base_telemetry_payload(
        journey_id=var_jid,
        event_id=var_snap_eid,
        event_type="position_snapshot",
        sequence_no=6,
        causation_event_id=var_fill_eid,
    )
    var_snap_pay["metrics"] = {"position_qty": 0.0, "cash": 100172.5331375}
    var_snap_pay["metadata"].update(var_fill_pay["metadata"])

    var_recon_pay = _base_telemetry_payload(
        journey_id=var_jid,
        event_id=var_eid,
        event_type="reconciliation_failed",
        sequence_no=7,
        causation_event_id=var_snap_eid,
    )
    var_recon_pay["metadata"].update(
        {
            "source_fill_event_id": var_fill_eid,
            "causal_parent_id": var_snap_eid,
            "reconciliation_evaluation_id": var_eval_id,
            "broker_order_id": "ord-proven-001",
        }
    )
    var_stage = CandidateStageRow(
        tenant_id="tenant-dev",
        environment="paper",
        journey_id=var_jid,
        source_event_id=var_eid,
        stage_name="reconciliation",
        stage_status="failed",
        stage_ordinal=1,
        source_ingested_seq=18000001,
        event_sequence=7,
        occurred_at_iso="2026-11-20T01:00:00Z",
        recorded_at_iso="2026-11-20T01:00:00Z",
        contract_fields={
            "event_id": f"{var_eid}:reconciliation",
            "canonical_event_id": var_eid,
            "event_type": "reconciliation_failed",
            "journey_id": var_jid,
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "stage": "reconciliation",
            "stage_status": "failed",
        },
        evidence_references=[],
        projection_revision=1,
        fingerprint="fp-var-001",
        journey_status="completed_with_variance",
        journey_projection_revision=1,
    )

    # 4. Irrecoverable reconciliation row (2026-10-07 row whose evaluation is missing from drift_evaluations)
    irr_jid = "tj_irrecoverable-20261007-001"
    irr_eid = "evt-recon-irr-001"
    irr_recon_pay = _base_telemetry_payload(
        journey_id=irr_jid,
        event_id=irr_eid,
        event_type="reconciliation_failed",
        sequence_no=7,
        created_at="2026-10-07T08:00:00Z",
    )
    irr_recon_pay["metadata"].update(
        {
            "reconciliation_evaluation_id": "rdeval-sched-missing-2026-10-07",
        }
    )
    irr_stage = CandidateStageRow(
        tenant_id="tenant-dev",
        environment="paper",
        journey_id=irr_jid,
        source_event_id=irr_eid,
        stage_name="reconciliation",
        stage_status="failed",
        stage_ordinal=1,
        source_ingested_seq=10000001,
        event_sequence=7,
        occurred_at_iso="2026-10-07T08:00:00Z",
        recorded_at_iso="2026-10-07T08:00:00Z",
        contract_fields={
            "event_id": f"{irr_eid}:reconciliation",
            "canonical_event_id": irr_eid,
            "event_type": "reconciliation_failed",
            "journey_id": irr_jid,
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "stage": "reconciliation",
            "stage_status": "failed",
        },
        evidence_references=[],
        projection_revision=1,
        fingerprint="fp-irr-001",
        journey_status="completed_with_variance",
        journey_projection_revision=1,
    )

    telemetry = {
        **buy_tel,
        **sell_tel,
        var_fill_eid: {
            "event_id": var_fill_eid,
            "event_type": "paper_fill_simulated",
            "created_at": "2026-11-20T00:59:00Z",
            "payload": var_fill_pay,
        },
        var_snap_eid: {
            "event_id": var_snap_eid,
            "event_type": "position_snapshot",
            "created_at": "2026-11-20T00:59:01Z",
            "payload": var_snap_pay,
        },
        var_eid: {
            "event_id": var_eid,
            "event_type": "reconciliation_failed",
            "created_at": "2026-11-20T01:00:00Z",
            "payload": var_recon_pay,
        },
        irr_eid: {
            "event_id": irr_eid,
            "event_type": "reconciliation_failed",
            "created_at": "2026-10-07T08:00:00Z",
            "payload": irr_recon_pay,
        },
    }
    evaluations = {
        **sell_evals,
        var_eval_id: {
            "id": var_eval_id,
            "evaluation_id": var_eval_id,
            "tenant_id": "tenant-dev",
            "binding_id": "rb-7f3c1be55a224fdaa36bb7514e0aa536",
            "status": "ok",
            "reconciliation_checks": [
                {"check": "runtime_state", "status": "ok", "observed": "active", "threshold": "active"}
            ],
        },
    }
    backend = InMemoryCauseRepairBackend(
        stages=[buy_stage, sell_stage, var_stage, irr_stage],
        telemetry_events=telemetry,
        companion_stages=sell_comp,
        drift_evaluations=evaluations,
    )

    report = run_cause_repair(backend, apply=True, work_dir=tmp_path)
    assert report.scanned_rows == 4
    assert report.recoverable_refusals == 1
    assert report.health_only_evaluations == 1
    assert report.proven_variances == 1
    assert report.irrecoverable_records == 1
    assert report.updated_rows == 4

    # Verify proven variance BFF analysis
    var_cf = backend.stages[
        ("tenant-dev", "paper", var_jid, var_eid, "reconciliation")
    ].contract_fields
    assert var_cf["reason_code"] == "cash_variance"
    var_analysis = _reconciliation_analysis(var_cf)
    assert var_analysis["classification"] == "accounting_variance"
    assert var_analysis["accounting_variance"] is True
    assert var_analysis["comparison_completed"] is True
    assert len(var_analysis["discrepancies"]) == 1
    assert var_analysis["discrepancies"][0]["check"] == "cash_change"
    assert var_analysis["discrepancies"][0]["delta"] == pytest.approx(-45.0)
    assert var_analysis["discrepancies"][0]["difference"] == pytest.approx(-45.0)

    # Verify irrecoverable record BFF analysis
    irr_cf = backend.stages[
        ("tenant-dev", "paper", irr_jid, irr_eid, "reconciliation")
    ].contract_fields
    assert irr_cf["reason_code"] == "reason_not_recorded"
    assert "reconciliation_evaluation" in irr_cf["missing_evidence"]
    irr_analysis = _reconciliation_analysis(irr_cf)
    assert irr_analysis["classification"] == "unverified_evidence"
    assert irr_analysis["accounting_variance"] is False
    assert irr_analysis["comparison_completed"] is False
    assert irr_analysis["reason_code"] == "reason_not_recorded"


def test_repeat_run_idempotency_concurrent_revision_protection_and_rollback(
    tmp_path: Path,
) -> None:
    buy_stage, buy_tel = _build_2317_buy_refusal_fixture()
    sell_stage, sell_tel, sell_comp, sell_evals = (
        _build_2317_sell_reconciliation_fixture()
    )
    backend = InMemoryCauseRepairBackend(
        stages=[buy_stage, sell_stage],
        telemetry_events={**buy_tel, **sell_tel},
        companion_stages=sell_comp,
        drift_evaluations=sell_evals,
    )

    # 1. Bounded batch apply (batch_size=1, max_batches=1 -> processes 1 row and saves cursor)
    batch1 = run_cause_repair(
        backend,
        apply=True,
        batch_size=1,
        max_batches=1,
        work_dir=tmp_path,
    )
    assert batch1.batches_processed == 1
    assert batch1.scanned_rows == 1
    assert batch1.updated_rows == 1
    assert batch1.has_more is True

    # 2. Resume processes the second row
    batch2 = run_cause_repair(
        backend,
        apply=True,
        batch_size=1,
        max_batches=5,
        resume=True,
        work_dir=tmp_path,
    )
    assert batch2.scanned_rows == 1
    assert batch2.updated_rows == 1
    assert batch2.has_more is False
    assert batch2.before_image_records == 2

    # 3. Repeat run changes ZERO already repaired rows
    repeat_report = run_cause_repair(
        backend,
        apply=True,
        batch_size=10,
        max_batches=5,
        work_dir=tmp_path,
    )
    assert repeat_report.scanned_rows == 2
    assert repeat_report.already_repaired_rows == 2
    assert repeat_report.needs_update_rows == 0
    assert repeat_report.updated_rows == 0
    assert repeat_report.before_image_records == 2  # No duplicate before-image rows appended

    # 4. Simulate a concurrent live projector revision bump on the 2317 buy journey
    # Rollback should protect the newly updated journey revision and only roll back the 2317 sell row
    backend.journey_revisions[
        ("tenant-dev", "paper", "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd")
    ] = 99
    before_image_file = tmp_path / BEFORE_IMAGE_FILENAME
    rb_report = run_cause_rollback(
        backend,
        before_image_path=before_image_file,
    )
    assert rb_report.scanned_rows == 2
    assert rb_report.rolled_back_rows == 1
    assert rb_report.skipped_concurrent_revision == 1

    # The 2317 sell row was rolled back to its original pre-repair state (revision 6)
    rolled_back_sell = backend.stages[
        (
            "tenant-dev",
            "paper",
            "tj_57418ba7-ef37-5015-9b5e-b6423872da89",
            "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14",
            "reconciliation",
        )
    ]
    assert rolled_back_sell.projection_revision == 6
    assert "reason_code" not in rolled_back_sell.contract_fields

    # The concurrently updated 2317 buy row was protected and kept its repaired fields
    protected_buy = backend.stages[
        (
            "tenant-dev",
            "paper",
            "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
            "91d55953-b575-4fe8-8d39-6e0cade58d59",
            "order_submission",
        )
    ]
    assert protected_buy.contract_fields["reason_code"] == "insufficient_cash"

    # 5. Concurrent revision bump during apply skips the concurrently mutated row
    def _bump_sell_on_apply(candidate: CandidateStageRow) -> None:
        if candidate.journey_id == "tj_57418ba7-ef37-5015-9b5e-b6423872da89":
            backend.journey_revisions[
                (candidate.tenant_id, candidate.environment, candidate.journey_id)
            ] += 1

    backend.on_before_apply = _bump_sell_on_apply
    concurrent_apply = run_cause_repair(
        backend,
        apply=True,
        work_dir=tmp_path,
    )
    assert concurrent_apply.needs_update_rows == 1
    assert concurrent_apply.updated_rows == 0
    assert concurrent_apply.skipped_concurrent_revision == 1

    # 6. Temporary evidence cleanup removes before-image and cursor files
    cleaned, freed = cleanup_temp_evidence(tmp_path, remove_all=True)
    assert cleaned == 2
    assert freed > 0
    assert not (tmp_path / BEFORE_IMAGE_FILENAME).exists()
    assert not (tmp_path / CURSOR_FILENAME).exists()


def test_cross_tenant_and_environment_join_isolation(tmp_path: Path) -> None:
    stage_row, telemetry = _build_2317_buy_refusal_fixture()
    # Tamper the source telemetry row so its tenant_id is tenant-other
    wrong_tenant_telemetry = json.loads(json.dumps(telemetry))
    eid = "91d55953-b575-4fe8-8d39-6e0cade58d59"
    wrong_tenant_telemetry[eid]["payload"]["tenant_id"] = "tenant-other"
    wrong_tenant_telemetry[eid]["payload"]["correlation_envelope"]["tenant_id"] = (
        "tenant-other"
    )
    wrong_tenant_telemetry[eid]["payload"]["metadata"]["tenant_id"] = "tenant-other"

    backend = InMemoryCauseRepairBackend(
        stages=[stage_row],
        telemetry_events=wrong_tenant_telemetry,
    )
    report = run_cause_repair(backend, apply=True, work_dir=tmp_path)
    assert report.recoverable_refusals == 0
    assert report.irrecoverable_records == 1
    repaired = backend.stages[
        (
            "tenant-dev",
            "paper",
            "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
            eid,
            "order_submission",
        )
    ].contract_fields
    assert repaired["reason_code"] == "reason_not_recorded"
    assert repaired["expression_reason_code"] == "long_cash"
    assert "source_telemetry_event" in repaired["missing_evidence"]


def test_cli_and_projector_subcommand_cleanup_temp(tmp_path: Path) -> None:
    dummy_before = tmp_path / BEFORE_IMAGE_FILENAME
    dummy_before.write_text('{"test": 1}\n', encoding="utf-8")
    out_file = tmp_path / "cleanup-report.json"

    rc = repair_cli.main(
        [
            "--work-dir",
            str(tmp_path),
            "--cleanup-temp",
            "--output",
            str(out_file),
        ]
    )
    assert rc == 0
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["mode"] == "cleanup_temp"
    assert payload["temp_files_cleaned"] == 1
    assert not dummy_before.exists()

    # Also test invocation via lifecycle_projector repair-causes subcommand
    dummy_cursor = tmp_path / CURSOR_FILENAME
    dummy_cursor.write_text('{"cursor": 1}\n', encoding="utf-8")
    rc2 = projector_main(
        [
            "repair-causes",
            "--work-dir",
            str(tmp_path),
            "--cleanup-temp",
        ]
    )
    assert rc2 == 0
    assert not dummy_cursor.exists()


def test_sell_without_holdings_broker_error_and_omitted_reason_refusals(
    tmp_path: Path,
) -> None:
    # 1. Pre-broker sell_without_holdings rejection
    no_hold_jid = "tj_sell-without-holdings-001"
    no_hold_eid = "evt-no-holdings-001"
    no_hold_pay = _base_telemetry_payload(
        journey_id=no_hold_jid,
        event_id=no_hold_eid,
        event_type="order_rejection",
        sequence_no=4,
    )
    no_hold_pay.update({"symbol": "2330.TWSE", "side": "sell", "quantity": 0.0})
    no_hold_pay["metadata"].update(
        {
            "reason": "sell_without_holdings",
            "reject_reason": "sell_without_holdings",
            "holding_quantity": 0.0,
            "requested_quantity": 1.0,
            "submitted_to_broker": False,
        }
    )
    no_hold_stage = CandidateStageRow(
        tenant_id="tenant-dev",
        environment="paper",
        journey_id=no_hold_jid,
        source_event_id=no_hold_eid,
        stage_name="order_submission",
        stage_status="failed",
        stage_ordinal=1,
        source_ingested_seq=101,
        event_sequence=4,
        occurred_at_iso="2026-11-16T05:00:00Z",
        recorded_at_iso="2026-11-16T05:00:00Z",
        contract_fields={
            "event_id": f"{no_hold_eid}:order_submission",
            "canonical_event_id": no_hold_eid,
            "event_type": "order_rejection",
            "journey_id": no_hold_jid,
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "stage": "order_submission",
            "stage_status": "failed",
            "symbol": "2330.TWSE",
            "side": "sell",
            "quantity": 0.0,
            "reason_code": "sell_without_holdings",
        },
        evidence_references=[],
        projection_revision=1,
        fingerprint="fp-no-hold",
        journey_status="failed",
        journey_projection_revision=1,
    )

    # 2. Post-submission taiwan_broker_error rejection
    brk_err_jid = "tj_taiwan-broker-error-001"
    brk_err_eid = "evt-broker-error-001"
    brk_err_pay = _base_telemetry_payload(
        journey_id=brk_err_jid,
        event_id=brk_err_eid,
        event_type="order_rejection",
        sequence_no=4,
    )
    brk_err_pay.update({"symbol": "2330.TW", "side": "sell", "quantity": 0.0})
    brk_err_pay["metadata"].update(
        {
            "broker_submission_status": "taiwan_broker_error",
            "execution_error_message": "<urlopen error [Errno -3] Temporary failure in name resolution>",
            "requested_quantity": 1.0,
            "submitted_to_broker": True,
        }
    )
    brk_err_stage = CandidateStageRow(
        tenant_id="tenant-dev",
        environment="paper",
        journey_id=brk_err_jid,
        source_event_id=brk_err_eid,
        stage_name="order_submission",
        stage_status="failed",
        stage_ordinal=1,
        source_ingested_seq=102,
        event_sequence=4,
        occurred_at_iso="2026-10-10T18:26:14Z",
        recorded_at_iso="2026-10-10T18:26:14Z",
        contract_fields={
            "event_id": f"{brk_err_eid}:order_submission",
            "canonical_event_id": brk_err_eid,
            "event_type": "order_rejection",
            "journey_id": brk_err_jid,
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "stage": "order_submission",
            "stage_status": "failed",
            "symbol": "2330.TW",
            "side": "sell",
            "quantity": 0.0,
        },
        evidence_references=[],
        projection_revision=1,
        fingerprint="fp-brk-err",
        journey_status="failed",
        journey_projection_revision=1,
    )

    # 3. Irrecoverable rejection (only expression code long_cash in source event)
    omitted_jid = "tj_omitted-reject-reason-001"
    omitted_eid = "evt-omitted-reject-001"
    omitted_pay = _base_telemetry_payload(
        journey_id=omitted_jid,
        event_id=omitted_eid,
        event_type="order_rejection",
        sequence_no=4,
    )
    omitted_pay.update({"symbol": "2317.TWSE", "side": "buy", "quantity": 0.0})
    omitted_pay["metadata"].update(
        {
            "reason_code": "long_cash",
            "submitted_to_broker": False,
            "requested_quantity": 10.0,
        }
    )
    omitted_stage = CandidateStageRow(
        tenant_id="tenant-dev",
        environment="paper",
        journey_id=omitted_jid,
        source_event_id=omitted_eid,
        stage_name="order_submission",
        stage_status="failed",
        stage_ordinal=1,
        source_ingested_seq=103,
        event_sequence=4,
        occurred_at_iso="2026-11-19T00:00:00Z",
        recorded_at_iso="2026-11-19T00:00:00Z",
        contract_fields={
            "event_id": f"{omitted_eid}:order_submission",
            "canonical_event_id": omitted_eid,
            "event_type": "order_rejection",
            "journey_id": omitted_jid,
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "stage": "order_submission",
            "stage_status": "failed",
            "reason_code": "long_cash",
        },
        evidence_references=[],
        projection_revision=1,
        fingerprint="fp-omitted",
        journey_status="failed",
        journey_projection_revision=1,
    )

    backend = InMemoryCauseRepairBackend(
        stages=[no_hold_stage, brk_err_stage, omitted_stage],
        telemetry_events={
            no_hold_eid: {
                "event_id": no_hold_eid,
                "event_type": "order_rejection",
                "created_at": "2026-11-16T05:00:00Z",
                "payload": no_hold_pay,
            },
            brk_err_eid: {
                "event_id": brk_err_eid,
                "event_type": "order_rejection",
                "created_at": "2026-10-10T18:26:14Z",
                "payload": brk_err_pay,
            },
            omitted_eid: {
                "event_id": omitted_eid,
                "event_type": "order_rejection",
                "created_at": "2026-11-19T00:00:00Z",
                "payload": omitted_pay,
            },
        },
    )
    report = run_cause_repair(backend, apply=True, work_dir=tmp_path)
    assert report.recoverable_refusals == 2
    assert report.irrecoverable_records == 1
    assert report.updated_rows == 3

    no_hold_cf = backend.stages[
        ("tenant-dev", "paper", no_hold_jid, no_hold_eid, "order_submission")
    ].contract_fields
    assert no_hold_cf["reason_code"] == "sell_without_holdings"
    assert no_hold_cf["reason_detail"] == {
        "holding_quantity": 0.0,
        "requested_quantity": 1.0,
    }
    assert no_hold_cf["submitted_to_broker"] is False
    assert no_hold_cf["order_status"] == "not_submitted"

    brk_cf = backend.stages[
        ("tenant-dev", "paper", brk_err_jid, brk_err_eid, "order_submission")
    ].contract_fields
    assert brk_cf["reason_code"] == "taiwan_broker_error"
    assert brk_cf["submitted_to_broker"] is True

    omitted_cf = backend.stages[
        ("tenant-dev", "paper", omitted_jid, omitted_eid, "order_submission")
    ].contract_fields
    assert omitted_cf["reason_code"] == "reason_not_recorded"
    assert omitted_cf["expression_reason_code"] == "long_cash"
    assert omitted_cf["missing_evidence"] == ["reject_reason"]


def test_complete_trade_specific_evidence_with_broker_orders_and_source_immutability(
    tmp_path: Path,
) -> None:
    recon_stage, telemetry, companions, evaluations = (
        _build_2317_sell_reconciliation_fixture()
    )
    # Add complete trade-specific accounting evidence to snapshot metadata
    snap_eid = "8d6761c5-5ae8-5799-87bb-10302186595a"
    telemetry[snap_eid]["payload"]["metadata"].update(
        {
            "instrument_id": "eq:2317",
            "kind": "cash_share",
            "multiplier": 1.0,
            "fill_quantity": 1.0,
            "fill_price": 218.5,
            "fee": 0.3113625,
            "tax": 0.6555,
            "position_quantity_before": 1.0,
            "position_quantity_after": 0.0,
            "cash_before": 100000.0,
            "cash_after": 100217.5331375,
        }
    )
    broker_orders = {
        "b00575cfc62f44d18ed052dad34d23ea": {
            "order_id": "b00575cfc62f44d18ed052dad34d23ea",
            "instrument_id": "eq:2317",
            "side": "sell",
            "status": "filled",
            "fill_qty": 1.0,
            "fill_price": 218.5,
            "correlation_envelope": {
                "tenant_id": "tenant-dev",
                "environment": "paper",
                "journey_id": "tj_57418ba7-ef37-5015-9b5e-b6423872da89",
            },
        }
    }
    telemetry_before = json.loads(json.dumps(telemetry))
    evals_before = json.loads(json.dumps(evaluations))
    companions_before = {
        k: json.loads(json.dumps(v)) for k, v in companions.items()
    }
    orders_before = json.loads(json.dumps(broker_orders))

    backend = InMemoryCauseRepairBackend(
        stages=[recon_stage],
        telemetry_events=telemetry,
        companion_stages=companions,
        drift_evaluations=evaluations,
    )
    report = run_cause_repair(
        backend,
        apply=True,
        work_dir=tmp_path,
        broker_orders=broker_orders,
    )
    assert report.health_only_evaluations == 1
    assert report.updated_rows == 1
    cf = backend.stages[
        (
            "tenant-dev",
            "paper",
            "tj_57418ba7-ef37-5015-9b5e-b6423872da89",
            "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14",
            "reconciliation",
        )
    ].contract_fields
    # Because every trade-specific check passed, only the evaluation's runtime health warning remains
    assert cf["reason_code"] == "runtime_unhealthy_at_reconciliation"
    assert "missing_evidence" not in cf
    checks = {c["check"]: c for c in cf["reason_detail"]}
    for name in (
        "broker_fill_qty",
        "broker_fill_price",
        "broker_status",
        "broker_side",
        "broker_instrument_id",
        "position_change",
        "committed_snapshot_position",
        "cash_change",
        "committed_snapshot_cash",
    ):
        assert checks[name]["status"] == "passed"

    # Immutable sources, evaluations, companion stages, and broker orders are completely untouched
    assert backend.telemetry_events == telemetry_before
    assert companions == companions_before
    assert broker_orders == orders_before
    for eval_id, orig_eval in evals_before.items():
        assert backend.drift_evaluations[eval_id] == orig_eval
        assert (
            backend.drift_evaluations[
                evaluation_storage_record_id("tenant-dev", eval_id)
            ]
            == orig_eval
        )


def test_before_image_caps_and_retention_cleanup(tmp_path: Path) -> None:
    stage_row, telemetry = _build_2317_buy_refusal_fixture()
    backend = InMemoryCauseRepairBackend(
        stages=[stage_row],
        telemetry_events=telemetry,
    )
    with pytest.raises(RuntimeError, match="Before-image byte cap"):
        run_cause_repair(
            backend,
            apply=True,
            work_dir=tmp_path,
            max_before_image_bytes=10,
        )
    # Stage row was not updated because before-image write failed closed first
    assert (
        backend.stages[
            (
                "tenant-dev",
                "paper",
                "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
                "91d55953-b575-4fe8-8d39-6e0cade58d59",
                "order_submission",
            )
        ].projection_revision
        == 3
    )


def test_dry_run_then_apply_resume_and_cursor_scope_transitions(
    tmp_path: Path,
) -> None:
    # 1. Exact documented procedure: dry-run followed by apply --resume in the same work_dir
    buy_stage, buy_tel = _build_2317_buy_refusal_fixture()
    backend = InMemoryCauseRepairBackend(
        stages=[buy_stage],
        telemetry_events=buy_tel,
    )

    dry_report = run_cause_repair(backend, apply=False, work_dir=tmp_path)
    assert dry_report.needs_update_rows == 1
    assert dry_report.updated_rows == 0

    apply_report = run_cause_repair(
        backend,
        apply=True,
        resume=True,
        work_dir=tmp_path,
    )
    assert apply_report.scanned_rows == 1
    assert apply_report.updated_rows == 1
    repaired_buy = backend.stages[
        (
            "tenant-dev",
            "paper",
            "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
            "91d55953-b575-4fe8-8d39-6e0cade58d59",
            "order_submission",
        )
    ]
    assert repaired_buy.contract_fields["reason_code"] == "insufficient_cash"
    assert repaired_buy.contract_fields["expression_reason_code"] == "long_cash"

    # Also verify mode binding even when an explicit cursor_path is shared between dry-run and apply
    shared_cursor = tmp_path / "shared-cursor.json"
    buy_stage2, buy_tel2 = _build_2317_buy_refusal_fixture()
    backend2 = InMemoryCauseRepairBackend(
        stages=[buy_stage2],
        telemetry_events=buy_tel2,
    )
    dry2 = run_cause_repair(
        backend2,
        apply=False,
        work_dir=tmp_path,
        cursor_path=shared_cursor,
    )
    assert dry2.needs_update_rows == 1
    apply2 = run_cause_repair(
        backend2,
        apply=True,
        resume=True,
        work_dir=tmp_path,
        cursor_path=shared_cursor,
    )
    assert apply2.scanned_rows == 1
    assert apply2.updated_rows == 1

    # 2. Filtered -> full and full -> filtered inventory transitions with resume=True
    buy_stage3, buy_tel3 = _build_2317_buy_refusal_fixture()
    sell_stage3, sell_tel3, sell_comp3, sell_evals3 = (
        _build_2317_sell_reconciliation_fixture()
    )
    scope_dir = tmp_path / "scope-transitions"
    backend3 = InMemoryCauseRepairBackend(
        stages=[buy_stage3, sell_stage3],
        telemetry_events={**buy_tel3, **sell_tel3},
        companion_stages=sell_comp3,
        drift_evaluations=sell_evals3,
    )
    # Note: tj_57418ba7... (sell) sorts BEFORE tj_79726064... (buy).
    # Running filtered apply on tj_79726064... advances the filtered cursor to the highest key.
    filtered_apply = run_cause_repair(
        backend3,
        apply=True,
        journey_ids=["tj_79726064-4cc2-5510-8e11-7f5e59ff89bd"],
        work_dir=scope_dir,
    )
    assert filtered_apply.scanned_rows == 1
    assert filtered_apply.updated_rows == 1

    # Full inventory with resume=True must NOT skip tj_57418ba7... due to the prior filtered cursor
    full_apply = run_cause_repair(
        backend3,
        apply=True,
        resume=True,
        journey_ids=None,
        work_dir=scope_dir,
    )
    assert full_apply.scanned_rows == 2
    assert full_apply.updated_rows == 1
    assert full_apply.already_repaired_rows == 1

    # Full inventory partial cursor followed by a filtered --resume run must not skip the filtered target
    buy_stage4, buy_tel4 = _build_2317_buy_refusal_fixture()
    sell_stage4, sell_tel4, sell_comp4, sell_evals4 = (
        _build_2317_sell_reconciliation_fixture()
    )
    rev_dir = tmp_path / "full-to-filtered"
    backend4 = InMemoryCauseRepairBackend(
        stages=[buy_stage4, sell_stage4],
        telemetry_events={**buy_tel4, **sell_tel4},
        companion_stages=sell_comp4,
        drift_evaluations=sell_evals4,
    )
    # Full dry-run writes cursor at tj_79726064...
    full_dry = run_cause_repair(backend4, apply=False, work_dir=rev_dir)
    assert full_dry.scanned_rows == 2
    # Filtered dry-run with resume=True for tj_57418ba7... must start fresh for that filtered scope
    filtered_dry = run_cause_repair(
        backend4,
        apply=False,
        resume=True,
        journey_ids=["tj_57418ba7-ef37-5015-9b5e-b6423872da89"],
        work_dir=rev_dir,
    )
    assert filtered_dry.scanned_rows == 1
    assert filtered_dry.needs_update_rows == 1


def test_missing_original_fill_telemetry_does_not_substitute_unrelated_same_journey_companion(
    tmp_path: Path,
) -> None:
    recon_stage, telemetry, companions, evaluations = (
        _build_2317_sell_reconciliation_fixture()
    )
    journey_id = "tj_57418ba7-ef37-5015-9b5e-b6423872da89"
    orig_fill_eid = "000600fb-6496-47d9-a550-1fa23449c1e2"

    # Remove the referenced fill telemetry and set the same-journey companion to an unrelated fill
    telemetry.pop(orig_fill_eid)
    companions[(journey_id, "fill_management")] = {
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "journey_id": journey_id,
        "stage_name": "fill_management",
        "source_event_id": "other-fill-same-journey",
        "stage_status": "succeeded",
        "contract_fields": {
            "event_id": "other-fill-same-journey:fill_management",
            "canonical_event_id": "other-fill-same-journey",
            "event_type": "paper_fill_simulated",
            "stage": "fill_management",
            "stage_status": "succeeded",
            "symbol": "2317.TWSE",
            "side": "sell",
            "quantity": 9.0,
            "price": 999.0,
            "fill_id": "paper-fill-other-fill-same-journey",
            "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
        },
    }

    backend = InMemoryCauseRepairBackend(
        stages=[recon_stage],
        telemetry_events=telemetry,
        companion_stages=companions,
        drift_evaluations=evaluations,
    )
    report = run_cause_repair(backend, apply=True, work_dir=tmp_path)
    assert report.scanned_rows == 1
    assert report.health_only_evaluations == 1
    assert report.updated_rows == 1

    cf = backend.stages[
        (
            "tenant-dev",
            "paper",
            journey_id,
            "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14",
            "reconciliation",
        )
    ].contract_fields
    assert cf["source_fill_event_id"] == orig_fill_eid
    assert cf["fill_id"] == f"paper-fill-{orig_fill_eid}"
    assert "quantity" not in cf
    assert "fill_quantity" not in cf
    assert "price" not in cf
    assert "fill_price" not in cf
    assert cf["reason_code"] == "reconciliation_evidence_incomplete"
    assert "fill_event" in cf["missing_evidence"]
    assert "fill_quantity" in cf["missing_evidence"]
    assert "fill_price" in cf["missing_evidence"]


def test_multiple_fills_in_same_journey_resolve_companions_by_exact_ids_and_reject_conflicts(
    tmp_path: Path,
) -> None:
    recon1_stage, telemetry, companions, evaluations = (
        _build_2317_sell_reconciliation_fixture()
    )
    journey_id = "tj_57418ba7-ef37-5015-9b5e-b6423872da89"
    fill1_eid = "000600fb-6496-47d9-a550-1fa23449c1e2"
    snap1_eid = "8d6761c5-5ae8-5799-87bb-10302186595a"
    eval_id = (
        "rdeval-sched-scheduled-tenant-dev-2026-11-16T-"
        "rb-7f3c1be55a224fdaa36bb-a39cdf74492b52bb"
    )

    # Add a second fill + snapshot + reconciliation stage on the same journey
    fill2_eid = "other-fill-same-journey"
    snap2_eid = "other-snap-same-journey"
    recon2_eid = "other-recon-same-journey"

    snap2_payload = _base_telemetry_payload(
        journey_id=journey_id,
        event_id=snap2_eid,
        event_type="position_snapshot",
        sequence_no=9,
        created_at="2026-11-16T07:10:00Z",
        causation_event_id=fill2_eid,
    )
    snap2_payload.update(
        {
            "symbol": "2317.TWSE",
            "side": "sell",
            "position_qty": 0.0,
            "metrics": {"position_qty": 0.0, "price": 999.0, "cash": 109000.0},
        }
    )
    snap2_payload["metadata"].update(
        {
            "symbol": "2317.TWSE",
            "side": "sell",
            "fill_id": f"paper-fill-{fill2_eid}",
            "source_fill_event_id": fill2_eid,
            "broker_order_id": "broker-order-fill-2",
            "causal_parent_id": fill2_eid,
        }
    )

    recon2_payload = _base_telemetry_payload(
        journey_id=journey_id,
        event_id=recon2_eid,
        event_type="reconciliation_failed",
        sequence_no=10,
        created_at="2026-11-16T07:15:00Z",
        producer="reconciliation-drift.scheduled",
        causation_event_id=snap2_eid,
    )
    recon2_payload.update({"symbol": "2317.TWSE", "side": "sell"})
    recon2_payload["metadata"].update(
        {
            "symbol": "2317.TWSE",
            "side": "sell",
            "fill_id": f"paper-fill-{fill2_eid}",
            "source_fill_event_id": fill2_eid,
            "broker_order_id": "broker-order-fill-2",
            "causal_parent_id": snap2_eid,
            "reconciliation_status": "warning",
            "reconciliation_evaluation_id": eval_id,
        }
    )
    recon2_stage = CandidateStageRow(
        tenant_id="tenant-dev",
        environment="paper",
        journey_id=journey_id,
        source_event_id=recon2_eid,
        stage_name="reconciliation",
        stage_status="failed",
        stage_ordinal=2,
        source_ingested_seq=17309999,
        event_sequence=10,
        occurred_at_iso="2026-11-16T07:15:00Z",
        recorded_at_iso="2026-11-16T07:15:00Z",
        contract_fields={
            "event_id": f"{recon2_eid}:reconciliation",
            "canonical_event_id": recon2_eid,
            "event_type": "reconciliation_failed",
            "journey_id": journey_id,
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "stage": "reconciliation",
            "stage_status": "failed",
            "symbol": "2317.TWSE",
            "side": "sell",
            "fill_id": f"paper-fill-{fill2_eid}",
            "broker_order_id": "broker-order-fill-2",
        },
        evidence_references=[],
        projection_revision=6,
        fingerprint="fp-2317-sell-recon-2",
        journey_status="completed_with_variance",
        journey_projection_revision=6,
    )

    # Remove fill1 telemetry so recon1 must recover from its exact companion stage while fill2 companion is also present
    telemetry.pop(fill1_eid)
    telemetry[snap2_eid] = {
        "event_id": snap2_eid,
        "event_type": "position_snapshot",
        "created_at": "2026-11-16T07:10:00Z",
        "payload": snap2_payload,
    }
    telemetry[recon2_eid] = {
        "event_id": recon2_eid,
        "event_type": "reconciliation_failed",
        "created_at": "2026-11-16T07:15:00Z",
        "payload": recon2_payload,
    }

    multi_companions = {
        (journey_id, "fill_management"): [
            {
                "tenant_id": "tenant-dev",
                "environment": "paper",
                "journey_id": journey_id,
                "stage_name": "fill_management",
                "source_event_id": fill2_eid,
                "stage_status": "succeeded",
                "contract_fields": {
                    "event_id": f"{fill2_eid}:fill_management",
                    "canonical_event_id": fill2_eid,
                    "stage": "fill_management",
                    "symbol": "2317.TWSE",
                    "side": "sell",
                    "quantity": 9.0,
                    "price": 999.0,
                    "fill_id": f"paper-fill-{fill2_eid}",
                    "broker_order_id": "broker-order-fill-2",
                },
            },
            companions[(journey_id, "fill_management")],
        ],
        (journey_id, "ledger_booking"): [
            {
                "tenant_id": "tenant-dev",
                "environment": "paper",
                "journey_id": journey_id,
                "stage_name": "ledger_booking",
                "source_event_id": snap2_eid,
                "stage_status": "succeeded",
                "contract_fields": {
                    "event_id": f"{snap2_eid}:ledger_booking",
                    "canonical_event_id": snap2_eid,
                    "stage": "ledger_booking",
                    "symbol": "2317.TWSE",
                    "side": "sell",
                    "quantity": 0.0,
                    "fill_id": f"paper-fill-{fill2_eid}",
                    "broker_order_id": "broker-order-fill-2",
                },
            },
            companions[(journey_id, "ledger_booking")],
        ],
    }

    backend = InMemoryCauseRepairBackend(
        stages=[recon1_stage, recon2_stage],
        telemetry_events=telemetry,
        companion_stages=multi_companions,
        drift_evaluations=evaluations,
    )
    report = run_cause_repair(backend, apply=True, work_dir=tmp_path)
    assert report.updated_rows == 2

    cf1 = backend.stages[
        ("tenant-dev", "paper", journey_id, recon1_stage.source_event_id, "reconciliation")
    ].contract_fields
    assert cf1["source_fill_event_id"] == fill1_eid
    assert cf1["fill_id"] == f"paper-fill-{fill1_eid}"
    assert cf1["snapshot_event_id"] == snap1_eid
    assert cf1["quantity"] == 1.0
    assert cf1["price"] == 218.5

    cf2 = backend.stages[
        ("tenant-dev", "paper", journey_id, recon2_eid, "reconciliation")
    ].contract_fields
    assert cf2["source_fill_event_id"] == fill2_eid
    assert cf2["fill_id"] == f"paper-fill-{fill2_eid}"
    assert cf2["snapshot_event_id"] == snap2_eid
    assert cf2["quantity"] == 9.0
    assert cf2["price"] == 999.0

    # Now test conflicting fill/snapshot evidence: snapshot claims fill_quantity=9, fill_price=999 while fill telemetry says 1@218.5
    c_recon, c_tel, c_comp, c_evals = _build_2317_sell_reconciliation_fixture()
    c_tel[snap1_eid]["payload"]["metadata"]["fill_quantity"] = 9.0
    c_tel[snap1_eid]["payload"]["metadata"]["fill_price"] = 999.0
    conflict_backend = InMemoryCauseRepairBackend(
        stages=[c_recon],
        telemetry_events=c_tel,
        companion_stages=c_comp,
        drift_evaluations=c_evals,
    )
    c_report = run_cause_repair(
        conflict_backend,
        apply=True,
        work_dir=tmp_path / "conflict",
    )
    assert c_report.updated_rows == 1
    assert c_report.proven_variances == 0
    assert c_report.health_only_evaluations == 1
    c_cf = conflict_backend.stages[
        ("tenant-dev", "paper", journey_id, c_recon.source_event_id, "reconciliation")
    ].contract_fields
    assert "quantity" not in c_cf
    assert "fill_quantity" not in c_cf
    assert "price" not in c_cf
    assert "fill_price" not in c_cf
    assert c_cf["reason_code"] == "reconciliation_evidence_incomplete"
    assert "conflicting_fill_snapshot_evidence" in c_cf["missing_evidence"]
    assert "fill_quantity" in c_cf["missing_evidence"]
    assert "fill_price" in c_cf["missing_evidence"]


def test_preexisting_proven_variance_idempotency_across_repeated_apply_runs(
    tmp_path: Path,
) -> None:
    recon_stage, telemetry, companions, evaluations = (
        _build_2317_sell_reconciliation_fixture()
    )
    seeded_fields = dict(recon_stage.contract_fields)
    seeded_fields["reason_detail"] = [
        {
            "check": "cash_change",
            "status": "failed",
            "expected": 100.0,
            "actual": 55.0,
        }
    ]
    seeded_stage = CandidateStageRow(
        **{
            **recon_stage.__dict__,
            "contract_fields": seeded_fields,
        }
    )
    backend = InMemoryCauseRepairBackend(
        stages=[seeded_stage],
        telemetry_events=telemetry,
        companion_stages=companions,
        drift_evaluations=evaluations,
    )

    run1 = run_cause_repair(backend, apply=True, work_dir=tmp_path)
    assert run1.proven_variances == 1
    assert run1.updated_rows == 1
    cf1 = backend.stages[
        (
            "tenant-dev",
            "paper",
            seeded_stage.journey_id,
            seeded_stage.source_event_id,
            "reconciliation",
        )
    ].contract_fields
    assert cf1["reason_code"] == "cash_variance"
    assert len(cf1["reason_detail"]) == 3

    run2 = run_cause_repair(backend, apply=True, work_dir=tmp_path)
    assert run2.proven_variances == 1
    assert run2.needs_update_rows == 0
    assert run2.updated_rows == 0
    assert run2.already_repaired_rows == 1
    cf2 = backend.stages[
        (
            "tenant-dev",
            "paper",
            seeded_stage.journey_id,
            seeded_stage.source_event_id,
            "reconciliation",
        )
    ].contract_fields
    assert len(cf2["reason_detail"]) == 3

    run3 = run_cause_repair(backend, apply=True, work_dir=tmp_path)
    assert run3.proven_variances == 1
    assert run3.needs_update_rows == 0
    assert run3.updated_rows == 0
    assert run3.already_repaired_rows == 1
    cf3 = backend.stages[
        (
            "tenant-dev",
            "paper",
            seeded_stage.journey_id,
            seeded_stage.source_event_id,
            "reconciliation",
        )
    ].contract_fields
    assert len(cf3["reason_detail"]) == 3


