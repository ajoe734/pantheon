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
import time
from typing import Callable


SERVICES = ("operator-bff", "agora-interaction-worker", "loop-run-projector-scheduler")
SCHEMA = "pantheon.dev-bff-image-bundle.v1"
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
PINNED_REF = re.compile(r"[^\s@:-][^\s@]*@sha256:[0-9a-f]{64}")
IMAGE_FORMAT = '{"id":{{json .Id}},"revision":{{with index .Config "Labels"}}{{json (index . "org.opencontainers.image.revision")}}{{else}}null{{end}},"repo_digests":{{json .RepoDigests}}}'
CONTAINER_FORMAT = '{"image_id":{{json .Image}},"status":{{json .State.Status}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}}'
BASELINE_PRINCIPAL_CONFIG = {
    "PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN_FILE": "/run/pantheon-principals/PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN",
    "PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID": "pantheon-dev-paper-provisioner",
    "PERSONA_EVALUATOR_BFF_TOKEN_FILE": "/run/pantheon-principals/PERSONA_EVALUATOR_BFF_TOKEN",
    "PERSONA_EVALUATOR_GOVERNANCE_TOKEN_FILE": "/run/pantheon-principals/PERSONA_EVALUATOR_GOVERNANCE_TOKEN",
}
# Historical auth configuration, not defaults for new candidate token issuance.
# Keep the exact strings (including absence/empty) in the existing sealed manifest.
BASELINE_AUTH_FLAGS = ("PANTHEON_BFF_MFA_REQUIRED", *(
    f"PANTHEON_BFF_DEV_LOGIN_{identity}_MFA_VERIFIED"
    for identity in ("OPERATOR", "VIEWER", "APPROVER", "RISK_OWNER", "OPERATOR_A", "OPERATOR_B")
))
BASELINE_CONFIG_KEYS = (*BASELINE_PRINCIPAL_CONFIG, *BASELINE_AUTH_FLAGS)

# Whole-stack compensation. The sealed three-image bundle above is unchanged;
# every other service is restored from the image the host already holds (its
# pantheon-protect/<service>:<id12> tag), so no archive is ever written for it.
STACK_SCHEMA = "pantheon.dev-stack-image-baseline.v1"
STACK_ENV_KEYS = ("PANTHEON_TENANT_ID", "GIT_SHA")
SERVICE_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")
STACK_MAX_SERVICES = 128
# Readback budget: recreated services start together, the slowest (source-ingest,
# ~110 s) must fit with room for a second attempt. Far inside the 60 minute step.
STACK_READBACK_SECONDS = 900
STACK_POLL_SECONDS = 5
STACK_FORMAT = (
    '{"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"oneoff":{{json (index .Config.Labels "com.docker.compose.oneoff")}},'
    '"image_id":{{json .Image}},"status":{{json .State.Status}},"exit_code":{{json .State.ExitCode}},'
    '"health_test":{{if .Config.Healthcheck}}{{json .Config.Healthcheck.Test}}{{else}}null{{end}},'
    '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}}'
)


def _env_format(keys) -> str:
    # Filtering happens in Docker's formatter: only the named nonsecret keys leave it.
    return ('{{range .Config.Env}}{{$v := split . "="}}{{if or ' + ' '.join(
        '(eq (index $v 0) "' + key + '")' for key in keys
    ) + '}}{{json .}}{{"\\n"}}{{end}}{{end}}')


