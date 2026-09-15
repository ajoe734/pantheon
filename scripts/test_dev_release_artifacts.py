"""Isolated primitive tests; all Docker IDs/archives here are synthetic fixtures."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts import dev_release_artifacts as artifacts


SOURCE = "a" * 40
FRONTEND = "b" * 40
IDS = tuple("sha256:" + str(i) * 64 for i in (1, 2, 3))


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.containers = {service: {"image_id": image, "status": "running", "health": "healthy"}
                           for service, image in zip(artifacts.SERVICES, IDS)}
        self.images = {image: {"id": image, "revision": SOURCE, "repo_digests": None} for image in IDS}
        self.fail_save = self.fail_load = self.fail_up = self.drift = False
        self.override = None

    def call(self, *args):
        self.calls.append(args)
        if args[0] == "ps":
            service = args[-1].rsplit("=", 1)[1]
            index = artifacts.SERVICES.index(service) + 1
            return f"{index:064x}\n"
        if args[0] == "inspect":
            return json.dumps(self.containers[artifacts.SERVICES[int(args[-1], 16) - 1]])
        if args[:2] == ("image", "inspect"):
            if args[-1] not in self.images:
                raise artifacts.ArtifactError("fixture image missing")
            return json.dumps(self.images[args[-1]])
        if args[:2] == ("image", "save"):
            Path(args[3]).write_text(json.dumps(self.images[args[-1]]))
            if self.fail_save:
                raise artifacts.ArtifactError("fixture partial save")
            return ""
        if args[:2] == ("image", "load"):
            if self.fail_load:
                raise artifacts.ArtifactError("fixture load failure")
            row = json.loads(Path(args[-1]).read_text())
            self.images[row["id"]] = row
            return "loaded"
        if args[0] == "compose":
            if self.fail_up:
                raise artifacts.ArtifactError("fixture recreate failure")
            paths = [args[i + 1] for i, value in enumerate(args) if value == "-f"]
            self.override = json.loads(Path(paths[-1]).read_text())
            for service, row in self.override["services"].items():
                self.containers[service]["image_id"] = row["image"]
            if self.drift:
                self.containers[artifacts.SERVICES[0]]["image_id"] = "sha256:" + "9" * 64
            return ""
        raise AssertionError(args)


@pytest.fixture
def image_case(tmp_path):
    root = tmp_path / "archives"
    root.mkdir(mode=0o700)
    docker = FakeDocker()
    checks = []
    lease = lambda: checks.append(True)
    bundle = artifacts.capture_images(docker=docker, archive_root=root, source_sha=SOURCE, check_lease=lease)
    compose = tmp_path / "compose.json"
    compose.write_text(json.dumps({"services": {s: {"build": "."} for s in artifacts.SERVICES}}))
    return root, docker, checks, lease, bundle, compose


def validate(case, bundle=None, **overrides):
    root, _, _, _, original, _ = case
    raw = artifacts.manifest_bytes(bundle if bundle is not None else original)
    params = dict(expected_sha256=hashlib.sha256(raw).hexdigest(), expected_source_sha=SOURCE, archive_root=root)
    params.update(overrides)
    return artifacts.validate_images(raw, **params)


def restore(case, **overrides):
    root, docker, _, lease, bundle, compose = case
    raw = artifacts.manifest_bytes(bundle)
    params = dict(expected_sha256=hashlib.sha256(raw).hexdigest(), expected_source_sha=SOURCE,
                  archive_root=root, compose_files=((compose, hashlib.sha256(compose.read_bytes()).hexdigest()),),
                  docker=docker, check_lease=lease, environment="dev")
    params.update(overrides)
    return artifacts.restore_images(raw, **params)


def test_capture_individual_ids_and_absent_registry_metadata(image_case):
    root, docker, checks, _, bundle, _ = image_case
    assert [bundle["services"][s]["image_id"] for s in artifacts.SERVICES] == list(IDS)
    assert len(bundle["archives"]) == len(list(root.glob("*.tar"))) == 3
    assert all(row["repo_digests"] is None for row in bundle["services"].values())
    assert len(checks) >= 5
    assert "Config.Env" not in str(docker.calls)
    assert validate(image_case) == bundle


def test_capture_deduplicates_shared_image_without_inventing_revision(tmp_path):
    tmp_path.chmod(0o700)
    docker = FakeDocker()
    for row in docker.containers.values():
        row["image_id"] = IDS[0]
    docker.images[IDS[0]]["revision"] = None
    result = artifacts.capture_images(docker=docker, archive_root=tmp_path, source_sha=SOURCE, check_lease=lambda: None)
    assert len(result["archives"]) == 1
    assert all(row["oci_revision"] is None for row in result["services"].values())
    assert len([call for call in docker.calls if call[:2] == ("image", "save")]) == 1


@pytest.mark.parametrize("change", ["source", "schema", "extra_service", "missing_service", "image_id", "archive_path", "archive_digest", "archive_size", "revision", "repo_digest", "extra_field"])
def test_invalid_manifests_fail_before_restore(image_case, change):
    bundle = copy.deepcopy(image_case[4])
    service = bundle["services"][artifacts.SERVICES[0]]
    archive = bundle["archives"][IDS[0]]
    if change == "source": bundle["source_sha"] = "c" * 40
    elif change == "schema": bundle["schema_version"] = "other"
    elif change == "extra_service": bundle["services"]["governance"] = service
    elif change == "missing_service": bundle["services"].pop(artifacts.SERVICES[0])
    elif change == "image_id": service["image_id"] = "latest"
    elif change == "archive_path": archive["name"] = "../elsewhere"
    elif change == "archive_digest": archive["sha256"] = "c" * 64
    elif change == "archive_size": archive["size"] = True
    elif change == "revision": service["oci_revision"] = "c" * 40
    elif change == "repo_digest": service["repo_digests"] = ["fabricated:latest"]
    elif change == "extra_field": bundle["secret"] = "should-not-be-accepted"
    with pytest.raises(artifacts.ArtifactError):
        validate(image_case, bundle)


def test_trusted_hash_and_duplicate_keys_are_enforced(image_case):
    with pytest.raises(artifacts.ArtifactError, match="trusted digest"):
        validate(image_case, expected_sha256="0" * 64)
    raw = b'{"source_sha":1,"source_sha":2}'
    with pytest.raises(artifacts.ArtifactError, match="duplicate"):
        artifacts.validate_images(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                                  expected_source_sha=SOURCE, archive_root=image_case[0])


@pytest.mark.parametrize("mode", ["missing", "tampered", "symlink", "directory"])
def test_unavailable_archive_never_loads_or_recreates(image_case, mode):
    root, docker, _, _, bundle, _ = image_case
    target = root / bundle["archives"][IDS[0]]["name"]
    target.unlink()
    if mode == "tampered": target.write_bytes(b"different")
    elif mode == "symlink": target.symlink_to(root / bundle["archives"][IDS[1]]["name"])
    elif mode == "directory": target.mkdir()
    docker.calls.clear()
    with pytest.raises((artifacts.ArtifactError, OSError)):
        restore(image_case)
    assert not docker.calls


def test_restore_loads_retained_ids_without_build_pull_or_owner_commands(image_case):
    root, docker, checks, _, bundle, _ = image_case
    docker.images.clear()
    for row in docker.containers.values():
        row["image_id"] = "sha256:" + "9" * 64
    docker.calls.clear()
    result = restore(image_case)
    assert result == {"image_readback_verified": True, "services": dict(zip(artifacts.SERVICES, IDS))}
    command = next(call for call in docker.calls if call[0] == "compose")
    assert command[-3:] == artifacts.SERVICES
    assert "--no-build" in command and "--no-deps" in command
    assert command[command.index("--pull") + 1] == "never"
    assert "--build" not in command and "build" not in command
    assert all(row["pull_policy"] == "never" for row in docker.override["services"].values())
    assert len([c for c in docker.calls if c[:2] == ("image", "load")]) == 3
    assert set(root.iterdir()) == {root / row["name"] for row in bundle["archives"].values()}


@pytest.mark.parametrize("mode", ["lease", "production", "compose", "load", "up", "readback"])
def test_restore_failures_never_emit_success(image_case, mode):
    _, docker, _, _, _, compose = image_case
    overrides = {}
    if mode == "lease":
        def denied(): raise artifacts.ArtifactError("expired lease")
        overrides["check_lease"] = denied
    elif mode == "production": overrides["environment"] = "production"
    elif mode == "compose": overrides["compose_files"] = ((compose, "0" * 64),)
    elif mode == "load": docker.images.clear(); docker.fail_load = True
    elif mode == "up": docker.fail_up = True
    elif mode == "readback": docker.drift = True
    docker.calls.clear()
    with pytest.raises(artifacts.ArtifactError):
        restore(image_case, **overrides)
    if mode in ("lease", "production", "compose"):
        assert not docker.calls


def test_partial_capture_is_not_published(tmp_path):
    tmp_path.chmod(0o700)
    docker = FakeDocker()
    docker.fail_save = True
    with pytest.raises(artifacts.ArtifactError):
        artifacts.capture_images(docker=docker, archive_root=tmp_path, source_sha=SOURCE, check_lease=lambda: None)
    assert not list(tmp_path.iterdir())


def test_capture_requires_private_real_storage(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(artifacts.ArtifactError, match="private"):
        artifacts.capture_images(docker=FakeDocker(), archive_root=tmp_path, source_sha=SOURCE, check_lease=lambda: None)


@pytest.fixture
def frontend_case(tmp_path):
    store = tmp_path / "releases"
    store.mkdir()
    release = store / "qualified-release"
    release.mkdir()
    (release / "assets").mkdir()
    (release / "assets" / "app.js").write_text("console.log('fixture');\n")
    (release / "index.html").write_text("<html>synthetic fixture</html>\n")
    digest = artifacts.frontend_dist_digest(release)
    manifest = {"schemaVersion": 1, "repository": "ajoe734/execute-plans", "deploymentState": "accepted",
                "app": "execute-plans", "sourceBranch": "dev", "bffCommitEvidence": True,
                "commit": FRONTEND, "frontendSha": FRONTEND, "bffCommit": SOURCE,
                "bffSourceCommitSha": SOURCE, "artifactDigestSha256": digest}
    (release / "deployment.json").write_text(json.dumps(manifest))
    link = tmp_path / "live"
    link.symlink_to(release)
    return store, release, link


def capture_fe(case):
    store, _, link = case
    return artifacts.capture_frontend(release_store=store, live_link=link, frontend_sha=FRONTEND, backend_sha=SOURCE)


@pytest.mark.parametrize("change", ["assets", "manifest_bytes", "target", "source", "symlink_asset", "outside"])
def test_same_source_does_not_mask_frontend_artifact_drift(frontend_case, change):
    store, release, link = frontend_case
    before = capture_fe(frontend_case)
    if change == "assets": (release / "index.html").write_text("different")
    elif change == "manifest_bytes":
        manifest = release / "deployment.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    elif change == "target":
        other = store / "same-bytes-other-release"
        shutil.copytree(release, other)
        link.unlink(); link.symlink_to(other)
    elif change == "source":
        manifest = release / "deployment.json"
        data = json.loads(manifest.read_text()); data["commit"] = "c" * 40
        manifest.write_text(json.dumps(data))
    elif change == "symlink_asset": (release / "escape.js").symlink_to(release / "index.html")
    elif change == "outside": link.unlink(); link.symlink_to(store.parent)
    with pytest.raises((artifacts.ArtifactError, OSError)):
        artifacts.verify_frontend(before, release_store=store, live_link=link)


def test_frontend_exact_readback(frontend_case):
    before = capture_fe(frontend_case)
    artifacts.verify_frontend(before, release_store=frontend_case[0], live_link=frontend_case[2])


@pytest.mark.parametrize("field,value", [("artifactDigest", "0" * 64), ("frontend", {"commitSha": "c" * 40}),
                                         ("bff", {"sourceCommitSha": "c" * 40}), ("sourceBranch", "main"),
                                         ("bffCommitEvidence", False)])
def test_frontend_conflicting_manifest_claims_fail(frontend_case, field, value):
    manifest = frontend_case[1] / "deployment.json"
    data = json.loads(manifest.read_text()); data[field] = value
    manifest.write_text(json.dumps(data))
    with pytest.raises(artifacts.ArtifactError):
        capture_fe(frontend_case)


def test_wrong_image_inside_authenticated_archive_fails_before_compose(image_case):
    root, docker, _, _, bundle, _ = image_case
    archive = bundle["archives"][IDS[0]]
    target = root / archive["name"]
    target.write_text(json.dumps({"id": "sha256:" + "f" * 64, "revision": SOURCE, "repo_digests": None}))
    archive["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    archive["size"] = target.stat().st_size
    archive["name"] = IDS[0].removeprefix("sha256:") + "-" + archive["sha256"] + ".tar"
    target.rename(root / archive["name"])
    docker.images.clear(); docker.calls.clear()
    with pytest.raises(artifacts.ArtifactError):
        restore(image_case)
    assert not any(call[0] == "compose" for call in docker.calls)


def test_same_image_can_have_distinct_archive_bytes_without_overwrite(image_case):
    root, docker, _, lease, first, _ = image_case
    for row in docker.images.values():
        row["repo_digests"] = []  # Same immutable image, different optional tag metadata.
    second = artifacts.capture_images(docker=docker, archive_root=root, source_sha=SOURCE, check_lease=lease)
    assert set(first["archives"]) == set(second["archives"])
    assert len(list(root.glob("*.tar"))) == 6
    validate(image_case, first)
    validate(image_case, second)


def test_actual_docker_transport_cannot_use_ambient_remote_context(monkeypatch):
    observed = []
    monkeypatch.setenv("DOCKER_HOST", "tcp://not-approved.invalid:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "not-approved")
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: observed.append((args, kwargs)) or
                        subprocess.CompletedProcess(args, 0, stdout="fixture"))
    assert artifacts.Docker().call("image", "inspect", IDS[0]) == "fixture"
    args, options = observed[0]
    assert args[:3] == ["docker", "--host", "unix:///var/run/docker.sock"]
    assert "DOCKER_HOST" not in options["env"] and "DOCKER_CONTEXT" not in options["env"]
    assert options["check"] and options.get("shell", False) is False


def test_canonical_dist_matches_separate_frontend_helper(frontend_case):
    """Optional cross-repo parity; point to the real FE file, never copy it here."""
    helper = os.environ.get("PANTHEON_FE_RELEASE_CANDIDATE_HELPER")
    if not helper or shutil.which("node") is None:
        pytest.skip("requires the separately checked-out FE digest helper and Node")
    release = frontend_case[1]
    (release / "é.txt").write_text("utf8 fixture")
    (release / "😀.txt").write_text("astral fixture")
    script = "const m=await import(process.argv[1]); console.log(m.digestReleaseDist({distDir:process.argv[2]}).artifactDigestSha256)"
    result = subprocess.run(["node", "--input-type=module", "-e", script,
                             Path(helper).resolve().as_uri(), str(release)], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == artifacts.frontend_dist_digest(release)


@pytest.mark.parametrize("field", ["failure_stage", "failure_kind", "failure_location", "exit_code"])
@pytest.mark.parametrize("invalid", [None, [], {}, True, "fixture-secret", 0, -1])
def test_malformed_failure_metadata_is_ignored_without_secondary_exception(field, invalid):
    row = artifacts.failure_record(artifacts.ArtifactError("fixture-secret"), "capture")
    row[field] = invalid
    assert artifacts.remote_failure_record(json.dumps(row).encode()) is None


def test_diagnostic_parser_ignores_duplicate_keys_untrusted_fields_and_bounded_noise():
    row = artifacts.failure_record(artifacts.ArtifactError("fixture-secret"), "capture")
    valid = json.dumps(row).encode()
    assert artifacts.remote_failure_record(b"private raw stderr\n" + valid) == row
    assert artifacts.remote_failure_record(b'{"status":"error",' + valid[1:]) is None
    assert artifacts.remote_failure_record(json.dumps({**row, "stderr": "fixture-secret"}).encode()) is None
    assert artifacts.remote_failure_record(b"[" * 2000) is None
    assert artifacts.remote_failure_record(b"x" * (1024 * 1024 + 1)) is None
    assert artifacts.remote_failure_record(b"\xff") is None


@pytest.mark.parametrize("status", [1, 37, 124, 255, -15])
def test_failure_record_preserves_innermost_subprocess_status_and_never_message(status):
    def call(*_args, **_kwargs):
        raise subprocess.CalledProcessError(status, ["fixture-secret-command"], stderr="fixture-secret-stderr")
    original = subprocess.run
    subprocess.run = call
    try:
        with pytest.raises(artifacts.ArtifactError) as caught:
            artifacts.Docker().call("inspect", "fixture-secret-image")
    finally:
        subprocess.run = original
    row = artifacts.failure_record(caught.value, "capture")
    assert row["exit_code"] == (status if status > 0 else 128 - status)
    assert row["failure_kind"] == "subprocess"
    assert row["failure_location"].startswith("dev_release_artifacts.py:")
    assert "fixture-secret" not in json.dumps(row)
    assert artifacts.remote_failure_record(json.dumps(row).encode()) == row


def test_unexpected_data_error_retains_checked_in_source_location():
    with pytest.raises(artifacts.ArtifactError) as caught:
        artifacts._json(b"not json fixture-secret")
    row = artifacts.failure_record(caught.value, "capture")
    assert row["failure_kind"] == "invalid-data"
    assert row["failure_location"].startswith("dev_release_artifacts.py:")
    assert "fixture-secret" not in json.dumps(row)
