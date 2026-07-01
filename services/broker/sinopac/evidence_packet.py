#!/usr/bin/env python3
"""Build the Management Shioaji sandbox evidence packet.

The packet is a truthful wrapper around a sandbox smoke summary. It packages
place/cancel/readback/reconcile proof for Management/OODA consumers while
keeping canary, live broker, and capital-binding side effects closed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
OODA_DIR = REPO_ROOT / "services" / "control-plane" / "ooda"
if str(OODA_DIR) not in sys.path:
    sys.path.insert(0, str(OODA_DIR))

from ooda_loop_packet import (  # noqa: E402
    ActBundle,
    DecideBundle,
    LearnBundle,
    LoopEnvironment,
    LoopStatus,
    LoopType,
    ObserveBundle,
    OodaLoopPacket,
    OrientBundle,
    validate_packet,
)


TASK_ID = "MGMT-BROKER-004"
MILESTONE_ID = "MGMT-OODA-M3"
SCHEMA_VERSION = "shioaji_sandbox_evidence_packet.v1"
BROKER = "shioaji"
ENVIRONMENT = "sandbox"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "support" / "evidence" / TASK_ID
DEFAULT_PACKET_NAME = "shioaji-sandbox-evidence-packet.json"
DEFAULT_MILESTONE_OUTPUT = REPO_ROOT / "support" / "evidence" / "MGMT-OODA-M3-shioaji-sandbox.json"
PROOF_BOUNDARY = "broker_sandbox_evidence_packet; not canary/live/capital proof"


class EvidencePacketError(RuntimeError):
    """Raised when a packet cannot be built from the supplied smoke summary."""


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise EvidencePacketError(f"{path} must contain a JSON object")
    return payload


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def portable_ref(path: str | Path) -> str:
    """Return a repo-relative artifact ref when the path belongs to this repo."""
    artifact_path = Path(path)
    resolved = artifact_path.resolve() if artifact_path.is_absolute() else (REPO_ROOT / artifact_path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "pass" if passed else "fail",
        "detail": detail,
    }


def derive_account_status(smoke_summary: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    error = smoke_summary.get("error") if isinstance(smoke_summary.get("error"), dict) else {}
    error_code = str(error.get("error_code") or "")
    environment = smoke_summary.get("environment") if isinstance(smoke_summary.get("environment"), dict) else {}
    api_key_configured = bool(environment.get("BROKER_SHIOAJI_API_KEY_configured"))
    secret_configured = bool(environment.get("BROKER_SHIOAJI_SECRET_KEY_configured"))
    run_mode = str(smoke_summary.get("run_mode") or "")
    status = str(smoke_summary.get("status") or "")

    if error_code == "SHIOAJI_ACCOUNT_MISSING":
        account_status = "missing"
        basis = "adapter reported missing sandbox account"
    elif error_code in {"SHIOAJI_CREDENTIALS_MISSING", "SHIOAJI_SDK_MISSING"}:
        account_status = "missing"
        basis = f"adapter reported {error_code}"
    elif status == "passed":
        account_status = "ready"
        basis = "place/cancel/readback/reconcile completed"
        if run_mode == "mock_api_replay":
            basis = "mock Shioaji API replay completed; real account credential readiness is not asserted"
    elif api_key_configured and secret_configured:
        account_status = "unsigned"
        basis = "credentials were configured but sandbox lifecycle did not complete"
    else:
        account_status = "missing"
        basis = "sandbox credentials are not configured"

    return account_status, {
        "basis": basis,
        "run_mode": run_mode,
        "api_key_configured": api_key_configured,
        "secret_key_configured": secret_configured,
        "raw_secret_material_persisted": bool(environment.get("raw_secret_material_persisted", False)),
    }


def build_ooda_packet(
    *,
    generated_at: str,
    task_packet_path: str,
    smoke_summary_path: str,
    packet_status: str,
) -> dict[str, Any]:
    packet = OodaLoopPacket(
        packet_id="ooda-mgmt-broker-004-shioaji-sandbox",
        loop_type=LoopType.REBALANCE,
        status=LoopStatus.CLOSED if packet_status == "passed" else LoopStatus.FAILED,
        environment=LoopEnvironment.SANDBOX,
        capital_pool_id="pool-ep5-broker-tw-sandbox",
        strategy_id="strategy-ep5-broker-tw-smoke",
        persona_ids=[],
        observe=ObserveBundle(
            source_refs=[
                "docs/04/pantheon_sa_supplemental_2026-05-15/SA_management_console_multi_persona_ooda.md",
                "docs/04/pantheon_sa_supplemental_2026-05-15/SD_management_console_multi_persona_ooda.md",
            ],
            telemetry_refs=[],
            signal_refs=[],
            market_refs=[smoke_summary_path],
        ),
        orient=OrientBundle(
            evidence_bundle_refs=[task_packet_path],
            risk_adjudication_ref="policy://live-broker-and-capital-binding-fail-closed",
        ),
        decide=DecideBundle(
            approval_decision_id="human-gate-required-before-canary",
            decision_rationale_ref="support/evidence/MGMT-BROKER-004/README.md",
            policy_decision_refs=[
                "PAPER_CANARY_LIVE_POLICY.md",
                "BINDING_AND_DEPLOYMENT_SEMANTICS.md",
            ],
        ),
        act=ActBundle(
            broker_evidence_refs=[smoke_summary_path, task_packet_path],
            command_receipt_refs=["command-receipt://mgmt-broker-004/no-live-command-issued"],
            live_capital_side_effects=False,
        ),
        learn=LearnBundle(
            postmortem_refs=["support/evidence/MGMT-BROKER-004/README.md"],
        ),
        audit_refs=[
            smoke_summary_path,
            task_packet_path,
        ],
        created_at=generated_at,
        updated_at=generated_at,
        closed_at=generated_at,
    )
    return packet.to_dict()


def build_evidence_packet(
    *,
    smoke_summary: dict[str, Any],
    smoke_summary_path: str,
    task_packet_path: str,
    generated_at: str | None = None,
    task_id: str = TASK_ID,
) -> dict[str, Any]:
    generated = generated_at or iso_now()
    smoke_summary_ref = portable_ref(smoke_summary_path)
    task_packet_ref = portable_ref(task_packet_path)
    account_status, account_status_detail = derive_account_status(smoke_summary)
    place_result = dict((smoke_summary.get("place") or {}).get("response") or {})
    cancel_result = dict((smoke_summary.get("cancel") or {}).get("response") or {})
    readback = smoke_summary.get("readback") if isinstance(smoke_summary.get("readback"), dict) else {}
    readback_result = {
        "after_place": dict(readback.get("after_place") or {}),
        "after_cancel": dict(readback.get("after_cancel") or {}),
    }
    reconcile_result = dict(smoke_summary.get("reconciliation") or {})
    live_gate = smoke_summary.get("live_gate") if isinstance(smoke_summary.get("live_gate"), dict) else {}
    no_real_capital = smoke_summary.get("no_real_capital") if isinstance(smoke_summary.get("no_real_capital"), dict) else {}
    environment = smoke_summary.get("environment") if isinstance(smoke_summary.get("environment"), dict) else {}

    checks = [
        check("broker_is_shioaji", smoke_summary.get("provider") == "Shioaji", "source smoke provider is Shioaji"),
        check("environment_is_sandbox", place_result.get("deployment_stage") == ENVIRONMENT, "order response stays in sandbox"),
        check("place_result_recorded", place_result.get("status") == "submitted", "place response status is submitted"),
        check("cancel_result_recorded", cancel_result.get("status") == "cancelled", "cancel response status is cancelled"),
        check(
            "readback_after_cancel_recorded",
            (readback_result.get("after_cancel") or {}).get("status") == "cancelled",
            "readback after cancel confirms cancelled status",
        ),
        check("reconcile_passed", reconcile_result.get("status") == "passed", "reconciliation status is passed"),
        check(
            "live_broker_fail_closed",
            live_gate.get("status") == "rejected"
            and (live_gate.get("response") or {}).get("error_code") == "SHIOAJI_LIVE_DISABLED",
            "live broker request is rejected with SHIOAJI_LIVE_DISABLED",
        ),
        check(
            "capital_binding_not_enabled",
            no_real_capital.get("real_capital_used") is False
            and no_real_capital.get("real_capital_reserved") is False,
            "real capital is neither used nor reserved",
        ),
        check(
            "no_secret_material_persisted",
            environment.get("raw_secret_material_persisted") is False,
            "raw Shioaji secret material is not persisted",
        ),
    ]

    status = "passed" if all(item["status"] == "pass" for item in checks) else "incomplete"
    packet = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "milestone_id": MILESTONE_ID,
        "generated_at": generated,
        "status": status,
        "broker": BROKER,
        "provider": "Shioaji",
        "environment": ENVIRONMENT,
        "account_status": account_status,
        "account_status_detail": account_status_detail,
        "place_result": place_result,
        "cancel_result": cancel_result,
        "readback_result": readback_result,
        "reconcile_result": reconcile_result,
        "production_live_enabled": False,
        "capital_binding_enabled": False,
        "human_gate_required": True,
        "fail_closed_posture": {
            "live_gate": live_gate,
            "no_real_capital": no_real_capital,
            "production_live_order_submitted": bool(no_real_capital.get("production_live_order_submitted", False)),
            "production_live_cancel_submitted": bool(no_real_capital.get("production_live_cancel_submitted", False)),
        },
        "proof_boundary": PROOF_BOUNDARY,
        "source_evidence_refs": [
            {
                "ref_type": "broker_sandbox_smoke_summary",
                "path": smoke_summary_ref,
                "source_task_id": smoke_summary.get("task_id"),
                "status": smoke_summary.get("status"),
                "run_mode": smoke_summary.get("run_mode"),
            }
        ],
        "canary_readiness_inputs": {
            "broker_sandbox_smoke_ref": smoke_summary_ref,
            "broker_sandbox_evidence_packet_ref": task_packet_ref,
            "human_gate_required": True,
            "risk_owner_approval_required": True,
            "operator_approval_required": True,
        },
        "acceptance_checks": checks,
    }
    ooda_packet = build_ooda_packet(
        generated_at=generated,
        task_packet_path=task_packet_ref,
        smoke_summary_path=smoke_summary_ref,
        packet_status=status,
    )
    packet["ooda_packet"] = ooda_packet
    packet["ooda_packet_validation_errors"] = validate_packet(ooda_packet)
    if packet["ooda_packet_validation_errors"]:
        packet["status"] = "incomplete"
    return packet


def write_packet_outputs(
    *,
    packet: dict[str, Any],
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    milestone_output: Path = DEFAULT_MILESTONE_OUTPUT,
) -> dict[str, str]:
    task_packet = output_dir / DEFAULT_PACKET_NAME
    dump_json(task_packet, packet)
    dump_json(milestone_output, packet)
    readme = output_dir / "README.md"

    def display_path(path: Path) -> str:
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return str(path)

    readme.write_text(
        "\n".join(
            [
                "# MGMT-BROKER-004 Shioaji Evidence Packet",
                "",
                "Generated by:",
                "",
                "```bash",
                (
                    "PYTHONPATH=. python3 services/broker/shioaji/evidence_packet.py "
                    "--smoke-summary-json support/evidence/MGMT-BROKER-003/summary.json"
                ),
                "```",
                "",
                "Evidence files:",
                "",
                f"- `{display_path(task_packet)}`",
                f"- `{display_path(milestone_output)}`",
                "",
                "Scope:",
                "",
                "- Shioaji sandbox evidence only.",
                "- Production live broker remains disabled.",
                "- Capital binding remains disabled.",
                "- Canary progression still requires risk-owner and operator approval.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "task_packet": str(task_packet),
        "milestone_output": str(milestone_output),
        "readme": str(readme),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the Shioaji sandbox evidence packet.")
    parser.add_argument("--smoke-summary-json", required=True, help="Path to a sandbox_smoke summary.json payload.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--milestone-output", default=str(DEFAULT_MILESTONE_OUTPUT))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--task-id", default=TASK_ID)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    smoke_path = Path(args.smoke_summary_json)
    output_dir = Path(args.output_dir)
    task_packet_path = output_dir / DEFAULT_PACKET_NAME
    smoke_summary = load_json(smoke_path)
    packet = build_evidence_packet(
        smoke_summary=smoke_summary,
        smoke_summary_path=str(smoke_path),
        task_packet_path=str(task_packet_path),
        generated_at=args.generated_at,
        task_id=args.task_id,
    )
    outputs = write_packet_outputs(
        packet=packet,
        output_dir=output_dir,
        milestone_output=Path(args.milestone_output),
    )
    print(json.dumps({"status": packet["status"], **outputs}, ensure_ascii=False))
    return 0 if packet["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
