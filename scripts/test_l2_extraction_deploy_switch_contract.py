"""Contract for L2-EXTRACTION-DEPLOY-SWITCH-20261115."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

from services.source_ingestion import hypothesis_card

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
DEPLOY = (ROOT / "scripts" / "deploy_nonprod_vm.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "nonprod-deploy.yml").read_text(encoding="utf-8")
ENABLED = "PANTHEON_L2_EXTRACTION_ENABLED"
CAP = "PANTHEON_L2_EXTRACTION_DAILY_CALL_CAP"


def test_compose_only_distillation_worker_receives_variables_with_off_defaults() -> None:
    holders = [
        name
        for name, svc in COMPOSE["services"].items()
        if ENABLED in (svc.get("environment") or {}) or CAP in (svc.get("environment") or {})
    ]
    assert holders == ["strategy-distillation-worker"]  # the only L2 extraction consumer (services/source_ingestion)
    env = COMPOSE["services"]["strategy-distillation-worker"]["environment"]
    assert env[ENABLED] == "${PANTHEON_L2_EXTRACTION_ENABLED:-0}"
    assert env[CAP] == "${PANTHEON_L2_EXTRACTION_DAILY_CALL_CAP:-400}"


def test_extraction_stays_off_when_unset(monkeypatch) -> None:
    monkeypatch.delenv(ENABLED, raising=False)
    assert hypothesis_card.extractor_from_env() is None
    monkeypatch.setenv(ENABLED, "false")
    assert hypothesis_card.extractor_from_env() is None


def test_workflow_and_script_pass_through_with_defaults() -> None:
    assert "DEV_L2_EXTRACTION_ENABLED: ${{ vars.DEV_L2_EXTRACTION_ENABLED || 'false' }}" in WORKFLOW
    assert "DEV_L2_EXTRACTION_DAILY_CALL_CAP: ${{ vars.DEV_L2_EXTRACTION_DAILY_CALL_CAP || '400' }}" in WORKFLOW
    assert 'DEV_L2_EXTRACTION_ENABLED="${DEV_L2_EXTRACTION_ENABLED:-false}"' in DEPLOY
    assert 'DEV_L2_EXTRACTION_DAILY_CALL_CAP="${DEV_L2_EXTRACTION_DAILY_CALL_CAP:-400}"' in DEPLOY
    assert 'PANTHEON_DEV_L2_EXTRACTION_ENABLED=$(shell_quote "$DEV_L2_EXTRACTION_ENABLED")' in DEPLOY
    assert 'PANTHEON_DEV_L2_EXTRACTION_DAILY_CALL_CAP=$(shell_quote "$DEV_L2_EXTRACTION_DAILY_CALL_CAP")' in DEPLOY
    assert f'{ENABLED}="${{PANTHEON_DEV_L2_EXTRACTION_ENABLED:-false}}"' in DEPLOY
    assert f'{CAP}="${{PANTHEON_DEV_L2_EXTRACTION_DAILY_CALL_CAP:-400}}"' in DEPLOY


def test_validation_precedes_service_changes() -> None:
    v = DEPLOY.index('error "DEV_L2_EXTRACTION_ENABLED must be true or false"')
    c = DEPLOY.index('error "DEV_L2_EXTRACTION_DAILY_CALL_CAP must be a positive integer"')
    first_action = DEPLOY.index("\nconfigure_management_ai_dev_env\n")
    assert v < first_action and c < first_action
    assert "^[1-9][0-9]*$" in DEPLOY


def test_validation_snippet_behaviour() -> None:
    m = re.search(r'case "\$DEV_L2_EXTRACTION_ENABLED" in.*?positive integer"\n', DEPLOY, re.S)
    assert m
    snippet = 'error(){ echo "$1" >&2; exit 1; }\n' + m.group(0)

    def run(en: str, cap: str) -> int:
        return subprocess.run(
            ["bash", "-c", snippet],
            env={"DEV_L2_EXTRACTION_ENABLED": en, "DEV_L2_EXTRACTION_DAILY_CALL_CAP": cap, "PATH": "/usr/bin:/bin"},
            capture_output=True,
        ).returncode

    assert run("true", "400") == 0
    assert run("false", "1") == 0
    for en, cap in [("yes", "400"), ("", "400"), ("true", "0"), ("true", "-5"), ("true", "abc"), ("true", "1.5"), ("true", "")]:
        assert run(en, cap) != 0, (en, cap)
