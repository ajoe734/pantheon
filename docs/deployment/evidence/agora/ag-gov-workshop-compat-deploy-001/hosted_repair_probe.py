#!/usr/bin/env python3
"""Bounded hosted proof for the Governance–Workshop compatibility repair.

The probe is intended to run inside the deployed ``operator-bff`` container,
where the governed dev-login credentials already exist.  It never prints a
credential or token and uses product HTTP APIs only:

* Registry creates and reads a StrategySpec whose Registry and strategy IDs
  are deliberately different.
* BFF creates, versions, researches, concludes, and reads the Workshop.
* Governance creates, reviews, decides, and reads the canonical
  ``strategy_workshop`` approval.

``seed`` creates the research-only flow and emits redacted resource IDs.
After the BFF is restarted, ``verify`` reads those IDs back and also checks the
public FE/BFF deployment pair.  Neither mode calls deployment, order, broker,
allocation, or capital APIs.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


TASK_ID = "AG-GOV-WORKSHOP-COMPAT-DEPLOY-001"
BFF_URL = os.getenv("AG_REPAIR_BFF_URL", "http://127.0.0.1:8001").rstrip("/")
REGISTRY_URL = os.getenv("AG_REPAIR_REGISTRY_URL", "http://registry:8087").rstrip("/")
GOVERNANCE_URL = os.getenv(
    "AG_REPAIR_GOVERNANCE_URL",
    "http://governance:8082",
).rstrip("/")
FE_MANIFEST_URL = os.getenv(
    "AG_REPAIR_FE_MANIFEST_URL",
    "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io/deployment.json",
)
EXPECTED_BFF_SHA = os.getenv("AG_REPAIR_EXPECTED_BFF_SHA", "")
EXPECTED_FE_SHA = os.getenv("AG_REPAIR_EXPECTED_FE_SHA", "")


class ProbeFailure(RuntimeError):
    """A fail-closed hosted qualification error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def request_json(
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
            parsed if isinstance(parsed, dict) else {"detail": parsed},
            {key.lower(): value for key, value in exc.headers.items()},
        )


def error_summary(body: dict[str, Any]) -> dict[str, Any]:
    error = body.get("error")
    if not isinstance(error, dict):
        detail = body.get("detail")
        error = detail.get("error") if isinstance(detail, dict) else None
    if not isinstance(error, dict):
        return {"message": body.get("detail") or body.get("message")}
    details = error.get("details")
    return {
        "code": error.get("code"),
        "message": error.get("message"),
        "reason": details.get("reason") if isinstance(details, dict) else details,
        "precondition_failed": (
            details.get("precondition_failed") if isinstance(details, dict) else None
        ),
    }


def jwt_claims(token: str) -> dict[str, Any]:
    encoded = token.split(".")[1]
    encoded += "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))


