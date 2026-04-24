#!/usr/bin/env python3
"""Seed EP5 execution secret versions from local files."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


SECRET_SPECS = {
    "broker_api_key": "pantheon-prod-broker-api-key",
    "broker_api_secret": "pantheon-prod-broker-api-secret",
    "exchange_api_key": "pantheon-prod-exchange-api-key",
    "exchange_api_secret": "pantheon-prod-exchange-api-secret",
}


class SeedError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed EP5 execution secret versions from local files"
    )
    parser.add_argument("--project", required=True, help="GCP project id")
    parser.add_argument("--broker-api-key-file", required=True, help="Path to broker API key file")
    parser.add_argument(
        "--broker-api-secret-file", required=True, help="Path to broker API secret file"
    )
    parser.add_argument("--exchange-api-key-file", required=True, help="Path to exchange API key file")
    parser.add_argument(
        "--exchange-api-secret-file", required=True, help="Path to exchange API secret file"
    )
    parser.add_argument(
        "--allow-existing-versions",
        action="store_true",
        help="Allow adding new versions even if a secret already has versions",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the plan without writing secret versions",
    )
    return parser


def run_gcloud(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, text=True, capture_output=True)


def latest_version_count(project: str, secret_name: str) -> int:
    completed = run_gcloud(
        [
            "gcloud",
            "secrets",
            "versions",
            "list",
            secret_name,
            "--project",
            project,
            "--format=value(name)",
        ]
    )
    output = completed.stdout.strip()
    if not output:
        return 0
    return len([line for line in output.splitlines() if line.strip()])


def ensure_secret_exists(project: str, secret_name: str) -> None:
    run_gcloud(
        [
            "gcloud",
            "secrets",
            "describe",
            secret_name,
            "--project",
            project,
        ]
    )


def read_value(path: Path) -> str:
    if not path.exists():
        raise SeedError(f"missing file: {path}")
    value = path.read_text().strip()
    if not value:
        raise SeedError(f"empty secret value file: {path}")
    return value


def add_secret_version(project: str, secret_name: str, value: str) -> None:
    with tempfile.NamedTemporaryFile("w", delete=False) as handle:
        handle.write(value)
        handle.flush()
        temp_path = handle.name
    try:
        run_gcloud(
            [
                "gcloud",
                "secrets",
                "versions",
                "add",
                secret_name,
                "--project",
                project,
                f"--data-file={temp_path}",
            ]
        )
    finally:
        Path(temp_path).unlink(missing_ok=True)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    file_map = {
        "broker_api_key": Path(args.broker_api_key_file),
        "broker_api_secret": Path(args.broker_api_secret_file),
        "exchange_api_key": Path(args.exchange_api_key_file),
        "exchange_api_secret": Path(args.exchange_api_secret_file),
    }

    values = {key: read_value(path) for key, path in file_map.items()}
    summary: dict[str, object] = {
        "project": args.project,
        "dry_run": args.dry_run,
        "secrets": [],
    }

    for key, secret_name in SECRET_SPECS.items():
        ensure_secret_exists(args.project, secret_name)
        version_count = latest_version_count(args.project, secret_name)
        if version_count and not args.allow_existing_versions:
            raise SeedError(
                f"{secret_name} already has {version_count} version(s); rerun with --allow-existing-versions to append"
            )
        summary["secrets"].append(
            {
                "secret_name": secret_name,
                "input_file": str(file_map[key]),
                "value_length": len(values[key]),
                "existing_versions": version_count,
            }
        )
        if not args.dry_run:
            add_secret_version(args.project, secret_name, values[key])

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
