"""
Postmortem → EvolutionDecisionProposal auto-trigger bridge

Subscribes to the postmortem-published event and decides whether to emit an
EvolutionDecisionProposal based on severity and corrective_action_required.

Design constraints
------------------
- Independent module. Does NOT modify POST-001 or EVO-001 public APIs.
- Never writes to governance store. Returns a proposal dict for governance to
  admit via the existing /api/evolution/proposals endpoint.
- Fail-fast on malformed input (missing required fields or invalid severity).

Input shape (postmortem published event payload dict)
-----------------------------------------------------
Required:
  postmortem_id            str  — unique postmortem identifier
  incident_id              str  — parent IncidentCase id
  severity                 str  — "low" | "medium" | "high" | "critical"
                                  (propagated from linked IncidentCase)
  artifact_id              str  — artifact under execution at incident time
  artifact_version         str  — version of that artifact

Optional:
  corrective_action_required  bool  — explicit flag from postmortem content
  deployment_stage            str   — paper | canary | live | frozen
  evidence_refs               list  — upstream evidence refs to forward

Output shape (EvolutionDecisionProposal dict or None)
------------------------------------------------------
  source_postmortem_id    str
  source_incident_id      str
  proposed_action         str  — one of PROPOSED_ACTIONS
  cooldown_window_hours   int
  evidence_refs           list[dict]
  target_artifact_id      str
  target_artifact_version str
  rationale               str
  created_by_id           str  — "postmortem-bridge"
  created_by_role         str  — "evolution_controller"
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Valid proposed actions the bridge can emit.
# Governance maps these to EvolutionActionType when admitting the proposal.
PROPOSED_ACTIONS = frozenset(
    {"rollback", "retrain", "revalidate", "redeploy", "retire", "freeze"}
)

VALID_SEVERITIES = frozenset({"low", "medium", "high", "critical"})

# Severities that trigger a proposal when corrective_action_required is absent
_TRIGGER_SEVERITIES = frozenset({"high", "critical"})

_SEVERITY_ACTION: Dict[str, str] = {
    "high": "rollback",
    "critical": "freeze",
}

_SEVERITY_COOLDOWN_HOURS: Dict[str, int] = {
    "high": 72,
    "critical": 168,
}

_CORRECTIVE_ACTION = "retrain"
_CORRECTIVE_COOLDOWN_HOURS = 24

_REQUIRED_FIELDS = (
    "postmortem_id",
    "incident_id",
    "severity",
    "artifact_id",
    "artifact_version",
)


class PostmortemBridgeError(ValueError):
    """Raised when the postmortem event payload is malformed."""


def on_postmortem_published(
    postmortem: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Evaluate a postmortem-published event and return an EvolutionDecisionProposal
    dict when the event meets the trigger criteria, or None when it does not.

    Trigger rules (evaluated in order; first match wins):
      1. severity == "critical"  → proposed_action = "freeze",
                                   cooldown_window_hours = 168
      2. severity == "high"      → proposed_action = "rollback",
                                   cooldown_window_hours = 72
      3. corrective_action_required == True → proposed_action = "retrain",
                                              cooldown_window_hours = 24
      4. otherwise → return None (no proposal)

    Severity priority overrides corrective_action_required so that high/critical
    incidents always result in the strongest action, regardless of the flag.

    Parameters
    ----------
    postmortem : dict
        Postmortem-published event payload.  Required keys: postmortem_id,
        incident_id, severity, artifact_id, artifact_version.

    Returns
    -------
    dict or None
        EvolutionDecisionProposal payload dict, or None when no proposal is
        warranted.

    Raises
    ------
    PostmortemBridgeError
        If required fields are missing or severity is not a recognised value.
    """
    _validate(postmortem)

    severity: str = postmortem["severity"]
    corrective: bool = bool(postmortem.get("corrective_action_required", False))

    if severity in _TRIGGER_SEVERITIES:
        action = _SEVERITY_ACTION[severity]
        cooldown = _SEVERITY_COOLDOWN_HOURS[severity]
    elif corrective:
        action = _CORRECTIVE_ACTION
        cooldown = _CORRECTIVE_COOLDOWN_HOURS
    else:
        return None

    return _build_proposal(postmortem, action, cooldown)


