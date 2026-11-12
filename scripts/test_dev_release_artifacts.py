"""Isolated primitive tests; all Docker IDs/archives here are synthetic fixtures."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

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
        # Whole-stack primitives: project-wide listing, stack rows, env, tag.
        if args[0] == "ps" and len(args) == 5:
            return "".join(f"{index + 1:064x}\n" for index in range(len(artifacts.SERVICES)))
        if args[0] == "inspect" and args[2] == artifacts.STACK_FORMAT:
            service = artifacts.SERVICES[int(args[-1], 16) - 1]
            row = self.containers[service]
            return json.dumps({"service": service, "oneoff": "False", "image_id": row["image_id"],
                               "status": row["status"], "exit_code": 0, "health_test": ["CMD", "true"],
                               "health": row["health"]})
        if args[0] == "inspect" and args[2] == artifacts.STACK_ENV_FORMAT:
            return json.dumps("GIT_SHA=" + SOURCE) + "\n"
        if args[0] == "tag":
            self.tags = getattr(self, "tags", {})
            self.tags[args[2]] = args[1]
            return ""
        if args[0] == "ps":
            service = args[-1].rsplit("=", 1)[1]
            index = artifacts.SERVICES.index(service) + 1
            return f"{index:064x}\n"
        if args[0] == "inspect":
            return json.dumps(self.containers[artifacts.SERVICES[int(args[-1], 16) - 1]])
        if args[:2] == ("image", "inspect"):
            ref = getattr(self, "tags", {}).get(args[-1], args[-1])
            if ref not in self.images:
                raise artifacts.ArtifactError("fixture image missing")
            return json.dumps(self.images[ref])
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
        if args[0] == "compose" and "config" in args:
            return json.dumps({"services": {name: {"build": {"context": "."}, "image": "pantheon-" + name}
                                            for name in artifacts.SERVICES}})
        if args[0] == "compose":
            if self.fail_up:
                raise artifacts.ArtifactError("fixture recreate failure")
            paths = [args[i + 1] for i, value in enumerate(args) if value == "-f"]
            self.override = json.loads(Path(paths[-1]).read_text())
            for service, row in self.override["services"].items():
                if service in self.containers:
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


def test_restore_health_timeout_preserves_retained_worker_probe(image_case, tmp_path):
    _, docker, _, _, _, compose = image_case
    probe = ["CMD", "python", "scripts/run_agora_interaction_worker.py", "--healthcheck"]
    model = json.loads(compose.read_text())
    model["services"]["agora-interaction-worker"]["healthcheck"] = {
        "test": probe, "timeout": "5s", "interval": "30s", "retries": 3,
    }
    compose.write_text(json.dumps(model))
    restore(image_case)
    override = tmp_path / "readback-override.json"
    override.write_text(json.dumps(docker.override))
    # Use Compose itself to prove that changing the timeout keeps the original
    # probe and failure threshold, rather than disabling or replacing health.
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose), "-f", str(override), "config", "--format", "json"],
        capture_output=True, text=True, check=True,
    )
    health = json.loads(result.stdout)["services"]["agora-interaction-worker"]["healthcheck"]
    assert health["test"] == probe
    assert health["timeout"] == "30s"
    assert health["interval"] == "30s"
    assert health["retries"] == 3
    assert health.get("disable") is not True


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


def test_capture_still_rejects_stopped_container(tmp_path):
    tmp_path.chmod(0o700)
    docker = FakeDocker()
    docker.containers[artifacts.SERVICES[0]]["status"] = "exited"
    with pytest.raises(artifacts.ArtifactError, match="not running"):
        artifacts.capture_images(docker=docker, archive_root=tmp_path,
                                 source_sha=SOURCE, check_lease=lambda: None)
    assert not any(call[:2] == ("image", "save") for call in docker.calls)


def test_restore_still_rejects_unhealthy_container(image_case):
    image_case[1].containers["loop-run-projector-scheduler"]["health"] = "unhealthy"
    with pytest.raises(artifacts.ArtifactError, match="not running and healthy"):
        restore(image_case)


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


@pytest.mark.parametrize("change", ["assets", "manifest_bytes", "target", "source", "symlink_asset", "symlink_receipt", "outside"])
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
    elif change == "symlink_receipt": (release / ".prepared-receipt.json").symlink_to(release / "index.html")
    elif change == "outside": link.unlink(); link.symlink_to(store.parent)
    with pytest.raises((artifacts.ArtifactError, OSError)):
        artifacts.verify_frontend(before, release_store=store, live_link=link)


def test_frontend_exact_readback(frontend_case):
    before = capture_fe(frontend_case)
    artifacts.verify_frontend(before, release_store=frontend_case[0], live_link=frontend_case[2])


def test_prepared_receipt_does_not_change_frontend_asset_identity(frontend_case):
    before = capture_fe(frontend_case)
    (frontend_case[1] / ".prepared-receipt.json").write_text('{"fixture": "prepared"}\n')
    assert capture_fe(frontend_case) == before
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
    (release / ".prepared-receipt.json").write_text('{"fixture": "prepared"}\n')
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


def test_capture_and_validate_images_reject_conflicting_oci_revisions(tmp_path):
    root = tmp_path / "archives"
    root.mkdir(mode=0o700)
    docker = FakeDocker()
    conflicting_sha = "d" * 40
    docker.images[IDS[0]]["revision"] = conflicting_sha
    checks = []
    lease = lambda: checks.append(True)

    with pytest.raises(artifacts.ArtifactError, match="observed OCI revision conflicts with baseline"):
        artifacts.capture_images(docker=docker, archive_root=root, source_sha=SOURCE, check_lease=lease)

    docker.images[IDS[0]]["revision"] = SOURCE
    bundle = artifacts.capture_images(docker=docker, archive_root=root, source_sha=SOURCE, check_lease=lease)
    bundle["services"]["operator-bff"]["oci_revision"] = conflicting_sha
    raw = artifacts.manifest_bytes(bundle)
    with pytest.raises(artifacts.ArtifactError, match="invalid observed OCI revision"):
        artifacts.validate_images(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                                  expected_source_sha=SOURCE, archive_root=root)


def test_save_image_persists_archive_and_rejects_empty_or_differing_overwrite(tmp_path):
    root = tmp_path / "archives"
    root.mkdir(mode=0o700)
    docker = FakeDocker()
    checks = []
    lease = lambda: checks.append(True)

    record = artifacts.save_image(docker=docker, archive_root=root, image_id=IDS[0], check_lease=lease)
    assert checks == [True]
    assert record["name"].startswith(IDS[0].removeprefix("sha256:") + "-")
    target = root / record["name"]
    assert target.is_file()
    digest, size = artifacts._file_digest(target)
    assert record["sha256"] == digest and record["size"] == size

    second = artifacts.save_image(docker=docker, archive_root=root, image_id=IDS[0], check_lease=lease)
    assert second == record

    target.write_bytes(b"tampered")
    with pytest.raises(artifacts.ArtifactError, match="existing image archive differs; refusing overwrite"):
        artifacts.save_image(docker=docker, archive_root=root, image_id=IDS[0], check_lease=lease)

    class EmptyDocker(FakeDocker):
        def call(self, *args):
            if args[:2] == ("image", "save"):
                Path(args[3]).touch()
                return ""
            return super().call(*args)

    with pytest.raises(artifacts.ArtifactError, match="empty image archive"):
        artifacts.save_image(docker=EmptyDocker(), archive_root=root, image_id="sha256:" + "9" * 64, check_lease=lease)




# --- Whole-stack compensation (fake Docker at the real hosted scale) --------

STACK_SIZE = 57
SEALED = artifacts.SERVICES
SCHEDULER = artifacts.CONTROL_SERVICE
OTHERS = tuple(f"svc-{index:02d}" for index in range(1, STACK_SIZE - len(SEALED) - 3)) + ("source-ingest", SCHEDULER, "paper-signal-producer")
# Healthy steady scheduler posture; EXCLUSIVE is absent (not empty), FORCE is empty.
STEADY_CONTROL = {"SOURCE_INGEST_CONTROLLER_MODE": "reconcile_only", "SOURCE_INGEST_CONTROLLER_MAX_TICKS": "0",
                  "SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS": "",
                  "SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL": "scheduled_tick"}
# What a bounded refresh caller leaks into a Compose restore.
BOUNDED_CALLER = {"SOURCE_INGEST_CONTROLLER_MODE": "reconcile_and_pull", "SOURCE_INGEST_CONTROLLER_MAX_TICKS": "1",
                  "SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS": "tw-twse-tpex-official-market",
                  "SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS": "tw-twse-tpex-official-market",
                  "SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL": "reconciled_live_proof"}
# Source-ingest API egress posture and what the same bounded caller leaks into it.
API = artifacts.API_SERVICE
STEADY_API = {"PANTHEON_EXTERNAL_EGRESS": "allowlist", "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": ""}
BOUNDED_API = {"PANTHEON_EXTERNAL_EGRESS": "allowlist",
               "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": "openapi.twse.com.tw,www.tpex.org.tw"}
# Paper producer capital reader reference: the mounted credential file path is recorded,
# never its value; a restoring caller carries an empty file reference and expression off.
PAPER = artifacts.PAPER_SERVICE
STEADY_PAPER = {"PANTHEON_PAPER_EXPRESSION_LAYER": "on", "PANTHEON_CAPITAL_API_URL": "http://capital:8092",
                "DEPLOYMENT_CAPITAL_SERVICE_TOKEN_FILE": "/run/pantheon-principals/DEPLOYMENT_CAPITAL_SERVICE_TOKEN"}
EMPTY_PAPER_CALLER = {"PANTHEON_PAPER_EXPRESSION_LAYER": "off", "PANTHEON_CAPITAL_API_URL": "http://capital:8092",
                      "DEPLOYMENT_CAPITAL_SERVICE_TOKEN_FILE": ""}
STEADY = {SCHEDULER: STEADY_CONTROL, API: STEADY_API, PAPER: STEADY_PAPER}
CALLER = {SCHEDULER: BOUNDED_CALLER, API: BOUNDED_API, PAPER: EMPTY_PAPER_CALLER}
ONE_SHOT = "minio-init"
TENANT = "tenant-recorded-at-baseline"


def _sid(prefix: str, index: int) -> str:
    return "sha256:" + f"{prefix}{index:02x}".ljust(64, "0")


class StackDocker:
    """54 Compose services, a one-shot init container, shared-image semantics."""

    def __init__(self):
        self.calls = []
        self.names = (*SEALED, *OTHERS, ONE_SHOT)
        assert len(self.names) == STACK_SIZE
        self.images = {}
        self.tags = {}
        self.containers = {}
        self.compose_defaults = {}
        self.stuck = set()          # services a compose "up" fails to move
        self.unhealthy = set()      # services that never become healthy
        self.no_stop = set()        # services that ignore "docker stop"
        self.up_calls = []
        self.pinned = {}            # service -> digest-pinned Compose image reference
        self.source_scratch = tempfile.TemporaryDirectory(prefix="source-codec-test-")
        self.source_journal = Path(self.source_scratch.name) / "journal.jsonl"
        from services.source_ingestion.requirement_state import market_snapshot_writer_samples
        self.source_journal.write_text(market_snapshot_writer_samples()["official_daily"])
        self.source_codecs = {}  # image id -> exact immutable Git revision
        for index, service in enumerate(self.names):
            image = _sid("a", index)
            self.add_image(image)
            one_shot = service == ONE_SHOT
            self.containers[service] = {
                "cid": f"{index + 1:064x}", "image_id": image,
                "status": "exited" if one_shot else "running", "exit_code": 0,
                "health_test": None if one_shot else ["CMD", "true"],
                "health": None if one_shot else "healthy",
                "env": {"PANTHEON_TENANT_ID": TENANT if service == "source-ingest" else "tenant-" + service,
                        "GIT_SHA": SOURCE}}
            # What Compose would apply without the override: the failure mode
            # seen on source-ingest (tenant reset by a default).
            self.compose_defaults[service] = {"PANTHEON_TENANT_ID": "compose-default", "GIT_SHA": "f" * 40}
            self.tags["pantheon-" + service] = image   # what a plain "compose up" would start
            if service in STEADY:
                self.containers[service].update(control_env=dict(STEADY[service]), restart="unless-stopped")
                self.compose_defaults[service]["control"] = dict(CALLER[service])

    def add_image(self, image):
        self.images[image] = {"id": image, "revision": SOURCE, "repo_digests": None}

    def by_cid(self, cid):
        return next((s, c) for s, c in self.containers.items() if c["cid"] == cid)

    def mutate_to_candidate(self, keep=("grafana-like",)):
        """Whole stack on the rejected candidate except services in ``keep``."""
        for index, (service, row) in enumerate(self.containers.items()):
            if service in keep:
                continue
            image = _sid("c", index)
            self.add_image(image)
            self.tags["pantheon-" + service] = image   # the candidate build moved the names
            row.update(image_id=image, status="running" if service != ONE_SHOT else "exited",
                       health=None if service == ONE_SHOT else "healthy",
                       env={"PANTHEON_TENANT_ID": "compose-default", "GIT_SHA": "f" * 40})
        index = len(self.containers)
        self.containers["candidate-only"] = {
            "cid": f"{index + 1:064x}", "image_id": _sid("c", 99), "status": "running", "exit_code": 0,
            "health_test": ["CMD", "true"], "health": "healthy", "env": {"PANTHEON_TENANT_ID": "x", "GIT_SHA": "f" * 40}}
        self.add_image(_sid("c", 99))

    def call(self, *args):
        self.calls.append(args)
        if args[0] == "run":
            return run_local_source_probe(self, args)
        if args[0] == "ps":
            rows = list(self.containers.items())
            if len(args) > 5:
                service = args[-1].rsplit("=", 1)[1]
                rows = [item for item in rows if item[0] == service]
            return "".join(row["cid"] + "\n" for _, row in rows)
        if args[0] == "inspect":
            service, row = self.by_cid(args[-1])
            if args[2] == artifacts.STACK_FORMAT:
                return json.dumps({"service": service, "oneoff": "False", "image_id": row["image_id"],
                                   "status": row["status"], "exit_code": row["exit_code"],
                                   "health_test": row["health_test"], "health": row["health"]})
            if args[2] == artifacts.STACK_ENV_FORMAT:
                return "".join(json.dumps(f"{k}={v}") + "\n" for k, v in row["env"].items())
            if args[2] == artifacts.SOURCE_PATH_FORMAT:
                return json.dumps("SOURCE_INGEST_LATEST_MARKET_SNAPSHOT_PATH=" + str(self.source_journal)) + "\n"
            if args[2] in (artifacts.CONTROL_ENV_FORMAT, artifacts.API_ENV_FORMAT, artifacts.PAPER_ENV_FORMAT):
                keys = artifacts.CONTROL_POSTURE[service][0]
                return "".join(json.dumps(f"{k}={v}") + "\n" for k, v in row.get("control_env", {}).items() if k in keys)
            if args[2] == artifacts.RESTART_FORMAT:
                return json.dumps(row.get("restart", ""))
            if args[2] == artifacts.CONTAINER_FORMAT:
                return json.dumps({"image_id": row["image_id"], "status": row["status"], "health": row["health"]})
        if args[:2] == ("image", "inspect"):
            ref = self.tags.get(args[-1], args[-1])
            if ref not in self.images:
                raise artifacts.ArtifactError("fixture image missing")
            return json.dumps(self.images[ref])
        if args[:2] == ("image", "save"):
            Path(args[3]).write_text(json.dumps(self.images[args[-1]]))
            return ""
        if args[:2] == ("image", "load"):
            row = json.loads(Path(args[-1]).read_text())
            self.images[row["id"]] = row
            return ""
        if args[0] == "tag":
            self.tags[args[2]] = args[1]
            return ""
        if args[0] == "stop":
            service, row = self.by_cid(args[1])
            if service not in self.no_stop:
                row["status"], row["health"] = "exited", None
            return ""
        if args[0] == "start":
            service, row = self.by_cid(args[1])
            row["status"], row["health"] = "running", "healthy"
            return ""
        if args[0] == "compose" and "config" in args:
            return json.dumps({"services": {name: ({"image": self.pinned[name]} if name in self.pinned else
                                                   {"build": {"context": "."}, "image": "pantheon-" + name})
                                            for name in self.names}})
        if args[0] == "compose":
            paths = [args[i + 1] for i, value in enumerate(args) if value == "-f"]
            text = Path(paths[-1]).read_text()
            override = json.loads(text.replace("!reset null", "null"))["services"]
            names = [n for n in args[args.index("up") + 1:] if n in self.containers]
            self.up_calls.append({"names": names, "wait": "--wait" in args, "override": override, "text": text})
            for name in names:
                if name in self.stuck:
                    continue
                row = self.containers[name]
                env = dict(self.compose_defaults[name])
                control = env.pop("control", None)
                requested = dict(override[name].get("environment", {}))
                if control is not None:
                    keys = artifacts.CONTROL_POSTURE[name][0]
                    # "!reset" (parsed as null here) removes the key; a caller value never refills it.
                    merged = {**control, **requested}
                    row["control_env"] = {k: v for k, v in merged.items()
                                          if k in keys and (k not in requested or requested[k] is not None)}
                    row["restart"] = override[name].get("restart", "no")
                    requested = {k: v for k, v in requested.items() if k not in keys}
                env.update(requested)
                row.update(image_id=override[name]["image"], env=env,
                           status="exited" if name == ONE_SHOT else "running",
                           health=None if name == ONE_SHOT else ("unhealthy" if name in self.unhealthy else "healthy"))
            return ""
        raise AssertionError(args)


def run_local_source_probe(docker, args):
    """Docker transport fixture; codec and lifecycle calls execute for real.

    This proves source behavior locally, not Docker isolation or hosted recovery.
    """
    image = args[args.index("--entrypoint") + 2]
    code = args[args.index("-c") + 1]
    mode, argument = args[-2:]
    bootstrap = ""
    if image in docker.source_codecs:
        ref = docker.source_codecs[image]
        root = Path(__file__).resolve().parents[1]
        raw = subprocess.run(["git", "show", ref + ":services/source_ingestion/requirement_state.py"],
                             cwd=root, capture_output=True, check=True, timeout=15).stdout
        path = Path(docker.source_scratch.name) / (ref + ".py")
        path.write_bytes(raw)
        bootstrap = (
            "import importlib.util, sys, services.source_ingestion as package\n"
            f"spec=importlib.util.spec_from_file_location('services.source_ingestion.requirement_state',{str(path)!r})\n"
            "module=importlib.util.module_from_spec(spec)\n"
            "sys.modules[spec.name]=module\n"
            "spec.loader.exec_module(module)\n"
            "package.requirement_state=module\n"
        )
    result = subprocess.run([sys.executable, "-c", bootstrap + code, mode, argument],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise artifacts.ArtifactError("local codec subprocess failed")
    return result.stdout


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.sleeps += 1


@pytest.fixture
def stack_case(tmp_path):
    root = tmp_path / "archives"
    root.mkdir(mode=0o700)
    docker = StackDocker()
    lease = lambda: None
    bundle = artifacts.capture_images(docker=docker, archive_root=root, source_sha=SOURCE, check_lease=lease)
    stack = artifacts.capture_stack(docker=docker, source_sha=SOURCE, check_lease=lease)
    compose = tmp_path / "compose.json"
    compose.write_text(json.dumps({"services": {s: {"build": "."} for s in docker.names}}))
    baseline = {service: dict(row) for service, row in docker.containers.items()}
    return root, docker, lease, bundle, stack, compose, baseline


def restore_stack(case, **overrides):
    root, docker, lease, bundle, stack, compose, _ = case
    raw = artifacts.manifest_bytes(bundle)
    clock = Clock()
    params = dict(expected_sha256=hashlib.sha256(raw).hexdigest(), expected_source_sha=SOURCE,
                  archive_root=root, compose_files=((compose, hashlib.sha256(compose.read_bytes()).hexdigest()),),
                  docker=docker, check_lease=lease, environment="dev", stack=stack,
                  sleep=clock.sleep, monotonic=clock.monotonic)
    params.update(overrides)
    return artifacts.restore_images(raw, **params), clock


def test_stack_baseline_records_every_service_and_adds_no_archive(stack_case):
    root, docker, _, bundle, stack, _, baseline = stack_case
    assert set(stack["services"]) == set(docker.names) and len(stack["services"]) == STACK_SIZE
    assert stack["schema_version"] == artifacts.STACK_SCHEMA
    assert stack["services"][ONE_SHOT]["state"] == "completed"
    assert stack["services"]["source-ingest"]["env"]["PANTHEON_TENANT_ID"] == TENANT
    # The sealed bundle is unchanged: three services, three archives, original schema.
    assert bundle["schema_version"] == artifacts.SCHEMA and set(bundle["services"]) == set(SEALED)
    assert len(list(root.glob("*.tar"))) == len(bundle["archives"]) == 3
    assert not [call for call in docker.calls if call[:2] == ("image", "save")][3:]
    # Every recorded image is held under the tag the deploy prune step maintains.
    for service, row in stack["services"].items():
        assert docker.tags[f"pantheon-protect/{service}:{row['image_id'][7:19]}"] == row["image_id"]
    assert artifacts.validate_stack(stack, bundle=bundle) == stack


def test_whole_stack_success_restores_all_54_and_keeps_tenant(stack_case):
    _, docker, _, _, stack, _, baseline = stack_case
    docker.mutate_to_candidate(keep=("svc-03",))
    before_kept = dict(docker.containers["svc-03"])
    result, _ = restore_stack(stack_case)
    for service in docker.names:
        assert docker.containers[service]["image_id"] == baseline[service]["image_id"], service
    # Recorded tenant and GIT_SHA survive; Compose defaults never leak back in.
    assert docker.containers["source-ingest"]["env"]["PANTHEON_TENANT_ID"] == TENANT
    assert all(docker.containers[s]["env"] == baseline[s]["env"] for s in docker.names if s != "svc-03")
    recreated = {name for call in docker.up_calls for name in call["names"]}
    # Already on its recorded image: untouched, never recreated.
    assert "svc-03" not in recreated and docker.containers["svc-03"] == before_kept
    assert result["recreated_services"] == sorted(recreated)
    assert result["started_services"] == []
    assert docker.containers["candidate-only"]["status"] == "exited"
    assert result["stack"]["candidate_only_stopped"] == ["candidate-only"]
    assert set(result["stack"]["services"]) == set(docker.names)
    assert all(row["baseline_image_id"] == row["observed_image_id"] == stack["services"][s]["image_id"]
               for s, row in result["stack"]["services"].items())
    # One-shot init: exited 0 is a pass, and it is never restored with --wait.
    one_shot = next(call for call in docker.up_calls if ONE_SHOT in call["names"])
    assert one_shot["wait"] is False and docker.containers[ONE_SHOT]["status"] == "exited"
    # The sealed three keep their existing wait budget and agora healthcheck override.
    sealed = docker.up_calls[0]
    assert sealed["names"] == list(SEALED) and sealed["wait"] is True
    assert sealed["override"]["agora-interaction-worker"]["healthcheck"] == {"timeout": "30s"}
    assert sealed["override"]["operator-bff"]["environment"]["GIT_SHA"] == SOURCE
    ingest = next(call for call in docker.up_calls if "source-ingest" in call["names"])
    assert ingest["override"]["source-ingest"]["environment"]["PANTHEON_TENANT_ID"] == TENANT


def test_missing_recorded_image_is_named_and_nothing_is_mutated(stack_case):
    _, docker, _, _, stack, _, _ = stack_case
    docker.mutate_to_candidate()
    del docker.images[stack["services"]["svc-17"]["image_id"]]
    del docker.images[stack["services"]["svc-30"]["image_id"]]
    docker.calls.clear()
    with pytest.raises(artifacts.StackImageMissing) as error:
        restore_stack(stack_case)
    assert error.value.services == ("svc-17", "svc-30")
    assert "svc-17" in str(error.value) and "svc-30" in str(error.value)
    assert not [c for c in docker.calls if c[0] in ("compose", "stop", "start") or c[:2] == ("image", "load")]


def test_missing_image_at_capture_is_named(tmp_path):
    root = tmp_path / "a"
    root.mkdir(mode=0o700)
    docker = StackDocker()
    del docker.images[docker.containers["svc-05"]["image_id"]]
    with pytest.raises(artifacts.StackImageMissing) as error:
        artifacts.capture_stack(docker=docker, source_sha=SOURCE, check_lease=lambda: None)
    assert error.value.services == ("svc-05",)


def test_candidate_only_service_is_stopped_and_a_survivor_fails_the_proof(stack_case):
    _, docker, _, _, _, _, _ = stack_case
    docker.mutate_to_candidate()
    docker.no_stop.add("candidate-only")
    with pytest.raises(artifacts.StackMixedError) as error:
        restore_stack(stack_case)
    assert error.value.services == ("candidate-only",)
    assert str(error.value) == "stack is mixed: candidate-only"


def test_wrong_image_after_restore_fails_fast_naming_the_service(stack_case):
    _, docker, _, _, _, _, _ = stack_case
    docker.mutate_to_candidate()
    docker.stuck.update({"svc-09", "svc-10"})
    with pytest.raises(artifacts.StackMixedError) as error:
        restore_stack(stack_case)
    assert error.value.services == ("svc-09", "svc-10")


def test_unhealthy_service_fails_only_after_the_stated_budget(stack_case):
    _, docker, _, _, _, _, _ = stack_case
    docker.mutate_to_candidate()
    docker.unhealthy.add("svc-20")
    with pytest.raises(artifacts.StackMixedError) as error:
        _, _ = restore_stack(stack_case, readback_seconds=60)
    assert error.value.services == ("svc-20",)


def test_slow_service_that_becomes_healthy_within_budget_passes(stack_case):
    _, docker, _, _, stack, _, _ = stack_case
    docker.mutate_to_candidate()
    original = docker.call
    state = {"polls": 0}

    def slow(*args):
        if args[0] == "inspect" and args[2] == artifacts.STACK_FORMAT and docker.by_cid(args[-1])[0] == "source-ingest" and \
                docker.containers["source-ingest"]["image_id"] == stack["services"]["source-ingest"]["image_id"]:
            state["polls"] += 1
            if state["polls"] < 20:      # ~110 s at the 5 s poll interval
                docker.containers["source-ingest"]["health"] = "starting"
            else:
                docker.containers["source-ingest"]["health"] = "healthy"
        return original(*args)

    docker.call = slow
    _, clock = restore_stack(stack_case)
    assert 0 < clock.now <= artifacts.STACK_READBACK_SECONDS and clock.sleeps >= 15


def test_recreated_service_with_compose_default_environment_fails_the_proof(stack_case):
    _, docker, _, _, _, _, _ = stack_case
    docker.mutate_to_candidate()
    original = docker.call

    def reset_tenant(*args):
        result = original(*args)
        if args[0] == "compose":
            docker.containers["source-ingest"]["env"]["PANTHEON_TENANT_ID"] = "compose-default"
        return result

    docker.call = reset_tenant
    with pytest.raises(artifacts.StackMixedError) as error:
        restore_stack(stack_case)
    assert error.value.services == ("source-ingest",)


def test_stopped_service_on_recorded_image_is_started_not_recreated(stack_case):
    _, docker, _, _, _, _, _ = stack_case
    docker.mutate_to_candidate(keep=("svc-04",))
    docker.containers["svc-04"]["status"], docker.containers["svc-04"]["health"] = "exited", None
    result, _ = restore_stack(stack_case)
    assert docker.containers["svc-04"]["status"] == "running"
    assert not any("svc-04" in call["names"] for call in docker.up_calls)
    assert result["started_services"] == ["svc-04"]
    assert "svc-04" not in result["recreated_services"]


def test_three_image_restore_without_stack_leaves_the_rest_alone(stack_case):
    _, docker, _, _, _, _, _ = stack_case
    docker.mutate_to_candidate()
    others = {s: dict(r) for s, r in docker.containers.items() if s not in SEALED}
    root, _, lease, bundle, _, compose, _ = stack_case
    raw = artifacts.manifest_bytes(bundle)
    result = artifacts.restore_images(
        raw, expected_sha256=hashlib.sha256(raw).hexdigest(), expected_source_sha=SOURCE, archive_root=root,
        compose_files=((compose, hashlib.sha256(compose.read_bytes()).hexdigest()),),
        docker=docker, check_lease=lease, environment="dev")
    assert set(result) == {"image_readback_verified", "services"}
    assert [call["names"] for call in docker.up_calls] == [list(SEALED)]
    assert {s: r for s, r in docker.containers.items() if s not in SEALED} == others
    assert "environment" not in docker.up_calls[0]["override"]["operator-bff"]


@pytest.mark.parametrize("change", ["extra_field", "bad_image", "sealed_mismatch", "oneshot_health", "bad_env"])
def test_stack_validation_rejects_tampering(stack_case, change):
    _, _, _, bundle, stack, _, _ = stack_case
    value = copy.deepcopy(stack)
    if change == "extra_field":
        value["services"]["svc-01"]["extra"] = 1
    elif change == "bad_image":
        value["services"]["svc-01"]["image_id"] = "latest"
    elif change == "sealed_mismatch":
        value["services"]["operator-bff"]["image_id"] = _sid("d", 1)
    elif change == "oneshot_health":
        value["services"][ONE_SHOT]["has_healthcheck"] = True
    else:
        value["services"]["svc-01"]["env"]["EXTRA"] = "x"
    with pytest.raises(artifacts.ArtifactError):
        artifacts.validate_stack(value, bundle=bundle)


def test_unlabeled_third_party_services_are_recorded_and_restored_without_invented_env(stack_case):
    root, docker, lease, _, _, compose, _ = stack_case
    third_party = ("svc-10", ONE_SHOT)
    for service in third_party:
        docker.containers[service]["env"] = {}
        docker.compose_defaults[service] = {}
        docker.images[docker.containers[service]["image_id"]]["revision"] = None
    stack = artifacts.capture_stack(docker=docker, source_sha=SOURCE, check_lease=lease)
    for service in third_party:
        assert stack["services"][service]["env"] == {"PANTHEON_TENANT_ID": None, "GIT_SHA": None}
    artifacts.validate_stack(stack)
    case = (root, docker, lease, stack_case[3], stack, compose, None)
    docker.mutate_to_candidate()
    for service in third_party:
        docker.containers[service]["env"] = {}
    restore_stack(case)
    for service in third_party:
        assert docker.containers[service]["image_id"] == stack["services"][service]["image_id"]
        assert docker.containers[service]["env"] == {}
        for call in docker.up_calls:
            if service in call["override"]:
                assert "environment" not in call["override"][service]


def _docker_daemon():
    if not shutil.which("docker"):
        return None
    try:
        out = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def test_importing_module_makes_no_docker_call(tmp_path):
    stub = tmp_path / "bin" / "docker"
    stub.parent.mkdir()
    log = tmp_path / "calls.log"
    stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n')
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{stub.parent}:{os.environ['PATH']}")
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-c", "import scripts.test_dev_release_artifacts"],
                         cwd=root, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert not log.exists()


def test_image_format_against_real_docker_for_all_label_shapes(tmp_path):
    # Probe lazily: a module-level skipif would run `docker` at import time, and
    # other suites (restore chain) import this module with a stub docker on PATH.
    if _docker_daemon() is None:
        pytest.skip("no reachable Docker daemon")
    tag = "pantheon-imgfmt-test-" + os.urandom(4).hex()
    names = {"nolabels": f"{tag}:nolabels", "other": f"{tag}:other", "rev": f"{tag}:rev"}
    revision = "b" * 40
    try:
        empty = subprocess.run(["tar", "cf", "-", "--files-from", "/dev/null"], capture_output=True, check=True).stdout
        subprocess.run(["docker", "import", "-", names["nolabels"]], input=empty, capture_output=True, check=True)
        subprocess.run(["docker", "import", "--change", 'LABEL some.other="x"', "-", names["other"]],
                       input=empty, capture_output=True, check=True)
        subprocess.run(["docker", "import", "--change", f'LABEL org.opencontainers.image.revision="{revision}"',
                        "-", names["rev"]], input=empty, capture_output=True, check=True)
        rows = {}
        for key, name in names.items():
            raw = subprocess.run(["docker", "image", "inspect", "--format", artifacts.IMAGE_FORMAT, name],
                                 capture_output=True, text=True, check=True).stdout
            rows[key] = json.loads(raw)
        assert rows["nolabels"]["revision"] is None
        assert rows["other"]["revision"] is None
        assert rows["rev"]["revision"] == revision
        assert all(artifacts.IMAGE.fullmatch(row["id"]) for row in rows.values())
    finally:
        for name in names.values():
            subprocess.run(["docker", "image", "rm", "-f", name], capture_output=True)


def test_restore_repoints_every_compose_image_name_to_the_baseline(stack_case):
    _, docker, _, _, stack, _, _ = stack_case
    docker.mutate_to_candidate()
    assert all(docker.tags["pantheon-" + s].startswith("sha256:c") for s in docker.names if s != "grafana-like")
    restore_stack(stack_case)
    assert len(stack["services"]) == STACK_SIZE
    for service, row in stack["services"].items():
        assert docker.tags["pantheon-" + service] == row["image_id"], service
    assert any(call[:5] == ("compose", "-p", "pantheon", "--profile", "*") and "config" in call for call in docker.calls)
    # protect tags are unchanged in kind and nothing was pruned
    assert any(t.startswith("pantheon-protect/") for t in docker.tags)
    assert not any(call[:2] in (("image", "rm"), ("image", "prune")) for call in docker.calls)


def test_readback_names_service_whose_compose_image_still_resolves_to_candidate(stack_case):
    _, docker, _, _, stack, _, _ = stack_case
    docker.mutate_to_candidate()
    real = docker.call

    def call(*args):
        result = real(*args)
        if args[0] == "tag" and args[2] == "pantheon-svc-03":
            docker.tags["pantheon-svc-03"] = _sid("c", 3)   # tag did not stick
        return result

    docker.call = call
    with pytest.raises(artifacts.StackMixedError) as caught:
        restore_stack(stack_case)
    assert caught.value.services == ("svc-03",)
    assert "svc-03" in str(caught.value)


PINS = {"svc-01": "nats:2.11-alpine@sha256:" + "1" * 64, "svc-02": "postgres:16-alpine@sha256:" + "2" * 64,
        "svc-03": "redis:7-alpine@sha256:" + "3" * 64, "svc-04": "busybox:1.36@sha256:" + "4" * 64}


def pin_stack(docker):
    docker.pinned = dict(PINS)
    for service, ref in PINS.items():
        docker.tags[ref] = docker.containers[service]["image_id"]


def test_restore_skips_digest_pinned_refs_and_still_repoints_the_rest(stack_case):
    _, docker, _, _, stack, _, _ = stack_case
    pin_stack(docker)
    docker.mutate_to_candidate()
    for service, ref in PINS.items():
        docker.tags[ref] = stack["services"][service]["image_id"]  # a digest pin never drifts
    result, _ = restore_stack(stack_case)
    tagged = {call[2] for call in docker.calls if call[0] == "tag"}
    assert not tagged & set(PINS.values())
    assert all("pantheon-" + s in tagged for s in docker.names if s not in PINS)
    assert all(docker.tags["pantheon-" + s] == stack["services"][s]["image_id"] for s in docker.names if s not in PINS)
    assert set(result["stack"]["services"]) == set(stack["services"])


def test_digest_pinned_ref_not_resolving_to_recorded_image_fails_closed_naming_service(stack_case):
    _, docker, _, _, stack, _, _ = stack_case
    pin_stack(docker)
    docker.tags[PINS["svc-02"]] = _sid("c", 2)
    docker.add_image(_sid("c", 2))
    with pytest.raises(artifacts.StackMixedError) as caught:
        restore_stack(stack_case)
    assert caught.value.services == ("svc-02",)


def test_readback_checks_pinned_and_plain_refs_together(stack_case):
    _, docker, _, _, stack, _, _ = stack_case
    pin_stack(docker)
    refs = {**PINS, "svc-05": "pantheon-svc-05"}
    docker.tags["pantheon-svc-05"] = _sid("c", 5)
    docker.add_image(_sid("c", 5))
    with pytest.raises(artifacts.StackMixedError) as caught:
        artifacts.readback_stack(stack, docker=docker, check_lease=lambda: None, compose_refs=refs)
    assert caught.value.services == ("svc-05",)


@pytest.mark.parametrize("bad", ["nats@sha256:abc", "nats@sha512:" + "1" * 64, "nats@" + "1" * 64,
                                 "-nats@sha256:" + "1" * 64, "nats @sha256:" + "1" * 64, "@sha256:" + "1" * 64])
def test_malformed_compose_reference_still_fails_closed(stack_case, bad):
    _, docker, _, _, stack, _, _ = stack_case
    docker.pinned = {"svc-01": bad}
    with pytest.raises(artifacts.StackError) as caught:
        restore_stack(stack_case)
    assert "svc-01" in str(caught.value)


def test_restore_preserves_source_scheduler_and_api_posture_against_a_bounded_caller(stack_case):
    _, docker, _, _, stack, _, baseline = stack_case
    recorded = stack["services"][SCHEDULER]["control"]
    assert recorded["restart"] == "unless-stopped"
    # Absent stays null, empty stays empty; nothing outside the named keys (no token) is recorded.
    assert recorded["env"]["SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS"] is None
    assert recorded["env"]["SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS"] == ""
    assert set(stack["services"][API]["control"]["env"]) == set(artifacts.API_ENV_KEYS)
    docker.mutate_to_candidate()
    for service in (SCHEDULER, API, PAPER):
        docker.containers[service]["control_env"] = dict(CALLER[service])
        docker.containers[service].update(restart="no")
    docker.containers[SCHEDULER].update(status="exited", exit_code=1, health=None)
    result, _ = restore_stack(stack_case)
    for service in (SCHEDULER, API, PAPER):
        row = docker.containers[service]
        assert row["control_env"] == baseline[service]["control_env"] == STEADY[service]
        assert row["restart"] == "unless-stopped" and row["status"] == "running"
        assert service in result["recreated_services"]


def test_paper_reader_reference_is_recorded_without_any_credential(stack_case):
    _, docker, _, _, stack, _, _ = stack_case
    recorded = stack["services"][PAPER]["control"]["env"]
    assert recorded == STEADY_PAPER and set(recorded) == set(artifacts.PAPER_ENV_KEYS)
    assert "DEPLOYMENT_CAPITAL_SERVICE_TOKEN" not in recorded
    assert "DEPLOYMENT_CAPITAL_SERVICE_TOKEN=" not in json.dumps(stack)


def test_paper_producer_absent_reference_stays_absent(stack_case):
    stack = json.loads(json.dumps(stack_case[4]))
    stack["services"][PAPER]["control"]["env"]["DEPLOYMENT_CAPITAL_SERVICE_TOKEN_FILE"] = None
    assert artifacts.validate_stack(stack) is stack


def test_exited_scheduler_on_recorded_image_with_bounded_posture_is_recreated_not_started(stack_case):
    _, docker, _, _, stack, _, _ = stack_case
    docker.containers[SCHEDULER]["control_env"] = dict(BOUNDED_CALLER)
    docker.containers[SCHEDULER].update(status="exited", exit_code=1, health=None)
    result, _ = restore_stack(stack_case)
    assert SCHEDULER in result["recreated_services"] and SCHEDULER not in result["started_services"]
    assert docker.containers[SCHEDULER]["control_env"] == STEADY_CONTROL


def test_matching_scheduler_posture_is_left_alone(stack_case):
    result, _ = restore_stack(stack_case)
    assert SCHEDULER not in result["recreated_services"] and API not in result["recreated_services"]


@pytest.mark.parametrize("service", [SCHEDULER, API, PAPER])
def test_readback_fails_closed_on_source_posture_drift(stack_case, service):
    _, docker, _, _, stack, _, _ = stack_case
    docker.containers[service]["control_env"] = dict(CALLER[service])
    docker.stuck.add(service)
    with pytest.raises(artifacts.StackMixedError) as caught:
        restore_stack(stack_case)
    assert service in caught.value.services


@pytest.mark.parametrize("service", [SCHEDULER, API, PAPER])
def test_running_source_service_without_control_posture_is_invalid(stack_case, service):
    # No legacy shim: a capture lacking the required posture cannot prove restoration.
    stack = json.loads(json.dumps(stack_case[4]))
    del stack["services"][service]["control"]
    with pytest.raises(artifacts.ArtifactError):
        artifacts.validate_stack(stack)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI absent")
def test_real_compose_render_ignores_a_bounded_caller_for_every_recorded_key(stack_case, tmp_path):
    # Fake Docker cannot prove Compose semantics: render the real file with the real override.
    _, docker, _, _, stack, _, _ = stack_case
    docker.mutate_to_candidate()
    restore_stack(stack_case)
    text = next(c for c in docker.up_calls if SCHEDULER in c["names"])["text"]
    override = tmp_path / "override.yml"
    override.write_text(text)
    caller = {**BOUNDED_CALLER, **BOUNDED_API, "SOURCE_INGEST_CONTROLLER_RESTART_POLICY": "no"}
    repo = Path(__file__).resolve().parent.parent
    run = subprocess.run(["docker", "compose", "-f", str(repo / "docker-compose.yml"), "-f", str(override),
                          "--profile", "root", "config", "--format", "json"],
                         capture_output=True, text=True, env={**os.environ, **caller}, cwd=repo)
    if run.returncode:
        pytest.skip("docker compose cannot render the repository file in this environment")
    rendered = json.loads(run.stdout)["services"]
    for service in (SCHEDULER, API, PAPER):
        recorded = stack["services"][service]["control"]
        env = rendered[service]["environment"]
        for key, value in recorded["env"].items():
            assert (env.get(key) if value is not None else env.get(key, "<absent>")) == (
                value if value is not None else "<absent>"), key
        assert rendered[service]["restart"] == recorded["restart"]


def test_control_posture_validation_rejects_tampering(stack_case):
    stack = stack_case[4]
    for mutate in (lambda v: v["services"]["svc-01"].update(control={"env": {}, "restart": "no"}),
                   lambda v: v["services"][SCHEDULER]["control"].update(restart="sometimes"),
                   lambda v: v["services"][API]["control"]["env"].update(TOKEN="x"),
                   lambda v: v["services"][SCHEDULER]["control"]["env"].update(TOKEN="x")):
        value = json.loads(json.dumps(stack))
        mutate(value)
        with pytest.raises(artifacts.ArtifactError):
            artifacts.validate_stack(value)


def test_source_restore_rejects_exact_bee_before_any_owner_switch(stack_case):
    from services.source_ingestion.requirement_state import market_snapshot_writer_samples
    root, docker, _, _, stack, _, _ = stack_case
    samples = market_snapshot_writer_samples()
    retained = stack["services"][artifacts.API_SERVICE]["image_id"]
    docker.source_codecs[retained] = "bee62bc2978618539984aff9db136b064adc14a0"
    docker.source_journal.write_text(samples["official_daily"] * 4882 + samples["collector_close"])
    docker.mutate_to_candidate()
    before_data = docker.source_journal.read_bytes()
    before_owners = copy.deepcopy(docker.containers)
    docker.calls.clear()
    with pytest.raises(artifacts.SourceCompatibilityError, match="point_schema at journal line 4883") as caught:
        restore_stack(stack_case)
    assert docker.source_journal.read_bytes() == before_data
    assert docker.containers == before_owners
    assert not any(call[0] in ("compose", "stop", "start", "tag") for call in docker.calls)
    record = artifacts.failure_record(caught.value, "restore")
    assert record["source_compatibility"] == {"reason": "point_schema", "line": 4883}
    assert artifacts.remote_failure_record(json.dumps(record).encode()) == record
    assert str(root) not in json.dumps(record)


def test_source_writer_refused_before_extended_v1_can_poison_bee(stack_case):
    _, docker, _, _, stack, _, _ = stack_case
    retained = stack["services"][artifacts.API_SERVICE]["image_id"]
    docker.source_codecs[retained] = "bee62bc2978618539984aff9db136b064adc14a0"
    writer = _sid("f", 88)
    before = docker.source_journal.read_bytes()
    owners = copy.deepcopy(docker.containers)
    with pytest.raises(artifacts.SourceCompatibilityError):
        artifacts.qualify_source_writer(docker=docker, retained_image=retained,
                                         writer_image=writer, check_lease=lambda: None)
    assert docker.source_journal.read_bytes() == before
    assert docker.containers == owners
    assert not docker.up_calls
    probes = [call for call in docker.calls if call[0] == "run"]
    assert probes
    for call in probes:
        assert call[call.index("--network") + 1] == "none"
        assert "--read-only" in call and "--cap-drop" in call
        if call[-2] == "journal":
            assert call[call.index("--volumes-from") + 1].endswith(":ro")
        else:
            assert "--volumes-from" not in call  # synthetic writer never sees owner data


def test_compatible_retained_70a_and_current_writer_preserve_every_form(stack_case):
    from services.source_ingestion.requirement_state import market_snapshot_writer_samples
    _, docker, _, _, stack, _, _ = stack_case
    retained = stack["services"][artifacts.API_SERVICE]["image_id"]
    docker.source_codecs[retained] = "70a5bb860e855beacd14445c29bff64ccfa7e8f2"
    # Both readers must consume historical extended-v1 as well as the synthetic
    # writer protocol, using their actual complete codecs and checksums.
    docker.source_journal.write_text("".join(market_snapshot_writer_samples().values()))
    before = docker.source_journal.read_bytes()
    proof = artifacts.qualify_source_writer(docker=docker, retained_image=retained,
                                             writer_image=_sid("f", 88), check_lease=lambda: None)
    assert proof["retained"]["journal_sha256"] == hashlib.sha256(before).hexdigest()
    assert proof["retained"]["projection_sha256"] == proof["writer"]["projection_sha256"]
    assert docker.source_journal.read_bytes() == before
    assert not docker.up_calls


def test_source_capture_cannot_seal_an_already_incompatible_reader(stack_case):
    from services.source_ingestion.requirement_state import market_snapshot_writer_samples
    _, docker, _, _, stack, _, _ = stack_case
    retained = stack["services"][artifacts.API_SERVICE]["image_id"]
    docker.source_codecs[retained] = "bee62bc2978618539984aff9db136b064adc14a0"
    docker.source_journal.write_text(market_snapshot_writer_samples()["collector_close"])
    docker.calls.clear()
    with pytest.raises(artifacts.SourceCompatibilityError, match="point_schema"):
        artifacts.capture_stack(docker=docker, source_sha=SOURCE, check_lease=lambda: None)
    assert not any(call[0] in ("compose", "stop", "start", "tag") for call in docker.calls)


def test_exited_zero_initializer_image_still_fails_exact_stack_readback(stack_case):
    _, docker, _, _, stack, _, _ = stack_case
    expected = stack["services"][ONE_SHOT]
    assert expected["state"] == "completed"
    docker.containers[ONE_SHOT]["image_id"] = _sid("f", 88)
    with pytest.raises(artifacts.StackMixedError) as caught:
        artifacts.readback_stack(stack, docker=docker, check_lease=lambda: None)
    assert ONE_SHOT in caught.value.services
