#!/usr/bin/env python3
"""MGMT-SAFE-004 canary human-gate smoke.

The smoke exercises the real EP5 canary readiness packet builder with fixture
artifacts. A packet can become ready only when it carries explicit canary
promotion refs, risk-owner and operator approval refs, Shioaji broker sandbox
smoke evidence, the Shioaji sandbox evidence packet, and fail-closed
live/no-real-capital evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_ep5_canary_readiness as readiness  # noqa: E402


TASK_ID = "MGMT-SAFE-004"


@dataclass
class SmokeRow:
    row: str
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _valid_canary_plan(**promotion_gate_overrides: Any) -> dict[str, Any]:
    promotion_gate = {
        "promotion_gate_decision_id": "approval-canary-smoke-001",
        "human_gate_packet_ref": "docs/deployment/evidence/ep5-human-gate-input/smoke/human-gate-packet.json",
        "broker_sandbox_smoke_ref": "docs/deployment/evidence/ep5-broker-tw-002/smoke/summary.json",
        "risk_owner_approval_ref": "approval://risk-owner/canary-smoke",
        "operator_approval_ref": "approval://operator/canary-smoke",
        "persona_capital_binding_id": "pcb-canary-smoke-001",
        "allowed_deployment_scope": "canary",
        "capital_scale_pct": 5.0,
        "gross_scale_pct": 25.0,
    }
    promotion_gate.update(promotion_gate_overrides)
    return {
        "target_stage": "canary",
        "scale": {"capital_scale_pct": 5.0, "gross_scale_pct": 25.0},
        "rollback": {"action_type": "pause_then_replace"},
        "metadata": {"promotion_gate": promotion_gate},
    }


def _valid_broker_smoke() -> dict[str, Any]:
    return {
        "task_id": "EP5-BROKER-TW-002",
        "provider": "Shioaji",
        "status": "passed",
        "run_mode": "mock_api_replay",
        "proof_boundary": "broker_adapter_sandbox_smoke; not canary/live/capital proof",
        "order_ids": {
            "pantheon_order_id": "order-smoke-001",
            "shioaji_trade_id": "trade-smoke-001",
        },
        "reconciliation": {"status": "passed"},
        "live_gate": {
            "status": "rejected",
            "response": {"error_code": "SHIOAJI_LIVE_DISABLED"},
        },
        "no_real_capital": {
            "real_capital_used": False,
            "production_live_order_submitted": False,
        },
    }


def _valid_shioaji_evidence(
    *,
    broker_smoke_ref: str,
    evidence_packet_ref: str,
    status: str = "passed",
) -> dict[str, Any]:
    acceptance_check_details = {
        "broker_is_shioaji": "source smoke provider is Shioaji",
        "environment_is_sandbox": "order response stays in sandbox",
        "place_result_recorded": "place response status is submitted",
        "cancel_result_recorded": "cancel response status is cancelled",
        "readback_after_cancel_recorded": "readback after cancel confirms cancelled status",
        "reconcile_passed": "reconciliation status is passed",
        "live_broker_fail_closed": "live broker request is rejected with SHIOAJI_LIVE_DISABLED",
        "capital_binding_not_enabled": "real capital is neither used nor reserved",
        "no_secret_material_persisted": "raw Shioaji secret material is not persisted",
    }
    return {
        "schema_version": "shioaji_sandbox_evidence_packet.v1",
        "task_id": "MGMT-BROKER-004",
        "milestone_id": "MGMT-OODA-M3",
        "provider": "Shioaji",
        "broker": "shioaji",
        "environment": "sandbox",
        "status": status,
        "account_status": "ready",
        "production_live_enabled": False,
        "capital_binding_enabled": False,
        "human_gate_required": True,
        "proof_boundary": "broker_sandbox_evidence_packet; not canary/live/capital proof",
        "fail_closed_posture": {
            "live_gate": {
                "status": "rejected",
                "response": {"error_code": "SHIOAJI_LIVE_DISABLED"},
            },
            "no_real_capital": {
                "real_capital_used": False,
                "real_capital_reserved": False,
                "production_live_order_submitted": False,
            },
        },
        "canary_readiness_inputs": {
            "broker_sandbox_smoke_ref": broker_smoke_ref,
            "broker_sandbox_evidence_packet_ref": evidence_packet_ref,
            "human_gate_required": True,
            "risk_owner_approval_required": True,
            "operator_approval_required": True,
        },
        "acceptance_checks": [
            {"name": name, "status": "pass", "detail": acceptance_check_details[name]}
            for name in readiness.SHIOAJI_SANDBOX_REQUIRED_ACCEPTANCE_CHECK_NAMES
        ],
        "ooda_packet": {"status": "closed"},
        "ooda_packet_validation_errors": [],
    }


def _packet(
    output_dir: Path,
    case_id: str,
    plan: dict[str, Any],
    broker_smoke: dict[str, Any] | None,
    shioaji_evidence: dict[str, Any] | None = None,
    *,
    auto_shioaji_evidence: bool = True,
) -> dict[str, Any]:
    case_dir = output_dir / "fixtures" / case_id
    checklist_path = case_dir / "operator-checklist.json"
    datasource_summary_path = case_dir / "datasource-summary.json"
    plan_path = case_dir / "canary-deployment-plan.json"
    drill_summary_path = case_dir / "rollback-drill-summary.json"
    broker_smoke_summary_path = case_dir / "broker-smoke-summary.json"
    shioaji_evidence_packet_path = case_dir / "shioaji-sandbox-evidence-packet.json"

    _write_json(checklist_path, {"status": "pass", "check_health": True})
    _write_json(
        datasource_summary_path,
        {"status": "pass", "providers": ["ibkr", "kraken", "shioaji", "tej"]},
    )
    _write_json(plan_path, plan)
    _write_json(
        drill_summary_path,
        {
            "status": "executed",
            "replacement_binding_id": "rb-canary-smoke-replacement",
            "kill_switch_safe_mode": "paused",
        },
    )
    if broker_smoke is not None:
        _write_json(broker_smoke_summary_path, broker_smoke)
    if auto_shioaji_evidence and shioaji_evidence is None and broker_smoke is not None:
        shioaji_evidence = _valid_shioaji_evidence(
            broker_smoke_ref=str(broker_smoke_summary_path),
            evidence_packet_ref=str(shioaji_evidence_packet_path),
        )
    if shioaji_evidence is not None:
        _write_json(shioaji_evidence_packet_path, shioaji_evidence)

    return readiness.build_human_gate_packet(
        checklist_path=checklist_path,
        datasource_summary_path=datasource_summary_path,
        plan_path=plan_path,
        drill_summary_path=drill_summary_path,
        broker_smoke_summary_path=broker_smoke_summary_path if broker_smoke is not None else None,
        shioaji_evidence_packet_path=shioaji_evidence_packet_path if shioaji_evidence is not None else None,
        dual_vm_evidence_dir=case_dir,
        event_trace_status="packetized",
        event_trace_note="Trace read-model evidence remains packetized for this smoke.",
    )


def _pass(row: str, evidence: dict[str, Any] | None = None) -> SmokeRow:
    return SmokeRow(row=row, status="passed", evidence=evidence or {})


def _fail(row: str, error: str, evidence: dict[str, Any] | None = None) -> SmokeRow:
    return SmokeRow(row=row, status="failed", error=error, evidence=evidence or {})


def _check(row: str, condition: bool, evidence: dict[str, Any], error: str) -> SmokeRow:
    return _pass(row, evidence) if condition else _fail(row, error, evidence)


def _checks(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in packet["acceptance_checks"]}


def _row_for_ready_packet(output_dir: Path) -> SmokeRow:
    packet = _packet(output_dir, "ready", _valid_canary_plan(), _valid_broker_smoke())
    checks = _checks(packet)
    canary_plan = packet["bundle"]["canary_plan"]
    broker_smoke = packet["bundle"]["broker_sandbox_smoke"]
    shioaji_evidence = packet["bundle"]["shioaji_sandbox_evidence_packet"]
    return _check(
        "ready-with-explicit-human-gate-broker-smoke-and-shioaji-evidence",
        packet["status"] == "ready_for_review"
        and checks["canary_activation_gate_refs_present"]["status"] == "pass"
        and checks["broker_sandbox_smoke_consumed"]["status"] == "pass"
        and checks["shioaji_sandbox_evidence_packet_consumed"]["status"] == "pass"
        and canary_plan["target_stage"] == "canary"
        and canary_plan["promotion_gate"]["risk_owner_approval_ref"] == "approval://risk-owner/canary-smoke"
        and canary_plan["promotion_gate"]["operator_approval_ref"] == "approval://operator/canary-smoke"
        and broker_smoke["provider"] == "Shioaji"
        and broker_smoke["reconciliation_status"] == "passed"
        and broker_smoke["live_gate_status"] == "rejected"
        and shioaji_evidence["provider"] == "Shioaji"
        and shioaji_evidence["status"] == "passed",
        {
            "packet_status": packet["status"],
            "canary_gate_check": checks["canary_activation_gate_refs_present"]["status"],
            "broker_smoke_check": checks["broker_sandbox_smoke_consumed"]["status"],
            "shioaji_evidence_check": checks["shioaji_sandbox_evidence_packet_consumed"]["status"],
            "target_stage": canary_plan["target_stage"],
            "live_gate_status": broker_smoke["live_gate_status"],
            "shioaji_evidence_status": shioaji_evidence["status"],
        },
        "valid canary human-gate packet must be ready only with explicit approvals, Shioaji sandbox smoke, and Shioaji evidence",
    )


def _row_for_missing_operator(output_dir: Path) -> SmokeRow:
    packet = _packet(output_dir, "missing-operator", _valid_canary_plan(operator_approval_ref=""), _valid_broker_smoke())
    checks = _checks(packet)
    return _check(
        "missing-operator-approval-keeps-packet-incomplete",
        packet["status"] == "incomplete"
        and checks["canary_activation_gate_refs_present"]["status"] == "fail"
        and "operator_approval_ref" in checks["canary_activation_gate_refs_present"]["detail"],
        {
            "packet_status": packet["status"],
            "detail": checks["canary_activation_gate_refs_present"]["detail"],
        },
        "packet must stay incomplete without operator approval ref",
    )


def _row_for_missing_broker_smoke(output_dir: Path) -> SmokeRow:
    packet = _packet(output_dir, "missing-broker-smoke", _valid_canary_plan(), None)
    checks = _checks(packet)
    return _check(
        "missing-broker-smoke-keeps-packet-incomplete",
        packet["status"] == "incomplete"
        and checks["broker_sandbox_smoke_consumed"]["status"] == "fail",
        {
            "packet_status": packet["status"],
            "detail": checks["broker_sandbox_smoke_consumed"]["detail"],
        },
        "packet must stay incomplete without Shioaji broker sandbox smoke summary",
    )


def _row_for_missing_shioaji_evidence(output_dir: Path) -> SmokeRow:
    packet = _packet(
        output_dir,
        "missing-shioaji-evidence",
        _valid_canary_plan(),
        _valid_broker_smoke(),
        None,
        auto_shioaji_evidence=False,
    )
    checks = _checks(packet)
    return _check(
        "missing-shioaji-evidence-keeps-packet-incomplete",
        packet["status"] == "incomplete"
        and checks["broker_sandbox_smoke_consumed"]["status"] == "pass"
        and checks["shioaji_sandbox_evidence_packet_consumed"]["status"] == "fail",
        {
            "packet_status": packet["status"],
            "detail": checks["shioaji_sandbox_evidence_packet_consumed"]["detail"],
        },
        "packet must stay incomplete without the Shioaji sandbox evidence packet",
    )


def _row_for_live_target(output_dir: Path) -> SmokeRow:
    plan = _valid_canary_plan()
    plan["target_stage"] = "live"
    packet = _packet(output_dir, "live-target", plan, _valid_broker_smoke())
    checks = _checks(packet)
    return _check(
        "live-target-is-not-accepted-as-canary-human-gate",
        packet["status"] == "incomplete"
        and checks["canary_activation_gate_refs_present"]["status"] == "fail"
        and "target_stage=live" in checks["canary_activation_gate_refs_present"]["detail"],
        {
            "packet_status": packet["status"],
            "detail": checks["canary_activation_gate_refs_present"]["detail"],
        },
        "canary human gate must not accept a live target plan",
    )


def _row_for_live_enabled_broker_smoke(output_dir: Path) -> SmokeRow:
    broker_smoke = _valid_broker_smoke()
    broker_smoke["live_gate"] = {"status": "accepted", "response": {"error_code": None}}
    broker_smoke["production_live"] = {"enabled": True}
    packet = _packet(output_dir, "live-enabled-broker-smoke", _valid_canary_plan(), broker_smoke)
    checks = _checks(packet)
    return _check(
        "production-live-broker-smoke-is-rejected",
        packet["status"] == "incomplete"
        and checks["broker_sandbox_smoke_consumed"]["status"] == "fail",
        {
            "packet_status": packet["status"],
            "detail": checks["broker_sandbox_smoke_consumed"]["detail"],
        },
        "broker smoke must prove fail-closed live boundary before human gate can be ready",
    )


def run_smoke(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        _row_for_ready_packet(output_dir),
        _row_for_missing_operator(output_dir),
        _row_for_missing_broker_smoke(output_dir),
        _row_for_missing_shioaji_evidence(output_dir),
        _row_for_live_target(output_dir),
        _row_for_live_enabled_broker_smoke(output_dir),
    ]
    passed = sum(1 for row in rows if row.status == "passed")
    smoke_passed = passed == len(rows)
    return {
        "task_id": TASK_ID,
        "summary": {
            "passed": passed,
            "rows": len(rows),
            "smoke_passed": smoke_passed,
            "production_live_order_submitted": False,
            "real_capital_used": False,
            "canary_target_required": True,
            "risk_owner_approval_required": True,
            "operator_approval_required": True,
            "broker_sandbox_smoke_required": True,
            "shioaji_sandbox_evidence_required": True,
        },
        "assertions": {
            "canary_packet_ready_only_with_human_gate_refs": smoke_passed,
            "shioaji_broker_sandbox_smoke_required": smoke_passed,
            "shioaji_sandbox_evidence_packet_required": smoke_passed,
            "live_target_rejected": smoke_passed,
            "production_live_boundary_must_be_fail_closed": smoke_passed,
        },
        "rows": [asdict(row) for row in rows],
    }


def _print_summary(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(f"Canary human-gate smoke: {summary['passed']}/{summary['rows']} passed")
    for row in report["rows"]:
        marker = "ok " if row["status"] == "passed" else "err"
        print(f"{marker} {row['row']}")
        if row["error"]:
            print(f"    {row['error']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run MGMT-SAFE-004 canary human-gate smoke.")
    parser.add_argument("--output-dir", default="", help="Directory for local smoke fixture artifacts.")
    parser.add_argument("--json-out", default="", help="Optional path for the JSON evidence report.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="canary-human-gate-smoke-") as tmp:
        output_dir = Path(args.output_dir) if args.output_dir else Path(tmp)
        report = run_smoke(output_dir)
        if args.json_out:
            out = Path(args.json_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _print_summary(report)
        return 0 if report["summary"]["smoke_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
