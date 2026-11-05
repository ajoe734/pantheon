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
import os
import re
import tempfile
import threading
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Set, Tuple

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
            incidents_before = deepcopy(self._incidents)
            postmortems_before = deepcopy(self._postmortems)
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
    created_dt = _parse_iso_utc(inc.created_at)
    if created_dt is not None:
        candidates.append(created_dt)

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
                if self._path.exists():
                    self._incidents.clear()
                    self._postmortems.clear()
                    self._load(self._path)
                    self._loaded_mtime_ns = self._path.stat().st_mtime_ns
                yield
            finally:
                self._write_guard_depth = 0
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _refresh_from_disk(self) -> None:
        if not self._path or not self._path.exists():
            return
        mtime_ns = self._path.stat().st_mtime_ns
        if self._loaded_mtime_ns == mtime_ns:
            return
        self._load(self._path)
        self._loaded_mtime_ns = mtime_ns

    # ---- IncidentCase reads ----

    @_serialized_store_read
    def get_incident(self, incident_id: str) -> Optional[IncidentCase]:
        self._refresh_from_disk()
        return self._incidents.get(incident_id)

    def require_incident(self, incident_id: str) -> IncidentCase:
        inc = self.get_incident(incident_id)
        if inc is None:
            raise IncidentError(f"IncidentCase not found: {incident_id}")
        return inc

    @_serialized_store_read
    def list_incidents(self) -> List[IncidentCase]:
        self._refresh_from_disk()
        return list(self._incidents.values())

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
        direct = self._incidents.get(target)
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
        if inc.incident_id in self._incidents:
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
        """Merge additional evidence into an existing open IncidentCase."""
        existing = self.require_incident(incident_id)
        updates: Dict[str, Any] = {
            "telemetry_event_ids": _merge_unique(
                existing.telemetry_event_ids,
                incoming.telemetry_event_ids,
            ),
            "reconciliation_ids": _merge_unique(
                existing.reconciliation_ids,
                incoming.reconciliation_ids,
            ),
            "severity": _max_incident_severity(existing.severity, incoming.severity),
        }
        if not existing.incident_cluster_id and incoming.incident_cluster_id:
            updates["incident_cluster_id"] = incoming.incident_cluster_id
        if incoming.evidence_summary or (
            existing.evidence_summary and len(existing.evidence_summary) > MAX_EVIDENCE_SUMMARY_LENGTH
        ):
            updates["evidence_summary"] = _merge_summary(existing.evidence_summary, incoming.evidence_summary or "")

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
        if incoming.evidence_summary:
            extra_summary_parts.append(incoming.evidence_summary)

        updates: Dict[str, Any] = {
            "telemetry_event_ids": _merge_unique(
                existing.telemetry_event_ids,
                extra_telemetry,
            ),
            "reconciliation_ids": _merge_unique(
                existing.reconciliation_ids,
                incoming.reconciliation_ids,
            ),
            "severity": _max_incident_severity(existing.severity, incoming.severity),
        }
        if not existing.incident_cluster_id and incoming.incident_cluster_id:
            updates["incident_cluster_id"] = incoming.incident_cluster_id
        incoming_summary = "; ".join(part for part in extra_summary_parts if part)
        if incoming_summary or (
            existing.evidence_summary and len(existing.evidence_summary) > MAX_EVIDENCE_SUMMARY_LENGTH
        ):
            updates["evidence_summary"] = _merge_summary(existing.evidence_summary, incoming_summary)

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
        return self._postmortems.get(postmortem_id)

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
    def find_postmortem_for_incident(self, incident_id: str) -> Optional[Postmortem]:
        self._refresh_from_disk()
        matches = [p for p in self._postmortems.values() if p.incident_id == incident_id]
        return matches[0] if matches else None

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
        if pm.postmortem_id in self._postmortems:
            raise IncidentConcurrencyError(
                f"Postmortem already exists: {pm.postmortem_id}"
            )
        if pm.incident_id not in self._incidents:
            raise IncidentError(
                f"Postmortem references unknown IncidentCase: {pm.incident_id}. "
                "Create the IncidentCase first."
            )
        parent = self._incidents[pm.incident_id]
        existing_for_incident = next(
            (
                candidate
                for candidate in self._postmortems.values()
                if candidate.incident_id == pm.incident_id
            ),
            None,
        )
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
        if pm.incident_id not in self._incidents:
            raise IncidentError(
                f"Postmortem references unknown IncidentCase: {pm.incident_id}. "
                "Create the IncidentCase first."
            )
        parent = self._incidents[pm.incident_id]
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
            data = {
                "incidents": [i.to_dict() for i in self._incidents.values()],
                "postmortems": [p.to_dict() for p in self._postmortems.values()],
            }
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            )
            temporary_path = Path(handle.name)
            try:
                with handle:
                    json.dump(data, handle, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self._path)
                directory_fd = os.open(self._path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if temporary_path.exists():
                    temporary_path.unlink()
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
