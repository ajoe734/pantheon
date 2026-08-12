#!/usr/bin/env python3
"""Supervisor runtime promotion preflight and transactional operator.

The default CLI remains read-only.  An explicit ``--promote`` invocation uses
the immutable identity/preflight layers to perform a PID-generation-bound swap,
three-loop acceptance, and automatic rollback with durable evidence.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import io
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping, Protocol
from urllib.parse import urlsplit

from supervisor_runtime_health import (
    evaluate_runtime_health,
    pid_is_alive,
    resolved_coordinator_status_root,
    parse_utc_timestamp,
    lock_held,
)
from provision_live_supervisor_config import build_live_config, load_json_object
from migrate_task_state_store_v2 import (
    LEGACY_EVENT_TYPE,
    LEGACY_EVENT_VERSION,
    migrate as migrate_task_state_store_v2,
    snapshot_transaction,
)


HEX_40_PATTERN = re.compile(r"^[0-9a-f]{40}$")
TASK_BRIEF_PATH_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*\.md$")
SUPERVISOR_RUNTIME_LOG_PATH_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,249}\.log$"
)
ALLOWED_COMMAND_RUNTIMES_PREFIX = Path(
    "/home/lupin/pantheon-ci-deploy/command-runtimes"
)
ALLOWED_ROLLBACK_COMMAND_RUNTIMES_PREFIX = Path(
    "/home/lupin/pantheon-ci-deploy/rollback-command-runtimes"
)
LIVE_SUPERVISOR_CONFIG_PATH = Path(
    "/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json"
)
DEFAULT_PROMOTION_EVIDENCE_ROOT = (
    LIVE_SUPERVISOR_CONFIG_PATH.parent / "promotion-evidence"
)
TRUSTED_GITHUB_OWNER = "ajoe734"
TRUSTED_GITHUB_REPOSITORY = "pantheon"
TRUSTED_ORIGIN_DEV_URL = "https://github.com/ajoe734/pantheon.git"
TRUSTED_CANONICAL_ORIGIN_URL = "https://github.com/ajoe734/pantheon.git"
PROCFS_ROOT = Path("/proc")
SUPERVISOR_ENTRYPOINT_RELATIVE = PurePosixPath(
    ".orchestrator/supervisor.py"
)
PROCESS_ENVIRONMENT_ALLOWLIST = (
    "BRIDGE_SIGNING_PUBLIC_KEYS_JSON",
    "PANTHEON_COMMAND_ROOT",
    "PANTHEON_COMMAND_RUNTIME_SHA",
    "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON",
    "PANTHEON_STATUS_ROOT",
    "PYTHONDONTWRITEBYTECODE",
)
GOVERNED_LAUNCH_REQUIRED_ENVIRONMENT = (
    "BRIDGE_SIGNING_PUBLIC_KEYS_JSON",
    "PANTHEON_COMMAND_BASE_REF",
    "PANTHEON_COMMAND_REMOTE",
    "PANTHEON_COMMAND_ROOT",
    "PANTHEON_COMMAND_RUNTIME_SHA",
    "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON",
    "PANTHEON_STATUS_ROOT",
    "PYTHONDONTWRITEBYTECODE",
)
GOVERNED_LAUNCH_FORBIDDEN_ENVIRONMENT = frozenset(
    {
        "CLAUDE_CONFIG_DIR",
        "BRIDGE_SIGNING_KEY",
        "BRIDGE_SIGNING_PRIVATE_KEY",
        "BRIDGE_SIGNING_KEY_ID",
        "GH_CONFIG_DIR",
        "PANTHEON_STATUS_COMMAND_BASE_REF",
        "PANTHEON_STATUS_COMMAND_REMOTE",
        "PANTHEON_STATUS_COMMAND_ROOT",
        "PANTHEON_STATUS_COMMAND_SHA",
        "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY",
        "PANTHEON_CANONICAL_MUTATION_ASSERTION_PRIVATE_KEY",
        "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY_ID",
        "PANTHEON_TASK_STATE_EVENT_LOG",
        "PANTHEON_TASK_STATE_STORE_MODE",
        "PANTHEON_WORKTREE_ROOT",
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    }
)
GOVERNED_LAUNCH_FORBIDDEN_PREFIXES = (
    "GIT_",
    "ORCH_",
)
GOVERNED_LAUNCH_SOURCES = (
    ("supervisor", PurePosixPath(".orchestrator/supervisor.py"), False),
    (
        "watchdog_intent",
        PurePosixPath(".orchestrator/supervisor_watchdog.py"),
        True,
    ),
    ("watchdog_launcher", PurePosixPath("scripts/run-supervisor-watchdog.sh"), True),
    ("sync_dev_root", PurePosixPath("scripts/sync-dev-root.sh"), True),
    ("status_command_wrapper", PurePosixPath("scripts/ai-status.sh"), True),
    ("status_command", PurePosixPath("scripts/ai_status.py"), False),
    (
        "command_runtime_config",
        PurePosixPath("scripts/provision_live_supervisor_config.py"),
        False,
    ),
)

# These files are created by the supervisor/status machinery even when its
# durable state root is external.  This is intentionally a finite allowlist;
# in particular, config/state JSON and executable Python below .orchestrator
# are not accepted as generated runtime debris.
ALLOWED_GENERATED_UNTRACKED_FILES = frozenset(
    {
        PurePosixPath(".orchestrator/activity-audit.lock"),
        PurePosixPath(".orchestrator/approval-queue.lock"),
        PurePosixPath(".orchestrator/assistant-dev-packets/.drain.lock"),
        PurePosixPath(".orchestrator/assistant-dev-packets/.queue.lock"),
        PurePosixPath(".orchestrator/auto-integrator.lock"),
        PurePosixPath(".orchestrator/auto_commit_archive.lock"),
        PurePosixPath(".orchestrator/dashboard-autostart.lock"),
        PurePosixPath(".orchestrator/runtime-admission.lock"),
        PurePosixPath(".orchestrator/status-derived-views.lock"),
        PurePosixPath(".orchestrator/supervisor.lock"),
        PurePosixPath(".orchestrator/task-state.lock"),
    }
)
ALLOWED_GENERATED_UNTRACKED_DIRECTORIES = frozenset(
    {
        PurePosixPath(".orchestrator/logs"),
    }
)

@dataclass(frozen=True)
class GitRemoteIdentity:
    raw_url: str
    transport: str
    host: str
    owner: str
    repository: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repository}"


@dataclass(frozen=True)
class FilesystemIdentity:
    device: int
    inode: int
    mode: int


@dataclass(frozen=True)
class PathComponentIdentity:
    path: Path
    identity: FilesystemIdentity


@dataclass(frozen=True)
class CandidateRootHandle:
    path: Path
    descriptor: int
    identity: FilesystemIdentity
    git_descriptor: int
    git_identity: FilesystemIdentity
    git_objects_descriptor: int
    git_objects_identity: FilesystemIdentity
    git_config_descriptor: int
    git_config_identity: FilesystemIdentity
    git_head_descriptor: int
    git_head_identity: FilesystemIdentity
    git_index_descriptor: int
    git_index_identity: FilesystemIdentity


@dataclass(frozen=True)
class TrustedDevIdentity:
    commit: str
    candidate_commit_tree: str


@dataclass(frozen=True, order=True)
class TrackedGitlinkIdentity:
    relative_path: str
    commit: str


@dataclass(frozen=True)
class ProcessGeneration:
    pid: int
    starttime_ticks: int
    # Linux scheduler state is an observation, not generation identity.  A
    # live process can legitimately move between R and S while a promotion
    # reads the incumbent's state.  PID reuse is instead bound by the stable
    # (pid, starttime_ticks) pair; zombie rejection remains explicit at each
    # guarded process read.
    state: str = field(compare=False)


@dataclass(frozen=True)
class ProcessCwdIdentity:
    path: Path
    device: int
    inode: int


@dataclass(frozen=True)
class SupervisorAdmissionLockIdentity:
    path: Path
    device: int
    inode: int
    byte_length: int
    sha256: str
    mtime_ns: int
    ctime_ns: int
    # ``/proc/locks`` assigns this row ordinal while rendering the global lock
    # table.  Unrelated lock churn can renumber it even when this exact inode,
    # owner PID generation, and FLOCK mode have not changed.  Keep it in
    # evidence, but never use it as a promotion identity/CAS field.
    kernel_lock_id: str = field(compare=False)
    kernel_lock_kind: str
    kernel_lock_class: str
    kernel_lock_mode: str
    kernel_lock_start: str
    kernel_lock_end: str
    owner_pid: int
    owner_starttime_ticks: int


@dataclass(frozen=True)
class ExpectedSupervisorProcessContract:
    executable: Path
    argv: tuple[str, ...]
    entrypoint: Path
    config_path: Path
    cwd: Path
    cwd_device: int
    cwd_inode: int
    cwd_commit: str
    cwd_tree: str
    command_root: str
    runtime_sha: str
    status_root: str
    admission_lock_path: Path


@dataclass(frozen=True)
class SupervisorProcessIdentity:
    generation: ProcessGeneration
    executable: Path
    argv: tuple[str, ...]
    entrypoint: Path
    config_path: Path
    cwd: ProcessCwdIdentity
    cwd_commit: str
    cwd_tree: str
    environment_contract: tuple[tuple[str, str], ...]
    admission_lock: SupervisorAdmissionLockIdentity


@dataclass(frozen=True)
class LaunchFileIdentity:
    role: str
    path: Path
    device: int
    inode: int
    mode: int
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class LaunchJournalFileIdentity:
    role: str
    path: Path
    device: int
    inode: int
    mode: int
    captured_size: int
    prefix_sha256: str
    captured_at_size: int = field(compare=False)



@dataclass(frozen=True)
class GovernedSupervisorLaunchContract:
    interpreter: LaunchFileIdentity
    argv: tuple[str, ...]
    cwd: Path
    cwd_device: int
    cwd_inode: int
    required_environment: tuple[tuple[str, str], ...]
    scrubbed_environment_names_sha256: str
    scrubbed_environment_variable_count: int
    source_identities: tuple[LaunchFileIdentity, ...]
    status_command_root: Path
    status_command_runtime_sha: str
    status_command_remote: str
    status_command_base_ref: str
    status_root: Path
    task_state_event_log: Path
    worker_worktree_root: Path
    intentional_restart_path: Path
    stdout_log_path: Path
    stderr_log_path: Path
    task_state_event_log_identity: LaunchJournalFileIdentity | None = None



@dataclass(frozen=True)
class SupervisorConfigVariant:
    """One pre-rendered live-config generation bound to one command root."""

    command_root: Path
    supervisor_argv: tuple[str, ...]
    content: bytes
    byte_length: int
    sha256: str


class LaunchFilesystem(Protocol):
    def capture_regular_file(
        self,
        path: Path,
        *,
        role: str,
        require_executable: bool,
    ) -> LaunchFileIdentity: ...

    def capture_append_only_journal(
        self,
        path: Path,
        *,
        role: str,
        baseline_identity: LaunchJournalFileIdentity | None = None,
    ) -> LaunchJournalFileIdentity: ...

    def capture_directory(self, path: Path, *, label: str) -> FilesystemIdentity: ...

    def directory_is_writable(self, path: Path) -> bool: ...

    def path_exists(self, path: Path) -> bool: ...

    def file_is_writable(self, path: Path) -> bool: ...



class RuntimeProcessReader(Protocol):
    def list_pids(self) -> tuple[int, ...]: ...

    def read_generation(self, pid: int) -> ProcessGeneration: ...

    def read_argv(self, pid: int) -> tuple[str, ...]: ...

    def read_executable(self, pid: int) -> Path: ...

    def read_cwd(self, pid: int) -> ProcessCwdIdentity: ...

    def read_environment_contract(self, pid: int) -> dict[str, str]: ...

    def read_admission_lock(
        self,
        path: Path,
    ) -> SupervisorAdmissionLockIdentity: ...


@dataclass(frozen=True)
class CandidateRuntimeIdentity:
    candidate_root: Path
    candidate_root_device: int
    candidate_root_inode: int
    git_directory_device: int
    git_directory_inode: int
    git_objects_device: int
    git_objects_inode: int
    git_config_device: int
    git_config_inode: int
    git_head_device: int
    git_head_inode: int
    git_index_device: int
    git_index_inode: int
    basename: str
    head_commit: str
    tracked_tree_identity: str
    accepted_dev_commit: str
    remote_url: str
    canonical_remote: str
    repository_slug: str
    config_path: Path
    config_device: int
    config_inode: int
    config_path_components: tuple[PathComponentIdentity, ...]
    config_bytes: bytes
    config_byte_length: int
    config_sha256: str
    def verify_against_live_config(self, live_config_path: Path) -> None:
        """Re-read and compare the exact live-config path and byte identity."""
        if self.config_path != LIVE_SUPERVISOR_CONFIG_PATH:
            raise ValueError(
                "Captured config path is not the exact live supervisor config: "
                f"{self.config_path}"
            )
        snapshot = _capture_config_bytes(
            live_config_path,
            expected_path=LIVE_SUPERVISOR_CONFIG_PATH,
        )
        content, file_identity, path_components = snapshot
        if len(content) != self.config_byte_length:
            raise ValueError(
                "Config byte length drift: "
                f"{len(content)} != {self.config_byte_length}"
            )
        if content != self.config_bytes:
            raise ValueError("Config bytes drift detected")
        digest = hashlib.sha256(content).hexdigest()
        if digest != self.config_sha256:
            raise ValueError(
                f"Config SHA256 drift: {digest} != {self.config_sha256}"
            )
        if (
            file_identity.device != self.config_device
            or file_identity.inode != self.config_inode
        ):
            raise ValueError("Config file identity drift detected")
        if path_components != self.config_path_components:
            raise ValueError("Config path component identity drift detected")

    def verify_immutable_snapshot(
        self,
        *,
        require_accepted_dev_identity: bool = True,
    ) -> None:
        """Revalidate captured local identity and, normally, accepted dev tip."""
        root_handle = _open_candidate_root_handle(self.candidate_root)
        try:
            if root_handle.path != self.candidate_root:
                raise ValueError("Candidate root path drift detected")
            if (
                root_handle.identity.device != self.candidate_root_device
                or root_handle.identity.inode != self.candidate_root_inode
            ):
                raise ValueError("Candidate root file identity drift detected")
            # Git may atomically refresh its index while running the read-only
            # cleanliness probes used to build this immutable identity.  The
            # index is not executable authority: revalidate its current
            # contents through the complete cleanliness checks below.  The
            # repository control directory, objects, config and HEAD remain
            # exact identity bindings and must never be rebound here.
            current_static_git_identity = (
                root_handle.git_identity.device,
                root_handle.git_identity.inode,
                root_handle.git_objects_identity.device,
                root_handle.git_objects_identity.inode,
                root_handle.git_config_identity.device,
                root_handle.git_config_identity.inode,
                root_handle.git_head_identity.device,
                root_handle.git_head_identity.inode,
            )
            captured_static_git_identity = (
                self.git_directory_device,
                self.git_directory_inode,
                self.git_objects_device,
                self.git_objects_inode,
                self.git_config_device,
                self.git_config_inode,
                self.git_head_device,
                self.git_head_inode,
            )
            if current_static_git_identity != captured_static_git_identity:
                raise ValueError("Candidate Git metadata identity drift detected")

            remote_url = parse_origin_url(root_handle)
            remote = validate_remote_url(remote_url)
            if (
                remote_url != self.remote_url
                or remote.slug != self.repository_slug
                or f"github.com/{remote.slug}" != self.canonical_remote
            ):
                raise ValueError("Candidate remote identity drift detected")

            if require_accepted_dev_identity:
                head, tree, accepted = _capture_git_identity(
                    root_handle,
                    self.basename,
                )
            else:
                head, tree = _read_head_tree(root_handle)
                accepted = None
            if head != self.head_commit:
                raise ValueError(
                    f"Candidate HEAD drift: {head} != {self.head_commit}"
                )
            if tree != self.tracked_tree_identity:
                raise ValueError(
                    "Candidate tracked tree drift: "
                    f"{tree} != {self.tracked_tree_identity}"
                )
            if (
                accepted is not None
                and accepted.commit != self.accepted_dev_commit
            ):
                raise ValueError(
                    "Accepted origin/dev drift during immutable promotion snapshot: "
                    f"{accepted.commit} != {self.accepted_dev_commit}"
                )
            verify_working_tree_cleanliness(
                root_handle,
                expected_head=self.head_commit,
                expected_tree=self.tracked_tree_identity,
            )
            _assert_candidate_handle_path(root_handle)
        finally:
            _close_candidate_root_handle(root_handle)
        self.verify_against_live_config(self.config_path)


def _subprocess_environment() -> dict[str, str]:
    # Git exposes config, URL rewriting, object lookup, transport helpers and
    # protocol policy through GIT_* variables.  A trusted-dev fetch must not
    # inherit any of them from the invoking supervisor environment.
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("GIT_")
    }
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "LC_ALL": "C",
        }
    )
    return env


def _run_git(
    cwd: Path | CandidateRootHandle,
    *args: str,
    environment_overrides: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = _subprocess_environment()
    if isinstance(cwd, CandidateRootHandle):
        cwd_arg = Path(f"/dev/fd/{cwd.descriptor}")
        pass_fds = (
            cwd.descriptor,
            cwd.git_descriptor,
            cwd.git_objects_descriptor,
            cwd.git_config_descriptor,
            cwd.git_head_descriptor,
            cwd.git_index_descriptor,
        )
        display_cwd = cwd.path
        env.update(
            {
                "GIT_DIR": f"/dev/fd/{cwd.git_descriptor}",
                "GIT_WORK_TREE": f"/dev/fd/{cwd.descriptor}",
                "GIT_COMMON_DIR": f"/dev/fd/{cwd.git_descriptor}",
                "GIT_OBJECT_DIRECTORY": (
                    f"/dev/fd/{cwd.git_objects_descriptor}"
                ),
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_INDEX_FILE": f"/dev/fd/{cwd.git_index_descriptor}",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_COUNT": "3",
                "GIT_CONFIG_KEY_0": "core.fsmonitor",
                "GIT_CONFIG_VALUE_0": "false",
                "GIT_CONFIG_KEY_1": "core.untrackedCache",
                "GIT_CONFIG_VALUE_1": "false",
                "GIT_CONFIG_KEY_2": "core.hooksPath",
                "GIT_CONFIG_VALUE_2": os.devnull,
            }
        )
    else:
        cwd_arg = cwd
        pass_fds = ()
        display_cwd = cwd
    if environment_overrides is not None:
        env.update(environment_overrides)
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd_arg,
            capture_output=True,
            check=True,
            env=env,
            pass_fds=pass_fds,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        stderr = getattr(exc, "stderr", None)
        detail = str(stderr or exc).strip()
        raise ValueError(
            f"git {' '.join(args)} failed in {display_cwd}: {detail}"
        ) from exc


def _run_git_bytes(
    cwd: Path | CandidateRootHandle,
    *args: str,
    environment_overrides: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    env = _subprocess_environment()
    if isinstance(cwd, CandidateRootHandle):
        cwd_arg = Path(f"/dev/fd/{cwd.descriptor}")
        pass_fds = (
            cwd.descriptor,
            cwd.git_descriptor,
            cwd.git_objects_descriptor,
            cwd.git_config_descriptor,
            cwd.git_head_descriptor,
            cwd.git_index_descriptor,
        )
        display_cwd = cwd.path
        env.update(
            {
                "GIT_DIR": f"/dev/fd/{cwd.git_descriptor}",
                "GIT_WORK_TREE": f"/dev/fd/{cwd.descriptor}",
                "GIT_COMMON_DIR": f"/dev/fd/{cwd.git_descriptor}",
                "GIT_OBJECT_DIRECTORY": f"/dev/fd/{cwd.git_objects_descriptor}",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_INDEX_FILE": f"/dev/fd/{cwd.git_index_descriptor}",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_COUNT": "3",
                "GIT_CONFIG_KEY_0": "core.fsmonitor",
                "GIT_CONFIG_VALUE_0": "false",
                "GIT_CONFIG_KEY_1": "core.untrackedCache",
                "GIT_CONFIG_VALUE_1": "false",
                "GIT_CONFIG_KEY_2": "core.hooksPath",
                "GIT_CONFIG_VALUE_2": os.devnull,
            }
        )
    else:
        cwd_arg = cwd
        pass_fds = ()
        display_cwd = cwd
    if environment_overrides is not None:
        env.update(environment_overrides)
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd_arg,
            capture_output=True,
            check=True,
            env=env,
            pass_fds=pass_fds,
            text=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        stderr = getattr(exc, "stderr", None)
        detail = (
            stderr.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes)
            else str(stderr or exc)
        ).strip()
        raise ValueError(
            f"git {' '.join(args)} failed in {display_cwd}: {detail}"
        ) from exc


def _git_output(cwd: Path | CandidateRootHandle, *args: str) -> str:
    return _run_git(cwd, *args).stdout.strip()


def _run_mutable_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run read-only Git discovery against a mutable incumbent checkout."""
    return _run_git(
        cwd,
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        *args,
        environment_overrides={
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        },
    )


def _mutable_git_output(cwd: Path, *args: str) -> str:
    return _run_mutable_git(cwd, *args).stdout.strip()


def _validate_absolute_identity_path(path: Path, *, label: str) -> None:
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path: {path}")
    if ".." in path.parts:
        raise ValueError(f"{label} cannot contain '..': {path}")


def _filesystem_identity(descriptor: int) -> FilesystemIdentity:
    descriptor_stat = os.fstat(descriptor)
    return FilesystemIdentity(
        device=descriptor_stat.st_dev,
        inode=descriptor_stat.st_ino,
        mode=descriptor_stat.st_mode,
    )


def _identity_from_stat(path_stat: os.stat_result) -> FilesystemIdentity:
    return FilesystemIdentity(
        device=path_stat.st_dev,
        inode=path_stat.st_ino,
        mode=path_stat.st_mode,
    )


def _move_descriptor_above_standard_streams(descriptor: int) -> int:
    """Keep descriptor-bound identities out of subprocess stdio slots.

    A daemon may start with one or more of descriptors 0, 1, and 2 closed. In
    that case ``os.open`` reuses the vacant number. These identity descriptors
    are later exposed to Git through ``/dev/fd`` while
    ``subprocess.run(capture_output=True)`` installs its own child-side stdout
    and stderr pipes. Leaving a candidate directory on fd 1 or 2 therefore
    turns the same ``/dev/fd/<n>`` into a pipe in the child and produces a
    misleading ENOTDIR even though the descriptor is a directory in the
    promotion process.

    F_DUPFD_CLOEXEC allocates a descriptor at or above 3 while retaining the
    explicit ``pass_fds`` boundary used for Git children.
    """
    if descriptor > 2:
        return descriptor
    try:
        duplicate = fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 3)
    except BaseException:
        os.close(descriptor)
        raise
    os.close(descriptor)
    return duplicate


def _open_directory_descriptor(path: Path, *, label: str) -> int:
    """Open an absolute directory one no-follow component at a time."""
    _validate_absolute_identity_path(path, label=label)
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = _move_descriptor_above_standard_streams(
        os.open(path.anchor, flags)
    )
    traversed = Path(path.anchor)
    try:
        for component in path.parts[1:]:
            traversed = traversed / component
            try:
                next_descriptor = _move_descriptor_above_standard_streams(
                    os.open(
                        component,
                        flags,
                        dir_fd=descriptor,
                    )
                )
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    f"{label} does not exist: {traversed}"
                ) from exc
            except OSError as exc:
                raise ValueError(
                    f"{label} contains a symlink or non-directory component: "
                    f"{traversed}: {exc}"
                ) from exc
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _capture_directory_component_identities(
    path: Path,
    *,
    label: str,
) -> tuple[PathComponentIdentity, ...]:
    """Capture every no-follow directory identity from / through path."""
    _validate_absolute_identity_path(path, label=label)
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = _move_descriptor_above_standard_streams(
        os.open(path.anchor, flags)
    )
    traversed = Path(path.anchor)
    components = [
        PathComponentIdentity(
            path=traversed,
            identity=_filesystem_identity(descriptor),
        )
    ]
    try:
        for component in path.parts[1:]:
            traversed = traversed / component
            try:
                next_descriptor = _move_descriptor_above_standard_streams(
                    os.open(
                        component,
                        flags,
                        dir_fd=descriptor,
                    )
                )
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    f"{label} does not exist: {traversed}"
                ) from exc
            except OSError as exc:
                raise ValueError(
                    f"{label} contains a symlink or non-directory component: "
                    f"{traversed}: {exc}"
                ) from exc
            os.close(descriptor)
            descriptor = next_descriptor
            components.append(
                PathComponentIdentity(
                    path=traversed,
                    identity=_filesystem_identity(descriptor),
                )
            )
        return tuple(components)
    finally:
        os.close(descriptor)


def _assert_path_component_identities(
    expected: tuple[PathComponentIdentity, ...],
    *,
    label: str,
) -> None:
    if not expected:
        raise ValueError(f"{label} component identity is empty")
    path = expected[-1].path
    if stat.S_ISDIR(expected[-1].identity.mode):
        actual = _capture_directory_component_identities(path, label=label)
    else:
        parent_components = _capture_directory_component_identities(
            path.parent,
            label=label,
        )
        descriptor = _open_path_descriptor(
            path,
            label=label,
            require_directory=False,
        )
        try:
            actual = parent_components + (
                PathComponentIdentity(
                    path=path,
                    identity=_filesystem_identity(descriptor),
                ),
            )
        finally:
            os.close(descriptor)
    if actual != expected:
        raise ValueError(f"{label} component changed during identity capture: {path}")


def _open_relative_descriptor(
    parent_descriptor: int,
    name: str,
    *,
    label: str,
    require_directory: bool,
) -> int:
    if not name or "/" in name or name in {".", ".."}:
        raise ValueError(f"{label} must be one direct path component: {name!r}")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    if require_directory:
        flags |= os.O_DIRECTORY
    try:
        descriptor = _move_descriptor_above_standard_streams(
            os.open(name, flags, dir_fd=parent_descriptor)
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} does not exist") from exc
    except OSError as exc:
        raise ValueError(f"{label} is a symlink or has the wrong type: {exc}") from exc

    try:
        opened_stat = os.fstat(descriptor)
        entry_stat = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISLNK(entry_stat.st_mode):
            raise ValueError(f"{label} is a symlink")
        if (entry_stat.st_dev, entry_stat.st_ino) != (
            opened_stat.st_dev,
            opened_stat.st_ino,
        ):
            raise ValueError(f"{label} changed while it was opened")
        if require_directory and not stat.S_ISDIR(opened_stat.st_mode):
            raise ValueError(f"{label} is not a directory")
        if not require_directory and not stat.S_ISREG(opened_stat.st_mode):
            raise ValueError(f"{label} is not a regular file")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _assert_relative_identity(
    parent_descriptor: int,
    name: str,
    expected: FilesystemIdentity,
    *,
    label: str,
    require_directory: bool,
) -> None:
    descriptor = _open_relative_descriptor(
        parent_descriptor,
        name,
        label=label,
        require_directory=require_directory,
    )
    try:
        if _filesystem_identity(descriptor) != expected:
            raise ValueError(f"{label} identity changed")
    finally:
        os.close(descriptor)


def _assert_relative_entry_absent(
    parent_descriptor: int,
    name: str,
    *,
    label: str,
) -> None:
    try:
        os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError(f"Cannot validate {label}: {exc}") from exc
    raise ValueError(f"Forbidden {label} is present")