def login(identity: str) -> tuple[str, dict[str, Any]]:
    prefix = f"PANTHEON_BFF_DEV_LOGIN_{identity.upper()}"
    client_id = os.getenv(f"{prefix}_CLIENT_ID", "")
    client_secret = os.getenv(f"{prefix}_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise ProbeFailure(f"server-bound dev-login identity unavailable: {identity}")
    status, body, _headers = request_json(
        "POST",
        f"{BFF_URL}/bff/auth/dev-login",
        payload={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    token = body.get("access_token")
    if status != 200 or not isinstance(token, str) or not token:
        raise ProbeFailure(f"dev-login failed for {identity}: HTTP {status}")
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
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
        headers["X-Request-Id"] = f"request-{idempotency_key}"
    if etag:
        headers["If-Match"] = etag
    return headers


def require_status(
    report: dict[str, Any],
    name: str,
    status: int,
    expected: int,
    body: dict[str, Any],
    *,
    detail: dict[str, Any] | None = None,
) -> None:
    item: dict[str, Any] = {
        "name": name,
        "http_status": status,
        "expected_http_status": expected,
        "passed": status == expected,
    }
    if detail:
        item["detail"] = detail
    if status != expected:
        item["error"] = error_summary(body)
    report["checks"].append(item)
    if status != expected:
        raise ProbeFailure(
            f"{name} returned HTTP {status}, expected {expected}: "
            f"{json.dumps(error_summary(body), sort_keys=True)}"
        )


def require_value(
    report: dict[str, Any],
    name: str,
    condition: bool,
    detail: dict[str, Any],
) -> None:
    report["checks"].append({"name": name, "passed": condition, "detail": detail})
    if not condition:
        raise ProbeFailure(f"{name} failed: {json.dumps(detail, sort_keys=True)}")


def base_report(mode: str) -> dict[str, Any]:
    return {
        "schema_version": "pantheon.agora.governance-workshop-repair-proof.v1",
        "task_id": TASK_ID,
        "mode": mode,
        "started_at": utc_now(),
        "expected_pair": {
            "frontend_sha": EXPECTED_FE_SHA,
            "backend_sha": EXPECTED_BFF_SHA,
        },
        "safety": {
            "environment": "dev",
            "execution_authority": "none",
            "research_mode": "handoff_only",
            "no_live_capital": True,
            "backing_stores_edited_directly": False,
            "credentials_or_tokens_emitted": False,
        },
        "identities": {},
        "checks": [],
        "resources": {},
        "accepted": False,
    }


def assert_expected_identity_configured() -> None:
    if len(EXPECTED_BFF_SHA) != 40:
        raise ProbeFailure("AG_REPAIR_EXPECTED_BFF_SHA must be an exact commit")


def check_bff_identity(report: dict[str, Any]) -> None:
    status, body, _headers = request_json("GET", f"{BFF_URL}/bff/version")
    require_status(report, "public_bff_version", status, 200, body)
    observed = body.get("source_commit_sha")
    posture = body.get("config_posture")
    require_value(
        report,
        "exact_strict_bff_identity",
        observed == EXPECTED_BFF_SHA
        and isinstance(posture, dict)
        and posture.get("auth_mode") == "strict"
        and posture.get("auth_stub") is False,
        {"observed": observed, "posture": posture},
    )


def seed() -> dict[str, Any]:
    assert_expected_identity_configured()
    report = base_report("seed")
    check_bff_identity(report)

    owner_token, owner_claims = login("operator_a")
    approver_token, approver_claims = login("approver")
    report["identities"] = {
        "owner": owner_claims,
        "approver": approver_claims,
    }
    tenant_id = str(owner_claims["tenant_id"])
    owner_user_id = str(owner_claims["subject"])
    approver_user_id = str(approver_claims["subject"])
    require_value(
        report,
        "two_person_identity",
        owner_user_id != approver_user_id
        and owner_claims["mfa_verified"] is True
        and approver_claims["mfa_verified"] is True,
        {
            "owner": owner_user_id,
            "approver": approver_user_id,
            "owner_mfa_verified": owner_claims["mfa_verified"],
            "approver_mfa_verified": approver_claims["mfa_verified"],
        },
    )

    run_key = (
        f"ag-gov-workshop-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-"
        f"{secrets.token_hex(3)}"
    )
    strategy_id = f"strategy-{run_key}"
    registry_id = f"registry-{run_key}"
    approval_id = f"approval-{run_key}"
    require_value(
        report,
        "registry_strategy_ids_distinct",
        registry_id != strategy_id,
        {"registry_id": registry_id, "strategy_id": strategy_id},
    )

    strategy_spec = {
        "spec_version": "1.0",
        "strategy_id": strategy_id,
        "title": "Governance Workshop repaired hosted proof",
        "hypothesis": "A bounded signal can be researched without execution.",
        "objective": "Prove the repaired exact pair with no capital authority.",
        "lifecycle_state": "draft",
        "market_scope": {"symbols": ["RESEARCH_UNIVERSE"], "frequency": "1d"},
        "data_dependencies": [{"ref": f"dataset:{run_key}", "kind": "dataset"}],
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
        "provenance": {"source_kind": "manual", "created_at": utc_now()},
        "metadata": {"task_id": TASK_ID, "run_key": run_key},
    }
    status, body, _headers = request_json(
        "POST",
        f"{REGISTRY_URL}/api/registry/strategy-specs",
        payload={
            "registry_id": registry_id,
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "artifact_state": "draft",
            "source_seed_id": run_key,
            "lineage": {"source_run_ids": [run_key]},
            "metadata": {"task_id": TASK_ID},
            "strategy_spec": strategy_spec,
        },
    )
    require_status(report, "registry_strategy_spec_created", status, 200, body)

    owner_headers = scoped_headers(owner_token, tenant_id)
    status, body, _headers = request_json(
        "POST",
        f"{BFF_URL}/bff/agora/workshops",
        headers={
            **owner_headers,
            "Idempotency-Key": f"{run_key}-workshop",
        },
        payload={
            "initial_message": "Qualify the repaired research-only pair.",
            "title": "Governance Workshop repaired hosted proof",
            "strategy_spec_ref": registry_id,
            "metadata": {"task_id": TASK_ID},
        },
    )
    require_status(report, "workshop_created", status, 201, body)
    workshop = body["data"]
    workshop_id = str(workshop["workshop_id"])
    require_value(
        report,
        "workshop_preserves_distinct_registry_strategy_ids",
        workshop.get("strategy_id") == strategy_id
        and workshop.get("active_strategy_spec_registry_id") == registry_id,
        {
            "strategy_id": workshop.get("strategy_id"),
            "active_strategy_spec_registry_id": workshop.get(
                "active_strategy_spec_registry_id"
            ),
        },
    )

    status, body, response_headers = request_json(
        "GET",
        f"{BFF_URL}/bff/agora/workshops/{workshop_id}",
        headers=owner_headers,
    )
    require_status(report, "workshop_owner_read", status, 200, body)
    etag = response_headers.get("etag")
    if not etag:
        raise ProbeFailure("workshop owner read did not return ETag")

    status, body, response_headers = request_json(
        "POST",
        f"{BFF_URL}/bff/agora/workshops/{workshop_id}/versions",
        headers=scoped_headers(
            owner_token,
            tenant_id,
            idempotency_key=f"{run_key}-version",
            etag=etag,
        ),
        payload={
            "patch": [
                {
                    "op": "replace",
                    "path": "/title",
                    "value": "Selected repaired hosted candidate",
                }
            ],
            "reason": "Prove distinct Registry and strategy identity support.",
        },
    )
    require_status(report, "workshop_version_created", status, 201, body)
    version = body["data"]["resource"]["version"]
    version_id = str(version["workshop_version_id"])
    active_registry_id = str(version["strategy_spec_registry_id"])
    require_value(
        report,
        "version_preserves_distinct_registry_strategy_ids",
        version.get("strategy_id") == strategy_id
        and active_registry_id != strategy_id
        and active_registry_id != registry_id,
        {
            "strategy_id": version.get("strategy_id"),
            "initial_strategy_spec_registry_id": registry_id,
            "version_strategy_spec_registry_id": active_registry_id,
        },
    )
    etag = response_headers.get("etag")
    if not etag:
        raise ProbeFailure("workshop version create did not return ETag")

    status, body, response_headers = request_json(
        "POST",
        f"{BFF_URL}/bff/agora/workshops/{workshop_id}/versions/{version_id}/select",
        headers=scoped_headers(
            owner_token,
            tenant_id,
            idempotency_key=f"{run_key}-select",
            etag=etag,
        ),
    )
    require_status(report, "workshop_version_selected", status, 200, body)
    etag = response_headers.get("etag")
    if not etag:
        raise ProbeFailure("workshop version select did not return ETag")

    status, body, _headers = request_json(
        "POST",
        f"{GOVERNANCE_URL}/api/governance/approvals",
        headers=owner_headers,
        payload={
            "decision_id": approval_id,
            "target_type": "strategy_workshop",
            "target_id": workshop_id,
            "target_version": version_id,
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "risk_level": "low",
        },
    )
    require_status(
        report,
        "canonical_strategy_workshop_approval_created",
        status,
        201,
        body,
    )

    approver_headers = scoped_headers(approver_token, tenant_id)
    status, body, _headers = request_json(
        "POST",
        f"{GOVERNANCE_URL}/api/governance/approvals/{approval_id}/review",
        headers=approver_headers,
        payload={
            "actor_role": "governance_reviewer",
            "actor_id": approver_user_id,
        },
    )
    require_status(report, "approval_reviewed_by_distinct_actor", status, 200, body)

    status, body, _headers = request_json(
        "POST",
        f"{GOVERNANCE_URL}/api/governance/approvals/{approval_id}/decide",
        headers=approver_headers,
        payload={
            "outcome": "approved",
            "rationale": "Approve bounded handoff-only research and conclusion.",
            "actor_role": "governance_reviewer",
            "actor_id": approver_user_id,
            "conditions": ["No deployment, order, broker, or capital action."],
        },
    )
    require_status(report, "approval_decided_by_distinct_actor", status, 200, body)
    require_value(
        report,
        "canonical_approval_binding",
        body.get("decision_state") == "decided"
        and body.get("decision") == "approved"
        and body.get("target_type") == "strategy_workshop"
        and body.get("target_id") == workshop_id
        and body.get("target_version") == version_id
        and body.get("owner_user_id") == owner_user_id,
        {
            key: body.get(key)
            for key in (
                "decision_state",
                "decision",
                "target_type",
                "target_id",
                "target_version",
                "owner_user_id",
                "reviewed_by",
            )
        },
    )

    status, body, response_headers = request_json(
        "POST",
        f"{BFF_URL}/bff/agora/workshops/{workshop_id}/research-runs",
        headers=scoped_headers(
            owner_token,
            tenant_id,
            idempotency_key=f"{run_key}-research",
            etag=etag,
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
    require_status(report, "workshop_research_handoff_created", status, 202, body)
    etag = response_headers.get("etag")
    if not etag:
        raise ProbeFailure("research handoff did not return ETag")

    status, body, _headers = request_json(
        "POST",
        f"{BFF_URL}/bff/agora/workshops/{workshop_id}/conclude",
        headers=scoped_headers(
            owner_token,
            tenant_id,
            idempotency_key=f"{run_key}-conclude",
            etag=etag,
        ),
        payload={
            "final_version_id": version_id,
            "conclusion_notes": "Approved as research-only with no execution authority.",
            "approval_decision_id": approval_id,
        },
    )
    require_status(report, "workshop_concluded", status, 200, body)
    concluded = body["data"]["resource"]["workshop"]
    require_value(
        report,
        "conclusion_preserves_repaired_identity",
        concluded.get("status") == "concluded"
        and concluded.get("strategy_id") == strategy_id
        and concluded.get("active_strategy_spec_registry_id") == active_registry_id,
        {
            "status": concluded.get("status"),
            "strategy_id": concluded.get("strategy_id"),
            "active_strategy_spec_registry_id": concluded.get(
                "active_strategy_spec_registry_id"
            ),
        },
    )

    report["run_key"] = run_key
    report["resources"] = {
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "approver_user_id": approver_user_id,
        "strategy_id": strategy_id,
        "initial_registry_id": registry_id,
        "active_registry_id": active_registry_id,
        "workshop_id": workshop_id,
        "workshop_version_id": version_id,
        "approval_id": approval_id,
    }
    report["accepted"] = True
    return report


def verify(args: argparse.Namespace) -> dict[str, Any]:
    assert_expected_identity_configured()
    if len(EXPECTED_FE_SHA) != 40:
        raise ProbeFailure("AG_REPAIR_EXPECTED_FE_SHA must be an exact commit")
    report = base_report("verify-after-restart")
    report["resources"] = {
        "strategy_id": args.strategy_id,
        "initial_registry_id": args.initial_registry_id,
        "active_registry_id": args.active_registry_id,
        "workshop_id": args.workshop_id,
        "workshop_version_id": args.version_id,
        "approval_id": args.approval_id,
    }
    check_bff_identity(report)

    status, manifest, _headers = request_json("GET", FE_MANIFEST_URL)
    require_status(report, "public_frontend_deployment_manifest", status, 200, manifest)
    observed_fe = (
        manifest.get("frontendSha")
        or manifest.get("commit")
        or (manifest.get("frontend") or {}).get("commitSha")
    )
    observed_bff = (
        manifest.get("bffCommit")
        or manifest.get("bffSourceCommitSha")
        or (manifest.get("bff") or {}).get("sourceCommitSha")
    )
    build_mode = manifest.get("buildMode")
    require_value(
        report,
        "public_manifest_exact_safe_pair",
        observed_fe == EXPECTED_FE_SHA
        and observed_bff == EXPECTED_BFF_SHA
        and manifest.get("deploymentState") == "accepted"
        and isinstance(build_mode, dict)
        and build_mode.get("VITE_BFF_MODE") == "live"
        and build_mode.get("VITE_BFF_FALLBACK") == "strict"
        and str(build_mode.get("VITE_BFF_REAL_WRITES")).lower() == "false"
        and str(build_mode.get("VITE_BFF_ALLOW_DEV_STUB_WRITES")).lower()
        == "false",
        {
            "frontend_sha": observed_fe,
            "backend_sha": observed_bff,
            "deployment_state": manifest.get("deploymentState"),
            "build_mode": build_mode,
        },
    )

    owner_token, owner_claims = login("operator_a")
    report["identities"] = {"owner": owner_claims}
    tenant_id = str(owner_claims["tenant_id"])
    owner_headers = scoped_headers(owner_token, tenant_id)

    for label, registry_id in (
        ("initial", args.initial_registry_id),
        ("active_version", args.active_registry_id),
    ):
        status, registry, _headers = request_json(
            "GET",
            f"{REGISTRY_URL}/api/registry/strategy-specs/{registry_id}",
        )
        require_status(
            report,
            f"{label}_registry_readback_after_restart",
            status,
            200,
            registry,
        )
        entry = (
            registry.get("entry") if isinstance(registry.get("entry"), dict) else {}
        )
        require_value(
            report,
            f"{label}_registry_identity_after_restart",
            entry.get("registry_id") == registry_id
            and entry.get("strategy_id") == args.strategy_id
            and registry_id != args.strategy_id,
            {
                "registry_id": entry.get("registry_id"),
                "strategy_id": entry.get("strategy_id"),
            },
        )

    status, approval, _headers = request_json(
        "GET",
        f"{GOVERNANCE_URL}/api/governance/approvals/{args.approval_id}",
        headers=owner_headers,
    )
    require_status(report, "approval_readback_after_restart", status, 200, approval)
    require_value(
        report,
        "canonical_approval_after_restart",
        approval.get("decision_state") == "decided"
        and approval.get("decision") == "approved"
        and approval.get("target_type") == "strategy_workshop"
        and approval.get("target_id") == args.workshop_id
        and approval.get("target_version") == args.version_id,
        {
            key: approval.get(key)
            for key in (
                "decision_state",
                "decision",
                "target_type",
                "target_id",
                "target_version",
            )
        },
    )

    status, workshop, _headers = request_json(
        "GET",
        f"{BFF_URL}/bff/agora/workshops/{args.workshop_id}",
        headers=owner_headers,
    )
    require_status(report, "workshop_readback_after_restart", status, 200, workshop)
    data = workshop.get("data") if isinstance(workshop.get("data"), dict) else {}
    require_value(
        report,
        "concluded_repaired_workshop_after_restart",
        data.get("status") == "concluded"
        and data.get("strategy_id") == args.strategy_id
        and data.get("active_strategy_spec_registry_id") == args.active_registry_id
        and data.get("final_workshop_version_id") == args.version_id,
        {
            "status": data.get("status"),
            "strategy_id": data.get("strategy_id"),
            "active_strategy_spec_registry_id": data.get(
                "active_strategy_spec_registry_id"
            ),
            "final_workshop_version_id": data.get("final_workshop_version_id"),
        },
    )
    report["accepted"] = True
    return report


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--strategy-id", required=True)
    verify_parser.add_argument("--initial-registry-id", required=True)
    verify_parser.add_argument("--active-registry-id", required=True)
    verify_parser.add_argument("--workshop-id", required=True)
    verify_parser.add_argument("--version-id", required=True)
    verify_parser.add_argument("--approval-id", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    report: dict[str, Any]
    try:
        report = seed() if args.command == "seed" else verify(args)
    except Exception as exc:
        report = base_report(args.command)
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        report["completed_at"] = utc_now()
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2
    report["completed_at"] = utc_now()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
