from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .mode_policy import (
    DEFAULT_KERNEL_TTL_SECONDS,
    KERNEL_MODES,
    MAX_KERNEL_TTL_SECONDS,
    command_classes_for_mode,
)
from .models import AssistantMode


CONTROL_MODE_ROLES = {"operator", "admin"}
CONTROL_MODE_CAPABILITY_PREFIX = "assistant.kernel"
DEFAULT_CONTROL_IDLE_TTL_SECONDS = 600
MIN_CONTROL_PASSPHRASE_LENGTH = 12
PASSPHRASE_HASH_ENV = "PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH"
STORE_PATH_ENV = "PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH"
HASH_ALGORITHM = "pbkdf2_sha256"
HASH_ITERATIONS = 260_000


class ControlModeError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 422,
        field: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.field = field
        self.reason = reason or field


@dataclass(frozen=True)
class ControlModeActivation:
    activation_id: str
    actor_id: str
    mode: AssistantMode
    capabilities: List[str]
    command_classes: List[str]
    reason: str
    created_at: str
    expires_at: str
    idle_expires_at: str
    ttl_seconds: int
    idle_ttl_seconds: int
    management_session_id: Optional[str] = None
    status: str = "active"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def isoformat_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def parse_iso_z(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def actor_capabilities(actor: Any) -> List[str]:
    claims = getattr(actor, "claims", {}) if actor is not None else {}
    if not isinstance(claims, dict):
        claims = {}
    raw = claims.get("capabilities") or claims.get("capability") or claims.get("permissions") or []
    if isinstance(raw, str):
        import re

        raw = [item.strip() for item in re.split(r"[\s,]+", raw) if item.strip()]
    if not isinstance(raw, list):
        raw = []
    result: List[str] = []
    seen = set()
    for item in raw:
        clean = str(item or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def actor_has_control_role(actor: Any) -> bool:
    roles = {str(role or "").strip() for role in (getattr(actor, "roles", []) or [])}
    return bool(roles.intersection(CONTROL_MODE_ROLES))


def actor_has_kernel_capability(actor: Any) -> bool:
    return any(cap.startswith(CONTROL_MODE_CAPABILITY_PREFIX) for cap in actor_capabilities(actor))


def passphrase_hash(passphrase: str, *, salt_hex: Optional[str] = None) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        HASH_ITERATIONS,
    )
    return f"{HASH_ALGORITHM}${HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_passphrase_hash(passphrase: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_hex, digest_hex = encoded_hash.split("$", 3)
        iterations = int(iterations_raw)
    except (ValueError, TypeError):
        return False
    if algorithm != HASH_ALGORITHM or iterations <= 0:
        return False
    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            passphrase.encode("utf-8"),
            bytes.fromhex(salt_hex),
            iterations,
        ).hex()
    except ValueError:
        return False
    return hmac.compare_digest(digest, digest_hex)


def activation_to_dict(activation: ControlModeActivation) -> Dict[str, Any]:
    return {
        "state": activation.status,
        "active": activation.status == "active",
        "activationId": activation.activation_id,
        "activation_id": activation.activation_id,
        "actorId": activation.actor_id,
        "actor_id": activation.actor_id,
        "mode": activation.mode.value,
        "capabilities": list(activation.capabilities),
        "commandClasses": list(activation.command_classes),
        "command_classes": list(activation.command_classes),
        "reason": activation.reason,
        "createdAt": activation.created_at,
        "created_at": activation.created_at,
        "expiresAt": activation.expires_at,
        "expires_at": activation.expires_at,
        "idleExpiresAt": activation.idle_expires_at,
        "idle_expires_at": activation.idle_expires_at,
        "ttlSeconds": activation.ttl_seconds,
        "ttl_seconds": activation.ttl_seconds,
        "idleTtlSeconds": activation.idle_ttl_seconds,
        "idle_ttl_seconds": activation.idle_ttl_seconds,
        "managementSessionId": activation.management_session_id,
        "management_session_id": activation.management_session_id,
        "requiresConfirmation": True,
        "requires_confirmation": True,
    }


