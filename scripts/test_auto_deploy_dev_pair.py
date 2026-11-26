from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from scripts.auto_deploy_dev_pair import (
    BACKEND,
    CLOSEOUT_REPORT_PATHS,
    FRONTEND,
    ControllerError,
    drill_title,
    pair_title,
    reconcile,
)

B, F, OLD = "b" * 40, "f" * 40, "0" * 40


class Client:
    def __init__(self, repo, sha):
        self.repository, self.sha = repo, sha
        self.ci = "success"
        self.extra_ci = []  # (run_id, sha, conclusion) for non-tip commits
        self.active = False
        self.deploys = []
        self.failed_steps = {}
        self.comparisons = {}
        self.dispatches = []
        self.move_on_read = False
        self.reads = 0
        self.dispatch_head = None  # dev tip at dispatch time, as GitHub reports it

    def get_ref(self, ref):
        self.reads += 1
        return "c" * 40 if self.move_on_read and self.reads > 1 else self.sha

    def request(self, method, path):
        query = parse_qs(urlparse(path).query)
        if "/compare/" in path:
            comparison = self.comparisons.get(path.rsplit("/", 1)[-1], {})
            if isinstance(comparison, Exception):
                raise comparison
            return comparison
        if "/jobs" in path:
            run_id = int(urlparse(path).path.split("/")[-2])
            step = self.failed_steps.get(run_id, "Deploy dev VM stack under lease")
            return {"jobs": [{"name": "Deploy dev under shared environment lease",
                "steps": [{"name": step, "conclusion": "failure"}]}]}
        if "status" in query:
            return {"workflow_runs": [{"html_url": "https://github.test/active"}] if self.active else []}
        if "branch-ci.yml" in path:
            ci_runs = [{"id": 1, "head_sha": self.sha, "head_branch": "dev",
                "event": "push", "status": "completed", "conclusion": self.ci}]
            ci_runs += [{"id": rid, "head_sha": sha, "head_branch": "dev", "event": "push",
                "status": "completed", "conclusion": conclusion} for rid, sha, conclusion in self.extra_ci]
            return {"workflow_runs": [item for item in ci_runs
                if query.get("head_sha", [item["head_sha"]])[0] == item["head_sha"]]}
        dispatched = [{"id": 99, "head_sha": self.dispatch_head or inputs["ref"], "status": "queued",
            "display_title": f"Dev release {inputs['ref']} + {inputs['frontend_sha']}",
            "html_url": "https://github.test/99"} for _, inputs, _ in self.dispatches[:1]]
        history = self.deploys + dispatched
        return {"workflow_runs": [run for run in history
            if query.get("head_sha", [run["head_sha"]])[0] == run["head_sha"]]}

    def dispatch(self, workflow, inputs, ref):
        self.dispatches.append((workflow, inputs, ref))


def deploy_run(run_id, conclusion, backend=B, frontend=F):
    return {"id": run_id, "head_sha": backend, "status": "completed", "conclusion": conclusion,
            "display_title": f"Dev release {backend} + {frontend}",
            "html_url": f"https://github.test/{run_id}"}


def setup_pair(host_b=OLD, host_f=OLD):
    backend, frontend = Client(BACKEND, B), Client(FRONTEND, F)
    manifest = {"app": "execute-plans", "repository": FRONTEND, "sourceBranch": "dev",
        "deploymentState": "accepted", "profile": "operator-live", "frontendSha": host_f,
        "bffCommit": host_b, "releaseAdmission": {
            "backend": {"commitSha": host_b}, "frontend": {"commitSha": host_f}}}
    manifest["buildMode"] = {"VITE_BFF_MODE": "live", "VITE_BFF_FALLBACK": "strict",
        "VITE_BFF_REAL_WRITES": "true", "VITE_BFF_ALLOW_DEV_STUB_WRITES": "false",
        "VITE_BFF_EMBEDDED_BEARER_TOKEN": "false"}
    version = {"source_commit_sha": host_b, "config_posture": {"auth_mode": "strict", "auth_stub": False}}
    def fetch(url):
        return deepcopy(manifest if url.endswith("deployment.json") else version)
    def run(**kwargs):
        return reconcile(backend, frontend, fe_url="https://fe.test", bff_url="https://bff.test",
                         fetch=fetch, sleep=lambda _: None, **kwargs)
    return backend, frontend, manifest, version, run


