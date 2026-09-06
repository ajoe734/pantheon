"""Fail-closed authority verifier for paper→canary and canary→live cutovers."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from services.governance.human_gate.decision_model import (
    HumanGateDecision,
    HumanGateDecisionError,
    PASSING_EVIDENCE_STATUSES,
)

from deploy_authority import (
    DeployAuthorityError,
    DeployAuthorityUnavailableError,
    verify_deploy_authorities,
)


class PromotionAuthorityError(DeployAuthorityError):
    """Raised when governed stage-cutover authority cannot be proven."""


class PromotionAuthorityUnavailableError(
    PromotionAuthorityError, DeployAuthorityUnavailableError
):
    """Raised when a canonical promotion authority cannot be read."""


FetchJson = Callable[[str, float], Mapping[str, Any]]

_TRANSITIONS = {"paper": "canary", "canary": "live"}
_REQUIRED_ROLES = frozenset({"approver", "risk_owner", "operator"})
_REQUIRED_EVIDENCE = {
    "canary": frozenset(
        {
            "promotion_readiness_packet",
            "paper_observation",
            "paper_performance",
            "reconciliation",
            "incident_clearance",
            "rollback_target",
            "broker_sandbox_smoke",
            "broker_entitlement",
            "capital_authorization",
            "kill_switch_drill",
        }
    ),
    "live": frozenset(
        {
            "promotion_readiness_packet",
            "canary_observation",
            "canary_performance",
            "execution_quality",
            "reconciliation",
            "incident_clearance",
            "rollback_target",
            "broker_sandbox_smoke",
            "broker_entitlement",
            "capital_authorization",
            "kill_switch_drill",
        }
    ),
}


def _required_text(source: Mapping[str, Any], key: str, label: str) -> str:
    value = str(source.get(key) or "").strip()
    if not value:
        raise PromotionAuthorityError(f"{label}.{key} is required")
    return value


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _fetch_json(url: str, timeout_seconds: float) -> Mapping[str, Any]:
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        error_type = (
            PromotionAuthorityUnavailableError
            if exc.code in {408, 425, 429} or 500 <= exc.code <= 599
            else PromotionAuthorityError
        )
        raise error_type(
            f"authoritative human-gate read returned HTTP {exc.code}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise PromotionAuthorityUnavailableError(
            "authoritative human-gate read is unavailable"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PromotionAuthorityError(
            "authoritative human-gate read did not return JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise PromotionAuthorityError(
            "authoritative human-gate read must return a JSON object"
        )
    return payload


def verify_promotion_authorities(
    request: Mapping[str, Any],
    *,
    requesting_actor_id: str,
    deployment_base_url: str,
    registry_base_url: str,
    governance_base_url: str,
    capital_base_url: str,
    timeout_seconds: float = 5.0,
    fetch_json: FetchJson | None = None,
    approval_reader=None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a server-derived cutover request and correlated proof report."""

    current_binding_id = _required_text(
        request, "current_binding_id", "promotion request"
    )
    target_stage = _required_text(request, "target_stage", "promotion request")
    source_stage = next(
        (source for source, target in _TRANSITIONS.items() if target == target_stage),
        None,
    )
    if source_stage is None:
        raise PromotionAuthorityError(
            "target_stage must be canary or live for governed promotion"
        )
    environment = _required_text(request, "environment", "promotion request")
    human_gate_decision_id = _required_text(
        request, "human_gate_decision_id", "promotion request"
    )
    actor_id = str(requesting_actor_id or "").strip()
    if not actor_id:
        raise PromotionAuthorityError("authenticated requesting actor is required")

    fetch = fetch_json or _fetch_json
    try:
        authority = verify_deploy_authorities(
            request,
            deployment_base_url=deployment_base_url,
            registry_base_url=registry_base_url,
            governance_base_url=governance_base_url,
            capital_base_url=capital_base_url,
            timeout_seconds=timeout_seconds,
            fetch_json=fetch_json,
            approval_reader=approval_reader,
            now=now,
            allowed_target_stages=(target_stage,),
            allowed_registry_deployment_stages=(source_stage,),
        )
    except DeployAuthorityUnavailableError as exc:
        raise PromotionAuthorityUnavailableError(str(exc)) from exc
    except DeployAuthorityError as exc:
        raise PromotionAuthorityError(str(exc)) from exc

    plan_mismatches: list[str] = []
    if authority["deployment_plan_current_stage"] != source_stage:
        plan_mismatches.append(
            f"current_stage expected {source_stage!r}, got "
            f"{authority['deployment_plan_current_stage']!r}"
        )
    if authority["deployment_plan_binding_id"] != current_binding_id:
        plan_mismatches.append(
            f"binding_id expected {current_binding_id!r}, got "
            f"{authority['deployment_plan_binding_id']!r}"
        )
    if authority["deployment_plan_transition_type"] != "promote":
        plan_mismatches.append(
            "transition_type expected 'promote', got "
            f"{authority['deployment_plan_transition_type']!r}"
        )
    if authority["deployment_plan_runtime_action"] != "replace_binding":
        plan_mismatches.append(
            "runtime_action expected 'replace_binding', got "
            f"{authority['deployment_plan_runtime_action']!r}"
        )
    if plan_mismatches:
        raise PromotionAuthorityError(
            "deployment promotion authority mismatch: " + "; ".join(plan_mismatches)
        )

    human_gate_url = (
        f"{governance_base_url.strip().rstrip('/')}/api/governance/human-gates/"
        f"{quote(human_gate_decision_id, safe='')}"
    )
    raw_human_gate = fetch(human_gate_url, timeout_seconds)
    try:
        human_gate = HumanGateDecision.from_dict(raw_human_gate)
    except HumanGateDecisionError as exc:
        raise PromotionAuthorityError(
            f"canonical human gate is invalid: {exc}"
        ) from exc

    gate_mismatches: list[str] = []
    if human_gate.decision_id != human_gate_decision_id:
        gate_mismatches.append("decision_id does not match the requested human gate")
    if human_gate.target_type != "runtime_binding_promotion":
        gate_mismatches.append("target_type must be 'runtime_binding_promotion'")
    if human_gate.target_id != authority["plan_id"]:
        gate_mismatches.append("target_id must equal the canonical DeploymentPlan id")
    if human_gate.target_environment != environment:
        gate_mismatches.append("target_environment does not match the request")
    if frozenset(human_gate.required_roles) != _REQUIRED_ROLES:
        gate_mismatches.append(
            "required_roles must contain exactly approver, risk_owner, and operator"
        )
    if human_gate.status != "approved" or human_gate.can_proceed is not True:
        gate_mismatches.append("human gate must be approved with can_proceed=true")
    if str(human_gate.metadata.get("target_stage") or "") != target_stage:
        gate_mismatches.append("metadata.target_stage does not match the canonical target")
    if str(human_gate.metadata.get("source_binding_id") or "") != current_binding_id:
        gate_mismatches.append("metadata.source_binding_id does not match the cutover source")

    evidence_by_key = {item.key: item for item in human_gate.evidence_reviewed}
    required_evidence = _REQUIRED_EVIDENCE[target_stage]
    if frozenset(evidence_by_key) != required_evidence:
        gate_mismatches.append(
            f"reviewed evidence must contain exactly the {target_stage} promotion set"
        )
    for key in sorted(required_evidence):
        item = evidence_by_key.get(key)
        if item is not None and item.status not in PASSING_EVIDENCE_STATUSES:
            gate_mismatches.append(f"evidence {key!r} is not passing")

    signatures = human_gate.active_signatures_by_role()
    for role in sorted(_REQUIRED_ROLES):
        signature = signatures.get(role)
        if signature is None:
            gate_mismatches.append(f"missing active {role} signature")
            continue
        if signature.meaning != "approved" or signature.conditions:
            gate_mismatches.append(
                f"{role} signature must be unconditional approved"
            )
        if (
            signature.metadata.get("authn_token_kind") != "jwt"
            or signature.metadata.get("mfa_proof") != "jwt_claim"
        ):
            gate_mismatches.append(
                f"{role} signature lacks claim-bound JWT MFA provenance"
            )
    if gate_mismatches:
        raise PromotionAuthorityError(
            "human gate authority mismatch: " + "; ".join(gate_mismatches)
        )

    artifact_approval_actor_id = str(authority["approval_actor_id"])
    reviewer_actor_id = signatures["approver"].actor_id
    risk_owner_actor_id = signatures["risk_owner"].actor_id
    operator_actor_id = signatures["operator"].actor_id
    human_signer_actor_ids = {
        reviewer_actor_id,
        risk_owner_actor_id,
        operator_actor_id,
    }
    if len(human_signer_actor_ids) != 3:
        raise PromotionAuthorityError(
            "promotion reviewer, risk owner, and human-gate operator must be "
            "three distinct authenticated actors"
        )
    if actor_id in human_signer_actor_ids:
        raise PromotionAuthorityError(
            "cutover operator must be distinct from all promotion signers"
        )
    distinct_actors = {
        reviewer_actor_id,
        risk_owner_actor_id,
        operator_actor_id,
        actor_id,
    }
    if len(distinct_actors) != 4:
        raise PromotionAuthorityError(
            "promotion requires four distinct authenticated actors"
        )

    scale = authority.get("deployment_plan_scale")
    if not isinstance(scale, Mapping):
        raise PromotionAuthorityError("DeploymentPlan.scale is required for promotion")
    rollback = authority.get("deployment_plan_rollback")
    if not isinstance(rollback, Mapping):
        raise PromotionAuthorityError("DeploymentPlan.rollback is required for promotion")

    readiness_packet_ref = str(
        human_gate.can_proceed_input.readiness_packet_ref or ""
    ).strip()
    if not readiness_packet_ref:
        raise PromotionAuthorityError(
            "human gate readiness_packet_ref is required for promotion"
        )

    canonical = dict(request)
    canonical.update(
        {
            "loader_checks_passed": True,
            "persona_capital_binding_status": authority[
                "persona_capital_binding_status"
            ],
            "allowed_deployment_scope": authority["allowed_deployment_scope"],
            "capital_scale_pct": scale.get("capital_scale_pct"),
            "gross_scale_pct": scale.get("gross_scale_pct"),
            "promotion_gate_decision_id": human_gate_decision_id,
            "human_gate_packet_ref": readiness_packet_ref,
            "broker_sandbox_smoke_ref": evidence_by_key[
                "broker_sandbox_smoke"
            ].source_ref,
            "risk_owner_approval_ref": signatures["risk_owner"].source_ref
            or signatures["risk_owner"].signature_id,
            "operator_approval_ref": signatures["operator"].source_ref
            or signatures["operator"].signature_id,
        }
    )
    if target_stage == "live":
        canonical["canary_observation_ref"] = evidence_by_key[
            "canary_observation"
        ].source_ref

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report = {
        "status": "passed",
        "authority": "canonical_stage_promotion",
        "source_stage": source_stage,
        "target_stage": target_stage,
        "current_binding_id": current_binding_id,
        "requesting_actor_id": actor_id,
        "artifact_approval_actor_id": artifact_approval_actor_id,
        "promotion_reviewer_actor_id": reviewer_actor_id,
        "risk_owner_actor_id": risk_owner_actor_id,
        "human_gate_operator_actor_id": operator_actor_id,
        "distinct_actor_count": len(distinct_actors),
        "human_gate_decision_id": human_gate_decision_id,
        "human_gate_sha256": _digest(raw_human_gate),
        "human_gate_evidence_sha256": human_gate.evidence_hash,
        "human_gate_proof_url": human_gate_url,
        "deploy_authority": authority,
        "verified_at": observed_at.isoformat().replace("+00:00", "Z"),
    }
    metadata = (
        dict(canonical.get("metadata") or {})
        if isinstance(canonical.get("metadata"), Mapping)
        else {}
    )
    metadata["authoritative_promotion_attestation"] = report
    metadata["stage_promotion_parent_binding_id"] = current_binding_id
    canonical["metadata"] = metadata
    return {"request": canonical, "authority_report": report}


__all__ = [
    "PromotionAuthorityError",
    "PromotionAuthorityUnavailableError",
    "verify_promotion_authorities",
]
