from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from check_config_drift import (
    DEFAULT_INTENTIONAL_OVERRIDES,
    find_drift,
    fleet_capacity_errors,
    review_bridge_policy_errors,
    find_repository_integration_drift,
    find_repository_source_drift,
    get_dotted,
    set_dotted,
    git_commits_behind,
    main,
)


def test_repository_source_root_drift_requires_promotion() -> None:
    report = find_repository_source_drift(
        {"coordination": {"repositories": {"pantheon": {"local_path": "/old/dev-root"}}}},
        {"pantheon": "/new/dev-root", "execute_plans": "/code/execute-plans"},
    )

    assert report == [
        {
            "repository_id": "pantheon",
            "expected_local_path": "/new/dev-root",
            "live_local_path": "/old/dev-root",
        },
        {
            "repository_id": "execute_plans",
            "expected_local_path": "/code/execute-plans",
            "live_local_path": None,
        },
    ]


def test_repository_integration_root_drift_requires_promotion() -> None:
    report = find_repository_integration_drift(
        {
            "coordination": {
                "repositories": {
                    "pantheon": {"integration_path": "/integration/pantheon/old"}
                }
            }
        },
        {
            "pantheon": "/integration/pantheon/new",
            "execute_plans": "/integration/execute_plans/head",
        },
    )

    assert report == [
        {
            "repository_id": "pantheon",
            "expected_integration_path": "/integration/pantheon/new",
            "live_integration_path": "/integration/pantheon/old",
        },
        {
            "repository_id": "execute_plans",
            "expected_integration_path": "/integration/execute_plans/head",
            "live_integration_path": None,
        },
    ]


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
            "max_dispatches_per_tick": 10,
            "max_active_workers_per_task": 1,
            "max_concurrent_workers": 13,
        }
    }
    live = {
        "ready_dispatcher": {
            "max_dispatches_per_tick": 1,
            "max_active_workers_per_task": 2,
            "max_concurrent_workers": 1,
        }
    }

    report = find_drift(repo, live)

    assert report["intentional"] == []
    assert {item["path"] for item in report["drift"]} == {
        "ready_dispatcher.max_dispatches_per_tick",
        "ready_dispatcher.max_active_workers_per_task",
        "ready_dispatcher.max_concurrent_workers",
    }


def test_task_state_store_mode_drift_is_actionable_by_default() -> None:
    report = find_drift(
        {"task_state_store": {"mode": "authoritative"}},
        {"task_state_store": {"mode": "invalid"}},
    )

    assert report["intentional"] == []
    assert report["drift"] == [
        {"path": "task_state_store.mode", "repo": "authoritative", "live": "invalid"}
    ]


def test_owner_fallbacks_do_not_retry_shared_claude_account() -> None:
    config = json.loads(
        (Path(__file__).resolve().parents[1] / ".orchestrator" / "config.json").read_text(
            encoding="utf-8"
        )
    )
    fallbacks = config["worker_reassignment"]["owner_fallbacks"]

    # Claude and Claude2 share one provider account. Neither identity may be
    # used as an owner fallback for the other or re-entered from another lane.
    for candidates in fallbacks.values():
        assert "Claude" not in candidates
        assert "Claude2" not in candidates


def test_worker_reassignment_drift_is_actionable_by_default() -> None:
    repo = {
        "worker_reassignment": {
            "enabled": True,
            "max_reassignments_per_cycle": 4,
            "owner_fallbacks": {"Codex": ["Codex2"]},
            "reviewer_fallbacks": {"Codex": ["Claude"]},
        }
    }
    live = {
        "worker_reassignment": {
            "enabled": False,
            "max_reassignments_per_cycle": 0,
            "owner_fallbacks": {"Codex": ["Claude", "Antigravity"]},
            "reviewer_fallbacks": {"Codex": ["Claude"]},
        }
    }

    report = find_drift(repo, live)

    assert report["intentional"] == []
    assert {item["path"] for item in report["drift"]} == {
        "worker_reassignment.enabled",
        "worker_reassignment.max_reassignments_per_cycle",
        "worker_reassignment.owner_fallbacks",
    }


