"""Cross-job artifact seams, not evidence of a hosted deployment or rollback."""
from pathlib import Path
import os
import re
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/nonprod-deploy.yml").read_text())
DEPLOY = WORKFLOW["jobs"]["deploy-dev"]
COORDINATE = WORKFLOW["jobs"]["coordinate-dev-release"]


def step(job, identity):
    return next(item for item in job["steps"] if item.get("id") == identity)


def test_quota_preflight_precedes_candidate_mutation_and_logs_with_configurable_budget():
    acquisition = step(DEPLOY, "lease")
    assert acquisition["env"]["PANTHEON_DEV_LEASE_MIN_REMAINING_QUOTA"] == "${{ vars.DEV_LEASE_MIN_REMAINING_QUOTA || '1500' }}"
    body = acquisition["run"]
    assert body.index("sha256sum --check --strict") < body.index("wait-for-quota") < body.index('dev_environment_lease.py" acquire')
    assert "|| true" not in body
    assert DEPLOY["timeout-minutes"] == 360


def test_compensation_waits_before_heartbeat_and_cannot_be_displayed_as_success_on_failure():
    for job, identity in ((DEPLOY, "deploy_compensation"), (COORDINATE, "compensation")):
        compensation = step(job, identity)
        assert compensation.get("continue-on-error", False) is False
        assert compensation["timeout-minutes"] * 60 >= 3900 + 1500 + 180
    for body in (step(DEPLOY, "deploy_compensation")["run"],
                 (ROOT / "scripts/compensate_cross_repo_release.sh").read_text()):
        start = body.index("rollback_lease_deadline=$((SECONDS + 3900))")
        startup = body[start:]
        assert startup.index("acquire") < startup.index("--rate-limit-wait-seconds") < startup.index("heartbeat-loop")
        assert '--rate-limit-wait-seconds "${rollback_rate_wait}"' in startup
        assert '--wait-seconds 3900' in startup
        assert 'rollback_rate_wait=$((rollback_lease_deadline - SECONDS))' in startup
        assert "rate_limited_rollback_pending" in body
        assert '"${status}" -eq 79' in body


def test_baseline_is_guarded_and_uploaded_before_candidate_mutation():
    order = [item.get("id") for item in DEPLOY["steps"]]
    required = ["release_admission", "rollback_baseline", "lease", "artifact_baseline",
                "artifact_baseline_upload", "deploy", "artifact_candidate_seal",
                "artifact_candidate_upload", "paper_bootstrap", "public_smoke"]
    assert sorted(required, key=order.index) == required
    capture = step(DEPLOY, "artifact_baseline")
    assert "run_with_dev_environment_lease.sh" in capture["run"]
    assert "c6fbde26c185b963d962ef9d8433fa31498440886d89691f9410ab22a5afbaaa" in capture["run"]
    assert "6c82021b93621f16776d5d67a9e20cb9d690f7ebfa257ebf8c329f7d158fb2c2" in capture["run"]
    assert "capture_dev_artifact_baseline.py" in capture["run"]
    assert "acquire" not in capture["run"]  # no replacement lease authority
    assert "CLIENT_SECRET" not in step(DEPLOY, "artifact_baseline_upload")["with"]["path"]
    assert "*.tar" not in step(DEPLOY, "artifact_baseline_upload")["with"]["path"]


@pytest.mark.parametrize("field,source", [
    ("CANDIDATE_ID", "steps.release_admission.outputs.release_candidate_id"),
    ("RUN_ID", "github.run_id"), ("ATTEMPT", "github.run_attempt"),
    ("CONTROLLER_SHA", "steps.target.outputs.sha"),
    ("CANDIDATE_BACKEND_SHA", "steps.target.outputs.sha"),
    ("CANDIDATE_FRONTEND_SHA", "steps.frontend.outputs.sha"),
    ("PREVIOUS_BACKEND_SHA", "steps.rollback_baseline.outputs.sha"),
    ("PREVIOUS_FRONTEND_SHA", "steps.rollback_baseline.outputs.frontend_sha"),
])
def test_capture_and_candidate_have_the_same_admitted_eight_field_identity(field, source):
    expected = "${{ " + source + " }}"
    for name in ("artifact_baseline", "deploy"):
        assert step(DEPLOY, name)["env"]["PANTHEON_DEV_ARTIFACT_" + field] == expected


def test_candidate_receipt_upload_survives_deploy_failure_but_not_missing_seal():
    finalize = step(DEPLOY, "artifact_candidate_seal")
    assert "always()" in finalize["if"]
    assert finalize["continue-on-error"] is True
    assert "dev_candidate_receipt.py" in finalize["run"]
    assert "--context" in finalize["run"] and "--receipt" in finalize["run"]
    upload = step(DEPLOY, "artifact_candidate_upload")
    assert "always()" in upload["if"]
    assert "steps.artifact_candidate_seal.outcome == 'success'" in upload["if"]
    assert upload["with"]["path"] == "${{ steps.artifact_candidate_seal.outputs.receipt_file }}"
    artifacts = step(DEPLOY, "lease_cleanup")["env"]["ARTIFACTS_VERIFIED"]
    for name in ("artifact_baseline", "artifact_baseline_upload", "artifact_candidate_seal", "artifact_candidate_upload"):
        assert f"steps.{name}.outcome == 'success'" in artifacts


def test_download_binds_external_ids_digests_and_run_before_fe_gate():
    ids = [item.get("id") for item in COORDINATE["steps"]]
    assert ids.index("artifact_download") < ids.index("frontend_release")
    download = step(COORDINATE, "artifact_download")
    assert COORDINATE["permissions"]["actions"] == "read"
    for name in ("BASELINE", "CANDIDATE"):
        lower = name.lower()
        assert download["env"][name + "_ARTIFACT_ID"] == "${{ needs.deploy-dev.outputs.artifact_" + lower + "_id }}"
        assert download["env"][name + "_ARTIFACT_SHA256"] == "${{ needs.deploy-dev.outputs.artifact_" + lower + "_digest }}"
    assert download["run"].count("fetch_dev_artifact_evidence.py") == 2
    assert download["run"].count('--run-id "${GITHUB_RUN_ID}" --attempt "${GITHUB_RUN_ATTEMPT}"') == 2
    assert "download-artifact@" not in download.get("uses", "")


def test_inline_compensation_never_uses_source_equality_as_artifact_proof():
    rollback = step(DEPLOY, "deploy_compensation")
    assert "steps.artifact_baseline.outcome == 'success'" in rollback["if"]
    assert "steps.artifact_candidate_upload.outcome != 'success'" in rollback["if"]
    body = rollback["run"]
    assert "prepare --provenance runner-local" in body
    assert '"--artifact-${PANTHEON_DEV_ARTIFACT_OPERATION}"' in body
    assert "--artifact-readback-out" in body
    assert "skipping rollback deploy" not in body
    assert 'current_bff=' not in body
    assert "run_with_dev_environment_lease.sh" in body
    assert body.index("prepare --provenance runner-local") < body.index("acquire \\")


