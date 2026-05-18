#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / ".orchestrator" / "config.json"
DEFAULT_REPO = "ajoe734/pantheon"
DEFAULT_DEV_BRANCH = "dev"
DEFAULT_MAIN_BRANCH = "master"
DEFAULT_TASK_BRANCH_PREFIX = "task/"
DEFAULT_TASK_PR_THRESHOLD_HOURS = 24.0
DEFAULT_DEV_PUBLISH_THRESHOLD_HOURS = 24.0
DEFAULT_PUBLISH_PROMOTE_WINDOW_HOURS = 3.0

GHRunner = Callable[[list[str]], Any]


class WorkflowHealthError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_timestamp(value: str | datetime | None, *, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise WorkflowHealthError(f"{field} must be an ISO-8601 timestamp: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def age_hours(now: datetime, then: datetime) -> float:
    return max(0.0, (now - then).total_seconds() / 3600.0)


def slug(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    text = re.sub(r"[^a-z0-9_.:-]+", "-", text)
    return text.strip("-") or "unknown"


def load_json_file(path: Path) -> dict[str, Any]:
    payload = load_json_any(path)
    if not isinstance(payload, dict):
        raise WorkflowHealthError(f"{path} must contain a JSON object")
    return payload


def load_json_any(path: Path) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowHealthError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowHealthError(f"invalid JSON in {path}: {exc}") from exc
    return payload


def load_branch_workflow_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = load_json_file(path)
    return dict(payload.get("branch_workflow") or payload.get("wave_workflow") or {})


def load_repo_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json_file(path)


def default_repo(config: Mapping[str, Any] | None = None) -> str:
    env_repo = os.environ.get("GITHUB_REPOSITORY")
    if env_repo:
        return env_repo
    if config:
        github_bus = config.get("github_bus") if isinstance(config.get("github_bus"), Mapping) else None
        repo = github_bus.get("repo") if github_bus else None
        if isinstance(repo, str) and repo.strip():
            return repo.strip()
    return DEFAULT_REPO


def run_gh_api(args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", "api", *args],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise WorkflowHealthError(proc.stderr.strip() or f"gh api failed: {' '.join(args)}")
    text = proc.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkflowHealthError(f"gh api returned invalid JSON: {exc}") from exc


def normalize_sequence(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = payload["items"]
    else:
        raise WorkflowHealthError("expected a JSON array or object with items[]")
    return [item for item in items if isinstance(item, dict)]


def gh_list_open_pull_requests(
    *,
    repo: str,
    base_branch: str,
    gh: GHRunner = run_gh_api,
) -> list[dict[str, Any]]:
    payload = gh(
        [
            f"/repos/{repo}/pulls",
            "--method",
            "GET",
            "--paginate",
            "-f",
            "state=open",
            "-f",
            f"base={base_branch}",
            "-F",
            "per_page=100",
        ]
    )
    return normalize_sequence(payload)


def gh_fetch_branch_commit_at(
    *,
    repo: str,
    branch: str,
    gh: GHRunner = run_gh_api,
) -> datetime | None:
    payload = gh([f"/repos/{repo}/commits/{branch}"])
    if not isinstance(payload, dict):
        return None
    commit = payload.get("commit") if isinstance(payload.get("commit"), dict) else {}
    committer = commit.get("committer") if isinstance(commit.get("committer"), dict) else {}
    author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
    raw_date = committer.get("date") or author.get("date")
    return parse_timestamp(raw_date, field=f"{branch} latest commit date")


def load_publish_artifact(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return load_json_file(Path(path))


def publish_artifact_time(payload: Mapping[str, Any]) -> datetime | None:
    raw_value = (
        payload.get("last_publish_at")
        or payload.get("published_at")
        or payload.get("last_published_at")
        or payload.get("created_at")
    )
    return parse_timestamp(raw_value, field="publish artifact last_publish_at")


def make_finding(
    *,
    finding_type: str,
    key: str,
    severity: str,
    recommended_action: str,
    evidence_refs: Sequence[str],
    detected_at: datetime,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "finding_id": f"workflow-health:{finding_type}:{slug(key)}",
        "type": finding_type,
        "severity": severity,
        "recommended_action": recommended_action,
        "evidence_refs": list(evidence_refs),
        "detected_at": isoformat_z(detected_at),
        "evidence": dict(evidence),
    }


def pr_head_ref(pr: Mapping[str, Any]) -> str:
    head = pr.get("head")
    if isinstance(head, Mapping):
        ref = head.get("ref")
        if isinstance(ref, str):
            return ref
    for key in ("headRefName", "head_ref", "head"):
        value = pr.get(key)
        if isinstance(value, str):
            return value
    return ""


def pr_updated_at(pr: Mapping[str, Any]) -> datetime | None:
    return parse_timestamp(pr.get("updated_at") or pr.get("updatedAt"), field="pull request updated_at")


def pr_url(pr: Mapping[str, Any]) -> str:
    for key in ("html_url", "url"):
        value = pr.get(key)
        if isinstance(value, str):
            return value
    return ""


def check_task_pr_stale(
    threshold_hours: float = DEFAULT_TASK_PR_THRESHOLD_HOURS,
    *,
    now: str | datetime | None = None,
    repo: str = DEFAULT_REPO,
    base_branch: str = DEFAULT_DEV_BRANCH,
    task_branch_prefix: str = DEFAULT_TASK_BRANCH_PREFIX,
    prs: Sequence[Mapping[str, Any]] | None = None,
    gh: GHRunner = run_gh_api,
) -> list[dict[str, Any]]:
    detected_at = parse_timestamp(now, field="now") or utc_now()
    pull_requests = list(prs) if prs is not None else gh_list_open_pull_requests(repo=repo, base_branch=base_branch, gh=gh)
    findings: list[dict[str, Any]] = []
    for pr in pull_requests:
        head_ref = pr_head_ref(pr)
        if not head_ref.startswith(task_branch_prefix):
            continue
        updated_at = pr_updated_at(pr)
        if updated_at is None:
            continue
        open_for = age_hours(detected_at, updated_at)
        if open_for <= threshold_hours:
            continue
        number = pr.get("number") or head_ref
        findings.append(
            make_finding(
                finding_type="task_pr_stale",
                key=str(number),
                severity="warning",
                recommended_action=(
                    "Re-dispatch the task owner to rebase/fix CI or close the stale task PR; "
                    "do not merge manually from chair review."
                ),
                evidence_refs=[
                    f"gh-api:/repos/{repo}/pulls?state=open&base={base_branch}",
                    f"pr:{number}",
                    pr_url(pr) or f"head:{head_ref}",
                ],
                detected_at=detected_at,
                evidence={
                    "repo": repo,
                    "base_branch": base_branch,
                    "head_ref": head_ref,
                    "number": number,
                    "title": pr.get("title"),
                    "updated_at": isoformat_z(updated_at),
                    "age_hours": round(open_for, 2),
                    "threshold_hours": float(threshold_hours),
                },
            )
        )
    return findings


def check_dev_publish_stale(
    threshold_hours: float = DEFAULT_DEV_PUBLISH_THRESHOLD_HOURS,
    *,
    now: str | datetime | None = None,
    repo: str = DEFAULT_REPO,
    dev_branch: str = DEFAULT_DEV_BRANCH,
    dev_latest_commit_at: str | datetime | None = None,
    last_publish_at: str | datetime | None = None,
    publish_artifact: Mapping[str, Any] | None = None,
    gh: GHRunner = run_gh_api,
) -> list[dict[str, Any]]:
    detected_at = parse_timestamp(now, field="now") or utc_now()
    dev_commit_at = parse_timestamp(dev_latest_commit_at, field="dev_latest_commit_at")
    if dev_commit_at is None:
        dev_commit_at = gh_fetch_branch_commit_at(repo=repo, branch=dev_branch, gh=gh)
    if dev_commit_at is None:
        return []

    artifact = dict(publish_artifact or {})
    publish_at = parse_timestamp(last_publish_at, field="last_publish_at") or publish_artifact_time(artifact)
    if publish_at is not None and publish_at >= dev_commit_at:
        return []

    unpublished_for = age_hours(detected_at, dev_commit_at)
    if unpublished_for <= threshold_hours:
        return []

    return [
        make_finding(
            finding_type="dev_publish_stale",
            key=dev_branch,
            severity="warning",
            recommended_action=(
                "Inspect nightly-publish-cut.yml and dispatch it manually if the scheduled run missed "
                "a dev advance; keep frontend staging publication gated on backend health."
            ),
            evidence_refs=[
                f"gh-api:/repos/{repo}/commits/{dev_branch}",
                "artifact:last_publish_at" if publish_at is not None else "artifact:last_publish_at:missing",
            ],
            detected_at=detected_at,
            evidence={
                "repo": repo,
                "dev_branch": dev_branch,
                "dev_latest_commit_at": isoformat_z(dev_commit_at),
                "last_publish_at": isoformat_z(publish_at) if publish_at is not None else None,
                "age_hours": round(unpublished_for, 2),
                "threshold_hours": float(threshold_hours),
                "publish_version": artifact.get("version") or artifact.get("release_tag"),
            },
        )
    ]


def check_publish_promote_stale(
    window_hours: float = DEFAULT_PUBLISH_PROMOTE_WINDOW_HOURS,
    *,
    now: str | datetime | None = None,
    last_publish_at: str | datetime | None = None,
    master_promoted_at: str | datetime | None = None,
    publish_artifact: Mapping[str, Any] | None = None,
    version: str | None = None,
) -> list[dict[str, Any]]:
    detected_at = parse_timestamp(now, field="now") or utc_now()
    artifact = dict(publish_artifact or {})
    publish_at = parse_timestamp(last_publish_at, field="last_publish_at") or publish_artifact_time(artifact)
    if publish_at is None:
        return []
    promoted_at = parse_timestamp(
        master_promoted_at or artifact.get("master_promoted_at") or artifact.get("promoted_at"),
        field="master_promoted_at",
    )
    if promoted_at is not None and promoted_at >= publish_at:
        return []

    unpromoted_for = age_hours(detected_at, publish_at)
    if unpromoted_for <= window_hours:
        return []

    publish_version = version or artifact.get("version") or artifact.get("release_tag") or isoformat_z(publish_at)
    return [
        make_finding(
            finding_type="publish_promote_stale",
            key=str(publish_version),
            severity="warning",
            recommended_action=(
                "Triage publish-promote.yml or the open promote PR; do not manually merge a promote PR "
                "outside branch protection."
            ),
            evidence_refs=[
                f"release:{publish_version}",
                "master:promoted_at" if promoted_at is not None else "master:promoted_at:missing",
            ],
            detected_at=detected_at,
            evidence={
                "version": publish_version,
                "last_publish_at": isoformat_z(publish_at),
                "master_promoted_at": isoformat_z(promoted_at) if promoted_at is not None else None,
                "age_hours": round(unpromoted_for, 2),
                "window_hours": float(window_hours),
            },
        )
    ]


def build_report(
    *,
    now: str | datetime | None = None,
    repo: str | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    task_pr_json: Path | None = None,
    publish_artifact_path: Path | None = None,
    dev_latest_commit_at: str | None = None,
    last_publish_at: str | None = None,
    master_promoted_at: str | None = None,
    skip_task_pr: bool = False,
    skip_dev_publish: bool = False,
    skip_publish_promote: bool = False,
    task_pr_threshold_hours: float | None = None,
    dev_publish_threshold_hours: float | None = None,
    publish_promote_window_hours: float | None = None,
    gh: GHRunner = run_gh_api,
) -> dict[str, Any]:
    detected_at = parse_timestamp(now, field="now") or utc_now()
    repo_config = load_repo_config(config_path)
    branch_workflow = dict(repo_config.get("branch_workflow") or repo_config.get("wave_workflow") or {})
    selected_repo = repo or default_repo(repo_config)
    dev_branch = str(branch_workflow.get("dev_branch") or DEFAULT_DEV_BRANCH)
    main_branch = str(branch_workflow.get("main_branch") or DEFAULT_MAIN_BRANCH)
    task_prefix = str(branch_workflow.get("task_branch_prefix") or DEFAULT_TASK_BRANCH_PREFIX)
    task_pr_settings = branch_workflow.get("task_pr") if isinstance(branch_workflow.get("task_pr"), Mapping) else {}
    drift_alarms = branch_workflow.get("drift_alarms") if isinstance(branch_workflow.get("drift_alarms"), Mapping) else {}

    task_threshold = float(task_pr_threshold_hours or task_pr_settings.get("max_open_hours") or DEFAULT_TASK_PR_THRESHOLD_HOURS)
    dev_threshold = float(dev_publish_threshold_hours or DEFAULT_DEV_PUBLISH_THRESHOLD_HOURS)
    promote_window = float(
        publish_promote_window_hours
        or (float(drift_alarms.get("publish_must_promote_within_minutes")) / 60.0 if drift_alarms.get("publish_must_promote_within_minutes") else 0)
        or DEFAULT_PUBLISH_PROMOTE_WINDOW_HOURS
    )

    publish_artifact = load_publish_artifact(publish_artifact_path) if publish_artifact_path else {}
    findings: list[dict[str, Any]] = []

    if not skip_task_pr:
        prs = normalize_sequence(load_json_any(task_pr_json)) if task_pr_json else None
        findings.extend(
            check_task_pr_stale(
                task_threshold,
                now=detected_at,
                repo=selected_repo,
                base_branch=dev_branch,
                task_branch_prefix=task_prefix,
                prs=prs,
                gh=gh,
            )
        )

    if not skip_dev_publish:
        findings.extend(
            check_dev_publish_stale(
                dev_threshold,
                now=detected_at,
                repo=selected_repo,
                dev_branch=dev_branch,
                dev_latest_commit_at=dev_latest_commit_at,
                last_publish_at=last_publish_at,
                publish_artifact=publish_artifact,
                gh=gh,
            )
        )

    if not skip_publish_promote:
        findings.extend(
            check_publish_promote_stale(
                promote_window,
                now=detected_at,
                last_publish_at=last_publish_at,
                master_promoted_at=master_promoted_at,
                publish_artifact=publish_artifact,
            )
        )

    return {
        "ok": not findings,
        "generated_at": isoformat_z(detected_at),
        "repo": selected_repo,
        "dev_branch": dev_branch,
        "main_branch": main_branch,
        "finding_count": len(findings),
        "findings": findings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Emit chair-review workflow health stale findings.")
    parser.add_argument("--now", help="Override detection time as ISO-8601 UTC.")
    parser.add_argument("--repo")
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--task-pr-json", type=Path, help="Offline gh pull-request JSON array/object with items[].")
    parser.add_argument("--publish-artifact", type=Path, help="JSON artifact containing last_publish_at and optional version.")
    parser.add_argument("--dev-latest-commit-at")
    parser.add_argument("--last-publish-at")
    parser.add_argument("--master-promoted-at")
    parser.add_argument("--skip-task-pr", action="store_true")
    parser.add_argument("--skip-dev-publish", action="store_true")
    parser.add_argument("--skip-publish-promote", action="store_true")
    parser.add_argument("--task-pr-threshold-hours", type=float)
    parser.add_argument("--dev-publish-threshold-hours", type=float)
    parser.add_argument("--publish-promote-window-hours", type=float)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_report(
        now=args.now,
        repo=args.repo,
        config_path=args.config_path,
        task_pr_json=args.task_pr_json,
        publish_artifact_path=args.publish_artifact,
        dev_latest_commit_at=args.dev_latest_commit_at,
        last_publish_at=args.last_publish_at,
        master_promoted_at=args.master_promoted_at,
        skip_task_pr=args.skip_task_pr,
        skip_dev_publish=args.skip_dev_publish,
        skip_publish_promote=args.skip_publish_promote,
        task_pr_threshold_hours=args.task_pr_threshold_hours,
        dev_publish_threshold_hours=args.dev_publish_threshold_hours,
        publish_promote_window_hours=args.publish_promote_window_hours,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
