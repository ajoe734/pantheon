"""Secure file input helpers for the execution grant issuer and CLI.

OPS-EXECUTION-MFA-ISSUER-001.
Rejects symlinked, non-owned, or group/other-readable credential and
signing-key input files before any byte is read from them.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


class UnsafeCredentialFileError(RuntimeError):
    """Raised when a token or private-key input file fails safety checks."""


def read_private_file_strict(path: str | Path, *, description: str) -> bytes:
    """Read a sensitive file only if it passes strict input safety checks.

    The unresolved path is stat'd first (``os.lstat``, never following a
    symlink) so a symlinked, group/other-readable, or foreign-owned
    credential file is rejected before any content is read. ``O_NOFOLLOW``
    is used as an independent second guard at open time.
    """
    raw_path = Path(path)
    try:
        st = os.lstat(raw_path)
    except OSError as exc:
        raise UnsafeCredentialFileError(
            f"{description} not found or inaccessible: {raw_path}"
        ) from exc

    if stat.S_ISLNK(st.st_mode):
        raise UnsafeCredentialFileError(f"{description} must not be a symlink: {raw_path}")
    if not stat.S_ISREG(st.st_mode):
        raise UnsafeCredentialFileError(f"{description} must be a regular file: {raw_path}")
    if st.st_uid != os.geteuid():
        raise UnsafeCredentialFileError(
            f"{description} must be owned by the current user: {raw_path}"
        )
    if st.st_mode & 0o077:
        raise UnsafeCredentialFileError(
            f"{description} must not be readable or writable by group/other "
            f"(require mode 0600): {raw_path}"
        )

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(raw_path), flags)
    except OSError as exc:
        raise UnsafeCredentialFileError(f"Failed to open {description}: {raw_path}") from exc
    try:
        with open(fd, "rb") as f:
            return f.read()
    except Exception as exc:
        raise UnsafeCredentialFileError(f"Failed to read {description}: {raw_path}") from exc


def write_private_exclusive_file(
    path: str | Path,
    data: bytes | str,
    *,
    description: str = "Private output file",
) -> Path:
    """Atomically create and write to a private 0600 file without following symlinks or overwriting.

    Fails closed if the destination already exists, is a symlink, or cannot be created
    with exclusive 0600 permissions.
    """
    out_path = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(str(out_path), flags, 0o600)
    except FileExistsError as exc:
        raise UnsafeCredentialFileError(
            f"{description} destination already exists or is a symlink: {out_path}"
        ) from exc
    except OSError as exc:
        raise UnsafeCredentialFileError(
            f"Failed to create exclusive {description} at {out_path}: {exc}"
        ) from exc

    try:
        mode = "wb" if isinstance(data, bytes) else "w"
        encoding = None if isinstance(data, bytes) else "utf-8"
        with open(fd, mode, encoding=encoding) as f:
            f.write(data)
    except Exception:
        raise
    return out_path