def _open_path_descriptor(
    path: Path,
    *,
    label: str,
    require_directory: bool,
) -> int:
    """Open a leaf relative to a descriptor-bound, symlink-free parent."""
    _validate_absolute_identity_path(path, label=label)
    if path == Path(path.anchor):
        if not require_directory:
            raise ValueError(f"{label} cannot use the filesystem root as a file")
        return _open_directory_descriptor(path, label=label)

    parent_descriptor = _open_directory_descriptor(path.parent, label=label)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    if require_directory:
        flags |= os.O_DIRECTORY
    try:
        try:
            descriptor = _move_descriptor_above_standard_streams(
                os.open(path.name, flags, dir_fd=parent_descriptor)
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"{label} does not exist: {path}") from exc
        except OSError as exc:
            raise ValueError(
                f"{label} is a symlink or has the wrong file type: {path}: {exc}"
            ) from exc

        opened_stat = os.fstat(descriptor)
        entry_stat = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISLNK(entry_stat.st_mode):
            raise ValueError(f"{label} is a symlink: {path}")
        if (entry_stat.st_dev, entry_stat.st_ino) != (
            opened_stat.st_dev,
            opened_stat.st_ino,
        ):
            raise ValueError(f"{label} changed while it was opened: {path}")
        if require_directory and not stat.S_ISDIR(opened_stat.st_mode):
            raise ValueError(f"{label} is not a directory: {path}")
        if not require_directory and not stat.S_ISREG(opened_stat.st_mode):
            raise ValueError(f"{label} is not a regular file: {path}")
        return descriptor
    except BaseException:
        if "descriptor" in locals():
            os.close(descriptor)
        raise
    finally:
        os.close(parent_descriptor)


def _assert_path_identity(
    path: Path,
    expected: FilesystemIdentity,
    *,
    label: str,
    require_directory: bool,
) -> None:
    descriptor = _open_path_descriptor(
        path,
        label=label,
        require_directory=require_directory,
    )
    try:
        path_stat = os.fstat(descriptor)
        actual = FilesystemIdentity(
            device=path_stat.st_dev,
            inode=path_stat.st_ino,
            mode=path_stat.st_mode,
        )
        if actual != expected:
            raise ValueError(f"{label} changed during identity capture: {path}")
    finally:
        os.close(descriptor)


def _close_descriptors(*descriptors: int) -> None:
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _close_candidate_root_handle(handle: CandidateRootHandle) -> None:
    _close_descriptors(
        handle.git_index_descriptor,
        handle.git_head_descriptor,
        handle.git_config_descriptor,
        handle.git_objects_descriptor,
        handle.git_descriptor,
        handle.descriptor,
    )


def _assert_optional_git_file_is_local(
    parent_descriptor: int,
    name: str,
    *,
    label: str,
) -> None:
    try:
        descriptor = _open_relative_descriptor(
            parent_descriptor,
            name,
            label=label,
            require_directory=False,
        )
    except FileNotFoundError:
        return
    try:
        if os.fstat(descriptor).st_nlink != 1:
            raise ValueError(f"{label} must not be hard-linked")
    finally:
        os.close(descriptor)


def _assert_git_metadata_tree_has_no_symlinks(
    descriptor: int,
    *,
    label: str,
    expected_device: int,
) -> None:
    """Reject aliases, mount escapes, and non-file entries in a Git tree."""
    if os.fstat(descriptor).st_dev != expected_device:
        raise ValueError(f"{label} escaped the candidate filesystem")
    pending: list[tuple[int, str]] = [(os.dup(descriptor), label)]
    entries_seen = 0
    try:
        while pending:
            current, current_label = pending.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        entries_seen += 1
                        if entries_seen > 100_000:
                            raise ValueError(
                                f"{label} exceeds the bounded metadata entry limit"
                            )
                        entry_label = f"{current_label}/{entry.name}"
                        entry_stat = entry.stat(follow_symlinks=False)
                        if stat.S_ISLNK(entry_stat.st_mode):
                            raise ValueError(f"{entry_label} cannot be a symlink")
                        if entry_stat.st_dev != expected_device:
                            raise ValueError(
                                f"{entry_label} escaped the candidate filesystem"
                            )
                        if stat.S_ISDIR(entry_stat.st_mode):
                            child = _open_relative_descriptor(
                                current,
                                entry.name,
                                label=entry_label,
                                require_directory=True,
                            )
                            pending.append((child, entry_label))
                        elif stat.S_ISREG(entry_stat.st_mode):
                            child = _open_relative_descriptor(
                                current,
                                entry.name,
                                label=entry_label,
                                require_directory=False,
                            )
                            try:
                                if os.fstat(child).st_dev != expected_device:
                                    raise ValueError(
                                        f"{entry_label} escaped the candidate filesystem"
                                    )
                            finally:
                                os.close(child)
                        else:
                            raise ValueError(
                                f"{entry_label} is not regular Git metadata"
                            )
            finally:
                os.close(current)
    except BaseException:
        _close_descriptors(*(item[0] for item in pending))
        raise


def _assert_optional_git_tree_is_local(
    parent_descriptor: int,
    name: str,
    *,
    label: str,
    expected_device: int,
) -> None:
    try:
        descriptor = _open_relative_descriptor(
            parent_descriptor,
            name,
            label=label,
            require_directory=True,
        )
    except FileNotFoundError:
        return
    try:
        _assert_git_metadata_tree_has_no_symlinks(
            descriptor,
            label=label,
            expected_device=expected_device,
        )
    finally:
        os.close(descriptor)


def _assert_no_git_alternates(objects_descriptor: int) -> None:
    try:
        info_descriptor = _open_relative_descriptor(
            objects_descriptor,
            "info",
            label="Candidate Git objects/info directory",
            require_directory=True,
        )
    except FileNotFoundError:
        return
    try:
        _assert_relative_entry_absent(
            info_descriptor,
            "alternates",
            label="Candidate Git objects/info/alternates",
        )
    finally:
        os.close(info_descriptor)


def _assert_candidate_git_metadata(handle: CandidateRootHandle) -> None:
    metadata_identities = (
        handle.git_identity,
        handle.git_objects_identity,
        handle.git_config_identity,
        handle.git_head_identity,
        handle.git_index_identity,
    )
    if any(
        identity.device != handle.identity.device
        for identity in metadata_identities
    ):
        raise ValueError("Candidate Git metadata must stay on the candidate filesystem")
    for descriptor, label in (
        (handle.git_config_descriptor, "Candidate Git config"),
        (handle.git_head_descriptor, "Candidate Git HEAD"),
        (handle.git_index_descriptor, "Candidate Git index"),
    ):
        if os.fstat(descriptor).st_nlink != 1:
            raise ValueError(f"{label} must not be hard-linked")

    _assert_relative_identity(
        handle.descriptor,
        ".git",
        handle.git_identity,
        label="Candidate Git directory",
        require_directory=True,
    )
    _assert_relative_identity(
        handle.git_descriptor,
        "objects",
        handle.git_objects_identity,
        label="Candidate Git objects directory",
        require_directory=True,
    )
    _assert_relative_identity(
        handle.git_descriptor,
        "config",
        handle.git_config_identity,
        label="Candidate Git config",
        require_directory=False,
    )
    _assert_relative_identity(
        handle.git_descriptor,
        "HEAD",
        handle.git_head_identity,
        label="Candidate Git HEAD",
        require_directory=False,
    )
    _assert_relative_identity(
        handle.git_descriptor,
        "index",
        handle.git_index_identity,
        label="Candidate Git index",
        require_directory=False,
    )
    _assert_relative_entry_absent(
        handle.git_descriptor,
        "commondir",
        label="Candidate Git commondir pointer",
    )
    _assert_git_metadata_tree_has_no_symlinks(
        handle.git_objects_descriptor,
        label="Candidate Git objects directory",
        expected_device=handle.identity.device,
    )
    _assert_no_git_alternates(handle.git_objects_descriptor)
    _assert_optional_git_tree_is_local(
        handle.git_descriptor,
        "refs",
        label="Candidate Git refs directory",
        expected_device=handle.identity.device,
    )
    _assert_optional_git_tree_is_local(
        handle.git_descriptor,
        "info",
        label="Candidate Git info directory",
        expected_device=handle.identity.device,
    )
    _assert_optional_git_file_is_local(
        handle.git_descriptor,
        "packed-refs",
        label="Candidate Git packed-refs",
    )
    _assert_optional_git_file_is_local(
        handle.git_descriptor,
        "shallow",
        label="Candidate Git shallow boundary",
    )

    config_names = _run_git(
        handle,
        "config",
        "--file",
        f"/dev/fd/{handle.git_config_descriptor}",
        "--no-includes",
        "--name-only",
        "--list",
    ).stdout.splitlines()
    if any(
        name.lower().startswith(("include.", "includeif."))
        for name in config_names
    ):
        raise ValueError("Candidate Git config cannot include external config")

    git_dir = Path(_git_output(handle, "rev-parse", "--absolute-git-dir"))
    common_dir = Path(_git_output(handle, "rev-parse", "--git-common-dir"))
    try:
        git_dir_identity = _identity_from_stat(os.stat(git_dir))
        common_dir_identity = _identity_from_stat(os.stat(common_dir))
    except OSError as exc:
        raise ValueError(f"Candidate Git directory identity is unavailable: {exc}") from exc
    if (
        git_dir_identity != handle.git_identity
        or common_dir_identity != handle.git_identity
    ):
        raise ValueError("Candidate Git directory/common directory escaped the root")
    if _git_output(handle, "rev-parse", "--is-inside-work-tree") != "true":
        raise ValueError("Candidate Git metadata is not a worktree repository")
    if _git_output(handle, "rev-parse", "--is-bare-repository") != "false":
        raise ValueError("Candidate Git metadata unexpectedly identifies a bare repository")


def _open_candidate_root_handle(
    candidate_path: Path,
    *,
    require_immutable_location: bool = True,
) -> CandidateRootHandle:
    path = candidate_path if isinstance(candidate_path, Path) else Path(candidate_path)
    trusted_parent = ALLOWED_COMMAND_RUNTIMES_PREFIX
    trusted_rollback_parent = ALLOWED_ROLLBACK_COMMAND_RUNTIMES_PREFIX

    if (
        require_immutable_location
        and path.parent != trusted_parent
        and path.parent != trusted_rollback_parent
    ):
        raise ValueError(
            f"Candidate root {path} is not a direct child of {trusted_parent} or {trusted_rollback_parent}"
        )
    if require_immutable_location and not HEX_40_PATTERN.fullmatch(path.name):
        raise ValueError(
            "Candidate root basename is not a lowercase 40-hex commit: "
            f"{path.name}"
        )
    descriptor = _open_path_descriptor(
        path,
        label="Candidate root",
        require_directory=True,
    )
    git_descriptor = -1
    git_objects_descriptor = -1
    git_config_descriptor = -1
    git_head_descriptor = -1
    git_index_descriptor = -1
    try:
        git_descriptor = _open_relative_descriptor(
            descriptor,
            ".git",
            label="Candidate Git directory",
            require_directory=True,
        )
        git_objects_descriptor = _open_relative_descriptor(
            git_descriptor,
            "objects",
            label="Candidate Git objects directory",
            require_directory=True,
        )
        git_config_descriptor = _open_relative_descriptor(
            git_descriptor,
            "config",
            label="Candidate Git config",
            require_directory=False,
        )
        git_head_descriptor = _open_relative_descriptor(
            git_descriptor,
            "HEAD",
            label="Candidate Git HEAD",
            require_directory=False,
        )
        git_index_descriptor = _open_relative_descriptor(
            git_descriptor,
            "index",
            label="Candidate Git index",
            require_directory=False,
        )
        handle = CandidateRootHandle(
            path=path,
            descriptor=descriptor,
            identity=_filesystem_identity(descriptor),
            git_descriptor=git_descriptor,
            git_identity=_filesystem_identity(git_descriptor),
            git_objects_descriptor=git_objects_descriptor,
            git_objects_identity=_filesystem_identity(git_objects_descriptor),
            git_config_descriptor=git_config_descriptor,
            git_config_identity=_filesystem_identity(git_config_descriptor),
            git_head_descriptor=git_head_descriptor,
            git_head_identity=_filesystem_identity(git_head_descriptor),
            git_index_descriptor=git_index_descriptor,
            git_index_identity=_filesystem_identity(git_index_descriptor),
        )
        _assert_candidate_handle_path(handle)
    except BaseException:
        _close_descriptors(
            git_index_descriptor,
            git_head_descriptor,
            git_config_descriptor,
            git_objects_descriptor,
            git_descriptor,
            descriptor,
        )
        raise
    return handle


def _assert_candidate_handle_path(handle: CandidateRootHandle) -> None:
    _assert_path_identity(
        handle.path,
        handle.identity,
        label="Candidate root",
        require_directory=True,
    )
    _assert_candidate_git_metadata(handle)


def _capture_candidate_root(candidate_path: Path) -> tuple[Path, FilesystemIdentity]:
    handle = _open_candidate_root_handle(candidate_path)
    try:
        return handle.path, handle.identity
    finally:
        _close_candidate_root_handle(handle)


def resolve_candidate_root(candidate_path: Path) -> Path:
    """Return an exact, direct, symlink-free immutable runtime candidate root."""
    return _capture_candidate_root(candidate_path)[0]


def _candidate_handle(
    candidate_root: Path | CandidateRootHandle,
) -> tuple[CandidateRootHandle, bool]:
    if isinstance(candidate_root, CandidateRootHandle):
        return candidate_root, False
    return _open_candidate_root_handle(candidate_root), True


def parse_origin_url(candidate_root: Path | CandidateRootHandle) -> str:
    """Read the single raw local remote.origin.url without URL rewriting."""
    handle, close_handle = _candidate_handle(candidate_root)
    try:
        _assert_candidate_git_metadata(handle)
        raw = _run_git(
            handle,
            "config",
            "--file",
            f"/dev/fd/{handle.git_config_descriptor}",
            "--no-includes",
            "--get-all",
            "remote.origin.url",
        ).stdout
        urls = raw.splitlines()
        if len(urls) != 1 or not urls[0]:
            raise ValueError("Candidate must configure exactly one remote.origin.url")
        _assert_candidate_git_metadata(handle)
        return urls[0]
    finally:
        if close_handle:
            _close_candidate_root_handle(handle)


def validate_remote_url(url: str) -> GitRemoteIdentity:
    """Structurally parse and bind a GitHub remote to ajoe734/pantheon."""
    if url != url.strip() or any(ord(char) < 32 for char in url):
        raise ValueError(f"Invalid/untrusted remote origin URL: {url!r}")

    transport: str
    host: str
    path: str
    scp_match = re.fullmatch(r"git@([^:/]+):([^?#]+)", url)
    if scp_match:
        transport = "ssh"
        host = scp_match.group(1).lower()
        path = scp_match.group(2)
    else:
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "ssh"}:
            raise ValueError(f"Invalid/untrusted remote origin URL: {url}")
        if parsed.query or parsed.fragment or parsed.port is not None:
            raise ValueError(f"Invalid/untrusted remote origin URL: {url}")
        if parsed.scheme == "https":
            if parsed.username is not None or parsed.password is not None:
                raise ValueError(f"Invalid/untrusted remote origin URL: {url}")
        elif parsed.username != "git" or parsed.password is not None:
            raise ValueError(f"Invalid/untrusted remote origin URL: {url}")
        transport = parsed.scheme
        host = (parsed.hostname or "").lower()
        path = parsed.path.removeprefix("/")

    components = path.removesuffix(".git").split("/")
    if (
        host != "github.com"
        or len(components) != 2
        or components[0] != TRUSTED_GITHUB_OWNER
        or components[1] != TRUSTED_GITHUB_REPOSITORY
    ):
        raise ValueError(f"Invalid/untrusted remote origin URL: {url}")
    return GitRemoteIdentity(
        raw_url=url,
        transport=transport,
        host=host,
        owner=components[0],
        repository=components[1],
    )


def _read_head_tree(
    candidate_root: Path | CandidateRootHandle,
) -> tuple[str, str]:
    handle, close_handle = _candidate_handle(candidate_root)
    try:
        _assert_candidate_git_metadata(handle)
        prefix = _git_output(handle, "rev-parse", "--show-prefix")
        if prefix:
            raise ValueError(
                f"Candidate root must be the Git top level; prefix is {prefix!r}"
            )
        top_level = Path(_git_output(handle, "rev-parse", "--show-toplevel"))
        try:
            top_level_stat = os.stat(top_level)
        except OSError as exc:
            raise ValueError(
                f"Cannot bind candidate Git top level {top_level}: {exc}"
            ) from exc
        root_stat = os.fstat(handle.descriptor)
        if (top_level_stat.st_dev, top_level_stat.st_ino) != (
            root_stat.st_dev,
            root_stat.st_ino,
        ):
            raise ValueError(
                "Candidate root must be the Git top level: "
                f"{top_level} does not identify {handle.path}"
            )
        head = _git_output(handle, "rev-parse", "--verify", "HEAD^{commit}")
        tree = _git_output(handle, "rev-parse", "--verify", "HEAD^{tree}")
        if not HEX_40_PATTERN.fullmatch(head) or not HEX_40_PATTERN.fullmatch(tree):
            raise ValueError(
                "Candidate HEAD/tree identity is not a full lowercase SHA-1"
            )
        _assert_candidate_git_metadata(handle)
        return head, tree
    finally:
        if close_handle:
            _close_candidate_root_handle(handle)


def _fetch_trusted_dev_identity(candidate_head: str) -> TrustedDevIdentity:
    """Resolve dev in a fresh bare repo, isolated from candidate Git config/refs."""
    with tempfile.TemporaryDirectory(prefix="pantheon-trusted-dev-") as temp_dir:
        trusted_repo = Path(temp_dir)
        _run_git(trusted_repo, "init", "--bare", "--quiet")
        _run_git(
            trusted_repo,
            "fetch",
            "--quiet",
            "--no-tags",
            "--force",
            "--filter=blob:none",
            TRUSTED_ORIGIN_DEV_URL,
            "+refs/heads/dev:refs/heads/accepted-dev",
        )
        accepted_dev = _git_output(
            trusted_repo,
            "rev-parse",
            "--verify",
            "refs/heads/accepted-dev^{commit}",
        )
        if not HEX_40_PATTERN.fullmatch(accepted_dev):
            raise ValueError("Trusted origin/dev did not resolve to a full SHA-1")
        _run_git(trusted_repo, "cat-file", "-e", f"{candidate_head}^{{commit}}")
        _run_git(
            trusted_repo,
            "merge-base",
            "--is-ancestor",
            candidate_head,
            accepted_dev,
        )
        trusted_tree = _git_output(
            trusted_repo,
            "rev-parse",
            "--verify",
            f"{candidate_head}^{{tree}}",
        )
        if not HEX_40_PATTERN.fullmatch(trusted_tree):
            raise ValueError("Trusted candidate tree did not resolve to a full SHA-1")
        return TrustedDevIdentity(
            commit=accepted_dev,
            candidate_commit_tree=trusted_tree,
        )


def _capture_git_identity(
    candidate_root: Path | CandidateRootHandle,
    basename: str,
) -> tuple[str, str, TrustedDevIdentity]:
    handle, close_handle = _candidate_handle(candidate_root)
    try:
        head_before, tree_before = _read_head_tree(handle)
        if head_before != basename:
            raise ValueError(
                f"Candidate basename {basename} does not match HEAD commit {head_before}"
            )
        trusted_dev = _fetch_trusted_dev_identity(head_before)
        if trusted_dev.candidate_commit_tree != tree_before:
            raise ValueError(
                "Candidate tracked tree does not match the same commit from trusted dev: "
                f"{tree_before} != {trusted_dev.candidate_commit_tree}"
            )
        head_after, tree_after = _read_head_tree(handle)
        if (head_after, tree_after) != (head_before, tree_before):
            raise ValueError("Candidate HEAD/tree changed during trusted dev resolution")
        return head_before, tree_before, trusted_dev
    finally:
        if close_handle:
            _close_candidate_root_handle(handle)


def verify_git_head_and_dev_ancestry(
    candidate_root: Path | CandidateRootHandle,
    basename: str,
) -> str:
    """Verify basename, HEAD/tree, and ancestry against freshly fetched dev."""
    head, _tree, _trusted_dev = _capture_git_identity(candidate_root, basename)
    return head


def _is_allowed_generated_untracked_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    if candidate in ALLOWED_GENERATED_UNTRACKED_FILES:
        return True
    return _is_task_brief_path(path)


def _is_allowed_generated_untracked_directory(path: str) -> bool:
    candidate = PurePosixPath(path.rstrip("/"))
    return candidate in ALLOWED_GENERATED_UNTRACKED_DIRECTORIES


def _is_task_brief_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    parts = candidate.parts
    return (
        len(parts) == 3
        and parts[:2] == (".orchestrator", "task-briefs")
        and TASK_BRIEF_PATH_PATTERN.fullmatch(parts[2]) is not None
    )


def _assert_allowed_generated_untracked_file(
    handle: CandidateRootHandle,
    relative_path: str,
) -> None:
    """Bind an allowlisted generated path to a symlink-free regular file."""
    if relative_path.endswith("/"):
        raise ValueError(
            "Allowed generated path must identify a regular file, not a directory: "
            f"{relative_path}"
        )
    candidate = PurePosixPath(relative_path)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"Invalid generated path: {relative_path!r}")

    descriptors = [os.dup(handle.descriptor)]
    try:
        for component in candidate.parts[:-1]:
            descriptors.append(
                _open_relative_descriptor(
                    descriptors[-1],
                    component,
                    label=f"Allowed generated path component {component!r}",
                    require_directory=True,
                )
            )
        leaf = _open_relative_descriptor(
            descriptors[-1],
            candidate.parts[-1],
            label=f"Allowed generated file {relative_path!r}",
            require_directory=False,
        )
        try:
            if os.fstat(leaf).st_dev != handle.identity.device:
                raise ValueError(
                    "Allowed generated file escaped the candidate filesystem: "
                    f"{relative_path}"
                )
        finally:
            os.close(leaf)
    finally:
        _close_descriptors(*reversed(descriptors))


def _assert_allowed_generated_untracked_directory(
    handle: CandidateRootHandle,
    relative_path: str,
) -> None:
    """Admit only regular, non-executable ``.log`` leaves in the logs root."""
    candidate = PurePosixPath(relative_path.rstrip("/"))
    if candidate not in ALLOWED_GENERATED_UNTRACKED_DIRECTORIES:
        raise ValueError(
            f"Generated directory is not allowlisted: {relative_path!r}"
        )

    descriptors = [os.dup(handle.descriptor)]
    try:
        for component in candidate.parts:
            descriptor = _open_relative_descriptor(
                descriptors[-1],
                component,
                label=f"Allowed generated directory component {component!r}",
                require_directory=True,
            )
            if os.fstat(descriptor).st_dev != handle.identity.device:
                os.close(descriptor)
                raise ValueError(
                    "Allowed generated directory escaped the candidate filesystem: "
                    f"{relative_path}"
                )
            descriptors.append(descriptor)

        try:
            with os.scandir(descriptors[-1]) as entries:
                names = tuple(entry.name for entry in entries)
        except OSError as exc:
            raise ValueError(
                f"Cannot enumerate allowed generated directory {relative_path!r}: {exc}"
            ) from exc

        for name in names:
            if SUPERVISOR_RUNTIME_LOG_PATH_PATTERN.fullmatch(name) is None:
                raise ValueError(
                    "Forbidden entry in allowed generated log directory: "
                    f"{candidate / name}"
                )
            leaf = _open_relative_descriptor(
                descriptors[-1],
                name,
                label=f"Allowed generated log file {str(candidate / name)!r}",
                require_directory=False,
            )
            try:
                leaf_stat = os.fstat(leaf)
                if leaf_stat.st_dev != handle.identity.device:
                    raise ValueError(
                        "Allowed generated log file escaped the candidate filesystem: "
                        f"{candidate / name}"
                    )
                if leaf_stat.st_nlink != 1:
                    raise ValueError(
                        "Allowed generated log file must have exactly one link: "
                        f"{candidate / name}"
                    )
                if leaf_stat.st_mode & (
                    stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                ):
                    raise ValueError(
                        "Allowed generated log file must not be executable: "
                        f"{candidate / name}"
                    )
            finally:
                os.close(leaf)
    finally:
        _close_descriptors(*reversed(descriptors))


def _validated_gitlink_path(relative_path: str) -> PurePosixPath:
    candidate = PurePosixPath(relative_path)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"Invalid tracked gitlink path: {relative_path!r}")
    return candidate


def _parse_tree_gitlinks(output: str) -> tuple[TrackedGitlinkIdentity, ...]:
    gitlinks: set[TrackedGitlinkIdentity] = set()
    for record in output.split("\0"):
        if not record:
            continue
        try:
            metadata, relative_path = record.split("\t", 1)
            mode, object_type, object_id = metadata.split(" ")
        except ValueError as exc:
            raise ValueError(f"Malformed tracked tree record: {record!r}") from exc
        if mode != "160000":
            continue
        if object_type != "commit" or not HEX_40_PATTERN.fullmatch(object_id):
            raise ValueError(f"Malformed tracked gitlink tree record: {record!r}")
        _validated_gitlink_path(relative_path)
        identity = TrackedGitlinkIdentity(relative_path, object_id)
        if identity in gitlinks:
            raise ValueError(f"Duplicate tracked gitlink tree record: {relative_path}")
        gitlinks.add(identity)
    return tuple(sorted(gitlinks))


def _parse_index_gitlinks(output: str) -> tuple[TrackedGitlinkIdentity, ...]:
    gitlinks: set[TrackedGitlinkIdentity] = set()
    for record in output.split("\0"):
        if not record:
            continue
        try:
            metadata, relative_path = record.split("\t", 1)
            mode, object_id, stage = metadata.split(" ")
        except ValueError as exc:
            raise ValueError(f"Malformed candidate index record: {record!r}") from exc
        if mode != "160000":
            continue
        if stage != "0" or not HEX_40_PATTERN.fullmatch(object_id):
            raise ValueError(f"Malformed tracked gitlink index record: {record!r}")
        _validated_gitlink_path(relative_path)
        identity = TrackedGitlinkIdentity(relative_path, object_id)
        if identity in gitlinks:
            raise ValueError(f"Duplicate tracked gitlink index record: {relative_path}")
        gitlinks.add(identity)
    return tuple(sorted(gitlinks))


def _capture_bound_gitlinks(
    handle: CandidateRootHandle,
    tracked_tree: str,
) -> tuple[TrackedGitlinkIdentity, ...]:
    """Bind every mode-160000 index entry to the accepted tracked tree."""
    if not HEX_40_PATTERN.fullmatch(tracked_tree):
        raise ValueError(f"Invalid tracked tree identity: {tracked_tree!r}")
    tree_gitlinks = _parse_tree_gitlinks(
        _run_git(
            handle,
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            tracked_tree,
        ).stdout
    )
    index_gitlinks = _parse_index_gitlinks(
        _run_git(handle, "ls-files", "--stage", "-z").stdout
    )
    if index_gitlinks != tree_gitlinks:
        raise ValueError(
            "Tracked gitlink tree/index identity mismatch: "
            f"tree={tree_gitlinks!r}, index={index_gitlinks!r}"
        )
    return tree_gitlinks


def _assert_open_parent_chain_stable(
    chain: list[tuple[int, str, FilesystemIdentity, str]],
) -> None:
    for parent, name, identity, label in chain:
        _assert_relative_identity(
            parent,
            name,
            identity,
            label=label,
            require_directory=True,
        )


