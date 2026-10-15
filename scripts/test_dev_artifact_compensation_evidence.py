"""Offline compensation contracts; fixtures are not hosted rollback evidence."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest

from scripts import dev_artifact_compensation_evidence as e


def write(path, raw):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


@pytest.fixture
def case(tmp_path, monkeypatch):
    identity = dict(candidate_id="c" * 64, run_id="123456", attempt="2", controller_sha="d" * 40,
                    candidate_backend_sha="e" * 40, candidate_frontend_sha="f" * 40,
                    previous_backend_sha="a" * 40, previous_frontend_sha="b" * 40)
    bundle = {"schema_version": "pantheon.dev-bff-image-bundle.v1", "source_sha": "a" * 40,
              "services": {}, "archives": {}}
    for index, service in enumerate(e.candidate.SERVICES, 1):
        image = "sha256:" + str(index) * 64
        bundle["services"][service] = {"image_id": image, "oci_revision": None, "repo_digests": None}
        bundle["archives"][image] = {"name": image[7:] + "-" + "9" * 64 + ".tar", "sha256": "9" * 64, "size": 123}
    lease = "12345678-1234-4234-8234-123456789abc"
    baseline = {"schema_version": "pantheon.dev-release-artifact-baseline.v1", "environment": "dev",
                "project_id": "pantheon-dev-20260902", "vm": "pantheon-dev-deploy", "identity": deepcopy(identity),
                "capture_lease_id": lease, "captured_at": "2026-09-09T00:00:00Z", "image_bundle": bundle,
                "image_bundle_sha256": e.capture.digest(e.capture.encoded(bundle)), "compose_sha256": "8" * 64,
                "frontend": {"target": "/var/www/pantheon-dev-fe-releases/prior-fixture", "dist_sha256": "7" * 64,
                             "manifest_sha256": "6" * 64, "frontend_sha": "b" * 40, "backend_sha": "a" * 40},
                "baseline_nonsecret_config": {"PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN_FILE": None,
                                              "PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID": "",
                                              "PERSONA_EVALUATOR_BFF_TOKEN_FILE": None,
                                              "PERSONA_EVALUATOR_GOVERNANCE_TOKEN_FILE": None,
                                              **dict.fromkeys(e.capture.artifacts.BASELINE_AUTH_FLAGS, "false")}}
    baseline_hash = e.capture.digest(e.capture.encoded(baseline))
    record = {"schema_version": "pantheon.dev-candidate-image-admission.v1", "environment": "dev",
              "project_id": "pantheon-dev-20260902", "vm": "pantheon-dev-deploy", "identity": deepcopy(identity),
              "seal_lease_id": lease, "sealed_at": "2026-09-09T00:01:00Z", "baseline_manifest_sha256": baseline_hash,
              "candidate_compose_sha256": "5" * 64,
              "services": {service: {"image_id": "sha256:" + str(index) * 64, "oci_revision": "e" * 40,
                                     "git_sha": None if service == "loop-run-projector-scheduler" else "e" * 40,
                                     "compose_image": "pantheon-" + service}
                           for index, service in enumerate(e.candidate.SERVICES, 4)}}
    override = {"services": {service: {"image": row["image_id"], "pull_policy": "never"}
                             for service, row in record["services"].items()}}
    override["services"]["operator-bff"]["environment"] = {
        "BFF_IMAGE_DIGEST": record["services"]["operator-bff"]["image_id"]}
    record["image_override_sha256"] = e.capture.digest(e.capture.encoded(override))
    folder = e.capture.ARTIFACT_ROOT / f"baseline-123456-2-{identity['candidate_id']}"
    receipt = {"candidate_image_manifest_path": str(folder / "candidate-images.json"),
               "candidate_image_manifest_sha256": e.capture.digest(e.capture.encoded(record)),
               "candidate_image_manifest": record, "candidate_image_override_path": str(folder / "candidate-images.override.json"),
               "candidate_image_override_sha256": record["image_override_sha256"]}
    retained = tmp_path / "pantheon-dev-artifacts-123456-2"
    baseline_path = write(retained / "baseline/artifact-baseline.json", e.capture.encoded(baseline))
    receipt_path = write(retained / "candidate/candidate-receipt.json", e.capture.encoded(receipt))
    retained.chmod(0o700)
    env = {"TARGET_ENV": "dev", "GCP_DEPLOY_PROJECT_ID": "pantheon-dev-20260902", "DEV_VM": "pantheon-dev-deploy",
           "DEV_ZONE": "asia-east1-b", "RUNNER_TEMP": str(tmp_path), "PANTHEON_RELEASE_REPO_ROOT": str(tmp_path),
           "DEV_REMOTE_DIR": "/home/chloe_ong_dev_cctech_support_com/pantheon",
           e.ENV_PREFIX + "CANDIDATE_COMPOSE_FILE": "/home/chloe_ong_dev_cctech_support_com/pantheon/docker-compose.yml",
           "GITHUB_RUN_ID": "123456", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_REPOSITORY": "ajoe734/pantheon",
           "PANTHEON_RELEASE_CANDIDATE_ID": "c" * 64, "PANTHEON_FAILED_BACKEND_SHA": "e" * 40,
           "PANTHEON_FAILED_FRONTEND_SHA": "f" * 40, "PANTHEON_ROLLBACK_BACKEND_SHA": "a" * 40,
           "PANTHEON_ROLLBACK_FRONTEND_SHA": "b" * 40, e.ENV_PREFIX + "CONTROLLER_SHA": "d" * 40,
           e.ENV_PREFIX + "BASELINE_FILE": str(baseline_path), e.ENV_PREFIX + "MANIFEST_SHA256": baseline_hash,
           e.ENV_PREFIX + "CANDIDATE_RECEIPT_FILE": str(receipt_path),
           e.ENV_PREFIX + "CANDIDATE_IMAGE_MANIFEST_SHA256": receipt["candidate_image_manifest_sha256"],
           "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": "87654321-4321-4321-8321-cba987654321",
           "GITHUB_TOKEN": "fixture-secret-never-export", "DEV_BFF_JWT_SECRET": "fixture-secret-never-export"}
    monkeypatch.setattr(e.capture, "read_implementation", lambda root, name, sha: ("# committed " + name + "\n").encode())
    value = {"schema_version": "pantheon.dev-artifact-readback.v1", "operation": "restore", "manifest_sha256": baseline_hash,
             "identity": deepcopy(identity), "pre_restore_source_observations": ["unavailable_http_503"],
             "image_readback_verified": True, "images": {service: row["image_id"] for service, row in bundle["services"].items()},
             "frontend": deepcopy(baseline["frontend"]), "baseline_nonsecret_config_verified": True,
             "protected_owners_unchanged": True,
             "owners": {service: {"container_id": "1" * 64, "image_id": "sha256:" + "2" * 64,
                                   "started_at": "2026-09-09T00:00:00.000001Z", "restart_count": 0} for service in e.OWNERS},
             "public": {"source_sha": "a" * 40, "fe_manifest_bytes_verified": True, "strict_auth_denials_verified": True,
                        "dev_login_enabled": True, "authenticated_viewer_readback_verified": True}}
    value["owners"]["dev-paper-principal-issuer"] = None
    return dict(env=env, identity=identity, baseline=baseline, receipt=receipt, value=value,
                baseline_path=baseline_path, receipt_path=receipt_path, retained=retained)


def actions(env):
    for kind, number in (("BASELINE", "123"), ("CANDIDATE", "456")):
        env[e.ENV_PREFIX + kind + "_ACTIONS_ARTIFACT_ID"] = number
        env[e.ENV_PREFIX + kind + "_ACTIONS_ARTIFACT_SHA256"] = number[0] * 64


def no_candidate(env):
    for suffix in ("CANDIDATE_RECEIPT_FILE", "CANDIDATE_IMAGE_MANIFEST_SHA256",
                   "CANDIDATE_ACTIONS_ARTIFACT_ID", "CANDIDATE_ACTIONS_ARTIFACT_SHA256"):
        env.pop(e.ENV_PREFIX + suffix, None)


def test_local_receipt_survives_failed_upload_and_new_rollback_lease(case):
    output = case["retained"] / "compensate.env"
    evidence = e.prepare(case["env"], "runner-local", output)
    assert evidence["exports"][e.ENV_PREFIX + "OPERATION"] == "restore"
    assert evidence["candidate_actions_artifact"] is None
    assert "fixture-secret-never-export" not in output.read_text()
    assert "LEASE_TOKEN" not in output.read_text() and "_ACK=" not in output.read_text()
    words = [shlex.split(line) for line in output.read_text().splitlines()]
    assert all(row[0] == "export" and len(row) == 2 for row in words)
    assert output.stat().st_mode & 0o777 == 0o600
    assert evidence["exports"][e.ENV_PREFIX + "DRIVER_PATH"].endswith("/" + "d" * 40 + "/dev_release_artifact_driver.py")


@pytest.mark.parametrize("provenance", e.PROVENANCES)
def test_no_candidate_means_verify_only(case, provenance):
    if provenance == "actions-download": actions(case["env"])
    no_candidate(case["env"])
    value = e.load_evidence(case["env"], provenance)
    assert value["exports"][e.ENV_PREFIX + "OPERATION"] == "verify"
    assert value["exports"][e.ENV_PREFIX + "CANDIDATE_IMAGE_MANIFEST_SHA256"] == ""
    case["value"].update(operation="verify", pre_restore_source_observations=[])
    assert e.validate_readback(case["value"], value, "verify") == case["value"]
    with pytest.raises(e.capture.CaptureError): e.validate_readback(case["value"], value, "restore")


@pytest.mark.parametrize("field", ["PANTHEON_RELEASE_CANDIDATE_ID", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
                                   e.ENV_PREFIX + "CONTROLLER_SHA", "PANTHEON_FAILED_BACKEND_SHA", "PANTHEON_FAILED_FRONTEND_SHA",
                                   "PANTHEON_ROLLBACK_BACKEND_SHA", "PANTHEON_ROLLBACK_FRONTEND_SHA"])
def test_every_identity_field_is_externally_bound(case, field):
    case["env"][field] = "9" * len(case["env"][field])
    with pytest.raises(e.capture.CaptureError): e.load_evidence(case["env"], "runner-local")


@pytest.mark.parametrize("field", ["TARGET_ENV", "GCP_DEPLOY_PROJECT_ID", "DEV_VM", "DEV_ZONE"])
def test_current_dev_boundary_is_required(case, field):
    case["env"][field] = "other"
    with pytest.raises(e.capture.CaptureError): e.load_evidence(case["env"], "runner-local")


@pytest.mark.parametrize("kind", ["BASELINE", "CANDIDATE"])
@pytest.mark.parametrize("part", ["ID", "SHA256"])
def test_cross_job_metadata_is_mandatory_and_cannot_be_partial(case, kind, part):
    actions(case["env"])
    case["env"].pop(e.ENV_PREFIX + kind + "_ACTIONS_ARTIFACT_" + part)
    for provenance in e.PROVENANCES:
        with pytest.raises(e.capture.CaptureError): e.load_evidence(case["env"], provenance)


@pytest.mark.parametrize("part", ["CANDIDATE_RECEIPT_FILE", "CANDIDATE_IMAGE_MANIFEST_SHA256"])
def test_candidate_receipt_and_external_digest_must_be_paired(case, part):
    case["env"].pop(e.ENV_PREFIX + part)
    with pytest.raises(e.capture.CaptureError): e.load_evidence(case["env"], "runner-local")


@pytest.mark.parametrize("kind", ["baseline", "receipt"])
@pytest.mark.parametrize("mutation", ["reencoded", "self_resealed", "duplicate", "symlink", "world_readable", "different_run"])
def test_modified_or_unsafe_evidence_is_not_repaired(case, kind, mutation):
    path, value = case[kind + "_path"], case[kind]
    if mutation == "reencoded": path.write_text(json.dumps(value, indent=2))
    elif mutation == "self_resealed":
        if kind == "baseline": value["frontend"]["target"] += "-tampered"
        else:
            value["candidate_image_manifest"]["candidate_compose_sha256"] = "0" * 64
            value["candidate_image_manifest_sha256"] = e.capture.digest(e.capture.encoded(value["candidate_image_manifest"]))
        path.write_bytes(e.capture.encoded(value))
    elif mutation == "duplicate": path.write_bytes(b'{"schema_version":"duplicate",' + e.capture.encoded(value)[1:])
    elif mutation == "symlink":
        alternate = path.with_suffix(".moved"); path.rename(alternate); path.symlink_to(alternate)
    elif mutation == "world_readable": path.chmod(0o644)
    else:
        alternate = write(case["retained"] / "wrong-run" / path.name, path.read_bytes())
        case["env"][e.ENV_PREFIX + ("BASELINE_FILE" if kind == "baseline" else "CANDIDATE_RECEIPT_FILE")] = str(alternate)
    with pytest.raises((e.capture.CaptureError, ValueError)): e.load_evidence(case["env"], "runner-local")


def test_cross_job_requires_metadata_and_preserves_external_ids(case):
    with pytest.raises(e.capture.CaptureError): e.load_evidence(case["env"], "actions-download")
    actions(case["env"])
    evidence = e.load_evidence(case["env"], "actions-download")
    assert evidence["baseline_actions_artifact"] == {"artifact_id": "123", "artifact_sha256": "1" * 64}
    assert evidence["candidate_actions_artifact"] == {"artifact_id": "456", "artifact_sha256": "4" * 64}


def test_both_driver_and_library_bytes_are_authenticated_against_exact_commit(case, monkeypatch):
    seen = []
    def committed(root, name, sha):
        seen.append((root, name, sha))
        if name == "dev_release_artifacts.py": raise e.capture.CaptureError("fixture uncommitted library drift")
        return b"# fixture"
    monkeypatch.setattr(e.capture, "read_implementation", committed)
    with pytest.raises(e.capture.CaptureError): e.load_evidence(case["env"], "runner-local")
    assert [row[1] for row in seen] == list(e.capture.IMPLEMENTATIONS)
    assert all(row[2] == case["identity"]["controller_sha"] for row in seen)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "lookalike", "duplicate_key", "oversize"])
def test_readback_framing_is_exactly_one_record(case, mutation):
    raw = e.PREFIX + e.capture.encoded(case["value"])
    if mutation == "duplicate": raw += raw
    elif mutation == "missing": raw = b"deployment source passed\n"
    elif mutation == "lookalike": raw += b" PANTHEON_ARTIFACT_READBACK_V1 {}\n"
    elif mutation == "duplicate_key": raw = e.PREFIX + b'{"operation":"verify",' + e.capture.encoded(case["value"])[1:]
    else: raw = e.PREFIX + b" " * 65536 + b"{}\n"
    path = write(case["retained"] / "remote.log", raw)
    output = case["retained"] / "readback.json"
    with pytest.raises((e.capture.CaptureError, ValueError)):
        e.readback(case["env"], "runner-local", path, output, "restore")
    assert not output.exists()


@pytest.mark.parametrize("mutation", ["source_only", "image", "image_missing", "fe_target", "fe_dist", "fe_manifest",
                                      "public_source", "auth_missing", "auth_integer", "dev_login_missing",
                                      "dev_login_integer", "login_claimed_without_registry", "login_absent_with_registry",
                                      "owner_missing", "owner_secret",
                                      "owner_restart_boolean", "config", "guard", "identity", "seal", "operation",
                                      "empty_observations", "invalid_observations", "duplicate_observations", "extra_field"])
def test_complete_readback_is_required(case, mutation):
    value = case["value"]
    if mutation == "source_only": value = {"source_commit_sha": "a" * 40}
    elif mutation == "image": value["images"]["operator-bff"] = "sha256:" + "9" * 64
    elif mutation == "image_missing": value["images"].pop("agora-interaction-worker")
    elif mutation.startswith("fe_"): value["frontend"][{"fe_target": "target", "fe_dist": "dist_sha256", "fe_manifest": "manifest_sha256"}[mutation]] = "changed"
    elif mutation == "public_source": value["public"]["source_sha"] = "e" * 40
    elif mutation == "auth_missing": value["public"].pop("authenticated_viewer_readback_verified")
    elif mutation == "auth_integer": value["public"]["strict_auth_denials_verified"] = 1
    elif mutation == "dev_login_missing": value["public"].pop("dev_login_enabled")
    elif mutation == "dev_login_integer": value["public"]["dev_login_enabled"] = 0
    elif mutation == "login_claimed_without_registry": value["public"]["dev_login_enabled"] = False
    elif mutation == "login_absent_with_registry": value["public"]["authenticated_viewer_readback_verified"] = False
    elif mutation == "owner_missing": value["owners"].pop("capital")
    elif mutation == "owner_secret": value["owners"]["capital"]["unexpected_env"] = "fixture-secret"
    elif mutation == "owner_restart_boolean": value["owners"]["capital"]["restart_count"] = True
    elif mutation == "config": value["baseline_nonsecret_config_verified"] = False
    elif mutation == "guard": value["protected_owners_unchanged"] = False
    elif mutation == "identity": value["identity"]["attempt"] = "3"
    elif mutation == "seal": value["manifest_sha256"] = "0" * 64
    elif mutation == "operation": value["operation"] = "verify"
    elif mutation == "empty_observations": value["pre_restore_source_observations"] = []
    elif mutation == "invalid_observations": value["pre_restore_source_observations"] = ["unavailable_tls"]
    elif mutation == "duplicate_observations": value["pre_restore_source_observations"] *= 2
    else: value["unknown"] = "fixture-secret"
    with pytest.raises(e.capture.CaptureError):
        e.validate_readback(value, e.load_evidence(case["env"], "runner-local"), "restore")


def test_predecessor_without_dev_login_registry_is_a_complete_readback(case):
    # Both literal falses together are the image's own declaration that no
    # dedicated identity exists to log in with; restore rejects them because
    # dev-login proof is required on restore, while verify accepts them.
    value = case["value"]
    value["public"]["dev_login_enabled"] = False
    value["public"]["authenticated_viewer_readback_verified"] = False
    with pytest.raises(e.capture.CaptureError):
        e.validate_readback(value, e.load_evidence(case["env"], "runner-local"), "restore")
    no_candidate(case["env"])
    value.update(operation="verify", pre_restore_source_observations=[])
    assert e.validate_readback(value, e.load_evidence(case["env"], "runner-local"), "verify") == value


def test_validated_readback_is_exclusive_private_and_preserves_all_evidence(case):
    path = write(case["retained"] / "remote.log", b"private diagnostics\n" + e.PREFIX + e.capture.encoded(case["value"]))
    output = case["retained"] / "readback.json"
    assert e.readback(case["env"], "runner-local", path, output, "restore") == case["value"]
    assert output.read_bytes() == e.capture.encoded(case["value"])
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): e.readback(case["env"], "runner-local", path, output, "restore")
    assert output.read_bytes() == e.capture.encoded(case["value"])


def test_v2_compensation_binds_readback_external_seals_and_original_failure_log(case):
    actions(case["env"])
    readback = write(case["retained"] / "readback.json", e.capture.encoded(case["value"]))
    failure = write(case["retained"] / "original-failure.log", b"original fixture failure and fixture-private-token\n")
    result = e.compensation_evidence(case["env"], "actions-download", readback, failure)
    assert result["schema_version"] == "pantheon.cross-repo-release-compensation.v2"
    assert result["artifact_readback"] == case["value"]
    assert result["operation"] == "restore"
    assert result["controller_failure_log_sha256"] == e.capture.digest(failure.read_bytes())
    assert result["candidate_image_manifest_sha256"] == case["receipt"]["candidate_image_manifest_sha256"]
    assert result["baseline_actions_artifact"]["artifact_id"] == "123"
    assert result["candidate_actions_artifact"]["artifact_id"] == "456"
    assert "fixture-private-token" not in e.capture.encoded(result).decode()


def test_cross_repo_caller_uses_guarded_exact_artifacts_and_v2_evidence():
    script = Path(__file__).with_name("compensate_cross_repo_release.sh")
    text = script.read_text()
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    assert 'prepare --provenance actions-download --output-env "${artifact_env}"' in text
    assert '"--artifact-${PANTHEON_DEV_ARTIFACT_OPERATION}"' in text
    assert '--artifact-readback-out "${artifact_readback}"' in text
    assert 'artifact.compensation_evidence(dict(os.environ), "actions-download"' in text
    assert '"${lease_wrapper}"' in text and "heartbeat-loop" in text and "verify-heartbeat-identity" in text
    assert "cross-repo-release-compensation.v1" not in text
    assert '"${DEV_BFF_URL%/}/bff/version"' not in text
    assert "docker compose" not in text and " build " not in text


def test_restore_final_positive_control_and_candidate_evidence(case):
    actions(case["env"])
    case["env"][e.ENV_PREFIX + "TARGET"] = "final"
    case["env"][e.ENV_PREFIX + "OPERATION"] = "restore-final"
    fe_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/candidate-fixture",
        "dist_sha256": "3" * 64,
        "manifest_sha256": "2" * 64,
        "frontend_sha": case["identity"]["candidate_frontend_sha"],
        "backend_sha": case["identity"]["candidate_backend_sha"],
    }
    seal_path = write(case["retained"] / "candidate/candidate-frontend.json", e.capture.encoded(fe_seal))
    case["env"][e.ENV_PREFIX + "FINAL_FE_SEAL_FILE"] = str(seal_path)
    output_env = case["retained"] / "candidate-compensate.env"
    evidence = e.prepare(case["env"], "actions-download", output_env)
    assert evidence["exports"][e.ENV_PREFIX + "OPERATION"] == "restore-final"
    assert evidence["exports"][e.ENV_PREFIX + "TARGET"] == "final"
    assert evidence["exports"][e.ENV_PREFIX + "FE_MANIFEST_SHA256"] == fe_seal["manifest_sha256"]
    assert evidence["exports"][e.ENV_PREFIX + "FE_DIST_SHA256"] == fe_seal["dist_sha256"]
    assert evidence["exports"][e.ENV_PREFIX + "FE_TARGET"] == fe_seal["target"]

    value = case["value"]
    value["operation"] = "restore-final"
    value["images"] = {service: row["image_id"] for service, row in case["receipt"]["candidate_image_manifest"]["services"].items()}
    value["frontend"] = deepcopy(fe_seal)
    value["public"]["source_sha"] = case["identity"]["candidate_backend_sha"]

    assert e.validate_readback(value, evidence, "restore-final") == value

    readback = write(case["retained"] / "readback-final.json", e.capture.encoded(value))
    failure = write(case["retained"] / "original-failure.log", b"candidate switch failure\n")
    comp = e.compensation_evidence(case["env"], "actions-download", readback, failure)
    assert comp["schema_version"] == "pantheon.cross-repo-release-compensation.v2"
    assert comp["operation"] == "restore-final"
    assert comp["outcome"] == "compensated"
    assert comp["restored_pair"] == {
        "backend_sha": case["identity"]["candidate_backend_sha"],
        "frontend_sha": case["identity"]["candidate_frontend_sha"],
    }
    assert comp["rejected_pair"] == {
        "backend_sha": case["identity"]["previous_backend_sha"],
        "frontend_sha": case["identity"]["previous_frontend_sha"],
    }


@pytest.mark.parametrize("fault", [
    "mixed_baseline_images", "mixed_baseline_fe", "tampered_seal_manifest",
    "tampered_seal_dist", "tampered_seal_target", "missing_candidate_receipt",
    "operation_mismatch", "wrong_public_source"
])
def test_restore_final_negative_controls(case, fault):
    actions(case["env"])
    case["env"][e.ENV_PREFIX + "TARGET"] = "final"
    case["env"][e.ENV_PREFIX + "OPERATION"] = "restore-final"
    fe_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/candidate-fixture",
        "dist_sha256": "3" * 64,
        "manifest_sha256": "2" * 64,
        "frontend_sha": case["identity"]["candidate_frontend_sha"],
        "backend_sha": case["identity"]["candidate_backend_sha"],
    }
    seal_path = write(case["retained"] / "candidate/candidate-frontend.json", e.capture.encoded(fe_seal))
    case["env"][e.ENV_PREFIX + "FINAL_FE_SEAL_FILE"] = str(seal_path)

    if fault == "missing_candidate_receipt":
        case["env"].pop(e.ENV_PREFIX + "CANDIDATE_RECEIPT_FILE")
        with pytest.raises(e.capture.CaptureError):
            e.load_evidence(case["env"], "actions-download")
        return

    if fault == "tampered_seal_manifest":
        case["env"][e.ENV_PREFIX + "FE_MANIFEST_SHA256"] = "0" * 64
        with pytest.raises(e.capture.CaptureError):
            e.load_evidence(case["env"], "actions-download")
        return

    if fault == "tampered_seal_dist":
        case["env"][e.ENV_PREFIX + "FE_DIST_SHA256"] = "0" * 64
        with pytest.raises(e.capture.CaptureError):
            e.load_evidence(case["env"], "actions-download")
        return

    if fault == "tampered_seal_target":
        case["env"][e.ENV_PREFIX + "FE_TARGET"] = "/wrong/path"
        with pytest.raises(e.capture.CaptureError):
            e.load_evidence(case["env"], "actions-download")
        return

    evidence = e.load_evidence(case["env"], "actions-download")
    value = case["value"]
    value["operation"] = "restore-final"
    value["images"] = {service: row["image_id"] for service, row in case["receipt"]["candidate_image_manifest"]["services"].items()}
    value["frontend"] = deepcopy(fe_seal)
    value["public"]["source_sha"] = case["identity"]["candidate_backend_sha"]

    if fault == "mixed_baseline_images":
        value["images"] = {service: row["image_id"] for service, row in case["baseline"]["image_bundle"]["services"].items()}
    elif fault == "mixed_baseline_fe":
        value["frontend"] = deepcopy(case["baseline"]["frontend"])
    elif fault == "operation_mismatch":
        value["operation"] = "restore"
    elif fault == "wrong_public_source":
        value["public"]["source_sha"] = case["identity"]["previous_backend_sha"]

    with pytest.raises(e.capture.CaptureError):
        e.validate_readback(value, evidence, "restore-final")


def test_restore_final_consumes_full_typed_readback_seal(case):
    actions(case["env"])
    case["env"][e.ENV_PREFIX + "TARGET"] = "final"
    case["env"][e.ENV_PREFIX + "OPERATION"] = "restore-final"
    fe_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/candidate-fixture",
        "dist_sha256": "3" * 64,
        "manifest_sha256": "2" * 64,
        "frontend_sha": case["identity"]["candidate_frontend_sha"],
        "backend_sha": case["identity"]["candidate_backend_sha"],
    }
    readback_as_seal = deepcopy(case["value"])
    readback_as_seal["operation"] = "verify"
    readback_as_seal["frontend"] = deepcopy(fe_seal)
    readback_as_seal["public"]["source_sha"] = case["identity"]["candidate_backend_sha"]
    write(case["retained"] / "candidate/candidate-frontend.json", e.capture.encoded(readback_as_seal))
    case["env"].pop(e.ENV_PREFIX + "FINAL_FE_SEAL_FILE", None)

    output_env = case["retained"] / "candidate-compensate.env"
    evidence = e.prepare(case["env"], "actions-download", output_env)
    assert evidence["exports"][e.ENV_PREFIX + "OPERATION"] == "restore-final"
    assert evidence["exports"][e.ENV_PREFIX + "TARGET"] == "final"
    assert evidence["exports"][e.ENV_PREFIX + "FE_MANIFEST_SHA256"] == fe_seal["manifest_sha256"]
    assert evidence["exports"][e.ENV_PREFIX + "FE_DIST_SHA256"] == fe_seal["dist_sha256"]
    assert evidence["exports"][e.ENV_PREFIX + "FE_TARGET"] == fe_seal["target"]

    value = case["value"]
    value["operation"] = "restore-final"
    value["images"] = {service: row["image_id"] for service, row in case["receipt"]["candidate_image_manifest"]["services"].items()}
    value["frontend"] = deepcopy(fe_seal)
    value["public"]["source_sha"] = case["identity"]["candidate_backend_sha"]

    assert e.validate_readback(value, evidence, "restore-final") == value


@pytest.mark.parametrize("field", list(e.capture.FIELDS))
def test_restore_final_rejects_tampered_typed_readback_seal(case, field):
    actions(case["env"])
    case["env"][e.ENV_PREFIX + "TARGET"] = "final"
    case["env"][e.ENV_PREFIX + "OPERATION"] = "restore-final"
    readback_as_seal = deepcopy(case["value"])
    readback_as_seal["operation"] = "verify"
    tampered_val = "0" * 64 if field == "candidate_id" else ("999999" if field in ("run_id", "attempt") else "0" * 40)
    readback_as_seal["identity"][field] = tampered_val
    seal_path = write(case["retained"] / "candidate/candidate-frontend.json", e.capture.encoded(readback_as_seal))
    case["env"][e.ENV_PREFIX + "FINAL_FE_SEAL_FILE"] = str(seal_path)
    with pytest.raises(e.capture.CaptureError) as exc_info:
        e.load_evidence(case["env"], "actions-download")
    assert f"artifact readback identity mismatch for {field}" in str(exc_info.value)


@pytest.mark.parametrize("structural_fault", ["missing_field", "extra_field", "non_dict_identity", "missing_frontend"])
def test_restore_final_rejects_structural_typed_readback_seal_negatives(case, structural_fault):
    actions(case["env"])
    case["env"][e.ENV_PREFIX + "TARGET"] = "final"
    case["env"][e.ENV_PREFIX + "OPERATION"] = "restore-final"
    readback_as_seal = deepcopy(case["value"])
    readback_as_seal["operation"] = "verify"
    if structural_fault == "missing_field":
        del readback_as_seal["identity"]["controller_sha"]
    elif structural_fault == "extra_field":
        readback_as_seal["identity"]["foreign_extra_key"] = "1"
    elif structural_fault == "non_dict_identity":
        readback_as_seal["identity"] = "not-a-dict"
    elif structural_fault == "missing_frontend":
        del readback_as_seal["frontend"]

    seal_path = write(case["retained"] / "candidate/candidate-frontend.json", e.capture.encoded(readback_as_seal))
    case["env"][e.ENV_PREFIX + "FINAL_FE_SEAL_FILE"] = str(seal_path)
    with pytest.raises(e.capture.CaptureError):
        e.load_evidence(case["env"], "actions-download")


def test_load_evidence_respects_dev_artifact_run_id_and_attempt(case):
    actions(case["env"])
    case["env"]["GITHUB_RUN_ID"] = "112233"
    case["env"]["GITHUB_RUN_ATTEMPT"] = "1"
    case["env"]["PANTHEON_DEV_ARTIFACT_RUN_ID"] = "998877"
    case["env"]["PANTHEON_DEV_ARTIFACT_ATTEMPT"] = "4"
    case["env"][e.ENV_PREFIX + "TARGET"] = "final"
    case["env"][e.ENV_PREFIX + "OPERATION"] = "restore-final"
    case["env"][e.ENV_PREFIX + "CANDIDATE_COMPOSE_FILE"] = str(case["retained"] / "docker-compose.yml")

    case["baseline"]["identity"]["run_id"] = "998877"
    case["baseline"]["identity"]["attempt"] = "4"
    baseline_bytes = e.capture.encoded(case["baseline"])
    manifest_hash = e.capture.digest(baseline_bytes)
    baseline_path = write(case["retained"] / "baseline/artifact-baseline.json", baseline_bytes)
    case["env"][e.ENV_PREFIX + "BASELINE_FILE"] = str(baseline_path)
    case["env"][e.ENV_PREFIX + "MANIFEST_SHA256"] = manifest_hash

    folder = e.capture.ARTIFACT_ROOT / f"baseline-998877-4-{case['identity']['candidate_id']}"
    case["receipt"]["candidate_image_manifest"]["identity"]["run_id"] = "998877"
    case["receipt"]["candidate_image_manifest"]["identity"]["attempt"] = "4"
    case["receipt"]["candidate_image_manifest"]["baseline_manifest_sha256"] = manifest_hash
    case["receipt"]["candidate_image_manifest_path"] = str(folder / "candidate-images.json")
    case["receipt"]["candidate_image_override_path"] = str(folder / "candidate-images.override.json")
    case["receipt"]["candidate_image_manifest_sha256"] = e.capture.digest(e.capture.encoded(case["receipt"]["candidate_image_manifest"]))
    receipt_path = write(case["retained"] / "candidate/candidate-receipt.json", e.capture.encoded(case["receipt"]))
    case["env"][e.ENV_PREFIX + "CANDIDATE_RECEIPT_FILE"] = str(receipt_path)
    case["env"][e.ENV_PREFIX + "CANDIDATE_IMAGE_MANIFEST_SHA256"] = case["receipt"]["candidate_image_manifest_sha256"]

    fe_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/candidate-fixture",
        "dist_sha256": "4" * 64,
        "manifest_sha256": "3" * 64,
        "frontend_sha": case["identity"]["candidate_frontend_sha"],
        "backend_sha": case["identity"]["candidate_backend_sha"],
    }
    seal_path = write(case["retained"] / "candidate/candidate-frontend.json", e.capture.encoded(fe_seal))
    case["env"][e.ENV_PREFIX + "FINAL_FE_SEAL_FILE"] = str(seal_path)

    evidence = e.load_evidence(case["env"], "actions-download")
    assert evidence["identity"]["run_id"] == "998877"
    assert evidence["identity"]["attempt"] == "4"
    assert evidence["candidate"] is not None
    assert evidence["fe_seal"] is not None

    readback = deepcopy(case["value"])
    readback["operation"] = "restore-final"
    readback["manifest_sha256"] = manifest_hash
    readback["identity"]["run_id"] = "998877"
    readback["identity"]["attempt"] = "4"
    readback["images"] = {service: row["image_id"] for service, row in case["receipt"]["candidate_image_manifest"]["services"].items()}
    readback["frontend"] = deepcopy(fe_seal)
    readback["public"]["source_sha"] = case["identity"]["candidate_backend_sha"]
    readback_path = write(case["retained"] / "readback-cross-run.json", e.capture.encoded(readback))

    result = e.compensation_evidence(case["env"], "actions-download", readback_path, failure_log=None)
    assert result["outcome"] == "restore-final"
    assert result["operation"] == "restore-final"
    assert result["workflow"]["run_id"] == "112233"
    assert result["workflow"]["run_attempt"] == "1"
    assert result["artifact_readback"]["identity"]["run_id"] == "998877"
    assert result["artifact_readback"]["identity"]["attempt"] == "4"
    assert result["restored_pair"]["backend_sha"] == case["identity"]["candidate_backend_sha"]
    assert result["restored_pair"]["frontend_sha"] == case["identity"]["candidate_frontend_sha"]


def test_load_evidence_final_requires_candidate_compose(case):
    actions(case["env"])
    case["env"][e.ENV_PREFIX + "TARGET"] = "final"
    fe_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/candidate-fixture",
        "dist_sha256": "4" * 64,
        "manifest_sha256": "3" * 64,
        "frontend_sha": case["identity"]["candidate_frontend_sha"],
        "backend_sha": case["identity"]["candidate_backend_sha"],
    }
    seal_path = write(case["retained"] / "candidate/candidate-frontend.json", e.capture.encoded(fe_seal))
    case["env"][e.ENV_PREFIX + "FINAL_FE_SEAL_FILE"] = str(seal_path)
    case["env"].pop(e.ENV_PREFIX + "CANDIDATE_COMPOSE_FILE", None)
    case["env"].pop(e.ENV_PREFIX + "COMPOSE_FILE", None)
    case["env"].pop("PANTHEON_DEPLOY_WORKTREE_ROOT", None)
    case["env"].pop("DEV_REMOTE_DIR", None)
    case["env"].pop("PANTHEON_REMOTE_DIR", None)
    with pytest.raises(e.capture.CaptureError, match="candidate compose file is required"):
        e.load_evidence(case["env"], "actions-download")


def test_compensation_evidence_handles_none_failure_log_for_restore_final(case):
    actions(case["env"])
    case["env"][e.ENV_PREFIX + "TARGET"] = "final"
    case["env"][e.ENV_PREFIX + "OPERATION"] = "restore-final"
    fe_seal = {
        "target": "/var/www/pantheon-dev-fe-releases/candidate-fixture",
        "dist_sha256": "4" * 64,
        "manifest_sha256": "3" * 64,
        "frontend_sha": case["identity"]["candidate_frontend_sha"],
        "backend_sha": case["identity"]["candidate_backend_sha"],
    }
    seal_path = write(case["retained"] / "candidate/candidate-frontend.json", e.capture.encoded(fe_seal))
    case["env"][e.ENV_PREFIX + "FINAL_FE_SEAL_FILE"] = str(seal_path)

    readback = deepcopy(case["value"])
    readback["operation"] = "restore-final"
    readback["images"] = {service: row["image_id"] for service, row in case["receipt"]["candidate_image_manifest"]["services"].items()}
    readback["frontend"] = deepcopy(fe_seal)
    readback["public"]["source_sha"] = case["identity"]["candidate_backend_sha"]
    readback_path = write(case["retained"] / "readback-final.json", e.capture.encoded(readback))

    res = e.compensation_evidence(case["env"], "actions-download", readback_path, failure_log=None)
    assert res["outcome"] == "restore-final"
    assert res["operation"] == "restore-final"
    assert res["rejected_pair"] is None
    assert res["restored_pair"]["backend_sha"] == case["identity"]["candidate_backend_sha"]
    assert res["controller_failure_log_sha256"] is None


def test_baseline_restore_without_qualified_seal_exports_only_sealed_baseline_to(case):
    actions(case["env"])
    assert not (case["retained"] / "candidate/candidate-frontend.json").exists()
    case["env"].pop(e.ENV_PREFIX + "FINAL_FE_SEAL_FILE", None)
    evidence = e.prepare(case["env"], "actions-download", case["retained"] / "baseline.env")
    exports = evidence["exports"]
    assert exports[e.ENV_PREFIX + "OPERATION"] == "restore"
    assert evidence["fe_seal"] is None
    assert e.ENV_PREFIX + "SEALED_FROM_JSON" not in exports
    assert json.loads(exports[e.ENV_PREFIX + "SEALED_TO_JSON"]) == evidence["baseline"]["frontend"]
