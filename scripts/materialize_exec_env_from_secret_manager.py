#!/usr/bin/env python3
"""Materialize VM-2 execution env files from Secret Manager."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)
SECRET_VERSION_URL = (
    "https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{secret}/versions/latest:access"
)
SECRET_KEY_MAP = {
    "BROKER_API_KEY": "BROKER_API_KEY_SECRET_NAME",
    "BROKER_API_SECRET": "BROKER_API_SECRET_SECRET_NAME",
    "EXCHANGE_API_KEY": "EXCHANGE_API_KEY_SECRET_NAME",
    "EXCHANGE_API_SECRET": "EXCHANGE_API_SECRET_SECRET_NAME",
}


class MaterializeError(RuntimeError):
    pass


def parse_env_file(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text().splitlines()
    env_map: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_map[key] = value
    return lines, env_map


def write_env_file(path: Path, lines: list[str], env_map: dict[str, str]) -> None:
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rendered.append(line)
            continue
        key, _ = line.split("=", 1)
        rendered.append(f"{key}={env_map.get(key, '')}")
    if lines and lines[-1] == "":
        text = "\n".join(rendered)
    else:
        text = "\n".join(rendered) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.chmod(path, 0o600)


def metadata_access_token() -> str | None:
    request = urllib.request.Request(
        METADATA_TOKEN_URL,
        headers={"Metadata-Flavor": "Google"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    token = str(payload.get("access_token") or "")
    return token or None


def gcloud_access_token() -> str:
    completed = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        check=True,
        capture_output=True,
        text=True,
    )
    token = completed.stdout.strip()
    if not token:
        raise MaterializeError("gcloud auth print-access-token returned an empty token")
    return token


def access_token() -> str:
    token = metadata_access_token()
    if token:
        return token
    return gcloud_access_token()


def fetch_secret_value(project: str, secret_name: str, token: str) -> str:
    url = SECRET_VERSION_URL.format(project=project, secret=secret_name)
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise MaterializeError(
            f"failed to access secret {secret_name!r}: HTTP {exc.code} {body}"
        ) from exc
    except Exception as exc:
        raise MaterializeError(f"failed to access secret {secret_name!r}: {exc}") from exc

    encoded = ((payload.get("payload") or {}).get("data")) or ""
    if not encoded:
        raise MaterializeError(f"secret {secret_name!r} returned no payload data")
    import base64

    return base64.b64decode(encoded).decode("utf-8")


def resolve_runtime_manager_token(
    env_map: dict[str, str], explicit_token: str | None, generate_token: bool
) -> str:
    current = env_map.get("PANTHEON_RUNTIME_MANAGER_TOKEN", "")
    if explicit_token:
        return explicit_token
    if current and current != "replace-me-runtime-manager-token":
        return current
    if generate_token:
        return secrets.token_hex(24)
    raise MaterializeError(
        "PANTHEON_RUNTIME_MANAGER_TOKEN is empty/placeholder and --generate-runtime-manager-token was not set"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize a VM-2 execution env file from Secret Manager"
    )
    parser.add_argument("--template", required=True, help="Input env template path")
    parser.add_argument("--output", required=True, help="Output env path")
    parser.add_argument("--project", required=True, help="GCP project id")
    parser.add_argument(
        "--runtime-manager-token",
        default=None,
        help="Optional explicit runtime-manager token",
    )
    parser.add_argument(
        "--generate-runtime-manager-token",
        action="store_true",
        help="Generate a new runtime-manager token if the template is empty/placeholder",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    template_path = Path(args.template)
    output_path = Path(args.output)
    lines, env_map = parse_env_file(template_path)

    token = access_token()
    for raw_key, secret_name_key in SECRET_KEY_MAP.items():
        secret_name = env_map.get(secret_name_key, "")
        if not secret_name:
            raise MaterializeError(f"{secret_name_key} is empty in {template_path}")
        env_map[raw_key] = fetch_secret_value(args.project, secret_name, token)

    env_map["PANTHEON_RUNTIME_MANAGER_TOKEN"] = resolve_runtime_manager_token(
        env_map=env_map,
        explicit_token=args.runtime_manager_token,
        generate_token=args.generate_runtime_manager_token,
    )
    env_map["PANTHEON_SECRETS_OPTIONAL"] = "false"
    write_env_file(output_path, lines, env_map)

    summary = {
        "template": str(template_path),
        "output": str(output_path),
        "project": args.project,
        "resolved_keys": sorted(SECRET_KEY_MAP),
        "runtime_manager_token": "set",
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MaterializeError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
