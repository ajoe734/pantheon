"""Projection of Capital's authoritative paper risk change on Persona GET/PATCH."""
from __future__ import annotations

from typing import Any, Mapping
import re

from .fleet_classification import classify_persona_purpose


def key_issued_at_ms(key: str) -> int:
    """Use execute-plans' existing stable idk_<base36 ms>_<nonce> keys.

    Opaque keys cannot support both bounded history and indefinite replay
    protection. Reject them before mutation rather than timestamping a retry.
    """
    match = re.fullmatch(r"idk_([a-z0-9]{1,10})_[a-z0-9]{1,64}", key)
    stamp = int(match[1], 36) if match else 0
    if not 0 < stamp <= 9999999999999:
        raise ValueError("Paper risk Idempotency-Key must use idk_<base36 issued milliseconds>_<nonce>")
    return stamp


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
    from .service import _chain_paper_capital_coordinator, _persona_provisioning_store

    try:
        pool_id = paper_pool_id(raw)
        if pool_id is None:
            return
        coordinator = _chain_paper_capital_coordinator(tenant_id)
        persona_id = str(raw.get("persona_id") or raw.get("id"))
        change = coordinator.read_paper_risk_change(
            tenant_id=tenant_id, persona_id=persona_id, pool_id=pool_id,
        )
        if change.get("requestedRisk") is None:
            # Before the first ordered edit, use real request provenance, never
            # the display DTO's normalized/default risk or policy metadata.
            record = _persona_provisioning_store().get_by_persona(tenant_id, persona_id)
            source = record.request_payload if record is not None else {
                **raw, "risk_level": (raw.get("metadata") or {}).get("risk_level"),
            }
            requested = coordinator._extract_request_risk_profile_context(source)[0]
            change["requestedRisk"] = requested
            if requested is None:
                change["status"] = "unavailable"
        dto["riskPolicyChange"] = public_change(change)
        if change.get("requestedRisk"):
            dto["risk"] = change["requestedRisk"]
    except Exception:
        dto["riskPolicyChange"] = {"requestedRisk": None, "effectiveRisk": None,
                                   "status": "unavailable", "policyVersion": None, "approvalId": None}
