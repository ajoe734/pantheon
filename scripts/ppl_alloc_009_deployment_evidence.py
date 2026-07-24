#!/usr/bin/env python3
"""Write a secret-free record of the deployed PPL-ALLOC-009 proof posture."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


FEATURE_ENV = "PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED"


def _boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-sha", required=True)
    parser.add_argument("--component", required=True)
    parser.add_argument("--auth-profile", required=True)
    parser.add_argument("--expected-enabled", type=_boolean, required=True)
    parser.add_argument("--effective-env-line", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-run-attempt", required=True)
    parser.add_argument("--workflow-run-url", required=True)
    parser.add_argument("--bff-url", required=True)
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", args.target_sha):
        raise SystemExit("target SHA must be a full lowercase commit SHA")
    resolved_component = "root" if args.component == "auto" else args.component
    expected_line = f"{FEATURE_ENV}={str(args.expected_enabled).lower()}"
    if args.effective_env_line.strip() != expected_line:
        raise SystemExit(
            "container feature posture mismatch: "
            f"expected {expected_line!r}, got {args.effective_env_line.strip()!r}"
        )
    if args.expected_enabled and (
        args.component != "root" or args.auth_profile != "strict"
    ):
        raise SystemExit(
            "enabled PPL-ALLOC-009 proof evidence requires explicit root/strict"
        )

    payload = {
        "schemaVersion": 1,
        "kind": "pantheon_dev_deployment_posture",
        "environment": "dev",
        "component": resolved_component,
        "backendSourceCommitSha": args.target_sha,
        "authProfile": args.auth_profile,
        "featureFlags": {
            FEATURE_ENV: args.expected_enabled,
        },
        "observation": {
            "source": "docker_inspect_container_environment",
            "container": "pantheon-operator-bff-1",
            "effectiveEnvLine": expected_line,
            "observedAt": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        },
        "workflow": {
            "runId": args.workflow_run_id,
            "runAttempt": args.workflow_run_attempt,
            "runUrl": args.workflow_run_url,
        },
        "bffUrl": args.bff_url,
        "safety": {
            "strictAuthRequiredWhenEnabled": True,
            "devRootRequiredWhenEnabled": True,
            "stagingLiveAllowed": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
