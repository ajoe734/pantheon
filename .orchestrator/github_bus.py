#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common import (
    ROOT,
    agent_config_for,
    command_exists,
    config_path,
    execution_context_files,
    load_config,
    load_json,
    load_jsonl,
    load_status,
    relpath,
    render_template,
    run_command,
    utc_now,
    write_activity_log,
    write_json,
)
from coordination_file_watcher import queue_coordination_dispatch
from cross_repo_issue_mapper import coordination_issue_body, coordination_issue_labels, coordination_issue_title
from github_cloud_relay import pull_commands, push_status_digest
from github_command_parser import GitHubCommand, parse_command
from multi_repo_registry import coordination_enabled, matching_repo_id, repository_slug, resolve_worker_kind
from runtime_state import enqueue_event
from watch_events import render_wakeup_message

COMMENT_MARKER = "<!-- pantheon-bus -->"
MAX_PROCESSED_IDS = 2000


class GitHubBusError(RuntimeError):
    pass


class GitHubBusOffline(GitHubBusError):
    pass


def resolve_gh_binary() -> str | None:
    vendored = ROOT / ".orchestrator" / "bin" / "gh"
    if vendored.exists() and os.access(vendored, os.X_OK):
        return str(vendored)
    return command_exists("gh")


def _iso_now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def default_bus_state() -> dict[str, Any]:
    return {
        "version": 1,
        "repo": None,
        "last_sync_at": None,
        "offline_until": None,
        "last_error": None,
        "processed_review_ids": [],
        "processed_comment_ids": [],
        "processed_webhook_deliveries": [],
        "poll_cursors": {
            "pr_reviews": 0,
            "issue_comments": 0,
            "coordination_comments": 0,
        },
        "tasks": {},
        "coordination": {},
    }


def load_bus_state(config: dict[str, Any]) -> dict[str, Any]:
    path = config_path(config, "github_bus_state")
    state = load_json(path, default=default_bus_state()) or {}
    merged = default_bus_state()
    merged.update(state)
    merged.setdefault("tasks", {})
    merged.setdefault("processed_review_ids", [])
    merged.setdefault("processed_comment_ids", [])
    merged.setdefault("processed_webhook_deliveries", [])
    merged.setdefault("poll_cursors", {})
    merged["poll_cursors"].setdefault("pr_reviews", 0)
    merged["poll_cursors"].setdefault("issue_comments", 0)
    merged["poll_cursors"].setdefault("coordination_comments", 0)
    merged.setdefault("coordination", {})
    return merged


def save_bus_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    pruned_tasks: dict[str, Any] = {}
    for task_id, entry in (state.get("tasks") or {}).items():
        if any(
            (
                entry.get("review_pr"),
                entry.get("ops_issue"),
                entry.get("last_review_hash"),
                entry.get("last_issue_hash"),
            )
        ):
            pruned_tasks[task_id] = entry
    state["tasks"] = pruned_tasks
    pruned_coordination: dict[str, Any] = {}
    for key, entry in (state.get("coordination") or {}).items():
        issue = (entry or {}).get("issue") or {}
        if any((issue.get("number"), issue.get("url"), entry.get("last_hash"))):
            pruned_coordination[key] = entry
    state["coordination"] = pruned_coordination
    state["last_sync_at"] = utc_now()
    state["processed_review_ids"] = state.get("processed_review_ids", [])[-MAX_PROCESSED_IDS:]
    state["processed_comment_ids"] = state.get("processed_comment_ids", [])[-MAX_PROCESSED_IDS:]
    state["processed_webhook_deliveries"] = state.get("processed_webhook_deliveries", [])[-MAX_PROCESSED_IDS:]
    write_json(config_path(config, "github_bus_state"), state)


def poll_batch_size(config: dict[str, Any], key: str, default: int) -> int:
    cfg = ((config.get("github_bus") or {}).get("poll_batch_sizes") or {})
    try:
        value = int(cfg.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(1, value)


def _poll_batch(items: list[Any], *, cursor: int, limit: int) -> tuple[list[Any], int]:
    if not items:
        return [], 0
    normalized_cursor = cursor if 0 <= cursor < len(items) else 0
    end = min(normalized_cursor + max(1, limit), len(items))
    batch = items[normalized_cursor:end]
    next_cursor = 0 if end >= len(items) else end
    return batch, next_cursor


def trim_text(value: str | None, limit: int = 400) -> str:
    if not value:
        return ""
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def infer_repo_slug(config: dict[str, Any], bus_state: dict[str, Any]) -> str | None:
    configured = (config.get("github_bus", {}) or {}).get("repo")
    if configured:
        return str(configured)
    if bus_state.get("repo"):
        return str(bus_state["repo"])
    proc = run_command(["git", "remote", "get-url", "origin"], cwd=ROOT)
    if proc.returncode != 0:
        return None
    remote = (proc.stdout or "").strip()
    patterns = [
        re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$"),
    ]
    for pattern in patterns:
        match = pattern.search(remote)
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"
    return None


def default_branch(config: dict[str, Any]) -> str:
    bus_cfg = config.get("github_bus", {}) or {}
    configured = bus_cfg.get("default_branch")
    if configured:
        return str(configured)
    proc = run_command(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=ROOT)
    if proc.returncode == 0:
        ref = (proc.stdout or "").strip()
        if "/" in ref:
            return ref.rsplit("/", 1)[-1]
    return "main"


def current_branch() -> str | None:
    proc = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT)
    if proc.returncode != 0:
        return None
    branch = (proc.stdout or "").strip()
    return branch or None


def branch_exists(branch: str) -> bool:
    proc = run_command(["git", "show-ref", "--verify", f"refs/heads/{branch}"], cwd=ROOT)
    return proc.returncode == 0


def branch_head_sha(branch: str) -> str | None:
    proc = run_command(["git", "rev-parse", branch], cwd=ROOT)
    if proc.returncode != 0:
        return None
    sha = (proc.stdout or '').strip()
    return sha or None


def branch_has_diff(base: str, branch: str) -> bool:
    proc = run_command(["git", "rev-list", "--count", f"{base}..{branch}"], cwd=ROOT)
    if proc.returncode != 0:
        return False
    try:
        return int((proc.stdout or '0').strip() or '0') > 0
    except ValueError:
        return False


def remote_branch_exists(branch: str, remote: str = "origin") -> bool:
    proc = run_command(["git", "ls-remote", "--heads", remote, branch], cwd=ROOT)
    if proc.returncode != 0:
        return False
    return bool((proc.stdout or "").strip())


