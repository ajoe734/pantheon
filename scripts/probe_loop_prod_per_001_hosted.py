#!/usr/bin/env python3
"""Hosted product-level probe for LOOP-PROD-PER-001.

The probe mints a short-lived dev-only JWT from the GitHub dev environment
secret, creates a dynamic paper Persona through the public BFF, then polls the
explicit provisioning reconciler until canonical runtime/schedule readback
converges or fails closed. The access token is never printed or written.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_ID = "LOOP-PROD-PER-001"
FIRST_EVALUATION_WORKFLOW_ID = "pantheon.persona.first-evaluation"
DEFAULT_BASE_URL = "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io"
DEFAULT_TENANT_ID = "pantheon-dev"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def mint_hs256_jwt(
    *,
    secret: str,
    issuer: str,
    audience: str,
    subject: str,
    tenant_id: str,
    run_id: str,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": subject,
        "user_id": subject,
        "sid": f"{TASK_ID.lower()}-{secrets.token_urlsafe(12)}",
        "roles": ["operator"],
        "app_metadata": {"tenant_id": tenant_id},
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "nbf": now - 30,
        "exp": now + 3600,
        "jti": secrets.token_urlsafe(18),
        "token_use": "pantheon-dev-hosted-proof",
        "tenant_id": tenant_id,
        "allowed_tenants": [tenant_id],
        "proof_task_id": TASK_ID,
        "proof_run_id": run_id,
    }
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    encoded_claims = b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_claims}.{b64url(signature)}"


def request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "X-Correlation-Id": f"{TASK_ID.lower()}-{secrets.token_hex(8)}",
    }
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    if headers:
        request_headers.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    started = utc_now()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            parsed = json.loads(raw) if raw else None
            return {
                "method": method,
                "url": url,
                "started_at": started,
                "completed_at": utc_now(),
                "status": response.status,
                "ok": 200 <= response.status < 300,
                "headers": response_headers(response.headers),
                "json": parsed,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = {"unparsed_body_prefix": raw[:500]}
        return {
            "method": method,
            "url": url,
            "started_at": started,
            "completed_at": utc_now(),
            "status": exc.code,
            "ok": False,
            "headers": response_headers(exc.headers),
            "json": parsed,
        }


def response_headers(headers: Any) -> dict[str, str]:
    keep = {
        "x-request-id",
        "x-correlation-id",
        "content-type",
        "retry-after",
    }
    return {
        key.lower(): value
        for key, value in headers.items()
        if key.lower() in keep
    }


def body_data(response: dict[str, Any]) -> dict[str, Any]:
    payload = response.get("json")
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return {}


def body_meta(response: dict[str, Any]) -> dict[str, Any]:
    payload = response.get("json")
    if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
        return payload["meta"]
    return {}


def error_reason(response: dict[str, Any]) -> str | None:
    payload = response.get("json")
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    details = error.get("details")
    if isinstance(details, dict):
        reason = details.get("reason") or details.get("precondition_failed")
        if reason:
            return str(reason)
    return str(error.get("code") or "") or None


def check(evidence: dict[str, Any], name: str, passed: bool, details: dict[str, Any] | None = None) -> None:
    evidence["checks"].append(
        {
            "name": name,
            "status": "pass" if passed else "fail",
            "details": details or {},
            "recorded_at": utc_now(),
        }
    )


def persona_payload(persona_name: str) -> dict[str, Any]:
    return {
        "name": persona_name,
        "risk": "low",
        "archetype": "market-neutral-scout",
        "mandate": "Hosted paper provisioning proof for LOOP-PROD-PER-001.",
        "strategyFamily": "paper-evaluation",
        "traits": {
            "instruments": ["SPY"],
            "risk_appetite": "low",
            "decision_style": "evidence_first",
            "time_horizon": "intraday",
            "hard_rules": ["paper_only"],
            "persona_voice": "concise",
        },
        "requiredDataSources": [
            {
                "source": "market_data",
                "market": "US",
                "mode": "paper",
            }
        ],
    }


def append_call(evidence: dict[str, Any], key: str, response: dict[str, Any]) -> dict[str, Any]:
    evidence["calls"][key] = response
    return response


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    expected_sha = args.expected_sha or os.getenv("GITHUB_SHA", "")
    run_id = args.run_id or "-".join(
        value
        for value in (
            os.getenv("GITHUB_RUN_ID"),
            os.getenv("GITHUB_RUN_ATTEMPT"),
            secrets.token_hex(4),
        )
        if value
    )
    tenant_id = args.tenant_id
    subject = f"pantheon-dev-proof-loop-prod-per-001-{run_id}"
    secret = (
        os.getenv("DEV_BFF_JWT_SECRET")
        or os.getenv("PANTHEON_BFF_JWT_SECRET")
        or ""
    )
    issuer = os.getenv("DEV_BFF_JWT_ISSUER") or os.getenv("PANTHEON_BFF_JWT_ISSUER") or "pantheon-dev"
    audience = (
        os.getenv("DEV_BFF_JWT_AUDIENCE")
        or os.getenv("PANTHEON_BFF_JWT_AUDIENCE")
        or "bff-operators"
    )
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "task_id": TASK_ID,
        "generated_at": utc_now(),
        "target": {
            "base_url": base_url,
            "expected_source_commit_sha": expected_sha,
            "tenant_id": tenant_id,
        },
        "actor": {
            "agent": "Codex2",
            "subject": subject,
            "roles": ["operator"],
            "token_written": False,
        },
        "identity": {
            "run_id": run_id,
            "github_run_url": os.getenv("GITHUB_SERVER_URL", "https://github.com")
            + "/"
            + os.getenv("GITHUB_REPOSITORY", "ajoe734/pantheon")
            + "/actions/runs/"
            + os.getenv("GITHUB_RUN_ID", ""),
        },
        "checks": [],
        "calls": {},
        "polls": [],
        "result": {
            "status": "failed",
            "reason": "probe_incomplete",
        },
    }

    if not secret:
        check(evidence, "jwt_secret.available", False, {"reason": "DEV_BFF_JWT_SECRET absent"})
        evidence["result"] = {"status": "blocked", "reason": "DEV_BFF_JWT_SECRET absent"}
        return evidence

    token = mint_hs256_jwt(
        secret=secret,
        issuer=issuer,
        audience=audience,
        subject=subject,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    check(evidence, "jwt.minted_in_memory", True, {"issuer": issuer, "audience": audience})

    version = append_call(evidence, "version", request_json("GET", f"{base_url}/bff/version"))
    version_data = version.get("json") if isinstance(version.get("json"), dict) else {}
    config_posture = version_data.get("config_posture") if isinstance(version_data, dict) else {}
    check(
        evidence,
        "deployment_identity.exact_sha",
        version.get("status") == 200 and version_data.get("source_commit_sha") == expected_sha,
        {
            "observed_source_commit_sha": version_data.get("source_commit_sha"),
            "expected_source_commit_sha": expected_sha,
            "build_time": version_data.get("build_time"),
        },
    )
    check(
        evidence,
        "auth_posture.strict_no_stub",
        isinstance(config_posture, dict)
        and config_posture.get("auth_stub") is False
        and config_posture.get("auth_mode") == "strict",
        {"config_posture": config_posture},
    )

    ready = append_call(evidence, "readyz", request_json("GET", f"{base_url}/readyz"))
    ready_json = ready.get("json") if isinstance(ready.get("json"), dict) else {}
    check(
        evidence,
        "readyz.dependencies_ok",
        ready.get("status") == 200
        and ready_json.get("ready") is True
        and all(
            dep.get("status") == "ok"
            for dep in (ready_json.get("dependencies") or {}).values()
            if isinstance(dep, dict)
        ),
        {"ready": ready_json.get("ready"), "dependencies": ready_json.get("dependencies")},
    )

    create_url = f"{base_url}/bff/personas"
    unauth = append_call(
        evidence,
        "negative_create_without_auth",
        request_json(
            "POST",
            create_url,
            body={"name": f"{TASK_ID} unauth {run_id}", "risk": "low"},
            headers={"Idempotency-Key": f"{TASK_ID.lower()}-unauth-{run_id}"},
        ),
    )
    check(
        evidence,
        "security.create_requires_auth",
        unauth.get("status") == 401,
        {"status": unauth.get("status"), "reason": error_reason(unauth)},
    )

    wrong_tenant = append_call(
        evidence,
        "negative_create_wrong_tenant",
        request_json(
            "POST",
            create_url,
            token=token,
            body={"name": f"{TASK_ID} wrong tenant {run_id}", "risk": "low", "tenantId": "tenant-other"},
            headers={"Idempotency-Key": f"{TASK_ID.lower()}-wrong-tenant-{run_id}"},
        ),
    )
    check(
        evidence,
        "security.create_rejects_cross_tenant",
        wrong_tenant.get("status") == 403,
        {"status": wrong_tenant.get("status"), "reason": error_reason(wrong_tenant)},
    )

    live_capital = append_call(
        evidence,
        "negative_create_live_capital",
        request_json(
            "POST",
            create_url,
            token=token,
            body={"name": f"{TASK_ID} live capital {run_id}", "risk": "low", "capitalMode": "live"},
            headers={"Idempotency-Key": f"{TASK_ID.lower()}-live-capital-{run_id}"},
        ),
    )
    check(
        evidence,
        "security.create_rejects_live_capital",
        live_capital.get("status") == 422,
        {"status": live_capital.get("status"), "reason": error_reason(live_capital)},
    )

    unique_suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    dynamic_persona_name = f"{TASK_ID} hosted proof {unique_suffix}"
    payload = persona_payload(dynamic_persona_name)
    idempotency_key = f"{TASK_ID.lower()}-hosted-{unique_suffix}"
    evidence["persona_request"] = {
        "name": dynamic_persona_name,
        "idempotency_key": idempotency_key,
        "payload": payload,
    }

    create = append_call(
        evidence,
        "persona_create",
        request_json(
            "POST",
            create_url,
            token=token,
            body=payload,
            headers={"Idempotency-Key": idempotency_key},
            timeout=120,
        ),
    )
    create_data = body_data(create)
    persona_id = str(create_data.get("id") or "")
    check(
        evidence,
        "persona.create_initial_state_provisioning",
        create.get("status") == 201
        and create_data.get("state") == "provisioning"
        and not create_data.get("runtimeId")
        and not create_data.get("runtimeBindingId"),
        {
            "status": create.get("status"),
            "persona_id": persona_id,
            "state": create_data.get("state"),
            "runtimeId": create_data.get("runtimeId"),
            "runtimeBindingId": create_data.get("runtimeBindingId"),
        },
    )
    if not persona_id:
        evidence["result"] = {"status": "failed", "reason": "persona_create_missing_id"}
        return evidence

    duplicate = append_call(
        evidence,
        "persona_create_idempotency_retry",
        request_json(
            "POST",
            create_url,
            token=token,
            body=payload,
            headers={"Idempotency-Key": idempotency_key},
            timeout=60,
        ),
    )
    duplicate_data = body_data(duplicate)
    check(
        evidence,
        "persona.duplicate_retry_converges",
        duplicate.get("status") == 201 and duplicate_data.get("id") == persona_id,
        {
            "status": duplicate.get("status"),
            "persona_id": persona_id,
            "duplicate_persona_id": duplicate_data.get("id"),
            "state": duplicate_data.get("state"),
        },
    )

    final_reconcile: dict[str, Any] | None = None
    final_detail: dict[str, Any] | None = None
    deadline = time.monotonic() + args.timeout_seconds
    attempt = 0
    while time.monotonic() <= deadline:
        attempt += 1
        if attempt > 1:
            time.sleep(args.poll_seconds)
        reconcile = request_json(
            "POST",
            f"{base_url}/bff/personas/{persona_id}/provisioning/reconcile",
            token=token,
            timeout=60,
        )
        detail = request_json("GET", f"{base_url}/bff/personas/{persona_id}", token=token)
        reconcile_data = body_data(reconcile)
        detail_data = body_data(detail)
        state = str(reconcile_data.get("state") or detail_data.get("state") or "")
        poll_row = {
            "attempt": attempt,
            "reconcile_status": reconcile.get("status"),
            "detail_status": detail.get("status"),
            "state": state,
            "runtimeId": reconcile_data.get("runtimeId") or detail_data.get("runtimeId"),
            "runtimeBindingId": reconcile_data.get("runtimeBindingId") or detail_data.get("runtimeBindingId"),
            "deploymentPlanId": reconcile_data.get("deploymentPlanId") or detail_data.get("deploymentPlanId"),
            "reconcile_meta": body_meta(reconcile),
            "observed_at": utc_now(),
        }
        evidence["polls"].append(poll_row)
        final_reconcile = reconcile
        final_detail = detail
        if state == "paper_running":
            break
        if state in {"failed", "provisioning_failed"}:
            break

    final_data = body_data(final_reconcile or {}) or body_data(final_detail or {})
    if final_detail is not None and body_data(final_detail).get("state") == "paper_running":
        final_data = body_data(final_detail)
    runtime_id = str(final_data.get("runtimeId") or "")
    runtime_binding_id = str(final_data.get("runtimeBindingId") or "")
    deployment_plan_id = str(final_data.get("deploymentPlanId") or "")
    final_meta = body_meta(final_reconcile or {})
    check(
        evidence,
        "persona.reconcile_terminal_paper_running",
        final_data.get("state") == "paper_running"
        and bool(runtime_id)
        and bool(runtime_binding_id)
        and bool(deployment_plan_id)
        and final_meta.get("status") == "ok"
        and not final_meta.get("degraded_dependencies"),
        {
            "state": final_data.get("state"),
            "runtimeId": runtime_id,
            "runtimeBindingId": runtime_binding_id,
            "deploymentPlanId": deployment_plan_id,
            "reconcile_meta": final_meta,
        },
    )
    evidence["final_persona"] = {
        "detail": final_detail,
        "reconcile": final_reconcile,
    }

    runtime_binding = None
    runtime_status = None
    deployment_plan = None
    if runtime_binding_id:
        runtime_binding = append_call(
            evidence,
            "runtime_binding_detail",
            request_json(
                "GET",
                f"{base_url}/api/v1/runtime-bindings/{runtime_binding_id}",
                token=token,
            ),
        )
    if runtime_id:
        runtime_status = append_call(
            evidence,
            "runtime_status",
            request_json(
                "GET",
                f"{base_url}/api/v1/runtimes/{runtime_id}/status",
                token=token,
            ),
        )
    if deployment_plan_id:
        deployment_plan = append_call(
            evidence,
            "deployment_plan_detail",
            request_json(
                "GET",
                f"{base_url}/api/v1/deployment-plans/{deployment_plan_id}",
                token=token,
            ),
        )

    runtime_binding_data = body_data(runtime_binding or {})
    runtime_status_data = body_data(runtime_status or {})
    deployment_plan_data = body_data(deployment_plan or {})
    plan_artifact_id = str(deployment_plan_data.get("artifact_id") or "")
    binding_artifact_id = str(runtime_binding_data.get("artifact_id") or "")
    check(
        evidence,
        "runtime_binding.authoritative_readback",
        bool(runtime_binding)
        and runtime_binding.get("status") == 200
        and (
            runtime_binding_data.get("runtime_binding_id") == runtime_binding_id
            or runtime_binding_data.get("binding_id") == runtime_binding_id
            or runtime_binding_data.get("id") == runtime_binding_id
        )
        and runtime_binding_data.get("runtime_id") == runtime_id
        and (not binding_artifact_id or binding_artifact_id.startswith("reg-strategy-artifact-")),
        {
            "status": (runtime_binding or {}).get("status"),
            "runtime_binding_id": runtime_binding_id,
            "runtime_id": runtime_id,
            "artifact_id": binding_artifact_id,
            "deployment_mode": runtime_binding_data.get("deployment_mode"),
        },
    )
    check(
        evidence,
        "runtime_status.authoritative_readback",
        bool(runtime_status)
        and runtime_status.get("status") == 200
        and (
            runtime_status_data.get("runtime_binding_id") == runtime_binding_id
            or runtime_status_data.get("binding_id") == runtime_binding_id
            or runtime_status_data.get("id") == runtime_binding_id
        ),
        {
            "status": (runtime_status or {}).get("status"),
            "runtime_binding_id": runtime_binding_id,
            "runtime_id": runtime_id,
        },
    )
    check(
        evidence,
        "deployment_plan.uses_strategy_artifact",
        bool(deployment_plan)
        and deployment_plan.get("status") == 200
        and deployment_plan_data.get("id") == deployment_plan_id
        and plan_artifact_id.startswith("reg-strategy-artifact-")
        and (not binding_artifact_id or binding_artifact_id == plan_artifact_id),
        {
            "status": (deployment_plan or {}).get("status"),
            "deployment_plan_id": deployment_plan_id,
            "artifact_id": plan_artifact_id,
            "binding_artifact_id": binding_artifact_id,
        },
    )

    runtime_profile = append_call(
        evidence,
        "persona_runtime_profile",
        request_json("GET", f"{base_url}/bff/personas/{persona_id}/runtime-profile", token=token),
    )
    capabilities = append_call(
        evidence,
        "persona_capabilities",
        request_json("GET", f"{base_url}/bff/personas/{persona_id}/capabilities", token=token),
    )
    evaluations = append_call(
        evidence,
        "persona_evaluations",
        request_json("GET", f"{base_url}/bff/personas/{persona_id}/evaluations", token=token),
    )
    capability_data = body_data(capabilities)
    effective_workflows = capability_data.get("effectiveWorkflows") or []
    create_meta = body_meta(create)
    check(
        evidence,
        "first_evaluation_schedule.readback_gated_final_state",
        final_data.get("state") == "paper_running"
        and final_meta.get("status") == "ok"
        and create_meta.get("first_evaluation_workflow_id") == FIRST_EVALUATION_WORKFLOW_ID,
        {
            "workflow_id": FIRST_EVALUATION_WORKFLOW_ID,
            "create_meta_workflow_id": create_meta.get("first_evaluation_workflow_id"),
            "reconcile_meta": final_meta,
            "effective_workflows": effective_workflows,
            "evaluations_status": evaluations.get("status"),
            "runtime_profile_status": runtime_profile.get("status"),
            "capabilities_status": capabilities.get("status"),
        },
    )

    failed = [row for row in evidence["checks"] if row["status"] != "pass"]
    if failed:
        evidence["result"] = {
            "status": "failed",
            "reason": "checks_failed",
            "failed_checks": [row["name"] for row in failed],
            "persona_id": persona_id,
        }
    else:
        evidence["result"] = {
            "status": "passed",
            "persona_id": persona_id,
            "runtime_id": runtime_id,
            "runtime_binding_id": runtime_binding_id,
            "deployment_plan_id": deployment_plan_id,
            "strategy_artifact_id": plan_artifact_id,
        }
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("BFF_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--expected-sha", default=os.getenv("EXPECTED_SHA", ""))
    parser.add_argument("--tenant-id", default=os.getenv("TENANT_ID") or DEFAULT_TENANT_ID)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout-seconds", type=int, default=420)
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    evidence = run_probe(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "task_id": TASK_ID,
                "result": evidence.get("result"),
                "checks": {
                    "passed": sum(1 for row in evidence.get("checks", []) if row.get("status") == "pass"),
                    "failed": [row.get("name") for row in evidence.get("checks", []) if row.get("status") != "pass"],
                },
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if evidence.get("result", {}).get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
