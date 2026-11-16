"""The scheduled-connector fixtures must not leave temp directories behind."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PREFIX = "source_ingest_scheduled_"

_INNER = textwrap.dedent(
    """
    from services.source_ingestion.tests.test_scheduled_connector import client  # noqa: F401


    def test_passes(client):
        http, tempdir, module = client
        assert tempdir.is_dir()


    def test_fails(client):
        http, tempdir, module = client
        assert tempdir.is_dir()
        raise AssertionError("deliberate failure")
    """
)


@pytest.mark.parametrize("selector", ["test_passes", "test_fails"])
def test_client_fixture_removes_its_tempdir(tmp_path: Path, selector: str) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    inner_dir = tmp_path / "inner"
    inner_dir.mkdir()
    inner = inner_dir / "test_inner_tempdir_probe.py"
    inner.write_text(_INNER)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(_REPO_ROOT), f"{inner}::{selector}"],
        cwd=_REPO_ROOT,
        env={**__import__("os").environ, "TMPDIR": str(scratch), "PYTHONPATH": str(_REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=300,
    )
    expected_rc = 1 if selector == "test_fails" else 0
    assert result.returncode == expected_rc, result.stdout + result.stderr
    leftovers = sorted(p.name for p in scratch.iterdir() if p.name.startswith(_PREFIX))
    assert leftovers == [], f"fixture left temp directories behind: {leftovers}"
