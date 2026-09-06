"""Focused tests for the pre-handoff mechanical gates.

Each scenario reproduces an actual review rejection observed on 2026-09-05/06,
so a regression here means the corresponding round trip becomes possible again.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import handoff_preflight as preflight  # noqa: E402


def _run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _run(["git", "init", "-q", "-b", "main"], root)
    _run(["git", "config", "user.email", "test@example.com"], root)
    _run(["git", "config", "user.name", "Test"], root)
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _run(["git", "add", "seed.txt"], root)
    _run(["git", "commit", "-q", "-m", "seed"], root)
    return root


def _commit(root: Path, relative: str, body: str, message: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _run(["git", "add", relative], root)
    _run(["git", "commit", "-q", "-m", message], root)


# --- artifact scope -------------------------------------------------------

def test_artifact_scope_accepts_files_under_declared_directory():
    result = preflight.check_artifact_scope(
        files=["services/control-plane/bff/personas/routes/detail.py"],
        artifacts=["services/control-plane/bff/personas/**"],
    )
    assert result.ok
    assert "1 changed file" in result.summary


def test_artifact_scope_accepts_exact_declared_file():
    result = preflight.check_artifact_scope(
        files=["scripts/probe_dev_runtime_paper_lifecycle.py"],
        artifacts=["scripts/probe_dev_runtime_paper_lifecycle.py"],
    )
    assert result.ok


def test_artifact_scope_rejects_undeclared_file_and_names_it():
    """REGISTRY-STRATEGY-UNIFIED-CONTRACT-001 lost a cycle to exactly this."""
    result = preflight.check_artifact_scope(
        files=["services/registry/service.py", "services/foundation/pg_store.py"],
        artifacts=["services/registry/**"],
    )
    assert not result.ok
    assert "services/foundation/pg_store.py" in result.details
    assert "services/registry/service.py" not in result.details


def test_artifact_scope_rejects_empty_contract():
    result = preflight.check_artifact_scope(files=["a.py"], artifacts=[])
    assert not result.ok
    assert "a.py" in result.details


def test_artifact_scope_strips_repository_prefix():
    result = preflight.check_artifact_scope(
        files=["services/registry/service.py"],
        artifacts=["pantheon:services/registry/**"],
    )
    assert result.ok


def test_artifact_scope_rejects_traversal_outside_the_repository():
    result = preflight.check_artifact_scope(
        files=["../outside.py"], artifacts=["services/**"]
    )
    assert not result.ok


def test_declared_artifact_paths_drops_unusable_entries():
    allowed = preflight.declared_artifact_paths(["", None, "  ", "services/x/**"])
    assert allowed == ["services/x/**"]


def test_artifact_scope_accepts_a_filename_glob():
    """BFF-TEST-ARCH-001 declares services/control-plane/bff/test_*.py."""
    result = preflight.check_artifact_scope(
        files=["services/control-plane/bff/test_persona_live_integration.py"],
        artifacts=["services/control-plane/bff/test_*.py"],
    )
    assert result.ok, result.render()


def test_filename_glob_does_not_cross_directory_segments():
    result = preflight.check_artifact_scope(
        files=["services/control-plane/bff/nested/test_deep.py"],
        artifacts=["services/control-plane/bff/test_*.py"],
    )
    assert not result.ok


def test_artifact_scope_accepts_a_mid_path_glob():
    """BFF-TEST-ARCH-001 also declares services/control-plane/bff/*/test*.py."""
    result = preflight.check_artifact_scope(
        files=["services/control-plane/bff/personas/test_routes.py"],
        artifacts=["services/control-plane/bff/*/test*.py"],
    )
    assert result.ok, result.render()


def test_double_star_spans_segments():
    result = preflight.check_artifact_scope(
        files=["services/control-plane/bff/tests/deep/nested/test_x.py"],
        artifacts=["services/control-plane/bff/tests/**"],
    )
    assert result.ok, result.render()


