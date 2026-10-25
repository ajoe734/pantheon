import json
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



# --- Hosted all-12 chain segment (HOSTED-ALL12-WORKFLOW-INTEGRATION-20261023) ----------------

HOSTED = WORKFLOW["jobs"]["hosted-all12-chain-phase"]
HOSTED_STEPS = {step["name"]: step for step in HOSTED["steps"]}
HOSTED_PHASE_STEP = HOSTED_STEPS["Run requested phase through the existing stimulus parent"]
HOSTED_VALIDATE = HOSTED_STEPS["Validate protected exact dev source and hosted segment request"]["run"]
WORKFLOW_TEXT = (REPO_ROOT / ".github/workflows/nonprod-deploy.yml").read_text(encoding="utf-8")
ON_INPUTS = (WORKFLOW.get("on") or WORKFLOW.get(True))["workflow_dispatch"]["inputs"]


def _load_vm_source() -> str:
    body = HOSTED_PHASE_STEP["run"]
    start = body.index("<<'PY'\n", body.index("run_phase.py")) + len("<<'PY'\n")
    return body[start : body.index("\nPY\n", start)] + "\n"


def _load_vm_runner():
    import importlib.util
    import sys

    source = _load_vm_source()
    path = Path(tempfile.mkdtemp()) / "run_phase.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("hosted_run_phase_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_hosted_mode_is_explicit_and_mutually_exclusive() -> None:
    assert ON_INPUTS["hosted_all12_chain"]["default"] is False
    assert ON_INPUTS["chain_phase"]["options"] == [
        "A_research", "B_learning", "C_promote", "D_observe", "E_close",
    ]
    flag = "inputs.hosted_all12_chain"
    assert flag in HOSTED["if"]
    assert f"!{flag}" in WORKFLOW["jobs"]["deploy-dev"]["if"]
    assert f"!{flag}" in JOB["if"]
    assert "!inputs.isolated_provider_qualification" in WORKFLOW["jobs"]["deploy-dev"]["if"]
    assert "needs" not in HOSTED
    # The hosted job can never deploy, compensate, switch FE or restart services.
    text = yaml.safe_dump(HOSTED)
    for forbidden in ("deploy_nonprod_vm", "rollback", "compensation", "docker compose restart", "restart "):
        assert forbidden not in text
    assert WORKFLOW["concurrency"] == {
        "group": "pantheon-nonprod-deploy-${{ inputs.environment }}",
        "cancel-in-progress": False,
    }
    assert HOSTED["permissions"] == {"contents": "read", "actions": "read"}
    assert WORKFLOW_TEXT.count("uses: actions/checkout@") == 6  # inventory unchanged


def test_hosted_lease_is_qualification_per_mutating_segment_and_released_after_pause() -> None:
    acquire = HOSTED_STEPS["Acquire qualification lease and start identity-bound heartbeat"]
    assert acquire["if"] == "${{ env.MUTATING == 'true' }}"
    assert "--mode qualification" in acquire["run"] and "--mode deployment" not in acquire["run"]
    assert "heartbeat-loop" in acquire["run"] and "verify-heartbeat-identity" in acquire["run"]
    assert HOSTED["env"]["MUTATING"] == "${{ inputs.chain_phase != 'D_observe' && 'true' || 'false' }}"
    release = HOSTED_STEPS["Stop heartbeat and release the segment lease"]
    assert release["if"].startswith("${{ always()")
    # Not gated on the segment being complete: pause and failure release as well.
    assert "complete_success" not in release["run"]
    run = release["run"]
    assert run.index("verify-heartbeat-identity") < run.index("kill -TERM")
    assert run.index("kill -TERM") < run.index('dev_environment_lease.py" release')
    names = [step["name"] for step in HOSTED["steps"]]
    assert names.index(release["name"]) > names.index(HOSTED_PHASE_STEP["name"])
    assert "sleep 3600" not in yaml.safe_dump(HOSTED)  # never waits on a human


