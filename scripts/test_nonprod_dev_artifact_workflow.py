"""Cross-job artifact seams, not evidence of a hosted deployment or rollback."""
from pathlib import Path
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
    admission = DEPLOY["outputs"]["bff_fe_pair_verified"]
    for name in ("deploy", "public_smoke", "artifact_baseline_upload", "artifact_candidate_seal", "artifact_candidate_upload"):
        assert f"steps.{name}.outcome == 'success'" in admission


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