def test_inline_compensation_readback_directory_is_bound_in_its_own_step(tmp_path):
    rollback = step(DEPLOY, "deploy_compensation")
    assert rollback["env"]["EVIDENCE_DIR"] == "${{ steps.release_admission.outputs.evidence_dir }}"
    # Exercise the real shell expansion under nounset without running a deploy.
    argument = re.search(r'--artifact-readback-out\s+("[^"\n]+")', rollback["run"])
    assert argument is not None
    result = subprocess.run(
        ["bash", "-u", "-c", "printf '%s' " + argument.group(1)],
        env={"EVIDENCE_DIR": str(tmp_path)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(tmp_path / "dev-compensation-artifact-readback.json")


@pytest.mark.parametrize("job_name", ["deploy-dev", "coordinate-dev-release"])
def test_dev_workflow_embedded_shell_is_syntactically_executable(job_name):
    for item in WORKFLOW["jobs"][job_name]["steps"]:
        if "run" not in item:
            continue
        # Parse, never execute a workflow or interpolate real secrets.
        rendered = re.sub(r"\$\{\{.*?\}\}", "validated-placeholder", item["run"])
        result = subprocess.run(["bash", "-n"], input=rendered, text=True, capture_output=True)
        assert result.returncode == 0, (item.get("id", item.get("name")), result.stderr)


def evaluate(expression, values, cancelled=False):
    """Evaluate the boolean/string subset used by these loaded Actions gates."""
    expression = expression.removeprefix("${{").removesuffix("}}").strip()
    expression = re.sub(r"(?:steps|env|needs)\.[\w.-]+",
                        lambda match: repr(values.get(match[0], "")), expression)
    expression = expression.replace("always()", "True").replace("cancelled()", repr(cancelled))
    expression = expression.replace("job.status", repr("cancelled" if cancelled else "success"))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", " not ", expression)
    return eval(expression.strip(), {"__builtins__": {}}, {})


# The stack-safety set. Functional checks (market refresh, paper baseline,
# probes, Agora smoke) are advisory and deliberately absent: see ADVISORY.
MANDATORY = ("heartbeat", "deploy", "public_smoke", "steady_restart_loop",
             "deploy-posture-evidence", "artifact_baseline_upload",
             "artifact_candidate_seal", "artifact_candidate_upload")
ADVISORY = ("market_refresh", "paper_bootstrap", "evolution-dispatch-probe",
            "loop-prod-tel-002-probe", "agora")


def run_completion(tmp_path, component, outcomes, cancelled=False, fault=None, requested=None, strict=False):
    """Run the production cleanup tail with only lease transport/stop stubbed."""
    cleanup = step(DEPLOY, "lease_cleanup")
    if requested is None:  # explicit paper request on auto/root unless a test says otherwise
        requested = component in ("auto", "root")
    values = {"env.TARGET_COMPONENT": component,
              "env.PAPER_BOOTSTRAP_REQUESTED": str(requested).lower(),
              "env.FUNCTIONAL_CHECKS_STRICT": str(strict).lower(),
              "steps.functional_gate.outcome": "success" if strict else "skipped",
              **{f"steps.{key}.outcome": value for key, value in outcomes.items()}}
    env = {**os.environ, "TARGET_COMPONENT": component,
           "FUNCTIONAL_CHECKS_STRICT": str(strict).lower(),
           "PAPER_BOOTSTRAP_REQUESTED": values["env.PAPER_BOOTSTRAP_REQUESTED"]}
    for key, expression in cleanup["env"].items():
        if key.endswith("_OUTCOME"):
            env[key] = values.get(expression[4:-3], "")
    env["ARTIFACTS_VERIFIED"] = str(evaluate(cleanup["env"]["ARTIFACTS_VERIFIED"], values, cancelled)).lower()
    for key in ("LEASE_PID_FILE", "LEASE_FAILURE_FILE", "LEASE_SHUTDOWN_FILE", "LEASE_STATE_FILE", "GITHUB_OUTPUT"):
        env[key] = str(tmp_path / key)
    if fault != "missing_heartbeat":
        Path(env["LEASE_PID_FILE"]).write_text("12345")
    Path(env["LEASE_SHUTDOWN_FILE"]).write_text(
        '{"status":"stopped","resource":"pantheon-dev-environment"}' if fault != "shutdown" else '{}')
    if fault == "heartbeat_failure":
        Path(env["LEASE_FAILURE_FILE"]).write_text("guarded_command_failed")
    controller = tmp_path / "scripts"
    controller.mkdir()
    (controller / "dev_environment_lease.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        f"with Path({str(tmp_path / 'calls')!r}).open('a') as log: log.write(sys.argv[1] + '\\n')\n"
        f"sys.exit(75 if sys.argv[1] == {fault!r} else 0)\n")
    body = cleanup["run"][cleanup["run"].index("complete_success=false"):]
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         f'controller="{tmp_path}"; lease_token=fixture; stop_heartbeat() {{ return {75 if fault == "stop" else 0}; }}\n' + body],
        env=env, text=True, capture_output=True, timeout=10)
    output = Path(env["GITHUB_OUTPUT"])
    values["steps.lease_cleanup.outputs.complete_success"] = (
        "true" if output.exists() and "complete_success=true" in output.read_text() else "")
    verified = evaluate(DEPLOY["outputs"]["bff_fe_pair_verified"], values, cancelled)
    values["needs.deploy-dev.outputs.bff_fe_pair_verified"] = str(verified).lower()
    admitted = evaluate(COORDINATE["if"], values, cancelled)
    compensated = evaluate(step(DEPLOY, "deploy_compensation")["if"], values, cancelled)
    calls = tmp_path / "calls"
    return result, admitted, compensated, calls.read_text().splitlines() if calls.exists() else []


@pytest.mark.parametrize("component", ["auto", "root", "bff"])
@pytest.mark.parametrize("outcome", ["failure", "skipped", "cancelled", ""])
@pytest.mark.parametrize("failed", MANDATORY)
def test_mandatory_failure_cannot_admit_or_release_and_requires_compensation(tmp_path, component, outcome, failed):
    outcomes = dict.fromkeys((*MANDATORY, "lease", "artifact_baseline"), "success")
    outcomes[failed] = outcome
    result, admitted, compensate, calls = run_completion(tmp_path, component, outcomes)
    assert result.returncode == 0, result.stderr
    assert admitted is False
    assert compensate is True
    assert "release" not in calls