STACK_ENV_FORMAT = _env_format(STACK_ENV_KEYS)
# Source controller posture. The scheduler's steady posture (reconcile-only, unbounded,
# no forced pull, restart unless-stopped) is otherwise re-derived from the restoring
# caller's variables; a bounded refresh would then pull externally and exit after one
# tick. Only these nonsecret names and the restart policy are recorded; no tokens.
CONTROL_SERVICE = "source-ingest-scheduler"
API_SERVICE = "source-ingest"
CONTROL_ENV_KEYS = (
    "SOURCE_INGEST_CONTROLLER_MODE", "SOURCE_INGEST_CONTROLLER_MAX_TICKS",
    "SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS", "SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS",
    "SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL",
)
CONTROL_ENV_FORMAT = _env_format(CONTROL_ENV_KEYS)
# The restored source-ingest API likewise inherits the caller's external egress posture
# (allowed hosts); record only those two nonsecret names.
API_ENV_KEYS = ("PANTHEON_EXTERNAL_EGRESS", "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS")
API_ENV_FORMAT = _env_format(API_ENV_KEYS)
# The paper signal producer's capital reader needs the capital URL, the expression
# layer switch and the path of the already mounted reader credential file. Only those
# nonsecret references are recorded; the credential value (and its env twin) never is.
PAPER_SERVICE = "paper-signal-producer"
PAPER_ENV_KEYS = ("PANTHEON_PAPER_EXPRESSION_LAYER", "PANTHEON_CAPITAL_API_URL",
                  "DEPLOYMENT_CAPITAL_SERVICE_TOKEN_FILE")
PAPER_ENV_FORMAT = _env_format(PAPER_ENV_KEYS)
CONTROL_POSTURE = {CONTROL_SERVICE: (CONTROL_ENV_KEYS, CONTROL_ENV_FORMAT),
                   API_SERVICE: (API_ENV_KEYS, API_ENV_FORMAT),
                   PAPER_SERVICE: (PAPER_ENV_KEYS, PAPER_ENV_FORMAT)}
RESTART_FORMAT = "{{json .HostConfig.RestartPolicy.Name}}"
RESTART_POLICIES = ("no", "always", "unless-stopped", "on-failure")


class ArtifactError(RuntimeError):
    pass


class StackError(ArtifactError):
    """A whole-stack failure that may name services (never exception text)."""

    def __init__(self, message: str, services=()):
        super().__init__(message)
        self.services = tuple(sorted(set(services)))


class StackImageMissing(StackError):
    pass


class StackMixedError(StackError):
    pass


FAILURE_CODE = "DEV_ARTIFACT_OPERATION_FAILED"
FAILURE_STAGES = frozenset({"initialize", "install", "transport", "capture", "verify",
                            "seal-candidate", "restore", "restore-final", "seal-result", "publish-evidence"})
FAILURE_KINDS = frozenset({"contract", "timeout", "subprocess", "filesystem", "invalid-data", "unexpected"})
FAILURE_SOURCE = re.compile(r"(?:capture_dev_artifact_baseline|dev_release_artifact_driver|dev_release_artifacts|dev_remote_guarded_exec)\.py:[1-9][0-9]{0,5}")


def failure_record(error: Exception, stage: str) -> dict:
    """Public failure metadata, never exception text, locals, argv or stderr.

    The last checked-in source line and the innermost explicit cause locate
    contract errors AND unexpected bugs without maintaining per-function stage
    enums or mutating the CLI namespace. This record is diagnostic, not proof.
    """
    location = "unknown"
    cause = error
    seen = set()
    while id(cause) not in seen:
        seen.add(id(cause))
        trace = cause.__traceback__
        while trace is not None:
            candidate = f"{Path(trace.tb_frame.f_code.co_filename).name}:{trace.tb_lineno}"
            if FAILURE_SOURCE.fullmatch(candidate):
                location = candidate
            trace = trace.tb_next
        if cause.__cause__ is None:
            break
        cause = cause.__cause__
    kind, code = "unexpected", 75
    if isinstance(cause, (TimeoutError, subprocess.TimeoutExpired)):
        kind, code = "timeout", 124
    elif isinstance(cause, subprocess.CalledProcessError):
        kind = "subprocess"
        status = cause.returncode
        if type(status) is int and 0 < abs(status) < 128:
            code = status if status > 0 else 128 - status
        elif type(status) is int and 128 <= status <= 255:
            code = status
    elif isinstance(cause, OSError):
        kind = "filesystem"
    elif isinstance(cause, ArtifactError):
        kind = "contract"
    elif isinstance(cause, (ValueError, TypeError, KeyError, AttributeError)):
        kind = "invalid-data"
    return {"status": "error", "error_code": FAILURE_CODE,
            "failure_stage": stage if isinstance(stage, str) and stage in FAILURE_STAGES else "initialize",
            "failure_kind": kind, "failure_location": location, "exit_code": code}


