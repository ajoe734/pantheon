"""Short-lived Ed25519 assertions for canonical task-state mutations.

Only the authenticated BFF holds the private key.  The canonical writer and
workers receive public verification keys, so possession of a worker process or
the supervisor environment cannot mint Human/Ops authority.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

PRIVATE_KEY_ENV = "PANTHEON_CANONICAL_MUTATION_ASSERTION_PRIVATE_KEY"
PRIVATE_KEY_ID_ENV = "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY_ID"
PUBLIC_KEYS_ENV = "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON"
ASSERTION_ENV = "PANTHEON_CANONICAL_MUTATION_ASSERTION_JSON"
CAPABILITY = "assistant.canonical.mutate"
# Human/Ops may only make bounded assignment/recovery mutations.  A merged
# delivery reconciliation is deliberately included because ai_status applies
# its separate, fail-closed merged-delivery and protected-closeout evidence
# checks before the task can become done.  Normal completion remains owner
# authority and is intentionally not an operator assertion action.
OPERATOR_ACTIONS = frozenset({"assign", "reopen", "note", "reconcile_merged_done"})
SCHEMA = "pantheon.canonical-mutation-assertion.v1"
LEGACY_CONSUMED_KEY = "consumed_operator_assertions"
CONSUMED_KEY = "consumed_canonical_mutation_assertions"
DEV_BRIDGE_CONSUMED_KEY = "consumed_dev_bridge_packets"
MAX_TTL_SECONDS = 300
MAX_CLOCK_SKEW_SECONDS = 5
MAX_CONSUMED_ASSERTIONS = 4096


def _utc(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("operator assertion timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("operator assertion timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _decode_key(value: str, *, label: str) -> bytes:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    try:
        decoded = bytes.fromhex(text)
    except ValueError:
        try:
            decoded = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"{label} is neither hex nor base64url") from exc
    if len(decoded) != 32:
        raise ValueError(f"{label} must decode to exactly 32 bytes")
    return decoded


def _private_key(explicit: bytes | None = None) -> tuple[str, Ed25519PrivateKey]:
    if explicit is not None:
        if len(explicit) != 32:
            raise ValueError("explicit Ed25519 private key must be 32 bytes")
        return "test", Ed25519PrivateKey.from_private_bytes(explicit)
    key_id = str(os.environ.get(PRIVATE_KEY_ID_ENV) or "").strip()
    if not key_id:
        raise ValueError(f"{PRIVATE_KEY_ID_ENV} is required")
    raw = _decode_key(os.environ.get(PRIVATE_KEY_ENV, ""), label=PRIVATE_KEY_ENV)
    return key_id, Ed25519PrivateKey.from_private_bytes(raw)


def _public_keys(
    *,
    explicit_private_key: bytes | None = None,
    explicit_public_keys: Mapping[str, bytes] | None = None,
) -> dict[str, Ed25519PublicKey]:
    if explicit_private_key is not None:
        _, private_key = _private_key(explicit_private_key)
        return {"test": private_key.public_key()}
    if explicit_public_keys is not None:
        raw_keys = dict(explicit_public_keys)
    else:
        raw = str(os.environ.get(PUBLIC_KEYS_ENV) or "").strip()
        if not raw:
            raise ValueError(f"{PUBLIC_KEYS_ENV} is required")
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{PUBLIC_KEYS_ENV} is invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError(f"{PUBLIC_KEYS_ENV} must be a JSON object")
        raw_keys = {
            str(key_id): _decode_key(str(value), label=f"public key {key_id}")
            for key_id, value in decoded.items()
        }
    if not raw_keys:
        raise ValueError("at least one canonical mutation public key is required")
    result: dict[str, Ed25519PublicKey] = {}
    for key_id, value in raw_keys.items():
        normalized_id = str(key_id).strip()
        if not normalized_id or not isinstance(value, bytes) or len(value) != 32:
            raise ValueError("canonical mutation public key entry is invalid")
        result[normalized_id] = Ed25519PublicKey.from_public_bytes(value)
    return result


def public_key_bytes(private_key: bytes) -> bytes:
    """Return raw public bytes for provisioning and tests."""

    _, signer = _private_key(private_key)
    return signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def validate_signing_key_pair() -> None:
    """Fail closed unless the active BFF private key matches its public map."""

    key_id, signer = _private_key()
    configured = _public_keys().get(key_id)
    if configured is None:
        raise ValueError(f"{PUBLIC_KEYS_ENV} does not contain active key {key_id!r}")
    derived_bytes = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    configured_bytes = configured.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if derived_bytes != configured_bytes:
        raise ValueError("canonical mutation private key does not match active public key")


def _canonical(assertion: Mapping[str, Any]) -> bytes:
    body = deepcopy(dict(assertion))
    body.pop("signature", None)
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def argv_digest(command: str, args: list[str]) -> str:
    encoded = json.dumps(
        {"command": command, "args": args},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def issue_assertion(
    *,
    operator_id: str,
    control_activation_id: str,
    task_id: str,
    action: str,
    args: list[str],
    old_assignment: Mapping[str, Any],
    new_assignment: Mapping[str, Any],
    reason: str,
    ttl_seconds: int = 120,
    key: bytes | None = None,
) -> dict[str, Any]:
    if not (1 <= ttl_seconds <= MAX_TTL_SECONDS):
        raise ValueError(f"operator assertion ttl must be 1..{MAX_TTL_SECONDS} seconds")
    if key is None:
        validate_signing_key_pair()
    key_id, signer = _private_key(key)
    now = datetime.now(timezone.utc)
    assertion: dict[str, Any] = {
        "schema": SCHEMA,
        "assertion_id": f"op_{secrets.token_hex(16)}",
        "operator_id": operator_id,
        "control_activation_id": control_activation_id,
        "capability": CAPABILITY,
        "task_id": task_id,
        "action": action,
        "argv_digest": argv_digest(action, args),
        "old_assignment": dict(old_assignment),
        "new_assignment": dict(new_assignment),
        "reason": reason,
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z"),
        "nonce": secrets.token_urlsafe(24),
    }
    signature = signer.sign(_canonical(assertion))
    assertion["signature"] = {
        "algorithm": "Ed25519",
        "key_id": key_id,
        "value": base64.urlsafe_b64encode(signature).decode().rstrip("="),
    }
    return assertion


def migrate_legacy_consumed_ledgers(
    state: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split the former mixed replay bucket once, without losing receipts."""

    operator = state.setdefault(CONSUMED_KEY, {})
    bridge = state.setdefault(DEV_BRIDGE_CONSUMED_KEY, {})
    if not isinstance(operator, dict) or not isinstance(bridge, dict):
        raise ValueError("canonical replay ledger is invalid")
    legacy = state.pop(LEGACY_CONSUMED_KEY, None)
    if legacy is not None:
        if not isinstance(legacy, dict):
            raise ValueError("legacy canonical replay ledger is invalid")
        for receipt_id, receipt in legacy.items():
            destination = bridge if str(receipt_id).startswith("bridge:") else operator
            destination.setdefault(str(receipt_id), receipt)
    return operator, bridge


