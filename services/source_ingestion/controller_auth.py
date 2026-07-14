"""Private service-token handling for destructive controller reconciliation."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


class ControllerAuthError(RuntimeError):
    """Raised when the controller service credential is unavailable or unsafe."""


def _validate_token(value: str) -> str:
    token = value.strip()
    if len(token) < 32 or any(character.isspace() for character in token):
        raise ControllerAuthError("source-ingest controller token must be at least 32 non-whitespace characters")
    return token


def load_controller_token(*, token_path: str | Path, create: bool) -> str:
    """Load an explicit token or a mode-0600 token shared through the data volume."""

    explicit = str(os.getenv("SOURCE_INGEST_CONTROLLER_TOKEN") or "").strip()
    if explicit:
        return _validate_token(explicit)

    path = Path(os.getenv("SOURCE_INGEST_CONTROLLER_TOKEN_FILE") or token_path)
    if path.exists():
        try:
            token = _validate_token(path.read_text(encoding="utf-8"))
            path.chmod(0o600)
            return token
        except (OSError, UnicodeError) as exc:
            raise ControllerAuthError(f"source-ingest controller token file is unreadable: {path}") from exc
    if not create:
        raise ControllerAuthError(f"source-ingest controller token file does not exist: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(48)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return load_controller_token(token_path=path, create=False)
    except OSError as exc:
        raise ControllerAuthError(f"source-ingest controller token file cannot be created: {path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ControllerAuthError(f"source-ingest controller token cannot be persisted: {path}") from exc
    return token


__all__ = ["ControllerAuthError", "load_controller_token"]
