#!/usr/bin/env python3
"""Reconcile the two protected dev tips through the existing release workflow."""
from __future__ import annotations

import argparse
import json
import os
import sys
import re
import time
from datetime import datetime, timedelta, timezone
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


# The one rule for "this path is not a release build input". Conservative on purpose:
# every Dockerfile does `COPY . ...` (build context is the repo root), so a path only
# qualifies when it is in .dockerignore or when no runtime code, compose file or deploy
# script reads it. docs/ as a whole does NOT qualify: services read docs/contracts,
# docs/deployment/loop-catalog.registry.json, docs/deployment/evidence/**, docs/04,
# docs/research/notes and more. See docs/deployment/evidence/DEV-DEPLOY-SKIP-NON-RUNTIME-CHANGES-20261117.
NON_BUILD_INPUT_PREFIXES = (".orchestrator/", "ai-task-archive/")  # both in .dockerignore
NON_BUILD_INPUT_MARKDOWN_PREFIXES = ("docs/operations/", "docs/reviews/")  # referenced as strings only


def is_non_build_input(path):
    if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
        return False
    if path in CLOSEOUT_REPORT_PATHS or path.startswith(NON_BUILD_INPUT_PREFIXES):
        return True
    if path.endswith(".md") and ("/" not in path or path.startswith(NON_BUILD_INPUT_MARKDOWN_PREFIXES)):
        return True
    return False


def non_runtime_change_count(backend, base_sha, head_sha):
    """Number of changed files when every one is outside the release build inputs, else None
    (fail open: any doubt returns None so the release is dispatched as before)."""
    if not base_sha or not head_sha or base_sha == head_sha:
        return None
    try:
        comparison = backend.request("GET", f"/repos/{backend.repository}/compare/{base_sha}...{head_sha}")
    except Exception:
        return None
    if not isinstance(comparison, dict) or comparison.get("truncated"):
        return None
    if comparison.get("status") != "ahead" or comparison.get("behind_by", 0) != 0:
        return None
    ahead_by = comparison.get("ahead_by", comparison.get("total_commits"))
    total_commits = comparison.get("total_commits", ahead_by)
    if (type(ahead_by) is not int or type(total_commits) is not int
            or ahead_by <= 0 or total_commits != ahead_by):
        return None
    commits = comparison.get("commits")
    if commits is not None and (not isinstance(commits, list) or len(commits) != total_commits):
        return None
    files = comparison.get("files")
    if not isinstance(files, list) or not (1 <= len(files) < 300):
        return None
    if comparison.get("total_files") not in (None, len(files)):
        return None
    for item in files:
        if not isinstance(item, dict) or item.get("truncated"):
            return None
        status = item.get("status")
        if status not in {"added", "modified", "removed", "renamed"}:
            return None
        filename = item.get("filename")
        previous = item.get("previous_filename")
        if not is_non_build_input(filename):
            return None
        if status == "renamed" and not previous:
            return None
        if previous is not None and not is_non_build_input(previous):
            return None
    return len(files)


def closeout_reports_only(backend, base_sha, head_sha):
    return non_runtime_change_count(backend, base_sha, head_sha) is not None


