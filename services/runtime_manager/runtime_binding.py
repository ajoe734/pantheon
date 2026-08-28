"""
RuntimeBinding — Execution Plane Platform Object

A RuntimeBinding records the canonical state of a LEAN runtime instance that is
actively executing (or historically executed) a governed artifact for a capital
pool.  It is the single source of truth for "what is actually running".

Public API
----------
RuntimeBinding          — immutable dataclass
RuntimeBindingStore     — write-guarded store (Execution Plane only)
validate_binding()      — semantic validation helper

Write authority
---------------
Only the Runtime Manager (Execution Plane) may write RuntimeBinding records.
Every write MUST be backed by a DeploymentPlan in `approved` or `executing`
status.  No write is permitted without a valid PersonaCapitalBinding reference.

Object references (RUN-001 acceptance criteria)
-----------------------------------------------
Each RuntimeBinding MUST carry three cross-object references:
  plan_id                    — the DeploymentPlan that triggered this binding
  persona_capital_binding_id — the PersonaCapitalBinding that authorises the
                               deployment scope (governance admissibility proof)
  deployment_mode            — the actual execution stage (paper / canary / live
                               / frozen); equivalent to "stage" in L1 docs
  execution_mode             — the runtime execution mode emitted to runtime and
                               telemetry surfaces; must equal deployment_mode

Single-runtime rule
-------------------
When the backing CapitalPool has single_runtime_enforced=True (the default),
RuntimeBindingStore will refuse to create a new `active` binding for a pool
that already has one.  The existing binding must be retired first.

Schema
------
The canonical JSON schema lives at:
    services/execution/runtime-manager/runtime_binding.schema.json
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

class DeploymentMode(str, Enum):
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"
    FROZEN = "frozen"


class RuntimeBindingStatus(str, Enum):
    ACTIVE = "active"
    RETIRED = "retired"
    FAILED = "failed"
    PENDING_PAUSE = "pending_pause"
    PAUSED = "paused"


class RollbackActionType(str, Enum):
    REPLACE = "replace"
    PAUSE_THEN_REPLACE = "pause_then_replace"
    LIQUIDATE_THEN_REPLACE = "liquidate_then_replace"


# ---------------------------------------------------------------------------
# Status transition guard
# ---------------------------------------------------------------------------

_ALLOWED_STATUS_TRANSITIONS: Dict[str, List[str]] = {
    RuntimeBindingStatus.ACTIVE.value: [
        RuntimeBindingStatus.RETIRED.value,
        RuntimeBindingStatus.FAILED.value,
        RuntimeBindingStatus.PENDING_PAUSE.value,
    ],
    RuntimeBindingStatus.PENDING_PAUSE.value: [
        RuntimeBindingStatus.PAUSED.value,
        RuntimeBindingStatus.FAILED.value,
    ],
    RuntimeBindingStatus.PAUSED.value: [
        RuntimeBindingStatus.ACTIVE.value,
        RuntimeBindingStatus.RETIRED.value,
        RuntimeBindingStatus.FAILED.value,
    ],
    # Terminal states — no further transitions allowed
    RuntimeBindingStatus.RETIRED.value: [],
    RuntimeBindingStatus.FAILED.value: [],
}


# ---------------------------------------------------------------------------
# RuntimeBinding dataclass
# ---------------------------------------------------------------------------

class RuntimeBindingError(ValueError):
    """Raised when RuntimeBinding validation or store operations fail."""


@dataclass(frozen=True)
class RuntimeBinding:
    """
    Immutable runtime binding record.

    Fields
    ------
    binding_id                  : unique identifier for this binding instance
    runtime_id                  : the LEAN instance / container / worker process
    capital_pool_id             : the capital pool this binding services
    artifact_id                 : the approved artifact being executed
    artifact_version            : the specific artifact version (semver)
    deployment_mode             : actual execution stage — paper / canary / live / frozen
    execution_mode              : runtime execution mode — paper / canary / live / frozen;
                                  defaults to deployment_mode for legacy records
    effective_at                : when this binding became active (UTC ISO-8601)
    status                      : operational status
    plan_id                     : DeploymentPlan that triggered this binding (required)
    persona_capital_binding_id  : PersonaCapitalBinding that authorises the
                                  deployment scope (required — governance proof)
    retired_at                  : when this binding was replaced or terminated
    rollback_parent             : binding_id of the binding replaced by rollback
    rollback_action_type        : rollback strategy used (if this is a rollback)
    metadata                    : arbitrary execution-plane metadata
    """
    binding_id: str
    runtime_id: str
    capital_pool_id: str
    artifact_id: str
    artifact_version: str
    deployment_mode: str
    effective_at: str
    status: str
    plan_id: str
    persona_capital_binding_id: str

    execution_mode: Optional[str] = None
    retired_at: Optional[str] = None
    rollback_parent: Optional[str] = None
    rollback_action_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            deployment_mode = DeploymentMode(self.deployment_mode).value
        except ValueError:
            raise RuntimeBindingError(
                f"Invalid deployment_mode: {self.deployment_mode!r}. "
                f"Must be one of {[e.value for e in DeploymentMode]}."
            )
        object.__setattr__(self, "deployment_mode", deployment_mode)

        raw_execution_mode = self.execution_mode if self.execution_mode not in (None, "") else deployment_mode
        try:
            execution_mode = DeploymentMode(raw_execution_mode).value
        except ValueError:
            raise RuntimeBindingError(
                f"Invalid execution_mode: {raw_execution_mode!r}. "
                f"Must be one of {[e.value for e in DeploymentMode]}."
            )
        if execution_mode != deployment_mode:
            raise RuntimeBindingError(
                f"execution_mode {execution_mode!r} must equal deployment_mode {deployment_mode!r}."
            )
        object.__setattr__(self, "execution_mode", execution_mode)

        try:
            RuntimeBindingStatus(self.status)
        except ValueError:
            raise RuntimeBindingError(
                f"Invalid status: {self.status!r}. "
                f"Must be one of {[e.value for e in RuntimeBindingStatus]}."
            )
        if self.rollback_action_type is not None:
            try:
                RollbackActionType(self.rollback_action_type)
            except ValueError:
                raise RuntimeBindingError(
                    f"Invalid rollback_action_type: {self.rollback_action_type!r}. "
                    f"Must be one of {[e.value for e in RollbackActionType]}."
                )

    def is_terminal(self) -> bool:
        """Return True if this binding has reached a terminal state."""
        return self.status in {
            RuntimeBindingStatus.RETIRED.value,
            RuntimeBindingStatus.FAILED.value,
        }

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != {}}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeBinding":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, payload: str) -> "RuntimeBinding":
        return cls.from_dict(json.loads(payload))


# ---------------------------------------------------------------------------
# Semantic validation
# ---------------------------------------------------------------------------

def validate_binding(binding: RuntimeBinding) -> List[str]:
    """Return a list of semantic validation errors.  Empty list = valid."""
    errors: List[str] = []

    if not binding.binding_id:
        errors.append("binding_id must not be empty")
    if not binding.runtime_id:
        errors.append("runtime_id must not be empty")
    if not binding.capital_pool_id:
        errors.append("capital_pool_id must not be empty")
    if not binding.artifact_id:
        errors.append("artifact_id must not be empty")
    if not binding.artifact_version:
        errors.append("artifact_version must not be empty")
    if not binding.execution_mode:
        errors.append("execution_mode must not be empty")
    elif binding.execution_mode != binding.deployment_mode:
        errors.append("execution_mode must equal deployment_mode")
    if not binding.effective_at:
        errors.append("effective_at must not be empty")
    if not binding.plan_id:
        errors.append("plan_id must not be empty — a DeploymentPlan reference is required")
    if not binding.persona_capital_binding_id:
        errors.append(
            "persona_capital_binding_id must not be empty — "
            "a PersonaCapitalBinding reference is required for governance admissibility proof"
        )

    if binding.is_terminal() and not binding.retired_at:
        errors.append(
            f"retired_at is required when status is {binding.status!r}"
        )

    if binding.rollback_parent and not binding.rollback_action_type:
        errors.append("rollback_action_type is required when rollback_parent is set")

    return errors


# ---------------------------------------------------------------------------
# RuntimeBindingStore
# ---------------------------------------------------------------------------

class RuntimeBindingStore:
    """
    Write-guarded store for RuntimeBinding records.

    Write authority
    ---------------
    Only the Runtime Manager (Execution Plane) is authorised to call write
    methods on this store.  All writes must be backed by an approved
    DeploymentPlan and a valid PersonaCapitalBinding.

    Single-runtime rule
    -------------------
    When `single_runtime_enforced=True` (passed at create time from the
    backing CapitalPool), `create()` will raise if the pool already has an
    active binding. Runtime-manager-owned replacements use `cutover()` to
    persist source retirement and replacement creation in one snapshot.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._bindings: Dict[str, RuntimeBinding] = {}
        self._path = path
        self._backup_path = self._backup_for(path) if path else None
        if path and path.exists():
            self._load(path)
        elif path:
            self._restore_from_backup()

    # ---- Read helpers ----

    def get(self, binding_id: str) -> Optional[RuntimeBinding]:
        return self._bindings.get(binding_id)

    def require(self, binding_id: str) -> RuntimeBinding:
        b = self.get(binding_id)
        if b is None:
            raise RuntimeBindingError(f"RuntimeBinding not found: {binding_id}")
        return b

    def list_all(self) -> List[RuntimeBinding]:
        return list(self._bindings.values())

    def find_by_pool(self, capital_pool_id: str) -> List[RuntimeBinding]:
        return [b for b in self._bindings.values() if b.capital_pool_id == capital_pool_id]

    def get_active_for_pool(self, capital_pool_id: str) -> Optional[RuntimeBinding]:
        """Return the single active binding for a pool, or None."""
        active = [
            b for b in self._bindings.values()
            if b.capital_pool_id == capital_pool_id
            and b.status == RuntimeBindingStatus.ACTIVE.value
        ]
        if len(active) > 1:
            raise RuntimeBindingError(
                f"Invariant violation: multiple active bindings for pool {capital_pool_id!r}. "
                "This indicates a broken single-runtime enforcement."
            )
        return active[0] if active else None

    def find_by_plan(self, plan_id: str) -> List[RuntimeBinding]:
        return [b for b in self._bindings.values() if b.plan_id == plan_id]

    # ---- Write operations (Execution Plane only) ----

    def create(
        self,
        binding: RuntimeBinding,
        *,
        single_runtime_enforced: bool = True,
    ) -> RuntimeBinding:
        """
        Create a new RuntimeBinding.

        Parameters
        ----------
        binding                 : the RuntimeBinding to create
        single_runtime_enforced : when True, raise if the pool already has an
                                  active binding (mirrors CapitalPool.single_runtime_enforced)

        Raises
        ------
        RuntimeBindingError : if validation fails or the single-runtime rule is violated
        """
        errors = validate_binding(binding)
        if errors:
            raise RuntimeBindingError(f"Invalid RuntimeBinding: {errors}")

        if binding.binding_id in self._bindings:
            raise RuntimeBindingError(f"RuntimeBinding already exists: {binding.binding_id}")

        if single_runtime_enforced and binding.status == RuntimeBindingStatus.ACTIVE.value:
            self._check_single_active_binding(binding.capital_pool_id)

        self._bindings[binding.binding_id] = binding
        try:
            self._save()
        except Exception:
            self._bindings.pop(binding.binding_id, None)
            raise
        return binding

    def transition_status(
        self,
        binding_id: str,
        new_status: str,
        *,
        retired_at: Optional[str] = None,
        metadata_patch: Optional[Dict[str, Any]] = None,
    ) -> RuntimeBinding:
        """
        Transition a binding to a new status following the allowed state machine.

        Terminal bindings (retired / failed) cannot be transitioned further.
        Sets `retired_at` automatically when transitioning to a terminal state
        if not provided by the caller.
        If `metadata_patch` is provided, it is merged atomically into metadata.
        """
        binding = self.require(binding_id)

        if binding.is_terminal():
            raise RuntimeBindingError(
                f"RuntimeBinding {binding_id!r} is in terminal state {binding.status!r} "
                "and cannot be transitioned."
            )

        allowed = _ALLOWED_STATUS_TRANSITIONS.get(binding.status, [])
        if new_status not in allowed:
            raise RuntimeBindingError(
                f"Invalid status transition: {binding.status!r} -> {new_status!r}. "
                f"Allowed from {binding.status!r}: {allowed}"
            )

        updates: Dict[str, Any] = {"status": new_status}
        target_status = RuntimeBindingStatus(new_status)
        if target_status in {RuntimeBindingStatus.RETIRED, RuntimeBindingStatus.FAILED}:
            updates["retired_at"] = retired_at or utc_now()

        if metadata_patch:
            current_metadata = dict(binding.metadata)
            for k, v in metadata_patch.items():
                if isinstance(v, dict) and isinstance(current_metadata.get(k), dict):
                    merged = dict(current_metadata[k])
                    merged.update(v)
                    current_metadata[k] = merged
                else:
                    current_metadata[k] = v
            updates["metadata"] = current_metadata

        updated = RuntimeBinding(
            **{**binding.to_dict(), **updates}
        )
        self._bindings[binding_id] = updated
        try:
            self._save()
        except Exception:
            self._bindings[binding_id] = binding
            raise
        return updated

    def patch_metadata(
        self,
        binding_id: str,
        metadata_patch: Dict[str, Any],
    ) -> RuntimeBinding:
        """Merge metadata into an existing binding."""
        binding = self.require(binding_id)
        current_metadata = dict(binding.metadata)
        for k, v in metadata_patch.items():
            if isinstance(v, dict) and isinstance(current_metadata.get(k), dict):
                merged = dict(current_metadata[k])
                merged.update(v)
                current_metadata[k] = merged
            else:
                current_metadata[k] = v
        updated = RuntimeBinding(
            **{**binding.to_dict(), "metadata": current_metadata}
        )
        self._bindings[binding_id] = updated
        try:
            self._save()
        except Exception:
            self._bindings[binding_id] = binding
            raise
        return updated

    def retire(self, binding_id: str, retired_at: Optional[str] = None) -> RuntimeBinding:
        """Retire a binding — convenience wrapper over transition_status."""
        return self.transition_status(
            binding_id,
            RuntimeBindingStatus.RETIRED.value,
            retired_at=retired_at,
        )

    def cutover(
        self,
        source_binding_id: str,
        replacement: RuntimeBinding,
        *,
        retired_at: Optional[str] = None,
        single_runtime_enforced: bool = True,
    ) -> tuple[RuntimeBinding, RuntimeBinding]:
        """Atomically create a replacement and retire its source.

        Both records are persisted in one filesystem snapshot.  A process
        crash therefore exposes either the complete pre-cutover state or the
        complete post-cutover state, never a child-active/source-active gap.
        """
        source = self.require(source_binding_id)
        if source.is_terminal():
            raise RuntimeBindingError(
                f"Cannot cut over terminal RuntimeBinding {source_binding_id!r}."
            )
        if RuntimeBindingStatus.RETIRED.value not in _ALLOWED_STATUS_TRANSITIONS.get(
            source.status, []
        ):
            raise RuntimeBindingError(
                "Atomic cutover cannot bypass the source status machine: "
                f"{source.status!r} -> {RuntimeBindingStatus.RETIRED.value!r}."
            )
        errors = validate_binding(replacement)
        if errors:
            raise RuntimeBindingError(
                f"Invalid replacement RuntimeBinding: {errors}"
            )
        if replacement.binding_id in self._bindings:
            raise RuntimeBindingError(
                f"RuntimeBinding already exists: {replacement.binding_id}"
            )
        if replacement.capital_pool_id != source.capital_pool_id:
            raise RuntimeBindingError(
                "Atomic cutover replacement must remain in the source capital pool."
            )
        if single_runtime_enforced and replacement.status == RuntimeBindingStatus.ACTIVE.value:
            conflicting = [
                binding
                for binding in self._bindings.values()
                if binding.capital_pool_id == source.capital_pool_id
                and binding.status == RuntimeBindingStatus.ACTIVE.value
                and binding.binding_id != source_binding_id
            ]
            if conflicting:
                raise RuntimeBindingError(
                    "Atomic cutover found another active RuntimeBinding for pool "
                    f"{source.capital_pool_id!r}: "
                    f"{[binding.binding_id for binding in conflicting]!r}."
                )

        cutover_at = retired_at or utc_now()
        retired_source = RuntimeBinding(
            **{
                **source.to_dict(),
                "status": RuntimeBindingStatus.RETIRED.value,
                "retired_at": cutover_at,
            }
        )
        previous = self._bindings
        committed = dict(previous)
        committed[source_binding_id] = retired_source
        committed[replacement.binding_id] = replacement
        self._bindings = committed
        try:
            self._save()
        except Exception:
            self._bindings = previous
            raise
        return retired_source, replacement

    # ---- Single-runtime guard ----

    def _check_single_active_binding(self, capital_pool_id: str) -> None:
        existing = self.get_active_for_pool(capital_pool_id)
        if existing is not None:
            raise RuntimeBindingError(
                f"Single-runtime rule violation: capital pool {capital_pool_id!r} "
                f"already has an active RuntimeBinding ({existing.binding_id!r}). "
                "Retire the existing binding before creating a new one."
            )

    # ---- Persistence ----

    def _save(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            records = [b.to_dict() for b in self._bindings.values()]
            # Write the recovery copy first and the canonical snapshot last.
            # If the final atomic replace fails, callers can safely restore
            # their in-memory draft; no exception can occur after the canonical
            # file has committed.
            if records and self._backup_path:
                self._write_records(self._backup_path, records)
            self._write_records(self._path, records)

    def _load(self, path: Path) -> None:
        records = self._read_records(path)
        if not records and self._restore_from_backup():
            return
        for b in records:
            self._bindings[b.binding_id] = b
        if records and path == self._path and self._backup_path:
            self._write_records(self._backup_path, [b.to_dict() for b in records])

    @staticmethod
    def _backup_for(path: Path) -> Path:
        return path.with_name(f"{path.name}.bak")

    @staticmethod
    def _read_records(path: Path) -> List[RuntimeBinding]:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        data = json.loads(text)
        if not isinstance(data, list):
            raise RuntimeBindingError(f"Expected JSON array in {path}")
        return [RuntimeBinding.from_dict(record) for record in data]

    @staticmethod
    def _write_records(path: Path, records: List[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)

    def _restore_from_backup(self) -> bool:
        if not self._path or not self._backup_path or not self._backup_path.exists():
            return False
        records = self._read_records(self._backup_path)
        if not records:
            return False
        self._bindings = {b.binding_id: b for b in records}
        self._write_records(self._path, [b.to_dict() for b in records])
        return True
