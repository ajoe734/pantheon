"""Effective-action resolution for generic domain-command wrappers.

Generic wrapper commands (``RuntimeAction``, ``ReviewAction``, ...) carry the
real target action in ``params.action_id``/``params.actionId`` and their own
catalog entry is intentionally weak (``requires_confirm_token=False`` etc.)
because the wrapper itself covers many different underlying operations of
varying risk. When ``action_id`` names an action that also has its own
dedicated canonical command (for example ``RuntimeAction`` with
``action_id=RestartPaperRuntime``), durable admission must validate against
*that* canonical command's catalog entry -- never the wrapper's -- or a
caller can submit the wrapper to dispatch a high-risk action while only
satisfying the wrapper's weaker preconditions.

This module is the single source of truth for that alias table so admission
(``preconditions.py``, ``service.py``) and the domain adapters
(``runtime_adapter.py``, ``governance_adapter.py``) can never drift apart.

Resolution outcomes (``EffectiveAction.status``):

- ``not_wrapper``: ``command_type`` is not a generic wrapper this module
  understands; callers should validate against ``command_type`` unchanged.
- ``canonical``: ``action_id`` names a distinct canonical command; callers
  must validate against ``effective_command_id`` instead of the wrapper.
- ``generic_ok``: ``action_id`` is a recognized wrapper-only alias with no
  stronger canonical command to bypass; the wrapper's own (weak) entry is
  the correct and only applicable entry.
- ``unknown``: ``action_id`` does not match anything the wrapper's adapter
  actually dispatches; admission must reject fail-closed rather than accept
  a command that can only fail (or be silently misrouted) at execution time.
"""
from __future__ import annotations

from typing import Any, Dict, NamedTuple, Optional


class EffectiveAction(NamedTuple):
    effective_command_id: Optional[str]
    status: str


# --------------------------------------------------------------------------- #
# RuntimeAction: mirrors services/control-plane/bff/command_adapters/
# runtime_adapter.py RuntimeCommandAdapter.execute()'s action_id dispatch.
# --------------------------------------------------------------------------- #

_RUNTIME_ACTION_CANONICAL_ALIASES: Dict[str, str] = {
    "start": "StartRuntime",
    "restartpaperruntime": "RestartPaperRuntime",
    "restarttelemetrybridge": "RestartTelemetryBridge",
    "terminatestalepapermonitoringsession": "TerminateStalePaperMonitoringSession",
    "startpapermonitoringsession": "StartPaperMonitoringSession",
    "probetelemetryingest": "ProbeTelemetryIngest",
    "issuesafemode": "IssueSafeMode",
    "executerollback": "ExecuteRollback",
    "rollback": "ExecuteRollback",
    "hardrollback": "HardRollback",
    "approverollback": "ApproveRollback",
    "rejectrollback": "RejectRollback",
    "activatekillswitch": "ActivateKillSwitch",
    "killswitch": "ActivateKillSwitch",
    "issueriskoff": "IssueRiskOff",
}

# Pause/resume aliases (``pause``, ``pauseruntime``, ``pauseexecution``,
# ``pausepaperruntime``, ``resume``, ``unpause``, ``resumepaperruntime``)
# are deliberately NOT remapped to PauseRuntime/PausePaperRuntime/
# ResumePaperRuntime's own confirm_token/approval catalog entries here.
# RuntimeCommandAdapter._execute_pause already independently re-derives the
# verified RuntimeBinding and enforces the admission-stamped-tenant check
# for every pause/resume shape (fixed under DOMAIN-WRITERS-DURABILITY-
# CORRECTIVE-001's prior round and re-verified here); layering the
# canonical confirm_token/approval gate on top requires the wrapper to also
# accept ResumePaperRuntime's requires_approval=True evidence, which is a
# larger, separately-scoped reconciliation (see evidence.json residual
# note) and must not be rushed into this fail-closed admission gate
# alongside the concretely reproduced repair-action defects.
_RUNTIME_ACTION_GENERIC_ONLY = {
    "resume",
    "unpause",
    "pause",
    "pauseruntime",
    "pauseexecution",
    "pausepaperruntime",
    "resumepaperruntime",
}


def _resolve_runtime_action(params: Dict[str, Any]) -> EffectiveAction:
    action_id = str((params or {}).get("action_id") or (params or {}).get("actionId") or "").strip()
    lowered = action_id.lower()
    if not lowered:
        return EffectiveAction(None, "unknown")
    canonical = _RUNTIME_ACTION_CANONICAL_ALIASES.get(lowered)
    if canonical:
        return EffectiveAction(canonical, "canonical")
    if lowered in _RUNTIME_ACTION_GENERIC_ONLY:
        return EffectiveAction(None, "generic_ok")
    return EffectiveAction(None, "unknown")


# --------------------------------------------------------------------------- #
# ReviewAction: mirrors services/control-plane/bff/command_adapters/
# governance_adapter.py GovernanceCommandAdapter.execute()'s action_id
# dispatch for the subset reachable when command_type == "ReviewAction".
# --------------------------------------------------------------------------- #

_REVIEW_ACTION_CANONICAL_ALIASES: Dict[str, str] = {
    "approve": "ApproveDecision",
    "approvedecision": "ApproveDecision",
    "reject": "RejectDecision",
    "rejectdecision": "RejectDecision",
    "requestrevision": "RequestApprovalRevision",
    "requestapprovalrevision": "RequestApprovalRevision",
    "request-revision": "RequestApprovalRevision",
    "recordsponsordecision": "RecordSponsorDecision",
    "sponsor-decision": "RecordSponsorDecision",
    "humangateapprove": "HumanGateApprove",
    "humangatereject": "HumanGateReject",
    "humangaterequestmoreevidence": "HumanGateRequestMoreEvidence",
    "humangaterevoke": "HumanGateRevoke",
    "humangateextendttl": "HumanGateExtendTtl",
}

# ``requestreview``/``review`` build a review receipt directly (no aliasing
# into a stronger canonical command); nothing to enforce beyond the
# wrapper's own entry.
_REVIEW_ACTION_GENERIC_ONLY = {"requestreview", "review"}


def _resolve_review_action(params: Dict[str, Any]) -> EffectiveAction:
    action_id = str((params or {}).get("action_id") or (params or {}).get("actionId") or "").strip()
    lowered = action_id.lower()
    if not lowered:
        return EffectiveAction(None, "unknown")
    canonical = _REVIEW_ACTION_CANONICAL_ALIASES.get(lowered)
    if canonical:
        return EffectiveAction(canonical, "canonical")
    if lowered in _REVIEW_ACTION_GENERIC_ONLY:
        return EffectiveAction(None, "generic_ok")
    return EffectiveAction(None, "unknown")


_RESOLVERS = {
    "RuntimeAction": _resolve_runtime_action,
    "ReviewAction": _resolve_review_action,
}

GENERIC_WRAPPER_COMMANDS = frozenset(_RESOLVERS)


def resolve_effective_action(command_type: str, params: Optional[Dict[str, Any]]) -> EffectiveAction:
    """Resolve the canonical command that must gate admission for a wrapper.

    ``ExperimentAction`` is intentionally not in ``_RESOLVERS``: its
    action_id vocabulary (cancel/retry/archive/invalidate/promote) never
    aliases into a distinct canonical CommandType, so its own catalog entry
    is already the correct and only applicable entry (audited under
    DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001; see evidence.json).
    """
    resolver = _RESOLVERS.get(str(command_type or "").strip())
    if resolver is None:
        return EffectiveAction(None, "not_wrapper")
    return resolver(params or {})