# ---- quiet windows (DEV-DEPLOY-QUIET-WINDOWS-20261118) ----
# No release is dispatched while a window is open, nor when any part of the Root that can
# recreate, restart or rebuild hosted dev services or switch the served FE/BFF would land in one.
# A running release is never cancelled (busy() wins).
# The protected span runs from dispatch to the end of the Root's last such step
# (DEV-DEPLOY-QUIET-WINDOW-HOLD-COVERS-ROOT-SPAN-20261127). On 11/27 containers were recreated
# 42-62 min after dispatch (runner queue up to 21 min) and Admit/acceptance ran until about
# 2h45m, so the old 17-minute switch lead only covered the start. Those steps sit in all three
# Root jobs (deploy-dev, coordinate-dev-release, dev-acceptance-stage: its qualification lease
# is the last possible mutation), so a completed Root's created_at -> updated_at (queue time
# included) bounds the span from above; see the task evidence for the step list.
BACKEND_SWITCH_LEAD_MINUTES = 17  # legacy default lead for direct quiet_window_hold() callers
ROOT_SPAN_RECORDS = 10  # N: slowest of the last N completed dev Roots
# No record: 2h45m observed on 11/27 rounded up to 3h; the job timeouts (6h + 6h + 2h) would
# leave no weekday dispatch slot at all, so they are not used as the fallback.
ROOT_SPAN_FALLBACK_MINUTES = 180
DEFAULT_QUIET_WINDOWS = "mon-fri 00:30-05:30; daily 09:05-10:45"  # UTC: Taiwan session; persona daily cycle
QUIET_WINDOWS_VARIABLE = "DEV_DEPLOY_QUIET_WINDOWS"
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _parse_days(text):
    days = set()
    for part in text.split(","):
        part = part.strip().lower()
        if part == "daily":
            days.update(range(7))
        elif "-" in part:
            first, _, last = part.partition("-")
            if first not in _DAYS or last not in _DAYS or _DAYS.index(first) > _DAYS.index(last):
                raise ValueError(f"bad day range {part!r}")
            days.update(range(_DAYS.index(first), _DAYS.index(last) + 1))
        elif part in _DAYS:
            days.add(_DAYS.index(part))
        else:
            raise ValueError(f"bad day {part!r}")
    return frozenset(days)


def _parse_minute(text):
    match = _TIME.match(text.strip())
    if not match:
        raise ValueError(f"bad time {text!r}")
    return int(match.group(1)) * 60 + int(match.group(2))


def parse_quiet_windows(text):
    """'<days> HH:MM-HH:MM; ...' in UTC, days = daily | mon-fri | mon,wed. Raises ValueError.
    Returns [(days, start_minute, end_minute, label)]; a window never crosses midnight."""
    windows = []
    for entry in text.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        days_text, _, span = entry.rpartition(" ")
        start_text, _, end_text = span.partition("-")
        if not days_text or not end_text:
            raise ValueError(f"bad window {entry!r}")
        start, end = _parse_minute(start_text), _parse_minute(end_text)
        if start >= end:
            raise ValueError(f"window {entry!r} must end after it starts (UTC, no midnight crossing)")
        windows.append((_parse_days(days_text), start, end, entry))
    if not windows:
        raise ValueError("no windows")
    return windows


def load_quiet_windows(value):
    """(windows, source). Unset or blank -> defaults; malformed -> defaults, flagged (fail closed)."""
    if value is None or not value.strip():
        return parse_quiet_windows(DEFAULT_QUIET_WINDOWS), "default"
    try:
        return parse_quiet_windows(value), "variable"
    except ValueError as exc:
        return parse_quiet_windows(DEFAULT_QUIET_WINDOWS), f"malformed_defaults_used: {exc}"


def _open_window(windows, instant):
    minute = instant.hour * 60 + instant.minute
    for days, start, end, label in windows:
        if instant.weekday() in days and start <= minute < end:
            return label, instant.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    return None