def run_gh_process(
    args: list[str],
    *,
    timeout_seconds: float,
    gh_binary: str | None = None,
) -> subprocess.CompletedProcess[str]:
    # Avoid subprocess.run(..., timeout=...) here: if gh gets wedged in I/O,
    # subprocess.run waits on teardown and can stall the supervisor heartbeat.
    binary = gh_binary or resolve_gh_binary() or "gh"
    with tempfile.TemporaryFile() as stdout_handle, tempfile.TemporaryFile() as stderr_handle:
        process = subprocess.Popen(
            [binary, *args],
            cwd=str(ROOT),
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
            raise exc

        stdout_handle.seek(0)
        stderr_handle.seek(0)
        stdout = stdout_handle.read().decode("utf-8", errors="replace")
        stderr = stderr_handle.read().decode("utf-8", errors="replace")
        return subprocess.CompletedProcess([binary, *args], process.returncode or 0, stdout, stderr)


def run_gh(args: list[str], *, allow_offline: bool = True) -> subprocess.CompletedProcess[str]:
    gh_binary = resolve_gh_binary()
    if not gh_binary:
        raise GitHubBusError("GitHub CLI `gh` is not installed.")
    timeout_seconds = 8.0
    try:
        cfg = load_config()
        timeout_seconds = float((cfg.get("github_bus", {}) or {}).get("command_timeout_seconds", 8))
    except Exception:
        timeout_seconds = 8.0
    try:
        proc = run_gh_process(args, timeout_seconds=timeout_seconds, gh_binary=gh_binary)
    except subprocess.TimeoutExpired as exc:
        message = f"GitHub CLI timed out after {int(timeout_seconds)}s while running: gh {' '.join(args)}"
        if allow_offline:
            raise GitHubBusOffline(message) from exc
        raise GitHubBusError(message) from exc
    if proc.returncode == 0:
        return proc
    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
    lowered = combined.lower()
    if allow_offline and (
        "error connecting to api.github.com" in lowered
        or "check your internet connection" in lowered
        or "dial tcp" in lowered
        or "no such host" in lowered
    ):
        raise GitHubBusOffline(trim_text(combined, 600))
    raise GitHubBusError(trim_text(combined, 600))


def gh_json(args: list[str]) -> Any:
    proc = run_gh(args)
    text = (proc.stdout or "").strip()
    return json.loads(text) if text else None


def ensure_temp_body(text: str) -> Path:
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False)
    handle.write(text)
    handle.flush()
    handle.close()
    return Path(handle.name)


def task_bus_entry(bus_state: dict[str, Any], task_id: str) -> dict[str, Any]:
    return bus_state.setdefault("tasks", {}).setdefault(
        task_id,
        {
            "review_pr": None,
            "ops_issue": None,
            "last_review_hash": None,
            "last_issue_hash": None,
        },
    )


def coordination_bus_key(repo: str, feature_id: str) -> str:
    return f"{repo}:{feature_id}"


def coordination_bus_entry(bus_state: dict[str, Any], repo: str, feature_id: str) -> dict[str, Any]:
    return bus_state.setdefault("coordination", {}).setdefault(
        coordination_bus_key(repo, feature_id),
        {
            "repo": repo,
            "feature_id": feature_id,
            "issue": None,
            "last_hash": None,
        },
    )


def task_signature(task: dict[str, Any], fields: list[str]) -> str:
    payload = {field: task.get(field) for field in fields}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def build_template_body(config: dict[str, Any], template_key: str, variables: dict[str, Any]) -> str:
    template_rel = config.get("github_bus", {}).get("templates", {}).get(template_key)
    if not template_rel:
        raise GitHubBusError(f"Missing github_bus template config for {template_key}")
    template_path = ROOT / template_rel
    return render_template(template_path, variables).strip() + "\n"


def reviewer_handles(config: dict[str, Any], task: dict[str, Any]) -> list[str]:
    mapping = (config.get("github_bus", {}) or {}).get("reviewers", {}) or {}
    return list(mapping.get(task.get("reviewer"), []) or [])


def unpublished_branch_recheck_seconds(config: dict[str, Any]) -> int:
    cfg = (config.get("github_bus", {}) or {})
    try:
        value = int(cfg.get("unpublished_branch_recheck_seconds", 300))
    except (TypeError, ValueError):
        value = 300
    return max(30, value)


def create_label_args(labels: list[str]) -> list[str]:
    args: list[str] = []
    for label in labels:
        args.extend(["--label", label])
    return args


def edit_label_args(labels: list[str]) -> list[str]:
    args: list[str] = []
    for label in labels:
        args.extend(["--add-label", label])
    return args


def review_branch_for_task(config: dict[str, Any], status: dict[str, Any], task: dict[str, Any]) -> str | None:
    meta = task.get("github") or {}
    explicit = meta.get("head_branch")
    if explicit and branch_exists(str(explicit)):
        return str(explicit)

    owner = task.get("owner")
    for agent in status.get("agents", []):
        if agent.get("name") == owner:
            branch = agent.get("branch")
            if branch and branch_exists(str(branch)):
                return str(branch)

    branch = current_branch()
    if branch and branch != default_branch(config):
        return branch
    return None


def parse_number_from_url(url: str) -> int | None:
    match = re.search(r"/(issues|pull)/(\d+)$", url)
    if match:
        return int(match.group(2))
    return None


def find_existing_issue(repo: str, task_id: str) -> dict[str, Any] | None:
    data = gh_json(["issue", "list", "--repo", repo, "--state", "open", "--search", f'"[OpsBus] {task_id}" in:title', "--json", "number,title,url,state,labels"])
    if isinstance(data, list) and data:
        return data[0]
    return None


def find_existing_pr(repo: str, task_id: str, branch: str | None) -> dict[str, Any] | None:
    search = f'"[ReviewBus] {task_id}" in:title'
    args = ["pr", "list", "--repo", repo, "--state", "open", "--search", search, "--json", "number,title,url,headRefName,state"]
    if branch:
        args.extend(["--head", branch])
    data = gh_json(args)
    if isinstance(data, list) and data:
        return data[0]
    return None


def find_existing_coordination_issue(repo: str, feature_id: str) -> dict[str, Any] | None:
    data = gh_json(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            f'"[CoordBus] {feature_id}" in:title',
            "--json",
            "number,title,url,state,labels",
        ]
    )
    if isinstance(data, list) and data:
        return data[0]
    return None


