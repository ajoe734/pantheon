#!/usr/bin/env python3
"""Validate a runtime-manager-originated EP5 live order/cancel evidence packet."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_JSON_FILES = {
    "runtime_manager_command_envelope": "runtime-manager-command-envelope.dry-run.json",
    "ibkr_packet_manifest": "ibkr-packet-manifest.json",
    "runtime_lifecycle_schema": "runtime-manager-lifecycle.schema.json",
    "order_submit_request": "live-order-submit.request.json",
    "order_submit_response": "live-order-submit.response.json",
    "order_cancel_request": "live-order-cancel.request.json",
    "order_cancel_response": "live-order-cancel.response.json",
    "telemetry_trace_response": "telemetry-event-trace.response.json",
    "runtime_manager_excerpt": "runtime-manager-event-excerpt.json",
}

REQUIRED_TEXT_FILES = {
    "operator_checklist": "operator-checklist.md",
    "validator_expectations": "validator-expectations.md",
    "closeout_template": "closeout-template.md",
    "operator_note": "operator-note.md",
}

TWS_EVIDENCE_FILES = (
    "tws-open-order-transcript.md",
    "tws-open-order-screenshot.png",
    "tws-open-order-screenshot.jpg",
    "tws-open-order-screenshot.jpeg",
)

ACCEPTED_ORDER_STATES = {
    "accepted",
    "open",
    "submitted",
    "presubmitted",
    "pendingcancel",
}
TERMINAL_CANCEL_STATES = {
    "cancelled",
    "canceled",
    "inactive",
    "apicancelled",
    "cancel_confirmed",
}


def packet_readme() -> str:
    return "\n".join(
        [
            "# EP5-002 Live Order / Cancel Evidence Packet",
            "",
            "Status: pending operator capture",
            "",
            "This folder is a capture kit. It is not EP5-002 proof until the",
            "placeholder response files are replaced with real broker, runtime,",
            "telemetry, TWS, and operator evidence and the validator passes.",
            "The live side effect must originate from runtime-manager after",
            "explicit human approval; the dry-run command envelope in this",
            "packet is a template only.",
            "",
            "Required files:",
            "",
            "- runtime-manager-command-envelope.dry-run.json",
            "- ibkr-packet-manifest.json",
            "- runtime-manager-lifecycle.schema.json",
            "- operator-checklist.md",
            "- validator-expectations.md",
            "- closeout-template.md",
            "- live-order-submit.request.json",
            "- live-order-submit.response.json",
            "- live-order-cancel.request.json",
            "- live-order-cancel.response.json",
            "- telemetry-event-trace.response.json",
            "- runtime-manager-event-excerpt.json",
            "- tws-open-order-transcript.md or tws-open-order-screenshot.{png,jpg,jpeg}",
            "- operator-note.md",
            "",
            "Use the record-* commands documented in",
            "docs/deployment/ibkr-minimal-live-order-cancel-manual.md to fill",
            "this packet one observed fact at a time.",
            "",
        ]
    )


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def payload_body(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("body")
    if isinstance(body, dict):
        return body
    nested = payload.get("payload")
    if isinstance(nested, dict):
        return nested
    return payload


def normalize_state(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def first_value(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    nested = payload.get("payload")
    if isinstance(nested, dict):
        for key in keys:
            if key in nested:
                return nested[key]
    return None


def as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return "<" in value and ">" in value
    if isinstance(value, dict):
        return any(contains_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_placeholder(item) for item in value)
    return False


def json_contains_value(value: Any, expected: Any) -> bool:
    if expected is None:
        return False
    expected_text = str(expected)
    if isinstance(value, dict):
        return any(json_contains_value(item, expected_text) for item in value.values())
    if isinstance(value, list):
        return any(json_contains_value(item, expected_text) for item in value)
    return str(value) == expected_text


def json_contains_token(value: Any, tokens: set[str]) -> bool:
    if isinstance(value, dict):
        return any(json_contains_token(item, tokens) for item in value.values())
    if isinstance(value, list):
        return any(json_contains_token(item, tokens) for item in value)
    normalized = normalize_state(value)
    return any(token in normalized for token in tokens)


def normalized_equal(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def first_existing_tws_evidence(packet_dir: Path) -> Path | None:
    for filename in TWS_EVIDENCE_FILES:
        path = packet_dir / filename
        if path.exists():
            return path
    return None


def latest_ib_read_only_summary(packet_dir: Path) -> dict[str, Any] | None:
    summaries: list[dict[str, Any]] = []
    for path in packet_dir.glob("read-only-verify-*/summary.json"):
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") == "ib_read_only_verified":
            payload["_source_path"] = str(path)
            summaries.append(payload)
    if not summaries:
        return None
    return sorted(summaries, key=lambda item: str(item.get("generated_at") or ""))[-1]


def ib_read_only_summary_confirms_absent_no_fill(
    summary: dict[str, Any] | None,
    *,
    submit_order_id: Any,
    submit_request: dict[str, Any],
) -> bool:
    if not summary or not submit_order_id:
        return False
    target = summary.get("target") or {}
    session = summary.get("session") or {}
    open_orders = summary.get("open_orders") or {}
    executions = summary.get("executions") or {}
    target_matches = (
        normalized_equal(target.get("order_id"), submit_order_id)
        and normalized_equal(target.get("account"), submit_request.get("account"))
        and str(target.get("symbol") or "").upper() == str(submit_request.get("symbol") or "").upper()
    )
    return (
        target_matches
        and session.get("status") == "ok"
        and session.get("account_ref_present") is True
        and open_orders.get("status") == "ok"
        and open_orders.get("open_order_count") == 0
        and executions.get("status") == "ok"
        and executions.get("fill_status") == "no_matching_executions"
        and executions.get("matching_execution_count") == 0
        and as_number(executions.get("matching_shares")) == 0.0
    )


def packet_path(packet_dir: Path, key: str) -> Path:
    return packet_dir / REQUIRED_JSON_FILES[key]


def submit_order_id(packet_dir: Path) -> Any:
    response_path = packet_path(packet_dir, "order_submit_response")
    if not response_path.exists():
        return None
    return first_value(load_json(response_path), ("order_id", "broker_order_id", "perm_id"))


def init_packet(
    packet_dir: Path,
    *,
    account: str,
    limit_price: str,
    runtime_binding_id: str,
    deployment_plan_id: str,
    operator_id: str,
) -> dict[str, Any]:
    generated_at = iso_now()
    packet_dir.mkdir(parents=True, exist_ok=True)
    write_text(packet_dir / "README.md", packet_readme())
    dump_json(
        packet_dir / REQUIRED_JSON_FILES["runtime_manager_command_envelope"],
        {
            "status": "dry_run_template_only",
            "origin_service": "runtime-manager",
            "command_type": "runtime_manager.live_canary_order.submit_cancel",
            "dry_run": True,
            "requires_explicit_human_approval": True,
            "human_approval_ref": "pending_human_approval",
            "operator_id": operator_id,
            "runtime_binding_id": runtime_binding_id,
            "deployment_plan_id": deployment_plan_id,
            "target_stage": "operator_to_select_canary_or_live",
            "idempotency_key": "pending_operator_approved_idempotency_key",
            "payload_refs": {
                "submit_request": REQUIRED_JSON_FILES["order_submit_request"],
                "cancel_request": REQUIRED_JSON_FILES["order_cancel_request"],
            },
            "side_effect_boundary": "template_only_no_broker_side_effect",
        },
    )
    dump_json(
        packet_dir / REQUIRED_JSON_FILES["ibkr_packet_manifest"],
        {
            "packet_type": "ep5_runtime_manager_live_canary_order_cancel",
            "broker": "IBKR",
            "origin_service": "runtime-manager",
            "runtime_binding_id": runtime_binding_id,
            "deployment_plan_id": deployment_plan_id,
            "operator_id": operator_id,
            "required_files": [
                *REQUIRED_JSON_FILES.values(),
                *REQUIRED_TEXT_FILES.values(),
                "tws-open-order-transcript.md or tws-open-order-screenshot.{png,jpg,jpeg}",
            ],
            "guardrails": {
                "symbol": "AAPL",
                "quantity": 1,
                "order_type": "LMT",
                "outside_rth": False,
                "submit_after_human_approval_only": True,
                "cancel_immediately_after_accepted_or_open": True,
            },
        },
    )
    dump_json(
        packet_dir / REQUIRED_JSON_FILES["runtime_lifecycle_schema"],
        {
            "schema": "runtime_manager_live_canary_order_lifecycle_v1",
            "origin_service": "runtime-manager",
            "required_events": [
                "human_approval_archived",
                "live_order_submit_requested",
                "live_order_submitted",
                "live_order_cancel_requested",
                "live_order_cancelled_or_fill_recorded",
                "telemetry_trace_archived",
                "closeout_archived",
            ],
            "required_refs": [
                "runtime_binding_id",
                "deployment_plan_id",
                "operator_id",
                "broker_order_id_or_perm_id",
                "telemetry_event_id",
            ],
        },
    )
    dump_json(
        packet_dir / REQUIRED_JSON_FILES["order_submit_request"],
        {
            "body": {
                "account": account,
                "symbol": "AAPL",
                "security_type": "STK",
                "exchange": "SMART",
                "currency": "USD",
                "action": "BUY",
                "quantity": 1,
                "order_type": "LMT",
                "limit_price": limit_price,
                "time_in_force": "DAY",
                "outside_rth": False,
            },
            "capture_note": "Operator must confirm this limit does not cross the market before submission.",
        },
    )
    dump_json(
        packet_dir / REQUIRED_JSON_FILES["order_submit_response"],
        {
            "order_id": "<broker-order-id-after-submit>",
            "order_status": "<Submitted/Open/Accepted>",
            "captured_at": "<UTC timestamp>",
        },
    )
    dump_json(
        packet_dir / REQUIRED_JSON_FILES["order_cancel_request"],
        {"body": {"order_id": "<broker-order-id-after-submit>"}},
    )
    dump_json(
        packet_dir / REQUIRED_JSON_FILES["order_cancel_response"],
        {
            "order_id": "<broker-order-id-after-submit>",
            "order_status": "<Cancelled/Canceled>",
            "captured_at": "<UTC timestamp>",
        },
    )
    dump_json(
        packet_dir / REQUIRED_JSON_FILES["telemetry_trace_response"],
        {
            "target_type": "telemetry_event",
            "target_id": "<telemetry-event-id>",
            "refs": {
                "runtime_binding_ids": [runtime_binding_id],
                "deployment_plan_ids": [deployment_plan_id],
            },
        },
    )
    dump_json(
        packet_dir / REQUIRED_JSON_FILES["runtime_manager_excerpt"],
        {
            "runtime_binding_id": runtime_binding_id,
            "deployment_plan_id": deployment_plan_id,
            "operator_id": operator_id,
            "events": [
                {
                    "event_type": "<live_order_submitted>",
                    "order_id": "<broker-order-id-after-submit>",
                    "captured_at": "<UTC timestamp>",
                },
                {
                    "event_type": "<live_order_cancelled>",
                    "order_id": "<broker-order-id-after-submit>",
                    "captured_at": "<UTC timestamp>",
                },
            ],
        },
    )
    write_text(
        packet_dir / REQUIRED_TEXT_FILES["operator_checklist"],
        "\n".join(
            [
                "# Operator Checklist",
                "",
                "- human_approval_ref: pending_human_approval",
                "- runtime-manager health confirmed: pending",
                "- runtime binding confirmed: " + runtime_binding_id,
                "- deployment plan confirmed: " + deployment_plan_id,
                "- IBKR account confirmed: " + account,
                "- kill switch state known and actionable: pending",
                "- limit price confirmed non-marketable: pending",
                "- TWS watched during submit/cancel: pending",
                "- no live order submitted before explicit approval: yes",
                "",
            ]
        ),
    )
    write_text(
        packet_dir / REQUIRED_TEXT_FILES["validator_expectations"],
        "\n".join(
            [
                "# Validator Expectations",
                "",
                "- runtime-manager command envelope names runtime-manager as origin_service",
                "- operator checklist records human approval and pre-submit guardrails",
                "- IBKR manifest pins symbol AAPL, quantity 1, LMT, DAY, outside_rth=false",
                "- broker submit response acknowledges the live/canary order",
                "- cancel response or read-only verification resolves the same order without fill",
                "- telemetry trace includes runtime_binding_id, deployment_plan_id, and order id",
                "- runtime-manager excerpt includes submit and cancel/fill lifecycle events",
                "- closeout states canceled, filled, partially_filled, or otherwise_resolved",
                "",
            ]
        ),
    )
    write_text(
        packet_dir / REQUIRED_TEXT_FILES["closeout_template"],
        "\n".join(
            [
                "# EP5-002 Closeout",
                "",
                "- final_disposition: pending",
                "- broker_order_id: pending_submit_response",
                "- telemetry_event_id: pending_telemetry_trace",
                "- runtime_binding_id: " + runtime_binding_id,
                "- deployment_plan_id: " + deployment_plan_id,
                "- operator_id: " + operator_id,
                "- rollback_or_stop_action: pending",
                "- evidence_validation: pending_validation_artifact",
                "",
            ]
        ),
    )
    write_text(
        packet_dir / "tws-open-order-transcript.md",
        "\n".join(
            [
                "# TWS Open Order Transcript",
                "",
                "- observed_at: <UTC timestamp>",
                "- symbol: AAPL",
                "- order_id: <broker-order-id-after-submit>",
                "- state: <Submitted/Open/Accepted>",
                "- operator: " + operator_id,
                "",
            ]
        ),
    )
    write_text(
        packet_dir / REQUIRED_TEXT_FILES["operator_note"],
        "\n".join(
            [
                "# Operator Note",
                "",
                "- operator_id: " + operator_id,
                "- submitted_at: <UTC timestamp>",
                "- canceled_at: <UTC timestamp>",
                "- fill_disposition: not filled",
                "- note: Operator confirmed the live order was canceled and not filled.",
                "",
            ]
        ),
    )
    return {
        "task_id": "EP5-002",
        "generated_at": generated_at,
        "status": "initialized",
        "packet_dir": str(packet_dir),
        "next_step": "Replace placeholders with real broker/runtime/telemetry evidence, then run validate.",
    }


def record_submit_request(
    packet_dir: Path,
    *,
    account: str,
    limit_price: str,
) -> dict[str, Any]:
    dump_json(
        packet_path(packet_dir, "order_submit_request"),
        {
            "body": {
                "account": account,
                "symbol": "AAPL",
                "security_type": "STK",
                "exchange": "SMART",
                "currency": "USD",
                "action": "BUY",
                "quantity": 1,
                "order_type": "LMT",
                "limit_price": limit_price,
                "time_in_force": "DAY",
                "outside_rth": False,
            },
            "capture_note": "Operator confirmed this limit did not cross the market before submission.",
        },
    )
    return {"status": "recorded", "packet_dir": str(packet_dir), "file": REQUIRED_JSON_FILES["order_submit_request"]}


def record_submit_response(
    packet_dir: Path,
    *,
    order_id: str,
    status: str,
    captured_at: str | None = None,
    broker_order_id: str | None = None,
    perm_id: str | None = None,
) -> dict[str, Any]:
    recorded_at = captured_at or iso_now()
    response: dict[str, Any] = {
        "order_id": order_id,
        "order_status": status,
        "captured_at": recorded_at,
    }
    if broker_order_id:
        response["broker_order_id"] = broker_order_id
    if perm_id:
        response["perm_id"] = perm_id
    dump_json(packet_path(packet_dir, "order_submit_response"), response)
    dump_json(packet_path(packet_dir, "order_cancel_request"), {"body": {"order_id": order_id}})
    return {"status": "recorded", "packet_dir": str(packet_dir), "file": REQUIRED_JSON_FILES["order_submit_response"]}


def record_cancel_response(
    packet_dir: Path,
    *,
    status: str,
    captured_at: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    observed_order_id = order_id or submit_order_id(packet_dir)
    response = {
        "order_id": observed_order_id,
        "order_status": status,
        "captured_at": captured_at or iso_now(),
    }
    dump_json(packet_path(packet_dir, "order_cancel_response"), response)
    return {"status": "recorded", "packet_dir": str(packet_dir), "file": REQUIRED_JSON_FILES["order_cancel_response"]}


def record_telemetry_trace(
    packet_dir: Path,
    *,
    event_id: str,
    runtime_binding_id: str,
    deployment_plan_id: str,
    order_id: str | None = None,
    perm_id: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    observed_order_id = order_id or submit_order_id(packet_dir)
    event: dict[str, Any] = {
        "target_type": "telemetry_event",
        "target_id": event_id,
        "event_type": "live_order_absent_no_fill_verified",
        "broker": "IBKR",
        "order_id": observed_order_id,
        "observed_at": observed_at or iso_now(),
        "refs": {
            "runtime_binding_ids": [runtime_binding_id],
            "deployment_plan_ids": [deployment_plan_id],
        },
    }
    if perm_id:
        event["perm_id"] = perm_id
    dump_json(
        packet_path(packet_dir, "telemetry_trace_response"),
        event,
    )
    return {"status": "recorded", "packet_dir": str(packet_dir), "file": REQUIRED_JSON_FILES["telemetry_trace_response"]}


def record_runtime_excerpt(
    packet_dir: Path,
    *,
    runtime_binding_id: str,
    deployment_plan_id: str,
    operator_id: str,
    submitted_at: str | None = None,
    canceled_at: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    observed_order_id = order_id or submit_order_id(packet_dir)
    now = iso_now()
    dump_json(
        packet_path(packet_dir, "runtime_manager_excerpt"),
        {
            "runtime_binding_id": runtime_binding_id,
            "deployment_plan_id": deployment_plan_id,
            "operator_id": operator_id,
            "events": [
                {
                    "event_type": "live_order_submitted",
                    "order_id": observed_order_id,
                    "captured_at": submitted_at or now,
                },
                {
                    "event_type": "live_order_cancelled",
                    "order_id": observed_order_id,
                    "captured_at": canceled_at or now,
                },
            ],
        },
    )
    return {
        "status": "recorded",
        "packet_dir": str(packet_dir),
        "file": REQUIRED_JSON_FILES["runtime_manager_excerpt"],
    }


def record_tws_transcript(
    packet_dir: Path,
    *,
    state: str,
    operator_id: str,
    observed_at: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    observed_order_id = order_id or submit_order_id(packet_dir)
    write_text(
        packet_dir / "tws-open-order-transcript.md",
        "\n".join(
            [
                "# TWS Open Order Transcript",
                "",
                f"- observed_at: {observed_at or iso_now()}",
                "- symbol: AAPL",
                f"- order_id: {observed_order_id}",
                f"- state: {state}",
                f"- operator: {operator_id}",
                "",
                f"Operator observed AAPL order {observed_order_id} as {state} in TWS.",
                "",
            ]
        ),
    )
    return {"status": "recorded", "packet_dir": str(packet_dir), "file": "tws-open-order-transcript.md"}


def record_operator_note(
    packet_dir: Path,
    *,
    operator_id: str,
    submitted_at: str | None = None,
    canceled_at: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    observed_order_id = order_id or submit_order_id(packet_dir)
    write_text(
        packet_dir / REQUIRED_TEXT_FILES["operator_note"],
        "\n".join(
            [
                "# Operator Note",
                "",
                f"- operator_id: {operator_id}",
                f"- order_id: {observed_order_id}",
                f"- submitted_at: {submitted_at or iso_now()}",
                f"- canceled_at: {canceled_at or iso_now()}",
                "- fill_disposition: not filled",
                "- note: Operator confirmed the live order was canceled and not filled.",
                "",
            ]
        ),
    )
    return {"status": "recorded", "packet_dir": str(packet_dir), "file": REQUIRED_TEXT_FILES["operator_note"]}


def validate_packet(packet_dir: Path) -> dict[str, Any]:
    missing_files = [
        filename
        for filename in [*REQUIRED_JSON_FILES.values(), *REQUIRED_TEXT_FILES.values()]
        if not (packet_dir / filename).exists()
    ]
    tws_evidence_path = first_existing_tws_evidence(packet_dir)
    checks: list[dict[str, Any]] = []
    if missing_files:
        checks.append(
            {
                "name": "required_files_present",
                "status": "fail",
                "detail": "missing: " + ", ".join(missing_files),
            }
        )
    checks.append(
        {
            "name": "tws_open_order_evidence_present",
            "status": "pass" if tws_evidence_path else "fail",
            "detail": str(tws_evidence_path) if tws_evidence_path else "missing TWS transcript or screenshot",
        }
    )
    if missing_files:
        return {
            "generated_at": iso_now(),
            "status": "failed",
            "packet_dir": str(packet_dir),
            "checks": checks,
        }

    submit_request_payload = load_json(packet_dir / REQUIRED_JSON_FILES["order_submit_request"])
    command_envelope = load_json(packet_dir / REQUIRED_JSON_FILES["runtime_manager_command_envelope"])
    packet_manifest = load_json(packet_dir / REQUIRED_JSON_FILES["ibkr_packet_manifest"])
    lifecycle_schema = load_json(packet_dir / REQUIRED_JSON_FILES["runtime_lifecycle_schema"])
    submit_request = payload_body(submit_request_payload)
    submit_response = load_json(packet_dir / REQUIRED_JSON_FILES["order_submit_response"])
    cancel_request_payload = load_json(packet_dir / REQUIRED_JSON_FILES["order_cancel_request"])
    cancel_request = payload_body(cancel_request_payload)
    cancel_response = load_json(packet_dir / REQUIRED_JSON_FILES["order_cancel_response"])
    trace_response = load_json(packet_dir / REQUIRED_JSON_FILES["telemetry_trace_response"])
    runtime_excerpt = load_json(packet_dir / REQUIRED_JSON_FILES["runtime_manager_excerpt"])
    operator_checklist = (packet_dir / REQUIRED_TEXT_FILES["operator_checklist"]).read_text(encoding="utf-8").strip()
    validator_expectations = (
        packet_dir / REQUIRED_TEXT_FILES["validator_expectations"]
    ).read_text(encoding="utf-8").strip()
    closeout_template = (packet_dir / REQUIRED_TEXT_FILES["closeout_template"]).read_text(encoding="utf-8").strip()
    operator_note = (packet_dir / REQUIRED_TEXT_FILES["operator_note"]).read_text(encoding="utf-8").strip()
    packet_values = [
        command_envelope,
        packet_manifest,
        lifecycle_schema,
        submit_request_payload,
        submit_response,
        cancel_request_payload,
        cancel_response,
        trace_response,
        runtime_excerpt,
        operator_checklist,
        validator_expectations,
        closeout_template,
        operator_note,
    ]
    if tws_evidence_path and tws_evidence_path.suffix.lower() == ".md":
        packet_values.append(tws_evidence_path.read_text(encoding="utf-8"))

    placeholder_free = not any(contains_placeholder(value) for value in packet_values)
    checks.append(
        {
            "name": "placeholders_replaced",
            "status": "pass" if placeholder_free else "fail",
            "detail": "packet contains no <placeholder> values",
        }
    )

    limit_price = as_number(submit_request.get("limit_price"))

    envelope_refs = command_envelope.get("payload_refs") if isinstance(command_envelope.get("payload_refs"), dict) else {}
    checks.append(
        {
            "name": "runtime_manager_command_envelope_archived",
            "status": "pass"
            if command_envelope.get("origin_service") == "runtime-manager"
            and str(command_envelope.get("command_type") or "").startswith("runtime_manager.")
            and command_envelope.get("requires_explicit_human_approval") is True
            and command_envelope.get("runtime_binding_id")
            and command_envelope.get("deployment_plan_id")
            and envelope_refs.get("submit_request") == REQUIRED_JSON_FILES["order_submit_request"]
            and envelope_refs.get("cancel_request") == REQUIRED_JSON_FILES["order_cancel_request"]
            else "fail",
            "detail": {
                "origin_service": command_envelope.get("origin_service"),
                "command_type": command_envelope.get("command_type"),
                "requires_explicit_human_approval": command_envelope.get("requires_explicit_human_approval"),
                "runtime_binding_id": command_envelope.get("runtime_binding_id"),
                "deployment_plan_id": command_envelope.get("deployment_plan_id"),
            },
        }
    )

    request_guardrails = {
        "account": bool(submit_request.get("account")),
        "symbol": submit_request.get("symbol") == "AAPL",
        "security_type": str(submit_request.get("security_type") or "").upper() == "STK",
        "exchange": str(submit_request.get("exchange") or "").upper() == "SMART",
        "currency": str(submit_request.get("currency") or "").upper() == "USD",
        "action": str(submit_request.get("action") or "").upper() == "BUY",
        "quantity": submit_request.get("quantity") == 1,
        "order_type": str(submit_request.get("order_type") or "").upper() == "LMT",
        "time_in_force": str(submit_request.get("time_in_force") or "").upper() == "DAY",
        "outside_rth": submit_request.get("outside_rth") is False,
        "limit_price": limit_price is not None and limit_price > 0,
    }
    checks.append(
        {
            "name": "minimal_live_order_guardrails",
            "status": "pass" if all(request_guardrails.values()) else "fail",
            "detail": request_guardrails,
        }
    )

    manifest_guardrails = packet_manifest.get("guardrails") if isinstance(packet_manifest.get("guardrails"), dict) else {}
    checks.append(
        {
            "name": "ibkr_packet_manifest_matches_guardrails",
            "status": "pass"
            if packet_manifest.get("broker") == "IBKR"
            and packet_manifest.get("origin_service") == "runtime-manager"
            and manifest_guardrails.get("symbol") == "AAPL"
            and manifest_guardrails.get("quantity") == 1
            and str(manifest_guardrails.get("order_type") or "").upper() == "LMT"
            and manifest_guardrails.get("outside_rth") is False
            and manifest_guardrails.get("submit_after_human_approval_only") is True
            else "fail",
            "detail": {
                "broker": packet_manifest.get("broker"),
                "origin_service": packet_manifest.get("origin_service"),
                "guardrails": manifest_guardrails,
            },
        }
    )

    required_events = set(lifecycle_schema.get("required_events") or [])
    checks.append(
        {
            "name": "runtime_lifecycle_schema_declares_closeout_path",
            "status": "pass"
            if lifecycle_schema.get("origin_service") == "runtime-manager"
            and {"human_approval_archived", "live_order_submitted", "telemetry_trace_archived", "closeout_archived"}.issubset(required_events)
            else "fail",
            "detail": {
                "origin_service": lifecycle_schema.get("origin_service"),
                "required_events": sorted(required_events),
            },
        }
    )

    submit_state = normalize_state(
        first_value(submit_response, ("order_status", "status", "state", "broker_status"))
    )
    checks.append(
        {
            "name": "broker_acknowledged_live_order",
            "status": "pass" if submit_state in ACCEPTED_ORDER_STATES else "fail",
            "detail": {"observed_state": submit_state},
        }
    )

    submit_order_id = first_value(submit_response, ("order_id", "broker_order_id", "perm_id"))
    cancel_order_id = first_value(cancel_request, ("order_id", "broker_order_id", "perm_id"))
    checks.append(
        {
            "name": "cancel_targets_same_order",
            "status": "pass" if submit_order_id and cancel_order_id == submit_order_id else "fail",
            "detail": {"submit_order_id": submit_order_id, "cancel_order_id": cancel_order_id},
        }
    )

    cancel_state = normalize_state(
        first_value(cancel_response, ("order_status", "status", "state", "broker_status"))
    )
    ib_readback_summary = latest_ib_read_only_summary(packet_dir)
    ib_absent_no_fill = ib_read_only_summary_confirms_absent_no_fill(
        ib_readback_summary,
        submit_order_id=submit_order_id,
        submit_request=submit_request,
    )
    checks.append(
        {
            "name": "broker_confirmed_cancel",
            "status": "pass" if cancel_state in TERMINAL_CANCEL_STATES or ib_absent_no_fill else "fail",
            "detail": {
                "observed_state": cancel_state,
                "proof_source": "cancel_response"
                if cancel_state in TERMINAL_CANCEL_STATES
                else "ib_read_only_absent_no_fill"
                if ib_absent_no_fill
                else "none",
                "ib_read_only_summary": (ib_readback_summary or {}).get("_source_path"),
            },
        }
    )

    trace_refs = trace_response.get("refs") or {}
    checks.append(
        {
            "name": "telemetry_trace_archived",
            "status": "pass"
            if trace_response.get("target_type") == "telemetry_event"
            and trace_refs.get("runtime_binding_ids")
            and trace_refs.get("deployment_plan_ids")
            and submit_order_id
            and json_contains_value(trace_response, submit_order_id)
            else "fail",
            "detail": {
                "target_type": trace_response.get("target_type"),
                "runtime_binding_ids": trace_refs.get("runtime_binding_ids") or [],
                "deployment_plan_ids": trace_refs.get("deployment_plan_ids") or [],
                "submit_order_id": submit_order_id,
            },
        }
    )

    checks.append(
        {
            "name": "runtime_manager_lifecycle_archived",
            "status": "pass"
            if submit_order_id
            and json_contains_value(runtime_excerpt, submit_order_id)
            and json_contains_token(runtime_excerpt, {"submit", "accepted", "open"})
            and json_contains_token(runtime_excerpt, {"cancel"})
            else "fail",
            "detail": {"submit_order_id": submit_order_id},
        }
    )

    tws_detail: dict[str, Any] = {"path": str(tws_evidence_path) if tws_evidence_path else None}
    tws_status = "fail"
    if tws_evidence_path and tws_evidence_path.suffix.lower() == ".md":
        transcript = tws_evidence_path.read_text(encoding="utf-8")
        tws_status = (
            "pass"
            if (submit_order_id and str(submit_order_id) in transcript)
            and "AAPL" in transcript
            and json_contains_token(transcript, ACCEPTED_ORDER_STATES)
            else "fail"
        )
        tws_detail["mode"] = "transcript"
    elif tws_evidence_path:
        tws_status = "pass"
        tws_detail["mode"] = "screenshot_presence_only"
    checks.append(
        {
            "name": "tws_evidence_matches_order",
            "status": tws_status,
            "detail": tws_detail,
        }
    )

    note_lower = operator_note.lower()
    note_ok = (
        ("cancel" in note_lower or "canceled" in note_lower or "cancelled" in note_lower)
        and "not filled" in note_lower
    )
    checks.append(
        {
            "name": "operator_note_confirms_cancel_without_fill",
            "status": "pass" if note_ok else "fail",
            "detail": "operator note must state the order was canceled and not filled",
        }
    )

    passed = all(check["status"] == "pass" for check in checks)
    return {
        "task_id": "EP5-002",
        "generated_at": iso_now(),
        "status": "validated" if passed else "failed",
        "packet_dir": str(packet_dir),
        "proof_boundary": "manual live order/cancel packet validation",
        "checks": checks,
    }


def command_init(args: argparse.Namespace) -> int:
    result = init_packet(
        Path(args.packet_dir),
        account=args.account,
        limit_price=args.limit_price,
        runtime_binding_id=args.runtime_binding_id,
        deployment_plan_id=args.deployment_plan_id,
        operator_id=args.operator_id,
    )
    print(json.dumps({"status": result["status"], "packet_dir": result["packet_dir"]}, ensure_ascii=False))
    return 0


def command_record_submit(args: argparse.Namespace) -> int:
    result = record_submit_response(
        Path(args.packet_dir),
        order_id=args.order_id,
        status=args.status,
        captured_at=args.captured_at,
        broker_order_id=args.broker_order_id,
        perm_id=args.perm_id,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_record_request(args: argparse.Namespace) -> int:
    result = record_submit_request(
        Path(args.packet_dir),
        account=args.account,
        limit_price=args.limit_price,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_record_cancel(args: argparse.Namespace) -> int:
    result = record_cancel_response(
        Path(args.packet_dir),
        status=args.status,
        captured_at=args.captured_at,
        order_id=args.order_id,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_record_telemetry(args: argparse.Namespace) -> int:
    result = record_telemetry_trace(
        Path(args.packet_dir),
        event_id=args.event_id,
        runtime_binding_id=args.runtime_binding_id,
        deployment_plan_id=args.deployment_plan_id,
        order_id=args.order_id,
        perm_id=args.perm_id,
        observed_at=args.observed_at,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_record_runtime(args: argparse.Namespace) -> int:
    result = record_runtime_excerpt(
        Path(args.packet_dir),
        runtime_binding_id=args.runtime_binding_id,
        deployment_plan_id=args.deployment_plan_id,
        operator_id=args.operator_id,
        submitted_at=args.submitted_at,
        canceled_at=args.canceled_at,
        order_id=args.order_id,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_record_tws(args: argparse.Namespace) -> int:
    result = record_tws_transcript(
        Path(args.packet_dir),
        state=args.state,
        operator_id=args.operator_id,
        observed_at=args.observed_at,
        order_id=args.order_id,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_record_operator_note(args: argparse.Namespace) -> int:
    result = record_operator_note(
        Path(args.packet_dir),
        operator_id=args.operator_id,
        submitted_at=args.submitted_at,
        canceled_at=args.canceled_at,
        order_id=args.order_id,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    if not args.packet_dir or not args.output_dir:
        raise SystemExit("--packet-dir and --output-dir are required for validation")
    packet_dir = Path(args.packet_dir)
    output_dir = Path(args.output_dir)
    result = validate_packet(packet_dir)
    dump_json(output_dir / "ep5-live-order-cancel-validation.json", result)
    dump_json(
        output_dir / "summary.json",
        {
            "task_id": result.get("task_id", "EP5-002"),
            "generated_at": result["generated_at"],
            "status": result["status"],
            "packet_dir": result["packet_dir"],
        },
    )
    print(json.dumps({"status": result["status"], "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0 if result["status"] == "validated" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate an EP5 live order/cancel packet.")
    parser.add_argument("--packet-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    subparsers = parser.add_subparsers(dest="command")

    p_init = subparsers.add_parser("init", help="Create a pending operator-capture packet scaffold.")
    p_init.add_argument("--packet-dir", required=True)
    p_init.add_argument("--account", required=True)
    p_init.add_argument("--limit-price", required=True)
    p_init.add_argument("--runtime-binding-id", required=True)
    p_init.add_argument("--deployment-plan-id", required=True)
    p_init.add_argument("--operator-id", required=True)
    p_init.set_defaults(func=command_init)

    p_validate = subparsers.add_parser("validate", help="Validate an archived live order/cancel packet.")
    p_validate.add_argument("--packet-dir", required=True)
    p_validate.add_argument("--output-dir", required=True)
    p_validate.set_defaults(func=command_validate)

    p_record_request = subparsers.add_parser("record-request", help="Record the operator-approved submit request.")
    p_record_request.add_argument("--packet-dir", required=True)
    p_record_request.add_argument("--account", required=True)
    p_record_request.add_argument("--limit-price", required=True)
    p_record_request.set_defaults(func=command_record_request)

    p_record_submit = subparsers.add_parser("record-submit", help="Record broker submit acknowledgement.")
    p_record_submit.add_argument("--packet-dir", required=True)
    p_record_submit.add_argument("--order-id", required=True)
    p_record_submit.add_argument("--status", required=True)
    p_record_submit.add_argument("--captured-at", default=None)
    p_record_submit.add_argument("--broker-order-id", default=None)
    p_record_submit.add_argument("--perm-id", default=None)
    p_record_submit.set_defaults(func=command_record_submit)

    p_record_cancel = subparsers.add_parser("record-cancel", help="Record broker cancel confirmation.")
    p_record_cancel.add_argument("--packet-dir", required=True)
    p_record_cancel.add_argument("--status", required=True)
    p_record_cancel.add_argument("--captured-at", default=None)
    p_record_cancel.add_argument("--order-id", default=None)
    p_record_cancel.set_defaults(func=command_record_cancel)

    p_record_telemetry = subparsers.add_parser("record-telemetry", help="Record telemetry event trace refs.")
    p_record_telemetry.add_argument("--packet-dir", required=True)
    p_record_telemetry.add_argument("--event-id", required=True)
    p_record_telemetry.add_argument("--runtime-binding-id", required=True)
    p_record_telemetry.add_argument("--deployment-plan-id", required=True)
    p_record_telemetry.add_argument("--order-id", default=None)
    p_record_telemetry.add_argument("--perm-id", default=None)
    p_record_telemetry.add_argument("--observed-at", default=None)
    p_record_telemetry.set_defaults(func=command_record_telemetry)

    p_record_runtime = subparsers.add_parser("record-runtime", help="Record runtime-manager lifecycle excerpt.")
    p_record_runtime.add_argument("--packet-dir", required=True)
    p_record_runtime.add_argument("--runtime-binding-id", required=True)
    p_record_runtime.add_argument("--deployment-plan-id", required=True)
    p_record_runtime.add_argument("--operator-id", required=True)
    p_record_runtime.add_argument("--submitted-at", default=None)
    p_record_runtime.add_argument("--canceled-at", default=None)
    p_record_runtime.add_argument("--order-id", default=None)
    p_record_runtime.set_defaults(func=command_record_runtime)

    p_record_tws = subparsers.add_parser("record-tws", help="Record TWS open-order transcript.")
    p_record_tws.add_argument("--packet-dir", required=True)
    p_record_tws.add_argument("--state", required=True)
    p_record_tws.add_argument("--operator-id", required=True)
    p_record_tws.add_argument("--observed-at", default=None)
    p_record_tws.add_argument("--order-id", default=None)
    p_record_tws.set_defaults(func=command_record_tws)

    p_record_note = subparsers.add_parser("record-operator-note", help="Record operator cancel/no-fill note.")
    p_record_note.add_argument("--packet-dir", required=True)
    p_record_note.add_argument("--operator-id", required=True)
    p_record_note.add_argument("--submitted-at", default=None)
    p_record_note.add_argument("--canceled-at", default=None)
    p_record_note.add_argument("--order-id", default=None)
    p_record_note.set_defaults(func=command_record_operator_note)

    parser.set_defaults(func=command_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
