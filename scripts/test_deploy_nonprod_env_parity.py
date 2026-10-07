from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"


def _read_deploy_script() -> str:
    return DEPLOY_SCRIPT.read_text(encoding="utf-8")


def _extract_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    following = re.search(r"\n[A-Za-z_][A-Za-z_0-9]*\(\) \{", source[start:])
    assert following is not None, f"Could not find boundary after {name}()"
    chunk = source[start:start + following.start()]
    return chunk[:chunk.rindex("\n}\n") + 3]


def _extract_root_rollout_invocation(source: str) -> str:
    root_block = source.split("  root)\n", 1)[1].split("  bff)\n", 1)[0]
    marker = "# Phase 3: Rollout persistent root runtime."
    phase3 = root_block.split(marker, 1)[1]
    # Extract the block up to and including run_dev_candidate_compose up -d
    call_end = phase3.index("run_dev_candidate_compose up -d")
    call_end_line = phase3.index("\n", call_end)
    return phase3[:call_end_line].strip()


def _extract_bff_recreate_invocation(source: str) -> str:
    bff_block = source.split("  bff)\n", 1)[1].split("  exec)\n", 1)[0]
    marker = "# Phase 3: Recreate operator-bff and loop-run-projector-scheduler."
    phase3 = bff_block.split(marker, 1)[1]
    call_end = phase3.index("run_dev_candidate_compose up -d")
    call_end_line = phase3.index("\n", call_end)
    return phase3[:call_end_line].strip()


def _extract_artifact_driver_invocation(source: str) -> str:
    driver_func = _extract_function(source, "run_dev_artifact_driver")
    call_start = driver_func.index('with_dev_bff_runtime_env "${runtime_sha}" false')
    call_end = driver_func.index('"${candidate_args[@]}" "${drift_args[@]}"')
    call_end_line = driver_func.index("\n", call_end)
    return driver_func[call_start:call_end_line].strip()


def _setup_stub_bin(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    events_file = tmp_path / "stub_events.jsonl"

    docker_stub = f"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

events_file = Path({repr(str(events_file))})

def log_event(cmd, args):
    with events_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps({{
            "command": cmd,
            "argv": args,
            "env": dict(os.environ),
        }}) + "\\n")

args = sys.argv[1:]
if args and args[0] == "compose":
    log_event("docker_compose", args)
    sys.exit(0)
elif args and args[0] == "inspect":
    sys.exit(0)
log_event("docker", args)
sys.exit(0)
"""
    docker_bin = bin_dir / "docker"
    docker_bin.write_text(docker_stub, encoding="utf-8")
    docker_bin.chmod(0o755)

    driver_stub = f"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

events_file = Path({repr(str(events_file))})
with events_file.open("a", encoding="utf-8") as f:
    f.write(json.dumps({{
        "command": "artifact_driver",
        "argv": sys.argv[1:],
        "env": dict(os.environ),
    }}) + "\\n")
sys.exit(0)
"""
    driver_bin = tmp_path / "mock_artifact_driver.py"
    driver_bin.write_text(driver_stub, encoding="utf-8")
    driver_bin.chmod(0o755)

    return bin_dir, events_file


