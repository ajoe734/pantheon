"""Chair-review wave health checker.

Scans wave_state.history and emits a wave_health_report dict identifying:

  short_cycle    — a wave's open→close interval is below MIN_CYCLE_SECONDS
  skipped_wave   — consecutive wave IDs are not sequential successors
  actor_mismatch — the actor who opened a wave differs from the one who closed/froze it
  missing_freeze — a wave was closed without a prior freeze event

All functions are pure: no I/O, no state mutation.

Usage::

    from chair_review_wave_health import check_wave_health
    report = check_wave_health(wave_state)
    if not report["healthy"]:
        for f in report["findings"]:
            print(f["detail"])
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from wave_guards import (
    WaveGuardError,
    parse_state_timestamp,
    successor_wave_id,
)

MIN_CYCLE_SECONDS: int = 3600  # 60 minutes — healthy minimum wave duration

_SEVERITY_SHORT_CYCLE = "warn"
_SEVERITY_SKIPPED_WAVE = "error"
_SEVERITY_ACTOR_MISMATCH = "warn"
_SEVERITY_MISSING_FREEZE = "error"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_ts(value: Any) -> _dt.datetime | None:
    """Parse a timestamp string permissively; return None on failure."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return parse_state_timestamp(value, field_name="ts")
    except WaveGuardError:
        return None


