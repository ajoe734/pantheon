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
