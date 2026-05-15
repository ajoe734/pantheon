"""Operator command-plane routes for the deployable runtime-manager service.

`services.control_plane.internal.internal_api` is the legacy operator command
surface, loaded from `services/control-plane/internal/internal_api.py`. It
speaks the `/api/internal/v1/...` paths the BFF dispatches against and delegates
binding mutations through a `RuntimeManagerClient`. When that module runs as
its own Flask process it would HTTP-loopback into the runtime-manager service
for every operator command.

This module folds those routes into the runtime-manager process so the
deployable runtime-manager container is the single command plane:

* Pause/rollback/abort routes mutate the *same* in-process
  `RuntimeManagerService` instance that backs the canonical `/api/runtimes/...`
  surface; no HTTP loopback, no second store.
* Kill-switch routes share the runtime-manager service's
  ``execute_kill_switch`` path so the foundation idempotency record path
  guards both the canonical ``/api/kill-switch/dispatch`` route and the legacy
  ``/api/internal/v1/kill-switch`` operator path. Safe-mode reads from
  ``/api/kill-switch/...`` and ``/api/internal/v1/kill-switch`` observe one
  truth.
* Consultation sponsor decisions and command-state lookups reuse the legacy
  module's persistence helpers.

The legacy module remains importable for unit tests and degraded operator
fallbacks. This file does not duplicate its handler logic; it imports the
module and wires its handlers onto the runtime-manager Flask app after
patching the module-level shims used by the legacy code.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

from flask import Flask

from runtime_manager_client import RuntimeManagerClientError
from service import RuntimeManagerService


class _InProcessRuntimeManagerAdapter:
    """`RuntimeManagerClient`-shaped facade over the in-process service.

    The legacy internal_api routes only call ``get`` / ``transition`` /
    ``retire``. We intentionally implement just that surface and forward to
    whichever `RuntimeManagerService` is current at call time so the adapter
    stays correct across test reloads that reset ``_svc``.
    """

    def __init__(self, service_factory: Callable[[], RuntimeManagerService]) -> None:
        self._service_factory = service_factory

    def get(self, binding_id: str):
        binding = self._service_factory().get(binding_id)
        return binding.to_dict() if binding is not None else None

    def transition(self, binding_id: str, new_status: str):
        binding = self._service_factory().transition(binding_id, new_status)
        return binding.to_dict()

    def retire(self, binding_id: str, *, retired_at: str | None = None):
        binding = self._service_factory().retire(binding_id, retired_at=retired_at)
        return binding.to_dict()


def _derive_legacy_kill_switch_key(request: Dict[str, Any]) -> str:
    """Build a stable idempotency key for legacy operator kill-switch requests.

    The BFF and operator clients do not always supply ``idempotency_key``. We
    fingerprint the request fields that determine the side effect so two
    identical operator requests collapse onto a single foundation idempotency
    record instead of producing divergent kill-switch commands on retry.
    """
    fingerprint_fields = (
        "reason",
        "capital_pool_id",
        "actor_id",
        "binding_id",
        "severity",
        "action_override",
        "fallback_artifact_id",
        "fallback_artifact_version",
    )
    parts = [str(request.get(field, "") or "") for field in fingerprint_fields]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"legacy-ks:{digest}"


class _DictBackedRecord(SimpleNamespace):
    """SimpleNamespace that also exposes the original dict via ``to_dict``."""

    def __init__(self, source: Dict[str, Any]) -> None:
        super().__init__(**source)
        self.__dict__["_source"] = dict(source)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._source)


def _build_outcome_namespace(result: Dict[str, Any]) -> SimpleNamespace:
    """Reconstruct a ``KillSwitchOutcome``-shaped object from a service result."""
    from kill_switch_controller import SafeModeState  # noqa: WPS433 — local import

    command_dict = dict(result.get("command", {}) or {})
    audit_dict = dict(result.get("audit_entry", {}) or {})
    safe_mode_value = result.get("safe_mode_after") or SafeModeState.NORMAL.value
    try:
        safe_mode_after = SafeModeState(safe_mode_value)
    except ValueError:
        safe_mode_after = SafeModeState.NORMAL
    return SimpleNamespace(
        command=_DictBackedRecord(command_dict),
        audit_entry=_DictBackedRecord(audit_dict),
        safe_mode_after=safe_mode_after,
        idempotent_replay=bool(result.get("idempotent_replay")),
    )


class _SharedKillSwitchProxy:
    """Forward attribute access to the runtime-manager kill-switch controller.

    The legacy module uses ``_get_controller()`` which returns the module-level
    ``_controller`` global. By installing this proxy, every legacy access falls
    through to the live controller exposed by the current `RuntimeManagerService`.

    Two state-mutating calls are intercepted:

    * ``dispatch`` is converged through ``service.execute_kill_switch`` so the
      legacy ``/api/internal/v1/kill-switch`` path uses the same foundation
      idempotency record path as ``/api/kill-switch/dispatch``. The proxy
      reconstructs a ``KillSwitchOutcome``-shaped namespace from the service
      response so legacy callers (which expect ``outcome.command``,
      ``outcome.audit_entry``, ``outcome.safe_mode_after``) continue to work.
    * ``advance_safe_mode`` is forwarded but the durable kill-switch snapshot
      is rewritten after each successful call so safe-mode state survives
      container restarts.
    """

    def __init__(self, service_factory: Callable[[], RuntimeManagerService]) -> None:
        self._service_factory = service_factory

    def __getattr__(self, name):
        service = self._service_factory()
        attr = getattr(service._kill_switch, name)
        if name == "advance_safe_mode" and callable(attr):
            def _wrapped(*args, **kwargs):
                result = attr(*args, **kwargs)
                service._persist_ks_state()
                return result
            return _wrapped
        return attr

    # Explicit override: legacy callers do ``ctrl.dispatch(trigger, ...)``.
    # We rewrite the call into a service-level execute_kill_switch invocation
    # so the foundation idempotency record path is the single execution lane
    # for emergency commands.
    def dispatch(
        self,
        trigger,
        *,
        action_override=None,
        fallback_artifact_id: Optional[str] = None,
        fallback_artifact_version: Optional[str] = None,
    ):
        service = self._service_factory()
        request: Dict[str, Any] = {
            "reason": trigger.reason,
            "capital_pool_id": trigger.capital_pool_id,
            "actor_id": trigger.actor_id,
            "context": dict(getattr(trigger, "context", {}) or {}),
        }
        if getattr(trigger, "binding_id", None):
            request["binding_id"] = trigger.binding_id
        if getattr(trigger, "severity", None) is not None:
            request["severity"] = trigger.severity
        if action_override is not None:
            value = getattr(action_override, "value", action_override)
            request["action_override"] = value
        if fallback_artifact_id is not None:
            request["fallback_artifact_id"] = fallback_artifact_id
        if fallback_artifact_version is not None:
            request["fallback_artifact_version"] = fallback_artifact_version

        # Caller-supplied idempotency_key wins; otherwise we derive a stable
        # key from the request fingerprint so identical requests replay through
        # the foundation idempotency record path instead of producing divergent
        # side-effects.
        explicit_key = request["context"].pop("idempotency_key", None) or request["context"].pop(
            "PANTHEON_IDEMPOTENCY_KEY", None
        )
        if explicit_key:
            request["idempotency_key"] = str(explicit_key).strip() or None
        if not request.get("idempotency_key"):
            request["idempotency_key"] = _derive_legacy_kill_switch_key(request)

        result = service.execute_kill_switch(request)
        return _build_outcome_namespace(result)


def register_internal_api_routes(
    app: Flask,
    get_service: Callable[[], RuntimeManagerService],
) -> None:
    """Mount /api/internal/v1/... routes on `app` backed by the shared service.

    Idempotent: re-registration is suppressed by a module-level flag so test
    harnesses that re-import `main` do not duplicate routes.
    """
    import kill_switch_controller as ksc
    from services.control_plane.internal import internal_api as legacy

    legacy._runtime_manager_client = _InProcessRuntimeManagerAdapter(get_service)
    legacy._RuntimeManagerClientError = RuntimeManagerClientError
    # Intentionally leave legacy._RuntimeManagerClient untouched. Smoke tests
    # may reset _runtime_manager_client to None to force a fresh
    # `RuntimeManagerClient` re-creation; the legacy lazy importer must still
    # be able to materialise the canonical HTTP client class.

    # The legacy module captures PANTHEON_COMMAND_STATE_FILE at import time.
    # Re-read here so test harnesses (and operators changing env at deploy time)
    # see their configured path rather than the legacy import-time default.
    import os

    legacy._COMMAND_STATE_FILE = os.getenv(
        "PANTHEON_COMMAND_STATE_FILE",
        legacy._COMMAND_STATE_FILE,
    )

    legacy._KillSwitchController = ksc.KillSwitchController
    legacy._EmergencyTrigger = ksc.EmergencyTrigger
    legacy._KillSwitchActionType = ksc.KillSwitchActionType
    legacy._SafeModeState = ksc.SafeModeState
    legacy._KillSwitchError = ksc.KillSwitchError
    legacy._HardTriggerReason = ksc.HardTriggerReason
    legacy._SoftTriggerReason = ksc.SoftTriggerReason
    legacy._controller = _SharedKillSwitchProxy(get_service)

    existing_rules = {rule.rule for rule in app.url_map.iter_rules()}
    for rule in list(legacy.app.url_map.iter_rules()):
        if rule.endpoint in {"static", "health"}:
            continue
        if rule.rule in existing_rules:
            continue
        view_func = legacy.app.view_functions[rule.endpoint]
        methods = sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}) or ["GET"]
        app.add_url_rule(
            rule.rule,
            endpoint=f"legacy_{rule.endpoint}",
            view_func=view_func,
            methods=methods,
        )
