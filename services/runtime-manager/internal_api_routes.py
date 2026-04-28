"""Operator command-plane routes for the deployable runtime-manager service.

`services/control_plane/internal_api.py` is the legacy operator command surface.
It speaks the `/api/internal/v1/...` paths the BFF dispatches against and
delegates binding mutations through a `RuntimeManagerClient`. When that module
runs as its own Flask process it would HTTP-loopback into the runtime-manager
service for every operator command.

This module folds those routes into the runtime-manager process so the
deployable runtime-manager container is the single command plane:

* Pause/rollback/abort routes mutate the *same* in-process
  `RuntimeManagerService` instance that backs the canonical `/api/runtimes/...`
  surface; no HTTP loopback, no second store.
* Kill-switch routes share the runtime-manager service's `KillSwitchController`,
  so safe-mode reads from `/api/kill-switch/...` and `/api/internal/v1/kill-switch`
  observe one truth.
* Consultation sponsor decisions and command-state lookups reuse the legacy
  module's persistence helpers.

The legacy module remains importable for unit tests and degraded operator
fallbacks. This file does not duplicate its handler logic; it imports the
module and wires its handlers onto the runtime-manager Flask app after
patching the module-level shims used by the legacy code.
"""
from __future__ import annotations

from typing import Callable

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


class _SharedKillSwitchProxy:
    """Forward attribute access to the runtime-manager kill-switch controller.

    The legacy module uses ``_get_controller()`` which returns the module-level
    ``_controller`` global. By installing this proxy, every legacy access falls
    through to the live controller exposed by the current `RuntimeManagerService`.

    State-mutating calls (``dispatch``, ``advance_safe_mode``) are wrapped so
    the runtime-manager's durable kill-switch snapshot is rewritten after each
    legacy operator command. Without this, legacy ``/api/internal/v1/kill-switch``
    callers would update only in-memory state and lose it on container restart.

    Note: the service's ``execute_kill_switch`` idempotency-record path is not
    replicated here. Convergence with the durable foundation idempotency layer
    is tracked separately.
    """

    _PERSIST_AFTER = frozenset({"dispatch", "advance_safe_mode"})

    def __init__(self, service_factory: Callable[[], RuntimeManagerService]) -> None:
        self._service_factory = service_factory

    def __getattr__(self, name):
        service = self._service_factory()
        attr = getattr(service._kill_switch, name)
        if name in self._PERSIST_AFTER and callable(attr):
            def _wrapped(*args, **kwargs):
                result = attr(*args, **kwargs)
                service._persist_ks_state()
                return result
            return _wrapped
        return attr


def register_internal_api_routes(
    app: Flask,
    get_service: Callable[[], RuntimeManagerService],
) -> None:
    """Mount /api/internal/v1/... routes on `app` backed by the shared service.

    Idempotent: re-registration is suppressed by a module-level flag so test
    harnesses that re-import `main` do not duplicate routes.
    """
    import kill_switch_controller as ksc
    from services.control_plane import internal_api as legacy

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