@pytest.mark.parametrize("component", ["auto", "root", "bff", "sidecar"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_success_and_cancellation_follow_loaded_admission_and_compensation(tmp_path, component, cancelled):
    outcomes = dict.fromkeys((*MANDATORY, "lease", "artifact_baseline"), "success")
    result, admitted, compensate, calls = run_completion(tmp_path, component, outcomes, cancelled)
    assert result.returncode == 0, result.stderr
    assert admitted is (not cancelled and component != "sidecar")
    assert compensate is cancelled
    assert ("release" in calls) is admitted


@pytest.mark.parametrize("fault", ["missing_heartbeat", "heartbeat_failure", "stop", "shutdown", "verify", "release"])
def test_cleanup_cannot_publish_verification_before_identity_bound_release(tmp_path, fault):
    outcomes = dict.fromkeys((*MANDATORY, "lease", "artifact_baseline"), "success")
    result, admitted, _, calls = run_completion(tmp_path, "root", outcomes, fault=fault)
    assert not admitted
    assert result.returncode == (0 if fault in {"missing_heartbeat", "heartbeat_failure"} else 75 if fault in {"stop", "verify", "release"} else 1)
    assert ("release" in calls) is (fault == "release")


@pytest.mark.parametrize("prerequisite", ["lease", "artifact_baseline"])
@pytest.mark.parametrize("outcome", ["failure", "skipped", "cancelled", ""])
def test_compensation_needs_acquired_lease_and_captured_predecessor(prerequisite, outcome):
    values = {f"steps.{name}.outcome": "success" for name in (*MANDATORY, "lease", "artifact_baseline")}
    values.update({"env.TARGET_COMPONENT": "root", "steps.public_smoke.outcome": "failure",
                   f"steps.{prerequisite}.outcome": outcome})
    assert not evaluate(step(DEPLOY, "deploy_compensation")["if"], values)
    assert not evaluate(DEPLOY["outputs"]["bff_fe_pair_verified"], values)


PAPER_FLAGS = ("ppl_alloc_009_dev_proof_enabled", "run_loop_prod_tel_002_probe",
               "run_evolution_dispatch_probe", "run_bounded_source_refresh", "bootstrap_empty_host")


def paper_requested(component, **flags):
    expression = DEPLOY["env"]["PAPER_BOOTSTRAP_REQUESTED"]
    expression = expression.removeprefix("${{").removesuffix("}}").strip()
    for name in ("component", *PAPER_FLAGS):
        value = component if name == "component" else flags.get(name, False)
        expression = expression.replace("inputs." + name, repr(value))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    return eval(expression, {"__builtins__": {}}) == "true"


@pytest.mark.parametrize("component", ["auto", "root", "bff"])
def test_ordinary_release_does_not_request_paper_bootstrap(component):
    assert paper_requested(component) is False


@pytest.mark.parametrize("flag", PAPER_FLAGS)
@pytest.mark.parametrize("component", ["auto", "root", "bff"])
def test_each_explicit_flag_requests_paper_only_on_auto_or_root(component, flag):
    assert paper_requested(component, **{flag: True}) is (component != "bff")


def test_paper_step_and_retirement_share_the_one_applicability_decision():
    assert step(DEPLOY, "paper_bootstrap")["if"] == "${{ env.PAPER_BOOTSTRAP_REQUESTED == 'true' }}"
    assert "env.PAPER_BOOTSTRAP_REQUESTED == 'true'" in step(DEPLOY, "retire_release_cases")["if"]
    assert DEPLOY["outputs"]["paper_bootstrap_requested"] == "${{ env.PAPER_BOOTSTRAP_REQUESTED }}"


@pytest.mark.parametrize("component", ["auto", "root", "bff"])
def test_ordinary_release_skipped_paper_completes_without_compensation(tmp_path, component):
    outcomes = dict.fromkeys((*MANDATORY, "lease", "artifact_baseline"), "success")
    outcomes["paper_bootstrap"] = "skipped"
    result, admitted, compensate, calls = run_completion(tmp_path, component, outcomes, requested=False)
    assert result.returncode == 0, result.stderr
    assert admitted and not compensate and "release" in calls


@pytest.mark.parametrize("failed", ADVISORY)
@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", ""])
@pytest.mark.parametrize("component", ["auto", "root", "bff"])
def test_advisory_failure_leaves_deploy_successful_and_does_not_compensate(tmp_path, component, failed, outcome):
    outcomes = dict.fromkeys((*MANDATORY, "lease", "artifact_baseline"), "success")
    outcomes[failed] = outcome
    result, admitted, compensate, calls = run_completion(tmp_path, component, outcomes, requested=True)
    assert result.returncode == 0, result.stderr
    assert admitted and not compensate and "release" in calls


@pytest.mark.parametrize("name", ADVISORY)
def test_compensation_and_completion_conditions_do_not_name_advisory_steps(name):
    cleanup = step(DEPLOY, "lease_cleanup")
    for text in (step(DEPLOY, "deploy_compensation")["if"], step(DEPLOY, "retire_release_cases")["if"],
                 "".join(cleanup["env"].values()), cleanup["run"]):
        assert f"steps.{name}." not in text
    assert "AGORA_OUTCOME" not in cleanup["run"] and "PAPER_BOOTSTRAP_OUTCOME" not in cleanup["run"]


@pytest.mark.parametrize("component", ["auto", "root", "bff"])
def test_strict_functional_gate_failure_still_blocks_and_compensates(tmp_path, component):
    outcomes = dict.fromkeys((*MANDATORY, "lease", "artifact_baseline"), "success")
    outcomes["functional_gate"] = "failure"
    result, admitted, compensate, calls = run_completion(tmp_path, component, outcomes, requested=True, strict=True)
    assert result.returncode == 0, result.stderr
    assert not admitted and compensate and "release" not in calls


def test_strict_dispatch_without_failures_completes(tmp_path):
    outcomes = dict.fromkeys((*MANDATORY, "lease", "artifact_baseline"), "success")
    result, admitted, compensate, calls = run_completion(tmp_path, "root", outcomes, requested=True, strict=True)
    assert result.returncode == 0, result.stderr
    assert admitted and not compensate and "release" in calls


def test_strict_mode_is_a_repository_variable_and_defaults_to_advisory():
    on_section = WORKFLOW.get(True) or WORKFLOW.get("on")
    # A 26th dispatch input made GitHub reject the whole workflow on
    # 2026-11-17, so strict mode is a repository variable, never an input.
    assert "strict_functional_checks" not in on_section["workflow_dispatch"]["inputs"]
    assert DEPLOY["env"]["FUNCTIONAL_CHECKS_STRICT"] == (
        "${{ vars.DEV_DEPLOY_STRICT_FUNCTIONAL_CHECKS == 'true' && 'true' || 'false' }}"
    )
    assert "--strict-functional-checks" in step(DEPLOY, "deploy")["run"]
    assert "strict_functional_checks" not in (ROOT / "scripts/auto_deploy_dev_pair.py").read_text()


def test_no_workflow_exceeds_the_github_dispatch_input_limit():
    # GitHub accepts at most 25 workflow_dispatch inputs; one more invalidates
    # the workflow file for every trigger, including the automatic deployer.
    import yaml

    for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        triggers = document.get(True) or document.get("on") or {}
        dispatch = triggers.get("workflow_dispatch") if isinstance(triggers, dict) else None
        inputs = (dispatch or {}).get("inputs") or {}
        assert len(inputs) <= 25, f"{path.name} declares {len(inputs)} workflow_dispatch inputs (limit 25)"


def test_advisory_results_are_recorded_and_uploaded_without_failing_the_job():
    order = [item.get("id") for item in DEPLOY["steps"]]
    assert order.index("agora") < order.index("advisory_summary") < order.index("advisory_upload") \
        < order.index("functional_gate") < order.index("retire_release_cases")
    summary = step(DEPLOY, "advisory_summary")
    assert "always()" in summary["if"] and "summary.json" in summary["run"]
    assert "ADVISORY_CHECK_RESULT" in summary["run"] and "GITHUB_STEP_SUMMARY" in summary["run"]
    assert step(DEPLOY, "advisory_upload")["with"]["name"].startswith("pantheon-dev-advisory-checks-")
    for name in ("evolution-dispatch-probe", "loop-prod-tel-002-probe"):
        assert step(DEPLOY, name)["continue-on-error"] == "${{ env.FUNCTIONAL_CHECKS_STRICT != 'true' }}"
    for name in ("market_refresh", "paper_bootstrap", "agora"):
        # Lease-guarded: continue-on-error is not enough, so the guarded child
        # must be wrapped to exit 0 (design finding 2).
        assert "guarded_advisory" in step(DEPLOY, name)["run"]
        assert "continue-on-error" not in step(DEPLOY, name)
    assert "run_with_dev_environment_lease.sh" not in step(DEPLOY, "advisory_summary")["run"]


def test_inline_retirement_only_for_requested_case_after_compensation():
    order = [item.get("id") for item in DEPLOY["steps"]]
    assert order.index("retire_release_cases") < order.index("deploy_compensation") < order.index("lease_cleanup")
    retire = step(DEPLOY, "retire_release_cases")
    assert retire["env"]["RELEASE_OUTCOME"] == "pre_restore"
    assert "steps.deploy_compensation.outcome" not in retire["if"]
    base = {f"steps.{k}.outcome": "success" for k in (*MANDATORY, "lease", "artifact_baseline")}
    failed = {**base, "steps.public_smoke.outcome": "failure"}
    assert evaluate(retire["if"], {**failed, "env.PAPER_BOOTSTRAP_REQUESTED": "true"})
    assert not evaluate(retire["if"], {**failed, "env.PAPER_BOOTSTRAP_REQUESTED": "false"})
    assert not evaluate(retire["if"], {**base, "env.PAPER_BOOTSTRAP_REQUESTED": "true"})
    assert not evaluate(retire["if"], {"env.PAPER_BOOTSTRAP_REQUESTED": "true"})


STATUS_FUNCTIONS = re.compile(r"\b(?:cancelled|success|failure|always)\(\)")


def test_status_functions_appear_only_in_if_conditions():
    """GitHub rejects the whole workflow file when a status-check function is
    used outside an ``if``; every push then records a "workflow file issue"
    failure and dispatches cannot start."""
    misplaced = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, path + (str(key),))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, path + (str(index),))
        elif isinstance(node, str) and path[-1] != "if" and "${{" in node and STATUS_FUNCTIONS.search(node):
            misplaced.append(".".join(path))

    walk(WORKFLOW, ())
    assert misplaced == []


