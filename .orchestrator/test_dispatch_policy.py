from __future__ import annotations

import pytest

from dispatch_policy import (
    DEFAULT_ACTIVE_WORKER_STATUSES,
    DEFAULT_ORPHANED_QUEUE_EVENT_GRACE_SECONDS,
    REASON_OWNED_FINALIZE,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_READY,
    REASON_REVIEW_READY,
    dispatch_reason_priority,
    is_execution_dispatch_reason,
    normalized_status_set,
    ready_dispatch_settings,
)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (REASON_REVIEW_READY, 0),
        (REASON_OWNED_FINALIZE, 1),
        (REASON_OWNED_IN_PROGRESS, 2),
        (REASON_OWNED_READY, 3),
        ("discussion_planning_readout_dispatch", None),
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
        ("discussion_planning_baton_dispatch", False),
        ("", False),
        (None, False),
    ],
)
def test_is_execution_dispatch_reason_cases(reason: str | None, expected: bool) -> None:
    assert is_execution_dispatch_reason(reason) is expected


@pytest.mark.parametrize(
    ("values", "default", "expected"),
    [
        (None, ["Done"], {"done"}),
        (["Review", "DONE"], ["todo"], {"review", "done"}),
        (("Blocked", 1), ["todo"], {"blocked", "1"}),
        ("Review_Approved", ["todo"], {"review_approved"}),
        ([], ["todo"], set()),
        ([None, ""], ["todo"], {"none", ""}),
    ],
)
def test_normalized_status_set_cases(values: object, default: list[str], expected: set[str]) -> None:
    assert normalized_status_set(values, default) == expected


def test_ready_dispatch_settings_current_defaults() -> None:
    settings = ready_dispatch_settings({})

    assert settings["enabled"] is True
    assert settings["review_statuses"] == ["review"]
    assert settings["finalize_statuses"] == ["review_approved"]
    assert settings["owned_statuses"] == ["in_progress", "todo"]
    assert settings["dependency_done_statuses"] == ["done"]
    assert settings["worker_terminal_statuses"] == ["review", "done", "review_approved"]
    assert settings["active_worker_statuses"] == DEFAULT_ACTIVE_WORKER_STATUSES
    assert settings["max_tasks_per_agent"] is None
    assert settings["max_tasks_per_agent_by_agent"] == {}
    assert settings["max_dispatches_per_tick"] == 4
    assert settings["orphaned_queue_event_grace_seconds"] == DEFAULT_ORPHANED_QUEUE_EVENT_GRACE_SECONDS


def test_ready_dispatch_settings_treats_missing_ready_dispatcher_as_defaults() -> None:
    assert ready_dispatch_settings({"ready_dispatcher": None})["review_statuses"] == ["review"]


def test_ready_dispatch_settings_preserves_configured_values() -> None:
    settings = ready_dispatch_settings(
        {
            "ready_dispatcher": {
                "review_statuses": ["needs_review"],
                "finalize_statuses": ["approved"],
                "owned_statuses": ["queued"],
                "max_tasks_per_agent": 2,
                "max_dispatches_per_tick": 8,
            }
        }
    )

    assert settings["review_statuses"] == ["needs_review"]
    assert settings["finalize_statuses"] == ["approved"]
    assert settings["owned_statuses"] == ["queued"]
    assert settings["max_tasks_per_agent"] == 2
    assert settings["max_dispatches_per_tick"] == 8


def test_ready_dispatch_settings_uses_done_statuses_for_legacy_terminal_default() -> None:
    settings = ready_dispatch_settings({"ready_dispatcher": {"done_statuses": ["done"]}})

    assert settings["worker_terminal_statuses"] == ["done"]


def test_ready_dispatch_settings_explicit_worker_terminal_statuses_win() -> None:
    settings = ready_dispatch_settings(
        {
            "ready_dispatcher": {
                "done_statuses": ["done"],
                "worker_terminal_statuses": ["complete", "review_approved"],
            }
        }
    )

    assert settings["worker_terminal_statuses"] == ["complete", "review_approved"]


def test_ready_dispatch_settings_preserves_current_sidecar_and_queue_knobs() -> None:
    settings = ready_dispatch_settings(
        {
            "ready_dispatcher": {
                "sidecar_only_agents": ["Copilot"],
                "disabled_agents": ["Gemini"],
                "orphaned_queue_event_grace_seconds": 90,
                "helper_claim": {"enabled": False},
            }
        }
    )

    assert settings["sidecar_only_agents"] == ["Copilot"]
    assert settings["disabled_agents"] == ["Gemini"]
    assert settings["orphaned_queue_event_grace_seconds"] == 90
    assert settings["helper_claim"] == {"enabled": False}
