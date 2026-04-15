"""
PersonaCapitalBinding — Governance Object

A PersonaCapitalBinding formalises the governance relationship between a persona
and a capital pool. It expresses admissibility and deployment-scope authority —
NOT a deployment action.

Public API
----------
PersonaCapitalBinding      — immutable dataclass
PersonaCapitalBindingStore — in-memory store with optional JSON persistence
validate_binding()         — semantic validation helper
validate_binding_json()    — structural JSON-schema validation helper

Ownership rule
--------------
- Proposal:    Operator Console / Governance Workbench
- Validation:  Governance Plane (must reference an ApprovalDecision before activation)
- Write:       Capital Pool Plane (Binding Registry)

Single-pool runtime rule
------------------------
By default, each capital pool enforces single_runtime_enforced=True:
at most one live RuntimeBinding may be active for the pool.
Multiple personas can share a pool as `advisor` or `paper_owner`, but only one
persona can sponsor a live deployment at a time.

Multi-persona live co-existence requires upstream aggregation
(judge / committee / approved unified artifact) before deploying.

Schema
------
The canonical JSON schema lives at:
    services/control-plane/governance/persona_capital_binding.schema.json
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

class BindingRole(str, Enum):
    ADVISOR = "advisor"
    PAPER_OWNER = "paper_owner"
    LIVE_OWNER = "live_owner"


class DeploymentScope(str, Enum):
    NONE = "none"
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"

    @property
    def level(self) -> int:
        return {"none": 0, "paper": 1, "canary": 2, "live": 3}[self.value]

    def permits(self, target: "DeploymentScope") -> bool:
        """Return True if this scope is >= target scope."""
        return self.level >= target.level


class BindingStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    EXPIRED = "expired"


_ROLE_SCOPE_CEILING: Dict[str, DeploymentScope] = {
    BindingRole.ADVISOR.value: DeploymentScope.NONE,
    BindingRole.PAPER_OWNER.value: DeploymentScope.PAPER,
    BindingRole.LIVE_OWNER.value: DeploymentScope.LIVE,
}


def _parse_effective_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# PersonaCapitalBinding dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PersonaCapitalBinding:
    """
    Immutable binding record that expresses governance admissibility.

    A binding is NOT a deployment. It answers:
    - which persona is associated with which pool
    - what role it plays (advisor / paper_owner / live_owner)
    - what is the maximum deployment scope it is authorised to reach

    It does NOT answer:
    - which artifact is running
    - what the current deployment stage is

    Fields
    ------
    binding_id              : immutable unique identifier
    persona_id              : the persona being bound
    capital_pool_id         : the capital pool being bound to
    role                    : advisor | paper_owner | live_owner
    allowed_deployment_scope: upper-bound on deployment stage (none/paper/canary/live)
    status                  : pending | active | suspended | revoked | expired
    created_at              : ISO-8601 UTC creation timestamp
    mandate                 : optional free-form mandate description
    budget                  : capital budget allocated under this binding
    effective_from          : inclusive start of validity window
    effective_to            : exclusive end of validity window (None = open-ended)
    approval_decision_id    : ApprovalDecision required before activation
    updated_at              : last update timestamp
    created_by              : actor who created this binding
    metadata                : arbitrary consumer metadata
    """
    binding_id: str
    persona_id: str
    capital_pool_id: str
    role: str
    allowed_deployment_scope: str
    status: str
    created_at: str

    mandate: Optional[str] = None
    budget: Optional[float] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    approval_decision_id: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            BindingRole(self.role)
        except ValueError:
            raise PersonaCapitalBindingError(
                f"Invalid role: {self.role!r}. "
                f"Must be one of {[e.value for e in BindingRole]}."
            )
        try:
            DeploymentScope(self.allowed_deployment_scope)
        except ValueError:
            raise PersonaCapitalBindingError(
                f"Invalid allowed_deployment_scope: {self.allowed_deployment_scope!r}. "
                f"Must be one of {[e.value for e in DeploymentScope]}."
            )
        try:
            BindingStatus(self.status)
        except ValueError:
            raise PersonaCapitalBindingError(
                f"Invalid status: {self.status!r}. "
                f"Must be one of {[e.value for e in BindingStatus]}."
            )
        if self.budget is not None and self.budget < 0:
            raise PersonaCapitalBindingError("budget must be >= 0")

    @property
    def is_active(self) -> bool:
        return self.status == BindingStatus.ACTIVE.value

    def is_within_effective_window(self, at: Optional[datetime] = None) -> bool:
        """Return True when the binding is inside its validity window."""
        now = at or datetime.now(timezone.utc)
        if self.effective_from and now < _parse_effective_timestamp(self.effective_from):
            return False
        if self.effective_to and now >= _parse_effective_timestamp(self.effective_to):
            return False
        return True

    def permits_deployment_to(self, target_scope: str) -> bool:
        """
        Return True if this binding authorises deployment to the given scope.
        The binding must be active.
        """
        if not self.is_active or not self.is_within_effective_window():
            return False
        target = DeploymentScope(target_scope)
        role_ceiling = _ROLE_SCOPE_CEILING[self.role]
        if not role_ceiling.permits(target):
            return False
        self_scope = DeploymentScope(self.allowed_deployment_scope)
        return self_scope.permits(target)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != {}}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PersonaCapitalBinding":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class PersonaCapitalBindingError(ValueError):
    """Raised when binding validation or store operations fail."""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_binding(binding: PersonaCapitalBinding) -> List[str]:
    """Return a list of semantic validation errors. Empty list = valid."""
    errors: List[str] = []
    if not binding.binding_id:
        errors.append("binding_id must not be empty")
    if not binding.persona_id:
        errors.append("persona_id must not be empty")
    if not binding.capital_pool_id:
        errors.append("capital_pool_id must not be empty")
    if binding.status == BindingStatus.ACTIVE.value and not binding.approval_decision_id:
        errors.append("approval_decision_id is required before a binding can be active")
    role_ceiling = _ROLE_SCOPE_CEILING[binding.role]
    declared_scope = DeploymentScope(binding.allowed_deployment_scope)
    if not role_ceiling.permits(declared_scope):
        errors.append(
            "allowed_deployment_scope exceeds the deployment ceiling for the binding role: "
            f"role={binding.role!r} ceiling={role_ceiling.value!r} scope={binding.allowed_deployment_scope!r}"
        )
    if binding.effective_from and binding.effective_to:
        try:
            effective_from = _parse_effective_timestamp(binding.effective_from)
            effective_to = _parse_effective_timestamp(binding.effective_to)
        except ValueError as exc:
            errors.append(f"Invalid effective window timestamp: {exc}")
        else:
            if effective_to <= effective_from:
                errors.append("effective_to must be later than effective_from")
    return errors


def validate_binding_json(data: Dict[str, Any]) -> List[str]:
    """
    Validate raw dict against the JSON schema.
    Returns list of error messages; empty list means valid.
    Requires `jsonschema` to be installed; silently skips if not available.
    """
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []

    schema_path = Path(__file__).parent / "persona_capital_binding.schema.json"
    if not schema_path.exists():
        return [f"Schema file not found: {schema_path}"]

    with schema_path.open() as f:
        schema = json.load(f)

    errors = []
    validator = jsonschema.Draft7Validator(schema)
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        errors.append(f"{list(err.path)}: {err.message}")
    return errors


# ---------------------------------------------------------------------------
# PersonaCapitalBindingStore
# ---------------------------------------------------------------------------

class PersonaCapitalBindingStore:
    """
    In-memory store for PersonaCapitalBinding records with optional JSON persistence.

    Single-pool live-ownership rule
    --------------------------------
    When activating a binding with role=live_owner, the store enforces that no
    other active live_owner binding exists for the same capital pool.
    This implements the canonical rule:
        one capital pool -> one live deployment sponsor

    Multiple advisor / paper_owner bindings for the same pool are allowed.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._bindings: Dict[str, PersonaCapitalBinding] = {}
        self._path = path
        if path and path.exists():
            self._load(path)

    # ---- CRUD ----

    def create(self, binding: PersonaCapitalBinding) -> PersonaCapitalBinding:
        if binding.binding_id in self._bindings:
            raise PersonaCapitalBindingError(f"Binding already exists: {binding.binding_id}")
        errors = validate_binding(binding)
        if errors:
            raise PersonaCapitalBindingError(f"Invalid binding: {errors}")
        if binding.is_active and binding.role == BindingRole.LIVE_OWNER.value:
            self._check_single_live_owner(binding.capital_pool_id, exclude_id=None)
        self._bindings[binding.binding_id] = binding
        self._save()
        return binding

    def get(self, binding_id: str) -> Optional[PersonaCapitalBinding]:
        return self._bindings.get(binding_id)

    def require(self, binding_id: str) -> PersonaCapitalBinding:
        b = self.get(binding_id)
        if b is None:
            raise PersonaCapitalBindingError(f"Binding not found: {binding_id}")
        return b

    def list(
        self,
        persona_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        status: Optional[str] = None,
        role: Optional[str] = None,
    ) -> List[PersonaCapitalBinding]:
        bindings = list(self._bindings.values())
        if persona_id:
            bindings = [b for b in bindings if b.persona_id == persona_id]
        if capital_pool_id:
            bindings = [b for b in bindings if b.capital_pool_id == capital_pool_id]
        if status:
            bindings = [b for b in bindings if b.status == status]
        if role:
            bindings = [b for b in bindings if b.role == role]
        return bindings

    def activate(self, binding_id: str, approval_decision_id: str) -> PersonaCapitalBinding:
        """
        Transition binding to active status.

        Requires an ApprovalDecision ID. For live_owner role, enforces that no
        other active live_owner binding exists for the same pool.
        """
        binding = self.require(binding_id)
        _validate_binding_status_transition(binding.status, BindingStatus.ACTIVE.value)
        if binding.role == BindingRole.LIVE_OWNER.value:
            self._check_single_live_owner(binding.capital_pool_id, exclude_id=binding_id)
        updated = PersonaCapitalBinding(
            **{
                **binding.to_dict(),
                "status": BindingStatus.ACTIVE.value,
                "approval_decision_id": approval_decision_id,
                "updated_at": utc_now(),
            }
        )
        errors = validate_binding(updated)
        if errors:
            raise PersonaCapitalBindingError(f"Invalid binding: {errors}")
        self._bindings[binding_id] = updated
        self._save()
        return updated

    def update_status(self, binding_id: str, new_status: str) -> PersonaCapitalBinding:
        binding = self.require(binding_id)
        _validate_binding_status_transition(binding.status, new_status)
        updated = PersonaCapitalBinding(
            **{**binding.to_dict(), "status": new_status, "updated_at": utc_now()}
        )
        self._bindings[binding_id] = updated
        self._save()
        return updated

    # ---- Single-live-owner check ----

    def _check_single_live_owner(self, capital_pool_id: str, exclude_id: Optional[str]) -> None:
        """
        Raise PersonaCapitalBindingError if an active live_owner binding already
        exists for the given pool (excluding exclude_id).

        This enforces the canonical rule: one pool, one live deployment sponsor.
        """
        for b in self._bindings.values():
            if (
                b.capital_pool_id == capital_pool_id
                and b.role == BindingRole.LIVE_OWNER.value
                and b.status == BindingStatus.ACTIVE.value
                and b.binding_id != exclude_id
            ):
                raise PersonaCapitalBindingError(
                    f"Single-live-owner rule violated: pool {capital_pool_id!r} already has "
                    f"an active live_owner binding ({b.binding_id}). "
                    "Revoke or suspend it before activating a new live_owner."
                )

    def live_owner_for_pool(self, capital_pool_id: str) -> Optional[PersonaCapitalBinding]:
        """Return the active live_owner binding for a pool, if any."""
        owners = self.list(
            capital_pool_id=capital_pool_id,
            status=BindingStatus.ACTIVE.value,
            role=BindingRole.LIVE_OWNER.value,
        )
        return owners[0] if owners else None

    # ---- Deployment admissibility query ----

    def persona_may_deploy_to(
        self,
        persona_id: str,
        capital_pool_id: str,
        target_scope: str,
    ) -> bool:
        """
        Return True if the persona has an active binding for the pool that
        permits deployment to the requested scope.
        """
        bindings = self.list(persona_id=persona_id, capital_pool_id=capital_pool_id, status="active")
        return any(b.permits_deployment_to(target_scope) for b in bindings)

    # ---- Persistence ----

    def _save(self) -> None:
        if self._path:
            records = [b.to_dict() for b in self._bindings.values()]
            self._path.write_text(json.dumps(records, indent=2))

    def _load(self, path: Path) -> None:
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            raise PersonaCapitalBindingError(f"Expected JSON array in {path}")
        for record in data:
            b = PersonaCapitalBinding.from_dict(record)
            self._bindings[b.binding_id] = b


# ---------------------------------------------------------------------------
# Status transition guard
# ---------------------------------------------------------------------------

_ALLOWED_BINDING_STATUS_TRANSITIONS: Dict[str, List[str]] = {
    "pending": ["active", "revoked"],
    "active": ["suspended", "revoked", "expired"],
    "suspended": ["active", "revoked", "expired"],
    "revoked": [],
    "expired": [],
}


def _validate_binding_status_transition(current: str, target: str) -> None:
    allowed = _ALLOWED_BINDING_STATUS_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise PersonaCapitalBindingError(
            f"Invalid binding status transition: {current!r} -> {target!r}. "
            f"Allowed from {current!r}: {allowed}"
        )