def remote_failure_record(raw: bytes) -> dict | None:
    """Accept only bounded, fixed-shape diagnostics; malformed stderr is ignored."""
    if not isinstance(raw, bytes) or len(raw) > 1024 * 1024:
        return None
    for line in reversed(raw.splitlines()):
        try:
            value = _json(line)
        except (ArtifactError, ValueError, TypeError, RecursionError):
            continue
        if not isinstance(value, dict) or set(value) != {
            "status", "error_code", "failure_stage", "failure_kind", "failure_location", "exit_code"
        }:
            continue
        stage, kind, location = (value[key] for key in ("failure_stage", "failure_kind", "failure_location"))
        if (value["status"] == "error" and value["error_code"] == FAILURE_CODE
                and isinstance(stage, str) and stage in FAILURE_STAGES
                and isinstance(kind, str) and kind in FAILURE_KINDS
                and isinstance(location, str) and (location == "unknown" or FAILURE_SOURCE.fullmatch(location))
                and type(value["exit_code"]) is int and 1 <= value["exit_code"] <= 255):
            return value
    return None


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


def validate_baseline_config(value):
    # Earlier two-field manifests cannot prove the prior auth settings. Refuse
    # them before candidate admission/restore; recapture while prior still runs.
    _keys(value, BASELINE_CONFIG_KEYS, "baseline nonsecret configuration (auth capture required)")
    if any(value[key] not in (None, "", expected) for key, expected in BASELINE_PRINCIPAL_CONFIG.items()):
        raise ArtifactError("baseline configuration is outside the fixed allowlist")
    for key in BASELINE_AUTH_FLAGS:
        setting = value[key]
        if setting is not None and (not isinstance(setting, str) or
                setting.lower() not in ("", "true", "false", "1", "0", "yes", "no", "on", "off")):
            raise ArtifactError("baseline auth configuration must be a bounded boolean string")
    return value


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


RESET = "\x00reset"


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


def _current(docker: Docker, service: str, *, require_healthy: bool = True) -> dict:
    ids = docker.call("ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=pantheon",
                      "--filter", f"label=com.docker.compose.service={service}").split()
    if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
        raise ArtifactError(f"{service}: expected exactly one Compose container")
    row = _json(docker.call("inspect", "--format", CONTAINER_FORMAT, ids[0]))
    _keys(row, ("image_id", "status", "health"), "container")
    _match(row["image_id"], IMAGE, "container image ID")
    if row["status"] != "running" or (require_healthy and row["health"] != "healthy"):
        raise ArtifactError(f"{service}: container is not running and healthy")
    return row


def _image(docker: Docker, image_id: str) -> dict:
    row = _json(docker.call("image", "inspect", "--format", IMAGE_FORMAT, image_id))
    _keys(row, ("id", "revision", "repo_digests"), "image")
    if row["id"] != image_id:
        raise ArtifactError("image inspect ID mismatch")
    return row


def save_image(*, docker: Docker, archive_root: Path, image_id: str,
               check_lease: Callable[[], None]) -> dict:
    root = _directory(archive_root, private=True)
    with tempfile.TemporaryDirectory(prefix=".capture-", dir=root) as stage:
        temporary = Path(stage) / "image.tar"
        check_lease()
        docker.call("image", "save", "--output", str(temporary), image_id)
        digest, size = _file_digest(temporary)
        if size <= 0:
            raise ArtifactError("empty image archive")
        name = image_id.removeprefix("sha256:") + "-" + digest + ".tar"
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        target = root / name
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            if _file_digest(target) != (digest, size):
                raise ArtifactError("existing image archive differs; refusing overwrite")
        return {"name": name, "sha256": digest, "size": size}


