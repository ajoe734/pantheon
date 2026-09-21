"""Independent capture-wrapper contracts using local Git and fake transport.

All credentials, manifests and image IDs below are synthetic fixtures. Tests
never contact the VM, Docker, GitHub, or a real credential provider.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from scripts import capture_dev_artifact_baseline as c
from scripts import dev_release_artifacts as primitive


IDENTITY = dict(candidate_id="c" * 64, run_id="123456", attempt="2", controller_sha="d" * 40,
                candidate_backend_sha="e" * 40, candidate_frontend_sha="f" * 40,
                previous_backend_sha="a" * 40, previous_frontend_sha="b" * 40)
GUARD_ID = "12345678-1234-4234-8234-123456789abc"
VIEWER_SECRET = "fixture-viewer-'quoted'-$never-expanded\nline-two"


@pytest.mark.parametrize("entrypoint", [
    "capture_dev_artifact_baseline.py", "dev_remote_guarded_exec.py",
    "dev_candidate_receipt.py", "fetch_dev_artifact_evidence.py",
    "dev_artifact_compensation_evidence.py", "dev_release_artifact_driver.py",
])
@pytest.mark.parametrize("safe_path", ["ordinary", "environment", "flag"])
def test_direct_artifact_entrypoints_import_siblings_without_cwd_shadowing(
    tmp_path, entrypoint, safe_path,
):
    # Reproduce the real sanitized workflow, not pytest's package import path.
    for module in ("dev_release_artifacts", "capture_dev_artifact_baseline", "dev_candidate_receipt"):
        (tmp_path / f"{module}.py").write_text(
            "raise RuntimeError('untrusted-cwd-module-was-imported')\n"
        )
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    env.pop("PYTHONSAFEPATH", None)
    if safe_path == "environment":
        env["PYTHONSAFEPATH"] = "1"
    command = [sys.executable, "-B"]
    if safe_path == "flag":
        command.append("-P")
    command.extend([str(c.ROOT / "scripts" / entrypoint), "--help"])
    result = subprocess.run(command, cwd=tmp_path, env=env, text=True,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
    assert "untrusted-cwd-module" not in result.stdout + result.stderr


def environment(identity=None):
    result = {"TARGET_ENV": "dev", "GCP_DEPLOY_PROJECT_ID": "pantheon-dev-20260902",
              "DEV_VM": "pantheon-dev-deploy", "DEV_ZONE": "asia-east1-b",
              "DEV_DEPLOY_SSH_HOST": "34.81.52.222", "DEV_DEPLOY_SSH_USER": c.VM_HOME.name,
              "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": GUARD_ID,
              "DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID": "fixture-viewer", "DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET": VIEWER_SECRET,
              "DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET": "fixture-write-credential-must-not-travel",
              "GITHUB_TOKEN": "fixture-github-token-must-not-travel"}
    result.update({"PANTHEON_DEV_ARTIFACT_" + key.upper(): value for key, value in (identity or IDENTITY).items()})
    return result


def guard_state(identity=None):
    identity = identity or IDENTITY
    return {"schemaVersion": 1, "repository": "ajoe734/execute-plans", "branch": "environment-coordination",
            "path": ".pantheon/environment-leases/pantheon-dev-environment.json", "resource": "pantheon-dev-environment",
            "mode": "deployment", "leaseId": GUARD_ID, "expectedBackendSha": identity["candidate_backend_sha"]}


def manifest(identity=None):
    identity = identity or IDENTITY
    ids = {service: "sha256:" + str(index) * 64 for index, service in enumerate(primitive.SERVICES, 1)}
    bundle = {"schema_version": primitive.SCHEMA, "source_sha": identity["previous_backend_sha"],
              "services": {service: {"image_id": image, "oci_revision": None, "repo_digests": None} for service, image in ids.items()},
              "archives": {image: {"name": image[7:] + "-" + "9" * 64 + ".tar", "sha256": "9" * 64, "size": 123}
                           for image in ids.values()}}
    return {"schema_version": "pantheon.dev-release-artifact-baseline.v1", "environment": "dev",
            "project_id": "pantheon-dev-20260902", "vm": "pantheon-dev-deploy", "identity": dict(identity),
            "capture_lease_id": GUARD_ID, "captured_at": "2026-09-09T00:00:00Z", "image_bundle": bundle,
            "image_bundle_sha256": c.digest(c.encoded(bundle)), "compose_sha256": "8" * 64,
            "frontend": {"target": "/var/www/pantheon-dev-fe-releases/fixture-prior", "dist_sha256": "7" * 64,
                         "manifest_sha256": "6" * 64, "frontend_sha": identity["previous_frontend_sha"],
                         "backend_sha": identity["previous_backend_sha"]},
            "baseline_nonsecret_config": {
                "PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN_FILE": "/run/pantheon-principals/PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN",
                "PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID": "pantheon-dev-paper-provisioner",
                **dict.fromkeys(primitive.BASELINE_AUTH_FLAGS, "true")}}


def result(identity=None):
    identity = identity or IDENTITY
    outer = manifest(identity)
    return {"manifest_path": str(c.ARTIFACT_ROOT / f"baseline-{identity['run_id']}-{identity['attempt']}-{identity['candidate_id']}" / "manifest.json"),
            "manifest_sha256": c.digest(c.encoded(outer)), "manifest": outer}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(root, message):
    git(root, "add", "--all")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize("field", list(IDENTITY))
def test_identity_fields_are_required_and_not_shell_input(field):
    env = environment()
    env["PANTHEON_DEV_ARTIFACT_" + field.upper()] = "$(fixture-untrusted-command)"
    with pytest.raises(c.CaptureError): c.identity_from_environment(env)


@pytest.mark.parametrize("field", ["TARGET_ENV", "GCP_DEPLOY_PROJECT_ID", "DEV_VM", "DEV_ZONE", "DEV_DEPLOY_SSH_HOST", "DEV_DEPLOY_SSH_USER"])
def test_only_current_explicit_dev_target_is_accepted(field):
    env = environment(); env[field] = "unapproved"
    with pytest.raises(c.CaptureError): c.require_guarded_dev(env)


@pytest.mark.parametrize("value", ["", "not-a-uuid", GUARD_ID.upper().replace("1234", "ABCD", 1)])
def test_guard_context_is_required_but_not_an_authority_token(value):
    env = environment(); env["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"] = value
    with pytest.raises(c.CaptureError): c.require_guarded_dev(env)


@pytest.mark.parametrize("field", [None, "leaseId", "expectedBackendSha", "resource", "mode", "repository", "path"])
def test_local_guard_state_binds_context_to_candidate(tmp_path, field):
    env = environment()
    state = guard_state()
    if field: state[field] = "unapproved-state"
    path = tmp_path / "lease.json"; path.write_text(json.dumps(state))
    env["PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE"] = str(path)
    if field:
        with pytest.raises(c.CaptureError): c.require_guarded_dev(env)
    else:
        assert c.require_guarded_dev(env) == GUARD_ID


def test_seal_bytes_match_driver_canonical_serialization():
    emitted = result()
    raw, outputs = c.seal_result(json.dumps(emitted).encode(), IDENTITY)
    assert raw == primitive.manifest_bytes(emitted["manifest"])
    assert outputs == {"manifest_path": emitted["manifest_path"], "manifest_sha256": hashlib.sha256(raw).hexdigest()}
    assert VIEWER_SECRET not in raw.decode()


@pytest.mark.parametrize("change", ["digest", "identity", "project", "outside", "traversal", "wrong_run_path", "missing_field", "extra_field", "null_manifest", "list_manifest", "secret_config"])
def test_malformed_or_unrestorable_capture_result_cannot_be_sealed(change):
    emitted = result()
    if change == "digest": emitted["manifest_sha256"] = "0" * 64
    elif change == "identity": emitted["manifest"]["identity"]["attempt"] = "99"
    elif change == "project": emitted["manifest"]["project_id"] = "production"
    elif change == "outside": emitted["manifest_path"] = "/tmp/not-private/manifest.json"
    elif change == "traversal": emitted["manifest_path"] = str(c.ARTIFACT_ROOT) + "/../escape/manifest.json"
    elif change == "wrong_run_path": emitted["manifest_path"] = str(c.ARTIFACT_ROOT / "other-run" / "manifest.json")
    elif change == "missing_field": emitted["manifest"].pop("image_bundle")
    elif change == "extra_field": emitted["manifest"]["unexpected_secret_dump"] = "must-not-be-uploaded"
    elif change == "null_manifest": emitted["manifest"] = None
    elif change == "list_manifest": emitted["manifest"] = []
    elif change == "secret_config": emitted["manifest"]["baseline_nonsecret_config"]["UNEXPECTED_SECRET"] = "must-not-be-uploaded"
    if change != "digest": emitted["manifest_sha256"] = c.digest(c.encoded(emitted["manifest"]))
    with pytest.raises(c.CaptureError): c.seal_result(json.dumps(emitted).encode(), IDENTITY)


def test_duplicate_keys_are_rejected_before_becoming_an_external_seal():
    raw = json.dumps(result()).encode()
    raw = b'{"manifest_path":"/ignored-invalid-path",' + raw[1:]
    with pytest.raises(c.CaptureError): c.seal_result(raw, IDENTITY)


@pytest.mark.parametrize("key", primitive.BASELINE_AUTH_FLAGS)
@pytest.mark.parametrize("value", [None, "", "true", "false", "TRUE", "1", "0", "yes", "no", "on", "off"])
def test_seal_preserves_exact_captured_auth_values(key, value):
    emitted = result()
    emitted["manifest"]["baseline_nonsecret_config"][key] = value
    emitted["manifest_sha256"] = c.digest(c.encoded(emitted["manifest"]))
    raw, _ = c.seal_result(json.dumps(emitted).encode(), IDENTITY)
    assert json.loads(raw)["baseline_nonsecret_config"][key] == value


@pytest.mark.parametrize("failure", ["old_two_field_manifest", "missing_auth_flag", "not_a_boolean"])
def test_unknown_auth_baseline_cannot_be_sealed(failure):
    emitted = result()
    config = emitted["manifest"]["baseline_nonsecret_config"]
    if failure == "old_two_field_manifest":
        for key in primitive.BASELINE_AUTH_FLAGS:
            del config[key]
    elif failure == "missing_auth_flag":
        del config[primitive.BASELINE_AUTH_FLAGS[0]]
    else:
        config[primitive.BASELINE_AUTH_FLAGS[0]] = "fixture-private-unsupported"
    emitted["manifest_sha256"] = c.digest(c.encoded(emitted["manifest"]))
    with pytest.raises(c.CaptureError) as error:
        c.seal_result(json.dumps(emitted).encode(), IDENTITY)
    assert "fixture-private" not in str(error.value)


def test_read_implementation_requires_exact_committed_bytes(tmp_path):
    source = tmp_path / "controller-source"
    (source / "scripts").mkdir(parents=True)
    git(source, "init", "-q")
    path = source / "scripts" / c.IMPLEMENTATIONS[0]
    path.write_bytes(b"# fixture controller\n")
    sha = commit(source, "fixture controller")
    assert c.read_implementation(source, path.name, sha) == path.read_bytes()
    path.write_bytes(b"# uncommitted drift\n")
    with pytest.raises(c.CaptureError): c.read_implementation(source, path.name, sha)
    other = path.with_name("other.py"); path.rename(other); path.symlink_to(other)
    with pytest.raises(c.CaptureError): c.read_implementation(source, path.name, sha)


@pytest.fixture
def installer_case(tmp_path):
    source = tmp_path / "owner-mounted-source"
    source.mkdir()
    git(source, "init", "-q")
    (source / "docker-compose.yml").write_text("services: {}\n")
    prior = commit(source, "fixture prior Compose")
    (source / "docker-compose.yml").write_text("services: {governance: {image: upgraded-owner}}\n")
    current = commit(source, "fixture upgraded owner Compose")
    root = tmp_path / "private-retained"
    raw = b"# fixture immutable controller\n"
    payload = {"root": str(root), "source": str(source), "controller_sha": IDENTITY["controller_sha"],
               "previous_backend_sha": prior,
               "files": {name: {"base64": base64.b64encode(raw).decode(), "sha256": c.digest(raw)} for name in c.IMPLEMENTATIONS}}
    return source, prior, current, root, raw, payload


def install(payload):
    return subprocess.run([sys.executable, "-c", c.INSTALLER, base64.b64encode(c.encoded(payload)).decode()],
                          capture_output=True, text=True, preexec_fn=lambda: os.umask(0o077))


def test_installer_retains_private_controller_and_separate_prior_worktree(installer_case):
    source, prior, current, root, raw, payload = installer_case
    before = (source / "docker-compose.yml").read_bytes()
    completed = install(payload)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert git(source, "rev-parse", "HEAD") == current
    assert (source / "docker-compose.yml").read_bytes() == before
    config = root / "compose" / prior
    assert git(config, "rev-parse", "HEAD") == prior
    assert (config / "docker-compose.yml").read_text() == "services: {}\n"
    for name in c.IMPLEMENTATIONS:
        controller = root / "controllers" / IDENTITY["controller_sha"] / name
        assert controller.read_bytes() == raw
        assert controller.stat().st_mode & 0o777 == 0o400
    assert root.stat().st_mode & 0o777 == 0o700
    assert install(payload).returncode == 0


@pytest.mark.parametrize("change", ["bytes", "symlink", "digest", "root_mode", "config_symlink"])
def test_installer_rejects_retained_drift_without_overwrite(installer_case, change):
    _, prior, _, root, raw, payload = installer_case
    assert install(payload).returncode == 0
    controller = root / "controllers" / IDENTITY["controller_sha"] / c.IMPLEMENTATIONS[0]
    if change == "bytes": controller.chmod(0o600); controller.write_bytes(b"unapproved bytes")
    elif change == "symlink":
        target = controller.with_name("other.py"); controller.rename(target); controller.symlink_to(target)
    elif change == "digest": payload["files"][c.IMPLEMENTATIONS[0]]["sha256"] = "0" * 64
    elif change == "root_mode": root.chmod(0o755)
    elif change == "config_symlink":
        config = root / "compose" / prior
        alternate = config.with_name("other"); config.rename(alternate); config.symlink_to(alternate)
    assert install(payload).returncode != 0
    if change == "bytes": assert controller.read_bytes() == b"unapproved bytes"
    elif change != "symlink": assert controller.read_bytes() == raw


def test_new_worktree_does_not_execute_source_checkout_hooks(installer_case):
    source, _, _, _, _, payload = installer_case
    marker = source / "unexpected-source-hook-ran"
    hook = source / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\n" + "touch " + shlex.quote(str(marker)) + "\n")
    hook.chmod(0o700)
    assert install(payload).returncode == 0
    assert not marker.exists(), "new baseline worktree must not execute owner-source hooks"


def test_remote_script_forwards_only_viewer_and_inherited_channel(tmp_path):
    script = c.remote_script(IDENTITY, {name: b"# fixture implementation\n" for name in c.IMPLEMENTATIONS}, environment(), GUARD_ID)
    assert "fixture-write-credential-must-not-travel" not in script
    assert "fixture-github-token-must-not-travel" not in script
    assert "DEV_LOGIN_VIEWER_CLIENT_SECRET=" + shlex.quote(VIEWER_SECRET) in script
    assert '${PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD:?remote watchdog channel required}' in script
    command = script.splitlines()[-1]
    assert command.endswith('--guard-channel-fd "${PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD}"')
    args = shlex.split(command)[1:]
    assert args[0] == "python3" and args[2] == "capture"
    for field, value in IDENTITY.items(): assert args[args.index("--" + field.replace("_", "-")) + 1] == value
    assert args[args.index("--compose-file") + 1] == str(c.ARTIFACT_ROOT / "compose" / IDENTITY["previous_backend_sha"] / "docker-compose.yml")
    assert "--baseline-source" not in args
    assert "--observed-live-bff-sha" not in args
    path = tmp_path / "capture.sh"; path.write_text(script)
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0


def test_remote_script_forwards_drift_environment_when_present(tmp_path):
    env = environment()
    drift_source = "standby_frontend_pair_manifest+live_bff_drift_recovery"
    drift_sha = "dc15751a9b20f8bc0931529d68af8898e691c898"
    env["PANTHEON_DEV_ARTIFACT_BASELINE_SOURCE"] = drift_source
    env["PANTHEON_DEV_ARTIFACT_OBSERVED_LIVE_BFF_SHA"] = drift_sha
    script = c.remote_script(IDENTITY, {name: b"# fixture implementation\n" for name in c.IMPLEMENTATIONS}, env, GUARD_ID)
    command = script.splitlines()[-1]
    args = shlex.split(command)[1:]
    assert args[args.index("--baseline-source") + 1] == drift_source
    assert args[args.index("--observed-live-bff-sha") + 1] == drift_sha
    path = tmp_path / "capture.sh"; path.write_text(script)
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0


@pytest.mark.parametrize("invalid_sha", ["dc15751", "g" * 40])
def test_rendered_script_refuses_invalid_observed_drift_sha(tmp_path, invalid_sha):
    env = environment()
    drift_source = "standby_frontend_pair_manifest+live_bff_drift_recovery"
    env["PANTHEON_DEV_ARTIFACT_BASELINE_SOURCE"] = drift_source
    env["PANTHEON_DEV_ARTIFACT_OBSERVED_LIVE_BFF_SHA"] = invalid_sha
    script = c.remote_script(IDENTITY, {name: b"# fixture implementation\n" for name in c.IMPLEMENTATIONS}, env, GUARD_ID)
    command = script.splitlines()[-1]
    args = shlex.split(command)[1:]
    assert args[args.index("--baseline-source") + 1] == drift_source
    assert args[args.index("--observed-live-bff-sha") + 1] == invalid_sha

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_python = fake_bin / "python3"
    fake_python.write_text(f"""#!/bin/sh