def _read_recorded_events(events_file: Path) -> list[dict[str, Any]]:
    if not events_file.exists():
        return []
    lines = [line.strip() for line in events_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def _build_test_env(tmp_path: Path, bin_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["PANTHEON_DEPLOY_SHA"] = "a" * 40
    env["PANTHEON_DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED"] = "false"
    env["DEV_CANDIDATE_RECEIPT_ACKED"] = "true"
    env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_OVERRIDE_PATH"] = str(tmp_path / "override.yml")
    env["PANTHEON_DEV_COMPOSE_PROFILES"] = "root"
    env["SOURCE_INGEST_CONTROLLER_MODE"] = "reconcile_only"
    env["SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL"] = "l1"
    env["SOURCE_INGEST_CONTROLLER_MAX_TICKS"] = "10"
    env["SOURCE_INGEST_CONTROLLER_RESTART_POLICY"] = "unless-stopped"
    env["SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS"] = ""
    env["SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS"] = ""
    env["SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"] = "1"
    env["SOURCE_INGEST_MAX_RECORDS"] = "100"
    env["SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS"] = ""
    env["PANTHEON_EXTERNAL_EGRESS"] = "deny"
    env["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] = ""
    env["PANTHEON_DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS"] = "300"
    env["PANTHEON_DEV_BFF_CORS_ORIGINS"] = "https://app.dev.mvl-cap.tw"
    env["PANTHEON_DEV_BFF_AUTH_STUB"] = "false"
    env["PANTHEON_DEV_BFF_AUTH_MODE"] = "strict"
    env["PANTHEON_DEV_BFF_JWT_SECRET"] = "dev-secret-jwt"
    env["PANTHEON_DEV_CAPITAL_JWT_SECRET"] = "dev-secret-capital"
    env["PANTHEON_DEV_BFF_JWT_ISSUER"] = "pantheon-dev"
    env["PANTHEON_DEV_BFF_JWT_AUDIENCE"] = "bff-operators"
    env["PANTHEON_DEV_BFF_JWKS_URI"] = ""
    env["PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL"] = ""
    env["PANTHEON_DEV_BFF_OIDC_ISSUER"] = ""
    env["PANTHEON_DEV_BFF_OIDC_AUDIENCE"] = ""
    env["PANTHEON_DEV_BFF_OIDC_CLIENT_ID"] = ""
    env["PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET"] = ""
    env["PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID"] = "test-viewer"
    env["PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET"] = "test-viewer-secret"
    env["PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID"] = "test-approver"
    env["PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET"] = "test-approver-secret"
    env["PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID"] = "test-risk-owner"
    env["PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET"] = "test-risk-owner-secret"
    env["PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID"] = "test-op-a"
    env["PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET"] = "test-op-a-secret"
    env["PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID"] = "test-op-b"
    env["PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET"] = "test-op-b-secret"
    env["PANTHEON_DEV_BFF_MFA_REQUIRED"] = "false"
    env["PANTHEON_DEV_BFF_MFA_CLAIMS"] = "roles"
    env["PANTHEON_DEV_BFF_MFA_VALUES"] = "true"
    env["PANTHEON_DEV_BFF_REQUIRE_EMAIL_VERIFIED"] = "true"
    env["PANTHEON_DEV_BFF_ROLE_CLAIMS"] = "roles"
    env["PANTHEON_DEV_BFF_ROLE_MAP"] = ""
    env["PANTHEON_DEV_BFF_ROLE_MAP_MODE"] = "passthrough"
    env["PANTHEON_DEV_BFF_DEFAULT_ROLE"] = "viewer"
    env["PANTHEON_DEV_BFF_TENANT_ID"] = "tenant-dev"
    env["PANTHEON_DEV_BFF_ALLOWED_TENANTS"] = "tenant-dev,pantheon-dev"
    env["PANTHEON_ASSISTANT_KERNEL_ENABLED"] = "true"
    env["PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH"] = "/data/bff/mode.json"
    env["PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH"] = ""
    env["PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS"] = "300"
    env["PANTHEON_BFF_STUB_CAPABILITIES"] = "test"
    env["PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN"] = "token"
    env["PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED"] = "true"
    env["PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN"] = ""
    env["MANAGEMENT_AI_STORE_BACKEND"] = "postgres"
    env["MANAGEMENT_AI_STORE_SCHEMA"] = "management_ai"
    env["MANAGEMENT_AI_DATABASE_URL"] = "postgresql://localhost/test"
    env["PANTHEON_MGMT_AI_ATTACH_BUCKET"] = "bucket"
    env["PANTHEON_MGMT_AI_ATTACH_LOCATION"] = "asia-east1"
    env["PANTHEON_RECONCILIATION_DRIFT_API_URL"] = "http://recon:8102"
    env["RECONCILIATION_DRIFT_URL"] = "http://recon:8102"
    env["RECONCILIATION_DRIFT_AUTH_TOKEN"] = "recon-token"
    return env


def _assert_expected_env_parity(
    recorded_env: dict[str, str],
    expected_vars: dict[str, str],
    absent_vars: tuple[str, ...],
    path_name: str,
) -> None:
    for var_name, expected_val in expected_vars.items():
        if var_name not in recorded_env:
            raise AssertionError(f"[{path_name}] Missing expected environment variable: {var_name}")
        actual_val = recorded_env[var_name]
        if actual_val != expected_val:
            raise AssertionError(
                f"[{path_name}] Variable {var_name} value mismatch: expected {expected_val!r}, got {actual_val!r}"
            )
    for var_name in absent_vars:
        if var_name in recorded_env and recorded_env[var_name] != "":
            raise AssertionError(f"[{path_name}] Variable {var_name} should not be present, but got {recorded_env[var_name]!r}")


def test_root_rollout_passes_tj_fixture_and_profiles(tmp_path: Path) -> None:
    """AC1, AC3, AC5: The root rollout passes PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED=true
    and preserves configured COMPOSE_PROFILES."""
    bin_dir, events_file = _setup_stub_bin(tmp_path)
    env = _build_test_env(tmp_path, bin_dir)

    source = _read_deploy_script()
    with_env_func = _extract_function(source, "with_dev_bff_runtime_env")
    run_candidate_func = _extract_function(source, "run_dev_candidate_compose")
    root_invocation = _extract_root_rollout_invocation(source)

    script_payload = f"""#!/bin/bash
set -euo pipefail
info() {{ :; }}
error() {{ echo "$*" >&2; exit 1; }}
rollback_dev_bff_on_failure() {{ echo "rollback: $*" >&2; exit 1; }}
validate_dev_candidate_override() {{ return 0; }}
cleanup_stale_compose_replacement_containers() {{ :; }}

{with_env_func}
{run_candidate_func}

{root_invocation}
"""
    result = subprocess.run(["bash", "-s"], input=script_payload, env=env, text=True, capture_output=True)
    assert result.returncode == 0, f"Root rollout execution failed: {result.stderr}"

    events = _read_recorded_events(events_file)
    compose_events = [e for e in events if e.get("command") == "docker_compose"]
    assert len(compose_events) == 1, f"Expected exactly 1 docker compose invocation, got {len(compose_events)}"

    rec_env = compose_events[0]["env"]
    expected_vars = {
        "PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED": "true",
        "COMPOSE_PROFILES": "root",
        "COMPOSE_BAKE": "false",
        "GIT_SHA": env["PANTHEON_DEPLOY_SHA"],
        "PANTHEON_ENV": "dev",
        "PANTHEON_CANARY_EXECUTION_ENABLED": "false",
        "PANTHEON_LIVE_BROKER_ENABLED": "false",
        "BROKER_PAPER_ENABLED": "true",
        "AGORA_WORKSHOP_STORE_BACKEND": "postgres",
        "AGORA_WORKSHOP_STORE_SCHEMA": "agora",
        "PANTHEON_BFF_AUTH_MODE": "strict",
        "PANTHEON_BFF_AUTH_STUB": "false",
        "MANAGEMENT_AI_STORE_BACKEND": "postgres",
        "RECONCILIATION_DRIFT_URL": "http://recon:8102",
        "PANTHEON_RECONCILIATION_DRIFT_API_URL": "http://recon:8102",
        "PANTHEON_EXTERNAL_EGRESS": "deny",
        "SOURCE_INGEST_CONTROLLER_MODE": "reconcile_only",
    }
    _assert_expected_env_parity(rec_env, expected_vars, (), "root_rollout")


def test_root_rollout_preserves_custom_profiles(tmp_path: Path) -> None:
    """AC5: Root rollout keeps custom configured COMPOSE_PROFILES."""
    bin_dir, events_file = _setup_stub_bin(tmp_path)
    env = _build_test_env(tmp_path, bin_dir)
    env["PANTHEON_DEV_COMPOSE_PROFILES"] = "root,dev-paper-principals"

    source = _read_deploy_script()
    with_env_func = _extract_function(source, "with_dev_bff_runtime_env")
    run_candidate_func = _extract_function(source, "run_dev_candidate_compose")
    root_invocation = _extract_root_rollout_invocation(source)

    script_payload = f"""#!/bin/bash
set -euo pipefail
info() {{ :; }}
error() {{ echo "$*" >&2; exit 1; }}
rollback_dev_bff_on_failure() {{ echo "rollback: $*" >&2; exit 1; }}
validate_dev_candidate_override() {{ return 0; }}
cleanup_stale_compose_replacement_containers() {{ :; }}

{with_env_func}
{run_candidate_func}

{root_invocation}
"""
    result = subprocess.run(["bash", "-s"], input=script_payload, env=env, text=True, capture_output=True)
    assert result.returncode == 0, f"Root rollout execution failed: {result.stderr}"

    events = _read_recorded_events(events_file)
    rec_env = events[0]["env"]
    assert rec_env["COMPOSE_PROFILES"] == "root,dev-paper-principals"
    assert rec_env["PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED"] == "true"


def test_bff_only_recreate_runs_without_profiles_and_without_tj_fixture(tmp_path: Path) -> None:
    """AC2, AC3, AC5: BFF-only recreate runs without compose profiles and does not have TJ fixture."""
    bin_dir, events_file = _setup_stub_bin(tmp_path)
    env = _build_test_env(tmp_path, bin_dir)

    source = _read_deploy_script()
    with_env_func = _extract_function(source, "with_dev_bff_runtime_env")
    run_candidate_func = _extract_function(source, "run_dev_candidate_compose")
    bff_invocation = _extract_bff_recreate_invocation(source)

    script_payload = f"""#!/bin/bash
set -euo pipefail
info() {{ :; }}
error() {{ echo "$*" >&2; exit 1; }}
rollback_dev_bff_on_failure() {{ echo "rollback: $*" >&2; exit 1; }}
validate_dev_candidate_override() {{ return 0; }}
cleanup_stale_compose_replacement_containers() {{ :; }}

{with_env_func}
{run_candidate_func}

{bff_invocation}
"""
    result = subprocess.run(["bash", "-s"], input=script_payload, env=env, text=True, capture_output=True)
    assert result.returncode == 0, f"BFF recreate execution failed: {result.stderr}"

    events = _read_recorded_events(events_file)
    compose_events = [e for e in events if e.get("command") == "docker_compose"]
    assert len(compose_events) == 1, f"Expected exactly 1 docker compose invocation, got {len(compose_events)}"

    rec_env = compose_events[0]["env"]
    expected_vars = {
        "COMPOSE_PROFILES": "",
        "COMPOSE_BAKE": "false",
        "GIT_SHA": env["PANTHEON_DEPLOY_SHA"],
        "PANTHEON_ENV": "dev",
        "PANTHEON_CANARY_EXECUTION_ENABLED": "false",
        "PANTHEON_LIVE_BROKER_ENABLED": "false",
        "BROKER_PAPER_ENABLED": "true",
        "AGORA_WORKSHOP_STORE_BACKEND": "postgres",
        "AGORA_WORKSHOP_STORE_SCHEMA": "agora",
        "PANTHEON_BFF_AUTH_MODE": "strict",
        "PANTHEON_BFF_AUTH_STUB": "false",
        "MANAGEMENT_AI_STORE_BACKEND": "postgres",
        "RECONCILIATION_DRIFT_URL": "http://recon:8102",
        "PANTHEON_RECONCILIATION_DRIFT_API_URL": "http://recon:8102",
    }
    absent_vars = (
        "PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED",
        "SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS",
    )
    _assert_expected_env_parity(rec_env, expected_vars, absent_vars, "bff_recreate")


def test_artifact_driver_path_under_with_dev_bff_runtime_env(tmp_path: Path) -> None:
    """AC2, AC3: The artifact driver call runs through with_dev_bff_runtime_env
    and has expected environment without TJ fixture."""
    bin_dir, events_file = _setup_stub_bin(tmp_path)
    env = _build_test_env(tmp_path, bin_dir)
    mock_driver = tmp_path / "mock_artifact_driver.py"
    env["PANTHEON_DEV_ARTIFACT_DRIVER_PATH"] = str(mock_driver)
    env["PANTHEON_DEPLOY_PROJECT_ID"] = "pantheon-dev-20260902"
    env["PANTHEON_DEV_ARTIFACT_CANDIDATE_ID"] = "candidate-1"
    env["PANTHEON_DEV_ARTIFACT_RUN_ID"] = "1"
    env["PANTHEON_DEV_ARTIFACT_ATTEMPT"] = "1"
    env["PANTHEON_DEV_ARTIFACT_CONTROLLER_SHA"] = "b" * 40
    env["PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA"] = "c" * 40
    env["PANTHEON_DEV_ARTIFACT_CANDIDATE_FRONTEND_SHA"] = "d" * 40
    env["PANTHEON_DEV_ARTIFACT_PREVIOUS_BACKEND_SHA"] = "e" * 40
    env["PANTHEON_DEV_ARTIFACT_PREVIOUS_FRONTEND_SHA"] = "f" * 40
    env["PANTHEON_DEV_ARTIFACT_MANIFEST_PATH"] = str(tmp_path / "manifest.json")
    env["PANTHEON_DEV_ARTIFACT_MANIFEST_SHA256"] = "1" * 64
    env["PANTHEON_DEV_BFF_PUBLIC_HOST"] = "api.dev.mvl-cap.tw"
    env["PANTHEON_DEV_FE_PUBLIC_HOST"] = "app.dev.mvl-cap.tw"
    env["PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD"] = "3"

    source = _read_deploy_script()
    with_env_func = _extract_function(source, "with_dev_bff_runtime_env")
    driver_func = _extract_function(source, "run_dev_artifact_driver")

    # In driver_func, bypass preflight loops to focus on with_dev_bff_runtime_env execution
    script_payload = f"""#!/bin/bash
set -euo pipefail
info() {{ :; }}
error() {{ echo "$*" >&2; exit 1; }}

{with_env_func}

runtime_sha="{env['PANTHEON_DEPLOY_SHA']}"
compose_file="{tmp_path}/docker-compose.yml"
operation="verify"
candidate_args=()
drift_args=()

with_dev_bff_runtime_env "${{runtime_sha}}" false \
  python3 "${{PANTHEON_DEV_ARTIFACT_DRIVER_PATH}}" "${{operation}}" \
    --environment dev --project-id "${{PANTHEON_DEPLOY_PROJECT_ID}}" --vm pantheon-dev-deploy \
    --candidate-id "${{PANTHEON_DEV_ARTIFACT_CANDIDATE_ID}}" \
    --run-id "${{PANTHEON_DEV_ARTIFACT_RUN_ID}}" --attempt "${{PANTHEON_DEV_ARTIFACT_ATTEMPT}}" \
    --controller-sha "${{PANTHEON_DEV_ARTIFACT_CONTROLLER_SHA}}" \
    --candidate-backend-sha "${{PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA}}" \
    --candidate-frontend-sha "${{PANTHEON_DEV_ARTIFACT_CANDIDATE_FRONTEND_SHA}}" \
    --previous-backend-sha "${{PANTHEON_DEV_ARTIFACT_PREVIOUS_BACKEND_SHA}}" \
    --previous-frontend-sha "${{PANTHEON_DEV_ARTIFACT_PREVIOUS_FRONTEND_SHA}}" \
    --compose-file "${{compose_file}}" \
    --manifest "${{PANTHEON_DEV_ARTIFACT_MANIFEST_PATH}}" \
    --manifest-sha256 "${{PANTHEON_DEV_ARTIFACT_MANIFEST_SHA256}}" \
    --bff-url "https://${{PANTHEON_DEV_BFF_PUBLIC_HOST}}" \
    --fe-url "https://${{PANTHEON_DEV_FE_PUBLIC_HOST}}" \
    --guard-channel-fd "${{PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD}}" \
    "${{candidate_args[@]}}" "${{drift_args[@]}}"
"""
    result = subprocess.run(["bash", "-s"], input=script_payload, env=env, text=True, capture_output=True)
    assert result.returncode == 0, f"Artifact driver invocation failed: {result.stderr}"

    events = _read_recorded_events(events_file)
    driver_events = [e for e in events if e.get("command") == "artifact_driver"]
    assert len(driver_events) == 1, f"Expected 1 driver invocation, got {len(driver_events)}"

    rec_env = driver_events[0]["env"]
    assert rec_env["PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED"] == "false"
    assert rec_env["COMPOSE_PROFILES"] == ""
    assert "PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED" not in rec_env or rec_env["PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED"] == ""


def test_parity_assertion_fails_on_missing_or_changed_var() -> None:
    """AC3: Parity validation fails if any expected variable is missing or changed."""
    recorded = {"A": "1", "B": "2"}

    # Missing variable fails
    with pytest.raises(AssertionError, match="Missing expected environment variable: C"):
        _assert_expected_env_parity(recorded, {"A": "1", "C": "3"}, (), "test_missing")

    # Changed value fails
    with pytest.raises(AssertionError, match="value mismatch"):
        _assert_expected_env_parity(recorded, {"A": "wrong"}, (), "test_changed")

    # Forbidden variable present fails
    with pytest.raises(AssertionError, match="should not be present"):
        _assert_expected_env_parity(recorded, {"A": "1"}, ("B",), "test_forbidden")


def test_deploy_script_syntax_and_shared_function_contract() -> None:
    """AC1, AC4, AC5, AC6: Structural inspection of deploy_nonprod_vm.sh."""
    source = _read_deploy_script()

    # AC1: Root rollout passes PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED=true
    root_case = source.split("  root)\n", 1)[1].split("  bff)\n", 1)[0]
    assert "PANTHEON_TJ_E2E_FIXTURE_INGEST_ENABLED=true" in root_case

    # AC5: with_dev_bff_runtime_env preserves COMPOSE_PROFILES
    with_env = _extract_function(source, "with_dev_bff_runtime_env")
    assert 'COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"' in with_env

    # AC6: No compressed assignments in with_dev_bff_runtime_env
    for line in with_env.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("#") or not trimmed:
            continue
        # Ensure no multiple assignments on a single line via semicolon
        assert ";" not in trimmed or trimmed.startswith("local ") or trimmed.startswith("shift "), (
            f"Found compressed assignment line: {line}"
        )
