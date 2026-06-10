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
        self._consult_policies: Dict[str, ConsultPolicy] = consult_policies or {}
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
