"""Live gate adapter for the OpenClaw gateway adapter.

Enforces all pre-conditions required before a live broker handoff may proceed.
Default state is fail-closed: every gate must pass explicitly before the dry
handoff is permitted.  No real broker execution occurs here — the dry handoff
records the intent to the audit log and returns a structured preview payload.

Gate checks enforced in order:
  1. OPENCLAW_LIVE_ADAPTER_ENABLED env var (disabled by default)
  2. Human approval token (X-Human-Approval-Token header must match configured secret)
  3. Active live RuntimeBinding exists for the capital pool (via runtime-manager)
  4. Kill switch safe mode is NORMAL or NORMAL_RESTORED (via runtime-manager)
  5. Active binding is not in a rollback / liquidation state

Env vars:
  OPENCLAW_LIVE_ADAPTER_ENABLED         — "true" enables the gate checks (default: false)
  OPENCLAW_LIVE_HUMAN_APPROVAL_TOKEN    — required approval secret (no default)
  OPENCLAW_RUNTIME_MANAGER_URL          — runtime-manager base URL
  PANTHEON_RUNTIME_MANAGER_URL          — fallback runtime-manager URL
  PANTHEON_RUNTIME_MANAGER_TOKEN        — bearer token for runtime-manager calls
  OPENCLAW_LIVE_GATE_AUDIT_PATH         — override audit log path
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# Safe mode states that permit live handoff
_SAFE_MODE_LIVE_ALLOWED = frozenset({"normal", "normal_restored"})

# Binding statuses that indicate rollback or liquidation in progress
_BINDING_UNSAFE_STATUSES = frozenset({"pending_pause", "paused", "retired", "failed"})


class LiveGateError(Exception):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        status_code: int = 403,
        gate: str = "live_gate",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.gate = gate
        self.details = details or {}

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": "live_gate_error",
            "error_code": self.error_code,
            "message": self.message,
            "gate": self.gate,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class LiveGateAuditLog:
    """Append-only JSONL audit log for live gate intents and outcomes."""

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        clock: Callable[[], str] = _utc_now_iso,
    ) -> None:
        if path is None:
            raw = os.getenv("OPENCLAW_LIVE_GATE_AUDIT_PATH", "")
            path = raw or "/tmp/openclaw-gateway-adapter/live_gate_audit.jsonl"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()

    def record(self, entry: Dict[str, Any]) -> None:
        entry.setdefault("at", self._clock())
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def read(
        self,
        *,
        operator_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        results: List[Dict[str, Any]] = []
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        entry = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if operator_id and entry.get("operator_id") != operator_id:
                        continue
                    if capital_pool_id and entry.get("capital_pool_id") != capital_pool_id:
                        continue
                    results.append(entry)
        except OSError:
            return []
        return results[-limit:]


class LiveGateAdapter:
    """Enforces all live gate pre-conditions before permitting a dry handoff.

    Live orders are never executed — the dry handoff records intent and returns
    a structured preview payload with gate attestation.

    All gate checks fail closed: a missing, misconfigured, or ambiguous state
    is treated as a denial, not a pass.
    """

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        runtime_manager_url: Optional[str] = None,
        runtime_manager_token: Optional[str] = None,
        human_approval_token: Optional[str] = None,
        binding_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        safe_mode_resolver: Optional[Callable[[str], str]] = None,
        audit_log: Optional[LiveGateAuditLog] = None,
        clock: Callable[[], str] = _utc_now_iso,
        trace_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        if enabled is None:
            enabled = os.getenv("OPENCLAW_LIVE_ADAPTER_ENABLED", "").lower() in {"1", "true", "yes"}
        if runtime_manager_url is None:
            runtime_manager_url = (
                os.getenv("OPENCLAW_RUNTIME_MANAGER_URL", "")
                or os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "")
            )
        if runtime_manager_token is None:
            runtime_manager_token = os.getenv("PANTHEON_RUNTIME_MANAGER_TOKEN", "runtime-control-internal")
        if human_approval_token is None:
            human_approval_token = os.getenv("OPENCLAW_LIVE_HUMAN_APPROVAL_TOKEN", "")
        self._enabled = enabled
        self._runtime_manager_url = (runtime_manager_url or "").rstrip("/")
        self._runtime_manager_token = (runtime_manager_token or "").strip()
        self._human_approval_token = (human_approval_token or "").strip()
        self._binding_resolver = binding_resolver
        self._safe_mode_resolver = safe_mode_resolver
        self._audit = audit_log or LiveGateAuditLog()
        self._clock = clock
        self._trace_id_factory = trace_id_factory

    @property
    def enabled(self) -> bool:
        return self._enabled

    def capability_snapshot(self) -> Dict[str, Any]:
        return {
            "live_gate_enabled": self._enabled,
            "live_adapter_enabled": False,
            "human_approval_configured": bool(self._human_approval_token),
            "runtime_manager_configured": bool(self._runtime_manager_url or self._binding_resolver),
            "gate_checks": [
                "live_adapter_enabled",
                "human_approval_token",
                "active_live_runtime_binding",
                "kill_switch_safe_mode",
                "binding_not_in_rollback",
            ],
            "deployment_stage": "live_gate_harness",
            "is_real_capital": False,
            "is_real_order": False,
            "dry_handoff_only": True,
        }

    # ------------------------------------------------------------------
    # Public gate API
    # ------------------------------------------------------------------

    def check_all_gates(
        self,
        *,
        capital_pool_id: str,
        human_approval_token: str,
        operator_id: str,
    ) -> Dict[str, Any]:
        """Run all gate checks and return a structured attestation.

        Raises LiveGateError on any gate failure (fail-closed).
        Returns the attestation dict when all gates pass.
        """
        # Gate 1: Live adapter enabled
        self._gate_enabled_check()

        # Gate 2: Human approval token
        self._human_approval_check(human_approval_token)

        # Gate 3: Active live RuntimeBinding
        binding = self._binding_check(capital_pool_id=capital_pool_id, operator_id=operator_id)

        # Gate 4: Kill switch safe mode
        safe_mode = self._safe_mode_check(capital_pool_id=capital_pool_id)

        # Gate 5: Binding not in rollback/liquidation state
        self._rollback_policy_check(binding)

        return {
            "gates_passed": True,
            "capital_pool_id": capital_pool_id,
            "operator_id": operator_id,
            "binding_id": binding.get("binding_id"),
            "artifact_id": binding.get("artifact_id"),
            "safe_mode": safe_mode,
            "deployment_mode": binding.get("deployment_mode"),
            "checked_at": self._clock(),
        }

    def dry_handoff(
        self,
        *,
        capital_pool_id: str,
        strategy_id: str,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        operator_id: str,
        human_approval_token: str,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run all gate checks and record a dry handoff intent to the audit log.

        No real broker execution occurs.  The handoff payload is returned as a
        structured preview with gate attestation.  The audit log records the full
        intent and outcome regardless of gate result.
        """
        trace_id = trace_id or self._trace_id_factory()
        base_audit: Dict[str, Any] = {
            "event": "live_gate_dry_handoff_intent",
            "trace_id": trace_id,
            "operator_id": operator_id,
            "capital_pool_id": capital_pool_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "limit_price": limit_price,
            "is_real_order": False,
            "is_real_capital": False,
            "deployment_stage": "live_gate_harness",
            "dry_handoff": True,
        }
        self._audit.record({**base_audit, "outcome": "pending"})

        try:
            attestation = self.check_all_gates(
                capital_pool_id=capital_pool_id,
                human_approval_token=human_approval_token,
                operator_id=operator_id,
            )
        except LiveGateError as exc:
            self._audit.record(
                {
                    **base_audit,
                    "outcome": "denied",
                    "error_code": exc.error_code,
                    "error": exc.message,
                    "gate": exc.gate,
                }
            )
            raise

        result = {
            "status": "dry_handoff_accepted",
            "dry_handoff": True,
            "is_real_order": False,
            "is_real_capital": False,
            "deployment_stage": "live_gate_harness",
            "trace_id": trace_id,
            "capital_pool_id": capital_pool_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "limit_price": limit_price,
            "gate_attestation": attestation,
            "note": (
                "This is a dry handoff only. No real broker execution occurs. "
                "Live activation requires completing the EP5 human gate and "
                "enabling the live broker connector."
            ),
        }
        self._audit.record(
            {
                **base_audit,
                "outcome": "dry_handoff_accepted",
                "binding_id": attestation.get("binding_id"),
                "artifact_id": attestation.get("artifact_id"),
                "safe_mode": attestation.get("safe_mode"),
            }
        )
        return result

    def reject_live_order(self, operator_id: Optional[str] = None) -> None:
        """Always reject live orders. Gate state is irrelevant."""
        self._audit.record(
            {
                "event": "live_order_rejected",
                "operator_id": operator_id or "",
                "is_real_order": False,
                "is_real_capital": False,
                "live_enabled": False,
                "outcome": "rejected",
            }
        )
        raise LiveGateError(
            "LIVE_EXECUTION_DISABLED",
            (
                "Live broker execution is permanently disabled. "
                "Use POST /api/openclaw-adapter/broker/live/gate/dry-handoff for gate validation."
            ),
            status_code=403,
            gate="live_execution",
            details={"live_enabled": False, "deployment_stage": "live_gate_harness"},
        )

    def read_audit(
        self,
        *,
        operator_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return self._audit.read(
            operator_id=operator_id,
            capital_pool_id=capital_pool_id,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Gate checks (internal — each raises LiveGateError on failure)
    # ------------------------------------------------------------------

    def _gate_enabled_check(self) -> None:
        if not self._enabled:
            raise LiveGateError(
                "LIVE_GATE_DISABLED",
                (
                    "Live gate is disabled. "
                    "Set OPENCLAW_LIVE_ADAPTER_ENABLED=true to enable gate checks. "
                    "Live execution still requires all gate checks to pass."
                ),
                status_code=503,
                gate="live_adapter_enabled",
            )

    def _human_approval_check(self, provided_token: str) -> None:
        if not self._human_approval_token:
            raise LiveGateError(
                "HUMAN_APPROVAL_NOT_CONFIGURED",
                (
                    "OPENCLAW_LIVE_HUMAN_APPROVAL_TOKEN is not configured. "
                    "Human approval must be configured before live gate checks can proceed."
                ),
                status_code=503,
                gate="human_approval_token",
            )
        if not provided_token or not provided_token.strip():
            raise LiveGateError(
                "HUMAN_APPROVAL_TOKEN_MISSING",
                "X-Human-Approval-Token header is required for live gate handoff.",
                status_code=401,
                gate="human_approval_token",
            )
        if provided_token.strip() != self._human_approval_token:
            raise LiveGateError(
                "HUMAN_APPROVAL_TOKEN_INVALID",
                "X-Human-Approval-Token does not match the configured approval secret.",
                status_code=403,
                gate="human_approval_token",
            )

    def _binding_check(self, *, capital_pool_id: str, operator_id: str) -> Dict[str, Any]:
        if not capital_pool_id:
            raise LiveGateError(
                "CAPITAL_POOL_REQUIRED",
                "capital_pool_id is required for live gate handoff.",
                status_code=400,
                gate="active_live_runtime_binding",
            )
        binding = self._resolve_active_binding(capital_pool_id)
        if not binding:
            raise LiveGateError(
                "LIVE_RUNTIME_BINDING_NOT_FOUND",
                (
                    "No active RuntimeBinding was found for this capital_pool_id. "
                    "Live gate handoff requires an active live RuntimeBinding."
                ),
                status_code=409,
                gate="active_live_runtime_binding",
                details={"capital_pool_id": capital_pool_id},
            )
        if binding.get("deployment_mode") != "live":
            raise LiveGateError(
                "LIVE_RUNTIME_BINDING_REQUIRED",
                "RuntimeBinding deployment_mode must be 'live' for live gate handoff.",
                status_code=409,
                gate="active_live_runtime_binding",
                details={
                    "capital_pool_id": capital_pool_id,
                    "binding_id": binding.get("binding_id"),
                    "deployment_mode": binding.get("deployment_mode"),
                },
            )
        return binding

    def _safe_mode_check(self, *, capital_pool_id: str) -> str:
        safe_mode = self._resolve_safe_mode(capital_pool_id)
        if safe_mode not in _SAFE_MODE_LIVE_ALLOWED:
            raise LiveGateError(
                "KILL_SWITCH_UNSAFE_STATE",
                (
                    f"Kill switch safe mode is '{safe_mode}'. "
                    "Live gate handoff requires safe_mode_state to be 'normal' or 'normal_restored'. "
                    "Operator must clear the emergency state before live handoff is permitted."
                ),
                status_code=409,
                gate="kill_switch_safe_mode",
                details={
                    "capital_pool_id": capital_pool_id,
                    "safe_mode_state": safe_mode,
                    "allowed_states": sorted(_SAFE_MODE_LIVE_ALLOWED),
                },
            )
        return safe_mode

    def _rollback_policy_check(self, binding: Dict[str, Any]) -> None:
        status = (binding.get("status") or "").lower()
        if status in _BINDING_UNSAFE_STATUSES:
            raise LiveGateError(
                "BINDING_IN_UNSAFE_STATE",
                (
                    f"RuntimeBinding status is '{status}'. "
                    "Live gate handoff requires the binding to be in 'active' status. "
                    "A rollback, liquidation, or pause may be in progress."
                ),
                status_code=409,
                gate="binding_not_in_rollback",
                details={
                    "binding_id": binding.get("binding_id"),
                    "status": status,
                    "allowed_status": "active",
                },
            )
        if status != "active":
            raise LiveGateError(
                "BINDING_NOT_ACTIVE",
                f"RuntimeBinding status '{status}' is not 'active'. Live gate handoff is denied.",
                status_code=409,
                gate="binding_not_in_rollback",
                details={"binding_id": binding.get("binding_id"), "status": status},
            )

    # ------------------------------------------------------------------
    # Runtime-manager calls
    # ------------------------------------------------------------------

    def _resolve_active_binding(self, capital_pool_id: str) -> Optional[Dict[str, Any]]:
        if self._binding_resolver is not None:
            try:
                return self._binding_resolver(capital_pool_id)
            except LiveGateError:
                raise
            except Exception as exc:
                raise LiveGateError(
                    "RUNTIME_BINDING_CHECK_FAILED",
                    f"RuntimeBinding resolver failed: {exc}",
                    status_code=503,
                    gate="active_live_runtime_binding",
                ) from exc
        if not self._runtime_manager_url:
            raise LiveGateError(
                "RUNTIME_MANAGER_NOT_CONFIGURED",
                (
                    "OPENCLAW_RUNTIME_MANAGER_URL or PANTHEON_RUNTIME_MANAGER_URL "
                    "must be configured before live gate checks can proceed."
                ),
                status_code=503,
                gate="active_live_runtime_binding",
            )
        return self._call_runtime_manager_active_binding(capital_pool_id)

    def _call_runtime_manager_active_binding(self, capital_pool_id: str) -> Optional[Dict[str, Any]]:
        try:
            import httpx

            encoded_pool_id = urllib.parse.quote(capital_pool_id, safe="")
            headers = {"Accept": "application/json"}
            if self._runtime_manager_token:
                headers["Authorization"] = f"Bearer {self._runtime_manager_token}"
            with httpx.Client(timeout=5) as client:
                response = client.get(
                    f"{self._runtime_manager_url}/api/runtimes/{encoded_pool_id}/active",
                    headers=headers,
                )
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = {"message": response.text}
                error = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
                raise LiveGateError(
                    error.get("code") or body.get("error_code") or "RUNTIME_MANAGER_ERROR",
                    error.get("message") or body.get("message") or f"runtime-manager returned HTTP {response.status_code}",
                    status_code=response.status_code if response.status_code < 500 else 502,
                    gate="active_live_runtime_binding",
                    details=body,
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise LiveGateError(
                    "RUNTIME_BINDING_SCHEMA_ERROR",
                    "runtime-manager active binding response did not match the expected schema.",
                    status_code=502,
                    gate="active_live_runtime_binding",
                    details={"payload_type": type(payload).__name__},
                )
            return payload
        except LiveGateError:
            raise
        except Exception as exc:
            raise LiveGateError(
                "RUNTIME_MANAGER_UNAVAILABLE",
                f"runtime-manager is unavailable: {exc}",
                status_code=503,
                gate="active_live_runtime_binding",
            ) from exc

    def _resolve_safe_mode(self, capital_pool_id: str) -> str:
        if self._safe_mode_resolver is not None:
            try:
                return self._safe_mode_resolver(capital_pool_id)
            except LiveGateError:
                raise
            except Exception as exc:
                raise LiveGateError(
                    "SAFE_MODE_CHECK_FAILED",
                    f"Safe mode resolver failed: {exc}",
                    status_code=503,
                    gate="kill_switch_safe_mode",
                ) from exc
        if not self._runtime_manager_url:
            raise LiveGateError(
                "RUNTIME_MANAGER_NOT_CONFIGURED",
                (
                    "OPENCLAW_RUNTIME_MANAGER_URL must be configured "
                    "before kill switch safe mode can be checked."
                ),
                status_code=503,
                gate="kill_switch_safe_mode",
            )
        return self._call_runtime_manager_safe_mode(capital_pool_id)

    def _call_runtime_manager_safe_mode(self, capital_pool_id: str) -> str:
        try:
            import httpx

            encoded_pool_id = urllib.parse.quote(capital_pool_id, safe="")
            headers = {"Accept": "application/json"}
            if self._runtime_manager_token:
                headers["Authorization"] = f"Bearer {self._runtime_manager_token}"
            with httpx.Client(timeout=5) as client:
                response = client.get(
                    f"{self._runtime_manager_url}/api/kill-switch/{encoded_pool_id}/safe-mode",
                    headers=headers,
                )
            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = {"message": response.text}
                raise LiveGateError(
                    "SAFE_MODE_CHECK_ERROR",
                    f"runtime-manager safe-mode check failed: HTTP {response.status_code}",
                    status_code=502,
                    gate="kill_switch_safe_mode",
                    details=body,
                )
            payload = response.json()
            safe_mode = str(payload.get("safe_mode_state") or "").lower().strip()
            if not safe_mode:
                raise LiveGateError(
                    "SAFE_MODE_SCHEMA_ERROR",
                    "runtime-manager safe-mode response did not include safe_mode_state.",
                    status_code=502,
                    gate="kill_switch_safe_mode",
                    details={"payload": payload},
                )
            return safe_mode
        except LiveGateError:
            raise
        except Exception as exc:
            raise LiveGateError(
                "RUNTIME_MANAGER_UNAVAILABLE",
                f"runtime-manager is unavailable for safe-mode check: {exc}",
                status_code=503,
                gate="kill_switch_safe_mode",
            ) from exc
