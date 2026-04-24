"""
Core critic: assembles CriticResult from findings, key risks, and decision guidance.

Critics are called for borderline evaluations, failure forensics, and risk-flagged artifacts.
Like evaluators, critic output is advisory; promotion gates remain authoritative.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import (
    CriticResult,
    DecisionGuidance,
    Finding,
    KeyRisk,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def critique(
    *,
    strategy_id: str,
    target_artifact_id: str,
    target_artifact_type: str,
    target_promotion_state: str,
    critique_trigger: str,
    findings: List[Finding],
    key_risks: List[KeyRisk],
    decision_guidance: DecisionGuidance,
    critic_id: str = "critic_alpha_v1",
    critic_version: str = "1.0.0",
    registry_id: Optional[str] = None,
    critique_timestamp: Optional[str] = None,
    evaluation_context: Optional[Dict[str, Any]] = None,
    rationale: Optional[str] = None,
) -> CriticResult:
    """
    Build and return a validated CriticResult.

    findings must be non-empty per contract.
    key_risks may be empty.
    """
    return CriticResult(
        registry_id=registry_id or f"crit-{strategy_id}-{uuid.uuid4().hex[:8]}",
        artifact_type="critique_result",
        strategy_id=strategy_id,
        target_artifact_id=target_artifact_id,
        target_artifact_type=target_artifact_type,
        target_promotion_state=target_promotion_state,
        critic_id=critic_id,
        critic_version=critic_version,
        critique_timestamp=critique_timestamp or _utc_now(),
        critique_trigger=critique_trigger,
        findings=findings,
        key_risks=key_risks,
        decision_guidance=decision_guidance,
        evaluation_context=evaluation_context,
        rationale=rationale,
    )
