"""Single definition of which personas the daily persona cycle runs for.

Stdlib only on purpose: the acceptance probe runs as ``python3 -I acceptance/persona_day.py``
(no repo on sys.path) and loads this file by path, so the probe and the cycle controller
cannot hold two different rules.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

ELIGIBLE_STATES = {"paper_running", "paper_owner", "consultable"}
# The daily cycle registrar / meeting step enrol these (consultable personas are consulted, not cycled).
DAILY_CYCLE_STATES = ELIGIBLE_STATES - {"consultable"}


def persona_lifecycle_state(persona: Mapping[str, Any]) -> Any:
    """Controller rows use ``lifecycle_state``; the BFF persona list exposes ``state``."""
    return persona.get("lifecycle_state") if persona.get("lifecycle_state") is not None else persona.get("state")


def is_daily_cycle_persona(persona: Mapping[str, Any]) -> bool:
    return (persona_lifecycle_state(persona) in DAILY_CYCLE_STATES
            and str(persona.get("status", "active") or "active").lower() == "active")


def daily_cycle_personas(personas: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [p for p in personas if is_daily_cycle_persona(p)]