@pytest.mark.parametrize("host_b,host_f", [(OLD, F), (B, OLD), (OLD, OLD)])
def test_backend_frontend_and_combined_changes_dispatch_exact_pair(host_b, host_f):
    backend, _, _, _, run = setup_pair(host_b, host_f)
    result = run(apply=True)
    assert result["state"] == "dispatched"
    workflow, inputs, ref = backend.dispatches[0]
    assert workflow == "nonprod-deploy.yml" and ref == "dev"
    assert inputs["ref"] == B and inputs["frontend_sha"] == F
    assert inputs["frontend_profile"] == "operator-live"
    assert inputs["component"] == "root" and inputs["allow_dirty"] == "false"
    # Automatic dev releases are deploy-only: the full input set is pinned and
    # no functional-gate / PAPER_BOOTSTRAP_REQUESTED input may be sent.
    assert inputs == {
        "environment": "dev", "component": "root", "ref": B, "frontend_sha": F,
        "frontend_ref": "dev", "frontend_profile": "operator-live",
        "dev_auth_profile": "strict", "allow_dirty": "false",
    }


def test_accepted_pair_is_noop_but_live_bff_drift_is_not():
    backend, _, _, version, run = setup_pair(B, F)
    assert run(apply=True)["state"] == "up_to_date"
    assert not backend.dispatches
    version["source_commit_sha"] = OLD
    assert run(apply=True)["state"] == "dispatched"


@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("conclusion", [None, "failure", "cancelled", "skipped"])
def test_both_exact_commits_need_successful_ci(which, conclusion):
    setup = setup_pair()
    setup[which].ci = conclusion
    assert setup[-1](apply=True)["state"] == "waiting_for_ci"
    assert not setup[0].dispatches


@pytest.mark.parametrize("which", [0, 1])
def test_running_backend_or_frontend_is_not_cancelled_or_redispatched(which):
    setup = setup_pair()
    setup[which].active = True
    assert setup[-1](apply=True)["state"] == "deployment_in_progress"
    assert not setup[0].dispatches


def test_inspection_does_not_consume_dispatch_or_require_new_publish_cut():
    backend, _, _, _, run = setup_pair()
    # No remembered publish watermark: both runs independently inspect hosting.
    assert run()["state"] == "ready"
    assert run(apply=True)["state"] == "dispatched"
    assert len(backend.dispatches) == 1


@pytest.mark.parametrize("conclusion", ["failure", "timed_out"])
def test_failed_pair_is_reported_and_not_redispatched(conclusion):
    backend, _, _, _, run = setup_pair()
    backend.deploys = [deploy_run(10, conclusion)]
    for apply in (False, True):
        result = run(apply=apply)
        assert result["state"] == "failed_pair_not_retried"
        assert result["run_url"] == "https://github.test/10"
    assert not backend.dispatches


def test_dispatch_is_correlated_by_title_when_dev_advanced_past_selected_backend():
    backend, _, _, _, run = setup_pair()
    backend.dispatch_head = "d" * 40  # workflow_dispatch head_sha is the dev tip, not the selected backend
    result = run(apply=True)
    assert result["state"] == "dispatched" and result["run_url"] == "https://github.test/99"


def test_failed_pair_whose_run_head_is_a_later_dev_tip_is_not_redispatched():
    backend, _, _, _, run = setup_pair()
    failed = deploy_run(10, "failure")
    failed["head_sha"] = "d" * 40
    backend.deploys = [failed]
    for apply in (False, True):
        result = run(apply=apply)
        assert result["state"] == "failed_pair_not_retried"
        assert result["run_url"] == "https://github.test/10"
    assert not backend.dispatches


