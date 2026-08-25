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
    normalize_execution_resources,
    normalized_status_set,
    ready_dispatch_settings,
    task_execution_resources,
)


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
                "sidecar_only_agents": ["Copilot"],
            }
        }
    )
    assert settings["review_statuses"] == ["needs_review"]
    assert settings["finalize_statuses"] == ["approved"]
    assert settings["owned_statuses"] == ["queued"]
    assert settings["max_dispatches_per_tick"] == 8
    assert settings["max_concurrent_per_account"] == {"codex": 2}
    assert settings["sidecar_only_agents"] == ["Copilot"]


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
