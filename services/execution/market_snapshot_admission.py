"""Pure market snapshot admission rule for bounded paper sessions.

Provides a shared, side-effect-free admission function returning
admitted(snapshot_id, event_time, age_seconds) or
rejected(reason_code, detail, snapshot_id, event_time, age_seconds).

Used by both paper_signal_producer (final signal defense) and
paper_fleet_reconciler (fleet lifecycle defense).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence


@dataclass(frozen=True)
class SnapshotAdmissionDecision:
    """Decision returned by admit_market_snapshot."""

    admitted: bool
    snapshot_id: Optional[str] = None
    event_time: Optional[str] = None
    age_seconds: Optional[float] = None
    reason_code: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def admitted(
    snapshot_id: str,
    event_time: str,
    age_seconds: float,
) -> SnapshotAdmissionDecision:
    """Construct an admitted decision."""
    return SnapshotAdmissionDecision(
        admitted=True,
        snapshot_id=snapshot_id,
        event_time=event_time,
        age_seconds=age_seconds,
    )


def rejected(
    reason_code: str,
    detail: str,
    snapshot_id: Optional[str] = None,
    event_time: Optional[str] = None,
    age_seconds: Optional[float] = None,
) -> SnapshotAdmissionDecision:
    """Construct a rejected decision."""
    return SnapshotAdmissionDecision(
        admitted=False,
        reason_code=reason_code,
        detail=detail,
        snapshot_id=snapshot_id,
        event_time=event_time,
        age_seconds=age_seconds,
    )


def parse_rfc3339(value: Any, *, field_name: str = "timestamp") -> tuple[Optional[datetime], Optional[str]]:
    """Parse an RFC-3339 / ISO-8601 string into a UTC datetime.
    
    Returns (datetime, None) on success or (None, error_message) on failure.
    """
    text = str(value or "").strip()
    if not text:
        return None, f"{field_name} is required"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        return None, f"{field_name} {text!r} is not valid ISO-8601: {exc}"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), None


def admit_market_snapshot(
    snapshot: Any,
    *,
    expected_symbol: Optional[str] = None,
    max_age_seconds: int,
    minimum_closes: int = 2,
    now_iso: Optional[str] = None,
    binding_id: Optional[str] = None,
) -> SnapshotAdmissionDecision:
    """Pure, side-effect-free admission check for a market snapshot.

    Parameters
    ----------
    snapshot : Any
        Market snapshot mapping (e.g. from Source Ingest or inline market_input).
    expected_symbol : Optional[str]
        Expected market symbol (e.g. "AAPL.US").
    max_age_seconds : int
        Maximum allowed age in seconds.
    minimum_closes : int
        Minimum number of price bars required (default 2).
    now_iso : Optional[str]
        Current time for deterministic evaluation. If omitted, uses UTC now.
    binding_id : Optional[str]
        Identifier of the binding for logging / error context.

    Returns
    -------
    SnapshotAdmissionDecision
        admitted(...) on success, or rejected(...) with reason_code and detail.
    """
    b_ctx = f" (binding {binding_id})" if binding_id else ""

    if snapshot is None:
        return rejected(
            "market_input_missing",
            f"market snapshot is missing{b_ctx}",
        )

    if not isinstance(snapshot, Mapping):
        return rejected(
            "market_input_invalid",
            f"market snapshot must be an object{b_ctx}",
        )

    # 1. Closes list validation
    closes = snapshot.get("closes")
    if (
        closes is None
        or not isinstance(closes, Sequence)
        or isinstance(closes, (str, bytes))
        or not closes
    ):
        return rejected(
            "market_input_missing",
            f"market snapshot has no closes list{b_ctx}",
        )

    if len(closes) < minimum_closes:
        return rejected(
            "market_input_insufficient",
            f"market snapshot requires at least {minimum_closes} closes, got {len(closes)}{b_ctx}",
        )

    normalized_closes: list[float] = []
    for i, c in enumerate(closes):
        try:
            val = float(c)
        except (TypeError, ValueError):
            return rejected(
                "market_input_invalid",
                f"market close at index {i} is not numeric ({c!r}){b_ctx}",
            )
        if not math.isfinite(val) or val <= 0:
            return rejected(
                "market_input_invalid",
                f"market close at index {i} must be positive finite number ({val!r}){b_ctx}",
            )
        normalized_closes.append(val)

    # 2. Symbol validation
    snapshot_symbol = str(snapshot.get("symbol") or "").strip()
    exp_sym = str(expected_symbol or "").strip()
    if exp_sym and snapshot_symbol and snapshot_symbol != exp_sym:
        return rejected(
            "market_input_invalid",
            f"snapshot symbol {snapshot_symbol!r} does not match expected symbol {exp_sym!r}{b_ctx}",
        )

    # 3. Required lineage / identity fields
    required_fields = ("snapshot_id", "event_time", "source_ref", "lineage")
    missing = [f for f in required_fields if snapshot.get(f) in (None, "", [], {})]
    if missing:
        return rejected(
            "market_input_invalid",
            f"market snapshot is missing required fields: {', '.join(missing)}{b_ctx}",
        )

    snapshot_id = str(snapshot["snapshot_id"]).strip()
    if not snapshot_id:
        return rejected(
            "market_input_invalid",
            f"market snapshot snapshot_id must not be empty{b_ctx}",
        )

    # 4. Timestamp & Freshness validation
    event_time_dt, err = parse_rfc3339(snapshot["event_time"], field_name="event_time")
    if err or event_time_dt is None:
        return rejected(
            "market_input_invalid",
            f"invalid event_time: {err}{b_ctx}",
            snapshot_id=snapshot_id,
        )

    now_dt: Optional[datetime] = None
    if now_iso:
        now_dt, err = parse_rfc3339(now_iso, field_name="now")
        if err or now_dt is None:
            now_dt = datetime.now(timezone.utc)
    else:
        now_dt = datetime.now(timezone.utc)

    age_seconds = (now_dt - event_time_dt).total_seconds()
    event_time_str = event_time_dt.isoformat().replace("+00:00", "Z")

    if age_seconds > max_age_seconds:
        return rejected(
            "market_input_stale",
            f"Source snapshot {snapshot_id!r} is {int(age_seconds)}s old; maximum is {max_age_seconds}s{b_ctx}",
            snapshot_id=snapshot_id,
            event_time=event_time_str,
            age_seconds=age_seconds,
        )

    if age_seconds < -300:
        return rejected(
            "market_input_invalid",
            f"Source snapshot event_time is too far in the future ({int(age_seconds)}s){b_ctx}",
            snapshot_id=snapshot_id,
            event_time=event_time_str,
            age_seconds=age_seconds,
        )

    return admitted(
        snapshot_id=snapshot_id,
        event_time=event_time_str,
        age_seconds=age_seconds,
    )
