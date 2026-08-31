from __future__ import annotations

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dispatch_policy import (
    ALLOWLISTED_EXECUTION_RESOURCES,
    DEFAULT_ACTIVE_WORKER_STATUSES,
    REASON_OWNED_FINALIZE,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_READY,
    REASON_REVIEW_READY,
    dispatch_reason_priority,
    is_execution_dispatch_reason,
    is_operator_exact_head_acceptance,
    normalize_execution_resources,
    normalized_status_set,
    ready_dispatch_settings,
    task_execution_resources,
)


def operator_accepted_task(**overrides):
    head_sha = "b" * 40
    task = {
        "id": "ABC-001",
        "status": "review_approved",
        "review_binding": {
            "pr": 100,
            "head_sha": head_sha,
            "head_branch": "task/ABC-001",
            "base": "dev",
        },
        "operator_acceptance": {
            "pr": 100,
            "head_sha": head_sha,
            "head_branch": "task/ABC-001",
            "base": "dev",
            "decision": "operator-accept",
            "actor": "Human/Ops",
            "mode": "operator_exact_head",
            "operator_acceptance_proof_ref": (
                "refs/tags/pantheon-review/operator-accept/" + head_sha
            ),
        },
    }
    task.update(overrides)
    return task


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (REASON_REVIEW_READY, 0),
        (REASON_OWNED_FINALIZE, 1),
        (REASON_OWNED_IN_PROGRESS, 2),
        (REASON_OWNED_READY, 3),
        ("legacy_noncanonical_dispatch", None),
        (None, None),
    ],
)
def test_dispatch_reason_priority_cases(reason: str | None, expected: int | None) -> None:
    assert dispatch_reason_priority(reason) == expected


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (REASON_REVIEW_READY, True),
        (REASON_OWNED_FINALIZE, True),
        (REASON_OWNED_IN_PROGRESS, True),
        (REASON_OWNED_READY, True),
        ("legacy_noncanonical_dispatch", False),
        (None, False),
    ],
)
def test_is_execution_dispatch_reason_cases(reason: str | None, expected: bool) -> None:
    assert is_execution_dispatch_reason(reason) is expected


def test_operator_exact_head_acceptance_is_a_non_worker_lane() -> None:
    assert is_operator_exact_head_acceptance(operator_accepted_task()) is True


@pytest.mark.parametrize(
    "task",
    [
        operator_accepted_task(status="in_progress"),
        operator_accepted_task(operator_acceptance={}),
        operator_accepted_task(
            operator_acceptance={
                **operator_accepted_task()["operator_acceptance"],
                "actor": "Codex",
            }
        ),
        operator_accepted_task(
            operator_acceptance={
                **operator_accepted_task()["operator_acceptance"],
                "head_sha": "c" * 40,
            }
        ),
    ],
)
def test_malformed_operator_acceptance_does_not_suppress_dispatch(task) -> None:
    assert is_operator_exact_head_acceptance(task) is False


def test_ready_dispatch_settings_current_defaults() -> None:
    settings = ready_dispatch_settings({})
    assert settings["enabled"] is True
    assert settings["review_statuses"] == ["review"]
    assert settings["finalize_statuses"] == ["review_approved"]
    assert settings["owned_statuses"] == ["in_progress", "todo"]
    assert settings["dependency_done_statuses"] == ["done"]
    assert settings["worker_terminal_statuses"] == ["review", "done", "review_approved"]
    assert settings["active_worker_statuses"] == DEFAULT_ACTIVE_WORKER_STATUSES
    assert settings["max_dispatches_per_tick"] == 4
    assert settings["max_concurrent_per_account"] == {}
    assert settings["execution_resource_limits"] == {"pantheon-dev": 1}
    for retired in (
        "disabled_agents",
        "max_tasks_per_agent",
        "max_tasks_per_agent_by_agent",
        "max_concurrent_per_quota_group",
        "priority_preemption_grace_seconds",
    ):
        assert retired not in settings


def test_ready_dispatch_settings_preserves_only_supplied_current_values() -> None:
    settings = ready_dispatch_settings(
        {
            "ready_dispatcher": {
                "review_statuses": ["needs_review"],
                "finalize_statuses": ["approved"],
                "owned_statuses": ["queued"],
                "max_dispatches_per_tick": 8,
                "max_concurrent_per_account": {"codex": 2},
            }
        }
    )
    assert settings["review_statuses"] == ["needs_review"]
    assert settings["finalize_statuses"] == ["approved"]
    assert settings["owned_statuses"] == ["queued"]
    assert settings["max_dispatches_per_tick"] == 8
    assert settings["max_concurrent_per_account"] == {"codex": 2}


