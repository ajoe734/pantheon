#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_ENV_FILE = Path.home() / ".config" / "pantheon" / "worker-git.env"
SUPPORTED_MODES = {"auto", "ssh", "pat"}
TOKEN_ENV_KEYS = ("PANTHEON_WORKER_GIT_TOKEN", "GITHUB_TOKEN")


class CredentialError(RuntimeError):
    """Raised when worker git credentials are absent or unsafe to use."""


@dataclass(frozen=True)
class GitCredentials:
    mode: str
    env: dict[str, str]
    credential_path: str | None
    secret_env_keys: tuple[str, ...]
    secret_file_paths: tuple[str, ...]
    source: str

    def as_subprocess_env(self, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
        merged = dict(os.environ if base_env is None else base_env)
        merged.update(self.env)
        return merged

    def safe_summary(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "env_keys": sorted(self.env),
            "credential_path": self.credential_path,
            "secret_env_keys": list(self.secret_env_keys),
            "secret_file_paths": list(self.secret_file_paths),
            "source": self.source,
        }


def _expand_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise CredentialError(f"Invalid worker git env file line {line_number} in {path}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise CredentialError(f"Invalid worker git env file line {line_number} in {path}: empty key")
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values


def _load_effective_env(
    env: Mapping[str, str] | None,
    env_file: str | os.PathLike[str] | None,
) -> tuple[dict[str, str], str]:
    base = dict(os.environ if env is None else env)
    explicit_env = env is not None

    env_file_path: Path | None = None
    if env_file:
        env_file_path = _expand_path(str(env_file))
    elif base.get("PANTHEON_WORKER_GIT_ENV_FILE"):
        env_file_path = _expand_path(base["PANTHEON_WORKER_GIT_ENV_FILE"])
    elif not explicit_env and DEFAULT_ENV_FILE.exists():
        env_file_path = DEFAULT_ENV_FILE

    file_values: dict[str, str] = {}
    source = "environment"
    if env_file_path is not None:
        file_values = _parse_env_file(env_file_path)
        source = str(env_file_path)

    effective = {**file_values, **base}
    return effective, source


def _choose_mode(env: Mapping[str, str]) -> str:
    mode = env.get("PANTHEON_WORKER_GIT_AUTH_MODE", "auto").strip().lower() or "auto"
    if mode not in SUPPORTED_MODES:
        raise CredentialError(
            "Unsupported PANTHEON_WORKER_GIT_AUTH_MODE "
            f"{mode!r}; expected one of {', '.join(sorted(SUPPORTED_MODES))}"
        )
    if mode != "auto":
        return mode
    if env.get("PANTHEON_WORKER_GIT_SSH_KEY_PATH"):
        return "ssh"
    if env.get("PANTHEON_WORKER_GIT_ASKPASS") or env.get("PANTHEON_WORKER_GIT_TOKEN_FILE"):
        return "pat"
    if any(env.get(key) for key in TOKEN_ENV_KEYS):
        return "pat"
    raise CredentialError(
        "No worker git credential found. Set PANTHEON_WORKER_GIT_SSH_KEY_PATH for SSH "
        "or PANTHEON_WORKER_GIT_TOKEN/PANTHEON_WORKER_GIT_TOKEN_FILE plus "
        "PANTHEON_WORKER_GIT_ASKPASS for PAT mode."
    )


def _ssh_credentials(env: Mapping[str, str], source: str) -> GitCredentials:
    raw_key_path = env.get("PANTHEON_WORKER_GIT_SSH_KEY_PATH", "").strip()
    if not raw_key_path:
        raise CredentialError("SSH mode requires PANTHEON_WORKER_GIT_SSH_KEY_PATH")

    key_path = _expand_path(raw_key_path)
    if not key_path.is_file():
        raise CredentialError(f"PANTHEON_WORKER_GIT_SSH_KEY_PATH does not exist or is not a file: {key_path}")
    if key_path.stat().st_size <= 0:
        raise CredentialError(f"PANTHEON_WORKER_GIT_SSH_KEY_PATH is empty: {key_path}")

    key_mode = key_path.stat().st_mode
    if key_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise CredentialError(f"SSH private key must not be group/world accessible: {key_path}")

    parts = [
        "ssh",
        "-i",
        str(key_path),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
    ]

    known_hosts_raw = env.get("PANTHEON_WORKER_GIT_KNOWN_HOSTS", "").strip()
    if known_hosts_raw:
        known_hosts = _expand_path(known_hosts_raw)
        if not known_hosts.is_file():
            raise CredentialError(f"PANTHEON_WORKER_GIT_KNOWN_HOSTS does not exist or is not a file: {known_hosts}")
        parts.extend(["-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}"])
    else:
        parts.extend(["-o", "StrictHostKeyChecking=accept-new"])

    command = " ".join(shlex.quote(part) for part in parts)
    return GitCredentials(
        mode="ssh",
        env={
            "GIT_SSH_COMMAND": command,
            "GIT_TERMINAL_PROMPT": "0",
        },
        credential_path=str(key_path),
        secret_env_keys=(),
        secret_file_paths=(str(key_path),),
        source=source,
    )


def _token_present(env: Mapping[str, str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    present_env_keys = tuple(key for key in TOKEN_ENV_KEYS if env.get(key))
    token_file_raw = env.get("PANTHEON_WORKER_GIT_TOKEN_FILE", "").strip()
    secret_files: tuple[str, ...] = ()
    if token_file_raw:
        token_file = _expand_path(token_file_raw)
        if not token_file.is_file() or token_file.stat().st_size <= 0:
            raise CredentialError(f"PANTHEON_WORKER_GIT_TOKEN_FILE is missing or empty: {token_file}")
        secret_files = (str(token_file),)
    if not present_env_keys and not secret_files:
        raise CredentialError(
            "PAT mode requires PANTHEON_WORKER_GIT_TOKEN, GITHUB_TOKEN, "
            "or PANTHEON_WORKER_GIT_TOKEN_FILE"
        )
    return present_env_keys, secret_files


def _pat_credentials(env: Mapping[str, str], source: str) -> GitCredentials:
    askpass_raw = env.get("PANTHEON_WORKER_GIT_ASKPASS", "").strip()
    if not askpass_raw:
        raise CredentialError("PAT mode requires PANTHEON_WORKER_GIT_ASKPASS")
    askpass = _expand_path(askpass_raw)
    if not askpass.is_file():
        raise CredentialError(f"PANTHEON_WORKER_GIT_ASKPASS does not exist or is not a file: {askpass}")
    if not os.access(askpass, os.X_OK):
        raise CredentialError(f"PANTHEON_WORKER_GIT_ASKPASS is not executable: {askpass}")

    secret_env_keys, secret_file_paths = _token_present(env)
    username = env.get("PANTHEON_WORKER_GIT_USERNAME", "x-access-token").strip() or "x-access-token"

    env_additions = {
        "GIT_ASKPASS": str(askpass),
        "GIT_TERMINAL_PROMPT": "0",
        "PANTHEON_WORKER_GIT_USERNAME": username,
    }
    if secret_file_paths:
        env_additions["PANTHEON_WORKER_GIT_TOKEN_FILE"] = secret_file_paths[0]

    return GitCredentials(
        mode="pat",
        env=env_additions,
        credential_path=str(askpass),
        secret_env_keys=secret_env_keys,
        secret_file_paths=secret_file_paths,
        source=source,
    )


def load_git_credentials(
    env: Mapping[str, str] | None = None,
    *,
    env_file: str | os.PathLike[str] | None = None,
) -> GitCredentials:
    """Return git credential environment for non-interactive worker pushes.

    The returned object intentionally exposes only the environment entries that
    git needs in addition to the worker's existing environment. Raw token or key
    material is never copied into ``GitCredentials.env`` or ``safe_summary()``.
    """

    effective_env, source = _load_effective_env(env, env_file)
    mode = _choose_mode(effective_env)
    if mode == "ssh":
        return _ssh_credentials(effective_env, source)
    if mode == "pat":
        return _pat_credentials(effective_env, source)
    raise AssertionError(f"unhandled worker git credential mode: {mode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve non-interactive git push credentials for Pantheon workers.")
    parser.add_argument("--env-file", help="Optional worker git env file to load before process environment.")
    parser.add_argument("--json", action="store_true", help="Print a secret-free JSON summary.")
    args = parser.parse_args(argv)

    try:
        credentials = load_git_credentials(env_file=args.env_file)
    except CredentialError as exc:
        print(f"credential_unavailable: {exc}")
        return 2

    if args.json:
        print(json.dumps(credentials.safe_summary(), indent=2, sort_keys=True))
    else:
        print(f"credential_available mode={credentials.mode} source={credentials.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
