"""EP5 canary closeout packet renderer.

The renderer consumes the EP5-009 canary observation report and produces a
deterministic closeout packet plus a reviewer-friendly Markdown summary. It is
pure for library callers: no broker calls, no runtime writes, and no shared
store mutation. The optional CLI writes only the requested output files.
"""
from __future__ import annotations

import argparse
import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.governance.promotion_readiness.packet_model import (
    BlockingReason,
    PromotionReadinessPacket,
)
from services.governance.promotion_readiness.validator import validate_readiness


CLOSEOUT_PACKET_VERSION = "2026-05-20.EP5-010.closeout"
CLOSEOUT_DEPENDS_ON_TASKS = ("EP5-002-V2", "EP5-005-V2", "EP5-009-V2")
PASSING_STATUS_VALUES = {"pass", "passed", "success", "succeeded", "ok", "ready"}


class EP5CloseoutRendererError(ValueError):
    """Raised when the closeout renderer input is structurally invalid."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise EP5CloseoutRendererError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise EP5CloseoutRendererError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EP5CloseoutRendererError(f"{field_name} must be an object")
    return copy.deepcopy(dict(value))


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, list):
        raise EP5CloseoutRendererError(f"{field_name} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        text = str(item or "").strip()
        if not text:
            raise EP5CloseoutRendererError(f"{field_name}[{index}] must not be empty")
        result.append(text)
    return result


def _status_passes(value: Any) -> bool:
    return str(value or "").strip().lower() in PASSING_STATUS_VALUES


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _reason_dict(
    item: BlockingReason | Mapping[str, Any] | str,
    *,
    source_ref: str,
) -> dict[str, Any]:
    if isinstance(item, BlockingReason):
        payload = item.to_dict()
    elif isinstance(item, Mapping):
        raw = dict(item)
        code = _required_text(raw.get("code") or raw.get("message"), "blocking_reasons[].code")
        message = _required_text(raw.get("message") or code, "blocking_reasons[].message")
        payload = {
            "code": code,
            "message": message,
            "severity": _optional_text(raw.get("severity")) or "blocking",
        }
        if raw.get("details"):
            payload["details"] = copy.deepcopy(raw["details"])
        if raw.get("source_ref"):
            payload["source_ref"] = raw["source_ref"]
    else:
        code = _required_text(item, "blocking_reasons[]")
        payload = {
            "code": code,
            "message": code,
            "severity": "blocking",
        }

    payload.setdefault("severity", "blocking")
    payload.setdefault("source_ref", source_ref)
    return payload


def _dedupe_reasons(reasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for reason in reasons:
        key = (str(reason.get("code") or ""), str(reason.get("message") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(reason)
    return deduped


def _evidence_summary(packet: PromotionReadinessPacket) -> dict[str, Any]:
    return {
        "required": list(packet.evidence.required),
        "provided": [item.to_dict() for item in packet.evidence.provided],
        "missing": list(packet.evidence.missing),
        "gate_results": [item.to_dict() for item in packet.evidence.gate_results],
    }


def _proof_flags(
    proof_packet: Mapping[str, Any],
    readiness_packet: PromotionReadinessPacket,
) -> dict[str, Any]:
    proof = _mapping(proof_packet.get("proof"), "proof_packet.proof")
    flags = readiness_packet.flags.values
    return {
        "deployment_stage_is_canary": flags.get("deployment_stage_is_canary"),
        "canary_runtime_started": proof.get("canary_runtime_started"),
        "runtime_heartbeat_received": proof.get("runtime_heartbeat_received"),
        "order_route_mode": proof.get("order_route_mode"),
        "order_route_mode_paper_safe": flags.get("order_route_mode"),
        "telemetry_ingested": proof.get("telemetry_ingested"),
        "rollback_drill_completed": proof.get("rollback_drill_completed"),
        "kill_switch_demo_completed": proof.get("kill_switch_demo_completed"),
        "audit_events_recorded": proof.get("audit_events_recorded"),
        "incident_path_tested": proof.get("incident_path_tested"),
        "live_capital_side_effects": proof.get("live_capital_side_effects"),
    }


def _fail_closed_summary(
    proof_flags: Mapping[str, Any],
    readiness_packet: PromotionReadinessPacket,
) -> dict[str, Any]:
    flags = readiness_packet.flags.values
    unsafe_true_flags = list(readiness_packet.flags.unsafe_true_flags())
    broker_live = _as_bool(
        flags.get("broker_production_live_enabled")
        or flags.get("BROKER_PRODUCTION_LIVE_ENABLED")
    )
    capital_live = _as_bool(
        flags.get("capital_binding_live_enabled")
        or flags.get("CAPITAL_BINDING_LIVE_ENABLED")
    )
    live_capital = _as_bool(
        proof_flags.get("live_capital_side_effects")
        or flags.get("live_capital_side_effects")
    )

    extra_failures: list[str] = []
    if broker_live and "broker_production_live_enabled" not in unsafe_true_flags:
        extra_failures.append("broker_production_live_enabled")
    if capital_live and "capital_binding_live_enabled" not in unsafe_true_flags:
        extra_failures.append("capital_binding_live_enabled")
    if live_capital and "live_capital_side_effects" not in unsafe_true_flags:
        extra_failures.append("live_capital_side_effects")

    return {
        "passed": not unsafe_true_flags and not extra_failures,
        "unsafe_true_flags": [*unsafe_true_flags, *extra_failures],
        "broker_production_live_enabled": broker_live,
        "capital_binding_live_enabled": capital_live,
        "live_capital_side_effects": live_capital,
        "deployment_stage": readiness_packet.target.deployment_stage,
        "order_route_mode": proof_flags.get("order_route_mode"),
    }


def build_ep5_closeout_packet(
    observation_report: Mapping[str, Any],
    *,
    packet_id: str | None = None,
    generated_at: str | None = None,
    generated_by: str = "EP5-010-V2/closeout_renderer",
    source_task_id: str = "EP5-010-V2",
) -> dict[str, Any]:
    """Build a JSON-serializable EP5 closeout packet.

    The input is the dictionary returned by
    ``build_canary_observation_report``. The returned packet is suitable for
    deterministic JSON rendering with ``render_ep5_closeout_json``.
    """

    if not isinstance(observation_report, Mapping):
        raise EP5CloseoutRendererError("observation_report must be an object")

    report = _mapping(observation_report, "observation_report")
    report_id = _required_text(report.get("report_id"), "observation_report.report_id")
    run_id = _required_text(report.get("run_id"), "observation_report.run_id")
    report_status = _required_text(report.get("status"), "observation_report.status").lower()
    proof_packet = _mapping(report.get("proof_packet"), "observation_report.proof_packet")
    proof_packet_id = _required_text(proof_packet.get("packet_id"), "proof_packet.packet_id")
    proof_status = _required_text(proof_packet.get("status"), "proof_packet.status").lower()
    readiness_payload = _mapping(
        report.get("promotion_readiness_packet"),
        "observation_report.promotion_readiness_packet",
    )

    try:
        readiness_packet = PromotionReadinessPacket.from_dict(readiness_payload, validate=False)
    except Exception as exc:  # pragma: no cover - exact model errors are covered upstream
        raise EP5CloseoutRendererError(str(exc)) from exc

    evidence_refs = _string_list(report.get("evidence_refs"), "observation_report.evidence_refs")
    observation_window = _mapping(report.get("observation_window"), "observation_report.observation_window")
    proof_flags = _proof_flags(proof_packet, readiness_packet)
    fail_closed = _fail_closed_summary(proof_flags, readiness_packet)

    reasons: list[dict[str, Any]] = []
    reasons.extend(
        _reason_dict(item, source_ref="observation_report.blocking_reasons")
        for item in _string_list(report.get("blocking_reasons"), "observation_report.blocking_reasons")
    )
    proof_result = _mapping(proof_packet.get("result"), "proof_packet.result")
    reasons.extend(
        _reason_dict(item, source_ref="proof_packet.result.blocking_reasons")
        for item in _string_list(proof_result.get("blocking_reasons"), "proof_packet.result.blocking_reasons")
    )
    reasons.extend(
        _reason_dict(item, source_ref="promotion_readiness_packet.blocking_reasons")
        for item in readiness_packet.blocking_reasons
    )
    reasons.extend(
        _reason_dict(item, source_ref="promotion_readiness.validator")
        for item in validate_readiness(readiness_packet)
    )

    if not _status_passes(report_status):
        reasons.append(
            _reason_dict(
                {
                    "code": "OBSERVATION_REPORT_NOT_PASSING",
                    "message": f"Observation report status is {report_status!r}",
                },
                source_ref="observation_report.status",
            )
        )
    if not _status_passes(proof_status):
        reasons.append(
            _reason_dict(
                {
                    "code": "PROOF_PACKET_NOT_PASSING",
                    "message": f"EP5 proof packet status is {proof_status!r}",
                },
                source_ref="proof_packet.status",
            )
        )
    if not readiness_packet.can_proceed:
        reasons.append(
            _reason_dict(
                {
                    "code": "PROMOTION_READINESS_NOT_PASSING",
                    "message": "PromotionReadinessPacket can_proceed is false",
                },
                source_ref="promotion_readiness_packet.can_proceed",
            )
        )
    if not fail_closed["passed"]:
        reasons.append(
            _reason_dict(
                {
                    "code": "FAIL_CLOSED_FLAG_ENABLED",
                    "message": "One or more fail-closed live/capital side-effect flags are true",
                    "details": {"unsafe_true_flags": fail_closed["unsafe_true_flags"]},
                    "severity": "critical",
                },
                source_ref="promotion_readiness_packet.flags",
            )
        )

    blocking_reasons = _dedupe_reasons(reasons)
    can_close_out = (
        not blocking_reasons
        and _status_passes(report_status)
        and _status_passes(proof_status)
        and readiness_packet.can_proceed
        and bool(fail_closed["passed"])
    )
    status = "passed" if can_close_out else "failed"
    if can_close_out:
        reason = (
            f"EP5 canary closeout passed for run={run_id!r}: observation, "
            "proof, readiness, and fail-closed checks are clean."
        )
    else:
        codes = ", ".join(reason["code"] for reason in blocking_reasons) or "unknown"
        reason = f"EP5 canary closeout blocked for run={run_id!r}: {codes}"

    return {
        "packet_id": packet_id or f"ep5-closeout-{uuid.uuid4().hex[:12]}",
        "packet_version": CLOSEOUT_PACKET_VERSION,
        "generated_at": generated_at or _utc_now_iso(),
        "generated_by": generated_by,
        "source_task_id": source_task_id,
        "depends_on_tasks": list(CLOSEOUT_DEPENDS_ON_TASKS),
        "status": status,
        "can_close_out": can_close_out,
        "reason": reason,
        "target": readiness_packet.target.to_dict(),
        "run": {
            "run_id": run_id,
            "report_id": report_id,
            "environment": readiness_packet.target.environment,
            "deployment_stage": readiness_packet.target.deployment_stage,
            "started_at": observation_window.get("started_at"),
            "ended_at": observation_window.get("ended_at"),
        },
        "inputs": {
            "observation_report_id": report_id,
            "proof_packet_id": proof_packet_id,
            "promotion_readiness_packet_id": readiness_packet.packet_id,
        },
        "observation": {
            "status": report_status,
            "telemetry": _mapping(report.get("telemetry"), "observation_report.telemetry"),
            "audit": _mapping(report.get("audit"), "observation_report.audit"),
            "incidents": _mapping(report.get("incidents"), "observation_report.incidents"),
            "reconciliation": _mapping(
                report.get("reconciliation"),
                "observation_report.reconciliation",
            ),
        },
        "proof": {
            "packet_id": proof_packet_id,
            "status": proof_status,
            "flags": proof_flags,
        },
        "promotion_readiness": {
            "packet_id": readiness_packet.packet_id,
            "can_proceed": readiness_packet.can_proceed,
            "reason": readiness_packet.reason,
            "evidence": _evidence_summary(readiness_packet),
            "approval": readiness_packet.approval.to_dict(),
            "flags": readiness_packet.flags.to_dict(),
        },
        "fail_closed": fail_closed,
        "evidence_refs": evidence_refs,
        "blocking_reasons": blocking_reasons,
    }


def render_ep5_closeout_json(packet: Mapping[str, Any]) -> str:
    """Render a closeout packet as deterministic pretty JSON."""

    return json.dumps(packet, indent=2, sort_keys=True) + "\n"


def _display(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(_display(item) for item in value) if value else "-"
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _escape_cell(value: Any) -> str:
    return _display(value).replace("|", "\\|").replace("\n", "<br>")


def _table(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_escape_cell(value) for value in row) + " |")
    return lines


def render_ep5_closeout_markdown(packet: Mapping[str, Any]) -> str:
    """Render a closeout packet as reviewer-friendly Markdown."""

    payload = _mapping(packet, "packet")
    run = _mapping(payload.get("run"), "packet.run")
    inputs = _mapping(payload.get("inputs"), "packet.inputs")
    proof = _mapping(payload.get("proof"), "packet.proof")
    proof_flags = _mapping(proof.get("flags"), "packet.proof.flags")
    observation = _mapping(payload.get("observation"), "packet.observation")
    readiness = _mapping(payload.get("promotion_readiness"), "packet.promotion_readiness")
    readiness_evidence = _mapping(readiness.get("evidence"), "packet.promotion_readiness.evidence")
    approval = _mapping(readiness.get("approval"), "packet.promotion_readiness.approval")
    fail_closed = _mapping(payload.get("fail_closed"), "packet.fail_closed")
    blocking_reasons = list(payload.get("blocking_reasons") or [])
    evidence_refs = list(payload.get("evidence_refs") or [])

    lines: list[str] = [
        "# EP5 Canary Closeout Packet",
        "",
        *(_table(
            ("Field", "Value"),
            [
                ("Status", payload.get("status")),
                ("Can close out", payload.get("can_close_out")),
                ("Packet ID", payload.get("packet_id")),
                ("Packet version", payload.get("packet_version")),
                ("Run ID", run.get("run_id")),
                ("Observation report", inputs.get("observation_report_id")),
                ("Proof packet", inputs.get("proof_packet_id")),
                ("Promotion readiness packet", inputs.get("promotion_readiness_packet_id")),
                ("Generated at", payload.get("generated_at")),
                ("Generated by", payload.get("generated_by")),
            ],
        )),
        "",
        "## Result",
        "",
        _display(payload.get("reason")),
        "",
        "## Observation",
        "",
        *(_table(
            ("Area", "Status", "Refs"),
            [
                (
                    "Telemetry",
                    _mapping(observation.get("telemetry"), "observation.telemetry").get("status"),
                    _mapping(observation.get("telemetry"), "observation.telemetry").get("refs", []),
                ),
                (
                    "Audit",
                    _mapping(observation.get("audit"), "observation.audit").get("status"),
                    _mapping(observation.get("audit"), "observation.audit").get("refs", []),
                ),
                (
                    "Incidents",
                    _mapping(observation.get("incidents"), "observation.incidents").get("status"),
                    _mapping(observation.get("incidents"), "observation.incidents").get("refs", []),
                ),
                (
                    "Reconciliation",
                    _mapping(observation.get("reconciliation"), "observation.reconciliation").get("status"),
                    _mapping(observation.get("reconciliation"), "observation.reconciliation").get("record_ref"),
                ),
            ],
        )),
        "",
        "## Proof Flags",
        "",
        *(_table(
            ("Flag", "Value"),
            [(key, value) for key, value in proof_flags.items()],
        )),
        "",
        "## Promotion Readiness",
        "",
        *(_table(
            ("Field", "Value"),
            [
                ("Can proceed", readiness.get("can_proceed")),
                ("Reason", readiness.get("reason")),
                ("Required evidence", readiness_evidence.get("required", [])),
                ("Missing evidence", readiness_evidence.get("missing", [])),
            ],
        )),
        "",
        "## Approval Gates",
        "",
        *(_table(
            ("Role", "Required", "Recorded", "State", "Source"),
            [
                (
                    "risk_owner",
                    _mapping(approval.get("risk_owner"), "approval.risk_owner").get("required"),
                    _mapping(approval.get("risk_owner"), "approval.risk_owner").get("recorded"),
                    _mapping(approval.get("risk_owner"), "approval.risk_owner").get("state"),
                    _mapping(approval.get("risk_owner"), "approval.risk_owner").get("source_ref"),
                ),
                (
                    "operator",
                    _mapping(approval.get("operator"), "approval.operator").get("required"),
                    _mapping(approval.get("operator"), "approval.operator").get("recorded"),
                    _mapping(approval.get("operator"), "approval.operator").get("state"),
                    _mapping(approval.get("operator"), "approval.operator").get("source_ref"),
                ),
            ],
        )),
        "",
        "## Fail-Closed Boundary",
        "",
        *(_table(
            ("Field", "Value"),
            [
                ("Passed", fail_closed.get("passed")),
                ("Unsafe true flags", fail_closed.get("unsafe_true_flags", [])),
                ("Broker production live enabled", fail_closed.get("broker_production_live_enabled")),
                ("Capital binding live enabled", fail_closed.get("capital_binding_live_enabled")),
                ("Live capital side effects", fail_closed.get("live_capital_side_effects")),
                ("Order route mode", fail_closed.get("order_route_mode")),
                ("Deployment stage", fail_closed.get("deployment_stage")),
            ],
        )),
        "",
        "## Blocking Reasons",
        "",
    ]

    if blocking_reasons:
        lines.extend(
            _table(
                ("Code", "Severity", "Source", "Message"),
                [
                    (
                        _mapping(reason, "blocking_reasons[]").get("code"),
                        _mapping(reason, "blocking_reasons[]").get("severity"),
                        _mapping(reason, "blocking_reasons[]").get("source_ref"),
                        _mapping(reason, "blocking_reasons[]").get("message"),
                    )
                    for reason in blocking_reasons
                ],
            )
        )
    else:
        lines.append("No blocking reasons.")

    lines.extend(["", "## Evidence References", ""])
    if evidence_refs:
        lines.extend(f"- `{ref}`" for ref in evidence_refs)
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This packet renders archived EP5 canary evidence only. It does not authorize broker-production live routing or live capital binding.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render an EP5 canary closeout packet")
    parser.add_argument("--input", required=True, help="EP5 observation report JSON path")
    parser.add_argument("--json-output", help="Optional closeout JSON output path")
    parser.add_argument("--markdown-output", help="Optional Markdown output path")
    parser.add_argument("--packet-id")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    observation_report = json.loads(input_path.read_text(encoding="utf-8"))
    packet = build_ep5_closeout_packet(observation_report, packet_id=args.packet_id)

    rendered_json = render_ep5_closeout_json(packet)
    if args.json_output:
        json_output = Path(args.json_output)
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(rendered_json, encoding="utf-8")
    else:
        print(rendered_json, end="")

    if args.markdown_output:
        markdown_output = Path(args.markdown_output)
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_ep5_closeout_markdown(packet), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
