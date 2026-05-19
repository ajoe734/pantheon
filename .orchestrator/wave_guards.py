"""Wave open/close/freeze guard functions.

Guards run before any wave state transition and raise WaveGuardError on
violation.  They are pure functions — no I/O, no state mutation.

Per design proposal WAVE_CADENCE_ADJUSTMENT_PROPOSAL.md § 4 option-C:
  H1 no-skip:     new wave_id must be the ISO-week successor of the last wave
  H4 cooldown:    previous wave must have closed >= 60 min ago
  baton-owner:    actor must match wave_state.baton_owner (or planning baton)
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

COOLDOWN_SECONDS = 3600  # 60 minutes

_WAVE_ID_RE = re.compile(r"^(\d{4})-W(\d{1,2})$")


class WaveGuardError(ValueError):
    """Raised when a wave transition violates a guard rule."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_wave_id(wave_id: str) -> tuple[int, int]:
    """Parse ``YYYY-WNN`` into ``(year, week)``.

    Raises WaveGuardError on bad format.
    """
    m = _WAVE_ID_RE.fullmatch(wave_id)
    if not m:
        raise WaveGuardError(f"Invalid wave_id format: {wave_id!r} (expected YYYY-WNN)")
    return int(m.group(1)), int(m.group(2))


def last_iso_week_of_year(year: int) -> int:
    """Return the highest ISO week number for *year* (52 or 53)."""
    # Dec 28 always falls in the last ISO week of its year.
    return _dt.date(year, 12, 28).isocalendar()[1]


def successor_wave_id(wave_id: str) -> str:
    """Return the expected next ``YYYY-WNN`` after *wave_id*.

    Handles year rollover (W52/W53 → next year W01).
    """
    year, week = parse_wave_id(wave_id)
    if week < last_iso_week_of_year(year):
        return f"{year}-W{week + 1:02d}"
    return f"{year + 1}-W01"


# ---------------------------------------------------------------------------
# Guard checks
# ---------------------------------------------------------------------------

def check_no_skip(wave_state: dict[str, Any], new_wave_id: str) -> None:
    """Reject *new_wave_id* if it skips past the successor of the last opened wave.

    If there is no prior wave history, any first wave is accepted.
    """
    history: list[dict[str, Any]] = wave_state.get("history") or []
    last_open_wave_id: str | None = None
    for event in reversed(history):
        if event.get("event") == "open":
            last_open_wave_id = event.get("wave_id")
            break

    if last_open_wave_id is None:
        return  # first wave — no predecessor to check

    expected = successor_wave_id(last_open_wave_id)
    if new_wave_id != expected:
        raise WaveGuardError(
            f"No-skip guard: last wave was {last_open_wave_id!r}, "
            f"expected {expected!r} but got {new_wave_id!r}"
        )


def check_cooldown(
    wave_state: dict[str, Any],
    now: _dt.datetime | None = None,
) -> None:
    """Reject if the previous wave closed fewer than 60 minutes ago."""
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)

    history: list[dict[str, Any]] = wave_state.get("history") or []
    last_close_ts: str | None = None
    for event in reversed(history):
        if event.get("event") == "close":
            last_close_ts = event.get("ts")
            break

    if last_close_ts is None:
        return  # no previous close; first wave is fine

    try:
        last_close_dt = _dt.datetime.fromisoformat(
            last_close_ts.replace("Z", "+00:00")
        )
    except (ValueError, AttributeError):
        return  # unparseable timestamp; be permissive

    elapsed = (now - last_close_dt).total_seconds()
    if elapsed < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - elapsed)
        raise WaveGuardError(
            f"Cooldown guard: previous wave closed {int(elapsed)} s ago; "
            f"must wait {remaining} s more (60-min cooldown required)"
        )


def check_baton_owner(
    wave_state: dict[str, Any],
    actor: str,
    planning_state: dict[str, Any] | None = None,
) -> None:
    """Reject if *actor* is not the current baton owner.

    Reads baton_owner from ``wave_state["baton_owner"]`` first; falls back to
    ``planning_state["baton_owner"]``.  If neither source has a baton_owner
    the check is skipped (permissive when unset).
    """
    baton_owner: str | None = wave_state.get("baton_owner")
    if not baton_owner and planning_state is not None:
        baton_owner = planning_state.get("baton_owner")

    if not baton_owner:
        return  # no baton configured; be permissive

    if actor != baton_owner:
        raise WaveGuardError(
            f"Baton-owner guard: actor {actor!r} is not the baton owner {baton_owner!r}"
        )


# ---------------------------------------------------------------------------
# Composite entry points
# ---------------------------------------------------------------------------

def check_wave_open(
    wave_state: dict[str, Any],
    new_wave_id: str,
    actor: str,
    planning_state: dict[str, Any] | None = None,
    now: _dt.datetime | None = None,
) -> None:
    """Run all guards for a wave-open operation.

    Order: no-skip → cooldown → baton-owner.
    Raises WaveGuardError with the first violation found.
    """
    check_no_skip(wave_state, new_wave_id)
    check_cooldown(wave_state, now)
    check_baton_owner(wave_state, actor, planning_state)


def check_wave_close(
    wave_state: dict[str, Any],
    actor: str,
    planning_state: dict[str, Any] | None = None,
) -> None:
    """Run baton-owner guard for a wave-close operation."""
    check_baton_owner(wave_state, actor, planning_state)


def check_wave_freeze(
    wave_state: dict[str, Any],
    actor: str,
    planning_state: dict[str, Any] | None = None,
) -> None:
    """Run baton-owner guard for a wave-freeze operation."""
    check_baton_owner(wave_state, actor, planning_state)
