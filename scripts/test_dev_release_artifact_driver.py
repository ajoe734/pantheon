"""Isolated driver contracts: synthetic Docker/HTTP, real files/Git/FDs.

No live VM, container creation, real credential, or deployment is used here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import ssl
import sys
from types import SimpleNamespace
import uuid

import pytest

from scripts import dev_release_artifact_driver as d
from scripts.test_dev_release_artifacts import FakeDocker, IDS
from scripts import dev_release_artifacts as primitive


class Barrier:
    def __init__(self):
        self.calls = 0
        self.fail_at = None

    def check(self):
        self.calls += 1
        if self.fail_at is not None and self.calls >= self.fail_at:
            raise d.a.ArtifactError("fixture cancellation")


class Docker(FakeDocker):
    def __init__(self, source):
        super().__init__()
        for image in self.images.values():
            image["revision"] = source
        self.owners = {service: {"container_id": f"{index + 100:064x}", "image_id": IDS[0],
                                 "started_at": "2026-09-09T00:00:00Z", "restart_count": 0}
                       for index, service in enumerate(d.OWNERS)}
        self.config = dict(d.BASELINE_CONFIG)
        self.config_drift = self.owner_drift = False
        self.extra_config = ""
        self.built = {}
        self.candidate_compose = None
        self.http = None
        self.build_contract_drift = None

    def call(self, *args):
        if args[0] == "ps" and args[-1].rsplit("=", 1)[1] in d.OWNERS:
            self.calls.append(args)
            owner = self.owners[args[-1].rsplit("=", 1)[1]]
            return "" if owner is None else owner["container_id"]
        if args[0] == "inspect" and args[2] == d.OWNER_FORMAT:
            self.calls.append(args)
            if int(args[-1], 16) <= 3:
                service = d.a.SERVICES[int(args[-1], 16) - 1]
                return json.dumps({"container_id": args[-1], "image_id": self.containers[service]["image_id"],
                                   "started_at": "2026-09-09T00:00:00Z", "restart_count": 0})
            return json.dumps(next(row for row in self.owners.values() if row and row["container_id"] == args[-1]))
        if args[0] == "inspect" and args[2] == d.CONFIG_FORMAT:
            self.calls.append(args)
            return "\n".join(json.dumps(key + "=" + value) for key, value in self.config.items() if value is not None) + self.extra_config + "\n"
        if args[:2] == ("image", "inspect") and args[3] == d.BUILT_IMAGE_FORMAT:
            self.calls.append(args)
            service = args[-1].removeprefix("pantheon-")
            row = self.built[service]
            result = json.dumps({key: row[key] for key in ("image_id", "oci_revision")})
            if row["git_sha"] is not None: result += "\n" + json.dumps("GIT_SHA=" + row["git_sha"])
            return result + "\n\n"  # Template newline plus Docker formatter newline.
        if args[0] == "compose" and "config" in args:
            self.calls.append(args)
            if "--images" in args:
                return "postgres:16-alpine\npantheon-" + args[-1] + "\n"
            model = {"services": {service: {"build": {"context": str(self.candidate_compose.parent), "dockerfile": d.DOCKERFILES[service]}}
                                  for service in d.a.SERVICES}}
            row = model["services"][d.a.SERVICES[0]]
            if self.build_contract_drift == "explicit_image": row["image"] = "pantheon-operator-bff"
            elif self.build_contract_drift == "dockerfile": row["build"]["dockerfile"] = "services/governance/Dockerfile"
            elif self.build_contract_drift == "context": row["build"]["context"] = "/unrelated/source"
            return json.dumps(model)
        try:
            result = super().call(*args)
        except primitive.ArtifactError as error:
            raise d.a.ArtifactError(str(error)) from None
        if args[0] == "compose":
            if self.http is not None:
                self.http.source = os.environ["GIT_SHA"]
                if self.http.recover_on_restore: self.http.version_failure = None
            for key in self.config:
                # An absent baseline key models an older Compose file without
                # this field. Existing keys use the driver's injected values.
                if self.config[key] is not None:
                    self.config[key] = os.environ.get(key, "")
            if self.config_drift:
                self.config[next(iter(self.config))] = ""
            if self.owner_drift:
                self.owners["governance"]["restart_count"] += 1
        return result


class HTTP:
    def __init__(self, source, manifest):
        self.source, self.manifest = source, manifest
        self.calls = []
        self.fail = None
        self.version_failure = None
        self.recover_on_restore = False
        self.login = {"access_token": "fixture-private-access-token", "meta": {"identity": "viewer"}, "scope": "viewer"}
        self.me = {"data": {"roles": ["viewer"], "operator_id": "pantheon-dev-viewer", "tenant_id": "tenant-dev",
                            "user": {"roles": ["viewer"], "operator_id": "pantheon-dev-viewer"}, "tenant": {"id": "tenant-dev"},
                            "environment": {"name": "dev", "auth_mode": "strict", "strict_auth": True},
                            "session": {"authenticated": True, "session_kind": "bearer", "fresh": True}}}

    def request(self, method, url, *, headers=None, body=None):
        self.calls.append((method, url, headers, body))
        path = d.urllib.parse.urlsplit(url).path
        if path == "/bff/version" and self.version_failure is not None:
            if isinstance(self.version_failure, Exception): raise self.version_failure
            return self.version_failure
        if path == self.fail:
            return 500, b'{"secret":"fixture-error-body"}'
        if path == "/health": return 200, b"{}"
        if path == "/bff/version": return 200, json.dumps({"source_commit_sha": self.source, "config_posture": {"auth_stub": False, "auth_mode": "strict"}}).encode()
        if path == "/deployment.json": return 200, self.manifest.read_bytes()
        if path == "/bff/auth/dev-login": return 200, json.dumps(self.login).encode()
        if path == "/bff/me":
            return (200, json.dumps(self.me).encode()) if headers == {"Authorization": "Bearer fixture-private-access-token"} else (401, b"{}")
        raise AssertionError((method, url))


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    source = tmp_path / "baseline-source"
    source.mkdir()
    compose = source / "docker-compose.yml"
    compose.write_text("services: {}\n")
    def git(*args):
        return subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("add", "docker-compose.yml")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture baseline")
    source_sha = git("rev-parse", "HEAD")
    candidate_source = tmp_path / "candidate-source"
    git("worktree", "add", "--detach", str(candidate_source), source_sha)
    subprocess.run(["git", "-C", str(candidate_source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "fixture candidate"], check=True, capture_output=True)
    candidate_sha = subprocess.run(["git", "-C", str(candidate_source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    store = tmp_path / "releases"
    store.mkdir()
    release = store / "accepted-prior"
    release.mkdir()
    (release / "index.html").write_text("fixture exact FE")
    manifest = release / "deployment.json"
    manifest.write_text(json.dumps({"schemaVersion": 1, "repository": "ajoe734/execute-plans", "app": "execute-plans",
                                    "sourceBranch": "dev", "bffCommitEvidence": True, "deploymentState": "accepted",
                                    "frontendSha": "b" * 40, "bffCommit": source_sha,
                                    "artifactDigestSha256": d.a.frontend_dist_digest(release)}))
    link = tmp_path / "live"
    link.symlink_to(release)
    monkeypatch.setattr(d, "ROOT", root)
    monkeypatch.setattr(d, "FE_STORE", store)
    monkeypatch.setattr(d, "FE_LINK", link)
    monkeypatch.setattr(d.socket, "gethostname", lambda: d.VM)
    monkeypatch.setenv("PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID", str(uuid.uuid4()))
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID", "fixture-viewer")
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET", "fixture-private-viewer-secret")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    # Ensure mutations to these names made by the driver are reset per test.
    for key in (*d.BASELINE_CONFIG, "GIT_SHA", "PANTHEON_ENV", "COMPOSE_PROFILES"):
        monkeypatch.setenv(key, "fixture-ambient-value")
    args = SimpleNamespace(command="capture", artifact_root=root, environment="dev", project_id=d.PROJECT, vm=d.VM,
                           candidate_id="c" * 64, run_id="123456", attempt="2", controller_sha="d" * 40,
                           candidate_backend_sha=candidate_sha, candidate_frontend_sha="f" * 40,
                           previous_backend_sha=source_sha, previous_frontend_sha="b" * 40,
                           compose_file=compose, bff_url="http://127.0.0.1:8001", fe_url="http://127.0.0.1:8100",
                           fe_release_store=store, fe_live_link=link, manifest=None, manifest_sha256=None,
                           candidate_image_manifest=None, candidate_image_manifest_sha256=None)
    docker, http = Docker(source_sha), HTTP(source_sha, manifest)
    docker.http = http
    docker.candidate_compose = candidate_source / "docker-compose.yml"
    docker.built = {service: {"image_id": "sha256:" + str(index) * 64, "oci_revision": candidate_sha,
                             "git_sha": candidate_sha if service != "loop-run-projector-scheduler" else None,
                             "compose_image": "pantheon-" + service} for index, service in enumerate(d.a.SERVICES, 4)}
    docker.images.update({row["image_id"]: {"id": row["image_id"], "revision": candidate_sha, "repo_digests": None} for row in docker.built.values()})
    return SimpleNamespace(args=args, docker=docker, http=http, barrier=Barrier(), release=release, git=git)


def execute(case, operation=None):
    if operation: case.args.command = operation
    return d.run(case.args, docker=case.docker, http=case.http, barrier=case.barrier)


def seal(case, *, admit_candidate=True):
    result = execute(case)
    case.args.manifest = Path(result["manifest_path"])
    case.args.manifest_sha256 = result["manifest_sha256"]
    if admit_candidate:
        admit(case)
    case.docker.calls.clear()
    return result


def admit(case):
    previous = case.args.compose_file
    case.args.compose_file = case.docker.candidate_compose
    try:
        result = execute(case, "seal-candidate")
    finally:
        case.args.compose_file = previous
        case.args.command = "capture"
    case.args.candidate_image_manifest = Path(result["candidate_image_manifest_path"])
    case.args.candidate_image_manifest_sha256 = result["candidate_image_manifest_sha256"]
    return result


def no_replacement(case):
    assert not any((call[0] == "compose" and "up" in call) or call[:2] == ("image", "load") for call in case.docker.calls)


def test_candidate_producer_seals_built_images_before_rollout(case):
    baseline = seal(case, admit_candidate=False)
    receipt = admit(case)
    assert set(receipt) == {"candidate_image_manifest_path", "candidate_image_manifest_sha256", "candidate_image_manifest",
                            "candidate_image_override_path", "candidate_image_override_sha256"}
    record = receipt["candidate_image_manifest"]
    assert record["baseline_manifest_sha256"] == baseline["manifest_sha256"]
    assert record["identity"]["candidate_backend_sha"] == case.args.candidate_backend_sha
    assert record["candidate_compose_sha256"] == hashlib.sha256(case.docker.candidate_compose.read_bytes()).hexdigest()
    assert record["services"] == case.docker.built
    path = Path(receipt["candidate_image_manifest_path"])
    assert path.name == "candidate-images.json" and path.parent == case.args.manifest.parent
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["candidate_image_manifest_sha256"]
    override = Path(receipt["candidate_image_override_path"])
    assert override.name == "candidate-images.override.json"
    assert hashlib.sha256(override.read_bytes()).hexdigest() == receipt["candidate_image_override_sha256"]
    assert json.loads(override.read_bytes()) == {"services": {service: {"image": row["image_id"], "pull_policy": "never"}
                                                             for service, row in case.docker.built.items()}}
    assert "fixture-private" not in json.dumps(receipt)
    no_replacement(case)
    assert all(row["image_id"] == IDS[index] for index, row in enumerate(case.docker.containers.values()))


@pytest.mark.parametrize("change", ["explicit_image", "dockerfile", "context", "revision", "missing_bff_git", "conflicting_git", "already_replaced"])
def test_candidate_producer_fails_before_admitting_unqualified_images(case, change):
    seal(case, admit_candidate=False)
    if change in ("explicit_image", "dockerfile", "context"): case.docker.build_contract_drift = change
    elif change == "revision": case.docker.built[d.a.SERVICES[0]]["oci_revision"] = "0" * 40
    elif change == "missing_bff_git": case.docker.built[d.a.SERVICES[0]]["git_sha"] = None
    elif change == "conflicting_git": case.docker.built[d.a.SERVICES[0]]["git_sha"] = "0" * 40
    elif change == "already_replaced": case.docker.containers[d.a.SERVICES[1]]["image_id"] = case.docker.built[d.a.SERVICES[1]]["image_id"]
    with pytest.raises(d.a.ArtifactError): admit(case)
    assert not (case.args.manifest.parent / "candidate-images.json").exists()
    no_replacement(case)


def test_candidate_producer_cannot_rewrite_prior_admission(case):
    seal(case)
    before = case.args.candidate_image_manifest.read_bytes()
    with pytest.raises(d.a.ArtifactError, match="already sealed"): admit(case)
    assert case.args.candidate_image_manifest.read_bytes() == before


@pytest.mark.parametrize("change", ["missing_path", "missing_hash", "hash", "path", "baseline", "identity", "service", "source", "override", "bytes", "extra"])
def test_restore_requires_earlier_external_candidate_receipt_not_current_discovery(case, change):
    seal(case)
    path = case.args.candidate_image_manifest
    if change == "missing_path": case.args.candidate_image_manifest = None
    elif change == "missing_hash": case.args.candidate_image_manifest_sha256 = None
    elif change == "hash": case.args.candidate_image_manifest_sha256 = "0" * 64
    elif change == "path": case.args.candidate_image_manifest = path.with_name("other-candidate.json")
    elif change == "bytes": path.write_bytes(path.read_bytes() + b" ")
    elif change == "override": path.with_name("candidate-images.override.json").write_text('{"services":{}}')
    else:
        value = json.loads(path.read_bytes())
        if change == "baseline": value["baseline_manifest_sha256"] = "0" * 64
        elif change == "identity": value["identity"]["attempt"] = "99"
        elif change == "service": value["services"]["governance"] = value["services"][d.a.SERVICES[0]]
        elif change == "source": value["services"][d.a.SERVICES[0]]["oci_revision"] = "0" * 40
        elif change == "extra": value["arbitrary_observed_images"] = {}
        raw = d.a.manifest_bytes(value); path.write_bytes(raw)
        case.args.candidate_image_manifest_sha256 = hashlib.sha256(raw).hexdigest()
    with pytest.raises(d.a.ArtifactError): execute(case, "restore")
    no_replacement(case)


@pytest.mark.parametrize("service", d.a.SERVICES)
def test_same_source_unadmitted_current_image_never_gets_overwritten(case, service):
    seal(case)
    case.docker.containers[service]["image_id"] = "sha256:" + "9" * 64
    # Public source still equals the admitted prior source: insufficient proof.
    assert case.http.source == case.args.previous_backend_sha
    with pytest.raises(d.a.ArtifactError, match="neither admitted"): execute(case, "restore")
    no_replacement(case)


def test_partial_candidate_rollout_is_recoverable_only_with_bound_receipt(case):
    seal(case)
    case.docker.containers[d.a.SERVICES[1]]["image_id"] = case.docker.built[d.a.SERVICES[1]]["image_id"]
    result = execute(case, "restore")
    assert result["images"] == dict(zip(d.a.SERVICES, IDS))


@pytest.mark.parametrize("failure", ["wrong_source", "unavailable_source"])
def test_current_source_must_match_the_admitted_current_bff_image(case, failure):
    seal(case)
    if failure == "wrong_source": case.http.source = case.args.candidate_backend_sha  # BFF image is still prior.
    else: case.http.fail = "/bff/version"
    with pytest.raises(d.a.ArtifactError, match="current public BFF source"): execute(case, "restore")
    no_replacement(case)


@pytest.mark.parametrize("failure,observation", [
    ((502, b"bad gateway"), "unavailable_http_502"),
    ((503, b"service unavailable"), "unavailable_http_503"),
    ((504, b"gateway timeout"), "unavailable_http_504"),
    (d.urllib.error.URLError(ConnectionRefusedError("fixture-private-refusal")), "unavailable_connection_refused"),
    (ConnectionResetError("fixture-private-reset"), "unavailable_connection_reset"),
    (TimeoutError("fixture-private-timeout"), "unavailable_timeout"),
])
def test_exact_failed_candidate_can_restore_when_public_http_is_unavailable(case, failure, observation):
    seal(case)
    for service in d.a.SERVICES:
        case.docker.containers[service]["image_id"] = case.docker.built[service]["image_id"]
    case.http.version_failure = failure
    case.http.recover_on_restore = True
    result = execute(case, "restore")
    assert result["images"] == dict(zip(d.a.SERVICES, IDS))
    assert result["pre_restore_source_observations"] == [observation]
    assert result["public"]["source_sha"] == case.args.previous_backend_sha
    assert result["public"]["authenticated_viewer_readback_verified"] is True
    assert "fixture-private" not in json.dumps(result)


@pytest.mark.parametrize("failure", [
    (200, b"invalid JSON"), (200, b'{}'), (401, b"unauthorized"), (500, b"internal server error"),
    d.urllib.error.URLError(ssl.SSLCertVerificationError("fixture-untrusted-certificate")),
    d.urllib.error.URLError("untyped opaque network failure"),
])
def test_candidate_receipt_does_not_bypass_known_identity_or_tls_failures(case, failure):
    seal(case)
    for service in d.a.SERVICES:
        case.docker.containers[service]["image_id"] = case.docker.built[service]["image_id"]
    case.http.version_failure = failure
    with pytest.raises(d.a.ArtifactError): execute(case, "restore")
    no_replacement(case)


def test_unavailable_public_api_never_blesses_an_unadmitted_image(case):
    seal(case)
    case.docker.containers[d.a.SERVICES[0]]["image_id"] = "sha256:" + "9" * 64
    case.http.version_failure = (502, b"bad gateway")
    with pytest.raises(d.a.ArtifactError, match="neither admitted"): execute(case, "restore")
    no_replacement(case)


def test_postrestore_public_failure_still_prevents_success(case):
    seal(case)
    case.http.version_failure = (502, b"still unavailable")
    with pytest.raises(d.a.ArtifactError): execute(case, "restore")
    assert any(call[0] == "compose" and "up" in call for call in case.docker.calls)


def test_cas_rechecks_live_images_after_archive_load_before_container_replacement(case):
    seal(case)
    case.docker.images.clear()
    original = case.docker.call
    def drift(*args):
        result = original(*args)
        if args[:2] == ("image", "load"):
            case.docker.containers[d.a.SERVICES[0]]["image_id"] = "sha256:" + "9" * 64
        return result
    case.docker.call = drift
    with pytest.raises(d.a.ArtifactError, match="changed before"): execute(case, "restore")
    assert not any(call[0] == "compose" and "up" in call for call in case.docker.calls)


def test_capture_verify_and_sanitized_external_seal(case):
    captured = seal(case)
    manifest = captured["manifest"]
    assert manifest["identity"]["controller_sha"] == case.args.controller_sha
    assert manifest["identity"]["previous_backend_sha"] == case.args.previous_backend_sha
    assert manifest["baseline_nonsecret_config"] == d.BASELINE_CONFIG
    assert case.args.manifest.stat().st_mode & 0o777 == 0o600
    assert case.args.manifest_sha256 == hashlib.sha256(case.args.manifest.read_bytes()).hexdigest()
    assert (case.args.manifest.parent / "baseline-compose.yml").read_bytes() == case.args.compose_file.read_bytes()
    result = execute(case, "verify")
    assert result["protected_owners_unchanged"] and result["baseline_nonsecret_config_verified"]
    assert result["public"]["strict_auth_denials_verified"]
    assert result["public"]["authenticated_viewer_readback_verified"]
    assert "fixture-private" not in json.dumps((captured, result))
    assert "Config.Env" not in d.OWNER_FORMAT
    assert "{{json .Config.Env}}" not in str(case.docker.calls)
    no_replacement(case)


def test_same_source_different_images_requires_exact_restore(case):
    captured = seal(case)
    for row in case.docker.containers.values(): row["image_id"] = "sha256:" + "9" * 64
    with pytest.raises(d.a.ArtifactError, match="image readback differs"):
        execute(case, "verify")
    no_replacement(case)
    with pytest.raises(d.a.ArtifactError, match="neither admitted"):
        execute(case, "restore")
    no_replacement(case)
    for service, row in case.docker.containers.items(): row["image_id"] = case.docker.built[service]["image_id"]
    case.http.source = case.args.candidate_backend_sha
    case.docker.images.clear()
    result = execute(case, "restore")
    assert result["images"] == dict(zip(d.a.SERVICES, IDS))
    command = next(call for call in case.docker.calls if call[0] == "compose")
    assert command[-3:] == d.a.SERVICES
    assert "--no-build" in command and "--no-deps" in command
    assert command[command.index("--pull") + 1] == "never"
    assert "--build" not in command and "build" not in command
    assert case.docker.config == captured["manifest"]["baseline_nonsecret_config"]
    assert os.environ["COMPOSE_PROFILES"] == ""


@pytest.mark.parametrize("field", [*d.IDENTITY_FIELDS, "environment", "project_id", "vm", "artifact_root", "fe_live_link", "fe_release_store"])
def test_scope_mismatch_fails_before_mutation(case, field):
    seal(case)
    old = getattr(case.args, field)
    setattr(case.args, field, old / "different" if isinstance(old, Path) else ("0" if field in ("run_id", "attempt") else "0" * len(old)))
    with pytest.raises(d.a.ArtifactError): execute(case, "restore")
    no_replacement(case)


@pytest.mark.parametrize("failure", ["hash", "extra_field", "fe_source", "archive", "compose", "manifest_path", "symlink", "lease", "host", "strict_auth"])
def test_preconditions_reject_before_container_replacement(case, monkeypatch, failure):
    captured = seal(case)
    if failure == "hash": case.args.manifest_sha256 = "0" * 64
    elif failure in ("extra_field", "fe_source"):
        outer = captured["manifest"]
        if failure == "extra_field": outer["secret"] = "never accepted"
        else: outer["frontend"]["frontend_sha"] = "0" * 40
        raw = d.a.manifest_bytes(outer)
        case.args.manifest.write_bytes(raw)
        case.args.manifest_sha256 = hashlib.sha256(raw).hexdigest()
    elif failure == "archive": next((d.ROOT / "images").glob("*.tar")).write_bytes(b"tampered")
    elif failure == "compose": case.args.compose_file.write_bytes(b"services: {governance: {}}\n")
    elif failure == "manifest_path": case.args.manifest = case.args.manifest.parent / "elsewhere.json"
    elif failure == "symlink":
        original = case.args.manifest
        target = original.with_name("other.json")
        original.rename(target); original.symlink_to(target)
    elif failure == "lease": monkeypatch.delenv("PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID")
    elif failure == "host": monkeypatch.setattr(d.socket, "gethostname", lambda: "production")
    elif failure == "strict_auth": monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    with pytest.raises(d.a.ArtifactError): execute(case, "restore")
    no_replacement(case)


@pytest.mark.parametrize("failure", ["assets", "manifest_bytes", "target"])
def test_fe_owned_restoration_is_precondition_even_when_sources_equal(case, failure):
    seal(case)
    if failure == "assets": (case.release / "index.html").write_text("changed bytes")
    elif failure == "manifest_bytes":
        path = case.release / "deployment.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif failure == "target":
        import shutil
        alternate = case.release.with_name("same-source-other-target")
        shutil.copytree(case.release, alternate)
        case.args.fe_live_link.unlink(); case.args.fe_live_link.symlink_to(alternate)
    with pytest.raises(d.a.ArtifactError): execute(case, "restore")
    no_replacement(case)


@pytest.mark.parametrize("value", [None, ""])
def test_absent_or_empty_baseline_file_binding_never_creates_authority(case, value):
    key = "PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN_FILE"
    case.docker.config[key] = value
    seal(case)
    result = execute(case, "restore")
    assert result["baseline_nonsecret_config_verified"]
    assert case.docker.config[key] == value
    assert os.environ[key] == ""


@pytest.mark.parametrize("failure", ["path", "actor", "unexpected", "duplicate"])
def test_nonsecret_configuration_allowlist_fails_before_sealing(case, failure):
    if failure == "path": case.docker.config["PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN_FILE"] = "/unapproved/token"
    elif failure == "actor": case.docker.config["PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID"] = "operator-admin"
    elif failure == "unexpected": case.docker.extra_config = '\n"UNEXPECTED_SECRET=never-print"'
    else: case.docker.extra_config = '\n"PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID="'
    with pytest.raises(d.a.ArtifactError): execute(case)
    assert not list(d.ROOT.rglob("manifest.json"))
    no_replacement(case)


@pytest.mark.parametrize("failure", ["owner", "config", "public", "image"])
def test_failed_post_restore_readback_never_reports_success(case, failure):
    seal(case)
    if failure == "owner": case.docker.owner_drift = True
    elif failure == "config": case.docker.config_drift = True
    elif failure == "public": case.http.source = "0" * 40
    elif failure == "image": case.docker.drift = True
    with pytest.raises(d.a.ArtifactError): execute(case, "restore")


@pytest.mark.parametrize("change", ["login_role", "role", "user_role", "subject", "tenant", "tenant_object", "environment", "strict", "session", "missing"])
def test_http_200_is_not_enough_for_server_bound_viewer_readback(case, change):
    data = case.http.me["data"]
    if change == "login_role": case.http.login["meta"]["identity"] = "operator_a"
    elif change == "role": data["roles"] = ["viewer", "operator"]
    elif change == "user_role": data["user"]["roles"] = ["admin"]
    elif change == "subject": data["operator_id"] = "other-viewer"
    elif change == "tenant": data["tenant_id"] = "default"
    elif change == "tenant_object": data["tenant"]["id"] = "production"
    elif change == "environment": data["environment"]["name"] = "production"
    elif change == "strict": data["environment"]["strict_auth"] = False
    elif change == "session": data["session"]["session_kind"] = "stub"
    elif change == "missing": data.pop("user")
    with pytest.raises(d.a.ArtifactError): execute(case)
    assert not list(d.ROOT.rglob("manifest.json"))
    no_replacement(case)


def test_missing_optional_issuer_is_recorded_without_starting_it(case):
    case.docker.owners["dev-paper-principal-issuer"] = None
    seal(case)
    assert execute(case, "restore")["owners"]["dev-paper-principal-issuer"] is None


def test_baseline_compose_requires_actual_old_git_head_and_blob(case):
    path = case.args.compose_file
    assert d._compose_bytes(path, case.args.previous_backend_sha) == path.read_bytes()
    # Unrelated runtime state is permitted, but a HEAD mismatch is not.
    (path.parent / "runtime.fixture").write_text("unrelated live state")
    assert d._compose_bytes(path, case.args.previous_backend_sha) == path.read_bytes()
    case.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "other source")
    with pytest.raises(d.a.ArtifactError, match="baseline source blob"):
        d._compose_bytes(path, case.args.previous_backend_sha)


def test_sealed_capture_cannot_be_silently_recaptured(case):
    result = seal(case)
    before = case.args.manifest.read_bytes()
    with pytest.raises(d.a.ArtifactError, match="already sealed"): execute(case, "capture")
    assert case.args.manifest.read_bytes() == before
    assert hashlib.sha256(before).hexdigest() == result["manifest_sha256"]


def test_atomic_publish_is_private_immutable_and_rejects_symlink(tmp_path):
    tmp_path.chmod(0o700)
    target = tmp_path / "manifest.json"
    d._publish(target, b"exact bytes")
    d._publish(target, b"exact bytes")
    with pytest.raises(d.a.ArtifactError): d._publish(target, b"different")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(d.a.ArtifactError): d._publish(link, b"exact bytes")
    assert target.read_bytes() == b"exact bytes"
    assert target.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".seal-*"))


@pytest.mark.parametrize("url", ["http://production.invalid", "http://35.201.204.12", "http://localhost@production.invalid", "http://localhost/?token=secret", "http://api.dev.mvl-cap.tw", "file:///etc/passwd"])
def test_endpoint_boundary(url):
    with pytest.raises(d.a.ArtifactError): d._url(url, "bff")


def test_private_channel_requires_pulses_and_rejects_eof_and_staleness(monkeypatch):
    read_fd, write_fd = os.pipe()
    try:
        barrier = d.CancellationBarrier(read_fd, max_silence=1)
        os.write(write_fd, b"p")
        barrier.check()
        original = d.time.monotonic
        monkeypatch.setattr(d.time, "monotonic", lambda: original() + 2)
        with pytest.raises(d.a.ArtifactError, match="stale"): barrier.check()
        monkeypatch.setattr(d.time, "monotonic", original)
        os.close(write_fd); write_fd = None
        with pytest.raises(d.a.ArtifactError, match="closed"): barrier.check()
    finally:
        os.close(read_fd)
        if write_fd is not None: os.close(write_fd)


def test_queued_pulse_followed_by_eof_is_not_a_valid_heartbeat():
    read_fd, write_fd = os.pipe()
    try:
        barrier = d.CancellationBarrier(read_fd)
        os.write(write_fd, b"previously-queued-pulse")
        os.close(write_fd)
        with pytest.raises(d.a.ArtifactError, match="closed"): barrier.check()
    finally:
        os.close(read_fd)


def test_uuid_context_and_live_parent_do_not_replace_first_channel_pulse(monkeypatch):
    read_fd, write_fd = os.pipe()
    try:
        barrier = d.CancellationBarrier(read_fd, max_silence=1)
        original = d.time.monotonic
        monkeypatch.setattr(d.time, "monotonic", lambda: original() + 2)
        with pytest.raises(d.a.ArtifactError, match="never became ready"): barrier.check()
    finally:
        os.close(read_fd); os.close(write_fd)


def test_private_channel_rejects_parent_replacement(monkeypatch):
    read_fd, write_fd = os.pipe()
    try:
        barrier = d.CancellationBarrier(read_fd)
        os.write(write_fd, b"p")
        monkeypatch.setattr(d, "_parent_identity", lambda _pid: "reused-pid-new-starttime")
        with pytest.raises(d.a.ArtifactError, match="parent changed"): barrier.check()
    finally:
        os.close(read_fd); os.close(write_fd)


def test_private_channel_rejects_regular_file_and_stdio(tmp_path):
    with (tmp_path / "not-channel").open("wb") as stream:
        with pytest.raises(d.a.ArtifactError): d.CancellationBarrier(stream.fileno())
    with pytest.raises(d.a.ArtifactError): d.CancellationBarrier(0)


def test_guarded_child_is_cancelled_and_only_local_docker_host_used(tmp_path, monkeypatch):
    executable = tmp_path / "fake-docker"
    marker = tmp_path / "child.json"
    executable.write_text(f"#!{sys.executable}\nimport json,os,sys,time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(json.dumps({{'pid':os.getpid(),'pgid':os.getpgrp(),'args':sys.argv[1:],'remote':os.environ.get('DOCKER_HOST')}}))\ntime.sleep(30)\n")
    executable.chmod(0o700)
    monkeypatch.setenv("DOCKER_HOST", "tcp://unapproved.invalid:2375")
    barrier = Barrier()
    barrier.fail_at = 5
    with pytest.raises(d.a.ArtifactError, match="cancellation"):
        d.GuardedDocker(barrier, executable=str(executable)).call("image", "inspect", IDS[0])
    row = json.loads(marker.read_text())
    assert row["args"][:2] == ["--host", "unix:///var/run/docker.sock"]
    assert row["pgid"] == os.getpgrp(), "Docker CLI must remain in the watchdog-controlled process group"
    assert row["remote"] is None
    with pytest.raises(ProcessLookupError): os.kill(row["pid"], 0)


def test_cli_errors_are_sanitized_and_never_claim_completion(monkeypatch, capsys):
    def failure(_argv): raise RuntimeError("fixture-private-token subprocess-secret-error")
    monkeypatch.setattr(d, "parse_args", failure)
    assert d.main([]) == 75
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"status": "error", "error_code": "DEV_ARTIFACT_DRIVER_FAILED"}


def test_cancellation_before_capture_never_seals(case):
    case.barrier.fail_at = 1
    with pytest.raises(d.a.ArtifactError): execute(case)
    assert not list(d.ROOT.rglob("manifest.json"))
    assert not case.docker.calls
