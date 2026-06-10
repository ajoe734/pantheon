"""
Incident Domain Backbone Objects — INC-001

IncidentCase and Postmortem are the canonical incident/postmortem objects for
Pantheon.  They attach to the runtime binding, deployment stage, and lineage
evidence so that every incident has a traceable chain back to the governed
artifact and capital pool that produced it.

Lineage edges (formal, normalized per LIN-001 read-model contract)
------------------------------------------------------------------
incident_case.runtime_binding  : IncidentCase.binding_id → RuntimeBinding
postmortem.incident_case       : Postmortem.incident_id  → IncidentCase
evolution_decision.postmortem  : EvolutionDecision.linked_postmortem_id → Postmortem

Write authority
---------------
Only the Incident domain writes IncidentCase and Postmortem records.

Evidence attachment
-------------------
Every IncidentCase MUST carry the binding/stage/lineage evidence fields that
allow forensic reconstruction without querying a live RuntimeBinding.  These
fields are denormalized copies of the canonical RuntimeBinding state at the
time of incident creation — the formal edge (binding_id) remains the truth
anchor; the denormalized fields are read acceleration.

Schema
------
Canonical JSON schemas live at:
    services/incident/incident_case.schema.json
    services/incident/postmortem.schema.json
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class IncidentSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IncidentStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class PostmortemStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    PUBLISHED = "published"


# ---------------------------------------------------------------------------
# IncidentCase
# ---------------------------------------------------------------------------

class IncidentError(ValueError):
    """Raised when IncidentCase or Postmortem validation fails."""


@dataclass(frozen=True)
class IncidentCase:
    """
    Canonical incident record.

    An IncidentCase is opened when an anomaly, governance breach, or operator
    trigger requires formal tracking.  It MUST carry the runtime binding
    identity and the deployment evidence (stage, plan, pool, artifact) so that
    the incident is fully traceable through the lineage chain.

    Lineage edge
    ------------
    incident_case.runtime_binding : binding_id → RuntimeBinding (required)

    Evidence fields (denormalized from RuntimeBinding at incident creation)
    -----------------------------------------------------------------------
    deployment_stage              : paper | canary | live | frozen
    deployment_plan_id            : plan that was active when incident occurred
    capital_pool_id               : capital pool under management
    persona_capital_binding_id    : governance admissibility proof
    artifact_id                   : the artifact under execution
    artifact_version              : specific artifact version
    runtime_id                    : LEAN runtime / container / worker process

    Traceability
    ------------
    trace_id is required (L1 traceability rules, §9 of LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md)
    """

    # Identity
    incident_id: str
    title: str
    status: str
    severity: str
    created_at: str

    # Formal lineage edge — incident_case.runtime_binding
    binding_id: str

    # Deployment evidence (denormalized from RuntimeBinding; see docstring)
    deployment_stage: str
    deployment_plan_id: str
    capital_pool_id: str
    persona_capital_binding_id: str
    artifact_id: str
    artifact_version: str
    runtime_id: str

    # Traceability (required, §9)
    trace_id: str

    # Optional evidence fields
    resolved_at: Optional[str] = None
    telemetry_event_ids: List[str] = field(default_factory=list)
    evidence_summary: Optional[str] = None
    lineage_ref: Optional[str] = None          # composite ref, e.g. "{artifact_id}@{artifact_version}"

    def __post_init__(self) -> None:
        try:
            IncidentSeverity(self.severity)
        except ValueError:
            raise IncidentError(
                f"Invalid severity: {self.severity!r}. "
                f"Must be one of {[e.value for e in IncidentSeverity]}."
            )
        try:
            IncidentStatus(self.status)
        except ValueError:
            raise IncidentError(
                f"Invalid status: {self.status!r}. "
                f"Must be one of {[e.value for e in IncidentStatus]}."
            )
        _VALID_STAGES = {"paper", "canary", "live", "frozen"}
        if self.deployment_stage not in _VALID_STAGES:
            raise IncidentError(
                f"Invalid deployment_stage: {self.deployment_stage!r}. "
                f"Must be one of {sorted(_VALID_STAGES)}."
            )

    def is_open(self) -> bool:
        return self.status in {IncidentStatus.OPEN.value, IncidentStatus.INVESTIGATING.value}

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != [] and v != {}}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IncidentCase":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, payload: str) -> "IncidentCase":
        return cls.from_dict(json.loads(payload))


def validate_incident_case(inc: IncidentCase) -> List[str]:
    """Return a list of semantic validation errors. Empty list = valid."""
    errors: List[str] = []

    if not inc.incident_id:
        errors.append("incident_id must not be empty")
    if not inc.title:
        errors.append("title must not be empty")

    # Formal lineage edge
    if not inc.binding_id:
        errors.append("binding_id must not be empty — a RuntimeBinding reference is required")

    # Deployment evidence
    if not inc.deployment_stage:
        errors.append("deployment_stage must not be empty")
    if not inc.deployment_plan_id:
        errors.append("deployment_plan_id must not be empty")
    if not inc.capital_pool_id:
        errors.append("capital_pool_id must not be empty")
    if not inc.persona_capital_binding_id:
        errors.append(
            "persona_capital_binding_id must not be empty — "
            "PersonaCapitalBinding reference is required for governance traceability"
        )
    if not inc.artifact_id:
        errors.append("artifact_id must not be empty")
    if not inc.artifact_version:
        errors.append("artifact_version must not be empty")
    if not inc.runtime_id:
        errors.append("runtime_id must not be empty")

    # Traceability
    if not inc.trace_id:
        errors.append("trace_id must not be empty — required by L1 traceability rules")

    if inc.status in {IncidentStatus.RESOLVED.value, IncidentStatus.CLOSED.value}:
        if not inc.resolved_at:
            errors.append(f"resolved_at is required when status is {inc.status!r}")

    return errors


# ---------------------------------------------------------------------------
# Postmortem
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Postmortem:
    """
    Canonical postmortem record.

    A Postmortem is attached to an IncidentCase and documents the root cause,
    timeline, contributing factors, and action items.  It propagates the key
    binding and lineage evidence from its parent incident for direct querying
    without a cross-object join.

    Lineage edge
    ------------
    postmortem.incident_case : incident_id → IncidentCase (required)

    Evidence fields (propagated from IncidentCase)
    -----------------------------------------------
    binding_id                : carries the same RuntimeBinding ref as the incident
    deployment_stage          : stage at time of incident
    deployment_plan_id        : deployment plan active at incident time
    capital_pool_id           : capital pool under management
    persona_capital_binding_id: governance admissibility proof
    artifact_id               : artifact under execution
    artifact_version          : specific artifact version
    runtime_id                : runtime that was active when the incident occurred
    trace_id                  : traceability through the event chain

    Evolution link
    --------------
    linked_evolution_decision_id is set after EVO-003 creates an EvolutionDecision
    that references this postmortem (evolution_decision.postmortem edge).
    """

    # Identity
    postmortem_id: str
    title: str
    status: str
    created_at: str

    # Formal lineage edge — postmortem.incident_case
    incident_id: str

    # Evidence fields propagated from IncidentCase (denormalized for direct query)
    binding_id: str
    deployment_stage: str
    deployment_plan_id: str
    capital_pool_id: str
    persona_capital_binding_id: str
    artifact_id: str
    artifact_version: str
    runtime_id: str
    trace_id: str

    # Postmortem content
    root_cause: str
    contributing_factors: List[str] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    action_items: List[str] = field(default_factory=list)
    author_ids: List[str] = field(default_factory=list)

    # Optional
    published_at: Optional[str] = None
    linked_evolution_decision_id: Optional[str] = None  # set after EVO-003

    def __post_init__(self) -> None:
        try:
            PostmortemStatus(self.status)
        except ValueError:
            raise IncidentError(
                f"Invalid postmortem status: {self.status!r}. "
                f"Must be one of {[e.value for e in PostmortemStatus]}."
            )

    def is_published(self) -> bool:
        return self.status == PostmortemStatus.PUBLISHED.value

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != [] and v != {}}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Postmortem":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, payload: str) -> "Postmortem":
        return cls.from_dict(json.loads(payload))


def validate_postmortem(pm: Postmortem) -> List[str]:
    """Return a list of semantic validation errors. Empty list = valid."""
    errors: List[str] = []

    if not pm.postmortem_id:
        errors.append("postmortem_id must not be empty")
    if not pm.title:
        errors.append("title must not be empty")
    if not pm.root_cause:
        errors.append("root_cause must not be empty")

    # Formal lineage edge
    if not pm.incident_id:
        errors.append("incident_id must not be empty — an IncidentCase reference is required")

    # Propagated evidence fields
    if not pm.binding_id:
        errors.append("binding_id must not be empty")
    if not pm.deployment_stage:
        errors.append("deployment_stage must not be empty")
    if not pm.deployment_plan_id:
        errors.append("deployment_plan_id must not be empty")
    if not pm.capital_pool_id:
        errors.append("capital_pool_id must not be empty")
    if not pm.persona_capital_binding_id:
        errors.append(
            "persona_capital_binding_id must not be empty — "
            "PersonaCapitalBinding reference is required for governance traceability"
        )
    if not pm.artifact_id:
        errors.append("artifact_id must not be empty")
    if not pm.artifact_version:
        errors.append("artifact_version must not be empty")
    if not pm.runtime_id:
        errors.append("runtime_id must not be empty")
    if not pm.trace_id:
        errors.append("trace_id must not be empty")

    if pm.status == PostmortemStatus.PUBLISHED.value and not pm.published_at:
        errors.append("published_at is required when postmortem status is 'published'")

    return errors


def build_postmortem_learn_feedback_writeback(
    postmortem: Postmortem | Dict[str, Any],
    *,
    sponsor_persona_id: str,
    contributing_persona_ids: List[str],
    summary: Optional[str] = None,
    contributor_feedback: Optional[List[Dict[str, Any]]] = None,
    proposal_ids: Optional[List[str]] = None,
    proposal_ids_by_persona: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Build a memory-service Learn feedback payload from a published postmortem."""
    payload = postmortem.to_dict() if isinstance(postmortem, Postmortem) else dict(postmortem)
    postmortem_id = str(payload.get("postmortem_id") or "").strip()
    if not postmortem_id:
        raise IncidentError("postmortem_id is required for Learn feedback writeback")
    feedback_summary = summary or str(payload.get("root_cause") or payload.get("title") or "").strip()
    if not feedback_summary:
        raise IncidentError("summary or postmortem root_cause is required for Learn feedback writeback")
    evidence_refs = [
        {"ref_type": "postmortem", "ref_id": postmortem_id},
        {"ref_type": "incident", "ref_id": payload.get("incident_id")},
        {"ref_type": "runtime_binding", "ref_id": payload.get("binding_id")},
        {"ref_type": "trace", "ref_id": payload.get("trace_id")},
    ]
    evidence_refs = [ref for ref in evidence_refs if ref.get("ref_id")]
    return {
        "source_event_type": "postmortem_published",
        "source_event_id": postmortem_id,
        "write_authority": "incident-svc",
        "sponsor_persona_id": sponsor_persona_id,
        "contributing_persona_ids": list(contributing_persona_ids),
        "summary": feedback_summary,
        "headline": f"Postmortem Learn feedback for {postmortem_id}",
        "body": feedback_summary,
        "evidence_refs": evidence_refs,
        "contributor_feedback": list(contributor_feedback or []),
        "proposal_ids": list(proposal_ids or []),
        "proposal_ids_by_persona": dict(proposal_ids_by_persona or {}),
        "tags": ["postmortem", str(payload.get("deployment_stage") or "")],
    }


