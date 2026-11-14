"""Tests for compose file, fetch policy, README installation guide, and zero-secret check (AC1, AC8)."""

from __future__ import annotations

import configparser
import json
import re
import shlex
import subprocess
from pathlib import Path

from collector_stack.config import DEFAULT_POLICY_PATH, load_collector_config

COLLECTOR_DIR = Path(__file__).resolve().parent.parent


def test_compose_policy_caddy_systemd_and_readme_are_complete() -> None:
    compose_path = COLLECTOR_DIR / "docker-compose.yml"
    policy_path = COLLECTOR_DIR / "fetch-policy.json"
    caddy_path = COLLECTOR_DIR / "Caddyfile"
    readme_path = COLLECTOR_DIR / "README.md"

    assert compose_path.is_file()
    assert policy_path.is_file()
    assert caddy_path.is_file()
    assert readme_path.is_file()

    compose_text = compose_path.read_text(encoding="utf-8")
    assert "diygod/rsshub:" in compose_text
    assert "collector-worker:" in compose_text
    assert "bundle-server:" in compose_text
    assert '"443:443"' in compose_text

    caddy_text = caddy_path.read_text(encoding="utf-8")
    assert "Strict-Transport-Security" in caddy_text
    assert "X-Content-Type-Options" in caddy_text
    assert "-Server" in caddy_text
    assert "Bearer {$COLLECTOR_BUNDLE_TOKEN}" in caddy_text
    assert "/status.json" in caddy_text
    assert "/bundles/*" in caddy_text
    assert "route {" in caddy_text
    assert caddy_text.index("@missing_or_bad_token") < caddy_text.index("file_server {")

    readme_text = readme_path.read_text(encoding="utf-8")
    for required_section in (
        "ufw allow 443/tcp",
        "/etc/pantheon-collector/author_hmac.salt",
        "docker compose up -d",
        "systemctl enable",
        "/var/lib/pantheon-collector/library/books",
    ):
        assert required_section in readme_text, f"README missing install step: {required_section}"

    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    assert len(config.sources) >= 18


def test_documented_source_timers_match_enabled_policy_cadences() -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    enabled = {sid: source for sid, source in config.sources.items() if source.enabled}
    faster = {sid for sid, source in enabled.items() if source.cadence_seconds < 21600}
    timer_intervals = {
        "pantheon-collector-hourly": ("*-*-* *:15:00", 3600),
        "pantheon-collector": ("*-*-* 00/6:15:00", 21600),
    }
    for template, (calendar, _) in timer_intervals.items():
        unit = configparser.ConfigParser(interpolation=None)
        assert unit.read(COLLECTOR_DIR / "systemd" / f"{template}@.timer")
        assert unit["Timer"]["OnCalendar"] == calendar
        assert unit["Timer"]["Unit"] == "pantheon-collector@%i.service"
        assert unit["Timer"].getboolean("Persistent")
        assert unit["Install"]["WantedBy"] == "timers.target"
        if template == "pantheon-collector-hourly":
            assert unit["Timer"]["RandomizedDelaySec"] == "0"
            assert unit["Timer"]["AccuracySec"] == "1s"

    readme = (COLLECTOR_DIR / "README.md").read_text(encoding="utf-8")
    step = readme.split("### Step 2.5", 1)[1].split("### Step 2.6", 1)[0]
    commands = step.split("```bash\n", 1)[1].split("```", 1)[0]
    # Execute the actual documented loops with sudo replaced by a logger: no
    # systemctl or filesystem command reaches the host, even on a root runner.
    result = subprocess.run(
        ["bash", "-euc", 'sudo() { printf "%s\\n" "$*"; }\n' + commands],
        cwd=COLLECTOR_DIR, check=True, capture_output=True, text=True, timeout=10,
    )
    active = {sid: set(timer_intervals) for sid in enabled}
    scheduled: dict[str, str] = {}
    for line in result.stdout.splitlines():
        args = shlex.split(line)
        if args[:1] != ["systemctl"] or len(args) != 4:
            continue
        match = re.fullmatch(r"(pantheon-collector(?:-hourly)?)@(.+)\.timer", args[3])
        if match is None:
            continue  # Prune timer is separate from per-source scheduling.
        template, sid = match.groups()
        assert sid in enabled, f"README schedules disabled or unknown source {sid}"
        assert args[2] == "--now"
        if args[1] == "disable":
            active[sid].discard(template)
        else:
            assert args[1] == "enable"
            assert not active[sid] - {template}, f"Disable alternate timer first for {sid}"
            assert sid not in scheduled, f"README enables multiple timers for {sid}"
            active[sid].add(template)
            scheduled[sid] = template

    assert scheduled.keys() == enabled.keys(), "README must schedule every enabled source"
    assert {sid for sid, timer in scheduled.items() if timer == "pantheon-collector-hourly"} == faster
    for sid, template in scheduled.items():
        assert active[sid] == {template}, f"Expected exactly one active timer for {sid}"
        interval = timer_intervals[template][1]
        assert interval <= enabled[sid].cadence_seconds, f"{sid} needs a faster timer than {template}"


def test_no_secret_or_real_token_is_committed() -> None:
    secret_patterns = [
        re.compile(r"AIza[0-9A-Za-z_-]{35}"),  # Google API key
        re.compile(r"ya29\.[0-9A-Za-z_-]+"),  # Google OAuth access token
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"gh[pousr]_[0-9A-Za-z]{36,}"),  # GitHub token
        re.compile(r"sk-[0-9A-Za-z]{32,}"),  # OpenAI/LLM secret key
        re.compile(r'"type"\s*:\s*"service_account"'),  # GCP service account JSON
    ]

    for path in COLLECTOR_DIR.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in secret_patterns:
            assert not pat.search(text), f"Potential secret pattern {pat.pattern!r} found in {path}"
        assert not path.name.endswith(".salt"), f"Real salt file must not be committed: {path}"
        assert path.name != ".env", f".env file must not be committed: {path}"


def test_regime_readme_month_backfill_commands_match_container_entrypoint(capsys):
    from collector_stack.cli import main
    readme = (COLLECTOR_DIR / 'README.md').read_text(encoding='utf-8')
    section = readme.split('## Regime inputs:', 1)[1]
    script = section.split('```bash\n', 1)[1].split('```', 1)[0].replace('\\\n', '')
    commands = [shlex.split(line) for line in script.splitlines() if line.startswith('docker compose')]
    assert len(commands) == 3
    for command in commands:
        args = command[command.index('collector-worker') + 1:]
        assert args[0] == '--root-dir'  # Python/module already supplied by image ENTRYPOINT.
        if '--dry-run' not in args:
            args.append('--dry-run')
        assert main(args) == 0
        plan = json.loads(capsys.readouterr().out)
        assert plan['first_date'] == '2012-01' and plan['min_interval_seconds'] >= 5
