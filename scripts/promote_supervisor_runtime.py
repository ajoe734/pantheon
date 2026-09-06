#!/usr/bin/env python3
"""Replace the supervisor with one exact authoritative-V2 runtime.

This command intentionally has no incumbent compatibility path.  A promotion
is a short replacement operation: render the candidate's V2 config, stop the
existing supervisor, atomically install that config, and launch the candidate.
It never reconstructs a retired runtime or tries to restore one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

GIT_SCRIPTS_DIR = Path(__file__).resolve().parent / "git"
if str(GIT_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(GIT_SCRIPTS_DIR))

import auto_integrator  # noqa: E402  (shared stable integration lock)

from provision_live_supervisor_config import (
    build_live_config,
    ensure_approval_queue_marker,
    load_json_object,
    parse_repository_integration_roots,
    parse_repository_source_roots,
    validate_python_dependencies,
    validated_immutable_command_root,
    validated_root,
    write_json_atomic,
)


# The deployment layout is host-owned, not repository-owned. The default keeps
# the established operator path working untouched; PANTHEON_DEPLOY_ROOT lets a
# rebuilt or additional host own the same shape under its own home, so the
# control plane is not tied to one machine's directory tree.
DEFAULT_DEPLOY_ROOT = Path.home() / "pantheon-ci-deploy"
DEPLOY_ROOT = Path(
    os.environ.get("PANTHEON_DEPLOY_ROOT") or DEFAULT_DEPLOY_ROOT
).expanduser()
LIVE_SUPERVISOR_CONFIG_PATH = DEPLOY_ROOT / "runtime" / "live-supervisor-mainroot-config.json"
COMMAND_RUNTIME_PARENT = DEPLOY_ROOT / "command-runtimes"
TASK_STATE_MODE = "authoritative"
SUPERVISOR_PUBLIC_AUTHORITY_ENV_NAMES = (
    "BRIDGE_SIGNING_PUBLIC_KEYS_JSON",
)
SUPERVISOR_FORBIDDEN_AUTHORITY_ENV_NAMES = (
    "BRIDGE_SIGNING_PRIVATE_KEY",
    "BRIDGE_SIGNING_KEY",
    "BRIDGE_SIGNING_KEY_ID",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().absolute()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file: {path}")
    return load_json_object(path)


def _public_authority_environment(path: Path) -> dict[str, str]:
    """Read the fixed public verifier file without evaluating shell content."""

    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError(f"supervisor verifier env must be an absolute regular file: {path}")
    if stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != 0o600:
        raise ValueError("supervisor verifier env must have mode 600")
    parsed: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, raw_value = line.partition("=")
        if not separator or name not in SUPERVISOR_PUBLIC_AUTHORITY_ENV_NAMES:
            raise ValueError(f"invalid public supervisor authority entry at line {number}")
        try:
            values = shlex.split(raw_value, posix=True)
        except ValueError as exc:
            raise ValueError(f"invalid public supervisor authority entry at line {number}") from exc
        if len(values) != 1 or not values[0].strip() or name in parsed:
            raise ValueError(f"invalid public supervisor authority entry at line {number}")
        parsed[name] = values[0]
    if set(parsed) != set(SUPERVISOR_PUBLIC_AUTHORITY_ENV_NAMES):
        raise ValueError("supervisor verifier env must define every public verifier map")
    return parsed


def supervisor_launch_environment(
    source: Mapping[str, str], *, authority_env_file: Path | None = None
) -> dict[str, str]:
    """Return the verifier-only environment for a directly promoted supervisor."""

    environment = dict(source)
    for name in SUPERVISOR_FORBIDDEN_AUTHORITY_ENV_NAMES:
        environment.pop(name, None)
    if authority_env_file is not None:
        environment.update(_public_authority_environment(authority_env_file))
    for name in SUPERVISOR_PUBLIC_AUTHORITY_ENV_NAMES:
        raw = str(environment.get(name) or "").strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} must be valid JSON") from exc
        if not isinstance(payload, dict) or not payload or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in payload.items()
        ):
            raise ValueError(f"{name} must be a non-empty public-key map")
        environment[name] = raw
    return environment


def _git_output(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise ValueError("candidate V2 source Git identity is unavailable") from exc


def candidate_runtime_identity(repo_root: Path) -> dict[str, str]:
    """Return the only source identity allowed to be launched."""

    identity = validated_immutable_command_root(repo_root)
    root = Path(identity["root"])
    runtime_parent = COMMAND_RUNTIME_PARENT.expanduser().absolute().resolve()
    if root.parent != runtime_parent or root.name != identity["head"]:
        raise ValueError(
            "candidate V2 command source must be the exact "
            f"command-runtimes/<HEAD> checkout under {runtime_parent}"
        )
    if _git_output(root, "status", "--porcelain"):
        raise ValueError("candidate V2 source tree must be clean")
    return identity


def _validate_authoritative_store(config: Mapping[str, Any]) -> None:
    store = config.get("task_state_store")
    if not isinstance(store, Mapping):
        raise ValueError("V2 config must define task_state_store")
    if str(store.get("mode") or "").strip().lower() != TASK_STATE_MODE:
        raise ValueError("V2 config requires task_state_store.mode=authoritative")
    event_log = Path(str(store.get("event_log") or "")).expanduser()
    if not event_log.is_absolute() or event_log.name in {"", ".", ".."}:
        raise ValueError("V2 config requires an absolute task-state event log")


def render_v2_config(
    repo_root: Path,
    *,
    status_root: Path,
    live_config_path: Path,
    python_executable: Path,
    repository_source_roots: Mapping[str, Path | str] | None = None,
    repository_integration_roots: Mapping[str, Path | str] | None = None,
    requirements_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Render a new V2 runtime config without using an incumbent overlay."""

    identity = candidate_runtime_identity(repo_root)
    # Validate before touching any state so a candidate interpreter that is
    # missing a required dependency (or was silently de-virtualized back to
    # the base interpreter) fails here, leaving the incumbent supervisor,
    # live config, leases, and cron/watchdog binding untouched.
    if requirements_path is not None:
        validate_python_dependencies(python_executable, requirements_path)
    repo_config = _load_json(repo_root / ".orchestrator" / "config.json", label="candidate config")
    rendered = build_live_config(
        repo_config,
        existing_live_config=None,
        command_root=repo_root,
        status_root=status_root,
        live_config_path=live_config_path,
        python_executable=python_executable,
        repository_source_roots=repository_source_roots,
        repository_integration_roots=repository_integration_roots,
    )
    _validate_authoritative_store(rendered)
    return rendered, identity


