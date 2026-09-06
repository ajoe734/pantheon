#!/usr/bin/env python3
"""Fail a delivery locally, before handoff, on the defects review keeps finding.

Every check here already existed somewhere downstream -- commit trailers in the
Branch CI Gate, artifact-contract scope in the reviewer's own reading, evidence
SHA identity in whichever reviewer happened to resolve the commit.  Downstream
is the wrong place: each of those findings costs a full owner->review->reopen
round trip, and the reviewer capacity spent re-deriving a missing ``Task-ID``
trailer is capacity not spent on the defects only a reviewer can find.

The checks are deliberately the mechanical subset.  Nothing here judges whether
the delivery is *correct*; it judges whether the delivery is *submittable*.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

OID_RE = re.compile(r"^[0-9a-f]{40}$")
# Evidence manifests name commits as bare 40-hex strings in free-form fields.
EMBEDDED_OID_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])")

REPO_ROOT = Path(__file__).resolve().parents[2]

# The trailer rules have exactly one definition -- the module the required
# Branch CI Gate job runs.  Its CLI resolves commits against its own checkout
# (cwd=ROOT), so preflight reuses the validator and collects commits itself,
# which is also what lets this run against a worker worktree.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_commit_trailers as trailers  # noqa: E402


@dataclass
class CheckResult:
    """One named gate outcome; ``details`` are the exact offending items."""

    name: str
    ok: bool
    summary: str
    details: list[str] = field(default_factory=list)

    def render(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        lines = [f"[{mark}] {self.name}: {self.summary}"]
        lines.extend(f"        {detail}" for detail in self.details)
        return "\n".join(lines)


def _git(args: Sequence[str], *, repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def changed_files(*, repo: Path, base: str, head: str) -> list[str]:
    """Files this delivery actually touches, base..head, merges included."""

    raw = _git(["diff", "--name-only", f"{base}...{head}"], repo=repo)
    return sorted({line.strip() for line in raw.splitlines() if line.strip()})


def _non_merge_commits(*, repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    """Return (sha, message) for each non-merge commit in base..head."""

    raw = _git(
        ["log", "--no-merges", "--format=%H%x00%B%x1e", f"{base}..{head}"], repo=repo
    )
    items: list[tuple[str, str]] = []
    for chunk in raw.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, _, body = chunk.partition("\x00")
        items.append((sha, body))
    return items


def check_commit_trailers(*, repo: Path, base: str, head: str, delivery_class: str) -> CheckResult:
    """Apply the Branch CI Gate's own trailer rules to this delivery's commits."""

    required, prefix_required = trailers.load_settings()
    required = trailers.required_trailers_for_delivery(required, delivery_class)
    commits = _non_merge_commits(repo=repo, base=base, head=head)
    if not commits:
        return CheckResult(
            "commit-trailers", True, f"no non-merge commits in {base}..{head}"
        )
    details: list[str] = []
    for sha, message in commits:
        problems = trailers.check_message(message, required, prefix_required)
        for problem in problems:
            details.append(f"{sha[:12]}: {problem}")
    if details:
        return CheckResult(
            "commit-trailers",
            False,
            f"{len(details)} trailer problem(s) across {len(commits)} commit(s)",
            details,
        )
    return CheckResult(
        "commit-trailers",
        True,
        f"all {len(commits)} commit(s) in {base}..{head} carry required trailers",
    )


def _clean_relative(path: str) -> str | None:
    candidate = str(path or "").strip().replace("\\", "/").strip("/")
    if not candidate:
        return None
    parts = [part for part in candidate.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _artifact_matcher(pattern: str) -> re.Pattern[str]:
    """Compile one declared artifact into a path matcher.

    Real task contracts mix three shapes -- ``services/x/**`` (a subtree),
    ``services/x/test_*.py`` and ``services/x/*/test*.py`` (globs), and a bare
    file path.  A prefix-only match would silently reject the glob forms, which
    are the ones the BFF test tasks actually declare, so ``*`` is segment-local
    and ``**`` spans segments, matching how the paths read.
    """

    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    body = "".join(out)
    # A declared directory authorizes everything beneath it.
    return re.compile(rf"^{body}(?:/.*)?$")


def declared_artifact_paths(artifacts: Iterable[Any]) -> list[str]:
    """Normalized, repository-relative artifact declarations."""

    allowed: list[str] = []
    for artifact in artifacts or []:
        raw = str(artifact or "")
        # "repo:path" scoping -- only this repository's side is comparable here.
        if ":" in raw and not raw.startswith("/"):
            prefix, _, remainder = raw.partition(":")
            if prefix and "/" not in prefix:
                raw = remainder
        cleaned = _clean_relative(raw)
        if cleaned is not None:
            allowed.append(cleaned)
    return allowed


def check_artifact_scope(
    *,
    files: Sequence[str],
    artifacts: Iterable[Any],
) -> CheckResult:
    """Every changed file must fall under a declared artifact path."""

    allowed = declared_artifact_paths(artifacts)
    if not allowed:
        return CheckResult(
            "artifact-scope",
            False,
            "task declares no artifact contract; nothing authorizes these changes",
            list(files),
        )
    if not files:
        return CheckResult(
            "artifact-scope",
            True,
            "no files changed in this range; nothing to authorize",
        )
    matchers = [_artifact_matcher(pattern) for pattern in allowed]
    outside: list[str] = []
    for name in files:
        cleaned = _clean_relative(name)
        if cleaned is None:
            outside.append(name)
            continue
        if not any(matcher.match(cleaned) for matcher in matchers):
            outside.append(name)
    if outside:
        return CheckResult(
            "artifact-scope",
            False,
            f"{len(outside)} changed file(s) are outside the declared artifact contract",
            [*outside, f"declared: {', '.join(allowed)}"],
        )
    return CheckResult(
        "artifact-scope",
        True,
        f"all {len(files)} changed file(s) fall under the declared contract",
    )


def _commit_exists(oid: str, *, repo: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{oid}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _blob_at(path: str, *, repo: Path, rev: str) -> str | None:
    """Return a file's content at one revision, or None when absent there."""

    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{rev}:{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


@dataclass
class SiblingRepositories:
    """Which configured checkouts are available to resolve a cited commit.

    ``missing`` matters as much as ``available``: an unresolvable commit id
    means something different when every paired repository is present than when
    one of them was never checked out on this host.
    """

    available: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def sibling_repositories(*, repo: Path, config_path: Path | None = None) -> SiblingRepositories:
    """Local checkouts of the other repositories a manifest may legitimately cite.

    Paired deliveries name an execute-plans head as often as a pantheon one, so
    verifying commit identity against this repository alone would reject every
    correct cross-repository citation.  Paths come from the coordination
    registry through its own resolver, so this never becomes a second, drifting
    definition of where a repository lives.
    """

    path = config_path or (repo / ".orchestrator" / "config.json")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SiblingRepositories()

    registry_dir = repo / ".orchestrator"
    resolved: dict[str, Path | None] = {}
    if str(registry_dir) not in sys.path:
        sys.path.insert(0, str(registry_dir))
    try:
        import multi_repo_registry  # noqa: PLC0415

        for repo_id in multi_repo_registry.repositories(config):
            resolved[repo_id] = multi_repo_registry.repository_local_path(config, repo_id)
    except Exception:
        # Fall back to the raw registry rather than losing the check entirely.
        registry = config.get("coordination", {}).get("repositories")
        if not isinstance(registry, Mapping):
            registry = config.get("repositories")
        if isinstance(registry, Mapping):
            for repo_id, entry in registry.items():
                if not isinstance(entry, Mapping):
                    continue
                local = str(entry.get("local_path") or "").strip()
                resolved[repo_id] = Path(local).expanduser() if local else None

    result = SiblingRepositories()
    for repo_id, candidate in sorted(resolved.items()):
        if candidate is None:
            continue
        if candidate.resolve(strict=False) == repo.resolve(strict=False):
            continue
        if (candidate / ".git").exists():
            result.available.append(candidate)
        else:
            result.missing.append(f"{repo_id} -> {candidate} (no checkout)")
    return result


def _iter_strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for value in node.values():
            yield from _iter_strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _iter_strings(value)


def check_evidence_manifest(
    *,
    repo: Path,
    manifest_path: str | None,
    verify_oids: bool,
    head: str = "HEAD",
    siblings: "SiblingRepositories | None" = None,
) -> CheckResult:
    """The manifest must exist, parse, and name only resolvable commits.

    A manifest citing a commit no configured checkout has ever seen is the exact
    defect that sent OSS-COVERAGE-PLAN-001 back through a full review cycle: it
    named a stale execute-plans head as the paired dev SHA.  Resolution spans
    this repository plus the configured sibling checkouts, so a correct paired
    citation passes and only a genuinely unknown commit fails.
    """

    if not manifest_path:
        return CheckResult("evidence-manifest", True, "no manifest declared; nothing to verify")
    # Read the manifest out of the reviewed tree, not the working directory: the
    # delivery under test is a commit, and the checkout running this gate is
    # frequently on some other ref.
    raw = _blob_at(manifest_path, repo=repo, rev=head)
    if raw is None:
        return CheckResult(
            "evidence-manifest",
            False,
            f"declared manifest is not present at {head[:12]}: {manifest_path}",
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return CheckResult(
            "evidence-manifest",
            False,
            f"manifest is not readable JSON: {exc}",
        )
    if not verify_oids:
        return CheckResult(
            "evidence-manifest",
            True,
            f"{manifest_path} parses; commit identity not verified (--verify-evidence-oids off)",
        )
    siblings = siblings or SiblingRepositories()
    searched = [repo, *siblings.available]
    unknown: list[str] = []
    seen: set[str] = set()
    for text in _iter_strings(payload):
        for oid in EMBEDDED_OID_RE.findall(text.lower()):
            if oid in seen:
                continue
            seen.add(oid)
            if not any(_commit_exists(oid, repo=candidate) for candidate in searched):
                unknown.append(oid)
    if unknown:
        rendered = ", ".join(str(candidate) for candidate in searched)
        details = [*unknown, f"searched: {rendered}"]
        # Without every paired checkout, "unresolvable" can mean "not fetched
        # here" rather than "wrong"; say so instead of implying a wrong id.
        details.extend(
            f"not searched: {entry}" for entry in siblings.missing
        )
        return CheckResult(
            "evidence-manifest",
            False,
            f"{len(unknown)} commit id(s) cited by the manifest resolve in no available checkout",
            details,
        )
    return CheckResult(
        "evidence-manifest",
        True,
        f"{manifest_path} parses and all {len(seen)} cited commit id(s) resolve",
    )


def _task_from_payload(payload: Any, *, source: str) -> dict[str, Any]:
    """Accept either the ``show`` envelope or a bare task row."""

    if isinstance(payload, Mapping):
        task = payload.get("task")
        if isinstance(task, Mapping):
            return dict(task)
        if "artifacts" in payload or "id" in payload:
            return dict(payload)
    raise RuntimeError(f"{source} contains no recognizable task row")


def load_task(
    task_id: str,
    *,
    repo: Path,
    task_json: Path | None = None,
) -> dict[str, Any]:
    """Read the canonical task row, or an explicitly supplied snapshot of it.

    The status command is the authority, but it only runs inside a bound
    runtime (``PANTHEON_TASK_STATE_STORE_MODE=authoritative``), which a worker
    already has and an ad-hoc shell does not.  ``--task-json`` keeps the gate
    usable in the second case without inventing a second way to read state.
    """

    if task_json is not None:
        try:
            payload = json.loads(task_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"could not read {task_json}: {exc}") from exc
        return _task_from_payload(payload, source=str(task_json))

    proc = subprocess.run(
        [sys.executable, "scripts/ai_status.py", "show", task_id],
        capture_output=True,
        text=True,
        cwd=str(repo),
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "no output"
        raise RuntimeError(
            f"ai_status show {task_id} failed: {detail}. Run this inside the worker "
            "status runtime, or pass --task-json with the task row."
        )
    return _task_from_payload(json.loads(proc.stdout), source=f"ai_status show {task_id}")


def resolve_manifest_path(task: Mapping[str, Any]) -> str | None:
    binding = task.get("delivery_binding")
    if isinstance(binding, Mapping):
        manifest = binding.get("evidence_manifest")
        if isinstance(manifest, Mapping):
            declared = str(manifest.get("path") or "").strip()
            if declared:
                return declared
    review_file = str(task.get("review_file") or "").strip()
    if review_file:
        return review_file
    for artifact in task.get("artifacts") or []:
        candidate = str(artifact or "").strip()
        if candidate.endswith("evidence.json"):
            return candidate
    return None


def run_preflight(
    *,
    repo: Path,
    task_id: str,
    base: str,
    head: str,
    delivery_class: str,
    verify_evidence_oids: bool,
    task_json: Path | None = None,
) -> list[CheckResult]:
    task = load_task(task_id, repo=repo, task_json=task_json)
    files = changed_files(repo=repo, base=base, head=head)
    return [
        check_commit_trailers(
            repo=repo, base=base, head=head, delivery_class=delivery_class
        ),
        check_artifact_scope(files=files, artifacts=task.get("artifacts") or []),
        check_evidence_manifest(
            repo=repo,
            manifest_path=resolve_manifest_path(task),
            verify_oids=verify_evidence_oids,
            head=head,
            siblings=sibling_repositories(repo=repo),
        ),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the mechanical delivery gates locally, before handoff, so a "
            "reviewer never spends a cycle on them."
        )
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--base", default="origin/dev")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", default=".", help="Repository or worktree root")
    parser.add_argument(
        "--task-json",
        type=Path,
        default=None,
        help=(
            "Read the task row from this file instead of the status command "
            "(for use outside a bound worker status runtime)"
        ),
    )
    parser.add_argument(
        "--delivery-class",
        choices=("product", "tooling"),
        default="product",
    )
    parser.add_argument(
        "--verify-evidence-oids",
        dest="verify_evidence_oids",
        action="store_true",
        default=True,
        help="Require every commit id cited by the evidence manifest to resolve (default)",
    )
    parser.add_argument(
        "--no-verify-evidence-oids",
        dest="verify_evidence_oids",
        action="store_false",
        help="Skip commit-identity checks for deliveries citing another repository",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        results = run_preflight(
            repo=repo,
            task_id=args.task_id,
            base=args.base,
            head=args.head,
            delivery_class=args.delivery_class,
            verify_evidence_oids=args.verify_evidence_oids,
            task_json=args.task_json,
        )
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"handoff preflight could not run: {exc}", file=sys.stderr)
        return 2
    for result in results:
        print(result.render())
    failed = [result for result in results if not result.ok]
    if failed:
        names = ", ".join(result.name for result in failed)
        print(
            f"\nhandoff preflight FAILED ({names}). Fix these before handoff; "
            "they are the exact checks review and CI apply downstream.",
            file=sys.stderr,
        )
        return 1
    print("\nhandoff preflight passed; mechanical gates are clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