def capture_images(*, docker: Docker, archive_root: Path, source_sha: str,
                   check_lease: Callable[[], None]) -> dict:
    """Capture observed IDs and authenticated archives; never invent RepoDigests.

    ``source_sha`` must already be bound to the served baseline by the caller.
    An absent OCI revision remains absent; it is not inferred from that SHA.
    Preserve running predecessors even when unhealthy so a repair can deploy;
    artifact capture does not certify health. Restore/readback still require it.
    """
    _match(source_sha, SHA, "source SHA")
    root = _directory(archive_root, private=True)
    check_lease()
    services, archives = {}, {}
    for service in SERVICES:
        image_id = _current(docker, service, require_healthy=False)["image_id"]
        image = _image(docker, image_id)
        revision = image["revision"]
        valid_revisions = {None, "", "unknown", source_sha}
        if revision not in valid_revisions:
            raise ArtifactError("observed OCI revision conflicts with baseline")
        services[service] = {"image_id": image_id, "oci_revision": revision,
                             "repo_digests": image["repo_digests"]}
    for image_id in sorted({row["image_id"] for row in services.values()}):
        archives[image_id] = save_image(docker=docker, archive_root=root, image_id=image_id,
                                        check_lease=check_lease)
    for service in SERVICES:
        if _current(docker, service, require_healthy=False)["image_id"] != services[service]["image_id"]:
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
        valid_revisions = {None, "", "unknown", expected_source_sha}
        if row["oci_revision"] not in valid_revisions:
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


def _stack_rows(docker: Docker) -> dict[str, list[dict]]:
    """Every non-one-off Compose container of the project, by service."""
    ids = docker.call("ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=pantheon").split()
    rows: dict[str, list[dict]] = {}
    for container_id in ids:
        _match(container_id, re.compile(r"[0-9a-f]{12,64}"), "container ID")
        row = _json(docker.call("inspect", "--format", STACK_FORMAT, container_id))
        _keys(row, ("service", "oneoff", "image_id", "status", "exit_code", "health_test", "health"), "stack container")
        if row["oneoff"] == "True":
            continue
        _match(row["service"], SERVICE_NAME, "Compose service name")
        _match(row["image_id"], IMAGE, "container image ID")
        row["container_id"] = container_id
        rows.setdefault(row["service"], []).append(row)
    return rows


def _has_healthcheck(row: dict) -> bool:
    test = row["health_test"]
    return isinstance(test, list) and bool(test) and test[0] != "NONE"


def _stack_env(docker: Docker, container_id: str, keys=STACK_ENV_KEYS, fmt=STACK_ENV_FORMAT) -> dict:
    found = dict.fromkeys(keys)
    for line in docker.call("inspect", "--format", fmt, container_id).splitlines():
        if not line.strip():
            continue
        entry = _json(line)
        name, separator, value = entry.partition("=") if isinstance(entry, str) else ("", "", "")
        if not separator or name not in found:
            raise ArtifactError("unexpected container environment entry")
        found[name] = value
    return found


def _stack_control(docker: Docker, service: str, container_id: str) -> dict:
    """Source posture: nonsecret env (absent stays None, empty stays "") + restart policy."""
    keys, fmt = CONTROL_POSTURE[service]
    policy = _json(docker.call("inspect", "--format", RESTART_FORMAT, container_id)) or "no"
    return {"env": _stack_env(docker, container_id, keys, fmt), "restart": policy}


def _protect_tag(service: str, image_id: str) -> str:
    return f"pantheon-protect/{service}:{image_id.removeprefix('sha256:')[:12]}"


