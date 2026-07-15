from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional


REPAIR_RECEIPT_KEY_ENV = "PANTHEON_ASSISTANT_REPAIR_RECEIPT_KEY"
BFF_JWT_SECRET_ENV = "PANTHEON_BFF_JWT_SECRET"
REPAIR_RECEIPT_TTL_ENV = "PANTHEON_ASSISTANT_REPAIR_RECEIPT_TTL_SECONDS"
DEFAULT_REPAIR_RECEIPT_TTL_SECONDS = 900
MAX_REPAIR_RECEIPT_TTL_SECONDS = 3600
REPAIR_RECEIPT_KIND = "pantheon.assistant.repair-worktree.v1"

_REPAIR_FIELDS = (
    "task_id",
    "task_worktree",
    "declared_scope",
    "expected_branch",
    "remote",
    "merge_target",
    "repo_key",
    "require_clean",
    "require_pr",
)


class RepairReceiptError(ValueError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso_z(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RepairReceiptError("Repair receipt timestamp is invalid", reason="invalid_timestamp") from exc
    if parsed.tzinfo is None:
        raise RepairReceiptError("Repair receipt timestamp must include a timezone", reason="invalid_timestamp")
    return parsed.astimezone(timezone.utc)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:  # noqa: BLE001 - malformed capability must fail closed.
        raise RepairReceiptError("Repair receipt encoding is invalid", reason="invalid_format") from exc


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _receipt_key() -> bytes:
    configured = (
        os.getenv(REPAIR_RECEIPT_KEY_ENV, "").strip()
        or os.getenv(BFF_JWT_SECRET_ENV, "").strip()
    )
    if not configured:
        raise RepairReceiptError(
            "Assistant repair receipt signing key is not configured",
            reason="receipt_key_unconfigured",
        )
    return hmac.new(
        configured.encode("utf-8"),
        b"pantheon-assistant-repair-receipt-v1",
        hashlib.sha256,
    ).digest()


def _receipt_ttl_seconds() -> int:
    raw = os.getenv(REPAIR_RECEIPT_TTL_ENV, "").strip()
    try:
        value = int(raw) if raw else DEFAULT_REPAIR_RECEIPT_TTL_SECONDS
    except ValueError:
        value = DEFAULT_REPAIR_RECEIPT_TTL_SECONDS
    return min(max(value, 1), MAX_REPAIR_RECEIPT_TTL_SECONDS)


def canonical_repair_metadata(value: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for field in _REPAIR_FIELDS:
        if field not in value:
            continue
        raw = value.get(field)
        if field == "declared_scope":
            if not isinstance(raw, list):
                raise RepairReceiptError(
                    "Repair receipt declared_scope must be a list",
                    reason="invalid_repair_metadata",
                )
            scope = [str(item or "").strip() for item in raw]
            if not scope or any(not item for item in scope):
                raise RepairReceiptError(
                    "Repair receipt declared_scope must contain non-empty paths",
                    reason="invalid_repair_metadata",
                )
            result[field] = scope
        elif field in {"require_clean", "require_pr"}:
            if not isinstance(raw, bool):
                raise RepairReceiptError(
                    f"Repair receipt {field} must be a boolean",
                    reason="invalid_repair_metadata",
                )
            result[field] = raw
        else:
            clean = str(raw or "").strip()
            if clean:
                result[field] = clean

    required = {
        "task_id",
        "task_worktree",
        "declared_scope",
        "expected_branch",
        "remote",
        "merge_target",
        "repo_key",
    }
    missing = sorted(required.difference(result))
    if missing:
        raise RepairReceiptError(
            f"Repair receipt metadata is missing required fields: {', '.join(missing)}",
            reason="invalid_repair_metadata",
        )
    return result


def issue_repair_receipt(
    repair: Mapping[str, Any],
    *,
    actor_id: str,
    tenant_id: str,
    control_status: Mapping[str, Any],
    now: Optional[datetime] = None,
) -> str:
    issued_at = (now or _utc_now()).astimezone(timezone.utc).replace(microsecond=0)
    activation_id = str(
        control_status.get("activation_id") or control_status.get("activationId") or ""
    ).strip()
    if not activation_id:
        raise RepairReceiptError(
            "Active repair control mode has no activation id",
            reason="activation_id_missing",
        )
    payload = {
        "kind": REPAIR_RECEIPT_KIND,
        "actor_id": str(actor_id or "").strip(),
        "tenant_id": str(tenant_id or "").strip(),
        "activation_id": activation_id,
        "management_session_id": str(
            control_status.get("management_session_id")
            or control_status.get("managementSessionId")
            or ""
        ).strip()
        or None,
        "issued_at": _iso_z(issued_at),
        "expires_at": _iso_z(issued_at + timedelta(seconds=_receipt_ttl_seconds())),
        "repair": canonical_repair_metadata(repair),
    }
    if not payload["actor_id"] or not payload["tenant_id"]:
        raise RepairReceiptError(
            "Repair receipt actor and tenant bindings are required",
            reason="binding_missing",
        )
    encoded = _b64url_encode(_canonical_json(payload))
    signature = _b64url_encode(hmac.new(_receipt_key(), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_repair_receipt(
    token: str,
    *,
    actor_id: str,
    tenant_id: str,
    control_status: Mapping[str, Any],
    supplied_repair: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    clean_token = str(token or "").strip()
    try:
        encoded, signature = clean_token.split(".", 1)
    except ValueError as exc:
        raise RepairReceiptError("Repair receipt is missing or malformed", reason="invalid_format") from exc
    expected_signature = _b64url_encode(
        hmac.new(_receipt_key(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(signature, expected_signature):
        raise RepairReceiptError("Repair receipt signature is invalid", reason="invalid_signature")
    try:
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepairReceiptError("Repair receipt payload is invalid", reason="invalid_format") from exc
    if not isinstance(payload, dict) or payload.get("kind") != REPAIR_RECEIPT_KIND:
        raise RepairReceiptError("Repair receipt kind is invalid", reason="invalid_kind")

    checked_at = (now or _utc_now()).astimezone(timezone.utc)
    if checked_at >= _parse_iso_z(payload.get("expires_at")):
        raise RepairReceiptError("Repair receipt has expired", reason="expired")
    _parse_iso_z(payload.get("issued_at"))

    expected_activation = str(
        control_status.get("activation_id") or control_status.get("activationId") or ""
    ).strip()
    expected_session = str(
        control_status.get("management_session_id")
        or control_status.get("managementSessionId")
        or ""
    ).strip() or None
    bindings = {
        "actor_id": str(actor_id or "").strip(),
        "tenant_id": str(tenant_id or "").strip(),
        "activation_id": expected_activation,
        "management_session_id": expected_session,
    }
    for field, expected in bindings.items():
        actual = payload.get(field)
        if actual != expected:
            raise RepairReceiptError(
                f"Repair receipt {field} binding does not match the active request",
                reason=f"{field}_mismatch",
            )

    signed_repair = canonical_repair_metadata(payload.get("repair") or {})
    if supplied_repair is not None:
        supplied = canonical_repair_metadata(supplied_repair)
        if not hmac.compare_digest(_canonical_json(supplied), _canonical_json(signed_repair)):
            raise RepairReceiptError(
                "Repair metadata differs from the prepared worktree receipt",
                reason="repair_metadata_mismatch",
            )
    return signed_repair
