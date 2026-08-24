from __future__ import annotations

import json
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
    head_sha: str = FRONTEND_SHA,
) -> dict[str, Any]:
    return {
        "id": run_id,
        "path": f".github/workflows/{workflow}@refs/heads/dev",
        "head_repository": {"full_name": "ajoe734/execute-plans"},
        "repository": {"full_name": "ajoe734/execute-plans"},
        "event": "workflow_dispatch",
        "head_branch": "dev",
        "head_sha": head_sha,
        "display_title": title,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://github.test/runs/{run_id}",
    }


class FakeClient:
    def __init__(
        self,
        *,
        gate_conclusion: str = "success",
        deploy_conclusion: str = "success",
        ambiguous: bool = False,
        timeout_workflow: str | None = None,
    ):
        self.dispatches: list[tuple[str, dict[str, str]]] = []
        self.gate_conclusion = gate_conclusion
        self.deploy_conclusion = deploy_conclusion
        self.ambiguous = ambiguous
        self.timeout_workflow = timeout_workflow

    def list_runs(self, workflow: str) -> list[dict[str, Any]]:
        dispatched = [item for item in self.dispatches if item[0] == workflow]
        if not dispatched:
            return []
        if self.timeout_workflow == workflow:
            return []
        inputs = dispatched[-1][1]
        candidate_id = inputs.get("release_candidate_id", CANDIDATE_ID)
        title = (
            f"Release candidate {candidate_id}"
            if workflow == GATE_WORKFLOW
            else f"Deploy release candidate {candidate_id}"
        )
        head_sha = inputs.get("fe_sha") or inputs.get("candidate_sha") or FRONTEND_SHA
        run = _run(
            101 if workflow == GATE_WORKFLOW else 202,
            workflow=workflow,
            title=title,
            status="in_progress",
            conclusion=None,
            head_sha=head_sha,
        )
        if self.ambiguous:
            return [run, {**run, "id": run["id"] + 1}]
        return [run]

    def dispatch(self, workflow: str, inputs: dict[str, str]) -> None:
        self.dispatches.append((workflow, inputs))

    def get_run(self, run_id: int) -> dict[str, Any]:
        if run_id == 101:
            gate_dispatches = [item for item in self.dispatches if item[0] == GATE_WORKFLOW]
            inputs = gate_dispatches[-1][1] if gate_dispatches else {}
            candidate_id = inputs.get("release_candidate_id", CANDIDATE_ID)
            head_sha = inputs.get("fe_sha", FRONTEND_SHA)
            return _run(
                101,
                workflow=GATE_WORKFLOW,
                title=f"Release candidate {candidate_id}",
                conclusion=self.gate_conclusion,
                head_sha=head_sha,
            )
        deploy_dispatches = [item for item in self.dispatches if item[0] == DEPLOY_WORKFLOW]
        inputs = deploy_dispatches[-1][1] if deploy_dispatches else {}
        candidate_id = inputs.get("release_candidate_id", CANDIDATE_ID)
        head_sha = inputs.get("candidate_sha", FRONTEND_SHA)
        return _run(
            202,
            workflow=DEPLOY_WORKFLOW,
            title=f"Deploy release candidate {candidate_id}",
            conclusion=self.deploy_conclusion,
            head_sha=head_sha,
        )


PREV_FRONTEND_SHA = "2" * 40
PREV_PAIR_ID = "prev-pair-identity-12345"
SERVED_PAIR_ID = "execute-plans-canonical-pair-67890"