def test_coordinate_dev_release_verifies_and_seals_final_candidate():
    ids = [item.get("id") for item in COORDINATE["steps"]]
    assert "final_artifact_verify" in ids
    assert ids.index("frontend_release") < ids.index("final_artifact_verify") < ids.index("release_evidence_upload")
    verify_step = step(COORDINATE, "final_artifact_verify")
    assert "steps.frontend_release.outcome == 'success'" in verify_step["if"]
    assert "compensate_cross_repo_release.sh" in verify_step["run"]
    assert verify_step["env"]["PANTHEON_DEV_ARTIFACT_OPERATION"] == "verify"
    assert verify_step["env"]["PANTHEON_DEV_ARTIFACT_TARGET"] == "final"
    assert verify_step["env"]["PANTHEON_ROLLBACK_EVIDENCE_OUT"] == "${{ env.RELEASE_EVIDENCE_DIR }}/candidate/candidate-frontend.json"
    assert verify_step["env"]["PANTHEON_ENVIRONMENT_LEASE_TOKEN"] == "${{ secrets.COORDINATION_REPO_TOKEN }}"

    assert verify_step["env"]["PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE"] == "${{ vars.DEV_DEPLOY_WORKTREE_ROOT && format('{0}/dev-{1}/docker-compose.yml', vars.DEV_DEPLOY_WORKTREE_ROOT, inputs.component == 'bff' && 'bff' || 'root') || '' }}"
    assert verify_step["env"]["PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPONENT"] == "${{ inputs.component == 'bff' && 'bff' || 'root' }}"
    assert verify_step["env"]["DEV_BFF_AUTH_MODE"] == "strict"
    assert verify_step["env"]["DEV_BFF_JWT_SECRET"] == "${{ secrets.DEV_BFF_JWT_SECRET }}"
    assert verify_step["env"]["DEV_BFF_OIDC_CLIENT_ID"] == "${{ secrets.DEV_BFF_OIDC_CLIENT_ID }}"
    assert verify_step["env"]["DEV_BFF_OIDC_CLIENT_SECRET"] == "${{ secrets.DEV_BFF_OIDC_CLIENT_SECRET }}"
    assert verify_step["env"]["DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID"] == "${{ vars.DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID || 'pantheon-dev-viewer-v1' }}"
    assert verify_step["env"]["DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET"] in (
        "${{ secrets.DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET }}",
        "${{ secrets['DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET'] }}",
    )
    assert verify_step["env"]["DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN"] == "${{ secrets.DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN }}"
    assert "PANTHEON_EXECUTE_PLANS_ROOT" in verify_step["env"]
    assert "PANTHEON_DEV_FE_DEPLOY_SCRIPT" in verify_step["env"]


