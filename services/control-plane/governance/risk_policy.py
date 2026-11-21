"""Governance-plane import surface for the capital-owned RiskPolicy contract."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.capital.risk_policy import (
    POLICY_DIGEST_FIELDS,
    POLICY_PROVENANCE_DIGEST_KEYS,
    BoundPoolRiskPolicyResolution,
    RiskPolicy,
    RiskPolicyCheck,
    RiskPolicyCheckStatus,
    RiskPolicyDecision,
    RiskPolicyError,
    RiskPolicyEvaluation,
    RiskPolicyEvaluationContext,
    RiskPolicyEvaluator,
    RiskPolicyLoadError,
    RiskPolicyTargetType,
    capital_pool_approval_digest,
    load_risk_policy,
    load_risk_policy_model,
    normalize_risk_policy,
    read_capital_pool_record,
    resolve_bound_pool_risk_policy,
    resolve_pool_risk_policy_binding,
    risk_policy_digest,
    risk_policy_rejection_message,
)

__all__ = [
    "POLICY_DIGEST_FIELDS",
    "POLICY_PROVENANCE_DIGEST_KEYS",
    "BoundPoolRiskPolicyResolution",
    "RiskPolicy",
    "RiskPolicyCheck",
    "RiskPolicyCheckStatus",
    "RiskPolicyDecision",
    "RiskPolicyError",
    "RiskPolicyEvaluation",
    "RiskPolicyEvaluationContext",
    "RiskPolicyEvaluator",
    "RiskPolicyLoadError",
    "RiskPolicyTargetType",
    "capital_pool_approval_digest",
    "load_risk_policy",
    "load_risk_policy_model",
    "normalize_risk_policy",
    "read_capital_pool_record",
    "resolve_bound_pool_risk_policy",
    "resolve_pool_risk_policy_binding",
    "risk_policy_digest",
    "risk_policy_rejection_message",
]
