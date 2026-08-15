#!/usr/bin/env python3
"""Bounded GitHub review-evidence reader for the canonical task board.

The board owns task creation, assignment, blockers and retries.  This module
only consumes reviews for PRs already bound to a task, then asks the canonical
status command to apply the resulting lifecycle transition.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common import ROOT, command_exists, config_path, load_config, load_json, load_status, utc_now, write_activity_log, write_json


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
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def trim_text(value: str | None, limit: int = 400) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def default_bus_state() -> dict[str, Any]:
    return {
        "version": 2,
        "repo": None,
        "last_sync_at": None,
        "offline_until": None,
        "last_error": None,
        "processed_review_ids": [],
        "poll_cursor": 0,
        "tasks": {},
    }


def load_bus_state(config: dict[str, Any]) -> dict[str, Any]:
    state = load_json(config_path(config, "github_bus_state"), default={}) or {}
    merged = default_bus_state()
    if isinstance(state, dict):
        for key in (
            "repo",
            "last_sync_at",
            "offline_until",
            "last_error",
            "processed_review_ids",
            "poll_cursor",
            "tasks",
        ):
            if key in state:
                merged[key] = state[key]
    tasks = merged["tasks"]
    merged["tasks"] = {
        str(task_id): {"review_pr": dict(entry["review_pr"])}
        for task_id, entry in (tasks or {}).items()
        if isinstance(entry, dict) and isinstance(entry.get("review_pr"), dict)
    }
    merged["processed_review_ids"] = list(merged.get("processed_review_ids") or [])
    try:
        merged["poll_cursor"] = max(0, int(merged.get("poll_cursor") or 0))
    except (TypeError, ValueError):
        merged["poll_cursor"] = 0
    return merged


def save_bus_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    state["version"] = 2
    state["last_sync_at"] = utc_now()
    state["processed_review_ids"] = list(state.get("processed_review_ids") or [])[-MAX_PROCESSED_IDS:]
    tasks = state.get("tasks") or {}
    state["tasks"] = {
        str(task_id): {"review_pr": dict(entry["review_pr"])}
        for task_id, entry in tasks.items()
        if isinstance(entry, dict) and isinstance(entry.get("review_pr"), dict)
    }
    write_json(config_path(config, "github_bus_state"), state)


def poll_batch_size(config: dict[str, Any]) -> int:
    try:
        return max(1, int((config.get("github_bus") or {}).get("review_batch_size", 5)))
    except (TypeError, ValueError):
        return 5


def _poll_batch(items: list[Any], *, cursor: int, limit: int) -> tuple[list[Any], int]:
    if not items:
        return [], 0
    cursor = cursor if 0 <= cursor < len(items) else 0
    end = min(cursor + limit, len(items))
    return items[cursor:end], 0 if end >= len(items) else end


def infer_repo_slug(config: dict[str, Any], bus_state: dict[str, Any]) -> str | None:
    configured = str((config.get("github_bus") or {}).get("repo") or "").strip()
    return configured or str(bus_state.get("repo") or "").strip() or None


def run_bounded_process(command: list[str], *, timeout_seconds: float, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryFile() as stdout_handle, tempfile.TemporaryFile() as stderr_handle:
        process = subprocess.Popen(command, cwd=str(cwd), stdout=stdout_handle, stderr=stderr_handle, start_new_session=True)
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=0.2)
            raise exc
        stdout_handle.seek(0)
        stderr_handle.seek(0)
        return subprocess.CompletedProcess(
            command,
            process.returncode or 0,
            stdout_handle.read().decode("utf-8", errors="replace"),
            stderr_handle.read().decode("utf-8", errors="replace"),
        )


def run_gh_process(args: list[str], *, timeout_seconds: float, gh_binary: str | None = None) -> subprocess.CompletedProcess[str]:
    binary = gh_binary or resolve_gh_binary() or "gh"
    return run_bounded_process([binary, *args], timeout_seconds=timeout_seconds)


def run_gh(args: list[str], *, allow_offline: bool = True) -> subprocess.CompletedProcess[str]:
    binary = resolve_gh_binary()
    if not binary:
        raise GitHubBusError("GitHub CLI `gh` is not installed")
    try:
        timeout = float((load_config().get("github_bus") or {}).get("command_timeout_seconds", 8))
    except Exception:
        timeout = 8.0
    try:
        proc = run_gh_process(args, timeout_seconds=timeout, gh_binary=binary)
    except subprocess.TimeoutExpired as exc:
        message = f"GitHub CLI timed out after {int(timeout)}s"
        if allow_offline:
            raise GitHubBusOffline(message) from exc
        raise GitHubBusError(message) from exc
    if proc.returncode == 0:
        return proc
    message = trim_text(f"{proc.stdout}\n{proc.stderr}", 600)
    if allow_offline and any(token in message.lower() for token in ("connecting to api.github.com", "dial tcp", "no such host")):
        raise GitHubBusOffline(message)
    raise GitHubBusError(message)


def gh_json(args: list[str]) -> Any:
    output = run_gh(args).stdout.strip()
    return json.loads(output) if output else None


def allowed_logins(config: dict[str, Any], task: dict[str, Any]) -> set[str]:
    reviewers = (config.get("github_bus") or {}).get("reviewers") or {}
    return {str(value).strip() for value in reviewers.get(task.get("reviewer"), []) if str(value).strip()}


def _review_key(review_id: int | str) -> str:
    return f"review:{review_id}"


def poll_pr_reviews(config: dict[str, Any], bus_state: dict[str, Any], status: dict[str, Any], repo: str) -> bool:
    by_id = {str(task.get("id") or ""): task for task in status.get("tasks") or [] if str(task.get("id") or "")}
    candidates = [
        (task_id, entry["review_pr"])
        for task_id, entry in (bus_state.get("tasks") or {}).items()
        if task_id in by_id and isinstance(entry, dict) and isinstance(entry.get("review_pr"), dict)
        and entry["review_pr"].get("number")
    ]
    batch, cursor = _poll_batch(candidates, cursor=int(bus_state.get("poll_cursor") or 0), limit=poll_batch_size(config))
    bus_state["poll_cursor"] = cursor
    seen = set(bus_state.get("processed_review_ids") or [])
    changed = False
    for task_id, reference in batch:
        task = by_id[task_id]
        number = int(reference["number"])
        reviews = gh_json(["api", f"repos/{repo}/pulls/{number}/reviews?per_page=100"])
        if not isinstance(reviews, list):
            continue
        allowed = allowed_logins(config, task)
        for review in reviews:
            review_id = review.get("id")
            if review_id is None or _review_key(review_id) in seen:
                continue
            seen.add(_review_key(review_id))
            actor = str((review.get("user") or {}).get("login") or "").strip()
            if allowed and actor not in allowed:
                continue
            review_state = str(review.get("state") or "").upper()
            message = trim_text(review.get("body"), 240)
            if review_state not in {"APPROVED", "CHANGES_REQUESTED"}:
                continue
            detail = f"GitHub PR #{number} {review_state.lower()} by @{actor}."
            write_activity_log(
                config,
                {
                    "type": "github_review_observed",
                    "task_id": task_id,
                    "message": detail + (f" {message}" if message else ""),
                    "github_pr": number,
                    "github_review_id": review_id,
                    "github_review_state": review_state,
                },
            )
            # GitHub supplies evidence; it is not a reviewer lease.  The
            # assigned reviewer or local Human/Ops command applies the one
            # canonical lifecycle transition after exact-head verification.
            if task.get("status") == "review":
                changed = True
    bus_state["processed_review_ids"] = list(seen)
    return changed


def should_skip_for_offline_backoff(config: dict[str, Any], bus_state: dict[str, Any]) -> bool:
    del config
    until = _parse_iso(str(bus_state.get("offline_until") or ""))
    return until is not None and _iso_now_dt() < until


def sync_github_bus(config: dict[str, Any], runtime_state: dict[str, Any]) -> bool:
    del runtime_state
    settings = config.get("github_bus") or {}
    if not settings.get("enabled", False):
        return False
    state = load_bus_state(config)
    if should_skip_for_offline_backoff(config, state):
        return False
    last_sync = _parse_iso(str(state.get("last_sync_at") or ""))
    interval = max(1, int(settings.get("poll_interval_seconds", 30)))
    if last_sync and (_iso_now_dt() - last_sync).total_seconds() < interval:
        return False
    repo = infer_repo_slug(config, state)
    if not repo:
        raise GitHubBusError("github_bus.repo is required")
    try:
        changed = poll_pr_reviews(config, state, load_status(config), repo)
        state["repo"] = repo
        state["offline_until"] = None
        state["last_error"] = None
    except GitHubBusOffline as exc:
        state["offline_until"] = (_iso_now_dt() + timedelta(seconds=int(settings.get("offline_backoff_seconds", 300)))).isoformat().replace("+00:00", "Z")
        state["last_error"] = str(exc)
        changed = False
    save_bus_state(config, state)
    return changed
