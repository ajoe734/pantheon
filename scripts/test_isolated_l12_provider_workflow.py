import os
import re
import subprocess
import tempfile
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
    assert env["PANTHEON_OPENCLAW_ANTHROPIC_API_KEY"] == (
        "${{ secrets.DEV_OPENCLAW_ANTHROPIC_API_KEY }}"
    )
    assert env["PANTHEON_OPENCLAW_ANTHROPIC_WORKSPACE_ID"] == (
        "${{ vars.DEV_OPENCLAW_ANTHROPIC_WORKSPACE_ID }}"
    )
    assert env["PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN"] == (
        "${{ secrets.DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN }}"
    )
    assert env["PANTHEON_OPENCLAW_OPENAI_OAUTH_TOKEN"] == (
        "${{ secrets.DEV_OPENCLAW_OPENAI_OAUTH_TOKEN }}"
    )
    assert "PANTHEON_OPENCLAW_OPENAI_API_KEY" not in env
    assert env["PANTHEON_ENVIRONMENT_LEASE_TOKEN"] == "${{ secrets.COORDINATION_REPO_TOKEN }}"
    assert "secrets." not in yaml.safe_dump({k: v for k, v in JOB.items() if k != "steps"})
    # Credentials never appear in argv: only the credential-free batch is exported.
    assert not re.search(
        r"\$\{?PANTHEON_OPENCLAW_(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|ANTHROPIC_WORKSPACE_ID|OPENAI_OAUTH_TOKEN)",
        run,
    )


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
    assert "ANTHROPIC_API_KEY: ${PANTHEON_OPENCLAW_ANTHROPIC_API_KEY:-}" in COMPOSE_TEXT
    assert "ANTHROPIC_WORKSPACE_ID: ${PANTHEON_OPENCLAW_ANTHROPIC_WORKSPACE_ID:-}" in COMPOSE_TEXT
    assert "OPENCLAW_OPENAI_OAUTH_TOKEN: ${PANTHEON_OPENCLAW_OPENAI_OAUTH_TOKEN:-}" in COMPOSE_TEXT
    assert "OPENAI_API_KEY" not in COMPOSE_TEXT
    assert "CODEX_HOME:" not in COMPOSE_TEXT
    start = COMPOSE_TEXT.index('if [ -n \\"$$PANTHEON_OPENCLAW_ISOLATED_MODEL_BATCH\\" ]')
    startup = COMPOSE_TEXT[start : COMPOSE_TEXT.index("exec node dist/index.js gateway", start)]
    assert (
        '[ -n \\"$$ANTHROPIC_API_KEY\\" ] && [ -n \\"$$ANTHROPIC_WORKSPACE_ID\\" ] '
        '&& [ -n \\"$$OPENCLAW_OPENAI_OAUTH_TOKEN\\" ] || exit 1'
    ) in startup
    assert "loadAuthProfileStoreForSecretsRuntime" not in startup
    assert "saveAuthProfileStore" not in startup
    assert 'config set --batch-json \\"$$PANTHEON_OPENCLAW_ISOLATED_MODEL_BATCH\\"' in startup
    assert "|| exit 1; fi" in startup


def test_formal_dev_deploy_and_rollback_steps_bind_anthropic_credentials() -> None:
    matched_steps = 0
    for job_name in ("deploy-dev", "coordinate-dev-release"):
        for step in WORKFLOW["jobs"][job_name]["steps"]:
            env = step.get("env") or {}
            if "DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN" not in env:
                continue
            matched_steps += 1
            assert env.get("DEV_OPENCLAW_ANTHROPIC_API_KEY") == (
                "${{ secrets.DEV_OPENCLAW_ANTHROPIC_API_KEY }}"
            )
            assert env.get("DEV_OPENCLAW_ANTHROPIC_WORKSPACE_ID") == (
                "${{ vars.DEV_OPENCLAW_ANTHROPIC_WORKSPACE_ID }}"
            )
            assert env.get("DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN") == (
                "${{ secrets.DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN }}"
            )
    assert matched_steps == 7


def test_payload_fetch_does_not_add_checkout_action_or_leak_token() -> None:
    fetch = STEPS["Fetch requested payload"]
    assert "uses" not in fetch
    assert "GH_TOKEN" in fetch["env"] and "GIT_CONFIG_VALUE_0" not in fetch["env"]
    assert "x-access-token:%s" in fetch["run"] and "git submodule update" in fetch["run"]
    assert "FETCH_HEAD" not in fetch["run"]
    assert 'target_sha="$(git rev-parse --verify "${TARGET_REF}^{commit}")"' in fetch["run"]
    assert 'git checkout -q --detach "${target_sha}"' in fetch["run"]