def test_coordinate_dev_release_roundtrip_opt_in_steps():
    on_section = WORKFLOW.get(True) or WORKFLOW.get("on")
    inputs = on_section["workflow_dispatch"]["inputs"]
    assert "run_exact_artifact_roundtrip" in inputs
    assert inputs["run_exact_artifact_roundtrip"]["type"] == "boolean"
    assert inputs["run_exact_artifact_roundtrip"]["default"] is False

    ids = [item.get("id") for item in COORDINATE["steps"]]
    assert "roundtrip_baseline_restore" in ids
    assert "roundtrip_final_return" in ids
    assert ids.index("final_artifact_verify") < ids.index("roundtrip_baseline_restore") < ids.index("roundtrip_final_return") < ids.index("compensation")

    base_step = step(COORDINATE, "roundtrip_baseline_restore")
    assert "inputs.run_exact_artifact_roundtrip" in base_step["if"]
    assert "steps.final_artifact_verify.outcome == 'success'" in base_step["if"]
    assert base_step["env"]["PANTHEON_DEV_ARTIFACT_OPERATION"] == "restore"
    assert base_step["env"]["PANTHEON_DEV_ARTIFACT_TARGET"] == "baseline"
    assert "roundtrip-baseline-readback.json" in base_step["env"]["PANTHEON_ROLLBACK_EVIDENCE_OUT"]

    final_step = step(COORDINATE, "roundtrip_final_return")
    assert "inputs.run_exact_artifact_roundtrip" in final_step["if"]
    assert "steps.roundtrip_baseline_restore.outcome == 'success'" in final_step["if"]
    assert final_step["env"]["PANTHEON_DEV_ARTIFACT_OPERATION"] == "restore-final"
    assert final_step["env"]["PANTHEON_DEV_ARTIFACT_TARGET"] == "final"
    assert "roundtrip-final-readback.json" in final_step["env"]["PANTHEON_ROLLBACK_EVIDENCE_OUT"]

    comp_step = step(COORDINATE, "compensation")
    assert "steps.roundtrip_baseline_restore.outcome != 'success'" in comp_step["if"]
    assert "steps.roundtrip_final_return.outcome != 'success'" in comp_step["if"]




def _eval_gha(expression, outcomes, roundtrip, requested="true"):
    import re

    expr = expression.strip()
    if expr.startswith("${{"):
        expr = expr[3:-2].strip()
    expr = re.sub(r"steps\.(\w+)\.outputs\.evidence_dir", "'x'", expr)
    expr = re.sub(
        r"steps\.(\w+)\.conclusion",
        lambda m: repr("skipped" if outcomes.get(m.group(1), "skipped") == "skipped" else "failure"),
        expr,
    )
    expr = re.sub(r"steps\.(\w+)\.outcome", lambda m: repr(outcomes.get(m.group(1), "skipped")), expr)
    expr = expr.replace("inputs.run_exact_artifact_roundtrip", repr(roundtrip))
    expr = expr.replace("needs.deploy-dev.outputs.paper_bootstrap_requested", repr(requested))
    expr = expr.replace("always()", "True").replace("&&", " and ").replace("||", " or ")
    expr = re.sub(r"!(?!=)", " not ", expr)
    return eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - fixed repo-owned expression


def test_retirement_requires_complete_roundtrip_or_successful_compensation():
    ids = [item.get("id") for item in COORDINATE["steps"]]
    assert (
        ids.index("final_artifact_verify")
        < ids.index("roundtrip_retire_release_case")
        < ids.index("roundtrip_baseline_restore")
        < ids.index("roundtrip_final_return")
        < ids.index("rollback_retire_release_case")
        < ids.index("compensation")
        < ids.index("retire_release_cases")
        < ids.index("release_evidence_upload")
    )
    roundtrip_retire = step(COORDINATE, "roundtrip_retire_release_case")
    rollback_retire = step(COORDINATE, "rollback_retire_release_case")
    accepted_retire = step(COORDINATE, "retire_release_cases")
    assert roundtrip_retire["env"]["RELEASE_OUTCOME"] == "pre_restore"
    assert rollback_retire["env"]["RELEASE_OUTCOME"] == "pre_restore"
    assert accepted_retire["env"]["RELEASE_OUTCOME"] == "accepted"
    assert "steps.compensation.outcome" not in rollback_retire["if"]

    base = {"frontend_release": "success", "final_artifact_verify": "success"}

    # Roundtrip opt-in retires current run case before roundtrip_baseline_restore.
    assert bool(_eval_gha(roundtrip_retire["if"], base, True, "true")) is True
    assert bool(_eval_gha(roundtrip_retire["if"], base, False, "true")) is False
    assert bool(_eval_gha(roundtrip_retire["if"], base, True, "false")) is False

    # Rejected release retires current run case before compensation, without waiting for compensation success.
    failed = {**base, "roundtrip_baseline_restore": "failure"}
    assert bool(_eval_gha(rollback_retire["if"], failed, True, "true")) is True
    assert bool(_eval_gha(rollback_retire["if"], {"frontend_release": "failure"}, False, "true")) is True
    assert bool(_eval_gha(rollback_retire["if"], failed, True, "false")) is False
    assert bool(_eval_gha(rollback_retire["if"], base, False, "true")) is False

    # Accepted cleanup of non-served cases runs only on accepted non-roundtrip releases with paper requested.
    assert bool(_eval_gha(accepted_retire["if"], base, False, "true")) is True
    assert bool(_eval_gha(accepted_retire["if"], base, True, "true")) is False
    assert bool(_eval_gha(accepted_retire["if"], failed, True, "true")) is False
    assert bool(_eval_gha(accepted_retire["if"], base, False, "false")) is False


