"""
Persona Registry — Persona Plane Platform Objects

Defines the three-layer persona representation for Pantheon:

  1. Persona          — registry object (static, governance truth)
  2. SessionPersona   — session object (control layer, task context)
  3. CapabilitySnapshot — effective capability set, computed at session start

Public API
----------
PersonaLifecycleState   — enum of valid persona lifecycle states
SessionType             — enum of valid session types
SessionStatus           — enum of valid session status values
Persona                 — immutable dataclass (registry object)
SessionPersona          — immutable dataclass (session object)
CapabilitySnapshot      — immutable dataclass (effective capabilities)
PersonaRegistry         — in-memory store with optional JSON persistence
PersonaSessionStore     — in-memory session store
validate_persona()      — semantic validation helper

PER-001 contract decisions
--------------------------
A. SessionPersona carries three first-class runtime-context fields
   (from Codex PER-001A review follow-up §7.1):

     runtime_binding_id  — the active RuntimeBinding at session start
     deployment_stage    — deployment_mode from that RuntimeBinding (paper/canary/live)
     capital_pool_id     — the capital pool the persona is acting against

   These are nullable for non-deployment sessions (research, consult, red_team).

B. Naming clarification (§7.5 of PER-001A mapping):
     DeploymentPlan.binding_id          ←→  the PersonaCapitalBinding reference
     RuntimeBinding.persona_capital_binding_id  ←→  the same governance edge at runtime
   SessionPersona resolves both references via runtime_binding_id + capital_pool_id.

Schema
------
Canonical JSON schemas live at:
    services/control-plane/persona/persona_registry.schema.json
    services/control-plane/persona/session_persona.schema.json
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PersonaLifecycleState(str, Enum):
    DRAFT = "draft"
    RESEARCH_ONLY = "research_only"
    CONSULTABLE = "consultable"
    PAPER_OWNER = "paper_owner"
    LIVE_OWNER = "live_owner"
    FROZEN = "frozen"
    RETIRED = "retired"

    def allows_deployment_sponsorship(self) -> bool:
        return self in {
            PersonaLifecycleState.PAPER_OWNER,
            PersonaLifecycleState.LIVE_OWNER,
        }

    def allows_new_sessions(self) -> bool:
        """Returns False only for retired personas (except audit/replay)."""
        return self != PersonaLifecycleState.RETIRED


class SessionType(str, Enum):
    INTERACTIVE = "interactive"
    TRAINER = "trainer"
    RESEARCH_TASK = "research_task"
    CONSULT = "consult"
    COMMITTEE = "committee"
    RED_TEAM = "red_team"
    BACKGROUND_JOB = "background_job"

    def requires_runtime_binding(self) -> bool:
        """True for session types that act against a live/paper deployment."""
        return self in {
            SessionType.INTERACTIVE,
            SessionType.BACKGROUND_JOB,
        }


class SessionStatus(str, Enum):
    ACTIVE = "active"
    TERMINATED = "terminated"
    DEGRADED = "degraded"
    QUARANTINED = "quarantined"
    AUDIT_REPLAY = "audit_replay"


class DeploymentStage(str, Enum):
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"
    FROZEN = "frozen"


# ---------------------------------------------------------------------------
# Lifecycle transition guard
# ---------------------------------------------------------------------------

_ALLOWED_LIFECYCLE_TRANSITIONS: Dict[str, List[str]] = {
    PersonaLifecycleState.DRAFT.value: [
        PersonaLifecycleState.RESEARCH_ONLY.value,
    ],
    PersonaLifecycleState.RESEARCH_ONLY.value: [
        PersonaLifecycleState.CONSULTABLE.value,
        PersonaLifecycleState.FROZEN.value,
    ],
    PersonaLifecycleState.CONSULTABLE.value: [
        PersonaLifecycleState.PAPER_OWNER.value,
        PersonaLifecycleState.FROZEN.value,
    ],
    PersonaLifecycleState.PAPER_OWNER.value: [
        PersonaLifecycleState.LIVE_OWNER.value,
        PersonaLifecycleState.FROZEN.value,
    ],
    PersonaLifecycleState.LIVE_OWNER.value: [
        PersonaLifecycleState.FROZEN.value,
        PersonaLifecycleState.RETIRED.value,
    ],
    PersonaLifecycleState.FROZEN.value: [
        PersonaLifecycleState.RESEARCH_ONLY.value,
        PersonaLifecycleState.RETIRED.value,
    ],
    # Terminal state
    PersonaLifecycleState.RETIRED.value: [],
}


def _validate_lifecycle_transition(current: str, target: str) -> None:
    allowed = _ALLOWED_LIFECYCLE_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise PersonaRegistryError(
            f"Lifecycle transition {current!r} → {target!r} is not allowed. "
            f"Allowed: {allowed}."
        )


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class PersonaRegistryError(ValueError):
    """Raised on persona registry validation or store operation failures."""


class PersonaSessionError(ValueError):
    """Raised on session persona validation or store operation failures."""


# ---------------------------------------------------------------------------
# Persona dataclass (registry object — static layer)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Persona:
    """
    Immutable registry persona record.

    This is the governance truth for a persona's identity, mandate, and policy
    references. It does NOT carry runtime state; that lives in SessionPersona
    and CapabilitySnapshot.

    Fields
    ------
    persona_id          : immutable unique identifier
    name                : human-readable display name
    mandate             : operating mandate (strategy scope and constraints)
    lifecycle_state     : current governance lifecycle state
    created_at          : creation timestamp (UTC ISO-8601)
    strategy_family     : optional strategy family tag
    workspace_ref       : reference to this persona's private workspace
    tool_profile_id     : reference to the persona's tool profile
    route_policy_id     : reference to the route policy document
    consult_policy_id   : reference to the consult policy document
    owner               : governance operator who owns this persona record
    status              : administrative status (active/suspended/archived)
    updated_at          : last update timestamp
    metadata            : arbitrary governance metadata
    """
    persona_id: str
    name: str
    mandate: str
    lifecycle_state: str
    created_at: str

    strategy_family: Optional[str] = None
    workspace_ref: Optional[str] = None
    tool_profile_id: Optional[str] = None
    route_policy_id: Optional[str] = None
    consult_policy_id: Optional[str] = None
    owner: Optional[str] = None
    status: str = "active"
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            PersonaLifecycleState(self.lifecycle_state)
        except ValueError:
            raise PersonaRegistryError(
                f"Invalid lifecycle_state: {self.lifecycle_state!r}. "
                f"Must be one of {[e.value for e in PersonaLifecycleState]}."
            )
        if self.status not in {"active", "suspended", "archived"}:
            raise PersonaRegistryError(
                f"Invalid status: {self.status!r}. Must be active/suspended/archived."
            )

    def to_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if v is not None and v != {}}
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Persona":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# CapabilitySnapshot dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapabilitySnapshot:
    """
    Immutable effective capability set for a session.

    Computed once at session start from:
      - persona's route_policy
      - persona's consult_policy
      - RBAC / environment restrictions

    Once computed, this snapshot does NOT change if the registry policies
    change during the session (session-bound immutable snapshot rule).

    Fields
    ------
    snapshot_id         : unique identifier for this snapshot
    persona_id          : persona this snapshot was computed for
    generated_at        : when the snapshot was computed (UTC ISO-8601)
    effective_tools     : list of tool names/IDs allowed in this session
    effective_skills    : list of skill names/IDs allowed in this session
    effective_workflows : list of workflow names/IDs allowed in this session
    restrictions        : list of explicit restrictions applied
    source_refs         : list of policy document refs used to compute this snapshot
    metadata            : arbitrary metadata
    """
    snapshot_id: str
    persona_id: str
    generated_at: str

    effective_tools: List[str] = field(default_factory=list)
    effective_skills: List[str] = field(default_factory=list)
    effective_workflows: List[str] = field(default_factory=list)
    restrictions: List[str] = field(default_factory=list)
    source_refs: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapabilitySnapshot":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


# ---------------------------------------------------------------------------
# SessionPersona dataclass (session object — control layer)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionPersona:
    """
    Immutable session persona record.

    Created when a persona is used for a concrete task. Binds persona identity
    to a task context and freezes the effective capability snapshot at that
    instant.

    PER-001 contract fields (first-class, not bundle convention):
    ------------------------------------------------------------
    runtime_binding_id  — the RuntimeBinding active at session start.
                          Required for INTERACTIVE and BACKGROUND_JOB sessions;
                          None for research/consult/red_team sessions.
    deployment_stage    — deployment_mode of the RuntimeBinding (paper/canary/live/frozen).
                          Set iff runtime_binding_id is set.
    capital_pool_id     — the capital pool the session is acting against.
                          Set iff runtime_binding_id is set.

    These three fields complete the audit chain:
      Persona → SessionPersona → RuntimeBinding → DeploymentPlan → PersonaCapitalBinding

    Fields
    ------
    session_id              : unique session identifier
    persona_id              : the persona this session belongs to
    session_type            : type of session (interactive/trainer/consult/...)
    status                  : session lifecycle status
    started_at              : session start time (UTC ISO-8601)
    capability_snapshot_id  : the CapabilitySnapshot frozen for this session
    trace_id                : distributed trace identifier for audit (required, always)
    request_id              : idempotency / correlation identifier (required, always)
    context_bundle_ref      : reference to the context bundle for this session
    task_ref                : reference to the task being executed
    runtime_binding_id      : active RuntimeBinding at session start (nullable)
    deployment_stage        : deployment stage from RuntimeBinding (nullable)
    capital_pool_id         : capital pool context (nullable)
    ended_at                : session end time (UTC ISO-8601, nullable)
    metadata                : arbitrary session metadata
    """
    session_id: str
    persona_id: str
    session_type: str
    status: str
    started_at: str
    capability_snapshot_id: str
    trace_id: str
    request_id: str

    context_bundle_ref: Optional[str] = None
    task_ref: Optional[str] = None
    runtime_binding_id: Optional[str] = None
    deployment_stage: Optional[str] = None
    capital_pool_id: Optional[str] = None
    ended_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.trace_id is None:
            raise PersonaSessionError("trace_id is required and must not be None.")
        if self.request_id is None:
            raise PersonaSessionError("request_id is required and must not be None.")
        try:
            SessionType(self.session_type)
        except ValueError:
            raise PersonaSessionError(
                f"Invalid session_type: {self.session_type!r}. "
                f"Must be one of {[e.value for e in SessionType]}."
            )
        try:
            SessionStatus(self.status)
        except ValueError:
            raise PersonaSessionError(
                f"Invalid status: {self.status!r}. "
                f"Must be one of {[e.value for e in SessionStatus]}."
            )
        if self.deployment_stage is not None:
            try:
                DeploymentStage(self.deployment_stage)
            except ValueError:
                raise PersonaSessionError(
                    f"Invalid deployment_stage: {self.deployment_stage!r}. "
                    f"Must be one of {[e.value for e in DeploymentStage]}."
                )
        # Non-blank guard: runtime-context identifier fields must not be blank when set.
        if self.runtime_binding_id is not None and not self.runtime_binding_id.strip():
            raise PersonaSessionError(
                "runtime_binding_id must not be blank when set."
            )
        if self.capital_pool_id is not None and not self.capital_pool_id.strip():
            raise PersonaSessionError(
                "capital_pool_id must not be blank when set."
            )
        # Symmetric consistency rule for runtime-context fields:
        # runtime_binding_id, deployment_stage, and capital_pool_id are co-dependent:
        # all three must be set together, or all absent.
        has_binding = self.runtime_binding_id is not None
        has_stage = self.deployment_stage is not None
        has_pool = self.capital_pool_id is not None

        if has_binding and not (has_stage and has_pool):
            raise PersonaSessionError(
                "When runtime_binding_id is set, both deployment_stage and "
                "capital_pool_id must also be set."
            )
        if not has_binding and (has_stage or has_pool):
            raise PersonaSessionError(
                "deployment_stage and capital_pool_id may only be set when "
                "runtime_binding_id is also set."
            )
        if has_stage != has_pool:
            raise PersonaSessionError(
                "deployment_stage and capital_pool_id must both be set or both absent."
            )

    def to_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if v is not None and v != {}}
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionPersona":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_persona(persona: Persona) -> List[str]:
    """Return list of semantic validation errors; empty means valid."""
    errors: List[str] = []
    if not persona.persona_id.strip():
        errors.append("persona_id must not be blank.")
    if not persona.mandate.strip():
        errors.append("mandate must not be blank.")
    if not persona.created_at:
        errors.append("created_at is required.")
    return errors


def validate_session(session: SessionPersona) -> List[str]:
    """Return list of semantic validation errors; empty means valid."""
    errors: List[str] = []
    if not session.session_id.strip():
        errors.append("session_id must not be blank.")
    if not session.persona_id.strip():
        errors.append("persona_id must not be blank.")
    if not session.capability_snapshot_id.strip():
        errors.append("capability_snapshot_id must not be blank.")
    if not session.trace_id.strip():
        errors.append("trace_id must not be blank.")
    if not session.request_id.strip():
        errors.append("request_id must not be blank.")
    stype = SessionType(session.session_type)
    if stype.requires_runtime_binding() and (
        session.runtime_binding_id is None or not session.runtime_binding_id.strip()
    ):
        errors.append(
            f"session_type={session.session_type!r} requires a non-blank "
            f"runtime_binding_id (deployment-bound session)."
        )
    if session.runtime_binding_id is not None and not session.runtime_binding_id.strip():
        errors.append("runtime_binding_id must not be blank when set.")
    if session.capital_pool_id is not None and not session.capital_pool_id.strip():
        errors.append("capital_pool_id must not be blank when set.")
    return errors


# ---------------------------------------------------------------------------
# PersonaRegistry — store for Persona objects
# ---------------------------------------------------------------------------

class PersonaRegistry:
    """
    In-memory persona registry with optional JSON persistence.

    Lifecycle transitions are validated; only Governance Plane may advance
    state beyond research_only.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._store: Dict[str, Persona] = {}
        if path is not None and path.exists():
            self._load(path)
        self._path = path

    def create(self, persona: Persona) -> Persona:
        if persona.persona_id in self._store:
            raise PersonaRegistryError(
                f"Persona {persona.persona_id!r} already exists."
            )
        errors = validate_persona(persona)
        if errors:
            raise PersonaRegistryError(f"Persona validation failed: {errors}")
        self._store[persona.persona_id] = persona
        self._save()
        return persona

    def get(self, persona_id: str) -> Optional[Persona]:
        return self._store.get(persona_id)

    def require(self, persona_id: str) -> Persona:
        p = self.get(persona_id)
        if p is None:
            raise PersonaRegistryError(f"Persona not found: {persona_id!r}")
        return p

    def list(
        self,
        lifecycle_state: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Persona]:
        result = list(self._store.values())
        if lifecycle_state is not None:
            result = [p for p in result if p.lifecycle_state == lifecycle_state]
        if status is not None:
            result = [p for p in result if p.status == status]
        return result

    def advance_lifecycle(
        self, persona_id: str, target_state: str, updated_at: Optional[str] = None
    ) -> Persona:
        """Advance persona lifecycle state with transition validation."""
        persona = self.require(persona_id)
        _validate_lifecycle_transition(persona.lifecycle_state, target_state)
        updated = Persona(
            **{
                **persona.to_dict(),
                "lifecycle_state": target_state,
                "updated_at": updated_at or utc_now(),
            }
        )
        self._store[persona_id] = updated
        self._save()
        return updated

    def update_status(self, persona_id: str, new_status: str) -> Persona:
        persona = self.require(persona_id)
        if new_status not in {"active", "suspended", "archived"}:
            raise PersonaRegistryError(f"Invalid status: {new_status!r}")
        updated = Persona(
            **{**persona.to_dict(), "status": new_status, "updated_at": utc_now()}
        )
        self._store[persona_id] = updated
        self._save()
        return updated

    def _save(self) -> None:
        if self._path is None:
            return
        data = {pid: p.to_dict() for pid, p in self._store.items()}
        self._path.write_text(json.dumps(data, indent=2))

    def _load(self, path: Path) -> None:
        raw = json.loads(path.read_text())
        for pid, d in raw.items():
            self._store[pid] = Persona.from_dict(d)


