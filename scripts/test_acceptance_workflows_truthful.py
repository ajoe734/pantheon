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


def test_persona_day_uses_existing_viewer_identity_only():
    text = (WORKFLOWS / "persona-day-acceptance.yml").read_text()
    assert "DEV_ACCEPTANCE_READ_TOKEN" not in text
    assert "secrets.DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET" in text
    secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
    assert secrets == {"DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET"}
    assert "cron:" in text and '"30 11 * * 1-5"' in text


# --- post-switch management crawl in the dev acceptance stage (ACCEPTANCE-STAGE-POST-SWITCH-CRAWL-20261126) ---

def _deploy_workflow():
    import yaml
    return yaml.safe_load((WORKFLOWS / "nonprod-deploy.yml").read_text())


def _named(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


def test_acceptance_stage_runs_existing_crawl_after_probes_without_failing_the_deploy():
    workflow = _deploy_workflow()
    job = workflow["jobs"]["dev-acceptance-stage"]
    assert job["continue-on-error"] is True
    assert set(job["needs"]) == {"deploy-dev", "coordinate-dev-release"}  # after the switch and stack check
    names = [step.get("name") for step in job["steps"]]
    checkout = _named(job, "Checkout deployed frontend for the management crawl")
    prepare = _named(job, "Prepare management crawl dependencies (no credentials)")
    stage = _named(job, "Run acceptance checks and probes")
    # checkout and install precede the lease; the crawl itself runs inside the stage step, after the probes
    assert names.index(checkout["name"]) < names.index(prepare["name"]) < \
        names.index("Acquire qualification lease and start identity-bound heartbeat") < names.index(stage["name"])
    assert checkout["with"]["repository"] == "ajoe734/execute-plans"
    assert checkout["with"]["ref"] == "${{ needs.coordinate-dev-release.outputs.frontend_sha }}"
    assert checkout["with"]["persist-credentials"] is False
    for step in (checkout, prepare, stage):
        assert step.get("continue-on-error") is True
    assert "secrets." not in json_dump(checkout) + json_dump(prepare)  # no credential before the crawl
    assert prepare["timeout-minutes"] == 20
    assert "--crawl-frontend-dir" in stage["run"] and '--crawl-prepared "${CRAWL_PREPARED}"' in stage["run"]
    assert stage["env"]["CRAWL_PREPARED"] == "${{ steps.crawl_prepare.outcome }}"
    assert stage["run"].rstrip().endswith("exit 0")
    upload = _named(job, "Upload acceptance verdict")
    assert "acceptance-crawl.json" in upload["with"]["path"] and upload["with"]["retention-days"] == 30


def test_pre_switch_gate_and_release_probes_are_unchanged():
    text = (WORKFLOWS / "nonprod-deploy.yml").read_text()
    coordinate = text.split("\n  coordinate-dev-release:\n", 1)[1].split("\n  dev-acceptance-stage:\n", 1)[0]
    assert "accept-management-hosted-production" not in coordinate and "crawl" not in coordinate.lower()
    stage = _deploy_workflow()["jobs"]["dev-acceptance-stage"]
    run = _named(stage, "Run acceptance checks and probes")["run"]
    assert "python3 scripts/dev_acceptance_stage.py run" in run and '"${lease_args[@]}"' in run
    assert text.count("ACCEPTANCE_VIEWER_CLIENT_SECRET") == 1  # viewer identity stays in the one stage step


def json_dump(value):
    import json
    return json.dumps(value, sort_keys=True)