def capture_stack(*, docker: Docker, source_sha: str, check_lease: Callable[[], None]) -> dict:
    """Record the image of every running (or completed one-shot) Compose service.

    Nothing is archived: the host already holds each running image, and the
    deploy's builder-cache prune tags every running image as
    pantheon-protect/<service>:<id12> before it removes anything. The same tag
    is applied here (idempotent) so the image of every recorded service is held
    from capture time, and a missing image is reported by service name.
    """
    _match(source_sha, SHA, "source SHA")
    check_lease()
    first = _stack_rows(docker)
    services, missing = {}, []
    for service, containers in sorted(first.items()):
        if len(containers) != 1:
            raise StackError(f"{service}: expected exactly one Compose container", [service])
        row = containers[0]
        completed = row["status"] == "exited" and row["exit_code"] == 0 and not _has_healthcheck(row)
        if row["status"] != "running" and not completed:
            continue
        try:
            _image(docker, row["image_id"])
        except ArtifactError:
            missing.append(service)
            continue
        services[service] = {"image_id": row["image_id"], "state": "running" if row["status"] == "running" else "completed",
                             "has_healthcheck": _has_healthcheck(row),
                             "env": _stack_env(docker, row["container_id"])}
        if service in CONTROL_POSTURE and row["status"] == "running":
            services[service]["control"] = _stack_control(docker, service, row["container_id"])
    if missing:
        raise StackImageMissing("recorded stack image is missing", missing)
    if not services or len(services) > STACK_MAX_SERVICES:
        raise ArtifactError("stack baseline service count is out of bounds")
    for service, row in services.items():
        check_lease()
        try:
            docker.call("tag", row["image_id"], _protect_tag(service, row["image_id"]))
        except ArtifactError as error:
            raise StackError(f"{service}: failed to hold the recorded image", [service]) from error
    second = _stack_rows(docker)
    for service, row in services.items():
        again = second.get(service)
        if not again or len(again) != 1 or again[0]["image_id"] != row["image_id"] or \
                (again[0]["status"] == "running") != (row["state"] == "running"):
            raise StackError(f"{service}: container changed during stack capture", [service])
    if {s for s, c in second.items() if c[0]["status"] == "running"} != {s for s, r in services.items() if r["state"] == "running"}:
        raise ArtifactError("stack membership changed during capture")
    check_lease()
    return validate_stack({"schema_version": STACK_SCHEMA, "source_sha": source_sha, "services": services})


def validate_stack(value, *, bundle: dict | None = None, source_sha: str | None = None) -> dict:
    _keys(value, ("schema_version", "source_sha", "services"), "stack baseline")
    if value["schema_version"] != STACK_SCHEMA:
        raise ArtifactError("stack baseline schema mismatch")
    _match(value["source_sha"], SHA, "stack source SHA")
    if source_sha is not None and value["source_sha"] != source_sha:
        raise ArtifactError("stack baseline source mismatch")
    services = value["services"]
    if not isinstance(services, dict) or not 0 < len(services) <= STACK_MAX_SERVICES:
        raise ArtifactError("invalid stack baseline services")
    for service, row in services.items():
        _match(service, SERVICE_NAME, "stack service name")
        # A running source controller/API must carry its recorded posture; nothing else may.
        controlled = service in CONTROL_POSTURE and row.get("state") == "running"
        _keys(row, ("image_id", "state", "has_healthcheck", "env") + (("control",) if controlled else ()),
              "stack service")
        if controlled:
            control = row["control"]
            _keys(control, ("env", "restart"), "stack control posture")
            _keys(control["env"], CONTROL_POSTURE[service][0], "stack control environment")
            if control["restart"] not in RESTART_POLICIES or any(
                    item is not None and (not isinstance(item, str) or len(item) > 512 or "\0" in item)
                    for item in control["env"].values()):
                raise ArtifactError("invalid stack control posture")
        _match(row["image_id"], IMAGE, "stack image ID")
        if row["state"] not in ("running", "completed") or type(row["has_healthcheck"]) is not bool:
            raise ArtifactError("invalid stack service state")
        if row["state"] == "completed" and row["has_healthcheck"]:
            raise ArtifactError("a completed one-shot service cannot have a healthcheck")
        _keys(row["env"], STACK_ENV_KEYS, "stack service environment")
        if any(item is not None and (not isinstance(item, str) or len(item) > 512 or "\0" in item)
               for item in row["env"].values()):
            raise ArtifactError("invalid stack service environment")
    if bundle is not None:
        for service in SERVICES:
            if service not in services or services[service]["image_id"] != bundle["services"][service]["image_id"] \
                    or services[service]["state"] != "running":
                raise ArtifactError("stack baseline disagrees with the sealed image bundle")
    return value


