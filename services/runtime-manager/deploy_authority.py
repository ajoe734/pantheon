"""Authoritative admission for a new paper RuntimeBinding.

The runtime-manager request body is a descriptor, not proof.  Before a new
binding may be created, this module reads the canonical deployment, registry,
governance, and capital services and binds their immutable facts to the
requested artifact, plan, pool, persona, and PersonaCapitalBinding.  Both the
deployment outbox worker and the runtime-manager HTTP boundary use this
verifier; the latter re-runs it so a caller cannot manufacture successful
plan, loader, approval, or capital assertions.

Only a new ``paper`` binding is admitted here.  Canary/live activation needs a
separate governed promotion/cutover verifier with target-bound MFA and
two-person proof; non-empty caller-supplied reference strings are deliberately
not accepted as that proof.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Collection, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from services.registry.strategy_artifact import (
    StrategyArtifactValidationError,
    strategy_artifact_checksum,
    validate_strategy_artifact,
)


from services.governance.approval_authority import (
    ApprovalInvalid, ApprovalUnavailable, configured_approval_reader,
)


class DeployAuthorityError(RuntimeError):
    """Raised when authoritative deploy admission cannot be proven."""


class DeployAuthorityUnavailableError(DeployAuthorityError):
    """Raised when canonical authorities cannot currently be read."""


FetchJson = Callable[[str, float], Mapping[str, Any]]


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _deployment_plan_authority_view(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the immutable admission portion of a DeploymentPlan.

    Deployment owns a small runtime projection on the plan (status, binding
    id, and metadata.runtime_lifecycle).  Those fields change after Runtime
    Manager commits a binding, so they cannot be used to decide whether a
    response-loss replay still describes the same authorization.  The adapter
    validates their exact expected post-state separately.  Every other plan
    field, including current_stage, remains covered by this digest.
    """
    view = json.loads(json.dumps(dict(payload), allow_nan=False))
    for field in ("status", "binding_id"):
        view.pop(field, None)
    metadata = view.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("runtime_lifecycle", None)
        if not metadata:
            view["metadata"] = {}
    elif metadata is None:
        view["metadata"] = {}
    return view


def _required_text(source: Mapping[str, Any], key: str, label: str) -> str:
    value = str(source.get(key) or "").strip()
    if not value:
        raise DeployAuthorityError(f"{label}.{key} is required")
    return value


