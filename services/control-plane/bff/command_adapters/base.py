"""Base abstractions and utilities for BFF Domain Command Adapters.

Every operator-initiated action or command routes to an authoritative domain owner
(Capital, Deployment, Runtime, Persona, Governance, Incident, Evolution, Strategy, etc.)
or raises an explicit ActionUnavailableError if the action is not available in the current
production deployment. No fake completion receipts are emitted.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

log = logging.getLogger(__name__)

_DEFAULT_REQUEST_TIMEOUT = int(os.getenv("PANTHEON_COMMAND_TIMEOUT_SECONDS", "30"))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ActionUnavailableError(ValueError):
    """Raised when an action is explicitly unavailable or unsupported in production.

    ``retryable``/``downstream_status`` distinguish a genuinely unsupported
    action (default: not retryable, 422) from a transient confirmation gap on
    a mutation that may have already committed — e.g. a readback GET that
    fails right after a successful write (reviewer finding 7): the caller
    should retry the read, not treat this the same as "action not supported".
    """

    def __init__(
        self,
        message: str,
        *,
        action_id: str = "",
        entity_type: str = "",
        error_code: str = "ACTION_UNAVAILABLE",
        suggestion: str = "Submit a supported domain action or use the governed CLI workflow.",
        retryable: bool = False,
        downstream_status: int = 422,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.action_id = action_id
        self.entity_type = entity_type
        self.error_code = error_code
        self.suggestion = suggestion
        self.retryable = retryable
        self.downstream_status = downstream_status


def get_base_url(primary_env: str, *fallback_envs: str) -> str:
    for env_name in (primary_env, *fallback_envs):
        val = os.getenv(env_name, "").strip()
        if val:
            return val.rstrip("/")
    raise RuntimeError(
        f"Domain backend is unconfigured: set {primary_env}"
        + (f" or one of {', '.join(fallback_envs)}" if fallback_envs else "")
        + "."
    )


def capital_url(path: str) -> str:
    base = get_base_url("PANTHEON_CAPITAL_API_URL", "PANTHEON_CAPITAL_SERVICE_URL")
    return f"{base}{path}"


def internal_url(path: str) -> str:
    base = get_base_url("PANTHEON_INTERNAL_API_URL")
    return f"{base}{path}"


def governance_url(path: str) -> str:
    base = get_base_url("PANTHEON_GOVERNANCE_API_URL", "PANTHEON_GOVERNANCE_APPROVAL_API_URL")
    return f"{base}{path}"


def registry_url(path: str) -> str:
    base = get_base_url("PANTHEON_REGISTRY_API_URL", "PANTHEON_REGISTRY_URL")
    return f"{base}{path}"


def governance_approval_url(path: str) -> str:
    base = get_base_url("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "PANTHEON_GOVERNANCE_SERVICE_URL")
    return f"{base}{path}"


def deployment_url(path: str) -> str:
    base = get_base_url(
        "PANTHEON_DEPLOYMENT_API_URL",
        "PANTHEON_DEPLOYMENT_SERVICE_URL",
        "PANTHEON_INTERNAL_API_URL",
    )
    return f"{base}{path}"


def evolution_url(path: str) -> str:
    base = get_base_url("PANTHEON_EVOLUTION_API_URL", "PANTHEON_GOVERNANCE_API_URL")
    return f"{base}{path}"


def record_downstream_outcome(url: str, ok: bool, status_code: int, detail: Optional[str] = None) -> None:
    try:
        from services.control_plane.bff.downstream_health_monitor import get_downstream_health_monitor
        monitor = get_downstream_health_monitor()
        if monitor is None:
            return
        registry = monitor._resolve_target_registry()
        for name, target in registry.items():
            base_url = target.base_url.rstrip("/")
            if url.startswith(base_url):
                monitor.record_downstream_outcome(
                    target_name=name,
                    ok=ok,
                    status_code=status_code,
                    detail=detail,
                )
                break
    except Exception as exc:
        log.debug("failed to record downstream outcome for %s: %s", url, exc)


def _token_tenants(token: Optional[str]) -> Tuple[Optional[str], set[str]]:
    try:
        from services.control_plane.bff.auth.policy import (
            TENANT_ALLOWED_CLAIM_PATHS,
            TENANT_PRIMARY_CLAIM_PATHS,
            extract_identity,
            identity_claim_strings,
        )
        raw = str(token or "").removeprefix("Bearer ").strip()
        if not raw:
            return None, set()
        if raw.count(".") == 2:
            raw_b64 = raw.split(".")[1]
            c = json.loads(base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4)))
        else:
            c = getattr(extract_identity(f"Bearer {raw}"), "claims", {}) or {}
        primaries = identity_claim_strings(c, TENANT_PRIMARY_CLAIM_PATHS)
        al = set(identity_claim_strings(c, TENANT_ALLOWED_CLAIM_PATHS))
        return (primaries[0] if primaries and primaries[0] != "*" else None), al
    except Exception:
        return None, set()


def _token_tenant(token: Optional[str]) -> Optional[str]:
    primary, allowed = _token_tenants(token)
    concrete = {t for t in allowed if t != "*"}
    return primary or (next(iter(concrete)) if len(concrete) == 1 else None)


def bound_tenant(payload: Any, tenant_id: Optional[str] = None, auth_token: Optional[str] = None) -> str:
    """Return the trusted tenant; a payload tenant may only equal it, never fill it."""
    req_tid = str(tenant_id or "").strip()
    primary, allowed = _token_tenants(auth_token)
    if req_tid:
        if req_tid not in allowed and "*" not in allowed and req_tid != primary:
            raise ActionUnavailableError("Caller tenant_id is not the verified caller tenant.", error_code="TENANT_MISMATCH", downstream_status=403)
        trusted = req_tid
    else:
        concrete = {t for t in allowed if t != "*"}
        trusted = primary or (next(iter(concrete)) if len(concrete) == 1 else "")
    if not trusted:
        raise ActionUnavailableError("Caller tenant_id is not the verified caller tenant.", error_code="TENANT_MISMATCH", downstream_status=403)
    claimed = str(payload.get("tenant_id") or payload.get("tenant") or "").strip() if isinstance(payload, dict) else ""
    if claimed and (not trusted or claimed != trusted):
        raise ActionUnavailableError("Payload tenant_id is not the verified caller tenant.", error_code="TENANT_MISMATCH", downstream_status=403)
    return trusted


def _headers(payload: Any, auth_token: Optional[str], mfa_token: Optional[str], tenant_id: Optional[str]) -> Dict[str, str]:
    h = {"Accept": "application/json", "X-Pantheon-Service": "control-plane-bff"}
    has_claimed = bool(isinstance(payload, dict) and (payload.get("tenant_id") or payload.get("tenant")))
    raw = str(auth_token or "").removeprefix("Bearer ").strip()
    if raw.count(".") == 2 or tenant_id or has_claimed or (payload is not None and not auth_token):
        t = bound_tenant(payload, tenant_id, auth_token)
        if t:
            h["X-Tenant-Id"] = t
    elif auth_token:
        t = _token_tenant(auth_token)
        if t:
            h["X-Tenant-Id"] = t
    if payload is not None:
        h["Content-Type"] = "application/json"
    if auth_token:
        h["Authorization"] = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
    if mfa_token:
        h["X-MFA-Token"] = mfa_token
    return h


def http_request_json(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
    timeout: Optional[int] = None,
    tenant_id: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    """Execute HTTP request to a domain authority endpoint and parse JSON response."""
    from services.control_plane.bff import command_executor
    normalized_method = method.upper()
    if headers is None and normalized_method == "GET" and hasattr(command_executor, "_get_json"):
        return command_executor._get_json(url, auth_token=auth_token, mfa_token=mfa_token, tenant_id=tenant_id)
    if headers is None and normalized_method == "POST" and hasattr(command_executor, "_post_json"):
        return command_executor._post_json(url, payload or {}, auth_token=auth_token, mfa_token=mfa_token, tenant_id=tenant_id)

    headers = {**(headers or {}), **_headers(payload, auth_token, mfa_token, tenant_id)}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=normalized_method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or _DEFAULT_REQUEST_TIMEOUT) as resp:
            status_code = int(resp.status)
            body = json.loads(resp.read().decode("utf-8"))
            record_downstream_outcome(url, ok=True, status_code=status_code)
            return body
    except urllib.error.HTTPError as exc:
        record_downstream_outcome(url, ok=False, status_code=int(exc.code), detail=f"HTTP {exc.code}")
        raise
    except Exception as exc:
        record_downstream_outcome(url, ok=False, status_code=-1, detail=str(exc))
        raise


def http_request_json_with_headers(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
    timeout: Optional[int] = None,
    tenant_id: Optional[str] = None,
) -> Tuple[int, Dict[str, str], Any]:
    """Like :func:`http_request_json`, but returns ``(status_code, headers, body)``."""
    headers = _headers(payload, auth_token, mfa_token, tenant_id)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout or _DEFAULT_REQUEST_TIMEOUT) as resp:
            status_code = int(resp.status)
            raw = resp.read()
            body = json.loads(raw.decode("utf-8")) if raw else None
            response_headers = {key: value for key, value in resp.headers.items()}
            record_downstream_outcome(url, ok=True, status_code=status_code)
            return status_code, response_headers, body
    except urllib.error.HTTPError as exc:
        record_downstream_outcome(url, ok=False, status_code=int(exc.code), detail=f"HTTP {exc.code}")
        raise
    except Exception as exc:
        record_downstream_outcome(url, ok=False, status_code=-1, detail=str(exc))
        raise


def header_value(headers: Dict[str, str], name: str) -> Optional[str]:
    """Case-insensitive header lookup against a plain ``dict`` of response headers."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def build_domain_receipt(
    *,
    command_id: str,
    entity_type: str,
    entity_id: str,
    action_id: str,
    status: str,
    dispatch_path: str,
    domain_receipt: Optional[Dict[str, Any]] = None,
    authoritative_readback: Optional[Dict[str, Any]] = None,
    idempotent_replay: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct standard domain receipt dictionary."""
    receipt: Dict[str, Any] = {
        "command_id": command_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action_id": action_id,
        "status": status,
        "dispatch_path": dispatch_path,
        "domain_receipt": domain_receipt or {},
        "authoritative_readback": authoritative_readback,
        "idempotent_replay": idempotent_replay,
        "executed_at": utc_now(),
        "live_capital_side_effects": False,
    }
    if extra:
        receipt.update(extra)
    return receipt


class DomainCommandAdapter(ABC):
    """Abstract base class for domain command adapters."""

    @abstractmethod
    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        """Return True if this adapter can handle the given command/entity/action."""

    @abstractmethod
    def execute(
        self,
        command_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute command by dispatching to the authoritative domain owner."""
