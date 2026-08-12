from __future__ import annotations

import json
import types
from pathlib import Path

from check_config_drift import (
    DEFAULT_INTENTIONAL_OVERRIDES,
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


def test_coordination_enable_is_actionable_with_default_overrides() -> None:
    repo = {"coordination": {"enabled": False}}
    live = {"coordination": {"enabled": True}}

    report = find_drift(
        repo,
        live,
        critical_flags=("coordination.enabled",),
        overrides=DEFAULT_INTENTIONAL_OVERRIDES,
    )

    assert report["intentional"] == []
    assert report["drift"] == [
        {"path": "coordination.enabled", "repo": False, "live": True}
    ]


def test_progress_lease_policy_drift_is_actionable_by_default() -> None:
    repo = {
        "supervisor": {
            "observe_worker_commit_progress": True,
            "lease_requires_work_progress": True,
        },
        "worker_runtime": {
            "worker_lease_seconds": 600,
            "work_progress_stale_seconds": 360,
        },
    }
    live = {
        "supervisor": {
            "observe_worker_commit_progress": False,
            "lease_requires_work_progress": False,
        },
        "worker_runtime": {
            "worker_lease_seconds": 1800,
            "work_progress_stale_seconds": 900,
        },
    }

    report = find_drift(repo, live)

    assert report["intentional"] == []
    assert {item["path"] for item in report["drift"]} == {
        "supervisor.observe_worker_commit_progress",
        "supervisor.lease_requires_work_progress",
        "worker_runtime.worker_lease_seconds",
        "worker_runtime.work_progress_stale_seconds",
    }


def test_ready_dispatcher_capacity_drift_is_actionable_by_default() -> None:
    repo = {
        "ready_dispatcher": {
            "sidecar_only_agents": [],
            "target_workload": {"Codex": 30, "Codex2": 30},
            "max_dispatches_per_tick": 10,
            "max_active_workers_per_task": 1,
            "max_concurrent_workers": 13,
        }
    }
    live = {
        "ready_dispatcher": {
            "sidecar_only_agents": ["Codex"],
            "target_workload": {"Codex": 0, "Codex2": 0},
            "max_dispatches_per_tick": 1,
            "max_active_workers_per_task": 2,
            "max_concurrent_workers": 1,
        }
    }

    report = find_drift(repo, live)

    assert report["intentional"] == []
    assert {item["path"] for item in report["drift"]} == {
        "ready_dispatcher.sidecar_only_agents",
        "ready_dispatcher.target_workload",
        "ready_dispatcher.max_dispatches_per_tick",
        "ready_dispatcher.max_active_workers_per_task",
        "ready_dispatcher.max_concurrent_workers",
    }


def test_task_state_shadow_mode_drift_is_actionable_by_default() -> None:
    report = find_drift(
        {"task_state_store": {"mode": "shadow"}},
        {"task_state_store": {"mode": "off"}},
    )

    assert report["intentional"] == []
    assert report["drift"] == [
        {"path": "task_state_store.mode", "repo": "shadow", "live": "off"}
    ]


def test_find_drift_missing_flag_is_reported_not_drift() -> None:
    report = find_drift({}, {}, critical_flags=("ready_dispatcher.enabled",), overrides=frozenset())
    assert report["drift"] == []
    assert report["missing"][0]["path"] == "ready_dispatcher.enabled"


def test_find_drift_repo_owned_flag_missing_from_live_is_actionable() -> None:
    report = find_drift(
        {"ready_dispatcher": {"max_concurrent_per_account": {"codex1": 4}}},
        {"ready_dispatcher": {}},
        critical_flags=("ready_dispatcher.max_concurrent_per_account",),
        overrides=frozenset(),
    )

    assert report["missing"] == []
    assert report["drift"] == [
        {
            "path": "ready_dispatcher.max_concurrent_per_account",
            "repo": {"codex1": 4},
            "live": None,
        }
    ]


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
    commands = []

    def runner(cmd, **kwargs):
        commands.append(cmd)
        if "rev-list" in cmd:
            return types.SimpleNamespace(returncode=0, stdout="22\n")
        return types.SimpleNamespace(returncode=0, stdout="")

    assert git_commits_behind(Path("/x"), "origin/dev", runner=runner) == 22
    assert commands[0] == [
        "git",
        "-C",
        "/x",
        "fetch",
        "--quiet",
        "origin",
        "dev:refs/remotes/origin/dev",
    ]


def test_git_commits_behind_none_on_failure() -> None:
    def runner(cmd, **kwargs):
        return types.SimpleNamespace(returncode=1, stdout="")
    assert git_commits_behind(Path("/x"), "origin/dev", runner=runner) is None


def test_main_fix_aligns_drift(tmp_path: Path) -> None:
    repo = tmp_path / "repo.json"
    live = tmp_path / "live.json"
    repo.write_text(json.dumps({"ready_dispatcher": {"enabled": True}}))
    live.write_text(json.dumps({"ready_dispatcher": {"enabled": False}}))
    # without --fix: exit 1 (actionable drift)
    rc = main(["--repo-config", str(repo), "--live-config", str(live)])
    assert rc == 1
    # with --fix: live aligned, exit 0
    rc = main(["--repo-config", str(repo), "--live-config", str(live), "--fix"])
    assert rc == 0
    assert json.loads(live.read_text())["ready_dispatcher"]["enabled"] is True


def test_main_fix_adds_repo_owned_flag_missing_from_live(tmp_path: Path) -> None:
    repo = tmp_path / "repo.json"
    live = tmp_path / "live.json"
    repo.write_text(
        json.dumps({"ready_dispatcher": {"max_concurrent_per_account": {"codex1": 4}}})
    )
    live.write_text(json.dumps({"ready_dispatcher": {}}))

    rc = main(
        [
            "--repo-config",
            str(repo),
            "--live-config",
            str(live),
            "--fix",
        ]
    )

    assert rc == 0
    assert json.loads(live.read_text())["ready_dispatcher"][
        "max_concurrent_per_account"
    ] == {"codex1": 4}


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