def _fetch_json(
    url: str,
    timeout_seconds: float,
    *,
    headers: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    request = Request(
        url,
        headers={"Accept": "application/json", **dict(headers or {})},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        error_type = (
            DeployAuthorityUnavailableError
            if exc.code in {408, 425, 429} or 500 <= exc.code <= 599
            else DeployAuthorityError
        )
        raise error_type(f"authoritative read {url!r} returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise DeployAuthorityUnavailableError(
            f"authoritative read {url!r} is unavailable: {getattr(exc, 'reason', exc)}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeployAuthorityError(
            f"authoritative read {url!r} did not return JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise DeployAuthorityError(
            f"authoritative read {url!r} must return a JSON object"
        )
    return payload


def _deployment_request_headers() -> dict[str, str]:
    token = (
        os.getenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN")
        or os.getenv("PANTHEON_DEPLOYMENT_AUTH_TOKEN")
        or ""
    ).strip()
    tenant_id = os.getenv("PANTHEON_DEPLOYMENT_TENANT_ID", "").strip()
    if bool(token) != bool(tenant_id):
        raise DeployAuthorityUnavailableError(
            "deployment authority token and tenant must be configured together"
        )
    if not token:
        return {}
    authorization = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    return {
        "Authorization": authorization,
        "X-Tenant-Id": tenant_id,
    }


def _parse_time(value: str, label: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DeployAuthorityError(f"{label} is not an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise DeployAuthorityError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def verify_deploy_authorities(
    request: Mapping[str, Any],
    *,
    deployment_base_url: str,
    registry_base_url: str,
    governance_base_url: str,
    capital_base_url: str,
    timeout_seconds: float = 5.0,
    fetch_json: FetchJson | None = None,
    approval_reader=None,
    now: datetime | None = None,
    allowed_plan_statuses: Collection[str] = ("approved", "executing"),
    allowed_target_stages: Collection[str] = ("paper",),
    allowed_registry_deployment_stages: Collection[str] = ("none", "paper"),
) -> dict[str, Any]:
    """Return a target-bound loader/approval report or fail closed.

    The report contains only non-secret identities and canonical digests.  It
    is suitable for persistence in ``RuntimeBinding.metadata`` as the proof
    used at the admission cut.
    """

    target_stage = _required_text(request, "target_stage", "deploy request")
    admitted_target_stages = frozenset(
        str(stage) for stage in allowed_target_stages
    )
    if target_stage not in admitted_target_stages:
        if admitted_target_stages == frozenset({"paper"}):
            raise DeployAuthorityError(
                "new RuntimeBinding admission supports paper only; canary/live "
                "requires an authoritative target-bound MFA/two-person promotion verifier"
            )
        raise DeployAuthorityError(
            f"target_stage must be one of {sorted(admitted_target_stages)!r}; "
            f"got {target_stage!r}"
        )

    plan_status = _required_text(request, "plan_status", "deploy request")
    admitted_statuses = frozenset(str(status) for status in allowed_plan_statuses)
    if plan_status not in admitted_statuses:
        raise DeployAuthorityError(
            "DeploymentPlan status must be one of "
            f"{sorted(admitted_statuses)!r}; got {plan_status!r}"
        )

    deployment_url = deployment_base_url.strip().rstrip("/")
    registry_url = registry_base_url.strip().rstrip("/")
    governance_url = governance_base_url.strip().rstrip("/")
    capital_url = capital_base_url.strip().rstrip("/")
    if not deployment_url or not registry_url or not governance_url or not capital_url:
        raise DeployAuthorityUnavailableError(
            "canonical deployment, registry, governance, and capital authority URLs are required"
        )

    plan_id = _required_text(request, "plan_id", "deploy request")
    artifact_id = _required_text(request, "artifact_id", "deploy request")
    artifact_version = _required_text(request, "artifact_version", "deploy request")
    strategy_id = _required_text(request, "strategy_id", "deploy request")
    approval_decision_id = _required_text(
        request, "approval_decision_id", "deploy request"
    )
    capital_pool_id = _required_text(request, "capital_pool_id", "deploy request")
    sponsor_persona_id = _required_text(
        request, "sponsor_persona_id", "deploy request"
    )
    persona_capital_binding_id = _required_text(
        request, "persona_capital_binding_id", "deploy request"
    )
    persona_capital_binding_status = _required_text(
        request, "persona_capital_binding_status", "deploy request"
    )
    allowed_deployment_scope = _required_text(
        request, "allowed_deployment_scope", "deploy request"
    )

    fetch = fetch_json or _fetch_json
    deployment_fetch = fetch
    if fetch_json is None:
        deployment_headers = _deployment_request_headers()

        def deployment_fetch(url: str, timeout: float) -> Mapping[str, Any]:
            return _fetch_json(url, timeout, headers=deployment_headers)

    deployment_proof_url = (
        f"{deployment_url}/api/deployment/plans/{quote(plan_id, safe='')}"
    )
    registry_proof_url = (
        f"{registry_url}/api/registry/strategy-artifacts/{quote(artifact_id, safe='')}"
    )
    approval_proof_url = (
        f"{governance_url}/api/governance/approvals/"
        f"{quote(approval_decision_id, safe='')}"
    )
    capital_pool_proof_url = (
        f"{capital_url}/api/capital-pools/{quote(capital_pool_id, safe='')}"
    )
    capital_admissibility_proof_url = (
        f"{capital_url}/api/bindings/admissibility?"
        + urlencode(
            {
                "persona_id": sponsor_persona_id,
                "capital_pool_id": capital_pool_id,
                "target_stage": target_stage,
            }
        )
    )
    persona_binding_proof_url = (
        f"{capital_url}/api/bindings/{quote(persona_capital_binding_id, safe='')}"
    )
    plan = deployment_fetch(deployment_proof_url, timeout_seconds)
    registry_payload = fetch(registry_proof_url, timeout_seconds)
    # Generic authority transports are also used by Deployment's outbox worker.
    # They cannot stand in for the authenticated exact-ID approval reader.
    try:
        reader = approval_reader or configured_approval_reader('runtime_manager', base_url=governance_url)
        approval_evidence = reader.get(approval_decision_id)
        approval = approval_evidence.model_dump()
    except ApprovalUnavailable as exc:
        raise DeployAuthorityUnavailableError(str(exc)) from exc
    except ApprovalInvalid as exc:
        raise DeployAuthorityError(str(exc)) from exc
    capital_pool = fetch(capital_pool_proof_url, timeout_seconds)
    capital_admissibility = fetch(capital_admissibility_proof_url, timeout_seconds)
    persona_binding = fetch(persona_binding_proof_url, timeout_seconds)

    expected_plan = {
        "plan_id": plan_id,
        "status": plan_status,
        "target_stage": target_stage,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
        "strategy_id": strategy_id,
        "approval_decision_id": approval_decision_id,
        "capital_pool_id": capital_pool_id,
        "sponsor_persona_id": sponsor_persona_id,
    }
    plan_mismatches = [
        f"{field} expected {expected!r}, got {plan.get(field)!r}"
        for field, expected in expected_plan.items()
        if plan.get(field) != expected
    ]
    if plan_mismatches:
        raise DeployAuthorityError(
            "deployment authority mismatch: " + "; ".join(plan_mismatches)
        )
    plan_current_stage = str(plan.get("current_stage") or "").strip()
    if plan_current_stage not in {"none", "paper", "canary", "live"}:
        raise DeployAuthorityError(
            "DeploymentPlan.current_stage must be one of "
            "'none', 'paper', 'canary', or 'live'"
        )
    plan_metadata = plan.get("metadata")
    if plan_metadata is None:
        plan_metadata = {}
    if not isinstance(plan_metadata, Mapping):
        raise DeployAuthorityError("DeploymentPlan.metadata must be an object")
    plan_runtime_lifecycle = plan_metadata.get("runtime_lifecycle")
    if plan_runtime_lifecycle is None:
        plan_runtime_lifecycle = {}
    if not isinstance(plan_runtime_lifecycle, Mapping):
        raise DeployAuthorityError(
            "DeploymentPlan.metadata.runtime_lifecycle must be an object"
        )

    raw_entry = registry_payload.get("entry")
    if not isinstance(raw_entry, Mapping):
        raise DeployAuthorityError("registry readback is missing entry")
    entry = dict(raw_entry)

    expected_registry = {
        "registry_id": artifact_id,
        "version": artifact_version,
        "strategy_id": strategy_id,
        "artifact_state": "approved",
        "artifact_type": "execution_bundle",
        "approval_decision_id": approval_decision_id,
    }
    registry_mismatches = [
        f"{field} expected {expected!r}, got {entry.get(field)!r}"
        for field, expected in expected_registry.items()
        if entry.get(field) != expected
    ]
    deployment_stage = str(registry_payload.get("deployment_stage") or "").strip()
    admitted_registry_stages = frozenset(
        str(stage) for stage in allowed_registry_deployment_stages
    )
    if deployment_stage not in admitted_registry_stages:
        registry_mismatches.append(
            "deployment_stage expected one of "
            f"{sorted(admitted_registry_stages)!r}, got {deployment_stage!r}"
        )
    if registry_mismatches:
        raise DeployAuthorityError(
            "registry authority mismatch: " + "; ".join(registry_mismatches)
        )

    metadata = entry.get("metadata")
    artifact = metadata.get("strategy_artifact") if isinstance(metadata, Mapping) else None
    if not isinstance(artifact, Mapping):
        raise DeployAuthorityError(
            "registry entry is missing metadata.strategy_artifact loader payload"
        )
    embedded_expected = {
        "artifact_id": artifact_id,
        "version": artifact_version,
        "strategy_id": strategy_id,
    }
    embedded_mismatches = [
        f"{field} expected {expected!r}, got {artifact.get(field)!r}"
        for field, expected in embedded_expected.items()
        if artifact.get(field) != expected
    ]
    if embedded_mismatches:
        raise DeployAuthorityError(
            "embedded StrategyArtifact identity mismatch: "
            + "; ".join(embedded_mismatches)
        )
    try:
        validate_strategy_artifact(artifact)
        actual_checksum = strategy_artifact_checksum(artifact)
    except StrategyArtifactValidationError as exc:
        raise DeployAuthorityError(f"StrategyArtifact loader validation failed: {exc}") from exc
    recorded_checksum = _required_text(entry, "checksum", "registry entry")
    if recorded_checksum.lower() != actual_checksum.lower():
        raise DeployAuthorityError(
            "registry StrategyArtifact checksum mismatch: "
            f"recorded {recorded_checksum!r}, computed {actual_checksum!r}"
        )

    expected_approval = {
        'decision_id': approval_decision_id,
        'tenant_id': plan_metadata.get('tenant_id'),
        'target_type': 'registry_entry', 'target_id': artifact_id,
        'target_version': artifact_version, 'candidate_digest': recorded_checksum,
        'capital_pool_id': capital_pool_id, 'persona_id': sponsor_persona_id,
    }
    approval_mismatches = []
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        approval_evidence.require_valid(expected=expected_approval, now=observed_at)
    except ApprovalInvalid as exc:
        approval_mismatches.append(str(exc))

    if approval_mismatches:
        raise DeployAuthorityError(
            "governance authority mismatch: " + "; ".join(approval_mismatches)
        )

    pool_mismatches: list[str] = []
    if capital_pool.get("pool_id") != capital_pool_id:
        pool_mismatches.append(
            f"pool_id expected {capital_pool_id!r}, got {capital_pool.get('pool_id')!r}"
        )
    if capital_pool.get("status") != "active":
        pool_mismatches.append(
            f"status expected 'active', got {capital_pool.get('status')!r}"
        )
    if capital_pool.get("single_runtime_enforced") is not True:
        pool_mismatches.append("single_runtime_enforced must be literal true")
    if pool_mismatches:
        raise DeployAuthorityError(
            "capital pool authority mismatch: " + "; ".join(pool_mismatches)
        )

    expected_admissibility = {
        "persona_id": sponsor_persona_id,
        "capital_pool_id": capital_pool_id,
        "target_stage": target_stage,
        "permitted": True,
        "pool_status": "active",
        "single_runtime_enforced": True,
        "binding_id": persona_capital_binding_id,
        "binding_status": "active",
        "allowed_deployment_scope": allowed_deployment_scope,
    }
    admissibility_mismatches = [
        f"{field} expected {expected!r}, got {capital_admissibility.get(field)!r}"
        for field, expected in expected_admissibility.items()
        if capital_admissibility.get(field) != expected
    ]
    if admissibility_mismatches:
        raise DeployAuthorityError(
            "capital admissibility authority mismatch: "
            + "; ".join(admissibility_mismatches)
        )

    expected_binding = {
        "binding_id": persona_capital_binding_id,
        "persona_id": sponsor_persona_id,
        "capital_pool_id": capital_pool_id,
        "status": "active",
        "allowed_deployment_scope": allowed_deployment_scope,
    }
    binding_mismatches = [
        f"{field} expected {expected!r}, got {persona_binding.get(field)!r}"
        for field, expected in expected_binding.items()
        if persona_binding.get(field) != expected
    ]
    if persona_capital_binding_status != "active":
        binding_mismatches.append(
            "deploy request persona_capital_binding_status must be literal 'active'"
        )
    scope_rank = {"none": 0, "paper": 1, "canary": 2, "live": 3}
    if scope_rank.get(allowed_deployment_scope, -1) < scope_rank[target_stage]:
        binding_mismatches.append(
            f"allowed_deployment_scope={allowed_deployment_scope!r} does not permit "
            f"target_stage={target_stage!r}"
        )
    effective_from = str(persona_binding.get("effective_from") or "").strip()
    if effective_from and _parse_time(
        effective_from, "PersonaCapitalBinding.effective_from"
    ) > observed_at:
        binding_mismatches.append("PersonaCapitalBinding is not effective yet")
    effective_to = str(persona_binding.get("effective_to") or "").strip()
    if effective_to and _parse_time(
        effective_to, "PersonaCapitalBinding.effective_to"
    ) <= observed_at:
        binding_mismatches.append("PersonaCapitalBinding is expired")
    if binding_mismatches:
        raise DeployAuthorityError(
            "capital binding authority mismatch: " + "; ".join(binding_mismatches)
        )

    return {
        "status": "passed",
        "authority": "canonical_deployment_registry_governance_capital",
        "plan_id": plan_id,
        "plan_status": plan_status,
        "target_stage": target_stage,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
        "strategy_id": strategy_id,
        "artifact_checksum": actual_checksum,
        "approval_decision_id": approval_decision_id,
        "approval_actor_id": _required_text(
            approval, "actor_id", "ApprovalDecision"
        ),
        "capital_pool_id": capital_pool_id,
        "sponsor_persona_id": sponsor_persona_id,
        "persona_capital_binding_id": persona_capital_binding_id,
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": allowed_deployment_scope,
        "single_runtime_enforced": True,
        "deployment_plan_current_stage": plan_current_stage,
        "deployment_plan_binding_id": plan.get("binding_id"),
        "deployment_plan_runtime_lifecycle": dict(plan_runtime_lifecycle),
        "deployment_plan_transition_type": plan.get("transition_type"),
        "deployment_plan_runtime_action": plan.get("runtime_action"),
        "deployment_plan_scale": (
            dict(plan["scale"]) if isinstance(plan.get("scale"), Mapping) else None
        ),
        "deployment_plan_rollback": (
            dict(plan["rollback"])
            if isinstance(plan.get("rollback"), Mapping)
            else None
        ),
        "deployment_plan_sha256": _canonical_digest(plan),
        "deployment_plan_authority_sha256": _canonical_digest(
            _deployment_plan_authority_view(plan)
        ),
        "registry_entry_sha256": _canonical_digest(entry),
        "approval_decision_sha256": _canonical_digest(approval),
        "capital_pool_sha256": _canonical_digest(capital_pool),
        "capital_admissibility_sha256": _canonical_digest(capital_admissibility),
        "persona_capital_binding_sha256": _canonical_digest(persona_binding),
        "registry_view_sha256": _canonical_digest(registry_payload),
        "registry_deployment_stage": deployment_stage,
        "deployment_proof_url": deployment_proof_url,
        "registry_proof_url": registry_proof_url,
        "approval_proof_url": approval_proof_url,
        "capital_pool_proof_url": capital_pool_proof_url,
        "capital_admissibility_proof_url": capital_admissibility_proof_url,
        "persona_binding_proof_url": persona_binding_proof_url,
        "verified_at": observed_at.isoformat().replace("+00:00", "Z"),
    }


__all__ = [
    "DeployAuthorityError",
    "DeployAuthorityUnavailableError",
    "verify_deploy_authorities",
]