def test_circular_wait_with_active_forward_case_and_recovery_via_pre_restore_retirement(tmp_path):
    import json
    import sys
    from scripts import dev_release_artifacts as artifacts

    store_file = tmp_path / "provisioning-store.json"
    store_file.write_text(
        json.dumps(
            [
                {
                    "idempotency_key": "dev-paper-release-42390000000-1",
                    "created_at": "2026-11-06T00:00:00Z",
                    "state": "succeeded",
                    "tenant_id": "tenant-dev",
                    "persona_id": "persona-prior",
                    "has_expression_policy": False,
                },
                {
                    "idempotency_key": "dev-paper-release-42397678868-1",
                    "created_at": "2026-11-07T18:00:00Z",
                    "state": "succeeded",
                    "tenant_id": "tenant-dev",
                    "persona_id": "persona-current",
                    "has_expression_policy": True,
                },
                {
                    "idempotency_key": "operator-manual-case-1",
                    "created_at": "2026-11-07T12:00:00Z",
                    "state": "succeeded",
                    "tenant_id": "tenant-dev",
                    "persona_id": "persona-operator",
                    "has_expression_policy": True,
                },
            ]
        ),
        encoding="utf-8",
    )

    bff_Img = "sha256:" + "1" * 64
    prod_img = "sha256:" + "2" * 64
    stack = {
        "schema_version": artifacts.STACK_SCHEMA,
        "source_sha": "e9a1a196805663b175423964c2ccdc21bf2d24ea",
        "services": {
            "operator-bff": {
                "image_id": bff_Img,
                "state": "running",
                "has_healthcheck": True,
                "env": {"PANTHEON_TENANT_ID": "tenant-dev", "GIT_SHA": "e9a1a196805663b175423964c2ccdc21bf2d24ea"},
            },
            "market-signal-producer": {
                "image_id": prod_img,
                "state": "running",
                "has_healthcheck": True,
                "env": {"PANTHEON_TENANT_ID": "tenant-dev", "GIT_SHA": "e9a1a196805663b175423964c2ccdc21bf2d24ea"},
            },
        },
    }

    class _BaselineDocker:
        def call(self, *args):
            records = json.loads(store_file.read_text(encoding="utf-8"))
            unsupported_active = any(
                r["idempotency_key"].startswith("dev-paper-release-")
                and r["state"] != "retired"
                and r.get("has_expression_policy")
                for r in records
            )
            producer_health = "unhealthy" if unsupported_active else "healthy"
            if args[:2] == ("ps", "--all"):
                return f"{1:064x}\n{2:064x}\n"
            if args[:2] == ("inspect", "--format") and "com.docker.compose.service" in args[2]:
                if args[-1] == f"{1:064x}":
                    return json.dumps(
                        {
                            "service": "operator-bff",
                            "oneoff": "False",
                            "image_id": bff_Img,
                            "status": "running",
                            "exit_code": 0,
                            "health_test": ["CMD", "true"],
                            "health": "healthy",
                        }
                    )
                return json.dumps(
                    {
                        "service": "market-signal-producer",
                        "oneoff": "False",
                        "image_id": prod_img,
                        "status": "running",
                        "exit_code": 0,
                        "health_test": ["CMD", "true"],
                        "health": producer_health,
                    }
                )
            return '"PANTHEON_TENANT_ID=tenant-dev"\n"GIT_SHA=e9a1a196805663b175423964c2ccdc21bf2d24ea"\n'

    # 1. Without pre-restore retirement, older baseline producer rejects active expression_policy case.
    ticks = iter([0.0, 10.0, 20.0])
    with pytest.raises(artifacts.StackMixedError) as exc_info:
        artifacts.readback_stack(
            stack,
            docker=_BaselineDocker(),
            check_lease=lambda: None,
            sleep=lambda _: None,
            monotonic=lambda: next(ticks),
            budget_seconds=5,
        )
    assert exc_info.value.services == ("market-signal-producer",)

    # 2. Run the actual workflow pre-restore retirement steps against the store.
    workspace = tmp_path / "ws"
    for subdir in (".agora-gate-controller/scripts", ".target/scripts"):
        ctrl_scripts = workspace / subdir
        ctrl_scripts.mkdir(parents=True)
        (ctrl_scripts / "retire_dev_paper_release_cases.py").write_bytes(
            (ROOT / "scripts/retire_dev_paper_release_cases.py").read_bytes()
        )
    runner_py = tmp_path / "container_runner.py"
    runner_py.write_text(
        "import json, pathlib, sys, types\n"
        "from dataclasses import dataclass\n"
        f"store_path = pathlib.Path({str(store_file)!r})\n"
        "@dataclass\n"
        "class _Rec:\n"
        "    idempotency_key: str\n"
        "    created_at: str\n"
        "    state: str\n"
        "    tenant_id: str\n"
        "    persona_id: str\n"
        "class _Store:\n"
        "    def list_all(self):\n"
        "        return [\n"
        "            _Rec(r['idempotency_key'], r['created_at'], r['state'], r['tenant_id'], r['persona_id'])\n"
        "            for r in json.loads(store_path.read_text(encoding='utf-8'))\n"
        "        ]\n"
        "def _retire_case(tenant_id, persona_id, *, reason):\n"
        "    rows = json.loads(store_path.read_text(encoding='utf-8'))\n"
        "    for r in rows:\n"
        "        if r['tenant_id'] == tenant_id and r['persona_id'] == persona_id:\n"
        "            r['state'] = 'retired'\n"
        "            r['retire_reason'] = reason\n"
        "    store_path.write_text(json.dumps(rows), encoding='utf-8')\n"
        "    return {'persona_id': persona_id, 'state': 'retired', 'retirement': {'status': 'retired'}}\n"
        "code = sys.stdin.read()\n"
        "mod = types.ModuleType('retire_mod')\n"
        "exec(compile(code, 'retire_dev_paper_release_cases.py', 'exec'), mod.__dict__)\n"
        "mod._build_runtime = lambda: (_Store(), _retire_case)\n"
        "raise SystemExit(mod.main(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    for subdir in (".agora-gate-controller/scripts", ".target/scripts"):
        transport = workspace / subdir / "dev_vm_ssh.sh"
        transport.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if [[ \"${1:-}\" == \"prepare\" ]]; then\n"
            "  mkdir -p \"$2\" && touch \"$2/deploy_key\" \"$2/known_hosts\"\n"
            "  exit 0\n"
            "fi\n"
            "cmd=\"$2\"\n"
            "args=\"${cmd#*python - }\"\n"
            f"exec \"{sys.executable}\" \"{runner_py}\" ${{args}}\n",
            encoding="utf-8",
        )
        transport.chmod(0o755)
    creds = tmp_path / "pantheon-dev-ssh-42397678868-1"
    creds.mkdir()
    (creds / "deploy_key").write_text("k", encoding="utf-8")
    (creds / "known_hosts").write_text("h", encoding="utf-8")
    evidence_dir = tmp_path / "evidence"
    summary_file = tmp_path / "summary.md"
    initial_store_json = store_file.read_text(encoding="utf-8")
    for job, step_id in (
        (DEPLOY, "retire_release_cases"),
        (COORDINATE, "roundtrip_retire_release_case"),
        (COORDINATE, "rollback_retire_release_case"),
    ):
        store_file.write_text(initial_store_json, encoding="utf-8")
        retire_step = step(job, step_id)
        env = {
            **os.environ,
            "GITHUB_WORKSPACE": str(workspace),
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_RUN_ID": "42397678868",
            "GITHUB_RUN_ATTEMPT": "1",
            "RETIRE_EVIDENCE_DIR": str(evidence_dir),
            "RELEASE_OUTCOME": retire_step["env"]["RELEASE_OUTCOME"],
            "GITHUB_STEP_SUMMARY": str(summary_file),
        }
        proc = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-uo", "pipefail", "-c", retire_step["run"]],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        evidence = json.loads((evidence_dir / "dev-paper-release-case-retire.json").read_text(encoding="utf-8"))
        assert evidence["outcome"] == "pre_restore"
        assert evidence["preparation_only"] is True
        assert evidence["status"] == "retired"
        assert evidence["retire"] == ["dev-paper-release-42397678868-1"]
        assert evidence["results"] == {"dev-paper-release-42397678868-1": "retired"}

    rows_after = {r["idempotency_key"]: r for r in json.loads(store_file.read_text(encoding="utf-8"))}
    assert rows_after["dev-paper-release-42397678868-1"]["state"] == "retired"
    assert rows_after["dev-paper-release-42390000000-1"]["state"] == "succeeded"
    assert rows_after["operator-manual-case-1"]["state"] == "succeeded"

    # 3. Baseline stack readback now succeeds without circular wait.
    readback = artifacts.readback_stack(
        stack,
        docker=_BaselineDocker(),
        check_lease=lambda: None,
        sleep=lambda _: None,
        monotonic=lambda: 0.0,
        budget_seconds=5,
    )
    assert readback["scope"] == "stack"
    assert set(readback["services"]) == {"operator-bff", "market-signal-producer"}


def test_pre_restore_retirement_failure_records_error_evidence_and_never_blocks_restore(tmp_path):
    import json
    retire_step = step(DEPLOY, "retire_release_cases")
    evidence_dir = tmp_path / "evidence"
    summary_file = tmp_path / "summary.md"
    env = {
        **os.environ,
        "GITHUB_WORKSPACE": str(tmp_path / "missing-ws"),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_RUN_ID": "42397678868",
        "GITHUB_RUN_ATTEMPT": "1",
        "RETIRE_EVIDENCE_DIR": str(evidence_dir),
        "RELEASE_OUTCOME": retire_step["env"]["RELEASE_OUTCOME"],
        "GITHUB_STEP_SUMMARY": str(summary_file),
    }
    proc = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-uo", "pipefail", "-c", retire_step["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    evidence = json.loads((evidence_dir / "dev-paper-release-case-retire.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "error"
    assert evidence["outcome"] == "pre_restore"
    assert evidence["preparation_only"] is True
    assert "retirement script or transport missing" in evidence["error"]

    # Also verify remote runner failure records fallback error JSON and appends to GITHUB_STEP_SUMMARY without blocking.
    workspace = tmp_path / "ws"
    ctrl_scripts = workspace / ".agora-gate-controller/scripts"
    ctrl_scripts.mkdir(parents=True)
    (ctrl_scripts / "retire_dev_paper_release_cases.py").write_bytes(
        (ROOT / "scripts/retire_dev_paper_release_cases.py").read_bytes()
    )
    transport = ctrl_scripts / "dev_vm_ssh.sh"
    transport.write_text("#!/usr/bin/env bash\nexit 17\n", encoding="utf-8")
    transport.chmod(0o755)
    creds = tmp_path / "pantheon-dev-ssh-42397678868-1"
    creds.mkdir()
    (creds / "deploy_key").write_text("k", encoding="utf-8")
    (creds / "known_hosts").write_text("h", encoding="utf-8")
    env["GITHUB_WORKSPACE"] = str(workspace)
    proc_fail = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-uo", "pipefail", "-c", retire_step["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_fail.returncode == 0
    evidence_fail = json.loads((evidence_dir / "dev-paper-release-case-retire.json").read_text(encoding="utf-8"))
    assert evidence_fail["status"] == "error"
    assert evidence_fail["outcome"] == "pre_restore"
    assert evidence_fail["preparation_only"] is True
    assert evidence_fail["error"] == "retirement produced no parseable evidence"

    # And when remote helper emits structured JSON error evidence, it is preserved and summarized without blocking.
    transport.write_text(
        "#!/usr/bin/env bash\n"
        "printf '{\"schema_version\":\"pantheon.dev-paper-release-case-retire.v2\",\"status\":\"error\",\"outcome\":\"pre_restore\",\"preparation_only\":true,\"error\":\"RuntimeError: store unavailable\"}\\n'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    proc_json_err = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-uo", "pipefail", "-c", retire_step["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_json_err.returncode == 0
    evidence_json_err = json.loads((evidence_dir / "dev-paper-release-case-retire.json").read_text(encoding="utf-8"))
    assert evidence_json_err["status"] == "error"
    assert evidence_json_err["preparation_only"] is True
    assert "pre_restore" in summary_file.read_text(encoding="utf-8")


def test_roundtrip_legs_share_final_verify_candidate_compose_context():
    verify = step(COORDINATE, "final_artifact_verify")["env"]
    for step_id in ("roundtrip_baseline_restore", "roundtrip_final_return"):
        env = step(COORDINATE, step_id)["env"]
        for key in ("PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE", "PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPONENT"):
            assert env[key] == verify[key]


def test_compensation_helper_binds_fe_controller_to_admitted_frontend_run():
    text = (ROOT / "scripts/compensate_cross_repo_release.sh").read_text()
    assert "release-controller-accepted.json" in text
    assert 'receipt["frontend_deploy"]["run_id"]' in text
    assert "client.get_run(run_id)" in text
    assert 'export PANTHEON_DEV_FE_CONTROLLER_SHA="${fe_controller_sha}"' in text
    assert "CROSS_REPO_RELEASE_TOKEN=\"${lease_token}\"" in text
    assert "${PANTHEON_ENVIRONMENT_LEASE_TOKEN}" not in text.split("fe_controller_sha=\"\"", 1)[1].split("extra_deploy_args", 1)[0]
    # Neither the BE controller SHA nor the FE candidate SHA is a fallback.
    assert "PANTHEON_DEV_ARTIFACT_CONTROLLER_SHA" not in text.split("fe_controller_sha=\"\"", 1)[1].split("extra_deploy_args", 1)[0]
    assert "PANTHEON_DEV_FE_CONTROLLER_SHA" in (ROOT / "scripts/deploy_nonprod_vm.sh").read_text()


def _guarded_advisory_source(step_id):
    body = step(DEPLOY, step_id)["run"]
    start = body.index("guarded_advisory() {")
    marker = 'return "${child_rc}"\n}\n'
    return body[start:body.index(marker, start) + len(marker)]


@pytest.mark.parametrize("step_id", ["market_refresh", "paper_bootstrap", "agora"])
@pytest.mark.parametrize("strict,child_rc", [("false", 0), ("false", 7), ("true", 0), ("true", 7)])
def test_guarded_advisory_never_makes_the_lease_guard_see_a_failure(tmp_path, step_id, strict, child_rc):
    """The pinned guard quarantines the lease when its command exits non-zero, so
    advisory mode must hand the guard an exit 0 and still report the real code."""
    guard_dir = tmp_path / "ws/.lease-controller/scripts"
    guard_dir.mkdir(parents=True)
    guard = guard_dir / "run_with_dev_environment_lease.sh"
    guard.write_text(
        '#!/bin/bash\n"$@"\nrc=$?\n'
        f'echo "$rc" > {tmp_path}/guard_saw\n'
        f'[[ $rc -eq 0 ]] || echo quarantined > {tmp_path}/quarantine\n'
        "exit $rc\n")
    guard.chmod(0o755)
    script = (
        "set -euo pipefail\n"
        f'controller="{tmp_path}/ws/.lease-controller"; lease_token=x\n'
        + _guarded_advisory_source(step_id)
        + f'rc=0\nguarded_advisory "{tmp_path}/child.rc" bash -c "exit {child_rc}" || rc=$?\necho "$rc"\n')
    result = subprocess.run(
        ["bash", "-c", script], text=True, capture_output=True, timeout=30,
        env={**os.environ, "FUNCTIONAL_CHECKS_STRICT": strict, "GITHUB_WORKSPACE": str(tmp_path / "ws")})
    assert result.stdout.strip() == str(child_rc), result.stderr
    guard_saw = int((tmp_path / "guard_saw").read_text())
    if strict == "false":
        assert guard_saw == 0 and not (tmp_path / "quarantine").exists()
    else:
        assert guard_saw == child_rc


def test_steady_restart_loop_step_gates_compensation_and_release():
    import re
    body = (Path(__file__).resolve().parents[1] / ".github/workflows/nonprod-deploy.yml").read_text(encoding="utf-8")
    assert "id: steady_restart_loop" in body
    assert body.count("steps.steady_restart_loop.outcome != 'success'") == 2
    assert "STEADY_RESTART_LOOP_OUTCOME" in body
    step = body[body.index("id: steady_restart_loop\n"):body.index("Upload steady restart-loop evidence")]
    assert "sleep 90" in step and "--mode verify-restart-loop" in step
    assert ".State.Health" not in step and "readiness" not in step.split("run: |")[1].lower()


def test_job_script_paths_have_a_matching_checkout():
    """A job that checks out only into sub-directories has no scripts/ at the
    workspace root. Root 44172901470 passed the stack check and was rolled back
    because the restart-loop step ran ${GITHUB_WORKSPACE}/scripts/... in the
    deploy-dev job, which checks out only .target, .agora-gate-controller,
    .agora-frontend and .lease-controller."""
    workflow = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    reference = re.compile(r"\$\{?GITHUB_WORKSPACE\}?/((?:\.[A-Za-z0-9_-]+/)?)scripts\b")
    problems = []
    for job_name, job in document["jobs"].items():
        steps = job.get("steps") or []
        checkout_paths = {
            str((step.get("with") or {}).get("path") or "")
            for step in steps
            if str(step.get("uses") or "").startswith("actions/checkout@")
        }
        if not checkout_paths:
            continue  # the job fetches the repository itself with git
        for step in steps:
            for match in reference.finditer(str(step.get("run") or "")):
                if match.group(1).rstrip("/") not in checkout_paths:
                    problems.append(f"{job_name} / {step.get('name')}: {match.group(0)}")
    assert not problems, "script path without a matching checkout in the same job: " + "; ".join(problems)


def _run_enforce_step(tmp_path, values, with_candidate_frontend=True):
    """Run the real outcome-enforcing step in a bare checkout layout of its own job."""
    enforce = next(
        item for item in COORDINATE["steps"]
        if item.get("name") == "Enforce accepted or proven compensated outcome"
    )
    rendered = re.sub(
        r"\$\{\{\s*(.*?)\s*\}\}", lambda match: values.get(match.group(1), ""), enforce["run"]
    )
    workspace = tmp_path / "workspace"
    evidence = workspace / "evidence"
    (evidence / "candidate").mkdir(parents=True)
    if with_candidate_frontend:
        (evidence / "candidate" / "candidate-frontend.json").write_text("{}")
    (workspace / ".target").mkdir()  # the coordinator's own checkout layout
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", rendered],
        cwd=workspace, text=True, capture_output=True,
        env={"PATH": os.environ["PATH"], "RELEASE_EVIDENCE_DIR": str(evidence),
             "GITHUB_STEP_SUMMARY": str(summary)},
    )
    return result, summary.read_text() if summary.exists() else ""


def _enforce_values(**overrides):
    values = {
        "inputs.run_exact_artifact_roundtrip": "false",
        "steps.frontend_release.outcome": "success",
        "steps.final_artifact_verify.outcome": "success",
        "steps.frontend_release.outputs.release_outcome": "",
        "steps.frontend_release.outputs.gate_run_id": "44470794822",
        "steps.frontend_release.outputs.failed_gates": "Gate 1 - npm run test",
        "steps.compensation.outcome": "skipped",
    }
    values.update(overrides)
    return values


def test_deployed_not_accepted_release_exits_zero_and_names_gate_and_owner(tmp_path):
    result, summary = _run_enforce_step(
        tmp_path,
        _enforce_values(**{
            "steps.frontend_release.outputs.release_outcome": "deployed_not_accepted",
            "steps.final_artifact_verify.outcome": "skipped",
        }),
        with_candidate_frontend=False,
    )
    assert result.returncode == 0, result.stderr
    assert "deployed, NOT accepted" in summary
    assert "44470794822" in summary and "Gate 1 - npm run test" in summary
    assert "owning chain: frontend" in summary


def test_accepted_release_summary_is_unchanged_by_the_new_outcome(tmp_path):
    result, summary = _run_enforce_step(tmp_path, _enforce_values())
    assert result.returncode == 0, result.stderr
    assert "Exact FE/BFF release accepted" in summary


def test_failed_frontend_release_still_requires_compensation(tmp_path):
    result, summary = _run_enforce_step(
        tmp_path, _enforce_values(**{"steps.frontend_release.outcome": "failure"})
    )
    assert result.returncode == 75
    assert "rollback NOT proven" in summary


def test_controller_gets_manifest_and_target_environment_without_new_dispatch_inputs():
    run = step(COORDINATE, "frontend_release")["run"]
    assert '--compatibility-manifest-file "${manifest_file}"' in run
    assert '--environment "${TARGET_ENV}"' in run
    inputs = set(WORKFLOW[True]["workflow_dispatch"]["inputs"])
    assert not {name for name in inputs if "unchanged" in name or "tolerate" in name or "gate_failure" in name}


def test_gate_failure_marks_acceptance_verdict_failed_and_frontend_owned(tmp_path):
    acceptance = WORKFLOW["jobs"]["dev-acceptance-stage"]
    record = next(
        item for item in acceptance["steps"]
        if item.get("name") == "Record frontend gate failure for the unchanged served frontend"
    )
    assert "deployed_not_accepted" in record["if"]
    verdict = tmp_path / "acceptance-verdict.json"
    verdict.write_text('{"acceptance": "pending", "failed": [], "checks": []}')
    subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", record["run"]],
        check=True, cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "VERDICT_FILE": str(verdict),
             "GATE_RUN_ID": "44470794822", "FAILED_GATES": "Gate 1 - npm run test"},
    )
    import json
    result = json.loads(verdict.read_text())
    assert result["acceptance"] == "failed"
    assert result["failed"][0]["owner"] == "frontend"
    assert result["deployed"] is True and result["accepted"] is False
    assert result["owning_chain"] == "frontend"
    assert result["gate_run_id"] == "44470794822"
    assert result["failed_gates"] == ["Gate 1 - npm run test"]
    assert "44470794822" in result["failed"][0]["detail"]
    assert "Gate 1 - npm run test" in result["failed"][0]["detail"]


def test_final_artifact_verify_and_compensation_skip_deployed_not_accepted():
    verify = step(COORDINATE, "final_artifact_verify")
    assert "release_outcome != 'deployed_not_accepted'" in verify["if"]
    comp = step(COORDINATE, "compensation")
    # compensation keys off final_artifact_verify being non-skipped, so a skipped verify never compensates
    assert "final_artifact_verify.conclusion != 'skipped'" in comp["if"]
