#!/usr/bin/env python3
"""Autonomous paper-trading signal producer (dev demonstration driver).

Context
-------
The dev paper fleet (`pantheon-paper-runtime-*` workers) consumes trading
signals from Redis and executes paper MarketOrders. Each worker reads its OWN
per-binding queue key `pantheon:signals:pending:<binding_id>` (the binding id is
appended once the runtime resolves its binding), NOT the bare shared key.
Nothing in the deployed stack *produces* signals, so the OODA loops only fire
when hand-fed. This script is the minimal autonomous producer: it emits
schema-valid signals onto each active binding's queue on a fixed cadence so the
loops actually run end-to-end (signal -> fill -> telemetry).

It is a thin, dev-only driver — not a strategy engine. Signals use a configurable
symbol universe (LEAN format `TICKER.MARKET`, e.g. `AAPL.US`) with an alternating
BUY/SELL pattern and small share quantities — enough to drive the paper
execution + telemetry path without overclaiming alpha.

Signal schema: services/research/schema.json (major version 1). Symbols MUST be
`TICKER.MARKET_CODE` (US equities `.US`) or a crypto pair — bare tickers fail the
runtime symbol parser.

Usage
-----
    # fan out 2 signals to each of the given bindings, once
    python3 scripts/paper_signal_producer.py --redis-url redis://signal-store:6379 \
        --bindings rb-aaa,rb-bbb --per-binding-count 2 --ticks 1

    # single explicit queue key (e.g. one binding)
    python3 scripts/paper_signal_producer.py --queue-key pantheon:signals:pending:rb-aaa --count 3 --ticks 1

    # print NDJSON instead of pushing
    python3 scripts/paper_signal_producer.py --emit-only --count 4
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
import uuid

# LEAN symbol format: TICKER.MARKET_CODE (US equities use .US)
DEFAULT_SYMBOLS = ["AAPL.US", "MSFT.US", "NVDA.US", "GOOGL.US", "AMZN.US", "META.US", "TSLA.US", "AMD.US"]
SHARED_KEY = os.getenv("PANTHEON_SIGNAL_QUEUE_KEY", "pantheon:signals:pending")
PER_BINDING_PREFIX = "pantheon:signals:pending:"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_signal(symbol: str, index: int, strategy_id: str, quantity: float) -> dict:
    """Build one schema-valid signal payload (schema major version 1)."""
    buy = index % 2 == 0
    ts = _now_iso()
    sid = str(uuid.uuid4())
    return {
        "signal_id": sid,
        "version": "1.0",
        "strategy_id": strategy_id,
        "timestamp": ts,
        "symbol": symbol,
        "action": "BUY" if buy else "SELL",
        "direction": "LONG",
        "quantity": quantity,
        "quantity_type": "SHARES",
        "schema_version": "1.0",
        "signal_timestamp": ts,
        "source": "paper_signal_producer",
    }


def _connect(redis_url: str):
    import redis

    client = redis.Redis.from_url(redis_url, decode_responses=True)
    client.ping()
    return client


def _targets(args) -> list[tuple[str, int]]:
    """Return list of (queue_key, count) targets for one tick."""
    if args.bindings:
        bindings = [b.strip() for b in args.bindings.split(",") if b.strip()]
        return [(PER_BINDING_PREFIX + b, args.per_binding_count) for b in bindings]
    return [(args.queue_key, args.count)]


def produce_once(client, symbols, targets, strategy_id, quantity) -> int:
    pushed = 0
    idx = 0
    for queue_key, count in targets:
        for _ in range(count):
            sym = symbols[idx % len(symbols)]
            idx += 1
            payload = build_signal(sym, idx, strategy_id, quantity)
            if client is None:
                sys.stdout.write(json.dumps(payload) + "\n")
            else:
                client.rpush(queue_key, json.dumps(payload))
            pushed += 1
    return pushed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--redis-url", default=os.getenv("SIGNAL_STORE_URL", "redis://signal-store:6379"))
    ap.add_argument("--queue-key", default=SHARED_KEY, help="explicit single queue key")
    ap.add_argument("--bindings", default="", help="comma-separated binding ids; fan out per-binding")
    ap.add_argument("--per-binding-count", type=int, default=2, help="signals per binding per tick")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--count", type=int, default=16, help="signals per tick (single-key mode)")
    ap.add_argument("--quantity", type=float, default=3.0)
    ap.add_argument("--strategy-id", default="paper-driver-demo")
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--ticks", type=int, default=0, help="number of ticks (0 = run forever)")
    ap.add_argument("--emit-only", action="store_true")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    client = None if args.emit_only else _connect(args.redis_url)

    tick = 0
    total = 0
    while True:
        tick += 1
        targets = _targets(args)
        n = produce_once(client, symbols, targets, args.strategy_id, args.quantity)
        total += n
        if client is not None:
            sys.stderr.write(f"[tick {tick}] pushed {n} signals across {len(targets)} key(s); total {total}\n")
            sys.stderr.flush()
        if args.ticks and tick >= args.ticks:
            break
        if args.emit_only:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
