"""Latest paper risk intent, separate from immutable Persona creation evidence.

The pool row is the ordering authority. Receipts never evict: at the bounded
limit a new key fails closed rather than replaying an old edit as a new one.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from .pg_store import CapitalPool, CapitalPoolError, utc_now
from .risk_policy import (
    capital_pool_approval_digest, discover_approved_profile_risk_policy,
    resolve_pool_risk_policy_binding, risk_policy_digest,
)

MAX_CHANGE_RECEIPTS = 128
PROFILES = {"low", "medium", "high"}
PROFILE_ALIASES = ("requested_risk_profile", "risk_profile", "requested_risk", "risk")


def validate_scope(pool: CapitalPool, tenant_id: str, persona_id: str) -> None:
    meta = pool.metadata or {}
    # Formal ownership wins over metadata. Blank legacy ownership is unknown.
    tenant = getattr(pool, "tenant_id", None) if hasattr(pool, "tenant_id") else meta.get("tenant_id")
    if (not tenant_id or tenant != tenant_id or meta.get("tenant_id") != tenant_id
            or not persona_id or meta.get("persona_id") != persona_id
            or meta.get("execution_context") != "paper" or pool.single_runtime_enforced is not True
            or pool.status not in {"active", "suspended"}):
        raise CapitalPoolError("paper_risk_change_scope_invalid")


def _wire(pool: CapitalPool) -> dict[str, Any]:
    wire = pool.to_dict()
    wire["approval_digest"] = capital_pool_approval_digest(pool)
    return wire


def _resolve(pool: CapitalPool, tenant_id: str, reader: Any, loader: Any):
    return resolve_pool_risk_policy_binding(
        pool=_wire(pool), capital_pool_id=pool.pool_id, expected_tenant_id=tenant_id,
        expected_stage="paper", approval_reader=reader, risk_policy_loader=loader,
        require_approved_profile_mapping=True, require_limits=("gross_limit",),
        require_active=True, raise_on_error=False,
    )


def _bound(pool: CapitalPool, resolution: Any) -> bool:
    """Discovery alone is not a persisted effective binding."""
    policy = (pool.metadata or {}).get("risk_policy")
    return bool(pool.status == "active" and resolution.resolved and resolution.requested_profile in PROFILES
                and resolution.profile_mapping_approval_id
                and isinstance(policy, dict)
                and risk_policy_digest(policy) == resolution.risk_policy_digest
                and (not pool.risk_policy_ref or pool.risk_policy_ref == resolution.risk_policy_id))


def _pending_resolution(pool: CapitalPool, requested: str, current: Any, *,
                        tenant_id: str, reader: Any, loader: Any) -> dict[str, Any]:
    result = current.to_dict()
    discovered = discover_approved_profile_risk_policy(
        resolved_pool_id=pool.pool_id, effective_tenant_id=tenant_id, clean_stage="paper",
        clean_profile=requested, base_pool=_wire(pool), approval_reader=reader,
        risk_policy_loader=loader, require_limits=("gross_limit",),
    )
    # An applicable grant discovered after the original edit is a reason to
    # reconcile, never to mint another proposal or claim an installed binding.
    if discovered.resolved:
        result["owner_decision_request"] = None
        return result
    decision = {"requested_profile": requested, "capital_pool_id": pool.pool_id,
                "tenant_id": tenant_id, "stage": "paper", "absence_source": discovered.absence_source,
                "decision_required": discovered.reason}
    result.update(status=discovered.status, reason_code=discovered.reason_code, reason=discovered.reason,
                  risk_policy_id=None, risk_policy_version=None, risk_policy_digest=None,
                  owner_decision_request=decision)
    if discovered.status == "ambiguous_profile_mapping":
        decision.update(ambiguous_policy_ids=list(discovered.ambiguous_policy_ids),
                        ambiguous_approval_decision_ids=list(discovered.ambiguous_decision_ids))
        return result
    candidate = discovered.candidate_policy
    candidate_meta = candidate.metadata if candidate else {}
    if not candidate or not any(candidate_meta.get(key) == requested for key in
                                ("requested_profile", "requested_risk", "risk_profile")):
        # A legacy/default policy with no matching definition is not a proposed
        # financial mapping. Retain the requested tier for its owner to define.
        decision["absence_source"] = "policy_definition_and_approval_absent"
        return result
    candidate_pool = replace(pool, risk_policy_ref=None,
                             metadata={**pool.metadata, "risk_policy": candidate.to_dict()})
    decision.update(candidate_risk_policy_id=candidate.risk_policy_id,
                    candidate_risk_policy_version=candidate.version,
                    candidate_risk_policy_digest=risk_policy_digest(candidate),
                    candidate_pool_approval_digest=capital_pool_approval_digest(candidate_pool))
    return result


def read_change(pool: CapitalPool, *, tenant_id: str, persona_id: str,
                reader: Any, loader: Any) -> dict[str, Any]:
    validate_scope(pool, tenant_id, persona_id)
    meta = pool.metadata or {}
    intent = meta.get("risk_profile_change") or {}
    requested = intent.get("requested_profile") or meta.get("requested_risk_profile")
    current = _resolve(pool, tenant_id, reader, loader)
    effective = current if _bound(pool, current) else None
    if effective is None and intent.get("previous_effective_profile") in PROFILES:
        prior_meta = deepcopy(meta)
        for key in PROFILE_ALIASES:
            if key in prior_meta:
                prior_meta[key] = intent["previous_effective_profile"]
        prior_meta["requested_risk_profile"] = intent["previous_effective_profile"]
        prior = _resolve(replace(pool, metadata=prior_meta), tenant_id, reader, loader)
        if _bound(pool, prior):
            effective = prior
    resolution = current.to_dict()
    if intent and requested in PROFILES and not (effective and effective.requested_profile == requested):
        resolution = _pending_resolution(pool, requested, current, tenant_id=tenant_id, reader=reader, loader=loader)
    return {
        "requestedRisk": requested if requested in PROFILES else None,
        "effectiveRisk": effective.requested_profile if effective else None,
        "status": ("unavailable" if pool.status != "active" else
                   "effective" if effective and effective.requested_profile == requested else "pending_approval"),
        "policyVersion": effective.risk_policy_version if effective else None,
        "approvalId": effective.profile_mapping_approval_id if effective else None,
        "policyDigest": effective.risk_policy_digest if effective else None,
        "revision": int(intent.get("revision", 0)),
        "requestId": intent.get("request_id"),
        "resolution": resolution,
    }


def change_profile(pool: CapitalPool, body: Any, *, tenant_id: str,
                   reader: Any, loader: Any) -> CapitalPool:
    validate_scope(pool, tenant_id, body.expected_persona_id)
    meta = deepcopy(pool.metadata or {})
    intent = meta.get("risk_profile_change") or {}
    revision = int(intent.get("revision", 0))
    receipts = dict(meta.get("risk_profile_change_receipts") or {})
    receipt = receipts.get(body.request_id)
    if receipt:
        if receipt["request_hash"] != body.request_hash or receipt["profile"] != body.requested_profile:
            raise CapitalPoolError("paper_risk_change_idempotency_conflict")
        if receipt["revision"] != revision:
            return pool  # Superseded retry observes the latest intent; never replays it.
    else:
        if body.expected_revision != revision:
            raise CapitalPoolError("paper_risk_change_revision_conflict")
        if len(receipts) >= MAX_CHANGE_RECEIPTS:
            raise CapitalPoolError("paper_risk_change_receipt_limit")
        before = read_change(pool, tenant_id=tenant_id, persona_id=body.expected_persona_id,
                             reader=reader, loader=loader)
        revision += 1
        intent = {"revision": revision, "request_id": body.request_id,
                  "requested_profile": body.requested_profile,
                  "requested_by": body.requested_by, "requested_at": utc_now(),
                  "previous_effective_profile": before["effectiveRisk"]}
        receipts[body.request_id] = {"request_hash": body.request_hash,
                                     "profile": body.requested_profile, "revision": revision}
        # Legacy aliases must agree before a genuine edit translates them.
        prior_profiles = [meta[key] for key in PROFILE_ALIASES if meta.get(key) not in (None, "")]
        if any(not isinstance(p, str) or p not in PROFILES for p in prior_profiles) or len(set(prior_profiles)) > 1:
            raise CapitalPoolError("requested_profile_source_conflict")
        for key in PROFILE_ALIASES:
            if key in meta:
                meta[key] = body.requested_profile
        meta.update(requested_risk_profile=body.requested_profile,
                    require_approved_profile_mapping=True,
                    risk_profile_change=intent, risk_profile_change_receipts=receipts)
    candidate = replace(pool, metadata=meta)
    # Only existing policy definitions and independently verified approvals can
    # supply financial limits. Absence leaves the installed policy untouched.
    discovered = discover_approved_profile_risk_policy(
        resolved_pool_id=pool.pool_id, effective_tenant_id=tenant_id,
        clean_stage="paper", clean_profile=body.requested_profile,
        base_pool=_wire(candidate), approval_reader=reader,
        risk_policy_loader=loader, require_limits=("gross_limit",),
    )
    if pool.status == "active" and discovered.resolved and discovered.policy and discovered.approval_decision_id:
        policy = deepcopy(discovered.raw_policy) if discovered.raw_policy else discovered.policy.to_dict()
        meta.update(risk_policy=policy, profile_mapping_approval_id=discovered.approval_decision_id,
                    risk_policy_stale=False, risk_policy_superseded_by=None,
                    risk_policy_expires_at=None, risk_policy_effective_from=None,
                    expected_risk_policy_version=discovered.policy.version,
                    expected_risk_policy_digest=risk_policy_digest(discovered.policy))
        candidate = replace(pool, metadata=meta, risk_policy_ref=discovered.risk_policy_ref)
        verified = _resolve(candidate, tenant_id, reader, loader)
        if not _bound(candidate, verified):
            raise CapitalPoolError("paper_risk_change_binding_readback_failed")
    return candidate
