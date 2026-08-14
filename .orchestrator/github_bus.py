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
    command_exists,
    config_path,
    load_config,
    load_json,
    load_status,
    render_template,
    run_command,
    utc_now,
    write_activity_log,
    write_json,
)
from cross_repo_issue_mapper import coordination_issue_body, coordination_issue_labels, coordination_issue_title
from github_command_parser import GitHubCommand, parse_command
from multi_repo_registry import (
    coordination_enabled,
    matching_repo_id,
    repository_slug,
    resolve_repository,
)

COMMENT_MARKER = "<!-- pantheon-bus -->"
MAX_PROCESSED_IDS = 2000
REMOTE_BRANCH_LOOKUP_TIMEOUT_SECONDS = 8.0


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
    merged.setdefault("poll_cursors", {})
    merged["poll_cursors"].setdefault("pr_reviews", 0)
    merged["poll_cursors"].setdefault("issue_comments", 0)
    merged.pop("processed_webhook_deliveries", None)
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
    state.pop("processed_webhook_deliveries", None)
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


def delivery_base_branch(config: dict[str, Any], repo: str) -> str:
    """Resolve the integration branch that receives task delivery PRs.

    ``github_bus.default_branch`` predates the per-task branch workflow and may
    still name the production branch. ReviewBus must not use that legacy value
    as a PR base because doing so turns an integrated ``dev`` task into a broad
    promotion diff.
    """

    bus_cfg = config.get("github_bus", {}) or {}
    repo_id = matching_repo_id(config, repo)
    overrides = bus_cfg.get("delivery_base_branches") or {}
    if isinstance(overrides, dict):
        for key in (repo, repo_id):
            value = overrides.get(key) if key else None
            if str(value or "").strip():
                return str(value).strip()

    explicit = str(bus_cfg.get("delivery_base_branch") or "").strip()
    if explicit:
        return explicit

    # The primary Pantheon bus follows the same configured delivery branch as
    # task_finalize.sh. This remains authoritative even when the legacy
    # github_bus.default_branch value still says ``master``.
    workflow_base = str((config.get("branch_workflow") or {}).get("dev_branch") or "").strip()
    if workflow_base and (repo_id in {None, "pantheon"}):
        return workflow_base

    if repo_id and repo_id != "pantheon":
        repo_config = resolve_repository(config, repo_id)
        repository_base = str(
            repo_config.get("delivery_branch") or repo_config.get("default_branch") or ""
        ).strip()
        if repository_base:
            return repository_base

    legacy = str(bus_cfg.get("default_branch") or "").strip()
    legacy_note = f" Legacy github_bus.default_branch is `{legacy}` and is not a delivery-base authority." if legacy else ""
    raise GitHubBusError(
        f"No ReviewBus delivery base is configured for `{repo}`. Set "
        "github_bus.delivery_base_branches or branch_workflow.dev_branch."
        f"{legacy_note}"
    )


def branch_head_sha(branch: str) -> str | None:
    proc = run_command(["git", "rev-parse", branch], cwd=ROOT)
    if proc.returncode != 0:
        return None
    sha = (proc.stdout or '').strip()
    return sha or None


