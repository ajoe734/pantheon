from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from scripts.cross_repo_release_controller import (
    ControllerError,
    DEPLOY_WORKFLOW,
    GATE_WORKFLOW,
    ProofStateMachine,
    coordinate_release,
    create_candidate_record,
    derive_pair_id,
    restore_read_only_profile,
    validate_candidate_override,
    verify_served_identity,
)


FRONTEND_SHA = "1" * 40
BACKEND_SHA = "a" * 40
CANDIDATE_ID = "c" * 64
MANIFEST_SHA = "d" * 64
CONTROLLER_RUN_ID = "12345"
ROOT = Path(__file__).resolve().parents[1]
NONPROD_WORKFLOW = (
    ROOT / ".github" / "workflows" / "nonprod-deploy.yml"
).read_text(encoding="utf-8")
COMPENSATION_SCRIPT = (
    ROOT / "scripts" / "compensate_cross_repo_release.sh"
).read_text(encoding="utf-8")


def _run(
    run_id: int,
    *,
    workflow: str,
    title: str,
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "path": f".github/workflows/{workflow}@refs/heads/dev",
        "head_repository": {"full_name": "ajoe734/execute-plans"},
        "repository": {"full_name": "ajoe734/execute-plans"},
        "event": "workflow_dispatch",
        "head_branch": "dev",
        "head_sha": FRONTEND_SHA,
        "display_title": title,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://github.test/runs/{run_id}",
    }


class FakeClient:
    def __init__(self, *, gate_conclusion: str = "success", ambiguous: bool = False):
        self.dispatches: list[tuple[str, dict[str, str]]] = []
        self.gate_conclusion = gate_conclusion
        self.ambiguous = ambiguous

    def list_runs(self, workflow: str) -> list[dict[str, Any]]:
        dispatched = [item for item in self.dispatches if item[0] == workflow]
        if not dispatched:
            return []
        inputs = dispatched[-1][1]
        title = (
            f"Release candidate {inputs['release_candidate_id']}"
            if workflow == GATE_WORKFLOW
            else f"Deploy release candidate {inputs['release_candidate_id']}"
        )
        run = _run(
            101 if workflow == GATE_WORKFLOW else 202,
            workflow=workflow,
            title=title,
            status="in_progress",
            conclusion=None,
        )
        if self.ambiguous:
            return [run, {**run, "id": run["id"] + 1}]
        return [run]

    def dispatch(self, workflow: str, inputs: dict[str, str]) -> None:
        self.dispatches.append((workflow, inputs))

    def get_run(self, run_id: int) -> dict[str, Any]:
        if run_id == 101:
            return _run(
                101,
                workflow=GATE_WORKFLOW,
                title=f"Release candidate {CANDIDATE_ID}",
                conclusion=self.gate_conclusion,
            )
        return _run(
            202,
            workflow=DEPLOY_WORKFLOW,
            title=f"Deploy release candidate {CANDIDATE_ID}",
        )


def _default_fetch_fn(url: str) -> dict[str, Any]:
    if "version" in url:
        return {"source_commit_sha": BACKEND_SHA}
    if "deployment.json" in url:
        return {
            "frontendSha": FRONTEND_SHA,
            "pairId": derive_pair_id(BACKEND_SHA, FRONTEND_SHA),
        }
    return {}


def _coordinate(
    client: FakeClient,
    *,
    fetch_fn: Any = None,
    **overrides: Any,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "frontend_sha": FRONTEND_SHA,
        "backend_sha": BACKEND_SHA,
        "bff_base_url": "https://bff.test",
        "release_candidate_id": CANDIDATE_ID,
        "compatibility_manifest_sha256": MANIFEST_SHA,
        "controller_run_id": CONTROLLER_RUN_ID,
        "gate_timeout_seconds": 5,
        "deploy_timeout_seconds": 5,
        "poll_seconds": 0,
        "fetch_fn": fetch_fn or _default_fetch_fn,
        "sleep": lambda _: None,
    }
    kwargs.update(overrides)
    return coordinate_release(
        client,  # type: ignore[arg-type]
        **kwargs,
    )


