#!/usr/bin/env python3
"""Write fail-closed preflight evidence for strict BFF live probes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT = Path(".lovable") / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
STRICT_LIVE_SOAK_MIN_SECONDS = 75.0

REQUIRED_SECRET_ENV_VARS = (
    "PANTHEON_BFF_SMOKE_BEARER_TOKEN",
    "PANTHEON_BFF_RBAC_TOKENS_JSON",
    "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A",
    "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B",
)

REQUIRED_INPUT_NAMES = (
    "PANTHEON_BFF_BASE_URL",
    "APPROVAL_RACE_ID",
    "SOAK_SECONDS",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def present(value: str | None) -> bool:
    return bool((value or "").strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("PANTHEON_BFF_BASE_URL", ""))
    parser.add_argument("--approval-race-id", default=os.environ.get("APPROVAL_RACE_ID", ""))
    parser.add_argument("--soak-seconds", default=os.environ.get("SOAK_SECONDS", ""))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def invalid_soak_seconds(value: str) -> str:
    if not present(value):
        return ""
    try:
        seconds = float(value)
    except ValueError:
        return f"SOAK_SECONDS must be numeric and >= {STRICT_LIVE_SOAK_MIN_SECONDS:g}"
    if seconds < STRICT_LIVE_SOAK_MIN_SECONDS:
        return f"SOAK_SECONDS must be >= {STRICT_LIVE_SOAK_MIN_SECONDS:g}"
    return ""


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    required = [*REQUIRED_SECRET_ENV_VARS, *REQUIRED_INPUT_NAMES]
    present_map = {
        name: present(os.environ.get(name))
        for name in REQUIRED_SECRET_ENV_VARS
    }
    present_map["PANTHEON_BFF_BASE_URL"] = present(args.base_url)
    present_map["APPROVAL_RACE_ID"] = present(args.approval_race_id)
    present_map["SOAK_SECONDS"] = present(args.soak_seconds)
    missing = [name for name in required if not present_map[name]]
    invalid = [
        {"name": "SOAK_SECONDS", "reason": reason}
        for reason in [invalid_soak_seconds(args.soak_seconds)]
        if reason
    ]

    return {
        "task_id": "BFF-LIVE-EVIDENCE-PREFLIGHT",
        "strict_live_evidence_preflight": True,
        "generated_at": utc_now(),
        "target_url": args.base_url.strip(),
        "soak_seconds": args.soak_seconds.strip(),
        "min_soak_seconds": STRICT_LIVE_SOAK_MIN_SECONDS,
        "ref": os.environ.get("GITHUB_REF") or os.environ.get("GITHUB_REF_NAME", ""),
        "sha": os.environ.get("GITHUB_SHA", ""),
        "required": required,
        "present": present_map,
        "missing": missing,
        "invalid": invalid,
        "output_scope": ".lovable/audits/current-run",
        "secret_values_written": False,
    }


def main() -> int:
    args = parse_args()
    payload = build_payload(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(f"{json.dumps(payload, indent=2, sort_keys=True)}\n", encoding="utf-8")

    if payload["missing"] or payload["invalid"]:
        if payload["missing"]:
            print(
                "Missing strict live evidence inputs: " + ", ".join(payload["missing"]),
                file=sys.stderr,
            )
        if payload["invalid"]:
            invalid_text = "; ".join(f"{item['name']}: {item['reason']}" for item in payload["invalid"])
            print("Invalid strict live evidence inputs: " + invalid_text, file=sys.stderr)
        return 1

    print("Strict live evidence inputs present: " + ", ".join(payload["required"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
