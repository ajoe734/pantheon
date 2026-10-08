import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load(
    (REPO_ROOT / ".github/workflows/nonprod-deploy.yml").read_text(encoding="utf-8")
)
COMPOSE_TEXT = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
JOB = WORKFLOW["jobs"]["isolated-l12-provider-qualification"]
STEPS = {step["name"]: step for step in JOB["steps"]}
RUN_STEP = STEPS["Run original isolated L1-L12 stimulus gate"]


def test_modes_are_separated_from_root_deploy() -> None:
    flag = "inputs.isolated_provider_qualification"
    assert flag in JOB["if"]
    assert f"!{flag}" in WORKFLOW["jobs"]["deploy-dev"]["if"]
    # Release coordination only follows a verified deploy-dev pair.
    assert WORKFLOW["jobs"]["coordinate-dev-release"]["needs"] == ["deploy-dev"]
    assert "needs" not in JOB
    text = yaml.safe_dump(JOB)
    for forbidden in ("deploy_nonprod_vm", "DEV_DEPLOY_SSH", "frontend", "rollback"):
        assert forbidden not in text


def test_exact_protected_dev_source_is_required() -> None:
    script = STEPS["Validate protected exact dev source"]["run"]
    assert 'refs/remotes/origin/dev' in script and 'refs/heads/dev' in script
    assert '"${GITHUB_SHA}" != "${sha}"' in script
    assert JOB["environment"] == "dev"


def test_original_driver_owns_lease_with_environment_secrets() -> None:
    run = RUN_STEP["run"]
    assert "scripts/run_isolated_l12_runtime_e2e.py" in run
    assert "--provision-services --stimulus-gate" in run
    assert "dev_environment_lease.py" not in yaml.safe_dump(JOB)  # no outer lease
    env = RUN_STEP["env"]
    assert env["PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN"] == (
        "${{ secrets.DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN }}"
    )
    assert env["PANTHEON_ENVIRONMENT_LEASE_TOKEN"] == "${{ secrets.COORDINATION_REPO_TOKEN }}"
    assert "secrets." not in yaml.safe_dump({k: v for k, v in JOB.items() if k != "steps"})
    # The credential never appears in argv: only the credential-free batch is exported.
    assert not re.search(r"\$\{?PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN", run)


def test_bootstrap_runs_before_gate_and_project_is_unique_and_real_provider() -> None:
    run = RUN_STEP["run"]
    assert run.index("openclaw_isolated_model_bootstrap.py") < run.index(
        "run_isolated_l12_runtime_e2e.py"
    )
    assert "l12q${GITHUB_RUN_ID}x${GITHUB_RUN_ATTEMPT}" in run
    assert "GITHUB_RUN_ID % 10" in run
    for weakening in ("stub", "skip", "mock", "--preserve-provisioned-stack"):
        assert weakening not in run.lower()


def test_report_is_uploaded_even_on_failure() -> None:
    upload = STEPS["Upload qualification report"]
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["if-no-files-found"] == "warn"


def test_gateway_bootstrap_is_opt_in_and_fail_closed() -> None:
    assert (
        "PANTHEON_OPENCLAW_ISOLATED_MODEL_BATCH: "
        "${PANTHEON_OPENCLAW_ISOLATED_MODEL_BATCH:-}"
    ) in COMPOSE_TEXT
    start = COMPOSE_TEXT.index('if [ -n \\"$$PANTHEON_OPENCLAW_ISOLATED_MODEL_BATCH\\" ]')
    startup = COMPOSE_TEXT[start : COMPOSE_TEXT.index("exec node dist/index.js gateway", start)]
    assert '[ -n \\"$$CLAUDE_CODE_OAUTH_TOKEN\\" ] || exit 1' in startup
    assert "|| exit 1; fi" in startup


def test_payload_fetch_does_not_add_checkout_action_or_leak_token() -> None:
    fetch = STEPS["Fetch requested payload"]
    assert "uses" not in fetch
    assert "GH_TOKEN" in fetch["env"] and "GIT_CONFIG_VALUE_0" not in fetch["env"]
    assert "x-access-token:%s" in fetch["run"] and "git submodule update" in fetch["run"]


def test_preflight_verifies_runner_disk_and_memory_without_broad_deletion() -> None:
    preflight = STEPS["Preflight runner resources for full service set"]
    run = preflight["run"]
    assert "df -h" in run and "free -m" in run
    assert "avail_kb" in run and "avail_mem_kb" in run
    assert "rm -rf" not in run
