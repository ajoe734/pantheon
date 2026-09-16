"""PR and post-merge push must classify the same source commit identically."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import component_boundary
from scripts.git import check_commit_trailers as checker
from scripts.git import resolve_commit_trailer_range as ranges


TOOLING = ".orchestrator/supervisor.py"
PRODUCT = "services/control-plane/bff/main.py"
AUTH_DOC = "docs/operations/provider-auth-health-contract.md"
MANIFEST = Path("docs/02-architecture/component-boundary.yaml")
MESSAGE = "OPS-CI-TEST-001: exercise delivery classification\n\nLLM-Agent: Codex\nTask-ID: OPS-CI-TEST-001\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def commit(repo: Path, paths: list[str], *, reviewer: str | None = None) -> str:
    for name in paths:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test fixture\n", encoding="utf-8")
    git(repo, "add", "--all")
    message = MESSAGE + (f"Reviewer: {reviewer}\n" if reviewer else "")
    git(repo, "commit", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git(tmp_path, "init", "-b", "dev")
    git(tmp_path, "config", "user.name", "Fixture Author")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "core.hooksPath", "/dev/null")
    git(tmp_path, "config", "commit.gpgsign", "false")
    path = tmp_path / MANIFEST
    path.parent.mkdir(parents=True)
    path.write_text(component_boundary.DEFAULT_MANIFEST.read_text(), encoding="utf-8")
    commit(tmp_path, ["base.txt"], reviewer="Fixture Reviewer")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "CONFIG_FILE", tmp_path / "no-config.json")
    monkeypatch.setattr(ranges, "ROOT", tmp_path)
    monkeypatch.setenv("PANTHEON_TRAILER_CHECK_DISABLED", "0")
    return tmp_path


def scan(monkeypatch: pytest.MonkeyPatch, *selector: str, delivery_class: str = "auto") -> int:
    monkeypatch.setattr(sys, "argv", [
        "check_commit_trailers.py", *selector, "--skip-merge",
        "--delivery-class", delivery_class,
    ])
    return checker.main()


def test_pr_and_merged_push_accept_the_same_tooling_commit(repo, monkeypatch, capsys):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/dev", base)
    git(repo, "checkout", "-b", "fix/auth-contract")
    head = commit(repo, [TOOLING, AUTH_DOC])
    git(repo, "checkout", "dev")
    git(repo, "merge", "--no-ff", head, "-m", "Merge tooling repair")
    merged = git(repo, "rev-parse", "HEAD")
    pr_range = ranges.resolve_commit_range(
        event="pull_request", base_sha=base, head_sha=merged,
        ref_name="5733/merge", pr_base_ref="dev", pr_head_sha=head,
    )
    push_range = ranges.resolve_commit_range(
        event="push", base_sha=base, head_sha=merged, ref_name="dev", pr_base_ref="",
    )
    # Reproduce the old push default's real failure, not just a boolean mock.
    assert scan(monkeypatch, "--range", push_range, delivery_class="product") == 1
    assert "missing trailer: Reviewer" in capsys.readouterr().out
    for event, rev_range in (("pull_request", pr_range), ("push", push_range)):
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        assert scan(monkeypatch, "--range", rev_range) == 0
        assert f"{head}: delivery_class=tooling" in capsys.readouterr().out


@pytest.mark.parametrize("paths,expected", [
    ([TOOLING], 0),
    ([".orchestrator/probe\nservices/control-plane/bff/main.py"], 0),
    ([AUTH_DOC], 0),
    (["scripts/git/check_commit_trailers.py"], 0),
    (["docs/conventions/GIT_WORKFLOW.md"], 0),
    ([".github/workflows/branch-ci.yml"], 0),
    ([PRODUCT], 1),
    ([TOOLING, PRODUCT], 1),
    (["unknown/service.py"], 1),
    ([TOOLING, "unknown/service.py"], 1),
    ([], 1),
])
def test_source_scope_controls_reviewer_requirement(repo, monkeypatch, capsys, paths, expected):
    head = commit(repo, paths)
    # An event-local claim cannot turn product/unknown scope into tooling.
    monkeypatch.setenv("DELIVERY_CLASS", "tooling")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    assert scan(monkeypatch, "--rev", head) == expected
    output = capsys.readouterr().out
    assert ("missing trailer: Reviewer" in output) == bool(expected)


def test_mixed_range_keeps_each_commits_own_requirements(repo, monkeypatch, capsys):
    base = git(repo, "rev-parse", "HEAD")
    tooling = commit(repo, [TOOLING])
    product = commit(repo, [PRODUCT], reviewer="Fixture Reviewer")
    assert scan(monkeypatch, "--range", f"{base}..{product}") == 0
    output = capsys.readouterr().out
    assert f"{tooling}: delivery_class=tooling" in output
    assert f"{product}: delivery_class=product" in output


def test_later_deletion_does_not_hide_an_unreviewed_product_commit(repo, monkeypatch, capsys):
    base = git(repo, "rev-parse", "HEAD")
    product = commit(repo, [PRODUCT])
    git(repo, "rm", PRODUCT)
    end = commit(repo, [TOOLING], reviewer="Fixture Reviewer")
    assert git(repo, "diff", "--name-only", base, end) == TOOLING
    assert scan(monkeypatch, "--range", f"{base}..{end}") == 1
    output = capsys.readouterr().out
    assert f"{product}: delivery_class=product" in output
    assert "missing trailer: Reviewer" in output


def test_product_to_tooling_rename_still_requires_product_review(repo, monkeypatch, capsys):
    commit(repo, [PRODUCT], reviewer="Fixture Reviewer")
    (repo / TOOLING).parent.mkdir(parents=True)
    git(repo, "mv", PRODUCT, TOOLING)
    head = commit(repo, [])
    assert scan(monkeypatch, "--rev", head) == 1
    assert "missing trailer: Reviewer" in capsys.readouterr().out


def test_filename_with_newline_cannot_be_split_into_known_tooling(repo, monkeypatch, capsys):
    head = commit(repo, ["unmapped\n.orchestrator/hidden.py"])
    assert scan(monkeypatch, "--rev", head) == 1
    assert "missing trailer: Reviewer" in capsys.readouterr().out


@pytest.mark.parametrize("reviewer", ["Codex", "self", "n/a"])
def test_tooling_exemption_does_not_accept_a_fabricated_self_reviewer(repo, monkeypatch, reviewer):
    head = commit(repo, [TOOLING], reviewer=reviewer)
    assert scan(monkeypatch, "--rev", head) == 1


def test_root_commit_is_classified_without_a_parent(repo, monkeypatch):
    git(repo, "checkout", "--orphan", "root-fixture")
    git(repo, "rm", "-r", "--cached", ".")
    # Keep the local manifest available to the checker without adding it to
    # this root commit, so the diff consists of one known tooling path.
    path = repo / TOOLING
    path.parent.mkdir(parents=True)
    path.write_text("root fixture\n", encoding="utf-8")
    git(repo, "add", TOOLING)
    git(repo, "commit", "-m", MESSAGE)
    assert scan(monkeypatch, "--rev", "HEAD") == 0


def test_missing_manifest_cannot_silently_exempt_a_commit(repo, monkeypatch):
    head = commit(repo, [TOOLING])
    (repo / MANIFEST).unlink()
    with pytest.raises(component_boundary.BoundaryError):
        scan(monkeypatch, "--rev", head)


def test_unreadable_commit_fails_closed(repo, monkeypatch):
    with pytest.raises(subprocess.CalledProcessError):
        scan(monkeypatch, "--rev", "not-a-commit")


def test_auto_does_not_invent_scope_from_a_commit_message(repo, monkeypatch):
    message = repo / "message.txt"
    message.write_text(MESSAGE, encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        scan(monkeypatch, "--message-file", str(message))
    assert error.value.code == 2


def test_workflow_retires_the_event_label_classifier_and_runs_regressions():
    workflow = yaml.safe_load((component_boundary.ROOT / ".github/workflows/branch-ci.yml").read_text())
    step = next(s for s in workflow["jobs"]["trailers"]["steps"] if s.get("name") == "Check commit trailers")
    assert "DELIVERY_CLASS" not in step["env"]
    assert '--delivery-class auto' in step["run"]
    assert "labels" not in str(step.get("env"))
    assert "python3 -m pip install pyyaml" in step["run"]
    smoke = next(s for s in workflow["jobs"]["smoke"]["steps"] if s.get("name") == "Run tooling integration-authority gate")
    assert "scripts/git/test_commit_delivery_classification.py" in smoke["run"]
