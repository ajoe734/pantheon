"""Paper signal producer.

Fills the gap where active paper ``RuntimeBinding``s have a healthy execution
runtime (the paper ``SignalConsumer`` drain loop) but **no upstream signal
source**, so the loop never closes.

Discovers eligible paper bindings from the runtime-manager and uses a
first-class ``DecisionSignalProducer`` to validate and enqueue identity-complete
signals onto their binding-scoped Redis queues.

Fail-closed by construction: this only ever enqueues *paper* signals that the
runtime matches with simulated fills (``submitted_to_broker=false``). It never
enables live broker connect or order placement; those remain gated by the
runtime activation guard.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

log = logging.getLogger(__name__)

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


class BoundedPaperStrategy:
    """First-class bounded paper strategy.

    Generates identity-complete decision dictionary carrying tenant, persona,
    binding, runtime, and pool identities, asserting simulated order constraints.
    """

    def __init__(self, *, quantity: float = 7, symbol: str = "AAPL.US") -> None:
        self._quantity = quantity
        self._symbol = symbol
        self._seq = 0

    def __call__(self, binding: dict[str, Any] | BindingRef, now_iso: str) -> dict[str, Any]:
        self._seq += 1
        b_dict = _binding_dict_from_obj(binding)
        binding_id = b_dict["binding_id"]
        runtime_id = b_dict["runtime_id"]
        capital_pool_id = b_dict["capital_pool_id"]
        artifact_id = b_dict["artifact_id"]
        artifact_version = b_dict["artifact_version"]
        pcb_id = b_dict.get("persona_capital_binding_id")

        # Extract persona_id from pcb_id (e.g. pcb-smoke-001 -> smoke)
        persona_id = "default-persona"
        if pcb_id and pcb_id.startswith("pcb-"):
            parts = pcb_id.split("-")
            if len(parts) >= 2:
                persona_id = parts[1]

        tenant_id = os.getenv("PANTHEON_TENANT_ID", "default")
        strategy_id = b_dict.get("metadata", {}).get("strategy_id") or b_dict.get("strategy_id") or "smoke-strategy"
        symbol = b_dict.get("symbol") or self._symbol

        decision = {
            "decision_id": f"dec-{binding_id}-{now_iso}-{self._seq}",
            "strategy_id": strategy_id,
            "timestamp": now_iso,
            "symbol": symbol,
            "action": "BUY",
            "direction": "LONG",
            "quantity": self._quantity,
            "quantity_type": "SHARES",
            "binding_id": binding_id,
            "runtime_id": runtime_id,
            "capital_pool_id": capital_pool_id,
            "run_id": f"run-{binding_id}-{now_iso}",
            "source_worker": "paper-signal-producer",
            "metadata": {
                "tenant_id": tenant_id,
                "persona_id": persona_id,
                "persona_capital_binding_id": pcb_id,
                "capital_pool_id": capital_pool_id,
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
                "is_real_order": False,
                "is_real_capital": False,
            }
        }
        return decision


def _binding_dict_from_obj(binding: Any) -> dict[str, Any]:
    if isinstance(binding, dict):
        return binding
    return {
        "binding_id": getattr(binding, "binding_id", ""),
        "runtime_id": getattr(binding, "runtime_id", "rt-default"),
        "capital_pool_id": getattr(binding, "capital_pool_id", "pool-default"),
        "artifact_id": getattr(binding, "artifact_id", "art-default"),
        "artifact_version": getattr(binding, "artifact_version", "1.0.0"),
        "persona_capital_binding_id": getattr(binding, "persona_capital_binding_id", "pcb-default-001"),
        "symbol": getattr(binding, "symbol", "AAPL.US"),
        "strategy_id": getattr(binding, "strategy_id", "smoke-strategy"),
    }


class PaperSignalProducer:
    """Generate signals for paper bindings and enqueue them onto their queues.

    Drains or ticks across all eligible paper bindings, setting up isolated Redis
    keys and validation per binding.
    """

    def __init__(self, store_for: Callable[[Any], Any], strategy: Any) -> None:
        self._store_for = store_for
        self._strategy = strategy

    def produce(self, binding: Any, now_iso: str) -> int:
        """Emit + enqueue signals for one binding; return the count enqueued."""
        b_dict = _binding_dict_from_obj(binding)
        binding_id = b_dict["binding_id"]
        store = self._store_for(binding)

        # Generate strategy output
        out = self._strategy(binding, now_iso)

        enqueued = 0
        if isinstance(out, list):
            # Legacy list-of-signals flow
            for sig in out:
                if "metadata" not in sig:
                    sig["metadata"] = {}
                if isinstance(sig["metadata"], dict):
                    sig["metadata"]["is_real_order"] = False
                    sig["metadata"]["is_real_capital"] = False
                    sig["metadata"]["tenant_id"] = os.getenv("PANTHEON_TENANT_ID", "default")
                store.enqueue(sig)
                enqueued += 1
        elif isinstance(out, dict):
            # First-class decision dict flow
            from services.execution.lean_runtime.signal_producer import DecisionSignalProducer
            dsp = DecisionSignalProducer(store)

            if "metadata" not in out:
                out["metadata"] = {}
            if isinstance(out["metadata"], dict):
                out["metadata"]["is_real_order"] = False
                out["metadata"]["is_real_capital"] = False
                out["metadata"]["tenant_id"] = os.getenv("PANTHEON_TENANT_ID", "default")

            batch = dsp.produce(
                out,
                now_iso=now_iso,
                strategy_id=out.get("strategy_id"),
                source_worker=out.get("source_worker", "paper-signal-producer"),
                binding_id=binding_id,
                runtime_id=b_dict.get("runtime_id"),
                run_id=out.get("run_id"),
            )
            enqueued = batch.count

        if enqueued:
            log.info(
                "paper_signal_producer enqueued %d signal(s) for binding %s",
                enqueued,
                binding_id,
            )
        return enqueued

    def tick(self, bindings: Sequence[Any], now_iso: str) -> dict[str, int]:
        """Produce for every binding; return {binding_id: enqueued_count}."""
        return {
            _binding_dict_from_obj(b)["binding_id"]: self.produce(b, now_iso)
            for b in bindings
        }


# ---------------------------------------------------------------------------
# Deployable runner / Binding Discovery
# ---------------------------------------------------------------------------

def fetch_eligible_paper_bindings(
    runtime_manager_url: str,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch active paper bindings from runtime-manager desired state."""
    import urllib.request
    import json

    if not runtime_manager_url:
        return []

    url = f"{runtime_manager_url.rstrip('/')}/api/runtime-fleet/desired-state?stage=paper"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        bindings = payload.get("bindings", [])
        # Only return eligible active paper bindings
        return [b for b in bindings if b.get("status") == "active"]
    except Exception as e:
        log.warning("Failed to fetch active bindings from runtime-manager: %s", e)
        return []


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

    def store_for(binding_or_id: Any):
        if isinstance(binding_or_id, str):
            bid = binding_or_id
        else:
            bid = getattr(binding_or_id, "binding_id", "")
            if not bid and isinstance(binding_or_id, dict):
                bid = binding_or_id.get("binding_id", "")
        return RedisPendingSignalStore(
            signal_store_url, queue_key=binding_queue_key(bid)
        )

    return store_for


