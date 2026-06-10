"""Wave open/close/freeze guard functions.

Guards run before any wave state transition and raise WaveGuardError on
violation.  They are pure functions — no I/O, no state mutation.

Per design proposal WAVE_CADENCE_ADJUSTMENT_PROPOSAL.md § 4 option-C:
  H1 no-skip:     new wave_id must be the ISO-week successor of the last wave
  H4 cooldown:    previous wave must have closed >= 60 min ago
  freeze-close:   close requires frozen state for >= 30 min
  baton-owner:    actor must match wave_state.baton_owner (or planning baton)
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

COOLDOWN_SECONDS = 3600  # 60 minutes
MIN_FREEZE_SECONDS = 1800  # 30 minutes

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


def parse_state_timestamp(value: Any, *, field_name: str) -> _dt.datetime:
    """Parse a state timestamp and fail closed on missing or invalid values."""
    if not isinstance(value, str) or not value.strip():
        raise WaveGuardError(f"{field_name} is required")
    try:
        parsed = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise WaveGuardError(f"{field_name} is invalid: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def latest_event_after_last_open(wave_state: dict[str, Any]) -> dict[str, Any] | None:
    """Return the newest history event after the current wave was opened."""
    history: list[dict[str, Any]] = wave_state.get("history") or []
    last_open_pos: int = -1
    for index, event in enumerate(history):
        if event.get("event") == "open":
            last_open_pos = index
    if last_open_pos < 0:
        return None
    events_after_open = history[last_open_pos + 1:]
    return events_after_open[-1] if events_after_open else history[last_open_pos]


def latest_freeze_timestamp(wave_state: dict[str, Any]) -> str | None:
    """Return frozen_at or the newest freeze event timestamp."""
    frozen_at = wave_state.get("frozen_at")
    if isinstance(frozen_at, str) and frozen_at.strip():
        return frozen_at
    history: list[dict[str, Any]] = wave_state.get("history") or []
    for event in reversed(history):
        if event.get("event") == "freeze":
            ts = event.get("ts")
            if isinstance(ts, str) and ts.strip():
                return ts
            return None
    return None


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
        last_close_dt = parse_state_timestamp(last_close_ts, field_name="last close timestamp")
    except WaveGuardError:
        return  # unparseable timestamp; be permissive

    elapsed = (now - last_close_dt).total_seconds()
    if elapsed < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - elapsed)
        raise WaveGuardError(
            f"Cooldown guard: previous wave closed {int(elapsed)} s ago; "
            f"must wait {remaining} s more (60-min cooldown required)"
        )


def check_current_wave_closed(wave_state: dict[str, Any]) -> None:
    """Reject if the current or most-recently-opened wave has not been closed.

    Checks ``wave_state["status"]`` directly; falls back to history when the
    status key is absent.
    """
    status = wave_state.get("status")
    if status == "open":
        wave_id = wave_state.get("current_wave_id", "?")
        raise WaveGuardError(
            f"Current-wave-open guard: wave {wave_id!r} is still open; "
            "close it before opening the successor"
        )
    if status == "frozen":
        wave_id = wave_state.get("current_wave_id", "?")
        raise WaveGuardError(
            f"Current-wave-frozen guard: wave {wave_id!r} is frozen but not closed; "
            "close it before opening the successor"
        )

    history: list[dict[str, Any]] = wave_state.get("history") or []
    last_open_wave_id: str | None = None
    last_open_pos: int = -1
    for i, event in enumerate(history):
        if event.get("event") == "open":
            last_open_wave_id = event.get("wave_id")
            last_open_pos = i

    if last_open_wave_id is None:
        return  # no prior wave; first wave is fine

    closed = any(
        e.get("event") == "close"
        for e in history[last_open_pos + 1:]
    )
    if not closed:
        raise WaveGuardError(
            f"Current-wave-open guard: wave {last_open_wave_id!r} "
            "has not been closed; close it before opening the successor"
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


def check_current_wave_open_for_freeze(wave_state: dict[str, Any]) -> None:
    """Reject freeze unless the current wave is open."""
    status = wave_state.get("status")
    if status == "open":
        return
    if status:
        wave_id = wave_state.get("current_wave_id", "?")
        raise WaveGuardError(
            f"Freeze-state guard: wave {wave_id!r} has status {status!r}; "
            "freeze requires an open wave"
        )

    latest_event = latest_event_after_last_open(wave_state)
    if latest_event and latest_event.get("event") == "open":
        return
    raise WaveGuardError("Freeze-state guard: freeze requires an open wave")


def check_current_wave_frozen_for_close(
    wave_state: dict[str, Any],
    now: _dt.datetime | None = None,
) -> None:
    """Reject close unless the current wave has been frozen for 30 minutes."""
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)

    status = wave_state.get("status")
    if status != "frozen":
        wave_id = wave_state.get("current_wave_id", "?")
        raise WaveGuardError(
            f"Freeze-before-close guard: wave {wave_id!r} has status {status!r}; "
            "close requires status 'frozen'"
        )

    frozen_at = parse_state_timestamp(
        latest_freeze_timestamp(wave_state),
        field_name="frozen_at",
    )
    elapsed = (now - frozen_at).total_seconds()
    if elapsed < MIN_FREEZE_SECONDS:
        remaining = int(MIN_FREEZE_SECONDS - elapsed)
        raise WaveGuardError(
            f"Freeze-duration guard: current wave froze {int(elapsed)} s ago; "
            f"must wait {remaining} s more (30-min freeze required)"
        )


def check_wave_assign(wave_state: dict[str, Any]) -> None:
    """Reject new task assignments while the current wave is frozen."""
    if wave_state.get("status") != "frozen":
        return
    wave_id = wave_state.get("current_wave_id", "?")
    raise WaveGuardError(
        f"Assign-frozen guard: wave {wave_id!r} is frozen; "
        "new assignments are disabled until the wave is closed"
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

    Order: current-wave-closed → no-skip → cooldown → baton-owner.
    Raises WaveGuardError with the first violation found.
    """
    check_current_wave_closed(wave_state)
    check_no_skip(wave_state, new_wave_id)
    check_cooldown(wave_state, now)
    check_baton_owner(wave_state, actor, planning_state)


def check_wave_close(
    wave_state: dict[str, Any],
    actor: str,
    planning_state: dict[str, Any] | None = None,
    now: _dt.datetime | None = None,
) -> None:
    """Run freeze-before-close and baton-owner guards for wave close."""
    check_current_wave_frozen_for_close(wave_state, now)
    check_baton_owner(wave_state, actor, planning_state)


def check_wave_freeze(
    wave_state: dict[str, Any],
    actor: str,
    planning_state: dict[str, Any] | None = None,
) -> None:
    """Run open-state and baton-owner guards for wave freeze."""
    check_current_wave_open_for_freeze(wave_state)
    check_baton_owner(wave_state, actor, planning_state)
