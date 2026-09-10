"""Dev artifact primitives, deliberately not a standalone deployment command.

The controller must authenticate the outer admission, validate the environment
and Compose configuration, and keep these calls inside the pinned lease guard.
``check_lease`` is required at mutation boundaries; it is not a substitute for
that guard. No CLI, lease acquisition, FE switch, principal handling or DB code
is provided here. Docker diagnostics are not copied into public evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Callable


SERVICES = ("operator-bff", "agora-interaction-worker", "loop-run-projector-scheduler")
SCHEMA = "pantheon.dev-bff-image-bundle.v1"
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE_FORMAT = '{"id":{{json .Id}},"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}},"repo_digests":{{json .RepoDigests}}}'
CONTAINER_FORMAT = '{"image_id":{{json .Image}},"status":{{json .State.Status}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}}'


class ArtifactError(RuntimeError):
    pass


class Docker:
    def call(self, *args: str) -> str:
        # The eventual runner selects the approved VM. Never let an ambient
        # workstation Docker context redirect this primitive to another daemon.
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}}
        try:
            result = subprocess.run(
                ["docker", "--host", "unix:///var/run/docker.sock", *args],
                capture_output=True, text=True, timeout=300, check=True,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ArtifactError("Docker artifact operation failed") from exc
        return result.stdout


def _match(value, pattern, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ArtifactError(f"invalid {label}")
    return value


def _keys(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ArtifactError(f"invalid {label} fields")


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactError("duplicate JSON key")
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_no_duplicates)
    except (ValueError, UnicodeError) as exc:
        raise ArtifactError("invalid JSON") from exc


def manifest_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _directory(path: Path, *, private=False) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise ArtifactError("directory must be canonical and contain no symlinks")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ArtifactError("directory contains a symlink")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ArtifactError("directory is not a directory")
    if private and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise ArtifactError("artifact directory must be private and owned by this user")
    return path


def _file_digest(path: Path) -> tuple[str, int]:
    _directory(path.parent)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ArtifactError("artifact file unavailable") from exc
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ArtifactError("artifact must be a regular file")
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        current = path.lstat()
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise ArtifactError("artifact changed during read")
        return digest.hexdigest(), before.st_size


def _document_bytes(path: Path) -> bytes:
    _directory(path.parent)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ArtifactError("document unavailable") from exc
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ArtifactError("document must be a regular file")
        raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ArtifactError("document exceeds size bound")
        return raw


def _current(docker: Docker, service: str) -> dict:
    ids = docker.call("ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=pantheon",
                      "--filter", f"label=com.docker.compose.service={service}").split()
    if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
        raise ArtifactError(f"{service}: expected exactly one Compose container")
    row = _json(docker.call("inspect", "--format", CONTAINER_FORMAT, ids[0]))
    _keys(row, ("image_id", "status", "health"), "container")
    _match(row["image_id"], IMAGE, "container image ID")
    if row["status"] != "running" or row["health"] != "healthy":
        raise ArtifactError(f"{service}: container is not running and healthy")
    return row


def _image(docker: Docker, image_id: str) -> dict:
    row = _json(docker.call("image", "inspect", "--format", IMAGE_FORMAT, image_id))
    _keys(row, ("id", "revision", "repo_digests"), "image")
    if row["id"] != image_id:
        raise ArtifactError("image inspect ID mismatch")
    return row


def capture_images(*, docker: Docker, archive_root: Path, source_sha: str,
                   check_lease: Callable[[], None]) -> dict:
    """Capture observed IDs and authenticated archives; never invent RepoDigests.

    ``source_sha`` must already be bound to the served baseline by the caller.
    An absent OCI revision remains absent; it is not inferred from that SHA.
    """
    _match(source_sha, SHA, "source SHA")
    root = _directory(archive_root, private=True)
    check_lease()
    services, archives = {}, {}
    for service in SERVICES:
        image_id = _current(docker, service)["image_id"]
        image = _image(docker, image_id)
        revision = image["revision"]
        if revision not in (None, "", "unknown", source_sha):
            raise ArtifactError("observed OCI revision conflicts with baseline")
        services[service] = {"image_id": image_id, "oci_revision": revision,
                             "repo_digests": image["repo_digests"]}
    for image_id in sorted({row["image_id"] for row in services.values()}):
        with tempfile.TemporaryDirectory(prefix=".capture-", dir=root) as stage:
            temporary = Path(stage) / "image.tar"
            check_lease()
            docker.call("image", "save", "--output", str(temporary), image_id)
            digest, size = _file_digest(temporary)
            if size <= 0:
                raise ArtifactError("empty image archive")
            # docker save byte reproducibility is not an identity guarantee.
            # Retain independently hashed archives even for the same image ID.
            name = image_id.removeprefix("sha256:") + "-" + digest + ".tar"
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            target = root / name
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                if _file_digest(target) != (digest, size):
                    raise ArtifactError("existing image archive differs; refusing overwrite")
            archives[image_id] = {"name": name, "sha256": digest, "size": size}
    for service in SERVICES:
        if _current(docker, service)["image_id"] != services[service]["image_id"]:
            raise ArtifactError("container changed during artifact capture")
    check_lease()
    result = {"schema_version": SCHEMA, "source_sha": source_sha,
              "services": services, "archives": archives}
    raw = manifest_bytes(result)
    validate_images(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                    expected_source_sha=source_sha, archive_root=root)
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return result


def validate_images(raw: bytes, *, expected_sha256: str, expected_source_sha: str,
                    archive_root: Path) -> dict:
    """Verify a component manifest against a separately trusted outer digest."""
    _match(expected_sha256, DIGEST, "manifest digest")
    _match(expected_source_sha, SHA, "source SHA")
    if not isinstance(raw, bytes) or len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ArtifactError("manifest bytes do not match trusted digest")
    bundle = _json(raw)
    _keys(bundle, ("schema_version", "source_sha", "services", "archives"), "bundle")
    if bundle["schema_version"] != SCHEMA or bundle["source_sha"] != expected_source_sha:
        raise ArtifactError("bundle schema/source mismatch")
    _keys(bundle["services"], SERVICES, "services")
    root = _directory(archive_root, private=True)
    image_ids = set()
    for row in bundle["services"].values():
        _keys(row, ("image_id", "oci_revision", "repo_digests"), "service")
        image_ids.add(_match(row["image_id"], IMAGE, "image ID"))
        if row["oci_revision"] not in (None, "", "unknown", expected_source_sha):
            raise ArtifactError("invalid observed OCI revision")
        digests = row["repo_digests"]
        if digests is not None and (not isinstance(digests, list) or len(digests) > 32 or
                                   any(not isinstance(d, str) or not re.fullmatch(r"[a-zA-Z0-9._:/-]+@sha256:[0-9a-f]{64}", d) for d in digests)):
            raise ArtifactError("invalid observed RepoDigests")
    _keys(bundle["archives"], image_ids, "archives")
    for image_id, archive in bundle["archives"].items():
        _keys(archive, ("name", "sha256", "size"), "archive")
        _match(archive["sha256"], DIGEST, "archive digest")
        if archive["name"] != image_id.removeprefix("sha256:") + "-" + archive["sha256"] + ".tar":
            raise ArtifactError("archive must have its fixed image-ID and byte-digest basename")
        if type(archive["size"]) is not int or archive["size"] <= 0:
            raise ArtifactError("invalid archive size")
        if _file_digest(root / archive["name"]) != (archive["sha256"], archive["size"]):
            raise ArtifactError("archive bytes mismatch")
    return bundle


def restore_images(raw: bytes, *, expected_sha256: str, expected_source_sha: str,
                   archive_root: Path, compose_files: tuple[tuple[Path, str], ...],
                   docker: Docker, check_lease: Callable[[], None], environment: str) -> dict:
    """Restore ONLY three images. Caller owns authenticated admission/config/guard.

    No FE switch, issuer preparation, environment dump or fallback build/pull.
    This returns image readback, NOT whole-release compensation success.
    """
    if environment != "dev" or not compose_files:
        raise ArtifactError("artifact restore requires dev and trusted Compose files")
    check_lease()
    bundle = validate_images(raw, expected_sha256=expected_sha256,
                             expected_source_sha=expected_source_sha, archive_root=archive_root)
    for path, digest in compose_files:
        _match(digest, DIGEST, "Compose digest")
        if _file_digest(path)[0] != digest:
            raise ArtifactError("Compose configuration digest mismatch")
    for image_id, archive in bundle["archives"].items():
        try:
            _image(docker, image_id)
        except ArtifactError:
            check_lease()
            path = archive_root / archive["name"]
            if _file_digest(path) != (archive["sha256"], archive["size"]):
                raise ArtifactError("archive changed before load")
            docker.call("image", "load", "--input", str(path))
            _image(docker, image_id)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=archive_root) as stage:
        override = Path(stage) / "images.json"
        override.write_bytes(manifest_bytes({"services": {
            service: {"image": row["image_id"], "pull_policy": "never"}
            for service, row in bundle["services"].items()
        }}))
        command = ["compose", "-p", "pantheon"]
        for path, digest in compose_files:
            if _file_digest(path)[0] != digest:
                raise ArtifactError("Compose configuration changed before restore")
            command.extend(("-f", str(path)))
        command.extend(("-f", str(override), "up", "-d", "--no-build", "--pull", "never",
                        "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "120", *SERVICES))
        check_lease()
        docker.call(*command)
    check_lease()
    observed = {service: _current(docker, service)["image_id"] for service in SERVICES}
    if any(observed[service] != bundle["services"][service]["image_id"] for service in SERVICES):
        raise ArtifactError("restored image readback mismatch")
    check_lease()
    return {"image_readback_verified": True, "services": observed}


def frontend_dist_digest(root: Path) -> str:
    """FE canonicalAssetManifestBytes v1; deployment.json has a separate hash.

    This verifies bytes, not browser-secret scanning or source admission. The
    FE producer still owns those checks. No frontend source is copied here.
    """
    root = _directory(root)
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs:
            _directory(Path(directory) / name)
        for name in names:
            path = Path(directory) / name
            digest, size = _file_digest(path)
            relative = path.relative_to(root).as_posix()
            if relative == "deployment.json":
                continue
            if size > 2**53 - 1:
                raise ArtifactError("FE asset size exceeds canonical integer range")
            files.append({"path": relative, "sha256": digest, "size": size})
    files.sort(key=lambda row: row["path"].encode("utf-16-be"))
    canonical = json.dumps({"schemaVersion": 1, "files": files}, ensure_ascii=False,
                           separators=(",", ":")) + "\n"
    return hashlib.sha256(canonical.encode()).hexdigest()


def capture_frontend(*, release_store: Path, live_link: Path,
                     frontend_sha: str, backend_sha: str) -> dict:
    """Read-only exact target/assets/manifest capture; never performs a switch."""
    _match(frontend_sha, SHA, "frontend source SHA")
    _match(backend_sha, SHA, "backend source SHA")
    store = _directory(release_store)
    _directory(live_link.parent)
    if not live_link.is_symlink():
        raise ArtifactError("FE live path must be a symlink")
    target_text = os.readlink(live_link)
    target = Path(target_text)
    if not target.is_absolute() or target.parent != store:
        raise ArtifactError("FE target must be an immediate managed release")
    _directory(target)
    manifest = target / "deployment.json"
    before = _file_digest(manifest)
    raw = _document_bytes(manifest)
    if hashlib.sha256(raw).hexdigest() != before[0]:
        raise ArtifactError("FE manifest changed before parse")
    data = _json(raw)
    if (not isinstance(data, dict) or data.get("schemaVersion") != 1 or
        data.get("repository") != "ajoe734/execute-plans" or
        data.get("app") != "execute-plans" or data.get("sourceBranch") != "dev" or
        data.get("bffCommitEvidence") is not True or
        data.get("deploymentState") not in ("accepted", "standby")):
        raise ArtifactError("FE manifest is not a qualified release")
    for keys, expected in ((("frontendSha", "commit"), frontend_sha),
                           (("bffCommit", "bffSourceCommitSha"), backend_sha)):
        if not any(key in data for key in keys) or any(data[key] != expected for key in keys if key in data):
            raise ArtifactError("FE manifest source pair mismatch")
    for section, key, expected in (("frontend", "commitSha", frontend_sha), ("bff", "sourceCommitSha", backend_sha)):
        if section in data and (not isinstance(data[section], dict) or data[section].get(key) != expected):
            raise ArtifactError("nested FE manifest source pair mismatch")
    digest = frontend_dist_digest(target)
    claims = [data[key] for key in ("artifactDigestSha256", "artifactDigest") if key in data]
    if not claims or any(not isinstance(claim, str) or claim.removeprefix("sha256:") != digest for claim in claims):
        raise ArtifactError("FE manifest dist digest mismatch")
    if before != _file_digest(manifest) or os.readlink(live_link) != target_text:
        raise ArtifactError("FE release changed during capture")
    return {"target": target_text, "dist_sha256": digest, "manifest_sha256": before[0],
            "frontend_sha": frontend_sha, "backend_sha": backend_sha}


def verify_frontend(expected: dict, *, release_store: Path, live_link: Path) -> None:
    _keys(expected, ("target", "dist_sha256", "manifest_sha256", "frontend_sha", "backend_sha"), "FE baseline")
    current = capture_frontend(release_store=release_store, live_link=live_link,
                               frontend_sha=expected["frontend_sha"], backend_sha=expected["backend_sha"])
    if current != expected:
        raise ArtifactError("FE exact artifact/target mismatch")
