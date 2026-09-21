#!/usr/bin/env python3
"""
Pantheon LEAN Algorithm Smoke and Engine Replay Verification Script.

Proves:
1. PantheonAlgoBase signal consumer and pending signal store wiring.
2. EngineReplayAlgo model/tenant/session context propagation.
3. Checkpoint persistence to LEAN ObjectStore.
4. Duplicate suppression across engine restart.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Add externalized integrations/lean and fallback lean/Algorithm.Python to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "integrations", "lean")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lean", "Algorithm.Python")))

os.environ.update({
    "PANTHEON_RUNTIME_BINDING_ID": "rtb-engine-replay-001",
    "PANTHEON_RUNTIME_ID": "rt-engine-replay-001",
    "PANTHEON_DEPLOYMENT_PLAN_ID": "dp-engine-replay-001",
    "PANTHEON_DEPLOYMENT_STAGE": "paper",
    "PANTHEON_RUNTIME_ROLE": "paper",
    "PANTHEON_ARTIFACT_ID": "art-engine-replay-001",
    "PANTHEON_ARTIFACT_VERSION": "1.0.0",
    "PANTHEON_ARTIFACT_CHECKSUM": "sha256:engine-replay",
    "PANTHEON_STRATEGY_ID": "strat-engine-replay-001",
    "PANTHEON_CAPITAL_POOL_ID": "pool-engine-replay-001",
    "PANTHEON_PERSONA_CAPITAL_BINDING_ID": "pcb-engine-replay-001",
    "PANTHEON_ENGINE_BRIDGE_REMOTE": "https://github.com/QuantConnect/Lean.git",
    "PANTHEON_ENGINE_BRIDGE_SOURCE_PATH": "integrations/lean/pantheon_algo",
    "PANTHEON_ENGINE_BRIDGE_COMMIT": "23b735d99a357807dc0df9f4c51d30f05fe0d277",
    "PANTHEON_RUNTIME_ADAPTER_VERSION": "0.1.0",
    "PANTHEON_TRACE_ID": "trace-engine-replay-001",
    "PANTHEON_CORRELATION_ID": "corr-engine-replay-001",
    "PANTHEON_MODEL_ID": "model-alpha-v1",
    "PANTHEON_TENANT_ID": "tenant-ops",
    "PANTHEON_SESSION_ID": "session-restart-001",
})

from pantheon_algo.base import EngineReplayAlgo, PantheonAlgoBase, PersistentLeanObjectStore


class SmokeAlgo(PantheonAlgoBase):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def Debug(self, message: str) -> None:
        try:
            self.events.append(json.loads(message))
        except Exception:
            pass


def run_smoke() -> bool:
    try:
        algo = SmokeAlgo()
        algo.Initialize()

        # 1. Verify consumer and store wiring
        if algo._consumer is None:
            print("Smoke test failed: SignalConsumer was not initialized.")
            return False
        if algo._signal_store is None:
            print("Smoke test failed: PendingSignalStore was not initialized.")
            return False

        # 2. Verify scheduling
        scheduled = getattr(algo.Schedule, "scheduled_events", [])
        if not scheduled:
            print("Smoke test failed: SignalConsumer was not scheduled.")
            return False
        print(f"Smoke test: SignalConsumer scheduled ({len(scheduled)} event(s)).")

        # 3. Prove signal intake and order execution
        signal = {
            "signal_id": "smoke-sig-001",
            "version": "1.0",
            "strategy_id": "strat-engine-replay-001",
            "binding_id": "rtb-engine-replay-001",
            "runtime_id": "rt-engine-replay-001",
            "metadata": {
                "capital_pool_id": "pool-engine-replay-001",
            },
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 0.5,
            "quantity_type": "PERCENT_PORTFOLIO",
        }
        algo._signal_store.enqueue(signal)
        initial_depth = algo._signal_store.queue_depth()
        if initial_depth != 1:
            print(f"Smoke test failed: Expected queue depth 1, got {initial_depth}")
            return False

        algo.OnData()

        remaining_depth = algo._signal_store.queue_depth()
        if remaining_depth != 0:
            print(f"Smoke test failed: Queue was not drained, remaining depth: {remaining_depth}")
            return False

        if not algo.orders:
            print("Smoke test failed: No order executed from signal intake.")
            return False
        print(f"Smoke test passed: Signal consumed and order executed: {algo.orders[-1]}")

        # 4. Prove telemetry bridge event
        algo.emit_pantheon_event("SmokeTestEvent", metrics={"smoke": 1})
        if len(algo.events) > 0:
            print(f"Smoke test passed: Bridge event emitted: {algo.events[-1]['event_type']}")
            return True
        print("Smoke test failed: No event emitted.")
        return False
    except Exception as e:
        print(f"Smoke test failed with exception: {e}")
        return False


def get_replay_signal() -> dict[str, Any]:
    return {
        "signal_id": "engine-replay-sig-001",
        "version": "1.0",
        "strategy_id": "strat-engine-replay-001",
        "binding_id": "rtb-engine-replay-001",
        "runtime_id": "rt-engine-replay-001",
        "metadata": {
            "capital_pool_id": "pool-engine-replay-001",
            "model_id": "model-alpha-v1",
            "tenant_id": "tenant-ops",
            "session_id": "session-restart-001",
        },
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "symbol": "SPY.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 0.5,
        "quantity_type": "PERCENT_PORTFOLIO",
    }


def _clean_checkpoint(storage_dir: Path) -> None:
    targets = ["pantheon_restart_checkpoint", "storage", "summary_initial.json", "summary_restart.json"]
    for name in targets:
        target = storage_dir / name
        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink()
        except Exception:
            pass
    lingering = [name for name in targets if (storage_dir / name).exists()]
    if lingering and shutil.which("docker"):
        subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "rm", "-v", f"{storage_dir}:/s", "quantconnect/lean:18070", "-rf"] + [f"/s/{name}" for name in lingering],
            check=False,
        )


def execute_upstream_lean(storage_dir: Path, phase: str) -> dict[str, Any]:
    launcher_dll = Path("/Lean/Launcher/bin/Debug/QuantConnect.Lean.Launcher.dll")
    has_launcher = launcher_dll.is_file()
    has_docker = shutil.which("docker") is not None

    if not has_launcher and not has_docker:
        sys.stderr.write(
            "ERROR: Fail closed: Neither QuantConnect.Lean.Launcher.dll nor docker is available.\n"
            "Real upstream LEAN engine execution is required; mock/stub doubles are rejected.\n"
        )
        sys.exit(1)

    summary_file = storage_dir / f"summary_{phase}.json"
    if summary_file.exists():
        try:
            summary_file.unlink()
        except PermissionError:
            if shutil.which("docker"):
                subprocess.run(
                    ["docker", "run", "--rm", "--entrypoint", "rm", "-v", f"{storage_dir}:/s", "quantconnect/lean:18070", "-rf", f"/s/summary_{phase}.json"],
                    check=False,
                )

    algo_path = Path(REPO_ROOT) / "integrations" / "lean" / "pantheon_algo" / "base.py"
    if not algo_path.exists():
        sys.stderr.write(f"ERROR: Algorithm file not found at {algo_path}\n")
        sys.exit(1)

    env = os.environ.copy()
    env.update({
        "PANTHEON_RUNTIME_BINDING_ID": "rtb-engine-replay-001",
        "PANTHEON_RUNTIME_ID": "rt-engine-replay-001",
        "PANTHEON_DEPLOYMENT_PLAN_ID": "dp-engine-replay-001",
        "PANTHEON_DEPLOYMENT_STAGE": "paper",
        "PANTHEON_RUNTIME_ROLE": "paper",
        "PANTHEON_ARTIFACT_ID": "art-engine-replay-001",
        "PANTHEON_ARTIFACT_VERSION": "1.0.0",
        "PANTHEON_ARTIFACT_CHECKSUM": "sha256:engine-replay",
        "PANTHEON_STRATEGY_ID": "strat-engine-replay-001",
        "PANTHEON_CAPITAL_POOL_ID": "pool-engine-replay-001",
        "PANTHEON_PERSONA_CAPITAL_BINDING_ID": "pcb-engine-replay-001",
        "PANTHEON_ENGINE_BRIDGE_REMOTE": "https://github.com/QuantConnect/Lean.git",
        "PANTHEON_ENGINE_BRIDGE_SOURCE_PATH": "integrations/lean/pantheon_algo",
        "PANTHEON_ENGINE_BRIDGE_COMMIT": "23b735d99a357807dc0df9f4c51d30f05fe0d277",
        "PANTHEON_RUNTIME_ADAPTER_VERSION": "0.1.0",
        "PANTHEON_TRACE_ID": "trace-engine-replay-001",
        "PANTHEON_CORRELATION_ID": "corr-engine-replay-001",
        "PANTHEON_MODEL_ID": "model-alpha-v1",
        "PANTHEON_TENANT_ID": "tenant-ops",
        "PANTHEON_SESSION_ID": "session-restart-001",
    })

    if has_launcher:
        env["PYTHONPATH"] = f"{REPO_ROOT}/integrations/lean:{REPO_ROOT}"
        env["PANTHEON_SUMMARY_PATH"] = str(summary_file)
        cmd = [
            "dotnet",
            str(launcher_dll),
            "--close-automatically", "true",
            "--algorithm-language", "Python",
            "--algorithm-location", str(algo_path),
            "--algorithm-type-name", "EngineReplayAlgo",
            "--data-folder", "/Lean/Data/",
        ]
        res = subprocess.run(cmd, env=env, check=False)
        exit_code = res.returncode
    else:
        cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "-v", f"{REPO_ROOT}:/workspace:ro",
            "-v", f"{storage_dir}:/Lean/Launcher/bin/Debug/storage",
            "-e", "PYTHONPATH=/workspace/integrations/lean:/workspace",
            "-e", f"PANTHEON_SUMMARY_PATH=/Lean/Launcher/bin/Debug/storage/summary_{phase}.json",
        ]
        for k, v in env.items():
            if k.startswith("PANTHEON_"):
                cmd.extend(["-e", f"{k}={v}"])
        cmd.extend([
            "quantconnect/lean:18070",
            "--close-automatically", "true",
            "--algorithm-language", "Python",
            "--algorithm-location", "/workspace/integrations/lean/pantheon_algo/base.py",
            "--algorithm-type-name", "EngineReplayAlgo",
            "--data-folder", "/Lean/Data/",
        ])
        res = subprocess.run(cmd, check=False)
        exit_code = res.returncode

    if exit_code != 0:
        raise RuntimeError(f"Upstream LEAN execution for phase {phase} failed with exit code {exit_code}")

    if not summary_file.exists():
        raise RuntimeError(f"Summary file not generated by LEAN execution: {summary_file}")

    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    summary["exit_code"] = exit_code
    return summary


def run_engine_replay_initial(storage_dir: Path) -> dict[str, Any]:
    print(f"\n--- Starting Upstream Engine Replay: Phase 1 (Initial Run) [storage={storage_dir}] ---")
    _clean_checkpoint(storage_dir)

    summary = execute_upstream_lean(storage_dir, "initial")

    if not summary.get("lean_available"):
        raise RuntimeError("Phase 1 failed: real LEAN engine was not available")
    if summary.get("is_restart"):
        raise RuntimeError("Phase 1 expected initial run, but detected prior restart checkpoint")

    neg_test = summary.get("negative_test") or {}
    if not neg_test.get("passed"):
        raise RuntimeError(f"Phase 1 negative rejected-signal test failed: {neg_test}")
    if neg_test.get("result", {}).get("status") != "BINDING_MISMATCH" or neg_test.get("result", {}).get("new_orders_placed") != 0:
        raise RuntimeError(f"Phase 1 wrong binding was not rejected with 0 orders: {neg_test}")

    orders = summary.get("executed_orders") or []
    if len(orders) != 1 or orders[0].get("status") != "FILLED":
        raise RuntimeError(f"Phase 1 expected exactly 1 FILLED order, got: {orders}")

    chk = storage_dir / "pantheon_restart_checkpoint"
    if not chk.exists():
        raise RuntimeError(f"Phase 1 expected checkpoint file at {chk}, but it does not exist")

    print("Phase 1 completed successfully in upstream engine.")
    print(f"Phase 1 order executed: {orders[0]}")
    print(f"Emitted events: {summary.get('emitted_events')}")
    print(json.dumps(summary, indent=2))
    return summary


def run_engine_replay_restart(storage_dir: Path) -> dict[str, Any]:
    print(f"\n--- Starting Upstream Engine Replay: Phase 2 (Engine Restart Run) [storage={storage_dir}] ---")
    chk = storage_dir / "pantheon_restart_checkpoint"
    if not chk.exists():
        raise RuntimeError(f"Phase 2 expected prior checkpoint at {chk}, but it does not exist")

    summary = execute_upstream_lean(storage_dir, "restart")

    if not summary.get("lean_available"):
        raise RuntimeError("Phase 2 failed: real LEAN engine was not available")
    if not summary.get("is_restart"):
        raise RuntimeError("Phase 2 expected engine restart, but no prior checkpoint was detected by engine")

    neg_test = summary.get("negative_test") or {}
    if not neg_test.get("passed"):
        raise RuntimeError(f"Phase 2 negative rejected-signal test failed: {neg_test}")

    pos_test = summary.get("positive_test") or {}
    if pos_test.get("status") != "DUPLICATE_SUPPRESSED" or pos_test.get("new_orders_placed") != 0:
        raise RuntimeError(f"Duplicate suppression failed across engine restart: {pos_test}")

    orders = summary.get("executed_orders") or []
    if len(orders) != 0:
        raise RuntimeError(f"Phase 2 expected 0 executed orders (duplicate suppressed), got: {orders}")

    print("Phase 2 completed successfully in upstream engine.")
    print("Duplicate suppression confirmed: new_orders_placed=0, duplicate_suppressed=True")
    print(f"Emitted events: {summary.get('emitted_events')}")
    print(json.dumps(summary, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Pantheon LEAN algorithm smoke and engine replay runner.")
    parser.add_argument(
        "--replay",
        choices=["initial", "restart", "all", "smoke"],
        default="all",
        help="Replay execution phase: 'initial', 'restart', 'all' (both), or 'smoke' (standard smoke test only)",
    )
    parser.add_argument(
        "--storage-dir",
        type=Path,
        default=Path("/tmp/pantheon_storage"),
        help="Directory backing persistent LEAN ObjectStore double",
    )
    args = parser.parse_args()

    storage_dir = args.storage_dir.resolve()
    storage_dir.mkdir(parents=True, exist_ok=True)

    if args.replay == "smoke":
        ok = run_smoke()
        return 0 if ok else 1

    if args.replay == "initial":
        run_engine_replay_initial(storage_dir)
        return 0

    if args.replay == "restart":
        run_engine_replay_restart(storage_dir)
        return 0

    # Default "all": run smoke test, initial replay, and restart replay
    print("=== Running Base Smoke Test ===")
    if not run_smoke():
        return 1

    print("\n=== Running Engine Replay Suite ===")
    run_engine_replay_initial(storage_dir)
    run_engine_replay_restart(storage_dir)
    print("\n=== All LEAN Algorithm & Engine Replay Checks Passed Successfully ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
