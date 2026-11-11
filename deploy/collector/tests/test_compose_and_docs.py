"""Tests for compose file, fetch policy, README installation guide, and zero-secret check (AC1, AC8)."""

from __future__ import annotations

import re
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
