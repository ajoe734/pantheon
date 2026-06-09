from __future__ import annotations

import json
import types
from pathlib import Path

from check_config_drift import (
    find_drift,
    get_dotted,
    set_dotted,
    git_commits_behind,
    main,
)


def test_find_drift_flags_nonallowlisted_toggle() -> None:
    repo = {"chair_review": {"enabled": True}}
    live = {"chair_review": {"enabled": False}}
    report = find_drift(repo, live, critical_flags=("chair_review.enabled",), overrides=frozenset())
    assert len(report["drift"]) == 1
    assert report["drift"][0]["path"] == "chair_review.enabled"
    assert report["drift"][0]["repo"] is True and report["drift"][0]["live"] is False


def test_find_drift_allowlisted_override_is_not_drift() -> None:
    repo = {"coordination": {"enabled": True}}
    live = {"coordination": {"enabled": False}}
    report = find_drift(repo, live, critical_flags=("coordination.enabled",),
                        overrides=frozenset({"coordination.enabled"}))
    assert report["drift"] == []
    assert len(report["intentional"]) == 1
    assert report["intentional"][0]["path"] == "coordination.enabled"


def test_find_drift_missing_flag_is_reported_not_drift() -> None:
    report = find_drift({}, {}, critical_flags=("ready_dispatcher.enabled",), overrides=frozenset())
    assert report["drift"] == []
    assert report["missing"][0]["path"] == "ready_dispatcher.enabled"


def test_find_drift_equal_values_are_clean() -> None:
    repo = {"chair_review": {"enabled": True}}
    live = {"chair_review": {"enabled": True}}
    report = find_drift(repo, live, critical_flags=("chair_review.enabled",), overrides=frozenset())
    assert report == {"drift": [], "intentional": [], "missing": []}


def test_set_get_dotted_roundtrip() -> None:
    d: dict = {}
    set_dotted(d, "a.b.c", 5)
    assert get_dotted(d, "a.b.c") == 5
    assert d == {"a": {"b": {"c": 5}}}


def test_git_commits_behind_parses_count() -> None:
    def runner(cmd, **kwargs):
        if "rev-list" in cmd:
            return types.SimpleNamespace(returncode=0, stdout="22\n")
        return types.SimpleNamespace(returncode=0, stdout="")
    assert git_commits_behind(Path("/x"), "origin/dev", runner=runner) == 22


def test_git_commits_behind_none_on_failure() -> None:
    def runner(cmd, **kwargs):
        return types.SimpleNamespace(returncode=1, stdout="")
    assert git_commits_behind(Path("/x"), "origin/dev", runner=runner) is None


def test_main_fix_aligns_drift(tmp_path: Path) -> None:
    repo = tmp_path / "repo.json"
    live = tmp_path / "live.json"
    repo.write_text(json.dumps({"chair_review": {"enabled": True}}))
    live.write_text(json.dumps({"chair_review": {"enabled": False}}))
    # without --fix: exit 1 (actionable drift)
    rc = main(["--repo-config", str(repo), "--live-config", str(live)])
    assert rc == 1
    # with --fix: live aligned, exit 0
    rc = main(["--repo-config", str(repo), "--live-config", str(live), "--fix"])
    assert rc == 0
    assert json.loads(live.read_text())["chair_review"]["enabled"] is True


def test_main_behind_fails_only_when_threshold_exceeded(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo.json"
    live = tmp_path / "live.json"
    repo.write_text(json.dumps({"chair_review": {"enabled": True}}))
    live.write_text(json.dumps({"chair_review": {"enabled": True}}))
    import check_config_drift
    monkeypatch.setattr(check_config_drift, "git_commits_behind", lambda *a, **k: 22)
    # no threshold -> behind reported but exit 0
    assert main(["--repo-config", str(repo), "--live-config", str(live), "--dev-root", "/x"]) == 0
    # threshold exceeded -> exit 1
    assert main(["--repo-config", str(repo), "--live-config", str(live),
                 "--dev-root", "/x", "--max-behind", "5"]) == 1
