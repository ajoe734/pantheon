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
SEARCH_URL = os.getenv("SEARCH_URL", "http://127.0.0.1:8098")
POLICY_LEARNING_URL = os.getenv("POLICY_LEARNING_URL", "http://127.0.0.1:8100")

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

    _wait_for_health("runtime-manager", f"{RUNTIME_MANAGER_URL}/__health__")
    _wait_for_health("governance", f"{GOVERNANCE_URL}/health")
    _wait_for_health("telemetry", f"{TELEMETRY_URL}/__health__")
    _wait_for_health("incidents", f"{INCIDENTS_URL}/__health__")
    _wait_for_health("postmortems", f"{POSTMORTEMS_URL}/__health__")
    _wait_for_health("capital", f"{CAPITAL_URL}/health")
    _wait_for_health("deployment", f"{DEPLOYMENT_URL}/health")
    _wait_for_health("evolution", f"{EVOLUTION_URL}/health")
    _wait_for_health("lineage-read", f"{LINEAGE_READ_URL}/__health__")
    _wait_for_health("operator-bff", f"{BFF_URL}/health")
    _wait_for_health("persona", f"{PERSONA_URL}/health")
    _wait_for_health("router", f"{ROUTER_URL}/health")
    _wait_for_health("evaluation", f"{EVALUATION_URL}/__health__")
    _wait_for_health("feedback", f"{FEEDBACK_URL}/__health__")
    _wait_for_health("memory", f"{MEMORY_URL}/__health__")
    _wait_for_health("registry", f"{REGISTRY_URL}/__health__")
    _wait_for_health("optimizer-svc", f"{OPTIMIZER_URL}/__health__")
    _wait_for_health("promotion", f"{PROMOTION_URL}/__health__")
    _wait_for_health("search-svc", f"{SEARCH_URL}/health")
    _wait_for_health("policy-learning-svc", f"{POLICY_LEARNING_URL}/health")

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

    search_body = {
        "request_id": f"search-smoke-{suffix}",
        "trace_id": f"trace-search-smoke-{suffix}",
        "query": "momentum volatility",
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
        "documents": [
            {
                "result_id": f"search-doc-{suffix}",
                "match_type": "ticket",
                "title": "Search smoke note",
                "excerpt": "Momentum volatility evidence should survive governed search.",
                "content_ref": f"/research/tickets/search-doc-{suffix}",
                "relevance_score": 0.8,
            },
            {
                "result_id": f"search-private-{suffix}",
                "match_type": "ticket",
                "title": "Private search smoke note",
                "excerpt": "Momentum volatility evidence that should be filtered.",
                "content_ref": f"/research/tickets/search-private-{suffix}",
                "access_scope": ["risk-committee"],
                "relevance_score": 0.99,
            },
        ],
    }
    status, search_result = _request_json("POST", f"{SEARCH_URL}/api/search/query", body=search_body)
    if status != 200 or [item.get("result_id") for item in search_result.get("results", [])] != [f"search-doc-{suffix}"]:
        raise RuntimeError(f"search-svc governed query failed: {status} {search_result}")
    status, search_snapshot = _request_json(
        "GET",
        f"{SEARCH_URL}/api/search/snapshots/{search_body['request_id']}",
    )
    if status != 200 or search_snapshot.get("snapshot", {}).get("request_id") != search_body["request_id"]:
        raise RuntimeError(f"search-svc did not replay index snapshot: {status} {search_snapshot}")
    print("ok  search-svc applied governed filters and replayed search refs")


    policy_job_body = {
        "policy_id": "persona-alpha-routing",
        "objective": "Smoke-check non-production policy-learning lifecycle.",
        "adapter": "stub",
        "requested_mode": "stub",
        "source_refs": [{"type": "deployment_plan", "id": plan_id}],
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
