"""A failed acceptance probe must turn its workflow red: no pipe without pipefail, no phantom secret."""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PIPEFAIL = re.compile(r"pipefail|-[a-z]*e[a-z]*o\s+pipefail")


def _acceptance_workflows():
    return sorted(p for p in WORKFLOWS.glob("*acceptance*.yml"))


def _steps(text: str):
    """Split a workflow into step blocks on '- name:' / '- uses:' list items."""
    return re.split(r"(?m)^\s*-\s+(?=name:|uses:)", text)


def test_acceptance_workflows_exist():
    assert any(p.name == "persona-day-acceptance.yml" for p in _acceptance_workflows())


def test_piped_probe_steps_enable_pipefail():
    offenders = []
    for path in _acceptance_workflows():
        for step in _steps(path.read_text()):
            has_pipe = re.search(r"(?m)^[^#\n]*\bpython3?\b[^#\n]*\|\s*tee\b", step) or \
                re.search(r"(?m)^[^#\n]*acceptance/[^\n#]*\|\s*\w+", step)
            if has_pipe and not PIPEFAIL.search(step):
                offenders.append(f"{path.name}: {step.strip().splitlines()[0]}")
    assert not offenders, offenders


def test_persona_day_uses_existing_secrets_only():
    text = (WORKFLOWS / "persona-day-acceptance.yml").read_text()
    assert "DEV_ACCEPTANCE_READ_TOKEN" not in text
    assert "secrets.DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET" in text
    secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
    # Existing dev secrets only: viewer login + the release stage's pinned SSH key (no new secret).
    assert secrets == {"DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET", "DEV_DEPLOY_SSH_PRIVATE_KEY"}
    assert "cron:" in text and '"30 11 * * 1-5"' in text
