#!/usr/bin/env python3
"""Run and archive one integrated governed paper acceptance packet for EP4.

This harness proves the EP4 chain as one archived packet:

approval -> deployment -> runtime binding -> paper execution -> telemetry ->
incident/health -> kill-switch -> pause_then_replace rollback.

The script is transport-only and depends on already running services. It writes
request/response artifacts into one output directory so OSS-004C can archive a
single evidence bundle.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AcceptanceError(RuntimeError):
    """Raised when the governed acceptance packet fails."""


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def suffix() -> str:
    return uuid.uuid4().hex[:8]


def uuid4_str() -> str:
    return str(uuid.uuid4())


def dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
) -> tuple[int, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, parsed


def assert_status(label: str, status: int, allowed: set[int], payload: Any) -> None:
    if status not in allowed:
        raise AcceptanceError(f"{label} failed with HTTP {status}: {json.dumps(payload, ensure_ascii=False)}")


def write_http_artifact(
    output_dir: Path,
    stem: str,
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    allowed_statuses: set[int] | None = None,
    timeout: int = 15,
) -> tuple[int, Any]:
    request_record = {
        "method": method,
        "url": url,
        "headers": headers or {},
        "body": body,
    }
    dump_json(output_dir / f"{stem}.request.json", request_record)
    status, payload = request_json(method, url, body=body, headers=headers, timeout=timeout)
    response_record = {
        "status": status,
        "payload": payload,
    }
    dump_json(output_dir / f"{stem}.response.json", response_record)
    if allowed_statuses is not None:
        assert_status(stem, status, allowed_statuses, payload)
    return status, payload


def wait_for_json_field(
    url: str,
    *,
    field_path: list[str],
    expected: Any,
    headers: dict[str, str] | None = None,
    timeout_seconds: int = 20,
    interval_seconds: float = 0.5,
) -> Any:
    deadline = time.time() + timeout_seconds
    last_payload: Any = None
    while time.time() < deadline:
        status, payload = request_json("GET", url, headers=headers, timeout=10)
        if status == 200:
            cursor = payload
            for key in field_path:
                if not isinstance(cursor, dict) or key not in cursor:
                    cursor = None
                    break
                cursor = cursor[key]
            if cursor == expected:
                return payload
            last_payload = payload
        time.sleep(interval_seconds)
    raise AcceptanceError(
        f"timed out waiting for {url} field {'.'.join(field_path)} == {expected!r}; last payload={json.dumps(last_payload, ensure_ascii=False)}"
    )


def wait_for_telemetry_stats(
    url: str,
    *,
    min_total_ingested: int,
    max_total_rejected: int,
    timeout_seconds: int = 20,
    interval_seconds: float = 0.5,
) -> Any:
    deadline = time.time() + timeout_seconds
    last_payload: Any = None
    while time.time() < deadline:
        status, payload = request_json("GET", url, timeout=10)
        if status == 200 and isinstance(payload, dict):
            service = payload.get("service") or {}
            dlq = payload.get("dead_letter_queue") or {}
            total_ingested = int(service.get("total_ingested", 0))
            total_rejected = int(dlq.get("total_rejected", 0))
            if total_ingested >= min_total_ingested and total_rejected <= max_total_rejected:
                return payload
            last_payload = payload
        time.sleep(interval_seconds)
    raise AcceptanceError(
        "timed out waiting for telemetry stats to reflect accepted ingest; "
        f"min_total_ingested={min_total_ingested}, max_total_rejected={max_total_rejected}, "
        f"last payload={json.dumps(last_payload, ensure_ascii=False)}"
    )


def wait_for_health(
    label: str,
    url: str,
    output_dir: Path,
    *,
    expected_service: str | None = None,
    timeout_seconds: int = 60,
) -> Any:
    deadline = time.time() + timeout_seconds
    last_status = None
    last_payload: Any = None
    request_artifact = {
        "method": "GET",
        "url": url,
        "headers": {"Accept": "application/json"},
    }
    dump_json(output_dir / f"{label}.request.json", request_artifact)
    while time.time() < deadline:
        status, payload = request_json("GET", url, timeout=5)
        last_status, last_payload = status, payload
        if status == 200 and isinstance(payload, dict) and payload.get("status") == "ok":
            if expected_service and payload.get("service") != expected_service:
                raise AcceptanceError(
                    f"{label} returned service={payload.get('service')!r}, expected {expected_service!r}"
                )
            dump_json(output_dir / f"{label}.response.json", {"status": status, "payload": payload})
            return payload
        time.sleep(1)
    dump_json(output_dir / f"{label}.response.json", {"status": last_status, "payload": last_payload})
    raise AcceptanceError(
        f"{label} did not become healthy: status={last_status}, payload={json.dumps(last_payload, ensure_ascii=False)}"
    )


def redis_rpush_json(host: str, port: int, key: str, payload: dict[str, Any]) -> int:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    parts = [b"RPUSH", key.encode("utf-8"), encoded]
    frame = [f"*{len(parts)}\r\n".encode("utf-8")]
    for part in parts:
        frame.append(f"${len(part)}\r\n".encode("utf-8"))
        frame.append(part)
        frame.append(b"\r\n")
    with socket.create_connection((host, port), timeout=5) as conn:
        conn.sendall(b"".join(frame))
        response = b""
        while not response.endswith(b"\r\n"):
            chunk = conn.recv(1024)
            if not chunk:
                break
            response += chunk
    if not response.startswith(b":"):
        raise AcceptanceError(f"unexpected Redis response: {response!r}")
    try:
        return int(response[1:-2])
    except ValueError as exc:
        raise AcceptanceError(f"failed to parse Redis integer response: {response!r}") from exc


@dataclass
class Context:
    args: argparse.Namespace
    output_dir: Path
    auth_headers: dict[str, str]
    health: dict[str, Any]
    ids: dict[str, str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one integrated EP4 governed paper acceptance packet.")
    parser.add_argument("--governance-url", default="http://127.0.0.1:18082")
    parser.add_argument("--deployment-url", default="http://127.0.0.1:8006")
    parser.add_argument("--telemetry-url", default="http://127.0.0.1:18083")
    parser.add_argument("--incidents-url", default="http://127.0.0.1:18090")
    parser.add_argument("--runtime-manager-url", default="http://127.0.0.1:28081")
    parser.add_argument("--paper-runtime-url", default="http://127.0.0.1:28110")
    parser.add_argument("--signal-store-host", default="127.0.0.1")
    parser.add_argument("--signal-store-port", type=int, default=26379)
    parser.add_argument("--signal-queue-key", default="pantheon:signals:pending")
    parser.add_argument("--runtime-manager-token", default="runtime-control-internal")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--source-task-id", default="OSS-004C")
    parser.add_argument("--paper-runtime-id", default="paper-runtime-001")
    parser.add_argument("--workspace-ref", default="workspace-paper")
    parser.add_argument("--auth-profile-ref", default="auth-profile-paper")
    parser.add_argument("--persona-id", default="persona-paper-ops")
    parser.add_argument("--strategy-id", default="strategy-paper")
    return parser


def prepare_context(args: argparse.Namespace) -> Context:
    out = Path(args.output_dir).expanduser() if args.output_dir else Path(
        f"docs/deployment/evidence/ep4-governed-paper/{iso_now().replace(':', '').replace('-', '')}"
    )
    out.mkdir(parents=True, exist_ok=True)
    ids = {
        "run_suffix": suffix(),
        "trace_id": uuid4_str(),
        "request_id": f"req-ep4-{suffix()}",
        "session_id": f"session-ep4-{suffix()}",
        "approval_id": f"apv-ep4-{suffix()}",
        "plan_id": f"plan-ep4-{suffix()}",
        "registry_id": f"reg-ep4-{suffix()}",
        "fallback_registry_id": f"reg-ep4-fallback-{suffix()}",
        "capital_pool_id": f"pool-ep4-{suffix()}",
        "pcb_id": f"pcb-ep4-{suffix()}",
        "incident_id": f"inc-ep4-{suffix()}",
        "signal_id": f"signal-ep4-{suffix()}",
        "deploy_event_id": uuid4_str(),
        "rollback_event_id": uuid4_str(),
        "review_actor_id": "ep4-governance-reviewer",
        "kill_switch_actor_id": "ep4-operator-oncall",
    }
    return Context(
        args=args,
        output_dir=out,
        auth_headers={"Authorization": f"Bearer {args.runtime_manager_token}"},
        health={},
        ids=ids,
    )


def health_phase(ctx: Context) -> dict[str, Any]:
    args = ctx.args
    output_dir = ctx.output_dir
    health_payloads = {
        "governance-health": wait_for_health(
            "governance-health",
            f"{args.governance_url.rstrip('/')}/health",
            output_dir,
            expected_service="governance",
        ),
        "deployment-health": wait_for_health(
            "deployment-health",
            f"{args.deployment_url.rstrip('/')}/health",
            output_dir,
            expected_service="pantheon-deployment",
        ),
        "telemetry-health": wait_for_health(
            "telemetry-health",
            f"{args.telemetry_url.rstrip('/')}/__health__",
            output_dir,
            expected_service="telemetry-ingest",
        ),
        "incidents-health": wait_for_health(
            "incidents-health",
            f"{args.incidents_url.rstrip('/')}/__health__",
            output_dir,
            expected_service="incidents",
        ),
        "runtime-manager-health": wait_for_health(
            "runtime-manager-health",
            f"{args.runtime_manager_url.rstrip('/')}/__health__",
            output_dir,
            expected_service="runtime-manager",
        ),
        "paper-runtime-health": wait_for_health(
            "paper-runtime-health",
            f"{args.paper_runtime_url.rstrip('/')}/__health__",
            output_dir,
        ),
    }
    paper_health = health_payloads["paper-runtime-health"]
    if paper_health.get("runtime_package") != "paper_execution_runtime":
        raise AcceptanceError(f"paper runtime reported unexpected package: {paper_health.get('runtime_package')!r}")
    if paper_health.get("runtime_id") != args.paper_runtime_id:
        raise AcceptanceError(f"paper runtime_id mismatch: {paper_health.get('runtime_id')!r} != {args.paper_runtime_id!r}")
    if paper_health.get("workspace_ref") != args.workspace_ref:
        raise AcceptanceError(f"paper workspace_ref mismatch: {paper_health.get('workspace_ref')!r}")
    if not paper_health.get("runtime_manager_auth", {}).get("configured"):
        raise AcceptanceError("paper runtime manager auth is not configured")
    telemetry_url = str(paper_health.get("telemetry", {}).get("url") or "")
    if not telemetry_url:
        raise AcceptanceError("paper runtime telemetry is not configured; set PANTHEON_TELEMETRY_URL")
    ctx.health = health_payloads
    return health_payloads


def approval_phase(ctx: Context) -> dict[str, Any]:
    args = ctx.args
    ids = ctx.ids
    base = args.governance_url.rstrip("/")
    _, matrix = write_http_artifact(
        ctx.output_dir,
        "governance-write-authority",
        "GET",
        f"{base}/api/governance/write-authority",
        allowed_statuses={200},
    )
    proposal = {
        "decision_id": ids["approval_id"],
        "target_type": "registry_entry",
        "target_id": ids["registry_id"],
        "target_version": "1.2.0",
        "risk_level": "low",
        "capital_pool_id": ids["capital_pool_id"],
        "persona_id": args.persona_id,
    }
    _, proposed = write_http_artifact(
        ctx.output_dir,
        "approval-propose",
        "POST",
        f"{base}/api/governance/approvals",
        body=proposal,
        allowed_statuses={201},
    )
    _, reviewed = write_http_artifact(
        ctx.output_dir,
        "approval-review",
        "POST",
        f"{base}/api/governance/approvals/{ids['approval_id']}/review",
        body={
            "actor_role": "governance_reviewer",
            "actor_id": ids["review_actor_id"],
        },
        allowed_statuses={200},
    )
    _, decided = write_http_artifact(
        ctx.output_dir,
        "approval-decide",
        "POST",
        f"{base}/api/governance/approvals/{ids['approval_id']}/decide",
        body={
            "actor_role": "governance_reviewer",
            "actor_id": ids["review_actor_id"],
            "outcome": "approved",
            "rationale": "EP4 governed paper acceptance run",
        },
        allowed_statuses={200},
    )
    _, latest = write_http_artifact(
        ctx.output_dir,
        "approval-latest-approved",
        "GET",
        f"{base}/api/governance/approvals/latest-approved?{urllib.parse.urlencode({'target_type': 'registry_entry', 'target_id': ids['registry_id']})}",
        allowed_statuses={200},
    )
    _, audit = write_http_artifact(
        ctx.output_dir,
        "approval-audit",
        "GET",
        f"{base}/api/governance/audit?{urllib.parse.urlencode({'decision_id': ids['approval_id']})}",
        allowed_statuses={200},
    )
    if proposed.get("decision_state") != "proposed":
        raise AcceptanceError("approval proposal did not enter proposed state")
    if reviewed.get("decision_state") != "under_review":
        raise AcceptanceError("approval review did not enter under_review state")
    if decided.get("decision_state") != "decided" or decided.get("decision") != "approved":
        raise AcceptanceError("approval did not reach approved decided state")
    if latest is None or latest.get("decision_id") != ids["approval_id"]:
        raise AcceptanceError("latest-approved lookup did not resolve to this EP4 approval")
    audit_types = {entry.get("event_type") for entry in audit}
    required = {"approval_decision_created", "approval_decision_state_changed", "approval_decision_decided"}
    if not required.issubset(audit_types):
        raise AcceptanceError(f"approval audit log missing required events: {sorted(required - audit_types)}")
    return {
        "matrix": matrix,
        "proposed": proposed,
        "reviewed": reviewed,
        "decided": decided,
        "latest": latest,
        "audit": audit,
    }


def deployment_phase(ctx: Context, approval: dict[str, Any]) -> dict[str, Any]:
    args = ctx.args
    ids = ctx.ids
    base = args.deployment_url.rstrip("/")
    plan_payload = {
        "plan_id": ids["plan_id"],
        "approval_decision_id": ids["approval_id"],
        "registry_entry": {
            "registry_id": ids["registry_id"],
            "artifact_type": "model_artifact",
            "strategy_id": args.strategy_id,
            "version": "1.2.0",
            "artifact_state": "approved",
            "checksum": f"sha256:ep4-{ids['run_suffix']}",
            "approved_at": approval["decided"]["decided_at"],
            "lineage": {"source_run_ids": [f"ep4-run-{ids['run_suffix']}"]},
            "deployment_summary": {"current_stage": "none"},
        },
        "approval_decision": approval["decided"],
        "capital_pool_id": ids["capital_pool_id"],
        "sponsor_persona_id": args.persona_id,
        "target_stage": "paper",
        "created_by": "oss-004c-ep4-harness",
        "rollback": {
            "target_artifact_id": ids["fallback_registry_id"],
            "target_version": "1.1.9",
            "action_type": "pause_then_replace",
            "reason": "EP4 governed rollback drill fallback",
        },
        "metadata": {
            "source_task_id": args.source_task_id,
            "acceptance_mode": "ep4_governed_paper",
        },
        "authority_refs": {
            "write_owner": "runtime-manager",
            "authority_source": "runtime_binding",
            "runtime_role": "pantheon-paper-execution-runtime",
            "runtime_mode": "paper",
            "workspace_ref": args.workspace_ref,
            "auth_profile_ref": args.auth_profile_ref,
            "persona_id": args.persona_id,
            "session_id": ids["session_id"],
            "trace_id": ids["trace_id"],
            "request_id": ids["request_id"],
        },
    }
    _, validate = write_http_artifact(
        ctx.output_dir,
        "deployment-plan-validate",
        "POST",
        f"{base}/api/deployment/plans/validate",
        body=plan_payload,
        allowed_statuses={200},
    )
    _, created = write_http_artifact(
        ctx.output_dir,
        "deployment-plan-create",
        "POST",
        f"{base}/api/deployment/plans",
        body=plan_payload,
        allowed_statuses={201},
    )
    _, dispatched = write_http_artifact(
        ctx.output_dir,
        "deployment-plan-dispatch",
        "POST",
        f"{base}/api/deployment/plans/{ids['plan_id']}/dispatch",
        body={
            "trace_id": ids["trace_id"],
            "workflow_id": "pantheon.ep4.governed-paper",
            "source_task_id": args.source_task_id,
            "registry_entry": plan_payload["registry_entry"],
        },
        allowed_statuses={200},
    )
    saga = dispatched.get("deployment_saga", {}) or {}
    saga_id = saga.get("saga", {}).get("saga_id")
    outbox_event_id = saga.get("outbox_event", {}).get("event", {}).get("event_id")
    if not saga_id or not outbox_event_id:
        raise AcceptanceError("deployment dispatch did not return saga_id/outbox_event_id")
    _, consume = write_http_artifact(
        ctx.output_dir,
        "deployment-outbox-consume",
        "POST",
        f"{base}/api/deployment/outbox/{outbox_event_id}/consume",
        body={"consumer_name": "ep4-governed-acceptance"},
        allowed_statuses={200},
    )
    _, executing = write_http_artifact(
        ctx.output_dir,
        "deployment-plan-status-executing",
        "POST",
        f"{base}/api/deployment/plans/{ids['plan_id']}/status",
        body={"status": "executing"},
        allowed_statuses={200},
    )
    if not validate.get("ok"):
        raise AcceptanceError("deployment plan validation returned ok=false")
    return {
        "plan_payload": plan_payload,
        "validate": validate,
        "created": created,
        "dispatched": dispatched,
        "consume": consume,
        "executing": executing,
        "saga_id": saga_id,
    }


def runtime_phase(ctx: Context, deployment: dict[str, Any]) -> dict[str, Any]:
    args = ctx.args
    ids = ctx.ids
    base = args.runtime_manager_url.rstrip("/")
    runtime_deploy = {
        "plan_id": ids["plan_id"],
        "plan_status": "executing",
        "target_stage": "paper",
        "artifact_id": ids["registry_id"],
        "artifact_version": "1.2.0",
        "capital_pool_id": ids["capital_pool_id"],
        "persona_capital_binding_id": ids["pcb_id"],
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
        "loader_checks_passed": True,
        "runtime_id": args.paper_runtime_id,
    }
    _, binding = write_http_artifact(
        ctx.output_dir,
        "runtime-deploy",
        "POST",
        f"{base}/api/runtimes/deploy",
        body=runtime_deploy,
        headers=ctx.auth_headers,
        allowed_statuses={201},
    )
    binding_id = binding.get("binding_id")
    if not binding_id:
        raise AcceptanceError("runtime deploy did not return binding_id")
    deployment_base = args.deployment_url.rstrip("/")
    _, binding_created = write_http_artifact(
        ctx.output_dir,
        "deployment-saga-binding-created",
        "POST",
        f"{deployment_base}/api/deployment/sagas/{deployment['saga_id']}/binding-created",
        body={
            "binding_id": binding_id,
            "runtime_id": args.paper_runtime_id,
            "note": "execution plane created the governed paper binding",
        },
        allowed_statuses={200},
    )
    _, runtime_active = write_http_artifact(
        ctx.output_dir,
        "deployment-saga-runtime-active",
        "POST",
        f"{deployment_base}/api/deployment/sagas/{deployment['saga_id']}/runtime-active",
        body={
            "binding_id": binding_id,
            "runtime_id": args.paper_runtime_id,
            "note": "paper execution runtime package is healthy",
        },
        allowed_statuses={200},
    )
    _, saga_detail = write_http_artifact(
        ctx.output_dir,
        "deployment-saga-detail",
        "GET",
        f"{deployment_base}/api/deployment/sagas/{deployment['saga_id']}",
        allowed_statuses={200},
    )
    _, executed = write_http_artifact(
        ctx.output_dir,
        "deployment-plan-status-executed",
        "POST",
        f"{deployment_base}/api/deployment/plans/{ids['plan_id']}/status",
        body={"status": "executed"},
        allowed_statuses={200},
    )
    if saga_detail.get("status") != "completed":
        raise AcceptanceError(f"deployment saga did not complete: {saga_detail.get('status')!r}")
    return {
        "binding": binding,
        "binding_created": binding_created,
        "runtime_active": runtime_active,
        "saga_detail": saga_detail,
        "executed": executed,
        "binding_id": binding_id,
    }


def telemetry_phase_before_runtime(ctx: Context, runtime: dict[str, Any]) -> dict[str, Any]:
    args = ctx.args
    ids = ctx.ids
    base = args.telemetry_url.rstrip("/")
    _, stats_before = write_http_artifact(
        ctx.output_dir,
        "telemetry-stats-before-runtime",
        "GET",
        f"{base}/api/telemetry/stats",
        allowed_statuses={200},
    )
    total_ingested_before = int(stats_before["service"]["total_ingested"])
    total_rejected_before = int(stats_before["dead_letter_queue"]["total_rejected"])
    deploy_event = {
        "event_id": ids["deploy_event_id"],
        "event_type": "deploy_completed",
        "created_at": iso_now(),
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": runtime["binding_id"],
        "runtime_id": args.paper_runtime_id,
        "capital_pool_id": ids["capital_pool_id"],
        "artifact_id": ids["registry_id"],
        "artifact_version": "1.2.0",
        "plan_id": ids["plan_id"],
        "persona_capital_binding_id": ids["pcb_id"],
        "trace_id": ids["trace_id"],
        "authority_refs": {
            "write_owner": "runtime-manager",
            "authority_source": "runtime_binding",
            "runtime_role": "pantheon-paper-execution-runtime",
            "runtime_mode": "paper",
            "workspace_ref": args.workspace_ref,
            "auth_profile_ref": args.auth_profile_ref,
            "persona_id": args.persona_id,
            "session_id": ids["session_id"],
            "trace_id": ids["trace_id"],
            "request_id": ids["request_id"],
        },
        "target": {
            "registry_id": ids["registry_id"],
            "strategy_id": args.strategy_id,
            "artifact_version": "1.2.0",
            "artifact_type": "model_artifact",
            "promotion_state": "paper",
        },
        "metrics": {"action": "deploy_completed"},
        "metadata": {
            "source_task_id": args.source_task_id,
            "acceptance_mode": "governed_ep4_packet",
            "runtime_package": "paper_execution_runtime",
            "is_real_order": False,
            "is_real_capital": False,
            "capital_scale_pct": 0,
            "sim_fill_flag": True,
        },
    }
    _, ingested = write_http_artifact(
        ctx.output_dir,
        "telemetry-deploy-completed",
        "POST",
        f"{base}/api/telemetry/ingest",
        body=deploy_event,
        allowed_statuses={202},
    )
    stats_after = wait_for_telemetry_stats(
        f"{base}/api/telemetry/stats",
        min_total_ingested=total_ingested_before + 1,
        max_total_rejected=total_rejected_before,
    )
    dump_json(
        ctx.output_dir / "telemetry-stats-after-deploy.response.json",
        {"status": 200, "payload": stats_after},
    )
    trace_status, deploy_trace = write_http_artifact(
        ctx.output_dir,
        "telemetry-deploy-trace",
        "GET",
        f"{base}/api/telemetry/lineage/events/{ids['deploy_event_id']}/trace",
        allowed_statuses={200, 404},
    )
    return {
        "stats_before": stats_before,
        "deploy_event": deploy_event,
        "ingested": ingested,
        "deploy_trace": deploy_trace,
        "deploy_trace_status": trace_status,
        "stats_after": stats_after,
    }


def paper_runtime_phase(ctx: Context, runtime: dict[str, Any]) -> dict[str, Any]:
    args = ctx.args
    ids = ctx.ids
    base = args.paper_runtime_url.rstrip("/")
    _, runtime_state_before = write_http_artifact(
        ctx.output_dir,
        "paper-runtime-state-before-signal",
        "GET",
        f"{base}/api/runtime/state",
        allowed_statuses={200},
    )
    signal_payload = {
        "signal_id": ids["signal_id"],
        "version": "1.0",
        "strategy_id": args.strategy_id,
        "timestamp": iso_now(),
        "symbol": "AAPL.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 10,
        "quantity_type": "SHARES",
        "metadata": {
            "source_worker": "oss-004c-ep4-harness",
            "confidence_score": 1.0,
        },
    }
    rpush_len = redis_rpush_json(
        args.signal_store_host,
        args.signal_store_port,
        args.signal_queue_key,
        signal_payload,
    )
    dump_json(
        ctx.output_dir / "signal-enqueue.response.json",
        {
            "queue_length_after_rpush": rpush_len,
            "signal_payload": signal_payload,
            "signal_queue_key": args.signal_queue_key,
        },
    )
    _, drain = write_http_artifact(
        ctx.output_dir,
        "paper-runtime-drain",
        "POST",
        f"{base}/api/runtime/drain",
        body={},
        allowed_statuses={200},
    )
    _, orders = write_http_artifact(
        ctx.output_dir,
        "paper-runtime-orders",
        "GET",
        f"{base}/api/runtime/orders",
        allowed_statuses={200},
    )
    _, runtime_state_after = write_http_artifact(
        ctx.output_dir,
        "paper-runtime-state-after-signal",
        "GET",
        f"{base}/api/runtime/state",
        allowed_statuses={200},
    )
    processed = int(runtime_state_after.get("paper_state", {}).get("processed_signal_count") or 0)
    execution_events = int(runtime_state_after.get("paper_state", {}).get("execution_event_count") or 0)
    positions = runtime_state_after.get("paper_state", {}).get("positions") or []
    telemetry_sent = int(runtime_state_after.get("telemetry", {}).get("sent") or 0)
    if processed < 1 or execution_events < 1:
        raise AcceptanceError(f"paper runtime did not process the queued signal: processed={processed}, events={execution_events}")
    if not positions:
        raise AcceptanceError("paper runtime reported no positions after signal drain")
    if telemetry_sent < 2:
        raise AcceptanceError(f"paper runtime did not emit expected telemetry: sent={telemetry_sent}")
    return {
        "runtime_state_before": runtime_state_before,
        "signal_payload": signal_payload,
        "drain": drain,
        "orders": orders,
        "runtime_state_after": runtime_state_after,
        "telemetry_sent": telemetry_sent,
    }


def incident_phase(ctx: Context, runtime: dict[str, Any], telemetry: dict[str, Any]) -> dict[str, Any]:
    args = ctx.args
    ids = ctx.ids
    base = args.incidents_url.rstrip("/")
    incident_body = {
        "incident_id": ids["incident_id"],
        "title": "EP4 governed paper acceptance incident",
        "status": "open",
        "severity": "high",
        "binding_id": runtime["binding_id"],
        "deployment_stage": "paper",
        "deployment_plan_id": ids["plan_id"],
        "capital_pool_id": ids["capital_pool_id"],
        "persona_capital_binding_id": ids["pcb_id"],
        "artifact_id": ids["registry_id"],
        "artifact_version": "1.2.0",
        "runtime_id": args.paper_runtime_id,
        "trace_id": ids["trace_id"],
        "evidence_summary": "paper runtime produced a fill and the operator opened a governed incident before rollback",
    }
    _, created = write_http_artifact(
        ctx.output_dir,
        "incident-create",
        "POST",
        f"{base}/api/incidents",
        body=incident_body,
        allowed_statuses={201},
    )
    _, operator_payload = write_http_artifact(
        ctx.output_dir,
        "incident-operator-payload",
        "GET",
        f"{base}/api/incidents/{ids['incident_id']}/operator-payload",
        allowed_statuses={200},
    )
    return {
        "created": created,
        "operator_payload": operator_payload,
    }


def kill_switch_phase(ctx: Context, runtime: dict[str, Any]) -> dict[str, Any]:
    args = ctx.args
    ids = ctx.ids
    base = args.runtime_manager_url.rstrip("/")
    _, kill_switch = write_http_artifact(
        ctx.output_dir,
        "kill-switch-dispatch",
        "POST",
        f"{base}/api/kill-switch/dispatch",
        body={
            "reason": "operator_emergency_stop",
            "capital_pool_id": ids["capital_pool_id"],
            "binding_id": runtime["binding_id"],
            "actor_id": ids["kill_switch_actor_id"],
            "severity": "critical",
            "action_override": "pause",
        },
        headers=ctx.auth_headers,
        allowed_statuses={200},
    )
    binding_action = (kill_switch.get("binding_action") or {}).get("binding") or {}
    if binding_action.get("status") != "paused":
        raise AcceptanceError(f"kill-switch did not leave binding paused: {binding_action}")
    _, safe_mode = write_http_artifact(
        ctx.output_dir,
        "kill-switch-safe-mode",
        "GET",
        f"{base}/api/kill-switch/{ids['capital_pool_id']}/safe-mode",
        headers=ctx.auth_headers,
        allowed_statuses={200},
    )
    _, audit = write_http_artifact(
        ctx.output_dir,
        "kill-switch-audit-log",
        "GET",
        f"{base}/api/kill-switch/audit-log",
        headers=ctx.auth_headers,
        allowed_statuses={200},
    )
    return {
        "dispatch": kill_switch,
        "safe_mode": safe_mode,
        "audit": audit,
    }


def rollback_phase(ctx: Context, runtime: dict[str, Any]) -> dict[str, Any]:
    args = ctx.args
    ids = ctx.ids
    base = args.runtime_manager_url.rstrip("/")
    _, rollback = write_http_artifact(
        ctx.output_dir,
        "rollback-execute",
        "POST",
        f"{base}/api/rollback",
        body={
            "current_binding_id": runtime["binding_id"],
            "action_type": "pause_then_replace",
            "replacement_plan_id": f"{ids['plan_id']}-rollback",
            "replacement_plan_status": "approved",
            "replacement_deployment_mode": "paper",
            "replacement_artifact_id": ids["fallback_registry_id"],
            "replacement_artifact_version": "1.1.9",
            "replacement_persona_capital_binding_id": ids["pcb_id"],
            "replacement_persona_capital_binding_status": "active",
            "replacement_allowed_deployment_scope": "paper",
            "replacement_runtime_id": args.paper_runtime_id,
            "loader_checks_passed": True,
            "opened_by_artifact_id": ids["registry_id"],
        },
        headers=ctx.auth_headers,
        allowed_statuses={201},
    )
    old_binding = rollback.get("old_binding") or {}
    new_binding = rollback.get("new_binding") or {}
    if old_binding.get("status") != "retired":
        raise AcceptanceError(f"rollback old binding was not retired: {old_binding}")
    if new_binding.get("status") != "active":
        raise AcceptanceError(f"rollback new binding did not become active: {new_binding}")
    if new_binding.get("rollback_parent") != runtime["binding_id"]:
        raise AcceptanceError("rollback new binding did not retain rollback_parent linkage")
    return {
        "rollback": rollback,
        "replacement_binding_id": new_binding["binding_id"],
    }


def telemetry_phase_after_rollback(ctx: Context, runtime: dict[str, Any], rollback: dict[str, Any]) -> dict[str, Any]:
    args = ctx.args
    ids = ctx.ids
    base = args.telemetry_url.rstrip("/")
    _, stats_before = write_http_artifact(
        ctx.output_dir,
        "telemetry-stats-before-rollback",
        "GET",
        f"{base}/api/telemetry/stats",
        allowed_statuses={200},
    )
    total_ingested_before = int(stats_before["service"]["total_ingested"])
    total_rejected_before = int(stats_before["dead_letter_queue"]["total_rejected"])
    rollback_event = {
        "event_id": ids["rollback_event_id"],
        "event_type": "rollback_completed",
        "created_at": iso_now(),
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": rollback["replacement_binding_id"],
        "runtime_id": args.paper_runtime_id,
        "capital_pool_id": ids["capital_pool_id"],
        "artifact_id": ids["fallback_registry_id"],
        "artifact_version": "1.1.9",
        "plan_id": f"{ids['plan_id']}-rollback",
        "persona_capital_binding_id": ids["pcb_id"],
        "trace_id": ids["trace_id"],
        "rollback_parent": runtime["binding_id"],
        "rollback_action_type": "pause_then_replace",
        "authority_refs": {
            "write_owner": "runtime-manager",
            "authority_source": "runtime_binding",
            "runtime_role": "pantheon-paper-execution-runtime",
            "runtime_mode": "paper",
            "workspace_ref": args.workspace_ref,
            "auth_profile_ref": args.auth_profile_ref,
            "persona_id": args.persona_id,
            "session_id": ids["session_id"],
            "trace_id": ids["trace_id"],
            "request_id": ids["request_id"],
        },
        "target": {
            "registry_id": ids["fallback_registry_id"],
            "strategy_id": args.strategy_id,
            "artifact_version": "1.1.9",
            "artifact_type": "model_artifact",
            "promotion_state": "paper",
        },
        "metrics": {"action": "rollback_completed"},
        "metadata": {
            "source_task_id": args.source_task_id,
            "acceptance_mode": "governed_ep4_packet",
            "runtime_package": "paper_execution_runtime",
            "is_real_order": False,
            "is_real_capital": False,
            "capital_scale_pct": 0,
            "sim_fill_flag": True,
        },
    }
    _, ingested = write_http_artifact(
        ctx.output_dir,
        "telemetry-rollback-completed",
        "POST",
        f"{base}/api/telemetry/ingest",
        body=rollback_event,
        allowed_statuses={202},
    )
    stats_after = wait_for_telemetry_stats(
        f"{base}/api/telemetry/stats",
        min_total_ingested=total_ingested_before + 1,
        max_total_rejected=total_rejected_before,
    )
    dump_json(
        ctx.output_dir / "telemetry-stats-after-rollback.response.json",
        {"status": 200, "payload": stats_after},
    )
    trace_status, rollback_trace = write_http_artifact(
        ctx.output_dir,
        "telemetry-rollback-trace",
        "GET",
        f"{base}/api/telemetry/lineage/events/{ids['rollback_event_id']}/trace",
        allowed_statuses={200, 404},
    )
    return {
        "stats_before": stats_before,
        "rollback_event": rollback_event,
        "ingested": ingested,
        "rollback_trace": rollback_trace,
        "rollback_trace_status": trace_status,
        "stats_after": stats_after,
    }


def resolve_incident_phase(ctx: Context) -> dict[str, Any]:
    args = ctx.args
    ids = ctx.ids
    base = args.incidents_url.rstrip("/")
    _, resolved = write_http_artifact(
        ctx.output_dir,
        "incident-resolve",
        "POST",
        f"{base}/api/incidents/{ids['incident_id']}/status",
        body={"status": "resolved"},
        allowed_statuses={200},
    )
    return {"resolved": resolved}


def write_summary(
    ctx: Context,
    approval: dict[str, Any],
    deployment: dict[str, Any],
    runtime: dict[str, Any],
    telemetry_before_runtime: dict[str, Any],
    paper_runtime: dict[str, Any],
    incident: dict[str, Any],
    kill_switch: dict[str, Any],
    rollback: dict[str, Any],
    telemetry_after_rollback: dict[str, Any],
    incident_resolution: dict[str, Any],
) -> dict[str, Any]:
    before_total = int(telemetry_before_runtime["stats_before"]["service"]["total_ingested"])
    after_deploy_total = int(telemetry_before_runtime["stats_after"]["service"]["total_ingested"])
    after_rollback_total = int(telemetry_after_rollback["stats_after"]["service"]["total_ingested"])
    summary = {
        "run_timestamp_utc": iso_now(),
        "source_task_id": ctx.args.source_task_id,
        "approval_decision_id": ctx.ids["approval_id"],
        "plan_id": ctx.ids["plan_id"],
        "saga_id": deployment["saga_id"],
        "initial_binding_id": runtime["binding_id"],
        "replacement_binding_id": rollback["replacement_binding_id"],
        "incident_id": ctx.ids["incident_id"],
        "deploy_event_id": ctx.ids["deploy_event_id"],
        "rollback_event_id": ctx.ids["rollback_event_id"],
        "signal_id": ctx.ids["signal_id"],
        "paper_runtime_id": ctx.args.paper_runtime_id,
        "telemetry_counter_before_runtime": before_total,
        "telemetry_counter_after_deploy_event": after_deploy_total,
        "telemetry_counter_after_rollback_event": after_rollback_total,
        "telemetry_trace_after_deploy_status": telemetry_before_runtime["deploy_trace_status"],
        "telemetry_trace_after_rollback_status": telemetry_after_rollback["rollback_trace_status"],
        "runtime_emitted_telemetry_sent": paper_runtime["telemetry_sent"],
        "processed_signal_count": paper_runtime["runtime_state_after"]["paper_state"]["processed_signal_count"],
        "execution_event_count": paper_runtime["runtime_state_after"]["paper_state"]["execution_event_count"],
        "kill_switch_state": kill_switch["safe_mode"]["safe_mode_state"],
        "rollback_action_type": rollback["rollback"]["action_type"],
        "overall_result": "pass",
        "output_dir": str(ctx.output_dir),
    }
    dump_json(ctx.output_dir / "summary.json", summary)
    return summary


def main() -> int:
    args = build_parser().parse_args()
    ctx = prepare_context(args)
    try:
        approval = approval_phase(ctx) if health_phase(ctx) else None
        deployment = deployment_phase(ctx, approval)
        runtime = runtime_phase(ctx, deployment)
        telemetry_before_runtime = telemetry_phase_before_runtime(ctx, runtime)
        paper_runtime = paper_runtime_phase(ctx, runtime)
        incident = incident_phase(ctx, runtime, telemetry_before_runtime)
        kill_switch = kill_switch_phase(ctx, runtime)
        rollback = rollback_phase(ctx, runtime)
        telemetry_after_rollback = telemetry_phase_after_rollback(ctx, runtime, rollback)
        incident_resolution = resolve_incident_phase(ctx)
        summary = write_summary(
            ctx,
            approval,
            deployment,
            runtime,
            telemetry_before_runtime,
            paper_runtime,
            incident,
            kill_switch,
            rollback,
            telemetry_after_rollback,
            incident_resolution,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        failure = {
            "run_timestamp_utc": iso_now(),
            "overall_result": "fail",
            "error": f"{type(exc).__name__}: {exc}",
            "output_dir": str(ctx.output_dir),
        }
        dump_json(ctx.output_dir / "summary.json", failure)
        print(json.dumps(failure, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
