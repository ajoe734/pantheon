"""Human owner identities and governed approval shared by the deployed L12 suites.

The isolated harness mints ``PANTHEON_L12_OPERATOR_TOKEN`` (operator) and
``PANTHEON_L12_REVIEWER_TOKEN`` (governance_reviewer) with the signer that
Registry and Governance verify, for the tenant the stack runs as.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

# post(path, payload, headers, expected_statuses) -> decoded JSON object
GovernancePost = Callable[[str, Mapping[str, Any], Mapping[str, str], Sequence[int]], Mapping[str, Any]]


def human_token(role: str) -> str:
    token = os.getenv(f"PANTHEON_L12_{role}_TOKEN", "").strip()
    if not token:
        raise RuntimeError(f"PANTHEON_L12_{role}_TOKEN is not set by the isolated harness")
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_subject(token: str) -> str:
    payload = token.split(".")[1]
    return str(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"])


def approve_registry_entry(
    post: GovernancePost,
    *,
    decision_id: str,
    entry: Mapping[str, Any],
    tenant_id: str,
    rationale: str,
    risk_level: str = "low",
    proposal: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Record a decided Governance approval bound to one exact Registry entry.

    An operator proposes; a distinct governance_reviewer reviews and decides,
    with the expiry and candidate digest Registry requires before approving.
    """
    operator, reviewer = human_token("OPERATOR"), human_token("REVIEWER")
    reviewer_id = token_subject(reviewer)
    path = f"/api/governance/approvals/{decision_id}"

    def command(token: str, step: str, payload: Mapping[str, Any], expected: Sequence[int]) -> Mapping[str, Any]:
        headers = {**bearer(token), "Idempotency-Key": f"{decision_id}-{step}"}
        return post(path + f"/{step}" if step != "propose" else "/api/governance/approvals", payload, headers, expected)

    proposed = command(operator, "propose", {
        **dict(proposal or {}),
        "decision_id": decision_id,
        "expected_version": 0,
        "target_type": "registry_entry",
        "target_id": entry.get("registry_id"),
        "target_version": entry.get("version"),
        "candidate_digest": entry.get("checksum"),
        "risk_level": risk_level,
        "tenant_id": tenant_id,
        "owner_user_id": token_subject(operator),
    }, (201,))
    reviewed = command(reviewer, "review", {
        "expected_version": proposed.get("version"),
        "actor_id": reviewer_id,
        "actor_role": "governance_reviewer",
    }, (200,))
    decided = command(reviewer, "decide", {
        "expected_version": reviewed.get("version"),
        "actor_id": reviewer_id,
        "actor_role": "governance_reviewer",
        "outcome": "approved",
        "rationale": rationale,
        "candidate_digest": entry.get("checksum"),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, (200,))
    if decided.get("decision_state") != "decided" or decided.get("decision") != "approved":
        raise RuntimeError(f"Governance did not record an approved decision: {decided!r}")
    return decided


def registry_advance_body(
    entry: Mapping[str, Any],
    target_state: str,
    *,
    command_key: str,
    approval_decision_id: str | None = None,
) -> dict[str, Any]:
    """Registry advance is a caller-bound CAS on the entry the caller observed."""
    body: dict[str, Any] = {
        "target_state": target_state,
        "command_key": command_key,
        "expected_artifact_state": entry.get("artifact_state"),
        "expected_version": entry.get("version"),
        "expected_updated_at": entry.get("updated_at"),
    }
    if approval_decision_id:
        body["approval_decision_id"] = approval_decision_id
    return body
