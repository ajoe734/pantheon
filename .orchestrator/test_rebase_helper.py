"""Tests for rebase_helper.continue_or_skip_empty.

Three cases:
  1. clean_continue  — rebase finishes on the first --continue; no skips
  2. all_empty_skip  — all pending commits are empty; all are skipped
  3. conflict_bailout — a real conflict is detected; rebase is aborted
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rebase_helper import continue_or_skip_empty


def _make_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# Case 1: clean continue
# ---------------------------------------------------------------------------

def test_clean_continue():
    """Rebase finishes with one --continue call and no empty-commit skips."""
    # side_effect calls: (1) initial guard, (2) loop iter 0, (3) loop iter 1 → done
    with (
        patch("rebase_helper._rebase_in_progress", side_effect=[True, True, False]),
        patch("rebase_helper._has_conflicts", return_value=False),
        patch("rebase_helper._nothing_staged", return_value=False),
        patch("rebase_helper.subprocess.run", return_value=_make_proc(0)) as mock_run,
    ):
        result = continue_or_skip_empty("/fake/repo")

    assert result["action"] == "continued"
    assert result["skipped"] == 0
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd == ["git", "-C", "/fake/repo", "rebase", "--continue"]


# ---------------------------------------------------------------------------
# Case 2: all-empty skip
# ---------------------------------------------------------------------------

def test_all_empty_skip():
    """Two consecutive empty commits are auto-skipped; action is 'skipped'."""
    # side_effect calls: (1) initial guard, (2) loop iter 0, (3) loop iter 1, (4) loop iter 2 → done
    with (
        patch("rebase_helper._rebase_in_progress", side_effect=[True, True, True, False]),
        patch("rebase_helper._has_conflicts", return_value=False),
        patch("rebase_helper._nothing_staged", return_value=True),
        patch("rebase_helper.subprocess.run", return_value=_make_proc(0)) as mock_run,
    ):
        result = continue_or_skip_empty("/fake/repo")

    assert result["action"] == "skipped"
    assert result["skipped"] == 2
    assert mock_run.call_count == 2
    for c in mock_run.call_args_list:
        assert c[0][0] == ["git", "-C", "/fake/repo", "rebase", "--skip"]


# ---------------------------------------------------------------------------
# Case 3: real conflict bail-out
# ---------------------------------------------------------------------------

def test_conflict_bailout():
    """A merge conflict triggers --abort; action is 'aborted_with_conflict'."""
    with (
        patch("rebase_helper._rebase_in_progress", return_value=True),
        patch("rebase_helper._has_conflicts", return_value=True),
        patch("rebase_helper.subprocess.run", return_value=_make_proc(0)) as mock_run,
    ):
        result = continue_or_skip_empty("/fake/repo")

    assert result["action"] == "aborted_with_conflict"
    assert "conflict" in result["message"].lower()
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd == ["git", "-C", "/fake/repo", "rebase", "--abort"]


# ---------------------------------------------------------------------------
# Edge: no rebase in progress
# ---------------------------------------------------------------------------

def test_no_rebase_in_progress():
    """Returns no_rebase immediately when no rebase is active."""
    with patch("rebase_helper._rebase_in_progress", return_value=False):
        result = continue_or_skip_empty("/fake/repo")

    assert result["action"] == "no_rebase"
    assert result["skipped"] == 0
