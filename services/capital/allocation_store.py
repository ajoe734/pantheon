"""Durable owner store for capital allocations, rebalances, and containment.

The JSON backend persists one aggregate document so a rebalance can validate
and update every allocation line with a single atomic ``os.replace``.  The
Postgres backend uses the same aggregate shape in one ``PostgresJsonOwnerStore``
record, preserving the same all-or-nothing owner boundary.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterable, Optional


class AllocationAuthorityError(ValueError):
    """Base error for the allocation owner store."""

    status_code = 400
    code = "ALLOCATION_AUTHORITY_ERROR"


class AllocationAuthorityNotFound(AllocationAuthorityError):
    status_code = 404
    code = "ALLOCATION_AUTHORITY_NOT_FOUND"


class AllocationAuthorityConflict(AllocationAuthorityError):
    status_code = 409
    code = "ALLOCATION_AUTHORITY_CONFLICT"


class AllocationAuthorityValidationError(AllocationAuthorityError):
    status_code = 422
    code = "ALLOCATION_AUTHORITY_VALIDATION_FAILED"


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def stable_payload_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _server_payload_hash(payload: Dict[str, Any]) -> str:
    semantic = {
        key: value
        for key, value in payload.items()
        if key not in {"idempotency_key", "request_hash"}
    }
    return stable_payload_hash(semantic)


def _deepcopy(value: Any) -> Any:
    return copy.deepcopy(value)


class AllocationAuthorityStore:
    """Single-writer aggregate for rebalance and containment state."""

    _POSTGRES_RECORD_ID = "capital-allocation-authority"
    _SCHEMA_VERSION = 2
    _WEIGHT_TOLERANCE = 1e-12

    def __init__(
        self,
        *,
        path: Optional[Path] = None,
        owner_store: Any = None,
    ) -> None:
        if (path is None) == (owner_store is None):
            raise ValueError("Exactly one of path or owner_store is required")
        self._path = Path(path) if path is not None else None
        self._owner_store = owner_store
        self._lock = RLock()
        self._data = self._empty_data()
        with self._lock:
            self._reload_locked()

    @classmethod
    def _empty_data(cls) -> Dict[str, Any]:
        return {
            "schema_version": cls._SCHEMA_VERSION,
            "rebalances": {},
            "allocations": {},
            "containments": {},
            "idempotency": {},
            "command_receipts": {},
            "containment_commands": {},
            "owner_create_idempotency": {},
        }

    def _reload_locked(self) -> None:
        payload: Any = None
        if self._owner_store is not None:
            payload = self._owner_store.get(self._POSTGRES_RECORD_ID)
        elif self._path is not None and self._path.exists():
            raw = self._path.read_text(encoding="utf-8").strip()
            payload = json.loads(raw) if raw else None
        if payload is None:
            self._data = self._empty_data()
            return
        if not isinstance(payload, dict):
            raise AllocationAuthorityError("Allocation authority payload must be a JSON object")
        data = self._empty_data()
        data.update(_deepcopy(payload))
        # Loading an older aggregate is an in-memory schema migration.  Keep
        # backward-compatible fields, but ensure the next owner write records
        # the schema understood by this process instead of perpetuating the
        # stale version from disk/Postgres.
        data["schema_version"] = self._SCHEMA_VERSION
        for key in (
            "rebalances",
            "allocations",
            "containments",
            "idempotency",
            "command_receipts",
            "containment_commands",
            "owner_create_idempotency",
        ):
            if not isinstance(data.get(key), dict):
                raise AllocationAuthorityError(f"Allocation authority field {key!r} must be an object")
        self._data = data

    def _persist_locked(self) -> None:
        payload = _deepcopy(self._data)
        if self._owner_store is not None:
            self._owner_store.put(self._POSTGRES_RECORD_ID, payload)
            return
        assert self._path is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=f".{self._path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=True, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._path)
            try:
                directory_fd = os.open(str(self._path.parent), os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _require_text(payload: Dict[str, Any], field: str) -> str:
        value = str(payload.get(field) or "").strip()
        if not value:
            raise AllocationAuthorityValidationError(f"{field} is required")
        return value

    @staticmethod
    def _weight(value: Any, field: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AllocationAuthorityValidationError(f"{field} must be a number")
        weight = float(value)
        if not math.isfinite(weight) or weight < 0 or weight > 1:
            raise AllocationAuthorityValidationError(f"{field} must be between 0 and 1")
        return weight

    @staticmethod
    def _allocation_id(pool_id: str, line: Dict[str, Any]) -> str:
        sleeve_id = str(line.get("capital_sleeve_id") or "").strip()
        persona_id = str(line.get("persona_id") or "").strip()
        identity = f"sleeve:{sleeve_id}" if sleeve_id else f"persona:{persona_id}"
        return f"{pool_id}|{identity}"

    def _normalize_lines(
        self,
        pool_id: str,
        raw_lines: Any,
    ) -> list[Dict[str, Any]]:
        if not isinstance(raw_lines, list) or not raw_lines:
            raise AllocationAuthorityValidationError("lines must be a non-empty list")
        normalized: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw_line in enumerate(raw_lines):
            if not isinstance(raw_line, dict):
                raise AllocationAuthorityValidationError(f"lines[{index}] must be an object")
            line = _deepcopy(raw_line)
            persona_id = self._require_text(line, "persona_id")
            line_pool_id = str(line.get("capital_pool_id") or pool_id).strip()
            if line_pool_id != pool_id:
                raise AllocationAuthorityValidationError(
                    f"lines[{index}].capital_pool_id must equal {pool_id!r}"
                )
            current_weight = self._weight(line.get("current_weight"), f"lines[{index}].current_weight")
            target_weight = self._weight(line.get("target_weight"), f"lines[{index}].target_weight")
            delta = target_weight - current_weight
            if line.get("delta") is not None:
                supplied_delta = float(line["delta"])
                if not math.isclose(supplied_delta, delta, abs_tol=self._WEIGHT_TOLERANCE):
                    raise AllocationAuthorityValidationError(
                        f"lines[{index}].delta does not match target_weight-current_weight"
                    )
            line.update(
                {
                    "persona_id": persona_id,
                    "capital_pool_id": pool_id,
                    "capital_scope": str(line.get("capital_scope") or "pool"),
                    "current_weight": current_weight,
                    "target_weight": target_weight,
                    "delta": delta,
                    "cap_reasons": list(line.get("cap_reasons") or []),
                    "evidence_refs": list(line.get("evidence_refs") or []),
                }
            )
            allocation_id = self._allocation_id(pool_id, line)
            if allocation_id in seen:
                raise AllocationAuthorityValidationError(
                    f"Duplicate allocation identity in proposal: {allocation_id}"
                )
            seen.add(allocation_id)
            line["allocation_id"] = allocation_id
            normalized.append(line)
        return normalized

    def _idempotency_entry_locked(
        self,
        *,
        scope: str,
        key: str,
        request_hash: str,
        payload_hash: str,
    ) -> Optional[Dict[str, Any]]:
        entry = self._data["idempotency"].get(f"{scope}:{key}")
        if entry is None:
            return None
        if entry.get("request_hash") != request_hash or entry.get("payload_hash") != payload_hash:
            raise AllocationAuthorityConflict(
                f"Idempotency key {key!r} was already used with a different request"
            )
        return entry

    def reserve_owner_create(
        self,
        *,
        scope: str,
        actor_scope: str,
        key: str,
        request_hash: str,
        payload_hash: str,
        resource_id: str,
    ) -> tuple[Dict[str, Any], bool]:
        """Durably reserve an idempotent pool/binding create before its store write."""
        with self._lock:
            self._reload_locked()
            actor_scope = self._require_text({"actor_scope": actor_scope}, "actor_scope")
            ledger_key = f"{scope}:{actor_scope}:{key}"
            entry = self._data["owner_create_idempotency"].get(ledger_key)
            if entry is None:
                # Schema v1/v2 entries were endpoint-scoped but not actor-scoped.
                # They are inherently ambiguous, so exact legacy entries remain
                # readable while mismatches continue to fail closed.
                legacy_key = f"{scope}:{key}"
                entry = self._data["owner_create_idempotency"].get(legacy_key)
            if entry is not None:
                if (
                    entry.get("request_hash") != request_hash
                    or entry.get("payload_hash") != payload_hash
                    or entry.get("resource_id") != resource_id
                ):
                    raise AllocationAuthorityConflict(
                        f"Idempotency key {key!r} was already used with a different request"
                    )
                return _deepcopy(entry), True
            now = utc_now()
            entry = {
                "operation": scope,
                "actor_scope": actor_scope,
                "request_hash": request_hash,
                "payload_hash": payload_hash,
                "resource_id": resource_id,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
            }
            self._data["owner_create_idempotency"][ledger_key] = entry
            self._persist_locked()
            return _deepcopy(entry), False

    def complete_owner_create(
        self,
        *,
        scope: str,
        actor_scope: str,
        key: str,
    ) -> Dict[str, Any]:
        with self._lock:
            self._reload_locked()
            actor_scope = self._require_text({"actor_scope": actor_scope}, "actor_scope")
            ledger_key = f"{scope}:{actor_scope}:{key}"
            entry = self._data["owner_create_idempotency"].get(ledger_key)
            if entry is None:
                legacy_key = f"{scope}:{key}"
                legacy_entry = self._data["owner_create_idempotency"].get(legacy_key)
                if legacy_entry is not None:
                    ledger_key = legacy_key
                    entry = legacy_entry
            if entry is None:
                raise AllocationAuthorityError(
                    f"Owner create idempotency reservation is missing: {ledger_key}"
                )
            entry["status"] = "succeeded"
            entry["updated_at"] = utc_now()
            self._persist_locked()
            return _deepcopy(entry)

    def create_rebalance(self, payload: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        with self._lock:
            self._reload_locked()
            idempotency_key = self._require_text(payload, "idempotency_key")
            request_hash = self._require_text(payload, "request_hash")
            payload_hash = _server_payload_hash(payload)
            replay = self._idempotency_entry_locked(
                scope="rebalance.create",
                key=idempotency_key,
                request_hash=request_hash,
                payload_hash=payload_hash,
            )
            if replay is not None:
                record = self._data["rebalances"].get(replay.get("resource_id"))
                if record is None:
                    raise AllocationAuthorityError("Durable rebalance idempotency record is orphaned")
                return _deepcopy(record), True

            pool_id = self._require_text(payload, "capital_pool_id")
            lines = self._normalize_lines(pool_id, payload.get("lines"))
            now = utc_now()
            rebalance_id = str(payload.get("rebalance_id") or "").strip()
            if not rebalance_id:
                rebalance_id = f"rb-{now[:10].replace('-', '')}-{uuid.uuid4().hex[:12]}"
            if rebalance_id in self._data["rebalances"]:
                raise AllocationAuthorityConflict(f"Rebalance {rebalance_id!r} already exists")

            record = {
                "id": rebalance_id,
                "rebalance_id": rebalance_id,
                "capital_pool_id": pool_id,
                "ranking_snapshot_id": payload.get("ranking_snapshot_id"),
                "reason": str(payload.get("reason") or ""),
                "lines": lines,
                "simulation": _deepcopy(payload.get("simulation") or {}),
                "constraints": _deepcopy(payload.get("constraints") or {}),
                "rollback_target": _deepcopy(payload.get("rollback_target") or {}),
                "audit_refs": list(payload.get("audit_refs") or []),
                "status": "pending",
                "applied": False,
                "approval_ref": None,
                "apply_receipt": None,
                "failure": None,
                "request_hash": request_hash,
                "created_at": now,
                "updated_at": now,
                "created_by": self._require_text(payload, "actor_id"),
                "canonical_write_authority": "capital_service",
                "persistence_mode": "owner_store",
            }
            self._data["rebalances"][rebalance_id] = record
            self._data["idempotency"][f"rebalance.create:{idempotency_key}"] = {
                "operation": "rebalance.create",
                "request_hash": request_hash,
                "payload_hash": payload_hash,
                "resource_id": rebalance_id,
                "outcome": "succeeded",
                "created_at": now,
            }
            self._persist_locked()
            return _deepcopy(record), False

    def list_rebalances(
        self,
        *,
        capital_pool_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        with self._lock:
            self._reload_locked()
            records = list(self._data["rebalances"].values())
            if capital_pool_id:
                records = [item for item in records if item.get("capital_pool_id") == capital_pool_id]
            if status:
                records = [item for item in records if item.get("status") == status]
            return _deepcopy(sorted(records, key=lambda item: str(item.get("created_at") or "")))

    def get_rebalance(self, rebalance_id: str) -> Dict[str, Any]:
        with self._lock:
            self._reload_locked()
            record = self._data["rebalances"].get(rebalance_id)
            if record is None:
                raise AllocationAuthorityNotFound(f"Rebalance not found: {rebalance_id}")
            return _deepcopy(record)

    def apply_rebalance(self, rebalance_id: str, payload: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        with self._lock:
            self._reload_locked()
            idempotency_key = self._require_text(payload, "idempotency_key")
            request_hash = self._require_text(payload, "request_hash")
            command_id = self._require_text(payload, "command_id")
            approval_ref = str(payload.get("approval_ref") or "").strip() or None
            payload_hash = _server_payload_hash(payload)

            replay = self._idempotency_entry_locked(
                scope=f"rebalance.apply:{rebalance_id}",
                key=idempotency_key,
                request_hash=request_hash,
                payload_hash=payload_hash,
            )
            if replay is not None:
                if replay.get("outcome") == "failed":
                    raise AllocationAuthorityConflict(str(replay.get("error") or "Rebalance apply failed"))
                receipt = self._data["command_receipts"].get(replay.get("command_id"))
                if receipt is None:
                    raise AllocationAuthorityError("Durable apply idempotency record is orphaned")
                result = _deepcopy(receipt)
                result["idempotent_replay"] = True
                return result, True

            command_replay = self._data["command_receipts"].get(command_id)
            if command_replay is not None:
                if (
                    command_replay.get("rebalance_id") != rebalance_id
                    or command_replay.get("request_hash") != request_hash
                    or command_replay.get("payload_hash") != payload_hash
                ):
                    raise AllocationAuthorityConflict(
                        f"Command {command_id!r} was already used for a different apply request"
                    )
                self._data["idempotency"][f"rebalance.apply:{rebalance_id}:{idempotency_key}"] = {
                    "operation": "rebalance.apply",
                    "request_hash": request_hash,
                    "payload_hash": payload_hash,
                    "resource_id": rebalance_id,
                    "command_id": command_id,
                    "outcome": "succeeded",
                    "created_at": utc_now(),
                }
                self._persist_locked()
                result = _deepcopy(command_replay)
                result["idempotent_replay"] = True
                return result, True

            proposal = self._data["rebalances"].get(rebalance_id)
            if proposal is None:
                raise AllocationAuthorityNotFound(f"Rebalance not found: {rebalance_id}")
            if proposal.get("status") == "failed":
                raise AllocationAuthorityConflict(f"Rebalance {rebalance_id!r} is in failed terminal state")
            if proposal.get("applied"):
                raise AllocationAuthorityConflict(f"Rebalance {rebalance_id!r} was already applied")
            increases_live = any(
                str(line.get("stage") or "") == "live_running"
                and float(line.get("target_weight") or 0) > float(line.get("current_weight") or 0)
                for line in proposal.get("lines") or []
            )
            if increases_live and not approval_ref:
                raise AllocationAuthorityConflict(
                    "A human approval reference is required before applying a live capital increase"
                )

            allocations = self._data["allocations"]
            stale: list[Dict[str, Any]] = []
            for line in proposal.get("lines") or []:
                allocation = allocations.get(line.get("allocation_id"))
                if allocation is None:
                    expected = float(line.get("current_weight") or 0)
                    if expected == 0.0:
                        continue
                    stale.append(
                        {
                            "allocation_id": line.get("allocation_id"),
                            "expected_current_weight": expected,
                            "reason": "allocation_missing",
                        }
                    )
                    continue
                expected_identity = {
                    "capital_pool_id": proposal["capital_pool_id"],
                    "capital_scope": str(line.get("capital_scope") or "pool"),
                    "capital_sleeve_id": str(line.get("capital_sleeve_id") or "").strip() or None,
                    "persona_id": str(line.get("persona_id") or "").strip(),
                    "binding_id": str(line.get("binding_id") or "").strip() or None,
                }
                actual_identity = {
                    "capital_pool_id": allocation.get("capital_pool_id"),
                    "capital_scope": str(allocation.get("capital_scope") or "pool"),
                    "capital_sleeve_id": (
                        str(allocation.get("capital_sleeve_id") or "").strip() or None
                    ),
                    "persona_id": str(allocation.get("persona_id") or "").strip(),
                    "binding_id": str(allocation.get("binding_id") or "").strip() or None,
                }
                if actual_identity != expected_identity:
                    stale.append(
                        {
                            "allocation_id": line.get("allocation_id"),
                            "expected_identity": expected_identity,
                            "actual_identity": actual_identity,
                            "reason": "allocation_identity_mismatch",
                        }
                    )
                    continue
                actual = float(allocation.get("current_weight") or 0)
                expected = float(line.get("current_weight") or 0)
                if not math.isclose(actual, expected, abs_tol=self._WEIGHT_TOLERANCE):
                    stale.append(
                        {
                            "allocation_id": line.get("allocation_id"),
                            "expected_current_weight": expected,
                            "actual_current_weight": actual,
                            "reason": "stale_current_weight",
                        }
                    )

            now = utc_now()
            audit_ref = str(payload.get("audit_ref") or "").strip() or f"capital-audit:{rebalance_id}:{command_id}"
            receipt_ref = (
                str(payload.get("receipt_ref") or "").strip()
                or f"capital-rebalance-receipt:{rebalance_id}:{command_id}"
            )
            idempotency_record_key = f"rebalance.apply:{rebalance_id}:{idempotency_key}"
            if stale:
                failure = {
                    "code": "STALE_CURRENT_WEIGHT",
                    "message": "Authoritative allocation weights no longer match the proposal baseline",
                    "details": stale,
                    "command_id": command_id,
                    "approval_ref": approval_ref,
                    "audit_ref": audit_ref,
                    "failed_at": now,
                }
                proposal.update(
                    {
                        "status": "failed",
                        "applied": False,
                        "approval_ref": approval_ref,
                        "failure": failure,
                        "updated_at": now,
                    }
                )
                self._data["idempotency"][idempotency_record_key] = {
                    "operation": "rebalance.apply",
                    "request_hash": request_hash,
                    "payload_hash": payload_hash,
                    "resource_id": rebalance_id,
                    "command_id": command_id,
                    "outcome": "failed",
                    "error": failure["message"],
                    "created_at": now,
                }
                self._persist_locked()
                raise AllocationAuthorityConflict(failure["message"])

            allocation_readback: list[Dict[str, Any]] = []
            for line in proposal.get("lines") or []:
                allocation = allocations.get(line["allocation_id"])
                if allocation is None:
                    allocation = {
                        "allocation_id": line["allocation_id"],
                        "capital_pool_id": proposal["capital_pool_id"],
                        "capital_scope": line["capital_scope"],
                        "capital_sleeve_id": line.get("capital_sleeve_id"),
                        "persona_id": line["persona_id"],
                        "binding_id": line.get("binding_id"),
                        "stage": line.get("stage"),
                        "current_weight": 0.0,
                        "target_weight": 0.0,
                        "allocation_version": 0,
                        "binding_state": line.get("binding_state") or "bound",
                        "containment_state": None,
                        "last_rebalance_id": None,
                        "updated_at": now,
                        "authoritative_capital_readback": True,
                        "canonical_write_authority": "capital_service",
                    }
                    allocations[line["allocation_id"]] = allocation
                allocation.update(
                    {
                        "current_weight": line["target_weight"],
                        "target_weight": line["target_weight"],
                        "allocation_version": int(allocation.get("allocation_version") or 0) + 1,
                        "last_rebalance_id": rebalance_id,
                        "updated_at": now,
                    }
                )
                allocation_readback.append(_deepcopy(allocation))

            receipt = {
                "status": "applied",
                "rebalance_id": rebalance_id,
                "capital_pool_id": proposal["capital_pool_id"],
                "command_id": command_id,
                "approval_ref": approval_ref,
                "receipt_ref": receipt_ref,
                "audit_ref": audit_ref,
                "request_hash": request_hash,
                "payload_hash": payload_hash,
                "applied_at": now,
                "allocation_readback": allocation_readback,
                "authoritative_capital_readback": True,
                "authoritative_capital_state_applied": True,
                "live_capital_side_effects": False,
                "canonical_write_authority": "capital_service",
                "audit_delivery_status": "pending",
                "audit_delivery_attempts": 0,
                "audit_delivery_error": None,
                "audit_event_id": None,
                "audit_delivered_at": None,
                "idempotent_replay": False,
            }
            proposal.update(
                {
                    "status": "applied",
                    "applied": True,
                    "approval_ref": approval_ref,
                    "apply_command_id": command_id,
                    "apply_receipt_ref": receipt_ref,
                    "apply_audit_ref": audit_ref,
                    "apply_receipt": _deepcopy(receipt),
                    "applied_at": now,
                    "failure": None,
                    "updated_at": now,
                }
            )
            self._data["command_receipts"][command_id] = _deepcopy(receipt)
            self._data["idempotency"][idempotency_record_key] = {
                "operation": "rebalance.apply",
                "request_hash": request_hash,
                "payload_hash": payload_hash,
                "resource_id": rebalance_id,
                "command_id": command_id,
                "outcome": "succeeded",
                "created_at": now,
            }
            self._persist_locked()
            return _deepcopy(receipt), False

    def get_rebalance_receipt(self, command_id: str) -> Dict[str, Any]:
        with self._lock:
            self._reload_locked()
            receipt = self._data["command_receipts"].get(command_id)
            if receipt is None:
                raise AllocationAuthorityNotFound(
                    f"Rebalance receipt not found for command: {command_id}"
                )
            return _deepcopy(receipt)

    def update_rebalance_audit_delivery(
        self,
        command_id: str,
        *,
        event_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            self._reload_locked()
            receipt = self._data["command_receipts"].get(command_id)
            if receipt is None:
                raise AllocationAuthorityNotFound(
                    f"Rebalance receipt not found for command: {command_id}"
                )
            if receipt.get("audit_delivery_status") == "delivered":
                return _deepcopy(receipt)
            receipt["audit_delivery_attempts"] = int(
                receipt.get("audit_delivery_attempts") or 0
            ) + 1
            if event_id:
                receipt.update(
                    {
                        "audit_delivery_status": "delivered",
                        "audit_delivery_error": None,
                        "audit_event_id": event_id,
                        "audit_delivered_at": utc_now(),
                    }
                )
            else:
                receipt.update(
                    {
                        "audit_delivery_status": "pending",
                        "audit_delivery_error": str(error or "audit append failed"),
                    }
                )
            rebalance_id = str(receipt.get("rebalance_id") or "")
            proposal = self._data["rebalances"].get(rebalance_id)
            if proposal is not None:
                proposal["apply_receipt"] = _deepcopy(receipt)
                proposal["updated_at"] = utc_now()
            self._persist_locked()
            return _deepcopy(receipt)

    def list_allocations(
        self,
        *,
        capital_pool_id: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        with self._lock:
            self._reload_locked()
            records = list(self._data["allocations"].values())
            if capital_pool_id:
                records = [item for item in records if item.get("capital_pool_id") == capital_pool_id]
            if persona_id:
                records = [item for item in records if item.get("persona_id") == persona_id]
            return _deepcopy(sorted(records, key=lambda item: str(item.get("allocation_id") or "")))

    @staticmethod
    def _containment_state(action: str) -> str:
        return {
            "freeze": "frozen",
            "reduce_capital": "frozen",
            "reduce_capital_access": "frozen",
            "suspend": "suspended",
            "risk_off": "risk_off",
            "flatten": "risk_off",
            "rollback_allocation": "frozen",
            "retire": "retired",
        }[action]

    def create_containment(self, payload: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        with self._lock:
            self._reload_locked()
            idempotency_key = self._require_text(payload, "idempotency_key")
            request_hash = self._require_text(payload, "request_hash")
            payload_hash = _server_payload_hash(payload)
            replay = self._idempotency_entry_locked(
                scope="containment.create",
                key=idempotency_key,
                request_hash=request_hash,
                payload_hash=payload_hash,
            )
            if replay is not None:
                record = self._data["containments"].get(replay.get("resource_id"))
                if record is None:
                    raise AllocationAuthorityError("Durable containment idempotency record is orphaned")
                result = _deepcopy(record)
                result["idempotent_replay"] = True
                return result, True

            persona_id = self._require_text(payload, "persona_id")
            action = self._require_text(payload, "action").lower()
            forbidden_fragments = ("promote", "increase", "create_canary", "create_live")
            target_stage = str(payload.get("target_stage") or "").lower()
            if (
                any(fragment in action for fragment in forbidden_fragments)
                or bool(payload.get("allocation_increase"))
                or target_stage in {"canary", "canary_running", "live", "live_running"}
            ):
                raise AllocationAuthorityValidationError(
                    "Emergency containment cannot promote or increase allocation"
                )
            allowed = {
                "freeze",
                "reduce_capital",
                "reduce_capital_access",
                "suspend",
                "risk_off",
                "flatten",
                "rollback_allocation",
                "retire",
            }
            if action not in allowed:
                raise AllocationAuthorityValidationError(f"Unsupported containment action: {action}")
            evidence_refs = list(payload.get("evidence_refs") or [])
            if not evidence_refs:
                raise AllocationAuthorityValidationError("evidence_refs is required")

            pool_id = str(payload.get("capital_pool_id") or "").strip() or None
            matches = [
                allocation
                for allocation in self._data["allocations"].values()
                if allocation.get("persona_id") == persona_id
                and (pool_id is None or allocation.get("capital_pool_id") == pool_id)
            ]
            baseline_weight = sum(float(item.get("current_weight") or 0) for item in matches)
            current_weight = (
                baseline_weight
                if payload.get("current_weight") is None
                else self._weight(payload.get("current_weight"), "current_weight")
            )
            target_weight = (
                current_weight
                if payload.get("target_weight") is None
                else self._weight(payload.get("target_weight"), "target_weight")
            )
            if current_weight > baseline_weight + self._WEIGHT_TOLERANCE:
                raise AllocationAuthorityValidationError(
                    "Containment current_weight cannot exceed the authoritative baseline"
                )
            if target_weight > current_weight + self._WEIGHT_TOLERANCE:
                raise AllocationAuthorityValidationError(
                    "Containment target_weight must not increase current_weight"
                )
            if action.startswith("reduce_capital") and not matches:
                raise AllocationAuthorityNotFound(
                    f"No authoritative allocation baseline for persona {persona_id}"
                )
            if target_weight < current_weight - self._WEIGHT_TOLERANCE and len(matches) != 1:
                raise AllocationAuthorityValidationError(
                    "A reducing containment must resolve to exactly one allocation"
                )

            now = utc_now()
            containment_id = str(payload.get("containment_id") or "").strip()
            if not containment_id:
                containment_id = f"containment-{now[:10].replace('-', '')}-{uuid.uuid4().hex[:12]}"
            if containment_id in self._data["containments"]:
                raise AllocationAuthorityConflict(f"Containment {containment_id!r} already exists")
            command_id = str(payload.get("command_id") or "").strip() or f"containment-cmd-{uuid.uuid4().hex[:12]}"
            command_replay = self._data["containment_commands"].get(command_id)
            if command_replay is not None:
                raise AllocationAuthorityConflict(
                    f"Command {command_id!r} was already used for a containment"
                )
            state = self._containment_state(action)
            receipt_ref = (
                str(payload.get("receipt_ref") or "").strip()
                or f"capital-containment-receipt:{containment_id}:{command_id}"
            )
            audit_ref = (
                str(payload.get("audit_ref") or "").strip()
                or f"capital-audit:{containment_id}:{command_id}"
            )
            if target_weight < current_weight - self._WEIGHT_TOLERANCE:
                allocation = matches[0]
                allocation["current_weight"] = target_weight
                allocation["target_weight"] = target_weight
                allocation["allocation_version"] = int(allocation.get("allocation_version") or 0) + 1
            for allocation in matches:
                allocation["containment_state"] = state
                allocation["updated_at"] = now

            record = {
                "containment_id": containment_id,
                "persona_id": persona_id,
                "capital_pool_id": pool_id,
                "action": action,
                "state": state,
                "containment_state": state,
                "status": "executed",
                "trigger": str(payload.get("trigger") or ""),
                "evidence_refs": evidence_refs,
                "baseline_weight": baseline_weight,
                "current_weight": current_weight,
                "target_weight": target_weight,
                "command_id": command_id,
                "approval_ref": payload.get("approval_ref"),
                "two_man_signature_id": payload.get("two_man_signature_id"),
                "receipt_ref": receipt_ref,
                "audit_ref": audit_ref,
                "request_hash": request_hash,
                "payload_hash": payload_hash,
                "executed_at": now,
                "authoritative_containment_readback": True,
                "authoritative_capital_readback": True,
                "authoritative_capital_state_applied": True,
                "live_capital_side_effects": False,
                "canonical_write_authority": "capital_service",
                "audit_delivery_status": "pending",
                "audit_delivery_attempts": 0,
                "audit_delivery_error": None,
                "audit_event_id": None,
                "audit_delivered_at": None,
                "idempotent_replay": False,
            }
            self._data["containments"][containment_id] = record
            self._data["containment_commands"][command_id] = {
                "containment_id": containment_id,
                "request_hash": request_hash,
                "payload_hash": payload_hash,
            }
            self._data["idempotency"][f"containment.create:{idempotency_key}"] = {
                "operation": "containment.create",
                "request_hash": request_hash,
                "payload_hash": payload_hash,
                "resource_id": containment_id,
                "command_id": command_id,
                "outcome": "succeeded",
                "created_at": now,
            }
            self._persist_locked()
            return _deepcopy(record), False

    def get_containment_receipt(self, command_id: str) -> Dict[str, Any]:
        with self._lock:
            self._reload_locked()
            command = self._data["containment_commands"].get(command_id)
            if command is None:
                raise AllocationAuthorityNotFound(
                    f"Containment receipt not found for command: {command_id}"
                )
            record = self._data["containments"].get(command.get("containment_id"))
            if record is None:
                raise AllocationAuthorityError(
                    f"Containment command {command_id!r} has no durable receipt"
                )
            return _deepcopy(record)

    def update_containment_audit_delivery(
        self,
        command_id: str,
        *,
        event_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            self._reload_locked()
            command = self._data["containment_commands"].get(command_id)
            if command is None:
                raise AllocationAuthorityNotFound(
                    f"Containment receipt not found for command: {command_id}"
                )
            record = self._data["containments"].get(command.get("containment_id"))
            if record is None:
                raise AllocationAuthorityError(
                    f"Containment command {command_id!r} has no durable receipt"
                )
            if record.get("audit_delivery_status") == "delivered":
                return _deepcopy(record)
            record["audit_delivery_attempts"] = int(
                record.get("audit_delivery_attempts") or 0
            ) + 1
            if event_id:
                record.update(
                    {
                        "audit_delivery_status": "delivered",
                        "audit_delivery_error": None,
                        "audit_event_id": event_id,
                        "audit_delivered_at": utc_now(),
                    }
                )
            else:
                record.update(
                    {
                        "audit_delivery_status": "pending",
                        "audit_delivery_error": str(error or "audit append failed"),
                    }
                )
            self._persist_locked()
            return _deepcopy(record)

    def list_containments(
        self,
        *,
        persona_id: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        with self._lock:
            self._reload_locked()
            records = list(self._data["containments"].values())
            if persona_id:
                records = [item for item in records if item.get("persona_id") == persona_id]
            return _deepcopy(sorted(records, key=lambda item: str(item.get("executed_at") or "")))