def _default_fetch_fn(url: str) -> dict[str, Any]:
    if "version" in url:
        return {"source_commit_sha": BACKEND_SHA}
    if "deployment.json" in url:
        return {
            "frontendSha": FRONTEND_SHA,
            "bffCommit": BACKEND_SHA,
            "pairId": SERVED_PAIR_ID,
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
        "fe_base_url": "https://fe.test",
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
    assert evidence["proof_state"] == "READ_ONLY_RESTORED"
    assert evidence["candidate"]["profile"] == "read-only"
    assert evidence["candidate"]["pair_id"] == SERVED_PAIR_ID
    assert evidence["served_verification"]["status"] == "verified"
    assert evidence["served_verification"]["observed_bff_sha"] == BACKEND_SHA
    assert evidence["served_verification"]["observed_fe_sha"] == FRONTEND_SHA
    assert evidence["served_verification"]["observed_pair_id"] == SERVED_PAIR_ID


def test_predecessor_to_candidate_switching_and_served_binding() -> None:
    client = FakeClient()
    switched = False

    def dynamic_fetcher(url: str) -> dict[str, Any]:
        nonlocal switched
        if "version" in url:
            return {"source_commit_sha": BACKEND_SHA}
        if "deployment.json" in url:
            # Check if deploy has been dispatched yet
            if any(w == DEPLOY_WORKFLOW for w, _ in client.dispatches):
                switched = True
                return {
                    "frontendSha": FRONTEND_SHA,
                    "bffCommit": BACKEND_SHA,
                    "pairId": SERVED_PAIR_ID,
                }
            # Pre-dispatch: still on predecessor!
            return {
                "frontendSha": PREV_FRONTEND_SHA,
                "bffCommit": BACKEND_SHA,
                "pairId": PREV_PAIR_ID,
            }
        return {}

    evidence = _coordinate(client, fetch_fn=dynamic_fetcher)
    assert switched is True
    assert evidence["candidate"]["pair_id"] == SERVED_PAIR_ID
    assert evidence["candidate"]["execute_plans_sha"] == FRONTEND_SHA
    assert evidence["served_verification"]["observed_fe_sha"] == FRONTEND_SHA
    assert evidence["served_verification"]["observed_pair_id"] == SERVED_PAIR_ID
    assert evidence["outcome"] == "accepted"


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


def test_canonical_pair_id_handling_in_create_candidate_record() -> None:
    # String pair ID (slug, digest, etc.) accepted
    record = create_candidate_record(
        pantheon_sha=BACKEND_SHA,
        execute_plans_sha=FRONTEND_SHA,
        pair_id="l12-mfc-r4-hosted-closeout-20260814-exact-pair",
    )
    assert record["pair_id"] == "l12-mfc-r4-hosted-closeout-20260814-exact-pair"

    # None pair ID accepted (to be bound post-switch)
    record_none = create_candidate_record(
        pantheon_sha=BACKEND_SHA,
        execute_plans_sha=FRONTEND_SHA,
    )
    assert record_none["pair_id"] is None

    # Derive from manifest helper
    manifest = {"pairId": "execute-plans-pair-xyz"}
    assert derive_pair_id(manifest=manifest) == "execute-plans-pair-xyz"
    assert derive_pair_id() is None

    # Invalid pair ID (e.g. invalid chars or empty) rejected
    with pytest.raises(ControllerError, match="must be a valid pair identifier"):
        create_candidate_record(
            pantheon_sha=BACKEND_SHA,
            execute_plans_sha=FRONTEND_SHA,
            pair_id="",
        )


def test_stale_override_rejection_in_coordinate_release_execution_path() -> None:
    client = FakeClient()

    # Invalid pair_id in coordinate_release fails closed before any dispatch
    with pytest.raises(ControllerError, match="must be a valid pair identifier"):
        _coordinate(client, pair_id="invalid pair with spaces!")
    assert client.dispatches == []


def test_pre_dispatch_bff_mismatch_fails_closed_and_restores_read_only(tmp_path: Path) -> None:
    client = FakeClient()
    candidate_out = tmp_path / "candidate_bff_mismatch.json"

    def fake_bff_mismatch(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": "9" * 40}
        return {}

    with pytest.raises(ControllerError, match="served identity mismatch fails closed: BFF served"):
        _coordinate(
            client,
            fetch_fn=fake_bff_mismatch,
            candidate_out=candidate_out,
            candidate_profile="write-proof",
        )
    assert client.dispatches == []
    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["profile"] == "read-only"


def test_post_switch_fe_mismatch_fails_closed_and_restores_read_only(tmp_path: Path) -> None:
    client = FakeClient()
    candidate_out = tmp_path / "candidate_fe_mismatch.json"

    # FE never switches to candidate (still on predecessor after deploy)
    def fake_fe_mismatch(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": BACKEND_SHA}
        if "deployment.json" in url:
            return {
                "frontendSha": PREV_FRONTEND_SHA,
                "bffCommit": BACKEND_SHA,
                "pairId": PREV_PAIR_ID,
            }
        return {}

    with pytest.raises(ControllerError, match="served identity mismatch fails closed: FE served"):
        _coordinate(
            client,
            fetch_fn=fake_fe_mismatch,
            candidate_out=candidate_out,
            candidate_profile="write-proof",
        )
    assert [workflow for workflow, _ in client.dispatches] == [GATE_WORKFLOW, DEPLOY_WORKFLOW]
    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["profile"] == "read-only"


def test_post_switch_pair_mismatch_fails_closed_and_restores_read_only(tmp_path: Path) -> None:
    client = FakeClient()
    candidate_out = tmp_path / "candidate_pair_mismatch.json"

    def fake_pair_mismatch(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": BACKEND_SHA}
        if "deployment.json" in url:
            return {
                "frontendSha": FRONTEND_SHA,
                "bffCommit": BACKEND_SHA,
                "pairId": "conflicting-pair-identity",
            }
        return {}

    with pytest.raises(ControllerError, match="served identity mismatch fails closed: served pair ID"):
        _coordinate(
            client,
            pair_id="expected-pair-identity",
            fetch_fn=fake_pair_mismatch,
            candidate_out=candidate_out,
            candidate_profile="write-proof",
        )
    assert [workflow for workflow, _ in client.dispatches] == [GATE_WORKFLOW, DEPLOY_WORKFLOW]
    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["profile"] == "read-only"


def test_post_switch_missing_pair_fails_closed_and_restores_read_only(tmp_path: Path) -> None:
    client = FakeClient()
    candidate_out = tmp_path / "candidate_missing_pair.json"

    def fake_missing_pair(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": BACKEND_SHA}
        if "deployment.json" in url:
            return {
                "frontendSha": FRONTEND_SHA,
                "bffCommit": BACKEND_SHA,
                # Missing pairId and pair_id
            }
        return {}

    with pytest.raises(ControllerError, match="served deployment manifest lacks pair ID"):
        _coordinate(
            client,
            fetch_fn=fake_missing_pair,
            candidate_out=candidate_out,
            candidate_profile="write-proof",
        )
    assert [workflow for workflow, _ in client.dispatches] == [GATE_WORKFLOW, DEPLOY_WORKFLOW]
    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["profile"] == "read-only"


def test_post_switch_missing_fe_sha_fails_closed_and_restores_read_only(tmp_path: Path) -> None:
    client = FakeClient()
    candidate_out = tmp_path / "candidate_missing_fe.json"

    def fake_missing_fe(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": BACKEND_SHA}
        if "deployment.json" in url:
            return {
                "pairId": SERVED_PAIR_ID,
                # Missing frontendSha
            }
        return {}

    with pytest.raises(ControllerError, match="served frontend commit SHA must be one exact lowercase 40-character SHA"):
        _coordinate(
            client,
            fetch_fn=fake_missing_fe,
            candidate_out=candidate_out,
            candidate_profile="write-proof",
        )
    assert [workflow for workflow, _ in client.dispatches] == [GATE_WORKFLOW, DEPLOY_WORKFLOW]
    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["profile"] == "read-only"


def test_served_verification_transport_exception_restores_read_only(tmp_path: Path) -> None:
    client = FakeClient()
    candidate_out = tmp_path / "candidate_transport_error.json"

    def fake_transport_error(url: str) -> dict[str, Any]:
        raise RuntimeError("connection refused")

    with pytest.raises(ControllerError, match="served identity verification failed reaching BFF"):
        _coordinate(
            client,
            fetch_fn=fake_transport_error,
            candidate_out=candidate_out,
            candidate_profile="write-proof",
        )
    assert client.dispatches == []
    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["profile"] == "read-only"


def test_deploy_failure_restores_read_only(tmp_path: Path) -> None:
    client = FakeClient(deploy_conclusion="failure")
    candidate_out = tmp_path / "candidate_deploy_fail.json"

    with pytest.raises(ControllerError, match="concluded failure"):
        _coordinate(client, candidate_out=candidate_out, candidate_profile="write-proof")

    assert [workflow for workflow, _ in client.dispatches] == [GATE_WORKFLOW, DEPLOY_WORKFLOW]
    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["profile"] == "read-only"


def test_gate_timeout_restores_read_only(tmp_path: Path) -> None:
    client = FakeClient(timeout_workflow=GATE_WORKFLOW)
    candidate_out = tmp_path / "candidate_timeout.json"

    with pytest.raises(ControllerError, match="timed out discovering"):
        _coordinate(
            client,
            candidate_out=candidate_out,
            candidate_profile="write-proof",
            gate_timeout_seconds=1,
        )

    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["profile"] == "read-only"


def test_cancellation_and_expiry_restoration(tmp_path: Path) -> None:
    client = FakeClient(timeout_workflow=GATE_WORKFLOW)
    candidate_out = tmp_path / "candidate_cancel.json"

    def predecessor_fetcher(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": BACKEND_SHA}
        if "deployment.json" in url:
            return {
                "frontendSha": PREV_FRONTEND_SHA,
                "bffCommit": BACKEND_SHA,
                "pairId": PREV_PAIR_ID,
            }
        return {}

    with pytest.raises(ControllerError, match="timed out discovering"):
        _coordinate(
            client,
            candidate_out=candidate_out,
            candidate_profile="write-proof",
            fetch_fn=predecessor_fetcher,
            gate_timeout_seconds=1,
        )

    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["profile"] == "read-only"
    assert "restored_at" in data


def test_adversarial_monkeypatch_served_verification_raises() -> None:
    client = FakeClient()

    def raise_probe(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("adversarial probe served verification failed")

    with pytest.raises(RuntimeError, match="adversarial probe served verification failed"):
        _coordinate(client, fetch_fn=raise_probe)
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
    candidate = create_candidate_record(
        pantheon_sha=BACKEND_SHA,
        execute_plans_sha=FRONTEND_SHA,
        pair_id="l12-mfc-r4-hosted-closeout-20260814-exact-pair",
        profile="write-proof",
        source_mode="reconcile-only",
    )
    assert candidate["pantheon_sha"] == BACKEND_SHA
    assert candidate["execute_plans_sha"] == FRONTEND_SHA
    assert candidate["pair_id"] == "l12-mfc-r4-hosted-closeout-20260814-exact-pair"
    assert candidate["profile"] == "write-proof"
    assert candidate["source_mode"] == "reconcile-only"
    assert "expires_at" in candidate
    assert len(candidate["candidate_id"]) == 64


def test_stale_task_pair_or_child_inputs_cannot_override_candidate() -> None:
    canonical_pair = "canonical-pair-identity-12345"
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
        validate_candidate_override(candidate, {"pair_id": "mismatched-pair-identity"})

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
    canonical_pair = "canonical-pair-identity-12345"
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
    canonical_pair = "canonical-pair-identity-12345"
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
        "pairId": canonical_pair,
    }
    restored_verified = restore_read_only_profile(candidate, served_manifest=served_manifest)
    assert restored_verified["profile"] == "read-only"

    # Mismatched FE SHA raises error
    bad_fe_manifest = {
        "frontendSha": "9" * 40,
        "bffCommit": BACKEND_SHA,
    }
    with pytest.raises(ControllerError, match="read-only restoration verification mismatch: served FE="):
        restore_read_only_profile(candidate, served_manifest=bad_fe_manifest)

    # Mismatched BFF SHA raises error
    bad_bff_manifest = {
        "frontendSha": FRONTEND_SHA,
        "bffCommit": "9" * 40,
    }
    with pytest.raises(ControllerError, match="read-only restoration verification mismatch: served BFF="):
        restore_read_only_profile(candidate, served_manifest=bad_bff_manifest)

    # Mismatched pair ID raises error
    bad_pair_manifest = {
        "frontendSha": FRONTEND_SHA,
        "bffCommit": BACKEND_SHA,
        "pairId": "conflicting-pair-identity",
    }
    with pytest.raises(ControllerError, match="read-only restoration verification mismatch: served pair ID"):
        restore_read_only_profile(candidate, served_manifest=bad_pair_manifest)


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

    # SHA-only synthesis and pair_id output are removed from deploy-dev
    assert "pair_id: ${{ steps.release_admission.outputs.pair_id }}" not in deploy_dev
    assert "pair_id = hashlib.sha256(" not in deploy_dev

    # coordinate-dev-release outputs pair_id bound from the controller and does not pass --pair-id
    assert "outputs:" in coordinate_job
    assert "candidate_id:" in coordinate_job
    assert "pair_id: ${{ steps.frontend_release.outputs.pair_id }}" in coordinate_job
    assert "profile:" in coordinate_job
    assert "source_mode:" in coordinate_job
    assert "expires_at:" in coordinate_job
    assert "--pair-id" not in coordinate_job
    assert "PAIR_ID:" not in coordinate_job
    assert "--candidate-out" in coordinate_job
    assert '--fe-base-url "${DEV_FE_URL}"' in coordinate_job


REAL_BACKEND_SHA = "97945de7c5193baa9832f6c02674714d889577b9"
REAL_FRONTEND_SHA = "693d8612218e5ec6620c80ab7a16d3429e842f6c"
REAL_CANONICAL_PAIR_ID = "98c7d8026ef9c396b211b9f34c716be15c0d22c2e55bca4fc0755a9405d38529"
STALE_SYNTHESIZED_PAIR_ID = "0bbfd257a672dbeff45b693a65326915dd8bf92ef92c41e2b2826485fecbb408"


def test_execution_path_real_execute_plans_pair_contract_auto_binding_success(tmp_path: Path) -> None:
    """Execution-path contract test using real execute-plans pair contract and workflow path."""
    client = FakeClient()
    candidate_out = tmp_path / "candidate_real_path.json"

    def real_fetch_fn(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": REAL_BACKEND_SHA}
        if "deployment.json" in url:
            return {
                "schemaVersion": 1,
                "app": "execute-plans",
                "repository": "ajoe734/execute-plans",
                "sourceBranch": "dev",
                "commit": REAL_FRONTEND_SHA,
                "frontendSha": REAL_FRONTEND_SHA,
                "bffCommit": REAL_BACKEND_SHA,
                "deploymentState": "accepted",
                "pairId": REAL_CANONICAL_PAIR_ID,
            }
        return {}

    evidence = _coordinate(
        client,
        frontend_sha=REAL_FRONTEND_SHA,
        backend_sha=REAL_BACKEND_SHA,
        fetch_fn=real_fetch_fn,
        candidate_out=candidate_out,
        pair_id=None,  # Workflow input path: no synthetic or stale pair passed
    )

    assert evidence["outcome"] == "accepted"
    assert evidence["candidate"]["pair_id"] == REAL_CANONICAL_PAIR_ID
    assert evidence["candidate"]["pantheon_sha"] == REAL_BACKEND_SHA
    assert evidence["candidate"]["execute_plans_sha"] == REAL_FRONTEND_SHA
    assert evidence["candidate"]["profile"] == "read-only"
    assert evidence["candidate"]["source_mode"] == "reconcile-only"
    assert evidence["served_verification"]["observed_pair_id"] == REAL_CANONICAL_PAIR_ID
    assert evidence["proof_state"] == "READ_ONLY_RESTORED"

    # Confirm candidate_out file reflects the auto-bound canonical pair
    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["pair_id"] == REAL_CANONICAL_PAIR_ID
    assert data["profile"] == "read-only"


def test_execution_path_stale_synthesized_pair_override_rejected(tmp_path: Path) -> None:
    """Test that well-formed stale supplied pair inputs (like the SHA-only synthesis) are rejected."""
    client = FakeClient()
    candidate_out = tmp_path / "candidate_stale_rejected.json"

    def real_fetch_fn(url: str) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": REAL_BACKEND_SHA}
        if "deployment.json" in url:
            return {
                "schemaVersion": 1,
                "app": "execute-plans",
                "repository": "ajoe734/execute-plans",
                "sourceBranch": "dev",
                "commit": REAL_FRONTEND_SHA,
                "frontendSha": REAL_FRONTEND_SHA,
                "bffCommit": REAL_BACKEND_SHA,
                "deploymentState": "accepted",
                "pairId": REAL_CANONICAL_PAIR_ID,
            }
        return {}

    # Calling with the stale synthesized pair formula fails closed
    with pytest.raises(
        ControllerError,
        match=f"served identity mismatch fails closed: served pair ID {REAL_CANONICAL_PAIR_ID} != candidate {STALE_SYNTHESIZED_PAIR_ID}",
    ):
        _coordinate(
            client,
            frontend_sha=REAL_FRONTEND_SHA,
            backend_sha=REAL_BACKEND_SHA,
            fetch_fn=real_fetch_fn,
            candidate_out=candidate_out,
            pair_id=STALE_SYNTHESIZED_PAIR_ID,
            candidate_profile="write-proof",
        )

    # Read-only profile restored on failure
    data = json.loads(candidate_out.read_text(encoding="utf-8"))
    assert data["profile"] == "read-only"


def test_derive_pair_id_from_execute_plans_pair_artifact() -> None:
    """derive_pair_id extracts pairId from real execute-plans pair artifact and returns None if no manifest."""
    manifest = {
        "schemaVersion": 1,
        "app": "execute-plans",
        "pairId": REAL_CANONICAL_PAIR_ID,
        "releaseAdmission": {
            "backend": {"commitSha": REAL_BACKEND_SHA},
            "frontend": {"commitSha": REAL_FRONTEND_SHA},
        },
    }
    assert derive_pair_id(manifest=manifest) == REAL_CANONICAL_PAIR_ID
    # Without manifest, does NOT synthesize SHA256 of only SHAs
    assert derive_pair_id(REAL_BACKEND_SHA, REAL_FRONTEND_SHA) is None


def test_execution_path_cli_main_workflow_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI main execution path matching nonprod-deploy.yml arguments (without --pair-id)."""
    evidence_out = tmp_path / "controller-evidence.json"
    candidate_out = tmp_path / "release-candidate.json"
    github_output = tmp_path / "github_output.txt"

    monkeypatch.setenv("CROSS_REPO_RELEASE_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    def fake_fetch(url: str, timeout_seconds: int = 30) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": REAL_BACKEND_SHA}
        if "deployment.json" in url:
            return {
                "schemaVersion": 1,
                "app": "execute-plans",
                "frontendSha": REAL_FRONTEND_SHA,
                "bffCommit": REAL_BACKEND_SHA,
                "pairId": REAL_CANONICAL_PAIR_ID,
            }
        return {}

    monkeypatch.setattr("scripts.cross_repo_release_controller.fetch_url_json", fake_fetch)

    fake_client = FakeClient()
    monkeypatch.setattr(
        "scripts.cross_repo_release_controller.GitHubClient",
        lambda **kwargs: fake_client,
    )

    from scripts.cross_repo_release_controller import main

    exit_code = main([
        "--frontend-sha", REAL_FRONTEND_SHA,
        "--backend-sha", REAL_BACKEND_SHA,
        "--bff-base-url", "https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io",
        "--fe-base-url", "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io",
        "--release-candidate-id", CANDIDATE_ID,
        "--compatibility-manifest-sha256", MANIFEST_SHA,
        "--controller-run-id", "99999",
        "--evidence-out", str(evidence_out),
        "--candidate-out", str(candidate_out),
        "--poll-seconds", "0.01",
    ])

    assert exit_code == 0
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    assert evidence["outcome"] == "accepted"
    assert evidence["candidate"]["pair_id"] == REAL_CANONICAL_PAIR_ID

    # Verify $GITHUB_OUTPUT was written with pair_id
    output_text = github_output.read_text(encoding="utf-8")
    assert f"pair_id={REAL_CANONICAL_PAIR_ID}" in output_text
    assert f"pantheon_sha={REAL_BACKEND_SHA}" in output_text
    assert f"execute_plans_sha={REAL_FRONTEND_SHA}" in output_text
    assert "profile=read-only" in output_text


def test_execution_path_cli_main_stale_pair_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI main execution path with stale supplied --pair-id fails closed with exit code 1."""
    evidence_out = tmp_path / "controller-evidence-rejected.json"
    candidate_out = tmp_path / "release-candidate-rejected.json"

    monkeypatch.setenv("CROSS_REPO_RELEASE_TOKEN", "fake-token")

    def fake_fetch(url: str, timeout_seconds: int = 30) -> dict[str, Any]:
        if "version" in url:
            return {"source_commit_sha": REAL_BACKEND_SHA}
        if "deployment.json" in url:
            return {
                "schemaVersion": 1,
                "app": "execute-plans",
                "frontendSha": REAL_FRONTEND_SHA,
                "bffCommit": REAL_BACKEND_SHA,
                "pairId": REAL_CANONICAL_PAIR_ID,
            }
        return {}

    monkeypatch.setattr("scripts.cross_repo_release_controller.fetch_url_json", fake_fetch)

    fake_client = FakeClient()
    monkeypatch.setattr(
        "scripts.cross_repo_release_controller.GitHubClient",
        lambda **kwargs: fake_client,
    )

    from scripts.cross_repo_release_controller import main

    exit_code = main([
        "--frontend-sha", REAL_FRONTEND_SHA,
        "--backend-sha", REAL_BACKEND_SHA,
        "--bff-base-url", "https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io",
        "--fe-base-url", "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io",
        "--release-candidate-id", CANDIDATE_ID,
        "--compatibility-manifest-sha256", MANIFEST_SHA,
        "--controller-run-id", "99999",
        "--pair-id", STALE_SYNTHESIZED_PAIR_ID,
        "--evidence-out", str(evidence_out),
        "--candidate-out", str(candidate_out),
        "--poll-seconds", "0.01",
    ])

    assert exit_code == 1

