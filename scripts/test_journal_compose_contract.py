#!/usr/bin/env python3
"""Render the real Compose contract; optionally prove it with local paper fixtures.

No Compose deployment, hosted endpoint, or credentials are used. --runtime
starts a disposable network-isolated Postgres and runs three fresh BFF factory
processes with the rendered journal settings and actual read-only consumer
mount. A fourth process verifies database failure without local fallback.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parent.parent
PROBE = "services/control-plane/bff/tests/test_journal_runtime_contract.py"


def run(*args: str, env=None, timeout=60) -> str:
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Fixtures contain only public dummy configuration, never host env secrets.
        raise RuntimeError(f"command failed ({result.returncode}): {args[0:3]}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def render_compose(control: bool = False, overrides=None) -> dict:
    # Never consume a host .env or inherited product/service credentials.
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
           "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": "http://adapter.invalid",
           "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN": "local-render-fixture-only",
           "PANTHEON_PERSONA_SERVICE_TOKEN": "local-render-fixture-only"}
    env.update(overrides or {})
    args = ["docker", "compose", "--env-file", "/dev/null", "-f", "docker-compose.yml"]
    if control:
        args += ["-f", "docker-compose.control.yml"]
    return json.loads(run(*args, "config", "--format", "json", env=env))


def validate_compose_contract(repo_root: Path = ROOT) -> dict:
    assert repo_root.resolve() == ROOT
    for control in (False, True):
        rendered = render_compose(control)
        bff = rendered["services"]["operator-bff"]
        env = bff["environment"]
        assert env["GOVERNANCE_STORE_BACKEND"] == "postgres"
        assert env["GOVERNANCE_STORE_DSN"] == rendered["services"]["governance"]["environment"]["DATABASE_URL"]
        assert env["GOVERNANCE_STORE_BOOTSTRAP"] == "1"
        assert env["PANTHEON_DECISION_JOURNAL_DATA_DIR"] == "/data/bff/decision_journal"
        assert "PANTHEON_BFF_DECISION_JOURNAL_STORE" not in env
        mounts = {row["target"]: row for row in bff["volumes"]}
        assert mounts["/data/governance"]["read_only"] is True
        assert not mounts["/data/bff"].get("read_only", False)
        # Exact interpolation behavior, not substring matches against raw YAML.
        custom = "postgresql://fixture:fixture@database.invalid/paper"
        for variable in ("DATABASE_URL", "GOVERNANCE_STORE_DSN"):
            changed = render_compose(control, {variable: custom})
            assert changed["services"]["operator-bff"]["environment"]["GOVERNANCE_STORE_DSN"] == custom
    print("PASS: base/control rendered Compose defaults, DSN overrides, and read-only consumer mount")
    return rendered


def runtime_contract(rendered: dict, bff_image: str) -> None:
    name = "journal-contract-" + uuid.uuid4().hex[:10]
    network, database = name + "-net", name + "-pg"
    bff_env = rendered["services"]["operator-bff"]["environment"]
    with tempfile.TemporaryDirectory(prefix=name) as tmp:
        consumer = Path(tmp) / "consumer"
        writer = Path(tmp) / "writer"
        consumer.mkdir()
        writer.mkdir()
        network_created = database_created = False
        try:
            run("docker", "network", "create", "--internal", network)
            network_created = True
            run("docker", "run", "-d", "--name", database, "--network", network,
                "--network-alias", "postgres", "-e", "POSTGRES_USER=pantheon_app",
                "-e", "POSTGRES_PASSWORD=pantheon_app", "-e", "POSTGRES_DB=pantheon", "postgres:16-alpine")
            database_created = True
            for _ in range(30):
                probe = subprocess.run(["docker", "exec", database, "pg_isready", "-U", "pantheon_app"],
                                       capture_output=True, timeout=10)
                if probe.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Disposable Postgres did not become ready")

            def paper(phase: str):
                args = ["docker", "run", "--rm", "--network", network, "--read-only", "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges:true", "--user", f"{os.getuid()}:{os.getgid()}",
                        "--tmpfs", "/tmp", "-v", f"{ROOT}:/workspace:ro",
                        "-v", f"{consumer}:/data/governance:ro", "-v", f"{writer}:/data/bff:rw",
                        "-w", "/workspace", "-e", "PYTHONPATH=/workspace", "-e", "PYTHONDONTWRITEBYTECODE=1",
                        "-e", "BFF_DATA_DIR=/data/bff", "-e", "PANTHEON_ENV=test",
                        "-e", "JOURNAL_REQUIRE_RO_MOUNT=1"]
                for key in ("GOVERNANCE_STORE_BACKEND", "GOVERNANCE_STORE_DSN", "GOVERNANCE_STORE_BOOTSTRAP",
                            "PANTHEON_DECISION_JOURNAL_DATA_DIR", "PANTHEON_GOVERNANCE_DATA_DIR"):
                    value = bff_env[key]
                    if key == "GOVERNANCE_STORE_DSN":
                        value += "?connect_timeout=1"
                    if phase == "unavailable" and key == "GOVERNANCE_STORE_BOOTSTRAP":
                        value = "0"
                    args += ["-e", f"{key}={value}"]
                print(run(*args, "--entrypoint", "python", bff_image, PROBE, "--paper-probe", phase, timeout=90))

            paper("create")
            paper("restart")
            paper("reread")
            run("docker", "stop", "--time", "10", database)
            paper("unavailable")
            assert list(consumer.iterdir()) == [], "Read consumer mount was modified"
            assert not list(writer.rglob("decision_journal*.json")), "Unexpected local authority"
            print("PASS: mounted BFF paper create/restart/replay/tenant/CAS/unavailable; no JSON fallback")
        finally:
            if database_created:
                run("docker", "rm", "-f", database)
            if network_created:
                run("docker", "network", "rm", network)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--bff-image", default="pantheon-bff-test")
    args = parser.parse_args()
    config = validate_compose_contract()
    if args.runtime:
        runtime_contract(config, args.bff_image)
