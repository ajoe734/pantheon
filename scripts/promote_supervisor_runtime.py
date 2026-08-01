#!/usr/bin/env python3
"""Supervisor runtime promotion snapshot & invariant validation module.

Provides read-only snapshot collection and runtime promotion invariant checks.
Does not perform process termination, launch, rollback, or live promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from supervisor_runtime_health import (
    evaluate_runtime_health,
    pid_is_alive,
    resolved_coordinator_status_root,
    parse_utc_timestamp,
    lock_held,
)


HEX_40_PATTERN = re.compile(r"^[0-9a-f]{40}$")
TASK_BRIEF_PATH_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*\.md$")
ALLOWED_COMMAND_RUNTIMES_PREFIX = Path(
    "/home/lupin/pantheon-ci-deploy/command-runtimes"
)
LIVE_SUPERVISOR_CONFIG_PATH = Path(
    "/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json"
)
TRUSTED_GITHUB_OWNER = "ajoe734"
TRUSTED_GITHUB_REPOSITORY = "pantheon"
TRUSTED_ORIGIN_DEV_URL = "https://github.com/ajoe734/pantheon.git"

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
        PurePosixPath(".orchestrator/planning-state.lock"),
        PurePosixPath(".orchestrator/reap-in-progress.lock"),
        PurePosixPath(".orchestrator/runtime-admission.lock"),
        PurePosixPath(".orchestrator/status-derived-views.lock"),
        PurePosixPath(".orchestrator/supervisor.lock"),
        PurePosixPath(".orchestrator/task-state.lock"),
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
class CandidateRootHandle:
    path: Path
    descriptor: int
    identity: FilesystemIdentity


@dataclass(frozen=True)
class TrustedDevIdentity:
    commit: str
    candidate_commit_tree: str


@dataclass(frozen=True)
class CandidateRuntimeIdentity:
    candidate_root: Path
    candidate_root_device: int
    candidate_root_inode: int
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
        content, file_identity = snapshot
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

    def verify_immutable_snapshot(self) -> None:
        """Revalidate every captured root, Git, and config identity value."""
        root_handle = _open_candidate_root_handle(self.candidate_root)
        try:
            if root_handle.path != self.candidate_root:
                raise ValueError("Candidate root path drift detected")
            if (
                root_handle.identity.device != self.candidate_root_device
                or root_handle.identity.inode != self.candidate_root_inode
            ):
                raise ValueError("Candidate root file identity drift detected")

            remote_url = parse_origin_url(root_handle)
            remote = validate_remote_url(remote_url)
            if (
                remote_url != self.remote_url
                or remote.slug != self.repository_slug
                or f"github.com/{remote.slug}" != self.canonical_remote
            ):
                raise ValueError("Candidate remote identity drift detected")

            head, tree, accepted = _capture_git_identity(
                root_handle,
                self.basename,
            )
            if head != self.head_commit:
                raise ValueError(
                    f"Candidate HEAD drift: {head} != {self.head_commit}"
                )
            if tree != self.tracked_tree_identity:
                raise ValueError(
                    "Candidate tracked tree drift: "
                    f"{tree} != {self.tracked_tree_identity}"
                )
            if accepted.commit != self.accepted_dev_commit:
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
            os.close(root_handle.descriptor)
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
) -> subprocess.CompletedProcess[str]:
    if isinstance(cwd, CandidateRootHandle):
        cwd_arg = Path(f"/dev/fd/{cwd.descriptor}")
        pass_fds = (cwd.descriptor,)
        display_cwd = cwd.path
    else:
        cwd_arg = cwd
        pass_fds = ()
        display_cwd = cwd
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd_arg,
            capture_output=True,
            check=True,
            env=_subprocess_environment(),
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


def _git_output(cwd: Path | CandidateRootHandle, *args: str) -> str:
    return _run_git(cwd, *args).stdout.strip()


def _validate_absolute_identity_path(path: Path, *, label: str) -> None:
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path: {path}")
    if ".." in path.parts:
        raise ValueError(f"{label} cannot contain '..': {path}")


def _open_directory_descriptor(path: Path, *, label: str) -> int:
    """Open an absolute directory one no-follow component at a time."""
    _validate_absolute_identity_path(path, label=label)
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path.anchor, flags)
    traversed = Path(path.anchor)
    try:
        for component in path.parts[1:]:
            traversed = traversed / component
            try:
                next_descriptor = os.open(
                    component,
                    flags,
                    dir_fd=descriptor,
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
            descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
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


def _open_candidate_root_handle(candidate_path: Path) -> CandidateRootHandle:
    path = candidate_path if isinstance(candidate_path, Path) else Path(candidate_path)
    trusted_parent = ALLOWED_COMMAND_RUNTIMES_PREFIX

    if path.parent != trusted_parent:
        raise ValueError(
            f"Candidate root {path} is not a direct child of {trusted_parent}"
        )
    if not HEX_40_PATTERN.fullmatch(path.name):
        raise ValueError(
            "Candidate root basename is not a lowercase 40-hex commit: "
            f"{path.name}"
        )
    descriptor = _open_path_descriptor(
        path,
        label="Candidate root",
        require_directory=True,
    )
    root_stat = os.fstat(descriptor)
    root_identity = FilesystemIdentity(
        device=root_stat.st_dev,
        inode=root_stat.st_ino,
        mode=root_stat.st_mode,
    )
    handle = CandidateRootHandle(
        path=path,
        descriptor=descriptor,
        identity=root_identity,
    )
    try:
        _assert_candidate_handle_path(handle)
    except BaseException:
        os.close(descriptor)
        raise
    return handle


def _assert_candidate_handle_path(handle: CandidateRootHandle) -> None:
    _assert_path_identity(
        handle.path,
        handle.identity,
        label="Candidate root",
        require_directory=True,
    )


def _capture_candidate_root(candidate_path: Path) -> tuple[Path, FilesystemIdentity]:
    handle = _open_candidate_root_handle(candidate_path)
    try:
        return handle.path, handle.identity
    finally:
        os.close(handle.descriptor)


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
        raw = _run_git(
            handle,
            "config",
            "--local",
            "--get-all",
            "remote.origin.url",
        ).stdout
        urls = raw.splitlines()
        if len(urls) != 1 or not urls[0]:
            raise ValueError("Candidate must configure exactly one remote.origin.url")
        return urls[0]
    finally:
        if close_handle:
            os.close(handle.descriptor)


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
        return head, tree
    finally:
        if close_handle:
            os.close(handle.descriptor)


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
            os.close(handle.descriptor)


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
    parts = candidate.parts
    return (
        len(parts) == 3
        and parts[:2] == (".orchestrator", "task-briefs")
        and TASK_BRIEF_PATH_PATTERN.fullmatch(parts[2]) is not None
    )


def verify_working_tree_cleanliness(
    candidate_root: Path | CandidateRootHandle,
    *,
    expected_head: str | None = None,
    expected_tree: str | None = None,
) -> str:
    """Reject all tracked dirt and non-enumerated generated untracked files."""
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

        status_output = _run_git(
            handle,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
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
            if not _is_allowed_generated_untracked_path(relative_path):
                kind = "ignored" if status_code == "!!" else "untracked"
                raise ValueError(
                    f"Forbidden {kind} file found in candidate root: {relative_path}"
                )

        try:
            _run_git(handle, "diff-index", "--cached", "--quiet", "HEAD", "--")
        except ValueError as exc:
            raise ValueError("Candidate index differs from HEAD") from exc
        try:
            _run_git(handle, "diff-files", "--quiet", "--")
        except ValueError as exc:
            raise ValueError("Candidate tracked worktree differs from index") from exc

        head, tree = _read_head_tree(handle)
        if expected_head is not None and head != expected_head:
            raise ValueError(f"Candidate HEAD drift: {head} != {expected_head}")
        if expected_tree is not None and tree != expected_tree:
            raise ValueError(f"Candidate tree drift: {tree} != {expected_tree}")
        _assert_candidate_handle_path(handle)
        return tree
    finally:
        if close_handle:
            os.close(handle.descriptor)


def _capture_config_bytes(
    config_path: Path,
    *,
    expected_path: Path,
) -> tuple[bytes, FilesystemIdentity]:
    path = config_path if isinstance(config_path, Path) else Path(config_path)
    if path != expected_path:
        raise ValueError(
            f"Config path {path} does not match exact live config path {expected_path}"
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
    file_identity = FilesystemIdentity(
        device=before.st_dev,
        inode=before.st_ino,
        mode=before.st_mode,
    )
    _assert_path_identity(
        path,
        file_identity,
        label="Live config",
        require_directory=False,
    )
    return content, file_identity


def build_candidate_runtime_identity(
    candidate_path: Path,
    config_path: Path | None = None,
) -> CandidateRuntimeIdentity:
    """Capture one immutable candidate root, Git tree, and live-config snapshot."""
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
        verify_working_tree_cleanliness(
            root_handle,
            expected_head=head,
            expected_tree=tracked_tree,
        )

        selected_config_path = config_path or LIVE_SUPERVISOR_CONFIG_PATH
        config_bytes, config_identity = _capture_config_bytes(
            selected_config_path,
            expected_path=LIVE_SUPERVISOR_CONFIG_PATH,
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
        final_remote_url = parse_origin_url(root_handle)
        final_remote = validate_remote_url(final_remote_url)
        if final_remote_url != remote_url or final_remote.slug != remote.slug:
            raise ValueError("Candidate remote identity changed during capture")

        return CandidateRuntimeIdentity(
            candidate_root=resolved_root,
            candidate_root_device=root_identity.device,
            candidate_root_inode=root_identity.inode,
            basename=basename,
            head_commit=head,
            tracked_tree_identity=tracked_tree,
            accepted_dev_commit=trusted_dev.commit,
            remote_url=remote_url,
            canonical_remote=f"github.com/{remote.slug}",
            repository_slug=remote.slug,
            config_path=selected_config_path,
            config_device=config_identity.device,
            config_inode=config_identity.inode,
            config_bytes=config_bytes,
            config_byte_length=len(config_bytes),
            config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        )
    finally:
        os.close(root_handle.descriptor)


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
        "config_byte_length": identity.config_byte_length,
        "config_sha256": identity.config_sha256,
    }


def capture_promotion_snapshot(
    repo_root: Path,
    *,
    config_path_arg: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Capture live-schema supervisor runtime state and evaluate promotion invariants."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config_path_resolved = config_path_arg or (repo_root / ".orchestrator" / "config.json")

    candidate_identity: CandidateRuntimeIdentity | None = None
    identity_error: str | None = None
    try:
        candidate_identity = build_candidate_runtime_identity(repo_root)
        candidate_identity.verify_immutable_snapshot()
    except Exception as exc:
        identity_error = str(exc)

    file_errors: list[dict[str, str]] = []

    try:
        config = load_json_strict(config_path_resolved)
    except Exception as e:
        config = {}
        file_errors.append({"file": str(config_path_resolved), "error": str(e)})

    health_report = evaluate_runtime_health(
        repo_root,
        config_path_arg=config_path_resolved,
        now=now,
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

    coord_root = resolved_coordinator_status_root(repo_root, config)
    lock_path = coord_root / ".orchestrator" / "supervisor.lock"

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

    all_invariants_pass = all(inv["ok"] for inv in invariants)

    return {
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        "repo_root": str(repo_root),
        "config_path": str(config_path_resolved),
        "candidate_runtime_identity": (
            _candidate_identity_summary(candidate_identity)
            if candidate_identity is not None
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


def evaluate_promotion_invariants(
    health_report: dict[str, Any],
    ai_status: dict[str, Any],
    state: dict[str, Any],
    provider_capabilities: dict[str, Any] | None = None,
    lock_path: Path | None = None,
    file_errors: list[dict[str, str]] | None = None,
    now: datetime | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate read-only promotion invariants against live schema state."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config = config or {}
    file_errors = file_errors or []
    provider_capabilities = provider_capabilities or {}
    invariants: list[dict[str, Any]] = []

    # Invariant 0: Fail closed file reading invariant
    invariants.append({
        "name": "config_and_state_files_readable",
        "ok": len(file_errors) == 0,
        "details": {"file_errors": file_errors},
    })

    # Invariant 1: Health checks must all pass
    health_ok = health_report.get("healthy", False)
    invariants.append({
        "name": "runtime_health_clean",
        "ok": health_ok,
        "details": {"healthy": health_ok, "failed_checks": [c["name"] for c in health_report.get("checks", []) if not c.get("ok")]},
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

    # Invariant 5: Task state shadow authoritative / ok / caught_up validation
    # Live schema requires task_state_shadow to exist and have:
    # mode == "authoritative", ok is True, caught_up is True, last_error is None,
    # and projected_state_sha256 == expected_state_sha256 (if hash keys present/populated)
    shadow = supervisor_info.get("task_state_shadow")
    if not isinstance(shadow, dict):
        shadow = state.get("supervisor", {}).get("task_state_shadow") if isinstance(state.get("supervisor"), dict) else None

    shadow_ok = False
    shadow_reasons: list[str] = []
    if not isinstance(shadow, dict) or not shadow:
        shadow_reasons.append("task_state_shadow_missing")
    else:
        if shadow.get("mode") != "authoritative":
            shadow_reasons.append(f"mode_not_authoritative:{shadow.get('mode')}")
        if shadow.get("ok") is not True:
            shadow_reasons.append(f"ok_not_true:{shadow.get('ok')}")
        if shadow.get("caught_up") is not True:
            shadow_reasons.append(f"caught_up_not_true:{shadow.get('caught_up')}")
        if shadow.get("last_error") is not None:
            shadow_reasons.append(f"has_last_error:{shadow.get('last_error')}")
        proj_sha = shadow.get("projected_state_sha256")
        exp_sha = shadow.get("expected_state_sha256")
        if not proj_sha or not isinstance(proj_sha, str) or not proj_sha.strip():
            shadow_reasons.append("missing_projected_state_sha256")
        if not exp_sha or not isinstance(exp_sha, str) or not exp_sha.strip():
            shadow_reasons.append("missing_expected_state_sha256")
        if proj_sha and exp_sha and proj_sha != exp_sha:
            shadow_reasons.append(f"sha_mismatch:{proj_sha}!={exp_sha}")

    shadow_ok = len(shadow_reasons) == 0
    invariants.append({
        "name": "task_state_shadow_valid",
        "ok": shadow_ok,
        "details": {"task_state_shadow": shadow, "reasons": shadow_reasons},
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
            if w_status in ("running", "started", "active") and task_id:
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

        # Active started event requires nonempty lease_owner
        if not q_lease_owner or not isinstance(q_lease_owner, str) or not q_lease_owner.strip():
            parity_reasons.append(f"missing_lease_owner:{evt_id}")
            continue

        q_lease_owner = q_lease_owner.strip()

        # Find active workers reverse-linked to this event (matching canonical run_id == lease_owner)
        matched_workers: list[tuple[str, dict[str, Any]]] = []
        for w_name, w_info in workers.items():
            if isinstance(w_info, dict) and w_info.get("status") in ("running", "started", "active"):
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

    # Invariant 8: Provider readiness baseline comparing provider_capabilities against configured active providers
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture supervisor promotion snapshot & check invariants.")
    parser.add_argument("--repo", default=".", help="Pantheon repository root. Defaults to cwd.")
    parser.add_argument("--config-path", default=None, help="Path to .orchestrator/config.json.")
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