def test_coordinates_exact_gate_then_exact_deploy_on_dev() -> None:
    client = FakeClient()

    evidence = _coordinate(client)

    assert [workflow for workflow, _ in client.dispatches] == [
        GATE_WORKFLOW,
        DEPLOY_WORKFLOW,
    ]
    gate_inputs = client.dispatches[0][1]
    deploy_inputs = client.dispatches[1][1]
    assert gate_inputs == {
        "fe_sha": FRONTEND_SHA,
        "bff_sha": BACKEND_SHA,
        "bff_base_url": "https://bff.test",
        "pantheon_contract_ref": BACKEND_SHA,
        "release_candidate_id": CANDIDATE_ID,
        "compatibility_manifest_sha256": MANIFEST_SHA,
        "release_controller_run_id": CONTROLLER_RUN_ID,
        "soft_fail": "false",
    }
    assert deploy_inputs["gate_run_id"] == "101"
    assert deploy_inputs["candidate_sha"] == FRONTEND_SHA
    assert deploy_inputs["deployment_profile"] == "read-only"
    assert deploy_inputs["emergency_override"] == "false"
    assert evidence["compatibility_status"] == "compatible"
    assert evidence["backend"]["commit"] == BACKEND_SHA
    assert evidence["frontend"]["commit"] == FRONTEND_SHA
    assert evidence["integration_gate"]["run_id"] == "101"
    assert evidence["frontend_deploy"]["run_id"] == "202"
    assert evidence["outcome"] == "accepted"
    assert evidence["proof_state"] == "COMPLETE"
    assert evidence["candidate"]["profile"] == "read-only"
    assert evidence["candidate"]["pair_id"] == derive_pair_id(BACKEND_SHA, FRONTEND_SHA)
    assert evidence["served_verification"]["status"] == "verified"


def test_gate_failure_stops_before_frontend_deploy(tmp_path: Path) -> None:
    client = FakeClient(gate_conclusion="failure")
    candidate_out = tmp_path / "candidate.json"

    with pytest.raises(ControllerError, match="concluded failure"):
        _coordinate(client, candidate_out=candidate_out)

    assert [workflow for workflow, _ in client.dispatches] == [GATE_WORKFLOW]
    # Check that candidate_out was restored to read-only even on failure
    import json
    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["profile"] == "read-only"


def test_ambiguous_dispatch_fails_closed() -> None:
    client = FakeClient(ambiguous=True)

    with pytest.raises(ControllerError, match="multiple candidate runs"):
        _coordinate(client)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("frontend_sha", "main", "frontend SHA"),
        ("backend_sha", "f" * 39, "backend SHA"),
        ("release_candidate_id", "0" * 63, "release candidate ID"),
        ("compatibility_manifest_sha256", "x" * 64, "compatibility manifest"),
        ("controller_run_id", "0", "controller run ID"),
        ("bff_base_url", "http://bff.test", "must use HTTPS"),
    ],
)
def test_invalid_or_branch_like_identity_is_rejected(
    field: str, value: str, message: str
) -> None:
    client = FakeClient()
    kwargs: dict[str, Any] = {field: value}

    with pytest.raises(ControllerError, match=message):
        _coordinate(client, **kwargs)

    assert client.dispatches == []


def test_arbitrary_pair_id_rejected_in_create_candidate_record() -> None:
    canonical_pair = derive_pair_id(BACKEND_SHA, FRONTEND_SHA)

    # Valid matching pair ID is accepted
    record = create_candidate_record(
        pantheon_sha=BACKEND_SHA,
        execute_plans_sha=FRONTEND_SHA,
        pair_id=canonical_pair,
    )
    assert record["pair_id"] == canonical_pair

    # Arbitrary 64-hex pair ID is rejected immediately
    arbitrary_pair = "f" * 64
    with pytest.raises(ControllerError, match="does not match canonically derived pair ID"):
        create_candidate_record(
            pantheon_sha=BACKEND_SHA,
            execute_plans_sha=FRONTEND_SHA,
            pair_id=arbitrary_pair,
        )


def test_stale_override_rejection_in_coordinate_release_execution_path() -> None:
    client = FakeClient()

    # Mismatched pair_id in coordinate_release fails closed before any dispatch
    with pytest.raises(ControllerError, match="does not match canonically derived pair ID"):
        _coordinate(client, pair_id="f" * 64)
    assert client.dispatches == []


