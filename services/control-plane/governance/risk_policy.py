"""Governance-plane import surface for the capital-owned RiskPolicy contract."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.capital.risk_policy import (
    RiskPolicy,
    RiskPolicyCheck,
    RiskPolicyCheckStatus,
    RiskPolicyDecision,
    RiskPolicyError,
    RiskPolicyEvaluation,
    RiskPolicyEvaluationContext,
    RiskPolicyEvaluator,
    RiskPolicyTargetType,
    risk_policy_rejection_message,
)

__all__ = [
    "RiskPolicy",
    "RiskPolicyCheck",
    "RiskPolicyCheckStatus",
    "RiskPolicyDecision",
    "RiskPolicyError",
    "RiskPolicyEvaluation",
    "RiskPolicyEvaluationContext",
    "RiskPolicyEvaluator",
    "RiskPolicyTargetType",
    "risk_policy_rejection_message",
]
