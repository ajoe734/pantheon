"""Client/adapter for the authoritative runtime-manager service path.

The runtime-manager is the sole writer for RuntimeBinding mutations. Control-
plane code must use this client instead of touching RuntimeBindingStore
directly.

Transport modes
---------------
1. HTTP mode: when ``PANTHEON_RUNTIME_MANAGER_URL`` is set, dispatches to the
   deployable Flask service surface in ``services/runtime-manager/main.py``.
2. Local mode: otherwise instantiates ``RuntimeManagerService`` in-process.

Both modes normalize responses to plain dictionaries so callers can keep one
mutation path while tests stay lightweight.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from service import RuntimeManagerError, RuntimeManagerService  # noqa: E402
from runtime_binding import RuntimeBindingError  # noqa: E402


class RuntimeManagerClientError(RuntimeError):
    """Raised when the authoritative runtime-manager path rejects a request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.payload = payload or {}


class RuntimeManagerClient:
    """Thin adapter over the runtime-manager HTTP/local service path."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        bearer_token: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> None:
        self._base_url = (base_url or os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "")).strip().rstrip("/")
        self._bearer_token = (
            bearer_token
            or os.getenv("PANTHEON_RUNTIME_MANAGER_TOKEN", "runtime-control-internal")
        )
        self._timeout_seconds = int(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("PANTHEON_RUNTIME_MANAGER_TIMEOUT_SECONDS", "15")
        )
        self._local_service: Optional[RuntimeManagerService] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def deploy(self, request: Dict[str, Any]) -> Dict[str, Any]:
        if self._use_http():
            return self._request_json("POST", "/api/runtimes/deploy", request)
        binding = self._local().deploy(request)
        return binding.to_dict()

    def get(self, binding_id: str) -> Optional[Dict[str, Any]]:
        if self._use_http():
            try:
                return self._request_json("GET", f"/api/runtime-bindings/{binding_id}")
            except RuntimeManagerClientError as exc:
                if exc.status_code == 404:
                    return None
                raise
        binding = self._local().get(binding_id)
        return binding.to_dict() if binding is not None else None

    def list_all(self) -> list[Dict[str, Any]]:
        if self._use_http():
            payload = self._request_json("GET", "/api/runtime-bindings")
            return list(payload.get("bindings", []))
        return [binding.to_dict() for binding in self._local().list_all()]

    def list_by_pool(self, capital_pool_id: str) -> list[Dict[str, Any]]:
        if self._use_http():
            payload = self._request_json("GET", f"/api/runtime-bindings?pool_id={capital_pool_id}")
            return list(payload.get("bindings", []))
        return [binding.to_dict() for binding in self._local().list_by_pool(capital_pool_id)]

    def get_active_for_pool(self, capital_pool_id: str) -> Optional[Dict[str, Any]]:
        if self._use_http():
            try:
                return self._request_json("GET", f"/api/runtimes/{capital_pool_id}/active")
            except RuntimeManagerClientError as exc:
                if exc.status_code == 404:
                    return None
                raise
        binding = self._local().get_active_for_pool(capital_pool_id)
        return binding.to_dict() if binding is not None else None

    def transition(self, binding_id: str, new_status: str) -> Dict[str, Any]:
        if self._use_http():
            return self._request_json(
                "POST",
                f"/api/runtime-bindings/{binding_id}/transition",
                {"new_status": new_status},
            )
        binding = self._local().transition(binding_id, new_status)
        return binding.to_dict()

    def retire(self, binding_id: str, *, retired_at: Optional[str] = None) -> Dict[str, Any]:
        if self._use_http():
            payload: Dict[str, Any] = {}
            if retired_at:
                payload["retired_at"] = retired_at
            return self._request_json(
                "POST",
                f"/api/runtime-bindings/{binding_id}/retire",
                payload,
            )
        binding = self._local().retire(binding_id, retired_at=retired_at)
        return binding.to_dict()

    def rollback(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a canonical rollback action through the runtime-manager.

        Dispatches to POST /api/rollback (HTTP mode) or service.rollback() (local mode).
        Returns { action_type, old_binding, new_binding, cutover_at, position_lineage }.
        """
        if self._use_http():
            return self._request_json("POST", "/api/rollback", request)
        return self._local().rollback(request)

    # ------------------------------------------------------------------
    # Kill-switch fast path
    # ------------------------------------------------------------------

    def execute_kill_switch(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch an emergency kill-switch command via the runtime-manager fast path.

        Required: reason, capital_pool_id, actor_id
        Optional: binding_id, severity, action_override,
                  fallback_artifact_id, fallback_artifact_version, context

        Returns { command, audit_entry, safe_mode_after }.
        """
        if self._use_http():
            return self._request_json("POST", "/api/kill-switch/dispatch", request)
        return self._local().execute_kill_switch(request)

    def get_safe_mode(self, capital_pool_id: str) -> Dict[str, Any]:
        """Return the current SafeModeState for a capital pool.

        Returns { capital_pool_id, safe_mode_state }.
        """
        if self._use_http():
            return self._request_json("GET", f"/api/kill-switch/{capital_pool_id}/safe-mode")
        state = self._local().get_safe_mode(capital_pool_id)
        return {"capital_pool_id": capital_pool_id, "safe_mode_state": state}

    def advance_safe_mode(
        self,
        capital_pool_id: str,
        target_state: str,
        actor_id: str,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Manually advance the safe-mode state for a pool (governance recovery path).

        Returns { capital_pool_id, safe_mode_state }.
        """
        if self._use_http():
            payload: Dict[str, Any] = {"target_state": target_state, "actor_id": actor_id}
            if note is not None:
                payload["note"] = note
            return self._request_json(
                "POST", f"/api/kill-switch/{capital_pool_id}/safe-mode", payload
            )
        new_state = self._local().advance_safe_mode(
            capital_pool_id, target_state, actor_id=actor_id, note=note
        )
        return {"capital_pool_id": capital_pool_id, "safe_mode_state": new_state}

    def get_kill_switch_audit_log(self) -> list[Dict[str, Any]]:
        """Return all kill-switch audit entries for this service instance.

        Returns list of audit entry dicts.
        """
        if self._use_http():
            payload = self._request_json("GET", "/api/kill-switch/audit-log")
            return list(payload.get("entries", []))
        return self._local().get_kill_switch_audit_log()

    # ------------------------------------------------------------------
    # Evolution orchestration boundaries
    # ------------------------------------------------------------------

    def evolution_freeze(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute runtime follow-through for an approved evolution freeze decision.

        Required: evolution_decision_id, binding_id, freeze_action, actor_id
        Optional: note

        Returns { evolution_decision_id, freeze_action, binding, actor_id,
                  executed_at, note }.
        """
        if self._use_http():
            return self._request_json("POST", "/api/evolution/freeze", request)
        return self._local().evolution_freeze(request)

    def evolution_retrain(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Record dispatch of an approved retrain/revalidate EvolutionDecision.

        Required: evolution_decision_id, action_type, artifact_id, actor_id
        Optional: note

        Returns { evolution_decision_id, action_type, artifact_id, actor_id,
                  dispatched_at, routing_ref, note }.
        """
        if self._use_http():
            return self._request_json("POST", "/api/evolution/retrain", request)
        return self._local().evolution_retrain(request)

    def evolution_redeploy(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute redeploy follow-through after an approved evolution decision.

        Required: evolution_decision_id, plan_id, target_stage, artifact_id,
                  artifact_version, capital_pool_id, persona_capital_binding_id,
                  persona_capital_binding_status, allowed_deployment_scope,
                  loader_checks_passed
        Optional: plan_status, runtime_id, actor_id, note

        Returns { evolution_decision_id, binding, actor_id, redeployed_at, note }.
        """
        if self._use_http():
            return self._request_json("POST", "/api/evolution/redeploy", request)
        return self._local().evolution_redeploy(request)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _use_http(self) -> bool:
        return bool(self._base_url)

    def _local(self) -> RuntimeManagerService:
        if self._local_service is None:
            store_path = Path(
                os.getenv(
                    "PANTHEON_RUNTIME_BINDING_STORE_PATH",
                    "/tmp/pantheon/runtime-manager/bindings.json",
                )
            )
            store_path.parent.mkdir(parents=True, exist_ok=True)
            single_runtime_enforced = (
                os.getenv("PANTHEON_SINGLE_RUNTIME_ENFORCED", "true").lower() != "false"
            )
            self._local_service = RuntimeManagerService(
                store_path=store_path,
                single_runtime_enforced=single_runtime_enforced,
            )
        return self._local_service

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self._base_url}{path}"
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._bearer_token}",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            payload_dict: Dict[str, Any] = {}
            try:
                payload_dict = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload_dict = {}
            error = payload_dict.get("error", {}) if isinstance(payload_dict, dict) else {}
            raise RuntimeManagerClientError(
                error.get("message") or f"runtime-manager returned HTTP {exc.code}",
                status_code=exc.code,
                error_code=error.get("code"),
                payload=payload_dict,
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeManagerClientError(
                f"runtime-manager unavailable: {getattr(exc, 'reason', exc)}",
                error_code="RUNTIME_MANAGER_UNAVAILABLE",
            ) from exc
        except (RuntimeManagerError, RuntimeBindingError) as exc:
            raise RuntimeManagerClientError(
                str(exc),
                error_code=exc.__class__.__name__,
            ) from exc