def _prune_consumed(state: dict[str, Any], current: datetime) -> dict[str, Any]:
    consumed, _bridge = migrate_legacy_consumed_ledgers(state)
    if not isinstance(consumed, dict):
        raise ValueError("consumed operator assertion store is invalid")
    cutoff = current - timedelta(seconds=MAX_TTL_SECONDS + MAX_CLOCK_SKEW_SECONDS)
    retained: list[tuple[datetime, str, Any]] = []
    for assertion_id, item in consumed.items():
        if not isinstance(item, Mapping):
            continue
        try:
            consumed_at = _utc(item.get("consumed_at"))
        except ValueError:
            continue
        if consumed_at >= cutoff:
            retained.append((consumed_at, str(assertion_id), dict(item)))
    retained.sort(reverse=True)
    consumed.clear()
    for _, assertion_id, item in retained[: MAX_CONSUMED_ASSERTIONS - 1]:
        consumed[assertion_id] = item
    return consumed


def verify_and_consume(
    assertion: Mapping[str, Any],
    *,
    state: dict[str, Any],
    task_id: str,
    action: str,
    args: list[str],
    old_assignment: Mapping[str, Any],
    new_assignment: Mapping[str, Any],
    key: bytes | None = None,
    public_keys: Mapping[str, bytes] | None = None,
    now: datetime | None = None,
) -> None:
    required = {
        "schema", "assertion_id", "operator_id", "control_activation_id",
        "capability", "task_id", "action", "argv_digest", "old_assignment",
        "new_assignment", "reason", "issued_at", "expires_at", "nonce", "signature",
    }
    if not isinstance(assertion, Mapping) or set(assertion) != required:
        raise ValueError("operator assertion schema is not exact")
    signature = assertion.get("signature")
    if not isinstance(signature, Mapping) or set(signature) != {"algorithm", "key_id", "value"}:
        raise ValueError("operator assertion signature schema is not exact")
    if signature.get("algorithm") != "Ed25519":
        raise ValueError("operator assertion signature algorithm is invalid")
    key_id = str(signature.get("key_id") or "")
    verifier = _public_keys(
        explicit_private_key=key,
        explicit_public_keys=public_keys,
    ).get(key_id)
    if verifier is None:
        raise ValueError("operator assertion signing key is not trusted")
    try:
        signature_bytes = base64.urlsafe_b64decode(
            str(signature.get("value") or "") + "=" * (-len(str(signature.get("value") or "")) % 4)
        )
        verifier.verify(signature_bytes, _canonical(assertion))
    except (ValueError, binascii.Error, InvalidSignature) as exc:
        raise ValueError("operator assertion signature verification failed") from exc
    issued = _utc(assertion["issued_at"])
    expires = _utc(assertion["expires_at"])
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires <= issued or (expires - issued).total_seconds() > MAX_TTL_SECONDS:
        raise ValueError("operator assertion lifetime is invalid")
    if current < issued - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS) or current >= expires:
        raise ValueError("operator assertion is not currently valid")
    expected_bindings = {
        "schema": SCHEMA,
        "capability": CAPABILITY,
        "task_id": task_id,
        "action": action,
        "argv_digest": argv_digest(action, args),
        "old_assignment": dict(old_assignment),
        "new_assignment": dict(new_assignment),
    }
    for field, value in expected_bindings.items():
        if assertion.get(field) != value:
            raise ValueError(f"operator assertion {field} binding failed")
    if not str(assertion.get("operator_id") or "").strip():
        raise ValueError("operator assertion operator_id is required")
    if not str(assertion.get("control_activation_id") or "").strip():
        raise ValueError("operator assertion control_activation_id is required")
    if not str(assertion.get("reason") or "").strip():
        raise ValueError("operator assertion reason is required")
    assertion_id = str(assertion["assertion_id"])
    nonce = str(assertion["nonce"])
    consumed = _prune_consumed(state, current)
    if assertion_id in consumed or any(
        isinstance(item, Mapping) and item.get("nonce") == nonce for item in consumed.values()
    ):
        raise ValueError("operator assertion has already been consumed")
    consumed[assertion_id] = {
        "nonce": nonce,
        "task_id": task_id,
        "action": action,
        "operator_id": assertion["operator_id"],
        "consumed_at": current.isoformat().replace("+00:00", "Z"),
    }