def _pid_path(rendered: Mapping[str, Any]) -> Path:
    paths = rendered.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("V2 config must define paths")
    state_file = Path(str(paths.get("state_file") or "")).expanduser()
    if not state_file.is_absolute():
        raise ValueError("rendered V2 state_file must be absolute")
    return state_file.parent / "supervisor.pid"


def _incumbent_pid_path(live_config_path: Path, rendered: Mapping[str, Any]) -> Path:
    """Read the incumbent PID location only from its installed config.

    Status-root replacement is an ordinary V2 operation: command source and
    coordination state may both change together.  The prior config is the
    only durable identity for the process to stop; process cwd and a global
    product-root PID file are not authority.
    """

    if not live_config_path.exists():
        return _pid_path(rendered)
    incumbent = _load_json(live_config_path, label="installed live config")
    return _pid_path(incumbent)


def _read_pid(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
        pid = int(value)
    except (OSError, ValueError):
        return None
    return pid if pid > 0 and value == str(pid) else None


def _process_is_supervisor(pid: int) -> bool:
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return False
    argv = [item for item in raw.decode("utf-8", errors="ignore").split("\0") if item]
    return any(Path(item).name == "supervisor.py" for item in argv)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_existing_supervisor(pid_path: Path, *, timeout_seconds: float) -> int | None:
    """Stop the recorded supervisor.  No replacement is attempted on failure."""

    pid = _read_pid(pid_path)
    if pid is None or not _pid_alive(pid):
        return None
    if not _process_is_supervisor(pid):
        raise ValueError(f"pid file does not identify a supervisor process: {pid}")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _pid_alive(pid):
        raise RuntimeError(f"existing supervisor did not stop within {timeout_seconds:g}s")
    return pid


def launch_v2_supervisor(
    rendered: Mapping[str, Any],
    *,
    identity: Mapping[str, str],
    status_root: Path,
    authority_env_file: Path | None = None,
) -> int:
    watchdog = rendered.get("watchdog")
    if not isinstance(watchdog, Mapping):
        raise ValueError("V2 config must define watchdog")
    argv = watchdog.get("supervisor_command")
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) for item in argv):
        raise ValueError("V2 supervisor command is invalid")
    root = Path(identity["root"])
    environment = supervisor_launch_environment(
        os.environ, authority_env_file=authority_env_file
    )
    environment.update(
        {
            "PANTHEON_COMMAND_ROOT": str(root),
            "PANTHEON_COMMAND_RUNTIME_SHA": identity["head"],
            "PANTHEON_COMMAND_REMOTE": identity["repository"],
            "PANTHEON_COMMAND_BASE_REF": "origin/dev",
            "PANTHEON_STATUS_ROOT": str(status_root),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    log_path = status_root / ".orchestrator" / "logs" / "supervisor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(
        log_path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC,
        0o600,
    )
    os.chmod(log_path, 0o600)
    with os.fdopen(log_fd, "ab") as log_output:
        process = subprocess.Popen(
            argv,
            cwd=root,
            env=environment,
            stdout=log_output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return int(process.pid)


def _write_evidence(path: Path | None, result: Mapping[str, Any]) -> None:
    if path is None:
        return
    if not path.is_absolute():
        raise ValueError("evidence path must be absolute")
    write_json_atomic(path, dict(result))


def seal_command_runtime(root: Path) -> dict[str, Any]:
    """Remove write bits from one validated immutable command runtime.

    Auto workers execute status commands from this tree but never need to
    mutate it.  Sealing every non-symlink entry turns an accidental edit into
    an immediate permission error instead of poisoning every later worker's
    command-runtime integrity check.  Execute bits and all read bits are
    preserved.
    """

    root = root.expanduser().absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"command runtime seal target must be a direct directory: {root}")
    root = root.resolve()
    changed_paths = 0
    sealed_paths = 0
    for current_root, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
        current = Path(current_root)
        for name in (*filenames, *dirnames):
            path = current / name
            if path.is_symlink():
                continue
            mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
            sealed_mode = mode & ~0o222
            if mode != sealed_mode:
                os.chmod(path, sealed_mode, follow_symlinks=False)
                changed_paths += 1
            sealed_paths += 1
    root_mode = stat.S_IMODE(root.stat(follow_symlinks=False).st_mode)
    sealed_root_mode = root_mode & ~0o222
    if root_mode != sealed_root_mode:
        os.chmod(root, sealed_root_mode, follow_symlinks=False)
        changed_paths += 1
    sealed_paths += 1
    return {
        "outcome": "sealed",
        "root": str(root),
        "sealed_paths": sealed_paths,
        "changed_paths": changed_paths,
    }


def verify_worker_sandbox(root: Path) -> dict[str, Any]:
    """Prove bubblewrap can enforce the provider's read-only runtime mount."""

    binary = shutil.which("bwrap")
    if not binary:
        raise ValueError(
            "bubblewrap (bwrap) is required before promoting a worker command runtime"
        )
    runtime_root = root.expanduser().resolve()
    probe = subprocess.run(
        [
            binary,
            "--die-with-parent",
            "--unshare-pid",
            "--bind",
            "/",
            "/",
            "--dev-bind",
            "/dev",
            "/dev",
            "--ro-bind",
            str(runtime_root),
            str(runtime_root),
            "--proc",
            "/proc",
            "--",
            "/bin/true",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "bubblewrap probe failed").strip()
        raise ValueError(f"worker command-runtime sandbox is unavailable: {detail}")
    return {
        "outcome": "available",
        "binary": str(Path(binary).resolve()),
        "command_root": str(runtime_root),
    }


_EXECUTION_AUTHORIZATION_BARRIER_PROBE = r'''
import ast
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(root / ".orchestrator"), str(root / "scripts")]

# All authority and writes below belong to this disposable probe. No live
# verifier keys, worker identity, journal binding, or grants are inherited.
with tempfile.TemporaryDirectory(prefix="execution-barrier-preflight-") as scratch:
    status_root = Path(scratch) / "status"
    status_root.mkdir()
    event_log = Path(scratch) / "runtime" / "task-state-events.jsonl"
    os.environ["PANTHEON_STATUS_ROOT"] = str(status_root)
    os.environ["AI_NAME"] = "Codex2"
    import ai_status
    import common
    import execution_authorization as ea
    import worker_runner
    from development_bridge import dev_bridge_materialize as intake
    from rewrite import dispatch_admission as admission
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    modules = (ai_status, common, ea, worker_runner, intake, admission)
    provenance = {}
    for module in modules:
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(root), "barrier imported outside candidate: " + str(path)
        provenance[module.__name__] = {
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    assert ea.RUNTIME_CAPABILITY_EXECUTION_AUTHORIZATION in ea.RUNTIME_CAPABILITIES
    # Behavioral helper checks below also require the real entry points to
    # call those helpers. Retaining a detached function is not a barrier.
    worker_tree = ast.parse(Path(worker_runner.__file__).read_text())
    worker_functions = {
        node.name: node for node in worker_tree.body if isinstance(node, ast.FunctionDef)
    }
    def call_name(node):
        if not isinstance(node, ast.Call):
            return ""
        return ast.unparse(node.func)
    def direct_call(statement, name):
        value = getattr(statement, "value", None)
        return call_name(value) == name
    binding_function = worker_functions["validate_worker_entry_binding"]
    assert any(direct_call(node, "ensure_execution_authorized_before_launch")
               for node in binding_function.body), "worker entry detached from authorization"
    main_function = worker_functions["main"]
    binding_positions = [i for i, node in enumerate(main_function.body)
                         if direct_call(node, "validate_worker_entry_binding")]
    sandbox_positions = [i for i, node in enumerate(main_function.body)
                         if direct_call(node, "bind_worker_sandbox")]
    assert binding_positions and sandbox_positions and min(binding_positions) < min(sandbox_positions), (
        "worker main must validate canonical receipt before sandbox setup"
    )
    guarded_launches = []
    for node in ast.walk(main_function):
        if not isinstance(node, ast.With):
            continue
        if not any(call_name(item.context_expr) == "canonical_task_state_lock_file"
                   and any(keyword.arg == "shared" and isinstance(keyword.value, ast.Constant)
                           and keyword.value.value is True for keyword in item.context_expr.keywords)
                   for item in node.items):
            continue
        validation = [i for i, statement in enumerate(node.body)
                      if direct_call(statement, "validate_worker_entry_binding")]
        launch = [i for i, statement in enumerate(node.body)
                  if direct_call(statement, "subprocess.Popen")]
        if validation and launch and min(validation) < min(launch):
            guarded_launches.extend(node.body[i].value for i in launch)
    all_launches = [node for node in ast.walk(main_function) if call_name(node) == "subprocess.Popen"]
    assert all_launches and set(all_launches) == set(guarded_launches), (
        "worker launch lacks canonical lock and final receipt/authorization validation"
    )
    os.environ.update({
        "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
        "PANTHEON_TASK_STATE_EVENT_LOG": str(event_log),
        common.CANONICAL_TASK_STATE_IDENTITY_ENV: json.dumps(
            common.canonical_task_state_identity_for_paths(
                status_root=status_root, event_log=event_log,
            )
        ),
    })
    ai_status.configure_status_root_paths(status_root)
    source_key = Ed25519PrivateKey.generate()
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    canonical = lambda value: json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()
    os.environ["BRIDGE_SIGNING_PUBLIC_KEYS_JSON"] = json.dumps({
        "isolated-preflight-source": encode(source_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )),
    })
    state = ai_status.default_state()
    state["tasks"] = []
    state["handoffs"] = []
    state["blockers"] = []
    state["wave_state"] = {"status": "open"}
    for work_class in ("hosted", "functional"):
        task_id = "RUNTIME-PREFLIGHT-" + work_class.upper()
        packet_id = "isolated-preflight-" + work_class
        spec = {
            "id": task_id, "title": "Isolated runtime barrier preflight",
            "owner": "Codex2", "reviewer": "Codex", "target_repo": "pantheon",
            "phase": "Runtime preflight", "summary": "Disposable local probe",
            "depends_on": [], "artifacts": ["docs/deployment/evidence/" + task_id + "/"],
            "acceptance": ["Verify local runtime barriers without launching work"],
            "execution_resources": ["pantheon-dev"] if work_class == "hosted" else [],
        }
        packet = {
            "packet_id": packet_id, "work_class": work_class, "tasks": [spec],
            "actor": {"id": "isolated-preflight-source", "roles": ["source"]},
        }
        digest = hashlib.sha256(canonical(packet)).hexdigest()
        packet["signature"] = {
            "key_id": "isolated-preflight-source", "algorithm": "Ed25519",
            "value": encode(source_key.sign(canonical(packet))),
        }
        batch = {
            "packet_id": packet_id, "packet_digest": digest,
            "actor": ai_status.DEV_BRIDGE_BATCH_ACTOR, "signed_packet": packet,
            "tasks": [{
                "task_id": task_id, "owner": spec["owner"], "reviewer": spec["reviewer"],
                "title": spec["title"], "assignment_next": None,
                "task_metadata": {"dev_bridge": {
                    "packet_id": packet_id, "packet_digest": digest,
                    "task_spec": spec, "task_spec_hash": hashlib.sha256(canonical(spec)).hexdigest(),
                    "work_class": work_class, "conversation_id": "isolated-runtime-preflight",
                    "source_turn_ids": [], "documents": [],
                }},
            }],
        }
        # Verify the actual source signature before invoking the actual
        # materialization/assignment code. There is deliberately no MFA.
        intake.verify_signed_dev_bridge_packet(batch, state=state)
        intake.run_dev_bridge_materialize_batch(state, batch, commands={"assign": ai_status.command_assign})
    ai_status.save_state(state)
    state = ai_status.load_state()
    hosted = ai_status.get_task(state, "RUNTIME-PREFLIGHT-HOSTED")
    functional = ai_status.get_task(state, "RUNTIME-PREFLIGHT-FUNCTIONAL")
    assert hosted["execution_authorization"]["state"] == "pending_authorization"
    assert hosted["execution_authorization"]["old_runtime_hold"] is True
    assert hosted["waiting_for"] == "Human/Ops"
    assert hosted["execution_authorization"]["grant"] is None
    now = datetime.now(timezone.utc)
    lane = admission.DispatchLane("preflight", "Codex2", 1, (
        admission.DeliveryEndpoint("preflight-endpoint", "preflight-provider", "preflight-account"),
    ))
    snapshot = admission.AdmissionSnapshot(
        now=now,
        endpoint_health={"preflight-endpoint": admission.HealthRecord("healthy")},
        account_health={"preflight-account": admission.HealthRecord("healthy")},
        account_limits={"preflight-account": 1},
    )
    for task, should_run in ((hosted, False), (functional, True)):
        authorized = ea.is_execution_authorized(task, now=now)
        assert authorized is should_run
        # Dependency completion and removal of the compatibility hold must
        # not bypass the independent planner or late queue-delivery gate.
        intent = admission.TaskIntent(
            task["id"], "todo", task["owner"], task["reviewer"], True,
            execution_authorized=authorized,
        )
        for endpoint in (None, "preflight-endpoint"):
            decision = admission.evaluate_dispatch_intent(
                intent, lane, snapshot, requested_endpoint_id=endpoint,
            )
            assert decision.eligible is should_run, str(decision)
            if not should_run:
                assert decision.reason.value == "execution_authorization_required"
        try:
            worker_runner.ensure_execution_authorized_before_launch(
                status_root, task["id"], active_role="owner", run_id="isolated-unreserved-run",
            )
        except RuntimeError:
            assert not should_run, "ordinary execution incorrectly rejected at worker entry"
        else:
            assert should_run, "pending privileged task passed worker entry"
    # Enter the actual runner main with no usable launch receipt. The sole
    # stub is sandbox construction: if reached, it would use a harmless local
    # marker command. Runtime/source and receipt validators stay unmodified.
    subprocess.run(["git", "init", "-q", str(status_root)], check=True, capture_output=True)
    runner_id = "isolated-runner-entry"
    for name, value in (("state.json", {"workers": {runner_id: None}}),
                        ("approval-queue.json", {}), ("config.json", {})):
        worker_runner.write_json(status_root / ".orchestrator" / name, value)
    os.environ.update({
        "PANTHEON_COMMAND_ROOT": str(root),
        "PANTHEON_COMMAND_RUNTIME_SHA": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True,
        ).stdout.strip(),
        "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
        "PANTHEON_COMMAND_BASE_REF": "origin/dev",
    })
    sandbox_calls = []
    def isolated_sandbox(command, **kwargs):
        sandbox_calls.append(True)
        return command
    worker_runner.bind_worker_sandbox = isolated_sandbox
    heartbeat = status_root / ".orchestrator/worker-runtime/heartbeats/preflight.json"
    runner_status = status_root / ".orchestrator/worker-runtime/status/preflight.json"
    marker = Path(scratch) / "unauthorized-provider-marker"
    try:
        worker_runner.main([
            "--run-id", runner_id, "--heartbeat-path", str(heartbeat),
            "--status-path", str(runner_status), "--", sys.executable, "-c",
            "from pathlib import Path; Path(" + repr(str(marker)) + ").touch()",
        ])
    except RuntimeError as exc:
        assert "canonical worker receipt is malformed" in str(exc), str(exc)
    else:
        raise AssertionError("worker main accepted an unusable launch receipt")
    assert not sandbox_calls and not marker.exists() and not heartbeat.exists() and not runner_status.exists()
    print(json.dumps({
        "outcome": "barriers_verified", "command_root": str(root),
        "capability": "execution_authorization_v1", "python_executable": sys.executable,
        "python_prefix": sys.prefix, "module_provenance": provenance,
        "checks": ["signed_no_mfa_pending_intake", "durable_legacy_hold",
                   "planner_denies_pending", "late_delivery_denies_pending",
                   "worker_entry_denies_unreserved", "ordinary_functional_dispatch",
                   "worker_main_denies_invalid_receipt", "worker_launch_guard_wiring"],
    }, sort_keys=True))
'''


def verify_execution_authorization_barriers(
    root: Path, *, python_executable: Path
) -> dict[str, Any]:
    """Exercise candidate intake and execution gates with its selected Python.

    The isolated subprocess uses only candidate code and disposable TaskStore
    state. No live authority is inherited and no worker or grant is created.
    A declaration or an importable no-op hook cannot pass this preflight.
    """

    runtime_root = root.expanduser().resolve()
    python_executable = python_executable.expanduser().absolute()
    try:
        probe = subprocess.run(
            [str(python_executable), "-I", "-B", "-c", _EXECUTION_AUTHORIZATION_BARRIER_PROBE, str(runtime_root)],
            cwd=str(runtime_root),
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            capture_output=True, text=True, check=False, timeout=30,
        )
        if probe.returncode != 0:
            raise ValueError((probe.stderr or probe.stdout or "probe failed").strip())
        result = json.loads(probe.stdout)
        if (
            not isinstance(result, dict)
            or result.get("outcome") != "barriers_verified"
            or result.get("command_root") != str(runtime_root)
            or result.get("python_executable") != str(python_executable)
        ):
            raise ValueError("barrier probe returned mismatched runtime/interpreter provenance")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            "command runtime is missing the required deferred-intake/late-"
            f"execution authorization barriers: {exc}"
        ) from exc
    return result


def sync_coordination_root_code(candidate_root: Path, status_root: Path) -> dict[str, Any]:
    """Preserve the coordination checkout; executable code is immutable.

    The status root is shared mutable state and may contain worker-owned or
    operator-owned changes. Promotion must therefore never mirror, remove, or
    chmod repository files there. Supervisor and governed command execution
    are pinned to candidate_root, the validated command-runtimes/<SHA> tree.
    """

    return {
        "outcome": "preserved",
        "reason": "coordination_root_is_state_only",
        "candidate_root": str(candidate_root.resolve()),
        "status_root": str(status_root.resolve()),
        "paths": [],
    }


def _replace_supervisor_locked(
    repo_root: Path,
    *,
    status_root: Path,
    live_config_path: Path,
    python_executable: Path,
    termination_timeout: float,
    evidence_path: Path | None = None,
    authority_env_file: Path | None = None,
    repository_source_roots: Mapping[str, Path | str] | None = None,
    repository_integration_roots: Mapping[str, Path | str] | None = None,
    requirements_path: Path | None = None,
) -> dict[str, Any]:
    """Stop old, install exact V2 config, then launch exact V2 source."""

    if termination_timeout <= 0:
        raise ValueError("termination timeout must be positive")
    rendered, identity = render_v2_config(
        repo_root,
        status_root=status_root,
        live_config_path=live_config_path,
        python_executable=python_executable,
        repository_source_roots=repository_source_roots,
        repository_integration_roots=repository_integration_roots,
        requirements_path=requirements_path,
    )
    # A direct promotion bypasses the watchdog wrapper, so prove that the
    # verifier-only child environment is complete before stopping the healthy
    # incumbent. This keeps a bad authority file from turning into downtime.
    supervisor_launch_environment(os.environ, authority_env_file=authority_env_file)
    rendered_bytes = (json.dumps(rendered, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    approval_queue_value = rendered.get("paths", {}).get("approval_queue")
    if not isinstance(approval_queue_value, str) or not approval_queue_value.strip():
        raise ValueError("rendered V2 config must define paths.approval_queue")
    approval_queue_path = Path(approval_queue_value).expanduser().absolute()
    incumbent_pid_path = _incumbent_pid_path(live_config_path, rendered)
    result: dict[str, Any] = {
        "schema_version": 2,
        "kind": "supervisor_v2_replacement",
        "recorded_at": _utc_now(),
        "candidate": identity,
        "live_config": str(live_config_path),
        "config_sha256": _sha256(rendered_bytes),
        "task_state_store": dict(rendered["task_state_store"]),
        "repository_source_roots": {
            repository_id: str(entry.get("local_path"))
            for repository_id, entry in (
                (rendered.get("coordination") or {}).get("repositories") or {}
            ).items()
            if isinstance(entry, dict) and entry.get("local_path")
        },
        "repository_integration_roots": {
            repository_id: str(entry.get("integration_path"))
            for repository_id, entry in (
                (rendered.get("coordination") or {}).get("repositories") or {}
            ).items()
            if isinstance(entry, dict) and entry.get("integration_path")
        },
        "approval_queue": str(approval_queue_path),
        "supervisor_verifier_env_file": str(authority_env_file) if authority_env_file else None,
        "incumbent_pid_file": str(incumbent_pid_path),
        "stopped_pid": None,
        "launched_pid": None,
        "outcome": "failed",
    }
    try:
        result["command_runtime_seal"] = seal_command_runtime(Path(identity["root"]))
        result["worker_sandbox_preflight"] = verify_worker_sandbox(
            Path(identity["root"])
        )
        result["execution_authorization_barrier_preflight"] = (
            verify_execution_authorization_barriers(
                Path(identity["root"]), python_executable=python_executable,
            )
        )
        ensure_approval_queue_marker(approval_queue_path)
        stopped_pid = stop_existing_supervisor(
            incumbent_pid_path, timeout_seconds=termination_timeout
        )
        result["stopped_pid"] = stopped_pid
        write_json_atomic(live_config_path, rendered)
        result["launched_pid"] = launch_v2_supervisor(
            rendered,
            identity=identity,
            status_root=status_root,
            authority_env_file=authority_env_file,
        )
        result["outcome"] = "launched"
        result["exit_code"] = 0
        try:
            result["coordination_code_sync"] = sync_coordination_root_code(
                Path(identity["root"]), status_root
            )
        except Exception as sync_exc:
            result["coordination_code_sync"] = {
                "outcome": "failed",
                "error": f"{type(sync_exc).__name__}: {sync_exc}",
            }
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["exit_code"] = 1
    _write_evidence(evidence_path, result)
    return result


def replace_supervisor(
    repo_root: Path,
    *,
    status_root: Path,
    live_config_path: Path,
    python_executable: Path,
    termination_timeout: float,
    evidence_path: Path | None = None,
    authority_env_file: Path | None = None,
    repository_source_roots: Mapping[str, Path | str] | None = None,
    repository_integration_roots: Mapping[str, Path | str] | None = None,
    requirements_path: Path | None = None,
) -> dict[str, Any]:
    """Validate and switch config while excluding the canonical merge owner."""

    with auto_integrator.lock_file(status_root / auto_integrator.DEFAULT_LOCK):
        return _replace_supervisor_locked(
            repo_root,
            status_root=status_root,
            live_config_path=live_config_path,
            python_executable=python_executable,
            termination_timeout=termination_timeout,
            evidence_path=evidence_path,
            authority_env_file=authority_env_file,
            repository_source_roots=repository_source_roots,
            repository_integration_roots=repository_integration_roots,
            requirements_path=requirements_path,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Exact V2 command source root.")
    parser.add_argument("--status-root", required=True)
    parser.add_argument("--live-config", default=str(LIVE_SUPERVISOR_CONFIG_PATH))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--requirements",
        default=None,
        help=(
            "Minimal supervisor dependency contract to preflight-check against "
            "--python before stopping the incumbent. Defaults to "
            "<repo>/.orchestrator/requirements.txt when that file exists."
        ),
    )
    parser.add_argument("--termination-timeout", type=float, default=15.0)
    parser.add_argument("--evidence-path")
    parser.add_argument(
        "--authority-env-file",
        help=(
            "Absolute mode-600 public-verifier environment file. Required when "
            "the calling environment does not already provide the verifier map."
        ),
    )
    parser.add_argument(
        "--repository-source-root",
        action="append",
        default=[],
        metavar="REPOSITORY_ID=/ABSOLUTE/GIT/ROOT",
        help="Render an absolute source checkout into coordination.repositories.",
    )
    parser.add_argument(
        "--repository-integration-root",
        action="append",
        default=[],
        metavar="REPOSITORY_ID=/ABSOLUTE/GIT/ROOT",
        help="Render a dedicated clean merge checkout into coordination.repositories.",
    )
    parser.add_argument("--promote", action="store_true", help="Stop and replace the runtime.")
    parser.add_argument("--discover-only", action="store_true", help="Render and validate only.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.promote == args.discover_only:
        parser.error("select exactly one of --promote or --discover-only")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = _path(args.repo)
    status_root = _path(args.status_root)
    live_config_path = _path(args.live_config)
    python_executable = _path(args.python)
    try:
        repository_source_roots = parse_repository_source_roots(
            args.repository_source_root
        )
        repository_integration_roots = parse_repository_integration_roots(
            args.repository_integration_root
        )
        if not python_executable.is_file():
            raise ValueError(f"python executable does not exist: {python_executable}")
        requirements_path = (
            _path(args.requirements)
            if args.requirements
            else repo_root / ".orchestrator" / "requirements.txt"
        )
        if not requirements_path.is_file():
            if args.requirements:
                raise ValueError(f"requirements file does not exist: {requirements_path}")
            requirements_path = None
        validated_root(status_root, label="status root", required=(".git", "ai-status.json"))
        if args.discover_only:
            rendered, identity = render_v2_config(
                repo_root,
                status_root=status_root,
                live_config_path=live_config_path,
                python_executable=python_executable,
                repository_source_roots=repository_source_roots,
                repository_integration_roots=repository_integration_roots,
                requirements_path=requirements_path,
            )
            result: dict[str, Any] = {
                "schema_version": 2,
                "kind": "supervisor_v2_replacement_preflight",
                "outcome": "ready",
                "candidate": identity,
                "live_config": str(live_config_path),
                "task_state_store": dict(rendered["task_state_store"]),
                "supervisor_command": rendered["watchdog"]["supervisor_command"],
                "execution_authorization_barrier_preflight": verify_execution_authorization_barriers(
                    Path(identity["root"]), python_executable=python_executable,
                ),
                "repository_source_roots": {
                    repository_id: str(entry.get("local_path"))
                    for repository_id, entry in (
                        (rendered.get("coordination") or {}).get("repositories") or {}
                    ).items()
                    if isinstance(entry, dict) and entry.get("local_path")
                },
                "repository_integration_roots": {
                    repository_id: str(entry.get("integration_path"))
                    for repository_id, entry in (
                        (rendered.get("coordination") or {}).get("repositories") or {}
                    ).items()
                    if isinstance(entry, dict) and entry.get("integration_path")
                },
            }
        else:
            evidence_path = _path(args.evidence_path) if args.evidence_path else None
            authority_env_file = (
                _path(args.authority_env_file) if args.authority_env_file else None
            )
            result = replace_supervisor(
                repo_root,
                status_root=status_root,
                live_config_path=live_config_path,
                python_executable=python_executable,
                termination_timeout=args.termination_timeout,
                evidence_path=evidence_path,
                authority_env_file=authority_env_file,
                repository_source_roots=repository_source_roots,
                repository_integration_roots=repository_integration_roots,
                requirements_path=requirements_path,
            )
    except (OSError, ValueError, auto_integrator.IntegrationLockError) as exc:
        result = {"outcome": "failed", "exit_code": 1, "error": f"{type(exc).__name__}: {exc}"}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"supervisor_v2_replacement={result['outcome']}")
        if result.get("error"):
            print(result["error"], file=sys.stderr)
    return int(result.get("exit_code", 0 if result.get("outcome") == "ready" else 1))


if __name__ == "__main__":
    raise SystemExit(main())