def test_normalized_status_set_is_case_normalized() -> None:
    assert normalized_status_set(["Review", "DONE"], ["todo"]) == {"review", "done"}


def test_ready_dispatch_settings_execution_resource_limits() -> None:
    # Default is {'pantheon-dev': 1}
    assert ready_dispatch_settings({})["execution_resource_limits"] == {"pantheon-dev": 1}
    assert ready_dispatch_settings({"ready_dispatcher": {}})["execution_resource_limits"] == {"pantheon-dev": 1}
    assert ready_dispatch_settings({
        "ready_dispatcher": {"execution_resource_limits": {"pantheon-dev": 1}}
    })["execution_resource_limits"] == {"pantheon-dev": 1}

    # Rejection cases
    with pytest.raises(ValueError, match="boolean True is not allowed"):
        ready_dispatch_settings({"ready_dispatcher": {"execution_resource_limits": {"pantheon-dev": True}}})

    with pytest.raises(ValueError, match="expected int, got str"):
        ready_dispatch_settings({"ready_dispatcher": {"execution_resource_limits": {"pantheon-dev": "1"}}})

    with pytest.raises(ValueError, match="value must be 1, got 0"):
        ready_dispatch_settings({"ready_dispatcher": {"execution_resource_limits": {"pantheon-dev": 0}}})

    with pytest.raises(ValueError, match="value must be 1, got 2"):
        ready_dispatch_settings({"ready_dispatcher": {"execution_resource_limits": {"pantheon-dev": 2}}})

    with pytest.raises(ValueError, match="Unknown execution resource limit key"):
        ready_dispatch_settings({"ready_dispatcher": {"execution_resource_limits": {"custom-res": 1}}})


def test_normalize_execution_resources_valid_and_normalization() -> None:
    assert ALLOWLISTED_EXECUTION_RESOURCES == frozenset({"pantheon-dev"})
    assert normalize_execution_resources([]) == []
    assert normalize_execution_resources(["pantheon-dev"]) == ["pantheon-dev"]
    assert normalize_execution_resources(["  PANTHEON-DEV  "]) == ["pantheon-dev"]


def test_normalize_execution_resources_rejections() -> None:
    # Explicit null
    with pytest.raises(ValueError, match="must be a list, got null"):
        normalize_execution_resources(None)

    # Non-list
    with pytest.raises(ValueError, match="must be a list"):
        normalize_execution_resources("pantheon-dev")
    with pytest.raises(ValueError, match="must be a list"):
        normalize_execution_resources(123)
    with pytest.raises(ValueError, match="must be a list"):
        normalize_execution_resources({"pantheon-dev": 1})

    # Non-string element
    with pytest.raises(ValueError, match="elements must be strings"):
        normalize_execution_resources([123])
    with pytest.raises(ValueError, match="elements must be strings"):
        normalize_execution_resources([None])

    # Empty / whitespace string element
    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_execution_resources([""])
    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_execution_resources(["   "])

    # Unallowlisted resource
    with pytest.raises(ValueError, match="unallowlisted resource"):
        normalize_execution_resources(["unknown-res"])
    with pytest.raises(ValueError, match="allowlisted execution resources"):
        normalize_execution_resources(["vm-staging"])

    # Duplicate resource
    with pytest.raises(ValueError, match="duplicate resource"):
        normalize_execution_resources(["pantheon-dev", "pantheon-dev"])
    with pytest.raises(ValueError, match="duplicate resource"):
        normalize_execution_resources(["pantheon-dev", "  PANTHEON-DEV "])


def test_task_execution_resources_cases() -> None:
    # Omitted => []
    assert task_execution_resources(None) == []
    assert task_execution_resources({}) == []
    assert task_execution_resources({"id": "TASK-1"}) == []
    assert task_execution_resources({"id": "TASK-1", "execution_resources": []}) == []

    # Valid
    assert task_execution_resources({"id": "TASK-1", "execution_resources": ["pantheon-dev"]}) == ["pantheon-dev"]

    # Fails closed on explicit null / malformed / unallowlisted
    with pytest.raises(ValueError, match="must be a list, got null"):
        task_execution_resources({"id": "TASK-1", "execution_resources": None})
    with pytest.raises(ValueError, match="elements must be strings"):
        task_execution_resources({"id": "TASK-1", "execution_resources": [123]})
    with pytest.raises(ValueError, match="cannot be empty"):
        task_execution_resources({"id": "TASK-1", "execution_resources": [""]})
    with pytest.raises(ValueError, match="unallowlisted resource"):
        task_execution_resources({"id": "TASK-1", "execution_resources": ["bad"]})
    with pytest.raises(ValueError, match="duplicate resource"):
        task_execution_resources({"id": "TASK-1", "execution_resources": ["pantheon-dev", "pantheon-dev"]})


