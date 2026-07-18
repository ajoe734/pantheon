from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_manual_dispatches_queue_per_environment_without_top_level_cancellation() -> None:
    concurrency = _workflow()["concurrency"]

    assert concurrency == {
        "group": (
            "pantheon-nonprod-deploy-"
            "${{ github.event_name == 'workflow_dispatch' && inputs.environment || github.run_id }}"
        ),
        "queue": "max",
    }
    assert "cancel-in-progress" not in concurrency


def test_push_cancellation_is_scoped_to_each_deploy_job() -> None:
    jobs = _workflow()["jobs"]

    assert jobs["deploy-dev"]["concurrency"] == {
        "group": (
            "pantheon-nonprod-deploy-dev-"
            "${{ github.event_name == 'push' && 'dev-auto' || github.run_id }}"
        ),
        "cancel-in-progress": "${{ github.event_name == 'push' }}",
    }
    assert jobs["deploy-staging-live"]["concurrency"] == {
        "group": (
            "pantheon-nonprod-deploy-staging-live-"
            "${{ github.event_name == 'push' && 'staging-auto' || github.run_id }}"
        ),
        "cancel-in-progress": "${{ github.event_name == 'push' }}",
    }


def test_queue_max_is_never_combined_with_cancel_in_progress() -> None:
    workflow = _workflow()
    concurrency_blocks = [workflow["concurrency"]]
    concurrency_blocks.extend(
        job["concurrency"]
        for job in workflow["jobs"].values()
        if "concurrency" in job
    )

    for concurrency in concurrency_blocks:
        if concurrency.get("queue") == "max":
            assert "cancel-in-progress" not in concurrency
