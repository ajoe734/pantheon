"""VM-side dev artifact driver. Only invoke inside the existing pinned guard.

The inherited private heartbeat FD is a cancellation channel, NOT a lease or
credential. The runner owns lease verification and must keep that channel open
and pulsing for the complete SSH command. Parent PID checks alone are not proof
of containment. No GitHub token, lease acquisition or FE switching lives here.

Compose secrets come only from the caller's approved process environment. This
driver filters only the runtime path/actor and baked GIT_SHA from Config.Env.
No environment dump or expanded Compose configuration is retained or emitted.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Stable sibling transport: importing does not depend on a source checkout or
# PYTHONPATH. The runner authenticates BOTH files' bytes before invoking us.
_spec = importlib.util.spec_from_file_location("_dev_artifact_primitive", Path(__file__).with_name("dev_release_artifacts.py"))
a = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(a)

ROOT = Path("/home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/release-artifacts")
PROJECT = "pantheon-dev-20260902"
VM = "pantheon-dev-deploy"
FE_STORE = Path("/var/www/pantheon-dev-fe-releases")
FE_LINK = Path("/var/www/pantheon-dev-fe")
OUTER_SCHEMA = "pantheon.dev-release-artifact-baseline.v1"
CANDIDATE_SCHEMA = "pantheon.dev-candidate-image-admission.v1"
DOCKERFILES = dict(zip(a.SERVICES, ("services/control-plane/bff/Dockerfile", "services/control-plane/bff/Dockerfile", "services/telemetry/Dockerfile")))
OWNERS = ("governance", "registry", "deployment", "runtime-manager", "deployment-outbox-consumer", "capital", "dev-paper-principal-issuer")
IDENTITY_FIELDS = ("candidate_id", "run_id", "attempt", "controller_sha", "candidate_backend_sha",
                   "candidate_frontend_sha", "previous_backend_sha", "previous_frontend_sha")
OWNER_FORMAT = '{"container_id":{{json .Id}},"image_id":{{json .Image}},"started_at":{{json .State.StartedAt}},"restart_count":{{json .RestartCount}}}'
BASELINE_CONFIG = {
    "PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN_FILE": "/run/pantheon-principals/PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN",
    "PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID": "pantheon-dev-paper-provisioner",
}
# Filtering happens inside Docker's formatter. No unrelated entry crosses the
# subprocess boundary; unexpected values in these two fields fail without echo.
CONFIG_FORMAT = '{{range .Config.Env}}{{$v := split . "="}}{{if or ' + ' '.join(
    '(eq (index $v 0) "' + key + '")' for key in BASELINE_CONFIG
) + '}}{{json .}}{{"\\n"}}{{end}}{{end}}'
BUILT_IMAGE_FORMAT = ('{"image_id":{{json .Id}},"oci_revision":{{if index .Config "Labels"}}'
                      '{{json (index (index .Config "Labels") "org.opencontainers.image.revision")}}{{else}}null{{end}}}'
                      '{{"\\n"}}{{range (index .Config "Env")}}{{$v := split . "="}}{{if eq (index $v 0) "GIT_SHA"}}{{json .}}{{"\\n"}}{{end}}{{end}}')


def _parent_identity(pid):
    # comm may contain spaces and parentheses; fields after its final ')' have
    # stable offsets (starttime is field 22, index 19 after comm).
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        raise a.ArtifactError("guarded parent unavailable") from None


class CancellationBarrier:
    def __init__(self, channel_fd: int, *, max_silence: float = 10):
        if channel_fd < 3 or not 1 <= max_silence <= 30:
            raise a.ArtifactError("invalid private guard channel")
        info = os.fstat(channel_fd)
        if not (stat.S_ISFIFO(info.st_mode) or stat.S_ISSOCK(info.st_mode)):
            raise a.ArtifactError("guard channel must be an inherited pipe or socket")
        self.fd = channel_fd
        os.set_blocking(channel_fd, False)
        self.parent = os.getppid()
        self.parent_start = _parent_identity(self.parent)
        self.max_silence = max_silence
        self.last_pulse = None
        self.started = time.monotonic()

    def check(self):
        while True:
            if os.getppid() != self.parent or _parent_identity(self.parent) != self.parent_start:
                raise a.ArtifactError("guarded parent changed")
            # Bound draining so a flooding channel cannot starve cancellation.
            for _ in range(16):
                readable, _, _ = select.select([self.fd], [], [], 0)
                if not readable:
                    break
                chunk = os.read(self.fd, 4096)
                if not chunk:
                    raise a.ArtifactError("guard cancellation channel closed")
                self.last_pulse = time.monotonic()
            else:
                if select.select([self.fd], [], [], 0)[0]:
                    raise a.ArtifactError("guard channel exceeded heartbeat bound")
            now = time.monotonic()
            if self.last_pulse is not None:
                if now - self.last_pulse > self.max_silence:
                    raise a.ArtifactError("guard cancellation channel stale")
                return
            # No artifact mutation before the first runner-held pulse.
            if now - self.started >= self.max_silence:
                raise a.ArtifactError("guard channel never became ready")
            select.select([self.fd], [], [], .1)


class GuardedDocker(a.Docker):
    def __init__(self, barrier, *, executable="docker"):
        self.barrier = barrier
        self.executable = executable

    def call(self, *args):
        self.barrier.check()
        environment = {k: v for k, v in os.environ.items() if k not in
                       {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}}
        command = [self.executable, "--host", "unix:///var/run/docker.sock", *args]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=environment) as process:
            deadline = time.monotonic() + 300
            try:
                while True:
                    self.barrier.check()
                    if time.monotonic() > deadline:
                        raise a.ArtifactError("Docker artifact operation timed out")
                    try:
                        output, _ = process.communicate(timeout=.1)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                self.barrier.check()
                if process.returncode:
                    raise a.ArtifactError("Docker artifact operation failed")
                return output
            except BaseException:
                # Stay in the remote watchdog's PGID so its STOP/TERM contains
                # the Docker CLI too. Locally terminate only our exact child;
                # the outer watchdog owns cleanup of remaining descendants.
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                raise


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise a.ArtifactError("public probe redirect is forbidden")


class HTTP:
    def request(self, method, url, *, headers=None, body=None):
        request = urllib.request.Request(url, method=method,
                                        data=None if body is None else json.dumps(body).encode(),
                                        headers={"Accept-Encoding": "identity", "Content-Type": "application/json", **(headers or {})})
        try:
            response = urllib.request.build_opener(_NoRedirect()).open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise a.ArtifactError("public probe response exceeds bound")
            return response.code, raw


def _url(value, kind):
    parsed = urllib.parse.urlsplit(value)
    allowed = {"127.0.0.1", "localhost", "34.81.52.222", "api.dev.mvl-cap.tw" if kind == "bff" else "app.dev.mvl-cap.tw"}
    if (parsed.hostname not in allowed or parsed.scheme not in {"http", "https"} or
        parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise a.ArtifactError("public probe endpoint is outside the fixed dev boundary")
    if parsed.hostname.endswith("mvl-cap.tw") and parsed.scheme != "https":
        raise a.ArtifactError("public dev hostname requires TLS")
    return value.rstrip("/")


def _identity(args):
    for name in ("controller_sha", "candidate_backend_sha", "candidate_frontend_sha", "previous_backend_sha", "previous_frontend_sha"):
        a._match(getattr(args, name), a.SHA, name)
    a._match(args.candidate_id, a.DIGEST, "candidate ID")
    if not re.fullmatch(r"[0-9]{1,20}", args.run_id) or not re.fullmatch(r"[0-9]{1,10}", args.attempt):
        raise a.ArtifactError("invalid run/attempt")
    if args.environment != "dev" or args.project_id != PROJECT or args.vm != VM or socket.gethostname() != VM:
        raise a.ArtifactError("artifact driver is restricted to the approved dev VM")
    if args.artifact_root != ROOT:
        raise a.ArtifactError("artifact root differs from fixed private dev store")
    a._directory(ROOT, private=True)
    _url(args.bff_url, "bff"); _url(args.fe_url, "fe")
    if args.fe_release_store != FE_STORE or args.fe_live_link != FE_LINK:
        raise a.ArtifactError("FE paths differ from the managed dev paths")
    try:
        lease_id = str(uuid.UUID(os.environ["PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID"]))
    except (ValueError, KeyError):
        raise a.ArtifactError("missing guard context ID") from None
    return {name: getattr(args, name) for name in IDENTITY_FIELDS}, lease_id


def _publish(path, raw):
    a._directory(path.parent, private=True)
    with tempfile.TemporaryDirectory(prefix=".seal-", dir=path.parent) as stage:
        temporary = Path(stage) / "document"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if a._document_bytes(path) != raw:
                raise a.ArtifactError("retained file already exists with different bytes")
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def _compose_bytes(path, source_sha):
    if path.name != "docker-compose.yml":
        raise a.ArtifactError("restore requires the baseline root Compose file")
    a._directory(path.parent)
    try:
        head = subprocess.run(["git", "-C", str(path.parent), "rev-parse", "HEAD"], check=True, capture_output=True, text=True, timeout=15).stdout.strip()
        blob = subprocess.run(["git", "-C", str(path.parent), "show", f"{source_sha}:docker-compose.yml"], check=True, capture_output=True, timeout=15).stdout
    except subprocess.SubprocessError:
        raise a.ArtifactError("baseline Compose source is unavailable") from None
    raw = a._document_bytes(path)
    if head != source_sha or raw != blob:
        raise a.ArtifactError("Compose is not the exact baseline source blob")
    return raw


def _owners(docker):
    result = {}
    for service in OWNERS:
        ids = docker.call("ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=pantheon", "--filter", f"label=com.docker.compose.service={service}").split()
        if not ids and service == "dev-paper-principal-issuer":
            result[service] = None
            continue
        if len(ids) != 1:
            raise a.ArtifactError("protected owner identity is missing or ambiguous")
        a._match(ids[0], re.compile(r"[0-9a-f]{12,64}"), "owner container ID")
        row = a._json(docker.call("inspect", "--format", OWNER_FORMAT, ids[0]))
        a._keys(row, ("container_id", "image_id", "started_at", "restart_count"), "protected owner")
        a._match(row["image_id"], a.IMAGE, "owner image ID")
        a._match(row["container_id"], a.DIGEST, "owner full container ID")
        if (not isinstance(row["started_at"], str) or not row["started_at"] or
            type(row["restart_count"]) is not int or row["restart_count"] < 0):
            raise a.ArtifactError("invalid protected owner runtime identity")
        result[service] = row
    return result


def _validate_config(value):
    a._keys(value, BASELINE_CONFIG, "baseline nonsecret configuration")
    if any(value[key] not in (None, "", expected) for key, expected in BASELINE_CONFIG.items()):
        raise a.ArtifactError("baseline configuration is outside the fixed allowlist")
    return value


def _config(docker):
    ids = docker.call("ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=pantheon",
                      "--filter", "label=com.docker.compose.service=operator-bff").split()
    if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
        raise a.ArtifactError("baseline BFF identity is missing or ambiguous")
    result = dict.fromkeys(BASELINE_CONFIG)
    seen = set()
    for raw in docker.call("inspect", "--format", CONFIG_FORMAT, ids[0]).strip().splitlines():
        entry = a._json(raw)
        if not isinstance(entry, str) or "=" not in entry:
            raise a.ArtifactError("invalid filtered configuration")
        key, value = entry.split("=", 1)
        if key not in BASELINE_CONFIG or key in seen:
            raise a.ArtifactError("unexpected or duplicate filtered configuration")
        seen.add(key)
        result[key] = value
    return _validate_config(result)


def _public(args, expected_fe, http, barrier):
    def request(method, path, *, fe=False, **kwargs):
        barrier.check()
        response = http.request(method, _url(args.fe_url if fe else args.bff_url, "fe" if fe else "bff") + path, **kwargs)
        barrier.check()
        return response
    status, _ = request("GET", "/health")
    if status != 200: raise a.ArtifactError("BFF health readback failed")
    status, raw = request("GET", "/bff/version")
    version = a._json(raw) if status == 200 else None
    if not isinstance(version, dict) or version.get("source_commit_sha") != args.previous_backend_sha:
        raise a.ArtifactError("public BFF source readback mismatch")
    posture = version.get("config_posture", version)
    if not isinstance(posture, dict) or posture.get("auth_stub") is not False or posture.get("auth_mode") != "strict":
        raise a.ArtifactError("public BFF strict auth posture mismatch")
    status, raw = request("GET", "/deployment.json", fe=True)
    if status != 200 or hashlib.sha256(raw).hexdigest() != expected_fe["manifest_sha256"]:
        raise a.ArtifactError("public FE manifest bytes mismatch")
    for headers in ({}, {"Authorization": "Bearer artifact-driver-invalid-token"}):
        status, _ = request("GET", "/bff/me", headers=headers)
        if status not in (401, 403): raise a.ArtifactError("BFF strict auth negative probe failed")
    client_id = os.environ.get("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID", "")
    secret = os.environ.get("PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET", "")
    if not client_id or not secret:
        raise a.ArtifactError("approved viewer probe credentials are unavailable")
    status, raw = request("POST", "/bff/auth/dev-login", body={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret})
    login = a._json(raw) if status == 200 else None
    if (not isinstance(login, dict) or not isinstance(login.get("meta"), dict) or
        login["meta"].get("identity") != "viewer" or login.get("scope") != "viewer"):
        raise a.ArtifactError("login did not resolve the dedicated viewer identity")
    token = login.get("access_token")
    if not isinstance(token, str) or not token: raise a.ArtifactError("viewer authentication probe failed")
    status, raw = request("GET", "/bff/me", headers={"Authorization": "Bearer " + token})
    if status != 200: raise a.ArtifactError("authenticated viewer readback failed")
    envelope = a._json(raw)
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(data, dict): raise a.ArtifactError("authenticated viewer envelope is invalid")
    user, tenant, environment, session = (data.get(name) for name in ("user", "tenant", "environment", "session"))
    if (not all(isinstance(value, dict) for value in (user, tenant, environment, session)) or
        data.get("roles") != ["viewer"] or user.get("roles") != ["viewer"] or
        data.get("operator_id") != "pantheon-dev-viewer" or user.get("operator_id") != "pantheon-dev-viewer" or
        data.get("tenant_id") != "tenant-dev" or tenant.get("id") != "tenant-dev" or
        environment.get("name") != "dev" or environment.get("auth_mode") != "strict" or environment.get("strict_auth") is not True or
        session.get("authenticated") is not True or session.get("session_kind") != "bearer" or session.get("fresh") is not True):
        raise a.ArtifactError("server-bound viewer identity/tenant/auth readback mismatch")
    return {"source_sha": args.previous_backend_sha, "fe_manifest_bytes_verified": True,
            "strict_auth_denials_verified": True, "authenticated_viewer_readback_verified": True}


def _layout(args):
    folder = ROOT / f"baseline-{args.run_id}-{args.attempt}-{args.candidate_id}"
    return folder, ROOT / "images", folder / "manifest.json"


def _candidate_paths(folder):
    return folder / "candidate-images.json", folder / "candidate-images.override.json"


def _candidate_override(receipt):
    return a.manifest_bytes({"services": {service: {"image": row["image_id"], "pull_policy": "never"}
                                          for service, row in receipt["services"].items()}})


def _built_image(docker, service, candidate_sha):
    tag = "pantheon-" + service
    lines = docker.call("image", "inspect", "--format", BUILT_IMAGE_FORMAT, tag).strip().splitlines()
    if not lines: raise a.ArtifactError("built candidate image is unavailable")
    row = a._json(lines[0])
    a._keys(row, ("image_id", "oci_revision"), "built candidate image")
    a._match(row["image_id"], a.IMAGE, "built candidate image ID")
    if row["oci_revision"] != candidate_sha:
        raise a.ArtifactError("built candidate image lacks the exact source revision")
    git_sha = None
    if len(lines) > 2: raise a.ArtifactError("duplicate built source environment")
    if len(lines) == 2:
        value = a._json(lines[1])
        if value != "GIT_SHA=" + candidate_sha:
            raise a.ArtifactError("built image source environment differs from candidate")
        git_sha = candidate_sha
    if service != "loop-run-projector-scheduler" and git_sha != candidate_sha:
        raise a.ArtifactError("BFF-built candidate lacks its baked source environment")
    return {**row, "git_sha": git_sha, "compose_image": tag}


def _candidate_images(args, docker):
    # Never request expanded environment output. This no-interpolation model
    # remains only in memory; extract the three build/image fields, then drop it.
    model = a._json(docker.call("compose", "-p", "pantheon", "-f", str(args.compose_file),
                               "config", "--no-interpolate", "--no-env-resolution", "--format", "json"))
    services = model.get("services") if isinstance(model, dict) else None
    if not isinstance(services, dict): raise a.ArtifactError("candidate Compose model is invalid")
    contract = {service: {key: services.get(service, {}).get(key) for key in ("build", "image")}
                for service in a.SERVICES if isinstance(services.get(service), dict)}
    del model, services
    a._keys(contract, a.SERVICES, "candidate Compose services")
    result = {}
    for service in a.SERVICES:
        row = contract[service]
        build = row["build"]
        if (row["image"] is not None or not isinstance(build, dict) or
            build.get("context") != str(args.compose_file.parent) or build.get("dockerfile") != DOCKERFILES[service]):
            raise a.ArtifactError("candidate Compose no longer owns the fixed local image tag")
        names = docker.call("compose", "-p", "pantheon", "-f", str(args.compose_file), "config", "--images", service).splitlines()
        if "pantheon-" + service not in names:
            raise a.ArtifactError("candidate Compose did not resolve the expected service image")
        result[service] = _built_image(docker, service, args.candidate_backend_sha)
    return result


def _validate_candidate(raw, args, identity, *, expected_hash):
    a._match(expected_hash, a.DIGEST, "externally sealed candidate image digest")
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise a.ArtifactError("candidate image receipt differs from the external seal")
    receipt = a._json(raw)
    a._keys(receipt, ("schema_version", "environment", "project_id", "vm", "identity", "seal_lease_id", "sealed_at",
                      "baseline_manifest_sha256", "candidate_compose_sha256", "image_override_sha256", "services"), "candidate image receipt")
    if (receipt["schema_version"] != CANDIDATE_SCHEMA or receipt["environment"] != "dev" or
        receipt["project_id"] != PROJECT or receipt["vm"] != VM or receipt["identity"] != identity or
        receipt["baseline_manifest_sha256"] != args.manifest_sha256):
        raise a.ArtifactError("candidate image receipt is not bound to this admitted transition")
    for name in ("candidate_compose_sha256", "image_override_sha256"):
        a._match(receipt[name], a.DIGEST, name)
    try:
        uuid.UUID(receipt["seal_lease_id"])
        time.strptime(receipt["sealed_at"], "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError, AttributeError):
        raise a.ArtifactError("invalid candidate seal provenance") from None
    a._keys(receipt["services"], a.SERVICES, "candidate image services")
    for service, row in receipt["services"].items():
        a._keys(row, ("image_id", "oci_revision", "git_sha", "compose_image"), "candidate image service")
        a._match(row["image_id"], a.IMAGE, "candidate image ID")
        if (row["oci_revision"] != args.candidate_backend_sha or row["compose_image"] != "pantheon-" + service or
            row["git_sha"] not in (None, args.candidate_backend_sha) or
            (service != "loop-run-projector-scheduler" and row["git_sha"] != args.candidate_backend_sha)):
            raise a.ArtifactError("candidate image service/source binding is invalid")
    if hashlib.sha256(_candidate_override(receipt)).hexdigest() != receipt["image_override_sha256"]:
        raise a.ArtifactError("candidate image override identity differs from receipt")
    return receipt


def _load_candidate(args, identity, folder):
    path, override_path = _candidate_paths(folder)
    if args.candidate_image_manifest != path:
        raise a.ArtifactError("candidate receipt is not the fixed transition path")
    raw = a._document_bytes(path)
    receipt = _validate_candidate(raw, args, identity, expected_hash=args.candidate_image_manifest_sha256)
    if a._document_bytes(override_path) != _candidate_override(receipt):
        raise a.ArtifactError("retained candidate image override differs from admission")
    return receipt


def _service_snapshot(docker):
    result = {}
    for service in a.SERVICES:
        ids = docker.call("ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=pantheon",
                          "--filter", f"label=com.docker.compose.service={service}").split()
        if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
            raise a.ArtifactError("transition container identity is missing or ambiguous")
        row = a._json(docker.call("inspect", "--format", OWNER_FORMAT, ids[0]))
        a._keys(row, ("container_id", "image_id", "started_at", "restart_count"), "transition container")
        a._match(row["container_id"], a.DIGEST, "transition container ID")
        a._match(row["image_id"], a.IMAGE, "transition image ID")
        result[service] = row
    return result


class RestoreCAS:
    """Snapshot preconditions inside the exclusive outer guard, not a lock.

    The earlier externally sealed build receipt grants the candidate map;
    restore-time observations only compare against it and the admitted prior.
    Docker has no atomic conditional Compose-up API. The pinned outer guard
    serializes governed mutations; these checks detect drift before dispatch.
    """
    def __init__(self, args, baseline, candidate, docker, http, barrier):
        self.args, self.baseline, self.candidate = args, baseline, candidate
        self.docker, self.http, self.barrier = docker, http, barrier
        self.before = _service_snapshot(docker)
        self.replaced = False
        self.source_observations = set()
        self.check()

    def check(self):
        self.barrier.check()
        if self.replaced: return
        current = _service_snapshot(self.docker)
        if current != self.before:
            raise a.ArtifactError("current containers changed before artifact replacement")
        for service, row in current.items():
            allowed = {self.baseline["image_bundle"]["services"][service]["image_id"], self.candidate["services"][service]["image_id"]}
            if row["image_id"] not in allowed:
                raise a.ArtifactError("current image is neither admitted prior nor admitted candidate")
        observation = self._public_source(current)
        self.source_observations.add(observation)
        self.barrier.check()

    def _public_source(self, current):
        # A failed candidate may not serve HTTP. Its privately observed exact
        # image IDs still have the PRE-ROLLOUT externally sealed provenance.
        # Only these typed availability failures may omit the extra source
        # observation; TLS/certificate errors and known identity conflicts do
        # not qualify. Post-restore public/auth checks remain unconditional.
        unavailable = ((ConnectionRefusedError, "unavailable_connection_refused"),
                       (ConnectionResetError, "unavailable_connection_reset"),
                       (TimeoutError, "unavailable_timeout"),
                       (http.client.RemoteDisconnected, "unavailable_disconnect"))
        try:
            status, raw = self.http.request("GET", _url(self.args.bff_url, "bff") + "/bff/version")
        except (urllib.error.URLError, ConnectionRefusedError, ConnectionResetError, TimeoutError, http.client.RemoteDisconnected) as error:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            for kind, code in unavailable:
                if isinstance(reason, kind):
                    return code
            raise a.ArtifactError("current public BFF source failed outside the allowed availability boundary") from None
        if status in (502, 503, 504):
            return "unavailable_http_" + str(status)
        version = a._json(raw) if status == 200 else None
        source = version.get("source_commit_sha") if isinstance(version, dict) else None
        image = current["operator-bff"]["image_id"]
        allowed_sources = set()
        if image == self.baseline["image_bundle"]["services"]["operator-bff"]["image_id"]:
            allowed_sources.add(self.args.previous_backend_sha)
        if image == self.candidate["services"]["operator-bff"]["image_id"]:
            allowed_sources.add(self.args.candidate_backend_sha)
        if source not in allowed_sources:
            raise a.ArtifactError("current public BFF source is not bound to its admitted image")
        return "available_matching_source"

    def call(self, *args):
        mutating = args[:2] == ("image", "load") or (args[0] == "compose" and "up" in args)
        if mutating: self.check()
        result = self.docker.call(*args)
        if args[0] == "compose" and "up" in args: self.replaced = True
        return result


def _seal_candidate(args, identity, lease_id, outer, folder, compose, docker, http, barrier):
    path, override_path = _candidate_paths(folder)
    if path.exists() or path.is_symlink():
        raise a.ArtifactError("candidate images already sealed; refusing re-admission")
    if a._file_digest(folder / "baseline-compose.yml")[0] != outer["compose_sha256"]:
        raise a.ArtifactError("retained baseline Compose has drifted")
    a.validate_images(a.manifest_bytes(outer["image_bundle"]), expected_sha256=outer["image_bundle_sha256"],
                      expected_source_sha=args.previous_backend_sha, archive_root=ROOT / "images")
    a.verify_frontend(outer["frontend"], release_store=args.fe_release_store, live_link=args.fe_live_link)
    _public(args, outer["frontend"], http, barrier)
    before = _service_snapshot(docker)
    if any(row["image_id"] != outer["image_bundle"]["services"][service]["image_id"] for service, row in before.items()):
        raise a.ArtifactError("candidate admission must precede replacement of all three baseline images")
    owners = _owners(docker)
    os.environ.update(GIT_SHA=args.candidate_backend_sha, PANTHEON_ENV="dev")
    services = _candidate_images(args, docker)
    receipt = {"schema_version": CANDIDATE_SCHEMA, "environment": "dev", "project_id": PROJECT, "vm": VM,
               "identity": identity, "seal_lease_id": lease_id, "sealed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "baseline_manifest_sha256": args.manifest_sha256, "candidate_compose_sha256": hashlib.sha256(compose).hexdigest(),
               "services": services}
    override = _candidate_override(receipt)
    receipt["image_override_sha256"] = hashlib.sha256(override).hexdigest()
    raw = a.manifest_bytes(receipt)
    digest = hashlib.sha256(raw).hexdigest()
    _validate_candidate(raw, args, identity, expected_hash=digest)
    if _service_snapshot(docker) != before or _owners(docker) != owners:
        raise a.ArtifactError("running identities changed during candidate admission")
    if _compose_bytes(args.compose_file, args.candidate_backend_sha) != compose:
        raise a.ArtifactError("candidate Compose changed during image admission")
    if any(_built_image(docker, service, args.candidate_backend_sha) != services[service] for service in a.SERVICES):
        raise a.ArtifactError("built candidate image tags changed during admission")
    barrier.check()
    _publish(override_path, override)
    _publish(path, raw)
    barrier.check()
    return {"candidate_image_manifest_path": str(path), "candidate_image_manifest_sha256": digest,
            "candidate_image_manifest": receipt, "candidate_image_override_path": str(override_path),
            "candidate_image_override_sha256": receipt["image_override_sha256"]}


def _load(args, identity, manifest_path):
    if args.manifest != manifest_path:
        raise a.ArtifactError("manifest is outside the fixed run/candidate path")
    a._match(args.manifest_sha256, a.DIGEST, "trusted outer manifest digest")
    raw = a._document_bytes(manifest_path)
    if hashlib.sha256(raw).hexdigest() != args.manifest_sha256:
        raise a.ArtifactError("outer manifest bytes mismatch")
    outer = a._json(raw)
    a._keys(outer, ("schema_version", "environment", "project_id", "vm", "identity", "capture_lease_id", "captured_at", "image_bundle", "image_bundle_sha256", "frontend", "compose_sha256", "baseline_nonsecret_config"), "outer manifest")
    if (outer["schema_version"] != OUTER_SCHEMA or outer["environment"] != "dev" or
        outer["project_id"] != PROJECT or outer["vm"] != VM or outer["identity"] != identity):
        raise a.ArtifactError("outer manifest scope mismatch")
    a._match(outer["compose_sha256"], a.DIGEST, "baseline Compose digest")
    a._match(outer["image_bundle_sha256"], a.DIGEST, "image bundle digest")
    if (not isinstance(outer["frontend"], dict) or outer["frontend"].get("frontend_sha") != args.previous_frontend_sha or
        outer["frontend"].get("backend_sha") != args.previous_backend_sha):
        raise a.ArtifactError("FE source pair is not the admitted baseline")
    _validate_config(outer["baseline_nonsecret_config"])
    try:
        uuid.UUID(outer["capture_lease_id"])
        time.strptime(outer["captured_at"], "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError, AttributeError):
        raise a.ArtifactError("invalid capture provenance") from None
    return outer, raw


def run(args, *, docker, http, barrier):
    identity, lease_id = _identity(args)
    barrier.check()
    folder, images, manifest_path = _layout(args)
    compose = _compose_bytes(args.compose_file, args.candidate_backend_sha if args.command == "seal-candidate" else args.previous_backend_sha)
    compose_digest = hashlib.sha256(compose).hexdigest()
    if args.command == "capture":
        frontend = a.capture_frontend(release_store=args.fe_release_store, live_link=args.fe_live_link,
                                      frontend_sha=args.previous_frontend_sha, backend_sha=args.previous_backend_sha)
        _public(args, frontend, http, barrier)
        owners_before = _owners(docker)
        config_before = _config(docker)
        for directory in (folder, images):
            directory.mkdir(mode=0o700, exist_ok=True)
            a._directory(directory, private=True)
        if manifest_path.exists():
            raise a.ArtifactError("capture already sealed; use verify with its trusted digest")
        bundle = a.capture_images(docker=docker, archive_root=images, source_sha=args.previous_backend_sha, check_lease=barrier.check)
        a.verify_frontend(frontend, release_store=args.fe_release_store, live_link=args.fe_live_link)
        if _owners(docker) != owners_before: raise a.ArtifactError("protected owners changed during capture")
        if _config(docker) != config_before: raise a.ArtifactError("baseline configuration changed during capture")
        if _compose_bytes(args.compose_file, args.previous_backend_sha) != compose:
            raise a.ArtifactError("Compose changed during capture")
        bundle_raw = a.manifest_bytes(bundle)
        outer = {"schema_version": OUTER_SCHEMA, "environment": "dev", "project_id": PROJECT, "vm": VM,
                 "identity": identity, "capture_lease_id": lease_id, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "image_bundle": bundle, "image_bundle_sha256": hashlib.sha256(bundle_raw).hexdigest(),
                 "frontend": frontend, "compose_sha256": compose_digest, "baseline_nonsecret_config": config_before}
        raw = a.manifest_bytes(outer)
        barrier.check()
        _publish(folder / "baseline-compose.yml", compose)
        _publish(manifest_path, raw)
        barrier.check()
        return {"manifest_path": str(manifest_path), "manifest_sha256": hashlib.sha256(raw).hexdigest(), "manifest": outer}

    outer, raw = _load(args, identity, manifest_path)
    if args.command == "seal-candidate":
        return _seal_candidate(args, identity, lease_id, outer, folder, compose, docker, http, barrier)
    if compose_digest != outer["compose_sha256"] or a._file_digest(folder / "baseline-compose.yml")[0] != compose_digest:
        raise a.ArtifactError("baseline Compose bytes mismatch")
    bundle_raw = a.manifest_bytes(outer["image_bundle"])
    a.validate_images(bundle_raw, expected_sha256=outer["image_bundle_sha256"], expected_source_sha=args.previous_backend_sha, archive_root=images)
    # FE compensation remains FE-owned. Do not mutate even BFF if FE did not
    # restore its exact prior target and bytes first.
    a.verify_frontend(outer["frontend"], release_store=args.fe_release_store, live_link=args.fe_live_link)
    owners_before = _owners(docker)
    if args.command == "restore":
        if os.environ.get("PANTHEON_BFF_AUTH_STUB") != "false" or os.environ.get("PANTHEON_BFF_AUTH_MODE") != "strict":
            raise a.ArtifactError("restore caller did not inject the approved strict runtime environment")
        candidate = _load_candidate(args, identity, folder)
        cas = RestoreCAS(args, outer, candidate, docker, http, barrier)
        os.environ.update(GIT_SHA=args.previous_backend_sha, PANTHEON_ENV="dev", COMPOSE_PROFILES="")
        # Only the two admitted non-secret values override the caller. Empty
        # injection prevents ambient values from filling a baseline absence;
        # exact post-recreate equality remains mandatory, never inferred.
        os.environ.update({key: value or "" for key, value in outer["baseline_nonsecret_config"].items()})
        a.restore_images(bundle_raw, expected_sha256=outer["image_bundle_sha256"], expected_source_sha=args.previous_backend_sha,
                         archive_root=images, compose_files=((args.compose_file, compose_digest),), docker=cas,
                         check_lease=cas.check, environment="dev")
    observed = {service: a._current(docker, service)["image_id"] for service in a.SERVICES}
    if any(observed[service] != outer["image_bundle"]["services"][service]["image_id"] for service in a.SERVICES):
        raise a.ArtifactError("image readback differs despite any equal source SHA")
    if _config(docker) != outer["baseline_nonsecret_config"]:
        raise a.ArtifactError("restored nonsecret configuration differs from baseline")
    public = _public(args, outer["frontend"], http, barrier)
    a.verify_frontend(outer["frontend"], release_store=args.fe_release_store, live_link=args.fe_live_link)
    owners_after = _owners(docker)
    if owners_before != owners_after: raise a.ArtifactError("protected owners changed during artifact restore/verify")
    barrier.check()
    return {"schema_version": "pantheon.dev-artifact-readback.v1", "operation": args.command,
            "manifest_sha256": hashlib.sha256(raw).hexdigest(), "identity": identity,
            "pre_restore_source_observations": sorted(cas.source_observations) if args.command == "restore" else [],
            "image_readback_verified": True, "images": observed, "frontend": outer["frontend"],
            "baseline_nonsecret_config_verified": True,
            "protected_owners_unchanged": True, "owners": owners_after, "public": public}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "seal-candidate", "verify", "restore"))
    for name in ("environment", "project-id", "vm", "candidate-id", "run-id", "attempt", "controller-sha",
                 "candidate-backend-sha", "candidate-frontend-sha", "previous-backend-sha", "previous-frontend-sha", "bff-url", "fe-url"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--artifact-root", type=Path, default=ROOT)
    parser.add_argument("--compose-file", type=Path, required=True)
    parser.add_argument("--fe-release-store", type=Path, default=FE_STORE)
    parser.add_argument("--fe-live-link", type=Path, default=FE_LINK)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--candidate-image-manifest", type=Path)
    parser.add_argument("--candidate-image-manifest-sha256")
    parser.add_argument("--guard-channel-fd", type=int, required=True)
    parser.add_argument("--guard-max-silence-seconds", type=float, default=10)
    return parser.parse_args(argv)


def main(argv=None):
    def cancelled(_signal, _frame): raise a.ArtifactError("guarded operation cancelled")
    for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP): signal.signal(name, cancelled)
    try:
        args = parse_args(argv)
        barrier = CancellationBarrier(args.guard_channel_fd, max_silence=args.guard_max_silence_seconds)
        result = run(args, docker=GuardedDocker(barrier), http=HTTP(), barrier=barrier)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        # HTTP bodies, credentials and subprocess stderr are not diagnostics.
        print('{"status":"error","error_code":"DEV_ARTIFACT_DRIVER_FAILED"}', file=__import__("sys").stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
