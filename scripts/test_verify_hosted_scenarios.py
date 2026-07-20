from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("verify_hosted_scenarios.py")
SPEC = importlib.util.spec_from_file_location("verify_hosted_scenarios", SCRIPT)
assert SPEC and SPEC.loader
verifier_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier_module
SPEC.loader.exec_module(verifier_module)


def _config(tmp_path: Path):
    return verifier_module.Config(
        bff_base_url="https://bff.example.test",
        fe_deployment_url="https://fe.example.test/deployment.json",
        allowed_bff_origin="https://bff.example.test",
        allowed_fe_origin="https://fe.example.test",
        tenant_id="tenant-dev",
        forbidden_tenant_id="tenant-foreign",
        expected_bff_sha="b" * 40,
        expected_fe_sha="f" * 40,
        operator_client_id="operator-a-client",
        operator_client_secret="operator-a-secret",
        viewer_client_id="viewer-client",
        viewer_client_secret="viewer-secret",
        evidence_dir=tmp_path,
        github_server_url="https://github.com",
        github_repository="ajoe734/pantheon",
        github_run_id="123456",
        github_run_attempt="2",
        replay_as_of="2026-07-12T12:01:30Z",
        ambiguity_identifier="ambiguous-scenario-9",
    )


def _scenario_7_bundle(status: str):
    return verifier_module.ScenarioBundle(
        detail={
            "journey_id": "tj-scenario-7",
            "status": status,
            "stage_events": {
                "reconciliation": {
                    "stage": "reconciliation",
                    "stage_status": "failed",
                    "delta": 12.5,
                    "source_ref": "broker-readback://scenario-7",
                    "remediation": "open-reconciliation-incident",
                }
            },
        },
        timeline=(),
        evidence={},
        detail_meta={},
    )


def test_scenario_7_rejects_completed_rollup(tmp_path: Path) -> None:
    config = _config(tmp_path)
    recorder = verifier_module.EvidenceRecorder(config)
    verifier = verifier_module.HostedVerifier(config, recorder)
    verifier._bundle = lambda *_args, **_kwargs: _scenario_7_bundle("completed")

    with pytest.raises(verifier_module.VerificationError) as exc_info:
        verifier.verify_scenario_7()

    assert exc_info.value.code == "scenario-07.reconciliation_mismatch_not_completed"
    assert recorder.checks[-1]["passed"] is False


def test_scenario_7_accepts_visible_variance_and_remediation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    recorder = verifier_module.EvidenceRecorder(config)
    verifier = verifier_module.HostedVerifier(config, recorder)
    verifier._bundle = lambda *_args, **_kwargs: _scenario_7_bundle("completed_with_variance")

    verifier.verify_scenario_7()

    assert recorder.checks[-1]["passed"] is True


def test_evidence_artifacts_redact_all_credentials_and_tokens(tmp_path: Path) -> None:
    config = _config(tmp_path)
    recorder = verifier_module.EvidenceRecorder(config)
    issued_token = "issued.access.token"
    recorder.add_secret(issued_token)
    recorder.call(
        "auth-operator-a",
        {
            "request": {
                "authorization": f"Bearer {issued_token}",
                "client_secret": config.operator_client_secret,
            },
            "response": {
                "json": {
                    "access_token": issued_token,
                    "safe": "kept",
                    "nested": f"prefix-{config.viewer_client_secret}-suffix",
                }
            },
        },
    )
    recorder.check("sample", True, {"token": issued_token})

    recorder.write(passed=True)

    captured = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    )
    assert config.operator_client_secret not in captured
    assert config.viewer_client_secret not in captured
    assert issued_token not in captured
    assert REDACTED_MARKER in captured
    assert '"safe": "kept"' in captured


REDACTED_MARKER = "<redacted>"


def test_evidence_manifest_binds_run_and_exact_deployment_pair(tmp_path: Path) -> None:
    config = _config(tmp_path)
    recorder = verifier_module.EvidenceRecorder(config)

    summary = recorder.write(passed=True)
    on_disk = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))

    assert on_disk == summary
    assert summary["run"]["url"].endswith("/ajoe734/pantheon/actions/runs/123456")
    assert summary["deployment"]["expected_frontend_sha"] == "f" * 40
    assert summary["deployment"]["expected_bff_sha"] == "b" * 40
    assert len(summary["manifest_sha256"]) == 64
    assert (tmp_path / "evidence.sha256").read_text(encoding="utf-8").endswith("  evidence.json\n")


