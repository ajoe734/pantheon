"""Small canonical identity helpers shared by V2 scheduler components."""

from __future__ import annotations

from typing import Any, Mapping


def task_generation(task: Mapping[str, Any] | None) -> int:
    """Return the positive canonical generation or zero for invalid input."""

    try:
        generation = int((task or {}).get("generation", 1))
    except (TypeError, ValueError):
        return 0
    return generation if generation >= 1 else 0