def test_correlation_refuses_preexisting_or_other_pair_runs():
    backend, _, _, _, run = setup_pair()
    backend.dispatch_head = "d" * 40
    backend.deploys = [{**deploy_run(10, "success", OLD, F), "status": "queued"}]
    other = backend.request
    backend.request = lambda m, path: ({"workflow_runs": backend.deploys}
        if "nonprod-deploy" in path and "status" not in path else other(m, path))
    backend.dispatch = lambda *a, **k: backend.dispatches.append(a)
    with pytest.raises(ControllerError, match="could not be correlated"):
        run(apply=True)


def test_attempt_that_never_acquired_the_dev_lease_does_not_fail_the_pair():
    backend, _, _, _, run = setup_pair()
    backend.deploys = [deploy_run(10, "failure")]
    backend.failed_steps[10] = "Acquire shared dev environment lease"
    assert run(apply=True)["state"] == "dispatched"
    backend.failed_steps[10] = "Start identity-bound lease heartbeat"
    backend.dispatches.clear()
    assert run(apply=True)["state"] == "failed_pair_not_retried"


def test_market_session_refresh_failure_is_post_switch_and_not_retried():
    # The refresh now runs after the candidate stack deploy, so a failure is compensated
    # like other post-switch failures and the same pair is not retried automatically.
    from scripts.auto_deploy_dev_pair import MARKET_REFRESH_STEP, LEASE_STEP, PRE_MUTATION_STEPS
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/nonprod-deploy.yml").read_text(encoding="utf-8")
    assert f"- name: {MARKET_REFRESH_STEP}\n" in workflow and MARKET_REFRESH_STEP != LEASE_STEP
    assert MARKET_REFRESH_STEP not in PRE_MUTATION_STEPS
    backend, _, _, _, run = setup_pair()
    backend.deploys = [deploy_run(10, "failure")]
    for step in (MARKET_REFRESH_STEP, "Capture actual prior artifacts under pinned dev lease",
                 "Deploy dev VM stack under lease", "Ensure governed dev paper baseline under lease"):
        backend.failed_steps[10] = step
        backend.dispatches.clear()
        for apply in (False, True):
            assert run(apply=apply)["state"] == "failed_pair_not_retried", step
        assert not backend.dispatches


@pytest.mark.parametrize("failed_backend,failed_frontend", [(OLD, F), (B, OLD)])
def test_new_merge_on_either_dev_tip_after_failure_dispatches(failed_backend, failed_frontend):
    backend, _, _, _, run = setup_pair()
    backend.deploys = [deploy_run(10, "failure", failed_backend, failed_frontend)]
    assert run(apply=True)["state"] == "dispatched"
    assert backend.dispatches[0][1]["ref"] == B and backend.dispatches[0][1]["frontend_sha"] == F


@pytest.mark.parametrize("history,expected", [
    (["failure", "cancelled"], "dispatched"),
    (["cancelled"], "dispatched"),
    (["failure", "success"], "dispatched"),
    (["success", "failure"], "failed_pair_not_retried"),
])
def test_only_latest_completed_attempt_of_the_pair_decides(history, expected):
    backend, _, _, _, run = setup_pair()
    backend.deploys = [deploy_run(10 + index, conclusion) for index, conclusion in enumerate(history)]
    assert run(apply=True)["state"] == expected


def test_pair_title_matches_nonprod_deploy_run_name():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/nonprod-deploy.yml").read_text()
    assert "format('Dev release {0} + {1}', inputs.ref || github.sha, inputs.frontend_sha)" in workflow
    assert pair_title(B, F) == f"Dev release {B} + {F}"


def test_new_merge_during_inspection_defers_without_dispatch():
    backend, _, _, _, run = setup_pair()
    backend.move_on_read = True
    assert run(apply=True)["state"] == "superseded_before_dispatch"
    assert not backend.dispatches