def test_source_has_no_legacy_bearer_or_insecure_tls_override() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "lupin:admin::pantheon-dev" not in source
    assert "viewer-a:viewer::tenant-a" not in source
    assert "CERT_NONE" not in source
    assert "check_hostname = False" not in source
    assert "/bff/auth/dev-login" in source


def test_workflow_uses_dev_environment_credentials_and_uploads_evidence() -> None:
    workflow = (SCRIPT.parents[1] / ".github/workflows/tj-e2e-012-hosted-acceptance.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "environment: dev" in workflow
    assert "secrets.DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET" in workflow
    assert "secrets.DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET" in workflow
    assert "TJ_E2E_ALLOWED_BFF_ORIGIN" in workflow
    assert "TJ_E2E_ALLOWED_FE_ORIGIN" in workflow
    assert "scripts/verify_hosted_scenarios.py" in workflow
    assert "if: always()" in workflow
    assert "actions/upload-artifact@v4" in workflow


def test_registered_stage_zero_workflow_bridges_hosted_acceptance_on_dev() -> None:
    workflow = (SCRIPT.parents[1] / ".github/workflows/stage-0-ci.yml").read_text(encoding="utf-8")

    assert "- tj-e2e-012-hosted-acceptance" in workflow
    assert "inputs.mode == 'tj-e2e-012-hosted-acceptance'" in workflow
    assert "inputs.expected_sha" in workflow
    assert "inputs.expected_fe_sha" in workflow
    assert "inputs.environment != 'dev'" in workflow
    assert "secrets.DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET" in workflow
    assert "secrets.DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET" in workflow
    assert "python3 scripts/verify_hosted_scenarios.py" in workflow
    assert "tj-e2e-012-hosted-acceptance-${{ github.run_id }}-${{ github.run_attempt }}" in workflow


def test_config_rejects_a_non_allowlisted_credential_destination(monkeypatch, tmp_path: Path) -> None:
    values = {
        "BFF_BASE": "https://attacker.example.test",
        "TJ_E2E_FE_DEPLOYMENT_URL": "https://fe.example.test/deployment.json",
        "TJ_E2E_ALLOWED_BFF_ORIGIN": "https://bff.example.test",
        "TJ_E2E_ALLOWED_FE_ORIGIN": "https://fe.example.test",
        "TJ_E2E_TENANT_ID": "tenant-dev",
        "TJ_E2E_FORBIDDEN_TENANT_ID": "tenant-foreign",
        "TJ_E2E_EXPECTED_BFF_SHA": "b" * 40,
        "TJ_E2E_EXPECTED_FE_SHA": "f" * 40,
        "TJ_E2E_OPERATOR_CLIENT_ID": "operator-a-client",
        "TJ_E2E_OPERATOR_CLIENT_SECRET": "operator-a-secret",
        "TJ_E2E_VIEWER_CLIENT_ID": "viewer-client",
        "TJ_E2E_VIEWER_CLIENT_SECRET": "viewer-secret",
        "TJ_E2E_EVIDENCE_DIR": str(tmp_path),
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "ajoe734/pantheon",
        "GITHUB_RUN_ID": "123456",
        "GITHUB_RUN_ATTEMPT": "1",
        "TJ_E2E_REPLAY_AS_OF": "2026-07-12T12:01:30Z",
        "TJ_E2E_AMBIGUITY_IDENTIFIER": "ambiguous-scenario-9",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(verifier_module.VerificationError) as exc_info:
        verifier_module.Config.from_env()

    assert exc_info.value.code == "CONFIG_INVALID"
    assert "allowlisted HTTPS origin" in str(exc_info.value)


def test_run_collects_every_scenario_failure_in_one_evidence_run(tmp_path: Path) -> None:
    config = _config(tmp_path)
    recorder = verifier_module.EvidenceRecorder(config)
    verifier = verifier_module.HostedVerifier(config, recorder)
    verifier.verify_deployment = lambda: None
    verifier.authenticate = lambda: None

    called = []

    def fail(number):
        def check():
            called.append(number)
            raise verifier_module.VerificationError(f"scenario-{number:02d}.failed", "failed")

        return check

    for number in range(1, 13):
        setattr(verifier, f"verify_scenario_{number}", fail(number))

    with pytest.raises(verifier_module.VerificationError) as exc_info:
        verifier.run()

    assert called == list(range(1, 13))
    assert exc_info.value.code == "SCENARIOS_FAILED"
    assert len(exc_info.value.details["failures"]) == 12