def remote_branch_head_sha(branch: str, remote: str = "origin") -> str | None:
    """Return the remote branch head, or ``None`` when it cannot be bounded.

    This lookup runs in routine reconciliation.  A wedged remote helper must
    not prevent the supervisor from finalizing its scheduling cycle.
    """

    try:
        proc = run_bounded_process(
            ["git", "ls-remote", "--heads", remote, branch],
            timeout_seconds=REMOTE_BRANCH_LOOKUP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    expected_ref = f"refs/heads/{branch}"
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == expected_ref:
            sha = parts[0].strip()
            return sha or None
    return None


def branch_has_diff(base: str, branch: str) -> bool:
    proc = run_command(["git", "rev-list", "--count", f"{base}..{branch}"], cwd=ROOT)
    if proc.returncode != 0:
        return False
    try:
        return int((proc.stdout or '0').strip() or '0') > 0
    except ValueError:
        return False


def run_bounded_process(
    command: list[str],
    *,
    timeout_seconds: float,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    """Run a network-capable helper without allowing teardown to stall a cycle."""

    with tempfile.TemporaryFile() as stdout_handle, tempfile.TemporaryFile() as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
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
        return subprocess.CompletedProcess(command, process.returncode or 0, stdout, stderr)


def run_gh_process(
    args: list[str],
    *,
    timeout_seconds: float,
    gh_binary: str | None = None,
) -> subprocess.CompletedProcess[str]:
    binary = gh_binary or resolve_gh_binary() or "gh"
    return run_bounded_process(
        [binary, *args],
        timeout_seconds=timeout_seconds,
    )


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
    del status
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return None
    prefix = str((config.get("branch_workflow") or {}).get("task_branch_prefix") or "task/")
    expected = f"{prefix}{task_id}"
    meta = task.get("github") or {}
    explicit = str(meta.get("head_branch") or "").strip()
    if explicit and explicit != expected:
        raise GitHubBusError(
            f"ReviewBus task `{task_id}` declares head branch `{explicit}`, but the "
            f"configured exact task branch is `{expected}`. Refusing a broad or cross-task review."
        )
    return expected


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


def find_task_pr_candidates(repo: str, branch: str) -> list[dict[str, Any]]:
    data = gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--head",
            branch,
            "--limit",
            "100",
            "--json",
            "number,title,url,state,isDraft,headRefName,headRefOid,baseRefName,mergedAt,mergeCommit,createdAt,closedAt",
        ]
    )
    if not isinstance(data, list):
        return []
    return [
        item
        for item in data
        if isinstance(item, dict) and str(item.get("headRefName") or "") == branch
    ]


def _pr_merge_commit(pr: dict[str, Any]) -> str | None:
    merge_commit = pr.get("mergeCommit")
    if not isinstance(merge_commit, dict):
        return None
    value = str(merge_commit.get("oid") or "").strip()
    return value or None


