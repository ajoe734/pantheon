#!/usr/bin/env python3
"""Install or remove a conservative cron runner for the auto-integrator."""

from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path


CRON_TAG = "# pantheon-auto-integrator"
DEFAULT_INTERVAL = "*/5 * * * *"


def repo_root_from(value: str | None) -> Path:
    return Path(value or ".").expanduser().resolve()


def status_root_from(value: str | None, repo_root: Path) -> Path:
    return Path(value).expanduser().resolve() if value else repo_root


def config_file_from(value: str | None, status_root: Path) -> Path:
    return (
        Path(value).expanduser().resolve()
        if value
        else status_root / ".orchestrator" / "config.json"
    )


def render_cron_line(
    repo_root: Path,
    status_root: Path,
    config_file: Path | None = None,
    *,
    interval: str = DEFAULT_INTERVAL,
) -> str:
    repo = shlex.quote(str(repo_root))
    status = shlex.quote(str(status_root))
    config = shlex.quote(str(config_file or status_root / ".orchestrator" / "config.json"))
    log_dir = shlex.quote(str(status_root / ".orchestrator" / "logs"))
    log_file = shlex.quote(str(status_root / ".orchestrator" / "logs" / "auto-integrator-cron.log"))
    return (
        f"{interval} cd {repo} && mkdir -p {log_dir} && "
        f"PANTHEON_STATUS_ROOT={status} PANTHEON_AUTO_INTEGRATOR_CONFIG={config} "
        f"bash scripts/run-auto-integrator.sh "
        f">> {log_file} 2>&1 {CRON_TAG}"
    )


def current_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def write_crontab(lines: list[str], *, dry_run: bool) -> None:
    content = "\n".join(lines).rstrip()
    if content:
        content += "\n"
    if dry_run:
        print("dry-run: install crontab")
        print(content, end="")
        return
    subprocess.run(["crontab", "-"], input=content, text=True, check=True)


def install_cron(
    repo_root: Path,
    status_root: Path,
    config_file: Path,
    *,
    interval: str,
    dry_run: bool,
) -> None:
    line = render_cron_line(
        repo_root,
        status_root,
        config_file,
        interval=interval,
    )
    existing = [raw for raw in current_crontab() if CRON_TAG not in raw]
    write_crontab([*existing, line], dry_run=dry_run)
    print(f"installed cron auto-integrator entry: {CRON_TAG}")


def uninstall_cron(*, dry_run: bool) -> None:
    existing = [raw for raw in current_crontab() if CRON_TAG not in raw]
    write_crontab(existing, dry_run=dry_run)
    print(f"uninstalled cron auto-integrator entry: {CRON_TAG}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install or remove the Pantheon auto-integrator cron runner.")
    parser.add_argument("--repo", default=".", help="Pantheon git checkout used for integration. Defaults to cwd.")
    parser.add_argument(
        "--status-root",
        default=None,
        help="Canonical Pantheon status root. Defaults to --repo unless supplied.",
    )
    parser.add_argument(
        "--interval",
        default=DEFAULT_INTERVAL,
        help="Cron schedule prefix. Defaults to every five minutes.",
    )
    parser.add_argument(
        "--config-file",
        default=None,
        help=(
            "Rendered live supervisor config used for repository ownership. "
            "Defaults to <status-root>/.orchestrator/config.json."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print intended crontab without applying it.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the auto-integrator crontab entry.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = repo_root_from(args.repo)
    status_root = status_root_from(args.status_root, repo_root)
    config_file = config_file_from(args.config_file, status_root)
    if not args.uninstall and not (repo_root / "scripts" / "run-auto-integrator.sh").exists():
        print(f"not a Pantheon repo root, missing scripts/run-auto-integrator.sh: {repo_root}")
        return 2
    try:
        if args.uninstall:
            uninstall_cron(dry_run=args.dry_run)
        else:
            install_cron(
                repo_root,
                status_root,
                config_file,
                interval=args.interval,
                dry_run=args.dry_run,
            )
    except subprocess.CalledProcessError as exc:
        print(f"auto-integrator persistence command failed: {exc}")
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