def build_evolution_learn_feedback_writeback(
    evolution_outcome: Dict[str, Any],
    *,
    sponsor_persona_id: str,
    contributing_persona_ids: List[str],
    summary: Optional[str] = None,
    contributor_feedback: Optional[List[Dict[str, Any]]] = None,
    proposal_ids: Optional[List[str]] = None,
    proposal_ids_by_persona: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Build a memory-service Learn feedback payload from an approved evolution outcome."""
    if not isinstance(evolution_outcome, dict):
        raise PostmortemBridgeError("evolution_outcome must be a dict")
    decision_id = str(
        evolution_outcome.get("evolution_decision_id")
        or evolution_outcome.get("decision_id")
        or evolution_outcome.get("proposal_id")
        or ""
    ).strip()
    if not decision_id:
        raise PostmortemBridgeError("evolution_decision_id is required for Learn feedback writeback")
    feedback_summary = summary or str(evolution_outcome.get("rationale") or "").strip()
    if not feedback_summary:
        raise PostmortemBridgeError("summary or rationale is required for Learn feedback writeback")
    evidence_refs = list(evolution_outcome.get("evidence_refs") or [])
    evidence_refs.insert(0, {"ref_type": "evolution_decision", "ref_id": decision_id})
    if evolution_outcome.get("source_postmortem_id"):
        evidence_refs.append(
            {
                "ref_type": "postmortem",
                "ref_id": evolution_outcome.get("source_postmortem_id"),
            }
        )
    return {
        "source_event_type": "evolution_decision_approved",
        "source_event_id": decision_id,
        "write_authority": "evolution-svc",
        "sponsor_persona_id": sponsor_persona_id,
        "contributing_persona_ids": list(contributing_persona_ids),
        "summary": feedback_summary,
        "headline": f"Evolution Learn feedback for {decision_id}",
        "body": feedback_summary,
        "evidence_refs": evidence_refs,
        "contributor_feedback": list(contributor_feedback or []),
        "proposal_ids": list(proposal_ids or []),
        "proposal_ids_by_persona": dict(proposal_ids_by_persona or {}),
        "tags": [
            "evolution",
            str(evolution_outcome.get("proposed_action") or evolution_outcome.get("action_type") or ""),
        ],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate(postmortem: Any) -> None:
    """Raise PostmortemBridgeError if the payload is malformed."""
    if not isinstance(postmortem, dict):
        raise PostmortemBridgeError(
            f"postmortem must be a dict, got {type(postmortem).__name__!r}"
        )
    missing = [f for f in _REQUIRED_FIELDS if not postmortem.get(f)]
    if missing:
        raise PostmortemBridgeError(
            f"postmortem event payload is missing required fields: {missing}"
        )
    severity = postmortem["severity"]
    if severity not in VALID_SEVERITIES:
        raise PostmortemBridgeError(
            f"Invalid severity {severity!r}. Must be one of {sorted(VALID_SEVERITIES)}."
        )


def _build_proposal(
    postmortem: Dict[str, Any],
    action: str,
    cooldown_hours: int,
) -> Dict[str, Any]:
    upstream_refs: List[Dict[str, Any]] = list(postmortem.get("evidence_refs") or [])
    bridge_ref: Dict[str, Any] = {
        "ref_type": "postmortem",
        "ref_id": postmortem["postmortem_id"],
        "note": f"auto-triggered by postmortem-bridge; severity={postmortem['severity']}",
    }
    incident_ref: Dict[str, Any] = {
        "ref_type": "incident",
        "ref_id": postmortem["incident_id"],
    }
    evidence_refs = [bridge_ref, incident_ref] + upstream_refs

    rationale = (
        f"Postmortem {postmortem['postmortem_id']} published with "
        f"severity={postmortem['severity']!r}"
    )
    if postmortem.get("corrective_action_required"):
        rationale += " and corrective_action_required=true"
    rationale += f". Auto-proposed action: {action!r}."

    return {
        "source_postmortem_id": postmortem["postmortem_id"],
        "source_incident_id": postmortem["incident_id"],
        "proposed_action": action,
        "cooldown_window_hours": cooldown_hours,
        "evidence_refs": evidence_refs,
        "target_artifact_id": postmortem["artifact_id"],
        "target_artifact_version": postmortem["artifact_version"],
        "target_deployment_stage": postmortem.get("deployment_stage"),
        "rationale": rationale,
        "created_by_id": "postmortem-bridge",
        "created_by_role": "evolution_controller",
    }