def test_served_mismatch_before_child_dispatch_in_coordinate_release() -> None:
    client = FakeClient()

    # BFF mismatch fails closed before dispatch
    def fake_bff_mismatch(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": "9" * 40}
        return {}

    with pytest.raises(ControllerError, match="served identity mismatch fails closed"):
        _coordinate(client, fetch_fn=fake_bff_mismatch)
    assert client.dispatches == []

    # Network / transport error fails closed before dispatch
    def fake_transport_error(url: str) -> dict[str, Any]:
        raise RuntimeError("connection refused")

    with pytest.raises(ControllerError, match="served identity verification failed reaching BFF"):
        _coordinate(client, fetch_fn=fake_transport_error)
    assert client.dispatches == []


def test_adversarial_monkeypatch_served_verification_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()

    def raise_probe(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("adversarial probe served verification failed")

    monkeypatch.setattr(
        "scripts.cross_repo_release_controller.verify_served_identity",
        raise_probe,
    )

    with pytest.raises(RuntimeError, match="adversarial probe served verification failed"):
        _coordinate(client)
    assert client.dispatches == []


def test_adversarial_monkeypatch_restoration_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()

    def raise_probe(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("adversarial probe restoration failed")

    monkeypatch.setattr(
        "scripts.cross_repo_release_controller.restore_read_only_profile",
        raise_probe,
    )

    with pytest.raises(RuntimeError, match="adversarial probe restoration failed"):
        _coordinate(client)


def test_read_only_restoration_on_all_terminal_outcomes(tmp_path: Path) -> None:
    import json

    # 1. Success outcome
    client_success = FakeClient()
    candidate_out_success = tmp_path / "candidate_success.json"
    evidence = _coordinate(client_success, candidate_out=candidate_out_success)
    assert evidence["candidate"]["profile"] == "read-only"
    assert "restored_at" in evidence["candidate"]
    data_success = json.loads(candidate_out_success.read_text(encoding="utf-8"))
    assert data_success["profile"] == "read-only"

    # 2. Failure outcome (gate failure)
    client_failure = FakeClient(gate_conclusion="failure")
    candidate_out_failure = tmp_path / "candidate_failure.json"
    with pytest.raises(ControllerError, match="concluded failure"):
        _coordinate(client_failure, candidate_out=candidate_out_failure)
    data_failure = json.loads(candidate_out_failure.read_text(encoding="utf-8"))
    assert data_failure["profile"] == "read-only"
    assert "restored_at" in data_failure


def test_nonprod_workflow_seals_exact_dev_pair_before_any_switch() -> None:
    header = NONPROD_WORKFLOW[: NONPROD_WORKFLOW.index("permissions:")]
    deploy_job = NONPROD_WORKFLOW[
        NONPROD_WORKFLOW.index("  deploy-dev:") :
        NONPROD_WORKFLOW.index("  coordinate-dev-release:")
    ]

    assert '- "publish/v*"' not in header
    assert "frontend_sha:" in header
    assert (
        "github.event_name == 'workflow_dispatch' && inputs.environment == 'dev'"
        in deploy_job
    )
    assert (
        'GITHUB_REF}" != "refs/heads/dev" || "${GITHUB_SHA}" != "${sha}"'
        in deploy_job
    )
    assert (
        "Out-of-order execute-plans candidate rejected: current dev is"
        in deploy_job
    )
    generate = deploy_job.index(
        "Generate immutable exact-pair admission before any dev switch"
    )
    seal = deploy_job.index(
        "Seal exact-pair admission artifact before any dev switch"
    )
    switch = deploy_job.index("Deploy dev VM stack under lease")
    assert generate < seal < switch
    assert "agora_compat_manifest.py write" in deploy_job
    assert "--frontend-runtime-commit" in deploy_job
    assert "compatibility_status" not in deploy_job[generate:seal] or (
        "--compatibility-status accepted" in deploy_job[generate:seal]
    )
    assert "release_candidate_id" in deploy_job
    assert "compatibility_manifest_sha256" in deploy_job


def test_rejected_frontend_transaction_restores_and_proves_exact_pair() -> None:
    controller_job = NONPROD_WORKFLOW[
        NONPROD_WORKFLOW.index("  coordinate-dev-release:") :
        NONPROD_WORKFLOW.index("  deploy-staging-live:")
    ]

    assert "needs:\n      - deploy-dev" in controller_job
    assert "cross_repo_release_controller.py" in controller_job
    assert "CROSS_REPO_RELEASE_TOKEN" in controller_job
    assert "continue-on-error: true" in controller_job
    assert "compensate_cross_repo_release.sh" in controller_job
    assert "steps.frontend_release.outcome != 'success'" in controller_job
    assert "previous_backend_sha" in controller_job
    assert "previous_frontend_sha" in controller_job
    assert "release-compensation.json" in controller_job
    assert "exit 75" in controller_job

    assert "--component bff" in COMPENSATION_SCRIPT
    assert "PANTHEON_ROLLBACK_BACKEND_SHA" in COMPENSATION_SCRIPT
    assert "PANTHEON_ROLLBACK_FRONTEND_SHA" in COMPENSATION_SCRIPT
    assert "${DEV_BFF_URL%/}/bff/version" in COMPENSATION_SCRIPT
    assert "${DEV_FE_URL%/}/deployment.json" in COMPENSATION_SCRIPT
    assert "pantheon.cross-repo-release-compensation.v1" in COMPENSATION_SCRIPT
    assert '"outcome": "compensated"' in COMPENSATION_SCRIPT
    assert "docker-compose.yml" not in COMPENSATION_SCRIPT


def test_compensation_step_provides_every_var_the_script_requires() -> None:
    # OPS-COMPENSATE-RELEASE-GCP-PROJECT-ID-20260818: the compensation script
    # dies closed if any of its `required()` env vars is unset (see `for name
    # in ... do required "${name}"; done` below), but nothing previously
    # checked that the *calling* workflow step actually supplied all of them.
    # GCP_DEPLOY_PROJECT_ID silently fell out of the step's env block --
    # observed live as `compensate_cross_repo_release.sh` exiting 75 the one
    # time a real release rejection needed to trigger a rollback, well after
    # the step itself had long since been reviewed and merged. This asserts
    # the two never drift apart again, rather than re-diagnosing the same gap
    # by hand the next time it reopens.
    required_block = COMPENSATION_SCRIPT[
        COMPENSATION_SCRIPT.index("for name in \\") :
        COMPENSATION_SCRIPT.index("done\n", COMPENSATION_SCRIPT.index("for name in \\"))
    ]
    required_vars = re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*\\?\s*$", required_block, re.MULTILINE)
    assert "GCP_DEPLOY_PROJECT_ID" in required_vars

    # These are populated by the Actions runner itself for every step; a
    # workflow author never declares them explicitly.
    always_available = {
        "RUNNER_TEMP",
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_SERVER_URL",
    }

    # Slice the whole containing job, not just the compensation step's own
    # `env:` block: a step inherits its job's job-level env (e.g. DEV_BFF_URL
    # is only declared once, at job level, for every step in this job to
    # share) -- checking the step alone would falsely flag those as missing.
    job = NONPROD_WORKFLOW[
        NONPROD_WORKFLOW.index("  coordinate-dev-release:") :
        NONPROD_WORKFLOW.index("  deploy-staging-live:")
    ]
    missing = [name for name in required_vars if name not in always_available and f"{name}:" not in job]
    assert missing == []


def test_candidate_derives_exact_current_fe_bff_pair_and_pair_id() -> None:
    pair_id = derive_pair_id(BACKEND_SHA, FRONTEND_SHA)
    assert len(pair_id) == 64
    assert re.fullmatch(r"^[0-9a-f]{64}$", pair_id)

    # Deterministic derivation
    assert derive_pair_id(BACKEND_SHA, FRONTEND_SHA) == pair_id
    assert derive_pair_id(BACKEND_SHA, "2" * 40) != pair_id

    candidate = create_candidate_record(
        pantheon_sha=BACKEND_SHA,
        execute_plans_sha=FRONTEND_SHA,
        profile="write-proof",
        source_mode="reconcile-only",
    )
    assert candidate["pantheon_sha"] == BACKEND_SHA
    assert candidate["execute_plans_sha"] == FRONTEND_SHA
    assert candidate["pair_id"] == pair_id
    assert candidate["profile"] == "write-proof"
    assert candidate["source_mode"] == "reconcile-only"
    assert "expires_at" in candidate
    assert len(candidate["candidate_id"]) == 64


def test_stale_task_pair_or_child_inputs_cannot_override_candidate() -> None:
    canonical_pair = derive_pair_id(BACKEND_SHA, FRONTEND_SHA)
    candidate = create_candidate_record(
        pantheon_sha=BACKEND_SHA,
        execute_plans_sha=FRONTEND_SHA,
        candidate_id=CANDIDATE_ID,
        pair_id=canonical_pair,
    )

    # Valid matching inputs pass
    validate_candidate_override(
        candidate,
        {
            "frontend_sha": FRONTEND_SHA,
            "backend_sha": BACKEND_SHA,
            "candidate_id": CANDIDATE_ID,
            "pair_id": canonical_pair,
        },
    )

    # Stale / mismatched frontend SHA fails closed
    with pytest.raises(ControllerError, match="stale task pair or child inputs cannot override parent candidate"):
        validate_candidate_override(candidate, {"frontend_sha": "2" * 40})

    # Stale / mismatched backend SHA fails closed
    with pytest.raises(ControllerError, match="stale task pair or child inputs cannot override parent candidate"):
        validate_candidate_override(candidate, {"backend_sha": "b" * 40})

    # Stale / mismatched pair ID fails closed
    with pytest.raises(ControllerError, match="stale task pair or child inputs cannot override parent candidate"):
        validate_candidate_override(candidate, {"pair_id": "0" * 64})

    # Stale / mismatched candidate ID fails closed
    with pytest.raises(ControllerError, match="stale task pair or child inputs cannot override parent candidate"):
        validate_candidate_override(candidate, {"candidate_id": "1" * 64})


def test_no_operator_prompt_or_proof_window_ack_required() -> None:
    client = FakeClient()
    evidence = _coordinate(client)

    # Coordination proceeds automatically without operator prompt or proof_window_ack gate
    assert evidence["outcome"] == "accepted"
    assert "candidate" in evidence
    assert evidence["candidate"]["profile"] == "read-only"
    assert evidence["candidate"]["source_mode"] == "reconcile-only"


def test_served_mismatch_fails_closed() -> None:
    canonical_pair = derive_pair_id(BACKEND_SHA, FRONTEND_SHA)
    candidate = create_candidate_record(
        pantheon_sha=BACKEND_SHA,
        execute_plans_sha=FRONTEND_SHA,
        candidate_id=CANDIDATE_ID,
        pair_id=canonical_pair,
    )

    def fake_matching_fetcher(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": BACKEND_SHA}
        if "deployment.json" in url:
            return {
                "frontendSha": FRONTEND_SHA,
                "pairId": canonical_pair,
            }
        return {}

    result = verify_served_identity(
        bff_base_url="https://bff.test",
        fe_base_url="https://fe.test",
        expected_candidate=candidate,
        fetch_fn=fake_matching_fetcher,
    )
    assert result["status"] == "verified"
    assert result["observed_bff_sha"] == BACKEND_SHA
    assert result["observed_fe_sha"] == FRONTEND_SHA
    assert result["observed_pair_id"] == canonical_pair

    # BFF mismatch fails closed
    def fake_bff_mismatch(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": "9" * 40}
        return {}

    with pytest.raises(ControllerError, match="served identity mismatch fails closed"):
        verify_served_identity(
            bff_base_url="https://bff.test",
            expected_candidate=candidate,
            fetch_fn=fake_bff_mismatch,
        )

    # FE mismatch fails closed
    def fake_fe_mismatch(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": BACKEND_SHA}
        if "deployment.json" in url:
            return {"frontendSha": "9" * 40, "pairId": canonical_pair}
        return {}

    with pytest.raises(ControllerError, match="served identity mismatch fails closed"):
        verify_served_identity(
            bff_base_url="https://bff.test",
            fe_base_url="https://fe.test",
            expected_candidate=candidate,
            fetch_fn=fake_fe_mismatch,
        )


def test_read_only_restored_on_success_failure_cancellation_expiry() -> None:
    canonical_pair = derive_pair_id(BACKEND_SHA, FRONTEND_SHA)
    candidate = create_candidate_record(
        pantheon_sha=BACKEND_SHA,
        execute_plans_sha=FRONTEND_SHA,
        candidate_id=CANDIDATE_ID,
        pair_id=canonical_pair,
        profile="write-proof",
    )
    assert candidate["profile"] == "write-proof"

    restored = restore_read_only_profile(candidate)
    assert restored["profile"] == "read-only"
    assert restored["candidate_id"] == candidate["candidate_id"]
    assert restored["pair_id"] == candidate["pair_id"]
    assert "restored_at" in restored

    # Idempotent call returns same profile
    restored_again = restore_read_only_profile(restored)
    assert restored_again["profile"] == "read-only"

    # With served manifest verification
    served_manifest = {
        "frontendSha": FRONTEND_SHA,
        "bffCommit": BACKEND_SHA,
    }
    restored_verified = restore_read_only_profile(candidate, served_manifest=served_manifest)
    assert restored_verified["profile"] == "read-only"

    # Mismatched served manifest raises error
    bad_manifest = {
        "frontendSha": "9" * 40,
        "bffCommit": BACKEND_SHA,
    }
    with pytest.raises(ControllerError, match="read-only restoration verification mismatch"):
        restore_read_only_profile(candidate, served_manifest=bad_manifest)


def test_source_stays_reconcile_only_and_prohibits_live_capital() -> None:
    with pytest.raises(ControllerError, match="source_mode must be 'reconcile-only'"):
        create_candidate_record(
            pantheon_sha=BACKEND_SHA,
            execute_plans_sha=FRONTEND_SHA,
            source_mode="external-pull",
        )

    with pytest.raises(ControllerError, match="invalid profile"):
        create_candidate_record(
            pantheon_sha=BACKEND_SHA,
            execute_plans_sha=FRONTEND_SHA,
            profile="live-capital",
        )


def test_proof_state_machine_transitions() -> None:
    sm = ProofStateMachine("CREATED")
    assert sm.state == "CREATED"
    assert sm.transition("IDENTITY_VERIFIED") == "IDENTITY_VERIFIED"
    assert sm.transition("WRITE_PROOF_ACTIVE") == "WRITE_PROOF_ACTIVE"
    assert sm.transition("JOURNEYS_RUNNING") == "JOURNEYS_RUNNING"
    assert sm.transition("PROOF_CAPTURED") == "PROOF_CAPTURED"
    assert sm.transition("READ_ONLY_RESTORED") == "READ_ONLY_RESTORED"
    assert sm.transition("COMPLETE") == "COMPLETE"

    # Idempotent same-state transition
    assert sm.transition("COMPLETE") == "COMPLETE"

    # Error recovery transition directly to READ_ONLY_RESTORED from any state
    sm2 = ProofStateMachine("WRITE_PROOF_ACTIVE")
    assert sm2.transition("READ_ONLY_RESTORED") == "READ_ONLY_RESTORED"

    # Invalid state transition raises error
    sm3 = ProofStateMachine("CREATED")
    with pytest.raises(ControllerError, match="invalid state transition"):
        sm3.transition("COMPLETE")


def test_nonprod_workflow_outputs_candidate_auto_binding_contract() -> None:
    deploy_dev = NONPROD_WORKFLOW[
        NONPROD_WORKFLOW.index("  deploy-dev:") :
        NONPROD_WORKFLOW.index("  coordinate-dev-release:")
    ]
    coordinate_job = NONPROD_WORKFLOW[
        NONPROD_WORKFLOW.index("  coordinate-dev-release:") :
        NONPROD_WORKFLOW.index("  deploy-staging-live:")
    ]

    assert "pair_id: ${{ steps.release_admission.outputs.pair_id }}" in deploy_dev
    assert "outputs:" in coordinate_job
    assert "candidate_id:" in coordinate_job
    assert "pair_id:" in coordinate_job
    assert "profile:" in coordinate_job
    assert "source_mode:" in coordinate_job
    assert "expires_at:" in coordinate_job
    assert "--candidate-out" in coordinate_job
