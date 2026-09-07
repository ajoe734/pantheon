"""OPGAP-GATE-HARDENING-20260901 — a commit may not review itself.

The deploy gate that auto-rolled-back four healthy releases shipped as
`LLM-Agent: Codex` with `Reviewer: Codex`. Nothing in CI objected, so no second
party ever asked whether the assertion it added meant what it claimed. Author ==
reviewer is the condition that let the other failures in this family through.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parent / "check_commit_trailers.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_commit_trailers", CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = _load()
REQUIRED = ("LLM-Agent", "Task-ID", "Reviewer")


def _message(agent: str, reviewer: str) -> str:
    return (
        "TASK-ID-20260901: do a thing\n"
        "\n"
        "Body text.\n"
        "\n"
        f"LLM-Agent: {agent}\n"
        "Task-ID: TASK-ID-20260901\n"
        f"Reviewer: {reviewer}\n"
    )


def test_independent_reviewer_is_accepted() -> None:
    assert CHECK.check_message(_message("Claude", "Human/Ops"), REQUIRED, True) == []
    assert CHECK.check_message(_message("Antigravity", "Codex"), REQUIRED, True) == []


def test_self_review_is_rejected() -> None:
    problems = CHECK.check_message(_message("Codex", "Codex"), REQUIRED, True)
    assert any("self-review is not accepted" in p for p in problems), problems


def test_self_review_detection_ignores_case_and_spacing() -> None:
    problems = CHECK.check_message(_message("Codex", "  codex  "), REQUIRED, True)
    assert any("self-review is not accepted" in p for p in problems), problems


@pytest.mark.parametrize("reviewer", ["self", "Self-Review", "same as author", "n/a", "none"])
def test_placeholder_reviewers_are_rejected(reviewer: str) -> None:
    problems = CHECK.check_message(_message("Claude", reviewer), REQUIRED, True)
    assert any("independent reviewer" in p for p in problems), problems


def test_distinct_agents_of_the_same_family_are_accepted() -> None:
    """Codex and Codex2 are different workers; only identity is disqualifying."""
    assert CHECK.check_message(_message("Codex", "Codex2"), REQUIRED, True) == []


def test_missing_trailers_still_reported_without_duplicate_self_review_noise() -> None:
    message = "TASK-ID-20260901: do a thing\n\nBody.\n\nTask-ID: TASK-ID-20260901\n"
    problems = CHECK.check_message(message, REQUIRED, True)
    assert any("missing trailer: LLM-Agent" in p for p in problems), problems
    assert not any("self-review" in p for p in problems), problems


def test_tooling_delivery_does_not_require_reviewer() -> None:
    required = CHECK.required_trailers_for_delivery(REQUIRED, "tooling")
    message = (
        "TASK-ID-20260901: repair tooling\n\n"
        "LLM-Agent: Codex\n"
        "Task-ID: TASK-ID-20260901\n"
    )
    assert required == ("LLM-Agent", "Task-ID")
    assert CHECK.check_message(message, required, True) == []


def test_product_delivery_still_requires_reviewer() -> None:
    assert CHECK.required_trailers_for_delivery(REQUIRED, "product") == REQUIRED


# OPS-COMMIT-IDENTITY-001: a subject prefix must actually name the same task
# as the Task-ID trailer. Reproduces the dev46bbfe contradiction: a real
# >72-char generated task_id cannot appear verbatim in a bounded subject, so
# the subject legitimately carries `bound_commit_subject`'s deterministic
# compacted prefix instead, and CI must accept that -- while still rejecting
# a subject that names an unrelated task or a forged/duplicated trailer.

LONG_TASK_ID = (
    "INTEGRATION-UNBLOCK-GOV-APPROVAL-AUTHORITY-PREREQUISITE-001-"
    "MERGE-STATE-BLOCKED-B14932FE23E9"
)


def test_accepts_bounded_subject_prefix_for_a_generated_long_task_id() -> None:
    bounded_prefix = CHECK.canonical_commit_subject_prefix(LONG_TASK_ID)
    message = (
        f"{bounded_prefix}: repair merge state\n"
        "\n"
        "LLM-Agent: Claude\n"
        f"Task-ID: {LONG_TASK_ID}\n"
        "Reviewer: Codex2\n"
    )
    assert CHECK.check_message(message, REQUIRED, True) == []


def test_rejects_subject_prefix_naming_a_different_task() -> None:
    message = (
        "TASK-ID-OTHER: unrelated summary\n"
        "\n"
        "LLM-Agent: Claude\n"
        "Task-ID: TASK-ID-20260901\n"
        "Reviewer: Codex2\n"
    )
    problems = CHECK.check_message(message, REQUIRED, True)
    assert any("does not match Task-ID trailer" in p for p in problems), problems


def test_rejects_conflicting_task_id_trailers() -> None:
    message = (
        "TASK-ID-20260901: do a thing\n"
        "\n"
        "LLM-Agent: Claude\n"
        "Task-ID: TASK-ID-20260901\n"
        "Task-ID: TASK-ID-FORGED\n"
        "Reviewer: Codex2\n"
    )
    problems = CHECK.check_message(message, REQUIRED, True)
    assert any("conflicting trailer: Task-ID" in p for p in problems), problems


# OPS-COMMIT-IDENTITY-001 follow-up: `canonical_commit_subject_prefix` alone
# assumed a task_id needed its prefix compacted once it crossed ~60 chars,
# regardless of the description actually used. `bound_commit_subject` only
# compacts as a last resort (when the literal full prefix + real description
# still exceeds 72 chars), so a 61-char id paired with a short description
# ("fix") legitimately keeps its full, uncompacted prefix. CI must accept
# that genuine formatter output instead of only comparing against the
# (here, wrongly-compacted) single expected value.


def test_accepts_uncompacted_prefix_for_boundary_length_id_with_short_description() -> None:
    task_id = "A" * 61
    subject = CHECK.commit_subject_prefix_variants(task_id)[0] + ": fix"
    assert len(subject) <= 72
    message = (
        f"{subject}\n"
        "\n"
        "LLM-Agent: Claude\n"
        f"Task-ID: {task_id}\n"
        "Reviewer: Codex2\n"
    )
    assert CHECK.check_message(message, REQUIRED, True) == []