# ---------------------------------------------------------------------------
# IncidentStore — in-memory write-guarded store
# ---------------------------------------------------------------------------

class IncidentStore:
    """
    Write-guarded store for IncidentCase and Postmortem records.

    Write authority: Incident domain only.

    All writes are validated before storage.  The store supports optional
    persistence to a JSON file for single-process use.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._incidents: Dict[str, IncidentCase] = {}
        self._postmortems: Dict[str, Postmortem] = {}
        self._path = path
        self._loaded_mtime_ns: Optional[int] = None
        if path and path.exists():
            self._load(path)
            self._loaded_mtime_ns = path.stat().st_mtime_ns

    def _refresh_from_disk(self) -> None:
        if not self._path or not self._path.exists():
            return
        mtime_ns = self._path.stat().st_mtime_ns
        if self._loaded_mtime_ns == mtime_ns:
            return
        self._load(self._path)
        self._loaded_mtime_ns = mtime_ns

    # ---- IncidentCase reads ----

    def get_incident(self, incident_id: str) -> Optional[IncidentCase]:
        self._refresh_from_disk()
        return self._incidents.get(incident_id)

    def require_incident(self, incident_id: str) -> IncidentCase:
        inc = self.get_incident(incident_id)
        if inc is None:
            raise IncidentError(f"IncidentCase not found: {incident_id}")
        return inc

    def list_incidents(self) -> List[IncidentCase]:
        self._refresh_from_disk()
        return list(self._incidents.values())

    def find_incidents_by_binding(self, binding_id: str) -> List[IncidentCase]:
        self._refresh_from_disk()
        return [i for i in self._incidents.values() if i.binding_id == binding_id]

    def find_incidents_by_pool(self, capital_pool_id: str) -> List[IncidentCase]:
        self._refresh_from_disk()
        return [i for i in self._incidents.values() if i.capital_pool_id == capital_pool_id]

    def find_open_incidents(self) -> List[IncidentCase]:
        self._refresh_from_disk()
        return [i for i in self._incidents.values() if i.is_open()]

    # ---- IncidentCase writes ----

    def create_incident(self, inc: IncidentCase) -> IncidentCase:
        self._refresh_from_disk()
        errors = validate_incident_case(inc)
        if errors:
            raise IncidentError(f"Invalid IncidentCase: {errors}")
        if inc.incident_id in self._incidents:
            raise IncidentError(f"IncidentCase already exists: {inc.incident_id}")
        self._incidents[inc.incident_id] = inc
        self._save()
        return inc

    def update_incident_status(
        self,
        incident_id: str,
        new_status: str,
        *,
        resolved_at: Optional[str] = None,
    ) -> IncidentCase:
        """Transition an incident to a new status."""
        self._refresh_from_disk()
        inc = self.require_incident(incident_id)
        try:
            IncidentStatus(new_status)
        except ValueError:
            raise IncidentError(f"Invalid status: {new_status!r}")

        updates: Dict[str, Any] = {"status": new_status}
        if new_status in {IncidentStatus.RESOLVED.value, IncidentStatus.CLOSED.value}:
            updates["resolved_at"] = resolved_at or _utc_now()

        updated = IncidentCase(**{**inc.to_dict(), **updates})
        errors = validate_incident_case(updated)
        if errors:
            raise IncidentError(f"Status update produces invalid IncidentCase: {errors}")
        self._incidents[incident_id] = updated
        self._save()
        return updated

    # ---- Postmortem reads ----

    def get_postmortem(self, postmortem_id: str) -> Optional[Postmortem]:
        self._refresh_from_disk()
        return self._postmortems.get(postmortem_id)

    def require_postmortem(self, postmortem_id: str) -> Postmortem:
        pm = self.get_postmortem(postmortem_id)
        if pm is None:
            raise IncidentError(f"Postmortem not found: {postmortem_id}")
        return pm

    def list_postmortems(self) -> List[Postmortem]:
        self._refresh_from_disk()
        return list(self._postmortems.values())

    def find_postmortem_for_incident(self, incident_id: str) -> Optional[Postmortem]:
        self._refresh_from_disk()
        matches = [p for p in self._postmortems.values() if p.incident_id == incident_id]
        return matches[0] if matches else None

    # ---- Postmortem writes ----

    def create_postmortem(self, pm: Postmortem) -> Postmortem:
        """
        Create a Postmortem.

        The referenced IncidentCase must exist in this store before a
        Postmortem can be created.
        """
        self._refresh_from_disk()
        errors = validate_postmortem(pm)
        if errors:
            raise IncidentError(f"Invalid Postmortem: {errors}")
        if pm.postmortem_id in self._postmortems:
            raise IncidentError(f"Postmortem already exists: {pm.postmortem_id}")
        if pm.incident_id not in self._incidents:
            raise IncidentError(
                f"Postmortem references unknown IncidentCase: {pm.incident_id}. "
                "Create the IncidentCase first."
            )
        self._validate_postmortem_against_incident(pm, self._incidents[pm.incident_id])
        self._postmortems[pm.postmortem_id] = pm
        self._save()
        return pm

    def update_postmortem_status(
        self,
        postmortem_id: str,
        new_status: str,
        *,
        published_at: Optional[str] = None,
    ) -> Postmortem:
        self._refresh_from_disk()
        pm = self.require_postmortem(postmortem_id)
        try:
            PostmortemStatus(new_status)
        except ValueError:
            raise IncidentError(f"Invalid postmortem status: {new_status!r}")

        updates: Dict[str, Any] = {"status": new_status}
        if new_status == PostmortemStatus.PUBLISHED.value:
            updates["published_at"] = published_at or _utc_now()

        updated = Postmortem(**{**pm.to_dict(), **updates})
        errors = validate_postmortem(updated)
        if errors:
            raise IncidentError(f"Status update produces invalid Postmortem: {errors}")
        self._postmortems[postmortem_id] = updated
        self._save()
        return updated

    def link_evolution_decision(
        self,
        postmortem_id: str,
        evolution_decision_id: str,
    ) -> Postmortem:
        """
        Set the linked_evolution_decision_id on a Postmortem.

        Called by EVO-003 when an EvolutionDecision is created referencing
        this postmortem (evolution_decision.postmortem lineage edge).
        """
        self._refresh_from_disk()
        pm = self.require_postmortem(postmortem_id)
        updated = Postmortem(**{**pm.to_dict(), "linked_evolution_decision_id": evolution_decision_id})
        self._postmortems[postmortem_id] = updated
        self._save()
        return updated

    # ---- Persistence ----

    def _save(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "incidents": [i.to_dict() for i in self._incidents.values()],
                "postmortems": [p.to_dict() for p in self._postmortems.values()],
            }
            self._path.write_text(json.dumps(data, indent=2))
            self._loaded_mtime_ns = self._path.stat().st_mtime_ns

    def _load(self, path: Path) -> None:
        text = path.read_text()
        if not text.strip():
            return
        data = json.loads(text)
        for record in data.get("incidents", []):
            inc = IncidentCase.from_dict(record)
            self._incidents[inc.incident_id] = inc
        for record in data.get("postmortems", []):
            pm = Postmortem.from_dict(record)
            self._postmortems[pm.postmortem_id] = pm

    @staticmethod
    def _validate_postmortem_against_incident(pm: Postmortem, inc: IncidentCase) -> None:
        propagated_fields = (
            "binding_id",
            "deployment_stage",
            "deployment_plan_id",
            "capital_pool_id",
            "persona_capital_binding_id",
            "artifact_id",
            "artifact_version",
            "runtime_id",
            "trace_id",
        )
        mismatches = [
            field_name
            for field_name in propagated_fields
            if getattr(pm, field_name) != getattr(inc, field_name)
        ]
        if mismatches:
            details = ", ".join(
                f"{field_name}: incident={getattr(inc, field_name)!r}, postmortem={getattr(pm, field_name)!r}"
                for field_name in mismatches
            )
            raise IncidentError(
                "Postmortem propagated evidence must match the referenced IncidentCase: "
                f"{details}"
            )