def test_unaccepted_or_incomplete_baseline_does_not_trigger_blind_deploy():
    backend, _, manifest, _, run = setup_pair()
    manifest["deploymentState"] = "candidate"
    with pytest.raises(ControllerError, match="baseline"):
        run(apply=True)
    assert not backend.dispatches


def test_temporary_proof_profile_is_never_automatically_propagated():
    backend, _, manifest, _, run = setup_pair()
    manifest["profile"] = "write-proof"
    assert run(apply=True)["state"] == "waiting_for_persistent_profile"
    assert not backend.dispatches


def test_read_only_profile_is_preserved():
    backend, _, manifest, _, run = setup_pair()
    manifest["profile"] = "read-only"
    manifest["buildMode"]["VITE_BFF_REAL_WRITES"] = "false"
    run(apply=True)
    assert backend.dispatches[0][1]["frontend_profile"] == "read-only"


def test_conflicting_profile_flags_cannot_enable_writes():
    backend, _, manifest, _, run = setup_pair()
    manifest["buildMode"]["VITE_BFF_REAL_WRITES"] = "false"
    with pytest.raises(ControllerError, match="posture"):
        run(apply=True)
    assert not backend.dispatches


def report_compare(files=None, **overrides):
    if files is None:
        files = [{"filename": path, "status": "modified"} for path in sorted(CLOSEOUT_REPORT_PATHS)]
    payload = {
        "status": "ahead",
        "ahead_by": 1,
        "behind_by": 0,
        "total_commits": 1,
        "commits": [{"sha": B}],
        "files": files,
    }
    payload.update(overrides)
    return payload


def test_three_closeout_reports_only_with_unchanged_fe_and_coherent_served_be_never_dispatches():
    backend, _, manifest, _, run = setup_pair(OLD, F)
    backend.comparisons[f"{OLD}...{B}"] = report_compare()
    for apply in (False, True):
        result = run(apply=apply)
        assert result["state"] == "report_only_no_deploy"
        assert result["state"] != "up_to_date"
        assert result["backend_sha"] == B
        assert result["served_backend_sha"] == OLD
        assert result["frontend_sha"] == F
        assert result["frontend_profile"] == "operator-live"
    manifest["profile"] = "read-only"
    manifest["buildMode"]["VITE_BFF_REAL_WRITES"] = "false"
    readonly = run(apply=True)
    assert readonly["state"] == "report_only_no_deploy"
    assert readonly["frontend_profile"] == "read-only"
    assert not backend.dispatches


@pytest.mark.parametrize("extra_path", [
    "scripts/auto_deploy_dev_pair.py",
    "tests/integration/l12/test_stimulus_cross_loop_deployed_e2e.py",
    "docs/deployment/evidence/HOSTED-ALL12-E-BASELINE-20261025/evidence.json",
])
def test_closeout_reports_plus_other_backend_path_still_dispatches(extra_path):
    backend, _, _, _, run = setup_pair(OLD, F)
    files = [{"filename": path, "status": "modified"} for path in sorted(CLOSEOUT_REPORT_PATHS)]
    files.append({"filename": extra_path, "status": "modified"})
    backend.comparisons[f"{OLD}...{B}"] = report_compare(files=files)
    assert run(apply=True)["state"] == "dispatched"
    assert len(backend.dispatches) == 1


def test_changed_frontend_still_dispatches_even_when_backend_is_closeout_reports_only():
    backend, _, _, _, run = setup_pair(OLD, OLD)
    backend.comparisons[f"{OLD}...{B}"] = report_compare()
    assert run(apply=True)["state"] == "dispatched"
    assert len(backend.dispatches) == 1


