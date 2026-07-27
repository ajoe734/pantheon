from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.cross_repo_release_controller import (
    ControllerError,
    DEPLOY_WORKFLOW,
    GATE_WORKFLOW,
    coordinate_release,
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


def _coordinate(client: FakeClient) -> dict[str, Any]:
    return coordinate_release(
        client,  # type: ignore[arg-type]
        frontend_sha=FRONTEND_SHA,
        backend_sha=BACKEND_SHA,
        bff_base_url="https://bff.test",
        release_candidate_id=CANDIDATE_ID,
        compatibility_manifest_sha256=MANIFEST_SHA,
        controller_run_id=CONTROLLER_RUN_ID,
        gate_timeout_seconds=5,
        deploy_timeout_seconds=5,
        poll_seconds=0,
        sleep=lambda _: None,
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


def test_gate_failure_stops_before_frontend_deploy() -> None:
    client = FakeClient(gate_conclusion="failure")

    with pytest.raises(ControllerError, match="concluded failure"):
        _coordinate(client)

    assert [workflow for workflow, _ in client.dispatches] == [GATE_WORKFLOW]


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
        "sleep": lambda _: None,
    }
    kwargs[field] = value

    with pytest.raises(ControllerError, match=message):
        coordinate_release(client, **kwargs)  # type: ignore[arg-type]

    assert client.dispatches == []


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
