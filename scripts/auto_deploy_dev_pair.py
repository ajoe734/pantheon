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
    return (run.get("head_sha") == sha and run.get("head_branch") == "dev"
            and run.get("event") == "push" and run.get("status") == "completed"
            and run.get("conclusion") == "success")


def inspect_pair(backend, frontend, *, fe_url, bff_url, fetch=fetch_url_json):
    active_url = busy(backend, frontend)
    if active_url:
        return {"state": "deployment_in_progress", "run_url": active_url}
    backend_sha, frontend_sha = backend.get_ref("dev"), frontend.get_ref("dev")
    result = {"backend_sha": backend_sha, "frontend_sha": frontend_sha}
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
    if (manifest.get("frontendSha") == frontend_sha
            and manifest.get("bffCommit") == backend_sha
            and version.get("source_commit_sha") == backend_sha
            and manifest["releaseAdmission"].get("frontend", {}).get("commitSha") == frontend_sha
            and manifest["releaseAdmission"].get("backend", {}).get("commitSha") == backend_sha):
        return {**result, "state": "up_to_date"}
    pending = [client.repository for client, sha in ((backend, backend_sha), (frontend, frontend_sha))
               if not ci_passed(client, sha)]
    if pending:
        return {**result, "state": "waiting_for_ci", "repositories": pending}
    return {**result, "state": "ready"}


def reconcile(backend, frontend, *, fe_url, bff_url, apply=False, fetch=fetch_url_json, sleep=time.sleep):
    result = inspect_pair(backend, frontend, fe_url=fe_url, bff_url=bff_url, fetch=fetch)
    if result["state"] != "ready" or not apply:
        return result
    # A fresh read catches merges during CI/host checks. Admission in the child
    # checks again before any mutation; later merges belong to the next tick.
    if (backend.get_ref("dev") != result["backend_sha"]
            or frontend.get_ref("dev") != result["frontend_sha"]):
        return {**result, "state": "superseded_before_dispatch"}
    active_url = busy(backend, frontend)
    if active_url:
        return {**result, "state": "deployment_in_progress", "run_url": active_url}
    previous_ids = {run["id"] for run in runs(backend, DEPLOY, event="workflow_dispatch")}
    backend.dispatch(DEPLOY, {
        "environment": "dev", "component": "root", "ref": result["backend_sha"],
        "frontend_sha": result["frontend_sha"], "frontend_ref": "dev",
        "frontend_profile": result["frontend_profile"], "dev_auth_profile": "strict",
        "allow_dirty": "false", "run_loop_prod_tel_002_probe": "true",
    }, ref="dev")
    expected_title = f"Dev release {result['backend_sha']} + {result['frontend_sha']}"
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
