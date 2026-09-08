"""Request-time read of a rotating, consumer-specific service credential.

Only deployment configuration selects a file. A configured missing/unsafe file
fails closed; it never falls back to a stale environment credential. Owner JWT
verification remains authoritative for identity, expiry and permissions.
"""
from __future__ import annotations

import os
import stat
from typing import Mapping


def configured_dev_paper_grant_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    if not source.get("GOVERNANCE_DEV_PAPER_GRANT_FILE", "").strip():
        return True  # Existing unmounted test/config contracts retain env gating.
    try:
        return configured_service_token("GOVERNANCE_DEV_PAPER_GRANT", source) == "enabled"
    except RuntimeError:
        return False


def configured_service_token(variable: str, env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    path = source.get(variable + "_FILE", "").strip()
    if not path:
        return source.get(variable, "").strip()
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                raise ValueError("Unsafe credential file")
            value = handle.read(16385).strip()
            if not value or len(value) > 16384 or any(char.isspace() for char in value):
                raise ValueError("Invalid credential file")
            return value
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError("Configured service credential unavailable") from exc