def main() -> None:  # pragma: no cover
    import os
    import time
    from datetime import datetime, timezone

    logging.basicConfig(level=logging.INFO)
    signal_store_url = os.getenv("SIGNAL_STORE_URL", "").strip()
    if not signal_store_url:
        raise SystemExit("SIGNAL_STORE_URL is required (redis://signal-store:6379)")

    runtime_manager_url = os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip()
    runtime_manager_token = os.getenv("PANTHEON_RUNTIME_MANAGER_TOKEN", "").strip()

    interval = float(os.getenv("PAPER_PRODUCER_INTERVAL_SECONDS", "60"))
    producer = PaperSignalProducer(
        store_for=_redis_store_factory(signal_store_url), strategy=BoundedPaperStrategy()
    )
    log.info("paper_signal_producer started; interval=%.0fs", interval)

    while True:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Dynamic active binding resolution
        bindings = []
        if runtime_manager_url:
            bindings = fetch_eligible_paper_bindings(runtime_manager_url, runtime_manager_token)
            log.info("Discovered %d active paper bindings from runtime-manager", len(bindings))
        else:
            raw_bindings = os.getenv("PANTHEON_PAPER_PRODUCER_BINDINGS", "")
            if raw_bindings:
                bindings = parse_bindings_env(raw_bindings)
                log.info("Discovered %d bindings from PANTHEON_PAPER_PRODUCER_BINDINGS env", len(bindings))

        if bindings:
            producer.tick(bindings, now_iso)
        else:
            log.debug("No active paper bindings discovered; skipping tick")

        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    main()
