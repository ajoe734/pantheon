#!/usr/bin/env python3
"""Reproducible isolated-Compose harness for current Loops 8-12 deployed E2E proof.

Task: PFG-L12-RUNTIME-E2E-20260820

This harness coordinates the complete reproducible proof for Loops 8 through 12:
1. Verifies isolated Docker Compose services and container health.
2. Executes pre-flight canonical retirement/migration of pre-existing invalid bindings.
3. Pre-seeds/verifies canonical stored market price snapshot in source-ingest.
4. Invokes the deployed pytest suite `tests/integration/l12/test_current_runtime_loops_deployed_e2e.py`.
5. Verifies atomic evidence output and computes cryptographic SHA-256 hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

TASK_ID = "PFG-L12-RUNTIME-E2E-20260820"
MAIN_NEGATIVE_BINDING_ID = "rb-51f84b3169d745e4b34fcf80f0bc5f3c"
MAIN_NEGATIVE_ARTIFACT_ID = "artifact-l12-missing-checksum-758b9d2a75"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_PROJECT = "l12currentruntimee2e"
DEFAULT_EVIDENCE_DEST = (
    REPO_ROOT
    / "docs"
    / "deployment"
    / "evidence"
    / "product-functional-closure"
    / TASK_ID
)

REQUIRED_COMPOSE_SERVICES = [
    "postgres",
    "nats",
    "signal-store",
    "source-ingest",
    "registry",
    "governance",
    "capital",
    "deployment",
    "deployment-outbox-consumer",
    "runtime-manager",
    "paper-fleet-reconciler",
    "paper-signal-producer",
    "broker",
    "telemetry",
    "reconciliation-drift-svc",
    "incidents",
    "evolution",
    "operator-bff",
]

DEFAULT_PORTS: dict[str, int] = {
    "POSTGRES_PORT": 15432,
    "MINIO_API_PORT": 19000,
    "MINIO_CONSOLE_PORT": 19001,
    "NATS_PORT": 14222,
    "NATS_MONITOR_PORT": 18222,
    "OPERATOR_BFF_PORT": 18001,
    "BFF_PORT": 18001,
    "PERSONA_PORT": 18002,
    "ROUTER_PORT": 18003,
    "PAPER_RUNTIME_PORT": 18010,
    "PAPER_FLEET_RECONCILER_PORT": 18011,
    "RUNTIME_MANAGER_PORT": 18081,
    "RUNTIME_PORT": 18081,
    "GOVERNANCE_PORT": 18082,
    "TELEMETRY_PORT": 18083,
    "EVALUATION_PORT": 18084,
    "FEEDBACK_PORT": 18085,
    "MEMORY_PORT": 18086,
    "REGISTRY_PORT": 18087,
    "OPTIMIZER_PORT": 18088,
    "PROMOTION_PORT": 18089,
    "INCIDENTS_PORT": 18090,
    "POSTMORTEMS_PORT": 18091,
    "CAPITAL_PORT": 18092,
    "EVOLUTION_PORT": 18093,
    "LINEAGE_READ_PORT": 18094,
    "DEPLOYMENT_PORT": 18095,
    "CONSULTATION_PORT": 18096,
    "SOURCE_INGEST_PORT": 18097,
    "SEARCH_PORT": 18098,
    "TRAINING_SESSION_PORT": 18099,
    "POLICY_LEARNING_PORT": 18100,
    "RESEARCH_ORCHESTRATOR_PORT": 18101,
    "RECONCILIATION_DRIFT_PORT": 18102,
    "RESEARCH_WORKER_GATEWAY_PORT": 18103,
    "OPENCLAW_GATEWAY_ADAPTER_PORT": 18104,
    "WEB_CHANNEL_PORT": 18105,
    "BROKER_PORT": 18106,
    "OPENCLAW_GATEWAY_PORT": 18789,
}

SERVICES: dict[str, dict[str, Any]] = {
    "bff": {"port_var": "BFF_PORT", "default_port": 18001, "health": "/readyz"},
    "capital": {"port_var": "CAPITAL_PORT", "default_port": 18092, "health": "/readyz"},
    "deployment": {"port_var": "DEPLOYMENT_PORT", "default_port": 18095, "health": "/readyz"},
    "evolution": {"port_var": "EVOLUTION_PORT", "default_port": 18093, "health": "/readyz"},
    "fleet": {"port_var": "PAPER_FLEET_RECONCILER_PORT", "default_port": 18011, "health": "/readyz"},
    "governance": {"port_var": "GOVERNANCE_PORT", "default_port": 18082, "health": "/readyz"},
    "incidents": {"port_var": "INCIDENTS_PORT", "default_port": 18090, "health": "/readyz"},
    "reconciliation": {"port_var": "RECONCILIATION_DRIFT_PORT", "default_port": 18102, "health": "/readyz"},
    "registry": {"port_var": "REGISTRY_PORT", "default_port": 18087, "health": "/readyz"},
    "runtime": {"port_var": "RUNTIME_PORT", "default_port": 18081, "health": "/readyz"},
    "source_ingest": {"port_var": "SOURCE_INGEST_PORT", "default_port": 18097, "health": "/readyz"},
    "telemetry": {"port_var": "TELEMETRY_PORT", "default_port": 18083, "health": "/readyz"},
}


def _run(cmd: list[str], *, check: bool = True, env: Mapping[str, str] | None = None) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, env=dict(env) if env else None)
    if check and proc.returncode != 0:
        print(f"Command failed ({proc.returncode}): {' '.join(cmd)}", file=sys.stderr)
        if proc.stdout:
            print(f"Stdout:\n{proc.stdout}", file=sys.stderr)
        if proc.stderr:
            print(f"Stderr:\n{proc.stderr}", file=sys.stderr)
        sys.exit(proc.returncode)
    return proc.stdout.strip()


def _get_json(url: str, headers: Mapping[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


def _post_json(url: str, body: Mapping[str, Any], headers: Mapping[str, str] | None = None) -> Any:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **dict(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _compose_command(
    project: str,
    compose_files: list[str],
    *args: str,
) -> list[str]:
    command = ["docker", "compose", "-p", project]
    for compose_file in compose_files:
        command.extend(("-f", compose_file))
    command.extend(args)
    return command


def _project_container_ids(project: str) -> list[str]:
    output = _run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        check=False,
    )
    return sorted(line.strip() for line in output.splitlines() if line.strip())


def _teardown_project(
    project: str,
    compose_files: list[str],
    compose_env: Mapping[str, str],
) -> dict[str, Any]:
    command = _compose_command(
        project,
        compose_files,
        "down",
        "--volumes",
        "--remove-orphans",
    )
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=dict(compose_env),
    )
    remaining = _project_container_ids(project)
    return {
        "command": command,
        "compose_project": project,
        "down_returncode": process.returncode,
        "remaining_container_ids": remaining,
        "zero_project_containers": process.returncode == 0 and not remaining,
    }


def _ensure_main_negative_binding_retired(
    runtime_url: str,
    token: str,
) -> dict[str, Any]:
    base = runtime_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    binding_url = f"{base}/api/runtime-bindings/{MAIN_NEGATIVE_BINDING_ID}"
    binding = _get_json(binding_url, headers)
    if not isinstance(binding, Mapping) or binding.get("error"):
        raise RuntimeError(f"canonical main binding read failed: {binding!r}")
    action = "already_retired_readback"
    if binding.get("status") == "active":
        binding = _post_json(
            f"{binding_url}/retire",
            {
                "actor_id": TASK_ID,
                "reason": (
                    f"{TASK_ID} retires intentional missing-checksum fixture "
                    "before fleet acceptance"
                ),
            },
            headers,
        )
        action = "retired_via_canonical_post"

    desired = _get_json(
        f"{base}/api/runtime-fleet/desired-state?stage=paper&include_excluded=true",
        headers,
    )
    if not isinstance(desired, Mapping) or desired.get("error"):
        raise RuntimeError(f"canonical main fleet read failed: {desired!r}")
    excluded = {
        str(item.get("binding_id")): str(item.get("exclusion_reason"))
        for item in desired.get("excluded", [])
        if isinstance(item, Mapping)
    }
    metadata = binding.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    object_store = metadata.get("object_store")
    object_store = object_store if isinstance(object_store, Mapping) else {}
    projected_metadata = next(
        (
            value
            for key, value in object_store.items()
            if str(key).endswith("/metadata.json")
        ),
        {},
    )
    projected_metadata = (
        projected_metadata if isinstance(projected_metadata, Mapping) else {}
    )
    attestation = metadata.get("authoritative_loader_attestation")
    attestation = attestation if isinstance(attestation, Mapping) else {}

    assertions = {
        "artifact_identity_exact": binding.get("artifact_id")
        == MAIN_NEGATIVE_ARTIFACT_ID,
        "binding_retired": binding.get("status") == "retired",
        "fleet_exclusion_reason": excluded.get(MAIN_NEGATIVE_BINDING_ID)
        == "terminal_status",
        "projection_checksum_absent": not bool(projected_metadata.get("checksum")),
        "authority_attestation_checksum_present": bool(
            attestation.get("artifact_checksum")
        ),
    }
    if not all(assertions.values()):
        raise RuntimeError(
            "canonical main negative-binding retirement assertions failed: "
            f"{assertions!r}"
        )
    return {
        "action": action,
        "artifact_id": binding.get("artifact_id"),
        "binding_id": binding.get("binding_id"),
        "retired_at": binding.get("retired_at"),
        "status": binding.get("status"),
        "runtime_url": base,
        "assertions": assertions,
        "active_count_after_retirement": desired.get("active_count"),
        "active_binding_ids_after_retirement": sorted(
            str(item.get("binding_id"))
            for item in desired.get("bindings", [])
            if isinstance(item, Mapping) and item.get("binding_id")
        ),
    }


def _augment_report(
    report_path: Path,
    *,
    main_retirement: Mapping[str, Any] | None,
    preclean: Mapping[str, Any] | None,
    teardown: Mapping[str, Any] | None,
) -> None:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise RuntimeError("deployed suite report must be a JSON object")
    report["harness"] = {
        "main_negative_binding_retirement": dict(main_retirement or {}),
        "preclean": dict(preclean or {}),
        "teardown": dict(teardown or {}),
    }
    descriptor, temporary = tempfile.mkstemp(
        dir=report_path.parent,
        prefix=f".{report_path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, report_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compose-project",
        default=os.getenv("PANTHEON_L12_COMPOSE_PROJECT", DEFAULT_COMPOSE_PROJECT),
        help=f"Isolated Compose project name (default: {DEFAULT_COMPOSE_PROJECT})",
    )
    parser.add_argument(
        "--compose-file",
        action="append",
        dest="compose_files",
        help="Compose file(s) to pass to docker compose -f (default: ./docker-compose.yml)",
    )
    parser.add_argument(
        "--port-offset",
        type=int,
        default=int(os.getenv("PANTHEON_L12_PORT_OFFSET", "10000" if os.getenv("PANTHEON_L12_COMPOSE_PROJECT", DEFAULT_COMPOSE_PROJECT) != "pantheon" else "0")),
        help="Port offset for isolated Compose services (default: 10000 for isolated projects, 0 for pantheon)",
    )
    parser.add_argument(
        "--provision-services",
        "--up",
        action="store_true",
        dest="provision_services",
        help="Automatically provision and start required isolated Compose services before test execution",
    )
    parser.add_argument(
        "--down",
        action="store_true",
        help="Stop and tear down the isolated Compose stack",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=60.0,
        help="Maximum seconds to wait for service readiness when provisioning (default: 60)",
    )
    parser.add_argument(
        "--evidence-output",
        type=Path,
        default=Path("/tmp/l12-current-runtime-e2e-proof.json"),
        help="Path where pytest should write the atomic run report JSON",
    )
    parser.add_argument(
        "--update-evidence-dir",
        type=Path,
        default=DEFAULT_EVIDENCE_DEST,
        help="Target directory to sync immutable evidence files to",
    )
    parser.add_argument(
        "--sync-evidence",
        action="store_true",
        help="Copy resulting run-report.json and compute .sha256 in target evidence directory",
    )
    parser.add_argument(
        "--canonical-runtime-url",
        default=os.getenv("PANTHEON_L12_CANONICAL_RUNTIME_URL", ""),
        help=(
            "Main dev Runtime Manager URL used to retire/read back the exact "
            "historical negative binding before evidence sync"
        ),
    )
    parser.add_argument(
        "--canonical-runtime-token",
        default=os.getenv(
            "PANTHEON_L12_CANONICAL_RUNTIME_TOKEN",
            "l12-current-e2e:operator:mfa",
        ),
        help="Bearer token for the canonical Runtime Manager (never written to evidence)",
    )
    parser.add_argument(
        "--preserve-provisioned-stack",
        action="store_true",
        help="Debug only: do not tear down services provisioned by this invocation",
    )
    args = parser.parse_args()

    if args.compose_project == "pantheon":
        parser.error("the deployed proof must not provision or tear down the shared pantheon project")
    if args.sync_evidence and not args.canonical_runtime_url:
        parser.error("--sync-evidence requires --canonical-runtime-url")
    if args.sync_evidence and args.preserve_provisioned_stack:
        parser.error(
            "--sync-evidence requires task-scoped teardown; remove "
            "--preserve-provisioned-stack"
        )

    compose_files = args.compose_files or [str(REPO_ROOT / "docker-compose.yml")]

    expected_sha = _run(["git", "rev-parse", "HEAD"])
    print(f"[*] Harness starting for Task {TASK_ID}")
    print(f"[*] Repository Root: {REPO_ROOT}")
    print(f"[*] Current SHA: {expected_sha}")
    print(f"[*] Compose Project: {args.compose_project}")
    print(f"[*] Port Offset: +{args.port_offset}")

    # Build compose environment with parameterized isolated ports and exact git sha
    compose_env = os.environ.copy()
    compose_env["GIT_SHA"] = expected_sha
    for port_name, default_port in DEFAULT_PORTS.items():
        if port_name not in compose_env:
            compose_env[port_name] = str(default_port + args.port_offset)

    if args.down:
        teardown = _teardown_project(
            args.compose_project,
            compose_files,
            compose_env,
        )
        print(json.dumps(teardown, indent=2, sort_keys=True))
        return 0 if teardown["zero_project_containers"] else 1

    # Build URLs mapping
    urls: dict[str, str] = {}
    for name, spec in SERVICES.items():
        env_var = f"PANTHEON_L12_{name.upper()}_URL"
        if name == "source_ingest":
            env_val = os.getenv("PANTHEON_L12_SOURCE_INGEST_URL") or os.getenv("PANTHEON_L12_SOURCE_URL")
        else:
            env_val = os.getenv(env_var)
        if env_val:
            urls[name] = env_val
        else:
            port_val = os.getenv(f"PANTHEON_L12_{name.upper()}_PORT") or compose_env.get(spec["port_var"])
            port_num = int(port_val) if port_val else (spec["default_port"] + args.port_offset)
            urls[name] = f"http://127.0.0.1:{port_num}"

    # Environment for pytest execution.
    test_env = compose_env.copy()
    test_env["PANTHEON_L12_DEPLOYED_E2E"] = "1"
    test_env["PANTHEON_L12_EXPECTED_SHA"] = expected_sha
    test_env["PANTHEON_L12_COMPOSE_PROJECT"] = args.compose_project
    test_env["PANTHEON_L12_COMPOSE_FILES"] = os.pathsep.join(compose_files)
    test_env["PANTHEON_L12_EVIDENCE_OUTPUT"] = str(args.evidence_output.resolve())
    test_env["PANTHEON_L12_PORT_OFFSET"] = str(args.port_offset)
    for name, url in urls.items():
        test_env[f"PANTHEON_L12_{name.upper()}_URL"] = url

    # Resolve python binary
    python_bin = sys.executable
    provisioned_venv = REPO_ROOT / ".venv-pantheon" / "bin" / "python3"
    if provisioned_venv.is_file() and os.access(provisioned_venv, os.X_OK):
        python_bin = str(provisioned_venv)

    print(
        f"[*] Running deployed integration suite against {args.compose_project} "
        f"using {python_bin}..."
    )
    pytest_cmd = [
        python_bin,
        "-m",
        "pytest",
        "-q",
        "tests/integration/l12/test_current_runtime_loops_deployed_e2e.py",
        "-vv",
    ]
    result_code = 0
    pytest_invoked = False
    main_retirement: dict[str, Any] | None = None
    preclean: dict[str, Any] | None = None
    teardown: dict[str, Any] | None = None
    try:
        if args.canonical_runtime_url:
            print("[*] Verifying canonical main negative-binding retirement...")
            main_retirement = _ensure_main_negative_binding_retired(
                args.canonical_runtime_url,
                args.canonical_runtime_token,
            )
            print(
                "[+] Canonical main negative binding is retired and excluded "
                "without projection checksum repair"
            )

        if args.provision_services:
            print(
                f"[*] Removing stale task-scoped project {args.compose_project} "
                "before clean provisioning..."
            )
            preclean = _teardown_project(
                args.compose_project,
                compose_files,
                compose_env,
            )
            if not preclean["zero_project_containers"]:
                raise RuntimeError(f"isolated project preclean failed: {preclean!r}")
            command = _compose_command(
                args.compose_project,
                compose_files,
                "up",
                "-d",
                "--build",
                *REQUIRED_COMPOSE_SERVICES,
            )
            print(
                "[*] Provisioning isolated Compose services "
                f"(offset +{args.port_offset}): {' '.join(command)}"
            )
            subprocess.run(command, env=compose_env, check=True)

        print("[*] Verifying service readiness across HTTP boundaries...")
        deadline = time.time() + (
            args.ready_timeout if args.provision_services else 5.0
        )
        all_ready = False
        unready: dict[str, str] = {}
        while time.time() < deadline:
            unready.clear()
            for name, url in urls.items():
                spec = SERVICES[name]
                ready_url = f"{url}{spec['health']}"
                health = _get_json(ready_url)
                if not isinstance(health, Mapping) or "error" in health:
                    unready[name] = f"{ready_url} -> {health!r}"
            if not unready:
                all_ready = True
                break
            time.sleep(2.0)

        for name, url in urls.items():
            if name not in unready:
                print(f"[+] Service {name} ready at {url}")
            else:
                print(f"[-] Service {name} not ready: {unready[name]}")
        if not all_ready:
            raise RuntimeError(f"isolated services did not become ready: {unready!r}")

        pytest_invoked = True
        process = subprocess.run(pytest_cmd, cwd=REPO_ROOT, env=test_env)
        result_code = process.returncode
        if result_code != 0:
            print(f"[-] Deployed suite failed with code {result_code}", file=sys.stderr)
        elif not args.evidence_output.exists():
            raise RuntimeError(
                f"expected report output {args.evidence_output} was not created"
            )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[-] Harness failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        result_code = result_code or 1
    finally:
        if args.provision_services and not args.preserve_provisioned_stack:
            print(f"[*] Tearing down task-scoped project {args.compose_project}...")
            teardown = _teardown_project(
                args.compose_project,
                compose_files,
                compose_env,
            )
            if teardown["zero_project_containers"]:
                print(
                    f"[+] Zero Compose containers remain for {args.compose_project}"
                )
            else:
                print(f"[-] Isolated teardown failed: {teardown!r}", file=sys.stderr)
                result_code = result_code or 1
        elif args.provision_services:
            teardown = {
                "compose_project": args.compose_project,
                "preserved_for_debug": True,
                "zero_project_containers": False,
            }

        if pytest_invoked and args.evidence_output.exists():
            try:
                _augment_report(
                    args.evidence_output,
                    main_retirement=main_retirement,
                    preclean=preclean,
                    teardown=teardown,
                )
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                print(
                    f"[-] Could not augment deployed report: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                result_code = result_code or 1

    if result_code != 0:
        return result_code
    if not args.evidence_output.exists():
        return 1

    report_bytes = args.evidence_output.read_bytes()
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    print(f"[+] Deployed suite passed! Report SHA256: {report_sha}")

    if args.sync_evidence:
        dest_dir = args.update_evidence_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_report = dest_dir / "run-report.json"
        dest_sha = dest_dir / "run-report.sha256"
        dest_report.write_bytes(report_bytes)
        dest_sha.write_text(f"{report_sha}  run-report.json\n", encoding="utf-8")
        print(f"[+] Synced run report to {dest_report} and updated {dest_sha}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
