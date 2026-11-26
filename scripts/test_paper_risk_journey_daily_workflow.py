import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / ".github/workflows/paper-risk-journey-daily.yml").read_text()


def test_secret_set_is_exact():
    assert set(re.findall(r"secrets\.([A-Za-z0-9_]+)", TEXT)) == {
        "DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET", "DEV_DEPLOY_SSH_PRIVATE_KEY"}


def test_only_existing_variables():
    declared = set(re.findall(r"vars\.([A-Z0-9_]+)", TEXT))
    release = (ROOT / ".github/workflows/nonprod-deploy.yml").read_text()
    persona = (ROOT / ".github/workflows/persona-day-acceptance.yml").read_text()
    existing = release + persona
    assert declared and all(name in existing for name in declared), declared


def test_prepare_before_check_before_removal_and_key_only_in_prepare():
    prepare = TEXT.index("scripts/dev_vm_ssh.sh prepare")
    check = TEXT.index("acceptance/paper_risk_journey_day.py")
    remove = TEXT.index("rm -rf")
    assert prepare < check < remove
    assert TEXT.count("scripts/dev_vm_ssh.sh prepare") == 1
    assert "always()" in TEXT[remove - 200:remove]
    assert TEXT.count("DEV_DEPLOY_SSH_PRIVATE_KEY") == 2  # secret mapping + env name, prepare step only
    assert TEXT.index("DEV_DEPLOY_SSH_PRIVATE_KEY") < check
    assert TEXT.rindex("DEV_DEPLOY_SSH_PRIVATE_KEY") < check
    for needed in ("vars.DEV_DEPLOY_SSH_KNOWN_HOSTS", "vars.DEV_DEPLOY_SSH_HOST", "vars.NONPROD_REMOTE_USER",
                   "DEV_DEPLOY_SSH_KEY_FILE", "DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE"):
        assert needed in TEXT


def test_read_only_shape():
    assert "permissions:\n  contents: read" in TEXT
    assert "persist-credentials: false" in TEXT
    assert "environment: dev" in TEXT
    assert "retention-days: 30" in TEXT
    assert "workflow_dispatch" in TEXT and 'cron: "30 11 * * 1-5"' in TEXT
    assert "-euo pipefail" in TEXT
    assert not re.search(r"\b(POST|PUT|DELETE|PATCH)\b", TEXT)
