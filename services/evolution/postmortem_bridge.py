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

import re
from typing import Any, Dict, List, Mapping, Optional

# Valid proposed actions the bridge can emit.
# Governance maps these to EvolutionActionType when admitting the proposal.
PROPOSED_ACTIONS = frozenset(
    {
        "rollback",
        "retrain",
        "revalidate",
        "redeploy",
        "retire",
        "freeze",
        "flag_for_review",
    }
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
_POSTMORTEM_SOURCE = "postmortem_published_bridge"
_BRIDGE_CREATED_BY_ID = "postmortem-bridge"
_BRIDGE_CREATED_BY_ROLE = "evolution_controller"
_DEFAULT_TARGET_TYPE = "candidate_artifact"
_VALID_FREEZE_STAGES = frozenset({"paper", "canary", "live"})

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


def postmortem_bridge_key(
    postmortem: Mapping[str, Any] | Any,
    incident: Mapping[str, Any] | Any,
    *,
    target_type: str = _DEFAULT_TARGET_TYPE,
) -> str:
    """Return the idempotency key for a published postmortem bridge event."""
    pm = _payload(postmortem, "postmortem")
    inc = _payload(incident, "incident")
    _validate_postmortem_incident_pair(pm, inc, require_published=False)
    cluster = _incident_cluster_key(pm, inc)
    return f"{target_type}:{pm['artifact_id']}:incident_cluster:{cluster}"


def decision_id_for_published_postmortem(
    postmortem: Mapping[str, Any] | Any,
    incident: Mapping[str, Any] | Any,
    *,
    target_type: str = _DEFAULT_TARGET_TYPE,
) -> str:
    """Return the deterministic EvolutionDecision id for a bridge key."""
    key = postmortem_bridge_key(postmortem, incident, target_type=target_type)
    return f"evo-pm-{_safe_id(key)}"


def build_published_postmortem_proposal_request(
    postmortem: Mapping[str, Any] | Any,
    incident: Mapping[str, Any] | Any,
    *,
    decision_id: str | None = None,
    publish_event_id: str | None = None,
    created_by_id: str = _BRIDGE_CREATED_BY_ID,
    created_by_role: str = _BRIDGE_CREATED_BY_ROLE,
) -> Dict[str, Any]:
    """
    Build a ProposeRequest-compatible payload from a published postmortem.

    This is the admission bridge used by the evolution service. Unlike the
    legacy pure event filter above, every published postmortem produces a
    review-gated proposal keyed by target and incident cluster. Duplicate
    publish events therefore resolve to the same deterministic decision id.
    """
    pm = _payload(postmortem, "postmortem")
    inc = _payload(incident, "incident")
    _validate_postmortem_incident_pair(pm, inc, require_published=True)

    target_type = _DEFAULT_TARGET_TYPE
    bridge_key = postmortem_bridge_key(pm, inc, target_type=target_type)
    resolved_decision_id = decision_id or decision_id_for_published_postmortem(
        pm,
        inc,
        target_type=target_type,
    )
    action_type = _canonical_action_type(pm, inc)
    incident_cluster_id = _incident_cluster_key(pm, inc)
    target_stage = str(pm.get("deployment_stage") or inc.get("deployment_stage") or "").strip() or None

    metadata = {
        "task_id": "LOOP-AUTO-EVO-002",
        "source": _POSTMORTEM_SOURCE,
        "trigger_source": "postmortem.published",
        "source_postmortem_id": pm["postmortem_id"],
        "source_incident_id": inc["incident_id"],
        "source_trace_id": pm.get("trace_id") or inc.get("trace_id"),
        "postmortem_bridge_key": bridge_key,
        "idempotency_scope": "target_type+target_id+incident_cluster",
        "incident_cluster_id": incident_cluster_id,
        "runtime_binding_id": pm.get("binding_id") or inc.get("binding_id"),
        "deployment_plan_id": pm.get("deployment_plan_id") or inc.get("deployment_plan_id"),
        "persona_capital_binding_id": pm.get("persona_capital_binding_id")
        or inc.get("persona_capital_binding_id"),
        "runtime_id": pm.get("runtime_id") or inc.get("runtime_id"),
        "deployment_stage_snapshot": target_stage,
        "bridge_legacy_proposed_action": _legacy_proposed_action(pm, inc),
        "proposal_only": True,
        "review_gate_state": "awaiting_review",
        "review_required": True,
        "live_mutation_allowed": False,
        "runtime_binding_mutation_allowed": False,
        "broker_order_allowed": False,
        "capital_binding_mutation_allowed": False,
    }
    if publish_event_id:
        metadata["publish_event_id"] = publish_event_id
    if metadata["bridge_legacy_proposed_action"] == "rollback":
        metadata["rollback_is_followthrough"] = True

    return {
        "decision_id": resolved_decision_id,
        "target_type": target_type,
        "target_id": pm["artifact_id"],
        "target_version": pm["artifact_version"],
        "action_type": action_type,
        "rationale": _published_postmortem_rationale(pm, inc, action_type),
        "created_by_id": created_by_id,
        "created_by_role": created_by_role,
        "target_stage": target_stage,
        "capital_pool_id": pm.get("capital_pool_id") or inc.get("capital_pool_id"),
        "linked_incident_id": inc["incident_id"],
        "linked_postmortem_id": pm["postmortem_id"],
        "evidence_refs": _published_postmortem_evidence_refs(pm, inc),
        "threshold_snapshots": [_published_postmortem_threshold_snapshot(pm, inc)],
        "metadata": metadata,
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


def _payload(value: Mapping[str, Any] | Any, name: str) -> Dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise PostmortemBridgeError(f"{name} must be a mapping")
    return dict(value)


def _validate_postmortem_incident_pair(
    postmortem: Mapping[str, Any],
    incident: Mapping[str, Any],
    *,
    require_published: bool,
) -> None:
    missing_pm = [
        field
        for field in (
            "postmortem_id",
            "incident_id",
            "status",
            "artifact_id",
            "artifact_version",
            "deployment_stage",
        )
        if not postmortem.get(field)
    ]
    missing_incident = [
        field
        for field in ("incident_id", "severity")
        if not incident.get(field)
    ]
    if missing_pm or missing_incident:
        details = []
        if missing_pm:
            details.append(f"postmortem missing {missing_pm}")
        if missing_incident:
            details.append(f"incident missing {missing_incident}")
        raise PostmortemBridgeError("; ".join(details))
    if str(postmortem["incident_id"]) != str(incident["incident_id"]):
        raise PostmortemBridgeError(
            "postmortem incident_id must match incident.incident_id: "
            f"{postmortem['incident_id']!r} != {incident['incident_id']!r}"
        )
    severity = str(incident["severity"])
    if severity not in VALID_SEVERITIES:
        raise PostmortemBridgeError(
            f"Invalid incident severity {severity!r}. Must be one of {sorted(VALID_SEVERITIES)}."
        )
    if require_published and postmortem.get("status") != "published":
        raise PostmortemBridgeError(
            f"postmortem must be published before bridge admission: {postmortem.get('status')!r}"
        )
    if require_published and not postmortem.get("published_at"):
        raise PostmortemBridgeError("published postmortem must carry published_at")


def _incident_cluster_key(
    postmortem: Mapping[str, Any],
    incident: Mapping[str, Any],
) -> str:
    return str(
        postmortem.get("incident_cluster_id")
        or incident.get("incident_cluster_id")
        or incident["incident_id"]
    )


def _safe_id(value: str, *, max_len: int = 96) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._")
    return (safe or "postmortem")[:max_len]


def _has_corrective_action(postmortem: Mapping[str, Any]) -> bool:
    return bool(postmortem.get("corrective_action_required")) or bool(postmortem.get("action_items"))


def _canonical_action_type(
    postmortem: Mapping[str, Any],
    incident: Mapping[str, Any],
) -> str:
    severity = str(incident["severity"])
    stage = str(postmortem.get("deployment_stage") or incident.get("deployment_stage") or "")
    if severity == "critical" and stage in _VALID_FREEZE_STAGES:
        return "freeze"
    if severity == "high":
        return "flag_for_review"
    if _has_corrective_action(postmortem):
        return "retrain"
    return "flag_for_review"


def _legacy_proposed_action(
    postmortem: Mapping[str, Any],
    incident: Mapping[str, Any],
) -> str:
    severity = str(incident["severity"])
    if severity == "critical":
        return "freeze"
    if severity == "high":
        return "rollback"
    if _has_corrective_action(postmortem):
        return "retrain"
    return "flag_for_review"


def _published_postmortem_evidence_refs(
    postmortem: Mapping[str, Any],
    incident: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    _append_ref(
        refs,
        ref_type="manual_review_ticket",
        ref_id=str(postmortem["postmortem_id"]),
        storage_ref={"backend": "incident_store", "object_type": "Postmortem"},
        note="Published Postmortem evidence for derived EvolutionDecision proposal.",
    )
    _append_ref(
        refs,
        ref_type="manual_review_ticket",
        ref_id=str(incident["incident_id"]),
        storage_ref={"backend": "incident_store", "object_type": "IncidentCase"},
        note="Parent IncidentCase evidence for derived EvolutionDecision proposal.",
    )
    for event_id in postmortem.get("telemetry_event_ids") or incident.get("telemetry_event_ids") or []:
        _append_ref(
            refs,
            ref_type="telemetry_summary",
            ref_id=str(event_id),
            storage_ref={"backend": "telemetry", "object_type": "TelemetryEvent"},
            note="Telemetry event cited by the postmortem.",
        )
    for reconciliation_id in postmortem.get("reconciliation_ids") or incident.get("reconciliation_ids") or []:
        _append_ref(
            refs,
            ref_type="drift_report",
            ref_id=str(reconciliation_id),
            storage_ref={"backend": "reconciliation", "object_type": "ReconciliationReport"},
            note="Reconciliation or drift evidence cited by the postmortem.",
        )
    return refs


def _append_ref(
    refs: List[Dict[str, Any]],
    *,
    ref_type: str,
    ref_id: str,
    storage_ref: Dict[str, Any],
    note: str,
) -> None:
    if not ref_id:
        return
    if any(item["ref_type"] == ref_type and item["ref_id"] == ref_id for item in refs):
        return
    refs.append(
        {
            "ref_type": ref_type,
            "ref_id": ref_id,
            "storage_ref": storage_ref,
            "note": note,
        }
    )


def _published_postmortem_threshold_snapshot(
    postmortem: Mapping[str, Any],
    incident: Mapping[str, Any],
) -> Dict[str, Any]:
    severity = str(incident["severity"])
    if severity == "critical":
        metric_name = "severity1_incident_count"
        signal_type = "governance_incident"
        note = "Critical incident postmortem opens the high-risk freeze proposal path."
    elif severity == "high":
        metric_name = "severity2_postmortem_published"
        signal_type = "governance_incident"
        note = "High-severity postmortem requires explicit evolution review."
    else:
        metric_name = "postmortem_published_followup_required"
        signal_type = "manual_review"
        note = "Published postmortem requires an explicit evolution review decision."
    return {
        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
        "signal_type": signal_type,
        "metric_name": metric_name,
        "comparator": "gte",
        "observed_value": 1,
        "threshold_value": 1,
        "window": "postmortem-published",
        "breached": True,
        "note": note,
    }


def _published_postmortem_rationale(
    postmortem: Mapping[str, Any],
    incident: Mapping[str, Any],
    action_type: str,
) -> str:
    cluster = _incident_cluster_key(postmortem, incident)
    return (
        f"Create a review-gated {action_type} proposal from published postmortem "
        f"{postmortem['postmortem_id']} for incident {incident['incident_id']} "
        f"(cluster {cluster}). The proposal is idempotent by target and incident "
        "cluster and does not mutate runtime, broker, or capital-binding state."
    )
