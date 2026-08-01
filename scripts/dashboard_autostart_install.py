#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


SERVICE_BASENAME = "pantheon-dashboard-autostart"
SERVICE_NAME = f"{SERVICE_BASENAME}.service"
TIMER_NAME = f"{SERVICE_BASENAME}.timer"
CRON_TAG = "# pantheon-dashboard-autostart"


def repo_root_from(value: str | None) -> Path:
    return Path(value or ".").expanduser().resolve()


def systemd_quote(value: Path | str) -> str:
    text = str(value)
    if not text:
        return '""'
    if any(ch.isspace() or ch in {'"', "\\"} for ch in text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def render_systemd_service(repo_root: Path) -> str:
    script = repo_root / "scripts" / "dashboard_autostart.sh"
    return "\n".join(
        [
            "[Unit]",
            "Description=Pantheon dashboard recovery probe",
            "Documentation=file://%s"
            % (repo_root / "docs" / "operations" / "dashboard-autostart-persistence.md"),
            "After=default.target",
            "",
            "[Service]",
            "Type=oneshot",
            # The probe intentionally launches long-running tmux children. Keep
            # systemd from killing them when the oneshot process exits.
            "KillMode=process",
            f"WorkingDirectory={systemd_quote(repo_root)}",
            f"Environment={systemd_quote(f'PANTHEON_DASHBOARD_ROOT={repo_root}')}",
            f"ExecStart={systemd_quote(script)}",
            "",
        ]
    )


def render_systemd_timer() -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=Run Pantheon dashboard recovery probe",
            "",
            "[Timer]",
            "OnBootSec=30s",
            "OnCalendar=*-*-* *:*:00",
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
    script = shlex.quote(str(repo_root / "scripts" / "dashboard_autostart.sh"))
    root_assignment = shlex.quote(f"PANTHEON_DASHBOARD_ROOT={repo_root}")
    return (
        f"* * * * * cd {repo} && mkdir -p .orchestrator/logs && "
        f"env {root_assignment} bash {script} "
        f">> .orchestrator/logs/dashboard-autostart-cron.log 2>&1 {CRON_TAG}"
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


def run_command(command: list[str], *, dry_run: bool) -> None:
    if dry_run:
        print("dry-run: " + " ".join(shlex.quote(part) for part in command))
        return
    subprocess.run(command, check=True)


def write_text(path: Path, content: str, *, dry_run: bool) -> None:
    if dry_run:
        print(f"dry-run: write {path}")
        print(content, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def install_systemd(repo_root: Path, *, dry_run: bool, start_now: bool) -> None:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    write_text(
        unit_dir / SERVICE_NAME,
        render_systemd_service(repo_root),
        dry_run=dry_run,
    )
    write_text(unit_dir / TIMER_NAME, render_systemd_timer(), dry_run=dry_run)
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
    return result.stdout.splitlines() if result.returncode == 0 else []


def write_crontab(lines: list[str], *, dry_run: bool, action: str) -> None:
    content = "\n".join(lines).rstrip()
    if content:
        content += "\n"
    if dry_run:
        print(f"dry-run: {action} crontab")
        print(content, end="")
        return
    subprocess.run(["crontab", "-"], input=content, text=True, check=True)


def install_cron(repo_root: Path, *, dry_run: bool) -> None:
    existing = [line for line in current_crontab() if CRON_TAG not in line]
    write_crontab([*existing, render_cron_line(repo_root)], dry_run=dry_run, action="install")
    print(f"installed cron dashboard entry: {CRON_TAG}")


def uninstall_cron(*, dry_run: bool) -> None:
    existing = [line for line in current_crontab() if CRON_TAG not in line]
    write_crontab(existing, dry_run=dry_run, action="uninstall")
    print(f"uninstalled cron dashboard entry: {CRON_TAG}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install or remove persistent Pantheon dashboard recovery."
    )
    parser.add_argument("--repo", default=".", help="Pantheon repository root. Defaults to cwd.")
    parser.add_argument(
        "--method",
        choices=["auto", "systemd", "cron"],
        default="auto",
        help="Persistence backend. auto prefers user systemd and falls back to cron.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print changes without applying them.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the selected backend.")
    parser.add_argument(
        "--start-now",
        dest="start_now",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Immediately run one dashboard recovery probe for systemd installs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = repo_root_from(args.repo)
    if not args.uninstall and not (repo_root / "scripts" / "dashboard_autostart.sh").is_file():
        print(
            f"not a Pantheon repo root, missing scripts/dashboard_autostart.sh: {repo_root}",
            file=sys.stderr,
        )
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
        elif method == "systemd":
            install_systemd(repo_root, dry_run=args.dry_run, start_now=args.start_now)
        else:
            install_cron(repo_root, dry_run=args.dry_run)
    except subprocess.CalledProcessError as exc:
        print(f"dashboard persistence command failed: {exc}", file=sys.stderr)
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
