"""Production posture checks shared by source-ingest and search services."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


ENFORCED_MODES = {"stage", "staging", "staging-live", "prod", "production"}


def _is_enforced_mode(mode: str) -> bool:
    return mode in ENFORCED_MODES or mode.startswith("staging") or mode.startswith("prod")


@dataclass(frozen=True)
class PostureCheck:
    mode: str
    service: str
    enforced: bool
    errors: tuple[str, ...]
    backends: Mapping[str, str]
    object_store_configured: bool

    @property
    def status(self) -> str:
        return "error" if self.errors else "ok"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "mode": self.mode,
            "service": self.service,
            "enforced": self.enforced,
            "errors": list(self.errors),
            "backends": dict(self.backends),
            "object_store_configured": self.object_store_configured,
        }

    def alert_count(self) -> int:
        return len(self.errors)


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _clean(value: object) -> str:
    return str(value or "").strip()


def _is_true(value: object) -> bool:
    return _clean(value).lower() in {"1", "true", "yes", "on"}


def validate_source_search_posture(
    service: str,
    *,
    env: Mapping[str, str] | None = None,
) -> PostureCheck:
    env_map = _env(env)
    mode = _clean(env_map.get("PANTHEON_SOURCE_SEARCH_POSTURE")).lower() or "dev"
    enforced = _is_enforced_mode(mode)
    errors: list[str] = []

    backend_keys = {
        "source-ingest": ("SOURCE_INGEST_EVIDENCE_BACKEND",),
        "search": ("SEARCH_INDEX_STORE_BACKEND", "SEARCH_EVIDENCE_BACKEND"),
    }.get(service)
    if backend_keys is None:
        raise ValueError(f"unknown source/search posture service: {service}")

    backends = {key: _clean(env_map.get(key)).lower() or "jsonl" for key in backend_keys}
    object_store_keys = (
        "PANTHEON_S3_ENDPOINT",
        "PANTHEON_ARTIFACT_BUCKET",
        "PANTHEON_S3_ACCESS_KEY",
        "PANTHEON_S3_SECRET_KEY",
    )
    object_store_configured = all(_clean(env_map.get(key)) for key in object_store_keys)

    if enforced:
        database_url = _clean(env_map.get("DATABASE_URL"))
        if not database_url.startswith(("postgresql://", "postgres://")):
            errors.append("DATABASE_URL must be a Postgres DSN in staging/prod source-search posture")
        for key, backend in backends.items():
            if backend != "postgres":
                errors.append(f"{key} must be postgres in staging/prod source-search posture")
        if service == "search" and not _is_true(env_map.get("SEARCH_DURABLE_INDEX_ONLY")):
            errors.append("SEARCH_DURABLE_INDEX_ONLY must be true in staging/prod source-search posture")
        for key in object_store_keys:
            if not _clean(env_map.get(key)):
                errors.append(f"{key} is required for staging/prod source-search object-store posture")

    return PostureCheck(
        mode=mode,
        service=service,
        enforced=enforced,
        errors=tuple(errors),
        backends=backends,
        object_store_configured=object_store_configured,
    )


def require_source_search_posture(service: str) -> PostureCheck:
    check = validate_source_search_posture(service)
    if check.errors:
        raise RuntimeError("; ".join(check.errors))
    return check