def _stack_problems(stack: dict, current: dict[str, list[dict]]) -> tuple[dict[str, str], dict[str, str]]:
    """(hard, soft) problems by service. Hard ones cannot heal by waiting."""
    hard, soft = {}, {}
    for service, expected in stack["services"].items():
        containers = current.get(service, [])
        if len(containers) != 1:
            hard[service] = "container-missing" if not containers else "container-ambiguous"
            continue
        row = containers[0]
        if row["image_id"] != expected["image_id"]:
            hard[service] = "image-mismatch"
        elif expected["state"] == "completed":
            if row["status"] != "exited" or row["exit_code"] != 0:
                soft[service] = "one-shot-not-completed"
        elif row["status"] != "running":
            soft[service] = "not-running"
        elif expected["has_healthcheck"] and row["health"] != "healthy":
            soft[service] = "not-healthy"
    for service, containers in current.items():
        if service not in stack["services"] and any(c["status"] in ("running", "restarting", "paused") for c in containers):
            hard[service] = "candidate-only-still-running"
    return hard, soft


def compose_image_refs(docker: Docker, compose_files: tuple[tuple[Path, str], ...],
                       services) -> dict[str, str]:
    """Image reference Compose resolves per service WITHOUT any restore override.

    Only the first (base) Compose file is rendered: later files are the
    candidate image override, which must not decide where the names point.
    A digest-pinned reference ("name[:tag]@sha256:...") is returned unchanged:
    it cannot be re-tagged, so the restore skips tagging it and readback
    instead proves it resolves to the recorded image. Any other shape raises.
    """
    path, digest = compose_files[0]
    if _file_digest(path)[0] != digest:
        raise ArtifactError("Compose configuration changed before restore")
    model = _json(docker.call("compose", "-p", "pantheon", "--profile", "*", "-f", str(path), "config", "--format", "json").encode())
    declared = model.get("services") if isinstance(model, dict) else None
    if not isinstance(declared, dict):
        raise ArtifactError("Compose configuration has no services")
    refs = {}
    for service in sorted(services):
        row = declared.get(service)
        if not isinstance(row, dict):
            continue  # Compose cannot start an undeclared service; no name to re-point.
        image = row.get("image") or f"pantheon-{service}"
        if not isinstance(image, str) or not image or image.startswith("-") or any(c.isspace() for c in image):
            raise StackError(f"{service}: Compose image reference cannot be re-pointed", [service])
        if "@" in image and not PINNED_REF.fullmatch(image):
            raise StackError(f"{service}: Compose image reference cannot be re-pointed", [service])
        refs[service] = image
    return refs


def _resolved_image_id(docker: Docker, ref: str) -> str | None:
    try:
        return _json(docker.call("image", "inspect", "--format", IMAGE_FORMAT, ref).encode())["id"]
    except (ArtifactError, KeyError, TypeError):
        return None


def _mixed(problems: dict[str, str]) -> StackMixedError:
    return StackMixedError("stack is mixed: " + ", ".join(sorted(problems)), problems)