def test_hosted_credentials_stay_out_of_inputs_argv_and_uploads() -> None:
    hosted_inputs = {"chain_id", "chain_phase", "chain_input_json", "chain_prior_receipt_run_id", "hosted_all12_chain"}
    for name in hosted_inputs:
        assert not re.search(r"token|secret|password|credential", name)
    run = HOSTED_PHASE_STEP["run"]
    env = HOSTED_PHASE_STEP["env"]
    assert env["DEV_BFF_DEV_LOGIN_CLIENT_SECRET"] == "${{ secrets.DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET }}"
    # Secret values only leave the runner through the ssh stdin bundle.
    assert '< "${bundle}"' in run
    assert not re.search(r"\$\{?DEV_BFF_DEV_LOGIN_CLIENT_SECRET", run.split("<<'PY'")[0])
    assert "0o600" in run and "::add-mask::" in run
    for other in HOSTED["steps"]:
        if other is HOSTED_PHASE_STEP:
            continue
        assert "DEV_BFF_DEV_LOGIN" not in yaml.safe_dump(other)
    upload = HOSTED_STEPS["Upload hosted segment receipt"]
    assert upload["if"] == "${{ always() }}"
    assert "chain-input" not in upload["with"]["path"] and "bundle" not in upload["with"]["path"]
    assert "receipt.json" in upload["with"]["path"] and "phase.log" in upload["with"]["path"]
    guard = HOSTED_STEPS["Validate protected exact dev source and hosted segment request"]["run"]
    assert "token|secret|password|credential|authorization|mfa" in guard and '"approved"' in guard


def test_hosted_vm_runner_uses_existing_parent_node_and_pinned_offline_bootstrap() -> None:
    import re

    runner = _load_vm_runner()
    assert runner.PARENT_NODE == (
        "tests/integration/l12/test_stimulus_cross_loop_deployed_e2e.py::test_hosted_chain_phase"
    )
    run = HOSTED_PHASE_STEP["run"]
    assert "--without-pip" in _load_vm_source()
    wheels = HOSTED_STEPS["Download pinned pytest closure and pip wheel"]["run"]
    assert "dependencies/locks/root-tests.txt" in wheels and "--only-binary=:all:" in wheels
    assert "--no-index" in run
    for lib in ("jsonschema", "jsonschema-specifications", "attrs", "referencing", "rpds-py", "typing-extensions"):
        assert re.search(rf"[ ;]{re.escape(lib)}[ ;]", wheels)
    assert "--python-version 3.12" in wheels and "--abi cp312" in wheels and "host_arch" in wheels
    for forbidden in ("apt-get", "pip install --user", "sudo", "docker compose up", "docker compose restart"):
        assert forbidden not in run


def test_hosted_parent_environment_matches_the_merged_contract() -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "stimulus_parent_for_workflow_join",
        REPO_ROOT / "tests/integration/l12/test_stimulus_cross_loop_deployed_e2e.py",
    )
    parent = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = parent
    spec.loader.exec_module(parent)
    options = ON_INPUTS["chain_phase"]["options"]
    assert tuple(options) == parent.HOSTED_PHASES
    assert parent._CHAIN_ID_PATTERN.pattern == "^[a-z0-9][a-z0-9-]{7,63}$"
    assert "^[a-z0-9][a-z0-9-]{7,63}$" in HOSTED_VALIDATE
    runner = _load_vm_runner()
    parent_source = (REPO_ROOT / "tests/integration/l12/test_stimulus_cross_loop_deployed_e2e.py").read_text()
    script = HOSTED_PHASE_STEP["run"]
    for name in (
        "PANTHEON_L12_HOSTED_CHAIN", "PANTHEON_L12_STIMULUS_CROSS_LOOP_E2E",
        "PANTHEON_L12_CROSS_LOOP_RUN_ID", "PANTHEON_L12_HOSTED_CHAIN_PHASE",
        "PANTHEON_L12_HOSTED_CHAIN_INPUT", "PANTHEON_L12_HOSTED_CHAIN_PRIOR_RECEIPT",
        "PANTHEON_L12_STIMULUS_EXPECTED_SHA", "PANTHEON_L12_STIMULUS_EVIDENCE_OUTPUT",
        "PANTHEON_L12_TENANT_ID", "PANTHEON_L12_HOSTED_OWNER_TOKEN",
        "PANTHEON_L12_HOSTED_OWNER_ACTOR_ID", "PANTHEON_L12_HOSTED_OWNER_ROLES",
    ):
        assert name in parent_source and name in script
    for required in ("registry", "governance", "capital", "deployment", "runtime"):
        assert any(required.upper() in names for names, _s, _p in runner.OWNERS)