@pytest.mark.parametrize("comparison", [
    ControllerError("compare unavailable"),
    {},
    report_compare(status="diverged"),
    report_compare(behind_by=1),
    report_compare(files=[]),
    report_compare(truncated=True),
    report_compare(ahead_by=2, total_commits=2, commits=[{"sha": B}]),
    report_compare(total_files=4),
    report_compare(files=[{
        "filename": "docs/deployment/latest-dev-six-area-closure.md",
        "status": "modified",
        "truncated": True,
    }]),
    report_compare(files=[{
        "filename": "docs/deployment/latest-dev-six-area-closure.md",
        "status": "renamed",
        "previous_filename": "scripts/auto_deploy_dev_pair.py",
    }]),
    report_compare(files=[{
        "filename": "scripts/auto_deploy_dev_pair.py",
        "status": "renamed",
        "previous_filename": "docs/deployment/latest-dev-six-area-closure.md",
    }]),
    report_compare(files=[{
        "filename": "docs/deployment/latest-dev-six-area-closure.md",
        "status": "renamed",
    }]),
    report_compare(files=[{
        "filename": "scripts/auto_deploy_dev_pair.py",
        "status": "removed",
    }]),
])
def test_unknown_truncated_or_rename_outside_report_paths_cannot_suppress_real_change(comparison):
    backend, _, _, _, run = setup_pair(OLD, F)
    backend.comparisons[f"{OLD}...{B}"] = comparison
    assert run(apply=True)["state"] == "dispatched"
    assert len(backend.dispatches) == 1


@pytest.mark.parametrize("drift", ["bff_version", "backend_admission", "frontend_admission"])
def test_inconsistent_served_identity_cannot_masquerade_as_report_only(drift):
    other = "1" * 40
    backend, _, manifest, version, run = setup_pair(OLD, F)
    backend.comparisons[f"{OLD}...{B}"] = report_compare()
    backend.comparisons[f"{other}...{B}"] = report_compare()
    if drift == "bff_version":
        version["source_commit_sha"] = other
    elif drift == "backend_admission":
        manifest["releaseAdmission"]["backend"]["commitSha"] = other
    else:
        manifest["releaseAdmission"]["frontend"]["commitSha"] = OLD
    assert run(apply=True)["state"] == "dispatched"
    assert len(backend.dispatches) == 1


def drill_run(run_id, conclusion, backend=B, frontend=F):
    return {**deploy_run(run_id, conclusion, backend, frontend),
            "display_title": drill_title(backend, frontend)}


def test_drill_run_name_is_distinct_and_matches_workflow():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/nonprod-deploy.yml").read_text()
    assert "format('Dev compensation drill {0} + {1}', inputs.ref || github.sha, inputs.frontend_sha)" in workflow
    assert drill_title(B, F) == f"Dev compensation drill {B} + {F}"
    assert drill_title(B, F) != pair_title(B, F)


@pytest.mark.parametrize("conclusion", ["failure", "timed_out"])
def test_failed_drill_never_counts_as_a_failed_attempt_of_the_pair(conclusion):
    backend, _, _, _, run = setup_pair()
    backend.deploys = [drill_run(10, conclusion)]
    assert run(apply=False)["state"] != "failed_pair_not_retried"
    assert run(apply=True)["state"] == "dispatched"


def test_failed_drill_does_not_mask_or_clear_a_failed_normal_run():
    backend, _, _, _, run = setup_pair()
    backend.deploys = [deploy_run(10, "failure"), drill_run(11, "failure")]
    result = run(apply=True)
    assert result["state"] == "failed_pair_not_retried"
    assert result["run_url"] == "https://github.test/10"
    backend.deploys = [drill_run(10, "failure"), deploy_run(11, "failure")]
    assert run(apply=True)["state"] == "failed_pair_not_retried"


A1 = "a" * 40  # aeb227ff3 in the 2026-11-17 replay: green, on dev, not the tip


def ahead(**overrides):
    return {"status": "ahead", "ahead_by": 1, "behind_by": 0, **overrides}


