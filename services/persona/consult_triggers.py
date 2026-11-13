"""Consult policy trigger engine, risk veto gate, and panel persona bootstrap.

Design: SD_PERSONA_CYCLE_RECONNECT §6.4 / D6 / D7 / D8.

Three pure, dependency-free pieces:

* ``required_consultation`` – given a gated decision, returns the consultation
  the v1 policy demands (risk persona for trial admission, envelope publication
  and every harness patch; plus the red team persona when a patch touches
  guardrails or the consult policy itself).
* ``evaluate_gate`` – applies the risk veto. An oppose (or a missing/ambiguous
  risk answer) never auto-adopts the decision, in paper either; it becomes an
  operator decision. Consultation stays advisory, so the best outcome is
  ``adopt_eligible`` – never a grant of authority.
* ``bootstrap_consult_personas`` – idempotently provisions the two non-trading
  panel personas through the normal ``POST /api/personas`` create path.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

POLICY_ID = "consult-policy-v1"

# Consult policy the Persona owner records on the panel personas. The
# PersonaPolicyResolver catalog registers it as consultable with no tool or
# route policy, so these personas can sit on a panel but hold no trading
# authority.
CONSULT_PANEL_POLICY_ID = "consult-panel.non-trading"

RISK_PERSONA_ID = "persona-risk-officer"
RED_TEAM_PERSONA_ID = "persona-red-team"

DECISION_TRIAL_ADMISSION = "trial_admission"
DECISION_ENVELOPE_PUBLICATION = "envelope_publication"
DECISION_HARNESS_PATCH = "harness_patch"
GATED_DECISION_TYPES = frozenset(
    {DECISION_TRIAL_ADMISSION, DECISION_ENVELOPE_PUBLICATION, DECISION_HARNESS_PATCH}
)

# Patch scopes that additionally pull in the red team persona.
RED_TEAM_SCOPES = frozenset({"guardrails", "consult_policy"})

ROLE_INITIATOR = "primary_reviewer"
ROLE_RISK = "risk_reviewer"
ROLE_RED_TEAM = "red_team"

OUTCOME_ADOPT_ELIGIBLE = "adopt_eligible"
OUTCOME_OPERATOR_DECISION = "operator_decision"

# Recommendations that count as an oppose by the risk persona.
OPPOSE_RECOMMENDATIONS = frozenset({"reject", "freeze", "rollback"})
SUPPORT_RECOMMENDATIONS = frozenset({"approve", "approve_with_conditions"})


@dataclass(frozen=True)
class PanelParticipant:
    persona_id: str
    role: str
    may_veto: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "role": self.role,
            "may_veto": self.may_veto,
        }


@dataclass(frozen=True)
class ConsultRequirement:
    decision_type: str
    decision_id: str
    policy_id: str
    initiator_persona_id: str
    participants: tuple[PanelParticipant, ...]
    reasons: tuple[str, ...] = ()
    touches: tuple[str, ...] = ()
    target_version: str = ""
    candidate_digest: str = ""

    @property
    def risk_persona_id(self) -> str | None:
        for item in self.participants:
            if item.role == ROLE_RISK:
                return item.persona_id
        return None

    def to_panel_metadata(self) -> dict[str, Any]:
        """Shape stored under ``ConsultRequest.metadata['panel']``."""

        return {
            "policy_id": self.policy_id,
            "gated_decision": {
                "type": self.decision_type,
                "id": self.decision_id,
                **(
                    {"target_version": self.target_version}
                    if self.target_version
                    else {}
                ),
                **(
                    {"candidate_digest": self.candidate_digest}
                    if self.candidate_digest
                    else {}
                ),
            },
            "initiator_persona_id": self.initiator_persona_id,
            "participants": [item.to_dict() for item in self.participants],
            "reasons": list(self.reasons),
            "touches": list(self.touches),
        }


def required_consultation(
    decision_type: str,
    decision_id: str,
    *,
    initiator_persona_id: str,
    touches: Iterable[str] = (),
    risk_persona_id: str = RISK_PERSONA_ID,
    red_team_persona_id: str = RED_TEAM_PERSONA_ID,
    target_version: str = "",
    candidate_digest: str = "",
) -> ConsultRequirement | None:
    """Return the consultation a decision requires, or ``None`` if ungated."""

    if decision_type not in GATED_DECISION_TYPES:
        return None
    decision_id = str(decision_id or "").strip()
    initiator = str(initiator_persona_id or "").strip()
    if not decision_id or not initiator:
        raise ValueError("decision_id and initiator_persona_id are required")

    reasons = [f"{decision_type} requires the risk persona (v1 policy)"]
    participants = [
        PanelParticipant(initiator, ROLE_INITIATOR),
        PanelParticipant(risk_persona_id, ROLE_RISK, may_veto=True),
    ]
    scopes = {str(item).strip() for item in touches}
    hit = sorted(scopes & RED_TEAM_SCOPES)
    if decision_type == DECISION_HARNESS_PATCH and hit:
        participants.append(PanelParticipant(red_team_persona_id, ROLE_RED_TEAM))
        reasons.append(
            "harness patch touches " + ", ".join(hit) + "; red team required"
        )
    if decision_type == DECISION_TRIAL_ADMISSION and "paper_canary_candidacy" in scopes:
        participants.append(PanelParticipant(red_team_persona_id, ROLE_RED_TEAM))
        reasons.append("paper canary candidacy requires the red team persona")
    index: dict[str, int] = {}
    unique: list[PanelParticipant] = []
    for item in participants:
        if item.persona_id in index:
            # Never lose risk privileges when the initiator is also a
            # required reviewer: merge the veto onto the kept entry.
            pos = index[item.persona_id]
            kept = unique[pos]
            if item.may_veto and not kept.may_veto:
                unique[pos] = PanelParticipant(
                    kept.persona_id, ROLE_RISK, may_veto=True
                )
            continue
        index[item.persona_id] = len(unique)
        unique.append(item)
    return ConsultRequirement(
        decision_type=decision_type,
        decision_id=decision_id,
        policy_id=POLICY_ID,
        initiator_persona_id=initiator,
        participants=tuple(unique),
        reasons=tuple(reasons),
        touches=tuple(sorted(scopes)),
        target_version=str(target_version or ""),
        candidate_digest=str(candidate_digest or ""),
    )


@dataclass(frozen=True)
class GateOutcome:
    outcome: str
    reasons: tuple[str, ...]
    vetoed_by: str | None = None
    agreements: tuple[str, ...] = field(default_factory=tuple)
    disagreements: tuple[str, ...] = field(default_factory=tuple)

    @property
    def operator_decision_required(self) -> bool:
        return self.outcome == OUTCOME_OPERATOR_DECISION

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "operator_decision_required": self.operator_decision_required,
            "vetoed_by": self.vetoed_by,
            "reasons": list(self.reasons),
            "agreements": list(self.agreements),
            "disagreements": list(self.disagreements),
        }


def evaluate_gate(
    participants: Sequence[Mapping[str, Any]],
    recommendations: Mapping[str, str],
) -> GateOutcome:
    """Apply the risk veto to per-persona recommendations (no voting).

    ``participants`` is the ``panel.participants`` list; ``recommendations``
    maps persona_id to that persona's own recommendation. Fail closed: a
    required participant with no answer, or a risk answer that is not an
    explicit support, yields an operator decision.
    """

    reasons: list[str] = []
    vetoed_by: str | None = None
    for item in participants:
        persona_id = str(item.get("persona_id") or "")
        if persona_id not in recommendations:
            reasons.append(f"{persona_id}: no independent answer")
            continue
        if not item.get("may_veto"):
            continue
        rec = str(recommendations[persona_id])
        if rec in OPPOSE_RECOMMENDATIONS:
            vetoed_by = persona_id
            reasons.append(f"{persona_id}: risk veto ({rec})")
        elif rec not in SUPPORT_RECOMMENDATIONS:
            reasons.append(f"{persona_id}: risk did not support ({rec})")
    groups: dict[str, list[str]] = {}
    for persona_id, rec in recommendations.items():
        groups.setdefault("support" if rec in SUPPORT_RECOMMENDATIONS else rec, []).append(
            persona_id
        )
    agreements = tuple(
        f"{kind}: {', '.join(sorted(ids))}"
        for kind, ids in sorted(groups.items())
        if len(ids) > 1
    )
    disagreements = tuple(
        f"{kind}: {', '.join(sorted(ids))}" for kind, ids in sorted(groups.items())
    ) if len(groups) > 1 else ()
    if reasons:
        return GateOutcome(
            OUTCOME_OPERATOR_DECISION,
            tuple(reasons),
            vetoed_by,
            agreements,
            disagreements,
        )
    return GateOutcome(
        OUTCOME_ADOPT_ELIGIBLE,
        ("risk persona supports; consultation remains advisory",),
        None,
        agreements,
        disagreements,
    )


def trial_admission_decision_id(
    tenant_id: str, persona_id: str, artifact_ref: str, checksum: str, pool_version: int | str
) -> str:
    """Stable id binding the candidate artifact_ref, its checksum and the pool version."""

    raw = json.dumps(
        [tenant_id, persona_id, artifact_ref, checksum, str(pool_version)], separators=(",", ":")
    )
    return "trial-admission-" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def candidate_digest(artifact_ref: str, checksum: str) -> str:
    return hashlib.sha256(f"{artifact_ref}|{checksum}".encode()).hexdigest()


def pool_membership_signature(members: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    """What a trial-admission consultation is about: the live members, not their daily weights.

    The daily risk reweight appends a pool version every cycle, so a consultation bound to
    version V stays valid for a later version only while this signature is unchanged.
    """

    return tuple(
        sorted(
            (str(m["artifact_ref"]), str(m["checksum"]), str(m["stage"]))
            for m in members
            if m.get("stage") != "removed"
        )
    )


def trial_admission_requirement(
    tenant_id: str,
    persona_id: str,
    artifact_ref: str,
    checksum: str,
    pool_version: int | str,
    *,
    risk_persona_id: str = RISK_PERSONA_ID,
) -> ConsultRequirement:
    """The consultation a persona-initiated paper trial admission requires (never ``None``)."""

    required = required_consultation(
        DECISION_TRIAL_ADMISSION,
        trial_admission_decision_id(tenant_id, persona_id, artifact_ref, checksum, pool_version),
        initiator_persona_id=persona_id,
        risk_persona_id=risk_persona_id,
        target_version=str(pool_version),
        candidate_digest=candidate_digest(artifact_ref, checksum),
    )
    assert required is not None
    return required


# Verification reasons that mean "a human must decide", not "not verified".
OPERATOR_REASONS = frozenset({"risk_persona_requires_operator", "panel_conditions_require_operator"})


@dataclass(frozen=True)
class MemoCheck:
    ok: bool
    reason: str = "verified"

    @property
    def operator_decision_required(self) -> bool:
        return self.reason in OPERATOR_REASONS


def verify_published_memo(
    client: Any, memo_id: str, *, tenant_id: str, persona_id: str, requirement: ConsultRequirement
) -> MemoCheck:
    """Shared published-memo verification (harness adoption and pool admission).

    The memo and its request must be published and bound to the exact gated
    decision; the risk gate is recomputed from each participant's own
    transcript contribution (not the synthesis' claimed gate) and must be
    ``adopt_eligible``, with every participant and the memo plainly approving.
    """

    memo = client.get_memo(memo_id)
    if not memo or memo.get("status") != "published":
        return MemoCheck(False, "memo_not_published")
    request = client.get_request(memo["request_id"])
    if (
        not request
        or request.get("tenant_id") != tenant_id
        or request.get("status") != "published"
        or request.get("from_persona_id") != persona_id
        or request.get("target_type") != requirement.decision_type
        or request.get("target_id") != requirement.decision_id
        or memo.get("target_type") != requirement.decision_type
        or memo.get("target_id") != requirement.decision_id
    ):
        return MemoCheck(False, "request_binding_mismatch")
    panel = request.get("metadata", {}).get("panel", {})
    expected = requirement.to_panel_metadata()
    if (
        panel.get("gated_decision") != expected["gated_decision"]
        or panel.get("initiator_persona_id") != persona_id
    ):
        return MemoCheck(False, "panel_binding_mismatch")
    recommendations: dict[str, Any] = {}
    for transcript in client.list_transcripts():
        if transcript.get("request_id") != request["request_id"]:
            continue
        for event in transcript.get("events", []):
            actor = event.get("actor", {})
            if (
                actor.get("actor_type") == "persona"
                and event.get("evidence_refs")
                and event.get("event_type") == "persona_contribution"
            ):
                recommendations[actor.get("actor_id")] = event.get("content", {}).get(
                    "recommendation"
                )
    gate = evaluate_gate([p.to_dict() for p in requirement.participants], recommendations)
    if gate.operator_decision_required:
        return MemoCheck(False, "risk_persona_requires_operator")
    # Conditional approvals need their conditions resolved by governance.
    if any(recommendations.get(p.persona_id) != "approve" for p in requirement.participants):
        return MemoCheck(False, "panel_conditions_require_operator")
    if memo.get("recommendation") != "approve":
        return MemoCheck(False, "panel_conditions_require_operator")
    return MemoCheck(True)


# ---------------------------------------------------------------------------
# Panel persona bootstrap (normal persona create path)
# ---------------------------------------------------------------------------

PANEL_PERSONA_SPECS: tuple[dict[str, str], ...] = (
    {
        "persona_id": RISK_PERSONA_ID,
        "name": "Risk Officer",
        "panel_role": ROLE_RISK,
        "mandate": (
            "Independent risk reviewer. Never trades. Reviews trial admission, "
            "envelope publication and every harness patch; may veto."
        ),
    },
    {
        "persona_id": RED_TEAM_PERSONA_ID,
        "name": "Red Team",
        "panel_role": ROLE_RED_TEAM,
        "mandate": (
            "Adversarial reviewer. Never trades. Attacks patches that touch "
            "guardrails or the consult policy."
        ),
    },
)

HttpFn = Callable[[str, str, "dict[str, Any] | None"], Any]


def _default_http(token: str, timeout: float) -> HttpFn:
    def call(method: str, url: str, body: dict[str, Any] | None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return {"_conflict": True}
            raise
        return json.loads(raw) if raw else {}

    return call


def bootstrap_consult_personas(
    *,
    persona_url: str,
    token: str = "",
    actor_id: str | None = None,
    tenant_id: str | None = None,
    http: HttpFn | None = None,
    timeout_seconds: float = 15.0,
) -> dict[str, str]:
    """Create the risk and red team personas when missing; idempotent.

    Returns ``{persona_id: 'created' | 'exists' | 'repaired'}``. Both personas are created
    through ``POST /api/personas`` (the Persona owner's normal create path) in
    ``draft`` with ``metadata.trading=false`` / ``consultable=true``; advancing
    their lifecycle stays a governed step.
    """

    # The Persona owner binds the mutation actor to the authenticated service
    # actor, so the default must be that actor, not a free-form label.
    actor_id = actor_id or os.getenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID") or "operator-bff"
    call = http or _default_http(token, timeout_seconds)
    base = persona_url.rstrip("/")
    listing = call("GET", f"{base}/api/personas", None)
    if not isinstance(listing, list):
        raise RuntimeError("persona list response must be an array")
    existing = {
        str(item.get("persona_id")): item
        for item in listing
        if isinstance(item, dict)
    }
    result: dict[str, str] = {}
    for spec in PANEL_PERSONA_SPECS:
        persona_id = spec["persona_id"]
        if persona_id in existing:
            if existing[persona_id].get("consult_policy_id") == CONSULT_PANEL_POLICY_ID:
                result[persona_id] = "exists"
            else:
                # Complete a previously half-provisioned persona through the
                # owner's normal patch path; nothing else is touched.
                call(
                    "PATCH",
                    f"{base}/api/personas/{persona_id}",
                    {
                        "actor_id": actor_id,
                        "consult_policy_id": CONSULT_PANEL_POLICY_ID,
                    },
                )
                result[persona_id] = "repaired"
            continue
        body = {
            "actor_id": actor_id,
            "persona_id": persona_id,
            "name": spec["name"],
            "mandate": spec["mandate"],
            "lifecycle_state": "draft",
            "owner": actor_id,
            "tenant_id": tenant_id,
            "consult_policy_id": CONSULT_PANEL_POLICY_ID,
            "metadata": {
                "persona_class": "consult_panel",
                "panel_role": spec["panel_role"],
                "trading": False,
                "consultable": True,
            },
        }
        created = call("POST", f"{base}/api/personas", body)
        if isinstance(created, dict) and created.get("_conflict"):
            result[persona_id] = "exists"
        else:
            result[persona_id] = "created"
    return result
