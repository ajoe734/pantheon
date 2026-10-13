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
    assert [event["operation"] for event in _events(recorder)] == ["seal-candidate"]


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
    assert events[0]["from"] == from_seal
    assert events[0]["to"] == to_seal
    assert events[1]["command"] == "driver"
    assert events[1]["operation"] == "restore"

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


def test_full_cli_restore_final_connected_positive(fixture, tmp_path):
    from copy import deepcopy
    import shutil
    from test_deploy_nonprod_vm import _setup_stubbed_dev_environment
    from scripts import capture_dev_artifact_baseline as capture
    from scripts import dev_candidate_receipt as candidate_receipt

    fixture_env, recorder, driver, library = fixture
    repo_root = Path(__file__).parents[1]
    controller_sha = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True
    ).stdout.strip()

    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir(mode=0o700, exist_ok=True)

    run_id = fixture_env["PANTHEON_DEV_ARTIFACT_RUN_ID"]
    attempt = fixture_env["PANTHEON_DEV_ARTIFACT_ATTEMPT"]
    candidate_id = fixture_env["PANTHEON_DEV_ARTIFACT_CANDIDATE_ID"]

    retained = runner_temp / f"pantheon-dev-artifacts-{run_id}-{attempt}"
    retained.mkdir(mode=0o700, exist_ok=True)
    (retained / "baseline").mkdir(mode=0o700, exist_ok=True)
    (retained / "candidate").mkdir(mode=0o700, exist_ok=True)

    real_git = shutil.which("git")
    stub_git = tmp_path / "bin/git"
    stub_git.write_text(
        f'#!/usr/bin/env python3\n'
        f'import os, sys\n'
        f'if len(sys.argv) > 1 and sys.argv[1] == "-C":\n'
        f'    os.execv({real_git!r}, [{real_git!r}] + sys.argv[1:])\n'
        f'sys.exit(97)\n'
    )
    stub_git.chmod(0o755)

    fe_manifest_sha = "2" * 64
    fe_dist_sha = "3" * 64
    fe_target = "/var/www/pantheon-dev-fe-releases/candidate"
    from_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/baseline",
        "dist_sha256": "5" * 64,
        "manifest_sha256": "6" * 64,
        "frontend_sha": "f" * 40,
        "backend_sha": PRIOR,
    }
    to_seal = {
        "target": fe_target,
        "dist_sha256": fe_dist_sha,
        "manifest_sha256": fe_manifest_sha,
        "frontend_sha": "e" * 40,
        "backend_sha": SHA,
    }

    identity = {
        "candidate_id": candidate_id,
        "run_id": run_id,
        "attempt": attempt,
        "controller_sha": controller_sha,
        "candidate_backend_sha": SHA,
        "candidate_frontend_sha": "e" * 40,
        "previous_backend_sha": PRIOR,
        "previous_frontend_sha": "f" * 40,
    }

    bundle = {"schema_version": "pantheon.dev-bff-image-bundle.v1", "source_sha": PRIOR, "services": {}, "archives": {}}
    for index, service in enumerate(candidate_receipt.SERVICES, 1):
        image = "sha256:" + str(index) * 64
        bundle["services"][service] = {"image_id": image, "oci_revision": None, "repo_digests": None}
        bundle["archives"][image] = {"name": image[7:] + "-" + "9" * 64 + ".tar", "sha256": "9" * 64, "size": 123}

    lease = fixture_env["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"]
    baseline = {
        "schema_version": "pantheon.dev-release-artifact-baseline.v1",
        "environment": "dev",
        "project_id": "pantheon-dev-20260902",
        "vm": "pantheon-dev-deploy",
        "identity": deepcopy(identity),
        "capture_lease_id": lease,
        "captured_at": "2026-09-09T00:00:00Z",
        "image_bundle": bundle,
        "image_bundle_sha256": capture.digest(capture.encoded(bundle)),
        "compose_sha256": "8" * 64,
        "frontend": from_seal,
        "baseline_nonsecret_config": {
            "PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN_FILE": None,
            "PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID": "",
            "PERSONA_EVALUATOR_BFF_TOKEN_FILE": None,
            "PERSONA_EVALUATOR_GOVERNANCE_TOKEN_FILE": None,
            **dict.fromkeys(capture.artifacts.BASELINE_AUTH_FLAGS, "false"),
        },
    }
    baseline_hash = capture.digest(capture.encoded(baseline))
    folder = capture.ARTIFACT_ROOT / f"baseline-{run_id}-{attempt}-{candidate_id}"
    sealed_baseline, _ = capture.seal_result(capture.encoded({
        "manifest_path": str(folder / "manifest.json"),
        "manifest_sha256": baseline_hash,
        "manifest": baseline,
    }), identity)

    baseline_path = retained / "baseline/artifact-baseline.json"
    baseline_path.write_bytes(sealed_baseline)
    baseline_path.chmod(0o600)

    candidate_compose = tmp_path / "candidate-compose" / "docker-compose.yml"
    candidate_compose.parent.mkdir(parents=True, exist_ok=True)
    candidate_compose_content = "services:\n  operator-bff:\n    image: pantheon-operator-bff:connected\n"
    candidate_compose.write_text(candidate_compose_content)
    candidate_compose_sha = hashlib.sha256(candidate_compose_content.encode()).hexdigest()

    record = {
        "schema_version": "pantheon.dev-candidate-image-admission.v1",
        "environment": "dev",
        "project_id": "pantheon-dev-20260902",
        "vm": "pantheon-dev-deploy",
        "identity": deepcopy(identity),
        "seal_lease_id": lease,
        "sealed_at": "2026-09-09T00:01:00Z",
        "baseline_manifest_sha256": baseline_hash,
        "candidate_compose_sha256": candidate_compose_sha,
        "services": {
            service: {
                "image_id": "sha256:" + str(index) * 64,
                "oci_revision": SHA,
                "git_sha": None if service == "loop-run-projector-scheduler" else SHA,
                "compose_image": "pantheon-" + service,
            }
            for index, service in enumerate(candidate_receipt.SERVICES, 4)
        },
    }
    override = {
        "services": {
            service: {"image": row["image_id"], "pull_policy": "never"}
            for service, row in record["services"].items()
        }
    }
    override["services"]["operator-bff"]["environment"] = {
        "BFF_IMAGE_DIGEST": record["services"]["operator-bff"]["image_id"],
    }
    record["image_override_sha256"] = capture.digest(capture.encoded(override))
    receipt = {
        "candidate_image_manifest_path": str(folder / "candidate-images.json"),
        "candidate_image_manifest_sha256": capture.digest(capture.encoded(record)),
        "candidate_image_manifest": record,
        "candidate_image_override_path": str(folder / "candidate-images.override.json"),
        "candidate_image_override_sha256": record["image_override_sha256"],
    }
    receipt_path = retained / "candidate/candidate-receipt.json"
    receipt_path.write_bytes(capture.encoded(receipt))
    receipt_path.chmod(0o600)

    fe_seal_path = retained / "candidate/candidate-frontend.json"
    fe_seal_path.write_bytes(capture.encoded(to_seal))
    fe_seal_path.chmod(0o600)

    recorder_file = str(recorder)
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
        'python3 - <<\'PY\'\n'
        'import json, os, sys\n'
        'from_doc = json.loads(os.environ.get("PANTHEON_DEPLOY_SEALED_FROM_JSON", "{}"))\n'
        'to_doc = json.loads(os.environ.get("PANTHEON_DEPLOY_SEALED_TO_JSON", "{}"))\n'
        'for doc, name in ((from_doc, "from"), (to_doc, "to")):\n'
        '    for field in ("target", "dist_sha256", "manifest_sha256", "frontend_sha", "backend_sha"):\n'
        '        if field not in doc:\n'
        '            sys.exit(f"missing field {field} in {name} seal")\n'
        f'with open({recorder_file!r}, "a") as f:\n'
        '    f.write(json.dumps({\n'
        '        "command": "fe_deploy",\n'
        '        "action": os.environ.get("PANTHEON_DEPLOY_ACTION"),\n'
        '        "profile": os.environ.get("PANTHEON_DEPLOY_PROFILE"),\n'
        '        "delegated": os.environ.get("PANTHEON_DEPLOY_LEASE_DELEGATED"),\n'
        '        "owner": os.environ.get("PANTHEON_DEPLOY_LEASE_OWNER"),\n'
        '        "epoch": os.environ.get("PANTHEON_DEPLOY_LEASE_EPOCH"),\n'
        '        "run_id": os.environ.get("PANTHEON_DEPLOY_LEASE_RUN_ID"),\n'
        '    }) + "\\n")\n'
        'PY\n'
        'exit 0\n'
    )
    fe_script.chmod(0o755)

    typed_readback = {
        "schema_version": "pantheon.dev-artifact-readback.v1",
        "operation": "restore-final",
        "manifest_sha256": baseline_hash,
        "identity": identity,
        "pre_restore_source_observations": ["available_matching_source"],
        "image_readback_verified": True,
        "images": {service: row["image_id"] for service, row in record["services"].items()},
        "frontend": to_seal,
        "baseline_nonsecret_config_verified": True,
        "protected_owners_unchanged": True,
        "owners": {
            service: {
                "container_id": "1" * 64,
                "image_id": "sha256:" + "2" * 64,
                "started_at": "2026-09-09T00:00:00.000001Z",
                "restart_count": 0,
            }
            for service in ("governance", "registry", "deployment", "runtime-manager", "deployment-outbox-consumer", "capital")
        },
        "public": {
            "source_sha": SHA,
            "fe_manifest_bytes_verified": True,
            "strict_auth_denials_verified": True,
            "dev_login_enabled": True,
            "authenticated_viewer_readback_verified": True,
        },
    }
    typed_readback["owners"]["dev-paper-principal-issuer"] = None

    synth_root = fixture_env["SYNTHETIC_CONTROLLER_ROOT"]
    controller_dir = Path(synth_root) / controller_sha
    controller_dir.mkdir(mode=0o700, exist_ok=True)
    syn_library = controller_dir / "dev_release_artifacts.py"
    syn_library.write_text("# synthetic controller library\n")
    syn_library.chmod(0o400)
    syn_driver = controller_dir / "dev_release_artifact_driver.py"

    readback_payload = json.dumps(typed_readback)
    syn_driver.write_text(
        f'#!/usr/bin/env python3\n'
        f'import json, sys\n'
        f'operation = sys.argv[1]\n'
        f'args = dict(zip(sys.argv[2::2], sys.argv[3::2]))\n'
        f'with open({recorder_file!r}, "a") as f:\n'
        f'    f.write(json.dumps({{"command": "driver", "operation": operation, "args": args}}) + "\\n")\n'
        f'print({readback_payload!r})\n'
    )
    syn_driver.chmod(0o400)

    artifact_env = {key: value for key, value in fixture_env.items() if key.startswith("PANTHEON_DEV_ARTIFACT_")}
    artifact_env.update({
        "PANTHEON_DEV_ARTIFACT_CONTROLLER_SHA": controller_sha,
        "PANTHEON_DEV_ARTIFACT_DRIVER_PATH": str(syn_driver),
        "PANTHEON_DEV_ARTIFACT_DRIVER_SHA256": hashlib.sha256(syn_driver.read_bytes()).hexdigest(),
        "PANTHEON_DEV_ARTIFACT_LIBRARY_PATH": str(syn_library),
        "PANTHEON_DEV_ARTIFACT_LIBRARY_SHA256": hashlib.sha256(syn_library.read_bytes()).hexdigest(),
        "PANTHEON_DEV_ARTIFACT_MANIFEST_PATH": str(folder / "manifest.json"),
        "PANTHEON_DEV_ARTIFACT_MANIFEST_SHA256": baseline_hash,
        "PANTHEON_DEV_ARTIFACT_BASELINE_FILE": str(baseline_path),
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_RECEIPT_FILE": str(receipt_path),
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256": receipt["candidate_image_manifest_sha256"],
        "PANTHEON_DEV_ARTIFACT_FE_MANIFEST_SHA256": fe_manifest_sha,
        "PANTHEON_DEV_ARTIFACT_FE_DIST_SHA256": fe_dist_sha,
        "PANTHEON_DEV_ARTIFACT_FE_TARGET": fe_target,
        "PANTHEON_DEV_ARTIFACT_SEALED_FROM_JSON": json.dumps(from_seal),
        "PANTHEON_DEV_ARTIFACT_SEALED_TO_JSON": json.dumps(to_seal),
        "PANTHEON_DEV_ARTIFACT_FINAL_FE_SEAL_FILE": str(fe_seal_path),
        "PANTHEON_DEV_FE_DEPLOY_SCRIPT": str(fe_script),
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE": str(candidate_compose),
    })

    env, args_file, stdin_file = _setup_stubbed_dev_environment(tmp_path, sha=SHA, extra_env=artifact_env)
    env.update({
        "TARGET_ENV": "dev",
        "GCP_DEPLOY_PROJECT_ID": "pantheon-dev-20260902",
        "DEV_VM": "pantheon-dev-deploy",
        "DEV_ZONE": "asia-east1-b",
        "RUNNER_TEMP": str(runner_temp),
        "PANTHEON_RELEASE_REPO_ROOT": str(repo_root),
        "DEV_REMOTE_DIR": str(tmp_path / "MUST_NOT_ACCESS_OWNER_CHECKOUT"),
        "GITHUB_RUN_ID": "987654321",
        "GITHUB_RUN_ATTEMPT": "3",
        "PANTHEON_RELEASE_CANDIDATE_ID": candidate_id,
        "PANTHEON_FAILED_BACKEND_SHA": SHA,
        "PANTHEON_FAILED_FRONTEND_SHA": "e" * 40,
        "PANTHEON_ROLLBACK_BACKEND_SHA": PRIOR,
        "PANTHEON_ROLLBACK_FRONTEND_SHA": "f" * 40,
        "PANTHEON_DEV_ARTIFACT_TARGET": "final",
        "PANTHEON_DEV_ARTIFACT_OPERATION": "restore-final",
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
        "PANTHEON_DEV_ARTIFACT_EVIDENCE_PROVENANCE": "runner-local",
    })
    env["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"] = fixture_env["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"]
    state_file = Path(env["PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE"])
    state = json.loads(state_file.read_text())
    state["leaseId"] = env["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"]
    state["expectedBackendSha"] = SHA
    state_file.write_text(json.dumps(state))

    synth_parent = str(Path(synth_root).parent)

    mock_ssh = tmp_path / "bin/ssh"
    mock_ssh.write_text(
        '#!/usr/bin/env python3\n'
        'import base64, json, os, subprocess, sys\n'
        'lines = [sys.stdin.readline() for _ in range(3)]\n'
        'frame = json.loads(lines[1])\n'
        'payload = base64.b64decode(frame["script"]).decode()\n'
        f'payload = payload.replace({CONTROLLER_ROOT!r}, {synth_root!r})\n'
        f'payload = payload.replace({str(Path(CONTROLLER_ROOT).parent)!r}, {synth_parent!r})\n'
        'read_fd, write_fd = os.pipe()\n'
        'os.write(write_fd, b"fixture watchdog pulse")\n'
        'remote_env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), "USER": "tester", "LANG": "C.UTF-8"}\n'
        'remote_env["PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD"] = str(read_fd)\n'
        'proc = subprocess.run(["bash", "-s"], input=payload, text=True, capture_output=False, env=remote_env, pass_fds=(read_fd,))\n'
        'sys.exit(proc.returncode)\n'
    )
    mock_ssh.chmod(0o755)

    readback_out = tmp_path / "final-readback.json"
    result = subprocess.run([
        str(SCRIPT),
        "--environment", "dev",
        "--component", "bff",
        "--sha", SHA,
        "--artifact-restore-final",
        "--artifact-readback-out", str(readback_out),
        "--deadline-seconds", "30",
    ], env=env, capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    assert readback_out.exists(), "readback out file was not written"
    data = json.loads(readback_out.read_text())
    assert data["schema_version"] == "pantheon.dev-artifact-readback.v1"
    assert data["operation"] == "restore-final"
    assert data["identity"]["run_id"] == "12345"
    assert data["identity"]["attempt"] == "2"

    events = _events(recorder)
    assert len(events) == 2
    assert events[0]["command"] == "fe_deploy"
    assert events[0]["action"] == "restore-sealed"
    assert events[0]["owner"] == "pantheon-dev-deploy"
    assert events[0]["delegated"] == "true"
    assert events[0]["run_id"] == "987654321"
    assert events[1]["command"] == "driver"
    assert events[1]["operation"] == "restore-final"
    assert events[1]["args"]["--target"] == "final"
    assert events[1]["args"]["--compose-file"] == str(candidate_compose)