if [ "$1" = "-" ]; then
    exit 0
fi
shift
exec "{sys.executable}" "{c.ROOT}/scripts/dev_release_artifact_driver.py" "$@"
""")
    fake_python.chmod(0o755)

    r_fd, w_fd = os.pipe()
    os.write(w_fd, b"pulse")

    try:
        completed = subprocess.run(
            ["bash"],
            input=script,
            text=True,
            capture_output=True,
            env={**os.environ,
                 "PATH": f"{fake_bin}:{os.environ['PATH']}",
                 "PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD": str(r_fd),
                 "PYTHONPATH": "/tmp/pytest-lib:."},
            pass_fds=(r_fd,),
        )
        assert completed.returncode == 75
        row = json.loads(completed.stderr)
        assert row["failure_stage"] == "capture"
        assert row["exit_code"] == 75
        assert row["failure_kind"] == "contract"
        assert row["failure_location"].startswith("dev_release_artifact_driver.py:")
    finally:
        os.close(r_fd)
        os.close(w_fd)


@pytest.fixture
def fake_transport(tmp_path, monkeypatch):
    source = tmp_path / "controller"
    scripts = source / "scripts"; scripts.mkdir(parents=True)
    git(source, "init", "-q")
    for name in c.IMPLEMENTATIONS: (scripts / name).write_text("# pinned fixture implementation\n")
    sha = commit(source, "fixture approved controller")
    identity = {**IDENTITY, "controller_sha": sha}
    emitted = result(identity)
    marker = tmp_path / "transport.json"
    transport = scripts / "dev_remote_guarded_exec.py"
    transport.write_text("import json,sys,os\nfrom pathlib import Path\n"
                         "args=sys.argv[1:]\np=Path(args[args.index('--script-file')+1])\n"
                         f"Path({str(marker)!r}).write_text(json.dumps({{'args':args,'script_path':str(p),'mode':p.stat().st_mode & 0o777,'viewer_present':{VIEWER_SECRET!r} in p.read_text()}}))\n"
                         f"print({json.dumps(emitted)!r})\n")
    monkeypatch.setattr(c, "ROOT", source)
    for name, value in environment(identity).items(): monkeypatch.setenv(name, value)
    state = tmp_path / "guard-state.json"
    state.write_text(json.dumps(guard_state(identity)))
    monkeypatch.setenv("PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE", str(state))
    evidence = tmp_path / "evidence"
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(sys, "argv", ["capture", "--evidence-dir", str(evidence)])
    return source, identity, emitted, marker, evidence, output, transport


def test_main_actual_fake_transport_seals_only_nonsecret_evidence(fake_transport, capsys):
    _, _, emitted, marker, evidence, output, _ = fake_transport
    assert c.main() == 0
    sent = json.loads(marker.read_text())
    assert sent["mode"] == 0o600
    assert not Path(sent["script_path"]).exists()
    assert sent["args"][sent["args"].index("--deadline-seconds") + 1] == "1200"
    assert (evidence / "artifact-baseline.json").read_bytes() == c.encoded(emitted["manifest"])
    assert (evidence / "SHA256SUMS").read_text() == emitted["manifest_sha256"] + "  artifact-baseline.json\n"
    assert set(path.name for path in evidence.iterdir()) == {"artifact-baseline.json", "SHA256SUMS"}
    public = capsys.readouterr()
    for raw in (public.out, public.err, output.read_text(), (evidence / "artifact-baseline.json").read_text()):
        assert "fixture-viewer" not in raw and "must-not-travel" not in raw


def test_main_creates_private_run_parent_even_with_public_default_umask(fake_transport, monkeypatch):
    _, _, _, _, evidence, _, _ = fake_transport
    nested = evidence.parent / "new-run" / "baseline"
    monkeypatch.setattr(sys, "argv", ["capture", "--evidence-dir", str(nested)])
    previous = os.umask(0o022)
    try:
        assert c.main() == 0
    finally:
        os.umask(previous)
    assert nested.parent.stat().st_mode & 0o777 == 0o700
    assert nested.stat().st_mode & 0o777 == 0o700


def test_failed_transport_never_uploads_raw_diagnostics_or_creates_seal(fake_transport, capsys):
    _, _, _, _, evidence, output, transport = fake_transport
    transport.write_text("import sys\nprint('fixture-secret-stdout')\nprint('fixture-secret-stderr',file=sys.stderr)\nraise SystemExit(75)\n")
    assert c.main() == 75
    public = capsys.readouterr()
    assert "fixture-secret" not in public.out + public.err
    assert public.out == ""
    assert not list(evidence.iterdir())
    assert not output.exists()


@pytest.mark.parametrize("status", [1, 37, 124, 255])
@pytest.mark.parametrize("diagnostic", ["none", "valid", "duplicate", "list-stage", "list-kind"])
def test_remote_diagnostics_never_mask_actual_exit_or_leak_stderr(fake_transport, capsys, status, diagnostic):
    _, _, _, _, evidence, output, transport = fake_transport
    row = primitive.failure_record(primitive.ArtifactError("fixture-secret"), "capture")
    row["exit_code"] = 75  # Untrusted metadata cannot replace the observed exit.
    if diagnostic == "list-stage": row["failure_stage"] = []
    if diagnostic == "list-kind": row["failure_kind"] = []
    raw = json.dumps(row)
    if diagnostic == "duplicate": raw = '{"status":"error",' + raw[1:]
    if diagnostic == "none": raw = "fixture-secret SSH failure"
    transport.write_text("import sys\n" + f"print({raw!r},file=sys.stderr)\nraise SystemExit({status})\n")
    assert c.main() == status
    public = capsys.readouterr()
    observed = json.loads(public.err)
    assert observed["exit_code"] == status
    assert observed["failure_stage"] == ("capture" if diagnostic == "valid" else "transport")
    assert "fixture-secret" not in public.out + public.err
    assert not list(evidence.iterdir()) and not output.exists()


def test_installer_failure_before_driver_reports_stage_and_original_status():
    script = c.remote_script(IDENTITY, {name: b"# fixture\n" for name in c.IMPLEMENTATIONS}, environment(), GUARD_ID)
    # A real shell executes the generated boundary, but this local function
    # replaces Python before any filesystem, VM, Docker or network operation.
    stub = "python3() { echo fixture-secret-installer-error >&2; return 37; }\n"
    completed = subprocess.run(["bash"], input=stub + script, text=True, capture_output=True,
                               env={**os.environ, "PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD": "9"})
    assert completed.returncode == 37 and completed.stdout == ""
    row = json.loads(completed.stderr)
    assert row["failure_stage"] == "install" and row["exit_code"] == 37
    assert "fixture-secret" not in completed.stderr


def test_unexpected_seal_bug_reports_local_boundary_without_accepting_evidence(fake_transport, monkeypatch, capsys):
    _, _, _, _, evidence, output, _ = fake_transport
    def fail(*_args, **_kwargs):
        raise RuntimeError("fixture-secret unexpected parser bug")
    monkeypatch.setattr(c, "seal_result", fail)
    assert c.main() == 75
    public = capsys.readouterr()
    row = json.loads(public.err)
    assert row["failure_stage"] == "seal-result" and row["failure_kind"] == "unexpected"
    assert row["failure_location"].startswith("capture_dev_artifact_baseline.py:")
    assert "fixture-secret" not in public.out + public.err
    assert not list(evidence.iterdir()) and not output.exists()


def drifted_manifest(identity=None, drift_sha="dc15751a9b20f8bc0931529d68af8898e691c898"):
    outer = manifest(identity)
    bundle = outer["image_bundle"]
    bundle["source_sha"] = drift_sha
    for s in bundle["services"].values():
        s["oci_revision"] = drift_sha
    outer["image_bundle_sha256"] = c.digest(c.encoded(bundle))
    return outer


def drifted_result(identity=None, drift_sha="dc15751a9b20f8bc0931529d68af8898e691c898"):
    identity = identity or IDENTITY
    outer = drifted_manifest(identity, drift_sha)
    return {
        "manifest_path": str(c.ARTIFACT_ROOT / f"baseline-{identity['run_id']}-{identity['attempt']}-{identity['candidate_id']}" / "manifest.json"),
        "manifest_sha256": c.digest(c.encoded(outer)),
        "manifest": outer,
    }


def test_reproduction_run_35193009030_drift_seal_mismatch_fails_closed():
    # Run 35193009030 reproduction: remote driver capture succeeded on VM with
    # service oci_revision equal to live drifted sha dc15751a9, but wrapper seal
    # validation at capture_dev_artifact_baseline.py line 283 rejected it because
    # DRIFT_FIELDS were only forwarded to remote_script and not consulted during seal.
    drift_sha = "dc15751a9b20f8bc0931529d68af8898e691c898"
    emitted = result()
    for s in emitted["manifest"]["image_bundle"]["services"].values():
        s["oci_revision"] = drift_sha
    emitted["manifest"]["image_bundle_sha256"] = c.digest(c.encoded(emitted["manifest"]["image_bundle"]))
    emitted["manifest_sha256"] = c.digest(c.encoded(emitted["manifest"]))

    with pytest.raises(c.CaptureError, match="capture image revision differs from prior"):
        c.seal_result(json.dumps(emitted).encode(), IDENTITY)


def test_drifted_manifest_seal_succeeds_and_records_accepted_pair_and_observed_sha():
    drift_sha = "dc15751a9b20f8bc0931529d68af8898e691c898"
    emitted = drifted_result(IDENTITY, drift_sha)
    drift_source = "standby_frontend_pair_manifest+live_bff_drift_recovery"

    raw, outputs = c.seal_result(
        json.dumps(emitted).encode(),
        IDENTITY,
        baseline_source=drift_source,
        observed_live_bff_sha=drift_sha,
    )
    assert raw == primitive.manifest_bytes(emitted["manifest"])
    assert outputs["manifest_path"] == emitted["manifest_path"]
    assert outputs["manifest_sha256"] == hashlib.sha256(raw).hexdigest()
    assert outputs["previous_backend_sha"] == IDENTITY["previous_backend_sha"]
    assert outputs["previous_frontend_sha"] == IDENTITY["previous_frontend_sha"]
    assert outputs["observed_live_bff_sha"] == drift_sha

    sealed = json.loads(raw)
    assert sealed["identity"]["previous_backend_sha"] == IDENTITY["previous_backend_sha"]
    assert sealed["identity"]["previous_frontend_sha"] == IDENTITY["previous_frontend_sha"]
    assert sealed["frontend"]["backend_sha"] == IDENTITY["previous_backend_sha"]
    assert sealed["frontend"]["frontend_sha"] == IDENTITY["previous_frontend_sha"]
    assert sealed["image_bundle"]["source_sha"] == drift_sha
    for s in sealed["image_bundle"]["services"].values():
        assert s["oci_revision"] == drift_sha


def test_drifted_manifest_without_drift_inputs_fails_existing_checks():
    drift_sha = "dc15751a9b20f8bc0931529d68af8898e691c898"
    emitted = drifted_result(IDENTITY, drift_sha)

    # Calling seal_result without drift inputs fails at source_sha check
    with pytest.raises(c.CaptureError, match="capture image bundle differs from admitted prior"):
        c.seal_result(json.dumps(emitted).encode(), IDENTITY)

    # When source_sha matches previous_backend_sha but revisions are drifted, fails at revision check
    partial = result()
    for s in partial["manifest"]["image_bundle"]["services"].values():
        s["oci_revision"] = drift_sha
    partial["manifest"]["image_bundle_sha256"] = c.digest(c.encoded(partial["manifest"]["image_bundle"]))
    partial["manifest_sha256"] = c.digest(c.encoded(partial["manifest"]))
    with pytest.raises(c.CaptureError, match="capture image revision differs from prior"):
        c.seal_result(json.dumps(partial).encode(), IDENTITY)


@pytest.mark.parametrize("invalid_sha", ["dc15751", "g" * 40, "", "DC15751A9B20F8BC0931529D68AF8898E691C898", "1" * 39, "1" * 41])
def test_drifted_manifest_invalid_observed_sha_fails_closed(invalid_sha):
    drift_sha = "dc15751a9b20f8bc0931529d68af8898e691c898"
    emitted = drifted_result(IDENTITY, drift_sha)
    drift_source = "standby_frontend_pair_manifest+live_bff_drift_recovery"

    with pytest.raises(c.CaptureError, match="invalid observed live BFF drift identity"):
        c.seal_result(
            json.dumps(emitted).encode(),
            IDENTITY,
            baseline_source=drift_source,
            observed_live_bff_sha=invalid_sha,
        )


def test_drifted_manifest_observed_sha_mismatch_fails_closed():
    drift_sha = "dc15751a9b20f8bc0931529d68af8898e691c898"
    other_sha = "1" * 40
    emitted = drifted_result(IDENTITY, drift_sha)
    drift_source = "standby_frontend_pair_manifest+live_bff_drift_recovery"

    # observed_live_bff_sha does not match manifest image bundle source_sha or image revisions
    with pytest.raises(c.CaptureError, match="capture image bundle differs from admitted prior"):
        c.seal_result(
            json.dumps(emitted).encode(),
            IDENTITY,
            baseline_source=drift_source,
            observed_live_bff_sha=other_sha,
        )

    # When bundle source_sha equals other_sha, but service revisions still have drift_sha
    emitted_source_other = drifted_result(IDENTITY, other_sha)
    for s in emitted_source_other["manifest"]["image_bundle"]["services"].values():
        s["oci_revision"] = drift_sha
    emitted_source_other["manifest"]["image_bundle_sha256"] = c.digest(c.encoded(emitted_source_other["manifest"]["image_bundle"]))
    emitted_source_other["manifest_sha256"] = c.digest(c.encoded(emitted_source_other["manifest"]))
    with pytest.raises(c.CaptureError, match="capture image revision differs from prior"):
        c.seal_result(
            json.dumps(emitted_source_other).encode(),
            IDENTITY,
            baseline_source=drift_source,
            observed_live_bff_sha=other_sha,
        )


def test_real_wrapper_seal_path_with_drift_environment_succeeds(fake_transport, monkeypatch):
    source, identity, _, marker, evidence, output, transport = fake_transport
    drift_sha = "dc15751a9b20f8bc0931529d68af8898e691c898"
    drift_source = "standby_frontend_pair_manifest+live_bff_drift_recovery"
    emitted = drifted_result(identity, drift_sha)

    # Configure transport to emit the drifted result
    transport.write_text("import json,sys,os\nfrom pathlib import Path\n"
                         "args=sys.argv[1:]\np=Path(args[args.index('--script-file')+1])\n"
                         f"Path({str(marker)!r}).write_text(json.dumps({{'args':args,'script_path':str(p),'mode':p.stat().st_mode & 0o777,'viewer_present':{VIEWER_SECRET!r} in p.read_text()}}))\n"
                         f"print({json.dumps(emitted)!r})\n")

    monkeypatch.setenv("PANTHEON_DEV_ARTIFACT_BASELINE_SOURCE", drift_source)
    monkeypatch.setenv("PANTHEON_DEV_ARTIFACT_OBSERVED_LIVE_BFF_SHA", drift_sha)

    assert c.main() == 0

    sealed_bytes = (evidence / "artifact-baseline.json").read_bytes()
    assert sealed_bytes == c.encoded(emitted["manifest"])
    assert (evidence / "SHA256SUMS").read_text() == emitted["manifest_sha256"] + "  artifact-baseline.json\n"

    sealed = json.loads(sealed_bytes)
    assert sealed["identity"]["previous_backend_sha"] == identity["previous_backend_sha"]
    assert sealed["identity"]["previous_frontend_sha"] == identity["previous_frontend_sha"]
    assert sealed["image_bundle"]["source_sha"] == drift_sha

    outputs_content = output.read_text()
    assert f"previous_backend_sha={identity['previous_backend_sha']}" in outputs_content
    assert f"previous_frontend_sha={identity['previous_frontend_sha']}" in outputs_content
    assert f"observed_live_bff_sha={drift_sha}" in outputs_content
    assert f"compose_file={c.ARTIFACT_ROOT}/compose/{identity['previous_backend_sha']}/docker-compose.yml" in outputs_content