def test_failure_loop_drift_is_actionable_by_default() -> None:
    repo = {
        "worker_reassignment": {
            "failure_loop": {
                "enabled": True,
                "max_failures_in_window": 3,
                "window_seconds": 3600,
                "max_auto_reassignments": 1,
            }
        }
    }
    live = {
        "worker_reassignment": {
            "failure_loop": {
                "enabled": False,
                "max_failures_in_window": 3,
                "window_seconds": 3600,
                "max_auto_reassignments": 1,
            }
        }
    }

    report = find_drift(repo, live)

    assert report["intentional"] == []
    assert report["drift"] == [
        {
            "path": "worker_reassignment.failure_loop",
            "repo": repo["worker_reassignment"]["failure_loop"],
            "live": live["worker_reassignment"]["failure_loop"],
        }
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


BASE_REVIEW_POLICY = {
    "review_gate": {"github_review_bridge_required": False},
    "branch_workflow": {
        "task_pr": {
            "required_status_checks": [
                "Commit trailers",
                "Runtime mirror guard",
                "Smoke acceptance",
            ]
        }
    },
}


def test_main_fix_aligns_drift(tmp_path: Path) -> None:
    repo = tmp_path / "repo.json"
    live = tmp_path / "live.json"
    repo.write_text(json.dumps({"ready_dispatcher": {"enabled": True, "max_concurrent_workers": 13}, **BASE_REVIEW_POLICY}))
    live.write_text(json.dumps({"ready_dispatcher": {"enabled": False, "max_concurrent_workers": 13}, **BASE_REVIEW_POLICY}))
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
        json.dumps({
            "ready_dispatcher": {"max_concurrent_workers": 13, "max_concurrent_per_account": {"codex1": 4}},
            **BASE_REVIEW_POLICY,
        })
    )
    live.write_text(json.dumps({"ready_dispatcher": {"max_concurrent_workers": 13}, **BASE_REVIEW_POLICY}))

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
    config = {"chair_review": {"enabled": True}, "ready_dispatcher": {"max_concurrent_workers": 13}, **BASE_REVIEW_POLICY}
    repo.write_text(json.dumps(config))
    live.write_text(json.dumps(config))
    import check_config_drift
    monkeypatch.setattr(check_config_drift, "git_commits_behind", lambda *a, **k: 22)
    # no threshold -> behind reported but exit 0
    assert main(["--repo-config", str(repo), "--live-config", str(live), "--dev-root", "/x"]) == 0
    # threshold exceeded -> exit 1
    assert main(["--repo-config", str(repo), "--live-config", str(live),
                 "--dev-root", "/x", "--max-behind", "5"]) == 1


