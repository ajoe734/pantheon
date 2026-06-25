#!/usr/bin/env python3
"""Scheduler wrapper for paper_loop_run_projector — runs on a fixed interval.

Environment variables
---------------------
BFF_BASE                           Required.  BFF service base URL.
BFF_TOKEN                          Optional.  Auth bearer token (default: op-dev:admin:mfa).
PANTHEON_BFF_LOOP_RUN_STORE        Optional.  Output path (default: /data/bff/loop_runs.json).
LOOP_RUN_PROJECTOR_INTERVAL_SECONDS  Optional.  Projection interval (default: 300).
LOOP_RUN_PROJECTOR_MAX_TICKS       Optional.  Stop after N ticks; 0 = run forever (default: 0).
"""
from __future__ import annotations

import os
import sys
import time

# Ensure the projector module is importable from the same directory.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import paper_loop_run_projector as _projector  # noqa: E402


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def main() -> int:
    interval = _env_int("LOOP_RUN_PROJECTOR_INTERVAL_SECONDS", 300, minimum=30)
    max_ticks = _env_int("LOOP_RUN_PROJECTOR_MAX_TICKS", 0, minimum=0)
    tick = 0
    while True:
        tick += 1
        exit_code = _projector.main()
        sys.stderr.write(f"[loop-run-projector-scheduler] tick={tick} exit_code={exit_code}\n")
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
