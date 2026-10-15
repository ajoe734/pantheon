"""Execute the real shell payload with isolated recorder commands, never a VM.

The synthetic driver below verifies shell-to-driver arguments and failure
propagation only. Real image/Compose/owner/guard validation belongs to the
independent artifact driver tests; none of these fixtures is hosted evidence.
"""
from __future__ import annotations

import hashlib
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

# Reuse the original driver fixtures (real driver code, isolated Docker/HTTP models).
from scripts.test_dev_release_artifact_driver import case  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/deploy_nonprod_vm.sh"
SHA = "a" * 40
PRIOR = "b" * 40
CONTROLLER_ROOT = "/home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/release-artifacts/controllers"
REAL_NODE = Path(__import__("shutil").which("node") or "/usr/bin/node").resolve()
REAL_NODE_DIR = str(REAL_NODE.parent)


def _source() -> str:
    return SCRIPT.read_text()


def _function(name: str) -> str:
    source = _source()
    start = source.index(f"{name}() {{")
    # Use the next shell function boundary, not the first standalone '}', which
    # may close a Python dict inside an embedded here-document.
    following = re.search(r"\n[A-Za-z_][A-Za-z_0-9]*\(\) \{", source[start:])
    assert following is not None
    chunk = source[start:start + following.start()]
    return chunk[:chunk.rindex("\n}\n") + 3]


def _remote() -> str:
    return _source().split("<<'REMOTE'\n", 1)[1].split("\nREMOTE\n", 1)[0]


DRIVER = '''import hashlib, json, os, pathlib, sys
operation = sys.argv[1]
args = dict(zip(sys.argv[2::2], sys.argv[3::2]))
events = pathlib.Path(os.environ["RECORDER"])
with events.open("a") as out:
    out.write(json.dumps({"command": "driver", "operation": operation, "args": args,
        "runtime": {k: os.environ.get(k) for k in (
            "GIT_SHA", "PANTHEON_ENV", "PANTHEON_CANARY_EXECUTION_ENABLED",
            "PANTHEON_LIVE_BROKER_ENABLED", "BROKER_PAPER_ENABLED",
            "PANTHEON_BFF_AUTH_MODE", "PANTHEON_BFF_AUTH_STUB",
            "PANTHEON_BFF_MFA_REQUIRED", "PANTHEON_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED",
            "PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED",
            "PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID",
            "PANTHEON_BFF_GOVERNANCE_SERVICE_TOKEN_FILE")}}) + "\\n")
manifest = pathlib.Path(args["--manifest"])
if hashlib.sha256(manifest.read_bytes()).hexdigest() != args["--manifest-sha256"]:
    raise SystemExit(75)
if os.environ.get("DRIVER_REJECT"):
    raise SystemExit(75)
os.fstat(int(args["--guard-channel-fd"]))
print(json.dumps({"operation": operation, "fixture_only": True}))
'''