def _assert_gitlink_directory_empty(descriptor: int, relative_path: str) -> None:
    try:
        with os.scandir(descriptor) as entries:
            first = next(entries, None)
    except OSError as exc:
        raise ValueError(
            f"Cannot enumerate tracked gitlink worktree {relative_path!r}: {exc}"
        ) from exc
    if first is not None:
        raise ValueError(
            "Tracked gitlink worktree must be absent or an empty direct directory: "
            f"{relative_path!r} contains {first.name!r}"
        )


def _assert_tracked_gitlink_worktree(
    handle: CandidateRootHandle,
    gitlink: TrackedGitlinkIdentity,
) -> None:
    """Reject content hidden below a tracked gitlink using anchored descriptors."""
    path = _validated_gitlink_path(gitlink.relative_path)
    descriptors = [os.dup(handle.descriptor)]
    parent_chain: list[tuple[int, str, FilesystemIdentity, str]] = []
    try:
        for component in path.parts[:-1]:
            label = (
                f"Tracked gitlink {gitlink.relative_path!r} "
                f"path component {component!r}"
            )
            try:
                child = _open_relative_descriptor(
                    descriptors[-1],
                    component,
                    label=label,
                    require_directory=True,
                )
            except FileNotFoundError:
                _assert_relative_entry_absent(
                    descriptors[-1],
                    component,
                    label=label,
                )
                _assert_open_parent_chain_stable(parent_chain)
                _assert_relative_entry_absent(
                    descriptors[-1],
                    component,
                    label=label,
                )
                return
            identity = _filesystem_identity(child)
            if identity.device != handle.identity.device:
                os.close(child)
                raise ValueError(
                    f"Tracked gitlink {gitlink.relative_path!r} path escaped "
                    "the candidate filesystem"
                )
            parent_chain.append(
                (descriptors[-1], component, identity, label)
            )
            descriptors.append(child)

        leaf_name = path.parts[-1]
        leaf_label = f"Tracked gitlink worktree {gitlink.relative_path!r}"
        try:
            leaf = _open_relative_descriptor(
                descriptors[-1],
                leaf_name,
                label=leaf_label,
                require_directory=True,
            )
        except FileNotFoundError:
            _assert_relative_entry_absent(
                descriptors[-1],
                leaf_name,
                label=leaf_label,
            )
            _assert_open_parent_chain_stable(parent_chain)
            _assert_relative_entry_absent(
                descriptors[-1],
                leaf_name,
                label=leaf_label,
            )
            return

        try:
            leaf_identity = _filesystem_identity(leaf)
            if leaf_identity.device != handle.identity.device:
                raise ValueError(
                    f"Tracked gitlink {gitlink.relative_path!r} escaped "
                    "the candidate filesystem"
                )
            _assert_gitlink_directory_empty(leaf, gitlink.relative_path)
            _assert_relative_identity(
                descriptors[-1],
                leaf_name,
                leaf_identity,
                label=leaf_label,
                require_directory=True,
            )
            _assert_open_parent_chain_stable(parent_chain)
            _assert_gitlink_directory_empty(leaf, gitlink.relative_path)
            _assert_relative_identity(
                descriptors[-1],
                leaf_name,
                leaf_identity,
                label=leaf_label,
                require_directory=True,
            )
        except FileNotFoundError as exc:
            raise ValueError(
                f"Tracked gitlink {gitlink.relative_path!r} changed during validation"
            ) from exc
        finally:
            os.close(leaf)
    finally:
        _close_descriptors(*reversed(descriptors))


def _assert_tracked_gitlink_worktrees(
    handle: CandidateRootHandle,
    gitlinks: tuple[TrackedGitlinkIdentity, ...],
) -> None:
    for gitlink in gitlinks:
        _assert_tracked_gitlink_worktree(handle, gitlink)


def verify_working_tree_cleanliness(
    candidate_root: Path | CandidateRootHandle,
    *,
    expected_head: str | None = None,
    expected_tree: str | None = None,
) -> str:
    """Reject tracked, hidden-gitlink, and non-enumerated generated dirt."""
    handle, close_handle = _candidate_handle(candidate_root)
    try:
        index_flags = _run_git(handle, "ls-files", "-v", "-z").stdout
        for record in index_flags.split("\0"):
            if not record:
                continue
            if len(record) < 3 or record[1] != " ":
                raise ValueError(f"Malformed git index flag record: {record!r}")
            if record[0] != "H":
                raise ValueError(
                    "Forbidden tracked index flag "
                    f"{record[0]!r}: {record[2:]}"
                )

        head_before, tree_before = _read_head_tree(handle)
        if expected_head is not None and head_before != expected_head:
            raise ValueError(
                f"Candidate HEAD drift: {head_before} != {expected_head}"
            )
        if expected_tree is not None and tree_before != expected_tree:
            raise ValueError(
                f"Candidate tree drift: {tree_before} != {expected_tree}"
            )
        gitlinks_before = _capture_bound_gitlinks(handle, tree_before)
        _assert_tracked_gitlink_worktrees(handle, gitlinks_before)

        status_output = _run_git(
            handle,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
            "--ignore-submodules=all",
        ).stdout
        for record in status_output.split("\0"):
            if not record:
                continue
            if len(record) < 4 or record[2] != " ":
                raise ValueError(f"Malformed git status record: {record!r}")
            status_code = record[:2]
            relative_path = record[3:]
            if status_code not in {"??", "!!"}:
                raise ValueError(
                    f"Tracked git tree is dirty ({status_code}): {relative_path}"
                )
            if relative_path.endswith("/"):
                kind = "ignored" if status_code == "!!" else "untracked"
                if not _is_allowed_generated_untracked_directory(relative_path):
                    raise ValueError(
                        f"Forbidden {kind} directory found in candidate root: "
                        f"{relative_path}"
                    )
                _assert_allowed_generated_untracked_directory(
                    handle,
                    relative_path,
                )
                continue
            if not _is_allowed_generated_untracked_path(relative_path):
                kind = "ignored" if status_code == "!!" else "untracked"
                raise ValueError(
                    f"Forbidden {kind} file found in candidate root: {relative_path}"
                )
            _assert_allowed_generated_untracked_file(handle, relative_path)

        try:
            _run_git(handle, "diff-index", "--cached", "--quiet", "HEAD", "--")
        except ValueError as exc:
            raise ValueError("Candidate index differs from HEAD") from exc
        try:
            _run_git(
                handle,
                "diff-files",
                "--quiet",
                "--ignore-submodules=all",
                "--",
            )
        except ValueError as exc:
            raise ValueError("Candidate tracked worktree differs from index") from exc

        _assert_tracked_gitlink_worktrees(handle, gitlinks_before)
        head, tree = _read_head_tree(handle)
        if (head, tree) != (head_before, tree_before):
            raise ValueError("Candidate HEAD/tree changed during cleanliness validation")
        gitlinks_after = _capture_bound_gitlinks(handle, tree)
        if gitlinks_after != gitlinks_before:
            raise ValueError("Tracked gitlink identities changed during validation")
        _assert_candidate_handle_path(handle)
        return tree
    finally:
        if close_handle:
            _close_candidate_root_handle(handle)