class ControlModeStore:
    """Short-lived assistant control-mode activation and passphrase store.

    The passphrase is stored as a PBKDF2 hash. Activations are in-process and
    intentionally short lived; if a BFF worker restarts, operators re-activate.
    """

    def __init__(
        self,
        *,
        storage_path: Optional[str] = None,
        initial_passphrase: Optional[str] = None,
        initial_passphrase_hash: Optional[str] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._activations: Dict[str, ControlModeActivation] = {}
        self._storage_path = storage_path
        if self._storage_path is None:
            self._storage_path = os.getenv(STORE_PATH_ENV, "/tmp/pantheon-bff/assistant-control-mode.json")
        stored_hash = self._read_passphrase_hash()
        self._passphrase_hash = initial_passphrase_hash or stored_hash or os.getenv(PASSPHRASE_HASH_ENV, "").strip() or None
        if initial_passphrase:
            self._passphrase_hash = passphrase_hash(initial_passphrase)

    def configured(self) -> bool:
        return bool(self._passphrase_hash)

    def _storage_enabled(self) -> bool:
        raw = str(self._storage_path or "").strip()
        return bool(raw and raw.lower() not in {"off", "false", "disabled", "none"})

    def _read_passphrase_hash(self) -> Optional[str]:
        if not self._storage_enabled():
            return None
        path = Path(str(self._storage_path))
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        value = data.get("passphrase_hash") if isinstance(data, dict) else None
        return str(value).strip() or None

    def _write_passphrase_hash(self) -> None:
        if not self._storage_enabled():
            return
        path = Path(str(self._storage_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "passphrase_hash": self._passphrase_hash,
                    "updated_at": isoformat_z(utc_now()),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _verify_passphrase(self, passphrase: str) -> bool:
        encoded_hash = self._passphrase_hash
        if not encoded_hash:
            return False
        return verify_passphrase_hash(passphrase, encoded_hash)

    def set_passphrase(
        self,
        *,
        new_passphrase: str,
        current_passphrase: Optional[str] = None,
        require_current: bool = True,
    ) -> None:
        if len(str(new_passphrase or "")) < MIN_CONTROL_PASSPHRASE_LENGTH:
            raise ControlModeError(
                f"Control mode passphrase must be at least {MIN_CONTROL_PASSPHRASE_LENGTH} characters.",
                field="newPassphrase",
                reason="passphrase_too_short",
            )
        with self._lock:
            if self._passphrase_hash and require_current:
                if not current_passphrase or not self._verify_passphrase(current_passphrase):
                    raise ControlModeError(
                        "Current control mode passphrase is invalid.",
                        status_code=403,
                        field="currentPassphrase",
                        reason="invalid_current_passphrase",
                    )
            self._passphrase_hash = passphrase_hash(new_passphrase)
            self._write_passphrase_hash()

    def _expiry_status(self, activation: ControlModeActivation, now: datetime) -> Optional[str]:
        if activation.status != "active":
            return activation.status
        if now >= parse_iso_z(activation.expires_at):
            return "expired"
        if now >= parse_iso_z(activation.idle_expires_at):
            return "idle_expired"
        return None

    def _mark_status(self, actor_id: str, status: str) -> Optional[ControlModeActivation]:
        activation = self._activations.get(actor_id)
        if activation is None:
            return None
        updated = ControlModeActivation(
            activation_id=activation.activation_id,
            actor_id=activation.actor_id,
            mode=activation.mode,
            capabilities=activation.capabilities,
            command_classes=activation.command_classes,
            reason=activation.reason,
            created_at=activation.created_at,
            expires_at=activation.expires_at,
            idle_expires_at=activation.idle_expires_at,
            ttl_seconds=activation.ttl_seconds,
            idle_ttl_seconds=activation.idle_ttl_seconds,
            management_session_id=activation.management_session_id,
            status=status,
        )
        self._activations[actor_id] = updated
        return updated

    def status_for_actor(
        self,
        actor_id: str,
        *,
        management_session_id: Optional[str] = None,
        touch: bool = False,
    ) -> Dict[str, Any]:
        clean_actor_id = str(actor_id or "").strip()
        now = utc_now()
        with self._lock:
            activation = self._activations.get(clean_actor_id)
            if activation is None:
                return self.inactive_status(reason="not_active")
            if activation.management_session_id and management_session_id:
                if activation.management_session_id != management_session_id:
                    return self.inactive_status(reason="session_mismatch")
            expired_reason = self._expiry_status(activation, now)
            if expired_reason:
                activation = self._mark_status(clean_actor_id, expired_reason) or activation
                return self.inactive_status(reason=expired_reason, last_activation=activation)
            if touch:
                idle_expires_at = min(
                    now + timedelta(seconds=activation.idle_ttl_seconds),
                    parse_iso_z(activation.expires_at),
                )
                activation = ControlModeActivation(
                    activation_id=activation.activation_id,
                    actor_id=activation.actor_id,
                    mode=activation.mode,
                    capabilities=activation.capabilities,
                    command_classes=activation.command_classes,
                    reason=activation.reason,
                    created_at=activation.created_at,
                    expires_at=activation.expires_at,
                    idle_expires_at=isoformat_z(idle_expires_at),
                    ttl_seconds=activation.ttl_seconds,
                    idle_ttl_seconds=activation.idle_ttl_seconds,
                    management_session_id=activation.management_session_id,
                    status=activation.status,
                )
                self._activations[clean_actor_id] = activation
            return activation_to_dict(activation)

    def inactive_status(
        self,
        *,
        reason: str,
        last_activation: Optional[ControlModeActivation] = None,
    ) -> Dict[str, Any]:
        status = {
            "state": "inactive",
            "active": False,
            "reason": reason,
            "configured": self.configured(),
            "requiresRole": sorted(CONTROL_MODE_ROLES),
            "requires_role": sorted(CONTROL_MODE_ROLES),
            "requiresCapabilityPrefix": CONTROL_MODE_CAPABILITY_PREFIX,
            "requires_capability_prefix": CONTROL_MODE_CAPABILITY_PREFIX,
            "requiresMfa": True,
            "requires_mfa": True,
            "changePassphraseHref": "/bff/assistant/control-mode/passphrase",
            "change_passphrase_href": "/bff/assistant/control-mode/passphrase",
        }
        if last_activation is not None:
            status["lastActivationId"] = last_activation.activation_id
            status["last_activation_id"] = last_activation.activation_id
        return status

    def activate(
        self,
        *,
        actor_id: str,
        mode: AssistantMode,
        capabilities: List[str],
        reason: str,
        passphrase: str,
        ttl_seconds: int,
        idle_ttl_seconds: int,
        management_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if mode not in KERNEL_MODES:
            raise ControlModeError(
                "Control mode activation requires a kernel mode.",
                field="mode",
                reason="kernel_mode_required",
            )
        if not self.configured():
            raise ControlModeError(
                "Control mode passphrase is not configured.",
                status_code=409,
                field="passphrase",
                reason="passphrase_not_configured",
            )
        if not self._verify_passphrase(passphrase):
            raise ControlModeError(
                "Control mode passphrase is invalid.",
                status_code=403,
                field="passphrase",
                reason="invalid_passphrase",
            )
        clean_reason = str(reason or "").strip()
        if not clean_reason:
            raise ControlModeError(
                "Control mode activation requires a reason.",
                field="reason",
                reason="reason_required",
            )
        if ttl_seconds <= 0 or ttl_seconds > MAX_KERNEL_TTL_SECONDS:
            raise ControlModeError(
                f"Control mode ttlSeconds must be between 1 and {MAX_KERNEL_TTL_SECONDS}.",
                field="ttlSeconds",
                reason="ttl_invalid",
            )
        if idle_ttl_seconds <= 0 or idle_ttl_seconds > ttl_seconds:
            raise ControlModeError(
                "Control mode idleTtlSeconds must be positive and no larger than ttlSeconds.",
                field="idleTtlSeconds",
                reason="idle_ttl_invalid",
            )
        now = utc_now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        idle_expires_at = min(now + timedelta(seconds=idle_ttl_seconds), expires_at)
        activation = ControlModeActivation(
            activation_id=f"ctrl_{secrets.token_hex(8)}",
            actor_id=str(actor_id),
            mode=mode,
            capabilities=list(capabilities),
            command_classes=command_classes_for_mode(mode),
            reason=clean_reason,
            created_at=isoformat_z(now),
            expires_at=isoformat_z(expires_at),
            idle_expires_at=isoformat_z(idle_expires_at),
            ttl_seconds=ttl_seconds,
            idle_ttl_seconds=idle_ttl_seconds,
            management_session_id=str(management_session_id or "").strip() or None,
        )
        with self._lock:
            self._activations[activation.actor_id] = activation
        return activation_to_dict(activation)

    def deactivate(self, actor_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
        clean_actor_id = str(actor_id or "").strip()
        with self._lock:
            activation = self._activations.get(clean_actor_id)
            if activation is None:
                return self.inactive_status(reason="not_active")
            updated = self._mark_status(clean_actor_id, "revoked")
        return self.inactive_status(reason=reason or "revoked", last_activation=updated)


def default_idle_ttl(ttl_seconds: int) -> int:
    raw = os.getenv("PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS", "").strip()
    try:
        configured = int(raw) if raw else DEFAULT_CONTROL_IDLE_TTL_SECONDS
    except ValueError:
        configured = DEFAULT_CONTROL_IDLE_TTL_SECONDS
    return max(1, min(configured, ttl_seconds))
