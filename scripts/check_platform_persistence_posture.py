#!/usr/bin/env python3
"""Check staging/prod persistence posture without opening external connections."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.foundation.persistence_posture import validate_persistence_posture
from services.source_search_posture import validate_source_search_posture


PERSISTENCE_SERVICES = (
    "consultation",
    "training-session",
    "policy-learning",
    "research-orchestrator",
    "research-worker-gateway",
    "governance",
    "capital",
    "incidents",
    "postmortems",
    "promotion",
    "memory",
    "reconciliation-drift",
)


def _read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ValueError(f"{path}:{line_no}: expected KEY=VALUE")
        key, value = stripped.split("=", 1)
        env[key.strip()] = value.strip().strip("'\"")
    return env


def _checks(env: dict[str, str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for service in PERSISTENCE_SERVICES:
        results.append(validate_persistence_posture(service, env=env).to_dict())
    results.append(validate_source_search_posture("source-ingest", env=env).to_dict())
    results.append(validate_source_search_posture("search", env=env).to_dict())
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Read KEY=VALUE entries before checking posture")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    args = parser.parse_args()

    env = dict(os.environ)
    if args.env_file:
        env.update(_read_env_file(args.env_file))

    results = _checks(env)
    failed = [item for item in results if item.get("status") != "ok"]
    payload = {
        "status": "error" if failed else "ok",
        "checked": len(results),
        "failed": len(failed),
        "results": results,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for item in results:
            marker = "ok" if item.get("status") == "ok" else "error"
            service = item.get("service")
            mode = item.get("mode")
            enforced = item.get("enforced")
            print(f"{marker:5} {service} mode={mode} enforced={enforced}")
            for error in item.get("errors") or []:
                print(f"      - {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