def _select_task_pr_evidence(
    candidates: list[dict[str, Any]],
    *,
    task_id: str,
    branch: str,
    base: str,
    head_sha: str | None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    scoped = [item for item in candidates if str(item.get("headRefName") or "") == branch]
    if head_sha:
        matching_head = [
            item for item in scoped if str(item.get("headRefOid") or "") == head_sha
        ]
        if scoped and not matching_head:
            details = ", ".join(
                f"#{item.get('number')} @ {item.get('headRefOid') or '(missing)'}"
                for item in scoped
            )
            return None, "skipped_head_mismatch", (
                f"ReviewBus found task PR evidence for `{branch}`, but none matches exact "
                f"task head `{head_sha}` ({details}). Refusing stale or cross-commit review evidence."
            )
        scoped = matching_head

    matching_base = [item for item in scoped if str(item.get("baseRefName") or "") == base]
    if not head_sha:
        candidate_heads = {
            str(item.get("headRefOid") or "").strip()
            for item in matching_base
            if str(item.get("headRefOid") or "").strip()
        }
        if len(candidate_heads) > 1:
            raise GitHubBusError(
                f"ReviewBus found multiple commits for `{branch}` -> `{base}` while resolving "
                f"`{task_id}` and has no exact published/task head SHA. Preserve explicit task "
                "commit evidence before requesting review."
            )
        missing_head = [item for item in matching_base if not str(item.get("headRefOid") or "").strip()]
        if missing_head:
            numbers = ", ".join(f"#{item.get('number')}" for item in missing_head)
            return None, "skipped_no_head_sha", (
                f"ReviewBus found task PR evidence ({numbers}) for `{branch}` -> `{base}` "
                "without an exact head SHA. Refusing unscoped review evidence."
            )

    incomplete_merged = [
        item
        for item in matching_base
        if str(item.get("state") or "").upper() == "MERGED"
        and (not item.get("mergedAt") or not _pr_merge_commit(item))
    ]
    if incomplete_merged:
        numbers = ", ".join(f"#{item.get('number')}" for item in incomplete_merged)
        return None, "skipped_incomplete_merge_evidence", (
            f"ReviewBus found merged task PR evidence ({numbers}) for `{branch}` -> `{base}` "
            "without both merge time and merge commit. Refusing incomplete delivery evidence."
        )

    merged = [
        item
        for item in matching_base
        if str(item.get("state") or "").upper() == "MERGED"
        and item.get("mergedAt")
        and _pr_merge_commit(item)
    ]
    if len(merged) == 1:
        return merged[0], None, None
    if len(merged) > 1:
        numbers = ", ".join(f"#{item.get('number')}" for item in merged)
        raise GitHubBusError(
            f"ReviewBus found multiple merged PRs ({numbers}) for exact task commit "
            f"`{head_sha or '(unknown)'}` on `{branch}` -> `{base}`. Refusing ambiguous review evidence."
        )

    open_prs = [item for item in matching_base if str(item.get("state") or "").upper() == "OPEN"]
    if len(open_prs) == 1:
        return open_prs[0], None, None
    if len(open_prs) > 1:
        numbers = ", ".join(f"#{item.get('number')}" for item in open_prs)
        raise GitHubBusError(
            f"ReviewBus found multiple open PRs ({numbers}) for `{branch}` -> `{base}`. "
            "Refusing ambiguous review scope."
        )

    mismatched = [item for item in scoped if str(item.get("baseRefName") or "") != base]
    if mismatched:
        details = ", ".join(
            f"#{item.get('number')} -> {item.get('baseRefName') or '(missing)'}"
            for item in mismatched
        )
        return None, "skipped_base_mismatch", (
            f"ReviewBus found exact task branch evidence on the wrong base ({details}); "
            f"expected delivery base `{base}`. Refusing to create a synthetic integration PR."
        )

    closed = [
        item
        for item in matching_base
        if str(item.get("state") or "").upper() == "CLOSED"
    ]
    if closed:
        numbers = ", ".join(f"#{item.get('number')}" for item in closed)
        return None, "skipped_closed_pr", (
            f"ReviewBus found closed, unmerged task PR evidence ({numbers}) for "
            f"`{branch}` -> `{base}`. Refusing to replace it with a synthetic PR."
        )
    return None, None, None


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

    base = delivery_base_branch(config, repo)
    title = f"[ReviewBus] {task['id']} {task['title']}"
    task_github = task.get("github") or {}
    explicit_head_sha = str(task_github.get("head_sha") or "").strip() or None
    local_head_sha = branch_head_sha(branch)
    prior_head_sha = None
    if isinstance(pr_ref, dict) and pr_ref.get("branch") == branch:
        prior_head_sha = str(pr_ref.get("head_sha") or "").strip() or None
    candidates = find_task_pr_candidates(repo, branch)
    matching_candidate_heads = {
        str(item.get("headRefOid") or "").strip()
        for item in candidates
        if str(item.get("baseRefName") or "") == base
        and str(item.get("headRefOid") or "").strip()
    }

    preliminary_head_sha = explicit_head_sha or prior_head_sha or local_head_sha
    preliminary_skip_hash = json.dumps(
        {
            "state": "skipped_unpublished_branch",
            "task_id": task["id"],
            "branch": branch,
            "base": base,
            "head_sha": preliminary_head_sha,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    previous_unpublished = (
        not candidates
        and isinstance(pr_ref, dict)
        and pr_ref.get("state") == "skipped_unpublished_branch"
        and pr_ref.get("branch") == branch
        and pr_ref.get("head_sha") == preliminary_head_sha
        and entry.get("last_review_hash") == preliminary_skip_hash
    )
    if previous_unpublished:
        last_check = _parse_iso(str(pr_ref.get("last_remote_branch_check_at") or ""))
        if last_check and (_iso_now_dt() - last_check).total_seconds() < unpublished_branch_recheck_seconds(config):
            return False

    published_head_sha = remote_branch_head_sha(branch) if not explicit_head_sha else None
    if explicit_head_sha:
        head_sha = explicit_head_sha
    elif published_head_sha:
        # A task branch can carry more than one merged PR when a follow-up is
        # required. Its published head disambiguates the current immutable
        # delivery from older PRs for the same exact task branch.
        head_sha = published_head_sha
    elif len(matching_candidate_heads) == 1:
        # A task integrator may update the remote PR branch after the worker's
        # local ref was created. The unique PR head on the configured delivery
        # base is the immutable review identity in that case.
        head_sha = next(iter(matching_candidate_heads))
    elif candidates:
        # Let the selector diagnose multiple heads or base mismatch without a
        # stale local ref silently filtering the relevant PR evidence.
        head_sha = None
    else:
        head_sha = published_head_sha or prior_head_sha or local_head_sha

    if (
        isinstance(pr_ref, dict)
        and pr_ref.get("evidence_kind") == "merged_task_pr"
        and pr_ref.get("branch") == branch
        and pr_ref.get("base_branch") == base
        and pr_ref.get("head_sha")
        and pr_ref.get("merge_commit")
        and (not head_sha or pr_ref.get("head_sha") == head_sha)
    ):
        return False

    selected_pr, diagnostic_state, diagnostic = _select_task_pr_evidence(
        candidates,
        task_id=task["id"],
        branch=branch,
        base=base,
        head_sha=head_sha,
    )
    if selected_pr:
        selected_state = str(selected_pr.get("state") or "").upper()
        selected_head = str(selected_pr.get("headRefOid") or "").strip() or head_sha
        merge_commit = _pr_merge_commit(selected_pr)
        evidence_kind = "merged_task_pr" if selected_state == "MERGED" else "open_task_pr"
        review_hash = json.dumps(
            {
                "state": selected_state,
                "task_id": task["id"],
                "branch": branch,
                "base": base,
                "head_sha": selected_head,
                "pr_number": selected_pr.get("number"),
                "merge_commit": merge_commit,
                "merged_at": selected_pr.get("mergedAt"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if entry.get("last_review_hash") == review_hash and pr_ref:
            return False
        entry["review_pr"] = {
            "number": selected_pr.get("number"),
            "url": selected_pr.get("url"),
            "title": selected_pr.get("title") or title,
            "branch": branch,
            "base_branch": base,
            "state": selected_state.lower(),
            "head_sha": selected_head,
            "merge_commit": merge_commit,
            "merged_at": selected_pr.get("mergedAt"),
            "evidence_kind": evidence_kind,
            "last_remote_branch_check_at": utc_now(),
        }
        entry["last_review_hash"] = review_hash
        if evidence_kind == "merged_task_pr":
            message = (
                f"ReviewBus bound {task['id']} to merged PR #{selected_pr.get('number')} "
                f"for exact task commit `{selected_head}` and merge commit `{merge_commit}` "
                f"on `{base}`."
            )
        else:
            message = (
                f"ReviewBus bound {task['id']} to open task PR #{selected_pr.get('number')} "
                f"for exact task commit `{selected_head}` on `{base}`."
            )
        write_activity_log(
            config,
            {
                "type": "github_review_pr_bound",
                "task_id": task["id"],
                "message": message,
                "github_url": entry["review_pr"].get("url"),
                "head_sha": selected_head,
                "merge_commit": merge_commit,
                "base_branch": base,
            },
        )
        return True

    if diagnostic_state and diagnostic:
        candidate_evidence = [
            {
                "number": item.get("number"),
                "url": item.get("url"),
                "state": str(item.get("state") or "").lower(),
                "branch": item.get("headRefName"),
                "base_branch": item.get("baseRefName"),
                "head_sha": item.get("headRefOid"),
                "merge_commit": _pr_merge_commit(item),
            }
            for item in candidates
            if diagnostic_state == "skipped_head_mismatch"
            or not head_sha
            or str(item.get("headRefOid") or "") == head_sha
        ]
        diagnostic_hash = json.dumps(
            {
                "state": diagnostic_state,
                "task_id": task["id"],
                "branch": branch,
                "base": base,
                "head_sha": head_sha,
                "candidates": candidate_evidence,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if entry.get("last_review_hash") == diagnostic_hash and pr_ref:
            return False
        entry["review_pr"] = {
            "number": None,
            "url": None,
            "title": title,
            "branch": branch,
            "base_branch": base,
            "state": diagnostic_state,
            "head_sha": head_sha,
            "evidence_kind": "fail_closed",
            "diagnostic": diagnostic,
            "candidates": candidate_evidence,
        }
        entry["last_review_hash"] = diagnostic_hash
        write_activity_log(
            config,
            {
                "type": "github_review_pr_skipped",
                "task_id": task["id"],
                "message": diagnostic,
                "base_branch": base,
                "head_sha": head_sha,
            },
        )
        return True

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
    if not published_head_sha:
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

    if not head_sha:
        diagnostic = (
            f"ReviewBus found remote task branch `{branch}` but could not bind an exact head SHA. "
            "Refusing to create an unscoped review PR."
        )
        no_sha_hash = json.dumps(
            {
                "state": "skipped_no_head_sha",
                "task_id": task["id"],
                "branch": branch,
                "base": base,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if entry.get("last_review_hash") == no_sha_hash and pr_ref:
            return False
        entry["review_pr"] = {
            "number": None,
            "url": None,
            "title": title,
            "branch": branch,
            "base_branch": base,
            "state": "skipped_no_head_sha",
            "head_sha": None,
            "evidence_kind": "fail_closed",
            "diagnostic": diagnostic,
        }
        entry["last_review_hash"] = no_sha_hash
        write_activity_log(
            config,
            {
                "type": "github_review_pr_skipped",
                "task_id": task["id"],
                "message": diagnostic,
                "base_branch": base,
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

    if not branch_has_diff(f"origin/{base}", branch):
        entry["review_pr"] = {
            "number": (pr_ref or {}).get("number"),
            "url": (pr_ref or {}).get("url"),
            "title": title,
            "branch": branch,
            "base_branch": base,
            "state": "skipped_no_commits",
            "head_sha": head_sha,
            "evidence_kind": "fail_closed",
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
        "base_branch": base,
        "state": "open",
        "head_sha": head_sha,
        "evidence_kind": "created_review_pr",
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
            actor="Human/Ops",
        )
        reply = f"Reopened `{task_id}`; the canonical planner will redispatch it."
        changed = True
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


# Task states that never regain a task branch worth polling for a PR.
_PR_RECONCILIATION_TERMINAL_STATUSES = frozenset({"done", "archived", "superseded"})

# PR evidence kinds that mean "already resolved"; no need to re-probe the
# remote branch every tick once one of these is recorded.
_PR_RECONCILIATION_RESOLVED_EVIDENCE = frozenset({"merged_task_pr", "open_task_pr"})


def _pr_reconciliation_candidates(
    config: dict[str, Any],
    bus_state: dict[str, Any],
    status: dict[str, Any],
) -> list[dict[str, Any]]:
    """Tasks eligible for a PR upsert attempt this sync tick.

    PR creation must be an idempotent, continuously reconciled invariant
    ("does a branch with a diff exist -> does a PR exist for it") rather
    than a one-shot side effect of a single status transition. A task whose
    status is literally `"review"` right now is always eligible (existing
    fast path, cheapest check). Any other non-terminal task is *also*
    eligible when it already carries a task branch on the remote but no
    resolved PR evidence yet -- this is what prevents a task that leaves
    `"review"` before a PR is opened (reassignment, a blocked dependency, a
    crashed closeout worker) from being permanently PR-less. See
    SUP-REVIEW-PIPELINE-INTEGRITY-20260804.
    """
    candidates: list[dict[str, Any]] = []
    for task in status.get("tasks", []):
        task_status = task.get("status")
        if task_status == "review":
            candidates.append(task)
            continue
        if task_status in _PR_RECONCILIATION_TERMINAL_STATUSES:
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        entry = task_bus_entry(bus_state, task_id)
        pr_ref = entry.get("review_pr") or {}
        if pr_ref.get("evidence_kind") in _PR_RECONCILIATION_RESOLVED_EVIDENCE:
            continue
        try:
            branch = review_branch_for_task(config, status, task)
        except GitHubBusError:
            continue
        if not branch:
            continue
        if remote_branch_head_sha(branch) is None:
            continue
        candidates.append(task)
    return candidates


def sync_outbound(config: dict[str, Any], bus_state: dict[str, Any], status: dict[str, Any], runtime_state: dict[str, Any], repo: str) -> bool:
    changed = False
    blocked_tasks = {task.get("id"): task for task in status.get("tasks", []) if task.get("status") == "blocked"}
    review_tasks = _pr_reconciliation_candidates(config, bus_state, status)

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
        changed = poll_pr_reviews(config, bus_state, status, repo) or changed
        status = load_status(config)
        changed = poll_issue_comments(config, bus_state, status, repo) or changed
        status = load_status(config)
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
