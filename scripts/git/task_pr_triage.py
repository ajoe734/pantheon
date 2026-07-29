#!/usr/bin/env python3
"""Evidence-based triage for stale Pantheon task PRs and task branches.

The tool is deliberately asymmetric:

* it may inventory and classify every ``task/*`` branch;
* it emits branch deletion candidates as a dry-run manifest only;
* it never deletes a branch;
* PR closure candidates require durable supersession or merged-replacement
  evidence, and applying those closures requires an explicit ``--only`` list.

Live collection joins GitHub PR history, local remote refs, ``origin/dev``
reachability, canonical active task state, and terminal task archives.  The
classification functions are kept side-effect free so fixture tests can prove
the fail-closed rules without GitHub access.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPOSITORY = "ajoe734/pantheon"
ALLOWED_DISPOSITIONS = {
    "active-repair",
    "conflict-needs-owner",
    "merged-reachable",
    "superseded",
    "abandoned-unproven",
    "protected-retain",
}
TERMINAL_STATUSES = {"done", "superseded", "cancelled"}
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TRAILER_RE = re.compile(r"^(LLM-Agent|Task-ID|Reviewer):\s*(.+?)\s*$", re.MULTILINE)
FULL_PR_URL_RE = re.compile(
    r"https://github\.com/(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(?P<number>\d+)"
)
NAMED_PR_RE = re.compile(
    r"(?:(?P<repository>execute-plans|Pantheon)\s+)?PRs?\s+#(?P<number>\d+)",
    re.IGNORECASE,
)
SUPERSEDED_BY_RE = re.compile(r"superseded\s+by\s+#(?P<number>\d+)", re.IGNORECASE)


class TriageError(RuntimeError):
    """Raised when evidence is incomplete or violates a safety invariant."""


def _run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "no command output"
        raise TriageError(f"command failed ({' '.join(command)}): {detail}")
    return proc


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _snapshot_time(value: datetime) -> datetime:
    """Normalize the captured time to the precision published in the report."""

    return value.astimezone(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return _snapshot_time(value).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise TriageError(f"cannot read valid JSON from {path}: {exc}") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _normalize_task_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if not TASK_ID_RE.fullmatch(normalized):
        return None
    return normalized.casefold()


def _task_candidates(pr: dict[str, Any], trailers: dict[str, str]) -> list[str]:
    raw: list[str | None] = [trailers.get("Task-ID")]
    head = str(pr.get("head_ref") or pr.get("headRefName") or "")
    if head.startswith("task/"):
        raw.append(head.removeprefix("task/"))
    title_prefix = str(pr.get("title") or "").split(":", 1)[0].strip()
    if title_prefix.lower() not in {"publish", "merge", "revert"}:
        raw.append(title_prefix)
    result: list[str] = []
    for item in raw:
        normalized = _normalize_task_id(item)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def parse_commit_trailers(messages: Iterable[str]) -> dict[str, str]:
    """Return the first observed value for each ownership trailer."""

    trailers: dict[str, str] = {}
    for message in messages:
        for key, value in TRAILER_RE.findall(message):
            trailers.setdefault(key, value.strip())
        if "Task-ID" in trailers and "LLM-Agent" in trailers:
            break
    return trailers


def git_commit_trailers(commit: str, base_sha: str) -> dict[str, str]:
    proc = _run(
        ["git", "log", "-20", "--format=%B%x00", commit, "--not", base_sha],
        check=False,
    )
    messages = [part for part in proc.stdout.split("\0") if part.strip()]
    if not messages:
        fallback = _run(
            ["git", "log", "-20", "--format=%B%x00", commit], check=False
        )
        messages = [part for part in fallback.stdout.split("\0") if part.strip()]
    return parse_commit_trailers(messages)


def load_task_state(status_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    state_path = status_root / "ai-status.json"
    state = _load_json(state_path)
    active: dict[str, dict[str, Any]] = {}
    for task in state.get("tasks", []):
        task_id = _normalize_task_id(task.get("id"))
        if task_id:
            active[task_id] = task

    archives: dict[str, dict[str, Any]] = {}
    archive_root = status_root / "ai-task-archive" / "tasks"
    if archive_root.exists():
        for path in sorted(archive_root.glob("*.json")):
            try:
                record = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            task = record.get("task") or {}
            task_id = _normalize_task_id(record.get("task_id") or task.get("id"))
            if not task_id:
                continue
            record = dict(record)
            record["_path"] = str(path.relative_to(status_root))
            archives[task_id] = record
    return active, archives


def _archive_summary(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    task = record.get("task") or {}
    delivery = task.get("delivery") or {}
    return {
        "path": record.get("_path"),
        "task_id": record.get("task_id") or task.get("id"),
        "terminal_status": record.get("terminal_status") or task.get("status"),
        "terminal_outcome": record.get("terminal_outcome")
        or task.get("terminal_outcome"),
        "archived_at": record.get("archived_at"),
        "owner": task.get("owner"),
        "reviewer": task.get("reviewer"),
        "next": task.get("next"),
        "helper_parent": task.get("helper_parent"),
        "superseded_by": task.get("superseded_by"),
        "source_ref": task.get("source_ref"),
        "delivery": {
            key: delivery.get(key)
            for key in (
                "repository_slug",
                "branch",
                "commit",
                "merge_target_sha",
                "head_merged_to_target",
            )
            if delivery.get(key) is not None
        },
    }


def _find_task_record(
    candidates: Iterable[str],
    records: dict[str, dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    for candidate in candidates:
        if candidate in records:
            return candidate, records[candidate]
    return None, None


def extract_pr_references(text: str, default_repository: str) -> list[dict[str, Any]]:
    """Extract explicit PR references while preserving repository identity."""

    references: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for match in FULL_PR_URL_RE.finditer(text):
        references.append(
            {
                "repository": match.group("repository"),
                "number": int(match.group("number")),
                "source": match.group(0),
            }
        )
        occupied.append(match.span())

    for match in NAMED_PR_RE.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        named_repo = (match.group("repository") or "").lower()
        if named_repo == "execute-plans":
            repository = "ajoe734/execute-plans"
        elif named_repo == "pantheon":
            repository = DEFAULT_REPOSITORY
        else:
            repository = default_repository
        references.append(
            {
                "repository": repository,
                "number": int(match.group("number")),
                "source": match.group(0),
            }
        )

    for match in SUPERSEDED_BY_RE.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        references.append(
            {
                "repository": default_repository,
                "number": int(match.group("number")),
                "source": match.group(0),
            }
        )

    unique: dict[tuple[str, int], dict[str, Any]] = {}
    for reference in references:
        unique[(reference["repository"], reference["number"])] = reference
    return [unique[key] for key in sorted(unique)]


def _archive_evidence_text(
    archive: dict[str, Any] | None,
    archives: dict[str, dict[str, Any]],
) -> str:
    if not archive:
        return ""
    related = [archive]
    task = archive.get("task") or {}
    for field in ("helper_parent", "superseded_by"):
        related_id = _normalize_task_id(task.get(field))
        if related_id and related_id in archives:
            related.append(archives[related_id])
    return "\n".join(json.dumps(item, sort_keys=True) for item in related)


def collect_pr_history(repository: str) -> list[dict[str, Any]]:
    endpoint = (
        f"repos/{repository}/pulls?state=all&base=dev&per_page=100"
        "&sort=updated&direction=desc"
    )
    jq_filter = (
        ".[] | {number,state,title,html_url,created_at,updated_at,closed_at,"
        "merged_at,draft,merge_commit_sha,head:{ref:.head.ref,sha:.head.sha},"
        "base:{ref:.base.ref},user:.user.login} | @json"
    )
    proc = _run(["gh", "api", "--paginate", endpoint, "--jq", jq_filter])
    records: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        records.append(
            {
                "number": int(raw["number"]),
                "state": "MERGED" if raw.get("merged_at") else str(raw["state"]).upper(),
                "title": raw.get("title"),
                "url": raw.get("html_url"),
                "created_at": raw.get("created_at"),
                "updated_at": raw.get("updated_at"),
                "closed_at": raw.get("closed_at"),
                "merged_at": raw.get("merged_at"),
                "draft": bool(raw.get("draft")),
                "merge_commit_sha": raw.get("merge_commit_sha"),
                "head_ref": (raw.get("head") or {}).get("ref"),
                "head_sha": (raw.get("head") or {}).get("sha"),
                "base_ref": (raw.get("base") or {}).get("ref"),
                "author": raw.get("user"),
                "comments": [],
            }
        )
    return records


def collect_open_prs(repository: str) -> list[dict[str, Any]]:
    fields = (
        "number,title,url,headRefName,headRefOid,baseRefName,isDraft,"
        "mergeStateStatus,createdAt,updatedAt,author"
    )
    command = [
        "gh",
        "pr",
        "list",
        "--repo",
        repository,
        "--state",
        "open",
        "--base",
        "dev",
        "--limit",
        "500",
        "--json",
        fields,
    ]
    result: list[dict[str, Any]] = []
    for attempt in range(4):
        records = json.loads(_run(command).stdout)
        result = [
            {
                "number": int(item["number"]),
                "state": "OPEN",
                "title": item.get("title"),
                "url": item.get("url"),
                "created_at": item.get("createdAt"),
                "updated_at": item.get("updatedAt"),
                "closed_at": None,
                "merged_at": None,
                "draft": bool(item.get("isDraft")),
                "merge_state": item.get("mergeStateStatus") or "UNKNOWN",
                "head_ref": item.get("headRefName"),
                "head_sha": item.get("headRefOid"),
                "base_ref": item.get("baseRefName"),
                "author": (item.get("author") or {}).get("login"),
                "comments": [],
            }
            for item in records
        ]
        unresolved = [
            item
            for item in result
            if str(item.get("head_ref") or "").startswith("task/")
            and item.get("merge_state") == "UNKNOWN"
        ]
        if not unresolved:
            return result
        if attempt < 3:
            time.sleep(1)
    raise TriageError(
        "GitHub did not resolve mergeStateStatus for open task PR(s): "
        + ", ".join(f"#{item['number']}" for item in unresolved)
    )


def collect_pr_detail(repository: str, number: int) -> dict[str, Any]:
    fields = (
        "number,state,mergedAt,closedAt,headRefName,headRefOid,baseRefName,"
        "title,url,isDraft,createdAt,updatedAt,author,comments"
    )
    proc = _run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            repository,
            "--json",
            fields,
        ]
    )
    item = json.loads(proc.stdout)
    merged_at = item.get("mergedAt")
    return {
        "number": int(item["number"]),
        "state": "MERGED" if merged_at else str(item.get("state") or "").upper(),
        "title": item.get("title"),
        "url": item.get("url"),
        "created_at": item.get("createdAt"),
        "updated_at": item.get("updatedAt"),
        "closed_at": item.get("closedAt"),
        "merged_at": merged_at,
        "draft": bool(item.get("isDraft")),
        "merge_state": None,
        "head_ref": item.get("headRefName"),
        "head_sha": item.get("headRefOid"),
        "base_ref": item.get("baseRefName"),
        "author": (item.get("author") or {}).get("login"),
        "comments": [comment.get("body", "") for comment in item.get("comments", [])],
    }


def collect_branches(remote: str, base_sha: str) -> list[dict[str, Any]]:
    pattern = f"refs/remotes/{remote}/task/*"
    fmt = "%00".join(
        [
            "%(objectname)",
            "%(refname:short)",
            "%(committerdate:iso-strict)",
            "%(authorname)",
            "%(subject)",
        ]
    )
    all_refs = _run(["git", "for-each-ref", f"--format={fmt}", pattern]).stdout
    merged_refs = set(
        _run(
            [
                "git",
                "for-each-ref",
                f"--merged={base_sha}",
                "--format=%(refname:short)",
                pattern,
            ]
        ).stdout.splitlines()
    )
    branches: list[dict[str, Any]] = []
    for line in all_refs.splitlines():
        if not line.strip():
            continue
        sha, refname, committed_at, author, subject = (line.split("\0", 4) + [""] * 5)[:5]
        branch = refname.removeprefix(f"{remote}/")
        branches.append(
            {
                "branch": branch,
                "head_sha": sha,
                "committed_at": committed_at,
                "last_commit_author": author,
                "last_commit_subject": subject,
                "dev_reachable": refname in merged_refs,
            }
        )
    return sorted(branches, key=lambda item: item["branch"].casefold())


def _history_index(history: Iterable[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_number: dict[int, dict[str, Any]] = {}
    by_branch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pr in history:
        by_number[int(pr["number"])] = pr
        if pr.get("head_ref"):
            by_branch[str(pr["head_ref"])].append(pr)
    for records in by_branch.values():
        records.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return by_number, by_branch


def validate_open_ref_consistency(
    open_by_branch: dict[str, dict[str, Any]],
    branch_by_name: dict[str, dict[str, Any]],
) -> None:
    """Reject a GitHub/ref snapshot assembled across a concurrent update."""

    problems: list[str] = []
    for branch_name, pull in sorted(open_by_branch.items()):
        branch = branch_by_name.get(branch_name)
        if not branch:
            problems.append(f"PR #{pull['number']} branch {branch_name} is absent")
            continue
        if branch.get("head_sha") != pull.get("head_sha"):
            problems.append(
                f"PR #{pull['number']} head {pull.get('head_sha')} != "
                f"remote ref {branch.get('head_sha')}"
            )
    if problems:
        raise TriageError(
            "GitHub open-PR and remote-ref snapshots raced; refresh and rerun: "
            + "; ".join(problems)
        )


def capture_base_snapshot(remote: str, base_ref: str, refresh: bool) -> str:
    """Refresh requested refs first, then resolve the immutable ancestry base."""

    if refresh:
        _refresh_refs(remote)
    base_sha = _run(
        ["git", "rev-parse", "--verify", f"{base_ref}^{{commit}}"]
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise TriageError(f"{base_ref} did not resolve to a full commit SHA")
    return base_sha


def _snapshot_reachability(base_sha: str, head_shas: Iterable[str]) -> dict[str, bool]:
    """Compute reachability for many heads against one immutable commit snapshot."""

    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise TriageError("report base_sha must be a full lowercase commit SHA")
    resolved = _run(
        ["git", "rev-parse", "--verify", f"{base_sha}^{{commit}}"]
    ).stdout.strip()
    if resolved != base_sha:
        raise TriageError(f"report base_sha did not resolve exactly: {base_sha} -> {resolved}")

    heads = sorted(set(head_shas))
    invalid = [head for head in heads if not re.fullmatch(r"[0-9a-f]{40}", head)]
    if invalid:
        raise TriageError(f"report contains invalid head SHA: {invalid[0]}")
    if heads:
        objects = _run(
            ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
            input_text="\n".join(heads) + "\n",
        ).stdout.splitlines()
        unavailable = [
            line
            for line in objects
            if line.endswith(" missing") or not line.endswith(" commit")
        ]
        if unavailable:
            raise TriageError(
                "cannot reconcile ancestry because report head object is unavailable: "
                + unavailable[0]
            )

    ancestors = set(_run(["git", "rev-list", base_sha]).stdout.splitlines())
    return {head: head in ancestors for head in heads}


def validate_report_ancestry(report: dict[str, Any]) -> None:
    """Reconcile every recorded ancestry decision against report.base_sha."""

    base_sha = str(report.get("base_sha") or "")
    branches = report.get("branches") or []
    cohort = report.get("cohort_prs") or []
    rows = [(f"branch {item.get('branch')}", item) for item in branches]
    rows.extend((f"PR #{item.get('number')}", item) for item in cohort)
    head_shas = [str(item.get("head_sha") or "") for _, item in rows]
    reachability = _snapshot_reachability(base_sha, head_shas)

    mismatches: list[str] = []
    for label, item in rows:
        head_sha = str(item.get("head_sha") or "")
        recorded = item.get("dev_reachable")
        actual = reachability[head_sha]
        if not isinstance(recorded, bool) or recorded != actual:
            mismatches.append(
                f"{label} head {head_sha}: recorded={recorded!r}, actual={actual}"
            )
    if mismatches:
        sample = "; ".join(mismatches[:20])
        suffix = f"; ... {len(mismatches) - 20} more" if len(mismatches) > 20 else ""
        raise TriageError(
            f"{len(mismatches)} ancestry decision(s) disagree with immutable base "
            f"{base_sha}: {sample}{suffix}"
        )


def _resolved_replacements(
    text: str,
    repository: str,
    history_by_number: dict[int, dict[str, Any]],
    current_pr: int,
    allowed_task_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    replacements: list[dict[str, Any]] = []
    for reference in extract_pr_references(text, repository):
        if reference["repository"] != repository or reference["number"] == current_pr:
            continue
        evidence = history_by_number.get(reference["number"])
        if evidence and evidence.get("state") == "MERGED":
            if allowed_task_ids:
                evidence_ids = set(_task_candidates(evidence, {}))
                related = any(
                    evidence_id == allowed_id
                    or evidence_id.startswith(f"{allowed_id}-")
                    or allowed_id.startswith(f"{evidence_id}-")
                    for evidence_id in evidence_ids
                    for allowed_id in allowed_task_ids
                )
                if not related:
                    continue
            replacements.append(
                {
                    "repository": repository,
                    "number": reference["number"],
                    "state": "MERGED",
                    "url": evidence.get("url"),
                    "merged_at": evidence.get("merged_at"),
                    "merge_commit_sha": evidence.get("merge_commit_sha"),
                }
            )
    return replacements


def classify_pr(
    pr: dict[str, Any],
    *,
    triage_task_id: str,
    repository: str,
    dev_reachable: bool,
    trailers: dict[str, str],
    active_tasks: dict[str, dict[str, Any]],
    archives: dict[str, dict[str, Any]],
    history_by_number: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    candidates = _task_candidates(pr, trailers)
    active_key, active = _find_task_record(candidates, active_tasks)
    archive_key, archive = _find_task_record(candidates, archives)
    archive_summary = _archive_summary(archive)
    archive_text = _archive_evidence_text(archive, archives)
    comment_text = "\n".join(pr.get("comments") or [])
    related_task_ids = set(candidates if archive else [])
    archive_task = (archive or {}).get("task") or {}
    for field in ("helper_parent", "superseded_by"):
        normalized = _normalize_task_id(archive_task.get(field))
        if normalized:
            related_task_ids.add(normalized)
    replacements = _resolved_replacements(
        archive_text,
        repository,
        history_by_number,
        int(pr["number"]),
        related_task_ids,
    )
    comment_replacements = _resolved_replacements(
        comment_text,
        repository,
        history_by_number,
        int(pr["number"]),
    )
    replacements_by_number = {
        item["number"]: item for item in replacements + comment_replacements
    }
    replacements = [replacements_by_number[key] for key in sorted(replacements_by_number)]

    replacement_archive: dict[str, Any] | None = None
    if not archive:
        for replacement in replacements:
            replacement_pr = history_by_number.get(replacement["number"])
            if not replacement_pr:
                continue
            _, replacement_archive = _find_task_record(
                _task_candidates(replacement_pr, {}), archives
            )
            if replacement_archive:
                break

    owner = (
        (active or {}).get("owner")
        or ((archive or {}).get("task") or {}).get("owner")
        or ((replacement_archive or {}).get("task") or {}).get("owner")
        or trailers.get("LLM-Agent")
        or "Human/Ops"
    )
    owner_source = (
        "active-task"
        if (active or {}).get("owner")
        else "task-archive"
        if ((archive or {}).get("task") or {}).get("owner")
        else "replacement-task-archive"
        if ((replacement_archive or {}).get("task") or {}).get("owner")
        else "commit-trailer"
        if trailers.get("LLM-Agent")
        else "triage-fallback"
    )

    state = str(pr.get("state") or "").upper()
    terminal_outcome = (archive_summary or {}).get("terminal_outcome")
    close_authorized = False
    reasons: list[str] = []

    if state == "MERGED":
        disposition = "merged-reachable"
        reasons.append("GitHub records the cohort PR as merged")
    elif state == "CLOSED":
        if replacements or "superseded by" in comment_text.casefold():
            disposition = "superseded"
            reasons.append("closed PR has explicit supersession/merged-replacement evidence")
        elif dev_reachable:
            disposition = "merged-reachable"
            reasons.append("closed PR head is an ancestor of current dev")
        else:
            disposition = "abandoned-unproven"
            reasons.append("closed head is not dev-reachable and has no verified replacement")
    elif terminal_outcome == "superseded":
        disposition = "superseded"
        close_authorized = True
        reasons.append("durable task archive records terminal_outcome=superseded")
    elif terminal_outcome == "completed" and replacements:
        disposition = "superseded"
        close_authorized = True
        reasons.append("completed task archive cites a different merged Pantheon PR")
    elif dev_reachable:
        disposition = "merged-reachable"
        reasons.append("open PR head is already an ancestor of current dev")
    elif pr.get("draft"):
        disposition = "protected-retain"
        reasons.append("draft PR is protected from automatic retirement")
    elif str(pr.get("merge_state") or "").upper() == "DIRTY":
        disposition = "conflict-needs-owner"
        reasons.append("GitHub merge state is DIRTY and no supersession proof exists")
    else:
        disposition = "active-repair"
        reasons.append("open task PR needs owner refresh/rebase or an explicit retirement decision")

    result = {
        "number": int(pr["number"]),
        "url": pr.get("url"),
        "title": pr.get("title"),
        "state": state,
        "draft": bool(pr.get("draft")),
        "merge_state": pr.get("merge_state"),
        "head_ref": pr.get("head_ref"),
        "head_sha": pr.get("head_sha"),
        "base_ref": pr.get("base_ref"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "closed_at": pr.get("closed_at"),
        "merged_at": pr.get("merged_at"),
        "dev_reachable": dev_reachable,
        "task_candidates": candidates,
        "task_id": (active or {}).get("id")
        or ((archive or {}).get("task") or {}).get("id")
        or trailers.get("Task-ID"),
        "owner": owner,
        "owner_source": owner_source,
        "reviewer": (active or {}).get("reviewer")
        or ((archive or {}).get("task") or {}).get("reviewer")
        or trailers.get("Reviewer"),
        "active_task": {
            key: active.get(key)
            for key in ("id", "status", "owner", "reviewer", "next", "last_update")
            if active and active.get(key) is not None
        }
        if active_key
        else None,
        "archive": archive_summary if archive_key else None,
        "replacement_prs": replacements,
        "disposition": disposition,
        "reasons": reasons,
        "close_authorized": close_authorized,
    }
    if close_authorized:
        evidence_lines = []
        if archive_summary:
            evidence_lines.append(
                f"archive `{archive_summary['path']}` records task "
                f"`{archive_summary['task_id']}` as "
                f"`{archive_summary['terminal_outcome']}`"
            )
        for replacement in replacements:
            evidence_lines.append(
                f"replacement PR #{replacement['number']} is merged"
            )
        evidence_lines.append(
            "branch deletion is out of scope; the branch remains governed by the dry-run retention manifest"
        )
        result["closure_comment"] = (
            f"{triage_task_id} evidence-based disposition: **superseded**.\n\n"
            + "\n".join(f"- {line}" for line in evidence_lines)
        )
    return result


def classify_branch(
    branch: dict[str, Any],
    *,
    as_of: datetime,
    retention_days: int,
    open_pr: dict[str, Any] | None,
    pr_disposition: dict[str, Any] | None,
    history: list[dict[str, Any]],
    active_tasks: dict[str, dict[str, Any]],
    archives: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    branch_name = str(branch["branch"])
    task_slug = branch_name.removeprefix("task/")
    candidate = _normalize_task_id(task_slug)
    active = active_tasks.get(candidate or "")
    archive = archives.get(candidate or "")
    committed_at = _parse_datetime(branch.get("committed_at"))
    age_days = (
        round((as_of - committed_at).total_seconds() / 86400.0, 3)
        if committed_at
        else None
    )
    recent = age_days is None or age_days < retention_days

    if open_pr and pr_disposition:
        disposition = pr_disposition["disposition"]
        reasons = ["branch has an open PR and follows its PR disposition"]
    elif active and active.get("status") not in TERMINAL_STATUSES:
        disposition = "active-repair"
        reasons = ["canonical task state is nonterminal"]
    elif branch.get("dev_reachable"):
        disposition = "merged-reachable"
        reasons = ["branch head is an ancestor of current dev"]
    elif (_archive_summary(archive) or {}).get("terminal_outcome") == "superseded":
        disposition = "superseded"
        reasons = ["matching task archive is explicitly superseded"]
    elif recent:
        disposition = "protected-retain"
        reasons = ["branch is inside the retention window"]
    else:
        disposition = "abandoned-unproven"
        reasons = ["branch is old and ahead, but lacks recoverable retirement proof"]

    deletion_reasons: list[str] = []
    if open_pr:
        deletion_reasons.append("open-pr")
    if active and active.get("status") not in TERMINAL_STATUSES:
        deletion_reasons.append("active-task")
    if recent:
        deletion_reasons.append("inside-retention-window")
    if not branch.get("dev_reachable"):
        deletion_reasons.append("head-not-dev-reachable")
    deletion_eligible = not deletion_reasons and disposition == "merged-reachable"
    if not deletion_eligible and not deletion_reasons:
        deletion_reasons.append("disposition-not-eligible")

    return {
        **branch,
        "age_days": age_days,
        "open_pr": int(open_pr["number"]) if open_pr else None,
        "pr_history": [
            {
                key: item.get(key)
                for key in (
                    "number",
                    "state",
                    "url",
                    "head_sha",
                    "created_at",
                    "updated_at",
                    "closed_at",
                    "merged_at",
                )
            }
            for item in history
        ],
        "active_task": {
            key: active.get(key)
            for key in ("id", "status", "owner", "reviewer", "next", "last_update")
            if active and active.get(key) is not None
        }
        if active
        else None,
        "archive": _archive_summary(archive),
        "owner": (active or {}).get("owner")
        or ((archive or {}).get("task") or {}).get("owner"),
        "disposition": disposition,
        "reasons": reasons,
        "deletion_eligible": deletion_eligible,
        "deletion_exclusion_reasons": deletion_reasons,
    }


def build_report(
    *,
    task_id: str,
    repository: str,
    remote: str,
    base_ref: str,
    base_sha: str,
    as_of: datetime,
    overdue_hours: int,
    retention_days: int,
    expected_cohort_count: int,
    included_prs: list[int],
    history: list[dict[str, Any]],
    open_prs: list[dict[str, Any]],
    included_details: dict[int, dict[str, Any]],
    branches: list[dict[str, Any]],
    active_tasks: dict[str, dict[str, Any]],
    archives: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    history_by_number, history_by_branch = _history_index(history)
    open_by_number = {int(pr["number"]): pr for pr in open_prs}
    open_by_branch = {
        str(pr["head_ref"]): pr
        for pr in open_prs
        if str(pr.get("head_ref") or "").startswith("task/")
    }
    branch_by_name = {str(branch["branch"]): branch for branch in branches}
    validate_open_ref_consistency(open_by_branch, branch_by_name)

    cutoff = as_of - timedelta(hours=overdue_hours)
    cohort_numbers = {
        int(pr["number"])
        for pr in open_prs
        if str(pr.get("head_ref") or "").startswith("task/")
        and (_parse_datetime(pr.get("created_at")) or as_of) <= cutoff
    }
    cohort_numbers.update(included_prs)

    cohort: list[dict[str, Any]] = []
    for number in sorted(cohort_numbers, reverse=True):
        pr = open_by_number.get(number) or included_details.get(number) or history_by_number.get(number)
        if not pr:
            raise TriageError(f"cohort PR #{number} is missing from GitHub evidence")
        branch = branch_by_name.get(str(pr.get("head_ref")))
        dev_reachable = bool(branch and branch.get("dev_reachable"))
        if not branch and pr.get("head_sha"):
            dev_reachable = _snapshot_reachability(
                base_sha, [str(pr["head_sha"])]
            )[str(pr["head_sha"])]
        trailers = (
            git_commit_trailers(str(pr["head_sha"]), base_sha)
            if pr.get("head_sha")
            else {}
        )
        cohort.append(
            classify_pr(
                pr,
                triage_task_id=task_id,
                repository=repository,
                dev_reachable=dev_reachable,
                trailers=trailers,
                active_tasks=active_tasks,
                archives=archives,
                history_by_number=history_by_number,
            )
        )

    if len(cohort) != expected_cohort_count:
        raise TriageError(
            f"expected {expected_cohort_count} cohort PRs, found {len(cohort)}; "
            "refresh the audit baseline or pass the resolved cohort PR numbers explicitly"
        )

    pr_dispositions = {item["number"]: item for item in cohort}
    classified_branches: list[dict[str, Any]] = []
    for branch in branches:
        name = str(branch["branch"])
        open_pr = open_by_branch.get(name)
        disposition = pr_dispositions.get(int(open_pr["number"])) if open_pr else None
        classified_branches.append(
            classify_branch(
                branch,
                as_of=as_of,
                retention_days=retention_days,
                open_pr=open_pr,
                pr_disposition=disposition,
                history=history_by_branch.get(name, []),
                active_tasks=active_tasks,
                archives=archives,
            )
        )

    deletion_candidates = [
        {
            "branch": item["branch"],
            "head_sha": item["head_sha"],
            "committed_at": item["committed_at"],
            "age_days": item["age_days"],
            "disposition": item["disposition"],
            "proof": {
                "open_pr": None,
                "dev_reachable": True,
                "retention_satisfied": True,
                "recoverability": f"head is an ancestor of {base_sha}",
            },
            "remote_ref": f"refs/heads/{item['branch']}",
        }
        for item in classified_branches
        if item["deletion_eligible"]
    ]
    deletion_manifest = {
        "schema_version": 1,
        "task_id": task_id,
        "mode": "dry-run-only",
        "repository": repository,
        "remote": remote,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "as_of": _iso(as_of),
        "retention_days": retention_days,
        "candidate_count": len(deletion_candidates),
        "candidates": deletion_candidates,
        "guard": "No branch deletion is implemented or authorized by this tool.",
    }

    branch_counts = Counter(item["disposition"] for item in classified_branches)
    pr_counts = Counter(item["disposition"] for item in cohort)
    cohort_open_pr_count = sum(1 for item in cohort if item.get("state") == "OPEN")
    cohort_resolved_pr_count = len(cohort) - cohort_open_pr_count
    closure_candidates = [item for item in cohort if item["close_authorized"]]
    report = {
        "schema_version": 1,
        "task_id": task_id,
        "repository": repository,
        "remote": remote,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "as_of": _iso(as_of),
        "policy": {
            "overdue_hours": overdue_hours,
            "retention_days": retention_days,
            "allowed_dispositions": sorted(ALLOWED_DISPOSITIONS),
            "closure_rule": "open PR closure requires durable superseded state or a different verified merged PR",
            "branch_rule": "deletion dry run includes only old no-open-PR heads already reachable from dev",
        },
        "summary": {
            "cohort_pr_count": len(cohort),
            "cohort_open_pr_count": cohort_open_pr_count,
            "cohort_resolved_pr_count": cohort_resolved_pr_count,
            "cohort_pr_dispositions": dict(sorted(pr_counts.items())),
            "global_open_task_pr_count": len(open_by_branch),
            "resolved_baseline_prs": sorted(
                number for number in included_prs if number not in open_by_number
            ),
            "remote_task_branch_count": len(classified_branches),
            "no_open_pr_branch_count": sum(
                1 for item in classified_branches if item["open_pr"] is None
            ),
            "branch_dispositions": dict(sorted(branch_counts.items())),
            "closure_candidate_count": len(closure_candidates),
            "deletion_dry_run_candidate_count": len(deletion_candidates),
        },
        "cohort_prs": cohort,
        "closure_candidates": [
            {
                "number": item["number"],
                "head_ref": item["head_ref"],
                "head_sha": item["head_sha"],
                "disposition": item["disposition"],
                "owner": item["owner"],
                "archive": item["archive"],
                "replacement_prs": item["replacement_prs"],
                "comment": item.get("closure_comment"),
            }
            for item in closure_candidates
        ],
        "branches": classified_branches,
    }
    validate_report(report, deletion_manifest, expected_cohort_count)
    return report, deletion_manifest


def validate_report(
    report: dict[str, Any],
    deletion_manifest: dict[str, Any],
    expected_cohort_count: int | None = None,
) -> None:
    cohort = report.get("cohort_prs") or []
    if expected_cohort_count is not None and len(cohort) != expected_cohort_count:
        raise TriageError(
            f"cohort count {len(cohort)} does not match expected {expected_cohort_count}"
        )
    numbers = [item.get("number") for item in cohort]
    if len(numbers) != len(set(numbers)):
        raise TriageError("cohort contains duplicate PR numbers")
    for item in cohort:
        if item.get("disposition") not in ALLOWED_DISPOSITIONS:
            raise TriageError(f"PR #{item.get('number')} has invalid disposition")
        if not item.get("owner"):
            raise TriageError(f"PR #{item.get('number')} has no disposition owner")
        if item.get("close_authorized") and not (
            item.get("state") == "OPEN" and item.get("disposition") == "superseded"
        ):
            raise TriageError(
                f"PR #{item.get('number')} has unsafe close authorization"
            )

    branch_by_name = {item["branch"]: item for item in report.get("branches") or []}
    for field in ("repository", "remote", "base_ref", "base_sha", "as_of"):
        if deletion_manifest.get(field) != report.get(field):
            raise TriageError(f"branch manifest {field} does not match report")
    if deletion_manifest.get("task_id") != report.get("task_id"):
        raise TriageError("branch manifest task_id does not match report")
    if deletion_manifest.get("mode") != "dry-run-only":
        raise TriageError("branch manifest must remain dry-run-only")
    candidates = deletion_manifest.get("candidates") or []
    if deletion_manifest.get("candidate_count") != len(candidates):
        raise TriageError("branch manifest candidate_count is inconsistent")
    for candidate in candidates:
        branch = branch_by_name.get(candidate.get("branch"))
        if not branch:
            raise TriageError(f"deletion candidate {candidate.get('branch')} is absent")
        if not branch.get("deletion_eligible"):
            raise TriageError(f"deletion candidate {candidate['branch']} is not eligible")
        if branch.get("open_pr") is not None:
            raise TriageError(f"deletion candidate {candidate['branch']} has an open PR")
        if not branch.get("dev_reachable"):
            raise TriageError(
                f"deletion candidate {candidate['branch']} is not dev-reachable"
            )

    summary = report.get("summary") or {}
    cohort_open_pr_count = sum(
        1 for item in cohort if item.get("state") == "OPEN"
    )
    cohort_resolved_pr_count = len(cohort) - cohort_open_pr_count
    expected_summary_counts = {
        "cohort_pr_count": len(cohort),
        "cohort_open_pr_count": cohort_open_pr_count,
        "cohort_resolved_pr_count": cohort_resolved_pr_count,
    }
    for field, expected in expected_summary_counts.items():
        if summary.get(field) != expected:
            raise TriageError(
                f"summary {field}={summary.get(field)!r} does not match {expected}"
            )
    global_open_count = summary.get("global_open_task_pr_count")
    if not isinstance(global_open_count, int) or global_open_count < cohort_open_pr_count:
        raise TriageError(
            "summary global_open_task_pr_count must include every open cohort PR"
        )


def render_markdown(
    report: dict[str, Any],
    deletion_manifest: dict[str, Any],
    closure_results: dict[str, Any] | None = None,
) -> str:
    summary = report["summary"]
    lines = [
        f"# {report['task_id']} evidence report",
        "",
        f"Generated from live GitHub and git evidence at `{report['as_of']}`.",
        f"Base proof: `{report['base_ref']}` = `{report['base_sha']}`.",
        "",
        "## Cohort result",
        "",
        f"The fixed audit cohort contains **{summary['cohort_pr_count']}** task PRs: "
        f"{summary['cohort_open_pr_count']} remain open and "
        f"{summary['cohort_resolved_pr_count']} are now closed or merged.",
        f"Repository-wide, **{summary['global_open_task_pr_count']}** task PRs are open "
        "at this snapshot; that global count includes recent PRs outside the fixed "
        "overdue cohort.",
        "",
        "| PR | State | Merge | Draft | Disposition | Owner | Evidence |",
        "|---:|---|---|:---:|---|---|---|",
    ]
    for item in report["cohort_prs"]:
        evidence = "; ".join(item["reasons"])
        if item.get("replacement_prs"):
            evidence += "; merged replacement(s) " + ", ".join(
                f"#{ref['number']}" for ref in item["replacement_prs"]
            )
        lines.append(
            f"| [#{item['number']}]({item['url']}) | {item['state']} | "
            f"{item.get('merge_state') or '-'} | {'yes' if item['draft'] else 'no'} | "
            f"{item['disposition']} | {item['owner']} | {evidence} |"
        )

    lines.extend(
        [
            "",
            "## Superseded closure manifest",
            "",
            "Only the following still-open PRs passed the fail-closed closure rule.",
            "",
            "| PR | Head | Owner | Durable evidence |",
            "|---:|---|---|---|",
        ]
    )
    for item in report["closure_candidates"]:
        archive = item.get("archive") or {}
        replacements = item.get("replacement_prs") or []
        evidence = f"`{archive.get('path')}` ({archive.get('terminal_outcome')})"
        if replacements:
            evidence += "; " + ", ".join(
                f"#{ref['number']} merged" for ref in replacements
            )
        lines.append(
            f"| #{item['number']} | `{item['head_ref']}` | {item['owner']} | {evidence} |"
        )
    if not report["closure_candidates"]:
        lines.append("| - | - | - | No closure candidates |")
    applied = (closure_results or {}).get("results") or []
    if applied:
        applied_numbers = ", ".join(f"#{item['number']}" for item in applied)
        lines.extend(
            [
                "",
                "### Applied closure record",
                "",
                f"The explicit allowlist was applied at `{closure_results['applied_at']}` "
                f"to {applied_numbers}. Each PR was revalidated at its recorded head, "
                "received an evidence comment, and is now closed. Exact comment URLs, "
                "replacement evidence, and head SHAs remain recorded in "
                "`closure-results.json`.",
                "",
                f"Recorded branch deletions: {closure_results.get('branch_deletions', 0)}.",
            ]
        )

    lines.extend(
        [
            "",
            "## Branch inventory and deletion dry run",
            "",
            f"- Remote task branches: {summary['remote_task_branch_count']}",
            f"- No-open-PR task branches: {summary['no_open_pr_branch_count']}",
            f"- Dry-run deletion candidates: {deletion_manifest['candidate_count']}",
            "- No branch deletion command exists in this task or tool.",
            "",
            "Disposition counts:",
            "",
        ]
    )
    for disposition, count in summary["branch_dispositions"].items():
        lines.append(f"- `{disposition}`: {count}")
    lines.extend(
        [
            "",
            "The machine-readable report contains every branch, joined PR history, active/archive "
            "state, reachability, age, and exclusion reasons. The separate dry-run manifest includes "
            "only heads older than the retention window that have no open PR and are already ancestors "
            "of current `dev`.",
            "",
        ]
    )
    return "\n".join(lines)


def _refresh_refs(remote: str) -> None:
    _run(
        [
            "git",
            "fetch",
            remote,
            "--prune",
            f"+refs/heads/task/*:refs/remotes/{remote}/task/*",
            f"+refs/heads/dev:refs/remotes/{remote}/dev",
        ]
    )


def cmd_generate(args: argparse.Namespace) -> int:
    as_of = _snapshot_time(
        _parse_datetime(args.as_of) if args.as_of else datetime.now(timezone.utc)
    )
    assert as_of is not None
    status_root = Path(args.status_root).resolve()
    base_sha = capture_base_snapshot(args.remote, args.base_ref, args.refresh)
    active, archives = load_task_state(status_root)
    history = collect_pr_history(args.repository)
    branches = collect_branches(args.remote, base_sha)
    open_prs = collect_open_prs(args.repository)
    included = {
        number: collect_pr_detail(args.repository, number) for number in args.include_pr
    }
    report, manifest = build_report(
        task_id=args.task_id,
        repository=args.repository,
        remote=args.remote,
        base_ref=args.base_ref,
        base_sha=base_sha,
        as_of=as_of,
        overdue_hours=args.overdue_hours,
        retention_days=args.retention_days,
        expected_cohort_count=args.expected_cohort_count,
        included_prs=args.include_pr,
        history=history,
        open_prs=open_prs,
        included_details=included,
        branches=branches,
        active_tasks=active,
        archives=archives,
    )
    output = Path(args.output)
    manifest_path = Path(args.deletion_manifest)
    markdown_path = Path(args.markdown)
    closure_path = output.parent / "closure-results.json"
    closure_results = _load_json(closure_path) if closure_path.exists() else None
    _write_json(output, report)
    _write_json(manifest_path, manifest)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report, manifest, closure_results))
    print(
        json.dumps(
            {
                "report": str(output),
                "markdown": str(markdown_path),
                "deletion_manifest": str(manifest_path),
                "summary": report["summary"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    report = _load_json(Path(args.report))
    manifest = _load_json(Path(args.deletion_manifest))
    validate_report(report, manifest, args.expected_cohort_count)
    validate_report_ancestry(report)
    print(
        f"valid: {len(report['cohort_prs'])} PRs, "
        f"{len(report['branches'])} branches, "
        f"{manifest['candidate_count']} deletion dry-run candidates"
    )
    return 0


def cmd_close_superseded(args: argparse.Namespace) -> int:
    report = _load_json(Path(args.report))
    candidates = {
        int(item["number"]): item for item in report.get("closure_candidates") or []
    }
    requested = sorted(set(args.only))
    if args.apply and not requested:
        raise TriageError("--apply requires at least one explicit --only PR number")
    if not requested:
        requested = sorted(candidates)

    actions: list[dict[str, Any]] = []
    for number in requested:
        candidate = candidates.get(number)
        if not candidate:
            raise TriageError(f"PR #{number} is not an authorized closure candidate")
        live = collect_pr_detail(args.repository, number)
        if live["state"] != "OPEN":
            raise TriageError(f"PR #{number} is no longer open (state={live['state']})")
        if live.get("head_sha") != candidate.get("head_sha"):
            raise TriageError(f"PR #{number} head changed after the report snapshot")
        if live.get("base_ref") != "dev":
            raise TriageError(f"PR #{number} no longer targets dev")
        action = {
            "number": number,
            "head_sha": live.get("head_sha"),
            "comment": candidate.get("comment"),
            "applied": False,
        }
        if args.apply:
            _run(
                [
                    "gh",
                    "pr",
                    "comment",
                    str(number),
                    "--repo",
                    args.repository,
                    "--body",
                    str(candidate.get("comment") or ""),
                ]
            )
            _run(
                [
                    "gh",
                    "pr",
                    "close",
                    str(number),
                    "--repo",
                    args.repository,
                ]
            )
            action["applied"] = True
        actions.append(action)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", "actions": actions}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="collect live evidence and write reports")
    generate.add_argument("--task-id", default="OPS-TASK-PR-TRIAGE-001")
    generate.add_argument("--repository", default=DEFAULT_REPOSITORY)
    generate.add_argument("--remote", default="origin")
    generate.add_argument("--base-ref", default="origin/dev")
    generate.add_argument(
        "--status-root", default=os.environ.get("PANTHEON_STATUS_ROOT", str(ROOT))
    )
    generate.add_argument("--as-of")
    generate.add_argument("--overdue-hours", type=int, default=24)
    generate.add_argument("--retention-days", type=int, default=30)
    generate.add_argument("--expected-cohort-count", type=int, default=29)
    generate.add_argument("--include-pr", type=int, action="append", default=[])
    generate.add_argument("--refresh", action="store_true")
    generate.add_argument("--output", required=True)
    generate.add_argument("--markdown", required=True)
    generate.add_argument("--deletion-manifest", required=True)
    generate.set_defaults(func=cmd_generate)

    validate = sub.add_parser("validate", help="validate an existing report pair")
    validate.add_argument("--report", required=True)
    validate.add_argument("--deletion-manifest", required=True)
    validate.add_argument("--expected-cohort-count", type=int, default=29)
    validate.set_defaults(func=cmd_validate)

    close = sub.add_parser(
        "close-superseded", help="dry-run or explicitly close authorized superseded PRs"
    )
    close.add_argument("--repository", default=DEFAULT_REPOSITORY)
    close.add_argument("--report", required=True)
    close.add_argument("--only", type=int, action="append", default=[])
    close.add_argument("--apply", action="store_true")
    close.set_defaults(func=cmd_close_superseded)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except TriageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
