"""Shared helpers for Pantheon runtime-to-runtime bearer auth."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_TOKEN_ENV = "PANTHEON_RUNTIME_MANAGER_TOKEN"
_TOKEN_FILE_ENV = "PANTHEON_RUNTIME_MANAGER_TOKEN_FILE"
_DEFAULT_TOKEN = "runtime-control-internal"


@dataclass(frozen=True)
class RuntimeManagerAuthConfig:
    """Resolved bearer token plus non-secret status metadata."""

    token: str
    source: str
    env_key: str
    token_file_env_key: str
    used_default: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def status_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "source": self.source,
            "token_length": len(self.token),
            "used_default": self.used_default,
            "token_env": self.env_key,
            "token_file_env": self.token_file_env_key,
        }

    def headers(self) -> dict[str, str]:
        if not self.token:
            raise ValueError(
                "runtime-manager bearer token is not configured. "
                f"Set {self.env_key} or {self.token_file_env_key}."
            )
        return {"Authorization": f"Bearer {self.token}"}


def _read_token_file(raw_path: str) -> str:
    path = Path(raw_path).expanduser()
    return path.read_text(encoding="utf-8").strip()


def resolve_runtime_manager_auth(
    *,
    token: str | None = None,
    env: Mapping[str, str] | None = None,
    token_env_key: str = _TOKEN_ENV,
    token_file_env_key: str = _TOKEN_FILE_ENV,
    default_token: str = _DEFAULT_TOKEN,
) -> RuntimeManagerAuthConfig:
    """Resolve the runtime-manager bearer token from arg, file, env, or default."""

    env_map = env or os.environ

    if token is not None:
        return RuntimeManagerAuthConfig(
            token=token.strip(),
            source="argument",
            env_key=token_env_key,
            token_file_env_key=token_file_env_key,
            used_default=False,
        )

    token_file = str(env_map.get(token_file_env_key, "")).strip()
    if token_file:
        return RuntimeManagerAuthConfig(
            token=_read_token_file(token_file),
            source="token_file",
            env_key=token_env_key,
            token_file_env_key=token_file_env_key,
            used_default=False,
        )

    env_token = str(env_map.get(token_env_key, "")).strip()
    if env_token:
        return RuntimeManagerAuthConfig(
            token=env_token,
            source="env",
            env_key=token_env_key,
            token_file_env_key=token_file_env_key,
            used_default=False,
        )

    return RuntimeManagerAuthConfig(
        token=default_token,
        source="default",
        env_key=token_env_key,
        token_file_env_key=token_file_env_key,
        used_default=True,
    )