def test_selects_newest_green_ancestor_when_tip_ci_is_cancelled_replay_20261117():
    # 00:10Z-00:55Z: six consecutive tip runs were cancelled while aeb227ff3 was green.
    backend, _, _, _, run = setup_pair(OLD, F)
    backend.ci = "cancelled"
    backend.extra_ci = [(0, A1, "success"), (2, "d" * 40, "cancelled"), (3, "e" * 40, "cancelled")]
    backend.comparisons[f"{A1}...{B}"] = ahead()
    backend.comparisons[f"{OLD}...{A1}"] = ahead()
    result = run(apply=True)
    assert result["state"] == "dispatched"
    assert backend.dispatches[0][1]["ref"] == A1 and backend.dispatches[0][1]["frontend_sha"] == F
    assert result["tip_backend_sha"] == B


def test_green_commit_off_dev_or_not_ahead_of_served_is_never_selected():
    backend, _, _, _, run = setup_pair(OLD, F)
    backend.ci = "cancelled"
    backend.extra_ci = [(5, A1, "success")]
    backend.comparisons[f"{A1}...{B}"] = {"status": "diverged"}  # not on dev
    assert run(apply=True)["state"] == "waiting_for_ci"
    backend.comparisons[f"{A1}...{B}"] = ahead()
    backend.comparisons[f"{OLD}...{A1}"] = {"status": "behind"}  # older than served
    assert run(apply=True)["state"] == "waiting_for_ci"
    assert not backend.dispatches


def test_served_green_commit_is_up_to_date_while_tip_waits_not_redeployed():
    backend, _, _, _, run = setup_pair(A1, F)
    backend.ci = "cancelled"
    backend.extra_ci = [(0, A1, "success")]
    backend.comparisons[f"{A1}...{B}"] = ahead()
    assert run(apply=True)["state"] == "waiting_for_ci"
    assert not backend.dispatches


def test_failed_pair_is_evaluated_for_the_selected_candidate_not_the_tip():
    backend, _, _, _, run = setup_pair(OLD, F)
    backend.ci = "cancelled"
    backend.extra_ci = [(0, A1, "success")]
    backend.comparisons[f"{A1}...{B}"] = ahead()
    backend.comparisons[f"{OLD}...{A1}"] = ahead()
    backend.deploys = [deploy_run(10, "failure", B, F)]  # tip pair failed earlier: irrelevant
    assert run(apply=True)["state"] == "dispatched"
    backend.deploys = [deploy_run(11, "failure", A1, F)]
    backend.dispatches.clear()
    assert run(apply=True)["state"] == "failed_pair_not_retried"


def test_tip_moving_past_an_unchanged_candidate_does_not_supersede():
    backend, _, _, _, run = setup_pair(OLD, F)
    backend.ci = "cancelled"
    backend.extra_ci = [(0, A1, "success")]
    backend.comparisons[f"{A1}...{B}"] = ahead()
    backend.comparisons[f"{OLD}...{A1}"] = ahead()
    backend.comparisons[f"{A1}...{'c' * 40}"] = ahead()
    backend.move_on_read = True
    assert run(apply=True)["state"] == "dispatched"


def test_workflow_accepts_older_green_payload_on_dev_and_names_script_source():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/nonprod-deploy.yml").read_text()
    assert 'git merge-base --is-ancestor "${sha}" "${trusted_ref}"' in workflow
    assert 'git merge-base --is-ancestor "${requested}" "${ref_tip}"' in workflow
    assert "has no successful dev push Branch CI run" in workflow
    assert '"${sha}" != "${trusted_ref}"' not in workflow.split("Compensation drill is a dev root only drill")[1].split("docker compose")[0]
    # Older payload: deploy scripts run from .target (the payload), workflow from dev tip.
    assert "Deploy scripts under .target/ come from that payload" in workflow


def test_summary_reports_deployed_and_acceptance_separately():
    from scripts.auto_deploy_dev_pair import summary_lines
    text = summary_lines({"state": "dispatched", "run_url": "u"})
    assert "- deployed: pending" in text and "- acceptance: pending" in text
    assert summary_lines({"state": "up_to_date"}) == ""