def _capture_config_bytes(
    config_path: Path,
    *,
    expected_path: Path,
) -> tuple[
    bytes,
    FilesystemIdentity,
    tuple[PathComponentIdentity, ...],
]:
    path = config_path if isinstance(config_path, Path) else Path(config_path)
    if path != expected_path:
        raise ValueError(
            f"Config path {path} does not match exact live config path {expected_path}"
        )

    parent_components = _capture_directory_component_identities(
        path.parent,
        label="Live supervisor config path",
    )
    descriptor = _open_path_descriptor(
        path,
        label="Live supervisor config path",
        require_directory=False,
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"Live config path is not a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as config_file:
            content = config_file.read()
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    stable_fields_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mode,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    stable_fields_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mode,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if stable_fields_after != stable_fields_before or len(content) != before.st_size:
        raise ValueError(f"Live config changed during byte capture: {path}")
    file_identity = _identity_from_stat(before)
    path_components = parent_components + (
        PathComponentIdentity(
            path=path,
            identity=file_identity,
        ),
    )
    _assert_path_component_identities(
        path_components,
        label="Live config",
    )
    return content, file_identity, path_components


def _encode_live_config(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8", errors="strict")


def derive_supervisor_config_variant(
    identity: CandidateRuntimeIdentity,
    *,
    command_root: Path,
    repo_config_root: Path | None = None,
) -> SupervisorConfigVariant:
    """Render one target generation without mutating the live config.

    Candidate promotion consumes reviewed repo-owned policy through the same
    split-root renderer as provisioning. Rollback omits ``repo_config_root``
    and therefore restores the exact captured incumbent policy while changing
    only its immutable command root.
    """
    payload = copy.deepcopy(_strict_live_config(identity))
    watchdog = payload.get("watchdog")
    if not isinstance(watchdog, dict):
        raise ValueError("Captured live config watchdog object is missing")
    raw_command = watchdog.get("supervisor_command")
    if (
        not isinstance(raw_command, list)
        or not raw_command
        or any(not isinstance(item, str) or not item for item in raw_command)
    ):
        raise ValueError("Captured live config supervisor_command is invalid")
    supervisor_indexes = tuple(
        index
        for index, argument in enumerate(raw_command)
        if PurePosixPath(argument).name == "supervisor.py"
    )
    if len(supervisor_indexes) != 1:
        raise ValueError(
            "Captured live config must contain exactly one supervisor entrypoint"
        )
    command = list(raw_command)
    if repo_config_root is not None:
        repo_config_path = repo_config_root / ".orchestrator" / "config.json"
        repo_config = load_json_object(repo_config_path)
        _status_path, _state_path, _provider_path, status_root = (
            _runtime_document_paths(identity)
        )
        payload = build_live_config(
            repo_config,
            existing_live_config=payload,
            command_root=command_root,
            status_root=status_root,
            live_config_path=identity.config_path,
            python_executable=Path(command[0]),
        )
        watchdog = payload.get("watchdog")
        if not isinstance(watchdog, dict):
            raise ValueError("Rendered candidate config watchdog object is missing")
        rendered_command = watchdog.get("supervisor_command")
        if not isinstance(rendered_command, list) or not rendered_command:
            raise ValueError("Rendered candidate supervisor_command is invalid")
        command = list(rendered_command)
    supervisor_index = supervisor_indexes[0]
    if repo_config_root is not None:
        supervisor_indexes = tuple(
            index
            for index, argument in enumerate(command)
            if PurePosixPath(argument).name == "supervisor.py"
        )
        if len(supervisor_indexes) != 1:
            raise ValueError(
                "Rendered candidate config must contain exactly one supervisor entrypoint"
            )
        supervisor_index = supervisor_indexes[0]
    command[supervisor_index] = str(
        command_root / SUPERVISOR_ENTRYPOINT_RELATIVE
    )
    # The runtime root is immutable and its cleanliness is revalidated during
    # every post-launch observation.  Persist Python's no-bytecode flag in the
    # config-owned argv so both the promotion launch and later watchdog
    # restarts cannot create ignored __pycache__ content in that root.
    if "-B" not in command[1:supervisor_index]:
        command.insert(supervisor_index, "-B")
    watchdog["supervisor_command"] = command
    content = _encode_live_config(payload)
    return SupervisorConfigVariant(
        command_root=command_root,
        supervisor_argv=tuple(command),
        content=content,
        byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _identity_with_current_config(
    identity: CandidateRuntimeIdentity,
    *,
    expected_content: bytes,
) -> CandidateRuntimeIdentity:
    content, file_identity, path_components = _capture_config_bytes(
        identity.config_path,
        expected_path=LIVE_SUPERVISOR_CONFIG_PATH,
    )
    if content != expected_content:
        raise ValueError("Installed live config does not match the target generation")
    return replace(
        identity,
        config_device=file_identity.device,
        config_inode=file_identity.inode,
        config_path_components=path_components,
        config_bytes=content,
        config_byte_length=len(content),
        config_sha256=hashlib.sha256(content).hexdigest(),
    )


def atomic_install_live_config(
    identity: CandidateRuntimeIdentity,
    variant: SupervisorConfigVariant,
    *,
    allowed_predecessors: Mapping[str, bytes],
    fault_hook: Callable[[str], None] | None = None,
) -> CandidateRuntimeIdentity:
    """CAS-check, replace and fsync one live-config generation.

    The caller must own both the promotion lock and the runtime-admission lock.
    A post-replace error is deliberately surfaced; rollback may proceed only if
    the resulting bytes are one of its explicitly captured predecessors.
    """
    path = identity.config_path
    if variant.command_root != identity.candidate_root:
        raise ValueError("Config variant command root differs from runtime identity")
    if variant.byte_length != len(variant.content):
        raise ValueError("Config variant byte length is invalid")
    if hashlib.sha256(variant.content).hexdigest() != variant.sha256:
        raise ValueError("Config variant SHA-256 is invalid")
    if path != LIVE_SUPERVISOR_CONFIG_PATH:
        raise ValueError("Config install is restricted to the exact live config path")
    current, _file_identity, _components = _capture_config_bytes(
        path,
        expected_path=LIVE_SUPERVISOR_CONFIG_PATH,
    )
    current_sha = hashlib.sha256(current).hexdigest()
    if allowed_predecessors.get(current_sha) != current:
        raise ValueError(
            "Live config generation is not an allowed transaction predecessor"
        )

    parent_components = _capture_directory_component_identities(
        path.parent,
        label="Live config install directory",
    )
    directory_fd = _open_path_descriptor(
        path.parent,
        label="Live config install directory",
        require_directory=True,
    )
    temporary_path: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.promotion-",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), stat.S_IRUSR | stat.S_IWUSR)
                handle.write(variant.content)
                handle.flush()
                os.fsync(handle.fileno())
            if fault_hook is not None:
                fault_hook("after_temp_fsync")

            # Recheck the exact predecessor after the temporary generation is
            # durable. Unknown replacement bytes prohibit our rename/launch.
            current, _identity, _path_components = _capture_config_bytes(
                path,
                expected_path=LIVE_SUPERVISOR_CONFIG_PATH,
            )
            current_sha = hashlib.sha256(current).hexdigest()
            if allowed_predecessors.get(current_sha) != current:
                raise ValueError("Live config raced before atomic replacement")
            _assert_path_component_identities(
                parent_components,
                label="Live config install directory",
            )
            if fault_hook is not None:
                fault_hook("before_replace")
            current, _identity, _path_components = _capture_config_bytes(
                path,
                expected_path=LIVE_SUPERVISOR_CONFIG_PATH,
            )
            current_sha = hashlib.sha256(current).hexdigest()
            if allowed_predecessors.get(current_sha) != current:
                raise ValueError("Live config raced at atomic replacement")
            os.replace(
                temporary_path.name,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            temporary_path = None
            if fault_hook is not None:
                fault_hook("after_replace")
            installed = _identity_with_current_config(
                identity,
                expected_content=variant.content,
            )
            os.fsync(directory_fd)
            if fault_hook is not None:
                fault_hook("after_directory_fsync")
            _assert_path_component_identities(
                parent_components,
                label="Live config install directory",
            )
            return installed
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    finally:
        os.close(directory_fd)


def _hash_descriptor_range(descriptor: int, *, length: int) -> str:
    hasher = hashlib.sha256()
    if length == 0:
        return hasher.hexdigest()
    os.lseek(descriptor, 0, os.SEEK_SET)
    bytes_remaining = length
    chunk_size = 65536
    while bytes_remaining > 0:
        to_read = min(bytes_remaining, chunk_size)
        chunk = os.read(descriptor, to_read)
        if not chunk or len(chunk) == 0:
            raise ValueError("Unexpected end of file while reading descriptor range")
        hasher.update(chunk)
        bytes_remaining -= len(chunk)
    return hasher.hexdigest()


class OSLaunchFilesystem:
    """Descriptor-bound, read-only filesystem checks for launch preflight."""

    def capture_regular_file(
        self,
        path: Path,
        *,
        role: str,
        require_executable: bool,
    ) -> LaunchFileIdentity:
        parent_components = _capture_directory_component_identities(
            path.parent,
            label=f"Governed launch {role}",
        )
        descriptor = _open_path_descriptor(
            path,
            label=f"Governed launch {role}",
            require_directory=False,
        )
        try:
            before = os.fstat(descriptor)
            if before.st_nlink != 1:
                raise ValueError(f"Governed launch {role} must not be hard-linked: {path}")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                content = handle.read()
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        stable_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        stable_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if stable_before != stable_after or len(content) != before.st_size:
            raise ValueError(f"Governed launch {role} changed during capture: {path}")
        if require_executable and (
            before.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) == 0
            or not os.access(path, os.X_OK)
        ):
            raise ValueError(f"Governed launch {role} is not executable: {path}")
        file_identity = _identity_from_stat(before)
        _assert_path_component_identities(
            parent_components
            + (PathComponentIdentity(path=path, identity=file_identity),),
            label=f"Governed launch {role}",
        )
        return LaunchFileIdentity(
            role=role,
            path=path,
            device=before.st_dev,
            inode=before.st_ino,
            mode=before.st_mode,
            byte_length=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def capture_append_only_journal(
        self,
        path: Path,
        *,
        role: str,
        baseline_identity: LaunchJournalFileIdentity | None = None,
    ) -> LaunchJournalFileIdentity:
        parent_components = _capture_directory_component_identities(
            path.parent,
            label=f"Governed launch {role}",
        )
        descriptor = _open_path_descriptor(
            path,
            label=f"Governed launch {role}",
            require_directory=False,
        )
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"Governed launch {role} is not a regular file: {path}")
            if before.st_nlink != 1:
                raise ValueError(f"Governed launch {role} must not be hard-linked: {path}")

            if baseline_identity is not None:
                if before.st_dev != baseline_identity.device:
                    raise ValueError(f"Governed launch {role} device changed: {path}")
                if before.st_ino != baseline_identity.inode:
                    raise ValueError(f"Governed launch {role} inode changed: {path}")
                if before.st_mode != baseline_identity.mode:
                    raise ValueError(f"Governed launch {role} mode changed: {path}")
                if before.st_size < baseline_identity.captured_size:
                    raise ValueError(
                        f"Governed launch {role} was truncated below baseline size: {path}"
                    )
                prefix_len = baseline_identity.captured_size
                expected_prefix_sha256 = baseline_identity.prefix_sha256
            else:
                prefix_len = before.st_size
                expected_prefix_sha256 = None

            try:
                prefix_sha256 = _hash_descriptor_range(descriptor, length=prefix_len)
            except ValueError as exc:
                raise ValueError(
                    f"Governed launch {role} was truncated during capture: {path}"
                ) from exc

            if expected_prefix_sha256 is not None and prefix_sha256 != expected_prefix_sha256:
                raise ValueError(
                    f"Governed launch {role} prefix changed from baseline: {path}"
                )

            after = os.fstat(descriptor)
            if after.st_dev != before.st_dev:
                raise ValueError(f"Governed launch {role} device changed during capture: {path}")
            if after.st_ino != before.st_ino:
                raise ValueError(f"Governed launch {role} inode changed during capture: {path}")
            if after.st_mode != before.st_mode:
                raise ValueError(f"Governed launch {role} mode changed during capture: {path}")
            if after.st_nlink != 1:
                raise ValueError(f"Governed launch {role} must not be hard-linked: {path}")
            if after.st_size < before.st_size:
                raise ValueError(f"Governed launch {role} was truncated during capture: {path}")

            try:
                reverified_prefix_sha256 = _hash_descriptor_range(descriptor, length=prefix_len)
            except ValueError as exc:
                raise ValueError(
                    f"Governed launch {role} was truncated during capture: {path}"
                ) from exc

            if reverified_prefix_sha256 != prefix_sha256:
                raise ValueError(
                    f"Governed launch {role} prefix changed during capture: {path}"
                )
        finally:
            os.close(descriptor)

        file_identity = _identity_from_stat(before)
        _assert_path_component_identities(
            parent_components
            + (PathComponentIdentity(path=path, identity=file_identity),),
            label=f"Governed launch {role}",
        )

        return LaunchJournalFileIdentity(
            role=role,
            path=path,
            device=before.st_dev,
            inode=before.st_ino,
            mode=before.st_mode,
            captured_size=prefix_len,
            prefix_sha256=prefix_sha256,
            captured_at_size=after.st_size,
        )

    def capture_directory(self, path: Path, *, label: str) -> FilesystemIdentity:
        components = _capture_directory_component_identities(path, label=label)
        identity = components[-1].identity
        if not stat.S_ISDIR(identity.mode):
            raise ValueError(f"{label} is not a directory: {path}")
        _assert_path_component_identities(components, label=label)
        return identity

    def directory_is_writable(self, path: Path) -> bool:
        return os.access(path, os.W_OK | os.X_OK)

    def path_exists(self, path: Path) -> bool:
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        return True

    def file_is_writable(self, path: Path) -> bool:
        return os.access(path, os.W_OK)


def _path_is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _default_promotion_evidence_path(
    *,
    now: datetime | None = None,
) -> Path:
    recorded_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = recorded_at.strftime("%Y%m%dT%H%M%S%fZ")
    return DEFAULT_PROMOTION_EVIDENCE_ROOT / (
        f"supervisor-runtime-promotion-{stamp}-{os.getpid()}.json"
    )


def _validate_transaction_evidence_path(
    path: Path,
    *,
    plan: "PromotionPlan",
) -> None:
    _validate_absolute_identity_path(path, label="Transaction evidence path")
    executable_roots = (
        plan.candidate_identity.candidate_root,
        plan.incumbent_identity.candidate_root,
    )
    for executable_root in executable_roots:
        if _path_is_within(path, executable_root):
            raise ValueError(
                "Transaction evidence path must remain outside executable "
                f"command roots: {path} is within {executable_root}"
            )


def _is_forbidden_launch_environment_name(name: str) -> bool:
    return (
        name in GOVERNED_LAUNCH_FORBIDDEN_ENVIRONMENT
        or any(name.startswith(prefix) for prefix in GOVERNED_LAUNCH_FORBIDDEN_PREFIXES)
    )


def _expected_governed_launch_environment(
    identity: CandidateRuntimeIdentity,
    *,
    status_root: Path,
    authority_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if authority_environment is None else authority_environment
    expected = {
        "PANTHEON_COMMAND_BASE_REF": "origin/dev",
        "PANTHEON_COMMAND_REMOTE": identity.repository_slug,
        "PANTHEON_COMMAND_ROOT": str(identity.candidate_root),
        "PANTHEON_COMMAND_RUNTIME_SHA": identity.head_commit,
        "PANTHEON_STATUS_ROOT": str(status_root),
        # Unlike ``python -B``, this setting is inherited by every Python
        # subprocess the supervisor launches from the immutable command root.
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in (
        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON",
        "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON",
    ):
        value = str(source.get(name) or "").strip()
        if not value:
            raise ValueError(f"Governed launch environment is missing {name}")
        expected[name] = value
    return expected


def build_scrubbed_launch_environment(
    identity: CandidateRuntimeIdentity,
    *,
    status_root: Path,
    inherited_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if inherited_environment is None else inherited_environment
    environment = {
        str(name): str(value)
        for name, value in source.items()
        if not _is_forbidden_launch_environment_name(str(name))
    }
    environment.update(
        _expected_governed_launch_environment(
            identity,
            status_root=status_root,
            authority_environment=source,
        )
    )
    return environment


def _validate_governed_launch_environment(
    environment: Mapping[str, str],
    *,
    expected: Mapping[str, str],
) -> None:
    forbidden = sorted(
        name for name in environment if _is_forbidden_launch_environment_name(str(name))
    )
    if forbidden:
        raise ValueError(
            "Governed launch environment retains forbidden inherited variables: "
            + ",".join(forbidden)
        )
    for name in GOVERNED_LAUNCH_REQUIRED_ENVIRONMENT:
        if name not in environment:
            raise ValueError(f"Governed launch environment is missing {name}")
        actual = str(environment[name])
        if actual != expected[name]:
            raise ValueError(
                f"Governed launch environment {name} mismatch: "
                f"{actual!r} != {expected[name]!r}"
            )


def _environment_names_sha256(environment: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(str(item) for item in environment):
        encoded = name.encode("utf-8", errors="strict")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


def _strict_absolute_path_from_config(raw: Any, *, label: str) -> Path:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise ValueError(f"Captured live config {label} is invalid")
    path = Path(raw)
    _validate_absolute_identity_path(path, label=f"Captured live config {label}")
    return path


def _capture_optional_safe_file(
    filesystem: LaunchFilesystem,
    path: Path,
    *,
    role: str,
    require_writable: bool,
) -> None:
    if not filesystem.path_exists(path):
        return
    filesystem.capture_regular_file(
        path,
        role=role,
        require_executable=False,
    )
    if require_writable and not filesystem.file_is_writable(path):
        raise ValueError(f"Governed launch {role} is not writable: {path}")


def build_governed_supervisor_launch_contract(
    identity: CandidateRuntimeIdentity,
    *,
    supervisor_argv: tuple[str, ...] | None = None,
    filesystem: LaunchFilesystem | None = None,
    inherited_environment: Mapping[str, str] | None = None,
    launch_environment: Mapping[str, str] | None = None,
    launch_cwd: Path | None = None,
    stdout_log_path: Path | None = None,
    stderr_log_path: Path | None = None,
    baseline_task_state_event_log_identity: LaunchJournalFileIdentity | None = None,
    config_override: Mapping[str, Any] | None = None,
    defer_task_state_event_log_identity: bool = False,
) -> GovernedSupervisorLaunchContract:
    """Assemble and validate the exact next-launch contract without mutation."""
    fs = filesystem or OSLaunchFilesystem()
    expected_process = _expected_supervisor_process_contract(
        identity,
        supervisor_argv=supervisor_argv,
        config_override=config_override,
    )
    config = _strict_config_payload(identity, config_override)

    if expected_process.argv[0] != str(expected_process.executable):
        raise ValueError(
            "Governed interpreter path is not canonical: "
            f"{expected_process.argv[0]} != {expected_process.executable}"
        )
    interpreter = fs.capture_regular_file(
        expected_process.executable,
        role="interpreter",
        require_executable=True,
    )

    cwd = launch_cwd or identity.candidate_root
    if cwd != identity.candidate_root:
        raise ValueError(
            f"Governed launch cwd mismatch: {cwd} != {identity.candidate_root}"
        )
    cwd_identity = fs.capture_directory(cwd, label="Governed launch cwd")
    if (
        cwd_identity.device != identity.candidate_root_device
        or cwd_identity.inode != identity.candidate_root_inode
    ):
        raise ValueError("Governed launch cwd identity differs from candidate root")

    source_identities = tuple(
        fs.capture_regular_file(
            identity.candidate_root / Path(relative),
            role=role,
            require_executable=require_executable,
        )
        for role, relative, require_executable in GOVERNED_LAUNCH_SOURCES
    )
    source_by_role = {source.role: source for source in source_identities}
    if source_by_role["supervisor"].path != expected_process.entrypoint:
        raise ValueError("Governed supervisor source does not match configured entrypoint")

    task_state_store = config.get("task_state_store")
    if not isinstance(task_state_store, dict):
        raise ValueError("Captured live config task_state_store object is missing")
    if task_state_store.get("mode") != "authoritative":
        raise ValueError("Captured live config task_state_store.mode is not authoritative")
    task_state_event_log = _strict_absolute_path_from_config(
        task_state_store.get("event_log"),
        label="task_state_store.event_log",
    )
    fs.capture_directory(
        task_state_event_log.parent,
        label="Governed task-state event-log directory",
    )
    if not fs.directory_is_writable(task_state_event_log.parent):
        raise ValueError(
            "Governed task-state event-log directory is not writable: "
            f"{task_state_event_log.parent}"
        )
    task_state_event_log_identity: LaunchJournalFileIdentity | None
    if defer_task_state_event_log_identity:
        if baseline_task_state_event_log_identity is not None:
            raise ValueError("Deferred journal capture cannot carry a baseline identity")
        if fs.path_exists(task_state_event_log):
            raise ValueError(
                "Deferred candidate task-state journal already exists; "
                "migration must establish its exact identity"
            )
        task_state_event_log_identity = None
    else:
        task_state_event_log_identity = fs.capture_append_only_journal(
            task_state_event_log,
            role="task_state_event_log",
            baseline_identity=baseline_task_state_event_log_identity,
        )
        if not fs.file_is_writable(task_state_event_log):
            raise ValueError(
                f"Governed task-state event log is not writable: {task_state_event_log}"
            )
    if _path_is_within(task_state_event_log, identity.candidate_root):
        raise ValueError("Task-state event log must remain outside the command runtime")

    worker_worktrees = config.get("worker_worktrees")
    if not isinstance(worker_worktrees, dict):
        raise ValueError("Captured live config worker_worktrees object is missing")
    worker_worktree_root = _strict_absolute_path_from_config(
        worker_worktrees.get("root"),
        label="worker_worktrees.root",
    )
    fs.capture_directory(
        worker_worktree_root,
        label="Governed worker worktree root",
    )
    if not fs.directory_is_writable(worker_worktree_root):
        raise ValueError(
            f"Governed worker worktree root is not writable: {worker_worktree_root}"
        )
    if _path_is_within(identity.candidate_root, worker_worktree_root):
        raise ValueError("Command runtime cannot be a worker task worktree")
    if _path_is_within(worker_worktree_root, identity.candidate_root):
        raise ValueError("Worker worktree root cannot be inside the command runtime")

    status_root = Path(expected_process.status_root)
    scrubbed_environment = (
        dict(launch_environment)
        if launch_environment is not None
        else build_scrubbed_launch_environment(
            identity,
            status_root=status_root,
            inherited_environment=inherited_environment,
        )
    )
    expected_environment = _expected_governed_launch_environment(
        identity,
        status_root=status_root,
        authority_environment=scrubbed_environment,
    )
    _validate_governed_launch_environment(
        scrubbed_environment,
        expected=expected_environment,
    )

    if _path_is_within(status_root, identity.candidate_root):
        raise ValueError("Canonical status root must remain outside the command runtime")
    log_directory = status_root / ".orchestrator" / "logs"
    fs.capture_directory(log_directory, label="Governed durable log directory")
    if not fs.directory_is_writable(log_directory):
        raise ValueError(
            f"Governed durable log directory is not writable: {log_directory}"
        )
    default_log_path = (
        log_directory / f"supervisor-runtime-{identity.head_commit}.log"
    )
    stdout_path = stdout_log_path or default_log_path
    stderr_path = stderr_log_path or default_log_path
    if stdout_path != default_log_path or stderr_path != default_log_path:
        raise ValueError(
            "Governed stdout/stderr must use the exact durable supervisor log target"
        )
    _capture_optional_safe_file(
        fs,
        default_log_path,
        role="durable_stdout_stderr_log",
        require_writable=True,
    )

    intentional_restart = status_root / ".orchestrator" / "supervisor-restart-intent.json"
    fs.capture_directory(
        intentional_restart.parent,
        label="Governed intentional-restart directory",
    )
    if not fs.directory_is_writable(intentional_restart.parent):
        raise ValueError(
            "Governed intentional-restart directory is not writable: "
            f"{intentional_restart.parent}"
        )
    _capture_optional_safe_file(
        fs,
        intentional_restart,
        role="intentional_restart_record",
        require_writable=True,
    )

    return GovernedSupervisorLaunchContract(
        interpreter=interpreter,
        argv=expected_process.argv,
        cwd=cwd,
        cwd_device=cwd_identity.device,
        cwd_inode=cwd_identity.inode,
        required_environment=tuple(
            (name, str(expected_environment[name]))
            for name in sorted(expected_environment)
        ),
        scrubbed_environment_names_sha256=_environment_names_sha256(
            scrubbed_environment
        ),
        scrubbed_environment_variable_count=len(scrubbed_environment),
        source_identities=source_identities,
        status_command_root=identity.candidate_root,
        status_command_runtime_sha=identity.head_commit,
        status_command_remote=identity.repository_slug,
        status_command_base_ref="origin/dev",
        status_root=status_root,
        task_state_event_log=task_state_event_log,
        task_state_event_log_identity=task_state_event_log_identity,
        worker_worktree_root=worker_worktree_root,
        intentional_restart_path=intentional_restart,
        stdout_log_path=stdout_path,
        stderr_log_path=stderr_path,
    )


class ProcfsRuntimeProcessReader:
    """Read the minimum allowlisted process identity surface from procfs."""

    def __init__(self, proc_root: Path = PROCFS_ROOT) -> None:
        self.proc_root = proc_root

    def list_pids(self) -> tuple[int, ...]:
        try:
            entries = tuple(self.proc_root.iterdir())
        except OSError as exc:
            raise ValueError(f"Cannot enumerate procfs: {type(exc).__name__}") from exc
        return tuple(
            sorted(
                int(entry.name)
                for entry in entries
                if entry.name.isascii() and entry.name.isdigit()
            )
        )

    def _process_path(self, pid: int, name: str) -> Path:
        if pid <= 0 or not name or "/" in name:
            raise ValueError("Invalid procfs process field request")
        return self.proc_root / str(pid) / name

    def read_generation(self, pid: int) -> ProcessGeneration:
        path = self._process_path(pid, "stat")
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise ProcessLookupError(f"Process {pid} vanished") from exc
        except OSError as exc:
            raise ValueError(
                f"Process {pid} stat is unreadable: {type(exc).__name__}"
            ) from exc
        try:
            text = raw.decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Process {pid} stat is not ASCII") from exc
        close_paren = text.rfind(")")
        open_paren = text.find("(")
        if open_paren <= 0 or close_paren <= open_paren:
            raise ValueError(f"Process {pid} stat has an invalid comm field")
        try:
            recorded_pid = int(text[:open_paren].strip())
            fields = text[close_paren + 1 :].strip().split()
            state = fields[0]
            starttime_ticks = int(fields[19])
        except (IndexError, ValueError) as exc:
            raise ValueError(f"Process {pid} stat is missing generation fields") from exc
        if recorded_pid != pid or len(state) != 1 or starttime_ticks <= 0:
            raise ValueError(f"Process {pid} stat has invalid generation fields")
        return ProcessGeneration(
            pid=recorded_pid,
            starttime_ticks=starttime_ticks,
            state=state,
        )

    def read_argv(self, pid: int) -> tuple[str, ...]:
        path = self._process_path(pid, "cmdline")
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise ProcessLookupError(f"Process {pid} vanished while reading argv") from exc
        except OSError as exc:
            raise ValueError(
                f"Process {pid} argv is unreadable: {type(exc).__name__}"
            ) from exc
        if not raw:
            return ()
        if not raw.endswith(b"\0"):
            raise ValueError(f"Process {pid} argv is not NUL terminated")
        try:
            return tuple(part.decode("utf-8", errors="strict") for part in raw[:-1].split(b"\0"))
        except UnicodeDecodeError as exc:
            raise ValueError(f"Process {pid} argv is not valid UTF-8") from exc

    def read_executable(self, pid: int) -> Path:
        path = self._process_path(pid, "exe")
        try:
            raw = os.readlink(path)
        except FileNotFoundError as exc:
            raise ProcessLookupError(
                f"Process {pid} vanished while reading executable"
            ) from exc
        except OSError as exc:
            raise ValueError(
                f"Process {pid} executable is unreadable: {type(exc).__name__}"
            ) from exc
        if raw.endswith(" (deleted)"):
            raise ValueError(f"Process {pid} executable has been deleted")
        executable = Path(raw)
        if not executable.is_absolute():
            raise ValueError(f"Process {pid} executable is not absolute")
        try:
            return executable.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"Process {pid} executable cannot be resolved") from exc

    def read_cwd(self, pid: int) -> ProcessCwdIdentity:
        path = self._process_path(pid, "cwd")
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ProcessLookupError(f"Process {pid} vanished while reading cwd") from exc
        except OSError as exc:
            raise ValueError(
                f"Process {pid} cwd is unreadable: {type(exc).__name__}"
            ) from exc
        try:
            before = os.readlink(path)
            opened = os.fstat(descriptor)
            after = os.readlink(path)
        except FileNotFoundError as exc:
            raise ProcessLookupError(f"Process {pid} vanished while reading cwd") from exc
        except OSError as exc:
            raise ValueError(
                f"Process {pid} cwd changed while being read: {type(exc).__name__}"
            ) from exc
        finally:
            os.close(descriptor)
        if before != after or before.endswith(" (deleted)"):
            raise ValueError(f"Process {pid} cwd is deleted or changed")
        cwd = Path(before)
        if not cwd.is_absolute():
            raise ValueError(f"Process {pid} cwd is not absolute")
        try:
            resolved = cwd.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"Process {pid} cwd cannot be resolved") from exc
        resolved_stat = resolved.stat()
        if (resolved_stat.st_dev, resolved_stat.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise ValueError(f"Process {pid} cwd identity changed")
        return ProcessCwdIdentity(
            path=resolved,
            device=opened.st_dev,
            inode=opened.st_ino,
        )

    def read_environment_contract(self, pid: int) -> dict[str, str]:
        path = self._process_path(pid, "environ")
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise ProcessLookupError(
                f"Process {pid} vanished while reading environment contract"
            ) from exc
        except OSError as exc:
            raise ValueError(
                f"Process {pid} environment contract is unreadable: "
                f"{type(exc).__name__}"
            ) from exc
        allowlisted = {
            name.encode("ascii"): name for name in PROCESS_ENVIRONMENT_ALLOWLIST
        }
        contract: dict[str, str] = {}
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            raw_name, separator, raw_value = entry.partition(b"=")
            name = allowlisted.get(raw_name)
            if name is None:
                continue
            if not separator or name in contract:
                raise ValueError(
                    f"Process {pid} environment contract has duplicate or malformed {name}"
                )
            try:
                contract[name] = raw_value.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"Process {pid} environment contract has invalid UTF-8 for {name}"
                ) from exc
        return contract

    def read_admission_lock(
        self,
        path: Path,
    ) -> SupervisorAdmissionLockIdentity:
        return _capture_supervisor_admission_lock(path, self)


def _read_descriptor_bytes(descriptor: int, *, limit: int, label: str) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(4096, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ValueError(f"{label} exceeds {limit} bytes")
    return b"".join(chunks)


def _capture_supervisor_admission_lock(
    path: Path,
    reader: ProcfsRuntimeProcessReader,
) -> SupervisorAdmissionLockIdentity:
    """Bind the singleton admission lock file, kernel row, and owner generation."""
    descriptor = _open_path_descriptor(
        path,
        label="Supervisor admission lock",
        require_directory=False,
    )
    try:
        before = os.fstat(descriptor)
        if before.st_nlink != 1:
            raise ValueError("Supervisor admission lock must have exactly one link")
        identity = _identity_from_stat(before)
        _assert_path_identity(
            path,
            identity,
            label="Supervisor admission lock",
            require_directory=False,
        )
        content = _read_descriptor_bytes(
            descriptor,
            limit=64,
            label="Supervisor admission lock",
        )
        after = os.fstat(descriptor)
        stable_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        stable_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if stable_before != stable_after or len(content) != before.st_size:
            raise ValueError("Supervisor admission lock changed during capture")
        try:
            owner_text = content.decode("ascii", errors="strict").strip()
            owner_pid = int(owner_text)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Supervisor admission lock owner PID is invalid") from exc
        if owner_pid <= 0 or owner_text != str(owner_pid):
            raise ValueError("Supervisor admission lock owner PID is invalid")
        owner_generation = reader.read_generation(owner_pid)
        _assert_live_process_generation(
            reader,
            owner_generation,
            label="supervisor admission lock owner",
        )
        try:
            lock_rows = (reader.proc_root / "locks").read_text(
                encoding="ascii",
                errors="strict",
            ).splitlines()
        except OSError as exc:
            raise ValueError(
                f"Kernel lock table is unreadable: {type(exc).__name__}"
            ) from exc
        expected_device = (os.major(before.st_dev), os.minor(before.st_dev))
        matches: list[
            tuple[str, str, str, str, str, str, int]
        ] = []
        for row in lock_rows:
            fields = row.split()
            if len(fields) < 8 or fields[1] == "->":
                continue
            try:
                major_hex, minor_hex, inode_text = fields[5].split(":", 2)
                row_device = (int(major_hex, 16), int(minor_hex, 16))
                row_inode = int(inode_text)
                row_pid = int(fields[4])
            except (IndexError, ValueError):
                continue
            if row_device == expected_device and row_inode == before.st_ino:
                matches.append(
                    (
                        fields[0].rstrip(":"),
                        fields[1],
                        fields[2],
                        fields[3],
                        fields[6],
                        fields[7],
                        row_pid,
                    )
                )
        if len(matches) != 1:
            raise ValueError(
                "Supervisor admission lock must have exactly one kernel lock owner"
            )
        (
            lock_id,
            lock_kind,
            lock_class,
            lock_mode,
            lock_start,
            lock_end,
            kernel_owner_pid,
        ) = matches[0]
        if (
            lock_kind != "FLOCK"
            or lock_class != "ADVISORY"
            or lock_mode != "WRITE"
            or lock_start != "0"
            or lock_end != "EOF"
        ):
            raise ValueError("Supervisor admission lock has the wrong kernel lock mode")
        if kernel_owner_pid != owner_pid:
            raise ValueError("Supervisor admission lock file and kernel owner differ")
        _assert_live_process_generation(
            reader,
            owner_generation,
            label="supervisor admission lock owner",
        )
        _assert_path_identity(
            path,
            identity,
            label="Supervisor admission lock",
            require_directory=False,
        )
        return SupervisorAdmissionLockIdentity(
            path=path,
            device=before.st_dev,
            inode=before.st_ino,
            byte_length=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            mtime_ns=before.st_mtime_ns,
            ctime_ns=before.st_ctime_ns,
            kernel_lock_id=lock_id,
            kernel_lock_kind=lock_kind,
            kernel_lock_class=lock_class,
            kernel_lock_mode=lock_mode,
            kernel_lock_start=lock_start,
            kernel_lock_end=lock_end,
            owner_pid=owner_pid,
            owner_starttime_ticks=owner_generation.starttime_ticks,
        )
    finally:
        os.close(descriptor)


def _assert_live_process_generation(
    reader: RuntimeProcessReader,
    expected: ProcessGeneration,
    *,
    label: str,
    allow_zombie: bool = False,
) -> ProcessGeneration:
    current = reader.read_generation(expected.pid)
    if current.pid != expected.pid or current.starttime_ticks != expected.starttime_ticks:
        raise ValueError(
            f"Process generation changed before {label}: PID {expected.pid} was reused"
        )
    if not allow_zombie and current.state == "Z":
        raise ValueError(f"Process {expected.pid} is a zombie before {label}")
    return current


def _guarded_process_read(
    reader: RuntimeProcessReader,
    generation: ProcessGeneration,
    *,
    label: str,
    operation: Callable[[], Any],
    allow_zombie: bool = False,
) -> Any:
    _assert_live_process_generation(
        reader,
        generation,
        label=f"{label} read",
        allow_zombie=allow_zombie,
    )
    value = operation()
    _assert_live_process_generation(
        reader,
        generation,
        label=f"{label} comparison",
        allow_zombie=allow_zombie,
    )
    return value


def _guarded_process_compare(
    reader: RuntimeProcessReader,
    generation: ProcessGeneration,
    *,
    label: str,
    actual: Any,
    expected: Any,
) -> None:
    _assert_live_process_generation(reader, generation, label=label)
    if actual != expected:
        raise ValueError(f"Supervisor process {label} mismatch")


def _strict_live_config(identity: CandidateRuntimeIdentity) -> dict[str, Any]:
    try:
        decoded = identity.config_bytes.decode("utf-8", errors="strict")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Captured live config is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Captured live config must be a JSON object")
    return payload


def _strict_config_payload(
    identity: CandidateRuntimeIdentity,
    override: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return an isolated config generation for pre-install validation."""

    if override is None:
        return _strict_live_config(identity)
    payload = copy.deepcopy(dict(override))
    if not isinstance(payload, dict):
        raise ValueError("Governed config override must be a JSON object")
    return payload


def _absolute_config_path(raw: Any, *, label: str) -> Path:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise ValueError(f"Captured live config {label} is invalid")
    path = Path(raw)
    _validate_absolute_identity_path(path, label=f"Captured live config {label}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Captured live config {label} cannot be resolved") from exc
    if resolved != path:
        raise ValueError(f"Captured live config {label} is not canonical")
    return path


def _expected_supervisor_process_contract(
    identity: CandidateRuntimeIdentity,
    *,
    supervisor_argv: tuple[str, ...] | None = None,
    config_override: Mapping[str, Any] | None = None,
) -> ExpectedSupervisorProcessContract:
    config = _strict_config_payload(identity, config_override)
    watchdog = config.get("watchdog")
    if not isinstance(watchdog, dict):
        raise ValueError("Captured live config watchdog object is missing")
    raw_command: Any = (
        list(supervisor_argv)
        if supervisor_argv is not None
        else watchdog.get("supervisor_command")
    )
    if (
        not isinstance(raw_command, list)
        or not raw_command
        or any(not isinstance(item, str) or not item for item in raw_command)
    ):
        raise ValueError("Captured live config supervisor_command is invalid")
    argv = tuple(raw_command)
    expected_entrypoint = identity.candidate_root / SUPERVISOR_ENTRYPOINT_RELATIVE
    if argv.count(str(expected_entrypoint)) != 1:
        raise ValueError(
            "Captured live config does not bind the exact canonical supervisor entrypoint"
        )
    if argv.count("--config") != 1:
        raise ValueError("Captured live config must contain exactly one --config option")
    config_index = argv.index("--config")
    if config_index + 1 >= len(argv) or argv[config_index + 1] != str(identity.config_path):
        raise ValueError("Captured live config command does not bind its exact config path")
    executable_arg = Path(argv[0])
    if not executable_arg.is_absolute():
        raise ValueError("Captured live config executable must be absolute")
    try:
        executable = executable_arg.resolve(strict=True)
    except OSError as exc:
        raise ValueError("Captured live config executable cannot be resolved") from exc

    paths = config.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("Captured live config paths object is missing")
    status_file = _absolute_config_path(
        paths.get("status_file"),
        label="paths.status_file",
    )
    state_file = _absolute_config_path(
        paths.get("state_file"),
        label="paths.state_file",
    )
    status_root = status_file.parent
    if state_file.parent.name != ".orchestrator":
        raise ValueError("Captured live config state_file is outside .orchestrator")
    if state_file.parent.parent != status_root:
        raise ValueError("Captured live config status and state roots differ")

    return ExpectedSupervisorProcessContract(
        executable=executable,
        argv=argv,
        entrypoint=expected_entrypoint,
        config_path=identity.config_path,
        cwd=identity.candidate_root,
        cwd_device=identity.candidate_root_device,
        cwd_inode=identity.candidate_root_inode,
        cwd_commit=identity.head_commit,
        cwd_tree=identity.tracked_tree_identity,
        command_root=str(identity.candidate_root),
        runtime_sha=identity.head_commit,
        status_root=str(status_root),
        admission_lock_path=status_root / ".orchestrator" / "supervisor.lock",
    )


def _looks_like_supervisor_candidate(argv: tuple[str, ...]) -> bool:
    return any(
        PurePosixPath(argument).name == "supervisor.py"
        for argument in argv[1:]
    )


def _read_process_cwd_git_identity(
    cwd: ProcessCwdIdentity,
) -> tuple[str, str]:
    handle = _open_candidate_root_handle(cwd.path)
    try:
        if (
            handle.identity.device != cwd.device
            or handle.identity.inode != cwd.inode
        ):
            raise ValueError("Process cwd Git root identity changed")
        return _read_head_tree(handle)
    finally:
        _close_candidate_root_handle(handle)


def discover_incumbent_supervisor_process(
    identity: CandidateRuntimeIdentity,
    *,
    expected_argv: tuple[str, ...] | None = None,
    expected_contract: ExpectedSupervisorProcessContract | None = None,
    reader: RuntimeProcessReader | None = None,
    cwd_git_identity_reader: Callable[
        [ProcessCwdIdentity], tuple[str, str]
    ] | None = None,
    candidate_revalidator: Callable[[], None] | None = None,
) -> SupervisorProcessIdentity:
    """Discover and bind exactly one incumbent to the immutable candidate."""
    runtime_reader = reader or ProcfsRuntimeProcessReader()
    git_reader = cwd_git_identity_reader or _read_process_cwd_git_identity
    expected = expected_contract or _expected_supervisor_process_contract(
        identity,
        supervisor_argv=expected_argv,
    )
    if expected_contract is not None and expected_argv is not None:
        if expected_contract.argv != expected_argv:
            raise ValueError("Explicit process contract and argv disagree")
    lock_before = runtime_reader.read_admission_lock(
        expected.admission_lock_path
    )

    candidates: list[tuple[ProcessGeneration, tuple[str, ...]]] = []
    discovery_errors: list[str] = []
    for pid in runtime_reader.list_pids():
        try:
            generation = runtime_reader.read_generation(pid)
        except ProcessLookupError:
            continue
        except Exception as exc:
            discovery_errors.append(
                f"pid={pid}:stat:{type(exc).__name__}"
            )
            continue
        try:
            argv = _guarded_process_read(
                runtime_reader,
                generation,
                label="argv discovery",
                operation=lambda pid=pid: runtime_reader.read_argv(pid),
                allow_zombie=True,
            )
        except Exception as exc:
            discovery_errors.append(
                f"pid={pid}:argv:{type(exc).__name__}"
            )
            continue
        if _looks_like_supervisor_candidate(argv):
            if generation.state == "Z":
                raise ValueError(f"Supervisor candidate PID {pid} is a zombie")
            candidates.append((generation, argv))

    if discovery_errors:
        raise ValueError(
            "Supervisor candidate enumeration was incomplete: "
            + ",".join(discovery_errors)
        )
    if len(candidates) != 1:
        raise ValueError(
            "Expected exactly one live supervisor candidate; "
            f"found {len(candidates)}"
        )

    generation, argv = candidates[0]
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="admission lock owner PID",
        actual=lock_before.owner_pid,
        expected=generation.pid,
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="admission lock owner starttime",
        actual=lock_before.owner_starttime_ticks,
        expected=generation.starttime_ticks,
    )
    entrypoints = tuple(
        argument
        for argument in argv[1:]
        if PurePosixPath(argument).name == "supervisor.py"
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="canonical supervisor entrypoint",
        actual=entrypoints,
        expected=(str(expected.entrypoint),),
    )
    config_indexes = tuple(
        index for index, argument in enumerate(argv) if argument == "--config"
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="config option cardinality",
        actual=len(config_indexes),
        expected=1,
    )
    config_index = config_indexes[0]
    actual_config = argv[config_index + 1] if config_index + 1 < len(argv) else None
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="config path",
        actual=actual_config,
        expected=str(expected.config_path),
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="full argv",
        actual=argv,
        expected=expected.argv,
    )

    executable = _guarded_process_read(
        runtime_reader,
        generation,
        label="executable",
        operation=lambda: runtime_reader.read_executable(generation.pid),
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="executable",
        actual=executable,
        expected=expected.executable,
    )
    cwd = _guarded_process_read(
        runtime_reader,
        generation,
        label="cwd",
        operation=lambda: runtime_reader.read_cwd(generation.pid),
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="cwd realpath",
        actual=cwd.path,
        expected=expected.cwd,
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="cwd device",
        actual=cwd.device,
        expected=expected.cwd_device,
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="cwd inode",
        actual=cwd.inode,
        expected=expected.cwd_inode,
    )
    cwd_commit, cwd_tree = _guarded_process_read(
        runtime_reader,
        generation,
        label="cwd Git identity",
        operation=lambda: git_reader(cwd),
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="cwd commit",
        actual=cwd_commit,
        expected=expected.cwd_commit,
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="cwd tree",
        actual=cwd_tree,
        expected=expected.cwd_tree,
    )

    environment_contract = _guarded_process_read(
        runtime_reader,
        generation,
        label="environment contract",
        operation=lambda: runtime_reader.read_environment_contract(
            generation.pid
        ),
    )
    expected_environment = {
        "PANTHEON_COMMAND_ROOT": expected.command_root,
        "PANTHEON_COMMAND_RUNTIME_SHA": expected.runtime_sha,
        "PANTHEON_STATUS_ROOT": expected.status_root,
    }
    public_key_environment_names = (
        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON",
        "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON",
    )
    present_public_key_names = {
        name for name in public_key_environment_names if name in environment_contract
    }
    if present_public_key_names and present_public_key_names != set(public_key_environment_names):
        raise ValueError("Supervisor process public-key authority environment is incomplete")
    # A pre-V2 incumbent may have neither verifier map.  Once the V2 runtime is
    # launched, both maps are part of its exact process identity and every
    # later promotion must match the operator's expected public policy.
    if present_public_key_names:
        for name in public_key_environment_names:
            expected_value = str(os.environ.get(name) or "").strip()
            try:
                decoded = json.loads(expected_value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{name} must be valid JSON") from exc
            if not isinstance(decoded, dict) or not decoded:
                raise ValueError(f"{name} must be a non-empty JSON object")
            expected_environment[name] = expected_value
    supervisor_index = expected.argv.index(str(expected.entrypoint))
    if "-B" in expected.argv[1:supervisor_index]:
        # A repaired runtime binds both layers: ``-B`` protects the supervisor
        # interpreter and the environment protects every descendant.  Legacy
        # incumbents without ``-B`` remain capturable for their one governed
        # migration; candidate and rollback variants both receive the flag.
        expected_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if set(environment_contract) != set(expected_environment):
        _guarded_process_compare(
            runtime_reader,
            generation,
            label="environment allowlist",
            actual=set(environment_contract),
            expected=set(expected_environment),
        )
    for name in expected_environment:
        _guarded_process_compare(
            runtime_reader,
            generation,
            label=f"environment {name}",
            actual=environment_contract.get(name),
            expected=expected_environment[name],
        )

    if candidate_revalidator is not None:
        _assert_live_process_generation(
            runtime_reader,
            generation,
            label="candidate runtime revalidation",
        )
        candidate_revalidator()
    lock_after = runtime_reader.read_admission_lock(
        expected.admission_lock_path
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="admission lock generation",
        actual=lock_after,
        expected=lock_before,
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="final admission lock owner PID",
        actual=lock_after.owner_pid,
        expected=generation.pid,
    )
    _guarded_process_compare(
        runtime_reader,
        generation,
        label="final admission lock owner starttime",
        actual=lock_after.owner_starttime_ticks,
        expected=generation.starttime_ticks,
    )
    _assert_live_process_generation(
        runtime_reader,
        generation,
        label="final process identity",
    )
    return SupervisorProcessIdentity(
        generation=generation,
        executable=executable,
        argv=argv,
        entrypoint=expected.entrypoint,
        config_path=expected.config_path,
        cwd=cwd,
        cwd_commit=cwd_commit,
        cwd_tree=cwd_tree,
        environment_contract=tuple(
            (name, environment_contract[name])
            for name in expected_environment
        ),
        admission_lock=lock_after,
    )


def _argv_sha256(argv: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for argument in argv:
        encoded = argument.encode("utf-8", errors="strict")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


def _supervisor_process_identity_summary(
    identity: SupervisorProcessIdentity,
) -> dict[str, Any]:
    lock = identity.admission_lock
    return {
        "pid": identity.generation.pid,
        "starttime_ticks": identity.generation.starttime_ticks,
        "executable": str(identity.executable),
        "argv_count": len(identity.argv),
        "argv_sha256": _argv_sha256(identity.argv),
        "entrypoint": str(identity.entrypoint),
        "config_path": str(identity.config_path),
        "cwd": str(identity.cwd.path),
        "cwd_device": identity.cwd.device,
        "cwd_inode": identity.cwd.inode,
        "cwd_commit": identity.cwd_commit,
        "cwd_tree": identity.cwd_tree,
        "environment_contract": dict(identity.environment_contract),
        "admission_lock": {
            "path": str(lock.path),
            "device": lock.device,
            "inode": lock.inode,
            "byte_length": lock.byte_length,
            "sha256": lock.sha256,
            "mtime_ns": lock.mtime_ns,
            "ctime_ns": lock.ctime_ns,
            "kernel_lock_id": lock.kernel_lock_id,
            "kernel_lock_kind": lock.kernel_lock_kind,
            "kernel_lock_class": lock.kernel_lock_class,
            "kernel_lock_mode": lock.kernel_lock_mode,
            "kernel_lock_start": lock.kernel_lock_start,
            "kernel_lock_end": lock.kernel_lock_end,
            "owner_pid": lock.owner_pid,
            "owner_starttime_ticks": lock.owner_starttime_ticks,
        },
    }


def _governed_launch_contract_summary(
    contract: GovernedSupervisorLaunchContract,
) -> dict[str, Any]:
    return {
        "interpreter": {
            "path": str(contract.interpreter.path),
            "device": contract.interpreter.device,
            "inode": contract.interpreter.inode,
            "mode": contract.interpreter.mode,
            "byte_length": contract.interpreter.byte_length,
            "sha256": contract.interpreter.sha256,
        },
        "argv_count": len(contract.argv),
        "argv_sha256": _argv_sha256(contract.argv),
        "cwd": str(contract.cwd),
        "cwd_device": contract.cwd_device,
        "cwd_inode": contract.cwd_inode,
        "required_environment": dict(contract.required_environment),
        "scrubbed_environment_names_sha256": (
            contract.scrubbed_environment_names_sha256
        ),
        "scrubbed_environment_variable_count": (
            contract.scrubbed_environment_variable_count
        ),
        "sources": [
            {
                "role": source.role,
                "path": str(source.path),
                "device": source.device,
                "inode": source.inode,
                "mode": source.mode,
                "byte_length": source.byte_length,
                "sha256": source.sha256,
            }
            for source in contract.source_identities
        ],
        "status_command": {
            "root": str(contract.status_command_root),
            "runtime_sha": contract.status_command_runtime_sha,
            "remote": contract.status_command_remote,
            "base_ref": contract.status_command_base_ref,
        },
        "status_root": str(contract.status_root),
        "task_state_event_log": str(contract.task_state_event_log),
        "task_state_event_log_identity": {
            "path": str(contract.task_state_event_log_identity.path),
            "device": contract.task_state_event_log_identity.device,
            "inode": contract.task_state_event_log_identity.inode,
            "mode": contract.task_state_event_log_identity.mode,
            "captured_size": contract.task_state_event_log_identity.captured_size,
            "prefix_sha256": contract.task_state_event_log_identity.prefix_sha256,
            "captured_at_size": contract.task_state_event_log_identity.captured_at_size,
        } if contract.task_state_event_log_identity is not None else None,
        "worker_worktree_root": str(contract.worker_worktree_root),

        "intentional_restart_path": str(contract.intentional_restart_path),
        "stdout_log_path": str(contract.stdout_log_path),
        "stderr_log_path": str(contract.stderr_log_path),
    }


def build_candidate_runtime_identity(
    candidate_path: Path,
    config_path: Path | None = None,
) -> CandidateRuntimeIdentity:
    """Capture one candidate or incumbent root, Git tree, and live-config snapshot."""
    root_handle = _open_candidate_root_handle(candidate_path)
    try:
        resolved_root = root_handle.path
        root_identity = root_handle.identity
        basename = resolved_root.name

        remote_url = parse_origin_url(root_handle)
        remote = validate_remote_url(remote_url)
        head, tracked_tree, trusted_dev = _capture_git_identity(
            root_handle,
            basename,
        )
        _assert_candidate_handle_path(root_handle)

        selected_config_path = config_path or LIVE_SUPERVISOR_CONFIG_PATH
        config_bytes, config_identity, config_path_components = _capture_config_bytes(
            selected_config_path,
            expected_path=LIVE_SUPERVISOR_CONFIG_PATH,
        )

        verify_working_tree_cleanliness(
            root_handle,
            expected_head=head,
            expected_tree=tracked_tree,
        )

        # Repeat descriptor-bound root/tree/status checks after reading the
        # external config so a deleted/replaced root or concurrent mutation
        # cannot yield a mixed identity object.
        _assert_candidate_handle_path(root_handle)
        verify_working_tree_cleanliness(
            root_handle,
            expected_head=head,
            expected_tree=tracked_tree,
        )
        # A freshly materialized checkout can refresh and atomically replace
        # its index while the read-only cleanliness probes run.  Re-open the
        # candidate only after every such probe, then bind the returned
        # snapshot to those final metadata identities.  Later revalidation
        # still rejects any replacement after this capture point.
        final_handle = _open_candidate_root_handle(resolved_root)
        try:
            if final_handle.identity != root_identity:
                raise ValueError(
                    "Candidate root file identity changed during capture"
                )
            _assert_candidate_handle_path(final_handle)
            final_head, final_tree = _read_head_tree(final_handle)
            if (final_head, final_tree) != (head, tracked_tree):
                raise ValueError("Candidate HEAD/tree changed during capture")
            final_remote_url = parse_origin_url(final_handle)
            final_remote = validate_remote_url(final_remote_url)
            if final_remote_url != remote_url or final_remote.slug != remote.slug:
                raise ValueError("Candidate remote identity changed during capture")

            return CandidateRuntimeIdentity(
                candidate_root=resolved_root,
                candidate_root_device=root_identity.device,
                candidate_root_inode=root_identity.inode,
                git_directory_device=final_handle.git_identity.device,
                git_directory_inode=final_handle.git_identity.inode,
                git_objects_device=final_handle.git_objects_identity.device,
                git_objects_inode=final_handle.git_objects_identity.inode,
                git_config_device=final_handle.git_config_identity.device,
                git_config_inode=final_handle.git_config_identity.inode,
                git_head_device=final_handle.git_head_identity.device,
                git_head_inode=final_handle.git_head_identity.inode,
                git_index_device=final_handle.git_index_identity.device,
                git_index_inode=final_handle.git_index_identity.inode,
                basename=basename,
                head_commit=head,
                tracked_tree_identity=tracked_tree,
                accepted_dev_commit=trusted_dev.commit,
                remote_url=remote_url,
                canonical_remote=f"github.com/{remote.slug}",
                repository_slug=remote.slug,
                config_path=LIVE_SUPERVISOR_CONFIG_PATH,
                config_device=config_identity.device,
                config_inode=config_identity.inode,
                config_path_components=config_path_components,
                config_bytes=config_bytes,
                config_byte_length=len(config_bytes),
                config_sha256=hashlib.sha256(config_bytes).hexdigest(),
            )
        finally:
            _close_candidate_root_handle(final_handle)
    finally:
        _close_candidate_root_handle(root_handle)


def load_json_strict(path: Path) -> dict[str, Any]:
    """Load JSON from path, failing closed (raising exception/returning error envelope)."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    content = path.read_text(encoding="utf-8")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"JSON at {path} must be a dictionary object, got {type(data).__name__}")
    return data


def _candidate_identity_summary(identity: CandidateRuntimeIdentity) -> dict[str, Any]:
    return {
        "candidate_root": str(identity.candidate_root),
        "candidate_root_device": identity.candidate_root_device,
        "candidate_root_inode": identity.candidate_root_inode,
        "git_directory_device": identity.git_directory_device,
        "git_directory_inode": identity.git_directory_inode,
        "git_objects_device": identity.git_objects_device,
        "git_objects_inode": identity.git_objects_inode,
        "git_config_device": identity.git_config_device,
        "git_config_inode": identity.git_config_inode,
        "git_head_device": identity.git_head_device,
        "git_head_inode": identity.git_head_inode,
        "git_index_device": identity.git_index_device,
        "git_index_inode": identity.git_index_inode,
        "basename": identity.basename,
        "head_commit": identity.head_commit,
        "tracked_tree_identity": identity.tracked_tree_identity,
        "accepted_dev_commit": identity.accepted_dev_commit,
        "remote_url": identity.remote_url,
        "canonical_remote": identity.canonical_remote,
        "repository_slug": identity.repository_slug,
        "config_path": str(identity.config_path),
        "config_device": identity.config_device,
        "config_inode": identity.config_inode,
        "config_path_components": [
            {
                "path": str(component.path),
                "device": component.identity.device,
                "inode": component.identity.inode,
                "mode": component.identity.mode,
            }
            for component in identity.config_path_components
        ],
        "config_byte_length": identity.config_byte_length,
        "config_sha256": identity.config_sha256,
    }


def _runtime_health_identity(process: SupervisorProcessIdentity) -> dict[str, Any]:
    """Adapt the already generation-guarded promotion capture to #4763 health."""

    return {
        "pid": process.generation.pid,
        "starttime_ticks": process.generation.starttime_ticks,
        "state": process.generation.state,
        "argv": process.argv,
        "cwd": str(process.cwd.path),
        "environment": dict(process.environment_contract),
        "singleton_owner_pid": process.admission_lock.owner_pid,
        "singleton_owner_starttime_ticks": process.admission_lock.owner_starttime_ticks,
    }


def capture_promotion_snapshot(
    repo_root: Path,
    *,
    config_path_arg: Path | None = None,
    now: datetime | None = None,
    launch_filesystem: LaunchFilesystem | None = None,
    inherited_environment: Mapping[str, str] | None = None,
    launch_environment: Mapping[str, str] | None = None,
    launch_cwd: Path | None = None,
    stdout_log_path: Path | None = None,
    stderr_log_path: Path | None = None,
) -> dict[str, Any]:
    """Capture live-schema supervisor runtime state and evaluate promotion invariants."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config_path_resolved = config_path_arg or (repo_root / ".orchestrator" / "config.json")

    candidate_identity: CandidateRuntimeIdentity | None = None
    identity_error: str | None = None
    identity_revalidation_stages: list[str] = []
    supervisor_process_identity: SupervisorProcessIdentity | None = None
    supervisor_process_error: str | None = None
    governed_launch_contract: GovernedSupervisorLaunchContract | None = None
    governed_launch_error: str | None = None

    def revalidate_candidate(stage: str) -> bool:
        nonlocal identity_error
        if candidate_identity is None:
            return False
        try:
            candidate_identity.verify_immutable_snapshot()
        except Exception as exc:
            identity_error = f"{stage}: {exc}"
            return False
        identity_revalidation_stages.append(stage)
        return True

    try:
        candidate_identity = build_candidate_runtime_identity(repo_root)
    except Exception as exc:
        identity_error = str(exc)
    if candidate_identity is not None and identity_error is None:
        revalidate_candidate("after_root_git_discovery")
    if candidate_identity is not None and identity_error is None:
        try:
            supervisor_process_identity = discover_incumbent_supervisor_process(
                candidate_identity,
                candidate_revalidator=(
                    candidate_identity.verify_immutable_snapshot
                ),
            )
        except Exception as exc:
            supervisor_process_error = str(exc)
        else:
            revalidate_candidate("after_process_discovery")
    else:
        supervisor_process_error = "Candidate runtime identity is unavailable"
    if (
        candidate_identity is not None
        and identity_error is None
        and supervisor_process_identity is not None
        and supervisor_process_error is None
    ):
        try:
            governed_launch_contract = build_governed_supervisor_launch_contract(
                candidate_identity,
                filesystem=launch_filesystem,
                inherited_environment=inherited_environment,
                launch_environment=launch_environment,
                launch_cwd=launch_cwd,
                stdout_log_path=stdout_log_path,
                stderr_log_path=stderr_log_path,
            )
        except Exception as exc:
            governed_launch_error = str(exc)
        else:
            revalidate_candidate("after_launch_contract_assembly")
    else:
        governed_launch_error = "Candidate/process identity is unavailable"

    file_errors: list[dict[str, str]] = []

    try:
        config = load_json_strict(config_path_resolved)
    except Exception as e:
        config = {}
        file_errors.append({"file": str(config_path_resolved), "error": str(e)})

    health_identity_args: dict[str, Any] = {}
    if candidate_identity is not None and supervisor_process_identity is not None:
        health_identity_args = {
            "expected_command_root": candidate_identity.candidate_root,
            "expected_source_commit": candidate_identity.head_commit,
            "expected_config_sha256": candidate_identity.config_sha256,
            "expected_process_generation": (
                supervisor_process_identity.generation.pid,
                supervisor_process_identity.generation.starttime_ticks,
            ),
            "verified_runtime_identity": _runtime_health_identity(
                supervisor_process_identity
            ),
        }
    health_report = evaluate_runtime_health(
        candidate_identity.candidate_root if candidate_identity is not None else repo_root,
        config_path_arg=(
            candidate_identity.config_path
            if candidate_identity is not None
            else config_path_resolved
        ),
        now=now,
        **health_identity_args,
    )

    paths = config.get("paths") if isinstance(config.get("paths"), dict) else None
    if paths is None or "status_file" not in paths:
        file_errors.append({
            "file": "config.json:paths.status_file",
            "error": "Missing required paths.status_file in config",
        })
        status_path = repo_root / "ai-status.json"
    else:
        status_file_raw = str(paths["status_file"])
        status_path = Path(status_file_raw)
        if not status_path.is_absolute():
            status_path = repo_root / status_path

    try:
        ai_status = load_json_strict(status_path)
    except Exception as e:
        ai_status = {}
        file_errors.append({"file": str(status_path), "error": str(e)})

    if paths is None or "state_file" not in paths:
        file_errors.append({
            "file": "config.json:paths.state_file",
            "error": "Missing required paths.state_file in config",
        })
        state_path = repo_root / ".orchestrator" / "state.json"
    else:
        state_file_raw = str(paths["state_file"])
        state_path = Path(state_file_raw)
        if not state_path.is_absolute():
            state_path = repo_root / state_path

    try:
        state = load_json_strict(state_path)
    except Exception as e:
        state = {}
        file_errors.append({"file": str(state_path), "error": str(e)})

    provider_cap_path = None
    provider_capabilities: dict[str, Any] = {}
    if paths is not None and "provider_capabilities" in paths:
        raw_cap = str(paths["provider_capabilities"])
        p_path = Path(raw_cap)
        if not p_path.is_absolute():
            p_path = repo_root / p_path
        provider_cap_path = p_path
    else:
        file_errors.append({
            "file": "config.json:paths.provider_capabilities",
            "error": "Missing required paths.provider_capabilities in config",
        })
        provider_cap_path = repo_root / ".orchestrator" / "provider_capabilities.json"

    try:
        provider_capabilities = load_json_strict(provider_cap_path)
    except Exception as e:
        provider_capabilities = {}
        file_errors.append({"file": str(provider_cap_path), "error": str(e)})

    durable_queue_events: list[dict[str, Any]] = []
    raw_event_queue = paths.get("event_queue") if paths is not None else None
    if not isinstance(raw_event_queue, str) or not raw_event_queue.strip():
        file_errors.append({
            "file": "config.json:paths.event_queue",
            "error": "Missing required paths.event_queue in config",
        })
    else:
        event_queue_path = Path(raw_event_queue)
        if not event_queue_path.is_absolute():
            event_queue_path = repo_root / event_queue_path
        try:
            durable_queue_events = list(
                _capture_jsonl_document(
                    event_queue_path,
                    label="Durable supervisor event queue",
                ).rows
            )
        except Exception as exc:
            file_errors.append({"file": str(event_queue_path), "error": str(exc)})

    coord_root = resolved_coordinator_status_root(repo_root, config)
    lock_path = coord_root / ".orchestrator" / "supervisor.lock"

    if candidate_identity is not None and identity_error is None:
        revalidate_candidate("final_preflight_readback")
    if identity_error is not None and governed_launch_error is None:
        governed_launch_error = identity_error

    # Evaluate promotion invariants
    invariants = evaluate_promotion_invariants(
        health_report=health_report,
        ai_status=ai_status,
        state=state,
        provider_capabilities=provider_capabilities,
        lock_path=lock_path,
        file_errors=file_errors,
        now=now,
        config=config,
        durable_queue_events=durable_queue_events,
    )
    invariants.insert(
        0,
        {
            "name": "candidate_runtime_identity_immutable",
            "ok": candidate_identity is not None and identity_error is None,
            "details": {
                "error": identity_error,
                "identity": (
                    _candidate_identity_summary(candidate_identity)
                    if candidate_identity is not None
                    else None
                ),
            },
        },
    )
    invariants.insert(
        1,
        {
            "name": "incumbent_supervisor_process_identity_immutable",
            "ok": (
                supervisor_process_identity is not None
                and supervisor_process_error is None
            ),
            "details": {
                "error": supervisor_process_error,
                "identity": (
                    _supervisor_process_identity_summary(
                        supervisor_process_identity
                    )
                    if supervisor_process_identity is not None
                    else None
                ),
            },
        },
    )
    invariants.insert(
        2,
        {
            "name": "governed_supervisor_launch_contract_immutable",
            "ok": (
                governed_launch_contract is not None
                and governed_launch_error is None
                and identity_error is None
            ),
            "details": {
                "error": governed_launch_error,
                "contract": (
                    _governed_launch_contract_summary(governed_launch_contract)
                    if governed_launch_contract is not None
                    else None
                ),
            },
        },
    )

    all_invariants_pass = all(inv["ok"] for inv in invariants)

    return {
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        "repo_root": str(repo_root),
        "config_path": str(config_path_resolved),
        "preflight_mode": "discover_only",
        "identity_revalidation_stages": identity_revalidation_stages,
        "candidate_runtime_identity": (
            _candidate_identity_summary(candidate_identity)
            if candidate_identity is not None
            else None
        ),
        "incumbent_supervisor_process_identity": (
            _supervisor_process_identity_summary(
                supervisor_process_identity
            )
            if supervisor_process_identity is not None
            else None
        ),
        "governed_supervisor_launch_contract": (
            _governed_launch_contract_summary(governed_launch_contract)
            if governed_launch_contract is not None
            else None
        ),
        "health_report": health_report,
        "ai_status_summary": {
            "sprint": ai_status.get("sprint"),
            "updated_at": ai_status.get("updated_at"),
            "tasks_count": len(ai_status.get("tasks", [])) if isinstance(ai_status.get("tasks"), list) else 0,
            "agents_count": len(ai_status.get("agents", [])) if isinstance(ai_status.get("agents"), list) else 0,
        },
        "supervisor_state_summary": {
            "lifecycle": state.get("supervisor", {}).get("lifecycle") if isinstance(state.get("supervisor"), dict) else None,
            "last_heartbeat_at": state.get("supervisor", {}).get("last_heartbeat_at") if isinstance(state.get("supervisor"), dict) else None,
        },
        "file_errors": file_errors,
        "invariants": invariants,
        "eligible_for_promotion": all_invariants_pass,
    }


def _is_verified_legacy_journal_migration_source(
    config: Mapping[str, Any],
    failed_health_checks: list[dict[str, Any]],
) -> bool:
    """Accept only the legacy journal shape immediately before one migration.

    This is deliberately not a filename heuristic and it does not validate a
    legacy journal in full.  The migration transaction performs that full
    hash-chain audit while holding the authority locks.  Here we merely avoid
    requiring the V2 head file that a genuine legacy journal has never written.
    """

    if len(failed_health_checks) != 1:
        return False
    failed = failed_health_checks[0]
    if failed.get("name") != "readiness_task_head_accessible":
        return False

    store = config.get("task_state_store")
    if not isinstance(store, Mapping):
        return False
    raw_event_log = store.get("event_log")
    if not isinstance(raw_event_log, str) or not raw_event_log.strip():
        return False
    event_log = Path(raw_event_log)
    if not event_log.is_absolute() or event_log.is_symlink():
        return False
    expected_head = event_log.with_name(f"{event_log.name}.head.json")
    if failed.get("task_head") != str(expected_head):
        return False
    if not str(failed.get("error") or "").startswith("FileNotFoundError:"):
        return False

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(event_log, flags)
    except OSError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return False
        first_line = os.read(descriptor, 128 * 1024).split(b"\n", 1)[0]
    except OSError:
        return False
    finally:
        os.close(descriptor)
    try:
        first_event = json.loads(first_line.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(first_event, dict)
        and first_event.get("version") == LEGACY_EVENT_VERSION
        and first_event.get("type") == LEGACY_EVENT_TYPE
        and first_event.get("sequence") == 1
    )


def evaluate_promotion_invariants(
    health_report: dict[str, Any],
    ai_status: dict[str, Any],
    state: dict[str, Any],
    provider_capabilities: dict[str, Any] | None = None,
    lock_path: Path | None = None,
    file_errors: list[dict[str, str]] | None = None,
    now: datetime | None = None,
    config: dict[str, Any] | None = None,
    durable_queue_events: list[dict[str, Any]] | None = None,
    allow_legacy_journal_migration_source: bool = False,
) -> list[dict[str, Any]]:
    """Evaluate read-only promotion invariants against live schema state."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config = config or {}
    file_errors = file_errors or []
    provider_capabilities = provider_capabilities or {}
    durable_queue_events = durable_queue_events or []
    invariants: list[dict[str, Any]] = []

    # Invariant 0: Fail closed file reading invariant
    invariants.append({
        "name": "config_and_state_files_readable",
        "ok": len(file_errors) == 0,
        "details": {"file_errors": file_errors},
    })

    # Invariant 1: health checks must all pass.  The only exception is the
    # offline pre-migration source audit, where a verified legacy journal has
    # no V2 head file yet. Candidate and ordinary runtime health never use it.
    failed_health_checks = [
        check
        for check in health_report.get("checks", [])
        if isinstance(check, dict) and check.get("ok") is not True
    ]
    legacy_journal_migration_source = _is_verified_legacy_journal_migration_source(
        config,
        failed_health_checks,
    ) if allow_legacy_journal_migration_source else False
    health_ok = bool(health_report.get("healthy", False)) or legacy_journal_migration_source
    invariants.append({
        "name": "runtime_health_clean",
        "ok": health_ok,
        "details": {
            "healthy": health_report.get("healthy", False),
            "failed_checks": [str(check.get("name") or "unnamed") for check in failed_health_checks],
            "legacy_journal_migration_source": legacy_journal_migration_source,
        },
    })

    # Invariant 2: Supervisor lifecycle must be explicitly valid (e.g., 'running') and not None or 'degraded'
    supervisor_info = health_report.get("supervisor", {})
    lifecycle = supervisor_info.get("lifecycle")
    valid_lifecycles = {"running", "idle", "active"}
    lifecycle_ok = isinstance(lifecycle, str) and lifecycle in valid_lifecycles
    invariants.append({
        "name": "supervisor_lifecycle_valid",
        "ok": lifecycle_ok,
        "details": {"lifecycle": lifecycle, "valid_lifecycles": list(valid_lifecycles)},
    })

    # Invariant 3: Supervisor PID binding AND lock_path validation (must require PID alive AND lock held when running)
    pid = supervisor_info.get("pid")
    is_alive = pid_is_alive(pid) if pid is not None else False
    is_lock_held = lock_held(lock_path) if lock_path else False
    supervisor_bound = (pid is not None and is_alive) and is_lock_held
    invariants.append({
        "name": "supervisor_pid_bound_and_locked",
        "ok": supervisor_bound,
        "details": {"pid": pid, "pid_alive": is_alive, "lock_held": is_lock_held, "lock_path": str(lock_path) if lock_path else None},
    })

    # Invariant 4: ai-status.json must be a valid dict with tasks list
    has_valid_status = isinstance(ai_status, dict) and "tasks" in ai_status and isinstance(ai_status["tasks"], list)
    invariants.append({
        "name": "ai_status_schema_valid",
        "ok": has_valid_status,
        "details": {"is_dict": isinstance(ai_status, dict), "has_tasks": "tasks" in ai_status if isinstance(ai_status, dict) else False},
    })

    # Invariant 5: authoritative task-state projection validation.
    # Live schema requires task_state_projection to exist and have:
    # mode == "authoritative", ok is True, caught_up is True, last_error is None,
    # and projected_state_sha256 == expected_state_sha256 (if hash keys present/populated)
    projection = supervisor_info.get("task_state_projection")
    if not isinstance(projection, dict):
        projection = (
            state.get("supervisor", {}).get("task_state_projection")
            if isinstance(state.get("supervisor"), dict)
            else None
        )

    projection_ok = False
    projection_reasons: list[str] = []
    if not isinstance(projection, dict) or not projection:
        if not legacy_journal_migration_source:
            projection_reasons.append("task_state_projection_missing")
    else:
        if projection.get("mode") != "authoritative":
            projection_reasons.append(f"mode_not_authoritative:{projection.get('mode')}")
        if projection.get("ok") is not True:
            projection_reasons.append(f"ok_not_true:{projection.get('ok')}")
        if projection.get("caught_up") is not True:
            projection_reasons.append(f"caught_up_not_true:{projection.get('caught_up')}")
        if projection.get("last_error") is not None:
            projection_reasons.append(f"has_last_error:{projection.get('last_error')}")
        proj_sha = projection.get("projected_state_sha256")
        exp_sha = projection.get("expected_state_sha256")
        if not proj_sha or not isinstance(proj_sha, str) or not proj_sha.strip():
            projection_reasons.append("missing_projected_state_sha256")
        if not exp_sha or not isinstance(exp_sha, str) or not exp_sha.strip():
            projection_reasons.append("missing_expected_state_sha256")
        if proj_sha and exp_sha and proj_sha != exp_sha:
            projection_reasons.append(f"sha_mismatch:{proj_sha}!={exp_sha}")

    projection_ok = len(projection_reasons) == 0
    invariants.append({
        "name": "task_state_projection_valid",
        "ok": projection_ok,
        "details": {
            "task_state_projection": projection,
            "reasons": projection_reasons,
            "legacy_journal_migration_source": legacy_journal_migration_source,
        },
    })

    # Invariant 6: Fresh-loop sequence or staleness check
    # Requires last_successful_loop_at, last_loop_started_at, last_loop_finished_at, last_loop_error == None,
    # and bounded freshness (last_successful_loop_at age <= stall_after_seconds)
    supervisor_state = state.get("supervisor", {}) if isinstance(state.get("supervisor"), dict) else {}
    last_successful_loop_raw = supervisor_state.get("last_successful_loop_at")
    last_started_raw = supervisor_state.get("last_loop_started_at")
    last_finished_raw = supervisor_state.get("last_loop_finished_at")
    last_error = supervisor_state.get("last_loop_error")

    last_successful_loop_at = parse_utc_timestamp(last_successful_loop_raw)
    last_started_at = parse_utc_timestamp(last_started_raw)
    last_finished_at = parse_utc_timestamp(last_finished_raw)

    max_stall = float(config.get("supervisor", {}).get("stall_after_seconds", 900))
    loop_reasons: list[str] = []

    if last_successful_loop_at is None:
        loop_reasons.append("missing_last_successful_loop_at")
    if last_started_at is None:
        loop_reasons.append("missing_last_loop_started_at")
    if last_finished_at is None:
        loop_reasons.append("missing_last_loop_finished_at")
    if last_error is not None:
        loop_reasons.append(f"has_last_loop_error:{last_error}")

    if last_successful_loop_at is not None:
        loop_age = (now - last_successful_loop_at).total_seconds()
        if loop_age > max_stall or loop_age < 0:
            loop_reasons.append(f"loop_stale_or_future:age={loop_age},max={max_stall}")

    loop_fresh = len(loop_reasons) == 0
    invariants.append({
        "name": "fresh_loop_sequence",
        "ok": loop_fresh,
        "details": {
            "last_successful_loop_at": last_successful_loop_at.isoformat() if last_successful_loop_at else None,
            "max_stall": max_stall,
            "reasons": loop_reasons,
        },
    })
    # Invariant 7: state.workers, state.queue object ("events"), worker_worktrees.leases parity
    workers = state.get("workers", {}) if isinstance(state.get("workers"), dict) else {}
    queue_obj = state.get("queue", {}) if isinstance(state.get("queue"), dict) else {}
    queue_events = queue_obj.get("events", {}) if isinstance(queue_obj.get("events"), dict) else {}
    worker_worktrees = state.get("worker_worktrees", {}) if isinstance(state.get("worker_worktrees"), dict) else {}
    leases = worker_worktrees.get("leases", {}) if isinstance(worker_worktrees.get("leases"), dict) else {}

    active_worker_tasks: set[str] = set()
    duplicate_workers: list[str] = []
    parity_reasons: list[str] = []

    # Map active queue events by task_id and event_id
    active_queue_events_by_id: dict[str, dict[str, Any]] = {}
    active_queue_events_by_task: dict[str, list[dict[str, Any]]] = {}

    for evt_id, evt_info in queue_events.items():
        if isinstance(evt_info, dict):
            q_status = evt_info.get("status") or evt_info.get("state")
            if q_status not in ("completed", "failed", "cancelled", "done"):
                actual_id = evt_info.get("id") or evt_id
                active_queue_events_by_id[actual_id] = evt_info
                q_task = evt_info.get("task_id")
                if q_task:
                    active_queue_events_by_task.setdefault(q_task, []).append(evt_info)

    # Helper for canonical worker run_id: nonempty w_info.get("run_id") or w_name
    def get_canonical_run_id(w_name: str, w_info: dict[str, Any]) -> str:
        r_id = w_info.get("run_id")
        if isinstance(r_id, str) and r_id.strip():
            return r_id.strip()
        return w_name

    # Build mapping of canonical_run_id -> (w_name, w_info) across ALL workers (for historical resolution)
    # Detect duplicate canonical run identities and fail closed if active event or lineage touches one.
    workers_by_run_id: dict[str, tuple[str, dict[str, Any]]] = {}
    duplicate_canonical_run_ids: set[str] = set()

    for w_name, w_info in workers.items():
        if isinstance(w_info, dict):
            c_run_id = get_canonical_run_id(w_name, w_info)
            if c_run_id in workers_by_run_id:
                duplicate_canonical_run_ids.add(c_run_id)
            else:
                workers_by_run_id[c_run_id] = (w_name, w_info)

    # Validate active workers
    for w_name, w_info in workers.items():
        if isinstance(w_info, dict):
            task_id = w_info.get("current_task_id") or w_info.get("task_id")
            w_status = w_info.get("status")
            if w_status in ("running", "started", "active", "retry_backoff") and task_id:
                if task_id in active_worker_tasks:
                    duplicate_workers.append(f"{w_name}:{task_id}")
                else:
                    active_worker_tasks.add(task_id)

                w_q_evt_id = w_info.get("queue_event_id")
                w_canonical_run_id = get_canonical_run_id(w_name, w_info)

                # Verify lease parity: active task_id must exist in leases and match worker queue_event_id/run_id if specified
                matching_lease = None
                matching_lease_id = None
                for lease_id, lease_info in leases.items():
                    if isinstance(lease_info, dict) and lease_info.get("task_id") == task_id:
                        matching_lease = lease_info
                        matching_lease_id = lease_id
                        break

                if matching_lease is None:
                    parity_reasons.append(f"active_worker_missing_lease:{w_name}:{task_id}")
                else:
                    l_q_evt_id = matching_lease.get("last_queue_event_id") or matching_lease.get("queue_event_id")
                    l_run_id = matching_lease.get("run_id")
                    if w_q_evt_id and l_q_evt_id and w_q_evt_id != l_q_evt_id:
                        parity_reasons.append(f"mismatched_lease_queue_event_id:{matching_lease_id}:{l_q_evt_id}!={w_q_evt_id}")
                    if l_run_id and w_canonical_run_id != l_run_id:
                        parity_reasons.append(f"mismatched_lease_run_id:{matching_lease_id}:{l_run_id}!={w_canonical_run_id}")

                if w_q_evt_id and w_q_evt_id in active_queue_events_by_id:
                    q_evt = active_queue_events_by_id[w_q_evt_id]
                    q_worker = q_evt.get("worker") or q_evt.get("assigned_worker")
                    if q_worker and q_worker != w_name:
                        parity_reasons.append(f"mismatched_queue_event_worker:{w_q_evt_id}:{q_worker}!={w_name}")
                elif task_id in active_queue_events_by_task:
                    q_evts = active_queue_events_by_task[task_id]
                    matched_evt = False
                    for q_evt in q_evts:
                        q_evt_id = q_evt.get("id")
                        if w_q_evt_id and q_evt_id == w_q_evt_id:
                            matched_evt = True
                            break
                        elif not w_q_evt_id:
                            matched_evt = True
                            break
                    if w_q_evt_id and not matched_evt:
                        first_q_id = q_evts[0].get("id") if q_evts else "unknown"
                        parity_reasons.append(f"mismatched_worker_queue_event_id:{w_name}:{w_q_evt_id}!={first_q_id}")

    if len(duplicate_workers) > 0:
        parity_reasons.append(f"duplicate_active_workers:{duplicate_workers}")

    # Helper function to trace parent_run_id lineage through actual worker records
    def trace_retry_lineage(start_run_id: str, target_run_id: str, expected_task: str, expected_queue_evt_id: str) -> str | None:
        """Trace start_run_id -> target_run_id along parent_run_id links.

        Returns None if valid, or error reason string if invalid.
        """
        curr = start_run_id
        visited: set[str] = set()

        while curr:
            if curr in visited:
                return f"cycle_in_retry_lineage:{start_run_id}"
            visited.add(curr)

            if curr in duplicate_canonical_run_ids:
                return f"duplicate_canonical_run_id:{curr}"

            if curr not in workers_by_run_id:
                return f"missing_history:{start_run_id}->{curr}"

            _, node_info = workers_by_run_id[curr]
            node_task = node_info.get("current_task_id") or node_info.get("task_id")
            node_q_evt = node_info.get("queue_event_id")

            if expected_task and node_task and node_task != expected_task:
                return f"cross_task_retry_lineage:{start_run_id}:{node_task}!={expected_task}"
            if expected_queue_evt_id and node_q_evt and node_q_evt != expected_queue_evt_id:
                return f"cross_event_retry_lineage:{start_run_id}:{node_q_evt}!={expected_queue_evt_id}"

            if curr == target_run_id:
                return None  # Reached target event.run_id successfully and verified task/queue_event!

            parent = node_info.get("parent_run_id")
            if not parent or not isinstance(parent, str) or not parent.strip():
                return f"broken_worker_retry_lineage:{start_run_id}:{curr}!={target_run_id}"

            curr = parent.strip()

        return f"broken_worker_retry_lineage:{start_run_id}:did_not_reach_{target_run_id}"

    # Reverse-link validation: exactly one active worker for each active event matching queue_event_id and task_id
    for evt_id, evt_info in active_queue_events_by_id.items():
        q_task = evt_info.get("task_id")
        q_run_id = evt_info.get("run_id")
        q_lease_owner = evt_info.get("lease_owner")
        q_status = evt_info.get("status") or evt_info.get("state")

        # Skip unstarted queued/pending events that do not have a lease owner assigned
        if q_status in ("queued", "pending") and not (isinstance(q_lease_owner, str) and q_lease_owner.strip()):
            continue

        # Active started event requires nonempty lease_owner
        if not q_lease_owner or not isinstance(q_lease_owner, str) or not q_lease_owner.strip():
            parity_reasons.append(f"missing_lease_owner:{evt_id}")
            continue

        q_lease_owner = q_lease_owner.strip()

        # Find active workers reverse-linked to this event (matching canonical run_id == lease_owner)
        matched_workers: list[tuple[str, dict[str, Any]]] = []
        for w_name, w_info in workers.items():
            if isinstance(w_info, dict) and w_info.get("status") in ("running", "started", "active", "retry_backoff"):
                c_run_id = get_canonical_run_id(w_name, w_info)
                if c_run_id == q_lease_owner or w_info.get("queue_event_id") == evt_id:
                    matched_workers.append((w_name, w_info))

        if len(matched_workers) == 0:
            parity_reasons.append(f"active_queue_event_missing_worker:{evt_id}:{q_task}")
        elif len(matched_workers) > 1:
            m_names = [mw[0] for mw in matched_workers]
            parity_reasons.append(f"active_queue_event_multiple_workers:{evt_id}:{m_names}")
        else:
            w_name, w_info = matched_workers[0]
            w_task = w_info.get("current_task_id") or w_info.get("task_id")
            w_canonical_run_id = get_canonical_run_id(w_name, w_info)
            w_q_evt_id = w_info.get("queue_event_id")

            if w_task and q_task and w_task != q_task:
                parity_reasons.append(f"mismatched_queue_event_worker_task:{evt_id}:{w_name}:{w_task}!={q_task}")
            if w_q_evt_id and w_q_evt_id != evt_id:
                parity_reasons.append(f"mismatched_queue_event_worker_id:{evt_id}:{w_name}:{w_q_evt_id}!={evt_id}")

            # Exactly one active reverse-linked canonical run must equal lease_owner
            if w_canonical_run_id != q_lease_owner:
                parity_reasons.append(f"mismatched_worker_lease_owner:{w_name}:{w_canonical_run_id}!={q_lease_owner}")

            # Verify event.run_id resolves to an actual worker record
            if not q_run_id or not isinstance(q_run_id, str) or q_run_id.strip() not in workers_by_run_id:
                parity_reasons.append(f"missing_history:{evt_id}:run_id_{q_run_id}_not_found")
            else:
                q_run_id = q_run_id.strip()
                # Verify initial run or trace parent_run_id retry lineage
                if w_canonical_run_id != q_run_id:
                    err = trace_retry_lineage(
                        start_run_id=w_canonical_run_id,
                        target_run_id=q_run_id,
                        expected_task=q_task or "",
                        expected_queue_evt_id=evt_id,
                    )
                    if err:
                        parity_reasons.append(err)

    # Check for orphan active leases (leases with status active/running/started that have no active worker or active queue event)
    for lease_id, lease_info in leases.items():
        if not isinstance(lease_info, dict):
            continue
        l_status = lease_info.get("status") or lease_info.get("state")
        l_task = lease_info.get("task_id") or lease_id
        is_explicitly_active = l_status in ("active", "running", "started")
        if is_explicitly_active:
            if l_task not in active_worker_tasks and l_task not in active_queue_events_by_task:
                parity_reasons.append(f"orphan_active_lease:{l_task}")

    lease_parity_ok = len(parity_reasons) == 0
    invariants.append({
        "name": "worker_lease_parity_and_no_duplicates",
        "ok": lease_parity_ok,
        "details": {
            "workers_count": len(workers),
            "queue_events_count": len(queue_events),
            "leases_count": len(leases),
            "duplicate_active_workers": duplicate_workers,
            "reasons": parity_reasons,
        },
    })

    # V2 has no alternate control-worker authority. Promotion may not adopt a
    # legacy chair, discussion-planning, or coordination worker/queue row.
    # These are terminal runtime history states: they retain audit evidence,
    # but their process has already ended and no delivery lease remains. They
    # must not turn an otherwise drained V1 runtime into a fictitious mixed
    # writer. In contrast, ``retry_backoff`` remains nonterminal because it
    # still represents a delivery authority that must return to the queue.
    terminal_runtime_statuses = {
        "completed",
        "failed",
        "cancelled",
        "done",
        "superseded",
        "terminated",
        "retried",
        "retry_quarantined",
    }
    legacy_control_workers: list[str] = []
    legacy_control_queue_events: list[str] = []
    for worker_name, worker_info in workers.items():
        if not isinstance(worker_info, dict):
            continue
        worker_status = str(worker_info.get("status") or "").strip().lower()
        if worker_status in terminal_runtime_statuses:
            continue
        snapshot = worker_info.get("request_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        metadata = snapshot.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        reason = str(snapshot.get("reason") or "")
        if (
            worker_status == "fallback"
            or
            reason.startswith(("chair_review:", "discussion_planning_", "coordination:"))
            or any(isinstance(metadata.get(key), dict) for key in ("chair", "planning", "coordination"))
        ):
            legacy_control_workers.append(str(worker_name))
    for event_id, event_info in queue_events.items():
        if not isinstance(event_info, dict):
            continue
        event_status = str(event_info.get("status") or "").strip().lower()
        if event_status in terminal_runtime_statuses:
            continue
        marker = " ".join(
            str(event_info.get(key) or "")
            for key in ("reason", "event_key", "task_id")
        )
        if any(token in marker for token in ("chair_review:", "discussion_planning_", "coordination:")):
            legacy_control_queue_events.append(str(event_id))
    for index, event_info in enumerate(durable_queue_events):
        if not isinstance(event_info, dict):
            legacy_control_queue_events.append(f"durable:{index}:invalid")
            continue
        event_status = str(event_info.get("status") or "queued").strip().lower()
        if event_status in terminal_runtime_statuses:
            continue
        marker = " ".join(
            str(event_info.get(key) or "")
            for key in ("reason", "event_key", "task_id")
        )
        if any(
            token in marker
            for token in ("chair_review:", "discussion_planning_", "coordination:")
        ):
            legacy_control_queue_events.append(
                f"durable:{event_info.get('event_id') or index}"
            )
    invariants.append(
        {
            "name": "legacy_control_paths_drained",
            "ok": not legacy_control_workers and not legacy_control_queue_events,
            "details": {
                "workers": legacy_control_workers,
                "queue_events": legacy_control_queue_events,
            },
        }
    )

    # V1 workers retain their original command runtime and TaskStore journal
    # environment. They cannot cross the authority switch safely: after the
    # task lock is released they could write V1 while V2 is already canonical.
    undrained_workers = sorted(
        str(worker_name)
        for worker_name, worker_info in workers.items()
        if isinstance(worker_info, dict)
        and str(worker_info.get("status") or "").strip().lower()
        not in terminal_runtime_statuses
    )
    undrained_leases = sorted(
        str(lease_id)
        for lease_id, lease_info in leases.items()
        if isinstance(lease_info, dict)
        and str(lease_info.get("status") or lease_info.get("state") or "")
        .strip()
        .lower()
        in {"active", "running", "started", "leased"}
    )
    invariants.append(
        {
            "name": "v1_execution_authority_drained",
            "ok": not undrained_workers and not undrained_leases,
            "details": {
                "workers": undrained_workers,
                "leases": undrained_leases,
                "reason": "no V1 worker or execution lease may survive the V2 TaskStore cutover",
            },
        }
    )

    # Invariant 9: Provider readiness baseline comparing provider_capabilities against configured active providers
    provider_reasons: list[str] = []
    baseline_capabilities: dict[str, Any] = {}

    cap_providers = provider_capabilities.get("providers") if isinstance(provider_capabilities.get("providers"), dict) else {}
    configured_providers = config.get("providers", {}) if isinstance(config.get("providers"), dict) else {}

    if not cap_providers:
        provider_reasons.append("no_provider_capabilities_loaded")

    # Determine required active provider types ONLY from active workers or active queue events (or leases bound to an active worker/queue event)
    active_providers_required: set[str] = set()
    for w_name, w_info in workers.items():
        if isinstance(w_info, dict) and w_info.get("status") in ("running", "started", "active"):
            provider_type = w_info.get("provider") or w_info.get("type")
            if provider_type and isinstance(provider_type, str):
                active_providers_required.add(provider_type.lower())

    for lease_id, lease_info in leases.items():
        if isinstance(lease_info, dict):
            l_task = lease_info.get("task_id")
            l_status = lease_info.get("status") or lease_info.get("state")
            is_active_ref = (l_task in active_worker_tasks) or (l_task in active_queue_events_by_task) or (l_status in ("active", "running", "started"))
            if is_active_ref:
                provider_type = lease_info.get("provider") or lease_info.get("type")
                if provider_type and isinstance(provider_type, str):
                    active_providers_required.add(provider_type.lower())

    # Build readiness baseline for all configured providers
    for p_id, p_config in configured_providers.items():
        if not isinstance(p_config, dict):
            continue
        is_enabled = p_config.get("enabled", True)
        p_id_lower = p_id.lower()
        p_cap = cap_providers.get(p_id)
        baseline_entry = {
            "enabled": is_enabled,
            "required": p_id_lower in active_providers_required,
            "auth_ready": p_cap.get("auth_ready") if isinstance(p_cap, dict) else None,
            "local_worker_ready": p_cap.get("local_cli_worker_supported") if isinstance(p_cap, dict) else None,
        }
        baseline_capabilities[p_id] = baseline_entry

        # Readiness is only required for active providers in use
        if is_enabled and p_id_lower in active_providers_required:
            if not isinstance(p_cap, dict):
                provider_reasons.append(f"missing_provider_capability:{p_id}")
            else:
                if p_cap.get("auth_ready") is not True:
                    provider_reasons.append(f"provider_auth_not_ready:{p_id}")
                if p_cap.get("local_cli_worker_supported") is not True:
                    provider_reasons.append(f"provider_local_worker_not_ready:{p_id}")

    provider_readiness_ok = len(provider_reasons) == 0
    invariants.append({
        "name": "provider_readiness_baseline",
        "ok": provider_readiness_ok,
        "details": {
            "reasons": provider_reasons,
            "active_providers_required": sorted(list(active_providers_required)),
            "baseline_capabilities": baseline_capabilities,
        },
    })

    # Invariant 9: No orphaned in_progress tasks without owner
    tasks = ai_status.get("tasks", []) if isinstance(ai_status, dict) else []
    orphaned_tasks = []
    if isinstance(tasks, list):
        for task in tasks:
            if isinstance(task, dict) and task.get("status") == "in_progress":
                if not task.get("owner"):
                    orphaned_tasks.append(task.get("id"))
    invariants.append({
        "name": "no_orphaned_in_progress_tasks",
        "ok": len(orphaned_tasks) == 0,
        "details": {"orphaned_tasks": orphaned_tasks},
    })

    return invariants


@dataclass(frozen=True)
class CapturedJsonDocument:
    path: Path
    payload: dict[str, Any]
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class CapturedJsonlDocument:
    path: Path
    rows: tuple[dict[str, Any], ...]
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class RuntimeObservation:
    process: SupervisorProcessIdentity
    observed_at: datetime
    successful_loop_at: datetime | None
    state_sha256: str
    status_sha256: str
    provider_document_sha256: str
    provider_baseline_sha256: str
    task_state_projection_sha256: str | None
    worker_queue_sha256: str
    durable_queue_sha256: str
    config_sha256: str
    invariant_failures: tuple[str, ...]

    @property
    def exact_snapshot_key(self) -> tuple[Any, ...]:
        return (
            self.process.generation,
            self.process.cwd,
            self.process.cwd_commit,
            self.process.cwd_tree,
            self.state_sha256,
            self.status_sha256,
            self.provider_document_sha256,
            self.durable_queue_sha256,
            self.config_sha256,
        )

    @property
    def admission_identity_key(self) -> tuple[Any, ...]:
        """Return the immutable incumbent facts that must survive admission.

        State, status, and provider documents legitimately advance while a
        rollback runtime is materialized.  The admission lock still requires
        the incumbent process, source identity, and live configuration to be
        exactly the prepared ones; current health invariants are re-evaluated
        immediately before TERM.
        """

        return (
            self.process.generation,
            self.process.cwd,
            self.process.cwd_commit,
            self.process.cwd_tree,
            self.config_sha256,
        )


@dataclass(frozen=True)
class PromotionPlan:
    candidate_identity: CandidateRuntimeIdentity
    candidate_config: SupervisorConfigVariant
    candidate_launch: GovernedSupervisorLaunchContract
    incumbent_identity: CandidateRuntimeIdentity
    rollback_config: SupervisorConfigVariant
    incumbent_process: SupervisorProcessIdentity
    rollback_launch: GovernedSupervisorLaunchContract
    baseline: RuntimeObservation
    promotion_lock_path: Path
    runtime_admission_lock_path: Path
    task_state_lock_path: Path



class PromotionState(str, Enum):
    CREATED = "created"
    PREPARED = "prepared"
    ADMISSION_LOCKED = "admission_locked"
    INTENT_RECORDED = "intent_recorded"
    INCUMBENT_TERMINATED = "incumbent_terminated"
    CANDIDATE_CONFIG_INSTALLED = "candidate_config_installed"
    CANDIDATE_LAUNCHED = "candidate_launched"
    CANDIDATE_VERIFYING = "candidate_verifying"
    PROMOTED = "promoted"
    ROLLBACK_LOCKED = "rollback_locked"
    BAD_RUNTIME_TERMINATED = "bad_runtime_terminated"
    ROLLBACK_CONFIG_INSTALLED = "rollback_config_installed"
    ROLLBACK_LAUNCHED = "rollback_launched"
    ROLLBACK_VERIFYING = "rollback_verifying"
    ROLLED_BACK = "rolled_back"
    FORWARD_RECOVERY_REQUIRED = "forward_recovery_required"
    ABORTED = "aborted"
    ROLLBACK_FAILED = "rollback_failed"


class ProcessLaunchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        pid: int | None = None,
        generation: ProcessGeneration | None = None,
        child_absence_proven: bool | None = None,
        cleanup_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.pid = pid
        self.generation = generation
        self.child_absence_proven = (
            pid is None if child_absence_proven is None else child_absence_proven
        )
        self.cleanup_error = cleanup_error


class LoopMarkerRegressionError(RuntimeError):
    """A launched supervisor's successful-loop marker moved backwards."""


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(encoded).hexdigest()


def _capture_json_document(path: Path, *, label: str) -> CapturedJsonDocument:
    _validate_absolute_identity_path(path, label=label)
    parent_components = _capture_directory_component_identities(
        path.parent,
        label=label,
    )
    descriptor = _open_path_descriptor(
        path,
        label=label,
        require_directory=False,
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} is not a regular file: {path}")
        content = _read_descriptor_bytes(
            descriptor,
            limit=128 * 1024 * 1024,
            label=label,
        )
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    stable_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    stable_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if stable_before != stable_after or len(content) != before.st_size:
        raise ValueError(f"{label} changed during capture: {path}")
    file_identity = _identity_from_stat(before)
    _assert_path_component_identities(
        parent_components
        + (PathComponentIdentity(path=path, identity=file_identity),),
        label=label,
    )
    try:
        payload = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not strict UTF-8 JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return CapturedJsonDocument(
        path=path,
        payload=payload,
        byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _runtime_document_paths(
    identity: CandidateRuntimeIdentity,
) -> tuple[Path, Path, Path, Path]:
    config = _strict_live_config(identity)
    paths = config.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("Captured live config paths object is missing")
    status_path = _absolute_config_path(
        paths.get("status_file"),
        label="paths.status_file",
    )
    state_path = _absolute_config_path(
        paths.get("state_file"),
        label="paths.state_file",
    )
    provider_path = _absolute_config_path(
        paths.get("provider_capabilities"),
        label="paths.provider_capabilities",
    )
    if state_path.parent.name != ".orchestrator":
        raise ValueError("Captured live state path is outside .orchestrator")
    status_root = status_path.parent
    if state_path.parent.parent != status_root:
        raise ValueError("Captured live status and state roots differ")
    return status_path, state_path, provider_path, status_root


def _capture_jsonl_document(path: Path, *, label: str) -> CapturedJsonlDocument:
    """Capture one exact bounded JSONL authority without following symlinks."""

    parent_components = _capture_directory_component_identities(
        path.parent,
        label=label,
    )
    descriptor = _open_path_descriptor(
        path,
        label=label,
        require_directory=False,
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} is not a regular file: {path}")
        content = _read_descriptor_bytes(
            descriptor,
            limit=128 * 1024 * 1024,
            label=label,
        )
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    stable_before = (
        before.st_dev, before.st_ino, before.st_mode, before.st_size,
        before.st_mtime_ns, before.st_ctime_ns,
    )
    stable_after = (
        after.st_dev, after.st_ino, after.st_mode, after.st_size,
        after.st_mtime_ns, after.st_ctime_ns,
    )
    if stable_before != stable_after or len(content) != before.st_size:
        raise ValueError(f"{label} changed during capture: {path}")
    file_identity = _identity_from_stat(before)
    _assert_path_component_identities(
        parent_components + (PathComponentIdentity(path=path, identity=file_identity),),
        label=label,
    )
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(content.splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"{label} line {line_number} is not strict JSON"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(f"{label} line {line_number} must be an object")
        rows.append(row)
    return CapturedJsonlDocument(
        path=path,
        rows=tuple(rows),
        byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _durable_queue_path(config: Mapping[str, Any], status_root: Path) -> Path:
    paths = config.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("Captured live config paths object is missing")
    raw = paths.get("event_queue")
    if not isinstance(raw, str) or not raw.strip() or raw != raw.strip():
        raise ValueError("Captured live config paths.event_queue is invalid")
    path = Path(raw)
    if not path.is_absolute():
        path = status_root / path
    _validate_absolute_identity_path(path, label="Captured live config paths.event_queue")
    if path.parent.parent != status_root or path.parent.name != ".orchestrator":
        raise ValueError("Captured live event queue is outside status .orchestrator")
    return path


def capture_runtime_observation(
    identity: CandidateRuntimeIdentity,
    *,
    expected_argv: tuple[str, ...],
    expected_process_contract: ExpectedSupervisorProcessContract | None = None,
    expected_generation: ProcessGeneration | None = None,
    reader: RuntimeProcessReader | None = None,
    now: datetime | None = None,
    require_current_dev_identity: bool = True,
    allow_legacy_journal_migration_source: bool = False,
    cwd_git_identity_reader: Callable[
        [ProcessCwdIdentity], tuple[str, str]
    ] = _read_process_cwd_git_identity,
) -> RuntimeObservation:
    """Capture one exact process/state/config postcheck observation."""
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    runtime_reader = reader or ProcfsRuntimeProcessReader()
    identity.verify_immutable_snapshot(
        require_accepted_dev_identity=require_current_dev_identity
    )
    revalidate_identity = lambda: identity.verify_immutable_snapshot(
        require_accepted_dev_identity=require_current_dev_identity
    )
    process = discover_incumbent_supervisor_process(
        identity,
        expected_argv=expected_argv,
        expected_contract=expected_process_contract,
        reader=runtime_reader,
        cwd_git_identity_reader=cwd_git_identity_reader,
        candidate_revalidator=revalidate_identity,
    )
    if expected_generation is not None and process.generation != expected_generation:
        raise ValueError(
            "Supervisor process generation differs from the exact launched generation"
        )

    status_path, state_path, provider_path, status_root = _runtime_document_paths(
        identity
    )
    state_document = _capture_json_document(
        state_path,
        label="Supervisor runtime state",
    )
    status_document = _capture_json_document(
        status_path,
        label="Canonical task status",
    )
    provider_document = _capture_json_document(
        provider_path,
        label="Provider capabilities",
    )
    config = _strict_live_config(identity)
    durable_queue_document = _capture_jsonl_document(
        _durable_queue_path(config, status_root),
        label="Durable supervisor event queue",
    )
    identity.verify_against_live_config(identity.config_path)

    health_report = evaluate_runtime_health(
        identity.candidate_root,
        config_path_arg=identity.config_path,
        now=observed_at,
        expected_command_root=identity.candidate_root,
        expected_source_commit=identity.head_commit,
        expected_config_sha256=identity.config_sha256,
        expected_process_generation=(
            process.generation.pid,
            process.generation.starttime_ticks,
        ),
        verified_runtime_identity=_runtime_health_identity(process),
    )
    invariants = evaluate_promotion_invariants(
        health_report=health_report,
        ai_status=status_document.payload,
        state=state_document.payload,
        provider_capabilities=provider_document.payload,
        lock_path=status_root / ".orchestrator" / "supervisor.lock",
        file_errors=[],
        now=observed_at,
        config=config,
        durable_queue_events=list(durable_queue_document.rows),
        allow_legacy_journal_migration_source=allow_legacy_journal_migration_source,
    )
    failures = [
        str(invariant.get("name") or "unnamed_invariant")
        for invariant in invariants
        if invariant.get("ok") is not True
    ]
    supervisor_state = state_document.payload.get("supervisor")
    if not isinstance(supervisor_state, dict):
        supervisor_state = {}
        failures.append("supervisor_state_missing")
    if supervisor_state.get("pid") != process.generation.pid:
        failures.append("state_pid_not_exact_process")
    successful_loop_at = parse_utc_timestamp(
        supervisor_state.get("last_successful_loop_at")
    )
    projection = supervisor_state.get("task_state_projection")
    task_state_projection_sha = (
        str(projection.get("projected_state_sha256"))
        if isinstance(projection, dict) and projection.get("projected_state_sha256")
        else None
    )
    parity_payload = {
        "workers": state_document.payload.get("workers", {}),
        "queue": state_document.payload.get("queue", {}),
        "worker_worktree_leases": (
            state_document.payload.get("worker_worktrees", {}).get("leases", {})
            if isinstance(state_document.payload.get("worker_worktrees"), dict)
            else {}
        ),
    }
    provider_payload = provider_document.payload.get("providers", {})
    return RuntimeObservation(
        process=process,
        observed_at=observed_at,
        successful_loop_at=successful_loop_at,
        state_sha256=state_document.sha256,
        status_sha256=status_document.sha256,
        provider_document_sha256=provider_document.sha256,
        provider_baseline_sha256=_canonical_json_sha256(provider_payload),
        task_state_projection_sha256=task_state_projection_sha,
        worker_queue_sha256=_canonical_json_sha256(parity_payload),
        durable_queue_sha256=durable_queue_document.sha256,
        config_sha256=identity.config_sha256,
        invariant_failures=tuple(sorted(set(failures))),
    )


def _discover_supervisor_seed(
    reader: RuntimeProcessReader,
) -> tuple[ProcessGeneration, tuple[str, ...], ProcessCwdIdentity]:
    candidates: list[tuple[ProcessGeneration, tuple[str, ...]]] = []
    errors: list[str] = []
    for pid in reader.list_pids():
        try:
            generation = reader.read_generation(pid)
            argv = _guarded_process_read(
                reader,
                generation,
                label="incumbent seed argv",
                operation=lambda pid=pid: reader.read_argv(pid),
                allow_zombie=True,
            )
        except ProcessLookupError:
            continue
        except Exception as exc:
            errors.append(f"pid={pid}:{type(exc).__name__}")
            continue
        if _looks_like_supervisor_candidate(argv):
            if generation.state == "Z":
                raise ValueError(f"Supervisor candidate PID {pid} is a zombie")
            candidates.append((generation, argv))
    if errors:
        raise ValueError(
            "Supervisor seed enumeration was incomplete: " + ",".join(errors)
        )
    if len(candidates) != 1:
        raise ValueError(
            f"Expected exactly one live supervisor seed; found {len(candidates)}"
        )
    generation, argv = candidates[0]
    cwd = _guarded_process_read(
        reader,
        generation,
        label="incumbent seed cwd",
        operation=lambda: reader.read_cwd(generation.pid),
    )
    return generation, argv, cwd


class RuntimeAdmissionLock:
    """Exclusive runtime-admission lock shared with supervisor/watchdog I/O.

    The supervisor runtime modules use ``common.stable_sidecar_lock`` for this
    plane.  Promotion must enter through the same process-local registry so a
    watchdog intentional-restart write is genuinely re-entrant instead of
    opening a second flock descriptor and deadlocking against itself.
    """

    def __init__(
        self,
        path: Path,
        *,
        timeout: float = 30.0,
        plane: str = "runtime_admission",
    ) -> None:
        if timeout <= 0:
            raise ValueError("Stable-plane lock timeout must be positive")
        if plane not in {"runtime_admission", "task_state"}:
            raise ValueError(f"Unsupported stable lock plane: {plane}")
        self.path = path
        self.timeout = timeout
        self.plane = plane
        self._manager: Any | None = None
        self._owner_pid: int | None = None
        self._depth = 0

    @property
    def depth(self) -> int:
        return self._depth

    def acquire(self) -> None:
        owner_pid = os.getpid()
        if self._manager is not None:
            if self._owner_pid != owner_pid:
                raise RuntimeError(f"{self.plane} lock owner changed after fork")
            self._depth += 1
            return

        orchestrator_root = Path(__file__).resolve().parent.parent / ".orchestrator"
        if str(orchestrator_root) not in sys.path:
            sys.path.insert(0, str(orchestrator_root))
        from common import stable_sidecar_lock

        deadline = time.monotonic() + self.timeout
        while True:
            manager = stable_sidecar_lock(
                self.path,
                plane=self.plane,
                shared=False,
                nonblocking=True,
            )
            try:
                manager.__enter__()
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out acquiring {self.plane} lock: {self.path}"
                    ) from exc
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                continue
            self._manager = manager
            self._owner_pid = owner_pid
            self._depth = 1
            return

    def release(self) -> None:
        if self._manager is None or self._owner_pid != os.getpid():
            raise RuntimeError(f"{self.plane} lock is not owned by this process")
        self._depth -= 1
        if self._depth:
            return
        manager = self._manager
        self._manager = None
        self._owner_pid = None
        manager.__exit__(None, None, None)

    @contextmanager
    def held(self) -> Iterator["RuntimeAdmissionLock"]:
        self.acquire()
        try:
            yield self
        finally:
            self.release()


class PromotionLock:
    """Process-wide promotion serializer, acquired before runtime admission."""

    def __init__(self, path: Path, *, timeout: float = 30.0) -> None:
        if timeout <= 0:
            raise ValueError("Promotion lock timeout must be positive")
        self.path = path
        self.timeout = timeout
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            raise RuntimeError("Promotion lock is already held")
        if self.path.is_symlink():
            raise ValueError(f"Promotion lock cannot be a symlink: {self.path}")
        parent_components = _capture_directory_component_identities(
            self.path.parent,
            label="Promotion lock parent",
        )
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(self.path, flags, 0o600)
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                try:
                    fcntl.flock(
                        descriptor,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out acquiring promotion lock: {self.path}"
                        ) from exc
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            descriptor_stat = os.fstat(descriptor)
            path_stat = self.path.lstat()
            if (
                not stat.S_ISREG(descriptor_stat.st_mode)
                or stat.S_ISLNK(path_stat.st_mode)
                or (descriptor_stat.st_dev, descriptor_stat.st_ino)
                != (path_stat.st_dev, path_stat.st_ino)
            ):
                raise ValueError("Promotion lock identity changed during acquire")
            _assert_path_component_identities(
                parent_components,
                label="Promotion lock parent",
            )
            self._descriptor = descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            raise RuntimeError("Promotion lock is not held")
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    @contextmanager
    def held(self) -> Iterator["PromotionLock"]:
        self.acquire()
        try:
            yield self
        finally:
            self.release()


class PromotionBackend(Protocol):
    def promotion_lock_path(self, candidate_root: Path) -> Path: ...

    def prepare(self, candidate_root: Path) -> PromotionPlan: ...

    def revalidate(self, plan: PromotionPlan) -> RuntimeObservation: ...

    def migrate_task_state_store(self, plan: PromotionPlan) -> dict[str, Any]: ...

    def finalize_candidate_launch(
        self, plan: PromotionPlan
    ) -> GovernedSupervisorLaunchContract: ...

    def task_state_store_sequence(self, plan: PromotionPlan) -> int: ...

    def runtime_execution_authority_digests(
        self,
        identity: CandidateRuntimeIdentity,
    ) -> tuple[str, str]: ...

    def observe(
        self,
        identity: CandidateRuntimeIdentity,
        contract: GovernedSupervisorLaunchContract,
        generation: ProcessGeneration,
        *,
        require_current_dev_identity: bool,
    ) -> RuntimeObservation: ...

    def record_intent(
        self,
        identity: CandidateRuntimeIdentity,
        *,
        old_pid: int,
        target_sha: str,
    ) -> None: ...

    def install_config(
        self,
        identity: CandidateRuntimeIdentity,
        variant: SupervisorConfigVariant,
        *,
        allowed_predecessors: Mapping[str, bytes],
    ) -> CandidateRuntimeIdentity: ...

    def launch(
        self,
        identity: CandidateRuntimeIdentity,
        contract: GovernedSupervisorLaunchContract,
        *,
        require_current_dev_identity: bool,
    ) -> ProcessGeneration: ...

    def terminate(self, generation: ProcessGeneration, *, timeout: float) -> None: ...

    def generation_is_alive(self, generation: ProcessGeneration) -> bool: ...

    def pid_is_absent(self, pid: int) -> bool: ...

    def utcnow(self) -> datetime: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class OSPromotionBackend:
    def __init__(self, *, reader: RuntimeProcessReader | None = None) -> None:
        self.reader = reader or ProcfsRuntimeProcessReader()

    def promotion_lock_path(self, candidate_root: Path) -> Path:
        identity = build_candidate_runtime_identity(candidate_root)
        _status, _state, _provider, status_root = _runtime_document_paths(identity)
        return status_root / ".orchestrator" / "supervisor-runtime-promotion.lock"

    def prepare(self, candidate_root: Path) -> PromotionPlan:
        candidate_identity = build_candidate_runtime_identity(candidate_root)
        seed_generation, seed_argv, seed_cwd = _discover_supervisor_seed(self.reader)
        incumbent_identity = build_candidate_runtime_identity(seed_cwd.path)
        incumbent_process = discover_incumbent_supervisor_process(
            incumbent_identity,
            expected_argv=seed_argv,
            reader=self.reader,
            candidate_revalidator=incumbent_identity.verify_immutable_snapshot,
        )
        if incumbent_process.generation != seed_generation:
            raise ValueError("Incumbent changed during promotion preparation")
        if incumbent_identity.config_bytes != candidate_identity.config_bytes:
            raise ValueError("Candidate and rollback captured different config bytes")
        if incumbent_identity.candidate_root == candidate_identity.candidate_root:
            raise ValueError("Candidate runtime equals the rollback runtime")
        candidate_config = derive_supervisor_config_variant(
            candidate_identity,
            command_root=candidate_identity.candidate_root,
            repo_config_root=candidate_identity.candidate_root,
        )
        rollback_config = derive_supervisor_config_variant(
            candidate_identity,
            command_root=incumbent_identity.candidate_root,
        )
        candidate_config_payload = json.loads(candidate_config.content)
        rollback_config_payload = json.loads(rollback_config.content)
        candidate_store = candidate_config_payload.get("task_state_store")
        if not isinstance(candidate_store, dict):
            raise ValueError("Rendered candidate task_state_store is missing")
        candidate_event_log = Path(str(candidate_store.get("event_log") or ""))
        defer_candidate_journal = not candidate_event_log.exists()
        candidate_launch = build_governed_supervisor_launch_contract(
            candidate_identity,
            supervisor_argv=candidate_config.supervisor_argv,
            config_override=candidate_config_payload,
            defer_task_state_event_log_identity=defer_candidate_journal,
        )
        rollback_launch = build_governed_supervisor_launch_contract(
            incumbent_identity,
            supervisor_argv=rollback_config.supervisor_argv,
            config_override=rollback_config_payload,
        )
        baseline = capture_runtime_observation(
            incumbent_identity,
            expected_argv=seed_argv,
            expected_generation=incumbent_process.generation,
            reader=self.reader,
            allow_legacy_journal_migration_source=True,
        )
        if baseline.invariant_failures:
            raise ValueError(
                "Incumbent baseline invariants failed: "
                + ",".join(baseline.invariant_failures)
            )
        _, _, _, candidate_status_root = _runtime_document_paths(candidate_identity)
        _, _, _, incumbent_status_root = _runtime_document_paths(incumbent_identity)
        if candidate_status_root != incumbent_status_root:
            raise ValueError("Candidate and incumbent status roots differ")
        return PromotionPlan(
            candidate_identity=candidate_identity,
            candidate_config=candidate_config,
            candidate_launch=candidate_launch,
            incumbent_identity=incumbent_identity,
            rollback_config=rollback_config,
            incumbent_process=incumbent_process,
            rollback_launch=rollback_launch,
            baseline=baseline,
            promotion_lock_path=(
                candidate_status_root
                / ".orchestrator"
                / "supervisor-runtime-promotion.lock"
            ),
            runtime_admission_lock_path=(
                candidate_status_root / ".orchestrator" / "runtime-admission.lock"
            ),
            task_state_lock_path=(
                candidate_status_root / ".orchestrator" / "task-state.lock"
            ),
        )

    def revalidate(self, plan: PromotionPlan) -> RuntimeObservation:
        plan.candidate_identity.verify_immutable_snapshot()
        plan.incumbent_identity.verify_immutable_snapshot()

        if (
            build_governed_supervisor_launch_contract(
                plan.candidate_identity,
                supervisor_argv=plan.candidate_config.supervisor_argv,
                config_override=json.loads(plan.candidate_config.content),
                defer_task_state_event_log_identity=(
                    plan.candidate_launch.task_state_event_log_identity is None
                ),
                baseline_task_state_event_log_identity=(
                    plan.candidate_launch.task_state_event_log_identity
                ),
            )
            != plan.candidate_launch
        ):
            raise ValueError("Candidate governed launch contract drift detected")
        if (
            build_governed_supervisor_launch_contract(
                plan.incumbent_identity,
                supervisor_argv=plan.rollback_config.supervisor_argv,
                config_override=json.loads(plan.rollback_config.content),
                baseline_task_state_event_log_identity=plan.rollback_launch.task_state_event_log_identity,
            )
            != plan.rollback_launch
        ):
            raise ValueError("Rollback governed launch contract drift detected")

        return capture_runtime_observation(
            plan.incumbent_identity,
            expected_argv=plan.incumbent_process.argv,
            expected_generation=plan.incumbent_process.generation,
            reader=self.reader,
            allow_legacy_journal_migration_source=True,
        )

    def migrate_task_state_store(self, plan: PromotionPlan) -> dict[str, Any]:
        """Audit V1 and create the exact candidate V2 genesis before TERM."""

        incumbent_config = _strict_live_config(plan.incumbent_identity)
        candidate_config = json.loads(plan.candidate_config.content)
        incumbent_store = incumbent_config.get("task_state_store")
        candidate_store = candidate_config.get("task_state_store")
        candidate_paths = candidate_config.get("paths")
        if not all(
            isinstance(item, dict)
            for item in (incumbent_store, candidate_store, candidate_paths)
        ):
            raise ValueError("Promotion task-state migration config is incomplete")
        legacy_event_log = Path(str(incumbent_store.get("event_log") or ""))
        v2_event_log = Path(str(candidate_store.get("event_log") or ""))
        status_file = Path(str(candidate_paths.get("status_file") or ""))
        if not legacy_event_log.is_absolute() or not v2_event_log.is_absolute():
            raise ValueError("Promotion task-state journal paths must be absolute")
        status_document = _capture_json_document(
            status_file,
            label="Canonical task status for V2 migration",
        )
        return migrate_task_state_store_v2(
            legacy_event_log=legacy_event_log,
            event_log=v2_event_log,
            expected_state=status_document.payload,
        )

    def finalize_candidate_launch(
        self, plan: PromotionPlan
    ) -> GovernedSupervisorLaunchContract:
        """Bind the post-migration V2 journal into the exact launch contract."""

        return build_governed_supervisor_launch_contract(
            plan.candidate_identity,
            supervisor_argv=plan.candidate_config.supervisor_argv,
            config_override=json.loads(plan.candidate_config.content),
            baseline_task_state_event_log_identity=(
                plan.candidate_launch.task_state_event_log_identity
            ),
        )

    def task_state_store_sequence(self, plan: PromotionPlan) -> int:
        candidate_config = json.loads(plan.candidate_config.content)
        store = candidate_config.get("task_state_store")
        if not isinstance(store, dict):
            raise ValueError("Candidate task_state_store config is missing")
        event_log = Path(str(store.get("event_log") or ""))
        with snapshot_transaction(event_log) as transaction:
            snapshot = transaction.load_snapshot()
        return int(snapshot.get("event_count", 0) or 0)

    def runtime_execution_authority_digests(
        self,
        identity: CandidateRuntimeIdentity,
    ) -> tuple[str, str]:
        """Capture the worker/queue/lease authority while admission is frozen."""

        _status_path, state_path, _provider_path, status_root = (
            _runtime_document_paths(identity)
        )
        state_document = _capture_json_document(
            state_path,
            label="Supervisor runtime state rollback authority",
        )
        worker_worktrees = state_document.payload.get("worker_worktrees")
        worker_worktrees = (
            worker_worktrees if isinstance(worker_worktrees, dict) else {}
        )
        parity_payload = {
            "workers": state_document.payload.get("workers", {}),
            "queue": state_document.payload.get("queue", {}),
            "worker_worktree_leases": worker_worktrees.get("leases", {}),
        }
        config = _strict_live_config(identity)
        durable_queue = _capture_jsonl_document(
            _durable_queue_path(config, status_root),
            label="Durable supervisor event queue rollback authority",
        )
        return _canonical_json_sha256(parity_payload), durable_queue.sha256

    def observe(
        self,
        identity: CandidateRuntimeIdentity,
        contract: GovernedSupervisorLaunchContract,
        generation: ProcessGeneration,
        *,
        require_current_dev_identity: bool,
    ) -> RuntimeObservation:
        return capture_runtime_observation(
            identity,
            expected_argv=contract.argv,
            expected_generation=generation,
            reader=self.reader,
            require_current_dev_identity=require_current_dev_identity,
            allow_legacy_journal_migration_source=not require_current_dev_identity,
        )

    def record_intent(
        self,
        identity: CandidateRuntimeIdentity,
        *,
        old_pid: int,
        target_sha: str,
    ) -> None:
        orchestrator_root = Path(__file__).resolve().parent.parent / ".orchestrator"
        if str(orchestrator_root) not in sys.path:
            sys.path.insert(0, str(orchestrator_root))
        from supervisor_watchdog import record_intentional_restart

        record_intentional_restart(
            _strict_live_config(identity),
            old_pid=old_pid,
            target_sha=target_sha,
        )

    def install_config(
        self,
        identity: CandidateRuntimeIdentity,
        variant: SupervisorConfigVariant,
        *,
        allowed_predecessors: Mapping[str, bytes],
    ) -> CandidateRuntimeIdentity:
        return atomic_install_live_config(
            identity,
            variant,
            allowed_predecessors=allowed_predecessors,
        )

    def launch(
        self,
        identity: CandidateRuntimeIdentity,
        contract: GovernedSupervisorLaunchContract,
        *,
        require_current_dev_identity: bool,
    ) -> ProcessGeneration:
        identity.verify_immutable_snapshot(
            require_accepted_dev_identity=require_current_dev_identity
        )
        current_contract = build_governed_supervisor_launch_contract(
            identity,
            supervisor_argv=contract.argv,
            baseline_task_state_event_log_identity=contract.task_state_event_log_identity,
        )

        if current_contract != contract:
            raise ValueError("Governed launch contract drift detected before launch")
        environment = build_scrubbed_launch_environment(
            identity,
            status_root=contract.status_root,
        )
        _validate_governed_launch_environment(
            environment,
            expected=dict(contract.required_environment),
        )
        flags = (
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(contract.stdout_log_path, flags, 0o600)
        try:
            descriptor_stat = os.fstat(descriptor)
            if not stat.S_ISREG(descriptor_stat.st_mode):
                raise RuntimeError("Governed supervisor log is not a regular file")
            with os.fdopen(descriptor, "ab", closefd=False) as log_handle:
                process = subprocess.Popen(
                    list(contract.argv),
                    cwd=str(contract.cwd),
                    env=environment,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
        except Exception:
            os.close(descriptor)
            raise
        os.close(descriptor)
        try:
            generation = self.reader.read_generation(process.pid)
        except Exception as exc:
            child_absence_proven, cleanup_error = self._contain_spawned_child(
                process
            )
            raise ProcessLaunchError(
                "Launched supervisor generation could not be captured; "
                + (
                    "spawned child was terminated and reaped"
                    if child_absence_proven
                    else "spawned child containment could not be proven"
                ),
                pid=process.pid,
                child_absence_proven=child_absence_proven,
                cleanup_error=cleanup_error,
            ) from exc
        if generation.state == "Z":
            raise ProcessLaunchError(
                "Launched supervisor immediately became a zombie",
                pid=process.pid,
                generation=generation,
            )
        return generation

    @staticmethod
    def _contain_spawned_child(
        process: subprocess.Popen[Any],
        *,
        timeout: float = 5.0,
    ) -> tuple[bool, str | None]:
        """Boundedly stop and reap the exact child held by ``Popen``.

        This path is used only when procfs generation capture failed after a
        successful spawn.  The parent-owned child handle is the remaining
        exact ownership proof; PID-only signalling is deliberately avoided.
        """
        errors: list[str] = []
        try:
            if process.poll() is not None:
                return True, None
        except Exception as exc:
            errors.append(f"poll:{type(exc).__name__}:{exc}")
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        except Exception as exc:
            errors.append(f"terminate:{type(exc).__name__}:{exc}")
        try:
            process.wait(timeout=timeout)
            return True, "; ".join(errors) or None
        except subprocess.TimeoutExpired:
            errors.append("terminate_wait:TimeoutExpired")
        except Exception as exc:
            errors.append(f"terminate_wait:{type(exc).__name__}:{exc}")
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except Exception as exc:
            errors.append(f"kill:{type(exc).__name__}:{exc}")
        try:
            process.wait(timeout=timeout)
            return True, "; ".join(errors) or None
        except Exception as exc:
            errors.append(f"kill_wait:{type(exc).__name__}:{exc}")
        return False, "; ".join(errors)

    def generation_is_alive(self, generation: ProcessGeneration) -> bool:
        try:
            current = self.reader.read_generation(generation.pid)
        except ProcessLookupError:
            return False
        return current == generation and current.state != "Z"

    def pid_is_absent(self, pid: int) -> bool:
        try:
            self.reader.read_generation(pid)
        except ProcessLookupError:
            return True
        except Exception:
            return False
        return False

    def terminate(self, generation: ProcessGeneration, *, timeout: float) -> None:
        if not self.generation_is_alive(generation):
            return
        os.kill(generation.pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while self.generation_is_alive(generation) and time.monotonic() < deadline:
            time.sleep(0.05)
        if self.generation_is_alive(generation):
            os.kill(generation.pid, signal.SIGKILL)
            kill_deadline = time.monotonic() + min(2.0, timeout)
            while (
                self.generation_is_alive(generation)
                and time.monotonic() < kill_deadline
            ):
                time.sleep(0.05)
        if self.generation_is_alive(generation):
            raise TimeoutError(
                "Exact supervisor generation did not terminate: "
                f"pid={generation.pid} starttime={generation.starttime_ticks}"
            )

    def monotonic(self) -> float:
        return time.monotonic()

    def utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


_ALLOWED_PROMOTION_TRANSITIONS: dict[PromotionState, frozenset[PromotionState]] = {
    PromotionState.CREATED: frozenset({PromotionState.PREPARED, PromotionState.ABORTED}),
    PromotionState.PREPARED: frozenset(
        {PromotionState.ADMISSION_LOCKED, PromotionState.ABORTED}
    ),
    PromotionState.ADMISSION_LOCKED: frozenset(
        {PromotionState.INTENT_RECORDED, PromotionState.ABORTED}
    ),
    PromotionState.INTENT_RECORDED: frozenset(
        {PromotionState.INCUMBENT_TERMINATED, PromotionState.ROLLBACK_LOCKED}
    ),
    PromotionState.INCUMBENT_TERMINATED: frozenset(
        {PromotionState.CANDIDATE_CONFIG_INSTALLED, PromotionState.ROLLBACK_LOCKED}
    ),
    PromotionState.CANDIDATE_CONFIG_INSTALLED: frozenset(
        {PromotionState.CANDIDATE_LAUNCHED, PromotionState.ROLLBACK_LOCKED}
    ),
    PromotionState.CANDIDATE_LAUNCHED: frozenset(
        {
            PromotionState.CANDIDATE_VERIFYING,
            PromotionState.ROLLBACK_LOCKED,
            PromotionState.FORWARD_RECOVERY_REQUIRED,
        }
    ),
    PromotionState.CANDIDATE_VERIFYING: frozenset(
        {
            PromotionState.PROMOTED,
            PromotionState.ROLLBACK_LOCKED,
            PromotionState.FORWARD_RECOVERY_REQUIRED,
        }
    ),
    PromotionState.ROLLBACK_LOCKED: frozenset(
        {PromotionState.BAD_RUNTIME_TERMINATED, PromotionState.ROLLBACK_FAILED}
    ),
    PromotionState.BAD_RUNTIME_TERMINATED: frozenset(
        {PromotionState.ROLLBACK_CONFIG_INSTALLED, PromotionState.ROLLBACK_FAILED}
    ),
    PromotionState.ROLLBACK_CONFIG_INSTALLED: frozenset(
        {PromotionState.ROLLBACK_LAUNCHED, PromotionState.ROLLBACK_FAILED}
    ),
    PromotionState.ROLLBACK_LAUNCHED: frozenset(
        {PromotionState.ROLLBACK_VERIFYING, PromotionState.ROLLBACK_FAILED}
    ),
    PromotionState.ROLLBACK_VERIFYING: frozenset(
        {PromotionState.ROLLED_BACK, PromotionState.ROLLBACK_FAILED}
    ),
    PromotionState.PROMOTED: frozenset(),
    PromotionState.ROLLED_BACK: frozenset(),
    PromotionState.FORWARD_RECOVERY_REQUIRED: frozenset(),
    PromotionState.ABORTED: frozenset(),
    PromotionState.ROLLBACK_FAILED: frozenset(),
}


def _observation_summary(observation: RuntimeObservation) -> dict[str, Any]:
    return {
        "observed_at": observation.observed_at.isoformat().replace("+00:00", "Z"),
        "successful_loop_at": (
            observation.successful_loop_at.isoformat().replace("+00:00", "Z")
            if observation.successful_loop_at is not None
            else None
        ),
        "process": _supervisor_process_identity_summary(observation.process),
        "state_sha256": observation.state_sha256,
        "status_sha256": observation.status_sha256,
        "provider_document_sha256": observation.provider_document_sha256,
        "provider_baseline_sha256": observation.provider_baseline_sha256,
        "task_state_projection_sha256": observation.task_state_projection_sha256,
        "worker_queue_sha256": observation.worker_queue_sha256,
        "durable_queue_sha256": observation.durable_queue_sha256,
        "config_sha256": observation.config_sha256,
        "invariant_failures": list(observation.invariant_failures),
    }


def _durable_write_transaction_evidence(path: Path, payload: dict[str, Any]) -> None:
    if not path.is_absolute():
        raise ValueError("Transaction evidence path must be absolute")
    path.parent.mkdir(parents=True, exist_ok=True)
    _capture_directory_component_identities(
        path.parent,
        label="Transaction evidence directory",
    )
    try:
        leaf_stat = path.lstat()
    except FileNotFoundError:
        leaf_stat = None
    if leaf_stat is not None and (
        stat.S_ISLNK(leaf_stat.st_mode) or not stat.S_ISREG(leaf_stat.st_mode)
    ):
        raise RuntimeError(f"Transaction evidence leaf is unsafe: {path}")
    content = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8", errors="strict")
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temp_path.unlink(missing_ok=True)


class PromotionTransaction:
    def __init__(
        self,
        *,
        evidence_path: Path | None = None,
        backend: PromotionBackend | None = None,
        lock_factory: Callable[[Path, float], RuntimeAdmissionLock] | None = None,
        task_lock_factory: Callable[[Path, float], RuntimeAdmissionLock] | None = None,
        promotion_lock_factory: Callable[[Path, float], PromotionLock] | None = None,
        required_fresh_loops: int = 3,
        postcheck_timeout: float = 180.0,
        poll_interval: float = 0.5,
        lock_timeout: float = 30.0,
        termination_timeout: float = 15.0,
    ) -> None:
        if required_fresh_loops < 3:
            raise ValueError("Promotion and rollback require at least three fresh loops")
        if min(postcheck_timeout, poll_interval, lock_timeout, termination_timeout) <= 0:
            raise ValueError("Transaction timeouts and poll interval must be positive")
        self.requested_evidence_path = evidence_path
        self.evidence_path = _default_promotion_evidence_path()
        self.evidence_path_rejection: str | None = None
        self.backend = backend or OSPromotionBackend()
        self.lock_factory = lock_factory or (
            lambda path, timeout: RuntimeAdmissionLock(path, timeout=timeout)
        )
        self.task_lock_factory = task_lock_factory or (
            lambda path, timeout: RuntimeAdmissionLock(
                path, timeout=timeout, plane="task_state"
            )
        )
        self.promotion_lock_factory = promotion_lock_factory or (
            lambda path, timeout: PromotionLock(path, timeout=timeout)
        )
        self.required_fresh_loops = required_fresh_loops
        self.postcheck_timeout = postcheck_timeout
        self.poll_interval = poll_interval
        self.lock_timeout = lock_timeout
        self.termination_timeout = termination_timeout
        self.state = PromotionState.CREATED
        self.history: list[dict[str, Any]] = []
        self.plan: PromotionPlan | None = None
        self.candidate_active_identity: CandidateRuntimeIdentity | None = None
        self.rollback_active_identity: CandidateRuntimeIdentity | None = None
        self.candidate_generation: ProcessGeneration | None = None
        self.candidate_pid: int | None = None
        self.candidate_child_absence_proven: bool | None = None
        self.candidate_launch_cleanup_error: str | None = None
        self.candidate_launch_boundary_at: datetime | None = None
        self.rollback_generation: ProcessGeneration | None = None
        self.rollback_pid: int | None = None
        self.rollback_child_absence_proven: bool | None = None
        self.rollback_launch_cleanup_error: str | None = None
        self.rollback_launch_boundary_at: datetime | None = None
        self.candidate_observations: list[RuntimeObservation] = []
        self.rollback_observations: list[RuntimeObservation] = []
        self.task_state_migration: dict[str, Any] | None = None
        self.original_failure: str | None = None
        self.rollback_failure: str | None = None

    def _transition(self, state: PromotionState, **details: Any) -> None:
        if state not in _ALLOWED_PROMOTION_TRANSITIONS[self.state]:
            raise RuntimeError(
                f"Invalid promotion transition: {self.state.value}->{state.value}"
            )
        self.state = state
        self.history.append(
            {
                "state": state.value,
                "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "details": details,
            }
        )

    def _force_rollback_failed(self, error: Exception) -> None:
        self.rollback_failure = f"{type(error).__name__}: {error}"
        if self.state != PromotionState.ROLLBACK_FAILED:
            if PromotionState.ROLLBACK_FAILED not in _ALLOWED_PROMOTION_TRANSITIONS[
                self.state
            ]:
                self.history.append(
                    {
                        "state": PromotionState.ROLLBACK_FAILED.value,
                        "at": datetime.now(timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "details": {"forced_from": self.state.value},
                    }
                )
                self.state = PromotionState.ROLLBACK_FAILED
            else:
                self._transition(
                    PromotionState.ROLLBACK_FAILED,
                    error=self.rollback_failure,
                )

    def _capture_launch_boundary(self) -> datetime:
        boundary = self.backend.utcnow()
        if boundary.tzinfo is None or boundary.utcoffset() is None:
            raise ValueError("Backend launch boundary must be timezone-aware")
        return boundary.astimezone(timezone.utc)

    def _wait_for_fresh_loops(
        self,
        identity: CandidateRuntimeIdentity,
        contract: GovernedSupervisorLaunchContract,
        generation: ProcessGeneration,
        *,
        baseline: RuntimeObservation,
        launch_boundary: datetime,
        rollback: bool,
    ) -> list[RuntimeObservation]:
        if launch_boundary.tzinfo is None or launch_boundary.utcoffset() is None:
            raise ValueError("Launch boundary must be timezone-aware")
        launch_boundary = launch_boundary.astimezone(timezone.utc)
        deadline = self.backend.monotonic() + self.postcheck_timeout
        observations: list[RuntimeObservation] = []
        last_marker: datetime | None = None
        last_error: str | None = None
        while self.backend.monotonic() < deadline:
            try:
                observation = self.backend.observe(
                    identity,
                    contract,
                    generation,
                    require_current_dev_identity=not rollback,
                )
                if observation.config_sha256 != identity.config_sha256:
                    raise ValueError("Live config bytes drifted during transaction")
                if observation.invariant_failures:
                    raise ValueError(
                        "Postcheck invariants failed: "
                        + ",".join(observation.invariant_failures)
                    )
                marker = observation.successful_loop_at
                if marker is None:
                    raise ValueError("Postcheck is missing last_successful_loop_at")
                if last_marker is not None and marker < last_marker:
                    raise LoopMarkerRegressionError(
                        "Postcheck successful-loop marker regressed: "
                        f"{marker.isoformat()} < {last_marker.isoformat()}"
                    )
                if marker <= launch_boundary:
                    raise ValueError(
                        "Postcheck successful-loop marker is not strictly after "
                        "the launch boundary"
                    )
                if marker != last_marker:
                    last_marker = marker
                    observations.append(observation)
                    if len(observations) >= self.required_fresh_loops:
                        if rollback:
                            final = observations[-1]
                            mismatches = []
                            if (
                                final.task_state_projection_sha256
                                != baseline.task_state_projection_sha256
                            ):
                                mismatches.append("projection")
                            if final.worker_queue_sha256 != baseline.worker_queue_sha256:
                                mismatches.append("worker_queue_lease_parity")
                            if (
                                final.provider_baseline_sha256
                                != baseline.provider_baseline_sha256
                            ):
                                mismatches.append("provider_baseline")
                            if mismatches:
                                raise ValueError(
                                    "Rollback baseline mismatch: " + ",".join(mismatches)
                                )
                        return observations
            except LoopMarkerRegressionError:
                raise
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            self.backend.sleep(self.poll_interval)
        raise TimeoutError(
            "Timed out waiting for three distinct successful supervisor loops"
            + (f"; last_error={last_error}" if last_error else "")
        )

    def _rollback(self, lock: RuntimeAdmissionLock) -> dict[str, Any]:
        assert self.plan is not None
        plan = self.plan
        try:
            with (
                lock.held(),
                self.task_lock_factory(
                    plan.task_state_lock_path,
                    self.lock_timeout,
                ).held(),
            ):
                if (
                    self.task_state_migration is not None
                    and self.candidate_active_identity is not None
                ):
                    v2_sequence = self.backend.task_state_store_sequence(plan)
                    worker_queue_sha256, durable_queue_sha256 = (
                        self.backend.runtime_execution_authority_digests(
                            self.candidate_active_identity
                        )
                    )
                    runtime_authority_changed = (
                        worker_queue_sha256 != plan.baseline.worker_queue_sha256
                        or durable_queue_sha256 != plan.baseline.durable_queue_sha256
                    )
                    if v2_sequence > 1 or runtime_authority_changed:
                        # This is the authoritative rollback gate.  The fast
                        # precheck in run() may race a canonical writer or a
                        # queue/worker reservation before this exclusive
                        # admission lock is acquired.  V1 cannot be launched
                        # while any candidate-era execution authority remains.
                        self._transition(
                            PromotionState.FORWARD_RECOVERY_REQUIRED,
                            v2_sequence=v2_sequence,
                            worker_queue_sha256=worker_queue_sha256,
                            durable_queue_sha256=durable_queue_sha256,
                            runtime_authority_changed=runtime_authority_changed,
                            candidate_alive=(
                                self.candidate_generation is not None
                                and self.backend.generation_is_alive(
                                    self.candidate_generation
                                )
                            ),
                            error=self.original_failure,
                        )
                        result = self._evidence(
                            "forward_recovery_required",
                            exit_code=4,
                        )
                        _durable_write_transaction_evidence(
                            self.evidence_path,
                            result,
                        )
                        return result
                self._transition(PromotionState.ROLLBACK_LOCKED)
                if self.candidate_generation is None and self.candidate_pid is not None:
                    if not self.candidate_child_absence_proven:
                        self.candidate_child_absence_proven = (
                            self.backend.pid_is_absent(self.candidate_pid)
                        )
                    if not self.candidate_child_absence_proven:
                        raise RuntimeError(
                            "Candidate was spawned with unknown generation and its "
                            "absence cannot be proven; rollback launch is prohibited"
                        )
                active_generation: ProcessGeneration | None = None
                if (
                    self.candidate_generation is not None
                    and self.backend.generation_is_alive(self.candidate_generation)
                ):
                    active_generation = self.candidate_generation
                elif self.backend.generation_is_alive(plan.incumbent_process.generation):
                    active_generation = plan.incumbent_process.generation
                if active_generation is not None:
                    self.backend.record_intent(
                        plan.incumbent_identity,
                        old_pid=active_generation.pid,
                        target_sha=plan.incumbent_identity.head_commit,
                    )
                    self.backend.terminate(
                        active_generation,
                        timeout=self.termination_timeout,
                    )
                    if self.backend.generation_is_alive(active_generation):
                        raise RuntimeError(
                            "Bad runtime generation remained alive after termination"
                        )
                self._transition(
                    PromotionState.BAD_RUNTIME_TERMINATED,
                    pid=active_generation.pid if active_generation else None,
                )
                self.rollback_active_identity = self.backend.install_config(
                    plan.incumbent_identity,
                    plan.rollback_config,
                    allowed_predecessors={
                        plan.candidate_identity.config_sha256: (
                            plan.candidate_identity.config_bytes
                        ),
                        plan.candidate_config.sha256: plan.candidate_config.content,
                        plan.rollback_config.sha256: plan.rollback_config.content,
                    },
                )
                self._transition(
                    PromotionState.ROLLBACK_CONFIG_INSTALLED,
                    config_sha256=self.rollback_active_identity.config_sha256,
                )
                try:
                    self.rollback_generation = self.backend.launch(
                        self.rollback_active_identity,
                        plan.rollback_launch,
                        require_current_dev_identity=False,
                    )
                    self.rollback_pid = self.rollback_generation.pid
                except ProcessLaunchError as exc:
                    self.rollback_pid = exc.pid
                    self.rollback_generation = exc.generation
                    self.rollback_child_absence_proven = exc.child_absence_proven
                    self.rollback_launch_cleanup_error = exc.cleanup_error
                    raise
                if self.rollback_generation.pid == plan.incumbent_process.generation.pid:
                    raise ValueError("Rollback launch reused the incumbent PID")
                self._transition(
                    PromotionState.ROLLBACK_LAUNCHED,
                    pid=self.rollback_generation.pid,
                    starttime_ticks=self.rollback_generation.starttime_ticks,
                )
            if self.rollback_active_identity is None:
                raise RuntimeError("Rollback config identity was not installed")
            self.rollback_launch_boundary_at = self._capture_launch_boundary()
            self.history[-1]["details"]["launch_boundary_at"] = (
                self.rollback_launch_boundary_at.isoformat().replace("+00:00", "Z")
            )
            self._transition(PromotionState.ROLLBACK_VERIFYING)
            self.rollback_observations = self._wait_for_fresh_loops(
                self.rollback_active_identity,
                plan.rollback_launch,
                self.rollback_generation,
                baseline=plan.baseline,
                launch_boundary=self.rollback_launch_boundary_at,
                rollback=True,
            )
            self._transition(
                PromotionState.ROLLED_BACK,
                fresh_loops=len(self.rollback_observations),
            )
            result = self._evidence("rolled_back", exit_code=2)
        except Exception as exc:
            self._force_rollback_failed(exc)
            result = self._evidence("rollback_failed", exit_code=3)
        _durable_write_transaction_evidence(self.evidence_path, result)
        return result

    def _evidence(self, outcome: str, *, exit_code: int) -> dict[str, Any]:
        plan = self.plan
        return {
            "schema_version": 1,
            "kind": "supervisor_runtime_promotion_transaction",
            "outcome": outcome,
            "exit_code": exit_code,
            "state": self.state.value,
            "recorded_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "history": self.history,
            "original_failure": self.original_failure,
            "rollback_failure": self.rollback_failure,
            "task_state_migration": self.task_state_migration,
            "requested_evidence_path": (
                str(self.requested_evidence_path)
                if self.requested_evidence_path is not None
                else None
            ),
            "evidence_path_rejection": self.evidence_path_rejection,
            "candidate_pid": self.candidate_pid,
            "candidate_child_absence_proven": self.candidate_child_absence_proven,
            "candidate_launch_cleanup_error": self.candidate_launch_cleanup_error,
            "candidate_launch_boundary_at": (
                self.candidate_launch_boundary_at.isoformat().replace("+00:00", "Z")
                if self.candidate_launch_boundary_at is not None
                else None
            ),
            "rollback_pid": self.rollback_pid,
            "rollback_child_absence_proven": self.rollback_child_absence_proven,
            "rollback_launch_cleanup_error": self.rollback_launch_cleanup_error,
            "rollback_launch_boundary_at": (
                self.rollback_launch_boundary_at.isoformat().replace("+00:00", "Z")
                if self.rollback_launch_boundary_at is not None
                else None
            ),
            "incumbent": (
                {
                    "pid": plan.incumbent_process.generation.pid,
                    "starttime_ticks": plan.incumbent_process.generation.starttime_ticks,
                    "root": str(plan.incumbent_identity.candidate_root),
                    "commit": plan.incumbent_identity.head_commit,
                    "tree": plan.incumbent_identity.tracked_tree_identity,
                    "config_sha256": plan.incumbent_identity.config_sha256,
                    "rollback_root": str(plan.incumbent_identity.candidate_root),
                }
                if plan is not None
                else None
            ),
            "candidate": (
                {
                    "root": str(plan.candidate_identity.candidate_root),
                    "commit": plan.candidate_identity.head_commit,
                    "tree": plan.candidate_identity.tracked_tree_identity,
                    "config_sha256": plan.candidate_identity.config_sha256,
                }
                if plan is not None
                else None
            ),
            "config_transaction": (
                {
                    "promotion_lock_path": str(plan.promotion_lock_path),
                    "runtime_admission_lock_path": str(
                        plan.runtime_admission_lock_path
                    ),
                    "original_sha256": plan.candidate_identity.config_sha256,
                    "candidate_sha256": plan.candidate_config.sha256,
                    "rollback_sha256": plan.rollback_config.sha256,
                    "candidate_command_argv_sha256": _argv_sha256(
                        plan.candidate_config.supervisor_argv
                    ),
                    "candidate_command": list(
                        plan.candidate_config.supervisor_argv
                    ),
                    "rollback_command_argv_sha256": _argv_sha256(
                        plan.rollback_config.supervisor_argv
                    ),
                    "rollback_command": list(
                        plan.rollback_config.supervisor_argv
                    ),
                    "candidate_installed_sha256": (
                        self.candidate_active_identity.config_sha256
                        if self.candidate_active_identity is not None
                        else None
                    ),
                    "rollback_installed_sha256": (
                        self.rollback_active_identity.config_sha256
                        if self.rollback_active_identity is not None
                        else None
                    ),
                }
                if plan is not None
                else None
            ),
            "baseline": (
                _observation_summary(plan.baseline) if plan is not None else None
            ),
            "candidate_observations": [
                _observation_summary(item) for item in self.candidate_observations
            ],
            "rollback_observations": [
                _observation_summary(item) for item in self.rollback_observations
            ],
            "evidence_path": str(self.evidence_path),
        }

    def run(self, candidate_root: Path) -> dict[str, Any]:
        incumbent_stop_attempted = False
        promotion_lock: PromotionLock | None = None
        promotion_lock_acquired = False
        try:
            preflight_promotion_lock_path = self.backend.promotion_lock_path(
                candidate_root
            )
            promotion_lock = self.promotion_lock_factory(
                preflight_promotion_lock_path,
                self.lock_timeout,
            )
            promotion_lock.acquire()
            promotion_lock_acquired = True
            self.plan = self.backend.prepare(candidate_root)
            plan = self.plan
            if plan.promotion_lock_path != preflight_promotion_lock_path:
                raise ValueError("Promotion lock path changed during preparation")
            selected_evidence_path = (
                self.requested_evidence_path
                if self.requested_evidence_path is not None
                else self.evidence_path
            )
            try:
                _validate_transaction_evidence_path(
                    selected_evidence_path,
                    plan=plan,
                )
            except Exception as exc:
                self.evidence_path_rejection = f"{type(exc).__name__}: {exc}"
                raise
            self.evidence_path = selected_evidence_path
            self._transition(
                PromotionState.PREPARED,
                candidate_commit=plan.candidate_identity.head_commit,
                incumbent_commit=plan.incumbent_identity.head_commit,
            )
            lock = self.lock_factory(
                plan.runtime_admission_lock_path,
                self.lock_timeout,
            )
            with lock.held():
                task_lock = self.task_lock_factory(
                    plan.task_state_lock_path,
                    self.lock_timeout,
                )
                # Exact writer lock order: runtime admission -> task state.
                with task_lock.held():
                    locked_observation = self.backend.revalidate(plan)
                    if locked_observation.invariant_failures:
                        raise ValueError(
                            "Incumbent health invariants failed before TERM: "
                            + ",".join(locked_observation.invariant_failures)
                        )
                    if (
                        locked_observation.admission_identity_key
                        != plan.baseline.admission_identity_key
                    ):
                        raise ValueError(
                            "Incumbent process/config identity changed before TERM"
                        )
                    self._transition(PromotionState.ADMISSION_LOCKED)
                    self.task_state_migration = self.backend.migrate_task_state_store(plan)
                    if self.task_state_migration.get("ok") is not True:
                        raise ValueError("V1 to V2 task-state migration did not pass")
                    final_launch = self.backend.finalize_candidate_launch(plan)
                    if final_launch.task_state_event_log_identity is None:
                        raise ValueError("Final candidate launch lacks V2 journal identity")
                    plan = replace(plan, candidate_launch=final_launch)
                    self.plan = plan
                    self.history[-1]["details"]["task_state_migration"] = copy.deepcopy(
                        self.task_state_migration
                    )
                    self.backend.record_intent(
                        plan.candidate_identity,
                        old_pid=plan.incumbent_process.generation.pid,
                        target_sha=plan.candidate_identity.head_commit,
                    )
                    self._transition(
                        PromotionState.INTENT_RECORDED,
                        old_pid=plan.incumbent_process.generation.pid,
                        target_sha=plan.candidate_identity.head_commit,
                    )
                    incumbent_stop_attempted = True
                    self.backend.terminate(
                        plan.incumbent_process.generation,
                        timeout=self.termination_timeout,
                    )
                    if self.backend.generation_is_alive(
                        plan.incumbent_process.generation
                    ):
                        raise RuntimeError(
                            "Incumbent generation remained alive after termination"
                        )
                    self._transition(PromotionState.INCUMBENT_TERMINATED)
                    self.candidate_active_identity = self.backend.install_config(
                        plan.candidate_identity,
                        plan.candidate_config,
                        allowed_predecessors={
                            plan.candidate_identity.config_sha256: (
                                plan.candidate_identity.config_bytes
                            )
                        },
                    )
                    self._transition(
                        PromotionState.CANDIDATE_CONFIG_INSTALLED,
                        config_sha256=self.candidate_active_identity.config_sha256,
                    )
                    try:
                        self.candidate_generation = self.backend.launch(
                            self.candidate_active_identity,
                            plan.candidate_launch,
                            require_current_dev_identity=True,
                        )
                        self.candidate_pid = self.candidate_generation.pid
                    except ProcessLaunchError as exc:
                        self.candidate_pid = exc.pid
                        self.candidate_generation = exc.generation
                        self.candidate_child_absence_proven = exc.child_absence_proven
                        self.candidate_launch_cleanup_error = exc.cleanup_error
                        raise
                    self._transition(
                        PromotionState.CANDIDATE_LAUNCHED,
                        pid=self.candidate_generation.pid,
                        starttime_ticks=self.candidate_generation.starttime_ticks,
                    )
            if self.candidate_active_identity is None:
                raise RuntimeError("Candidate config identity was not installed")
            self.candidate_launch_boundary_at = self._capture_launch_boundary()
            self.history[-1]["details"]["launch_boundary_at"] = (
                self.candidate_launch_boundary_at.isoformat().replace("+00:00", "Z")
            )
            self._transition(PromotionState.CANDIDATE_VERIFYING)
            self.candidate_observations = self._wait_for_fresh_loops(
                self.candidate_active_identity,
                plan.candidate_launch,
                self.candidate_generation,
                baseline=plan.baseline,
                launch_boundary=self.candidate_launch_boundary_at,
                rollback=False,
            )
            self._transition(
                PromotionState.PROMOTED,
                fresh_loops=len(self.candidate_observations),
            )
            result = self._evidence("promoted", exit_code=0)
            _durable_write_transaction_evidence(self.evidence_path, result)
            return result
        except Exception as exc:
            self.original_failure = f"{type(exc).__name__}: {exc}"
            if self.plan is not None and incumbent_stop_attempted:
                if self.task_state_migration is not None and self.state in {
                    PromotionState.CANDIDATE_LAUNCHED,
                    PromotionState.CANDIDATE_VERIFYING,
                }:
                    v2_sequence = self.backend.task_state_store_sequence(self.plan)
                    if v2_sequence > 1:
                        # Once the candidate accepts a post-genesis canonical
                        # mutation, restoring the V1 authority would silently
                        # discard it. Preserve V2 and require forward repair.
                        self._transition(
                            PromotionState.FORWARD_RECOVERY_REQUIRED,
                            v2_sequence=v2_sequence,
                            candidate_alive=(
                                self.candidate_generation is not None
                                and self.backend.generation_is_alive(
                                    self.candidate_generation
                                )
                            ),
                            error=self.original_failure,
                        )
                        result = self._evidence(
                            "forward_recovery_required",
                            exit_code=4,
                        )
                        _durable_write_transaction_evidence(self.evidence_path, result)
                        return result
                lock = self.lock_factory(
                    self.plan.runtime_admission_lock_path,
                    self.lock_timeout,
                )
                return self._rollback(lock)
            if self.state != PromotionState.ABORTED:
                self._transition(PromotionState.ABORTED, error=self.original_failure)
            result = self._evidence("aborted", exit_code=1)
            _durable_write_transaction_evidence(self.evidence_path, result)
            return result
        finally:
            if promotion_lock is not None and promotion_lock_acquired:
                promotion_lock.release()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a supervisor promotion preflight or explicitly execute "
            "a rollback-safe runtime transaction."
        )
    )
    parser.add_argument("--repo", default=".", help="Pantheon repository root. Defaults to cwd.")
    parser.add_argument("--config-path", default=None, help="Path to .orchestrator/config.json.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--discover-only",
        action="store_true",
        help=(
            "Run the complete read-only identity, incumbent, launch-contract, "
            "and runtime-health preflight. This command never signals or launches a process."
        ),
    )
    modes.add_argument(
        "--promote",
        action="store_true",
        help=(
            "Explicitly execute the transactional runtime swap. Uses an "
            "external durable evidence path by default and returns nonzero "
            "after any rollback."
        ),
    )
    parser.add_argument(
        "--evidence-path",
        help=(
            "Absolute durable JSON evidence path. Defaults to the external "
            f"runtime evidence directory {DEFAULT_PROMOTION_EVIDENCE_ROOT}."
        ),
    )
    parser.add_argument("--postcheck-timeout", type=float, default=180.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--lock-timeout", type=float, default=30.0)
    parser.add_argument("--termination-timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON snapshot.")
    return parser.parse_args()


def _absolute_path_without_resolving_alias(path: str) -> Path:
    expanded = Path(path).expanduser()
    return expanded if expanded.is_absolute() else Path.cwd() / expanded


def main() -> int:
    args = parse_args()
    repo_root = _absolute_path_without_resolving_alias(args.repo)
    config_path = (
        _absolute_path_without_resolving_alias(args.config_path)
        if args.config_path
        else None
    )

    if args.promote:
        if config_path is not None:
            raise SystemExit(
                "--promote always uses the exact live supervisor config; "
                "--config-path is discover-only"
            )
        evidence_path = (
            _absolute_path_without_resolving_alias(args.evidence_path)
            if args.evidence_path
            else None
        )
        if (
            args.evidence_path
            and not Path(args.evidence_path).expanduser().is_absolute()
        ):
            raise SystemExit("--evidence-path must be absolute")
        result = PromotionTransaction(
            evidence_path=evidence_path,
            postcheck_timeout=args.postcheck_timeout,
            poll_interval=args.poll_interval,
            lock_timeout=args.lock_timeout,
            termination_timeout=args.termination_timeout,
        ).run(repo_root)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(
                "supervisor_promotion_transaction="
                f"{result['outcome']} state={result['state']} "
                f"evidence={result['evidence_path']}"
            )
        return int(result["exit_code"])

    snapshot = capture_promotion_snapshot(repo_root, config_path_arg=config_path)

    if args.json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        status_str = "ELIGIBLE" if snapshot["eligible_for_promotion"] else "INELIGIBLE"
        print(f"supervisor_promotion_snapshot={status_str} timestamp={snapshot['timestamp']}")
        for inv in snapshot["invariants"]:
            print(f"invariant {inv['name']}: {'ok' if inv['ok'] else 'FAIL'}")

    return 0 if snapshot["eligible_for_promotion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
