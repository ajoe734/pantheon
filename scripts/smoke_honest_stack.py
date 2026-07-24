#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


GOVERNANCE_URL = os.getenv("GOVERNANCE_URL", "http://127.0.0.1:8082")
RUNTIME_MANAGER_URL = os.getenv("RUNTIME_MANAGER_URL", "http://127.0.0.1:8081")
TELEMETRY_URL = os.getenv("TELEMETRY_URL", "http://127.0.0.1:8083")
INCIDENTS_URL = os.getenv("INCIDENTS_URL", "http://127.0.0.1:8090")
POSTMORTEMS_URL = os.getenv("POSTMORTEMS_URL", "http://127.0.0.1:8091")
CAPITAL_URL = os.getenv("CAPITAL_URL", "http://127.0.0.1:8092")
DEPLOYMENT_URL = os.getenv("DEPLOYMENT_URL", "http://127.0.0.1:8095")
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "http://127.0.0.1:8093")
LINEAGE_READ_URL = os.getenv("LINEAGE_READ_URL", "http://127.0.0.1:8094")
BFF_URL = os.getenv("BFF_URL", "http://127.0.0.1:8001")
PERSONA_URL = os.getenv("PERSONA_URL", "http://127.0.0.1:8002")
ROUTER_URL = os.getenv("ROUTER_URL", "http://127.0.0.1:8001")
EVALUATION_URL = os.getenv("EVALUATION_URL", "http://127.0.0.1:8084")
FEEDBACK_URL = os.getenv("FEEDBACK_URL", "http://127.0.0.1:8085")
MEMORY_URL = os.getenv("MEMORY_URL", "http://127.0.0.1:8086")
REGISTRY_URL = os.getenv("REGISTRY_URL", "http://127.0.0.1:8087")
OPTIMIZER_URL = os.getenv("OPTIMIZER_URL", "http://127.0.0.1:8088")
PROMOTION_URL = os.getenv("PROMOTION_URL", "http://127.0.0.1:8089")
CONSULTATION_URL = os.getenv("CONSULTATION_URL", "http://127.0.0.1:8096")
SOURCE_INGEST_URL = os.getenv("SOURCE_INGEST_URL", "http://127.0.0.1:8097")
SEARCH_URL = os.getenv("SEARCH_URL", "http://127.0.0.1:8098")
TRAINING_SESSION_URL = os.getenv("TRAINING_SESSION_URL", "http://127.0.0.1:8099")
POLICY_LEARNING_URL = os.getenv("POLICY_LEARNING_URL", "http://127.0.0.1:8100")
RESEARCH_ORCHESTRATOR_URL = os.getenv("RESEARCH_ORCHESTRATOR_URL", "http://127.0.0.1:8101")
RECONCILIATION_DRIFT_URL = os.getenv("RECONCILIATION_DRIFT_URL", "http://127.0.0.1:8102")
RESEARCH_WORKER_GATEWAY_URL = os.getenv("RESEARCH_WORKER_GATEWAY_URL", "http://127.0.0.1:8103")
OPENCLAW_GATEWAY_ADAPTER_URL = os.getenv("OPENCLAW_GATEWAY_ADAPTER_URL", "http://127.0.0.1:8104")
WEB_CHANNEL_URL = os.getenv("WEB_CHANNEL_URL", "http://127.0.0.1:8000")
SOURCE_INGEST_EXTERNAL_FEED_HOST = os.getenv("SOURCE_INGEST_EXTERNAL_FEED_HOST", "smoke-stack")

OPERATOR_TOKEN = "Bearer smoke-operator:operator"
APPROVER_TOKEN = "Bearer smoke-approver:approver"
RUNTIME_TOKEN = "Bearer runtime-control-internal"


