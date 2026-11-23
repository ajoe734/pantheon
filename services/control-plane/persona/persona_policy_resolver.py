"""
PersonaPolicyResolver — resolves effective policy decisions from persona policy references.

Converts the static policy reference fields on a Persona record
(route_policy_id, consult_policy_id, tool_profile_id) into a concrete,
auditable effective capability set.

Fail-closed rules
-----------------
- Suspended persona → all capabilities denied, return immediately.
- Unknown policy ID → that capability bucket is empty; denial reason added.
- Capital eligibility failure → denial reason added; does not empty other buckets.

Usage
-----
    from persona_policy_resolver import (
        ConsultPolicy,
        PersonaPolicyResolver,
        RoutePolicy,
        ToolProfile,
    )

    resolver = PersonaPolicyResolver(
        tool_profiles={"tp-default": ToolProfile("tp-default", ["web_search", "code_exec"])},
        route_policies={"rp-standard": RoutePolicy("rp-standard", allowed_workflows=["trade"])},
        consult_policies={"cp-standard": ConsultPolicy("cp-standard", allowed_skills=["market-analysis"])},
        binding_store=my_binding_store,   # optional; enables capital eligibility checks
    )

    result = resolver.resolve(persona)
    snapshot = resolver.resolve_to_snapshot(persona, snapshot_id="snap-1", generated_at=utc_now())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from types import SimpleNamespace
from collections.abc import Mapping


# ---------------------------------------------------------------------------
# Policy catalog entry types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolProfile:
    """A tool profile entry mapping a profile_id to allowed tools."""
    profile_id: str
    allowed_tools: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RoutePolicy:
    """A route policy entry controlling allowed workflows and optional skills."""
    policy_id: str
    allowed_workflows: List[str] = field(default_factory=list)
    allowed_skills: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConsultPolicy:
    """A consult policy entry controlling allowed consultation skills."""
    policy_id: str
    allowed_skills: List[str] = field(default_factory=list)
    # Panel personas (risk, red team) are consultable but never trade; a
    # consult policy with ``consultable=False`` keeps a persona off panels.
    consultable: bool = True


# Consult policy recorded on the risk / red team panel personas by the governed
# bootstrap (services.persona.consult_triggers.CONSULT_PANEL_POLICY_ID):
# consultable, no skills; with no tool or route policy they hold no trading
# authority.
BUILTIN_CONSULT_POLICIES: Dict[str, ConsultPolicy] = {
    "consult-panel.non-trading": ConsultPolicy(
        "consult-panel.non-trading", allowed_skills=[], consultable=True
    ),
}


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PolicyResolutionResult:
    """
    The outcome of resolving a persona's policy references into effective capabilities.

    Check ``denied`` to see whether any resolution failed closed; examine
    ``denial_reasons`` for the specific causes.
    """
    effective_tools: List[str] = field(default_factory=list)
    effective_skills: List[str] = field(default_factory=list)
    effective_workflows: List[str] = field(default_factory=list)
    source_refs: List[str] = field(default_factory=list)
    denial_reasons: List[str] = field(default_factory=list)
    consultable: bool = False

    @property
    def denied(self) -> bool:
        """True when at least one resolution step was denied."""
        return bool(self.denial_reasons)


# ---------------------------------------------------------------------------
# PersonaPolicyResolver
# ---------------------------------------------------------------------------

class PersonaPolicyResolver:
    """
    Resolves effective policy decisions from persona policy reference fields.

    Constructed with a policy catalog. Call ``resolve()`` for a given Persona
    to get a ``PolicyResolutionResult``, or call ``resolve_to_snapshot()`` to
    produce a ``CapabilitySnapshot`` in one step.

    Parameters
    ----------
    tool_profiles    : mapping of profile_id → ToolProfile
    route_policies   : mapping of policy_id → RoutePolicy
    consult_policies : mapping of policy_id → ConsultPolicy
    binding_store    : optional PersonaCapitalBindingStore; enables capital
                       eligibility checks when resolve() is called with
                       capital_pool_id and target_scope.
    """

    def __init__(
        self,
        tool_profiles: Optional[Dict[str, ToolProfile]] = None,
        route_policies: Optional[Dict[str, RoutePolicy]] = None,
        consult_policies: Optional[Dict[str, ConsultPolicy]] = None,
        binding_store: Any = None,
    ) -> None:
        self._tool_profiles: Dict[str, ToolProfile] = tool_profiles or {}
        self._route_policies: Dict[str, RoutePolicy] = route_policies or {}
        # Built-in panel policy first so a caller catalog can never be
        # required to carry it, and can never weaken it by reusing its id.
        self._consult_policies: Dict[str, ConsultPolicy] = {
            **(consult_policies or {}),
            **BUILTIN_CONSULT_POLICIES,
        }
        self._binding_store = binding_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        persona: Any,
        *,
        capital_pool_id: Optional[str] = None,
        target_scope: Optional[str] = None,
    ) -> PolicyResolutionResult:
        """
        Resolve effective capabilities for the given persona.

        Parameters
        ----------
        persona         : Persona object (must have persona_id, status,
                          tool_profile_id, route_policy_id, consult_policy_id).
        capital_pool_id : When provided together with target_scope, the binding
                          store is consulted to verify capital eligibility.
        target_scope    : Deployment scope for capital eligibility
                          (paper/canary/live).  Ignored when capital_pool_id
                          is absent.

        Returns
        -------
        PolicyResolutionResult
        """
        denial_reasons: List[str] = []
        effective_tools: List[str] = []
        effective_skills: List[str] = []
        effective_workflows: List[str] = []
        source_refs: List[str] = []
        consultable = False

        persona_id: str = str(getattr(persona, "persona_id", "") or "")

        # 1. Suspended persona — fail completely closed; no further checks.
        admin_status = str(getattr(persona, "status", "") or "").lower()
        if admin_status == "suspended":
            return PolicyResolutionResult(
                denial_reasons=[
                    f"persona {persona_id!r} is suspended; all capability access denied"
                ],
            )

        # 2. Resolve tool profile.
        tool_profile_id: Optional[str] = getattr(persona, "tool_profile_id", None)
        if tool_profile_id:
            profile = self._tool_profiles.get(tool_profile_id)
            if profile is None:
                denial_reasons.append(
                    f"tool_profile_id {tool_profile_id!r} is unknown; tool access denied (fail closed)"
                )
            else:
                effective_tools = list(profile.allowed_tools)
                source_refs.append(f"tool_profile:{tool_profile_id}")

        # 3. Resolve route policy.
        route_policy_id: Optional[str] = getattr(persona, "route_policy_id", None)
        if route_policy_id:
            route_policy = self._route_policies.get(route_policy_id)
            if route_policy is None:
                denial_reasons.append(
                    f"route_policy_id {route_policy_id!r} is unknown; workflow/route access denied (fail closed)"
                )
            else:
                effective_workflows = list(route_policy.allowed_workflows)
                for skill in route_policy.allowed_skills:
                    if skill not in effective_skills:
                        effective_skills.append(skill)
                source_refs.append(f"route_policy:{route_policy_id}")

        # 4. Resolve consult policy.
        consult_policy_id: Optional[str] = getattr(persona, "consult_policy_id", None)
        if consult_policy_id:
            consult_policy = self._consult_policies.get(consult_policy_id)
            if consult_policy is None:
                denial_reasons.append(
                    f"consult_policy_id {consult_policy_id!r} is unknown; consult skill access denied (fail closed)"
                )
            else:
                for skill in consult_policy.allowed_skills:
                    if skill not in effective_skills:
                        effective_skills.append(skill)
                source_refs.append(f"consult_policy:{consult_policy_id}")
                consultable = bool(consult_policy.consultable)

        # 5. Capital eligibility — only when both capital_pool_id and target_scope
        #    are provided and a binding_store is configured.
        if capital_pool_id and target_scope and self._binding_store is not None:
            may_deploy: bool = self._binding_store.persona_may_deploy_to(
                persona_id=persona_id,
                capital_pool_id=capital_pool_id,
                target_scope=target_scope,
            )
            if not may_deploy:
                denial_reasons.append(
                    f"capital eligibility denied: persona {persona_id!r} has no active "
                    f"binding for pool {capital_pool_id!r} at scope {target_scope!r}"
                )
            else:
                source_refs.append(f"capital_binding:{capital_pool_id}:{target_scope}")

        return PolicyResolutionResult(
            effective_tools=effective_tools,
            effective_skills=effective_skills,
            effective_workflows=effective_workflows,
            source_refs=source_refs,
            denial_reasons=denial_reasons,
            consultable=consultable,
        )

    def resolve_to_snapshot(
        self,
        persona: Any,
        snapshot_id: str,
        generated_at: str,
        *,
        capital_pool_id: Optional[str] = None,
        target_scope: Optional[str] = None,
    ) -> Any:
        """
        Resolve and produce a ``CapabilitySnapshot`` in one call.

        Imports ``CapabilitySnapshot`` from ``persona_registry`` at call time to
        avoid circular imports at module load.
        """
        from persona_registry import CapabilitySnapshot  # local import; avoids circular dep

        result = self.resolve(
            persona,
            capital_pool_id=capital_pool_id,
            target_scope=target_scope,
        )
        return CapabilitySnapshot(
            snapshot_id=snapshot_id,
            persona_id=str(getattr(persona, "persona_id", "") or ""),
            generated_at=generated_at,
            effective_tools=result.effective_tools,
            effective_skills=result.effective_skills,
            effective_workflows=result.effective_workflows,
            restrictions=[],
            source_refs=result.source_refs,
            denial_reasons=result.denial_reasons,
        )


def evaluate_participant_action_capability(
    persona: Mapping[str, Any], snapshot: Mapping[str, Any] | None, *,
    run_purpose: str = "daily_cycle", requirements: Mapping[str, Any] | None = None,
    resolver: PersonaPolicyResolver | None = None, owner_available: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Join owner-issued access with the active harness and the broker's ceiling.

    The caller must read both records from their owners. Inline meeting/BFF
    snapshots are never inputs to this function. A label cannot confer access.
    Referenced policies must resolve; absent references use the owner harness.
    """
    from integrations.openclaw.persona_agent_sync import active_harness_version, PERSONA_AGENT_TOOL_PROFILE

    pid = persona.get("persona_id")
    out = {"persona_id": pid, "status": "owner_unavailable", "executable": False,
           "can_be_assigned": False, "can_receive_persona_action": False,
           "can_execute_in_current_run": False, "can_mutate_trading_state": False,
           "consultation_can_mutate_trading_state": False, "run_purpose": run_purpose,
           "effective_tools": [], "effective_workflows": [], "accessible_data": [],
           "allowed_deliverables": [], "denial_reasons": []}

    def deny(status, reason):
        out.update(status=status, reason=reason, blocked_reason=reason, denial_reasons=[reason])
        return out

    if not owner_available:
        return deny("owner_unavailable", "capability_owner_unavailable")
    if persona.get("status") != "active" or persona.get("lifecycle_state") in {"frozen", "retired"}:
        return deny("suspended", "persona_not_active")
    identity = persona.get("metadata") or {}
    if persona.get("lifecycle_state") not in {"paper_owner", "paper_running"} or (
            identity.get("persona_class") in {"consult_panel", "agora_servant", "agora_advisor"}
            or identity.get("origin") in {"private_servant", "agora_advisor"}
            or identity.get("owner_scope") == "user_private"
            or persona.get("consult_policy_id") == "consult-panel.non-trading"):
        return deny("advisory_only", "persona_not_enrolled_for_daily_execution")
    if not snapshot or snapshot.get("persona_id") != pid:
        return deny("owner_unavailable", "capability_snapshot_missing")
    meta = snapshot.get("metadata") or {}
    if meta.get("canonical_write_authority") != "persona_service":
        return deny("downgraded_access", "unverified_capability_authority")
    if meta.get("access_status") in {"expired", "downgraded", "revoked"}:
        return deny("expired_access" if meta["access_status"] == "expired" else "downgraded_access",
                    "capability_access_" + meta["access_status"])
    if meta.get("expires_at"):
        try:
            expires = datetime.fromisoformat(meta["expires_at"].replace("Z", "+00:00"))
            if expires <= (now or datetime.now(timezone.utc)):
                return deny("expired_access", "capability_expired")
        except (ValueError, TypeError):
            return deny("expired_access", "capability_expiry_invalid")
    version, reason = active_harness_version(persona)
    if not version:
        return deny("no_workflow", reason)
    surface = version["surface"]
    tools = set(snapshot.get("effective_tools") or []) & set(PERSONA_AGENT_TOOL_PROFILE["allow"])
    # 'read' is the established harness permission for brokered decision reads.
    harness_tools = set(surface["guardrails"]["allowed_tools"])
    if "read" not in harness_tools:
        tools &= harness_tools
    workflows = set(snapshot.get("effective_workflows") or [])
    if persona.get("tool_profile_id") or persona.get("route_policy_id"):
        resolved = (resolver or PersonaPolicyResolver()).resolve(SimpleNamespace(**persona))
        if persona.get("tool_profile_id"):
            tools &= set(resolved.effective_tools)
        if persona.get("route_policy_id"):
            workflows &= set(resolved.effective_workflows)
        if resolved.denied:
            return deny("no_workflow", "; ".join(resolved.denial_reasons))
    workflow = surface["workflow"]
    if not workflows.intersection({"persona_daily_cycle", "pantheon.persona.daily-cycle"}) or not {
            "observe", "orient", "decide", "act", "learn"} <= set(workflow["steps"]):
        return deny("no_workflow", "effective_daily_workflow_missing")
    if "persona_proposal_submit" not in tools:
        return deny("downgraded_access", "decision_tool_unavailable")
    restrictions = set(snapshot.get("restrictions") or [])
    if restrictions & {"advisory_only", "no_execution", "read_only"}:
        return deny("advisory_only", "snapshot_advisory_only")
    accessible = set(meta.get("accessible_data") or [])
    required = set((requirements or {}).get("required_data") or [])
    required |= {r["dataset"] for r in persona.get("required_data_sources", []) if r.get("policy_gates")}
    if required - accessible or required & restrictions:
        return deny("restricted_data", "deliverable_data_not_accessible")
    deliverable = (requirements or {}).get("deliverable_kind", "research_note")
    if deliverable != "research_note" or "research_note" not in snapshot.get("allowed_capabilities", []):
        return deny("downgraded_access", "deliverable_not_granted")
    out.update(status="allowed", reason="owner_access_and_daily_workflow_verified",
               blocked_reason=None, effective_tools=sorted(tools), effective_workflows=sorted(workflows),
               accessible_data=sorted(accessible), allowed_deliverables=["research_note"],
               snapshot_id=snapshot.get("snapshot_id"), can_be_assigned=True, can_receive_persona_action=True)
    if run_purpose != "daily_cycle":
        out.update(status="advisory_only", reason="consultation_run_is_advisory", blocked_reason="consultation_run_is_advisory")
        return out
    out.update(executable=True, can_execute_in_current_run=True)
    return out
