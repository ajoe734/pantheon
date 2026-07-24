#!/usr/bin/env python3
"""Bounded hosted Strategy Workshop qualification for AG-HOSTED-CLOSE-001.

Run this program inside the deployed operator-bff container.  It obtains
short-lived, server-bound dev-login tokens from credentials already present in
the container environment.  Tokens and credentials are never printed.

The probe uses only product HTTP APIs:

* Registry creates the initial StrategySpec.
* BFF creates and updates the owner-scoped workshop.
* Governance creates, reviews, and decides the formal approval.

It deliberately does not edit any backing store.  A non-zero exit means the
hosted closeout is blocked; the redacted JSON report remains suitable for task
evidence.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


BFF_URL = os.getenv("AG_HOSTED_BFF_URL", "http://127.0.0.1:8001").rstrip("/")
REGISTRY_URL = os.getenv("AG_HOSTED_REGISTRY_URL", "http://registry:8087").rstrip("/")
GOVERNANCE_URL = os.getenv(
    "AG_HOSTED_GOVERNANCE_URL",
    "http://governance:8082",
).rstrip("/")
EXPECTED_BFF_SHA = os.getenv(
    "AG_HOSTED_EXPECTED_BFF_SHA",
    "00b38f41ec51296762d502c4bd5732f95ccf2953",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def http_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    body = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return (
                response.status,
                json.loads(raw) if raw else {},
                {key.lower(): value for key, value in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"detail": "non-JSON HTTP error"}
        return (
            exc.code,
            parsed,
            {key.lower(): value for key, value in exc.headers.items()},
        )


def error_summary(body: dict[str, Any]) -> dict[str, Any]:
    top_level_error = body.get("error")
    if isinstance(top_level_error, dict):
        details = top_level_error.get("details")
        return {
            "code": top_level_error.get("code"),
            "message": top_level_error.get("message"),
            "reason": details.get("reason") if isinstance(details, dict) else details,
            "precondition_failed": (
                details.get("precondition_failed")
                if isinstance(details, dict)
                else None
            ),
        }
    detail = body.get("detail")
    if isinstance(detail, dict):
        error = detail.get("error")
        if isinstance(error, dict):
            details = error.get("details")
            return {
                "code": error.get("code"),
                "message": error.get("message"),
                "reason": details.get("reason") if isinstance(details, dict) else details,
                "precondition_failed": (
                    details.get("precondition_failed")
                    if isinstance(details, dict)
                    else None
                ),
            }
        return {
            "code": detail.get("code"),
            "message": detail.get("message"),
            "reason": detail.get("reason"),
        }
    return {"message": detail or body.get("message")}


def jwt_claims(token: str) -> dict[str, Any]:
    encoded = token.split(".")[1]
    encoded += "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))


def login(identity: str) -> tuple[str, dict[str, Any]]:
    prefix = f"PANTHEON_BFF_DEV_LOGIN_{identity.upper()}"
    client_id = os.getenv(f"{prefix}_CLIENT_ID", "")
    client_secret = os.getenv(f"{prefix}_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(f"server-bound dev-login identity is unavailable: {identity}")
    status, body, _ = http_json(
        "POST",
        f"{BFF_URL}/bff/auth/dev-login",
        payload={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if status != 200 or not body.get("access_token"):
        raise RuntimeError(f"dev-login failed for {identity}: HTTP {status}")
    token = str(body["access_token"])
    claims = jwt_claims(token)
    return token, {
        "identity": claims.get("identity"),
        "subject": claims.get("sub"),
        "roles": claims.get("roles"),
        "tenant_id": claims.get("tenant_id"),
        "mfa_verified": bool(claims.get("mfa_verified")),
        "token_use": claims.get("token_use"),
    }


def scoped_headers(
    token: str,
    tenant_id: str,
    *,
    idempotency_key: str | None = None,
    etag: str | None = None,
    request_id: str | None = None,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if etag:
        headers["If-Match"] = etag
    if request_id:
        headers["X-Request-Id"] = request_id
    return headers


def main() -> int:
    run_key = f"ag-hosted-close-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    report: dict[str, Any] = {
        "schema_version": "pantheon.agora.hosted-closeout-probe.v1",
        "task_id": "AG-HOSTED-CLOSE-001",
        "run_key": run_key,
        "started_at": utc_now(),
        "expected_bff_sha": EXPECTED_BFF_SHA,
        "safety": {
            "environment": "dev",
            "execution_authority": "none",
            "live_capital_changed": False,
            "backing_stores_edited_directly": False,
            "credentials_or_tokens_emitted": False,
        },
        "identities": {},
        "checks": [],
        "resources": {},
        "blockers": [],
    }

    def check(
        name: str,
        status: int,
        expected: int | tuple[int, ...],
        body: dict[str, Any],
        *,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        accepted = (expected,) if isinstance(expected, int) else expected
        passed = status in accepted
        item: dict[str, Any] = {
            "name": name,
            "http_status": status,
            "expected_http_status": list(accepted),
            "passed": passed,
        }
        if detail:
            item["detail"] = detail
        if not passed or status >= 400:
            item["error"] = error_summary(body)
        report["checks"].append(item)
        if not passed:
            report["blockers"].append(
                {
                    "check": name,
                    "reason": "unexpected_http_status",
                    "observed": status,
                    "expected": list(accepted),
                    "error": error_summary(body),
                }
            )
        return passed

    try:
        status, version, _ = http_json("GET", f"{BFF_URL}/bff/version")
        check(
            "exact_bff_runtime",
            status,
            200,
            version,
            detail={"source_commit_sha": version.get("source_commit_sha")},
        )
        if version.get("source_commit_sha") != EXPECTED_BFF_SHA:
            report["blockers"].append(
                {
                    "check": "exact_bff_runtime",
                    "reason": "runtime_sha_mismatch",
                    "observed": version.get("source_commit_sha"),
                    "expected": EXPECTED_BFF_SHA,
                }
            )

        tokens: dict[str, str] = {}
        for identity in ("viewer", "operator_a", "operator_b", "approver"):
            token, claims = login(identity)
            tokens[identity] = token
            report["identities"][identity] = claims

        tenant_id = str(report["identities"]["operator_a"]["tenant_id"])
        owner_user_id = str(report["identities"]["operator_a"]["subject"])
        approver_user_id = str(report["identities"]["approver"]["subject"])

        status, body, _ = http_json("GET", f"{BFF_URL}/bff/agora/me")
        check("anonymous_read_denied", status, 401, body)

        viewer_headers = scoped_headers(tokens["viewer"], tenant_id)
        status, body, _ = http_json(
            "POST",
            f"{BFF_URL}/bff/agora/workshops",
            headers={
                **viewer_headers,
                "Idempotency-Key": f"{run_key}-viewer-denied",
            },
            payload={"initial_message": "This viewer write must be denied."},
        )
        check("viewer_write_denied", status, 403, body)

        strategy_id = f"strategy-{run_key}"
        # The deployed workshop create route currently stores
        # strategy_spec_ref in both strategy_id and registry-id fields.  A
        # shared valid identifier lets the probe continue far enough to test
        # the downstream approval contract without editing either store.  The
        # separate-id failure is retained in the task evidence as a blocker.
        registry_id = strategy_id
        strategy_spec = {
            "spec_version": "1.0",
            "strategy_id": strategy_id,
            "title": "AG hosted closeout research strategy",
            "hypothesis": "A bounded momentum signal can be evaluated without execution.",
            "objective": "Qualify the hosted Strategy Workshop research-only workflow.",
            "lifecycle_state": "draft",
            "market_scope": {
                "symbols": ["RESEARCH_UNIVERSE"],
                "frequency": "1d",
            },
            "data_dependencies": [
                {"ref": "dataset:ag-hosted-closeout", "kind": "dataset"}
            ],
            "execution_profile": {
                "signal_schema_version": "1.0",
                "quantity_type": "PERCENT_PORTFOLIO",
                "execution_mode_hint": "research",
            },
            "evaluation_plan": {"metrics": ["sharpe_ratio"]},
            "governance": {
                "approval_required": True,
                "policy_id": "research-only-v1",
            },
            "provenance": {
                "source_kind": "manual",
                "created_at": utc_now(),
            },
            "metadata": {"task_id": "AG-HOSTED-CLOSE-001", "run_key": run_key},
        }
        status, body, _ = http_json(
            "POST",
            f"{REGISTRY_URL}/api/registry/strategy-specs",
            payload={
                "registry_id": registry_id,
                "strategy_id": strategy_id,
                "version": "1.0.0",
                "artifact_state": "draft",
                "source_seed_id": run_key,
                "lineage": {"source_run_ids": [run_key]},
                "metadata": {"task_id": "AG-HOSTED-CLOSE-001"},
                "strategy_spec": strategy_spec,
            },
        )
        if not check("registry_strategy_spec_created", status, 200, body):
            raise RuntimeError("Registry StrategySpec creation failed")
        report["resources"]["strategy_id"] = strategy_id
        report["resources"]["registry_id"] = registry_id
        report["resources"]["registry_strategy_identity_coalesced"] = True

        owner_headers = scoped_headers(tokens["operator_a"], tenant_id)
        status, body, _ = http_json(
            "POST",
            f"{BFF_URL}/bff/agora/workshops",
            headers={
                **owner_headers,
                "Idempotency-Key": f"{run_key}-workshop",
            },
            payload={
                "initial_message": "Qualify this strategy in research-only mode.",
                "title": "AG hosted closeout workshop",
                "strategy_spec_ref": registry_id,
                "metadata": {"task_id": "AG-HOSTED-CLOSE-001"},
            },
        )
        if not check("workshop_created", status, 201, body):
            raise RuntimeError("Workshop creation failed")
        workshop_id = str(body["data"]["workshop_id"])
        report["resources"]["workshop_id"] = workshop_id

        status, body, _ = http_json(
            "GET",
            f"{BFF_URL}/bff/agora/workshops/{workshop_id}",
            headers=scoped_headers(tokens["operator_b"], tenant_id),
        )
        check("cross_owner_workshop_read_denied", status, (403, 404), body)

        status, body, response_headers = http_json(
            "GET",
            f"{BFF_URL}/bff/agora/workshops/{workshop_id}",
            headers=owner_headers,
        )
        if not check("workshop_owner_read", status, 200, body):
            raise RuntimeError("Workshop owner read failed")
        etag = response_headers.get("etag")
        if not etag:
            raise RuntimeError("Workshop owner read did not return ETag")

        status, body, _ = http_json(
            "GET",
            f"{BFF_URL}/bff/agora/workshops/{workshop_id}/versions",
            headers=owner_headers,
        )
        check("workshop_versions_listed", status, 200, body)

        status, body, response_headers = http_json(
            "POST",
            f"{BFF_URL}/bff/agora/workshops/{workshop_id}/versions",
            headers=scoped_headers(
                tokens["operator_a"],
                tenant_id,
                idempotency_key=f"{run_key}-version",
                etag=etag,
                request_id=f"{run_key}-version",
            ),
            payload={
                "patch": [
                    {
                        "op": "replace",
                        "path": "/title",
                        "value": "AG hosted closeout research candidate",
                    }
                ],
                "reason": "Bounded hosted qualification update",
            },
        )
        if not check("workshop_version_created", status, 201, body):
            raise RuntimeError("Workshop version creation failed")
        version_id = str(
            body["data"]["resource"]["version"]["workshop_version_id"]
        )
        report["resources"]["workshop_version_id"] = version_id
        etag = response_headers.get("etag")
        if not etag:
            raise RuntimeError("Workshop version creation did not return ETag")

        status, body, response_headers = http_json(
            "POST",
            f"{BFF_URL}/bff/agora/workshops/{workshop_id}/versions/{version_id}/select",
            headers=scoped_headers(
                tokens["operator_a"],
                tenant_id,
                idempotency_key=f"{run_key}-select",
                etag=etag,
                request_id=f"{run_key}-select",
            ),
        )
        if not check("workshop_version_selected", status, 200, body):
            raise RuntimeError("Workshop version selection failed")
        etag = response_headers.get("etag")
        if not etag:
            raise RuntimeError("Workshop version selection did not return ETag")

        status, body, response_headers = http_json(
            "POST",
            f"{BFF_URL}/bff/agora/workshops/{workshop_id}/consultations",
            headers=scoped_headers(
                tokens["operator_a"],
                tenant_id,
                idempotency_key=f"{run_key}-consultation",
                etag=etag,
                request_id=f"{run_key}-consultation",
            ),
            payload={
                "consultation_type": "committee",
                "subject": "Review the hosted research-only candidate",
                "context_refs": [f"registry:{registry_id}"],
            },
        )
        if not check("workshop_consultation_created", status, 201, body):
            raise RuntimeError("Workshop consultation creation failed")
        consultation = body["data"]["resource"]["consultation"]
        report["resources"]["consultation_request_id"] = (
            consultation.get("request_id") or consultation.get("id")
        )
        etag = response_headers.get("etag")
        if not etag:
            raise RuntimeError("Workshop consultation creation did not return ETag")

        unsupported_approval_id = f"approval-workshop-{run_key}"
        status, body, _ = http_json(
            "POST",
            f"{GOVERNANCE_URL}/api/governance/approvals",
            headers=owner_headers,
            payload={
                "decision_id": unsupported_approval_id,
                "target_type": "strategy_workshop",
                "target_id": workshop_id,
                "target_version": version_id,
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
                "risk_level": "low",
            },
        )
        check(
            "formal_approval_producer_accepts_workshop_target",
            status,
            201,
            body,
        )
        if status != 201:
            report["blockers"].append(
                {
                    "check": "formal_approval_producer_accepts_workshop_target",
                    "reason": "governance_target_type_contract_gap",
                    "producer": "governance /api/governance/approvals",
                    "required_target_type": "strategy_workshop",
                    "observed_http_status": status,
                    "error": error_summary(body),
                }
            )

        approval_id = f"approval-strategy-spec-{run_key}"
        status, body, _ = http_json(
            "POST",
            f"{GOVERNANCE_URL}/api/governance/approvals",
            headers=owner_headers,
            payload={
                "decision_id": approval_id,
                "target_type": "strategy_spec",
                "target_id": workshop_id,
                "target_version": version_id,
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
                "risk_level": "low",
            },
        )
        if not check("formal_approval_closest_supported_target_created", status, 201, body):
            raise RuntimeError("Closest supported formal approval creation failed")

        status, body, _ = http_json(
            "POST",
            f"{GOVERNANCE_URL}/api/governance/approvals/{approval_id}/review",
            headers=scoped_headers(tokens["approver"], tenant_id),
            payload={
                "actor_role": "governance_reviewer",
                "actor_id": approver_user_id,
            },
        )
        if not check("formal_approval_reviewed_by_distinct_actor", status, 200, body):
            raise RuntimeError("Formal approval review failed")

        status, body, _ = http_json(
            "POST",
            f"{GOVERNANCE_URL}/api/governance/approvals/{approval_id}/decide",
            headers=scoped_headers(tokens["approver"], tenant_id),
            payload={
                "outcome": "approved",
                "rationale": "Approve bounded research-only qualification.",
                "actor_role": "governance_reviewer",
                "actor_id": approver_user_id,
                "conditions": ["No deployment, orders, or live capital changes."],
            },
        )
        if not check("formal_approval_decided_by_distinct_actor", status, 200, body):
            raise RuntimeError("Formal approval decision failed")
        report["resources"]["formal_approval_id"] = approval_id

        status, body, _ = http_json(
            "POST",
            f"{BFF_URL}/bff/agora/workshops/{workshop_id}/research-runs",
            headers=scoped_headers(
                tokens["operator_a"],
                tenant_id,
                idempotency_key=f"{run_key}-research",
                etag=etag,
                request_id=f"{run_key}-research",
            ),
            payload={
                "research_context": "Validate without live execution.",
                "strategy_version_ref": version_id,
                "parameters": {"environment": "research"},
                "approval_decision_id": approval_id,
                "adapter": "handoff_only",
                "requested_mode": "handoff_only",
                "dispatch_mode": "handoff_only",
            },
        )
        check("workshop_research_run_created", status, 202, body)
        if status != 202:
            report["blockers"].append(
                {
                    "check": "workshop_research_run_created",
                    "reason": "workshop_rejects_only_producer_supported_approval_target",
                    "approval_decision_id": approval_id,
                    "observed_http_status": status,
                    "error": error_summary(body),
                }
            )

        status, body, _ = http_json(
            "POST",
            f"{BFF_URL}/bff/agora/workshops/{workshop_id}/conclude",
            headers=scoped_headers(
                tokens["operator_a"],
                tenant_id,
                idempotency_key=f"{run_key}-conclude",
                etag=etag,
                request_id=f"{run_key}-conclude",
            ),
            payload={
                "final_version_id": version_id,
                "conclusion_notes": "Research-only hosted qualification.",
                "approval_decision_id": approval_id,
            },
        )
        check("workshop_concluded", status, 200, body)
        if status != 200:
            report["blockers"].append(
                {
                    "check": "workshop_concluded",
                    "reason": "workshop_rejects_only_producer_supported_approval_target",
                    "approval_decision_id": approval_id,
                    "observed_http_status": status,
                    "error": error_summary(body),
                }
            )
    except Exception as exc:
        report["blockers"].append(
            {
                "check": "probe_execution",
                "reason": type(exc).__name__,
                "message": str(exc),
            }
        )

    report["completed_at"] = utc_now()
    report["accepted"] = not report["blockers"]
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["accepted"] else 2


if __name__ == "__main__":
    sys.exit(main())
