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


def test_baseline_is_guarded_and_uploaded_before_candidate_mutation():
    order = [item.get("id") for item in DEPLOY["steps"]]
    required = ["release_admission", "rollback_baseline", "lease", "artifact_baseline",
                "artifact_baseline_upload", "deploy", "artifact_candidate_seal",
                "artifact_candidate_upload", "paper_bootstrap", "public_smoke"]
    assert sorted(required, key=order.index) == required
    capture = step(DEPLOY, "artifact_baseline")
    assert "run_with_dev_environment_lease.sh" in capture["run"]
    assert "52276793f99162fc7ca307a1370addd8d99478208ebf7beb67eab23b97b83048" in capture["run"]
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


MANDATORY = ("heartbeat", "deploy", "paper_bootstrap", "public_smoke",
             "deploy-posture-evidence", "agora", "artifact_baseline_upload",
             "artifact_candidate_seal", "artifact_candidate_upload")


def run_completion(tmp_path, component, outcomes, cancelled=False, fault=None):
    """Run the production cleanup tail with only lease transport/stop stubbed."""
    cleanup = step(DEPLOY, "lease_cleanup")
    values = {"env.TARGET_COMPONENT": component,
              **{f"steps.{key}.outcome": value for key, value in outcomes.items()}}
    env = {**os.environ, "TARGET_COMPONENT": component}
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
    if component == "bff":
        outcomes["paper_bootstrap"] = "skipped"
    outcomes[failed] = outcome
    result, admitted, compensate, calls = run_completion(tmp_path, component, outcomes)
    inapplicable = component == "bff" and failed == "paper_bootstrap"
    accepted = inapplicable and outcome == "skipped"
    assert result.returncode == 0, result.stderr
    assert admitted is accepted
    assert compensate is (not inapplicable)
    assert ("release" in calls) is accepted


@pytest.mark.parametrize("component", ["auto", "root", "bff", "sidecar"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_success_and_cancellation_follow_loaded_admission_and_compensation(tmp_path, component, cancelled):
    outcomes = dict.fromkeys((*MANDATORY, "lease", "artifact_baseline"), "success")
    if component == "bff":
        outcomes["paper_bootstrap"] = "skipped"
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
    values.update({"env.TARGET_COMPONENT": "root", "steps.agora.outcome": "failure",
                   f"steps.{prerequisite}.outcome": outcome})
    assert not evaluate(step(DEPLOY, "deploy_compensation")["if"], values)
    assert not evaluate(DEPLOY["outputs"]["bff_fe_pair_verified"], values)


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