def _parse_instant(text):
    if not isinstance(text, str) or not text:
        raise ValueError("missing timestamp")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def root_span(backend, records=ROOT_SPAN_RECORDS, fallback=ROOT_SPAN_FALLBACK_MINUTES):
    """(minutes, source) of the slowest of the last `records` completed first-attempt dev Roots,
    measured dispatch (created_at, so runner queue time counts) to completion (updated_at).
    One GitHub read. No completed record -> fallback. Records whose timestamps cannot be read
    are skipped and counted in the source; when none is readable, or the list itself is
    unreadable, -> (None, 'unavailable: ...'), which holds fail-closed."""
    try:
        listed = runs(backend, DEPLOY, event="workflow_dispatch")
        if not isinstance(listed, list):
            raise ValueError("workflow_runs is not a list")
    except Exception as exc:
        return None, f"unavailable: {exc}"
    roots = [run for run in listed if isinstance(run, dict)
             and str(run.get("display_title", "")).startswith("Dev release ")
             and run.get("status") == "completed" and run.get("run_attempt", 1) == 1
             and type(run.get("id")) is int]
    roots = sorted(roots, key=lambda run: run["id"], reverse=True)[:records]
    if not roots:
        return fallback, f"fallback: no completed dev Root record ({fallback} min)"
    spans, unreadable = [], 0
    for run in roots:
        try:
            minutes = (_parse_instant(run.get("updated_at")) - _parse_instant(run.get("created_at"))).total_seconds() / 60
            if minutes < 0:
                raise ValueError("negative span")
            spans.append((-(-int(minutes * 60) // 60), run["id"]))
        except (ValueError, TypeError, OverflowError):
            unreadable += 1
    if not spans:
        return None, f"unavailable: none of the last {len(roots)} completed dev Roots has readable timestamps"
    minutes, run_id = max(spans, key=lambda item: item[0])
    note = f"; {unreadable} record(s) with unreadable timestamps skipped" if unreadable else ""
    return minutes, f"recorded: slowest of last {len(roots)} completed dev Roots (run {run_id}){note}"


def quiet_window_hold(windows, now, lead_minutes=BACKEND_SWITCH_LEAD_MINUTES, span_source=None):
    """None when a dispatch at `now` is allowed; else why it is held and the first allowed minute.
    Every minute from dispatch to dispatch + lead_minutes must be outside windows. With
    span_source (the Root span from root_span()) the decision names the span and its source;
    a span of None holds fail-closed."""
    now = now.astimezone(timezone.utc).replace(second=0, microsecond=0) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if lead_minutes is None:
        return {"reason": "root_span_unavailable", "window": None, "window_ends_at": None,
                "dispatch_allowed_from": None, "root_span_minutes": None, "root_span_source": span_source}

    def blocking(at):
        for offset in range(lead_minutes + 1):
            hit = _open_window(windows, at + timedelta(minutes=offset))
            if hit:
                return offset, hit
        return None

    first = blocking(now)
    if not first:
        return None
    offset, (label, ends) = first
    # Every dispatch before the end of a window the span hits still reaches into that window
    # (it starts before the end, and its span ends no earlier than the hit minute), so the next
    # candidate is that end. Each jump passes one window instance; 8 days bounds the search.
    resume, hit, limit = now, first, now + timedelta(days=8)
    while hit and resume <= limit:
        resume = hit[1][1]
        hit = blocking(resume)
    if offset == 0:
        reason = "dispatch_inside_window"
    else:
        reason = "backend_switch_inside_window" if span_source is None else "root_span_inside_window"
    decision = {
        "reason": reason, "window": label, "window_ends_at": ends.strftime("%Y-%m-%dT%H:%MZ"),
        "dispatch_allowed_from": None if hit else resume.strftime("%Y-%m-%dT%H:%MZ"),
    }
    if span_source is None:
        decision["backend_switch_lead_minutes"] = lead_minutes
    else:
        decision.update(root_span_minutes=lead_minutes, root_span_source=span_source)
    return decision


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
    attempts = [run for run in runs(backend, DEPLOY, event="workflow_dispatch")
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
            and (skipped := non_runtime_change_count(backend, served_backend, candidate_backend)) is not None):
        return {
            **result,
            "served_backend_sha": served_backend,
            "changed_file_count": skipped,
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


def reconcile(backend, frontend, *, fe_url, bff_url, apply=False, fetch=fetch_url_json, sleep=time.sleep,
              quiet_windows=None, now=None):
    result = inspect_pair(backend, frontend, fe_url=fe_url, bff_url=bff_url, fetch=fetch)
    if result["state"] != "ready" or not apply:
        return result
    if quiet_windows:
        # The one extra GitHub read for the hold: the recorded Root span.
        span, span_source = root_span(backend)
        result.update(root_span_minutes=span, root_span_source=span_source)
        hold = quiet_window_hold(quiet_windows, now or datetime.now(timezone.utc),
                                 lead_minutes=span, span_source=span_source)
        if hold:
            # Nothing is queued: the next trigger or the fallback cron re-selects the newest green pair.
            return {**result, "state": "held_quiet_window", **hold}
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
            # workflow_dispatch head_sha is the dev tip at dispatch time, not the selected
            # backend, so the full-pair title and an unseen run id identify this dispatch.
            if run["id"] not in previous_ids and run.get("display_title") == expected_title:
                return {**result, "state": "dispatched", "run_url": run["html_url"]}
        sleep(3)
    raise ControllerError("dispatch sent but run could not be correlated; inspect Nonprod Deploy before retrying")


def summary_lines(result):
    """Deployed and acceptance are reported separately; acceptance is the verdict of the run's
    dev-acceptance-stage job, and a release is 'accepted' only when that verdict is 'passed'."""
    if result.get("state") == "report_only_no_deploy":
        return (f"- skipped: no release build input changed ({result.get('changed_file_count')} file(s) between "
                f"served {result.get('served_backend_sha')} and {result.get('backend_sha')}); "
                "the commits stay merged and ride the next release that changes a build input\n")
    if result.get("state") == "held_quiet_window" and result.get("reason") == "root_span_unavailable":
        return (f"- held: Root span unknown ({result.get('root_span_source')}); no release dispatched (fail closed); "
                "the next trigger or hourly fallback retries; this is not a broken trigger\n")
    if result.get("state") == "held_quiet_window" and "root_span_minutes" in result:
        return (f"- held: quiet window `{result.get('window')}` ({result.get('reason')}); no release dispatched, "
                f"window ends {result.get('window_ends_at')}, dispatch allowed from {result.get('dispatch_allowed_from')} "
                f"(Root span {result.get('root_span_minutes')} min, {result.get('root_span_source')}); "
                "the next trigger or hourly fallback dispatches the newest green commit; this is not a broken trigger\n")
    if result.get("state") == "held_quiet_window":
        return (f"- held: quiet window `{result.get('window')}` ({result.get('reason')}); no release dispatched, "
                f"window ends {result.get('window_ends_at')}, dispatch allowed from {result.get('dispatch_allowed_from')} "
                f"(backend switch lead {result.get('backend_switch_lead_minutes')} min); "
                "the next trigger or hourly fallback dispatches the newest green commit; this is not a broken trigger\n")
    if result.get("state") != "dispatched":
        return ""
    return ("- deployed: pending (follow run_url)\n"
            "- acceptance: pending (verdict of the run's `dev-acceptance-stage` job; "
            "never rolls back, never fails the run)\n")


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
    windows, windows_source = load_quiet_windows(os.environ.get(QUIET_WINDOWS_VARIABLE))
    try:
        result = reconcile(*clients, fe_url=args.fe_url, bff_url=args.bff_url, apply=args.apply,
                           quiet_windows=windows)
    except Exception as exc:
        result = {"state": "inspection_or_dispatch_failed", "reason": str(exc)}
    result["quiet_windows_source"] = windows_source
    print(json.dumps(result, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as handle:
            handle.write("\n## Automatic dev pair check\n\n```json\n" + json.dumps(result, indent=2) + "\n```\n")
            handle.write(f"- state: {result['state']}\n")
            if windows_source.startswith("malformed"):
                handle.write(f"- WARNING: {QUIET_WINDOWS_VARIABLE} is malformed; default windows "
                             f"({DEFAULT_QUIET_WINDOWS}) are in force ({windows_source})\n")
            handle.write(summary_lines(result))
    return 1 if result["state"] == "inspection_or_dispatch_failed" else 0


if __name__ == "__main__":
    sys.exit(main())
