#!/usr/bin/env python3
"""Read-only check of normalized owner GET receipts; never calls a write route.

Input: JSON array of {tenant_id, persona_id, request_payload, capital_pool,
risk_policy_resolution, governance_proposal}. Hosted proof collects these
from the existing provisioning, Capital and Governance owner read surfaces.
"""
import argparse
import json
from pathlib import Path


def check(rows, expected_count=3):
    failures = []
    identities, pools = set(), set()
    if expected_count < 1 or not isinstance(rows, list) or len(rows) != expected_count:
        return {"passed": False, "failures": ["unexpected_account_count"]}
    for row in rows:
        tenant, persona = row["tenant_id"], row["persona_id"]
        pool, resolution, proposal = row["capital_pool"], row["risk_policy_resolution"], row["governance_proposal"]
        meta = pool.get("metadata", {})
        subject = (proposal.get("metadata") or {}).get("subject", {})
        req = row["request_payload"]
        req_meta = req.get("metadata") or {}
        risk_map = req.get("risk_profile") or {}
        sources = [req.get(k) for k in ("risk", "risk_level", "riskLevel")]
        sources += [risk_map.get(k) for k in ("risk_level", "riskLevel")]
        sources += [req_meta.get(k) for k in ("requested_risk_profile", "risk_profile", "requested_risk")]
        values = {v for v in sources if v is not None and v != ""}
        profile = next(iter(values)) if len(values) == 1 else None
        identity = (tenant, persona)
        valid = (
            profile in {"low", "medium", "high"} and identity not in identities
            and pool["pool_id"] not in pools
            and meta.get("persona_id") == persona and pool.get("tenant_id") == tenant
            and meta.get("execution_context") == "paper" and pool.get("single_runtime_enforced") is True
            and meta.get("requested_risk_profile") == profile
            and meta.get("require_approved_profile_mapping") is True
            and resolution.get("capital_pool_id") == pool["pool_id"]
            and resolution.get("tenant_id") == tenant and resolution.get("requested_profile") == profile
            and resolution.get("resolved") is False
            and resolution.get("status") == "unresolved_profile_mapping"
            and resolution.get("allow_new_or_increased_risk") is False
            and resolution.get("allow_risk_reducing_actions") is True
            and proposal.get("tenant_id") == tenant
            and proposal.get("target_type") == "capital_pool_activation"
            and proposal.get("target_id") == pool["pool_id"]
            and subject.get("pool_id") == pool["pool_id"]
            and subject.get("requested_risk") == profile
            and subject.get("risk_direction") == "increase"
            and subject.get("risk_policy_id") == resolution.get("risk_policy_id")
            and subject.get("risk_policy_version") == resolution.get("risk_policy_version")
            and subject.get("risk_policy_digest") == resolution.get("risk_policy_digest")
            and proposal.get("target_version") == pool.get("approval_digest")
            and (proposal.get("decision_state") in {"proposed", "under_review"}
                 or (proposal.get("decision_state") == "decided" and proposal.get("decision") == "rejected"))
        )
        identities.add(identity)
        pools.add(pool["pool_id"])
        if not valid:
            failures.append(persona)
    return {"passed": not failures, "checked": len(rows), "failures": failures}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts", type=Path)
    parser.add_argument("--expected-count", type=int, default=3)
    args = parser.parse_args()
    try:
        result = check(json.loads(args.receipts.read_text()), args.expected_count)
    except (AttributeError, KeyError, TypeError, ValueError):
        result = {"passed": False, "failures": ["malformed_owner_receipts"]}
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)