def test_artifact_scope_reports_no_changed_files_without_failing():
    result = preflight.check_artifact_scope(files=[], artifacts=["services/x/**"])
    assert result.ok
    assert "nothing to authorize" in result.summary


# --- commit trailers ------------------------------------------------------

def test_commit_trailers_pass_when_every_commit_carries_them(repo):
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()
    _commit(
        repo,
        "svc.py",
        "x = 1\n",
        "TASK-1: add svc\n\nLLM-Agent: Claude\nTask-ID: TASK-1\nReviewer: Codex\n",
    )
    result = preflight.check_commit_trailers(
        repo=repo, base=base, head="HEAD", delivery_class="tooling"
    )
    assert result.ok, result.render()


def test_commit_trailers_fail_and_report_the_offending_commit(repo):
    """BFF-TEST-ARCH-001 and BFF-ROUTER-STRUCT-001 both burned a cycle here."""
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()
    _commit(repo, "svc.py", "x = 1\n", "test(bff): decouple suites")
    result = preflight.check_commit_trailers(
        repo=repo, base=base, head="HEAD", delivery_class="tooling"
    )
    assert not result.ok
    assert result.details


# --- evidence manifest ----------------------------------------------------

def test_evidence_manifest_missing_file_is_reported(repo):
    result = preflight.check_evidence_manifest(
        repo=repo, manifest_path="docs/evidence.json", verify_oids=True
    )
    assert not result.ok
    assert "missing" in result.summary


def test_evidence_manifest_invalid_json_is_reported(repo):
    (repo / "evidence.json").write_text("{not json", encoding="utf-8")
    result = preflight.check_evidence_manifest(
        repo=repo, manifest_path="evidence.json", verify_oids=True
    )
    assert not result.ok
    assert "not readable JSON" in result.summary


def test_evidence_manifest_rejects_unresolvable_commit_id(repo):
    """OSS-COVERAGE-PLAN-001 was rejected for a mis-stated commit id."""
    bogus = "4fd6088d5478d32911029628d5047b5e37c6bbdf"
    (repo / "evidence.json").write_text(
        json.dumps({"verified": f"execute-plans {bogus} is the paired head"}),
        encoding="utf-8",
    )
    result = preflight.check_evidence_manifest(
        repo=repo, manifest_path="evidence.json", verify_oids=True
    )
    assert not result.ok
    assert bogus in result.details


def test_evidence_manifest_accepts_resolvable_commit_id(repo):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()
    (repo / "evidence.json").write_text(
        json.dumps({"nested": {"head_sha": head}}), encoding="utf-8"
    )
    result = preflight.check_evidence_manifest(
        repo=repo, manifest_path="evidence.json", verify_oids=True
    )
    assert result.ok, result.render()


def test_evidence_manifest_can_skip_oid_verification_for_foreign_repos(repo):
    (repo / "evidence.json").write_text(
        json.dumps({"fe": "4fd6088d5478d32911029628d5047b5e37c6bbdf"}),
        encoding="utf-8",
    )
    result = preflight.check_evidence_manifest(
        repo=repo, manifest_path="evidence.json", verify_oids=False
    )
    assert result.ok


def test_evidence_manifest_absent_declaration_is_not_a_failure(repo):
    result = preflight.check_evidence_manifest(
        repo=repo, manifest_path=None, verify_oids=True
    )
    assert result.ok


# --- sibling repositories (paired cross-repo evidence) --------------------

