"""Tests for scripts/previous_release_reader.py (RC1 WP1.5 harness)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import previous_release_reader as pr  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    _git(root, "init", "-q")
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "mod.py").write_text("VERSION = 'old'\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "old")
    (root / "pkg" / "mod.py").write_text("VERSION = 'new'\n")
    _git(root, "commit", "-qam", "new")
    return root


@pytest.fixture()
def driver(tmp_path: Path) -> Path:
    path = tmp_path / "driver.py"
    path.write_text(
        "import json, os, sys\n"
        "import pkg.mod as m\n"
        "print(json.dumps({'version': m.VERSION, 'file': m.__file__, 'tree': os.environ['PREVIOUS_RELEASE_TREE'], 'args': sys.argv[1:]}))\n"
    )
    return path


def test_good_revision_loads_old_code_not_working_tree(repo: Path, driver: Path):
    old = _git(repo, "rev-parse", "HEAD~1")
    result = pr.run_driver(old, driver, paths=["pkg"], modules=["pkg.mod"], driver_args=["a", "b"], repo=repo)
    payload = result.json()
    assert payload["version"] == "old"  # working tree says 'new'
    assert payload["args"] == ["a", "b"]
    assert payload["file"].startswith(payload["tree"])
    assert (repo / "pkg" / "mod.py").read_text() == "VERSION = 'new'\n"  # working tree untouched
    assert _git(repo, "status", "--porcelain") == ""


def test_temp_tree_removed_afterwards(repo: Path, driver: Path):
    old = _git(repo, "rev-parse", "HEAD~1")
    tree = Path(pr.run_driver(old, driver, paths=["pkg"], repo=repo).json()["tree"])
    assert not tree.exists()
    with pytest.raises(pr.DriverFailed):
        bad = driver.with_name("bad.py")
        bad.write_text("raise SystemExit(5)\n")
        pr.run_driver(old, bad, paths=["pkg"], repo=repo)
    leftovers = [p for p in Path(tempfile.gettempdir()).glob("previous-release-*") if p.is_dir() and p == tree]
    assert leftovers == []
    with pr.materialised_tree(old, ["pkg"], repo=repo) as inside:
        kept = inside
        assert (inside / "pkg" / "mod.py").is_file()
    assert not kept.exists()


def test_missing_revision_fails_readably_and_never_skips(repo: Path, driver: Path):
    with pytest.raises(pr.RevisionUnavailable, match="revision-unavailable: deadbeef00"):
        pr.run_driver("deadbeef00", driver, paths=["pkg"], repo=repo)
    code = pr.main(["--revision", "deadbeef00", "--path", "pkg", "--driver", str(driver)])
    assert code == pr.EXIT_UNAVAILABLE


def test_missing_path_fails_readably(repo: Path, driver: Path):
    with pytest.raises(pr.PathUnavailable, match="path-unavailable: nope"):
        pr.run_driver("HEAD", driver, paths=["nope"], repo=repo)


def test_module_outside_tree_is_refused(repo: Path, driver: Path):
    # 'json' resolves to the stdlib, not the revision tree: must not pass silently.
    with pytest.raises(pr.DriverFailed, match="module-outside-tree"):
        pr.run_driver("HEAD", driver, paths=["pkg"], modules=["json"], repo=repo)


def test_cli_roundtrip(repo: Path, driver: Path, capsys, monkeypatch):
    monkeypatch.setattr(pr, "REPO_ROOT", repo)
    old = _git(repo, "rev-parse", "HEAD~1")
    monkeypatch.setattr(pr, "run_driver", lambda *a, _r=pr.run_driver, **k: _r(*a, repo=repo, **k))
    assert pr.main(["--revision", old, "--path", "pkg", "--module", "pkg.mod", "--driver", str(driver)]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == "old"