@pytest.fixture
def fixture(tmp_path: Path):
    recorder = tmp_path / "commands.jsonl"
    artifact_root = tmp_path / "release-artifacts"
    controller_root = artifact_root / "controllers"
    controller_dir = controller_root / ("d" * 40)
    for directory in (artifact_root, controller_root, controller_dir):
        directory.mkdir(mode=0o700)
    library = controller_dir / "dev_release_artifacts.py"
    library.write_text("# isolated non-executable shell contract fixture\n")
    library.chmod(0o400)
    driver = controller_dir / "dev_release_artifact_driver.py"
    driver.write_text(DRIVER)
    driver.chmod(0o400)
    baseline = artifact_root / ("baseline-12345-2-" + "c" * 64)
    baseline.mkdir(mode=0o700)
    manifest = baseline / "manifest.json"
    manifest.write_text(json.dumps({"fixture": True, "previous": PRIOR}))
    compose = tmp_path / "baseline" / "docker-compose.yml"
    compose.parent.mkdir()
    compose.write_text("services: {}\n")
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    ambient_compose = ambient / "docker-compose.yml"
    ambient_compose.write_text("services: {ambient: {image: ambient-image}}\n")
    candidate_compose = tmp_path / "candidate" / "docker-compose.yml"
    candidate_compose.parent.mkdir()
    candidate_compose.write_text("services: {candidate: {image: candidate-image}}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker", "git", "curl", "apt-get", "systemctl", "sudo"):
        stub = bin_dir / name
        stub.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                        'with open(os.environ["RECORDER"],"a") as out:\n'
                        ' out.write(json.dumps({"command":sys.argv[0].split("/")[-1],"args":sys.argv[1:]})+"\\n")\n'
                        'raise SystemExit(97)\n')
        stub.chmod(0o755)
    stub_node = bin_dir / "node"
    stub_node.write_text('#!/usr/bin/env bash\n'
                         'echo "node: command not found" >&2\n'
                         'exit 127\n')
    stub_node.chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "RECORDER": str(recorder),
           "SYNTHETIC_CONTROLLER_ROOT": str(controller_root),
           "PANTHEON_DEV_FE_NODE_BIN_DIR": REAL_NODE_DIR}
    # Explicitly synthetic environment for the actual approved-prefix function.
    for variable in re.findall(r'\$\{([A-Z][A-Z0-9_]*)', _function("with_dev_bff_runtime_env")):
        env[variable] = "fixture"
    env.update({
        "PANTHEON_DEPLOY_ENV": "dev", "PANTHEON_DEPLOY_COMPONENT": "bff",
        "PANTHEON_DEPLOY_PROJECT_ID": "pantheon-dev-20260902", "PANTHEON_DEPLOY_SHA": PRIOR,
        "PANTHEON_REMOTE_DIR": str(ambient),
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE": str(candidate_compose),
        "PANTHEON_DEV_ARTIFACT_RESTORE": "true", "PANTHEON_DEV_BFF_AUTH_MODE": "strict",
        "PANTHEON_DEV_BFF_AUTH_STUB": "false", "PANTHEON_DEV_BFF_PUBLIC_HOST": "api.dev.mvl-cap.tw",
        "PANTHEON_DEV_BFF_MFA_REQUIRED": "false",
        "PANTHEON_DEV_FE_PUBLIC_HOST": "app.dev.mvl-cap.tw",
        "PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID": "fixture-viewer",
        "PANTHEON_BFF_GOVERNANCE_SERVICE_TOKEN_FILE": "/run/pantheon-principals/fixture.jwt",
        "PANTHEON_DEV_ARTIFACT_MANIFEST_PATH": str(manifest),
        "PANTHEON_DEV_ARTIFACT_MANIFEST_SHA256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "PANTHEON_DEV_ARTIFACT_DRIVER_PATH": str(driver),
        "PANTHEON_DEV_ARTIFACT_DRIVER_SHA256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "PANTHEON_DEV_ARTIFACT_LIBRARY_SHA256": hashlib.sha256(library.read_bytes()).hexdigest(),
        "PANTHEON_DEV_ARTIFACT_COMPOSE_FILE": str(compose),
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_ID": "c" * 64, "PANTHEON_DEV_ARTIFACT_RUN_ID": "12345",
        "PANTHEON_DEV_ARTIFACT_ATTEMPT": "2", "PANTHEON_DEV_ARTIFACT_CONTROLLER_SHA": "d" * 40,
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA": SHA,
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_FRONTEND_SHA": "e" * 40,
        "PANTHEON_DEV_ARTIFACT_PREVIOUS_BACKEND_SHA": PRIOR,
        "PANTHEON_DEV_ARTIFACT_PREVIOUS_FRONTEND_SHA": "f" * 40,
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": "11111111-1111-4111-8111-111111111111",
    })
    candidate_override = {"services": {name: {"image": "sha256:" + str(i) * 64, "pull_policy": "never"}
        for i, name in enumerate(("operator-bff", "agora-interaction-worker", "loop-run-projector-scheduler"), 1)}}
    candidate_override["services"]["operator-bff"]["environment"] = {
        "BFF_IMAGE_DIGEST": candidate_override["services"]["operator-bff"]["image"]}
    encoded = lambda value: (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    override = baseline / "candidate-images.override.json"
    override.write_bytes(encoded(candidate_override)); override.chmod(0o600)
    record = {"fixture_only": True, "services": {name: {"image_id": row["image"]}
        for name, row in candidate_override["services"].items()},
        "image_override_sha256": hashlib.sha256(override.read_bytes()).hexdigest()}
    candidate = baseline / "candidate-images.json"
    candidate.write_bytes(encoded(record)); candidate.chmod(0o600)
    result = {"candidate_image_manifest_path": str(candidate),
              "candidate_image_manifest_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
              "candidate_image_manifest": record, "candidate_image_override_path": str(override),
              "candidate_image_override_sha256": record["image_override_sha256"]}
    env.update({"PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_PATH": str(candidate),
                "PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256": result["candidate_image_manifest_sha256"],
                "FIXTURE_SEAL_OUTPUT": json.dumps(result)})
    driver.chmod(0o600)
    driver.write_text(DRIVER.replace('print(json.dumps({"operation": operation, "fixture_only": True}))',
        'print(os.environ["FIXTURE_SEAL_OUTPUT"] if operation == "seal-candidate" else '
        'json.dumps({"operation": operation, "fixture_only": True}))'))
    driver.chmod(0o400)
    env["PANTHEON_DEV_ARTIFACT_DRIVER_SHA256"] = hashlib.sha256(driver.read_bytes()).hexdigest()
    return env, recorder, driver, library


def _run(payload: str, env: dict[str, str], *, guard=True, synthetic_root=True, ack=None, ack_eof=False, guard_eof=False):
    # Patch only this extracted test payload's fixed source constant. The live
    # script has no root override, and arbitrary /tmp drivers are NOT valid.
    if synthetic_root:
        payload = payload.replace(CONTROLLER_ROOT, env["SYNTHETIC_CONTROLLER_ROOT"])
        payload = payload.replace(str(Path(CONTROLLER_ROOT).parent), str(Path(env["SYNTHETIC_CONTROLLER_ROOT"]).parent))
    if not guard:
        return subprocess.run(["bash", "-s"], input=payload, env=env, capture_output=True, text=True)
    read_fd, write_fd = os.pipe()
    ack_read, ack_write = os.pipe()
    try:
        os.write(write_fd, b"fixture pulse")
        extra = {}
        if ack is not None:
            os.write(ack_write, ack)
            extra["PANTHEON_DEV_ARTIFACT_RECEIPT_ACK_FD"] = str(ack_read)
        if guard_eof:
            os.close(write_fd)
            write_fd = None
        if ack_eof:
            os.close(ack_write)
            ack_write = None
        return subprocess.run(["bash", "-s"], input=payload,
                              env={**env, **extra, "PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD": str(read_fd)},
                              pass_fds=(read_fd, ack_read), capture_output=True, text=True)
    finally:
        os.close(read_fd)
        if write_fd is not None: os.close(write_fd)
        os.close(ack_read)
        if ack_write is not None: os.close(ack_write)


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_external_restore_executes_real_payload_without_checkout_or_other_mutations(fixture):
    env, recorder, *_ = fixture
    result = _run(_remote(), env)
    assert result.returncode == 0, result.stderr
    events = _events(recorder)
    assert [event["command"] for event in events] == ["driver"]
    event = events[0]
    assert event["operation"] == "restore"
    args = event["args"]
    assert args["--previous-backend-sha"] == PRIOR
    assert args["--candidate-backend-sha"] == SHA
    assert args["--manifest-sha256"] == env["PANTHEON_DEV_ARTIFACT_MANIFEST_SHA256"]
    assert args["--candidate-image-manifest-sha256"] == env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256"]
    assert args["--candidate-image-manifest"] == env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_PATH"]
    assert args["--compose-file"] == env["PANTHEON_DEV_ARTIFACT_COMPOSE_FILE"]
    assert args["--bff-url"] == "https://api.dev.mvl-cap.tw"
    assert args["--fe-url"] == "https://app.dev.mvl-cap.tw"
    assert event["runtime"] == {
        "GIT_SHA": PRIOR, "PANTHEON_ENV": "dev", "PANTHEON_CANARY_EXECUTION_ENABLED": "false",
        "PANTHEON_LIVE_BROKER_ENABLED": "false", "BROKER_PAPER_ENABLED": "true",
        "PANTHEON_BFF_AUTH_MODE": "strict", "PANTHEON_BFF_AUTH_STUB": "false",
        "PANTHEON_BFF_MFA_REQUIRED": "false", "PANTHEON_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED": None,
        "PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED": "false",
        "PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID": "fixture-viewer",
        "PANTHEON_BFF_GOVERNANCE_SERVICE_TOKEN_FILE": "/run/pantheon-principals/fixture.jwt",
    }


def test_restore_shell_leaves_legacy_auth_to_sealed_driver_not_candidate_flags(fixture):
    env, recorder, *_ = fixture
    env["PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED"] = "true"
    result = _run(_remote(), env)
    assert result.returncode == 0, result.stderr
    runtime = _events(recorder)[0]["runtime"]
    assert runtime["PANTHEON_BFF_MFA_REQUIRED"] == "false"
    assert runtime["PANTHEON_BFF_DEV_LOGIN_VIEWER_MFA_VERIFIED"] is None
    # The real driver's capture/restore tests separately require the sealed
    # baseline override before old Compose recreation, not this fake driver.


@pytest.mark.parametrize("variable,value", [
    ("PANTHEON_DEPLOY_COMPONENT", "root"), ("PANTHEON_DEPLOY_ENV", "staging-live"),
    ("PANTHEON_DEPLOY_SHA", SHA), ("PANTHEON_DEV_BFF_AUTH_MODE", "permissive"),
    ("PANTHEON_DEV_BFF_AUTH_STUB", "true"), ("PANTHEON_DEV_ARTIFACT_MANIFEST_PATH", ""),
    ("PANTHEON_DEV_ARTIFACT_MANIFEST_SHA256", ""),
    ("PANTHEON_DEV_ARTIFACT_DRIVER_SHA256", "0" * 64),
    ("PANTHEON_DEV_ARTIFACT_LIBRARY_SHA256", "9" * 64),
    ("PANTHEON_DEV_ROLLBACK_BACKEND_SHA", "8" * 40),
])
def test_restore_invalid_contract_stops_before_any_driver_or_other_command(fixture, variable, value):
    env, recorder, *_ = fixture
    result = _run(_remote(), {**env, variable: value})
    assert result.returncode != 0
    assert _events(recorder) == []


def test_restore_missing_remote_guard_stops_before_driver(fixture):
    env, recorder, *_ = fixture
    assert _run(_remote(), env, guard=False).returncode != 0
    assert _events(recorder) == []


def test_restore_tampered_driver_stops_before_execution(fixture):
    env, recorder, driver, _ = fixture
    driver.chmod(0o600)
    driver.write_text(driver.read_text() + "# tampered\n")
    driver.chmod(0o400)
    assert _run(_remote(), env).returncode != 0
    assert _events(recorder) == []


def test_unmodified_live_payload_rejects_arbitrary_tmp_driver_even_with_matching_hash(fixture):
    env, recorder, *_ = fixture
    result = _run(_remote(), env, synthetic_root=False)
    assert result.returncode != 0
    assert "pinned controller store" in result.stderr
    assert _events(recorder) == []


def test_restore_rejects_driver_path_bound_to_different_controller(fixture):
    env, recorder, *_ = fixture
    result = _run(_remote(), {**env, "PANTHEON_DEV_ARTIFACT_CONTROLLER_SHA": "9" * 40})
    assert result.returncode != 0
    assert _events(recorder) == []


@pytest.mark.parametrize("target,mode", [
    ("artifact_root", 0o777), ("controllers", 0o750), ("controller", 0o755),
    ("driver", 0o600), ("driver", 0o440), ("library", 0o444),
])
def test_restore_rejects_nonprivate_controller_store_or_mutable_implementation(fixture, target, mode):
    env, recorder, driver, library = fixture
    targets = {"artifact_root": driver.parents[2], "controllers": driver.parents[1],
               "controller": driver.parent, "driver": driver, "library": library}
    targets[target].chmod(mode)
    result = _run(_remote(), env)
    assert result.returncode != 0
    assert _events(recorder) == []


@pytest.mark.parametrize("target", ["controller", "library"])
def test_restore_rejects_symlink_controller_directory_or_library(fixture, target):
    env, recorder, driver, library = fixture
    path = driver.parent if target == "controller" else library
    retained = path.with_name(path.name + "-fixture-original")
    path.rename(retained)
    path.symlink_to(retained, target_is_directory=target == "controller")
    result = _run(_remote(), env)
    assert result.returncode != 0
    assert _events(recorder) == []


@pytest.mark.parametrize("target", ["controller", "driver", "library"])
def test_real_validator_rejects_wrong_owner_without_changing_host_ownership(fixture, target):
    env, recorder, driver, library = fixture
    path = {"controller": driver.parent, "driver": driver, "library": library}[target]
    validation = _function("run_dev_artifact_driver").split("<<'ARTIFACT_PY'\n", 1)[1].split("\nARTIFACT_PY", 1)[0]
    validation = validation.replace(CONTROLLER_ROOT, env["SYNTHETIC_CONTROLLER_ROOT"])
    # Execute the actual verifier with one synthetic lstat owner response. No
    # chown, sudo or mutation of a real user's files is needed by this test.
    shim = f'''import os, pathlib
from unittest.mock import patch
original_lstat = pathlib.Path.lstat
def synthetic_lstat(path, *args, **kwargs):
    result = original_lstat(path, *args, **kwargs)
    if str(path) == {str(path)!r}:
        fields = list(result)
        fields[4] = os.geteuid() + 1
        return os.stat_result(fields)
    return result
with patch.object(pathlib.Path, "lstat", synthetic_lstat):
    exec(compile({validation!r}, "actual-artifact-verifier", "exec"))
'''
    result = subprocess.run([sys.executable, "-c", shim, str(driver),
                             env["PANTHEON_DEV_ARTIFACT_DRIVER_SHA256"],
                             env["PANTHEON_DEV_ARTIFACT_LIBRARY_SHA256"],
                             env["PANTHEON_DEV_ARTIFACT_CONTROLLER_SHA"]],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "owner-private" in result.stderr
    assert _events(recorder) == []


def test_restore_tampered_manifest_propagates_driver_rejection_without_fallback(fixture):
    env, recorder, *_ = fixture
    Path(env["PANTHEON_DEV_ARTIFACT_MANIFEST_PATH"]).write_text('{"tampered":true}')
    assert _run(_remote(), env).returncode == 75
    assert [event["command"] for event in _events(recorder)] == ["driver"]


@pytest.mark.parametrize("reject", [False, True])
def test_internal_rollback_uses_same_driver_even_same_source_and_keeps_rollout_failed(fixture, reject):
    env, recorder, *_ = fixture
    env.update({"PANTHEON_DEPLOY_SHA": PRIOR, "PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA": PRIOR,
                "PANTHEON_DEPLOY_COMPONENT": "root", "DEV_CANDIDATE_RECEIPT_ACKED": "true"})
    if reject:
        env["DRIVER_REJECT"] = "fixture image mismatch"
    payload = "set -euo pipefail\ninfo() { :; }\nerror() { exit 1; }\n"
    payload += "dump_dev_root_failure_diagnostics() { :; }\n"
    payload += "\n".join(_function(name) for name in
                         ("with_dev_bff_runtime_env", "run_dev_artifact_driver", "rollback_dev_bff_on_failure"))
    payload += '\nrollback_dev_bff_on_failure fixture_gate\n'
    result = _run(payload, env)
    assert result.returncode == 1
    assert [event["command"] for event in _events(recorder)] == ["driver"]
    assert _events(recorder)[0]["operation"] == "restore"


@pytest.mark.parametrize("same_lease", [False, True])
def test_restore_lease_accepts_candidate_guard_or_fresh_prior_guard(fixture, tmp_path, same_lease):
    env, *_ = fixture
    expected = SHA if same_lease else PRIOR
    lease = tmp_path / "lease.json"
    lease.write_text(json.dumps({"schemaVersion": 1, "repository": "ajoe734/execute-plans",
        "branch": "environment-coordination", "path": ".pantheon/environment-leases/pantheon-dev-environment.json",
        "resource": "pantheon-dev-environment", "mode": "deployment",
        "leaseId": env["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"], "expectedBackendSha": expected}))
    env.update({"DEPLOY_ENV": "dev", "COMPONENT": "bff", "DEPLOY_SHA": PRIOR,
                "ALLOW_DIRTY": "false", "ARTIFACT_RESTORE": "true",
                "PANTHEON_DEV_LEASE_EXPECTED_BACKEND_SHA": expected,
                "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(lease)})
    payload = 'set -euo pipefail\ninfo() { :; }\nerror() { echo "$*" >&2; exit 1; }\n'
    payload += _function("validate_artifact_restore_request") + _function("verify_dev_environment_lease_contract")
    payload += '\nvalidate_artifact_restore_request\nverify_dev_environment_lease_contract\n'
    result = _run(payload, env, guard=False)
    assert result.returncode == 0, result.stderr
    if same_lease:
        env["PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA"] = "9" * 40
        assert _run(payload, env, guard=False).returncode != 0


def test_normal_bff_recreate_reuses_runtime_environment_without_changing_service_set():
    branch = _remote().split("\n  bff)\n", 1)[1].split("\n  exec)\n", 1)[0]
    expected = ('with_dev_bff_runtime_env "${PANTHEON_DEPLOY_SHA}" '
                '"${PANTHEON_DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED}" \\\n'
                '      run_dev_candidate_compose up -d --force-recreate --no-deps '
                'operator-bff agora-interaction-worker loop-run-projector-scheduler')
    assert expected in branch
    assert "prepare_dev_paper_principals" in branch
    assert "start_dev_paper_principal_issuer" in branch
    assert "cleanup_stale_compose_replacement_containers" in branch
    assert "ensure_dev_caddy_ingress" in branch


@pytest.mark.parametrize("component,expected", [("auto", 1), ("root", 1), ("bff", 0)])
def test_actual_cli_restore_flag_only_admits_explicit_bff(fixture, component, expected):
    env, recorder, *_ = fixture
    result = subprocess.run([str(SCRIPT), "--environment", "dev", "--component", component,
                             "--sha", PRIOR, "--artifact-restore", "--dry-run"],
                            env=env, capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
    assert _events(recorder) == []
    if expected == 0:
        assert "artifact_restore=true" in result.stdout


def test_actual_ssh_command_forwards_explicit_seal_metadata_but_not_runner_guard_fd(fixture, tmp_path):
    from test_deploy_nonprod_vm import _setup_stubbed_dev_environment

    fixture_env, *_ = fixture
    artifact_env = {key: value for key, value in fixture_env.items() if key.startswith("PANTHEON_DEV_ARTIFACT_")}
    artifact_env["PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD"] = "9876"  # must never cross SSH as authority
    env, args_file, stdin_file = _setup_stubbed_dev_environment(tmp_path, sha=PRIOR, extra_env=artifact_env)
    env["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"] = fixture_env["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"]
    state_file = Path(env["PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE"])
    state = json.loads(state_file.read_text())
    state["leaseId"] = env["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"]
    state_file.write_text(json.dumps(state))
    # Record only the three startup frames, then close. This fake transport
    # does NOT execute the VM payload or manufacture a successful readback.
    (tmp_path / "bin/ssh").write_text(
        "#!/usr/bin/python3\nimport pathlib,sys\n"
        f"pathlib.Path({str(args_file)!r}).write_text('\\n'.join(sys.argv[1:]))\n"
        f"pathlib.Path({str(stdin_file)!r}).write_bytes(b''.join(sys.stdin.buffer.readline() for _ in range(3)))\n")
    result = subprocess.run([str(SCRIPT), "--environment", "dev", "--component", "bff",
                             "--sha", PRIOR, "--artifact-restore", "--artifact-readback-out", str(tmp_path / "readback.json"),
                             "--deadline-seconds", "10"],
                            env={**env, "PANTHEON_DEV_ARTIFACT_EVIDENCE_PROVENANCE": "runner-local"},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode != 0  # no trusted typed readback was supplied
    assert args_file.exists(), (result.returncode, result.stdout, result.stderr)
    command = args_file.read_text().splitlines()[-1]
    assert "PANTHEON_DEV_ARTIFACT_RESTORE=true" not in command
    frames = stdin_file.read_bytes().splitlines()
    payload = base64.b64decode(json.loads(frames[1])["script"]).decode()
    exports = payload.splitlines()[2]
    assert "PANTHEON_DEV_ARTIFACT_RESTORE=true" in exports
    for name in ("MANIFEST_PATH", "MANIFEST_SHA256", "DRIVER_PATH", "DRIVER_SHA256", "LIBRARY_SHA256",
                 "COMPOSE_FILE", "CANDIDATE_ID", "RUN_ID", "ATTEMPT", "CONTROLLER_SHA",
                 "CANDIDATE_BACKEND_SHA", "CANDIDATE_FRONTEND_SHA", "PREVIOUS_BACKEND_SHA", "PREVIOUS_FRONTEND_SHA"):
        assert f"PANTHEON_DEV_ARTIFACT_{name}={artifact_env['PANTHEON_DEV_ARTIFACT_' + name]}" in exports
    assert "PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD=" not in exports
    assert "run_dev_artifact_driver" in payload
    assert not (tmp_path / "readback.json").exists()


@pytest.mark.parametrize("kind", ["matching", "wrong", "duplicate", "ack-eof", "guard-eof", "missing"])
def test_real_ack_waiter_requires_exact_receipt_and_live_private_pipes(fixture, kind):
    env, recorder, *_ = fixture
    digest = env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256"]
    ack = (digest + "\n").encode()
    if kind == "wrong": ack = b"0" * 64 + b"\n"
    if kind == "duplicate": ack += ack
    if kind == "missing": ack = None
    payload = "set -euo pipefail\n" + _function("await_dev_candidate_receipt_ack") + "\nawait_dev_candidate_receipt_ack\nprintf 'admitted-after-ack\\n'\n"
    result = _run(payload, env, ack=ack, ack_eof=kind == "ack-eof", guard_eof=kind == "guard-eof")
    assert (result.returncode == 0) is (kind == "matching"), result.stderr
    assert ("admitted-after-ack" in result.stdout) is (kind == "matching")
    assert _events(recorder) == []


@pytest.mark.parametrize("accepted", [False, True])
def test_candidate_producer_cannot_start_mutation_before_matching_runner_ack(fixture, accepted):
    env, recorder, *_ = fixture
    functions = ("with_dev_bff_runtime_env", "run_dev_artifact_driver", "validate_dev_candidate_override",
                 "await_dev_candidate_receipt_ack", "seal_dev_candidate_images")
    payload = "set -euo pipefail\ninfo() { echo \"$*\"; }\nerror() { exit 75; }\n"
    payload += "\n".join(_function(name) for name in functions)
    payload += "\nseal_dev_candidate_images\nprintf 'mutation-admitted=%s\\n' \"${DEV_CANDIDATE_RECEIPT_ACKED}\"\n"
    value = env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256"] if accepted else "0" * 64
    result = _run(payload, {**env, "PANTHEON_DEPLOY_SHA": SHA}, ack=(value + "\n").encode())
    assert (result.returncode == 0) is accepted, result.stderr
    assert ("mutation-admitted=true" in result.stdout) is accepted
    assert result.stdout.count("PANTHEON_ARTIFACT_CANDIDATE_SEAL_V1 ") == 1
    events = _events(recorder)
    assert [event["operation"] for event in events] == ["seal-candidate"]
    if accepted:
        assert events[0]["args"]["--compose-file"] == env["PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE"]
        assert events[0]["args"]["--compose-file"] != env["PANTHEON_DEV_ARTIFACT_COMPOSE_FILE"]
        assert events[0]["args"]["--compose-file"] != str(Path(env["PANTHEON_REMOTE_DIR"]) / "docker-compose.yml")


@pytest.mark.parametrize("corrupt_mode", ["unset", "nonexistent", "symlink"])
def test_seal_candidate_rejects_missing_or_malformed_candidate_compose(fixture, corrupt_mode, tmp_path):
    env, recorder, *_ = fixture
    functions = ("with_dev_bff_runtime_env", "run_dev_artifact_driver", "validate_dev_candidate_override",
                 "await_dev_candidate_receipt_ack", "seal_dev_candidate_images")
    payload = "set -euo pipefail\ninfo() { echo \"$*\"; }\nerror() { exit 75; }\n"
    payload += "\n".join(_function(name) for name in functions)
    payload += "\nseal_dev_candidate_images\n"

    test_env = {**env, "PANTHEON_DEPLOY_SHA": SHA}
    if corrupt_mode == "unset":
        test_env.pop("PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE", None)
    elif corrupt_mode == "nonexistent":
        test_env["PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE"] = str(tmp_path / "nonexistent-compose.yml")
    elif corrupt_mode == "symlink":
        sym = tmp_path / "symlink-compose.yml"
        sym.symlink_to(env["PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE"])
        test_env["PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE"] = str(sym)

    ack = (env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256"] + "\n").encode()
    result = _run(payload, test_env, ack=ack)
    assert result.returncode != 0
    # Must fail without executing driver or falling back to ambient or baseline
    assert _events(recorder) == []


def test_real_driver_seal_candidate_validates_candidate_git_source_and_rejects_ambient_or_tampered(case):
    from scripts.test_dev_release_artifact_driver import execute, seal
    from scripts import dev_release_artifact_driver as d

    seal(case, admit_candidate=False)

    # 1. Real driver rejects ambient/baseline Compose whose git HEAD is not candidate SHA
    case.args.compose_file = case.baseline_compose
    with pytest.raises(d.a.ArtifactError, match="Compose is not the exact baseline source blob"):
        execute(case, "seal-candidate")

    # 2. Real driver rejects tampered candidate Compose bytes
    original_bytes = case.docker.candidate_compose.read_bytes()
    try:
        case.docker.candidate_compose.write_text("services: {tampered: true}\n")
        case.args.compose_file = case.docker.candidate_compose
        with pytest.raises(d.a.ArtifactError, match="Compose is not the exact baseline source blob"):
            execute(case, "seal-candidate")
    finally:
        case.docker.candidate_compose.write_bytes(original_bytes)

    # 3. Exact candidate Compose succeeds with real driver
    case.args.compose_file = case.docker.candidate_compose
    result = execute(case, "seal-candidate")
    assert "candidate_image_manifest_path" in result


@pytest.mark.parametrize("profiles,services", [
    ("root,dev-paper-principals", []),
    ("root", []),
    ("", ["operator-bff", "agora-interaction-worker", "loop-run-projector-scheduler"]),
    (None, ["operator-bff"]),
])
def test_runtime_wrapper_preserves_caller_profiles_through_candidate_compose(fixture, profiles, services):
    env, recorder, *_ = fixture
    seal = json.loads(env["FIXTURE_SEAL_OUTPUT"])
    env.update({
        "DEV_CANDIDATE_RECEIPT_ACKED": "true",
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_OVERRIDE_PATH": seal["candidate_image_override_path"],
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_OVERRIDE_SHA256": seal["candidate_image_override_sha256"],
    })
    env.pop("COMPOSE_PROFILES", None)
    if profiles is not None:
        env["COMPOSE_PROFILES"] = profiles
    # Real wrapper and sealed-candidate validation; only Docker is a recorder.
    payload = "set -euo pipefail\ninfo() { :; }\n"
    payload += '\ndocker() { python3 -c \'import json,os,sys; print(json.dumps({"profiles":os.environ.get("COMPOSE_PROFILES"),"args":sys.argv[1:],"live":os.environ.get("PANTHEON_LIVE_BROKER_ENABLED")}))\' "$@"; }\n'
    payload += "\n".join(_function(name) for name in (
        "with_dev_bff_runtime_env", "validate_dev_candidate_override", "run_dev_candidate_compose",
    ))
    payload += f'\nwith_dev_bff_runtime_env {SHA} false run_dev_candidate_compose up -d'
    if services:
        payload += ' --no-deps ' + ' '.join(services)
    result = _run(payload + "\n", env)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["profiles"] == (profiles or "")
    assert observed["live"] == "false"
    assert observed["args"] == ["compose", "-p", "pantheon", "-f", "docker-compose.yml",
        "-f", seal["candidate_image_override_path"], "up", "-d", *(["--no-deps", *services] if services else [])]
    assert _events(recorder) == []


def test_three_candidate_start_sites_use_the_admitted_image_override():
    payload = _remote()
    case = payload[payload.index('case "${PANTHEON_DEPLOY_COMPONENT}" in'):]
    assert "run_dev_candidate_compose up -d \\\n" in case
    assert "run_dev_candidate_compose up -d --force-recreate --no-deps loop-run-projector-scheduler" in case
    assert "run_dev_candidate_compose up -d --force-recreate --no-deps operator-bff agora-interaction-worker loop-run-projector-scheduler" in case
    compose = _function("run_dev_candidate_compose")
    assert '"${DEV_CANDIDATE_RECEIPT_ACKED:-false}" == true' in compose
    assert 'validate_dev_candidate_override' in compose
    assert '-f "${PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_OVERRIDE_PATH}"' in compose
    for branch in (case[case.index("  root)"):case.index("  bff)")], case[case.index("  bff)"):case.index("  control)")]):
        assert branch.index("seal_dev_candidate_images") < branch.index("start_dev_paper_principal_issuer")
        assert branch.index("seal_dev_candidate_images") < branch.index("run_dev_candidate_compose up")


def test_final_artifact_driver_arguments_and_explicit_compose(fixture, tmp_path, monkeypatch):
    env, recorder, driver, library = fixture
    candidate_compose = tmp_path / "explicit-candidate" / "docker-compose.yml"
    candidate_compose.parent.mkdir(parents=True, exist_ok=True)
    candidate_compose_content = "services:\n  operator-bff:\n    image: pantheon-operator-bff:candidate\n"
    candidate_compose.write_text(candidate_compose_content)
    candidate_compose_sha = hashlib.sha256(candidate_compose_content.encode()).hexdigest()

    # Update candidate-images.json with candidate_compose_sha
    candidate_path = Path(env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_PATH"])
    candidate_data = json.loads(candidate_path.read_text())
    candidate_data["candidate_compose_sha256"] = candidate_compose_sha
    encoded_bytes = (json.dumps(candidate_data, sort_keys=True, separators=(",", ":")) + "\n").encode()
    candidate_path.write_bytes(encoded_bytes)
    env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256"] = hashlib.sha256(encoded_bytes).hexdigest()

    fe_manifest_sha = "2" * 64
    fe_dist_sha = "3" * 64
    fe_target = "/var/www/pantheon-dev-fe-releases/candidate-fe"

    env.update({
        "PANTHEON_DEPLOY_SHA": SHA,
        "PANTHEON_DEV_ARTIFACT_TARGET": "final",
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE": str(candidate_compose),
        "PANTHEON_DEV_ARTIFACT_FE_MANIFEST_SHA256": fe_manifest_sha,
        "PANTHEON_DEV_ARTIFACT_FE_DIST_SHA256": fe_dist_sha,
        "PANTHEON_DEV_ARTIFACT_FE_TARGET": fe_target,
    })

    # Put a hostile docker-compose.yml in current working directory
    hostile_cwd = tmp_path / "hostile-ambient-pwd"
    hostile_cwd.mkdir()
    hostile_compose = hostile_cwd / "docker-compose.yml"
    hostile_compose.write_text("services: {}\n# hostile ambient compose\n")
    monkeypatch.chdir(hostile_cwd)

    result = _run(_remote(), env)
    assert result.returncode == 0, result.stderr
    events = _events(recorder)
    assert len(events) == 1
    event = events[0]
    assert event["command"] == "driver"
    assert event["operation"] == "restore-final"
    args = event["args"]
    assert args["--target"] == "final"
    assert args["--compose-file"] == str(candidate_compose)
    assert args["--candidate-image-manifest"] == env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_PATH"]
    assert args["--fe-manifest-sha256"] == fe_manifest_sha
    assert args["--fe-dist-sha256"] == fe_dist_sha
    assert args["--fe-target"] == fe_target


def test_final_verify_driver_invocation(fixture, tmp_path):
    env, recorder, driver, library = fixture
    candidate_compose = tmp_path / "candidate-compose" / "docker-compose.yml"
    candidate_compose.parent.mkdir(parents=True, exist_ok=True)
    candidate_compose_content = "services:\n  operator-bff:\n    image: pantheon-operator-bff:verify\n"
    candidate_compose.write_text(candidate_compose_content)
    candidate_compose_sha = hashlib.sha256(candidate_compose_content.encode()).hexdigest()

    candidate_path = Path(env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_PATH"])
    candidate_data = json.loads(candidate_path.read_text())
    candidate_data["candidate_compose_sha256"] = candidate_compose_sha
    encoded_bytes = (json.dumps(candidate_data, sort_keys=True, separators=(",", ":")) + "\n").encode()
    candidate_path.write_bytes(encoded_bytes)
    env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256"] = hashlib.sha256(encoded_bytes).hexdigest()

    fe_manifest_sha = "4" * 64
    fe_dist_sha = "5" * 64
    fe_target = "/var/www/pantheon-dev-fe-releases/candidate-verify"

    env.update({
        "PANTHEON_DEPLOY_SHA": SHA,
        "PANTHEON_DEV_ARTIFACT_RESTORE": "false",
        "PANTHEON_DEV_ARTIFACT_VERIFY": "true",
        "PANTHEON_DEV_ARTIFACT_TARGET": "final",
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE": str(candidate_compose),
        "PANTHEON_DEV_ARTIFACT_FE_MANIFEST_SHA256": fe_manifest_sha,
        "PANTHEON_DEV_ARTIFACT_FE_DIST_SHA256": fe_dist_sha,
        "PANTHEON_DEV_ARTIFACT_FE_TARGET": fe_target,
    })

    result = _run(_remote(), env)
    assert result.returncode == 0, result.stderr
    events = _events(recorder)
    assert len(events) == 1
    event = events[0]
    assert event["command"] == "driver"
    assert event["operation"] == "verify"
    args = event["args"]
    assert args["--target"] == "final"
    assert args["--compose-file"] == str(candidate_compose)
    assert args["--candidate-image-manifest"] == env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_PATH"]
    assert args["--fe-manifest-sha256"] == fe_manifest_sha
    assert args["--fe-dist-sha256"] == fe_dist_sha
    assert args["--fe-target"] == fe_target


@pytest.mark.parametrize("flag,sha_used,missing_var,expected", [
    ("--artifact-restore-final", SHA, None, 0),
    ("--artifact-restore-final", PRIOR, None, 1),
    ("--artifact-target invalid", SHA, None, 1),
    ("--artifact-restore-final", SHA, "PANTHEON_DEV_ARTIFACT_FE_MANIFEST_SHA256", 1),
    ("--artifact-restore-final", SHA, "PANTHEON_DEV_ARTIFACT_FE_DIST_SHA256", 1),
])
def test_actual_cli_restore_final_options(fixture, flag, sha_used, missing_var, expected):
    env, recorder, *_ = fixture
    env.update({
        "PANTHEON_DEV_ARTIFACT_FE_MANIFEST_SHA256": "2" * 64,
        "PANTHEON_DEV_ARTIFACT_FE_DIST_SHA256": "3" * 64,
        "PANTHEON_DEV_ARTIFACT_FE_TARGET": "/var/www/pantheon-dev-fe-releases/candidate",
    })
    if missing_var:
        env.pop(missing_var, None)
    cmd = [str(SCRIPT), "--environment", "dev", "--component", "bff",
           "--sha", sha_used, *flag.split(), "--dry-run"]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
    if expected == 0:
        assert "artifact_restore=true" in result.stdout


def test_verify_without_fe_digests_succeeds(fixture, tmp_path):
    env, recorder, driver, library = fixture
    candidate_compose = tmp_path / "candidate-compose" / "docker-compose.yml"
    candidate_compose.parent.mkdir(parents=True, exist_ok=True)
    candidate_compose_content = "services:\n  operator-bff:\n    image: pantheon-operator-bff:verify\n"
    candidate_compose.write_text(candidate_compose_content)
    candidate_compose_sha = hashlib.sha256(candidate_compose_content.encode()).hexdigest()

    candidate_path = Path(env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_PATH"])
    candidate_data = json.loads(candidate_path.read_text())
    candidate_data["candidate_compose_sha256"] = candidate_compose_sha
    encoded_bytes = (json.dumps(candidate_data, sort_keys=True, separators=(",", ":")) + "\n").encode()
    candidate_path.write_bytes(encoded_bytes)
    env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256"] = hashlib.sha256(encoded_bytes).hexdigest()

    env.update({
        "PANTHEON_DEPLOY_SHA": SHA,
        "PANTHEON_DEV_ARTIFACT_RESTORE": "false",
        "PANTHEON_DEV_ARTIFACT_VERIFY": "true",
        "PANTHEON_DEV_ARTIFACT_TARGET": "final",
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE": str(candidate_compose),
    })
    env.pop("PANTHEON_DEV_ARTIFACT_FE_MANIFEST_SHA256", None)
    env.pop("PANTHEON_DEV_ARTIFACT_FE_DIST_SHA256", None)
    env.pop("PANTHEON_DEV_ARTIFACT_FE_TARGET", None)

    result = _run(_remote(), env)
    assert result.returncode == 0, result.stderr
    events = _events(recorder)
    assert len(events) == 1
    event = events[0]
    assert event["command"] == "driver"
    assert event["operation"] == "verify"
    assert event["args"]["--target"] == "final"
    assert event["args"]["--compose-file"] == str(candidate_compose)
    assert "--fe-manifest-sha256" not in event["args"]


def test_resolve_candidate_compose_mismatch_fails_closed(fixture, tmp_path):
    env, recorder, driver, library = fixture
    candidate_compose = tmp_path / "candidate-compose" / "docker-compose.yml"
    candidate_compose.parent.mkdir(parents=True, exist_ok=True)
    candidate_compose.write_text("services:\n  operator-bff:\n    image: pantheon-operator-bff:verify\n")

    candidate_path = Path(env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_PATH"])
    candidate_data = json.loads(candidate_path.read_text())
    candidate_data["candidate_compose_sha256"] = "9" * 64
    encoded_bytes = (json.dumps(candidate_data, sort_keys=True, separators=(",", ":")) + "\n").encode()
    candidate_path.write_bytes(encoded_bytes)
    env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256"] = hashlib.sha256(encoded_bytes).hexdigest()

    env.update({
        "PANTHEON_DEPLOY_SHA": SHA,
        "PANTHEON_DEV_ARTIFACT_RESTORE": "false",
        "PANTHEON_DEV_ARTIFACT_VERIFY": "true",
        "PANTHEON_DEV_ARTIFACT_TARGET": "final",
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE": str(candidate_compose),
    })

    result = _run(_remote(), env)
    assert result.returncode != 0
    assert "candidate Compose digest mismatch" in result.stderr


def test_fe_owner_restore_sealed_invocation(fixture, tmp_path):
    env, recorder, driver, library = fixture
    fe_script = tmp_path / "deploy-dev-vm.sh"
    fe_script.write_text(
        '#!/usr/bin/env bash\n'
        'set -euo pipefail\n'
        'AUTHORIZED_LEASE_OWNERS_REGEX="^(pantheon-release-controller|pantheon-dev-deploy|parent-controller|pantheon-proof-watchdog|pantheon-fe-deploy)$"\n'
        'owner="${PANTHEON_DEPLOY_LEASE_OWNER:-}"\n'
        'if [[ ! "${owner}" =~ ${AUTHORIZED_LEASE_OWNERS_REGEX} ]]; then\n'
        '    echo "Deployment rejected: unauthorized lease owner \'${owner}\'." >&2\n'
        '    exit 2\n'
        'fi\n'
        'if [[ "${PANTHEON_DEPLOY_LEASE_DELEGATED:-}" != "true" ]]; then\n'
        '    echo "Deployment rejected: non-delegated lease." >&2\n'
        '    exit 2\n'
        'fi\n'
        'export NODE_VERSION="$(node -e \'const v = process.version; if (!v.startsWith("v22.")) { process.exit(1); } console.log(v);\')"\n'
        'python3 - <<\'PY\'\n'
        'import json, os, sys\n'
        'from_doc = json.loads(os.environ.get("PANTHEON_DEPLOY_SEALED_FROM_JSON", "{}"))\n'
        'to_doc = json.loads(os.environ.get("PANTHEON_DEPLOY_SEALED_TO_JSON", "{}"))\n'
        'for doc, name in ((from_doc, "from"), (to_doc, "to")):\n'
        '    for field in ("target", "dist_sha256", "manifest_sha256", "frontend_sha", "backend_sha"):\n'
        '        if field not in doc:\n'
        '            sys.exit(f"missing field {field} in {name} seal")\n'
        'with open(os.environ["RECORDER"], "a") as f:\n'
        '    f.write(json.dumps({\n'
        '        "command": "fe_deploy",\n'
        '        "action": os.environ.get("PANTHEON_DEPLOY_ACTION"),\n'
        '        "profile": os.environ.get("PANTHEON_DEPLOY_PROFILE"),\n'
        '        "delegated": os.environ.get("PANTHEON_DEPLOY_LEASE_DELEGATED"),\n'
        '        "owner": os.environ.get("PANTHEON_DEPLOY_LEASE_OWNER"),\n'
        '        "epoch": os.environ.get("PANTHEON_DEPLOY_LEASE_EPOCH"),\n'
        '        "run_id": os.environ.get("PANTHEON_DEPLOY_LEASE_RUN_ID"),\n'
        '        "event_name": os.environ.get("GITHUB_EVENT_NAME"),\n'
        '        "node_version": os.environ.get("NODE_VERSION"),\n'
        '        "from": from_doc,\n'
        '        "to": to_doc,\n'
        '    }) + "\\n")\n'
        'PY\n'
    )
    fe_script.chmod(0o755)

    from_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/candidate",
        "dist_sha256": "4" * 64,
        "manifest_sha256": "3" * 64,
        "frontend_sha": "e" * 40,
        "backend_sha": SHA,
    }
    to_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/baseline",
        "dist_sha256": "5" * 64,
        "manifest_sha256": "6" * 64,
        "frontend_sha": "f" * 40,
        "backend_sha": PRIOR,
    }

    env.update({
        "PANTHEON_DEV_FE_DEPLOY_SCRIPT": str(fe_script),
        "PANTHEON_DEV_ARTIFACT_SEALED_FROM_JSON": json.dumps(from_seal),
        "PANTHEON_DEV_ARTIFACT_SEALED_TO_JSON": json.dumps(to_seal),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": "11111111-1111-4111-8111-111111111111",
        "PANTHEON_DEPLOY_LEASE_OWNER": "pantheon-dev-deploy",
    })

    result = _run(_remote(), env)
    assert result.returncode == 0, result.stderr
    events = _events(recorder)
    assert len(events) == 2
    assert events[0]["command"] == "fe_deploy"
    assert events[0]["action"] == "restore-sealed"
    assert events[0]["owner"] == "pantheon-dev-deploy"
    assert events[0]["event_name"] == ""
    assert events[0]["node_version"].startswith("v22.")
    assert events[0]["from"] == from_seal
    assert events[0]["to"] == to_seal
    assert events[1]["command"] == "driver"
    assert events[1]["operation"] == "restore"

    env_dispatch = dict(env, GITHUB_EVENT_NAME="workflow_dispatch")
    Path(env["RECORDER"]).write_text("")
    res_dispatch = _run(_remote(), env_dispatch)
    assert res_dispatch.returncode == 0, res_dispatch.stderr
    ev_dispatch = _events(recorder)
    assert len(ev_dispatch) == 2
    assert ev_dispatch[0]["event_name"] == "workflow_dispatch"

    env_pantheon_event = dict(env, GITHUB_EVENT_NAME="", PANTHEON_DEV_EVENT_NAME="workflow_dispatch")
    Path(env["RECORDER"]).write_text("")
    res_pantheon_event = _run(_remote(), env_pantheon_event)
    assert res_pantheon_event.returncode == 0, res_pantheon_event.stderr
    ev_pantheon_event = _events(recorder)
    assert len(ev_pantheon_event) == 2
    assert ev_pantheon_event[0]["event_name"] == "workflow_dispatch"

    fe_root = tmp_path / "live-fe"
    baseline_dir = tmp_path / "baseline-release"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    fe_root.symlink_to(baseline_dir)
    to_seal_matching = dict(to_seal, target=str(baseline_dir))

    env.update({
        "PANTHEON_DEV_FE_ROOT": str(fe_root),
        "PANTHEON_DEV_ARTIFACT_SEALED_TO_JSON": json.dumps(to_seal_matching),
    })
    Path(env["RECORDER"]).write_text("")
    result = _run(_remote(), env)
    assert result.returncode == 0, result.stderr
    events = _events(recorder)
    assert len(events) == 1
    assert events[0]["command"] == "driver"

    empty_node_env = dict(env,
                          PANTHEON_DEV_FE_NODE_BIN_DIR="",
                          PANTHEON_DEV_ARTIFACT_SEALED_TO_JSON=json.dumps(to_seal))
    res_empty = _run(_remote(), empty_node_env)
    assert res_empty.returncode != 0
    assert "FE owner Node runtime missing or unsafe" in res_empty.stderr

    bad_node_env = dict(env,
                        PANTHEON_DEV_FE_NODE_BIN_DIR=str(tmp_path / "missing-node-bin"),
                        PANTHEON_DEV_ARTIFACT_SEALED_TO_JSON=json.dumps(to_seal))
    res_bad = _run(_remote(), bad_node_env)
    assert res_bad.returncode != 0
    assert "FE owner Node runtime missing or unsafe" in res_bad.stderr

    wrong_node_dir = tmp_path / "wrong-node" / "bin"
    wrong_node_dir.mkdir(parents=True)
    wrong_node = wrong_node_dir / "node"
    wrong_node.write_text("#!/usr/bin/env bash\necho 'v20.18.0'\n")
    wrong_node.chmod(0o755)
    wrong_node_env = dict(env,
                          PANTHEON_DEV_FE_NODE_BIN_DIR=str(wrong_node_dir),
                          PANTHEON_DEV_ARTIFACT_SEALED_TO_JSON=json.dumps(to_seal))
    res_wrong = _run(_remote(), wrong_node_env)
    assert res_wrong.returncode != 0
    assert "returned invalid version 'v20.18.0' (expected v22.*)" in res_wrong.stderr


def test_fe_owner_restore_sealed_rejects_unauthorized_owner(fixture, tmp_path):
    env, recorder, driver, library = fixture
    fe_script = tmp_path / "deploy-dev-vm.sh"
    fe_script.write_text(
        '#!/usr/bin/env bash\n'
        'set -euo pipefail\n'
        'AUTHORIZED_LEASE_OWNERS_REGEX="^(pantheon-release-controller|pantheon-dev-deploy|parent-controller|pantheon-proof-watchdog|pantheon-fe-deploy)$"\n'
        'owner="${PANTHEON_DEPLOY_LEASE_OWNER:-}"\n'
        'if [[ ! "${owner}" =~ ${AUTHORIZED_LEASE_OWNERS_REGEX} ]]; then\n'
        '    echo "Deployment rejected: unauthorized lease owner \'${owner}\'." >&2\n'
        '    exit 2\n'
        'fi\n'
        'exit 0\n'
    )
    fe_script.chmod(0o755)

    from_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/candidate",
        "dist_sha256": "4" * 64,
        "manifest_sha256": "3" * 64,
        "frontend_sha": "e" * 40,
        "backend_sha": SHA,
    }
    to_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/baseline",
        "dist_sha256": "5" * 64,
        "manifest_sha256": "6" * 64,
        "frontend_sha": "f" * 40,
        "backend_sha": PRIOR,
    }

    env.update({
        "PANTHEON_DEV_FE_DEPLOY_SCRIPT": str(fe_script),
        "PANTHEON_DEV_ARTIFACT_SEALED_FROM_JSON": json.dumps(from_seal),
        "PANTHEON_DEV_ARTIFACT_SEALED_TO_JSON": json.dumps(to_seal),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": "11111111-1111-4111-8111-111111111111",
        "PANTHEON_DEPLOY_LEASE_OWNER": "11111111-1111-4111-8111-111111111111",
    })

    result = _run(_remote(), env)
    assert result.returncode != 0
    assert "unauthorized lease owner" in result.stderr


def test_fe_owner_restore_sealed_rejects_non_delegated_lease(fixture, tmp_path):
    env, recorder, driver, library = fixture
    fe_script = tmp_path / "deploy-dev-vm.sh"
    fe_script.write_text(
        '#!/usr/bin/env bash\n'
        'set -euo pipefail\n'
        'if [[ "${PANTHEON_DEPLOY_LEASE_DELEGATED:-}" != "true" ]]; then\n'
        '    echo "Deployment rejected: non-delegated lease." >&2\n'
        '    exit 2\n'
        'fi\n'
        'exit 0\n'
    )
    fe_script.chmod(0o755)

    from_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/candidate",
        "dist_sha256": "4" * 64,
        "manifest_sha256": "3" * 64,
        "frontend_sha": "e" * 40,
        "backend_sha": SHA,
    }
    to_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/baseline",
        "dist_sha256": "5" * 64,
        "manifest_sha256": "6" * 64,
        "frontend_sha": "f" * 40,
        "backend_sha": PRIOR,
    }

    env.update({
        "PANTHEON_DEV_FE_DEPLOY_SCRIPT": str(fe_script),
        "PANTHEON_DEV_ARTIFACT_SEALED_FROM_JSON": json.dumps(from_seal),
        "PANTHEON_DEV_ARTIFACT_SEALED_TO_JSON": json.dumps(to_seal),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": "11111111-1111-4111-8111-111111111111",
        "PANTHEON_DEPLOY_LEASE_OWNER": "pantheon-dev-deploy",
        "PANTHEON_DEPLOY_LEASE_DELEGATED": "false",
    })

    result = _run(_remote(), env)
    assert result.returncode != 0
    assert "non-delegated lease" in result.stderr


def test_artifact_restore_retains_runtime_preflight_while_verify_bypasses(fixture, tmp_path):
    from test_deploy_nonprod_vm import _setup_stubbed_dev_environment
    fixture_env, *_ = fixture
    env, _, _ = _setup_stubbed_dev_environment(tmp_path, sha=PRIOR, extra_env=fixture_env)
    env.update({
        "TARGET_ENV": "dev",
        "DEV_BFF_AUTH_MODE": "strict",
        "DEV_BFF_AUTH_STUB": "false",
        "DEV_BFF_JWT_SECRET": "",
        "DEV_BFF_OIDC_CLIENT_ID": "",
        "DEV_BFF_OIDC_CLIENT_SECRET": "",
    })
    state_file = Path(env["PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE"])
    state = json.loads(state_file.read_text())
    state["leaseId"] = env["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"]
    state["expectedBackendSha"] = PRIOR
    state_file.write_text(json.dumps(state))

    res_restore = subprocess.run([
        str(SCRIPT),
        "--environment", "dev",
        "--component", "bff",
        "--sha", PRIOR,
        "--artifact-restore",
    ], env=env, capture_output=True, text=True)
    assert res_restore.returncode != 0
    assert "strict auth cutover requested" in res_restore.stderr

    res_verify = subprocess.run([
        str(SCRIPT),
        "--environment", "dev",
        "--component", "bff",
        "--sha", PRIOR,
        "--artifact-verify",
    ], env=env, capture_output=True, text=True)
    assert "strict auth cutover requested" not in res_verify.stderr


# --- AC5 original BE chain -------------------------------------------------
# CLI -> original guarded sender -> isolated SSH -> original bootstrap/remote
# watchdog (real pulse, guard FD, exit) -> deploy payload -> ORIGINAL driver and
# library bytes from the admitted controller commit -> external readback
# validator consuming the driver-generated readback.
#
# Isolated (synthetic) boundaries, stated truthfully:
#   * SSH: a PATH `ssh` runs the requested remote command locally. It only
#     substitutes the fixed /home/... artifact-store constants inside the
#     transported payload, like the existing synthetic-root precedent.
#   * Docker, public HTTP, VM hostname, artifact/FE store paths: the driver
#     runs through a PATH `python3` shim that patches those module constants
#     and swaps GuardedDocker/HTTP for the driver test models (FakeDocker/HTTP).
#   * The runner-side validator runs with the same fixed ARTIFACT_ROOT patch.
#   * The FE deploy command is an explicit mock (env/args/exit propagation and
#     the live-link switch); the real FE controller/CAS is NOT exercised here.
DRIVER_HARNESS = '''
import importlib.util, json, os, pickle, sys
from pathlib import Path
driver_path, state_path, recorder, repo_root = sys.argv[1:5]
rest = sys.argv[5:]
sys.path.insert(0, repo_root)
sys.path.insert(0, str(Path(driver_path).parent))
spec = importlib.util.spec_from_file_location("dev_release_artifact_driver", driver_path)
d = importlib.util.module_from_spec(spec)
sys.modules["dev_release_artifact_driver"] = d
spec.loader.exec_module(d)
sys.modules["scripts.dev_release_artifact_driver"] = d
with open(state_path, "rb") as stream:
    state = pickle.load(stream)
d.ROOT, d.FE_STORE, d.FE_LINK = Path(state["root"]), Path(state["fe_store"]), Path(state["fe_link"])
d.socket.gethostname = lambda: d.VM
docker, http = state["docker"], state["http"]
class GuardedModel:
    def __init__(self, barrier):
        self.barrier = barrier
    def call(self, *args):
        self.barrier.check()
        output = docker.call(*args)
        self.barrier.check()
        return output
    def __getattr__(self, name):
        return getattr(docker, name)
d.GuardedDocker = GuardedModel
d.HTTP = lambda: http
code = d.main(rest)
sys.stdout.flush()
with open(recorder, "a") as out:
    out.write(json.dumps({"command": "driver", "argv": rest, "exit": code,
                          "docker_calls": [list(call) for call in docker.calls],
                          "http_paths": [call[1] for call in http.calls]}) + "\\n")
sys.exit(code)
'''

VALIDATOR_HARNESS = '''
import runpy, sys
from pathlib import Path
import re
script, artifact_root, fe_store = sys.argv[1:4]
sys.path.insert(0, str(Path(script).resolve().parent))
import capture_dev_artifact_baseline as capture
import dev_candidate_receipt as candidate
capture.ARTIFACT_ROOT = candidate.ARTIFACT_ROOT = Path(artifact_root)
_matches = capture.matches
capture.matches = lambda value, pattern: _matches(
    value, pattern.replace("/var/www/pantheon-dev-fe-releases", re.escape(fe_store)))
sys.argv = [script, *sys.argv[4:]]
runpy.run_path(script, run_name="__main__")
'''


def _install_chain_shims(tmp_path: Path, *, artifact_root: Path, fe_store: Path, state: Path, recorder: Path) -> None:
    bin_dir = tmp_path / "bin"
    (tmp_path / "driver_harness.py").write_text(DRIVER_HARNESS)
    (tmp_path / "validator_harness.py").write_text(VALIDATOR_HARNESS)
    shim = bin_dir / "python3"
    shim.write_text(
        '#!/usr/bin/python3\n'
        'import os, sys\n'
        'args = sys.argv[1:]\n'
        'name = os.path.basename(args[0]) if args else ""\n'
        f'py = {sys.executable!r}\n'
        'if name == "dev_release_artifact_driver.py":\n'
        f'    os.execv(py, [py, {str(tmp_path / "driver_harness.py")!r}, args[0], {str(state)!r}, {str(recorder)!r}, {str(ROOT)!r}, *args[1:]])\n'
        'if name == "dev_artifact_compensation_evidence.py":\n'
        f'    os.execv(py, [py, {str(tmp_path / "validator_harness.py")!r}, args[0], {str(artifact_root)!r}, {str(fe_store)!r}, *args[1:]])\n'
        'os.execv("/usr/bin/python3", ["python3", *args])\n'
    )
    shim.chmod(0o755)
    synth_root = artifact_root / "controllers"
    ssh = bin_dir / "ssh"
    ssh.write_text(
        f'#!{sys.executable}\n'
        'import base64, json, os, subprocess, sys, threading\n'
        'remote_command = sys.argv[-1]\n'
        'stdin = sys.stdin.buffer\n'
        'bootstrap = stdin.readline()\n'
        'envelope = json.loads(stdin.readline())\n'
        'script = base64.b64decode(envelope["script"]).decode()\n'
        f'script = script.replace({CONTROLLER_ROOT!r}, {str(synth_root)!r})\n'
        f'script = script.replace({str(Path(CONTROLLER_ROOT).parent)!r}, {str(artifact_root)!r})\n'
        'envelope["script"] = base64.b64encode(script.encode()).decode()\n'
        f'remote_env = {{"PATH": {str(bin_dir)!r} + ":/usr/bin:/bin", "HOME": {str(tmp_path / "remote-home")!r}, "USER": "tester", "LANG": "C.UTF-8"}}\n'
        'proc = subprocess.Popen(["bash", "-c", remote_command], stdin=subprocess.PIPE, env=remote_env)\n'
        'proc.stdin.write(bootstrap + (json.dumps(envelope, separators=(",", ":")) + "\\n").encode())\n'
        'proc.stdin.flush()\n'
        'def pump():\n'
        '    try:\n'
        '        while True:\n'
        '            chunk = stdin.read1(4096)\n'
        '            if not chunk:\n'
        '                break\n'
        '            proc.stdin.write(chunk)\n'
        '            proc.stdin.flush()\n'
        '    except (BrokenPipeError, ValueError):\n'
        '        pass\n'
        '    finally:\n'
        '        try:\n'
        '            proc.stdin.close()\n'
        '        except OSError:\n'
        '            pass\n'
        'threading.Thread(target=pump, daemon=True).start()\n'
        'os._exit(proc.wait())\n'
    )
    ssh.chmod(0o755)


FE_MOCK_SCRIPT = r'''#!/usr/bin/env bash
# Explicit mock of the FE owner command boundary (NOT the real FE controller).
set -euo pipefail
owner="${PANTHEON_DEPLOY_LEASE_OWNER:-}"
[[ "${owner}" =~ ^(pantheon-release-controller|pantheon-dev-deploy|parent-controller|pantheon-proof-watchdog|pantheon-fe-deploy)$ ]] \
  || { echo "Deployment rejected: unauthorized lease owner '${owner}'." >&2; exit 2; }
[[ "${PANTHEON_DEPLOY_LEASE_DELEGATED:-}" == "true" ]] || { echo "Deployment rejected: non-delegated lease." >&2; exit 2; }
export NODE_VERSION="$(node -e 'const v = process.version; if (!v.startsWith("v22.")) { process.exit(1); } console.log(v);')"
python3 - <<'PY'
import json, os
to_doc = json.loads(os.environ["PANTHEON_DEPLOY_SEALED_TO_JSON"])
from_doc = json.loads(os.environ["PANTHEON_DEPLOY_SEALED_FROM_JSON"])
with open("@RECORDER@", "a") as out:
    out.write(json.dumps({"command": "fe_deploy", **{key: os.environ.get(name) for key, name in (
        ("action", "PANTHEON_DEPLOY_ACTION"), ("profile", "PANTHEON_DEPLOY_PROFILE"),
        ("delegated", "PANTHEON_DEPLOY_LEASE_DELEGATED"), ("owner", "PANTHEON_DEPLOY_LEASE_OWNER"),
        ("epoch", "PANTHEON_DEPLOY_LEASE_EPOCH"), ("run_id", "PANTHEON_DEPLOY_LEASE_RUN_ID"),
        ("event_name", "GITHUB_EVENT_NAME"))},
        "node_version": os.environ.get("NODE_VERSION"),
        "from_target": from_doc["target"], "to_target": to_doc["target"]}) + "\n")
PY
target="$(python3 -c 'import json,os; print(json.loads(os.environ["PANTHEON_DEPLOY_SEALED_TO_JSON"])["target"])')"
ln -sfn "${target}" "${PANTHEON_DEV_FE_ROOT}"
'''


def _git(repo: Path, *args: str) -> str:
    import shutil
    return subprocess.run([shutil.which("git"), "-C", str(repo), "-c", "user.name=Fixture",
                           "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def _fe_controller_checkout(path: Path, script: str) -> str:
    (path / "scripts").mkdir(parents=True)
    subprocess.run([__import__("shutil").which("git"), "init", "-q", str(path)], check=True)
    (path / "scripts/deploy-dev-vm.sh").write_text(script)
    (path / "scripts/deploy-dev-vm.sh").chmod(0o755)
    (path / "scripts/atomic-symlink-cas.py").write_text("# fixture helper\n")
    (path / "scripts/atomic-release-manifest.py").write_text("# fixture helper\n")
    _git(path, "add", "scripts")
    _git(path, "commit", "-qm", "fixture FE controller")
    return _git(path, "rev-parse", "HEAD")


def _run_original_chain(case, tmp_path, monkeypatch, *, env_override=None, tamper_fe_seal=False):
    import pickle
    from types import SimpleNamespace
    from test_deploy_nonprod_vm import _setup_stubbed_dev_environment
    from scripts import capture_dev_artifact_baseline as capture
    from scripts import dev_candidate_receipt as candidate_receipt
    from scripts import dev_release_artifact_driver as d
    from scripts.test_dev_release_artifact_driver import admit, deploy_candidate_frontend, execute, seal

    artifact_root = d.ROOT
    monkeypatch.setattr(capture, "ARTIFACT_ROOT", artifact_root)
    monkeypatch.setattr(candidate_receipt, "ARTIFACT_ROOT", artifact_root)
    original_matches = capture.matches
    monkeypatch.setattr(capture, "matches", lambda value, pattern: original_matches(
        value, pattern.replace("/var/www/pantheon-dev-fe-releases", re.escape(str(d.FE_STORE)))))
    controller_sha = _git(ROOT, "rev-parse", "HEAD")
    case.args.controller_sha = controller_sha

    # Original implementation bytes, verified against the admitted commit.
    controllers = artifact_root / "controllers"
    controller_dir = controllers / controller_sha
    controllers.mkdir(mode=0o700)
    controller_dir.mkdir(mode=0o700)
    implementations = {}
    for name in capture.IMPLEMENTATIONS:
        implementations[name] = capture.read_implementation(ROOT, name, controller_sha)
        (controller_dir / name).write_bytes(implementations[name])
        (controller_dir / name).chmod(0o400)
    assert implementations["dev_release_artifact_driver.py"] == (ROOT / "scripts/dev_release_artifact_driver.py").read_bytes()

    # Evidence is produced by the original driver, in-process, before the CLI.
    baseline_result = seal(case, admit_candidate=False)
    receipt = admit(case)
    candidate_release = deploy_candidate_frontend(case)
    for service, row in case.docker.built.items():
        case.docker.containers[service]["image_id"] = row["image_id"]
    case.http.source = case.args.candidate_backend_sha
    case.args.compose_file = case.docker.candidate_compose
    case.args.target = "final"
    verify_res = execute(case, "verify")
    assert verify_res["frontend"]["frontend_sha"] == case.args.candidate_frontend_sha
    # Return to the exact baseline; the CLI must bring back the sealed final.
    case.args.fe_live_link.unlink()
    case.args.fe_live_link.symlink_to(case.release)
    case.http.manifest = case.release / "deployment.json"
    case.args.compose_file = case.baseline_compose
    case.args.target = "baseline"
    execute(case, "restore")
    del case.docker.images[case.docker.built["operator-bff"]["image_id"]]
    case.http.manifest = candidate_release / "deployment.json"
    case.docker.calls.clear()
    case.http.calls.clear()

    identity = {name: getattr(case.args, name) for name in capture.FIELDS}
    run_id, attempt, candidate_id = identity["run_id"], identity["attempt"], identity["candidate_id"]
    runner_temp = tmp_path / "runner_temp"
    retained = runner_temp / f"pantheon-dev-artifacts-{run_id}-{attempt}"
    for directory in (runner_temp, retained, retained / "baseline", retained / "candidate"):
        directory.mkdir(mode=0o700, exist_ok=True)
    baseline_path = retained / "baseline/artifact-baseline.json"
    sealed_baseline, _ = capture.seal_result(capture.encoded(baseline_result), identity)
    baseline_path.write_bytes(sealed_baseline)
    receipt_path = retained / "candidate/candidate-receipt.json"
    receipt_path.write_bytes(capture.encoded(receipt))
    fe_seal_path = retained / "candidate/candidate-frontend.json"
    fe_seal_path.write_bytes(capture.encoded(verify_res))
    if tamper_fe_seal:
        tampered = json.loads(json.dumps(verify_res))
        tampered["frontend"]["dist_sha256"] = "e" * 64
        fe_seal_path.write_bytes(capture.encoded(tampered))
    for path in (baseline_path, receipt_path, fe_seal_path):
        path.chmod(0o600)

    to_seal = verify_res["frontend"]
    from_seal = baseline_result["manifest"]["frontend"]
    assert to_seal["target"] == str(candidate_release)

    fe_repo = tmp_path / "execute-plans"
    fe_controller_sha = _fe_controller_checkout(fe_repo, FE_MOCK_SCRIPT.replace("@RECORDER@", str(tmp_path / "commands.jsonl")))
    recorder = tmp_path / "commands.jsonl"
    state = tmp_path / "driver-state.pkl"
    state.write_bytes(pickle.dumps({"docker": case.docker, "http": case.http, "root": str(d.ROOT),
                                    "fe_store": str(d.FE_STORE), "fe_link": str(d.FE_LINK)}))
    (tmp_path / "remote-home").mkdir()
    hostile = tmp_path / "hostile-cwd"
    hostile.mkdir()
    (hostile / "docker-compose.yml").write_text("services: {hostile: {image: attacker}}\n")

    artifact_env = {
        "PANTHEON_DEV_ARTIFACT_CONTROLLER_SHA": controller_sha,
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_ID": candidate_id,
        "PANTHEON_DEV_ARTIFACT_RUN_ID": run_id,
        "PANTHEON_DEV_ARTIFACT_ATTEMPT": attempt,
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA": identity["candidate_backend_sha"],
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_FRONTEND_SHA": identity["candidate_frontend_sha"],
        "PANTHEON_DEV_ARTIFACT_PREVIOUS_BACKEND_SHA": identity["previous_backend_sha"],
        "PANTHEON_DEV_ARTIFACT_PREVIOUS_FRONTEND_SHA": identity["previous_frontend_sha"],
        "PANTHEON_DEV_ARTIFACT_DRIVER_PATH": str(controller_dir / "dev_release_artifact_driver.py"),
        "PANTHEON_DEV_ARTIFACT_DRIVER_SHA256": hashlib.sha256(implementations["dev_release_artifact_driver.py"]).hexdigest(),
        "PANTHEON_DEV_ARTIFACT_LIBRARY_PATH": str(controller_dir / "dev_release_artifacts.py"),
        "PANTHEON_DEV_ARTIFACT_LIBRARY_SHA256": hashlib.sha256(implementations["dev_release_artifacts.py"]).hexdigest(),
        "PANTHEON_DEV_ARTIFACT_MANIFEST_PATH": baseline_result["manifest_path"],
        "PANTHEON_DEV_ARTIFACT_MANIFEST_SHA256": baseline_result["manifest_sha256"],
        "PANTHEON_DEV_ARTIFACT_COMPOSE_FILE": str(case.baseline_compose),
        "PANTHEON_DEV_ARTIFACT_BASELINE_FILE": str(baseline_path),
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_RECEIPT_FILE": str(receipt_path),
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_PATH": receipt["candidate_image_manifest_path"],
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256": receipt["candidate_image_manifest_sha256"],
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_OVERRIDE_PATH": receipt["candidate_image_override_path"],
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_OVERRIDE_SHA256": receipt["candidate_image_override_sha256"],
        "PANTHEON_DEV_ARTIFACT_FE_MANIFEST_SHA256": to_seal["manifest_sha256"],
        "PANTHEON_DEV_ARTIFACT_FE_DIST_SHA256": to_seal["dist_sha256"],
        "PANTHEON_DEV_ARTIFACT_FE_TARGET": to_seal["target"],
        "PANTHEON_DEV_ARTIFACT_SEALED_FROM_JSON": json.dumps(from_seal),
        "PANTHEON_DEV_ARTIFACT_SEALED_TO_JSON": json.dumps(to_seal),
        "PANTHEON_DEV_ARTIFACT_FINAL_FE_SEAL_FILE": str(fe_seal_path),
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE": str(case.docker.candidate_compose),
        "PANTHEON_DEV_FE_DEPLOY_SCRIPT": str(fe_repo / "scripts/deploy-dev-vm.sh"),
        "PANTHEON_DEV_FE_CONTROLLER_SHA": fe_controller_sha,
        "PANTHEON_DEV_FE_ROOT": str(d.FE_LINK),
        "PANTHEON_DEV_FE_RELEASES_DIR": str(d.FE_STORE),
    }
    artifact_env["PANTHEON_DEV_FE_NODE_BIN_DIR"] = REAL_NODE_DIR
    env, args_file, stdin_file = _setup_stubbed_dev_environment(tmp_path, sha=identity["candidate_backend_sha"], extra_env=artifact_env)
    real_git = __import__("shutil").which("git")
    stub_git = tmp_path / "bin/git"
    stub_git.write_text(
        '#!/usr/bin/env python3\nimport os, sys\n'
        'if len(sys.argv) > 1 and sys.argv[1] == "-C":\n'
        f'    os.execv({real_git!r}, [{real_git!r}] + sys.argv[1:])\n'
        'sys.exit(97)\n'
    )
    stub_git.chmod(0o755)
    for name in ("docker", "curl", "apt-get", "systemctl", "sudo"):
        stub = tmp_path / "bin" / name
        stub.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                        f'open({str(recorder)!r},"a").write(json.dumps({{"command":"UNEXPECTED_"+sys.argv[0].split("/")[-1],"args":sys.argv[1:]}})+"\\n")\n'
                        'sys.exit(97)\n')
        stub.chmod(0o755)
    # Bare SSH has no Node on PATH; simulate with failing stub (reproducing Root38569825116)
    stub_node = tmp_path / "bin/node"
    stub_node.write_text('#!/usr/bin/env bash\n'
                         'echo "node: command not found" >&2\n'
                         'exit 127\n')
    stub_node.chmod(0o755)
    _install_chain_shims(tmp_path, artifact_root=artifact_root, fe_store=d.FE_STORE, state=state, recorder=recorder)
    lease = os.environ["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"]
    env.update({
        "PANTHEON_DEV_FE_NODE_BIN_DIR": REAL_NODE_DIR,
        "TARGET_ENV": "dev",
        "GCP_DEPLOY_PROJECT_ID": "pantheon-dev-20260902",
        "DEV_VM": "pantheon-dev-deploy",
        "DEV_ZONE": "asia-east1-b",
        "RUNNER_TEMP": str(runner_temp),
        "PANTHEON_RELEASE_REPO_ROOT": str(ROOT),
        "DEV_REMOTE_DIR": str(tmp_path / "MUST_NOT_ACCESS_OWNER_CHECKOUT"),
        "PANTHEON_DEPLOY_LEASE_OWNER": "pantheon-dev-deploy",
        "PANTHEON_DEPLOY_LEASE_EPOCH": "1",
        "PANTHEON_DEPLOY_LEASE_RUN_ID": "987654321",
        "PANTHEON_DEPLOY_LEASE_DELEGATED": "true",
        "PANTHEON_RELEASE_CANDIDATE_ID": candidate_id,
        "PANTHEON_FAILED_BACKEND_SHA": identity["candidate_backend_sha"],
        "PANTHEON_FAILED_FRONTEND_SHA": identity["candidate_frontend_sha"],
        "PANTHEON_ROLLBACK_BACKEND_SHA": identity["previous_backend_sha"],
        "PANTHEON_ROLLBACK_FRONTEND_SHA": identity["previous_frontend_sha"],
        "PANTHEON_DEV_ARTIFACT_TARGET": "final",
        "PANTHEON_DEV_ARTIFACT_OPERATION": "restore-final",
        "PANTHEON_DEV_ARTIFACT_EVIDENCE_PROVENANCE": "runner-local",
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": lease,
        "DEV_BFF_AUTH_STUB": "false",
        "DEV_BFF_AUTH_MODE": "strict",
        "DEV_BFF_JWT_SECRET": "test-jwt-secret-for-strict-auth-32chars",
        "DEV_BFF_OIDC_CLIENT_ID": "test-oidc-client-id",
        "DEV_BFF_OIDC_CLIENT_SECRET": "test-oidc-client-secret",
        "DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET": "test-viewer-secret",
        "DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET": "test-approver-secret",
        "DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET": "test-risk-owner-secret",
        "DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET": "test-operator-a-secret",
        "DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET": "test-operator-b-secret",
        "DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN": "test-openclaw-token",
        "DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED": "false",
    })
    env.update(env_override or {})
    lease_state = Path(env["PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE"])
    lease_doc = json.loads(lease_state.read_text())
    lease_doc.update(leaseId=lease, expectedBackendSha=identity["candidate_backend_sha"])
    lease_state.write_text(json.dumps(lease_doc))

    readback_out = tmp_path / "final-readback.json"
    result = subprocess.run([
        str(SCRIPT), "--environment", "dev", "--component", "bff", "--sha", identity["candidate_backend_sha"],
        "--artifact-restore-final", "--artifact-readback-out", str(readback_out), "--deadline-seconds", "60",
    ], env=env, cwd=hostile, capture_output=True, text=True, timeout=120)

    return SimpleNamespace(result=result, readback_out=readback_out, recorder=recorder, identity=identity,
                           to_seal=to_seal, from_seal=from_seal, candidate_release=candidate_release,
                           controller_sha=controller_sha, case=case, d=d)


def test_full_cli_restore_final_executes_original_bff_chain(case, tmp_path, monkeypatch):
    chain = _run_original_chain(case, tmp_path, monkeypatch)
    result, readback_out, recorder, identity = chain.result, chain.readback_out, chain.recorder, chain.identity
    to_seal, from_seal, candidate_release = chain.to_seal, chain.from_seal, chain.candidate_release
    controller_sha, d = chain.controller_sha, chain.d
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    data = json.loads(readback_out.read_text())
    assert data["operation"] == "restore-final"
    assert data["identity"] == identity
    assert data["frontend"] == to_seal
    assert data["public"]["source_sha"] == identity["candidate_backend_sha"]
    assert data["public"]["authenticated_viewer_readback_verified"] is True
    assert data["images"] == {service: row["image_id"] for service, row in case.docker.built.items()}
    assert "hostile" not in readback_out.read_text()

    events = _events(recorder)
    assert [event["command"] for event in events] == ["fe_deploy", "driver"], events
    fe_event, driver_event = events
    assert (fe_event["action"], fe_event["owner"], fe_event["delegated"], fe_event["run_id"]) == (
        "restore-sealed", "pantheon-dev-deploy", "true", "987654321")
    assert fe_event["event_name"] == ""
    assert fe_event["node_version"].startswith("v22.")
    assert (fe_event["from_target"], fe_event["to_target"]) == (from_seal["target"], to_seal["target"])
    assert os.path.realpath(d.FE_LINK) == os.path.realpath(candidate_release)
    argv = driver_event["argv"]
    assert argv[0] == "restore-final" and driver_event["exit"] == 0
    options = dict(zip(argv[1::2], argv[2::2]))
    assert options["--target"] == "final"
    assert options["--compose-file"] == str(case.docker.candidate_compose)
    assert options["--controller-sha"] == controller_sha
    assert options["--fe-target"] == to_seal["target"]
    docker_calls = driver_event["docker_calls"]
    assert sum(call[:2] == ["image", "load"] for call in docker_calls) == 1
    up_calls = [call for call in docker_calls if call[0] == "compose" and "up" in call]
    assert len(up_calls) == 1 and "--no-build" in up_calls[0] and "--force-recreate" in up_calls[0]
    assert not any(call[0] in ("build", "pull") or call[:2] in (["image", "pull"], ["image", "build"]) for call in docker_calls)


def test_full_cli_restore_final_preserves_explicit_workflow_dispatch(case, tmp_path, monkeypatch):
    chain = _run_original_chain(case, tmp_path, monkeypatch,
                                env_override={"GITHUB_EVENT_NAME": "workflow_dispatch"})
    result, readback_out, recorder = chain.result, chain.readback_out, chain.recorder
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    events = _events(recorder)
    assert [event["command"] for event in events] == ["fe_deploy", "driver"], events
    fe_event = events[0]
    assert fe_event["event_name"] == "workflow_dispatch"
    assert (fe_event["action"], fe_event["owner"], fe_event["delegated"], fe_event["run_id"]) == (
        "restore-sealed", "pantheon-dev-deploy", "true", "987654321")
    assert fe_event["node_version"].startswith("v22.")


def test_original_chain_rejects_tampered_final_fe_seal_after_driver_readback(case, tmp_path, monkeypatch):
    chain = _run_original_chain(case, tmp_path, monkeypatch, tamper_fe_seal=True)
    assert chain.result.returncode != 0
    assert not chain.readback_out.exists()
    assert "evidence rejected" in chain.result.stderr or "dev-artifact-compensation" in chain.result.stderr


def test_original_chain_rejects_missing_or_unsafe_fe_node_runtime(case, tmp_path, monkeypatch):
    chain = _run_original_chain(case, tmp_path, monkeypatch,
                                env_override={"PANTHEON_DEV_FE_NODE_BIN_DIR": str(tmp_path / "missing-node-bin")})
    assert chain.result.returncode != 0
    assert "FE owner Node runtime missing or unsafe" in chain.result.stderr
    assert not chain.readback_out.exists()
    assert _events(chain.recorder) == []


def test_original_chain_rejects_unadmitted_fe_controller_before_fe_or_driver(case, tmp_path, monkeypatch):
    chain = _run_original_chain(case, tmp_path, monkeypatch, env_override={"PANTHEON_DEV_FE_CONTROLLER_SHA": "1" * 40})
    assert chain.result.returncode != 0
    assert "differs from admitted controller" in chain.result.stderr
    assert not chain.readback_out.exists()
    assert _events(chain.recorder) == []


def _fe_controller_check(script: Path, expected: str, operation: str = "restore-final"):
    source = _source()
    start = source.index("verify_fe_controller_checkout() {")
    function = source[start:source.index("\n}\n", start) + 3]
    shell = ("error() { echo \"ERR: $*\" >&2; exit 1; }\n" + function
             + f"\nverify_fe_controller_checkout {script} '{expected}' {operation}\n")
    return subprocess.run(["bash", "-c", shell], capture_output=True, text=True, cwd="/")


def test_fe_controller_checkout_must_match_admitted_commit_and_tracked_sources(tmp_path):
    repo = tmp_path / "execute-plans"
    sha = _fe_controller_checkout(repo, FE_MOCK_SCRIPT)
    script = repo / "scripts/deploy-dev-vm.sh"
    assert _fe_controller_check(script, sha).returncode == 0
    # A path alone is not version evidence: a different admitted commit fails.
    stale = _fe_controller_check(script, "2" * 40)
    assert stale.returncode != 0 and "differs from admitted controller" in stale.stderr
    # restore-final cannot proceed without the admitted identity; other restores keep behavior.
    assert "required for restore-final" in _fe_controller_check(script, "").stderr
    assert _fe_controller_check(script, "", "restore").returncode == 0
    # Modified tracked script or relative helper at the right HEAD is rejected.
    (repo / "scripts/atomic-symlink-cas.py").write_text("# tampered\n")
    assert "differs from admitted commit" in _fe_controller_check(script, sha).stderr
    _git(repo, "checkout", "--", "scripts/atomic-symlink-cas.py")
    script.write_text(FE_MOCK_SCRIPT + "\n# local edit\n")
    assert "differs from admitted commit" in _fe_controller_check(script, sha).stderr
    _git(repo, "checkout", "--", "scripts/deploy-dev-vm.sh")
    # A script outside the checkout's scripts/ layout is not the checkout's controller.
    copied = tmp_path / "copied-deploy-dev-vm.sh"
    copied.write_text(FE_MOCK_SCRIPT)
    assert _fe_controller_check(copied, sha).returncode != 0


def _fe_recorder_script(tmp_path):
    fe_script = tmp_path / "deploy-dev-vm.sh"
    fe_script.write_text(
        '#!/usr/bin/env bash\n'
        'python3 - <<\'PY\'\n'
        'import json, os\n'
        'with open(os.environ["RECORDER"], "a") as f:\n'
        '    f.write(json.dumps({"command": "fe_deploy", "action": os.environ.get("PANTHEON_DEPLOY_ACTION"),\n'
        '        "from": json.loads(os.environ["PANTHEON_DEPLOY_SEALED_FROM_JSON"]),\n'
        '        "to": json.loads(os.environ["PANTHEON_DEPLOY_SEALED_TO_JSON"])}) + "\\n")\n'
        'PY\n')
    fe_script.chmod(0o755)
    return fe_script


def test_baseline_restore_sources_from_retained_unqualified_candidate_capture(fixture, tmp_path):
    env, recorder, driver, library = fixture
    folder = Path(env["PANTHEON_DEV_ARTIFACT_MANIFEST_PATH"]).parent
    captured = {"target": "/var/www/pantheon-dev-fe-releases/candidate", "dist_sha256": "4" * 64,
                "manifest_sha256": "3" * 64, "frontend_sha": "e" * 40, "backend_sha": SHA}
    to_seal = {"target": "/var/www/pantheon-dev-fe-releases/baseline", "dist_sha256": "5" * 64,
               "manifest_sha256": "6" * 64, "frontend_sha": "f" * 40, "backend_sha": PRIOR}
    env.update({"PANTHEON_DEV_FE_DEPLOY_SCRIPT": str(_fe_recorder_script(tmp_path)),
                "PANTHEON_DEV_ARTIFACT_SEALED_TO_JSON": json.dumps(to_seal),
                "PANTHEON_DEPLOY_LEASE_OWNER": "pantheon-dev-deploy"})
    path = folder / "candidate-frontend-captured.json"
    path.write_text(json.dumps(captured)); path.chmod(0o600)
    result = _run(_remote(), env)
    assert result.returncode == 0, result.stderr
    events = _events(recorder)
    assert [e["command"] for e in events] == ["fe_deploy", "driver"]
    assert events[0]["action"] == "restore-sealed"
    assert events[0]["from"] == captured and events[0]["to"] == to_seal

    for mutate in (lambda v: dict(v, backend_sha=PRIOR), lambda v: dict(v, extra="x"),
                   lambda v: {k: x for k, x in v.items() if k != "dist_sha256"}):
        Path(env["RECORDER"]).write_text("")
        path.write_text(json.dumps(mutate(captured)))
        rejected = _run(_remote(), env)
        assert rejected.returncode != 0
        assert not any(e["command"] in ("fe_deploy", "driver") for e in _events(recorder))
