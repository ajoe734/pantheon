from __future__ import annotations

import os
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

@pytest.fixture
def fresh_bootstrap_database():
    try:
        import psycopg
    except ModuleNotFoundError:
        pytest.skip("psycopg is not installed in the test environment")
    if not os.getenv("TEST_DATABASE_ADMIN_URL"):
        pytest.skip("TEST_DATABASE_ADMIN_URL is not set")
    from services.trade_journey.test_projection_migration import (
        fresh_bootstrap_database as _upstream,
    )
    yield from _upstream()

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"
DUMMY_SHA = "249cd9c03675e2566a3d5f1e6a4be06af405da45"


@pytest.mark.parametrize("migration_failure", [False, True])
def test_projection_bootstrap_precedes_runtime_and_fails_closed(
    fresh_bootstrap_database, migration_failure, tmp_path
):
    """Execute the deployment function/CLI on PostgreSQL without contacting a VM."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    migration, runtime = fresh_bootstrap_database
    info = conninfo_to_dict(migration)
    if migration_failure:
        with psycopg.connect(migration) as conn:
            conn.execute("CREATE SCHEMA trade_journey_projection")
            conn.execute("CREATE VIEW trade_journey_projection.event_receipts AS SELECT 1 AS sentinel")
    deploy = DEPLOY_SCRIPT.read_text()
    function = deploy.split("bootstrap_dev_lifecycle_projection() {", 1)[1].split("\n}\n", 1)[0]
    startup = "bootstrap_dev_lifecycle_projection || rollback_dev_bff_on_failure \"projection_bootstrap\""
    assert deploy.count(startup) == 2
    for branch in ("root)", "bff)"):
        # Both deployment branches gate startup on the same migration function.
        block = deploy[deploy.rindex("  " + branch):]
        block = block[:block.index("    ;;\n")]
        assert block.index("seal_dev_candidate_images ||") < block.index(startup)
        assert block.index(startup) < block.index("# Phase 3:")
        assert "wait_for_exact_bff_lifecycle_readiness" in block
    config = tmp_path / "compose.json"
    config.write_text(json.dumps({"services": {"postgres": {"environment": {
        "POSTGRES_USER": info["user"], "POSTGRES_PASSWORD": info["password"],
        "POSTGRES_DB": info["dbname"],
    }}}}))
    script = tmp_path / "bootstrap.sh"
    script.write_text('''set -euo pipefail
docker() {
  case "$*" in
    'compose -p pantheon -f docker-compose.yml up -d --wait postgres') return 0 ;;
    'compose -p pantheon -f docker-compose.yml --profile core config --format json postgres') cat "$TEST_COMPOSE_CONFIG" ;;
    *) return 89 ;;
  esac
}
run_dev_candidate_compose() {
  [[ "$1 $2 $3 $4" == 'run --rm --no-deps -T' ]] || return 90
  shift 4
  [[ "$1 $2 $3" == '--entrypoint python loop-run-projector-scheduler' ]] || return 91
  shift 3
  "$TEST_PYTHON" "$@"
}
rollback_dev_bff_on_failure() { echo "rollback:$1"; exit 42; }
bootstrap_dev_lifecycle_projection() {''' + function + '\n}\n' + startup + '''
"$TEST_PYTHON" -c 'from services.trade_journey.lifecycle_projector import _configured_relational_projector; assert _configured_relational_projector().checkpoint == 0; print("runtime-started")'
''')
    env = {**os.environ, "TEST_PYTHON": sys.executable, "TEST_COMPOSE_CONFIG": str(config),
           "POSTGRES_USER": info["user"], "POSTGRES_PASSWORD": info["password"],
           "POSTGRES_DB": info["dbname"], "PGHOSTADDR": info["host"], "PGPORT": info["port"],
           "LIFECYCLE_PROJECTOR_PROJECTION_DSN": runtime,
           "LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA": "trade_journey_projection",
           "LIFECYCLE_PROJECTOR_WRITER_BACKEND": "relational"}
    for _ in range(2):
        result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=45)
        if migration_failure:
            assert result.returncode == 42, result.stderr
            assert "rollback:projection_bootstrap" in result.stdout
            assert "runtime-started" not in result.stdout
        else:
            assert result.returncode == 0, result.stderr
            assert "runtime-started" in result.stdout

@pytest.mark.parametrize("profiles", [None, "", "workers"])
def test_projection_bootstrap_renders_profiled_postgres_without_other_credentials(profiles):
    """Run the exact config command against real Compose; no containers start."""
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required for the render contract")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=15)
    if version.returncode:
        pytest.skip("Docker Compose plugin is unavailable")
    function = DEPLOY_SCRIPT.read_text().split("bootstrap_dev_lifecycle_projection() {", 1)[1].split("\n}\n", 1)[0]
    line = next(line for line in function.splitlines() if "config --format json" in line)
    command = shlex.split(line.split("|", 1)[0])
    command[2:2] = ["--env-file", "/dev/null"]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "POSTGRES_PASSWORD": "isolated-render-test"}
    if profiles is not None:
        env["COMPOSE_PROFILES"] = profiles
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert set(services) == {"postgres"}
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"] == "isolated-render-test"


@pytest.mark.parametrize("outer,expected", [(30, "30"), (90, "90"), (120, "120"), (1800, "120"), (3600, "120")])
def test_bounded_source_rpc_budget_is_exported_and_capped(outer, expected):
    result = _source_profile_budget(outer=outer)
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(f"budget={expected}\n")


def test_default_source_profile_keeps_ordinary_rpc_budget():
    result = _source_profile_budget(bounded=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("budget=unset\n")


@pytest.mark.parametrize("override,exit_code", [({"SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS": "29"}, 37), ({"PANTHEON_EXTERNAL_EGRESS": "deny"}, 37), ({"PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": "openapi.twse.com.tw"}, 1)])
def test_source_rpc_budget_does_not_bypass_profile_gates(override, exit_code):
    result = _source_profile_budget(override=override)
    assert result.returncode == exit_code
    assert "budget=" not in result.stdout


def _source_profile_budget(*, outer=1800, bounded=True, override=None):
    function = DEPLOY_SCRIPT.read_text().split("validate_source_refresh_profile() {", 1)[1].split("\n}\n", 1)[0]
    env = _clean_env({
        "PANTHEON_DEV_COMPOSE_PROFILES": "root,source-ingest-scheduler" if bounded else "root",
        "PANTHEON_EXTERNAL_EGRESS": "allowlist" if bounded else "deny",
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": "openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw" if bounded else "",
        "SOURCE_INGEST_CONTROLLER_MODE": "reconcile_and_pull" if bounded else "reconcile_only",
        "SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL": "reconciled_live_proof" if bounded else "scheduled_tick",
        "SOURCE_INGEST_CONTROLLER_MAX_TICKS": "1" if bounded else "0",
        "SOURCE_INGEST_CONTROLLER_RESTART_POLICY": "no" if bounded else "unless-stopped",
        "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY": "1",
        "SOURCE_INGEST_MAX_RECORDS": "100",
        "SOURCE_INGEST_BOUNDED_CONNECTOR_ID": "tw-twse-tpex-official-market",
        "SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS": str(outer),
        **(override or {}),
    })
    command = "set -euo pipefail\nerror() { echo \"$*\" >&2; exit 37; }\nvalidate_source_refresh_profile() {" + function + "\n}\nvalidate_source_refresh_profile\nbash -c 'echo budget=${SOURCE_INGEST_CONTROLLER_TIMEOUT_SECONDS-unset}'\n"
    return subprocess.run(["bash", "-c", command], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)


VALID_NEUTRAL_STAGING_ENV = {
    "PROJECT_ID": "neutral-staging-project",
    "REMOTE_USER": "deployer",
    "STAGING_CONTROL_VM": "neutral-staging-control",
    "STAGING_CONTROL_ZONE": "asia-east1-b",
    "STAGING_CONTROL_REMOTE_DIR": "/home/deployer/pantheon",
    "STAGING_EXEC_VM": "neutral-staging-exec",
    "STAGING_EXEC_ZONE": "asia-east1-b",
    "STAGING_EXEC_REMOTE_DIR": "/home/deployer/pantheon",
    "STAGING_EXEC_HEALTH_URL": "http://10.0.0.1:28081",
    "STAGING_BFF_CANONICAL_CORS_ORIGIN": "https://neutral-staging-fe.example.com",
    "STAGING_BFF_CORS_ORIGINS": "https://neutral-staging-fe.example.com",
}

VALID_NEUTRAL_DEV_ENV = {
    "PROJECT_ID": "synthetic-dev-project",
    "REMOTE_USER": "synthetic-user",
    "DEV_VM": "synthetic-dev-vm",
    "DEV_ZONE": "asia-east1-b",
    "DEV_REMOTE_DIR": "/home/synthetic-user/pantheon",
    "DEV_DEPLOY_SSH_HOST": "192.0.2.50",
    "DEV_BFF_PUBLIC_HOST": "api.synthetic.invalid",
    "DEV_FE_PUBLIC_HOST": "app.synthetic.invalid",
    "DEV_FE_STATIC_ROOT": "/var/www/pantheon-dev-fe",
    "DEV_BFF_CORS_ORIGINS": "https://app.synthetic.invalid",
}


def _clean_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    clean = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if extra_env:
        clean.update(extra_env)
    return clean


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _extract_job(workflow: str, start_job: str, next_job: str | None = None) -> str:
    start = workflow.index(f"  {start_job}:")
    if next_job:
        end = workflow.index(f"  {next_job}:", start)
        return workflow[start:end]
    return workflow[start:]


def test_workflow_declares_dev_only_bootstrap_inputs() -> None:
    workflow = _workflow_text()
    inputs_block = workflow[: workflow.index("permissions:")]

    assert "bootstrap_empty_host:" in inputs_block
    assert "bootstrap_predecessor_backend_sha:" in inputs_block
    assert "bootstrap_predecessor_frontend_sha:" in inputs_block

    assert "default: false" in inputs_block
    assert 'default: ""' in inputs_block


def test_workflow_target_step_rejects_bootstrap_on_non_dev_environment() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    target_step = dev_job[
        dev_job.index("- name: Resolve and validate dev payload") :
        dev_job.index("- name: Resolve exact execute-plans dev payload")
    ]

    assert 'if [[ "${BOOTSTRAP_EMPTY_HOST}" == "true" ]]; then' in target_step
    assert 'if [[ "${TARGET_ENV}" != "dev" ]]; then' in target_step
    assert "Bootstrap input is only permitted for dev environment" in target_step


def test_workflow_target_step_rejects_malformed_bootstrap_shas() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    target_step = dev_job[
        dev_job.index("- name: Resolve and validate dev payload") :
        dev_job.index("- name: Resolve exact execute-plans dev payload")
    ]

    assert '[[ "${BOOTSTRAP_PREDECESSOR_BACKEND_SHA,,}" =~ ^[0-9a-f]{40}$ ]]' in target_step
    assert "bootstrap_predecessor_backend_sha must be one exact lowercase 40-character commit SHA" in target_step
    assert '[[ "${BOOTSTRAP_PREDECESSOR_FRONTEND_SHA,,}" =~ ^[0-9a-f]{40}$ ]]' in target_step
    assert "bootstrap_predecessor_frontend_sha must be one exact lowercase 40-character commit SHA" in target_step


def test_workflow_rollback_baseline_rejects_missing_manifest_when_not_bootstrapping() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    baseline_step = dev_job[
        dev_job.index("- name: Capture exact hosted FE and BFF rollback baseline") :
        dev_job.index("- name: Seal exact-pair admission artifact")
    ]

    assert 'if [[ "${BOOTSTRAP_EMPTY_HOST:-false}" == "true" ]]; then' in baseline_step
    assert 'else' in baseline_step
    assert '"${DEV_FE_URL%/}/deployment.json" > "${deployment_json}"' in baseline_step


def test_workflow_rollback_baseline_rejects_repeated_bootstrap_when_manifest_exists() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    baseline_step = dev_job[
        dev_job.index("- name: Capture exact hosted FE and BFF rollback baseline") :
        dev_job.index("- name: Seal exact-pair admission artifact")
    ]

    assert 'manifest_status="$(curl --silent --show-error' in baseline_step
    assert '--output "${deployment_json}" --write-out \'%{http_code}\'' in baseline_step
    assert 'if [[ "${manifest_status}" != "404" ]]; then' in baseline_step
    assert "expected explicit HTTP 404" in baseline_step
    assert "deployment.json was unreachable" in baseline_step


def test_workflow_rollback_baseline_requires_ancestor_commits_for_bootstrap() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    baseline_step = dev_job[
        dev_job.index("- name: Capture exact hosted FE and BFF rollback baseline") :
        dev_job.index("- name: Seal exact-pair admission artifact")
    ]

    assert 'git -C .agora-gate-controller merge-base --is-ancestor \\\n              "${bootstrap_backend}" refs/remotes/origin/dev' in baseline_step
    assert 'git -C .agora-frontend merge-base --is-ancestor \\\n              "${bootstrap_frontend}" refs/remotes/origin/dev' in baseline_step
    assert "Bootstrap predecessor backend commit ${bootstrap_backend} is not contained in Pantheon dev." in baseline_step
    assert "Bootstrap predecessor frontend commit ${bootstrap_frontend} is not contained in execute-plans dev." in baseline_step


def test_workflow_rollback_baseline_admits_predecessor_via_agora_compat_manifest() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    baseline_step = dev_job[
        dev_job.index("- name: Capture exact hosted FE and BFF rollback baseline") :
        dev_job.index("- name: Seal exact-pair admission artifact")
    ]

    assert "bootstrap-predecessor-compatibility-manifest.json" in baseline_step
    assert "bootstrap-predecessor-candidate-ledger.json" in baseline_step
    assert "python3 .target/scripts/agora_compat_manifest.py write" in baseline_step
    assert "python3 .target/scripts/agora_compat_manifest.py deployment-gate" in baseline_step
    assert "python3 scripts/agora_compat_manifest.py" not in baseline_step
    assert "bootstrap predecessor pair is not compatible" in baseline_step
    assert 'baseline_source="bootstrap_predecessor_pair"' in baseline_step


def test_workflow_deploys_predecessor_pair_under_lease_in_strict_read_only_mode() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")

    step_marker = "- name: Deploy bootstrap predecessor pair in strict live read-only mode under lease"
    assert step_marker in dev_job
    bootstrap_step = dev_job[
        dev_job.index(step_marker) :
        dev_job.index("- name: Deploy dev VM stack under lease")
    ]

    assert "if: ${{ env.BOOTSTRAP_EMPTY_HOST == 'true' }}" in bootstrap_step
    assert "deploy_nonprod_vm.sh" in bootstrap_step
    assert '--component bff' in bootstrap_step
    assert '--sha "${BOOTSTRAP_PREDECESSOR_BACKEND_SHA}"' in bootstrap_step
    assert "cross_repo_release_controller.py" in bootstrap_step
    assert '--candidate-profile "read-only"' in bootstrap_step
    assert "mismatched served identity" in bootstrap_step
    assert "bootstrap FE profile must be read-only" in bootstrap_step
    assert 'PANTHEON_DEV_LEASE_EXPECTED_BACKEND_SHA: ${{ steps.target.outputs.sha }}' in bootstrap_step
    assert 'PANTHEON_DEV_BOOTSTRAP_PREDECESSOR: "true"' in bootstrap_step
    assert 'DEV_BFF_JWT_SECRET: ${{ secrets.DEV_BFF_JWT_SECRET }}' in bootstrap_step
    assert 'DEV_BFF_OIDC_CLIENT_SECRET: ${{ secrets.DEV_BFF_OIDC_CLIENT_SECRET }}' in bootstrap_step
    assert 'DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET: ${{ secrets.DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET }}' in bootstrap_step
    assert 'DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN: ${{ secrets.DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN }}' in bootstrap_step
    assert 'export DEV_BFF_AUTH_MODE=strict' in bootstrap_step


def test_deploy_script_allows_only_explicit_bootstrap_lease_identity_override() -> None:
    script = (ROOT / "scripts" / "deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    contract = script[
        script.index("verify_dev_environment_lease_contract()") :
        script.index("usage()", script.index("verify_dev_environment_lease_contract()"))
    ]
    assert 'PANTHEON_DEV_LEASE_EXPECTED_BACKEND_SHA:-${DEPLOY_SHA}' in contract
    assert 'PANTHEON_DEV_BOOTSTRAP_PREDECESSOR:-false' in contract
    assert "dev lease expected backend override is only permitted for an explicit bootstrap predecessor" in contract


def test_workflow_candidate_deploy_requires_predecessor_served_identity_readback() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")

    pred_step_index = dev_job.index("- name: Deploy bootstrap predecessor pair in strict live read-only mode under lease")
    deploy_step_index = dev_job.index("- name: Deploy dev VM stack under lease")

    assert pred_step_index < deploy_step_index
    deploy_step = dev_job[
        deploy_step_index :
        dev_job.index("- name: Ensure governed dev paper baseline under lease", deploy_step_index)
    ]
    assert "PANTHEON_DEV_ROLLBACK_BACKEND_SHA: ${{ steps.rollback_baseline.outputs.sha }}" in deploy_step
    assert '--rollback-sha "${{ steps.rollback_baseline.outputs.sha }}"' in deploy_step


def test_workflow_coordinate_release_passes_predecessor_pair_shas() -> None:
    workflow = _workflow_text()
    coordinate_job = _extract_job(workflow, "coordinate-dev-release")

    assert "PREVIOUS_BACKEND_SHA: ${{ needs.deploy-dev.outputs.previous_backend_sha }}" in coordinate_job
    assert "PREVIOUS_FRONTEND_SHA: ${{ needs.deploy-dev.outputs.previous_frontend_sha }}" in coordinate_job
    assert '--predecessor-fe-sha "${PREVIOUS_FRONTEND_SHA}"' in coordinate_job
    assert '--predecessor-bff-sha "${PREVIOUS_BACKEND_SHA}"' in coordinate_job


def test_workflow_bootstrap_requires_dev_variables() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    bootstrap_step = dev_job[
        dev_job.index("- name: Deploy bootstrap predecessor pair in strict live read-only mode under lease") :
        dev_job.index("- name: Deploy dev VM stack under lease")
    ]

    assert "for var_name in DEV_VM DEV_ZONE GCP_DEPLOY_PROJECT_ID DEV_BFF_URL DEV_FE_URL DEV_DEPLOY_DEADLINE_SECONDS; do" in bootstrap_step
    assert 'echo "Required bootstrap variable ${var_name} is unset or empty; refusing to deploy." >&2' in bootstrap_step
    assert "exit 1" in bootstrap_step


def test_workflow_rollback_baseline_requires_dev_urls_when_bootstrapping() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")
    baseline_step = dev_job[
        dev_job.index("- name: Capture exact hosted FE and BFF rollback baseline") :
        dev_job.index("- name: Seal exact-pair admission artifact")
    ]

    assert 'if [[ -z "${DEV_FE_URL:-}" || -z "${DEV_BFF_URL:-}" ]]; then' in baseline_step
    assert 'Empty-host bootstrap requires DEV_FE_URL and DEV_BFF_URL to be set.' in baseline_step


def test_workflow_contains_no_retired_project_or_host_fallbacks_in_bootstrap_or_staging() -> None:
    workflow = _workflow_text()
    dev_job = _extract_job(workflow, "deploy-dev", "coordinate-dev-release")

    # In dev bootstrap steps
    bootstrap_step = dev_job[
        dev_job.index("- name: Deploy bootstrap predecessor pair in strict live read-only mode under lease") :
        dev_job.index("- name: Deploy dev VM stack under lease")
    ]
    assert "pantheon-lupin-dev-20260719" not in bootstrap_step
    assert "sslip.io" not in bootstrap_step
    assert "35.201.204.12" not in bootstrap_step


def test_deploy_script_contains_no_retired_fallbacks_in_source() -> None:
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "pantheon-lupin-staging-control" not in script_text
    assert "pantheon-lupin-staging-exec" not in script_text
    assert "10.50.0.21" not in script_text
    assert "pantheon-lupin-staging-fe.104.155.223.192.sslip.io" not in script_text
    assert "pantheon-lupin-dev-bff.35.201.204.12.sslip.io" not in script_text
    assert "pantheon-lupin-dev-fe.35.201.204.12.sslip.io" not in script_text


def test_deploy_script_staging_live_rejects_missing_target_identity() -> None:
    proc = subprocess.run(
        [str(DEPLOY_SCRIPT), "--environment", "staging-live", "--sha", DUMMY_SHA, "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert "staging-live deployment requires --project-id or PROJECT_ID to be set" in proc.stderr


def test_deploy_script_staging_live_rejects_missing_remote_user() -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "staging-live",
            "--project-id",
            "neutral-project",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert "staging-live deployment requires REMOTE_USER to be set" in proc.stderr


@pytest.mark.parametrize(
    "missing_var",
    [
        "STAGING_CONTROL_VM",
        "STAGING_CONTROL_ZONE",
        "STAGING_CONTROL_REMOTE_DIR",
        "STAGING_EXEC_VM",
        "STAGING_EXEC_ZONE",
        "STAGING_EXEC_REMOTE_DIR",
        "STAGING_EXEC_HEALTH_URL",
        "STAGING_BFF_CORS_ORIGINS",
    ],
)
def test_deploy_script_staging_live_rejects_missing_required_variable(missing_var: str) -> None:
    env = dict(VALID_NEUTRAL_STAGING_ENV)
    del env[missing_var]
    if missing_var == "STAGING_BFF_CORS_ORIGINS":
        env.pop("STAGING_BFF_CANONICAL_CORS_ORIGIN", None)
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "staging-live",
            "--component",
            "all",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(env),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert f"staging-live deployment requires {missing_var} to be set; refusing to deploy with missing target identity" in proc.stderr


@pytest.mark.parametrize(
    "retired_project",
    [
        "pantheon-benjamin-20260528",
        "pantheon-lupin-dev-20260719",
    ],
)
def test_deploy_script_rejects_retired_project(retired_project: str) -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--project-id",
            retired_project,
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert f"GCP project {retired_project} is retired; refusing to deploy" in proc.stderr


@pytest.mark.parametrize(
    ("var_name", "retired_value"),
    [
        ("DEV_BFF_PUBLIC_HOST", "pantheon-lupin-dev-bff.35.201.204.12.sslip.io"),
        ("DEV_DEPLOY_SSH_HOST", "35.201.204.12"),
        ("DEV_DEPLOY_SSH_HOST", "35.201.239.38"),
        ("DEV_DEPLOY_SSH_HOST", "34.81.75.241"),
        ("DEV_DEPLOY_SSH_HOST", "35.236.178.81"),
        ("STAGING_CONTROL_VM", "pantheon-benjamin-20260528-control"),
        ("STAGING_CONTROL_VM", "pantheon-lupin-dev"),
        ("STAGING_BFF_CORS_ORIGINS", "https://pantheon-lupin-staging-fe.104.155.223.192.sslip.io"),
        ("STAGING_CONTROL_REMOTE_DIR", "/home/lupin/code/pantheon"),
        ("STAGING_EXEC_REMOTE_DIR", "/home/lupin/pantheon"),
        ("REMOTE_USER", "lupin"),
    ],
)
def test_deploy_script_rejects_retired_target_identity(var_name: str, retired_value: str) -> None:
    env = dict(VALID_NEUTRAL_STAGING_ENV)
    env[var_name] = retired_value
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "staging-live",
            "--component",
            "all",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(env),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert f"{var_name} contains retired target identity" in proc.stderr


@pytest.mark.parametrize(
    ("var_name", "retired_value"),
    [
        ("DEV_DEPLOY_SSH_HOST", "35.201.239.38"),
        ("DEV_DEPLOY_SSH_HOST", "34.81.75.241"),
        ("DEV_DEPLOY_SSH_HOST", "35.201.204.12"),
        ("DEV_DEPLOY_SSH_HOST", "104.155.223.192"),
        ("DEV_DEPLOY_SSH_HOST", "35.236.178.81"),
        ("DEV_REMOTE_DIR", "/home/lupin/code/pantheon"),
        ("DEV_REMOTE_DIR", "/home/lupin/pantheon"),
        ("DEV_VM", "pantheon-lupin-dev"),
        ("REMOTE_USER", "lupin"),
        ("DEV_BFF_PUBLIC_HOST", "pantheon-lupin-dev-bff.35.201.239.38.sslip.io"),
        ("DEV_FE_PUBLIC_HOST", "pantheon-lupin-dev-fe.35.201.239.38.sslip.io"),
        ("DEV_BFF_CORS_ORIGINS", "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io"),
        ("DEV_FE_STATIC_ROOT", "/home/lupin/pantheon-dev-fe"),
    ],
)
def test_deploy_script_dev_rejects_retired_target_identity(var_name: str, retired_value: str) -> None:
    env = dict(VALID_NEUTRAL_DEV_ENV)
    env[var_name] = retired_value
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(env),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert f"{var_name} contains retired target identity" in proc.stderr


def test_deploy_script_staging_live_accepts_valid_neutral_fixtures() -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "staging-live",
            "--component",
            "all",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(VALID_NEUTRAL_STAGING_ENV),
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"Staging dry run failed: {proc.stderr}"
    assert "environment=staging-live" in proc.stdout
    assert "component=all" in proc.stdout
    assert "staging_exec_health_url=http://10.0.0.1:28081" in proc.stdout
    assert "staging_bff_cors_origins=https://neutral-staging-fe.example.com" in proc.stdout

    for retired in [
        "sslip.io",
        "104.155.223.192",
        "35.201.204.12",
        "35.201.239.38",
        "34.81.75.241",
        "35.236.178.81",
        "pantheon-benjamin-20260528",
        "pantheon-lupin-dev-20260719",
        "pantheon-lupin-dev",
        "/home/lupin",
    ]:
        assert retired not in proc.stdout
        assert retired not in proc.stderr


def test_deploy_script_dev_dry_run_accepts_valid_neutral_fixtures() -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(VALID_NEUTRAL_DEV_ENV),
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"Dev neutral dry run failed: {proc.stderr}"
    assert "project=synthetic-dev-project" in proc.stdout
    assert "environment=dev" in proc.stdout
    assert "component=root" in proc.stdout
    assert "dev_bff_public_host=api.synthetic.invalid" in proc.stdout
    assert "dev_fe_public_host=app.synthetic.invalid" in proc.stdout

    for retired in [
        "sslip.io",
        "104.155.223.192",
        "35.201.204.12",
        "35.201.239.38",
        "34.81.75.241",
        "35.236.178.81",
        "pantheon-benjamin-20260528",
        "pantheon-lupin-dev-20260719",
        "pantheon-lupin-dev",
        "/home/lupin",
    ]:
        assert retired not in proc.stdout
        assert retired not in proc.stderr


def test_deploy_script_dev_dry_run_accepts_default_dev_identity() -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(),
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"Dev dry run failed: {proc.stderr}"
    assert "project=pantheon-dev-20260902" in proc.stdout
    assert "environment=dev" in proc.stdout
    assert "component=root" in proc.stdout
    assert "dev_bff_public_host=api.dev.mvl-cap.tw" in proc.stdout
    assert "dev_fe_public_host=app.dev.mvl-cap.tw" in proc.stdout

    for retired in [
        "sslip.io",
        "104.155.223.192",
        "35.201.204.12",
        "35.201.239.38",
        "34.81.75.241",
        "35.236.178.81",
        "pantheon-benjamin-20260528",
        "pantheon-lupin-dev-20260719",
        "pantheon-lupin-dev",
        "/home/lupin",
    ]:
        assert retired not in proc.stdout
        assert retired not in proc.stderr


def test_deploy_script_dev_rejects_composed_explicit_empty_variables() -> None:
    empty_env = {
        "PROJECT_ID": "",
        "REMOTE_USER": "",
        "DEV_VM": "",
        "DEV_ZONE": "",
        "DEV_REMOTE_DIR": "",
        "DEV_DEPLOY_SSH_HOST": "",
        "DEV_BFF_PUBLIC_HOST": "",
        "DEV_FE_PUBLIC_HOST": "",
        "DEV_FE_STATIC_ROOT": "",
        "DEV_BFF_CORS_ORIGINS": "",
    }
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(empty_env),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert "dev deployment requires --project-id or PROJECT_ID to be set" in proc.stderr


@pytest.mark.parametrize(
    ("empty_var", "expected_err"),
    [
        ("PROJECT_ID", "dev deployment requires --project-id or PROJECT_ID to be set"),
        ("REMOTE_USER", "dev deployment requires REMOTE_USER to be set"),
        ("DEV_VM", "dev deployment requires DEV_VM to be set; refusing to deploy with missing target identity"),
        ("DEV_ZONE", "dev deployment requires DEV_ZONE to be set; refusing to deploy with missing target identity"),
        ("DEV_REMOTE_DIR", "dev deployment requires DEV_REMOTE_DIR to be set; refusing to deploy with missing target identity"),
        ("DEV_DEPLOY_SSH_HOST", "dev deployment requires DEV_DEPLOY_SSH_HOST to be set; refusing to deploy with missing target identity"),
        ("DEV_BFF_PUBLIC_HOST", "dev deployment requires DEV_BFF_PUBLIC_HOST to be set; refusing to deploy with missing target identity"),
        ("DEV_FE_PUBLIC_HOST", "dev deployment requires DEV_FE_PUBLIC_HOST to be set; refusing to deploy with missing target identity"),
        ("DEV_FE_STATIC_ROOT", "dev deployment requires DEV_FE_STATIC_ROOT to be set; refusing to deploy with missing target identity"),
        ("DEV_BFF_CORS_ORIGINS", "dev deployment requires DEV_BFF_CORS_ORIGINS to be set; refusing to deploy with missing target identity"),
    ],
)
def test_deploy_script_dev_rejects_individual_explicit_empty_variable(empty_var: str, expected_err: str) -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env({empty_var: ""}),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert expected_err in proc.stderr


def test_deploy_script_dev_rejects_empty_cli_project_id() -> None:
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment",
            "dev",
            "--project-id",
            "",
            "--sha",
            DUMMY_SHA,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(),
        cwd=ROOT,
    )
    assert proc.returncode == 1
    assert "dev deployment requires --project-id or PROJECT_ID to be set" in proc.stderr


def test_dev_deploy_rejects_synthetic_target_before_ssh_without_staging_vars(tmp_path: Path) -> None:
    """A fake SSH exit zero must not be reported as accepted deployment.

    Positive guarded transport and durable receipt coverage belongs to
    test_dev_remote_guarded_exec.py::test_candidate_cli_real_receiver_persists_and_acknowledges_before_action;
    its test_candidate_cli_requires_receipt_even_when_remote_script_exits_zero
    also checks that exit status alone cannot substitute for receipt evidence.
    """
    import json
    lease_file = tmp_path / "dev-lease.json"
    lease_file.write_text(
        json.dumps({
            "schemaVersion": 1,
            "repository": "ajoe734/execute-plans",
            "branch": "environment-coordination",
            "path": ".pantheon/environment-leases/pantheon-dev-environment.json",
            "resource": "pantheon-dev-environment",
            "mode": "deployment",
            "leaseId": "stub-lease-bootstrap",
            "expectedBackendSha": DUMMY_SHA,
        }),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    key_file = tmp_path / "dev_key"
    key_file.write_text("fake-dev-key\n", encoding="utf-8")
    key_file.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fake-known-hosts\n", encoding="utf-8")
    known_hosts.chmod(0o600)
    args_file = tmp_path / "ssh_args.txt"
    stub_ssh = bin_dir / "ssh"
    stub_ssh.write_text(
        f"""#!/bin/sh
