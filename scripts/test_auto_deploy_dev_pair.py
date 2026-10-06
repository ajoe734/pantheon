from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from scripts.auto_deploy_dev_pair import BACKEND, FRONTEND, ControllerError, pair_title, reconcile

B, F, OLD = "b" * 40, "f" * 40, "0" * 40


class Client:
    def __init__(self, repo, sha):
        self.repository, self.sha = repo, sha
        self.ci = "success"
        self.active = False
        self.deploys = []
        self.dispatches = []
        self.move_on_read = False
        self.reads = 0

    def get_ref(self, ref):
        self.reads += 1
        return "c" * 40 if self.move_on_read and self.reads > 1 else self.sha

    def request(self, method, path):
        query = parse_qs(urlparse(path).query)
        if "status" in query:
            return {"workflow_runs": [{"html_url": "https://github.test/active"}] if self.active else []}
        if "branch-ci.yml" in path:
            return {"workflow_runs": [{"id": 1, "head_sha": self.sha, "head_branch": "dev",
                "event": "push", "status": "completed", "conclusion": self.ci}]}
        dispatched = [{"id": 99, "head_sha": self.sha, "status": "queued",
            "display_title": f"Dev release {B} + {F}", "html_url": "https://github.test/99"}]
        history = self.deploys + (dispatched if self.dispatches else [])
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
    assert inputs["run_loop_prod_tel_002_probe"] == "true"


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