def readback_stack(stack: dict, *, docker: Docker, check_lease: Callable[[], None],
                   recreated=(), sleep: Callable[[float], None] = time.sleep,
                   monotonic: Callable[[], float] = time.monotonic,
                   budget_seconds: int = STACK_READBACK_SECONDS,
                   compose_refs: dict[str, str] | None = None) -> dict:
    """Prove each recorded service runs its recorded image, healthy within budget.

    A wrong image, missing container or surviving candidate-only service fails at
    once; run/health states may heal and are polled until ``budget_seconds``.
    """
    deadline = monotonic() + budget_seconds
    while True:
        check_lease()
        current = _stack_rows(docker)
        hard, soft = _stack_problems(stack, current)
        if hard:
            raise _mixed({**soft, **hard})
        if not soft:
            break
        if monotonic() >= deadline:
            raise _mixed(soft)
        sleep(STACK_POLL_SECONDS)
    if compose_refs:
        # A later "compose up" without an override must not start a candidate.
        stale = {}
        for service, ref in sorted(compose_refs.items()):
            check_lease()
            expected = stack["services"].get(service)
            if expected and _resolved_image_id(docker, ref) != expected["image_id"]:
                stale[service] = "compose-image-mismatch"
        if stale:
            raise _mixed(stale)
    drift = {}
    for service in sorted(set(recreated) & set(stack["services"])):
        check_lease()
        if _stack_env(docker, current[service][0]["container_id"]) != stack["services"][service]["env"]:
            drift[service] = "environment-mismatch"
    for service, expected in sorted(stack["services"].items()):
        if "control" in expected:
            check_lease()
            if _stack_control(docker, service, current[service][0]["container_id"]) != expected["control"]:
                drift[service] = "source-control-posture-mismatch"
    if drift:
        raise _mixed(drift)
    check_lease()
    return {"scope": "stack",
            "services": {service: {"baseline_image_id": row["image_id"],
                                   "observed_image_id": current[service][0]["image_id"]}
                         for service, row in sorted(stack["services"].items())},
            "candidate_only_stopped": sorted(s for s in current if s not in stack["services"])}


