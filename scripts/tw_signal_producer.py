#!/usr/bin/env python3
"""REAL tw_session_momentum signal producer (correct home).

Reads the active Taiwan Equity paper RuntimeBinding, resolves parameters from
its registered StrategyArtifact, computes momentum based on the StrategyArtifact
parameterized logic using evaluate_strategy_action, validates the signals using
the canonical minimal signal store validator, and emits schema-valid signals to
the active binding queue.
"""
import json
import subprocess
import uuid
import datetime
import sys
import os

# Set up python path to import from the repository
sys.path.insert(0, "/home/lupin/code/pantheon")
sys.path.insert(0, "/tmp/pantheon-worker-worktrees/pantheon/evoloop-007")

from services.registry.strategy_artifact import evaluate_strategy_action
from services.execution.lean_runtime.pending_signal_store import validate_signal_payload_minimal

def main():
    # 1. Fetch active binding from runtime manager
    try:
        out = subprocess.run(
            ["docker", "exec", "pantheon-runtime-manager-1", "cat", "/data/runtime/runtime_bindings.json"],
            capture_output=True, text=True, timeout=10
        )
        if out.returncode != 0:
            print("ERROR: failed to read runtime bindings from container", file=sys.stderr)
            return 1
        bindings = json.loads(out.stdout)
    except Exception as e:
        print(f"ERROR: could not retrieve runtime bindings: {e}", file=sys.stderr)
        return 1

    active_binding = None
    for b in bindings:
        if b.get("runtime_id") == "runtime-tw-equity-paper" and b.get("status") == "active":
            active_binding = b
            break

    if not active_binding:
        print("ERROR: no active binding found for runtime-tw-equity-paper", file=sys.stderr)
        return 1

    binding_id = active_binding["binding_id"]
    strategy_id = active_binding.get("metadata", {}).get("strategy_id", "tw_session_momentum")
    
    # Resolve strategy artifact from the metadata
    strategy_artifact = active_binding.get("metadata", {}).get("strategy_artifact")
    if not strategy_artifact:
        print("ERROR: strategy_artifact not found in active binding metadata", file=sys.stderr)
        return 1

    params = strategy_artifact.get("parameters", {})
    symbols = params.get("symbols", ["2330.TW", "2317.TW", "2454.TW"])
    lookback_bars = int(params.get("lookback_bars", 2))
    order_quantity = float(params.get("order_quantity", 1.0))
    quantity_type = params.get("quantity_type", "SHARES")

    queue_key = f"pantheon:signals:pending:{binding_id}"
    print(f"Active binding: {binding_id}")
    print(f"Strategy: {strategy_id} | Queue: {queue_key}")
    print(f"Params: symbols={symbols}, lookback={lookback_bars}")

    # Map symbols to FinMind store tickers (e.g. 2330.TW -> 2330)
    store_symbols = [s.split(".")[0] for s in symbols]
    norm_glob = "/data/source-ingest/market_data_store/normalized/taiwanstockprice/date=*/*.jsonl"

    # 2. Read closes
    try:
        out = subprocess.run(
            ["docker", "exec", "pantheon-source-ingest-1", "sh", "-c", f"cat {norm_glob}"],
            capture_output=True, text=True, timeout=30
        )
        if out.returncode != 0:
            print("ERROR: failed to read source-ingest closes", file=sys.stderr)
            return 1
        raw_output = out.stdout
    except Exception as e:
        print(f"ERROR: could not read store closes: {e}", file=sys.stderr)
        return 1

    bars = {}
    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(json.loads(line)["metadata"]["body"])
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        sym = str(body.get("stock_id") or "")
        if sym in store_symbols and body.get("close") is not None:
            bars.setdefault(sym, {})[str(body["date"])] = float(body["close"])

    closes = {s: sorted(d.items()) for s, d in bars.items()}

    # 3. Evaluate and emit signals
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pushed = 0
    for sym_full in symbols:
        sym_short = sym_full.split(".")[0]
        series = closes.get(sym_short, [])
        if len(series) < lookback_bars:
            print(f"{sym_full}: <{lookback_bars} bars in store, skip")
            continue
        
        # Extract closing prices as a sequence of floats
        close_prices = [c for (d, c) in series]
        
        # Evaluate action using StrategyArtifact evaluate_strategy_action interpreter
        try:
            action = evaluate_strategy_action(strategy_artifact, close_prices)
        except Exception as e:
            print(f"ERROR: strategy evaluation failed for {sym_full}: {e}", file=sys.stderr)
            continue
            
        direction = "LONG" if action == "BUY" else "SHORT"

        sig = {
            "signal_id": str(uuid.uuid4()),
            "version": "1.0",
            "strategy_id": strategy_id,
            "timestamp": ts,
            "symbol": sym_full,
            "action": action,
            "direction": direction,
            "quantity": order_quantity,
            "quantity_type": quantity_type,
            "schema_version": "1.0",
            "signal_timestamp": ts,
            "source": "tw-finmind-momentum",
            "binding_id": binding_id,
        }

        # Validate signal payload using canonical minimal store validator
        try:
            validate_signal_payload_minimal(sig)
        except Exception as e:
            print(f"ERROR: signal validation failed for {sym_full}: {e}", file=sys.stderr)
            continue

        # Push to Redis
        try:
            subprocess.run(
                ["docker", "exec", "pantheon-signal-store-1", "redis-cli", "RPUSH", queue_key, json.dumps(sig)],
                capture_output=True, timeout=10
            )
            pushed += 1
            # Log momentum details for operator visibility
            (d_prev, c_prev) = series[-lookback_bars]
            (d_now, c_now) = series[-1]
            mom = (c_now - c_prev) / c_prev
            print(f"{sym_full} {d_prev}->{d_now} {c_prev}->{c_now} mom={mom*100:+.2f}% -> {action} (validated)")
        except Exception as e:
            print(f"ERROR pushing signal for {sym_full}: {e}", file=sys.stderr)

    print(f"REAL TW signals pushed = {pushed} -> {queue_key}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
