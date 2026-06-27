"""Fleet desired-state query for the paper/canary RuntimeBinding fleet reconciler.

This module is the canonical source of truth for which RuntimeBindings belong
to the managed fleet and what policy constraints apply per binding.

Fleet reconciliation scope
--------------------------
FLEET_MANAGED_STAGES    : stages under automated fleet management ("paper", "canary")
FLEET_ELIGIBLE_STATUS   : only bindings with this status are desired fleet members

Non-fleet bindings (excluded from fleet desired-state)
------------------------------------------------------
All other stages (live, frozen) are not managed by the paper/canary fleet reconciler.
All non-active statuses (retired, failed, pending_pause, paused) are excluded.
  - retired / failed   : terminal — must never run again
  - pending_pause      : draining — worker must be stopped, not started
  - paused             : draining — worker must be stopped, not started

Policy envelope
---------------
Each desired binding carries a PolicyEnvelope that records the stage, scope, and
fleet-eligibility decision.  The reconciler logs this envelope so that inclusion
or exclusion decisions can be audited without re-deriving the query logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional

# ---------------------------------------------------------------------------
# Fleet scope constants
# ---------------------------------------------------------------------------

FLEET_MANAGED_STAGES: FrozenSet[str] = frozenset({"paper", "canary"})
FLEET_ELIGIBLE_STATUS: FrozenSet[str] = frozenset({"active"})

# Exclusion reason for each non-eligible status value.
FLEET_EXCLUDED_STATUSES: Dict[str, str] = {
    "retired": "terminal_status",
    "failed": "terminal_status",
    "pending_pause": "draining",
    "paused": "draining",
}

_QUERY_VERSION = "1"


# ---------------------------------------------------------------------------
# Policy envelope
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PolicyEnvelope:
    """Per-binding policy constraints captured at query time.

    Fields
    ------
    stage                    : deployment_mode of the binding
    allowed_deployment_scope : from the binding's activation_gate metadata,
                               or None when not recorded
    fleet_eligible           : True when this binding is a desired fleet member
    exclusion_reason         : non-None when not fleet_eligible; one of
                               "non_fleet_stage", "terminal_status", "draining",
                               or "non_active_status"
    """

    stage: str
    allowed_deployment_scope: Optional[str]
    fleet_eligible: bool
    exclusion_reason: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "stage": self.stage,
            "fleet_eligible": self.fleet_eligible,
        }
        if self.allowed_deployment_scope is not None:
            d["allowed_deployment_scope"] = self.allowed_deployment_scope
        if self.exclusion_reason is not None:
            d["exclusion_reason"] = self.exclusion_reason
        return d


# ---------------------------------------------------------------------------
# Desired binding entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DesiredFleetBinding:
    """A binding that is a desired fleet member, with its policy envelope.

    Only bindings with deployment_mode in FLEET_MANAGED_STAGES and
    status in FLEET_ELIGIBLE_STATUS appear here.

    Fields
    ------
    binding_id                 : unique binding identifier
    runtime_id                 : the LEAN runtime instance / worker process
    capital_pool_id            : the capital pool this binding services
    deployment_mode            : stage ("paper" | "canary")
    status                     : current binding status ("active")
    plan_id                    : the triggering DeploymentPlan
    artifact_id                : the executing artifact
    artifact_version           : the executing artifact version
    persona_capital_binding_id : the authorising PersonaCapitalBinding
    effective_at               : when the binding became active (UTC ISO-8601)
    policy_envelope            : policy constraints at query time
    """

    binding_id: str
    runtime_id: str
    capital_pool_id: str
    deployment_mode: str
    status: str
    plan_id: str
    artifact_id: str
    artifact_version: str
    persona_capital_binding_id: str
    effective_at: str
    policy_envelope: PolicyEnvelope

    def to_dict(self) -> Dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "runtime_id": self.runtime_id,
            "capital_pool_id": self.capital_pool_id,
            "deployment_mode": self.deployment_mode,
            "status": self.status,
            "plan_id": self.plan_id,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "persona_capital_binding_id": self.persona_capital_binding_id,
            "effective_at": self.effective_at,
            "policy_envelope": self.policy_envelope.to_dict(),
        }


# ---------------------------------------------------------------------------
# Excluded binding record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExcludedBinding:
    """A binding explicitly excluded from fleet desired state, with reason.

    Workers for excluded bindings must be stopped by the reconciler, not started.
    """

    binding_id: str
    deployment_mode: str
    status: str
    exclusion_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "deployment_mode": self.deployment_mode,
            "status": self.status,
            "exclusion_reason": self.exclusion_reason,
        }


# ---------------------------------------------------------------------------
# Fleet desired state
# ---------------------------------------------------------------------------

@dataclass
class FleetDesiredState:
    """The complete desired-state result for fleet reconciliation.

    The reconciler must:
      - ensure exactly one worker is running for each binding in ``bindings``
      - stop workers for any binding_id that appears in ``excluded``
      - stop workers for any binding_id not present in either list (stale)

    Fields
    ------
    query_version     : schema version; increment when the shape changes
    fleet_stages      : stages included in fleet management for this response
    eligible_statuses : statuses that qualify a binding as a desired fleet member
    bindings          : desired fleet members (active paper/canary bindings)
    excluded          : bindings explicitly excluded, with exclusion reason
    """

    query_version: str
    fleet_stages: List[str]
    eligible_statuses: List[str]
    bindings: List[DesiredFleetBinding]
    excluded: List[ExcludedBinding]

    @property
    def active_count(self) -> int:
        return len(self.bindings)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)

    def to_dict(self, *, include_excluded: bool = False) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "query_version": self.query_version,
            "fleet_stages": self.fleet_stages,
            "eligible_statuses": self.eligible_statuses,
            "active_count": self.active_count,
            "bindings": [b.to_dict() for b in self.bindings],
        }
        if include_excluded:
            result["excluded_count"] = self.excluded_count
            result["excluded"] = [e.to_dict() for e in self.excluded]
        return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_allowed_scope(binding_dict: Dict[str, Any]) -> Optional[str]:
    """Extract allowed_deployment_scope from binding metadata.activation_gate."""
    metadata = binding_dict.get("metadata")
    if not isinstance(metadata, dict):
        return None
    gate = metadata.get("activation_gate")
    if not isinstance(gate, dict):
        return None
    scope = str(gate.get("allowed_deployment_scope") or "").strip()
    return scope or None


def _policy_envelope_for(binding_dict: Dict[str, Any]) -> PolicyEnvelope:
    """Build a PolicyEnvelope for a RuntimeBinding dict."""
    stage = str(binding_dict.get("deployment_mode") or "")
    status = str(binding_dict.get("status") or "")
    allowed_scope = _extract_allowed_scope(binding_dict)

    if stage not in FLEET_MANAGED_STAGES:
        return PolicyEnvelope(
            stage=stage,
            allowed_deployment_scope=allowed_scope,
            fleet_eligible=False,
            exclusion_reason="non_fleet_stage",
        )

    if status not in FLEET_ELIGIBLE_STATUS:
        reason = FLEET_EXCLUDED_STATUSES.get(status, "non_active_status")
        return PolicyEnvelope(
            stage=stage,
            allowed_deployment_scope=allowed_scope,
            fleet_eligible=False,
            exclusion_reason=reason,
        )

    return PolicyEnvelope(
        stage=stage,
        allowed_deployment_scope=allowed_scope,
        fleet_eligible=True,
        exclusion_reason=None,
    )


# ---------------------------------------------------------------------------
# Public query function
# ---------------------------------------------------------------------------

def build_fleet_desired_state(
    bindings: List[Dict[str, Any]],
    *,
    stage_filter: Optional[str] = None,
) -> FleetDesiredState:
    """Build the fleet desired state from a list of RuntimeBinding dicts.

    Bindings in FLEET_MANAGED_STAGES with status in FLEET_ELIGIBLE_STATUS become
    desired fleet members.  All others are recorded in the ``excluded`` list with
    their exclusion reason so the reconciler can stop their workers.

    Bindings that do not match ``stage_filter`` (when supplied) are silently
    omitted from both lists — the caller scoped the query to a single stage.

    Parameters
    ----------
    bindings      : list of RuntimeBinding.to_dict() records
    stage_filter  : when set, only process bindings with this deployment_mode
                    ("paper" | "canary")

    Returns
    -------
    FleetDesiredState with bindings (desired members) and excluded (non-members)
    """
    desired: List[DesiredFleetBinding] = []
    excluded: List[ExcludedBinding] = []

    for b in bindings:
        stage = str(b.get("deployment_mode") or "")

        if stage_filter is not None and stage != stage_filter:
            continue

        envelope = _policy_envelope_for(b)

        if envelope.fleet_eligible:
            desired.append(
                DesiredFleetBinding(
                    binding_id=str(b.get("binding_id") or ""),
                    runtime_id=str(b.get("runtime_id") or ""),
                    capital_pool_id=str(b.get("capital_pool_id") or ""),
                    deployment_mode=stage,
                    status=str(b.get("status") or ""),
                    plan_id=str(b.get("plan_id") or ""),
                    artifact_id=str(b.get("artifact_id") or ""),
                    artifact_version=str(b.get("artifact_version") or ""),
                    persona_capital_binding_id=str(
                        b.get("persona_capital_binding_id") or ""
                    ),
                    effective_at=str(b.get("effective_at") or ""),
                    policy_envelope=envelope,
                )
            )
        else:
            excluded.append(
                ExcludedBinding(
                    binding_id=str(b.get("binding_id") or ""),
                    deployment_mode=stage,
                    status=str(b.get("status") or ""),
                    exclusion_reason=envelope.exclusion_reason or "unknown",
                )
            )

    if stage_filter and stage_filter in FLEET_MANAGED_STAGES:
        fleet_stages = [stage_filter]
    else:
        fleet_stages = sorted(FLEET_MANAGED_STAGES)

    return FleetDesiredState(
        query_version=_QUERY_VERSION,
        fleet_stages=fleet_stages,
        eligible_statuses=sorted(FLEET_ELIGIBLE_STATUS),
        bindings=desired,
        excluded=excluded,
    )
