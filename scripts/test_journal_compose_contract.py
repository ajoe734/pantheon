#!/usr/bin/env python3
"""JOURNAL-RUNTIME-CONTRACT-CORRECTIVE-001: Local Compose contract validation.

Validates that docker-compose.yml and docker-compose.control.yml enforce the
canonical journal runtime contract:
1. operator-bff declares GOVERNANCE_STORE_BACKEND with default 'postgres'.
2. operator-bff declares GOVERNANCE_STORE_DSN chained to DATABASE_URL.
3. operator-bff declares GOVERNANCE_STORE_BOOTSTRAP=1.
4. operator-bff declares PANTHEON_DECISION_JOURNAL_DATA_DIR.
5. operator-bff preserves read-only mount '/data/governance:ro' (does NOT change to :rw).
6. operator-bff mounts durable bff-data volume at '/data/bff'.
7. docker-compose.control.yml operator-bff declares matching backend contract.
"""
from __future__ import annotations

import sys
from pathlib import Path
import yaml


def validate_compose_contract(repo_root: Path) -> None:
    compose_path = repo_root / "docker-compose.yml"
    control_path = repo_root / "docker-compose.control.yml"

    if not compose_path.exists():
        raise FileNotFoundError(f"Missing docker-compose.yml at {compose_path}")
    if not control_path.exists():
        raise FileNotFoundError(f"Missing docker-compose.control.yml at {control_path}")

    # 1. Inspect docker-compose.yml
    with open(compose_path, "r", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    services = compose_data.get("services", {})
    if "operator-bff" not in services:
        raise AssertionError("operator-bff service not found in docker-compose.yml")

    bff = services["operator-bff"]
    env = bff.get("environment", {})
    vols = bff.get("volumes", [])

    # Check env in docker-compose.yml
    gov_backend = env.get("GOVERNANCE_STORE_BACKEND", "")
    if "postgres" not in gov_backend:
        raise AssertionError(
            f"operator-bff GOVERNANCE_STORE_BACKEND must default to postgres, got: {gov_backend!r}"
        )

    gov_dsn = env.get("GOVERNANCE_STORE_DSN", "")
    if not gov_dsn or "DATABASE_URL" not in gov_dsn:
        raise AssertionError(
            f"operator-bff GOVERNANCE_STORE_DSN must chain to DATABASE_URL, got: {gov_dsn!r}"
        )

    gov_bootstrap = env.get("GOVERNANCE_STORE_BOOTSTRAP", "")
    if "1" not in str(gov_bootstrap):
        raise AssertionError(
            f"operator-bff GOVERNANCE_STORE_BOOTSTRAP must default to 1, got: {gov_bootstrap!r}"
        )

    data_dir = env.get("PANTHEON_DECISION_JOURNAL_DATA_DIR", "")
    if "/data/bff" not in str(data_dir):
        raise AssertionError(
            f"operator-bff PANTHEON_DECISION_JOURNAL_DATA_DIR must point under /data/bff, got: {data_dir!r}"
        )

    # Check volumes in docker-compose.yml
    has_gov_ro = any(
        isinstance(v, str) and "/data/governance:ro" in v
        for v in vols
    )
    if not has_gov_ro:
        raise AssertionError(
            "operator-bff must retain read-only /data/governance:ro volume mount"
        )

    has_gov_rw = any(
        isinstance(v, str) and "/data/governance" in v and ":ro" not in v
        for v in vols
    )
    if has_gov_rw:
        raise AssertionError(
            "operator-bff must NOT mount /data/governance as read-write; merely changing :ro to :rw is forbidden"
        )

    has_bff_data = any(
        isinstance(v, str) and "/data/bff" in v
        for v in vols
    )
    if not has_bff_data:
        raise AssertionError(
            "operator-bff must mount /data/bff volume for local journal fallback"
        )

    # 2. Inspect docker-compose.control.yml
    # Control file uses YAML !override tag; we load with a custom loader or ignore tags
    class SafeLoaderIgnoreUnknown(yaml.SafeLoader):
        pass

    SafeLoaderIgnoreUnknown.add_constructor(
        None,
        lambda loader, node: loader.construct_scalar(node)
        if isinstance(node, yaml.ScalarNode)
        else (loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node)),
    )

    with open(control_path, "r", encoding="utf-8") as f:
        control_data = yaml.load(f, Loader=SafeLoaderIgnoreUnknown)

    ctrl_services = control_data.get("services", {})
    if "operator-bff" not in ctrl_services:
        raise AssertionError("operator-bff service not found in docker-compose.control.yml")

    ctrl_bff = ctrl_services["operator-bff"]
    ctrl_env = ctrl_bff.get("environment", {})

    ctrl_gov_backend = ctrl_env.get("GOVERNANCE_STORE_BACKEND", "")
    if "postgres" not in ctrl_gov_backend:
        raise AssertionError(
            f"docker-compose.control.yml operator-bff GOVERNANCE_STORE_BACKEND must default to postgres, got: {ctrl_gov_backend!r}"
        )

    ctrl_gov_dsn = ctrl_env.get("GOVERNANCE_STORE_DSN", "")
    if not ctrl_gov_dsn or "DATABASE_URL" not in ctrl_gov_dsn:
        raise AssertionError(
            f"docker-compose.control.yml operator-bff GOVERNANCE_STORE_DSN must chain to DATABASE_URL, got: {ctrl_gov_dsn!r}"
        )

    ctrl_gov_bootstrap = ctrl_env.get("GOVERNANCE_STORE_BOOTSTRAP", "")
    if "1" not in str(ctrl_gov_bootstrap):
        raise AssertionError(
            f"docker-compose.control.yml operator-bff GOVERNANCE_STORE_BOOTSTRAP must default to 1, got: {ctrl_gov_bootstrap!r}"
        )

    ctrl_data_dir = ctrl_env.get("PANTHEON_DECISION_JOURNAL_DATA_DIR", "")
    if "/data/bff" not in str(ctrl_data_dir):
        raise AssertionError(
            f"docker-compose.control.yml operator-bff PANTHEON_DECISION_JOURNAL_DATA_DIR must point under /data/bff, got: {ctrl_data_dir!r}"
        )

    print("PASS: docker-compose.yml and docker-compose.control.yml journal runtime contract verified successfully.")


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    try:
        validate_compose_contract(repo_root)
        sys.exit(0)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