def test_hosted_vm_runner_owner_labels_are_real_compose_services() -> None:
    import re

    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    services = set(re.findall(r"^  ([a-z0-9][a-z0-9-]*):\s*$", compose, re.MULTILINE))
    runner = _load_vm_runner()
    research = [svc for names, svc, _p in runner.OWNERS if "RESEARCH" in names]
    assert research == ["research-orchestrator-svc"]
    assert "research-orchestrator-svc" in services


def _fake_docker(
    runner,
    tmp_path,
    *,
    head="a" * 40,
    dirty="",
    capital="cap-token-123456",
    working_dir=None,
    heads=None,
    dirties=None,
):
    calls: list[list[str]] = []
    state = {"pytest_env": None}
    bff_working_dir = str(tmp_path) if working_dir is None else str(working_dir)
    containers = {"operator-bff": "cid-bff", "training-session-preview-worker": "cid-train"}
    for _names, service, _port in runner.OWNERS:
        containers.setdefault(service, f"cid-{service}")
    for _env, service, _src, _fb in runner.SERVICE_CREDENTIALS:
        containers.setdefault(service, f"cid-{service}")
    port_for = {cid: 20000 + i for i, cid in enumerate(containers.values())}

    def fake_run(argv, **_kw):
        calls.append(list(argv))
        out = ""
        if argv[:2] == ["git", "-C"] and "rev-parse" in argv:
            out = (heads or {}).get(argv[2], head)
        elif argv[:2] == ["git", "-C"] and "status" in argv:
            out = (dirties or {}).get(argv[2], dirty)
        elif argv[:2] == ["docker", "ps"]:
            service = [a for a in argv if a.startswith("label=com.docker.compose.service=")][0].split("=", 2)[2]
            out = containers.get(service, "")
        elif argv[:2] == ["docker", "port"]:
            out = f"0.0.0.0:{port_for[argv[2]]}\n[::]:{port_for[argv[2]]}\n"
        elif argv[:2] == ["docker", "inspect"] and "working_dir" in argv[-1]:
            out = bff_working_dir
        elif argv[:2] == ["docker", "inspect"]:
            out = "/srv/dev-root/docker-compose.yml,/srv/dev-root/override.yml"
        elif argv[:2] == ["docker", "exec"] and "printenv" in argv:
            out = {
                "AGORA_HANDOFF_SERVICE_TOKEN": "agora-secret-1234",
                "POLICY_LEARNING_SERVICE_TOKEN": "policy-secret-1234",
                "CONSULTATION_SERVICE_TOKEN": "consult-secret-1234",
                "PANTHEON_PERSONA_SERVICE_TOKEN": "persona-secret-1234",
                "PANTHEON_PERSONA_SERVICE_ACTOR_ID": "persona-writer-actor",
                "TRAINING_SESSION_WORKER_TOKEN_FILE": "/run/principals/TRAINING",
            }.get(argv[-1], "")
        elif argv[:2] == ["docker", "exec"] and argv[2] == "cid-train" and "cat" in argv:
            out = "training-secret-1234\n"
        elif argv[:2] == ["docker", "exec"] and "-i" in argv:
            raise AssertionError("hosted runner must not mint a Capital service token")
        return type("P", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    def fake_subprocess_run(argv, **kw):
        state["pytest_env"] = kw["env"]
        state["cwd"] = kw["cwd"]
        state["argv"] = argv
        Path(kw["env"]["PANTHEON_L12_STIMULUS_EVIDENCE_OUTPUT"]).write_text(
            json.dumps({"hosted": {"chain_id": kw["env"]["PANTHEON_L12_CROSS_LOOP_RUN_ID"], "phase_status": "paused"}}),
            encoding="utf-8",
        )
        leaked = "stdout executor-token-abcdef agora-secret-1234"
        return type("P", (), {"returncode": 0, "stdout": leaked, "stderr": ""})()

    return calls, state, fake_run, fake_subprocess_run



def test_hosted_vm_runner_assembles_private_environment_and_redacts(monkeypatch, tmp_path: Path) -> None:
    runner = _load_vm_runner()
    calls, state, fake_run, fake_sub = _fake_docker(runner, tmp_path)
    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(runner.subprocess, "run", fake_sub)
    args = type("A", (), dict(
        scratch=str(tmp_path), worktree=str(tmp_path), expected_sha="a" * 40,
        chain_id="hosted-all12-chain-0001", phase="C_promote", timeout=60,
    ))()
    bundle = {"executor_token": "executor-token-abcdef", "actor_id": "human-1", "roles": ["operator"], "tenant_id": "tenant-dev"}
    (tmp_path / "prior-receipt.json").write_text("{}", encoding="utf-8")
    assert runner.execute(args, bundle) == 0
    env = state["pytest_env"]
    assert env["PANTHEON_L12_HOSTED_CHAIN"] == "1" and env["PANTHEON_L12_STIMULUS_CROSS_LOOP_E2E"] == "1"
    assert env["PANTHEON_L12_HOSTED_CHAIN_PHASE"] == "C_promote"
    assert env["PANTHEON_L12_TENANT_ID"] == "tenant-dev"
    assert env["PANTHEON_L12_SOURCE_READER_TOKEN"] == "executor-token-abcdef"
    assert env["PANTHEON_L12_SOURCE_READER_TENANT_ID"] == "tenant-dev"
    assert env["PANTHEON_L12_STIMULUS_EXPECTED_SHA"] == "a" * 40
    assert env["PANTHEON_L12_HOSTED_CHAIN_PRIOR_RECEIPT"].endswith("prior-receipt.json")
    assert env["PANTHEON_L12_COMPOSE_FILES"] == "/srv/dev-root/docker-compose.yml:/srv/dev-root/override.yml"
    assert env["PANTHEON_L12_COMPOSE_PROJECT"] == "pantheon"
    # Ports are the resolved published ports, not defaults.
    assert env["PANTHEON_L12_REGISTRY_URL"].startswith("http://127.0.0.1:2")
    assert env["PANTHEON_L12_SOURCE_URL"] == env["PANTHEON_L12_SOURCE_INGEST_URL"]
    assert env["PANTHEON_L12_TRAINING_TOKEN"] == "training-secret-1234"
    assert "PANTHEON_L12_CAPITAL_TOKEN" not in env
    assert env["PANTHEON_L12_PERSONA_SERVICE_TOKEN"] == "persona-secret-1234"
    assert env["PANTHEON_L12_PERSONA_SERVICE_ACTOR_ID"] == "persona-writer-actor"
    assert state["argv"][-1] == runner.PARENT_NODE and state["cwd"] == str(tmp_path)
    log = (tmp_path / "phase.log").read_text(encoding="utf-8")
    for secret in ("executor-token-abcdef", "agora-secret-1234", "persona-secret-1234"):
        assert secret not in log
    meta = json.loads((tmp_path / "segment-meta.json").read_text(encoding="utf-8"))
    assert meta["pytest_exit"] == 0 and meta["capital_credential"] == "executor_session_token_no_service_token"
    assert "executor-token-abcdef" not in json.dumps(meta)
    assert all("secret" not in " ".join(call) for call in calls)  # no credential in any argv


def _compose_files_with_label(runner, monkeypatch, label):
    fake = type("P", (), {"returncode": 0, "stdout": label, "stderr": ""})()
    monkeypatch.setattr(runner, "run", lambda argv, **_kw: fake)
    return runner.compose_files("cid-bff")


def test_hosted_compose_files_drop_only_missing_restore_override(monkeypatch, tmp_path: Path) -> None:
    runner = _load_vm_runner()
    base = tmp_path / "docker-compose.yml"
    extra = tmp_path / "override.yml"
    base.write_text("services: {}\n", encoding="utf-8")
    extra.write_text("services: {}\n", encoding="utf-8")
    restore = tmp_path / "images" / ".restore-x1nvgllk" / "images.json"
    label = ",".join([str(base), str(restore), str(extra)])
    assert _compose_files_with_label(runner, monkeypatch, label) == f"{base}:{extra}"
    # A still-present restore override is a real input and is retained.
    restore.parent.mkdir(parents=True)
    restore.write_text("{}", encoding="utf-8")
    assert str(restore) in _compose_files_with_label(runner, monkeypatch, label)


def test_hosted_compose_files_keep_missing_persistent_input_visible(monkeypatch, tmp_path: Path) -> None:
    runner = _load_vm_runner()
    missing = tmp_path / "gone-override.yml"
    restore = tmp_path / "images" / ".restore-abc" / "images.json"
    out = _compose_files_with_label(runner, monkeypatch, f"{missing},{restore}")
    assert out == str(missing)  # kept, so docker still fails visibly
    assert _compose_files_with_label(runner, monkeypatch, str(restore)) == ""
    # Only the exact restore image-override shape is dropped; other absent
    # files under a .restore-* segment stay visible.
    others = [
        tmp_path / ".restore-abc" / "docker-compose.yml",
        tmp_path / ".restore-abc" / "images.json",
        tmp_path / "images" / ".restore-abc" / "config.yml",
        tmp_path / "images" / ".restore-abc" / "nested" / "images.json",
    ]
    for other in others:
        assert _compose_files_with_label(runner, monkeypatch, str(other)) == str(other)


def test_hosted_vm_runner_fails_without_any_compose_base(monkeypatch, tmp_path: Path) -> None:
    runner = _load_vm_runner()
    _calls, state, fake_run, fake_sub = _fake_docker(runner, tmp_path)
    restore = str(tmp_path / "images" / ".restore-q" / "images.json")
    inner = fake_run

    def run_no_base(argv, **kw):
        if argv[:2] == ["docker", "inspect"]:
            return type("P", (), {"returncode": 0, "stdout": restore, "stderr": ""})()
        return inner(argv, **kw)

    monkeypatch.setattr(runner, "run", run_no_base)
    monkeypatch.setattr(runner.subprocess, "run", fake_sub)
    args = type("A", (), dict(
        scratch=str(tmp_path), worktree=str(tmp_path), expected_sha="a" * 40,
        chain_id="hosted-all12-chain-0001", phase="C_promote", timeout=60,
    ))()
    bundle = {"executor_token": "executor-token-abcdef", "actor_id": "h", "roles": [], "tenant_id": "t"}
    assert runner.execute(args, bundle) == 2
    assert state["pytest_env"] is None


def test_hosted_vm_runner_uses_accepted_bff_compose_working_dir_and_covers_root_served(
    monkeypatch, tmp_path: Path
) -> None:
    runner = _load_vm_runner()
    root_sha = "55b67c49ed7b0d036df0befd72a4813404ee76db"
    bff_sha = "4289bcb686050131522f2318a0ebb05b8d4a8886"
    dev_root = str(tmp_path / "dev-root")
    dev_bff = str(tmp_path / "dev-bff")
    heads = {dev_root: root_sha, dev_bff: bff_sha}
    bundle = {
        "executor_token": "executor-token-abcdef",
        "actor_id": "human-1",
        "roles": ["operator"],
        "tenant_id": "tenant-dev",
    }

    # Reproduce run 40203361162 source split: args.worktree (dev-root) is at 55b67c49...
    # while operator-bff compose working_dir (dev-bff) is at accepted 4289bcb6...
    calls, state, fake_run, fake_sub = _fake_docker(
        runner, tmp_path, working_dir=dev_bff, heads=heads, dirties={dev_root: "", dev_bff: ""}
    )
    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(runner.subprocess, "run", fake_sub)
    args = type("A", (), dict(
        scratch=str(tmp_path), worktree=dev_root, expected_sha=bff_sha,
        chain_id="h12-01a11-20261023-9aeb", phase="A_research", timeout=60,
    ))()
    assert runner.execute(args, bundle) == 0
    assert state["cwd"] == dev_bff
    assert state["pytest_env"]["PANTHEON_L12_STIMULUS_EXPECTED_SHA"] == bff_sha
    meta = json.loads((tmp_path / "segment-meta.json").read_text(encoding="utf-8"))
    assert meta["managed_checkout"] == {
        "head": bff_sha,
        "expected": bff_sha,
        "clean": True,
        "worktree": dev_bff,
    }
    git_worktrees = [c[2] for c in calls if c[:2] == ["git", "-C"]]
    assert git_worktrees == [dev_bff, dev_bff]

    # Root-served case (both explicit compose working_dir == dev-root and empty label fallback).
    for label_dir in (dev_root, ""):
        _calls, state_root, fake_run_root, fake_sub_root = _fake_docker(
            runner, tmp_path, working_dir=label_dir, heads=heads, dirties={dev_root: "", dev_bff: ""}
        )
        monkeypatch.setattr(runner, "run", fake_run_root)
        monkeypatch.setattr(runner.subprocess, "run", fake_sub_root)
        args_root = type("A", (), dict(
            scratch=str(tmp_path), worktree=dev_root, expected_sha=root_sha,
            chain_id="h12-01a11-20261023-9aeb", phase="A_research", timeout=60,
        ))()
        assert runner.execute(args_root, bundle) == 0
        assert state_root["cwd"] == dev_root
        meta_root = json.loads((tmp_path / "segment-meta.json").read_text(encoding="utf-8"))
        assert meta_root["managed_checkout"] == {
            "head": root_sha,
            "expected": root_sha,
            "clean": True,
            "worktree": dev_root,
        }


def test_hosted_vm_runner_refuses_dirty_or_other_checkout(monkeypatch, tmp_path: Path) -> None:
    runner = _load_vm_runner()
    root_sha = "55b67c49ed7b0d036df0befd72a4813404ee76db"
    bff_sha = "4289bcb686050131522f2318a0ebb05b8d4a8886"
    dev_root = str(tmp_path / "dev-root")
    dev_bff = str(tmp_path / "dev-bff")
    for head, dirty in (("b" * 40, ""), ("a" * 40, " M file.py")):
        _calls, state, fake_run, fake_sub = _fake_docker(runner, tmp_path, head=head, dirty=dirty)
        monkeypatch.setattr(runner, "run", fake_run)
        monkeypatch.setattr(runner.subprocess, "run", fake_sub)
        args = type("A", (), dict(
            scratch=str(tmp_path), worktree=str(tmp_path), expected_sha="a" * 40,
            chain_id="hosted-all12-chain-0001", phase="A_research", timeout=60,
        ))()
        assert runner.execute(args, {"executor_token": "x" * 12, "tenant_id": "t"}) == 2
        assert state["pytest_env"] is None  # the parent is never launched

    # Never relabel 55b67c49... as 4289bcb6..., and refuse dirty resolved BFF checkout even if root is clean.
    for heads, dirties in (
        ({dev_root: root_sha, dev_bff: root_sha}, {dev_root: "", dev_bff: ""}),
        ({dev_root: bff_sha, dev_bff: bff_sha}, {dev_root: "", dev_bff: " M services/bff/app.py"}),
    ):
        _calls, state, fake_run, fake_sub = _fake_docker(
            runner, tmp_path, working_dir=dev_bff, heads=heads, dirties=dirties
        )
        monkeypatch.setattr(runner, "run", fake_run)
        monkeypatch.setattr(runner.subprocess, "run", fake_sub)
        args = type("A", (), dict(
            scratch=str(tmp_path), worktree=dev_root, expected_sha=bff_sha,
            chain_id="h12-01a11-20261023-9aeb", phase="A_research", timeout=60,
        ))()
        assert runner.execute(args, {"executor_token": "x" * 12, "tenant_id": "t"}) == 2
        assert state["pytest_env"] is None
        meta = json.loads((tmp_path / "segment-meta.json").read_text(encoding="utf-8"))
        assert meta["error"] == "managed checkout is not the exact accepted clean source"
        assert meta["managed_checkout"]["expected"] == bff_sha
        assert meta["managed_checkout"]["head"] == heads[dev_bff]
        assert meta["managed_checkout"]["worktree"] == dev_bff


def test_hosted_validation_guard_script(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    subprocess.run(["git", "init", "-q", "-b", "dev", str(remote)], check=True)
    for key, value in (("user.name", "t"), ("user.email", "t@t")):
        subprocess.run(["git", "-C", str(remote), "config", key, value], check=True)
    (remote / "f").write_text("1")
    subprocess.run(["git", "-C", str(remote), "add", "."], check=True)
    subprocess.run(["git", "-C", str(remote), "commit", "-qm", "c1"], check=True)
    sha = subprocess.check_output(["git", "-C", str(remote), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "-C", str(remote), "update-ref", "refs/remotes/origin/dev", sha], check=True)
    runner_temp = tmp_path / "temp"
    runner_temp.mkdir()
    base = {
        **os.environ, "GITHUB_REF": "refs/heads/dev", "GITHUB_SHA": sha, "TARGET_COMPONENT": "auto",
        "ALLOW_DIRTY": "false", "DEV_AUTH_PROFILE": "strict", "ISOLATED_REQUESTED": "false",
        "CHAIN_ID": "hosted-all12-chain-0001", "CHAIN_PHASE": "A_research", "CHAIN_INPUT_JSON": "",
        "PRIOR_RECEIPT_RUN_ID": "", "RUNNER_TEMP": str(runner_temp), "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_OUTPUT": str(tmp_path / "out"),
    }

    def run(**override):
        return subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", HOSTED_VALIDATE],
            cwd=remote, env={**base, **override}, capture_output=True, text=True,
        )

    good = run(CHAIN_INPUT_JSON='{"decisions": {"H1": "dec-1"}, "predecessors": {"registry_id": "reg-1"}}')
    assert good.returncode == 0, good.stderr
    packet = json.loads((runner_temp / "hosted-all12-1-1/chain-input.json").read_text())
    assert packet == {
        "chain_id": "hosted-all12-chain-0001", "phase": "A_research",
        "decisions": {"H1": "dec-1"}, "predecessors": {"registry_id": "reg-1"},
    }
    assert run(GITHUB_SHA="c" * 40).returncode != 0
    assert run(ISOLATED_REQUESTED="true").returncode != 0
    assert run(ALLOW_DIRTY="true").returncode != 0
    assert run(DEV_AUTH_PROFILE="permissive-stub").returncode != 0
    assert run(CHAIN_ID="Short").returncode != 0
    assert run(CHAIN_PHASE="F_extra").returncode != 0
    assert run(PRIOR_RECEIPT_RUN_ID="abc").returncode != 0
    for bad in ('{"decisions": {"api_token": "x"}}', '{"decisions": {"approved": true}}', '{"other": 1}'):
        assert run(CHAIN_INPUT_JSON=bad).returncode != 0, bad
