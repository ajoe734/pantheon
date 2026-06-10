#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE_BASENAME = "pantheon-supervisor-watchdog"
SERVICE_NAME = f"{SERVICE_BASENAME}.service"
TIMER_NAME = f"{SERVICE_BASENAME}.timer"
CRON_TAG = "# pantheon-supervisor-watchdog"


def repo_root_from(value: str | None) -> Path:
    raw = Path(value or ".").expanduser()
    return raw.resolve()


def systemd_quote(value: Path | str) -> str:
    text = str(value)
    if not text:
        return '""'
    if any(ch.isspace() or ch in {'"', "\\"} for ch in text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def render_systemd_service(repo_root: Path) -> str:
    script = repo_root / "scripts" / "run-supervisor-watchdog.sh"
    return "\n".join(
        [
            "[Unit]",
            "Description=Pantheon supervisor watchdog",
            "Documentation=file://%s" % (repo_root / "docs" / "operations" / "supervisor-watchdog-persistence.md"),
            "After=default.target",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={systemd_quote(repo_root)}",
            "Environment=PYTHONUNBUFFERED=1",
            f"ExecStart={systemd_quote(script)} --restart",
            "",
        ]
    )


def render_systemd_timer() -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=Run Pantheon supervisor watchdog",
            "",
            "[Timer]",
            "OnBootSec=30s",
            "OnUnitActiveSec=60s",
            "AccuracySec=10s",
            "Persistent=true",
            f"Unit={SERVICE_NAME}",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )


def render_cron_line(repo_root: Path) -> str:
    repo = shlex.quote(str(repo_root))
    return (
        f"* * * * * cd {repo} && mkdir -p .orchestrator/logs && "
        "bash scripts/run-supervisor-watchdog.sh --restart "
        f">> .orchestrator/logs/supervisor-watchdog-cron.log 2>&1 {CRON_TAG}"
    )


def user_systemd_available() -> bool:
    if shutil.which("systemctl") is None:
        return False
    result = subprocess.run(
        ["systemctl", "--user", "show-environment"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def run_command(command: list[str], *, dry_run: bool, input_text: str | None = None) -> None:
    printable = " ".join(shlex.quote(part) for part in command)
    if dry_run:
        print(f"dry-run: {printable}")
        return
    subprocess.run(command, input=input_text, text=True, check=True)


def write_text(path: Path, content: str, *, dry_run: bool) -> None:
    if dry_run:
        print(f"dry-run: write {path}")
        print(content, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def install_systemd(repo_root: Path, *, dry_run: bool, start_now: bool) -> None:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    service_path = unit_dir / SERVICE_NAME
    timer_path = unit_dir / TIMER_NAME
    write_text(service_path, render_systemd_service(repo_root), dry_run=dry_run)
    write_text(timer_path, render_systemd_timer(), dry_run=dry_run)
    run_command(["systemctl", "--user", "daemon-reload"], dry_run=dry_run)
    run_command(["systemctl", "--user", "enable", "--now", TIMER_NAME], dry_run=dry_run)
    if start_now:
        run_command(["systemctl", "--user", "start", SERVICE_NAME], dry_run=dry_run)
    print(f"installed systemd user timer: {TIMER_NAME}")


def uninstall_systemd(*, dry_run: bool) -> None:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    run_command(["systemctl", "--user", "disable", "--now", TIMER_NAME], dry_run=dry_run)
    for path in (unit_dir / SERVICE_NAME, unit_dir / TIMER_NAME):
        if dry_run:
            print(f"dry-run: remove {path}")
        else:
            path.unlink(missing_ok=True)
    run_command(["systemctl", "--user", "daemon-reload"], dry_run=dry_run)
    print(f"uninstalled systemd user timer: {TIMER_NAME}")


def current_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def install_cron(repo_root: Path, *, dry_run: bool) -> None:
    line = render_cron_line(repo_root)
    existing = [raw for raw in current_crontab() if CRON_TAG not in raw]
    new_content = "\n".join([*existing, line]).rstrip() + "\n"
    if dry_run:
        print("dry-run: install crontab")
        print(new_content, end="")
    else:
        subprocess.run(["crontab", "-"], input=new_content, text=True, check=True)
    print(f"installed cron watchdog entry: {CRON_TAG}")


def uninstall_cron(*, dry_run: bool) -> None:
    existing = [raw for raw in current_crontab() if CRON_TAG not in raw]
    new_content = "\n".join(existing).rstrip()
    if new_content:
        new_content += "\n"
    if dry_run:
        print("dry-run: uninstall crontab")
        print(new_content, end="")
    else:
        subprocess.run(["crontab", "-"], input=new_content, text=True, check=True)
    print(f"uninstalled cron watchdog entry: {CRON_TAG}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install or remove the persistent Pantheon supervisor watchdog."
    )
    parser.add_argument("--repo", default=".", help="Pantheon repository root. Defaults to cwd.")
    parser.add_argument(
        "--method",
        choices=["auto", "systemd", "cron"],
        default="auto",
        help="Persistence backend. auto prefers user systemd and falls back to cron.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print intended changes without applying them.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the selected persistence backend.")
    parser.add_argument(
        "--start-now",
        dest="start_now",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Immediately run one watchdog probe after installing systemd units.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = repo_root_from(args.repo)
    if not (repo_root / "scripts" / "run-supervisor-watchdog.sh").exists():
        print(f"not a Pantheon repo root, missing scripts/run-supervisor-watchdog.sh: {repo_root}", file=sys.stderr)
        return 2

    method = args.method
    if method == "auto":
        method = "systemd" if user_systemd_available() else "cron"

    try:
        if args.uninstall:
            if method == "systemd":
                uninstall_systemd(dry_run=args.dry_run)
            else:
                uninstall_cron(dry_run=args.dry_run)
        else:
            if method == "systemd":
                install_systemd(repo_root, dry_run=args.dry_run, start_now=args.start_now)
            else:
                install_cron(repo_root, dry_run=args.dry_run)
    except subprocess.CalledProcessError as exc:
        print(f"watchdog persistence command failed: {exc}", file=sys.stderr)
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
