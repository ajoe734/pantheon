"""DEV-GREEN-COMMIT-DEPLOY-TRIGGER-20261117: dev releases follow green commits."""
from pathlib import Path

import yaml

from scripts.test_auto_deploy_dev_pair import A1, B, F, OLD, ahead, setup_pair

ROOT = Path(__file__).resolve().parents[1]
BRANCH_CI = ROOT / ".github/workflows/branch-ci.yml"
PUBLISH = ROOT / ".github/workflows/nightly-publish-cut.yml"


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(doc):
    return doc.get(True) or doc["on"]


def test_dev_push_runs_are_never_cancelled_in_progress_but_others_are():
    doc = _load(BRANCH_CI)
    assert doc["concurrency"]["group"] == "branch-ci-${{ github.workflow }}-${{ github.ref }}"
    expr = doc["concurrency"]["cancel-in-progress"]
    assert expr == "${{ !(github.event_name == 'push' && github.ref == 'refs/heads/dev') }}"
    # One group per ref: at most one in progress plus one pending (newer push replaces pending).
    assert "github.ref" in doc["concurrency"]["group"]

    def evaluate(event, ref):
        return not (event == "push" and ref == "refs/heads/dev")

    assert evaluate("push", "refs/heads/dev") is False
    assert evaluate("pull_request", "refs/pull/1/merge") is True
    assert evaluate("push", "refs/heads/master") is True
    assert evaluate("push", "refs/heads/publish/x") is True
    assert evaluate("workflow_dispatch", "refs/heads/promote/x") is True


def test_publish_cut_triggers_on_green_dev_push_ci_and_keeps_offset_schedule():
    doc = _load(PUBLISH)
    triggers = _on(doc)
    assert triggers["workflow_run"] == {
        "workflows": [_load(BRANCH_CI)["name"]], "types": ["completed"], "branches": ["dev"]}
    cron = triggers["schedule"][0]["cron"]
    assert cron.split()[0] != "0" and cron == "17 * * * *"
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"force"}
    for job in ("cut", "dev-pair"):
        cond = " ".join(doc["jobs"][job]["if"].split())
        assert "github.event_name != 'workflow_run'" in cond
        assert "github.event.workflow_run.conclusion == 'success'" in cond
        assert "github.event.workflow_run.event == 'push'" in cond
        assert "github.event.workflow_run.head_branch == 'dev'" in cond
    assert doc["concurrency"] == {"group": "nightly-publish-cut", "cancel-in-progress": False}


def test_branch_ci_dispatch_inputs_unchanged_and_both_workflows_parse():
    assert set(_on(_load(BRANCH_CI))["workflow_dispatch"]["inputs"]) == {
        "expected_head_sha", "promote_pr_number"}
    for path in (BRANCH_CI, PUBLISH):
        assert isinstance(_load(path)["jobs"], dict)


def test_triggered_run_for_already_served_commit_dispatches_nothing():
    backend, _, _, _, run = setup_pair(B, F)  # hosted already serves the green tip
    assert run(apply=True)["state"] != "dispatched"
    assert not backend.dispatches


def test_triggered_run_for_superseded_green_commit_never_deploys_it():
    # A run triggered by A1 finishes while a newer commit B is also green: only B is deployed.
    backend, _, _, _, run = setup_pair(OLD, F)
    backend.extra_ci = [(0, A1, "success")]
    backend.comparisons[f"{A1}...{B}"] = ahead()
    backend.comparisons[f"{OLD}...{B}"] = ahead()
    result = run(apply=True)
    assert result["state"] == "dispatched"
    assert [d[1]["ref"] for d in backend.dispatches] == [B]


def test_triggered_run_for_served_commit_while_tip_not_green_dispatches_nothing():
    backend, _, _, _, run = setup_pair(A1, F)
    backend.ci = "cancelled"
    backend.extra_ci = [(0, A1, "success")]
    backend.comparisons[f"{A1}...{B}"] = ahead()
    assert run(apply=True)["state"] == "waiting_for_ci"
    assert not backend.dispatches


def test_publish_cut_reads_quiet_windows_from_one_repo_variable_and_cut_job_is_not_gated():
    doc = _load(PUBLISH)
    assert set(_on(doc)["workflow_dispatch"]["inputs"]) == {"force"}  # no new dispatch input
    step = next(s for s in doc["jobs"]["dev-pair"]["steps"] if s.get("name", "").startswith("Check both"))
    assert step["env"]["DEV_DEPLOY_QUIET_WINDOWS"] == "${{ vars.DEV_DEPLOY_QUIET_WINDOWS }}"
    assert "DEV_DEPLOY_QUIET" not in PUBLISH.read_text(encoding="utf-8").split("  dev-pair:")[0]
    assert "needs" not in doc["jobs"]["cut"] and "needs" not in doc["jobs"]["dev-pair"]