def issue_mutation_with_label_fallback(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return run_gh(command)
    except GitHubBusError as exc:
        message = str(exc).lower()
        if "label" not in message:
            raise
        rebuilt: list[str] = []
        skip_next = False
        for item in command:
            if skip_next:
                skip_next = False
                continue
            if item in {"--label", "--add-label"}:
                skip_next = True
                continue
            rebuilt.append(item)
        return run_gh(rebuilt)


def upsert_ops_issue(config: dict[str, Any], bus_state: dict[str, Any], repo: str, task: dict[str, Any], reason: str, details: str) -> bool:
    entry = task_bus_entry(bus_state, task["id"])
    issue_ref = entry.get("ops_issue")
    labels = list((config.get("github_bus", {}) or {}).get("labels", {}).get("ops", []))
    variables = {
        "marker": COMMENT_MARKER,
        "task_id": task["id"],
        "task_title": task.get("title") or task["id"],
        "task_summary": task.get("summary_zh") or task.get("title") or task["id"],
        "task_status": task.get("status") or "unknown",
        "task_owner": task.get("owner") or "-",
        "task_reviewer": task.get("reviewer") or "-",
        "depends_on": ", ".join(task.get("depends_on", [])) or "-",
        "next_step": task.get("next") or "-",
        "reason": reason,
        "details": details,
    }
    body = build_template_body(config, "ops_issue", variables)
    title = f"[OpsBus] {task['id']} blocked: {trim_text(reason, 60) or task['title']}"
    issue_hash = json.dumps({"title": title, "body": body, "labels": labels}, ensure_ascii=False, sort_keys=True)
    if entry.get("last_issue_hash") == issue_hash and issue_ref:
        return False

    body_file = ensure_temp_body(body)
    try:
        if issue_ref and issue_ref.get("number"):
            number = int(issue_ref["number"])
            run_gh(["issue", "edit", str(number), "--repo", repo, "--title", title, "--body-file", str(body_file), *edit_label_args(labels)])
            issue = dict(issue_ref)
        else:
            found = find_existing_issue(repo, task["id"])
            if found:
                number = int(found["number"])
                run_gh(["issue", "edit", str(number), "--repo", repo, "--title", title, "--body-file", str(body_file), *edit_label_args(labels)])
                issue = {"number": number, "url": found.get("url"), "title": title}
            else:
                proc = run_gh(["issue", "create", "--repo", repo, "--title", title, "--body-file", str(body_file), *create_label_args(labels)])
                url = (proc.stdout or "").strip().splitlines()[-1]
                issue = {"number": parse_number_from_url(url), "url": url, "title": title}
    finally:
        body_file.unlink(missing_ok=True)

    entry["ops_issue"] = {
        "number": issue.get("number"),
        "url": issue.get("url"),
        "title": title,
        "last_comment_id": (issue_ref or {}).get("last_comment_id"),
        "state": "open",
    }
    entry["last_issue_hash"] = issue_hash
    write_activity_log(
        config,
        {
            "type": "github_ops_issue_synced",
            "task_id": task["id"],
            "message": f"GitHub ops issue synced for {task['id']}",
            "github_url": entry["ops_issue"].get("url"),
        },
    )
    return True


def close_ops_issue(config: dict[str, Any], entry: dict[str, Any], task_id: str, reason: str, repo: str) -> bool:
    issue_ref = entry.get("ops_issue")
    if not issue_ref or not issue_ref.get("number"):
        return False
    if issue_ref.get("state") == "closed":
        return False
    number = int(issue_ref["number"])
    comment = f"{COMMENT_MARKER}\nResolved locally: {reason}".strip()
    run_gh(["issue", "close", str(number), "--repo", repo, "--comment", comment])
    issue_ref["state"] = "closed"
    write_activity_log(
        config,
        {
            "type": "github_ops_issue_closed",
            "task_id": task_id,
            "message": reason,
            "github_url": issue_ref.get("url"),
        },
    )
    return True


def coordination_counterpart_links(bus_state: dict[str, Any], feature_id: str, current_repo: str) -> list[str]:
    links: list[str] = []
    for key, entry in (bus_state.get("coordination") or {}).items():
        if not key.endswith(f":{feature_id}"):
            continue
        if entry.get("repo") == current_repo:
            continue
        issue = (entry or {}).get("issue") or {}
        if issue.get("url"):
            links.append(str(issue["url"]))
    return links


def upsert_coordination_issue(config: dict[str, Any], bus_state: dict[str, Any], repo: str, feature: dict[str, Any]) -> bool:
    feature_id = str(feature.get("feature_id") or "").strip()
    if not feature_id:
        return False
    entry = coordination_bus_entry(bus_state, repo, feature_id)
    issue_ref = entry.get("issue")
    title = coordination_issue_title(feature)
    labels = coordination_issue_labels(config, feature)
    body = coordination_issue_body(
        feature,
        repo_slug=repo,
        counterpart_links=coordination_counterpart_links(bus_state, feature_id, repo),
    )
    issue_hash = json.dumps({"title": title, "body": body, "labels": labels}, ensure_ascii=False, sort_keys=True)
    if entry.get("last_hash") == issue_hash and issue_ref:
        return False

    body_file = ensure_temp_body(body)
    try:
        if issue_ref and issue_ref.get("number"):
            number = int(issue_ref["number"])
            issue_mutation_with_label_fallback(
                ["issue", "edit", str(number), "--repo", repo, "--title", title, "--body-file", str(body_file), *edit_label_args(labels)]
            )
            issue = dict(issue_ref)
        else:
            found = find_existing_coordination_issue(repo, feature_id)
            if found:
                number = int(found["number"])
                issue_mutation_with_label_fallback(
                    ["issue", "edit", str(number), "--repo", repo, "--title", title, "--body-file", str(body_file), *edit_label_args(labels)]
                )
                issue = {"number": number, "url": found.get("url"), "title": title}
            else:
                proc = issue_mutation_with_label_fallback(
                    ["issue", "create", "--repo", repo, "--title", title, "--body-file", str(body_file), *create_label_args(labels)]
                )
                url = (proc.stdout or "").strip().splitlines()[-1]
                issue = {"number": parse_number_from_url(url), "url": url, "title": title}
    finally:
        body_file.unlink(missing_ok=True)

    entry["issue"] = {
        "number": issue.get("number"),
        "url": issue.get("url"),
        "title": title,
        "state": "open",
    }
    entry["last_hash"] = issue_hash
    write_activity_log(
        config,
        {
            "type": "github_coordination_issue_synced",
            "task_id": feature_id,
            "message": f"GitHub coordination issue synced for {feature_id} in {repo}.",
            "github_url": entry["issue"].get("url"),
        },
    )
    return True


def upsert_review_pr(config: dict[str, Any], bus_state: dict[str, Any], status: dict[str, Any], repo: str, task: dict[str, Any]) -> bool:
    entry = task_bus_entry(bus_state, task["id"])
    pr_ref = entry.get("review_pr")
    branch = review_branch_for_task(config, status, task)
    if not branch:
        skip_hash = json.dumps({"state": "skipped_no_branch", "task_id": task["id"], "status": task.get("status")}, ensure_ascii=False, sort_keys=True)
        if entry.get("last_review_hash") == skip_hash and (entry.get("review_pr") or {}).get("state") == "skipped_no_branch":
            return False
        entry["review_pr"] = {
            "number": (pr_ref or {}).get("number"),
            "url": (pr_ref or {}).get("url"),
            "title": f"[ReviewBus] {task['id']} {task['title']}",
            "branch": None,
            "state": "skipped_no_branch",
        }
        entry["last_review_hash"] = skip_hash
        write_activity_log(
            config,
            {
                "type": "github_review_pr_skipped",
                "task_id": task["id"],
                "message": "Review task is in review, but no non-default local branch is available for PR creation.",
            },
        )
        return True

    base = default_branch(config)
    title = f"[ReviewBus] {task['id']} {task['title']}"
    head_sha = branch_head_sha(branch)
    skip_hash = json.dumps(
        {
            "state": "skipped_unpublished_branch",
            "task_id": task["id"],
            "branch": branch,
            "base": base,
            "head_sha": head_sha,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    previous_unpublished = (
        isinstance(pr_ref, dict)
        and pr_ref.get("state") == "skipped_unpublished_branch"
        and pr_ref.get("branch") == branch
        and pr_ref.get("head_sha") == head_sha
        and entry.get("last_review_hash") == skip_hash
    )
    if previous_unpublished:
        last_check = _parse_iso(str(pr_ref.get("last_remote_branch_check_at") or ""))
        if last_check and (_iso_now_dt() - last_check).total_seconds() < unpublished_branch_recheck_seconds(config):
            return False

    if not remote_branch_exists(branch):
        checked_at = utc_now()
        entry["review_pr"] = {
            "number": (pr_ref or {}).get("number"),
            "url": (pr_ref or {}).get("url"),
            "title": title,
            "branch": branch,
            "state": "skipped_unpublished_branch",
            "head_sha": head_sha,
            "last_remote_branch_check_at": checked_at,
        }
        entry["last_review_hash"] = skip_hash
        if previous_unpublished:
            return False
        write_activity_log(
            config,
            {
                "type": "github_review_pr_skipped",
                "task_id": task["id"],
                "message": f"Review task is in review, but branch `{branch}` is not pushed to `origin` yet.",
            },
        )
        return True
    variables = {
        "marker": COMMENT_MARKER,
        "task_id": task["id"],
        "task_title": task.get("title") or task["id"],
        "task_summary": task.get("summary_zh") or task.get("title") or task["id"],
        "task_status": task.get("status") or "review",
        "task_owner": task.get("owner") or "-",
        "task_reviewer": task.get("reviewer") or "-",
        "depends_on": ", ".join(task.get("depends_on", [])) or "-",
        "next_step": task.get("next") or "-",
        "artifacts": "\n".join(f"- `{item}`" for item in (task.get("artifacts") or [])) or "- (none listed)",
        "branch": branch,
        "base_branch": base,
    }
    body = build_template_body(config, "review_pr", variables)
    labels = list((config.get("github_bus", {}) or {}).get("labels", {}).get("review", []))
    pr_hash = json.dumps({"title": title, "body": body, "labels": labels, "branch": branch, "base": base, "head_sha": head_sha}, ensure_ascii=False, sort_keys=True)
    if entry.get("last_review_hash") == pr_hash and pr_ref:
        return False

    if not branch_has_diff(base, branch):
        entry["review_pr"] = {
            "number": (pr_ref or {}).get("number"),
            "url": (pr_ref or {}).get("url"),
            "title": title,
            "branch": branch,
            "state": "skipped_no_commits",
            "head_sha": head_sha,
        }
        entry["last_review_hash"] = pr_hash
        write_activity_log(
            config,
            {
                "type": "github_review_pr_skipped",
                "task_id": task["id"],
                "message": f"Review task is in review, but branch `{branch}` has no commits ahead of `{base}` yet.",
            },
        )
        return True

    body_file = ensure_temp_body(body)
    try:
        if pr_ref and pr_ref.get("number"):
            number = int(pr_ref["number"])
            run_gh(["pr", "edit", str(number), "--repo", repo, "--title", title, "--body-file", str(body_file), *edit_label_args(labels)])
            pr = dict(pr_ref)
        else:
            found = find_existing_pr(repo, task["id"], branch)
            if found:
                number = int(found["number"])
                run_gh(["pr", "edit", str(number), "--repo", repo, "--title", title, "--body-file", str(body_file), *edit_label_args(labels)])
                pr = {"number": number, "url": found.get("url"), "title": title, "headRefName": branch}
            else:
                create_args = ["pr", "create", "--repo", repo, "--draft", "--title", title, "--body-file", str(body_file), "--base", base, "--head", branch]
                if labels:
                    create_args.extend(create_label_args(labels))
                if (config.get("github_bus", {}) or {}).get("auto_request_reviewers", True):
                    for handle in reviewer_handles(config, task):
                        create_args.extend(["--reviewer", handle])
                proc = run_gh(create_args)
                url = (proc.stdout or "").strip().splitlines()[-1]
                pr = {"number": parse_number_from_url(url), "url": url, "title": title, "headRefName": branch}
    finally:
        body_file.unlink(missing_ok=True)

    entry["review_pr"] = {
        "number": pr.get("number"),
        "url": pr.get("url"),
        "title": title,
        "branch": branch,
        "state": "open",
        "last_remote_branch_check_at": utc_now(),
    }
    entry["last_review_hash"] = pr_hash
    write_activity_log(
        config,
        {
            "type": "github_review_pr_synced",
            "task_id": task["id"],
            "message": f"GitHub review PR synced for {task['id']}",
            "github_url": entry["review_pr"].get("url"),
        },
    )
    return True


def run_ai_status(command: str, target: str, message: str, *, actor: str | None = None) -> None:
    env = os.environ.copy()
    if actor:
        env["AI_NAME"] = actor
    proc = subprocess.run(
        ["python3", "scripts/ai_status.py", command, target, message],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise GitHubBusError(trim_text((proc.stderr or proc.stdout or "ai_status failed"), 600))


def post_issue_comment(repo: str, issue_number: int, body: str) -> None:
    run_gh(["issue", "comment", str(issue_number), "--repo", repo, "--body", body])


def allowed_logins(config: dict[str, Any], task: dict[str, Any] | None = None) -> set[str]:
    mapping = (config.get("github_bus", {}) or {}).get("reviewers", {}) or {}
    values: set[str] = set()
    for handles in mapping.values():
        for handle in handles or []:
            values.add(handle)
    if task:
        for handle in mapping.get(task.get("reviewer"), []) or []:
            values.add(handle)
    return values


def comment_key(kind: str, item_id: int | str) -> str:
    return f"{kind}:{item_id}"


def resolve_task(
    status: dict[str, Any],
    task_id: str | None,
    fallback_task: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    if task_id:
        normalized = task_id.strip()
        for item in status.get("tasks", []):
            if str(item.get("id")) == normalized:
                return str(item.get("id")), item
        lowered = normalized.lower()
        for item in status.get("tasks", []):
            if str(item.get("id") or "").lower() == lowered:
                return str(item.get("id")), item
    if fallback_task:
        return str(fallback_task.get("id")), fallback_task
    return task_id, None


def apply_bus_command(
    config: dict[str, Any],
    bus_state: dict[str, Any],
    status: dict[str, Any],
    repo: str,
    command: GitHubCommand,
    actor: str,
    *,
    task: dict[str, Any] | None = None,
    issue_number: int | None = None,
    runtime_state: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    task_id, target_task = resolve_task(status, command.target or (task or {}).get("id"), fallback_task=task)
    changed = False
    reply = ""
    owner = str((target_task or task or {}).get("owner") or "").strip() or None
    reviewer = str((target_task or task or {}).get("reviewer") or "").strip() or None

    if command.verb == "approve" and target_task:
        if target_task.get("status") == "review":
            run_ai_status(
                "approve",
                task_id,
                f"GitHub approval bus approved via {'issue #' + str(issue_number) if issue_number else 'relay/webhook'} by @{actor}.",
                actor=reviewer,
            )
        else:
            run_ai_status(
                "reopen",
                task_id,
                f"GitHub approval bus approved via {'issue #' + str(issue_number) if issue_number else 'relay/webhook'} by @{actor}; resuming work.",
                actor=owner or reviewer,
            )
        reply = f"Applied `/approve` to `{task_id}`."
        changed = True
    elif command.verb == "deny" and target_task:
        if target_task.get("status") == "review":
            run_ai_status(
                "reopen",
                task_id,
                f"GitHub approval bus denied via {'issue #' + str(issue_number) if issue_number else 'relay/webhook'} by @{actor}; returning to implementation.",
                actor=reviewer or owner,
            )
        else:
            run_ai_status(
                "note",
                task_id,
                f"GitHub approval bus denial noted via {'issue #' + str(issue_number) if issue_number else 'relay/webhook'} by @{actor}.",
                actor=owner or reviewer,
            )
        reply = f"Recorded `/deny` for `{task_id}`."
        changed = True
    elif command.verb == "retry" and target_task:
        run_ai_status(
            "reopen",
            task_id,
            f"GitHub retry requested via {'issue #' + str(issue_number) if issue_number else 'relay/webhook'} by @{actor}.",
            actor=owner or reviewer,
        )
        queue_resume_for_task(config, target_task)
        reply = f"Queued retry for `{task_id}`."
        changed = True
    elif command.verb == "resume" and command.target:
        changed = queue_resume_for_agent(config, status, command.target)
        reply = f"Queued resume for `{command.target}`." if changed else f"No resumable task found for `{command.target}`."
    elif command.verb == "recheck" and target_task:
        entry = task_bus_entry(bus_state, task_id)
        entry["last_issue_hash"] = None
        entry["last_review_hash"] = None
        reply = f"Cleared cached GitHub sync hashes for `{task_id}`; it will be re-synced on the next poll."
        changed = True
    elif command.verb == "status":
        feature = coordination_feature_summary(runtime_state or {}, command.args[-1] if command.args else task_id or "")
        if feature and not target_task:
            reply = (
                f"Feature `{feature.get('feature_id')}` is `{feature.get('status')}`; "
                f"labels={','.join(feature.get('state_labels') or []) or '-'}, "
                f"worker=`{feature.get('worker_kind') or '-'}`, next={trim_text(feature.get('next_step') or '-', 120)}"
            )
        else:
            reply = task_summary_line(target_task or task or {"id": task_id or "-", "status": "unknown", "owner": "-", "reviewer": "-", "next": "-"})
    elif command.verb == "dispatch" and len(command.args) >= 2:
        worker_kind = resolve_worker_kind(command.args[0])
        feature_id = command.args[1]
        if not worker_kind:
            reply = f"Unknown worker alias `{command.args[0]}`."
        else:
            changed = queue_coordination_command(
                config,
                feature_id=feature_id,
                payload_type="dispatch-request",
                worker_kind=worker_kind,
                actor=actor,
                issue_number=issue_number,
            )
            reply = f"Queued `{worker_kind}` for `{feature_id}`." if changed else f"Did not queue `{worker_kind}` for `{feature_id}`."
    elif command.verb == "needs-runtime" and command.args:
        feature_id = command.args[0]
        changed = queue_coordination_command(
            config,
            feature_id=feature_id,
            payload_type="needs-runtime",
            worker_kind="runtime-worker",
            actor=actor,
            issue_number=issue_number,
        )
        reply = f"Queued runtime escalation for `{feature_id}`." if changed else f"Did not queue runtime escalation for `{feature_id}`."
    elif command.verb == "contract-ready" and command.args:
        feature_id = command.args[0]
        changed = queue_coordination_command(
            config,
            feature_id=feature_id,
            payload_type="contract-ready",
            worker_kind="front-sync-worker",
            actor=actor,
            issue_number=issue_number,
        )
        reply = f"Queued contract-ready replay for `{feature_id}`." if changed else f"Did not queue contract-ready replay for `{feature_id}`."
    elif command.verb == "approve-engine" and command.args:
        feature_id = command.args[0]
        changed = queue_coordination_command(
            config,
            feature_id=feature_id,
            payload_type="dispatch-request",
            worker_kind="engine-worker",
            actor=actor,
            issue_number=issue_number,
        )
        reply = f"Queued engine worker for `{feature_id}`." if changed else f"Did not queue engine worker for `{feature_id}`."
    else:
        reply = f"Unsupported or incomplete command `{command.raw}`."

    if changed:
        fallback_task_id = command.args[-1] if command.args else None
        write_activity_log(
            config,
            {
                "type": "github_issue_command_applied" if issue_number else "github_remote_command_applied",
                "task_id": task_id if target_task else (task or {}).get("id") or fallback_task_id,
                "message": f"Applied GitHub command `{command.raw}` from @{actor}.",
                "github_issue": issue_number,
            },
        )
    return changed, reply


def process_issue_command(
    config: dict[str, Any],
    bus_state: dict[str, Any],
    status: dict[str, Any],
    repo: str,
    issue_number: int,
    task: dict[str, Any],
    command: GitHubCommand,
    actor: str,
) -> bool:
    changed, reply_text = apply_bus_command(
        config,
        bus_state,
        status,
        repo,
        command,
        actor,
        task=task,
        issue_number=issue_number,
    )
    reply = f"{COMMENT_MARKER}\n{reply_text}"

    if reply:
        post_issue_comment(repo, issue_number, reply)
    return changed


def process_coordination_issue_command(
    config: dict[str, Any],
    bus_state: dict[str, Any],
    status: dict[str, Any],
    repo: str,
    issue_number: int,
    command: GitHubCommand,
    actor: str,
    runtime_state: dict[str, Any],
) -> bool:
    changed, reply_text = apply_bus_command(
        config,
        bus_state,
        status,
        repo,
        command,
        actor,
        issue_number=issue_number,
        runtime_state=runtime_state,
    )
    reply = f"{COMMENT_MARKER}\n{reply_text}"
    if reply:
        post_issue_comment(repo, issue_number, reply)
    return changed


def task_summary_line(task: dict[str, Any]) -> str:
    return (
        f"Task `{task.get('id')}` is `{task.get('status')}`; "
        f"owner=`{task.get('owner')}`, reviewer=`{task.get('reviewer')}`, next={trim_text(task.get('next') or '-', 120)}"
    )


def coordination_feature_summary(runtime_state: dict[str, Any], feature_id: str) -> dict[str, Any] | None:
    return (((runtime_state.get("coordination") or {}).get("features") or {}).get(feature_id) if runtime_state else None)


def coordination_command_payload(feature_id: str, payload_type: str, worker_kind: str | None, actor: str, issue_number: int | None) -> dict[str, Any]:
    return {
        "feature_id": feature_id,
        "type": payload_type,
        "worker_kind": worker_kind,
        "source_repo": "pantheon",
        "summary": f"GitHub coordination command from @{actor}",
        "requested_by": actor,
        "human_approved": payload_type == "dispatch-request" and worker_kind == "engine-worker",
        "dispatch_nonce": f"{actor}:{issue_number or 'relay'}:{utc_now()}",
    }


def queue_coordination_command(
    config: dict[str, Any],
    *,
    feature_id: str,
    payload_type: str,
    worker_kind: str | None,
    actor: str,
    issue_number: int | None,
) -> bool:
    payload = coordination_command_payload(feature_id, payload_type, worker_kind, actor, issue_number)
    effective_worker_kind = worker_kind
    if payload_type == "needs-runtime":
        effective_worker_kind = "runtime-worker"
    if payload_type == "dispatch-request" and effective_worker_kind is None:
        return False
    if payload_type == "needs-engine":
        effective_worker_kind = "engine-worker"
    if payload_type == "contract-ready":
        effective_worker_kind = "front-sync-worker"
        payload["type"] = "dispatch-request"
        payload["worker_kind"] = effective_worker_kind
    if payload_type == "needs-runtime":
        payload["worker_kind"] = effective_worker_kind
    if payload_type == "needs-engine":
        payload["worker_kind"] = effective_worker_kind
        payload["human_approved"] = False
    if payload_type == "dispatch-request" and effective_worker_kind == "engine-worker":
        payload["human_approved"] = True

    return queue_coordination_dispatch(
        config,
        worker_kind=effective_worker_kind or "",
        feature_id=feature_id,
        payload=payload,
        source_path=None,
        reason=payload.get("type") or "dispatch-request",
    )


def queue_resume_for_task(config: dict[str, Any], task: dict[str, Any]) -> bool:
    target_agent = task.get("owner")
    if not target_agent:
        return False
    event = {
        "key": f"github-resume:{task['id']}:{target_agent}:{utc_now()}",
        "task_id": task.get("id"),
        "target_agent": target_agent,
        "reason": "github_retry",
        "task": {
            "id": task.get("id"),
            "artifacts": task.get("artifacts") or [],
            "next": task.get("next"),
        },
    }
    message = render_wakeup_message(config, event, target_agent)
    payload = {
        "event_id": f"github-{task['id']}-{_iso_now_dt().strftime('%Y%m%dT%H%M%SZ')}",
        "created_at": utc_now(),
        "event_key": event["key"],
        "task_id": task.get("id"),
        "target_agent": agent_config_for(config, target_agent)["id"],
        "target_display_name": target_agent,
        "provider": agent_config_for(config, target_agent).get("provider", target_agent),
        "reason": "github_retry",
        "message": message,
        "context_files": execution_context_files(config, task.get("id")),
        "target_files": task.get("artifacts") or [],
        "metadata": {"task": {"id": task.get("id")}},
    }
    enqueue_event(config, payload)
    write_activity_log(
        config,
        {
            "type": "github_resume_queued",
            "task_id": task.get("id"),
            "target_agent": target_agent,
            "message": "Queued resume wake-up from GitHub approval bus.",
            "queue_event_id": payload["event_id"],
        },
    )
    return True


def queue_resume_for_agent(config: dict[str, Any], status: dict[str, Any], agent_name: str) -> bool:
    target = agent_name.strip().title()
    candidates = [
        task
        for task in status.get("tasks", [])
        if task.get("owner") == target and task.get("status") in {"todo", "in_progress", "review", "blocked"}
    ]
    if not candidates:
        return False
    prioritized = sorted(candidates, key=lambda task: (task.get("status") != "in_progress", task.get("last_update") or ""), reverse=False)
    return queue_resume_for_task(config, prioritized[0])


def poll_issue_comments(config: dict[str, Any], bus_state: dict[str, Any], status: dict[str, Any], repo: str) -> bool:
    changed = False
    seen = set(bus_state.get("processed_comment_ids", []))
    candidates = []
    for task in status.get("tasks", []):
        entry = (bus_state.get("tasks", {}) or {}).get(task["id"]) or {}
        issue_ref = entry.get("ops_issue") or {}
        number = issue_ref.get("number")
        if not number:
            continue
        candidates.append(task)

    cursors = bus_state.setdefault("poll_cursors", {})
    batch, next_cursor = _poll_batch(
        candidates,
        cursor=int(cursors.get("issue_comments", 0) or 0),
        limit=poll_batch_size(config, "issue_comments", 5),
    )
    cursors["issue_comments"] = next_cursor

    for task in batch:
        entry = (bus_state.get("tasks", {}) or {}).get(task["id"]) or {}
        issue_ref = entry.get("ops_issue") or {}
        number = issue_ref.get("number")
        if not number:
            continue
        comments = gh_json(["api", f"repos/{repo}/issues/{number}/comments?per_page=100"])
        if not isinstance(comments, list):
            continue
        allowed = allowed_logins(config, task)
        for comment in comments:
            comment_id = comment.get("id")
            if comment_id is None:
                continue
            key = comment_key("issue", comment_id)
            if key in seen:
                continue
            body = comment.get("body") or ""
            if COMMENT_MARKER in body:
                seen.add(key)
                continue
            actor = ((comment.get("user") or {}).get("login") or "").strip()
            if allowed and actor not in allowed:
                seen.add(key)
                continue
            command = parse_command(body)
            if not command:
                seen.add(key)
                continue
            process_issue_command(config, bus_state, status, repo, int(number), task, command, actor)
            seen.add(key)
            changed = True
    bus_state["processed_comment_ids"] = list(seen)
    return changed


def poll_coordination_issue_comments(
    config: dict[str, Any],
    bus_state: dict[str, Any],
    status: dict[str, Any],
    runtime_state: dict[str, Any],
) -> bool:
    changed = False
    seen = set(bus_state.get("processed_comment_ids", []))
    allowed = allowed_logins(config)
    candidates = [
        entry
        for entry in (bus_state.get("coordination") or {}).values()
        if str(entry.get("repo") or "").strip() and ((entry or {}).get("issue") or {}).get("number")
    ]
    cursors = bus_state.setdefault("poll_cursors", {})
    batch, next_cursor = _poll_batch(
        candidates,
        cursor=int(cursors.get("coordination_comments", 0) or 0),
        limit=poll_batch_size(config, "coordination_comments", 3),
    )
    cursors["coordination_comments"] = next_cursor

    for entry in batch:
        repo = str(entry.get("repo") or "").strip()
        issue_ref = (entry or {}).get("issue") or {}
        number = issue_ref.get("number")
        if not repo or not number:
            continue
        comments = gh_json(["api", f"repos/{repo}/issues/{number}/comments?per_page=100"])
        if not isinstance(comments, list):
            continue
        for comment in comments:
            comment_id = comment.get("id")
            if comment_id is None:
                continue
            key = comment_key("issue", comment_id)
            if key in seen:
                continue
            body = comment.get("body") or ""
            if COMMENT_MARKER in body:
                seen.add(key)
                continue
            actor = ((comment.get("user") or {}).get("login") or "").strip()
            if allowed and actor not in allowed:
                seen.add(key)
                continue
            command = parse_command(body)
            if not command:
                seen.add(key)
                continue
            process_coordination_issue_command(config, bus_state, status, repo, int(number), command, actor, runtime_state)
            seen.add(key)
            changed = True
    bus_state["processed_comment_ids"] = list(seen)
    return changed


def poll_pr_reviews(config: dict[str, Any], bus_state: dict[str, Any], status: dict[str, Any], repo: str) -> bool:
    changed = False
    seen = set(bus_state.get("processed_review_ids", []))
    candidates = []
    for task in status.get("tasks", []):
        entry = (bus_state.get("tasks", {}) or {}).get(task["id"]) or {}
        pr_ref = entry.get("review_pr") or {}
        number = pr_ref.get("number")
        if not number:
            continue
        candidates.append(task)

    cursors = bus_state.setdefault("poll_cursors", {})
    batch, next_cursor = _poll_batch(
        candidates,
        cursor=int(cursors.get("pr_reviews", 0) or 0),
        limit=poll_batch_size(config, "pr_reviews", 5),
    )
    cursors["pr_reviews"] = next_cursor

    for task in batch:
        entry = (bus_state.get("tasks", {}) or {}).get(task["id"]) or {}
        pr_ref = entry.get("review_pr") or {}
        number = pr_ref.get("number")
        if not number:
            continue

        reviews = gh_json(["api", f"repos/{repo}/pulls/{number}/reviews?per_page=100"])
        if isinstance(reviews, list):
            allowed = allowed_logins(config, task)
            for review in reviews:
                review_id = review.get("id")
                if review_id is None:
                    continue
                key = comment_key("review", review_id)
                if key in seen:
                    continue
                actor = ((review.get("user") or {}).get("login") or "").strip()
                if allowed and actor not in allowed:
                    seen.add(key)
                    continue
                state_value = str(review.get("state") or "").upper()
                body = trim_text(review.get("body"), 240)
                if state_value == "APPROVED":
                    run_ai_status("approve", task["id"], f"GitHub PR approved via PR #{number} by @{actor}.", actor=str(task.get("reviewer") or "").strip() or None)
                    write_activity_log(config, {"type": "github_review_approved", "task_id": task["id"], "message": f"PR #{number} approved by @{actor}.", "github_pr": number})
                    changed = True
                elif state_value == "CHANGES_REQUESTED":
                    detail = f"GitHub PR requested changes via PR #{number} by @{actor}."
                    if body:
                        detail += f" {body}"
                    run_ai_status("reopen", task["id"], detail, actor=str(task.get("reviewer") or task.get("owner") or "").strip() or None)
                    write_activity_log(config, {"type": "github_review_changes_requested", "task_id": task["id"], "message": detail, "github_pr": number})
                    changed = True
                elif state_value == "COMMENTED":
                    note = f"GitHub PR comment via PR #{number} by @{actor}."
                    if body:
                        note += f" {body}"
                    run_ai_status("note", task["id"], note, actor=str(task.get("reviewer") or task.get("owner") or "").strip() or None)
                    changed = True
                seen.add(key)
        try:
            pr_details = gh_json([
                "pr", "view", str(number), "--repo", repo,
                "--json", "statusCheckRollup,mergeStateStatus,mergeable,state,mergedAt"
            ])
            if isinstance(pr_details, dict):
                pr_ref["status_check_rollup"] = pr_details.get("statusCheckRollup")
                pr_ref["merge_state_status"] = pr_details.get("mergeStateStatus")
                pr_ref["mergeable"] = pr_details.get("mergeable")
                pr_ref["state"] = pr_details.get("state")
                pr_ref["merged_at"] = pr_details.get("mergedAt")
                pr_ref["last_status_check_at"] = utc_now()
                changed = True
        except Exception as exc:
            # Don't fail the whole sync if one PR view fails
            print(f"Warning: failed to poll PR #{number} status: {exc}", file=sys.stderr)

    bus_state["processed_review_ids"] = list(seen)
    return changed


def consume_webhook_events(
    config: dict[str, Any],
    bus_state: dict[str, Any],
    status: dict[str, Any],
    repo: str,
    runtime_state: dict[str, Any],
) -> bool:
    path = config_path(config, "github_webhook_events")
    if not path.exists():
        return False

    seen = set(bus_state.get("processed_webhook_deliveries", []))
    changed = False
    for event in load_jsonl(path):
        delivery = event.get("delivery")
        if not delivery or delivery in seen:
            continue
        kind = event.get("event")
        payload = event.get("payload") or {}
        if kind == "issue_comment":
            issue = payload.get("issue") or {}
            comment = payload.get("comment") or {}
            actor = ((comment.get("user") or {}).get("login") or "").strip()
            command = parse_command(comment.get("body") or "")
            if command:
                issue_number = issue.get("number")
                task = None
                for task_id, entry in bus_state.get("tasks", {}).items():
                    issue_ref = entry.get("ops_issue") or {}
                    if issue_ref.get("number") == issue_number:
                        task = next((item for item in status.get("tasks", []) if item.get("id") == task_id), None)
                        break
                if task and issue_number:
                    changed = process_issue_command(config, bus_state, status, repo, int(issue_number), task, command, actor) or changed
                elif issue_number:
                    for entry in (bus_state.get("coordination") or {}).values():
                        issue_ref = (entry or {}).get("issue") or {}
                        if issue_ref.get("number") != issue_number:
                            continue
                        changed = process_coordination_issue_command(
                            config,
                            bus_state,
                            status,
                            str(entry.get("repo") or repo),
                            int(issue_number),
                            command,
                            actor,
                            runtime_state,
                        ) or changed
                        break
        elif kind == "pull_request_review":
            review = payload.get("review") or {}
            pr = payload.get("pull_request") or {}
            actor = ((review.get("user") or {}).get("login") or "").strip()
            pr_number = pr.get("number")
            if pr_number:
                for task_id, entry in bus_state.get("tasks", {}).items():
                    review_ref = entry.get("review_pr") or {}
                    if review_ref.get("number") != pr_number:
                        continue
                    task = next((item for item in status.get("tasks", []) if item.get("id") == task_id), None)
                    if not task:
                        continue
                    state_value = str(review.get("state") or "").upper()
                    body = trim_text(review.get("body"), 240)
                    if state_value == "APPROVED":
                        run_ai_status("approve", task_id, f"GitHub PR approved via webhook PR #{pr_number} by @{actor}.", actor=str(task.get("reviewer") or "").strip() or None)
                        changed = True
                    elif state_value == "CHANGES_REQUESTED":
                        detail = f"GitHub PR requested changes via webhook PR #{pr_number} by @{actor}."
                        if body:
                            detail += f" {body}"
                        run_ai_status("reopen", task_id, detail, actor=str(task.get("reviewer") or task.get("owner") or "").strip() or None)
                        changed = True
                    elif state_value == "COMMENTED":
                        note = f"GitHub PR comment via webhook PR #{pr_number} by @{actor}."
                        if body:
                            note += f" {body}"
                        run_ai_status("note", task_id, note, actor=str(task.get("reviewer") or task.get("owner") or "").strip() or None)
                        changed = True
                    break
        seen.add(delivery)
    bus_state["processed_webhook_deliveries"] = list(seen)
    return changed


def push_cloud_relay_digest(config: dict[str, Any], status: dict[str, Any], runtime_state: dict[str, Any], bus_state: dict[str, Any]) -> None:
    digest = {
        "objective": status.get("objective"),
        "updated_at": status.get("updated_at"),
        "task_counts": {
            "review": sum(1 for task in status.get("tasks", []) if task.get("status") == "review"),
            "blocked": sum(1 for task in status.get("tasks", []) if task.get("status") == "blocked"),
            "in_progress": sum(1 for task in status.get("tasks", []) if task.get("status") == "in_progress"),
        },
        "worker_counts": {
            "failed": sum(1 for worker in runtime_state.get("workers", {}).values() if worker.get("status") == "failed"),
            "waiting_approval": sum(1 for worker in runtime_state.get("workers", {}).values() if worker.get("status") == "waiting_approval"),
            "stalled": sum(1 for worker in runtime_state.get("workers", {}).values() if worker.get("status") == "stalled"),
        },
        "repo": bus_state.get("repo"),
    }
    push_status_digest(config, digest)


def consume_cloud_relay_commands(
    config: dict[str, Any],
    bus_state: dict[str, Any],
    status: dict[str, Any],
    repo: str,
    runtime_state: dict[str, Any],
) -> bool:
    changed = False
    for item in pull_commands(config):
        command = parse_command(item.get("command") or "")
        if not command:
            continue
        actor = item.get("actor") or "relay"
        task_id = command.target
        task = next((entry for entry in status.get("tasks", []) if entry.get("id") == task_id), None) if task_id else None
        command_changed, _ = apply_bus_command(config, bus_state, status, repo, command, actor, task=task, issue_number=None, runtime_state=runtime_state)
        changed = command_changed or changed
    return changed


def sync_outbound(config: dict[str, Any], bus_state: dict[str, Any], status: dict[str, Any], runtime_state: dict[str, Any], repo: str) -> bool:
    changed = False
    blocked_tasks = {task.get("id"): task for task in status.get("tasks", []) if task.get("status") == "blocked"}
    review_tasks = [task for task in status.get("tasks", []) if task.get("status") == "review"]

    blocker_by_task = {item.get("task_id"): item for item in status.get("blockers", []) if item.get("status") == "open"}

    for task in review_tasks:
        try:
            changed = upsert_review_pr(config, bus_state, status, repo, task) or changed
        except GitHubBusError as exc:
            write_activity_log(
                config,
                {
                    "type": "github_review_pr_failed",
                    "task_id": task.get("id"),
                    "message": trim_text(str(exc), 600),
                    "github_repo": repo,
                },
            )

    for task_id, task in blocked_tasks.items():
        blocker = blocker_by_task.get(task_id)
        reason = blocker.get("message") if blocker else (task.get("next") or "Task is blocked")
        details = f"Waiting for: {blocker.get('waiting_for')}" if blocker else (task.get("waiting_for") or "-")
        try:
            changed = upsert_ops_issue(config, bus_state, repo, task, reason, details) or changed
        except GitHubBusError as exc:
            write_activity_log(
                config,
                {
                    "type": "github_ops_issue_failed",
                    "task_id": task.get("id"),
                    "message": trim_text(str(exc), 600),
                    "github_repo": repo,
                },
            )

    if (config.get("github_bus", {}) or {}).get("close_resolved_issues", True):
        for task_id, entry in bus_state.get("tasks", {}).items():
            if task_id in blocked_tasks:
                continue
            if entry.get("ops_issue") and entry["ops_issue"].get("state") != "closed":
                task = next((item for item in status.get("tasks", []) if item.get("id") == task_id), None)
                reason = f"Task status is now `{task.get('status')}`." if task else "Task no longer requires an ops issue."
                changed = close_ops_issue(config, entry, task_id, reason, repo) or changed

    return changed


def sync_coordination_outbound(config: dict[str, Any], bus_state: dict[str, Any], runtime_state: dict[str, Any]) -> bool:
    if not coordination_enabled(config):
        return False
    changed = False
    features = ((runtime_state.get("coordination") or {}).get("features") or {})
    for feature in features.values():
        for repo_id in feature.get("issue_repo_ids", []) or []:
            slug = repository_slug(config, repo_id)
            if not slug:
                continue
            try:
                changed = upsert_coordination_issue(config, bus_state, slug, feature) or changed
            except GitHubBusError as exc:
                write_activity_log(
                    config,
                    {
                        "type": "github_coordination_issue_failed",
                        "task_id": feature.get("feature_id"),
                        "message": trim_text(str(exc), 600),
                        "github_repo": slug,
                    },
                )
    return changed


def should_skip_for_offline_backoff(config: dict[str, Any], bus_state: dict[str, Any]) -> bool:
    offline_until = _parse_iso(bus_state.get("offline_until"))
    if not offline_until:
        return False
    return _iso_now_dt() < offline_until


def mark_offline(config: dict[str, Any], bus_state: dict[str, Any], error: str) -> None:
    backoff = int((config.get("github_bus", {}) or {}).get("offline_backoff_seconds", 300))
    bus_state["offline_until"] = (_iso_now_dt() + timedelta(seconds=backoff)).isoformat().replace("+00:00", "Z")
    if bus_state.get("last_error") != error:
        write_activity_log(config, {"type": "github_bus_offline", "message": error})
    bus_state["last_error"] = error


def sync_github_bus(config: dict[str, Any], runtime_state: dict[str, Any]) -> bool:
    bus_cfg = config.get("github_bus", {}) or {}
    if not bus_cfg.get("enabled", False):
        return False

    bus_state = load_bus_state(config)
    if should_skip_for_offline_backoff(config, bus_state):
        return False

    last_sync = _parse_iso(bus_state.get("last_sync_at"))
    interval = int(bus_cfg.get("poll_interval_seconds", 30))
    if last_sync and (_iso_now_dt() - last_sync).total_seconds() < interval:
        return False

    repo = infer_repo_slug(config, bus_state)
    if not repo:
        mark_offline(config, bus_state, "Could not infer GitHub repo slug from config or git remote.")
        save_bus_state(config, bus_state)
        return False

    status = load_status(config)
    bus_state["repo"] = repo

    try:
        changed = False
        changed = sync_outbound(config, bus_state, status, runtime_state, repo) or changed
        changed = sync_coordination_outbound(config, bus_state, runtime_state) or changed
        status = load_status(config)
        changed = consume_webhook_events(config, bus_state, status, repo, runtime_state) or changed
        status = load_status(config)
        changed = poll_pr_reviews(config, bus_state, status, repo) or changed
        status = load_status(config)
        changed = poll_issue_comments(config, bus_state, status, repo) or changed
        status = load_status(config)
        changed = poll_coordination_issue_comments(config, bus_state, status, runtime_state) or changed
        status = load_status(config)
        changed = consume_cloud_relay_commands(config, bus_state, status, repo, runtime_state) or changed
        status = load_status(config)
        push_cloud_relay_digest(config, status, runtime_state, bus_state)
        bus_state["offline_until"] = None
        bus_state["last_error"] = None
        save_bus_state(config, bus_state)
        return changed
    except GitHubBusOffline as exc:
        mark_offline(config, bus_state, str(exc))
        save_bus_state(config, bus_state)
        return False
    except Exception as exc:  # pragma: no cover - defensive bus isolation
        mark_offline(config, bus_state, f"GitHub bus error: {trim_text(str(exc), 600)}")
        save_bus_state(config, bus_state)
        return False


if __name__ == "__main__":
    raise SystemExit("Use sync_github_bus() from .orchestrator/supervisor.py")
