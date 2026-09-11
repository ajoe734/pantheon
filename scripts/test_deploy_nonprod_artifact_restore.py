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

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/deploy_nonprod_vm.sh"
SHA = "a" * 40
PRIOR = "b" * 40
CONTROLLER_ROOT = "/home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/release-artifacts/controllers"


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
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker", "git", "curl", "apt-get", "systemctl", "sudo"):
        stub = bin_dir / name
        stub.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                        'with open(os.environ["RECORDER"],"a") as out:\n'
                        ' out.write(json.dumps({"command":sys.argv[0].split("/")[-1],"args":sys.argv[1:]})+"\\n")\n'
                        'raise SystemExit(97)\n')
        stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "RECORDER": str(recorder),
           "SYNTHETIC_CONTROLLER_ROOT": str(controller_root)}
    # Explicitly synthetic environment for the actual approved-prefix function.
    for variable in re.findall(r'\$\{([A-Z][A-Z0-9_]*)', _function("with_dev_bff_runtime_env")):
        env[variable] = "fixture"
    env.update({
        "PANTHEON_DEPLOY_ENV": "dev", "PANTHEON_DEPLOY_COMPONENT": "bff",
        "PANTHEON_DEPLOY_PROJECT_ID": "pantheon-dev-20260902", "PANTHEON_DEPLOY_SHA": PRIOR,
        "PANTHEON_REMOTE_DIR": str(tmp_path / "MUST_NOT_ACCESS_OWNER_CHECKOUT"),
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
    assert [event["operation"] for event in _events(recorder)] == ["seal-candidate"]


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
