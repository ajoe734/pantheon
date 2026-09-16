"""Single owner for Management NL durable command admission/replay.

BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: prior to this module,
``POST /bff/management/nl/ask`` (JSON) went through a durable
admit/wait/replay/complete state machine backed by
``ManagementNlCommandIdempotencyStore``, while
``POST /bff/management/nl/ask/stream`` (SSE) skipped it entirely and called
the provider inline with no dedup, no 409-on-conflict, and no durable
replay on reconnect. There was also a second, legacy, env-flag-gated
idempotency mechanism (an in-memory dict plus
``ManagementAiConversationStore.get_idempotency``/``put_idempotency``) that
could bypass the durable store altogether.

This module is the single, real implementation of the admission/replay
decision logic both transports must share: reserve-or-replay-or-wait via
``admit``, mark a reservation ``complete`` exactly once, or
``mark_uncertain`` it on a known failure so a later retry is possible
in the future (never silently re-executed). ``main.py`` keeps thin
module-level functions (``_mgmt_nl_command_admit`` etc.) that delegate to a
single composed :class:`ManagementNlUseCase` instance so both
``bff_management_nl_ask`` and ``bff_management_nl_ask_stream`` call the
exact same code path -- no per-transport duplicate.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Mapping, Optional, Tuple

from .management_contracts import ManagementNlUseCaseDeps
from ..management_nl_command_idempotency import (
    ManagementNlCommandPayloadConflict,
    ManagementNlCommandRecoveryRequired,
    ManagementNlCommandReservation,
    ManagementNlCommandScope,
    ManagementNlCommandStorageError,
)

_ADMISSION_ERRORS = (
    ManagementNlCommandPayloadConflict,
    ManagementNlCommandRecoveryRequired,
    ManagementNlCommandStorageError,
)


class ManagementNlUseCase:
    """Durable command admission/replay shared by ask and ask/stream."""

    def __init__(self, deps: ManagementNlUseCaseDeps) -> None:
        self._deps = deps

    @staticmethod
    def scope(*, actor_id: str, tenant_id: str, route: str, resolved_key: str) -> ManagementNlCommandScope:
        return ManagementNlCommandScope(
            actor_id=actor_id,
            tenant_id=tenant_id,
            route=route,
            idempotency_key=resolved_key,
        )

    async def admit(
        self,
        *,
        scope: ManagementNlCommandScope,
        request_hash: str,
        display_key: str,
    ) -> Tuple[Optional[ManagementNlCommandReservation], Optional[Dict[str, Any]]]:
        """Reserve ownership, replay a terminal result, or wait on an owner.

        Returns ``(reservation, None)`` when this call is now the owner and
        must invoke the provider; returns ``(None, result)`` when a terminal
        result already exists (fresh reservation completed inline, or a
        concurrent owner reached completion while we waited) and must be
        replayed verbatim instead of invoking the provider again.
        """
        store = self._deps.command_store()
        try:
            admission = await asyncio.to_thread(
                store.admit,
                scope,
                request_hash=request_hash,
                legacy_result=None,
                legacy_terminal=False,
            )
        except _ADMISSION_ERRORS as exc:
            self._deps.raise_admission_error(exc, display_key)

        if admission.state == "owner":
            return admission.reservation, None
        if admission.state == "complete":
            return None, admission.result
        if admission.state != "wait":
            self._deps.raise_admission_error(
                ManagementNlCommandStorageError(
                    f"Unsupported Management NL command admission state: {admission.state}"
                ),
                display_key,
            )

        deadline = asyncio.get_running_loop().time() + self._deps.wait_seconds()
        while True:
            if asyncio.get_running_loop().time() >= deadline:
                self._deps.raise_wait_timeout()
            await asyncio.sleep(self._deps.poll_seconds())
            try:
                admission = await asyncio.to_thread(
                    store.observe,
                    scope,
                    request_hash=request_hash,
                )
            except _ADMISSION_ERRORS as exc:
                self._deps.raise_admission_error(exc, display_key)
            if admission.state == "complete":
                return None, admission.result
            if admission.state != "wait":
                self._deps.raise_admission_error(
                    ManagementNlCommandStorageError(
                        f"Unsupported Management NL command observation state: {admission.state}"
                    ),
                    display_key,
                )

    async def complete(
        self,
        reservation: Optional[ManagementNlCommandReservation],
        result: Mapping[str, Any],
        *,
        display_key: str,
    ) -> None:
        """Persist the terminal result for a reservation exactly once.

        A no-op when ``reservation`` is ``None`` (a replayed/legacy-owned
        result never held a reservation of its own).
        """
        if reservation is None:
            return
        store = self._deps.command_store()
        try:
            await asyncio.to_thread(store.complete, reservation, result)
        except _ADMISSION_ERRORS as exc:
            self._deps.raise_admission_error(exc, display_key)

    async def mark_uncertain(
        self,
        reservation: Optional[ManagementNlCommandReservation],
        *,
        reason: str,
        on_failure: Optional[Any] = None,
    ) -> None:
        """Fail closed after a known owner error without releasing the key.

        A prior uncertain reservation is never silently retried; it becomes
        retryable again only once the store's recovery window elapses.
        """
        if reservation is None:
            return
        store = self._deps.command_store()
        try:
            await asyncio.to_thread(store.mark_uncertain, reservation, reason=reason)
        except Exception:  # noqa: BLE001 - best-effort; caller already failed
            if on_failure is not None:
                on_failure()
