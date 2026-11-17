#!/usr/bin/env python3
"""Reconcile the two protected dev tips through the existing release workflow."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

if __package__:
    from .cross_repo_release_controller import ControllerError, GitHubClient, fetch_url_json
else:
    from cross_repo_release_controller import ControllerError, GitHubClient, fetch_url_json

BACKEND = "ajoe734/pantheon"
FRONTEND = "ajoe734/execute-plans"
DEPLOY = "nonprod-deploy.yml"
ACTIVE = ("in_progress", "queued", "waiting", "pending", "requested")
FAILED = ("failure", "timed_out")
LEASE_STEP = "Acquire shared dev environment lease"
# Runs after the candidate stack is up (post-switch), so its failure is compensated like
# any other post-switch failure and is deliberately not a pre-mutation step.
MARKET_REFRESH_STEP = "Refresh Taiwan market session under lease after candidate stack deploy"
PRE_MUTATION_STEPS = frozenset({LEASE_STEP})
CLOSEOUT_REPORT_PATHS = frozenset({
    "docs/deployment/latest-dev-six-area-closure.md",
    "docs/deployment/evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/evidence.json",
    "docs/deployment/evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/source-delivery-ledger.json",
})


def closeout_reports_only(backend, base_sha, head_sha):
    if not base_sha or not head_sha or base_sha == head_sha:
        return False
    try:
        comparison = backend.request("GET", f"/repos/{backend.repository}/compare/{base_sha}...{head_sha}")
    except Exception:
        return False
    if not isinstance(comparison, dict) or comparison.get("truncated"):
        return False
    if comparison.get("status") != "ahead" or comparison.get("behind_by", 0) != 0:
        return False
    ahead_by = comparison.get("ahead_by", comparison.get("total_commits"))
    total_commits = comparison.get("total_commits", ahead_by)
    if (type(ahead_by) is not int or type(total_commits) is not int
            or ahead_by <= 0 or total_commits != ahead_by):
        return False
    commits = comparison.get("commits")
    if commits is not None and (not isinstance(commits, list) or len(commits) != total_commits):
        return False
    files = comparison.get("files")
    if not isinstance(files, list) or not (1 <= len(files) < 300):
        return False
    if comparison.get("total_files") not in (None, len(files)):
        return False
    for item in files:
        if not isinstance(item, dict) or item.get("truncated"):
            return False
        status = item.get("status")
        if status not in {"added", "modified", "removed", "renamed"}:
            return False
        filename = item.get("filename")
        previous = item.get("previous_filename")
        if filename not in CLOSEOUT_REPORT_PATHS:
            return False
        if status == "renamed" and not previous:
            return False
        if previous is not None and previous not in CLOSEOUT_REPORT_PATHS:
            return False
    return True


def pair_title(backend_sha, frontend_sha):
    # Mirrors the dev run-name in nonprod-deploy.yml; both SHAs identify the pair.
    return f"Dev release {backend_sha} + {frontend_sha}"


def drill_title(backend_sha, frontend_sha):
    # Mirrors the compensation drill run-name; it must never equal pair_title so a
    # failed drill is not counted as a failed attempt of the pair.
    return f"Dev compensation drill {backend_sha} + {frontend_sha}"


def runs(client, workflow, **filters):
    query = urlencode({"branch": "dev", "per_page": 100, **filters})
    return client.request("GET", f"/repos/{client.repository}/actions/workflows/{workflow}/runs?{query}")["workflow_runs"]


def busy(backend, frontend):
    for client, workflow in ((backend, DEPLOY), (frontend, "pantheon-dev-fe-deploy.yml")):
        for status in ACTIVE:
            active = runs(client, workflow, status=status)
            if active:
                return active[0]["html_url"]
    return None


def ci_passed(client, sha):
    candidates = runs(client, "branch-ci.yml", event="push", head_sha=sha)
    # Only the latest attempt for this exact protected-branch commit counts.
    if not candidates:
        return False
    run = max(candidates, key=lambda item: item["id"])
    return _green(run, sha)


def _green(run, sha=None):
    return (run.get("head_branch") == "dev" and run.get("event") == "push"
            and run.get("status") == "completed" and run.get("conclusion") == "success"
            and bool(run.get("head_sha")) and (sha is None or run.get("head_sha") == sha))


def _compare_status(client, base, head):
    try:
        payload = client.request("GET", f"/repos/{client.repository}/compare/{base}...{head}")
    except Exception:
        return None
    return payload.get("status") if isinstance(payload, dict) else None


def on_dev(client, sha, tip):
    """sha is the tip or an ancestor of it (never a commit that is not on dev)."""
    return sha == tip or _compare_status(client, sha, tip) in {"identical", "ahead"}


MAX_CANDIDATES = 30


def newest_green_candidate(client, tip, served):
    """Newest commit with a successful dev push Branch CI run that is on dev and ahead of served.

    Returns None when no such commit exists (including when served is already the newest).
    """
    green = [run for run in runs(client, "branch-ci.yml", event="push") if _green(run)]
    seen = set()
    for run in sorted(green, key=lambda item: item["id"], reverse=True)[:MAX_CANDIDATES]:
        sha = run["head_sha"]
        if sha in seen:
            continue
        seen.add(sha)
        if sha == served:
            return None  # everything older than the served commit is not ahead of it
        if not on_dev(client, sha, tip):
            continue
        if sha != tip and served and _compare_status(client, served, sha) != "ahead":
            continue
        return sha
    return None


def lost_lease_race(backend, run_id):
    # Without the lease, or when the market session cannot be proven before mutation,
    # no later step touches the VM, so the pair was never tried.
    jobs = backend.request("GET", f"/repos/{backend.repository}/actions/runs/{run_id}/jobs?per_page=100")["jobs"]
    return any(step.get("name") in PRE_MUTATION_STEPS and step.get("conclusion") == "failure"
               for job in jobs for step in job.get("steps") or [])


def failed_pair_run(backend, backend_sha, frontend_sha):
    attempts = [run for run in runs(backend, DEPLOY, event="workflow_dispatch", head_sha=backend_sha)
                if run.get("display_title") == pair_title(backend_sha, frontend_sha)
                and run.get("status") == "completed"]
    if not attempts:
        return None
    # Only the latest attempt counts: a cancelled, lease-starved or later
    # successful attempt leaves the pair eligible, and a new merge on either
    # dev tip is a new pair.
    latest = max(attempts, key=lambda item: item["id"])
    if latest.get("conclusion") not in FAILED or lost_lease_race(backend, latest["id"]):
        return None
    return latest["html_url"]


def inspect_pair(backend, frontend, *, fe_url, bff_url, fetch=fetch_url_json):
    active_url = busy(backend, frontend)
    if active_url:
        return {"state": "deployment_in_progress", "run_url": active_url}
    backend_sha, frontend_sha = backend.get_ref("dev"), frontend.get_ref("dev")
    result = {"backend_sha": backend_sha, "frontend_sha": frontend_sha}
    # backend_sha/frontend_sha are replaced by the selected candidates below.
    manifest = fetch(f"{fe_url.rstrip('/')}/deployment.json")
    version = fetch(f"{bff_url.rstrip('/')}/bff/version")
    if (manifest.get("app") != "execute-plans"
            or manifest.get("repository") != FRONTEND
            or manifest.get("sourceBranch") != "dev"
            or manifest.get("deploymentState") not in {"accepted", "functional-accepted"}
            or not manifest.get("releaseAdmission")):
        raise ControllerError("hosted baseline is not an accepted exact pair; inspect/restore it before automatic deployment")
    profile = manifest.get("deploymentProfile") or manifest.get("profile")
    if profile not in {"read-only", "operator-live"}:
        return {**result, "state": "waiting_for_persistent_profile"}
    # Preserve the existing accepted dev profile, never enable a proof window.
    result["frontend_profile"] = profile
    mode = manifest.get("buildMode", {})
    posture = version.get("config_posture", {})
    if (mode.get("VITE_BFF_MODE") != "live" or mode.get("VITE_BFF_FALLBACK") != "strict"
            or mode.get("VITE_BFF_REAL_WRITES") != ("true" if profile == "operator-live" else "false")
            or mode.get("VITE_BFF_ALLOW_DEV_STUB_WRITES") != "false"
            or mode.get("VITE_BFF_EMBEDDED_BEARER_TOKEN") != "false"
            or posture.get("auth_mode") != "strict" or posture.get("auth_stub") is not False):
        raise ControllerError("hosted profile/auth posture is inconsistent; automatic deployment cannot authorize new write modes")
    served_backend = manifest.get("bffCommit")
    served_frontend = manifest.get("frontendSha")
    coherent_served = (
        isinstance(served_backend, str) and bool(served_backend)
        and isinstance(served_frontend, str) and bool(served_frontend)
        and version.get("source_commit_sha") == served_backend
        and (manifest["releaseAdmission"].get("backend") or {}).get("commitSha") == served_backend
        and (manifest["releaseAdmission"].get("frontend") or {}).get("commitSha") == served_frontend
    )
    candidates = select_pair(backend, frontend, backend_sha, frontend_sha,
                             served_backend if coherent_served else None,
                             served_frontend if coherent_served else None)
    candidate_backend, candidate_frontend = candidates
    result["tip_backend_sha"], result["tip_frontend_sha"] = backend_sha, frontend_sha
    pending = [client.repository for client, cand in ((backend, candidate_backend), (frontend, candidate_frontend))
               if not cand]
    if pending:
        return {**result, "state": "waiting_for_ci", "repositories": pending}
    result["backend_sha"], result["frontend_sha"] = candidate_backend, candidate_frontend
    result["served_backend"] = served_backend if coherent_served else None
    result["served_frontend"] = served_frontend if coherent_served else None
    if coherent_served and served_frontend == candidate_frontend and served_backend == candidate_backend:
        if candidate_backend == backend_sha and candidate_frontend == frontend_sha:
            return {**result, "state": "up_to_date"}
        # Nothing green is newer than what is served; the tips are still being tested.
        return {**result, "state": "waiting_for_ci", "repositories": [
            client.repository for client, cand, tip in (
                (backend, candidate_backend, backend_sha), (frontend, candidate_frontend, frontend_sha))
            if cand != tip]}
    if (coherent_served and served_frontend == candidate_frontend
            and closeout_reports_only(backend, served_backend, candidate_backend)):
        return {
            **result,
            "served_backend_sha": served_backend,
            "state": "report_only_no_deploy",
        }
    failed_url = failed_pair_run(backend, candidate_backend, candidate_frontend)
    if failed_url:
        return {**result, "state": "failed_pair_not_retried", "run_url": failed_url}
    return {**result, "state": "ready"}


def select_pair(backend, frontend, backend_tip, frontend_tip, served_backend, served_frontend):
    """Per repository: the newest green commit ahead of served, else the served commit itself
    while it is still on dev (so the other repository can move alone), else None."""
    picked = []
    for client, tip, served in ((backend, backend_tip, served_backend), (frontend, frontend_tip, served_frontend)):
        cand = newest_green_candidate(client, tip, served)
        if cand is None and served and on_dev(client, served, tip) and served != tip:
            cand = served
        if cand is None and served == tip and served:
            cand = served
        picked.append(cand)
    return tuple(picked)


def reconcile(backend, frontend, *, fe_url, bff_url, apply=False, fetch=fetch_url_json, sleep=time.sleep):
    result = inspect_pair(backend, frontend, fe_url=fe_url, bff_url=bff_url, fetch=fetch)
    if result["state"] != "ready" or not apply:
        return result
    # A fresh read catches merges during CI/host checks. Admission in the child
    # checks again before any mutation; later merges belong to the next tick.
    # Re-select against the current tips: only a different (newer) selected pair, or a
    # candidate that is no longer on dev, supersedes; a tip moving past an unchanged
    # candidate does not.
    now_backend, now_frontend = backend.get_ref("dev"), frontend.get_ref("dev")
    reselected = select_pair(backend, frontend, now_backend, now_frontend,
                             result.get("served_backend"), result.get("served_frontend"))
    if reselected != (result["backend_sha"], result["frontend_sha"]):
        return {**result, "state": "superseded_before_dispatch"}
    active_url = busy(backend, frontend)
    if active_url:
        return {**result, "state": "deployment_in_progress", "run_url": active_url}
    previous_ids = {run["id"] for run in runs(backend, DEPLOY, event="workflow_dispatch")}
    backend.dispatch(DEPLOY, {
        "environment": "dev", "component": "root", "ref": result["backend_sha"],
        "frontend_sha": result["frontend_sha"], "frontend_ref": "dev",
        "frontend_profile": result["frontend_profile"], "dev_auth_profile": "strict",
        "allow_dirty": "false",
    }, ref="dev")
    expected_title = pair_title(result["backend_sha"], result["frontend_sha"])
    for _ in range(20):
        for run in runs(backend, DEPLOY, event="workflow_dispatch"):
            if (run["id"] not in previous_ids and run.get("head_sha") == result["backend_sha"]
                    and run.get("display_title") == expected_title):
                return {**result, "state": "dispatched", "run_url": run["html_url"]}
        sleep(3)
    raise ControllerError("dispatch sent but run could not be correlated; inspect Nonprod Deploy before retrying")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fe-url", required=True)
    parser.add_argument("--bff-url", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        parser.error("GH_TOKEN is required")
    clients = [GitHubClient(api_url="https://api.github.com", token=token, repository=repo)
               for repo in (BACKEND, FRONTEND)]
    try:
        result = reconcile(*clients, fe_url=args.fe_url, bff_url=args.bff_url, apply=args.apply)
    except Exception as exc:
        result = {"state": "inspection_or_dispatch_failed", "reason": str(exc)}
    print(json.dumps(result, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as handle:
            handle.write("\n## Automatic dev pair check\n\n```json\n" + json.dumps(result, indent=2) + "\n```\n")
            handle.write("A dispatched run is pending deployment and acceptance; follow its run_url.\n")
    return 1 if result["state"] == "inspection_or_dispatch_failed" else 0


if __name__ == "__main__":
    sys.exit(main())
