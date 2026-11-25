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
import math
import os
import re
import threading
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Set, Tuple

from services.foundation.reliable_delivery import sweep_stale_temp_siblings

try:  # pragma: no cover - Linux production and CI provide fcntl.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


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


class IncidentConcurrencyError(IncidentError):
    """Raised when a guarded mutation loses a durable compare-and-set race."""


def _serialized_store_read(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        # Postgres refreshes replace both in-memory caches.  Reads must share
        # the write lock so they cannot clear a mutation between its domain
        # update and durable compare-and-set.
        with self._thread_lock:
            return method(self, *args, **kwargs)

    return wrapped


def _serialized_store_write(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._write_guard():
            # JSON guards refresh before yielding; Postgres-backed subclasses
            # refresh here because their guard has no filesystem path. Preserve
            # that durable state so a failed _save (or any other exception after
            # mutation) cannot leak an uncommitted transition through reads.
            self._refresh_from_disk()
            # IncidentCase/Postmortem are frozen and every mutation replaces the
            # record, so a shallow map copy restores durable state exactly.  A
            # whole-store deepcopy here cost ~40 MB per write at hosted scale.
            incidents_before = dict(self._incidents)
            postmortems_before = dict(self._postmortems)
            loaded_mtime_before = self._loaded_mtime_ns
            try:
                return method(self, *args, **kwargs)
            except BaseException:
                self._incidents.clear()
                self._incidents.update(incidents_before)
                self._postmortems.clear()
                self._postmortems.update(postmortems_before)
                self._loaded_mtime_ns = loaded_mtime_before
                raise

    return wrapped


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
    reconciliation_ids: List[str] = field(default_factory=list)
    incident_cluster_id: Optional[str] = None
    evidence_summary: Optional[str] = None
    lineage_ref: Optional[str] = None          # composite ref, e.g. "{artifact_id}@{artifact_version}"
    # Canonical breach identity (e.g. metric_name/window/policy_source) for
    # producers that need to distinguish two breaches sharing an explicit
    # caller-supplied incident_id. None for incidents created by paths that
    # don't set it (e.g. drift reports); a collision guard must treat that as
    # "identity unknown", not "identity matches" (see services/incidents/
    # consumer.py `_require_same_incident_identity`).
    threshold_identity: Optional[str] = None
    # Structured summary and occurrence fields (INCIDENT-HUMAN-SUMMARY-AND-OCCURRENCES-20261120)
    kind: Optional[str] = None
    metric: Optional[str] = None
    observed: Optional[Any] = None
    threshold: Optional[Any] = None
    comparator: Optional[str] = None
    occurrence_count: Optional[int] = None
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None

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
    telemetry_event_ids: List[str] = field(default_factory=list)
    reconciliation_ids: List[str] = field(default_factory=list)
    incident_cluster_id: Optional[str] = None
    incident_evidence_summary: Optional[str] = None
    lineage_ref: Optional[str] = None

    # Optional
    published_at: Optional[str] = None
    published_event_id: Optional[str] = None
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
# Monitor-agent finding helpers (BP5-SVC-011 / MONITOR-AGENT-INCIDENT-DEDUPE)
# ---------------------------------------------------------------------------

MONITOR_AGENT_BINDING_ID = "infra-binding-monitor-agent"
MONITOR_AGENT_PLAN_ID = "infra-plan-monitor-agent"
MONITOR_AGENT_POOL_ID = "infra-pool-non-trading"
MONITOR_AGENT_AUTHORITY_ID = "infra-authority-monitor-agent"
MONITOR_AGENT_RUNTIME_ID = "infra-runtime:monitor-agent"
MONITOR_AGENT_CLUSTER_PREFIX = "infra-cluster:monitor-agent:agent-finding-"
MONITOR_AGENT_ARTIFACT_PREFIX = "infra-component-agent-finding-"
MONITOR_AGENT_COMPONENT_KIND = "monitor-agent-finding"
NOT_REOBSERVED_RESOLUTION_REASON = "not_reobserved"
ORPHAN_RECOVERED_RESOLUTION_REASON = "orphan_recovered"

INFRASTRUCTURE_SUBJECT_PREFIX = "infra-subject-"
INFRASTRUCTURE_PLAN_PREFIX = "infra-plan-"
INFRASTRUCTURE_POOL_PREFIX = "infra-pool-"
INFRASTRUCTURE_AUTHORITY_PREFIX = "infra-authority-"
INFRASTRUCTURE_ARTIFACT_PREFIX = "infra-component-"
INFRASTRUCTURE_RUNTIME_PREFIX = "infra-endpoint-"
INFRASTRUCTURE_CLUSTER_PREFIX = "infrastructure:"

_OBSERVED_AT_RE = re.compile(r"(?:last_)?observed_at=([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+\-Z]+)")
_SNAPSHOT_TS_RE = re.compile(r"monitor-snapshot-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z")
_FINGERPRINT_KV_RE = re.compile(r"fingerprint=([^;\s]+)")
_CANONICAL_KEY_KV_RE = re.compile(r"canonical_key=([^;\s]+)")
_COMPONENT_KV_RE = re.compile(r"(?:component|service_name)=agent-finding-([^;\s]+)")
_RESOLUTION_REASON_KV_RE = re.compile(r"resolution_reason=([^;\s]+)")
_SERVICE_NAME_KV_RE = re.compile(r"(?:^|;\s*)service_name=([^;]+)")
_MERGED_INCIDENT_ID_KV_RE = re.compile(r"(?:^|;\s*)merged_incident_id=([A-Za-z0-9_.:-]+)")


def _parse_iso_utc(raw: Optional[str]) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_monitor_agent_finding_incident(inc: IncidentCase) -> bool:
    """Return True strictly for monitor-agent finding incidents.

    Never matches runtime, drift, infrastructure-health, or deployment incidents.
    """
    if not str(inc.incident_id or "").startswith("agent-"):
        return False
    if inc.deployment_plan_id != MONITOR_AGENT_PLAN_ID:
        return False
    if inc.persona_capital_binding_id != MONITOR_AGENT_AUTHORITY_ID:
        return False
    cluster = str(inc.incident_cluster_id or "")
    summary = str(inc.evidence_summary or "")
    artifact = str(inc.artifact_id or "")
    return (
        ":monitor-agent:agent-finding-" in cluster
        or f"component_kind={MONITOR_AGENT_COMPONENT_KIND}" in summary
        or artifact.startswith(MONITOR_AGENT_ARTIFACT_PREFIX)
    )


def is_infrastructure_postmortem(pm: Any) -> bool:
    """Apply the incident predicates to a stored postmortem row.

    A postmortem stores ``incident_evidence_summary`` where the predicates read
    ``evidence_summary``; map it so real stored rows are classified correctly.
    ``pm`` may be a Postmortem or a dict.
    """
    def attr(name: str) -> Any:
        return pm.get(name) if isinstance(pm, dict) else getattr(pm, name, None)

    shim = IncidentCase.__new__(IncidentCase)
    for name in (
        "incident_id", "binding_id", "deployment_plan_id",
        "persona_capital_binding_id", "incident_cluster_id", "artifact_id",
        "runtime_id",
    ):
        object.__setattr__(shim, name, attr(name))
    object.__setattr__(
        shim, "evidence_summary",
        attr("incident_evidence_summary") or attr("evidence_summary"),
    )
    return is_infrastructure_health_incident(shim) or is_monitor_agent_finding_incident(shim)


def normalize_infrastructure_component(value: str) -> str:
    """Normalize a downstream infrastructure component/service name."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-").lower()
    return cleaned[:64]


def is_infrastructure_health_incident(inc: IncidentCase) -> bool:
    """Return True strictly for non-trading infrastructure-health incidents.

    Excludes monitor-agent finding, runtime, drift, and deployment incidents.
    """
    if is_monitor_agent_finding_incident(inc):
        return False
    if str(inc.incident_id or "").startswith("agent-"):
        return False
    cluster = str(inc.incident_cluster_id or "")
    summary = str(inc.evidence_summary or "")
    artifact = str(inc.artifact_id or "")
    if (
        ":monitor-agent:agent-finding-" in cluster
        or f"component_kind={MONITOR_AGENT_COMPONENT_KIND}" in summary
        or artifact.startswith(MONITOR_AGENT_ARTIFACT_PREFIX)
    ):
        return False
    if cluster.startswith(("drift:", "deployment:", "runtime:")):
        return False

    binding = str(inc.binding_id or "")
    plan = str(inc.deployment_plan_id or "")
    authority = str(inc.persona_capital_binding_id or "")
    runtime = str(inc.runtime_id or "")

    if not (
        binding.startswith(INFRASTRUCTURE_SUBJECT_PREFIX)
        and plan.startswith(INFRASTRUCTURE_PLAN_PREFIX)
        and authority.startswith(INFRASTRUCTURE_AUTHORITY_PREFIX)
    ):
        return False
    return (
        cluster.startswith(INFRASTRUCTURE_CLUSTER_PREFIX)
        or "non_trading_infrastructure_incident=true" in summary
        or artifact.startswith(INFRASTRUCTURE_ARTIFACT_PREFIX)
        or runtime.startswith(INFRASTRUCTURE_RUNTIME_PREFIX)
    )


def extract_infrastructure_incident_component(inc: IncidentCase) -> Optional[str]:
    """Return the normalized component name for an infrastructure-health incident."""
    if not is_infrastructure_health_incident(inc):
        return None
    artifact = str(inc.artifact_id or "").strip()
    if artifact.startswith(INFRASTRUCTURE_ARTIFACT_PREFIX):
        comp = normalize_infrastructure_component(artifact[len(INFRASTRUCTURE_ARTIFACT_PREFIX):])
        if comp:
            return comp
    cluster = str(inc.incident_cluster_id or "").strip()
    if cluster.startswith(INFRASTRUCTURE_CLUSTER_PREFIX):
        parts = cluster.split(":", 3)
        if len(parts) == 4:
            comp = normalize_infrastructure_component(parts[3])
            if comp:
                return comp
    summary = str(inc.evidence_summary or "")
    match = _SERVICE_NAME_KV_RE.search(summary)
    if match:
        comp = normalize_infrastructure_component(match.group(1))
        if comp:
            return comp
    binding = str(inc.binding_id or "").strip()
    pool = str(inc.capital_pool_id or "").strip()
    if pool.startswith(INFRASTRUCTURE_POOL_PREFIX):
        tenant_id = pool[len(INFRASTRUCTURE_POOL_PREFIX):].strip()
        prefix = f"{INFRASTRUCTURE_SUBJECT_PREFIX}{tenant_id}-"
        if binding.startswith(prefix):
            comp = normalize_infrastructure_component(binding[len(prefix):])
            if comp:
                return comp
    if binding.startswith(f"{INFRASTRUCTURE_SUBJECT_PREFIX}default-"):
        comp = normalize_infrastructure_component(
            binding[len(f"{INFRASTRUCTURE_SUBJECT_PREFIX}default-"):]
        )
        if comp:
            return comp
    return None


def extract_infrastructure_incident_referenced_ids(inc: IncidentCase) -> Set[str]:
    """Return the canonical incident_id plus any merged secondary incident_ids."""
    refs: Set[str] = set()
    if inc.incident_id:
        refs.add(inc.incident_id.strip())
    for item in inc.telemetry_event_ids or []:
        text = str(item or "").strip()
        if text.startswith("merged_incident_id:"):
            merged_id = text.split(":", 1)[1].strip()
            if merged_id:
                refs.add(merged_id)
    summary = str(inc.evidence_summary or "")
    for match in _MERGED_INCIDENT_ID_KV_RE.finditer(summary):
        merged_id = match.group(1).strip()
        if merged_id:
            refs.add(merged_id)
    return refs


def extract_incident_last_observed_at(inc: IncidentCase) -> Optional[datetime]:
    """Return the latest UTC observation timestamp recorded on an IncidentCase."""
    candidates: List[datetime] = []
    for ts_raw in (inc.created_at, inc.first_seen_at, inc.last_seen_at):
        parsed_ts = _parse_iso_utc(ts_raw)
        if parsed_ts is not None:
            candidates.append(parsed_ts)

    summary = str(inc.evidence_summary or "")
    for match in _OBSERVED_AT_RE.finditer(summary):
        parsed = _parse_iso_utc(match.group(1))
        if parsed is not None:
            candidates.append(parsed)

    sources = [summary, *(str(item or "") for item in (inc.telemetry_event_ids or []))]
    for source in sources:
        for match in _SNAPSHOT_TS_RE.finditer(source):
            y, m, d, hh, mm, ss = (int(g) for g in match.groups())
            try:
                candidates.append(datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc))
            except ValueError:
                continue

    return max(candidates) if candidates else None


def extract_incident_resolution_reason(inc: IncidentCase) -> Optional[str]:
    """Return the recorded resolution reason on an IncidentCase, if any."""
    summary = str(inc.evidence_summary or "")
    matches = _RESOLUTION_REASON_KV_RE.findall(summary)
    if matches:
        return matches[-1]
    for item in reversed(inc.telemetry_event_ids or []):
        text = str(item or "").strip()
        if text.startswith("resolution_reason:"):
            reason = text.split(":", 1)[1].strip()
            if reason:
                return reason
    return None


def extract_monitor_agent_finding_references(inc: IncidentCase) -> Set[str]:
    """Return all stable reference tokens that identify an agent finding incident."""
    refs: Set[str] = set()
    if inc.incident_id:
        refs.add(inc.incident_id.strip())
    if inc.metric:
        refs.add(str(inc.metric).strip())
    cluster = str(inc.incident_cluster_id or "").strip()
    if cluster:
        refs.add(cluster)
        marker = ":monitor-agent:agent-finding-"
        if marker in cluster:
            suffix = cluster.split(marker, 1)[1].strip()
            if suffix:
                refs.add(suffix)
                refs.add(f"agent-finding-{suffix}")
    artifact = str(inc.artifact_id or "").strip()
    if artifact.startswith(MONITOR_AGENT_ARTIFACT_PREFIX):
        suffix = artifact[len(MONITOR_AGENT_ARTIFACT_PREFIX):].strip()
        if suffix:
            refs.add(suffix)
    summary = str(inc.evidence_summary or "")
    for pattern in (_FINGERPRINT_KV_RE, _CANONICAL_KEY_KV_RE, _COMPONENT_KV_RE):
        for match in pattern.finditer(summary):
            token = match.group(1).strip()
            if token:
                refs.add(token)
    return refs


def canonical_monitor_agent_finding_key(
    fingerprint: str,
    title: str = "",
    rationale: str = "",
) -> str:
    """Normalize recurring monitor-agent finding families into a stable dedupe key."""
    fp_raw = str(fingerprint or "").strip()
    text = f"{fp_raw} {title or ''} {rationale or ''}".lower()

    # 1. Reconciler generation / fence_token / epoch advances or jumps
    has_reconciler_anchor = any(
        tok in text
        for tok in (
            "reconcil",
            "fence_token",
            "fence-token",
            "fence token",
            "leader_epoch",
            "leader-epoch",
            "leader epoch",
        )
    )
    has_generation_signal = bool(
        re.search(
            r"(?:generations?|gen[-_=\s]*\d+|\bgen\b|fence[-_\s]*tokens?|\bfence\b|"
            r"epochs?|tokens?|advanc\w*|jump\w*|step\w*|increment\w*|monotonic\w*|"
            r"rollover\w*|counter\w*|restart\w*)",
            text,
        )
    )
    if has_reconciler_anchor and has_generation_signal:
        return "reconciler-generation-change"

    # 2. Paper fleet size / active runtime or binding count changes
    has_fleet_anchor = "fleet" in text and bool(
        re.search(
            r"(?:size\w*|count\w*|runtimes?|active|bindings?|paper|chang\w*|drift\w*|"
            r"mismatch\w*|scal\w*|shrink\w*|shrank|grow\w*|grew|drop\w*|increas\w*|"
            r"decreas\w*|total|number|capacity|workers?|nodes?|slots?|instances?|"
            r"strategies|strategy|\d+)",
            text,
        )
    )
    has_runtime_count_phrase = any(
        phrase in text
        for phrase in (
            "runtime count",
            "runtime-count",
            "runtime_count",
            "active_runtimes",
            "active-runtimes",
            "active runtimes",
            "total_runtimes",
            "total-runtimes",
            "total runtimes",
            "binding count",
            "binding-count",
            "binding_count",
            "worker count",
            "worker-count",
            "worker_count",
            "instance count",
            "instance-count",
            "strategy count",
            "strategy-count",
            "paper runtime",
            "runtimes count",
        )
    ) or (
        bool(re.search(r"\b(?:runtimes?|bindings?|workers?|instances?)\b", text))
        and bool(
            re.search(
                r"\b(?:count\w*|size\w*|fleet|number of (?:active |paper |running )?"
                r"(?:runtimes?|bindings?|workers?|instances?))\b",
                text,
            )
        )
    )
    if has_fleet_anchor or has_runtime_count_phrase:
        return "fleet-size-change"

    # 3. Performance summary count / residual mismatch
    if any(k in text for k in ("performance", "summary", "attribution")) and bool(
        re.search(r"(?:residuals?|summary[-_\s]+counts?|count[-_\s]+residuals?|unexplained)", text)
    ):
        return "performance-summary-residual"

    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", fp_raw.lower())[:64].strip("-")
    return safe or "finding"


# ---------------------------------------------------------------------------
# IncidentStore — in-memory write-guarded store
# ---------------------------------------------------------------------------

MAX_EVIDENCE_SUMMARY_LENGTH = 4000


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
        self._thread_lock = threading.RLock()
        self._write_guard_depth = 0
        if path and path.exists():
            self._load(path)
            self._loaded_mtime_ns = path.stat().st_mtime_ns
        if path:
            with self._write_guard():
                pass

    @contextmanager
    def _write_guard(self) -> Iterator[None]:
        """Serialize JSON read-modify-write cycles across threads/processes."""

        with self._thread_lock:
            if self._path is None or self._write_guard_depth > 0:
                self._write_guard_depth += 1
                try:
                    yield
                finally:
                    self._write_guard_depth -= 1
                return
            self._write_guard_depth = 1
            self._path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self._path.with_name(f".{self._path.name}.lock")
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                self._sweep_stale_temp_files()
                # Reparse only when another writer changed the file; the
                # previous unconditional reload re-read the whole store on
                # every write.
                self._reload_if_changed()
                yield
            finally:
                self._write_guard_depth = 0
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _refresh_from_disk(self) -> None:
        self._reload_if_changed()

    def _reload_if_changed(self) -> None:
        if not self._path or not self._path.exists():
            return
        mtime_ns = self._path.stat().st_mtime_ns
        if self._loaded_mtime_ns == mtime_ns:
            return
        self._incidents.clear()
        self._postmortems.clear()
        self._load(self._path)
        self._loaded_mtime_ns = mtime_ns

    def _temp_path(self) -> Path:
        assert self._path is not None
        return self._path.with_name(f".{self._path.name}.tmp")

    def _sweep_stale_temp_files(self) -> List[Path]:
        """Remove partial atomic-write leftovers; call only under the write lock.

        Shared rule (``sweep_stale_temp_siblings``): the deterministic
        ``.<name>.tmp`` and legacy random-name siblings of this store only, regular
        files only, and a temp newer than the real file is kept and reported.
        At most one temp file exists between writes, bounded by one store size.
        """

        if self._path is None:
            return []
        return sweep_stale_temp_siblings(self._path, keep_newer=True)

    # ---- IncidentCase reads ----

    @_serialized_store_read
    def get_incident(self, incident_id: str) -> Optional[IncidentCase]:
        self._refresh_from_disk()
        return self._incident_by_id(incident_id)

    def require_incident(self, incident_id: str) -> IncidentCase:
        inc = self.get_incident(incident_id)
        if inc is None:
            raise IncidentError(f"IncidentCase not found: {incident_id}")
        return inc

    @_serialized_store_read
    def list_incidents(self) -> List[IncidentCase]:
        self._refresh_from_disk()
        return list(self._incidents.values())

    # ---- Bounded-read hooks (overridden by the Postgres store) ----

    def _incident_by_id(self, incident_id: str) -> Optional[IncidentCase]:
        """Point lookup that may reach beyond the resident cache."""
        return self._incidents.get(incident_id)

    def _postmortem_by_id(self, postmortem_id: str) -> Optional[Postmortem]:
        return self._postmortems.get(postmortem_id)

    def _postmortem_for_incident_id(self, incident_id: str) -> Optional[Postmortem]:
        matches = [p for p in self._postmortems.values() if p.incident_id == incident_id]
        return matches[0] if matches else None

    @_serialized_store_read
    def count_incidents(self) -> int:
        self._refresh_from_disk()
        return len(self._incidents)

    @_serialized_store_read
    def count_incidents_with_id_prefix(self, prefix: str) -> int:
        self._refresh_from_disk()
        return sum(1 for i in self._incidents.values() if i.incident_id.startswith(prefix))

    @_serialized_store_read
    def query_incidents(
        self,
        *,
        binding_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        open_only: bool = False,
        limit: Optional[int] = None,
    ) -> List[IncidentCase]:
        """Filtered incidents, most recently created first, optionally capped."""
        self._refresh_from_disk()
        rows = [
            i
            for i in self._incidents.values()
            if (not binding_id or i.binding_id == binding_id)
            and (not capital_pool_id or i.capital_pool_id == capital_pool_id)
            and (not status or i.status == status)
            and (not severity or i.severity == severity)
            and (not open_only or i.is_open())
        ]
        rows.sort(key=lambda i: (i.created_at, i.incident_id), reverse=True)
        return rows[:limit] if limit is not None else rows

    @_serialized_store_read
    def find_latest_infrastructure_incident_for_component(
        self,
        component: str,
    ) -> Optional[IncidentCase]:
        self._refresh_from_disk()
        for candidate in reversed(list(self._incidents.values())):
            if (
                is_infrastructure_health_incident(candidate)
                and extract_infrastructure_incident_component(candidate) == component
            ):
                return candidate
        return None

    @_serialized_store_read
    def find_incidents_by_binding(self, binding_id: str) -> List[IncidentCase]:
        self._refresh_from_disk()
        return [i for i in self._incidents.values() if i.binding_id == binding_id]

    @_serialized_store_read
    def find_incidents_by_pool(self, capital_pool_id: str) -> List[IncidentCase]:
        self._refresh_from_disk()
        return [i for i in self._incidents.values() if i.capital_pool_id == capital_pool_id]

    @_serialized_store_read
    def find_open_incidents(self) -> List[IncidentCase]:
        self._refresh_from_disk()
        return [i for i in self._incidents.values() if i.is_open()]

    @_serialized_store_read
    def find_matching_open_monitor_agent_finding(
        self,
        *,
        fingerprint: str,
        title: str = "",
        rationale: str = "",
        incident_id_ref: Optional[str] = None,
    ) -> Optional[IncidentCase]:
        """Find an open monitor-agent finding incident matching a stable reference or canonical family."""
        self._refresh_from_disk()
        open_findings = sorted(
            (
                inc
                for inc in self._incidents.values()
                if inc.is_open() and is_monitor_agent_finding_incident(inc)
            ),
            key=lambda item: (item.created_at, item.incident_id),
        )
        if not open_findings:
            return None

        explicit_ref = str(incident_id_ref or "").strip()
        if explicit_ref:
            for inc in open_findings:
                if inc.incident_id == explicit_ref or explicit_ref in extract_monitor_agent_finding_references(inc):
                    return inc

        fp_clean = str(fingerprint or "").strip()
        if fp_clean:
            for inc in open_findings:
                if inc.incident_id == fp_clean or fp_clean in extract_monitor_agent_finding_references(inc):
                    return inc

        canonical_key = canonical_monitor_agent_finding_key(fp_clean, title, rationale)
        canonical_cluster = f"{MONITOR_AGENT_CLUSTER_PREFIX}{canonical_key}"
        for inc in open_findings:
            refs = extract_monitor_agent_finding_references(inc)
            if inc.incident_cluster_id == canonical_cluster or canonical_key in refs:
                return inc
            inc_canonical = canonical_monitor_agent_finding_key(
                inc.incident_cluster_id or "",
                inc.title,
                inc.evidence_summary or "",
            )
            if inc_canonical == canonical_key:
                return inc

        return None

    @_serialized_store_read
    def find_open_infrastructure_incidents_for_component(
        self,
        component: str,
        *,
        cluster_id: Optional[str] = None,
    ) -> List[IncidentCase]:
        """Find all open infrastructure-health incidents for a component, oldest first."""
        self._refresh_from_disk()
        comp = normalize_infrastructure_component(component)
        cluster_clean = str(cluster_id or "").strip()
        if not comp and not cluster_clean:
            return []
        matches: List[IncidentCase] = []
        for inc in self._incidents.values():
            if not inc.is_open() or not is_infrastructure_health_incident(inc):
                continue
            inc_comp = extract_infrastructure_incident_component(inc)
            if (comp and inc_comp == comp) or (
                cluster_clean and str(inc.incident_cluster_id or "").strip() == cluster_clean
            ):
                matches.append(inc)
        matches.sort(key=lambda item: (item.created_at, item.incident_id))
        return matches

    @_serialized_store_read
    def find_infrastructure_incident_by_id_or_alias(
        self,
        incident_id: str,
    ) -> Optional[IncidentCase]:
        """Find an infrastructure-health incident by primary incident_id or merged alias."""
        self._refresh_from_disk()
        target = str(incident_id or "").strip()
        if not target:
            return None
        direct = self._incident_by_id(target)
        if direct is not None and is_infrastructure_health_incident(direct):
            return direct
        candidates = sorted(
            (
                inc
                for inc in self._incidents.values()
                if is_infrastructure_health_incident(inc)
                and target in extract_infrastructure_incident_referenced_ids(inc)
            ),
            key=lambda item: (0 if item.is_open() else 1, item.created_at, item.incident_id),
        )
        return candidates[0] if candidates else None

    # ---- IncidentCase writes ----

    @_serialized_store_write
    def create_incident(self, inc: IncidentCase) -> IncidentCase:
        errors = validate_incident_case(inc)
        if errors:
            raise IncidentError(f"Invalid IncidentCase: {errors}")
        if self._incident_by_id(inc.incident_id) is not None:
            raise IncidentError(f"IncidentCase already exists: {inc.incident_id}")
        self._incidents[inc.incident_id] = inc
        self._save(
            aggregate_type="incident",
            record_id=inc.incident_id,
            expected_snapshot=None,
        )
        return inc

    @_serialized_store_write
    def update_incident_status(
        self,
        incident_id: str,
        new_status: str,
        *,
        resolved_at: Optional[str] = None,
        telemetry_event_ids: Optional[List[str]] = None,
        resolution_reason: Optional[str] = None,
        expected_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> IncidentCase:
        """Transition an incident to a new status.

        Supplied telemetry_event_ids are appended to (never replace) the
        incident's existing references.
        """
        inc = self.require_incident(incident_id)
        if expected_snapshot is not None and inc.to_dict() != dict(expected_snapshot):
            raise IncidentConcurrencyError(
                f"IncidentCase changed concurrently before status transition: {incident_id}"
            )
        try:
            IncidentStatus(new_status)
        except ValueError:
            raise IncidentError(f"Invalid status: {new_status!r}")

        # Enforce no regression from terminal statuses
        if inc.status == IncidentStatus.CLOSED.value and new_status != IncidentStatus.CLOSED.value:
            raise IncidentError(f"Incident is closed and cannot transition to {new_status}")
        if inc.status == IncidentStatus.RESOLVED.value and new_status not in {
            IncidentStatus.RESOLVED.value,
            IncidentStatus.CLOSED.value,
        }:
            raise IncidentError(f"Incident is resolved and cannot regress to {new_status}")

        updates: Dict[str, Any] = {"status": new_status}
        merged_telemetry = list(telemetry_event_ids or [])
        reason_clean = str(resolution_reason or "").strip()
        if not reason_clean:
            for item in reversed(merged_telemetry):
                text = str(item or "").strip()
                if text.startswith("resolution_reason:"):
                    candidate_reason = text.split(":", 1)[1].strip()
                    if candidate_reason:
                        reason_clean = candidate_reason
                        break
        if reason_clean:
            merged_telemetry.append(f"resolution_reason:{reason_clean}")
            updates["evidence_summary"] = _merge_summary(
                inc.evidence_summary,
                f"resolution_reason={reason_clean}",
            )
        if merged_telemetry:
            updates["telemetry_event_ids"] = _merge_unique(
                inc.telemetry_event_ids, merged_telemetry
            )
        if new_status in {IncidentStatus.RESOLVED.value, IncidentStatus.CLOSED.value}:
            updates["resolved_at"] = inc.resolved_at or resolved_at or _utc_now()

        updated = IncidentCase(**{**inc.to_dict(), **updates})
        errors = validate_incident_case(updated)
        if errors:
            raise IncidentError(f"Status update produces invalid IncidentCase: {errors}")
        self._incidents[incident_id] = updated
        self._save(
            aggregate_type="incident",
            record_id=incident_id,
            expected_snapshot=inc.to_dict(),
        )
        return updated

    def _stale_monitor_agent_finding_candidates(
        self,
        *,
        resolve_after_seconds: int,
        now_dt: datetime,
    ) -> List[IncidentCase]:
        if resolve_after_seconds <= 0:
            return []
        candidates: List[IncidentCase] = []
        for inc in self._incidents.values():
            if not inc.is_open():
                continue
            if not is_monitor_agent_finding_incident(inc):
                continue
            last_obs = extract_incident_last_observed_at(inc)
            if last_obs is None:
                continue
            if (now_dt - last_obs).total_seconds() < resolve_after_seconds:
                continue
            candidates.append(inc)
        return candidates

    @_serialized_store_read
    def has_stale_monitor_agent_findings(
        self,
        *,
        resolve_after_seconds: int,
        now: Optional[datetime] = None,
    ) -> bool:
        """Return True when at least one open monitor-agent finding is stale."""
        if resolve_after_seconds <= 0:
            return False
        self._refresh_from_disk()
        now_dt = now or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        else:
            now_dt = now_dt.astimezone(timezone.utc)
        return bool(
            self._stale_monitor_agent_finding_candidates(
                resolve_after_seconds=resolve_after_seconds,
                now_dt=now_dt,
            )
        )

    @_serialized_store_write
    def resolve_stale_monitor_agent_findings(
        self,
        *,
        resolve_after_seconds: int,
        now: Optional[datetime] = None,
    ) -> List[IncidentCase]:
        """Resolve open monitor-agent finding incidents not re-observed within the window.

        Applies strictly to monitor-agent finding incidents and never touches
        runtime, drift, infrastructure-health, or deployment incidents.
        """
        if resolve_after_seconds <= 0:
            return []
        now_dt = now or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        else:
            now_dt = now_dt.astimezone(timezone.utc)
        resolved_iso = now_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

        transitions: List[Tuple[str, Mapping[str, Any], IncidentCase]] = []
        for inc in self._stale_monitor_agent_finding_candidates(
            resolve_after_seconds=resolve_after_seconds,
            now_dt=now_dt,
        ):
            updates: Dict[str, Any] = {
                "status": IncidentStatus.RESOLVED.value,
                "resolved_at": inc.resolved_at or resolved_iso,
                "telemetry_event_ids": _merge_unique(
                    inc.telemetry_event_ids,
                    [f"resolution_reason:{NOT_REOBSERVED_RESOLUTION_REASON}"],
                ),
                "evidence_summary": _merge_summary(
                    inc.evidence_summary,
                    f"resolution_reason={NOT_REOBSERVED_RESOLUTION_REASON}",
                ),
            }
            updated = IncidentCase(**{**inc.to_dict(), **updates})
            errors = validate_incident_case(updated)
            if errors:
                raise IncidentError(f"Auto-resolution produces invalid IncidentCase: {errors}")
            transitions.append((inc.incident_id, inc.to_dict(), updated))

        if not transitions:
            return []
        return self._persist_batch_incident_updates(transitions)

    def _persist_batch_incident_updates(
        self,
        transitions: List[Tuple[str, Mapping[str, Any], IncidentCase]],
    ) -> List[IncidentCase]:
        resolved: List[IncidentCase] = []
        for record_id, expected_snapshot, updated in transitions:
            self._incidents[record_id] = updated
            self._save(
                aggregate_type="incident",
                record_id=record_id,
                expected_snapshot=expected_snapshot,
            )
            resolved.append(updated)
        return resolved

    @_serialized_store_write
    def merge_incident_evidence(self, incident_id: str, incoming: IncidentCase) -> IncidentCase:
        """Merge additional evidence into an existing open IncidentCase.

        Updates occurrence_count, last_seen_at, and the latest observed value in
        place. Single-report evidence_summary replacement bounds row growth:
        evidence_summary no longer grows by appending whole reports.
        """
        existing = self.require_incident(incident_id)
        merged_telemetry = _merge_unique(
            existing.telemetry_event_ids,
            incoming.telemetry_event_ids,
        )
        merged_reconciliation = _merge_unique(
            existing.reconciliation_ids,
            incoming.reconciliation_ids,
        )
        merged_severity = _max_incident_severity(existing.severity, incoming.severity)

        incoming_summary_clean = (incoming.evidence_summary or "").strip()
        existing_summary_clean = (existing.evidence_summary or "").strip()
        has_oversized_existing = len(existing_summary_clean) > MAX_EVIDENCE_SUMMARY_LENGTH
        has_new_telemetry = merged_telemetry != list(existing.telemetry_event_ids or [])
        has_new_reconciliation = merged_reconciliation != list(existing.reconciliation_ids or [])
        has_new_severity = merged_severity != existing.severity
        has_new_cluster = bool(not existing.incident_cluster_id and incoming.incident_cluster_id)
        has_new_summary = bool(incoming_summary_clean and incoming_summary_clean != existing_summary_clean)
        incoming_observed = (
            incoming.observed
            if incoming.observed is not None
            else _extract_observed_from_summary_text(incoming_summary_clean)
        )
        has_new_observed = bool(incoming_observed is not None and incoming_observed != existing.observed)

        incoming_obs_dt = extract_incident_last_observed_at(incoming)
        existing_obs_dt = extract_incident_last_observed_at(existing)
        has_newer_timestamp = bool(
            incoming_obs_dt is not None
            and (existing_obs_dt is None or incoming_obs_dt > existing_obs_dt)
        )
        is_new_occurrence = (
            has_new_telemetry
            or has_new_reconciliation
            or has_new_summary
            or has_new_observed
            or has_newer_timestamp
            or has_oversized_existing
        )
        if not is_new_occurrence and not has_new_severity and not has_new_cluster:
            return existing

        updates: Dict[str, Any] = {
            "telemetry_event_ids": merged_telemetry,
            "reconciliation_ids": merged_reconciliation,
            "severity": merged_severity,
        }
        if has_new_cluster:
            updates["incident_cluster_id"] = incoming.incident_cluster_id

        base_count = (
            existing.occurrence_count
            if isinstance(existing.occurrence_count, int) and existing.occurrence_count >= 1
            else _infer_stored_occurrence_count(existing.to_dict())
        )
        incoming_count = (
            incoming.occurrence_count
            if isinstance(incoming.occurrence_count, int) and incoming.occurrence_count >= 1
            else 1
        )
        updates["occurrence_count"] = base_count + incoming_count
        updates["first_seen_at"] = existing.first_seen_at or existing.created_at
        incoming_obs_iso = (
            incoming_obs_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if incoming_obs_dt is not None
            else None
        )
        existing_obs_iso = (
            existing_obs_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if existing_obs_dt is not None
            else None
        )
        updates["last_seen_at"] = (
            _latest_iso_timestamp(
                existing.last_seen_at,
                existing.created_at,
                existing_obs_iso,
                incoming.last_seen_at,
                incoming.created_at,
                incoming_obs_iso,
            )
            or existing.created_at
        )
        if incoming_observed is not None:
            updates["observed"] = incoming_observed
        elif existing.observed is not None:
            updates["observed"] = existing.observed

        for attr in ("kind", "metric"):
            existing_val = getattr(existing, attr)
            incoming_val = getattr(incoming, attr)
            if existing_val is not None:
                updates[attr] = existing_val
            elif incoming_val is not None:
                updates[attr] = incoming_val

        for attr in ("threshold", "comparator"):
            incoming_val = getattr(incoming, attr)
            if incoming_val is not None:
                updates[attr] = incoming_val
            elif getattr(existing, attr) is not None:
                updates[attr] = getattr(existing, attr)

        if incoming_summary_clean or has_oversized_existing:
            updates["evidence_summary"] = _replace_single_report_summary(
                existing.evidence_summary,
                incoming.evidence_summary,
            )

        updated = IncidentCase(**{**existing.to_dict(), **updates})
        errors = validate_incident_case(updated)
        if errors:
            raise IncidentError(f"Evidence merge produces invalid IncidentCase: {errors}")
        if updated.to_dict() == existing.to_dict():
            return existing
        self._incidents[incident_id] = updated
        self._save(
            aggregate_type="incident",
            record_id=incident_id,
            expected_snapshot=existing.to_dict(),
        )
        return updated

    @_serialized_store_write
    def merge_infrastructure_incident_into_open(
        self,
        incident_id: str,
        incoming: IncidentCase,
    ) -> IncidentCase:
        """Merge a second infrastructure-health open event into the canonical open incident."""
        existing = self.require_incident(incident_id)
        if not is_infrastructure_health_incident(existing):
            raise IncidentError(
                f"Cannot merge infrastructure incident into non-infrastructure incident: {incident_id}"
            )
        extra_telemetry = list(incoming.telemetry_event_ids or [])
        extra_summary_parts: List[str] = []
        if incoming.incident_id and incoming.incident_id != existing.incident_id:
            extra_telemetry.append(f"merged_incident_id:{incoming.incident_id}")
            if incoming.telemetry_event_ids:
                extra_telemetry.append(
                    f"merged_event:{incoming.incident_id}:{incoming.telemetry_event_ids[0]}"
                )
            extra_summary_parts.append(f"merged_incident_id={incoming.incident_id}")
        incoming_raw_summary = (incoming.evidence_summary or "").strip()
        if incoming_raw_summary:
            extra_summary_parts.append(incoming_raw_summary)

        merged_telemetry = _merge_unique(
            existing.telemetry_event_ids,
            extra_telemetry,
        )
        merged_reconciliation = _merge_unique(
            existing.reconciliation_ids,
            incoming.reconciliation_ids,
        )
        merged_severity = _max_incident_severity(existing.severity, incoming.severity)
        incoming_summary = "; ".join(part for part in extra_summary_parts if part)
        existing_summary = (existing.evidence_summary or "").strip()
        has_oversized_existing = len(existing_summary) > MAX_EVIDENCE_SUMMARY_LENGTH

        incoming_obs_dt = extract_incident_last_observed_at(incoming)
        existing_obs_dt = extract_incident_last_observed_at(existing)
        is_new_occurrence = (
            merged_telemetry != list(existing.telemetry_event_ids or [])
            or merged_reconciliation != list(existing.reconciliation_ids or [])
            or bool(incoming_summary and incoming_summary != existing_summary)
            or bool(incoming.observed is not None and incoming.observed != existing.observed)
            or bool(
                incoming_obs_dt is not None
                and (existing_obs_dt is None or incoming_obs_dt > existing_obs_dt)
            )
            or has_oversized_existing
        )
        if not is_new_occurrence and merged_severity == existing.severity:
            return existing

        updates: Dict[str, Any] = {
            "telemetry_event_ids": merged_telemetry,
            "reconciliation_ids": merged_reconciliation,
            "severity": merged_severity,
        }
        if not existing.incident_cluster_id and incoming.incident_cluster_id:
            updates["incident_cluster_id"] = incoming.incident_cluster_id

        base_count = (
            existing.occurrence_count
            if isinstance(existing.occurrence_count, int) and existing.occurrence_count >= 1
            else _infer_stored_occurrence_count(existing.to_dict(), kind="infrastructure")
        )
        incoming_count = (
            incoming.occurrence_count
            if isinstance(incoming.occurrence_count, int) and incoming.occurrence_count >= 1
            else 1
        )
        updates["occurrence_count"] = base_count + incoming_count
        updates["first_seen_at"] = existing.first_seen_at or existing.created_at
        incoming_obs_iso = (
            incoming_obs_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if incoming_obs_dt is not None
            else None
        )
        existing_obs_iso = (
            existing_obs_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if existing_obs_dt is not None
            else None
        )
        updates["last_seen_at"] = (
            _latest_iso_timestamp(
                existing.last_seen_at,
                existing.created_at,
                existing_obs_iso,
                incoming.last_seen_at,
                incoming.created_at,
                incoming_obs_iso,
            )
            or existing.created_at
        )
        incoming_observed = (
            incoming.observed
            if incoming.observed is not None
            else _extract_observed_from_summary_text(incoming_summary)
        )
        if incoming_observed is not None:
            updates["observed"] = incoming_observed
        elif existing.observed is not None:
            updates["observed"] = existing.observed

        for attr in ("kind", "metric"):
            existing_val = getattr(existing, attr)
            incoming_val = getattr(incoming, attr)
            if existing_val is not None:
                updates[attr] = existing_val
            elif incoming_val is not None:
                updates[attr] = incoming_val

        for attr in ("threshold", "comparator"):
            incoming_val = getattr(incoming, attr)
            if incoming_val is not None:
                updates[attr] = incoming_val
            elif getattr(existing, attr) is not None:
                updates[attr] = getattr(existing, attr)

        if incoming_summary or has_oversized_existing:
            updates["evidence_summary"] = _replace_single_report_summary(
                existing.evidence_summary,
                incoming_summary,
            )

        updated = IncidentCase(**{**existing.to_dict(), **updates})
        errors = validate_incident_case(updated)
        if errors:
            raise IncidentError(f"Infrastructure evidence merge produces invalid IncidentCase: {errors}")
        if updated.to_dict() == existing.to_dict():
            return existing
        self._incidents[incident_id] = updated
        self._save(
            aggregate_type="incident",
            record_id=incident_id,
            expected_snapshot=existing.to_dict(),
        )
        return updated

    @_serialized_store_write
    def upsert_open_infrastructure_incident(
        self,
        incoming: IncidentCase,
    ) -> Tuple[IncidentCase, bool]:
        """Ensure at most one open infrastructure incident exists per component.

        If an open infrastructure incident for the same component already exists,
        merges ``incoming`` into the earliest open incident and resolves any
        extra pre-existing open duplicates for that component.
        """
        errors = validate_incident_case(incoming)
        if errors:
            raise IncidentError(f"Invalid IncidentCase: {errors}")
        if not is_infrastructure_health_incident(incoming):
            raise IncidentError(
                f"IncidentCase is not a non-trading infrastructure-health incident: {incoming.incident_id}"
            )
        comp = extract_infrastructure_incident_component(incoming) or ""
        open_for_component = self.find_open_infrastructure_incidents_for_component(
            comp,
            cluster_id=incoming.incident_cluster_id,
        )
        if open_for_component:
            primary = open_for_component[0]
            merged = self.merge_infrastructure_incident_into_open(primary.incident_id, incoming)
            if len(open_for_component) > 1:
                for duplicate in open_for_component[1:]:
                    merged = self.merge_infrastructure_incident_into_open(primary.incident_id, duplicate)
                self.resolve_open_infrastructure_incidents_for_component(
                    comp,
                    resolved_at=incoming.created_at,
                    resolution_reason=ORPHAN_RECOVERED_RESOLUTION_REASON,
                    exclude_incident_ids={primary.incident_id},
                    cluster_id=incoming.incident_cluster_id,
                )
                merged = self.require_incident(primary.incident_id)
            return merged, False
        created = self.create_incident(incoming)
        return created, True

    @_serialized_store_write
    def resolve_open_infrastructure_incidents_for_component(
        self,
        component: str,
        *,
        resolved_at: Optional[str] = None,
        telemetry_event_ids: Optional[List[str]] = None,
        resolution_reason: Optional[str] = None,
        exclude_incident_ids: Optional[Set[str]] = None,
        cluster_id: Optional[str] = None,
    ) -> List[IncidentCase]:
        """Resolve every open infrastructure-health incident for a component.

        Strictly excludes monitor-agent finding, runtime, drift, and deployment
        incidents.
        """
        comp = normalize_infrastructure_component(component)
        cluster_clean = str(cluster_id or "").strip()
        if not comp and not cluster_clean:
            return []
        excluded = {str(item or "").strip() for item in (exclude_incident_ids or set()) if str(item or "").strip()}
        resolved_iso = str(resolved_at or _utc_now()).strip() or _utc_now()
        reason_clean = str(resolution_reason or "").strip()
        extra_events = list(telemetry_event_ids or [])
        if reason_clean:
            extra_events.append(f"resolution_reason:{reason_clean}")

        transitions: List[Tuple[str, Mapping[str, Any], IncidentCase]] = []
        candidates = sorted(
            self._incidents.values(),
            key=lambda item: (item.created_at, item.incident_id),
        )
        for inc in candidates:
            if not inc.is_open() or not is_infrastructure_health_incident(inc):
                continue
            if inc.incident_id in excluded:
                continue
            inc_comp = extract_infrastructure_incident_component(inc)
            if not (
                (comp and inc_comp == comp)
                or (cluster_clean and str(inc.incident_cluster_id or "").strip() == cluster_clean)
            ):
                continue

            updates: Dict[str, Any] = {
                "status": IncidentStatus.RESOLVED.value,
                "resolved_at": inc.resolved_at or resolved_iso,
            }
            if extra_events:
                updates["telemetry_event_ids"] = _merge_unique(inc.telemetry_event_ids, extra_events)
            if reason_clean:
                updates["evidence_summary"] = _merge_summary(
                    inc.evidence_summary,
                    f"resolution_reason={reason_clean}",
                )
            updated = IncidentCase(**{**inc.to_dict(), **updates})
            errors = validate_incident_case(updated)
            if errors:
                raise IncidentError(f"Component recovery resolution produces invalid IncidentCase: {errors}")
            transitions.append((inc.incident_id, inc.to_dict(), updated))

        if not transitions:
            return []
        return self._persist_batch_incident_updates(transitions)

    def _orphaned_infrastructure_incident_candidates(
        self,
        *,
        healthy_components: List[str],
        referenced_incident_ids: Optional[List[str]] = None,
    ) -> List[IncidentCase]:
        healthy_set = {
            normalize_infrastructure_component(item)
            for item in (healthy_components or [])
            if normalize_infrastructure_component(item)
        }
        if not healthy_set:
            return []
        referenced_set = {
            str(item or "").strip()
            for item in (referenced_incident_ids or [])
            if str(item or "").strip()
        }
        candidates = sorted(
            self._incidents.values(),
            key=lambda item: (item.created_at, item.incident_id),
        )
        matched: List[IncidentCase] = []
        for inc in candidates:
            if not inc.is_open() or not is_infrastructure_health_incident(inc):
                continue
            comp = extract_infrastructure_incident_component(inc)
            if not comp or comp not in healthy_set:
                continue
            if extract_infrastructure_incident_referenced_ids(inc) & referenced_set:
                continue
            matched.append(inc)
        return matched

    @_serialized_store_read
    def has_orphaned_infrastructure_incidents(
        self,
        *,
        healthy_components: List[str],
        referenced_incident_ids: Optional[List[str]] = None,
    ) -> bool:
        """Return True when at least one open infrastructure incident is orphaned."""
        self._refresh_from_disk()
        return bool(
            self._orphaned_infrastructure_incident_candidates(
                healthy_components=healthy_components,
                referenced_incident_ids=referenced_incident_ids,
            )
        )

    @_serialized_store_write
    def resolve_orphaned_infrastructure_incidents(
        self,
        *,
        healthy_components: List[str],
        referenced_incident_ids: Optional[List[str]] = None,
        resolved_at: Optional[str] = None,
        telemetry_event_ids: Optional[List[str]] = None,
        resolution_reason: str = ORPHAN_RECOVERED_RESOLUTION_REASON,
    ) -> List[IncidentCase]:
        """Resolve open infrastructure incidents whose component is healthy and unreferenced by the BFF ledger.

        Applies strictly to non-trading infrastructure-health incidents and never
        touches monitor-agent finding, runtime, drift, or deployment incidents.
        """
        candidates = self._orphaned_infrastructure_incident_candidates(
            healthy_components=healthy_components,
            referenced_incident_ids=referenced_incident_ids,
        )
        if not candidates:
            return []
        resolved_iso = str(resolved_at or _utc_now()).strip() or _utc_now()
        reason_clean = str(resolution_reason or ORPHAN_RECOVERED_RESOLUTION_REASON).strip() or ORPHAN_RECOVERED_RESOLUTION_REASON
        extra_events = list(telemetry_event_ids or [])
        extra_events.append(f"resolution_reason:{reason_clean}")

        transitions: List[Tuple[str, Mapping[str, Any], IncidentCase]] = []
        for inc in candidates:
            updates: Dict[str, Any] = {
                "status": IncidentStatus.RESOLVED.value,
                "resolved_at": inc.resolved_at or resolved_iso,
                "telemetry_event_ids": _merge_unique(inc.telemetry_event_ids, extra_events),
                "evidence_summary": _merge_summary(
                    inc.evidence_summary,
                    f"resolution_reason={reason_clean}",
                ),
            }
            updated = IncidentCase(**{**inc.to_dict(), **updates})
            errors = validate_incident_case(updated)
            if errors:
                raise IncidentError(f"Orphan recovery resolution produces invalid IncidentCase: {errors}")
            transitions.append((inc.incident_id, inc.to_dict(), updated))

        if not transitions:
            return []
        return self._persist_batch_incident_updates(transitions)

    # ---- Postmortem reads ----

    @_serialized_store_read
    def get_postmortem(self, postmortem_id: str) -> Optional[Postmortem]:
        self._refresh_from_disk()
        return self._postmortem_by_id(postmortem_id)

    def require_postmortem(self, postmortem_id: str) -> Postmortem:
        pm = self.get_postmortem(postmortem_id)
        if pm is None:
            raise IncidentError(f"Postmortem not found: {postmortem_id}")
        return pm

    @_serialized_store_read
    def list_postmortems(self) -> List[Postmortem]:
        self._refresh_from_disk()
        return list(self._postmortems.values())

    @_serialized_store_read
    def list_all_postmortems(self) -> List[Postmortem]:
        """Every stored postmortem, never capped (exact-total read path)."""
        self._refresh_from_disk()
        return list(self._postmortems.values())

    @_serialized_store_read
    def find_postmortem_for_incident(self, incident_id: str) -> Optional[Postmortem]:
        self._refresh_from_disk()
        return self._postmortem_for_incident_id(incident_id)

    # ---- Postmortem writes ----

    @_serialized_store_write
    def create_postmortem(self, pm: Postmortem) -> Postmortem:
        """
        Create a Postmortem.

        The referenced IncidentCase must exist in this store before a
        Postmortem can be created.
        """
        errors = validate_postmortem(pm)
        if errors:
            raise IncidentError(f"Invalid Postmortem: {errors}")
        if self._postmortem_by_id(pm.postmortem_id) is not None:
            raise IncidentConcurrencyError(
                f"Postmortem already exists: {pm.postmortem_id}"
            )
        parent = self._incident_by_id(pm.incident_id)
        if parent is None:
            raise IncidentError(
                f"Postmortem references unknown IncidentCase: {pm.incident_id}. "
                "Create the IncidentCase first."
            )
        existing_for_incident = self._postmortem_for_incident_id(pm.incident_id)
        if existing_for_incident is not None:
            raise IncidentConcurrencyError(
                "Postmortem already exists for IncidentCase "
                f"{pm.incident_id}: {existing_for_incident.postmortem_id}"
            )
        self._validate_postmortem_against_incident(pm, parent)
        self._postmortems[pm.postmortem_id] = pm
        self._save(
            aggregate_type="postmortem",
            record_id=pm.postmortem_id,
            expected_snapshot=None,
            consistency_checks=(("incident", pm.incident_id, parent.to_dict()),),
        )
        return pm

    @_serialized_store_write
    def update_postmortem_draft(
        self,
        pm: Postmortem,
        *,
        expected_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> Postmortem:
        """Replace an existing draft Postmortem with an updated draft."""
        existing = self.require_postmortem(pm.postmortem_id)
        if expected_snapshot is not None and existing.to_dict() != dict(expected_snapshot):
            raise IncidentConcurrencyError(
                "Postmortem changed concurrently before draft update: "
                f"{pm.postmortem_id}"
            )
        if existing.status != PostmortemStatus.DRAFT.value or pm.status != PostmortemStatus.DRAFT.value:
            raise IncidentError("Only draft Postmortems can be updated by the draft worker")
        if existing.incident_id != pm.incident_id:
            raise IncidentError(
                "Postmortem draft updates cannot change the referenced IncidentCase: "
                f"existing={existing.incident_id!r}, incoming={pm.incident_id!r}"
            )
        errors = validate_postmortem(pm)
        if errors:
            raise IncidentError(f"Invalid Postmortem: {errors}")
        parent = self._incident_by_id(pm.incident_id)
        if parent is None:
            raise IncidentError(
                f"Postmortem references unknown IncidentCase: {pm.incident_id}. "
                "Create the IncidentCase first."
            )
        self._validate_postmortem_against_incident(pm, parent)
        self._postmortems[pm.postmortem_id] = pm
        self._save(
            aggregate_type="postmortem",
            record_id=pm.postmortem_id,
            expected_snapshot=existing.to_dict(),
            consistency_checks=(("incident", pm.incident_id, parent.to_dict()),),
        )
        return pm

    @_serialized_store_write
    def update_postmortem_status(
        self,
        postmortem_id: str,
        new_status: str,
        *,
        published_at: Optional[str] = None,
        published_event_id: Optional[str] = None,
        expected_snapshot: Optional[Mapping[str, Any]] = None,
        expected_incident_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> Postmortem:
        pm = self.require_postmortem(postmortem_id)
        if expected_snapshot is not None and pm.to_dict() != dict(expected_snapshot):
            raise IncidentConcurrencyError(
                "Postmortem changed concurrently before status transition: "
                f"{postmortem_id}"
            )
        incident = self.require_incident(pm.incident_id)
        if (
            expected_incident_snapshot is not None
            and incident.to_dict() != dict(expected_incident_snapshot)
        ):
            raise IncidentConcurrencyError(
                "Parent IncidentCase changed concurrently before postmortem status transition: "
                f"{pm.incident_id}"
            )
        try:
            PostmortemStatus(new_status)
        except ValueError:
            raise IncidentError(f"Invalid postmortem status: {new_status!r}")

        if pm.status == PostmortemStatus.PUBLISHED.value:
            if new_status != PostmortemStatus.PUBLISHED.value:
                raise IncidentError(
                    f"Postmortem is published and cannot transition to {new_status}"
                )
            if published_event_id and published_event_id != pm.published_event_id:
                raise IncidentError(
                    "Published Postmortem cannot be republished with a different event identity"
                )
            if published_at and published_at != pm.published_at:
                raise IncidentError(
                    "Published Postmortem cannot replace its original published_at"
                )
            return pm

        updates: Dict[str, Any] = {"status": new_status}
        if new_status == PostmortemStatus.PUBLISHED.value:
            updates["published_at"] = published_at or _utc_now()
            if published_event_id:
                updates["published_event_id"] = published_event_id

        updated = Postmortem(**{**pm.to_dict(), **updates})
        errors = validate_postmortem(updated)
        if errors:
            raise IncidentError(f"Status update produces invalid Postmortem: {errors}")
        self._postmortems[postmortem_id] = updated
        self._save(
            aggregate_type="postmortem",
            record_id=postmortem_id,
            expected_snapshot=pm.to_dict(),
            consistency_checks=(("incident", pm.incident_id, incident.to_dict()),),
        )
        return updated

    @_serialized_store_write
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
        pm = self.require_postmortem(postmortem_id)
        updated = Postmortem(**{**pm.to_dict(), "linked_evolution_decision_id": evolution_decision_id})
        self._postmortems[postmortem_id] = updated
        self._save(
            aggregate_type="postmortem",
            record_id=postmortem_id,
            expected_snapshot=pm.to_dict(),
        )
        return updated

    # ---- Persistence ----

    def _save(
        self,
        *,
        aggregate_type: str,
        record_id: str,
        expected_snapshot: Optional[Mapping[str, Any]],
        consistency_checks: tuple[tuple[str, str, Mapping[str, Any]], ...] = (),
    ) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._temp_path()
            try:
                with temporary_path.open("w", encoding="utf-8") as handle:
                    # Stream one record at a time in compact form: no whole-store
                    # dict, no indent (the pure-Python encoder path), ~half the bytes.
                    handle.write('{"incidents":[')
                    for index, record in enumerate(self._incidents.values()):
                        if index:
                            handle.write(",")
                        handle.write(json.dumps(record.to_dict(), separators=(",", ":")))
                    handle.write('],"postmortems":[')
                    for index, record in enumerate(self._postmortems.values()):
                        if index:
                            handle.write(",")
                        handle.write(json.dumps(record.to_dict(), separators=(",", ":")))
                    handle.write("]}")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self._path)
                directory_fd = os.open(self._path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._loaded_mtime_ns = self._path.stat().st_mtime_ns

    def _load(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as handle:
            try:
                data = json.load(handle)
            except json.JSONDecodeError:
                handle.seek(0)
                if not handle.read().strip():
                    return
                raise
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


def _merge_unique(*values: List[str]) -> List[str]:
    seen: Dict[str, None] = {}
    for group in values:
        for value in group or []:
            item = str(value).strip()
            if item:
                seen[item] = None
    return list(seen)


def _max_incident_severity(left: str, right: str) -> str:
    rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    return left if rank.get(left, 0) >= rank.get(right, 0) else right


def _merge_summary(existing: Optional[str], incoming: str, limit: int = MAX_EVIDENCE_SUMMARY_LENGTH) -> str:
    existing, incoming = (existing or "").strip(), (incoming or "").strip()
    if (not incoming or incoming in existing) and len(existing) <= limit:
        return existing
    marker = "[older entries dropped] "
    clean = existing[len(marker):] if existing.startswith(marker) else existing
    merged = f"{clean}; {incoming}" if clean and incoming else (incoming or clean)
    candidate = f"{marker}{merged}" if existing.startswith(marker) else merged
    if len(candidate) <= limit:
        return candidate
    max_tail = limit - len(marker)
    raw_tail = merged[-max_tail:]
    if raw_tail.startswith(" ") and len(merged) > max_tail and merged[-max_tail - 1] == ";":
        tail = raw_tail[1:]
    else:
        sep = raw_tail.find("; ")
        tail = raw_tail[sep + 2:] if sep != -1 else raw_tail
    if incoming and incoming not in tail:
        tail = incoming if len(incoming) <= max_tail else incoming[-max_tail:]
    return f"{marker}{tail}"


def _replace_single_report_summary(
    existing: Optional[str],
    incoming: Optional[str],
    limit: int = MAX_EVIDENCE_SUMMARY_LENGTH,
) -> str:
    """Keep only the latest single report summary bounded to limit (no multi-report append)."""
    incoming_clean = (incoming or "").strip()
    if incoming_clean:
        return incoming_clean[:limit]
    existing_clean = (existing or "").strip()
    marker = "[older entries dropped] "
    if existing_clean.startswith(marker):
        existing_clean = existing_clean[len(marker):].strip()
    return existing_clean[:limit]


# ---------------------------------------------------------------------------
# Read-time summary & timeline derivation (INCIDENT-HUMAN-SUMMARY-AND-OCCURRENCES-20261120)
# ---------------------------------------------------------------------------

_THRESHOLD_METRIC_KV_RE = re.compile(r"(?:^|;\s*)threshold_metric=([^;\s]+)")
_WORST_METRIC_KV_RE = re.compile(r"(?:^|;\s*)worst_metric=([^;\s]+)")
_OBSERVED_KV_RE = re.compile(r"(?:^|;\s*)observed=([^;\s]+)")
_THRESHOLD_VALUE_KV_RE = re.compile(r"(?:^|;\s*)threshold(?:_value)?=([^;\s]+)")
_COMPARATOR_KV_RE = re.compile(r"(?:^|;\s*)comparator=([^;\s]+)")
_DRIFT_REPORT_SEG_RE = re.compile(r"DriftReport\s+([A-Za-z0-9_.:-]+)(.*?)(?=(?:;\s*DriftReport\s+)|$)")
_INFRA_FAILURE_PROBE_RE = re.compile(
    r"(?:^|;\s*)(?P<probe>[A-Za-z0-9_.-]+)\s+observed\s+(?P<obs>\d+)/(?P<thresh>\d+)\s+failures"
)
_RATIONALE_KV_RE = re.compile(
    r"(?:^|;\s*)rationale=(.*?)(?=(?:;\s*(?:resolution_reason|non_trading_infrastructure_incident|"
    r"tenant_id|producer|service_name|component_kind|component|endpoint|source_event_id|"
    r"fingerprint|canonical_key|observed_at|snapshot_ref)=)|$)"
)
_PRODUCER_KV_RE = re.compile(r"(?:^|;\s*)producer=([^;\s]+)")
_SNAPSHOT_REF_KV_RE = re.compile(r"(?:^|;\s*)snapshot_ref=([^;\s]+)")


def _coerce_numeric_or_value(raw: Any) -> Any:
    if raw is None or isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    if re.fullmatch(r"[+-]?\d+", text):
        try:
            return int(text)
        except ValueError:
            pass
    try:
        val = float(text)
        if math.isfinite(val):
            return val
    except ValueError:
        pass
    return text


def _extract_observed_from_summary_text(summary_text: str) -> Any:
    if not summary_text:
        return None
    obs_matches = _OBSERVED_KV_RE.findall(summary_text)
    if obs_matches:
        return _coerce_numeric_or_value(obs_matches[-1])
    infra_matches = list(_INFRA_FAILURE_PROBE_RE.finditer(summary_text))
    if infra_matches:
        return int(infra_matches[-1].group("obs"))
    return None


def _format_metric_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        if abs(value) >= 0.01:
            formatted = f"{value:.2f}"
            if "." in formatted:
                trimmed = formatted.rstrip("0").rstrip(".")
                return trimmed if len(trimmed.split(".")[1]) >= 1 else f"{value:.2f}" if "." in trimmed else formatted
            return formatted
        return f"{value:.4g}"
    return str(value)


def _comparator_symbol(comparator: Optional[str]) -> str:
    mapping = {
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
        "eq": "=",
        "neq": "!=",
    }
    clean = str(comparator or "").strip().lower()
    return mapping.get(clean, clean or ">")


def _humanize_metric_name(metric: Optional[str]) -> str:
    text = str(metric or "").strip()
    if not text:
        return ""
    if text.startswith("drift:"):
        text = text[len("drift:"):]
    cleaned = re.sub(r"[_:\-]+", " ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _non_meta_telemetry_event_ids(raw_ids: Any) -> List[str]:
    if not isinstance(raw_ids, list):
        return []
    result: List[str] = []
    for item in raw_ids:
        text = str(item or "").strip()
        if not text:
            continue
        if text.startswith(("resolution_reason:", "merged_incident_id:", "merged_event:")):
            continue
        if text not in result:
            result.append(text)
    return result


def _latest_iso_timestamp(*values: Optional[str]) -> Optional[str]:
    best_dt: Optional[datetime] = None
    best_raw: Optional[str] = None
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        parsed = _parse_iso_utc(text)
        if parsed is not None:
            if best_dt is None or parsed >= best_dt:
                best_dt = parsed
                best_raw = text
        elif best_raw is None:
            best_raw = text
    return best_raw


def _infer_incident_kind(data: Mapping[str, Any]) -> str:
    raw_kind = str(data.get("kind") or data.get("incident_kind") or "").strip().lower()
    if raw_kind in {"drift", "threshold", "infrastructure", "agent_finding", "deployment", "runtime"}:
        return raw_kind
    incident_id = str(data.get("incident_id") or data.get("id") or "").strip()
    cluster = str(data.get("incident_cluster_id") or "").strip()
    summary = str(data.get("evidence_summary") or "")
    artifact = str(data.get("artifact_id") or "").strip()

    if (
        incident_id.startswith("agent-")
        or ":monitor-agent:agent-finding-" in cluster
        or f"component_kind={MONITOR_AGENT_COMPONENT_KIND}" in summary
        or artifact.startswith(MONITOR_AGENT_ARTIFACT_PREFIX)
    ):
        return "agent_finding"
    if (
        incident_id.startswith("inc-drift-")
        or cluster.startswith("drift:")
        or "DriftReport " in summary
    ):
        return "drift"
    if (
        incident_id.startswith("inc-threshold-")
        or data.get("threshold_identity")
        or ("threshold_metric=" in summary and not cluster.startswith("drift:"))
    ):
        return "threshold"
    if (
        cluster.startswith(INFRASTRUCTURE_CLUSTER_PREFIX)
        or "non_trading_infrastructure_incident=true" in summary
        or incident_id.startswith("infra-")
        or artifact.startswith(INFRASTRUCTURE_ARTIFACT_PREFIX)
    ):
        return "infrastructure"
    if cluster.startswith("deployment:") or incident_id.startswith("inc-deployment-"):
        return "deployment"
    return raw_kind or "incident"


def _infer_stored_occurrence_count(data: Mapping[str, Any], *, kind: Optional[str] = None) -> int:
    raw_count = data.get("occurrence_count")
    if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count >= 1:
        return raw_count
    resolved_kind = kind or _infer_incident_kind(data)
    non_meta_telemetry = _non_meta_telemetry_event_ids(data.get("telemetry_event_ids"))
    reconciliation_ids = [
        str(x).strip()
        for x in (data.get("reconciliation_ids") or [])
        if str(x or "").strip()
    ]
    summary = str(data.get("evidence_summary") or "")
    if resolved_kind == "drift":
        drift_report_ids = [r for r in reconciliation_ids if r.startswith("drift-")]
        segment_count = len(_DRIFT_REPORT_SEG_RE.findall(summary))
        return max(1, len(non_meta_telemetry), len(drift_report_ids), segment_count)
    if resolved_kind == "agent_finding":
        snap_matches = _SNAPSHOT_REF_KV_RE.findall(summary)
        return max(1, len(non_meta_telemetry), len(dict.fromkeys(snap_matches)))
    if resolved_kind == "infrastructure":
        merged_ids = [
            str(x).strip()
            for x in (data.get("telemetry_event_ids") or [])
            if str(x or "").strip().startswith("merged_incident_id:")
        ]
        summary_merged = _MERGED_INCIDENT_ID_KV_RE.findall(summary)
        all_merged = set(
            item.split(":", 1)[1].strip() for item in merged_ids if ":" in item
        ) | {m.strip() for m in summary_merged if m.strip()}
        return max(1, 1 + len(all_merged), len(non_meta_telemetry))
    return max(1, len(non_meta_telemetry))


def _snapshot_iso_from_token(token: str) -> Optional[str]:
    match = _SNAPSHOT_TS_RE.search(str(token or ""))
    if not match:
        return None
    y, m, d, hh, mm, ss = (int(g) for g in match.groups())
    try:
        dt = datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def build_incident_timeline(
    incident_or_data: IncidentCase | Mapping[str, Any],
    *,
    summary_fields: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Build chronological occurrence and lifecycle timeline entries for an incident."""
    data = (
        incident_or_data.to_dict()
        if isinstance(incident_or_data, IncidentCase)
        else dict(incident_or_data)
    )
    fields = dict(summary_fields) if summary_fields is not None else derive_incident_summary_fields(data, include_timeline=False)
    incident_id = str(data.get("incident_id") or data.get("id") or "incident").strip()
    kind = str(fields.get("kind") or "incident")
    metric = fields.get("metric")
    metric_label = _humanize_metric_name(metric) or "metric"
    observed = fields.get("observed")
    threshold = fields.get("threshold")
    comparator = fields.get("comparator")
    comp_sym = _comparator_symbol(comparator)
    occurrence_count = int(fields.get("occurrence_count") or 1)
    first_seen_at = str(fields.get("first_seen_at") or data.get("created_at") or _utc_now())
    last_seen_at = str(fields.get("last_seen_at") or first_seen_at)
    summary_text = str(data.get("evidence_summary") or "")

    non_meta_telemetry = _non_meta_telemetry_event_ids(data.get("telemetry_event_ids"))
    reconciliation_ids = [
        str(x).strip()
        for x in (data.get("reconciliation_ids") or [])
        if str(x or "").strip()
    ]

    entries: List[Dict[str, Any]] = []

    if kind == "drift":
        drift_report_ids = [r for r in reconciliation_ids if r.startswith("drift-")]
        if not drift_report_ids and reconciliation_ids:
            drift_report_ids = list(reconciliation_ids)
        seg_map: Dict[str, Dict[str, Any]] = {}
        for report_id, seg_body in _DRIFT_REPORT_SEG_RE.findall(summary_text):
            obs_m = _OBSERVED_KV_RE.findall(seg_body)
            thr_m = _THRESHOLD_VALUE_KV_RE.findall(seg_body)
            cmp_m = _COMPARATOR_KV_RE.findall(seg_body)
            seg_map[report_id] = {
                "observed": _coerce_numeric_or_value(obs_m[-1]) if obs_m else observed,
                "threshold": _coerce_numeric_or_value(thr_m[-1]) if thr_m else threshold,
                "comparator": cmp_m[-1] if cmp_m else comparator,
            }
        total = max(occurrence_count, len(non_meta_telemetry), len(drift_report_ids), 1)
        for idx in range(total):
            rep_id = drift_report_ids[idx] if idx < len(drift_report_ids) else (
                drift_report_ids[-1] if drift_report_ids else None
            )
            tel_id = non_meta_telemetry[idx] if idx < len(non_meta_telemetry) else (
                non_meta_telemetry[-1] if non_meta_telemetry else None
            )
            source_ref = rep_id or tel_id or str(data.get("trace_id") or incident_id)
            seg_info = seg_map.get(rep_id or "", {})
            entry_obs = seg_info.get("observed", observed)
            entry_thr = seg_info.get("threshold", threshold)
            entry_cmp = seg_info.get("comparator", comparator)
            entry_sym = _comparator_symbol(entry_cmp)
            ts = first_seen_at if idx == 0 else (last_seen_at if idx == total - 1 else first_seen_at)
            if entry_obs is not None and entry_thr is not None:
                note = (
                    f"Occurrence {idx + 1} of {total}: drift report {source_ref} "
                    f"observed {metric_label} {_format_metric_value(entry_obs)} "
                    f"({entry_sym} {_format_metric_value(entry_thr)})."
                )
            else:
                note = f"Occurrence {idx + 1} of {total}: drift report {source_ref} on {metric_label}."
            entry_params = {
                "occurrence_index": idx + 1,
                "occurrence_count": total,
                "source_ref": source_ref,
                "drift_report_id": rep_id,
                "telemetry_event_id": tel_id,
                "metric": metric,
                "observed": entry_obs,
                "threshold": entry_thr,
                "comparator": entry_cmp,
            }
            entries.append(
                {
                    "entry_id": f"{incident_id}:occurrence:{idx + 1}",
                    "incident_id": incident_id,
                    "ts": ts,
                    "timestamp": ts,
                    "actor": "reconciliation-drift",
                    "actor_ref": "reconciliation-drift",
                    "entry_type": "occurrence",
                    "source_ref": source_ref,
                    "evidence_ref": source_ref,
                    "summary_key": "incident.timeline.drift_occurrence",
                    "summary_params": entry_params,
                    "summary": note,
                    "note": note,
                }
            )
    elif kind == "agent_finding":
        snap_refs = list(non_meta_telemetry)
        for snap_match in _SNAPSHOT_REF_KV_RE.findall(summary_text):
            if snap_match not in snap_refs:
                snap_refs.append(snap_match)
        obs_times = [m.group(1) for m in _OBSERVED_AT_RE.finditer(summary_text)]
        rationale_matches = [m.strip() for m in _RATIONALE_KV_RE.findall(summary_text) if m.strip()]
        total = max(occurrence_count, len(snap_refs), 1)
        for idx in range(total):
            source_ref = snap_refs[idx] if idx < len(snap_refs) else (
                snap_refs[-1] if snap_refs else str(data.get("trace_id") or incident_id)
            )
            snap_ts = _snapshot_iso_from_token(source_ref)
            obs_ts = obs_times[idx] if idx < len(obs_times) else None
            ts = obs_ts or snap_ts or (first_seen_at if idx == 0 else last_seen_at)
            rat = (
                rationale_matches[idx]
                if idx < len(rationale_matches)
                else (rationale_matches[-1] if rationale_matches else str(data.get("title") or metric_label))
            )
            note = f"Occurrence {idx + 1} of {total}: {rat} (snapshot {source_ref})."
            entry_params = {
                "occurrence_index": idx + 1,
                "occurrence_count": total,
                "source_ref": source_ref,
                "snapshot_ref": source_ref,
                "metric": metric,
                "rationale": rat,
            }
            entries.append(
                {
                    "entry_id": f"{incident_id}:occurrence:{idx + 1}",
                    "incident_id": incident_id,
                    "ts": ts,
                    "timestamp": ts,
                    "actor": "monitor-agent",
                    "actor_ref": "monitor-agent",
                    "entry_type": "occurrence",
                    "source_ref": source_ref,
                    "evidence_ref": source_ref,
                    "summary_key": "incident.timeline.agent_finding_occurrence",
                    "summary_params": entry_params,
                    "summary": note,
                    "note": note,
                }
            )
    elif kind == "infrastructure":
        producer_match = _PRODUCER_KV_RE.search(summary_text)
        actor = producer_match.group(1).strip() if producer_match else "control-plane-bff"
        service_match = _SERVICE_NAME_KV_RE.search(summary_text)
        comp_name = (
            service_match.group(1).strip()
            if service_match
            else str(fields.get("summary_params", {}).get("service_name") or metric_label or "component")
        )
        event_refs = list(non_meta_telemetry)
        if not event_refs:
            event_refs = [str(data.get("trace_id") or incident_id)]
        total = max(occurrence_count, len(event_refs), 1)
        for idx in range(total):
            source_ref = event_refs[idx] if idx < len(event_refs) else event_refs[-1]
            ts = first_seen_at if idx == 0 else last_seen_at
            note = (
                f"Occurrence {idx + 1} of {total}: infrastructure health event "
                f"{source_ref} on {comp_name}."
            )
            entry_params = {
                "occurrence_index": idx + 1,
                "occurrence_count": total,
                "source_ref": source_ref,
                "service_name": comp_name,
                "metric": metric,
                "observed": observed,
                "threshold": threshold,
                "comparator": comparator,
            }
            entries.append(
                {
                    "entry_id": f"{incident_id}:occurrence:{idx + 1}",
                    "incident_id": incident_id,
                    "ts": ts,
                    "timestamp": ts,
                    "actor": actor,
                    "actor_ref": actor,
                    "entry_type": "occurrence",
                    "source_ref": source_ref,
                    "evidence_ref": source_ref,
                    "summary_key": "incident.timeline.infrastructure_occurrence",
                    "summary_params": entry_params,
                    "summary": note,
                    "note": note,
                }
            )
    else:
        actor = "telemetry-engine" if kind == "threshold" else "incident-svc"
        event_refs = list(non_meta_telemetry) or list(reconciliation_ids) or [str(data.get("trace_id") or incident_id)]
        total = max(occurrence_count, len(event_refs), 1)
        for idx in range(total):
            source_ref = event_refs[idx] if idx < len(event_refs) else event_refs[-1]
            ts = first_seen_at if idx == 0 else last_seen_at
            if observed is not None and threshold is not None and threshold != 0:
                note = (
                    f"Occurrence {idx + 1} of {total}: observed {metric_label} "
                    f"{_format_metric_value(observed)} ({comp_sym} {_format_metric_value(threshold)}) "
                    f"({source_ref})."
                )
            else:
                title_text = str(data.get("title") or f"Incident on {metric_label}").strip()
                note = f"Occurrence {idx + 1} of {total}: {title_text} ({source_ref})."
            entry_params = {
                "occurrence_index": idx + 1,
                "occurrence_count": total,
                "source_ref": source_ref,
                "metric": metric,
                "observed": observed,
                "threshold": threshold,
                "comparator": comparator,
            }
            entries.append(
                {
                    "entry_id": f"{incident_id}:occurrence:{idx + 1}",
                    "incident_id": incident_id,
                    "ts": ts,
                    "timestamp": ts,
                    "actor": actor,
                    "actor_ref": actor,
                    "entry_type": "occurrence",
                    "source_ref": source_ref,
                    "evidence_ref": source_ref,
                    "summary_key": "incident.timeline.occurrence",
                    "summary_params": entry_params,
                    "summary": note,
                    "note": note,
                }
            )

    status = str(data.get("status") or "").strip().lower()
    resolved_at = str(data.get("resolved_at") or "").strip() or None
    if status in {"resolved", "closed"} or resolved_at:
        res_ts = resolved_at or last_seen_at or first_seen_at
        reason_matches = _RESOLUTION_REASON_KV_RE.findall(summary_text)
        res_reason: Optional[str] = reason_matches[-1] if reason_matches else None
        if not res_reason:
            for item in reversed(data.get("telemetry_event_ids") or []):
                text = str(item or "").strip()
                if text.startswith("resolution_reason:"):
                    res_reason = text.split(":", 1)[1].strip() or None
                    if res_reason:
                        break
        res_note = (
            f"Incident {status or 'resolved'} ({res_reason})."
            if res_reason
            else f"Incident {status or 'resolved'}."
        )
        res_source = f"resolution_reason:{res_reason}" if res_reason else incident_id
        entries.append(
            {
                "entry_id": f"{incident_id}:resolved",
                "incident_id": incident_id,
                "ts": res_ts,
                "timestamp": res_ts,
                "actor": "incident-svc",
                "actor_ref": "incident-svc",
                "entry_type": "resolved",
                "source_ref": res_source,
                "evidence_ref": res_source,
                "summary_key": "incident.timeline.resolved",
                "summary_params": {
                    "status": status or "resolved",
                    "resolved_at": res_ts,
                    "resolution_reason": res_reason,
                },
                "summary": res_note,
                "note": res_note,
            }
        )

    return entries


def derive_incident_summary_fields(
    incident_or_data: IncidentCase | Mapping[str, Any],
    *,
    include_timeline: bool = True,
) -> Dict[str, Any]:
    """Derive localizable summary fields, occurrence counts, and timeline at read time.

    Never mutates stored rows. Works on both newly written IncidentCase records
    and legacy rows that carry an appended evidence_summary.
    """
    data = (
        incident_or_data.to_dict()
        if isinstance(incident_or_data, IncidentCase)
        else dict(incident_or_data)
    )
    kind = _infer_incident_kind(data)
    summary_text = str(data.get("evidence_summary") or "")
    cluster_id = str(data.get("incident_cluster_id") or "").strip()
    artifact_id = str(data.get("artifact_id") or "").strip()

    service_match = _SERVICE_NAME_KV_RE.search(summary_text)
    service_name: Optional[str] = service_match.group(1).strip() if service_match else None
    if not service_name and kind == "infrastructure":
        if artifact_id.startswith(INFRASTRUCTURE_ARTIFACT_PREFIX):
            service_name = normalize_infrastructure_component(
                artifact_id[len(INFRASTRUCTURE_ARTIFACT_PREFIX):]
            )
        elif cluster_id.startswith(INFRASTRUCTURE_CLUSTER_PREFIX):
            parts = cluster_id.split(":", 3)
            if len(parts) == 4:
                service_name = normalize_infrastructure_component(parts[3])

    infra_probe_matches = list(_INFRA_FAILURE_PROBE_RE.finditer(summary_text))
    infra_probe_match = infra_probe_matches[-1] if infra_probe_matches else None

    metric: Optional[str] = (
        str(data["metric"]).strip()
        if data.get("metric") not in (None, "")
        else None
    )
    if not metric:
        tm_matches = _THRESHOLD_METRIC_KV_RE.findall(summary_text)
        wm_matches = _WORST_METRIC_KV_RE.findall(summary_text)
        if tm_matches:
            metric = tm_matches[-1].strip()
        elif wm_matches:
            metric = wm_matches[-1].strip()
        elif data.get("threshold_identity"):
            try:
                parsed_ident = json.loads(str(data["threshold_identity"]))
                if isinstance(parsed_ident, list) and parsed_ident and parsed_ident[0]:
                    metric = str(parsed_ident[0]).strip()
            except Exception:
                pass
        if not metric:
            if kind == "drift" and cluster_id.startswith("drift:"):
                metric = cluster_id.split(":")[-1].strip() or "drift"
            elif kind == "agent_finding":
                ck_matches = _CANONICAL_KEY_KV_RE.findall(summary_text)
                fp_matches = _FINGERPRINT_KV_RE.findall(summary_text)
                if ck_matches:
                    metric = ck_matches[-1].strip()
                elif fp_matches:
                    metric = fp_matches[-1].strip()
                elif ":monitor-agent:agent-finding-" in cluster_id:
                    metric = cluster_id.split(":monitor-agent:agent-finding-", 1)[1].strip()
                elif artifact_id.startswith(MONITOR_AGENT_ARTIFACT_PREFIX):
                    metric = artifact_id[len(MONITOR_AGENT_ARTIFACT_PREFIX):].strip()
                else:
                    metric = "agent_finding"
            elif kind == "infrastructure":
                if infra_probe_match is not None:
                    metric = infra_probe_match.group("probe").strip()
                elif service_name:
                    metric = service_name
                else:
                    metric = "health_probe"
            elif cluster_id:
                metric = cluster_id.split(":")[-1].strip() or kind
            else:
                metric = kind

    observed: Any = data.get("observed")
    if observed is None:
        obs_matches = _OBSERVED_KV_RE.findall(summary_text)
        if obs_matches:
            observed = _coerce_numeric_or_value(obs_matches[-1])
        elif infra_probe_match is not None:
            observed = int(infra_probe_match.group("obs"))

    threshold: Any = data.get("threshold")
    if threshold is None:
        thr_matches = _THRESHOLD_VALUE_KV_RE.findall(summary_text)
        if thr_matches:
            threshold = _coerce_numeric_or_value(thr_matches[-1])
        elif infra_probe_match is not None:
            threshold = int(infra_probe_match.group("thresh"))

    comparator: Optional[str] = (
        str(data["comparator"]).strip()
        if data.get("comparator") not in (None, "")
        else None
    )
    if not comparator:
        cmp_matches = _COMPARATOR_KV_RE.findall(summary_text)
        if cmp_matches:
            comparator = cmp_matches[-1].strip()
        elif infra_probe_match is not None:
            comparator = "gte"
        elif observed is not None and threshold is not None:
            comparator = "gt"

    occurrence_count = _infer_stored_occurrence_count(data, kind=kind)
    created_at = (
        str(
            data.get("created_at")
            or data.get("opened_at")
            or data.get("triggered_at")
            or data.get("submitted_at")
            or ""
        ).strip()
        or None
    )
    first_seen_at = str(data.get("first_seen_at") or "").strip() or created_at

    observed_at_candidates: List[Optional[str]] = [
        str(data.get("last_seen_at") or "").strip() or None,
        str(data.get("updated_at") or "").strip() or None,
        first_seen_at,
        created_at,
    ]
    for match in _OBSERVED_AT_RE.finditer(summary_text):
        observed_at_candidates.append(match.group(1))
    for source in [summary_text, *(str(x or "") for x in (data.get("telemetry_event_ids") or []))]:
        snap_iso = _snapshot_iso_from_token(source)
        if snap_iso:
            observed_at_candidates.append(snap_iso)
    last_seen_at = _latest_iso_timestamp(*observed_at_candidates) or first_seen_at

    occurrence_label = "1 occurrence" if occurrence_count == 1 else f"{occurrence_count} occurrences"
    comp_sym = _comparator_symbol(comparator)
    metric_label = _humanize_metric_name(metric) or "metric"
    rationale_matches = [m.strip() for m in _RATIONALE_KV_RE.findall(summary_text) if m.strip()]
    rationale = rationale_matches[-1] if rationale_matches else None

    if kind == "drift":
        if observed is not None and threshold is not None:
            summary_key = "incident.summary.drift_threshold_breach"
            summary = (
                f"Observed {metric_label} {_format_metric_value(observed)} "
                f"({comp_sym} {_format_metric_value(threshold)}) across {occurrence_label}."
            )
        else:
            summary_key = "incident.summary.drift_detected"
            summary = f"Drift detected on {metric_label} ({occurrence_label})."
    elif kind == "threshold":
        summary_key = "incident.summary.threshold_breach"
        if observed is not None and threshold is not None:
            summary = (
                f"Observed {metric_label} {_format_metric_value(observed)} "
                f"({comp_sym} {_format_metric_value(threshold)}) across {occurrence_label}."
            )
        else:
            summary = f"Threshold breach on {metric_label} ({occurrence_label})."
    elif kind == "infrastructure":
        summary_key = "incident.summary.infrastructure_degradation"
        target_label = service_name or metric_label or "component"
        if infra_probe_match is not None or (
            observed is not None and threshold is not None and threshold != 0
        ):
            summary = (
                f"Infrastructure health check failed for {target_label}: "
                f"{_format_metric_value(observed)}/{_format_metric_value(threshold)} probe failures "
                f"({occurrence_label})."
            )
        else:
            summary = f"Infrastructure degradation on {target_label} ({occurrence_label})."
    elif kind == "agent_finding":
        summary_key = "incident.summary.agent_finding"
        clean_finding = rationale or str(data.get("title") or "").strip() or f"Monitor finding on {metric_label}"
        summary = f"{clean_finding} ({occurrence_label})."
    else:
        summary_key = "incident.summary.deployment" if kind == "deployment" else "incident.summary.generic"
        if observed is not None and threshold is not None and threshold != 0:
            summary = (
                f"Observed {metric_label} {_format_metric_value(observed)} "
                f"({comp_sym} {_format_metric_value(threshold)}) across {occurrence_label}."
            )
        else:
            title_text = str(data.get("title") or "").strip() or f"Incident on {metric_label}"
            summary = f"{title_text} ({occurrence_label})."

    binding_id = str(data.get("binding_id") or data.get("runtime_binding_id") or "").strip() or None
    deployment_stage = str(data.get("deployment_stage") or data.get("deployment_mode") or "").strip() or None
    summary_params: Dict[str, Any] = {
        "kind": kind,
        "metric": metric,
        "observed": observed,
        "threshold": threshold,
        "comparator": comparator,
        "comparator_symbol": comp_sym if comparator else None,
        "occurrence_count": occurrence_count,
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
        "binding_id": binding_id,
        "deployment_stage": deployment_stage,
    }
    if service_name:
        summary_params["service_name"] = service_name
    if rationale:
        summary_params["rationale"] = rationale

    evidence_refs: List[str] = []
    for ref in [*(data.get("reconciliation_ids") or []), *(data.get("telemetry_event_ids") or [])]:
        ref_str = str(ref or "").strip()
        if ref_str and not ref_str.startswith(("resolution_reason:", "merged_event:")) and ref_str not in evidence_refs:
            evidence_refs.append(ref_str)

    result: Dict[str, Any] = {
        "kind": kind,
        "metric": metric,
        "observed": observed,
        "threshold": threshold,
        "comparator": comparator,
        "occurrence_count": occurrence_count,
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
        "summary_key": summary_key,
        "summary_params": summary_params,
        "summary": summary,
        "evidence_refs": evidence_refs,
    }
    if include_timeline:
        result["timeline"] = build_incident_timeline(data, summary_fields=result)
    return result
