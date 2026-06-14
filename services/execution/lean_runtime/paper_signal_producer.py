"""Paper signal producer.

Fills the gap where active paper ``RuntimeBinding``s have a healthy execution
runtime (the paper ``SignalConsumer`` drain loop) but **no upstream signal
source**, so the loop never closes. Investigation on 2026-06-14 confirmed the
deployed paper workers consume + simulate fills correctly the moment a valid
signal lands on their pending queue; the only missing piece was a producer.

A *strategy* is any callable ``strategy(binding, now_iso) -> list[dict]`` that
returns zero or more signal payloads. The built-in :class:`SmokeStrategy`
emits one deterministic, schema-valid BUY signal (the same minimal strategy the
LEAN algorithm smoke uses), which lets a paper runtime close the loop
end-to-end — consume -> simulated fill -> position -> telemetry — without a
live broker or market-data feed.

Fail-closed by construction: this only ever enqueues *paper* signals that the
runtime matches with simulated fills (``submitted_to_broker=false``). It never
enables live broker connect or order placement; those remain gated by the
runtime activation guard.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

log = logging.getLogger(__name__)

# Signals carry both the consumer field names (version/timestamp) and are
# accepted by the store validator via its schema_version/signal_timestamp
# aliases, so one payload satisfies both validation layers.
SIGNAL_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class BindingRef:
    """The minimal binding context a producer needs to emit a signal."""

    binding_id: str
    strategy_id: str
    symbol: str = "AAPL.US"


# A strategy turns a binding + wall-clock into zero or more signal payloads.
Strategy = Callable[[BindingRef, str], list[dict[str, Any]]]


def build_smoke_signal(
    binding: BindingRef,
    now_iso: str,
    *,
    action: str = "BUY",
    direction: str = "LONG",
    quantity: float = 7,
    quantity_type: str = "SHARES",
    seq: int = 0,
) -> dict[str, Any]:
    """Build one schema-valid signal payload for *binding*.

    The shape satisfies both ``validate_signal_payload_minimal`` (store side)
    and the canonical ``services/research/schema.json`` consumer validation.
    """
    return {
        "version": SIGNAL_SCHEMA_VERSION,
        "signal_id": f"sig-{binding.binding_id}-{now_iso}-{seq}",
        "strategy_id": binding.strategy_id,
        "timestamp": now_iso,
        "symbol": binding.symbol,
        "action": action,
        "direction": direction,
        "quantity": quantity,
        "quantity_type": quantity_type,
        "binding_id": binding.binding_id,
    }


class SmokeStrategy:
    """Minimal deterministic strategy: one BUY signal per tick.

    Useful for proving a paper binding closes the loop and for soak/smoke runs.
    Not an alpha source — real strategies replace this callable.
    """

    def __init__(self, *, quantity: float = 7) -> None:
        self._quantity = quantity
        self._seq = 0

    def __call__(self, binding: BindingRef, now_iso: str) -> list[dict[str, Any]]:
        self._seq += 1
        return [build_smoke_signal(binding, now_iso, quantity=self._quantity, seq=self._seq)]


class PaperSignalProducer:
    """Generate signals for paper bindings and enqueue them onto their queues.

    ``store_for`` maps a :class:`BindingRef` to the ``PendingSignalStore`` whose
    queue the binding's runtime drains (binding-scoped in production). The
    store's ``enqueue`` performs minimal transport validation, so malformed
    signals are rejected here rather than silently lost downstream.
    """

    def __init__(self, store_for: Callable[[BindingRef], Any], strategy: Strategy) -> None:
        self._store_for = store_for
        self._strategy = strategy

    def produce(self, binding: BindingRef, now_iso: str) -> int:
        """Emit + enqueue signals for one binding; return the count enqueued."""
        store = self._store_for(binding)
        signals = self._strategy(binding, now_iso)
        enqueued = 0
        for sig in signals:
            store.enqueue(sig)
            enqueued += 1
        if enqueued:
            log.info(
                "paper_signal_producer enqueued %d signal(s) for binding %s",
                enqueued,
                binding.binding_id,
            )
        return enqueued

    def tick(self, bindings: Sequence[BindingRef], now_iso: str) -> dict[str, int]:
        """Produce for every binding; return {binding_id: enqueued_count}."""
        return {b.binding_id: self.produce(b, now_iso) for b in bindings}


# ---------------------------------------------------------------------------
# Deployable runner
# ---------------------------------------------------------------------------
def parse_bindings_env(raw: str) -> list[BindingRef]:
    """Parse ``PANTHEON_PAPER_PRODUCER_BINDINGS`` JSON into BindingRefs.

    Expected: ``[{"binding_id": "...", "strategy_id": "...", "symbol": "AAPL.US"}, ...]``
    ``symbol`` is optional (defaults to the BindingRef default).
    """
    import json

    if not raw or not raw.strip():
        return []
    items = json.loads(raw)
    if not isinstance(items, list):
        raise ValueError("PANTHEON_PAPER_PRODUCER_BINDINGS must be a JSON list")
    bindings: list[BindingRef] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("each binding entry must be an object")
        binding_id = str(item["binding_id"]).strip()
        strategy_id = str(item["strategy_id"]).strip()
        if not binding_id or not strategy_id:
            raise ValueError("binding_id and strategy_id are required")
        symbol = str(item.get("symbol") or "AAPL.US").strip()
        bindings.append(BindingRef(binding_id=binding_id, strategy_id=strategy_id, symbol=symbol))
    return bindings


def _redis_store_factory(signal_store_url: str):
    """Return a store_for that builds a binding-scoped redis pending store."""
    from services.execution.lean_runtime.pending_signal_store import (
        RedisPendingSignalStore,
        binding_queue_key,
    )

    def store_for(binding: BindingRef):
        return RedisPendingSignalStore(
            signal_store_url, queue_key=binding_queue_key(binding.binding_id)
        )

    return store_for


def main() -> None:  # pragma: no cover - thin deployment glue
    import os
    import time
    from datetime import datetime, timezone

    logging.basicConfig(level=logging.INFO)
    signal_store_url = os.getenv("SIGNAL_STORE_URL", "").strip()
    if not signal_store_url:
        raise SystemExit("SIGNAL_STORE_URL is required (redis://signal-store:6379)")
    bindings = parse_bindings_env(os.getenv("PANTHEON_PAPER_PRODUCER_BINDINGS", ""))
    if not bindings:
        raise SystemExit("PANTHEON_PAPER_PRODUCER_BINDINGS is empty; nothing to produce for")
    interval = float(os.getenv("PAPER_PRODUCER_INTERVAL_SECONDS", "60"))
    producer = PaperSignalProducer(
        store_for=_redis_store_factory(signal_store_url), strategy=SmokeStrategy()
    )
    log.info("paper_signal_producer started; bindings=%d interval=%.0fs", len(bindings), interval)
    while True:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        producer.tick(bindings, now_iso)
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    main()