@pytest.mark.parametrize("invalid", [
    {}, {"ready_dispatcher": {}}, {"ready_dispatcher": None},
] + [
    {"ready_dispatcher": {"max_concurrent_workers": v}}
    for v in (None, True, False, "13", "", 13.0, 13.5, -1, [], {})
] + [
    {"ready_dispatcher": {"max_concurrent_workers": 13}, "watchdog": {"max_active_workers": v}}
    for v in (12, 13, 14, None, True, "13", [], {})
] + [
    {"ready_dispatcher": {"max_concurrent_workers": 13}, "watchdog": v}
    for v in (None, [], False)
])
def test_invalid_fleet_contract_fails_even_when_equal_and_fix_requested(tmp_path, capsys, invalid):
    repo = tmp_path / "repo.json"
    live = tmp_path / "live.json"
    payload = json.dumps(invalid)
    repo.write_text(payload)
    live.write_text(payload)
    assert len(fleet_capacity_errors(invalid, invalid)) == 2
    assert main(["--repo-config", str(repo), "--live-config", str(live), "--json", "--fix"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert len(report["fleet_capacity_errors"]) == 2
    assert report["fixed"] == []
    assert live.read_text() == payload


def test_fleet_contract_drift_is_actionable_and_valid_shape_passes(tmp_path, capsys):
    repo = tmp_path / "repo.json"
    live = tmp_path / "live.json"
    valid = {"ready_dispatcher": {"max_concurrent_workers": 13}, **BASE_REVIEW_POLICY}
    repo.write_text(json.dumps(valid))
    live.write_text(json.dumps({"ready_dispatcher": {"max_concurrent_workers": 14}, **BASE_REVIEW_POLICY}))
    assert main(["--repo-config", str(repo), "--live-config", str(live), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["fleet_capacity_errors"] == []
    assert report["drift"] == [{"path": "ready_dispatcher.max_concurrent_workers", "repo": 13, "live": 14}]
    live.write_text(json.dumps(valid))
    assert main(["--repo-config", str(repo), "--live-config", str(live)]) == 0


def test_review_bridge_policy_errors_flags_invalid_or_contradictory_shapes() -> None:
    valid_false = {
        "review_gate": {"github_review_bridge_required": False},
        "branch_workflow": {
            "task_pr": {
                "required_status_checks": [
                    "Commit trailers",
                    "Runtime mirror guard",
                    "Smoke acceptance",
                ]
            }
        },
    }
    valid_true = {
        "review_gate": {"github_review_bridge_required": True},
        "branch_workflow": {
            "task_pr": {
                "required_status_checks": [
                    "Commit trailers",
                    "Runtime mirror guard",
                    "Smoke acceptance",
                    "Pantheon canonical review gate",
                ]
            }
        },
    }
    contradictory_false = {
        "review_gate": {"github_review_bridge_required": False},
        "branch_workflow": {
            "task_pr": {
                "required_status_checks": [
                    "Commit trailers",
                    "Runtime mirror guard",
                    "Smoke acceptance",
                    "Pantheon canonical review gate",
                ]
            }
        },
    }

    # Both valid false
    assert review_bridge_policy_errors(valid_false, valid_false) == []
    # Both valid true
    assert review_bridge_policy_errors(valid_true, valid_true) == []

    # Repo valid, live contradictory
    errors = review_bridge_policy_errors(valid_false, contradictory_false)
    assert len(errors) == 1
    assert errors[0]["source"] == "live"
    assert "contradictory review bridge policy" in errors[0]["error"]

    # Repo contradictory, live valid
    errors = review_bridge_policy_errors(contradictory_false, valid_false)
    assert len(errors) == 1
    assert errors[0]["source"] == "repo"
    assert "contradictory review bridge policy" in errors[0]["error"]

    # Both contradictory
    errors = review_bridge_policy_errors(contradictory_false, contradictory_false)
    assert len(errors) == 2
    assert {e["source"] for e in errors} == {"repo", "live"}

    # Both empty dictionaries fail closed on missing whole sections
    errors = review_bridge_policy_errors({}, {})
    assert len(errors) == 2
    assert {e["source"] for e in errors} == {"repo", "live"}
    for err in errors:
        assert "review_gate configuration is required" in err["error"]

    # One empty, one valid
    errors = review_bridge_policy_errors({}, valid_false)
    assert len(errors) == 1
    assert errors[0]["source"] == "repo"
    assert "review_gate configuration is required" in errors[0]["error"]

    # Absent and malformed whole sections fail closed
    malformed_repo = {"review_gate": "invalid", "branch_workflow": {"task_pr": {"required_status_checks": []}}}
    malformed_live = {"review_gate": {"github_review_bridge_required": False}, "branch_workflow": 12345}
    errors = review_bridge_policy_errors(malformed_repo, malformed_live)
    assert len(errors) == 2
    assert {e["source"] for e in errors} == {"repo", "live"}
    repo_err = next(e for e in errors if e["source"] == "repo")
    live_err = next(e for e in errors if e["source"] == "live")
    assert "review_gate configuration is required and must be a mapping" in repo_err["error"]
    assert "branch_workflow configuration is required and must be a mapping" in live_err["error"]


def test_find_drift_flags_review_bridge_policy_drift() -> None:
    repo = {
        "review_gate": {"github_review_bridge_required": False},
        "branch_workflow": {
            "task_pr": {
                "required_status_checks": [
                    "Commit trailers",
                    "Runtime mirror guard",
                    "Smoke acceptance",
                ]
            }
        },
    }
    live = {
        "review_gate": {"github_review_bridge_required": True},
        "branch_workflow": {
            "task_pr": {
                "required_status_checks": [
                    "Commit trailers",
                    "Runtime mirror guard",
                    "Smoke acceptance",
                    "Pantheon canonical review gate",
                ]
            }
        },
    }

    report = find_drift(repo, live)
    assert report["intentional"] == []
    drift_paths = {item["path"]: item for item in report["drift"]}
    assert "review_gate.github_review_bridge_required" in drift_paths
    assert drift_paths["review_gate.github_review_bridge_required"]["repo"] is False
    assert drift_paths["review_gate.github_review_bridge_required"]["live"] is True
    assert "branch_workflow.task_pr.required_status_checks" in drift_paths


def test_main_rejects_contradictory_review_bridge_policy_even_when_equal_and_fix_requested(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contradictory = {
        "ready_dispatcher": {"max_concurrent_workers": 13},
        "review_gate": {"github_review_bridge_required": False},
        "branch_workflow": {
            "task_pr": {
                "required_status_checks": [
                    "Commit trailers",
                    "Runtime mirror guard",
                    "Smoke acceptance",
                    "Pantheon canonical review gate",
                ]
            }
        },
    }
    repo = tmp_path / "repo.json"
    live = tmp_path / "live.json"
    payload = json.dumps(contradictory)
    repo.write_text(payload)
    live.write_text(payload)

    assert main(["--repo-config", str(repo), "--live-config", str(live), "--json", "--fix"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert len(report["review_bridge_policy_errors"]) == 2
    assert report["fixed"] == []
    assert live.read_text() == payload


def test_main_passes_with_valid_aligned_false_review_bridge_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    valid_cfg = {
        "ready_dispatcher": {"max_concurrent_workers": 13},
        "review_gate": {"github_review_bridge_required": False},
        "branch_workflow": {
            "task_pr": {
                "required_status_checks": [
                    "Commit trailers",
                    "Runtime mirror guard",
                    "Smoke acceptance",
                ]
            }
        },
    }
    repo = tmp_path / "repo.json"
    live = tmp_path / "live.json"
    payload = json.dumps(valid_cfg)
    repo.write_text(payload)
    live.write_text(payload)

    assert main(["--repo-config", str(repo), "--live-config", str(live), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["review_bridge_policy_errors"] == []
    assert report["drift"] == []