# ---------------------------------------------------------------------------
# PersonaSessionStore — store for SessionPersona objects
# ---------------------------------------------------------------------------

class PersonaSessionStore:
    """
    In-memory session store with optional JSON persistence.

    Sessions are append-only; once created they may only be updated via
    explicit status transition methods.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._store: Dict[str, SessionPersona] = {}
        if path is not None and path.exists():
            self._load(path)
        self._path = path

    def create(self, session: SessionPersona) -> SessionPersona:
        if session.session_id in self._store:
            raise PersonaSessionError(
                f"Session {session.session_id!r} already exists."
            )
        errors = validate_session(session)
        if errors:
            raise PersonaSessionError(f"Session validation failed: {errors}")
        self._store[session.session_id] = session
        self._save()
        return session

    def get(self, session_id: str) -> Optional[SessionPersona]:
        return self._store.get(session_id)

    def require(self, session_id: str) -> SessionPersona:
        s = self.get(session_id)
        if s is None:
            raise PersonaSessionError(f"Session not found: {session_id!r}")
        return s

    def list(
        self,
        persona_id: Optional[str] = None,
        status: Optional[str] = None,
        session_type: Optional[str] = None,
    ) -> List[SessionPersona]:
        result = list(self._store.values())
        if persona_id is not None:
            result = [s for s in result if s.persona_id == persona_id]
        if status is not None:
            result = [s for s in result if s.status == status]
        if session_type is not None:
            result = [s for s in result if s.session_type == session_type]
        return result

    def terminate(self, session_id: str, ended_at: Optional[str] = None) -> SessionPersona:
        """Transition session to terminated status."""
        session = self.require(session_id)
        if session.status not in {SessionStatus.ACTIVE.value}:
            raise PersonaSessionError(
                f"Cannot terminate session with status {session.status!r}. "
                f"Session must be active."
            )
        updated_fields = {**session.to_dict(), "status": "terminated"}
        if ended_at:
            updated_fields["ended_at"] = ended_at
        else:
            updated_fields["ended_at"] = utc_now()
        updated = SessionPersona.from_dict(updated_fields)
        self._store[session_id] = updated
        self._save()
        return updated

    def mark_degraded(self, session_id: str) -> SessionPersona:
        session = self.require(session_id)
        updated = SessionPersona.from_dict(
            {**session.to_dict(), "status": SessionStatus.DEGRADED.value}
        )
        self._store[session_id] = updated
        self._save()
        return updated

    def activate(self, session_id: str) -> SessionPersona:
        session = self.require(session_id)
        updated = SessionPersona.from_dict(
            {**session.to_dict(), "status": SessionStatus.ACTIVE.value}
        )
        self._store[session_id] = updated
        self._save()
        return updated

    def quarantine(self, session_id: str) -> SessionPersona:
        session = self.require(session_id)
        updated = SessionPersona.from_dict(
            {**session.to_dict(), "status": SessionStatus.QUARANTINED.value}
        )
        self._store[session_id] = updated
        self._save()
        return updated

    def _save(self) -> None:
        if self._path is None:
            return
        data = {sid: s.to_dict() for sid, s in self._store.items()}
        self._path.write_text(json.dumps(data, indent=2))

    def _load(self, path: Path) -> None:
        raw = json.loads(path.read_text())
        for sid, d in raw.items():
            self._store[sid] = SessionPersona.from_dict(d)