def restore_images(raw: bytes, *, expected_sha256: str, expected_source_sha: str,
                   archive_root: Path, compose_files: tuple[tuple[Path, str], ...],
                   docker: Docker, check_lease: Callable[[], None], environment: str,
                   stack: dict | None = None, sleep: Callable[[float], None] = time.sleep,
                   monotonic: Callable[[], float] = time.monotonic,
                   readback_seconds: int = STACK_READBACK_SECONDS) -> dict:
    """Restore the sealed three images; with ``stack`` also every other service.

    Caller owns authenticated admission/config/guard. No FE switch, issuer
    preparation, environment dump or fallback build/pull. Without ``stack`` this
    returns image readback for the three services only, NOT whole-release
    compensation success. With ``stack`` it returns a whole-stack proof or raises.
    The three sealed services are always recreated (their configuration is
    restored from the seal); any other recorded service already on its recorded
    image is left alone.
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
    rest, held_ids = [], set(bundle["archives"])
    if stack is not None:
        validate_stack(stack, bundle=bundle, source_sha=expected_source_sha)
        rest = sorted(service for service in stack["services"] if service not in SERVICES)
        # Prove every held image BEFORE the first mutation, naming each gap.
        missing = []
        for service in rest:
            image_id = stack["services"][service]["image_id"]
            if image_id in held_ids:
                continue
            try:
                _image(docker, image_id)
            except ArtifactError:
                missing.append(service)
        if missing:
            raise StackImageMissing("recorded stack image is missing: " + ", ".join(sorted(missing)), missing)
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

    def pinned(service: str) -> dict:
        # A recreated container must keep the tenant and GIT_SHA it ran with at
        # baseline; Compose defaults reset PANTHEON_TENANT_ID (source-ingest).
        env = {key: value for key, value in stack["services"][service]["env"].items() if value is not None}
        row = stack["services"][service]
        out = {"environment": env} if env else {}
        if "control" in row:
            # Absent -> "!reset" removes the key from the merged service (a plain null would
            # let Compose resolve it from the caller's environment); empty stays empty.
            out["environment"] = {**env, **{k: (RESET if v is None else v) for k, v in row["control"]["env"].items()}}
            out["restart"] = row["control"]["restart"]
        return out

    def compose_up(stage: str, services: dict[str, dict], tail: tuple[str, ...], names: list[str]) -> None:
        override = Path(stage) / "images.json"
        # JSON is valid YAML; the sentinel becomes Compose's "!reset null" tag.
        text = manifest_bytes({"services": services}).decode().replace(json.dumps(RESET), "!reset null")
        override.write_bytes(text.encode())
        command = ["compose", "-p", "pantheon"]
        for path, digest in compose_files:
            if _file_digest(path)[0] != digest:
                raise ArtifactError("Compose configuration changed before restore")
            command.extend(("-f", str(path)))
        command.extend(("-f", str(override), "up", "-d", "--no-build", "--pull", "never", *tail, *names))
        check_lease()
        docker.call(*command)
        override.unlink()

    recreated, started = [], []
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=archive_root) as stage:
        compose_up(stage, {
            service: {
                "image": row["image_id"], "pull_policy": "never",
                # Retained worker images import the application in their
                # original healthcheck (measured ~6s on dev). Keep that exact
                # check and require healthy, with a bounded execution budget.
                **({"healthcheck": {"timeout": "30s"}}
                   if service == "agora-interaction-worker" else {}),
                **(pinned(service) if stack is not None else {}),
            }
            for service, row in bundle["services"].items()
        }, ("--no-deps", "--force-recreate", "--wait", "--wait-timeout", "120"), list(SERVICES))
        if stack is not None:
            recreated.extend(SERVICES)
            current = _stack_rows(docker)
            for service, containers in sorted(current.items()):
                if service in stack["services"]:
                    continue
                for row in containers:
                    if row["status"] in ("running", "restarting", "paused"):
                        check_lease()
                        docker.call("stop", row["container_id"])
            wanted = {"running": [], "completed": []}
            for service in rest:
                expected = stack["services"][service]
                containers = current.get(service, [])
                if len(containers) == 1 and containers[0]["image_id"] == expected["image_id"] and \
                        ("control" not in expected or
                         _stack_control(docker, service, containers[0]["container_id"]) == expected["control"]):
                    if expected["state"] == "running" and containers[0]["status"] != "running":
                        check_lease()
                        docker.call("start", containers[0]["container_id"])
                        started.append(service)
                    continue
                wanted[expected["state"]].append(service)
            # One-shot init containers exit 0 by design, so no group uses
            # --wait; readback requires "running and healthy" or "exited 0".
            for state in ("running", "completed"):
                if wanted[state]:
                    compose_up(stage, {service: {"image": stack["services"][service]["image_id"],
                                                 "pull_policy": "never", **pinned(service)}
                                       for service in wanted[state]},
                               ("--no-deps", "--force-recreate"), wanted[state])
                    recreated.extend(wanted[state])
    check_lease()
    observed = {service: _current(docker, service)["image_id"] for service in SERVICES}
    if any(observed[service] != bundle["services"][service]["image_id"] for service in SERVICES):
        raise ArtifactError("restored image readback mismatch")
    # Point each Compose image name back at the restored image so a later plain
    # "compose up" cannot start the rejected candidate. Protect tags are kept.
    restored_ids = ({service: row["image_id"] for service, row in stack["services"].items()}
                    if stack is not None else
                    {service: bundle["services"][service]["image_id"] for service in SERVICES})
    refs = compose_image_refs(docker, compose_files, restored_ids)
    for service, ref in refs.items():
        if "@" in ref:
            continue  # digest pin: cannot be re-tagged; readback verifies it resolves to the recorded image
        check_lease()
        try:
            docker.call("tag", restored_ids[service], ref)
        except ArtifactError as error:
            raise StackError(f"{service}: failed to re-point the Compose image name", [service]) from error
    result = {"image_readback_verified": True, "services": observed}
    if stack is not None:
        result["stack"] = readback_stack(stack, docker=docker, check_lease=check_lease,
                                         recreated=recreated, sleep=sleep, monotonic=monotonic,
                                         budget_seconds=readback_seconds, compose_refs=refs)
        result["recreated_services"] = sorted(recreated)
        result["started_services"] = sorted(started)
    check_lease()
    return result


def frontend_dist_digest(root: Path) -> str:
    """FE canonicalAssetManifestBytes v1; deployment.json has a separate hash.

    This verifies bytes, not browser-secret scanning or source admission. The
    FE producer still owns those checks. Its prepared receipt is deployment
    metadata, excluded from the asset digest. No frontend source is copied here.
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
            if relative in ("deployment.json", ".prepared-receipt.json"):
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