def _request_json(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8")
        return response.getcode(), json.loads(text) if text else None


def _wait_for_health(name: str, url: str, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    last_error = "unknown"
    while time.time() < deadline:
        try:
            status, payload = _request_json("GET", url, timeout=5)
            if status == 200:
                print(f"ok  {name}: {payload}")
                return
            last_error = f"unexpected status {status}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"{name} health check did not become ready: {last_error}")


def _serve_source_feed(payload: dict) -> tuple[ThreadingHTTPServer, str]:
    body = json.dumps(payload).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            if self.path != "/feed.json":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    feed_url = f"http://{SOURCE_INGEST_EXTERNAL_FEED_HOST}:{server.server_port}/feed.json"
    return server, feed_url


def _verify_sse_replay() -> None:
    status, published = _request_json(
        "POST",
        f"{BFF_URL}/api/v1/internal/sse/publish?{urllib.parse.urlencode({'event_type': 'runtime_state_changed', 'runtime_id': 'runtime-smoke'})}",
        body={"status": "active", "binding_id": "binding-smoke"},
        headers={"Authorization": OPERATOR_TOKEN},
    )
    if status != 200:
        raise RuntimeError(f"failed to publish SSE event: {status} {published}")

    request = urllib.request.Request(
        f"{BFF_URL}/api/v1/runtime/runtime-smoke/events/stream",
        headers={"Authorization": OPERATOR_TOKEN, "Accept": "text/event-stream"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        chunk = response.readline().decode("utf-8") + response.readline().decode("utf-8")
    if "runtime_state_changed" not in chunk:
        raise RuntimeError(f"SSE stream did not replay the published runtime event: {chunk!r}")
    print("ok  operator-bff SSE replayed a runtime event")


def main() -> int:
    suffix = str(int(time.time()))
    future_timestamp = (
        datetime.now(timezone.utc) + timedelta(seconds=5)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    plan_id = f"plan-smoke-{suffix}"
    pool_id = f"pool-smoke-{suffix}"
    persona_binding_id = f"pcb-smoke-{suffix}"
    incident_id = f"inc-smoke-{suffix}"
    postmortem_id = f"pm-smoke-{suffix}"

    _wait_for_health("runtime-manager", f"{RUNTIME_MANAGER_URL}/readyz")
    _wait_for_health("governance", f"{GOVERNANCE_URL}/readyz")
    _wait_for_health("telemetry", f"{TELEMETRY_URL}/readyz")
    _wait_for_health("incidents", f"{INCIDENTS_URL}/readyz")
    _wait_for_health("postmortems", f"{POSTMORTEMS_URL}/readyz")
    _wait_for_health("capital", f"{CAPITAL_URL}/readyz")
    _wait_for_health("deployment", f"{DEPLOYMENT_URL}/readyz")
    _wait_for_health("evolution", f"{EVOLUTION_URL}/readyz")
    _wait_for_health("lineage-read", f"{LINEAGE_READ_URL}/readyz")
    _wait_for_health("operator-bff", f"{BFF_URL}/readyz")
    _wait_for_health("persona", f"{PERSONA_URL}/readyz")
    _wait_for_health("router", f"{ROUTER_URL}/readyz")
    _wait_for_health("evaluation", f"{EVALUATION_URL}/readyz")
    _wait_for_health("feedback", f"{FEEDBACK_URL}/readyz")
    _wait_for_health("memory", f"{MEMORY_URL}/readyz")
    _wait_for_health("registry", f"{REGISTRY_URL}/readyz")
    _wait_for_health("optimizer-svc", f"{OPTIMIZER_URL}/readyz")
    _wait_for_health("promotion", f"{PROMOTION_URL}/readyz")
    _wait_for_health("consultation-svc", f"{CONSULTATION_URL}/readyz")
    _wait_for_health("source-ingest", f"{SOURCE_INGEST_URL}/readyz")
    _wait_for_health("search-svc", f"{SEARCH_URL}/readyz")
    _wait_for_health("training-session-svc", f"{TRAINING_SESSION_URL}/readyz")
    _wait_for_health("policy-learning-svc", f"{POLICY_LEARNING_URL}/readyz")
    _wait_for_health("research-orchestrator-svc", f"{RESEARCH_ORCHESTRATOR_URL}/readyz")
    _wait_for_health("reconciliation-drift-svc", f"{RECONCILIATION_DRIFT_URL}/readyz")
    _wait_for_health("research-worker-gateway-svc", f"{RESEARCH_WORKER_GATEWAY_URL}/readyz")
    _wait_for_health("openclaw-gateway-adapter", f"{OPENCLAW_GATEWAY_ADAPTER_URL}/livez")
    _wait_for_health("web-channel", f"{WEB_CHANNEL_URL}/readyz")

    status, openclaw_health = _request_json("GET", f"{OPENCLAW_GATEWAY_ADAPTER_URL}/healthz")
    if status != 200 or openclaw_health.get("status") not in {"ok", "degraded"}:
        raise RuntimeError(f"openclaw-gateway-adapter health facade failed: {status} {openclaw_health}")
    try:
        status, openclaw_ready = _request_json("GET", f"{OPENCLAW_GATEWAY_ADAPTER_URL}/readyz")
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", errors="replace")
        openclaw_ready = json.loads(body) if body else {}
    if status not in {200, 503} or openclaw_ready.get("status") not in {"ok", "degraded"}:
        raise RuntimeError(f"openclaw-gateway-adapter readiness semantics failed: {status} {openclaw_ready}")
    status, openclaw_capabilities = _request_json("GET", f"{OPENCLAW_GATEWAY_ADAPTER_URL}/api/openclaw-adapter/capabilities")
    openclaw_activation_state = openclaw_capabilities.get("activation_state")
    openclaw_upstream_status = (openclaw_capabilities.get("upstream") or {}).get("status")
    openclaw_activation_safe = (
        (
            openclaw_activation_state == "upstream_client_degraded"
            and openclaw_upstream_status == "degraded"
        )
        or (
            openclaw_activation_state == "upstream_client_ready"
            and openclaw_upstream_status == "ok"
        )
    )
    if (
        status != 200
        or not openclaw_activation_safe
        or openclaw_capabilities.get("broker_execution") != "deferred"
        or openclaw_capabilities.get("paper_adapter") != "deferred"
        or openclaw_capabilities.get("live_adapter") != "deferred"
    ):
        raise RuntimeError(f"openclaw-gateway-adapter capability facade unsafe: {status} {openclaw_capabilities}")
    try:
        status, openclaw_session = _request_json(
            "POST",
            f"{OPENCLAW_GATEWAY_ADAPTER_URL}/api/openclaw-adapter/sessions",
            body={"agent_id": "agent-smoke", "session_type": "interactive"},
        )
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", errors="replace")
        openclaw_session = json.loads(body) if body else {}
    if (
        status != 503
        or openclaw_session.get("status") not in {"deferred", "upstream_error"}
        or openclaw_session.get("error_code") not in {"CAPABILITY_DENIED", "UPSTREAM_UNAVAILABLE"}
        or (
            openclaw_session.get("error_code") == "CAPABILITY_DENIED"
            and openclaw_session.get("retryable") is not False
        )
        or (
            openclaw_session.get("error_code") == "UPSTREAM_UNAVAILABLE"
            and (
                openclaw_session.get("retryable") is not True
                or openclaw_session.get("owner_plane") != "openclaw_runtime"
                or openclaw_session.get("error_layer") != "upstream"
            )
        )
    ):
        raise RuntimeError(f"openclaw-gateway-adapter session path was not safely deferred: {status} {openclaw_session}")
    print("ok  openclaw-gateway-adapter exposes facade/degraded semantics without broker activation")

    status, matrix = _request_json("GET", f"{GOVERNANCE_URL}/api/governance/write-authority")
    if status != 200 or "matrix" not in (matrix or {}):
        raise RuntimeError(f"governance write-authority unavailable: {status} {matrix}")
    print("ok  governance write-authority is readable")

    deploy_body = {
        "plan_id": plan_id,
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-smoke",
        "artifact_version": "1.0.0",
        "capital_pool_id": pool_id,
        "persona_capital_binding_id": persona_binding_id,
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "runtime_id": "runtime-smoke",
    }
    status, binding = _request_json(
        "POST",
        f"{RUNTIME_MANAGER_URL}/api/runtimes/deploy",
        body=deploy_body,
        headers={"Authorization": RUNTIME_TOKEN},
    )
    if status != 201 or not binding or "binding_id" not in binding:
        raise RuntimeError(f"runtime deploy failed: {status} {binding}")
    print(f"ok  runtime-manager created binding {binding['binding_id']}")

    telemetry_event = {
        "event_id": "evt-smoke-001",
        "event_type": "pnl_snapshot",
        "created_at": future_timestamp,
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": binding["binding_id"],
        "runtime_id": "runtime-smoke",
        "capital_pool_id": pool_id,
        "artifact_id": "artifact-smoke",
        "artifact_version": "1.0.0",
        "plan_id": plan_id,
        "persona_capital_binding_id": persona_binding_id,
        "target": {"strategy_id": "strategy-smoke"},
        "metrics": {"pnl": 1.25},
    }
    status, telemetry_result = _request_json(
        "POST",
        f"{TELEMETRY_URL}/api/telemetry/ingest",
        body=telemetry_event,
    )
    if status != 202:
        raise RuntimeError(f"telemetry ingest failed: {status} {telemetry_result}")
    print("ok  telemetry accepted an event linked to the runtime binding")

    status, drift_eval = _request_json(
        "POST",
        f"{RECONCILIATION_DRIFT_URL}/api/reconciliation-drift/evaluations",
        body={
            "binding_id": binding["binding_id"],
            "runtime_id": "runtime-smoke",
            "baseline_metrics": {"pnl": 1.0},
            "telemetry_events": [telemetry_event],
            "lineage_projection": {
                "target_id": binding["binding_id"],
                "derived_only": True,
            },
            "runtime_evidence": {
                "binding_id": binding["binding_id"],
                "runtime_id": "runtime-smoke",
                "status": binding.get("status", "active"),
            },
            "evidence_refs": [
                {"type": "telemetry_event", "id": telemetry_event["event_id"]},
                {"type": "runtime_binding", "id": binding["binding_id"]},
            ],
        },
    )
    if status != 201 or drift_eval.get("source_contract", {}).get("derived_only") is not True:
        raise RuntimeError(f"reconciliation-drift evaluation failed: {status} {drift_eval}")
    status, drift_summary = _request_json(
        "GET",
        f"{RECONCILIATION_DRIFT_URL}/api/reconciliation-drift/summary?{urllib.parse.urlencode({'binding_id': binding['binding_id']})}",
    )
    if status != 200 or drift_summary.get("emergency_control_chain_affected") is not False:
        raise RuntimeError(f"reconciliation-drift summary failed: {status} {drift_summary}")
    print("ok  reconciliation-drift-svc built a derived drift read model outside the emergency control chain")

    incident_body = {
        "incident_id": incident_id,
        "title": "Smoke incident",
        "status": "open",
        "severity": "high",
        "binding_id": binding["binding_id"],
        "deployment_stage": "paper",
        "deployment_plan_id": plan_id,
        "capital_pool_id": pool_id,
        "persona_capital_binding_id": persona_binding_id,
        "artifact_id": "artifact-smoke",
        "artifact_version": "1.0.0",
        "runtime_id": "runtime-smoke",
        "trace_id": f"trace-smoke-{suffix}",
        "evidence_summary": "Compose smoke incident",
        "telemetry_event_ids": [],
    }
    status, incident = _request_json("POST", f"{INCIDENTS_URL}/api/incidents", body=incident_body)
    if status != 201:
        raise RuntimeError(f"incident creation failed: {status} {incident}")
    print("ok  incident evidence service created an incident")

    postmortem_body = {
        "postmortem_id": postmortem_id,
        "title": "Smoke postmortem",
        "status": "draft",
        "incident_id": incident_id,
        "binding_id": binding["binding_id"],
        "deployment_stage": "paper",
        "deployment_plan_id": plan_id,
        "capital_pool_id": pool_id,
        "persona_capital_binding_id": persona_binding_id,
        "artifact_id": "artifact-smoke",
        "artifact_version": "1.0.0",
        "runtime_id": "runtime-smoke",
        "trace_id": f"trace-smoke-{suffix}",
        "root_cause": "Compose smoke validation",
        "contributing_factors": ["single-vm-smoke"],
        "timeline": [
            {"at": future_timestamp, "event": "stack_booted"},
            {"at": future_timestamp, "event": "incident_recorded"},
        ],
        "action_items": ["keep compose topology honest"],
        "author_ids": ["smoke-runner"],
    }
    status, postmortem = _request_json("POST", f"{POSTMORTEMS_URL}/api/postmortems", body=postmortem_body)
    if status != 201:
        raise RuntimeError(f"postmortem creation failed: {status} {postmortem}")
    print("ok  postmortem evidence service created a linked postmortem")

    consult_body = {
        "from_persona_id": "persona-alpha",
        "target_type": "persona",
        "target_ref": "persona-beta",
        "task": "Smoke-check consultation service boundary through the BFF.",
        "context_refs": [{"type": "deployment_plan", "id": plan_id}],
        "priority": "normal",
        "consultation_type": "risk_review",
    }
    status, consult_request = _request_json(
        "POST",
        f"{BFF_URL}/api/v1/consult/requests",
        body=consult_body,
        headers={"Authorization": OPERATOR_TOKEN},
    )
    if status != 200 or not consult_request or "request_id" not in consult_request:
        raise RuntimeError(f"BFF consultation request creation failed: {status} {consult_request}")
    status, service_request = _request_json(
        "GET",
        f"{CONSULTATION_URL}/api/consult/requests/{consult_request['request_id']}",
    )
    if status != 200 or service_request.get("request_id") != consult_request["request_id"]:
        raise RuntimeError(f"consultation-svc did not persist BFF request: {status} {service_request}")
    print("ok  consultation-svc persisted a BFF-created consult request")

    source_search_token = f"source-smoke-{suffix}"
    source_feed_server, source_feed_url = _serve_source_feed(
        {
            "next_watermark": future_timestamp,
            "records": [
                {
                    "source_id": f"src-smoke-note-{suffix}",
                    "title": "Compose smoke note",
                    "content_ref": f"memory://compose-smoke/{suffix}",
                    "metadata": {
                        "body": (
                            "Compose smoke source evidence should be durable for "
                            f"momentum volatility search {source_search_token}."
                        ),
                        "access_scope": ["operator", "research"],
                        "keywords": ["compose", "smoke", "momentum", "volatility", source_search_token],
                    },
                },
                {
                    "source_id": f"src-smoke-private-note-{suffix}",
                    "title": "Compose private smoke note",
                    "content_ref": f"memory://compose-smoke/private/{suffix}",
                    "metadata": {
                        "body": (
                            "Momentum volatility evidence that should be filtered before "
                            f"ranking {source_search_token}."
                        ),
                        "access_scope": ["risk-committee"],
                        "keywords": ["momentum", "volatility", source_search_token],
                    },
                },
            ],
        }
    )
    source_connector_body = {
        "connector": {
            "connector_id": "conn-smoke-notes",
            "source_type": "internal_note",
            "provider": "Pantheon smoke",
            "license_scope": "internal",
        },
        "fetch": {
            "mode": "external_feed",
            "network_scope": "internal_service",
            "url": source_feed_url,
            "allowed_url_prefixes": [source_feed_url.rsplit("/", 1)[0] + "/"],
            "timeout_seconds": 5,
            "max_bytes": 8192,
            "max_records": 10,
            "default_access_scope": ["operator", "research"],
        },
    }
    try:
        status, configured_source = _request_json(
            "POST",
            f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
            body=source_connector_body,
        )
        if status != 201 or configured_source.get("connector", {}).get("connector_id") != "conn-smoke-notes":
            raise RuntimeError(f"source-ingest connector configuration failed: {status} {configured_source}")
        source_ingest_body = {
            "connector_id": "conn-smoke-notes",
            "trace_id": f"trace-source-ingest-smoke-{suffix}",
            "trigger_type": "compose_smoke",
        }
        status, ingest_result = _request_json(
            "POST",
            f"{SOURCE_INGEST_URL}/api/source-ingest/jobs",
            body=source_ingest_body,
        )
        if status != 201 or ingest_result.get("run", {}).get("status") != "completed":
            raise RuntimeError(f"source-ingest job trigger failed: {status} {ingest_result}")
    finally:
        source_feed_server.shutdown()
        source_feed_server.server_close()
    run_id = ingest_result["run"]["ingest_run_id"]
    status, replayed_run = _request_json("GET", f"{SOURCE_INGEST_URL}/api/source-ingest/jobs/{run_id}")
    if status != 200 or replayed_run.get("run", {}).get("ingest_run_id") != run_id:
        raise RuntimeError(f"source-ingest did not replay run status: {status} {replayed_run}")
    status, replayed_watermark = _request_json(
        "GET",
        f"{SOURCE_INGEST_URL}/api/source-ingest/watermarks/conn-smoke-notes",
    )
    if status != 200 or replayed_watermark.get("watermark", {}).get("last_ingest_run_id") != run_id:
        raise RuntimeError(f"source-ingest did not replay watermark: {status} {replayed_watermark}")
    evidence_refs = ingest_result.get("evidence_refs") or {}
    evidence_bundle_id = evidence_refs.get("evidence_bundle_id")
    smoke_knowledge_object_id = (evidence_refs.get("knowledge_object_ids") or [""])[0]
    if not evidence_bundle_id:
        raise RuntimeError(f"source-ingest did not return evidence refs: {ingest_result}")
    if not smoke_knowledge_object_id:
        raise RuntimeError(f"source-ingest did not return knowledge object refs: {ingest_result}")
    status, source_record = _request_json(
        "GET",
        f"{SOURCE_INGEST_URL}/api/source-ingest/source-records/src-smoke-note-{suffix}",
    )
    if status != 200 or source_record.get("source_record", {}).get("source_id") != f"src-smoke-note-{suffix}":
        raise RuntimeError(f"source-ingest did not replay source record ref: {status} {source_record}")
    status, evidence_bundle = _request_json(
        "GET",
        f"{SOURCE_INGEST_URL}/api/source-ingest/evidence/bundles/{evidence_bundle_id}",
    )
    if status != 200 or evidence_bundle.get("bundle", {}).get("evidence_bundle_id") != evidence_bundle_id:
        raise RuntimeError(f"source-ingest did not replay evidence bundle ref: {status} {evidence_bundle}")
    print("ok  source-ingest autonomously fetched and persisted source/evidence refs")

    status, ingest_triggered_runs = _request_json(
        "GET",
        f"{SEARCH_URL}/api/search/index/pipeline-runs?limit=10",
    )
    if status != 200 or not any(
        run.get("triggered_by") == "ingest_completion" and run.get("trigger_ref") == run_id
        for run in ingest_triggered_runs.get("runs", [])
    ):
        raise RuntimeError(
            f"search-svc did not record ingest-completion index trigger for {run_id}: "
            f"{status} {ingest_triggered_runs}"
        )
    print("ok  search-svc recorded ingest-completion incremental index trigger")

    replay_connector_body = {
        "connector": {
            "connector_id": "conn-smoke-replay-notes",
            "source_type": "internal_note",
            "provider": "Pantheon smoke replay",
            "license_scope": "internal",
        },
        "fetch": {
            "mode": "static_records",
            "next_watermark": future_timestamp,
            "fail_until_attempt": 2,
            "failure_reason": "smoke replay fixture unavailable",
            "records": [
                {
                    "source_id": f"src-smoke-replayed-note-{suffix}",
                    "title": "Compose smoke replay note",
                    "content_ref": f"memory://compose-smoke/replay/{suffix}",
                }
            ],
        },
    }
    status, configured_replay_source = _request_json(
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
        body=replay_connector_body,
    )
    if status != 201 or configured_replay_source.get("connector", {}).get("connector_id") != "conn-smoke-replay-notes":
        raise RuntimeError(f"source-ingest replay connector configuration failed: {status} {configured_replay_source}")
    status, failed_ingest = _request_json(
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/jobs",
        body={
            "connector_id": "conn-smoke-replay-notes",
            "trace_id": f"trace-source-ingest-replay-smoke-{suffix}",
            "trigger_type": "compose_smoke_failure",
        },
    )
    if status != 201 or failed_ingest.get("run", {}).get("status") != "failed":
        raise RuntimeError(f"source-ingest did not route configured failure to DLQ: {status} {failed_ingest}")
    dlq_entry_id = failed_ingest.get("dlq_entries", [{}])[0].get("entry_id")
    if not dlq_entry_id:
        raise RuntimeError(f"source-ingest configured failure did not return DLQ entry: {failed_ingest}")
    status, replay_result = _request_json(
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/dlq/replay",
        body={
            "tag": "retry_exhausted",
            "entry_ids": [dlq_entry_id],
            "reason": "compose smoke replay after configured recovery",
        },
    )
    if status != 200 or replay_result.get("summary", {}).get("applied") != 1:
        raise RuntimeError(f"source-ingest DLQ replay failed: {status} {replay_result}")
    status, replayed_source_record = _request_json(
        "GET",
        f"{SOURCE_INGEST_URL}/api/source-ingest/source-records/src-smoke-replayed-note-{suffix}",
    )
    if status != 200 or replayed_source_record.get("source_record", {}).get("source_id") != f"src-smoke-replayed-note-{suffix}":
        raise RuntimeError(f"source-ingest did not persist replayed source record: {status} {replayed_source_record}")
    print("ok  source-ingest replayed a configured failure from DLQ")

    # Configure a dedicated connector for the autonomous scheduled-run path.
    sched_connector_body = {
        "connector": {
            "connector_id": "conn-smoke-scheduled",
            "source_type": "internal_note",
            "provider": "Pantheon smoke scheduler",
            "license_scope": "internal",
        },
        "fetch": {
            "mode": "static_records",
            "next_watermark": future_timestamp,
            "records": [
                {
                    "source_id": f"src-smoke-sched-{suffix}",
                    "title": "Smoke scheduled note",
                    "content_ref": f"memory://compose-smoke/sched/{suffix}",
                    "metadata": {
                        "body": f"Autonomous scheduled source evidence {source_search_token}",
                        "access_scope": ["operator", "research"],
                        "keywords": ["scheduled", "autonomous", source_search_token],
                    },
                }
            ],
        },
    }
    status, configured_sched = _request_json(
        "POST",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors",
        body=sched_connector_body,
    )
    if status != 201 or configured_sched.get("connector", {}).get("connector_id") != "conn-smoke-scheduled":
        raise RuntimeError(f"source-ingest scheduled connector configuration failed: {status} {configured_sched}")
    # Set schedule: interval_seconds=1 so the connector is immediately due.
    status, sched_config = _request_json(
        "PUT",
        f"{SOURCE_INGEST_URL}/api/source-ingest/connectors/conn-smoke-scheduled/schedule",
        body={"interval_seconds": 1, "enabled": True},
    )
    if status != 200 or not sched_config.get("schedule", {}).get("enabled"):
        raise RuntimeError(f"source-ingest schedule set failed: {status} {sched_config}")
    # Trigger autonomous run.
    status, scheduled_run = _request_json("POST", f"{SOURCE_INGEST_URL}/api/source-ingest/run-scheduled")
    if status != 200 or scheduled_run.get("summary", {}).get("total_ran", 0) < 1:
        raise RuntimeError(f"source-ingest run-scheduled failed: {status} {scheduled_run}")
    status, sched_source = _request_json(
        "GET",
        f"{SOURCE_INGEST_URL}/api/source-ingest/source-records/src-smoke-sched-{suffix}",
    )
    if status != 200 or sched_source.get("source_record", {}).get("source_id") != f"src-smoke-sched-{suffix}":
        raise RuntimeError(f"source-ingest scheduled run did not persist source record: {status} {sched_source}")
    print("ok  source-ingest autonomous scheduled connector ran and persisted evidence")

    status, search_index = _request_json("POST", f"{SEARCH_URL}/api/search/index/reload")
    if status != 200 or search_index.get("indexed_object_count", 0) < 2:
        raise RuntimeError(f"search-svc did not load persisted source evidence index: {status} {search_index}")

    status, pipeline_refresh = _request_json(
        "POST",
        f"{SEARCH_URL}/api/search/index/refresh",
        body={"triggered_by": "honest_stack_smoke", "trigger_ref": run_id},
    )
    pipeline_snapshot = pipeline_refresh.get("pipeline_snapshot", {}) if pipeline_refresh else {}
    if (
        status != 200
        or pipeline_snapshot.get("schema_version") != "index_pipeline_snapshot.v1"
        or pipeline_snapshot.get("indexed_count", 0) < 2
    ):
        raise RuntimeError(f"search-svc pipeline refresh failed: {status} {pipeline_refresh}")
    status, freshness = _request_json("GET", f"{SEARCH_URL}/api/search/index/freshness")
    if (
        status != 200
        or freshness.get("status") != "fresh"
        or freshness.get("last_pipeline_run_id") != pipeline_snapshot.get("pipeline_run_id")
    ):
        raise RuntimeError(f"search-svc freshness SLA status failed: {status} {freshness}")
    status, pipeline_runs = _request_json("GET", f"{SEARCH_URL}/api/search/index/pipeline-runs?limit=10")
    if status != 200 or not any(
        run.get("pipeline_run_id") == pipeline_snapshot.get("pipeline_run_id")
        for run in pipeline_runs.get("runs", [])
    ):
        raise RuntimeError(f"search-svc did not retain pipeline snapshot: {status} {pipeline_runs}")
    print("ok  search-svc refreshed schema-versioned index pipeline and reported freshness")

    search_body = {
        "request_id": f"search-smoke-{suffix}",
        "trace_id": f"trace-search-smoke-{suffix}",
        "query": f"momentum volatility {source_search_token}",
        "persona_id": "operator-workbench",
        "workspace_id": "research-workbench",
        "source_types": ["internal_note"],
        "filters_applied": {"smoke": "honest-stack"},
        "access_context": {
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "environment": "paper",
            "access_scopes": ["operator", "research"],
            "license_scopes": ["internal"],
        },
    }
    status, search_result = _request_json("POST", f"{SEARCH_URL}/api/search/query", body=search_body)
    if status != 200 or [item.get("result_id") for item in search_result.get("results", [])] != [smoke_knowledge_object_id]:
        raise RuntimeError(f"search-svc governed query failed: {status} {search_result}")
    status, search_snapshot = _request_json(
        "GET",
        f"{SEARCH_URL}/api/search/snapshots/{search_body['request_id']}",
    )
    if status != 200 or search_snapshot.get("snapshot", {}).get("request_id") != search_body["request_id"]:
        raise RuntimeError(f"search-svc did not replay index snapshot: {status} {search_snapshot}")
    print("ok  search-svc applied governed filters and replayed search refs")

    # Materialize the search index from autonomous ingest evidence.
    status, materialized = _request_json("POST", f"{SEARCH_URL}/api/search/index/materialize")
    if status != 200 or materialized.get("indexed_object_count", 0) < 2:
        raise RuntimeError(f"search-svc materialized index failed or has too few objects: {status} {materialized}")
    status, replayed_materialize = _request_json("GET", f"{SEARCH_URL}/api/search/index/materialize")
    if status != 200 or replayed_materialize.get("materialized_at") != materialized.get("materialized_at"):
        raise RuntimeError(f"search-svc did not replay materialized index: {status} {replayed_materialize}")
    print("ok  search-svc materialized index from autonomous ingest evidence and replayed state")

    trainer_body = {
        "persona_id": "persona-alpha",
        "objective": "Smoke-check append-only teaching and preview replay boundary.",
        "context_refs": [{"type": "deployment_plan", "id": plan_id}],
        "actor_id": "smoke-operator",
    }
    status, trainer_session = _request_json("POST", f"{TRAINING_SESSION_URL}/api/training/sessions", body=trainer_body)
    if status != 201 or not trainer_session.get("session_id"):
        raise RuntimeError(f"training-session session creation failed: {status} {trainer_session}")
    trainer_session_id = trainer_session["session_id"]
    status, event_result = _request_json(
        "POST",
        f"{TRAINING_SESSION_URL}/api/training/sessions/{trainer_session_id}/events",
        body={"actor": "operator", "event_type": "message", "message_body": "Tighten risk response."},
    )
    if status != 201 or event_result.get("event", {}).get("sequence_number") != 1:
        raise RuntimeError(f"training-session event append failed: {status} {event_result}")
    status, preview = _request_json(
        "POST",
        f"{TRAINING_SESSION_URL}/api/training/sessions/{trainer_session_id}/preview",
        body={"mode": "refresh"},
    )
    if status != 201 or preview.get("session_id") != trainer_session_id:
        raise RuntimeError(f"training-session preview refresh failed: {status} {preview}")
    print("ok  training-session-svc persisted a session event and preview")

    policy_job_body = {
        "policy_id": "persona-alpha-routing",
        "objective": "Smoke-check non-production policy-learning lifecycle.",
        "adapter": "stub",
        "requested_mode": "stub",
        "source_refs": [{"type": "training_session", "id": trainer_session_id}],
        "actor_id": "smoke-operator",
    }
    status, policy_job = _request_json("POST", f"{POLICY_LEARNING_URL}/api/policy-learning/jobs", body=policy_job_body)
    if status != 201 or policy_job.get("status") != "proposed":
        raise RuntimeError(f"policy-learning stub proposal failed: {status} {policy_job}")
    status, production_rejection = _request_json(
        "POST",
        f"{POLICY_LEARNING_URL}/api/policy-learning/jobs",
        body={
            "policy_id": "persona-alpha-routing",
            "objective": "Production activation must remain disabled.",
            "adapter": "qlib",
            "requested_mode": "production",
            "actor_id": "smoke-operator",
        },
    )
    if status != 201 or production_rejection.get("rejection", {}).get("reason") != "production_adapter_disabled":
        raise RuntimeError(f"policy-learning production rejection failed: {status} {production_rejection}")
    print("ok  policy-learning-svc persisted stub lifecycle and rejected production adapter")

    status, research_task = _request_json(
        "POST",
        f"{RESEARCH_ORCHESTRATOR_URL}/api/research-orchestrator/tasks",
        body={
            "title": "Compose smoke research task",
            "objective": "Verify research orchestration lifecycle and handoff boundary.",
            "source_refs": [{"type": "search_snapshot", "id": search_body["request_id"]}],
            "actor_id": "smoke-operator",
        },
    )
    if status != 201 or not research_task.get("task_id"):
        raise RuntimeError(f"research-orchestrator task creation failed: {status} {research_task}")
    status, research_run = _request_json(
        "POST",
        f"{RESEARCH_ORCHESTRATOR_URL}/api/research-orchestrator/tasks/{research_task['task_id']}/runs",
        body={"adapter": "stub", "requested_mode": "stub", "dispatch_mode": "stub", "actor_id": "smoke-operator"},
    )
    if status != 201 or research_run.get("status") != "queued":
        raise RuntimeError(f"research-orchestrator stub dispatch failed: {status} {research_run}")
    status, research_artifact = _request_json(
        "POST",
        f"{RESEARCH_ORCHESTRATOR_URL}/api/research-orchestrator/runs/{research_run['run_id']}/artifacts",
        body={
            "artifact_type": "strategy_spec",
            "artifact_family": "research_signal",
            "title": "Compose smoke research artifact",
            "storage_ref": f"memory://research-orchestrator/{suffix}/strategy-spec",
            "checksum": "sha256:compose-smoke",
            "actor_id": "smoke-operator",
        },
    )
    if status != 201 or research_artifact.get("artifact_state") != "draft":
        raise RuntimeError(f"research-orchestrator artifact handoff failed: {status} {research_artifact}")
    status, research_rejection = _request_json(
        "POST",
        f"{RESEARCH_ORCHESTRATOR_URL}/api/research-orchestrator/tasks/{research_task['task_id']}/runs",
        body={"adapter": "qlib", "requested_mode": "production", "dispatch_mode": "stub", "actor_id": "smoke-operator"},
    )
    if status != 201 or research_rejection.get("rejection", {}).get("reason") != "production_adapter_disabled":
        raise RuntimeError(f"research-orchestrator production rejection failed: {status} {research_rejection}")
    print("ok  research-orchestrator-svc persisted lifecycle/handoff and rejected production adapter")

    status, guidance = _request_json(
        "GET",
        f"{BFF_URL}/api/v1/operator/degraded-control-guidance",
        headers={"Authorization": APPROVER_TOKEN},
    )
    if status != 200 or guidance.get("data", {}).get("current_state") != "fresh":
        raise RuntimeError(f"operator BFF honest-mode guidance failed: {status} {guidance}")
    print("ok  operator-bff is in fresh honest mode")

    _verify_sse_replay()
    print("stack smoke passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"smoke failed: HTTP {exc.code} {body}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