def test_evidence_manifest_resolves_a_sibling_repository_commit(repo, tmp_path):
    """A paired execute-plans head is a legitimate citation, not a defect."""
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    _run(["git", "init", "-q", "-b", "main"], sibling)
    _run(["git", "config", "user.email", "t@e.com"], sibling)
    _run(["git", "config", "user.name", "T"], sibling)
    (sibling / "fe.txt").write_text("fe\n", encoding="utf-8")
    _run(["git", "add", "fe.txt"], sibling)
    _run(["git", "commit", "-q", "-m", "fe"], sibling)
    sibling_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(sibling), capture_output=True, text=True
    ).stdout.strip()

    (repo / "evidence.json").write_text(
        json.dumps({"frontend_sha": sibling_head}), encoding="utf-8"
    )
    without = preflight.check_evidence_manifest(
        repo=repo, manifest_path="evidence.json", verify_oids=True
    )
    assert not without.ok, "sibling commit is unknown to this repository alone"

    with_sibling = preflight.check_evidence_manifest(
        repo=repo,
        manifest_path="evidence.json",
        verify_oids=True,
        siblings=preflight.SiblingRepositories(available=[sibling]),
    )
    assert with_sibling.ok, with_sibling.render()


def test_sibling_repositories_reads_the_coordination_registry(repo, tmp_path):
    sibling = tmp_path / "execute-plans"
    (sibling / ".git").mkdir(parents=True)
    config = repo / ".orchestrator" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "coordination": {
                    "repositories": {
                        "pantheon": {"repo": "ajoe734/pantheon"},
                        "execute_plans": {"local_path": str(sibling)},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    found = preflight.sibling_repositories(repo=repo)
    assert found.available == [sibling]
    assert found.missing == []


def test_sibling_repositories_skips_paths_without_a_checkout(repo):
    config = repo / ".orchestrator" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {"coordination": {"repositories": {"gone": {"local_path": "/nope/missing"}}}}
        ),
        encoding="utf-8",
    )
    found = preflight.sibling_repositories(repo=repo)
    assert found.available == []
    assert found.missing and "gone" in found.missing[0]


def test_sibling_repositories_tolerates_a_missing_config(repo):
    found = preflight.sibling_repositories(repo=repo)
    assert found.available == [] and found.missing == []


# --- manifest path resolution --------------------------------------------

def test_resolve_manifest_path_prefers_the_frozen_delivery_binding():
    task = {
        "delivery_binding": {
            "evidence_manifest": {"path": "docs/deployment/evidence/T/evidence.json"}
        },
        "review_file": "other.json",
        "artifacts": ["docs/deployment/evidence/T/evidence.json"],
    }
    assert (
        preflight.resolve_manifest_path(task)
        == "docs/deployment/evidence/T/evidence.json"
    )


def test_resolve_manifest_path_falls_back_to_declared_artifact():
    task = {"artifacts": ["services/x/**", "docs/deployment/evidence/T/evidence.json"]}
    assert (
        preflight.resolve_manifest_path(task)
        == "docs/deployment/evidence/T/evidence.json"
    )


def test_resolve_manifest_path_returns_none_when_undeclared():
    assert preflight.resolve_manifest_path({"artifacts": ["services/x/**"]}) is None


# --- changed files --------------------------------------------------------

def test_changed_files_lists_the_delivery_diff(repo):
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()
    _commit(repo, "a/one.py", "1\n", "TASK-1: one\n\nTask-ID: TASK-1\n")
    _commit(repo, "b/two.py", "2\n", "TASK-1: two\n\nTask-ID: TASK-1\n")
    assert preflight.changed_files(repo=repo, base=base, head="HEAD") == [
        "a/one.py",
        "b/two.py",
    ]


# --- reporting ------------------------------------------------------------

def test_check_result_renders_details_under_the_headline():
    rendered = preflight.CheckResult(
        "artifact-scope", False, "1 file outside", ["services/x.py"]
    ).render()
    assert rendered.splitlines()[0].startswith("[FAIL] artifact-scope")
    assert "services/x.py" in rendered


def test_main_reports_two_when_the_task_row_cannot_be_read(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no task row")

    monkeypatch.setattr(preflight, "load_task", boom)
    assert preflight.main(["--task-id", "T", "--repo", str(tmp_path)]) == 2