def _extract_wave_cycles(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group history events into per-wave lifecycle records.

    Each record::

        {
          wave_id: str,
          open_ts: str | None,
          open_actor: str | None,
          freeze_ts: str | None,
          freeze_actor: str | None,
          close_ts: str | None,
          close_actor: str | None,
          incomplete: bool,   # True when no close event seen yet
        }
    """
    cycles: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for event in history:
        ev = event.get("event")
        ts = event.get("ts")
        actor = event.get("actor")
        wave_id = event.get("wave_id")

        if ev == "open":
            if current is not None:
                current["incomplete"] = True
                cycles.append(current)
            current = {
                "wave_id": wave_id or "?",
                "open_ts": ts,
                "open_actor": actor,
                "freeze_ts": None,
                "freeze_actor": None,
                "close_ts": None,
                "close_actor": None,
                "incomplete": False,
            }
        elif ev == "freeze" and current is not None:
            if current["freeze_ts"] is None:  # record first freeze only
                current["freeze_ts"] = ts
                current["freeze_actor"] = actor
        elif ev == "close":
            if current is not None:
                current["close_ts"] = ts
                current["close_actor"] = actor
                cycles.append(current)
                current = None
            # close with no preceding open: skip

    if current is not None:
        current["incomplete"] = True
        cycles.append(current)

    return cycles


# ---------------------------------------------------------------------------
# Individual checks — each returns a list of finding dicts
# ---------------------------------------------------------------------------

def _check_short_cycles(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for cyc in cycles:
        if cyc["incomplete"] or cyc["close_ts"] is None:
            continue
        open_dt = _parse_ts(cyc["open_ts"])
        close_dt = _parse_ts(cyc["close_ts"])
        if open_dt is None or close_dt is None:
            continue
        duration = (close_dt - open_dt).total_seconds()
        if duration < MIN_CYCLE_SECONDS:
            findings.append({
                "finding_type": "short_cycle",
                "wave_id": cyc["wave_id"],
                "detail": (
                    f"Wave {cyc['wave_id']!r} lasted {int(duration)} s "
                    f"(minimum {MIN_CYCLE_SECONDS} s required)"
                ),
                "severity": _SEVERITY_SHORT_CYCLE,
                "duration_seconds": int(duration),
            })
    return findings


def _check_skipped_waves(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect non-sequential wave IDs between consecutive opened waves."""
    findings = []
    opened = [c for c in cycles if c["wave_id"] != "?"]
    for i in range(len(opened) - 1):
        prev_id = opened[i]["wave_id"]
        next_id = opened[i + 1]["wave_id"]
        try:
            expected = successor_wave_id(prev_id)
        except WaveGuardError:
            continue  # unparseable wave_id; skip
        if next_id != expected:
            findings.append({
                "finding_type": "skipped_wave",
                "wave_id": next_id,
                "detail": (
                    f"Wave sequence gap: after {prev_id!r} the next wave was "
                    f"{next_id!r} but expected {expected!r}"
                ),
                "severity": _SEVERITY_SKIPPED_WAVE,
                "prev_wave_id": prev_id,
                "expected_wave_id": expected,
            })
    return findings


def _check_actor_mismatch(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect waves where the close/freeze actor differs from the open actor."""
    findings = []
    for cyc in cycles:
        open_actor = cyc.get("open_actor")
        if not open_actor:
            continue
        for role, ts_key, actor_key in (
            ("close", "close_ts", "close_actor"),
            ("freeze", "freeze_ts", "freeze_actor"),
        ):
            other_ts = cyc.get(ts_key)
            other_actor = cyc.get(actor_key)
            if other_ts and other_actor and other_actor != open_actor:
                finding: dict[str, Any] = {
                    "finding_type": "actor_mismatch",
                    "wave_id": cyc["wave_id"],
                    "detail": (
                        f"Wave {cyc['wave_id']!r}: opened by {open_actor!r} "
                        f"but {role}d by {other_actor!r}"
                    ),
                    "severity": _SEVERITY_ACTOR_MISMATCH,
                    "open_actor": open_actor,
                    f"{role}_actor": other_actor,
                }
                findings.append(finding)
    return findings


def _check_missing_freeze(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect closed waves that skipped the freeze stage."""
    findings = []
    for cyc in cycles:
        if cyc["incomplete"]:
            continue
        if cyc["close_ts"] is not None and cyc["freeze_ts"] is None:
            findings.append({
                "finding_type": "missing_freeze",
                "wave_id": cyc["wave_id"],
                "detail": (
                    f"Wave {cyc['wave_id']!r} was closed without a prior freeze stage"
                ),
                "severity": _SEVERITY_MISSING_FREEZE,
            })
    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_wave_health(wave_state: dict[str, Any]) -> dict[str, Any]:
    """Analyse *wave_state* and return a ``wave_health_report`` dict.

    *wave_state* is the ``wave_state`` sub-dict from ``ai-status.json``.

    Return shape::

        {
          "healthy": bool,           # False when any finding is present
          "waves_checked": int,      # number of wave lifecycle records analysed
          "findings": [
            {
              "finding_type": "short_cycle" | "skipped_wave"
                            | "actor_mismatch" | "missing_freeze",
              "wave_id": str,
              "detail": str,         # human-readable description
              "severity": "warn" | "error",
              # type-specific extra fields may be present
            }
          ],
          "summary": str,            # one-line human-readable summary
        }
    """
    history: list[dict[str, Any]] = wave_state.get("history") or []
    cycles = _extract_wave_cycles(history)

    findings: list[dict[str, Any]] = []
    findings.extend(_check_short_cycles(cycles))
    findings.extend(_check_skipped_waves(cycles))
    findings.extend(_check_actor_mismatch(cycles))
    findings.extend(_check_missing_freeze(cycles))

    healthy = len(findings) == 0
    waves_checked = len(cycles)

    if not findings:
        summary = f"No violations found across {waves_checked} wave cycle(s)."
    else:
        errors = sum(1 for f in findings if f["severity"] == "error")
        warns = sum(1 for f in findings if f["severity"] == "warn")
        parts = []
        if errors:
            parts.append(f"{errors} error(s)")
        if warns:
            parts.append(f"{warns} warning(s)")
        summary = (
            f"Found {' and '.join(parts)} across {waves_checked} wave cycle(s): "
            + "; ".join(f["detail"] for f in findings)
        )

    return {
        "healthy": healthy,
        "waves_checked": waves_checked,
        "findings": findings,
        "summary": summary,
    }
