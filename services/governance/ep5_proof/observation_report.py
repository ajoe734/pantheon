"""Canary observation report builder for EP5 proof packets.

The builder composes the observation-window references needed by the EP5
canary proof path:

* telemetry refs proving runtime heartbeat / event ingest
* audit refs proving operator-governance actions were recorded
* incident refs when the observation window opened or exercised an incident path
* reconciliation status and record refs

It is pure for library callers: no broker calls, no runtime writes, and no
shared store mutation. The optional CLI writes only the requested JSON output.
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.governance.ep5_proof.dry_run import EP5DryRunError, run_canary_dry_run


REPORT_VERSION = "2026-05-20.EP5-009.observation-report"

PASSING_RECONCILIATION_STATUSES = {
    "pass",
    "passed",
    "ok",
    "complete",
    "completed",
    "matched",
    "within_threshold",
    "no_drift",
}
BREACH_RECONCILIATION_STATUSES = {
    "breach",
    "breached",
    "threshold_breach",
    "threshold_breached",
    "drift",
    "drift_detected",
}


class CanaryObservationReportError(ValueError):
    """Raised when the observation report request is structurally invalid."""


class CanaryObservationReportRequest(BaseModel):
    """Input for building an EP5 canary observation report."""

    model_config = ConfigDict(extra="forbid")

    report_id: str | None = None
    proof_id: str | None = None
    promotion_readiness_packet_id: str | None = None
    run_id: str | None = None
    persona_id: str
    runtime_id: str
    runtime_binding_id: str
    artifact_id: str
    deployment_plan_id: str
    environment: Literal["canary"] = "canary"
    mode: Literal["validate_only", "sandbox"] = "validate_only"
    order_route_mode: Literal["validate_only", "sandbox"] | None = None
    runtime_started: bool = True
    runtime_heartbeat_received: bool | None = None
    telemetry_ingested: bool | None = None
    rollback_drill_completed: bool = False
    kill_switch_demo_completed: bool = False
    telemetry_refs: list[str] = Field(default_factory=list)
    audit_refs: list[str] = Field(default_factory=list)
    incident_refs: list[str] = Field(default_factory=list)
    reconciliation_status: str = "passed"
    reconciliation_record_ref: str | None = None
    drift_report_ref: str | None = None
    threshold_breached: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    live_capital_side_effects: Literal[False] = False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _required_text(value: str | None, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CanaryObservationReportError(f"{field_name} is required")
    return text


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_refs(values: list[str], field_name: str) -> list[str]:
    refs: list[str] = []
    for index, value in enumerate(values):
        text = str(value or "").strip()
        if not text:
            raise CanaryObservationReportError(f"{field_name}[{index}] must not be empty")
        refs.append(text)
    return refs


def _normalize_status(value: str) -> str:
    return str(value or "").strip().lower()


def _scoped_ref(prefix: str, value: str) -> str:
    text = value.strip()
    if text.startswith(f"{prefix}:"):
        return text
    return f"{prefix}:{text}"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _request_from_payload(
    payload: Mapping[str, Any] | CanaryObservationReportRequest,
) -> CanaryObservationReportRequest:
    if isinstance(payload, CanaryObservationReportRequest):
        return payload
    try:
        return CanaryObservationReportRequest.model_validate(dict(payload))
    except ValidationError as exc:
        raise CanaryObservationReportError(str(exc)) from exc


def _blocking_reasons(
    *,
    telemetry_refs: list[str],
    audit_refs: list[str],
    incident_refs: list[str],
    reconciliation_status: str,
    reconciliation_record_ref: str | None,
    threshold_breached: bool,
) -> list[str]:
    blocking: list[str] = []
    if not telemetry_refs:
        blocking.append("MISSING_TELEMETRY_REFS")
    if not audit_refs:
        blocking.append("MISSING_AUDIT_REFS")
    if not reconciliation_record_ref:
        blocking.append("MISSING_RECONCILIATION_RECORD_REF")
    if threshold_breached:
        blocking.append("RECONCILIATION_THRESHOLD_BREACH")
        if not incident_refs:
            blocking.append("INCIDENT_REF_REQUIRED")
    elif reconciliation_status not in PASSING_RECONCILIATION_STATUSES:
        blocking.append("RECONCILIATION_NOT_PASSING")
    return blocking


def _evidence_refs(
    request: CanaryObservationReportRequest,
    *,
    telemetry_refs: list[str],
    audit_refs: list[str],
    incident_refs: list[str],
    reconciliation_record_ref: str | None,
    drift_report_ref: str | None,
) -> list[str]:
    refs = _clean_refs(request.evidence_refs, "evidence_refs")
    refs.extend(_scoped_ref("telemetry", ref) for ref in telemetry_refs)
    refs.extend(_scoped_ref("audit", ref) for ref in audit_refs)
    refs.extend(_scoped_ref("incident", ref) for ref in incident_refs)
    if reconciliation_record_ref:
        refs.append(_scoped_ref("reconciliation", reconciliation_record_ref))
    if drift_report_ref:
        refs.append(_scoped_ref("drift-report", drift_report_ref))
    return _dedupe(refs)


def build_canary_observation_report(
    payload: Mapping[str, Any] | CanaryObservationReportRequest,
) -> dict[str, Any]:
    """Build an EP5 canary observation report and proof-packet evidence refs."""

    request = _request_from_payload(payload)
    report_id = _required_text(request.report_id or _new_id("canary-observation"), "report_id")
    run_id = _required_text(request.run_id or _new_id("canary-run"), "run_id")
    persona_id = _required_text(request.persona_id, "persona_id")
    runtime_id = _required_text(request.runtime_id, "runtime_id")
    runtime_binding_id = _required_text(request.runtime_binding_id, "runtime_binding_id")
    artifact_id = _required_text(request.artifact_id, "artifact_id")
    deployment_plan_id = _required_text(request.deployment_plan_id, "deployment_plan_id")
    started_at = request.started_at or _utc_now_iso()
    ended_at = request.ended_at or started_at

    telemetry_refs = _clean_refs(request.telemetry_refs, "telemetry_refs")
    audit_refs = _clean_refs(request.audit_refs, "audit_refs")
    incident_refs = _clean_refs(request.incident_refs, "incident_refs")
    reconciliation_status = _normalize_status(request.reconciliation_status)
    reconciliation_record_ref = _optional_text(request.reconciliation_record_ref)
    drift_report_ref = _optional_text(request.drift_report_ref)
    threshold_breached = (
        request.threshold_breached
        or reconciliation_status in BREACH_RECONCILIATION_STATUSES
    )
    evidence_refs = _evidence_refs(
        request,
        telemetry_refs=telemetry_refs,
        audit_refs=audit_refs,
        incident_refs=incident_refs,
        reconciliation_record_ref=reconciliation_record_ref,
        drift_report_ref=drift_report_ref,
    )

    report_blocking = _blocking_reasons(
        telemetry_refs=telemetry_refs,
        audit_refs=audit_refs,
        incident_refs=incident_refs,
        reconciliation_status=reconciliation_status,
        reconciliation_record_ref=reconciliation_record_ref,
        threshold_breached=threshold_breached,
    )

    telemetry_ingested = (
        bool(telemetry_refs) if request.telemetry_ingested is None else request.telemetry_ingested
    )
    runtime_heartbeat_received = (
        bool(telemetry_refs)
        if request.runtime_heartbeat_received is None
        else request.runtime_heartbeat_received
    )

    try:
        proof_bundle = run_canary_dry_run(
            {
                "proof_id": request.proof_id or f"ep5-proof-{report_id}",
                "promotion_readiness_packet_id": (
                    request.promotion_readiness_packet_id or f"prp-{report_id}"
                ),
                "run_id": run_id,
                "persona_id": persona_id,
                "runtime_id": runtime_id,
                "runtime_binding_id": runtime_binding_id,
                "artifact_id": artifact_id,
                "deployment_plan_id": deployment_plan_id,
                "environment": request.environment,
                "mode": request.mode,
                "order_route_mode": request.order_route_mode or request.mode,
                "runtime_started": request.runtime_started,
                "runtime_heartbeat_received": runtime_heartbeat_received,
                "telemetry_ingested": telemetry_ingested,
                "rollback_drill_completed": request.rollback_drill_completed,
                "kill_switch_demo_completed": request.kill_switch_demo_completed,
                "audit_events_recorded": bool(audit_refs),
                "incident_path_tested": bool(incident_refs),
                "evidence_refs": evidence_refs,
                "started_at": started_at,
                "ended_at": ended_at,
                "live_capital_side_effects": False,
            }
        )
    except EP5DryRunError as exc:
        raise CanaryObservationReportError(str(exc)) from exc

    proof_packet = dict(proof_bundle["proof_packet"])
    result = dict(proof_packet["result"])
    blocking_reasons = _dedupe([*result["blocking_reasons"], *report_blocking])
    passed = bool(result["pass"]) and not report_blocking
    result["pass"] = passed
    result["blocking_reasons"] = blocking_reasons
    result["evidence_refs"] = evidence_refs
    proof_packet["result"] = result
    proof_packet["status"] = "passed" if passed else "failed"

    return {
        "report_id": report_id,
        "report_version": REPORT_VERSION,
        "status": proof_packet["status"],
        "run_id": run_id,
        "generated_at": _utc_now_iso(),
        "runtime": {
            "runtime_id": runtime_id,
            "runtime_binding_id": runtime_binding_id,
            "artifact_id": artifact_id,
            "deployment_plan_id": deployment_plan_id,
        },
        "observation_window": {
            "started_at": started_at,
            "ended_at": ended_at,
        },
        "telemetry": {
            "status": "present" if telemetry_refs else "missing",
            "refs": telemetry_refs,
        },
        "audit": {
            "status": "present" if audit_refs else "missing",
            "refs": audit_refs,
        },
        "incidents": {
            "status": "observed" if incident_refs else "not_observed",
            "refs": incident_refs,
            "threshold_breached": threshold_breached,
        },
        "reconciliation": {
            "status": reconciliation_status,
            "record_ref": reconciliation_record_ref,
            "drift_report_ref": drift_report_ref,
            "threshold_breached": threshold_breached,
        },
        "evidence_refs": evidence_refs,
        "blocking_reasons": blocking_reasons,
        "proof_packet": proof_packet,
        "promotion_readiness_packet": proof_bundle["promotion_readiness_packet"],
        "canary_run": proof_bundle["canary_run"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an EP5 canary observation report")
    parser.add_argument("--output", help="Optional JSON output path")
    parser.add_argument("--report-id")
    parser.add_argument("--proof-id")
    parser.add_argument("--promotion-readiness-packet-id")
    parser.add_argument("--run-id")
    parser.add_argument("--persona-id", default="persona-ep5-demo")
    parser.add_argument("--runtime-id", default="rt-ep5-observation")
    parser.add_argument("--runtime-binding-id", default="rb-ep5-observation")
    parser.add_argument("--artifact-id", default="artifact-ep5-demo")
    parser.add_argument("--deployment-plan-id", default="deployment-plan-ep5-observation")
    parser.add_argument("--mode", choices=("validate_only", "sandbox"), default="validate_only")
    parser.add_argument("--telemetry-ref", action="append", default=[])
    parser.add_argument("--audit-ref", action="append", default=[])
    parser.add_argument("--incident-ref", action="append", default=[])
    parser.add_argument("--evidence-ref", action="append", default=[])
    parser.add_argument("--reconciliation-status", default="passed")
    parser.add_argument("--reconciliation-record-ref")
    parser.add_argument("--drift-report-ref")
    parser.add_argument("--threshold-breached", action="store_true")
    parser.add_argument("--rollback-drill-completed", action="store_true")
    parser.add_argument("--kill-switch-demo-completed", action="store_true")
    parser.add_argument("--started-at")
    parser.add_argument("--ended-at")
    args = parser.parse_args(argv)

    payload = {
        "report_id": args.report_id,
        "proof_id": args.proof_id,
        "promotion_readiness_packet_id": args.promotion_readiness_packet_id,
        "run_id": args.run_id,
        "persona_id": args.persona_id,
        "runtime_id": args.runtime_id,
        "runtime_binding_id": args.runtime_binding_id,
        "artifact_id": args.artifact_id,
        "deployment_plan_id": args.deployment_plan_id,
        "mode": args.mode,
        "telemetry_refs": args.telemetry_ref,
        "audit_refs": args.audit_ref,
        "incident_refs": args.incident_ref,
        "evidence_refs": args.evidence_ref,
        "reconciliation_status": args.reconciliation_status,
        "reconciliation_record_ref": args.reconciliation_record_ref,
        "drift_report_ref": args.drift_report_ref,
        "threshold_breached": args.threshold_breached,
        "rollback_drill_completed": args.rollback_drill_completed,
        "kill_switch_demo_completed": args.kill_switch_demo_completed,
        "started_at": args.started_at,
        "ended_at": args.ended_at,
    }
    result = build_canary_observation_report(payload)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