from scripts.auto_deploy_dev_pair import is_non_build_input, summary_lines


@pytest.mark.parametrize("path", [
    "docs/operations/problem-register.md",
    "docs/reviews/2026-05-12-x.md",
    "README.md",
    ".orchestrator/skills/a.md",
    "ai-task-archive/2026/x.json",
    "docs/deployment/latest-dev-six-area-closure.md",
])
def test_non_build_input_paths(path):
    assert is_non_build_input(path)


@pytest.mark.parametrize("path", [
    "docs/contracts/research_demand.v1.schema.json",  # read by services at runtime
    "docs/deployment/loop-catalog.registry.json",
    "docs/deployment/evidence/other/evidence.json",
    "docs/04/x.md",
    "docs/operations/x.json",
    "services/research/README.md",  # services read *.md
    ".orchestrator.py", "ai-task-archive", "scripts/x.py", "", "../README.md", "docs/operations/../../x.py",
])
def test_build_input_or_unproven_paths_are_not_skipped(path):
    assert not is_non_build_input(path)


def test_documentation_and_tooling_commit_is_not_released_and_is_reported():
    backend, _, _, _, run = setup_pair(OLD, F)
    files = [{"filename": "docs/operations/problem-register.md", "status": "modified"},
             {"filename": ".orchestrator/skills/x.md", "status": "added"}]
    backend.comparisons[f"{OLD}...{B}"] = report_compare(files=files)
    result = run(apply=True)
    assert result["state"] == "report_only_no_deploy"
    assert result["changed_file_count"] == 2 and result["served_backend_sha"] == OLD
    assert not backend.dispatches
    text = summary_lines(result)
    assert "skipped" in text and OLD in text and "2 file" in text


def test_later_build_input_candidate_releases_and_carries_skipped_commits():
    backend, _, _, _, run = setup_pair(OLD, F)
    backend.comparisons[f"{OLD}...{B}"] = report_compare(
        files=[{"filename": "docs/operations/a.md", "status": "modified"}])
    assert run(apply=True)["state"] == "report_only_no_deploy"
    assert not backend.dispatches
    # the next candidate still compares against the served commit (OLD), so it covers the skipped one
    backend.comparisons[f"{OLD}...{B}"] = report_compare(
        ahead_by=2, total_commits=2, commits=[{"sha": "1"}, {"sha": B}],
        files=[{"filename": "docs/operations/a.md", "status": "modified"},
               {"filename": "services/x/main.py", "status": "modified"}])
    assert run(apply=True)["state"] == "dispatched"
    assert backend.dispatches[0][1]["ref"] == B


def test_over_300_files_and_boundary_rename_fail_open():
    backend, _, _, _, run = setup_pair(OLD, F)
    many = [{"filename": f"docs/operations/{i}.md", "status": "modified"} for i in range(300)]
    backend.comparisons[f"{OLD}...{B}"] = report_compare(files=many)
    assert run(apply=True)["state"] == "dispatched"
    backend.dispatches.clear()
    backend.comparisons[f"{OLD}...{B}"] = report_compare(files=[{
        "filename": "docs/operations/a.md", "status": "renamed", "previous_filename": "docs/contracts/a.md"}])
    assert run(apply=True)["state"] == "dispatched"


# ---- quiet windows (DEV-DEPLOY-QUIET-WINDOWS-20261118) ----
from datetime import datetime, timezone

from scripts.auto_deploy_dev_pair import (
    DEFAULT_QUIET_WINDOWS, load_quiet_windows, parse_quiet_windows, quiet_window_hold)


def utc(day, hh, mm):
    return datetime(2026, 11, day, hh, mm, tzinfo=timezone.utc)  # 11/16 is a Monday, 11/21 Saturday


DEFAULTS = parse_quiet_windows(DEFAULT_QUIET_WINDOWS)


