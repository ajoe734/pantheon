"""Base abstractions and utilities for BFF Domain Command Adapters.

Every operator-initiated action or command routes to an authoritative domain owner
(Capital, Deployment, Runtime, Persona, Governance, Incident, Evolution, Strategy, etc.)
or raises an explicit ActionUnavailableError if the action is not available in the current
production deployment. No fake completion receipts are emitted.
"""
from __future__ import annotations

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
    """Raised when an action is explicitly unavailable or unsupported in production."""

    def __init__(
        self,
        message: str,
        *,
        action_id: str = "",
        entity_type: str = "",
        error_code: str = "ACTION_UNAVAILABLE",
        suggestion: str = "Submit a supported domain action or use the governed CLI workflow.",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.action_id = action_id
        self.entity_type = entity_type
        self.error_code = error_code
        self.suggestion = suggestion


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


def runtime_repair_url(path: str) -> str:
    base = get_base_url("PANTHEON_RUNTIME_MANAGER_API_URL", "PANTHEON_INTERNAL_API_URL")
    return f"{base}{path}"


def governance_url(path: str) -> str:
    base = get_base_url("PANTHEON_GOVERNANCE_API_URL", "PANTHEON_EVOLUTION_API_URL")
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


def http_request_json(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Any:
    """Execute HTTP request to a domain authority endpoint and parse JSON response."""
    from services.control_plane.bff import command_executor
    normalized_method = method.upper()
    if normalized_method == "GET" and hasattr(command_executor, "_get_json"):
        return command_executor._get_json(url, auth_token=auth_token, mfa_token=mfa_token)
    if normalized_method == "POST" and hasattr(command_executor, "_post_json"):
        # _post_json hardcodes method="POST"; PATCH/PUT/DELETE must not reuse
        # it or they would silently be sent as POST against a route that
        # doesn't accept it.
        return command_executor._post_json(url, payload or {}, auth_token=auth_token, mfa_token=mfa_token)

    req_timeout = timeout or _DEFAULT_REQUEST_TIMEOUT
    headers: Dict[str, str] = {"Accept": "application/json"}
    data: Optional[bytes] = None

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}" if not auth_token.startswith("Bearer ") else auth_token
    if mfa_token:
        headers["X-MFA-Token"] = mfa_token

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=req_timeout) as resp:
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
