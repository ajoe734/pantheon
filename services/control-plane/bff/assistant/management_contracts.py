"""Typed contracts for the Management NL single-use-case seam.

BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: durable command admission/replay for
``POST /bff/management/nl/ask`` and ``POST /bff/management/nl/ask/stream``
must be exactly one implementation shared by both HTTP transports, not a
per-transport copy. :class:`ManagementNlUseCaseDeps` carries every
collaborator :class:`~.management_service.ManagementNlUseCase` needs as an
explicit injected callable -- it has zero import-time dependency on
``main.py`` globals, mirroring the seam pattern established by
``AssistantSourceCollectorDeps`` in ``source_collectors.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, NoReturn, Optional

from ..management_nl_command_idempotency import ManagementNlCommandIdempotencyStore


@dataclass
class ManagementNlUseCaseDeps:
    """Collaborators for :class:`~.management_service.ManagementNlUseCase`.

    All fields close over real main.py runtime state (the durable store
    factory, which is itself env/config-derived and lazily (re)built, plus
    the wait/poll timing knobs and the error-shaping callables that turn a
    store exception into the route's exact HTTP error contract). None of
    them have a context-free default, so every field is required.
    """

    # Returns the (lazily constructed / config-cached) durable command
    # admission store. A callable rather than a bound instance so config
    # changes (env var edits between requests, e.g. in tests) still apply.
    command_store: Callable[[], ManagementNlCommandIdempotencyStore]

    # How long a caller should wait for a concurrent, exact-duplicate
    # request to reach a terminal result before giving up with 409.
    wait_seconds: Callable[[], float]

    # Poll interval while waiting on a concurrent owner's reservation.
    poll_seconds: Callable[[], float]

    # Raises the route's typed HTTPException for a known admission error
    # (payload conflict / recovery required / storage error). Must not
    # return normally.
    raise_admission_error: Callable[[Exception, str], NoReturn]

    # Raises the route's typed 409 for "an exact concurrent request owns
    # this idempotency key and has not reached a terminal result" once the
    # wait deadline elapses. Must not return normally.
    raise_wait_timeout: Callable[[], NoReturn]
