"""Process-wide SSE shutdown signal.

uvicorn waits for open streaming responses before it runs the ASGI lifespan
shutdown, so the lifespan alone cannot end SSE generators. The lifespan
startup chains a SIGTERM/SIGINT hook that calls :func:`begin_shutdown`; every
SSE generator races its waits against :func:`wait_shutdown` and ends cleanly.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import threading
from typing import Any, Awaitable, Optional, Tuple, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

_event: Optional[asyncio.Event] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_requested = False


def _get_event() -> asyncio.Event:
    global _event, _loop
    loop = asyncio.get_running_loop()
    if _event is None or _loop is not loop:
        _event = asyncio.Event()
        _loop = loop
        if _requested:
            _event.set()
    return _event


def is_shutting_down() -> bool:
    return _requested


def begin_shutdown() -> None:
    """Signal all SSE generators to end. Safe to call from any thread."""
    global _requested
    _requested = True
    event, loop = _event, _loop
    if event is None or loop is None or loop.is_closed():
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        event.set()
    else:
        loop.call_soon_threadsafe(event.set)


def reset() -> None:
    global _event, _loop, _requested
    _event, _loop, _requested = None, None, False


async def wait_shutdown() -> None:
    await _get_event().wait()


async def race(awaitable: Awaitable[T]) -> Tuple[bool, Optional[T]]:
    """Await ``awaitable`` unless shutdown fires first.

    Returns ``(True, None)`` on shutdown (the awaitable is cancelled) and
    ``(False, result)`` otherwise.
    """
    if _requested:
        task = asyncio.ensure_future(awaitable)
        task.cancel()
        return True, None
    task = asyncio.ensure_future(awaitable)
    stop = asyncio.ensure_future(wait_shutdown())
    try:
        await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop.cancel()
        if not task.done():
            task.cancel()
    if task.done() and not task.cancelled():
        return False, task.result()
    return True, None


def install_signal_hooks() -> Any:
    """Chain begin_shutdown() in front of the current SIGTERM/SIGINT handlers.

    Returns a callable restoring the previous handlers. No-op off the main
    thread (signal.signal is main-thread only).
    """
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    previous = {}

    def _make(prev):
        def _handler(signum, frame):
            begin_shutdown()
            if callable(prev):
                prev(signum, frame)
            elif prev == signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                signal.raise_signal(signum)
        return _handler

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            prev = signal.getsignal(sig)
            signal.signal(sig, _make(prev))
            previous[sig] = prev
        except (ValueError, OSError):
            log.debug("could not chain signal %s", sig)

    def _restore() -> None:
        for sig, prev in previous.items():
            try:
                signal.signal(sig, prev)
            except (ValueError, OSError):
                pass

    return _restore