def test_dispatch_inside_window_is_held_and_names_window_and_end():
    backend, _, _, _, run = setup_pair(OLD, F)
    result = run(apply=True, quiet_windows=DEFAULTS, now=utc(18, 3, 0))  # Wednesday
    assert result["state"] == "held_quiet_window" and result["reason"] == "dispatch_inside_window"
    assert result["window_ends_at"] == "2026-11-18T05:30Z" and "00:30-05:30" in result["window"]
    assert not backend.dispatches


def test_outside_windows_and_no_windows_dispatch_as_before():
    for kwargs in ({"quiet_windows": DEFAULTS, "now": utc(18, 6, 0)}, {}):
        backend, _, _, _, run = setup_pair(OLD, F)
        assert run(apply=True, **kwargs)["state"] == "dispatched"
        assert len(backend.dispatches) == 1


def test_dispatch_just_before_window_is_held_by_backend_switch_lead():
    hold = quiet_window_hold(DEFAULTS, utc(18, 0, 13))  # switch at 00:30
    assert hold["reason"] == "backend_switch_inside_window" and hold["backend_switch_lead_minutes"] == 17
    assert quiet_window_hold(DEFAULTS, utc(18, 0, 12)) is None
    # persona window: 08:48 + 17 = 09:05
    assert quiet_window_hold(DEFAULTS, utc(18, 8, 48))["window"].endswith("09:05-10:45")
    assert quiet_window_hold(DEFAULTS, utc(18, 8, 47)) is None
    # leaving a window: switch must also be outside, so allowed from the window end
    assert quiet_window_hold(DEFAULTS, utc(18, 5, 30)) is None


def test_weekend_trading_window_does_not_apply_but_persona_window_is_daily():
    assert quiet_window_hold(DEFAULTS, utc(21, 3, 0)) is None  # Saturday
    assert quiet_window_hold(DEFAULTS, utc(21, 9, 30)) is not None


def test_dispatch_resumes_after_window_with_newest_green_and_no_backlog():
    backend, _, _, _, run = setup_pair(OLD, F)
    backend.extra_ci = [(0, A1, "success")]  # greens arrived during the window
    backend.comparisons[f"{A1}...{B}"] = ahead()
    backend.comparisons[f"{OLD}...{B}"] = ahead()
    for minute in (0, 1, 2):
        assert run(apply=True, quiet_windows=DEFAULTS, now=utc(18, 3, minute))["state"] == "held_quiet_window"
    assert not backend.dispatches
    assert run(apply=True, quiet_windows=DEFAULTS, now=utc(18, 5, 30))["state"] == "dispatched"
    assert [d[1]["ref"] for d in backend.dispatches] == [B]


def test_running_release_is_not_cancelled_when_window_starts():
    backend, _, _, _, run = setup_pair(OLD, F)
    backend.active = True  # release started before the window
    result = run(apply=True, quiet_windows=DEFAULTS, now=utc(18, 3, 0))
    assert result["state"] == "deployment_in_progress"
    assert not backend.dispatches and not hasattr(backend, "cancelled")


def test_malformed_variable_fails_closed_to_defaults_and_says_so():
    for bad in ("garbage", "mon-fri 25:00-26:00", "mon-fri 05:00-04:00", "xyz 01:00-02:00", ";"):
        windows, source = load_quiet_windows(bad)
        assert windows == DEFAULTS and source.startswith("malformed_defaults_used")
    assert load_quiet_windows(None) == (DEFAULTS, "default")
    assert load_quiet_windows("  ") == (DEFAULTS, "default")
    windows, source = load_quiet_windows("sat,sun 01:00-02:00")
    assert source == "variable" and quiet_window_hold(windows, utc(21, 1, 30)) is not None


def test_summary_shows_held_state():
    text = summary_lines(quiet_window_hold(DEFAULTS, utc(18, 3, 0)) | {"state": "held_quiet_window"})
    assert "held: quiet window" in text and "05:30" in text and "not a broken trigger" in text