def test_tempgit_multipayload_fetch_and_mismatch_guard() -> None:
    val_script = STEPS["Validate protected exact dev source"]["run"]
    with tempfile.TemporaryDirectory() as tmpdir:
        remote = os.path.join(tmpdir, "remote")
        subprocess.run(["git", "init", "-b", "dev", remote], check=True, capture_output=True)
        subprocess.run(["git", "-C", remote, "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", remote, "config", "user.email", "test@test"], check=True)

        with open(os.path.join(remote, "f.txt"), "w") as f:
            f.write("c1")
        subprocess.run(["git", "-C", remote, "add", "."], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-m", "c1"], check=True)
        old_sha = subprocess.check_output(["git", "-C", remote, "rev-parse", "HEAD"]).decode().strip()

        with open(os.path.join(remote, "f.txt"), "w") as f:
            f.write("c2")
        subprocess.run(["git", "-C", remote, "add", "."], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-m", "c2"], check=True)
        dev_sha = subprocess.check_output(["git", "-C", remote, "rev-parse", "HEAD"]).decode().strip()

        local = os.path.join(tmpdir, "local")
        subprocess.run(["git", "init", "-q", local], check=True)
        subprocess.run(["git", "-C", local, "remote", "add", "origin", remote], check=True)

        # Multipayload fetch: origin/dev and requested oldsha
        subprocess.run(
            ["git", "-C", local, "fetch", "-q", "origin", "+refs/heads/dev:refs/remotes/origin/dev", old_sha],
            check=True,
        )

        # Verify FETCH_HEAD first entry is origin/dev (the override footgun)
        with open(os.path.join(local, ".git", "FETCH_HEAD"), encoding="utf-8") as f:
            first_line = f.readline()
            assert dev_sha in first_line

        # Exact requested commit resolution and checkout
        target_sha = subprocess.check_output(
            ["git", "-C", local, "rev-parse", "--verify", f"{old_sha}^{{commit}}"]
        ).decode().strip()
        subprocess.run(["git", "-C", local, "checkout", "-q", "--detach", target_sha], check=True)
        assert subprocess.check_output(["git", "-C", local, "rev-parse", "HEAD"]).decode().strip() == old_sha

        # Run exact workflow validation script: mismatch rejected before resources or lease
        env = os.environ.copy()
        env.update({
            "GITHUB_REF": "refs/heads/dev",
            "GITHUB_SHA": dev_sha,
            "TARGET_COMPONENT": "auto",
            "ALLOW_DIRTY": "false",
            "DEV_AUTH_PROFILE": "strict",
        })
        mismatch_res = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", val_script],
            cwd=local,
            env=env,
            capture_output=True,
            text=True,
        )
        assert mismatch_res.returncode != 0
        assert "Isolated qualification must run from the exact current Pantheon dev commit" in mismatch_res.stderr

        # Empty input: workflow SHA accepted only when protected current dev
        subprocess.run(["git", "-C", local, "checkout", "-q", "--detach", dev_sha], check=True)
        pass_res = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", val_script],
            cwd=local,
            env=env,
            capture_output=True,
            text=True,
        )
        assert pass_res.returncode == 0

        # Stale workflow SHA rejected even if checked out
        env["GITHUB_SHA"] = old_sha
        stale_res = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", val_script],
            cwd=local,
            env=env,
            capture_output=True,
            text=True,
        )
        assert stale_res.returncode != 0


def test_preflight_verifies_runner_disk_and_memory_without_broad_deletion() -> None:
    preflight = STEPS["Preflight runner resources for full service set"]
    run = preflight["run"]
    assert "df -h" in run and "free -m" in run
    assert "avail_kb" in run and "avail_mem_kb" in run
    assert "rm -rf" not in run


def test_harness_redacts_openclaw_secrets_and_records_non_secret_auth_posture_and_teardown(
    monkeypatch, tmp_path: Path
) -> None:
    import json
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import run_isolated_l12_runtime_e2e as harness  # noqa: E402

    compose_env = {
        "PANTHEON_OPENCLAW_ISOLATED_MODEL_BATCH": '[{"path":"models.providers.openai"}]',
        "PANTHEON_OPENCLAW_ANTHROPIC_API_KEY": "sk-ant-secret-sentinel-value",
        "PANTHEON_OPENCLAW_ANTHROPIC_WORKSPACE_ID": "wrkspc_non_secret_id",
        "PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN": "claude-oauth-secret-sentinel",
        "PANTHEON_OPENCLAW_OPENAI_OAUTH_TOKEN": "openai-oauth-secret-sentinel",
    }
    posture = harness._openclaw_isolated_auth_posture(compose_env)
    assert posture == {
        "isolated_model_batch_configured": True,
        "anthropic_api_key_present": True,
        "anthropic_workspace_present": True,
        "claude_code_oauth_token_present": True,
        "openai_oauth_token_present": True,
    }
    assert "sentinel" not in json.dumps(posture)

    row = json.dumps({"Service": "openclaw-gateway", "State": "running", "Health": "unhealthy"})
    err = "ERROR gateway failed with openai-oauth-secret-sentinel and sk-ant-secret-sentinel-value"
    monkeypatch.setattr(
        harness.subprocess,
        "run",
        lambda cmd, **_kw: type("P", (), {"returncode": 0, "stdout": row if "ps" in cmd else err, "stderr": ""})(),
    )
    diag_dir = tmp_path / "diagnostics"
    harness._capture_failure_diagnostics("l12q", ["docker-compose.yml"], compose_env, diag_dir)
    for name in ("compose-ps.txt", "logs-openclaw-gateway.txt", "service-error-lines.txt"):
        content = (diag_dir / name).read_text(encoding="utf-8")
        assert "openai-oauth-secret-sentinel" not in content
        assert "sk-ant-secret-sentinel-value" not in content
    assert "[REDACTED:PANTHEON_OPENCLAW_OPENAI_OAUTH_TOKEN]" in (diag_dir / "service-error-lines.txt").read_text(encoding="utf-8")