# DTG-CLEAN-M6 characterization tests for the candidate-evaluation/admission
# functions moved from supervisor.py -- not a re-test of
# .orchestrator/test_supervisor.py's extensive dispatch coverage (which
# already exercises this exact code through supervisor.py's re-export and
# continues to pass unchanged), but proof that this module is genuinely
# usable on its own: no circular import, the lazy supervisor handback
# resolves, and explain/live parity holds structurally (one function, both
# callers).


def test_module_imports_with_no_circular_dependency() -> None:
    # supervisor.py imports dispatch_policy at its own top level; importing
    # supervisor here (a second, independent path into the same dependency
    # graph) must not raise, proving the graph is a DAG (supervisor ->
    # dispatch_policy -> {common, rewrite.dispatch_admission,
    # rewrite.task_machine, task_archive}, with the reverse edge only ever
    # taken lazily, at call time, via _supervisor_module()).
    import supervisor  # noqa: F401


def test_lazy_supervisor_handback_resolves() -> None:
    import dispatch_policy

    supervisor = dispatch_policy._supervisor_module()
    for name in (
        "_admission_health_records",
        "parse_runtime_timestamp",
        "account_concurrency_limit",
        "agent_account_id",
        "build_dispatch_event",
        "delivery_lane_for_agent",
        "dependencies_satisfied",
        "dispatch_loop_agent_ids",
        "ready_dispatch_max_concurrent_workers",
        "review_decision_intent_replay_eligible",
        "runtime_delivery_health",
        "task_review_requeue_intent",
        "task_review_requeue_record",
    ):
        assert hasattr(supervisor, name), name


def test_explain_and_live_dispatch_share_one_candidate_function() -> None:
    """explain_dispatch_for_task and the live dispatch loop must evaluate
    every candidate through the exact same function object -- the
    architectural guarantee that makes 'explain' trustworthy."""

    import dispatch_policy
    import supervisor

    assert supervisor.evaluate_dispatch_candidate is dispatch_policy.evaluate_dispatch_candidate
    assert (
        supervisor.evaluate_task_delivery_admission
        is dispatch_policy.evaluate_task_delivery_admission
    )


def test_entry_points_are_exported() -> None:
    import dispatch_policy

    for name in (
        "build_delivery_admission_snapshot",
        "evaluate_task_delivery_admission",
        "dispatch_event_is_in_unchanged_cooldown",
        "task_review_requeue_is_materialized",
        "evaluate_dispatch_candidate",
    ):
        assert callable(getattr(dispatch_policy, name)), name


def test_dispatch_event_cooldown_is_pure_and_time_bounded() -> None:
    from dispatch_policy import dispatch_event_is_in_unchanged_cooldown

    now = "2026-08-31T00:10:00Z"
    seen = {"evt-1": "2026-08-31T00:05:00Z"}
    assert dispatch_event_is_in_unchanged_cooldown(
        seen, "evt-1", cooldown_seconds=900, now=now
    )
    assert not dispatch_event_is_in_unchanged_cooldown(
        seen, "evt-1", cooldown_seconds=60, now=now
    )
    assert not dispatch_event_is_in_unchanged_cooldown(
        seen, "evt-missing", cooldown_seconds=900, now=now
    )
    assert not dispatch_event_is_in_unchanged_cooldown(
        seen, "evt-1", cooldown_seconds=0, now=now
    )


def test_task_review_requeue_is_materialized_fails_closed_on_no_record() -> None:
    # The full valid-schema "materialized" case is already covered
    # extensively by .orchestrator/test_supervisor.py through the
    # re-export; this proves the pure predicate fails closed on the
    # absence of a canonical record, matching task_review_requeue_record's
    # own fail-closed contract.
    from dispatch_policy import task_review_requeue_is_materialized

    assert not task_review_requeue_is_materialized(None)
    assert not task_review_requeue_is_materialized({})
    assert not task_review_requeue_is_materialized(
        {"review_requeue_intent": {"status": "pending"}}
    )