printf '%s\\n' "$@" > '{args_file}'
exit 0
""",
        encoding="utf-8",
    )
    stub_ssh.chmod(0o755)

    env = dict(VALID_NEUTRAL_DEV_ENV)
    env.update({
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(lease_file),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": "stub-lease-bootstrap",
        "DEV_BFF_AUTH_STUB": "true",
        "DEV_BFF_AUTH_MODE": "permissive",
        "DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED": "false",
        "DEV_DEPLOY_SSH_KEY_FILE": str(key_file),
        "DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE": str(known_hosts),
    })
    # Ensure no staging variables leak into the execution environment
    for k in list(env.keys()):
        if k.startswith("STAGING_"):
            del env[k]

    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment", "dev",
            "--sha", DUMMY_SHA,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )
    assert proc.returncode == 75, proc.stderr
    assert "guarded artifact transport requires the explicit current dev target" in proc.stderr
    assert "deployment complete:" not in proc.stdout
    assert not args_file.exists(), "invalid target reached SSH"


def test_deploy_script_staging_live_executes_beyond_dry_run_with_stubbed_gcloud_and_no_dev_vars(tmp_path: Path) -> None:
    """Staging-live deployment beyond dry-run executes cleanly with stubbed gcloud without requiring dev variables."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub_gcloud = bin_dir / "gcloud"
    cmd_file = tmp_path / "gcloud_cmd.txt"
    stub_gcloud.write_text(
        f"""#!/bin/sh
printf '%s\\n' "$@" > '{cmd_file}'
exit 0
""",
        encoding="utf-8",
    )
    stub_gcloud.chmod(0o755)

    env = dict(VALID_NEUTRAL_STAGING_ENV)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    # Ensure no DEV variables are in the environment
    for k in list(env.keys()):
        if k.startswith("DEV_"):
            del env[k]

    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment", "staging-live",
            "--component", "control",
            "--sha", DUMMY_SHA,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"deploy_nonprod_vm.sh staging-live failed: {proc.stderr}"
    assert f"deployment complete: staging-live/control {DUMMY_SHA}" in proc.stdout
    assert cmd_file.exists()
    gcloud_args = cmd_file.read_text(encoding="utf-8")
    assert "--project=neutral-staging-project" in gcloud_args
    assert "deployer@neutral-staging-control" in gcloud_args
