"""Projection of Capital's authoritative paper risk change on Persona GET/PATCH."""
from __future__ import annotations

from typing import Any, Mapping

from .fleet_classification import classify_persona_purpose


def paper_pool_id(raw: Mapping[str, Any]) -> str | None:
    if classify_persona_purpose(raw)[0] != "trading":
        return None
    meta = raw.get("metadata") or {}
    modes = [str(source.get(key) or "").lower() for source in (raw, meta)
             for key in ("capital_mode", "deployment_stage", "execution_context")]
    if any(mode in {"live", "real", "canary", "live_running", "canary_running"} for mode in modes):
        return None
    if not any(mode in {"paper", "paper_running"} for mode in modes):
        return None
    ids = {str(meta[key]) for key in ("capital_pool_id", "internal_paper_capital_pool_id", "legacy_paper_capital_pool_id")
           if meta.get(key)}
    # Conflicting pool identities cannot authorize a write to either pool.
    if len(ids) > 1:
        raise ValueError("paper_risk_change_pool_identity_conflict")
    return next(iter(ids)) if ids else None


def public_change(change: Mapping[str, Any]) -> dict[str, Any]:
    return {key: change.get(key) for key in
            ("requestedRisk", "effectiveRisk", "status", "policyVersion", "approvalId")}


def project_risk_change(dto: dict[str, Any], raw: Mapping[str, Any], tenant_id: str) -> None:
    from .service import _chain_paper_capital_coordinator

    try:
        pool_id = paper_pool_id(raw)
        if pool_id is None:
            return
        change = _chain_paper_capital_coordinator(tenant_id).read_paper_risk_change(
            tenant_id=tenant_id, persona_id=str(raw.get("persona_id") or raw.get("id")), pool_id=pool_id,
        )
        if change.get("requestedRisk") is None and dto.get("risk") in {"low", "medium", "high"}:
            # Before the first ordered edit, the verified Persona request is
            # still the request authority. It cannot establish an effective tier.
            change["requestedRisk"] = dto["risk"]
        dto["riskPolicyChange"] = public_change(change)
        if change.get("requestedRisk"):
            dto["risk"] = change["requestedRisk"]
    except Exception:
        dto["riskPolicyChange"] = {"requestedRisk": None, "effectiveRisk": None,
                                   "status": "unavailable", "policyVersion": None, "approvalId": None}
