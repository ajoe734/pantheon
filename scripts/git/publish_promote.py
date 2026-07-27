#!/usr/bin/env python3
"""Discover publish snapshots ready for promotion to master and open PRs.

Reads policy from .orchestrator/config.json `branch_workflow.promote`
(falls back to legacy `wave_workflow.promote` if a migration hasn't run):

  soak_days                 — minimum days since the release/<VER> tag
  regression_label_prefix   — any open issue with this label prefix blocks promote
  block_labels              — additional explicit block labels on issues
  promote_pr_label          — label applied to the opened PR

Subcommands:
  discover  --github-output <path>   write candidate list to a GITHUB_OUTPUT file
  open-prs                            read PROMOTE_CANDIDATES env var and open PRs

The PR shape is `promote/<VER>` branched from current master with a merge
commit for `publish/<VER>`. `master-release.yml` reacts on merge.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / ".orchestrator" / "config.json"

# Report both legacy YYYY.WW.P and current YYYY.MM.DD.N tags. Only the format
# configured by nightly_publish.version_format is eligible for automation.
RELEASE_TAG_RE = re.compile(r"^refs/tags/release/(v\d{4}\.\d{2}(?:\.\d+){1,2})$")
DAILY_RELEASE_RE = re.compile(r"^v\d{4}\.\d{2}\.\d{2}\.\d+$")
BRANCH_CI_WORKFLOW = "branch-ci.yml"
REQUIRED_PROMOTE_CHECKS = frozenset(
    {
        "Commit trailers",
        "Runtime mirror guard",
        "Smoke acceptance",
    }
)


class PromotionConflict(RuntimeError):
    """An expected per-candidate merge conflict that must not stop the train."""


def load_promote_settings() -> dict:
    cfg: dict = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            cfg = {}
    # Prefer new key, fall back to legacy for transition window.
    wf = cfg.get("branch_workflow") or cfg.get("wave_workflow") or {}
    promote = wf.get("promote") or {}
    nightly = wf.get("nightly_publish") or {}
    return {
        "main_branch": wf.get("main_branch", "master"),
        "publish_branch_prefix": wf.get("publish_branch_prefix", "publish/"),
        "release_tag_prefix": wf.get("release_tag_prefix", "release/"),
        "soak_days": int(promote.get("soak_days", 1)),
        "regression_label_prefix": promote.get("regression_label_prefix", "regression/"),
        "block_labels": list(promote.get("block_labels") or []),
        "promote_pr_label": promote.get("promote_pr_label", "auto-promote"),
        "version_format": nightly.get("version_format", "vYYYY.MM.DD.N"),
    }


def run_git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, cwd=ROOT
    ).stdout.strip()


def _run_git_result(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=ROOT
    )


def _result_detail(proc: subprocess.CompletedProcess[str]) -> str:
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return detail[0] if detail else f"command exited {proc.returncode}"


def git_is_ancestor(ancestor: str, descendant: str) -> bool:
    return _run_git_result("merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def fetch_promote_refs(main_branch: str) -> None:
    """Fetch only the refs required to classify immutable publish snapshots."""
    proc = _run_git_result(
        "fetch",
        "origin",
        main_branch,
        "+refs/heads/publish/*:refs/remotes/origin/publish/*",
        "--tags",
        "--prune",
        "--quiet",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"failed to fetch promote refs: {_result_detail(proc)}")


def publish_ref_matches_tag(version: str, publish_prefix: str, release_prefix: str) -> bool:
    publish_ref = f"origin/{publish_prefix}{version}"
    release_ref = f"refs/tags/{release_prefix}{version}^{{commit}}"
    release = _run_git_result("rev-parse", release_ref)
    if release.returncode != 0:
        return False
    publish = _run_git_result("rev-parse", publish_ref)
    # Publish branches are retention-managed after their immutable release tag
    # is cut. A pruned branch is valid; a surviving branch that drifted is not.
    if publish.returncode != 0:
        return True
    return publish.stdout.strip() == release.stdout.strip()


def _gh_api_rows(endpoint: str, jq_filter: str) -> tuple[list[dict], str | None]:
    """Return paginated REST rows without relying on GitHub GraphQL."""

    proc = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            "--method",
            "GET",
            endpoint,
            "--jq",
            jq_filter,
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if proc.returncode != 0:
        return [], _result_detail(proc)
    rows: list[dict] = []
    try:
        for line in proc.stdout.splitlines():
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    except json.JSONDecodeError:
        return [], "GitHub REST API returned invalid JSON"
    return rows, None


def _list_open_pull_rows(main_branch: str | None = None) -> tuple[list[dict], str | None]:
    query = "state=open&per_page=100"
    if main_branch:
        query += f"&base={quote(main_branch, safe='')}"
    return _gh_api_rows(
        f"repos/{{owner}}/{{repo}}/pulls?{query}",
        ".[] | {number,html_url,head:{ref:.head.ref,sha:.head.sha}} | @json",
    )


def _required_check_rollup(head_sha: str) -> tuple[list[dict], str | None]:
    return _gh_api_rows(
        f"repos/{{owner}}/{{repo}}/commits/{head_sha}/check-runs?per_page=100",
        ".check_runs[] | {name,status,conclusion} | @json",
    )


def _normalize_pull(row: dict) -> dict:
    head = row.get("head") or {}
    return {
        "number": row.get("number"),
        "url": row.get("html_url"),
        "headRefName": head.get("ref"),
        "headRefOid": head.get("sha"),
    }


def find_open_promote_pr(promote_branch: str) -> tuple[dict | None, str | None]:
    """Return the open PR for a promote branch, failing closed on API errors."""
    rows, error = _list_open_pull_rows()
    if error:
        return None, error
    for raw in rows:
        pr = _normalize_pull(raw)
        if pr["headRefName"] != promote_branch:
            continue
        head_sha = str(pr.get("headRefOid") or "")
        if not head_sha:
            return None, "existing promote PR did not expose an exact head SHA"
        checks, check_error = _required_check_rollup(head_sha)
        if check_error:
            return None, check_error
        pr["statusCheckRollup"] = checks
        return pr, None
    return None, None


def list_open_promote_prs(main_branch: str) -> tuple[dict[str, dict], str | None]:
    """Load the promote PR backlog in one API call for hourly discovery."""
    rows, error = _list_open_pull_rows(main_branch)
    if error:
        return {}, error
    normalized = [_normalize_pull(row) for row in rows]
    return {
        row["headRefName"]: row
        for row in normalized
        if str(row.get("headRefName", "")).startswith("promote/")
    }, None


def fetch_blocking_issue_map(
    prefix: str, block_labels: list[str]
) -> tuple[dict[str, list[str]], list[str], str | None]:
    """Load version-specific and global regression blockers in one API call."""
    if not os.environ.get("GH_TOKEN"):
        return {}, [], None
    issues, error = _gh_api_rows(
        "repos/{owner}/{repo}/issues?state=open&per_page=100",
        ".[] | {number,title,labels,pull_request} | @json",
    )
    if error:
        return {}, [], error

    by_version: dict[str, list[str]] = {}
    global_blockers: list[str] = []
    for issue in issues:
        if issue.get("pull_request"):
            continue
        names = {label.get("name", "") for label in issue.get("labels", [])}
        summary = f"#{issue['number']} {issue['title']}"
        for name in names:
            if name.startswith(prefix) and name[len(prefix) :]:
                by_version.setdefault(name[len(prefix) :], []).append(summary)
            if name in block_labels:
                global_blockers.append(f"{summary} (label: {name})")
    return by_version, list(dict.fromkeys(global_blockers)), None


def missing_required_promote_checks(pr: dict) -> list[str]:
    """Return required Branch CI contexts not yet attached to this PR head."""

    present = {
        str(check.get("name", "")).strip()
        for check in pr.get("statusCheckRollup") or []
        if isinstance(check, dict)
    }
    return sorted(REQUIRED_PROMOTE_CHECKS - present)


def dispatch_promote_ci(
    promote_branch: str, expected_head_sha: str, pr_number: int | str
) -> None:
    """Dispatch Branch CI on the immutable promote head.

    A PR opened by a workflow's GITHUB_TOKEN does not recursively trigger the
    pull_request workflow. workflow_dispatch is the supported recursive event,
    and selecting the promote ref attaches the required check runs to that
    exact PR head rather than to dev.
    """

    if not re.fullmatch(r"[0-9a-f]{40}", expected_head_sha):
        raise RuntimeError("promote PR head must be a full lowercase commit SHA")
    subprocess.run(
        [
            "gh",
            "workflow",
            "run",
            BRANCH_CI_WORKFLOW,
            "--ref",
            promote_branch,
            "-f",
            f"expected_head_sha={expected_head_sha}",
            "-f",
            f"promote_pr_number={pr_number}",
        ],
        check=True,
        cwd=ROOT,
    )


def assess_promotion_mode(main_ref: str, release_ref: str) -> tuple[str, str]:
    """Classify how a release can be joined to master without mutating refs.

    A clean common-history merge uses the normal merge path.  Pantheon's master
    was re-rooted in July 2026, so immutable dev snapshots can legitimately have
    no common ancestor with it.  That case is handled by a two-parent snapshot
    bridge whose tree is exactly the release tree.  Content conflicts with a
    real merge base remain fail-closed for manual reconciliation.
    """
    base = _run_git_result("merge-base", main_ref, release_ref)
    # Git versions differ in whether a no-base result exits 0 or 1. The
    # presence of a base SHA is the portable signal; an empty successful
    # result must not be misclassified as a content merge.
    if base.returncode in {0, 1} and not base.stdout.strip():
        return "snapshot_bridge", "histories are unrelated"
    if base.returncode != 0:
        return "error", _result_detail(base)
    merge_tree = _run_git_result("merge-tree", "--write-tree", main_ref, release_ref)
    if merge_tree.returncode == 0:
        return "clean_merge", "merge-tree preflight succeeded"
    if merge_tree.returncode == 1:
        # A publish tag is an immutable, already-gated full snapshot. For the
        # single maximal candidate, preserving a textual conflict resolution
        # can retain stale master-only fragments and produce a tree that was
        # never accepted on dev. Build a two-parent merge whose tree is exactly
        # the release tree instead; parent 1 keeps rollback to master and parent
        # 2 makes every accepted release commit reachable.
        return (
            "snapshot_replace",
            "standard merge conflicted; promote the exact immutable release tree",
        )
    return "error", _result_detail(merge_tree)


def ensure_git_identity() -> None:
    """Configure a committer identity if the checkout has none.

    The promote flow creates a `git merge --no-ff` merge commit. GitHub's
    actions/checkout does NOT set user.name/user.email, so the merge aborts with
    exit 128 ("Committer identity unknown") -- which silently broke every
    scheduled publish-promote run and stalled dev->master promotion. Set the
    github-actions bot identity, but only when unset so local runs keep theirs.
    """
    for key, value in (
        ("user.email", "github-actions[bot]@users.noreply.github.com"),
        ("user.name", "github-actions[bot]"),
    ):
        existing = subprocess.run(
            ["git", "config", key], capture_output=True, text=True, cwd=ROOT
        )
        if existing.returncode != 0 or not existing.stdout.strip():
            subprocess.run(["git", "config", key, value], check=False, cwd=ROOT)


def list_release_tags() -> list[tuple[str, datetime]]:
    """Return [(version, tagged_at)] for every release/v*.*.* tag on origin."""
    out = run_git(
        "for-each-ref",
        "--format=%(refname) %(creatordate:iso-strict)",
        "refs/tags/release/",
    )
    items: list[tuple[str, datetime]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        ref, _, ts = line.partition(" ")
        m = RELEASE_TAG_RE.match(ref)
        if not m:
            continue
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        items.append((m.group(1), when))
    return items


def discover(
    input_version: str | None,
    soak_days: int,
    prefix: str,
    block_labels: list[str],
    publish_prefix: str,
    *,
    main_branch: str = "master",
    release_prefix: str = "release/",
    version_format: str = "vYYYY.MM.DD.N",
) -> list[dict]:
    """Return a deterministic disposition for every selected release tag."""
    now = datetime.now(timezone.utc)
    candidates: list[dict] = []

    # A lookup failure must not be mistaken for "not merged" or "no PR".
    fetch_promote_refs(main_branch)

    tags = sorted(list_release_tags(), key=lambda item: (item[1], item[0]))
    requested = None
    if input_version:
        requested = input_version if input_version.startswith("v") else f"v{input_version}"

    open_prs, pr_lookup_error = list_open_promote_prs(main_branch)
    version_blockers, global_blockers, issue_lookup_error = fetch_blocking_issue_map(
        prefix, block_labels
    )
    lookup_error = pr_lookup_error or issue_lookup_error

    for version, tagged_at in tags:
        age_days = (now - tagged_at).total_seconds() / 86400.0
        release_ref = f"refs/tags/{release_prefix}{version}"
        publish_branch = f"{publish_prefix}{version}"
        promote_branch = f"promote/{version}"
        candidate = {
            "version": version,
            "publish_branch": publish_branch,
            "release_ref": release_ref,
            "age_days": round(age_days, 2),
            "tagged_at": tagged_at.isoformat(),
            "blockers": [],
            "promote_branch": promote_branch,
            "disposition": "pending",
        }
        candidates.append(candidate)

        if version_format == "vYYYY.MM.DD.N" and not DAILY_RELEASE_RE.fullmatch(version):
            candidate["disposition"] = "legacy_format"
            candidate["detail"] = (
                f"version does not match configured {version_format}; report only"
            )
            continue

        if age_days < soak_days and version != requested:
            candidate["disposition"] = "soaking"
            candidate["detail"] = f"requires {soak_days} soak days"
            continue

        if not publish_ref_matches_tag(version, publish_prefix, release_prefix):
            candidate["disposition"] = "invalid_publish_ref"
            candidate["detail"] = "immutable release tag is missing or surviving publish branch drifted"
            continue

        main_ref = f"origin/{main_branch}"
        if git_is_ancestor(release_ref, main_ref) or git_is_ancestor(
            f"origin/{publish_branch}", main_ref
        ):
            candidate["disposition"] = "already_reachable"
            candidate["detail"] = f"{version} is already reachable from {main_ref}"
            continue

        if lookup_error:
            candidate["disposition"] = "lookup_failed"
            candidate["detail"] = lookup_error
            continue

        blockers = list(version_blockers.get(version, [])) + global_blockers
        candidate["blockers"] = blockers
        if blockers:
            candidate["disposition"] = "blocked"
            candidate["detail"] = "; ".join(blockers)
            continue

        existing_pr = open_prs.get(promote_branch)
        if existing_pr:
            candidate["existing_pr"] = existing_pr

        mode, detail = assess_promotion_mode(main_ref, release_ref)
        candidate["promotion_mode"] = mode
        candidate["detail"] = detail
        if mode in {"conflicted", "error"}:
            candidate["disposition"] = mode

    # Only maximal promotable snapshots are eligible.  If release A is an
    # ancestor of release B, promoting B makes A reachable and opening both
    # PRs would create duplicate historical promotion work.
    promotable = [
        candidate
        for candidate in candidates
        if candidate["disposition"] == "pending"
        and candidate.get("promotion_mode")
        in {
            "clean_merge",
            "snapshot_bridge",
            "snapshot_replace",
        }
    ]
    terminal_snapshots: list[dict] = []
    for candidate in reversed(promotable):
        descendants = [
            terminal
            for terminal in terminal_snapshots
            if git_is_ancestor(candidate["release_ref"], terminal["release_ref"])
        ]
        if descendants:
            successor = max(
                descendants, key=lambda item: (item["tagged_at"], item["version"])
            )
            candidate["disposition"] = "superseded"
            candidate["superseded_by"] = successor["version"]
        elif candidate.get("existing_pr"):
            # Keep the bulk PR lookup cheap. Asking GraphQL for
            # statusCheckRollup across up to 1,000 PRs caused repeatable 502s
            # in runs 30284788017 and 30284856368. The action step performs one
            # exact-branch lookup and dispatches only when that PR head is
            # actually missing a required context.
            candidate["disposition"] = "existing_pr"
            terminal_snapshots.append(candidate)
        else:
            candidate["disposition"] = "eligible"
            terminal_snapshots.append(candidate)

    if requested:
        return [candidate for candidate in candidates if candidate["version"] == requested]
    return candidates


def append_step_summary(title: str, candidates: list[dict]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a") as fh:
        fh.write(f"## {title}\n\n")
        fh.write("| Version | Disposition | Mode | Detail |\n")
        fh.write("|---|---|---|---|\n")
        for candidate in candidates:
            detail = str(candidate.get("detail", "")).replace("|", "\\|")
            fh.write(
                f"| {candidate.get('version', '')} | "
                f"{candidate.get('disposition', '')} | "
                f"{candidate.get('promotion_mode', '')} | {detail} |\n"
            )


def cmd_discover(args: argparse.Namespace) -> int:
    settings = load_promote_settings()
    candidates = discover(
        args.version,
        settings["soak_days"],
        settings["regression_label_prefix"],
        settings["block_labels"],
        settings["publish_branch_prefix"],
        main_branch=settings["main_branch"],
        release_prefix=settings["release_tag_prefix"],
        version_format=settings["version_format"],
    )
    eligible = [c for c in candidates if c["disposition"] == "eligible"]
    actionable = [
        c
        for c in candidates
        if c["disposition"] in {"eligible", "existing_pr"}
    ]
    if args.github_output:
        with open(args.github_output, "a") as fh:
            fh.write(f"candidate_count={len(actionable)}\n")
            fh.write("candidates<<__EOC__\n")
            fh.write(json.dumps(actionable))
            fh.write("\n__EOC__\n")
            fh.write("dispositions<<__EOD__\n")
            fh.write(json.dumps(candidates))
            fh.write("\n__EOD__\n")
    append_step_summary("Publish candidate dispositions", candidates)
    print(
        json.dumps(
            {
                "counts": dict(Counter(c["disposition"] for c in candidates)),
                "all": candidates,
                "eligible": eligible,
                "actionable": actionable,
            },
            indent=2,
        )
    )
    return 0


def create_snapshot_bridge(version: str, main_ref: str, release_ref: str) -> str:
    """Create a rollback-safe two-parent commit with the exact release tree."""
    tree = run_git("rev-parse", f"{release_ref}^{{tree}}")
    commit = run_git(
        "commit-tree",
        tree,
        "-p",
        main_ref,
        "-p",
        release_ref,
        "-m",
        f"promote: {version}",
    )
    run_git("reset", "--hard", commit)
    if run_git("rev-parse", "HEAD^{tree}") != tree:
        raise RuntimeError("snapshot bridge tree does not match the immutable release tree")
    return commit


def open_candidate(cand: dict, settings: dict) -> dict:
    version = cand["version"]
    publish_branch = cand["publish_branch"]
    promote_branch = cand["promote_branch"]
    main_branch = settings["main_branch"]
    release_ref = f"refs/tags/{settings['release_tag_prefix']}{version}"
    main_ref = f"origin/{main_branch}"
    promote_label = settings["promote_pr_label"]

    run_git("fetch", "origin", main_branch, "--tags")
    if not publish_ref_matches_tag(
        version, settings["publish_branch_prefix"], settings["release_tag_prefix"]
    ):
        raise RuntimeError("release tag is missing or surviving publish branch drifted")

    existing_pr, lookup_error = find_open_promote_pr(promote_branch)
    if lookup_error:
        raise RuntimeError(f"existing PR lookup failed: {lookup_error}")
    if existing_pr:
        missing = missing_required_promote_checks(existing_pr)
        if not missing:
            return {
                "version": version,
                "disposition": "existing_pr",
                "detail": existing_pr.get("url", "existing promote PR"),
            }
        promote_head = str(existing_pr.get("headRefOid", "")).strip()
        if not promote_head:
            raise RuntimeError("existing promote PR did not expose an exact head SHA")
        dispatch_promote_ci(promote_branch, promote_head, existing_pr["number"])
        subprocess.run(
            ["gh", "pr", "merge", promote_branch, "--auto", "--merge"],
            check=False,
            cwd=ROOT,
        )
        return {
            "version": version,
            "disposition": "ci_dispatched",
            "missing_required_checks": missing,
            "head_sha": promote_head,
            "detail": (
                f"dispatched required Branch CI for existing PR "
                f"#{existing_pr['number']} at {promote_head}"
            ),
        }

    run_git("checkout", "-B", promote_branch, main_ref)
    promotion_mode = cand.get("promotion_mode")
    if promotion_mode in {"snapshot_bridge", "snapshot_replace"}:
        create_snapshot_bridge(version, main_ref, release_ref)
    else:
        try:
            merge_args = ["merge", "--no-ff", "--no-edit", "-m", f"promote: {version}"]
            run_git(*merge_args, release_ref)
        except subprocess.CalledProcessError as exc:
            _run_git_result("merge", "--abort")
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise PromotionConflict(detail) from exc

    # A normal push is intentionally the only publication path.  The PR and
    # auto-merge request leave master behind its required protected checks.
    run_git("push", "-u", "origin", promote_branch)
    promote_head = run_git("rev-parse", "HEAD")

    body = (
        f"Auto-generated promotion of `{publish_branch}` "
        f"(release tag `release/{version}`) into `{main_branch}`.\n\n"
        f"- soak age: {cand['age_days']} days\n"
        f"- blockers: {cand['blockers'] or 'none'}\n"
        f"- promotion mode: {cand.get('promotion_mode', 'clean_merge')}\n\n"
        "Merging this PR will trigger `master-release.yml` to tag "
        f"`prod/{version}` on master. The PR remains subject to all "
        "required branch-protection checks."
    )
    subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            main_branch,
            "--head",
            promote_branch,
            "--title",
            f"Promote {version} to {main_branch}",
            "--body",
            body,
        ],
        check=True,
        cwd=ROOT,
    )
    opened_pr, lookup_error = find_open_promote_pr(promote_branch)
    if lookup_error:
        raise RuntimeError(f"created PR lookup failed: {lookup_error}")
    if not opened_pr:
        raise RuntimeError("created promote PR was not visible to exact-head CI dispatch")
    if str(opened_pr.get("headRefOid", "")).strip() != promote_head:
        raise RuntimeError(
            "created promote PR head changed before required CI dispatch"
        )
    if promote_label:
        subprocess.run(
            ["gh", "pr", "edit", promote_branch, "--add-label", promote_label],
            check=False,
            cwd=ROOT,
        )
    dispatch_promote_ci(promote_branch, promote_head, opened_pr["number"])
    subprocess.run(
        ["gh", "pr", "merge", promote_branch, "--auto", "--merge"],
        check=False,
        cwd=ROOT,
    )
    return {
        "version": version,
        "disposition": "pr_opened",
        "promotion_mode": cand.get("promotion_mode", "clean_merge"),
        "head_sha": promote_head,
        "detail": (
            f"opened {promote_branch}, dispatched required Branch CI, "
            "and requested protected auto-merge"
        ),
    }


def cmd_open_prs(_args: argparse.Namespace) -> int:
    settings = load_promote_settings()
    ensure_git_identity()
    raw = os.environ.get("PROMOTE_CANDIDATES", "[]")
    try:
        candidates = json.loads(raw)
    except json.JSONDecodeError:
        print(f"PROMOTE_CANDIDATES is not valid JSON: {raw[:80]}", file=sys.stderr)
        return 2
    if not isinstance(candidates, list):
        return 0
    results: list[dict] = []
    had_error = False
    for cand in candidates:
        version = cand.get("version", "unknown")
        try:
            results.append(open_candidate(cand, settings))
        except PromotionConflict as exc:
            results.append(
                {
                    "version": version,
                    "disposition": "conflicted",
                    "detail": str(exc).splitlines()[0],
                }
            )
        except (KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
            had_error = True
            detail = getattr(exc, "stderr", None) or str(exc)
            results.append(
                {
                    "version": version,
                    "disposition": "error",
                    "detail": str(detail).strip().splitlines()[0],
                }
            )
    append_step_summary("Promote PR results", results)
    print(json.dumps({"results": results}, indent=2))
    return 1 if had_error else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--github-output")
    d.add_argument("--version", default=None)
    d.set_defaults(func=cmd_discover)
    o = sub.add_parser("open-prs")
    o.set_defaults(func=cmd_open_prs)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
