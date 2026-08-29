"""Pure market snapshot admission rule for bounded paper sessions.

Provides a shared, side-effect-free admission function returning
admitted(snapshot_id, event_time, age_seconds) or
rejected(reason_code, detail, snapshot_id, event_time, age_seconds).

Used by market_snapshot_admission's callers (paper_signal_producer, final
signal defense; paper_fleet_reconciler, fleet lifecycle defense) and, via
the standalone `evaluate_taiwan_market_freshness` /
`lookup_official_tw_holiday` helpers below, by
services/control-plane/bff/agora/operational_readiness.py and
scripts/deploy_nonprod_vm.sh so every call site shares one Taiwan
market-session freshness rule instead of divergent local heuristics.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

# --- Taiwan (Asia/Taipei) market-session freshness -------------------------
#
# TWSE/TPEx official daily closes are only produced on trading days. A flat
# 24h max-age rejects a valid Friday close on Saturday/Sunday even though no
# newer official close can exist until the next session. This section
# replaces that flat check, for Taiwan-listed symbols only, with a
# deterministic rule keyed on Asia/Taipei session boundaries plus governed
# holiday evidence, so every site (execution, reconciler, BFF readiness,
# deploy gate) shares one behavior instead of reimplementing age math.

TAIPEI_TZ = timezone(timedelta(hours=8))
TW_SESSION_CLOSE_LOCAL = (13, 30)  # Asia/Taipei regular session close (see MARKET_CALENDAR_AND_SESSION_POLICY.md)
TW_SYMBOL_SUFFIXES = (".TWSE", ".TPEX", ".TW", ".TWO")
TW_OFFICIAL_LINEAGE_PREFIXES = ("tw-official:",)
TW_OFFICIAL_CONNECTOR_PREFIXES = ("tw-twse-tpex-official-market",)

# Governed reference table of confirmed official TWSE/TPEx market holidays.
# Each entry cites the announcing authority so the record is verifiable, not
# just an assumption; a date absent from this table is treated as a normal
# trading day unless the caller supplies its own verified evidence via the
# `holiday_lookup` parameter (used by tests and any future live calendar
# feed). Source: MARKET_CALENDAR_AND_SESSION_POLICY.md section 3.4 category
# list, dated against TWSE/TPEx's publicly announced 2026 holiday schedule.
OFFICIAL_TW_MARKET_HOLIDAYS: Dict[str, Dict[str, str]] = {
    "2026-01-01": {"name": "New Year's Day", "authority": "TWSE/TPEx announced holiday schedule"},
    "2026-02-16": {"name": "Lunar New Year (eve)", "authority": "TWSE/TPEx announced holiday schedule"},
    "2026-02-17": {"name": "Lunar New Year", "authority": "TWSE/TPEx announced holiday schedule"},
    "2026-02-18": {"name": "Lunar New Year", "authority": "TWSE/TPEx announced holiday schedule"},
    "2026-02-19": {"name": "Lunar New Year", "authority": "TWSE/TPEx announced holiday schedule"},
    "2026-02-20": {"name": "Lunar New Year (makeup)", "authority": "TWSE/TPEx announced holiday schedule"},
    "2026-02-27": {"name": "Peace Memorial Day (observed)", "authority": "TWSE/TPEx announced holiday schedule"},
    "2026-04-03": {"name": "Children's Day / Tomb Sweeping (observed)", "authority": "TWSE/TPEx announced holiday schedule"},
    "2026-05-01": {"name": "Labor Day", "authority": "TWSE/TPEx announced holiday schedule"},
    "2026-06-19": {"name": "Dragon Boat Festival", "authority": "TWSE/TPEx announced holiday schedule"},
    "2026-09-25": {"name": "Mid-Autumn Festival", "authority": "TWSE/TPEx announced holiday schedule"},
    "2026-10-09": {"name": "National Day (observed)", "authority": "TWSE/TPEx announced holiday schedule"},
}

# Sentinel returned by a calendar_evidence lookup to signal that evidence
# was consulted but could not be verified (malformed record, missing source
# citation, or the evidence source itself is unreachable) -- kept distinct
# from "no record" (a plain weekday with no holiday claim, which is
# fail-closed as an ordinary trading day, not as "unverifiable").
CALENDAR_EVIDENCE_UNVERIFIABLE = object()


def lookup_official_tw_holiday(date_iso: str) -> Optional[Mapping[str, str]]:
    """Default governed holiday evidence lookup.

    Returns the holiday record for `date_iso` (a `YYYY-MM-DD` string) or
    `None` if the date is not a recorded official TWSE/TPEx holiday.
    """
    return OFFICIAL_TW_MARKET_HOLIDAYS.get(date_iso)


def is_taiwan_symbol(symbol: Optional[str]) -> bool:
    """True when `symbol` is a Taiwan-listed execution or canonical symbol."""
    s = str(symbol or "").strip().upper()
    if not s:
        return False
    return any(s.endswith(suffix) for suffix in TW_SYMBOL_SUFFIXES)


def _is_official_tw_lineage(lineage: Any) -> bool:
    if not isinstance(lineage, Mapping):
        return False
    for key in ("source_ids", "connector_ids"):
        values = lineage.get(key)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        for value in values:
            sv = str(value)
            if sv.startswith(TW_OFFICIAL_LINEAGE_PREFIXES) or sv.startswith(TW_OFFICIAL_CONNECTOR_PREFIXES):
                return True
    return False


def _tw_session_close_utc(trade_date: date) -> datetime:
    hour, minute = TW_SESSION_CLOSE_LOCAL
    local_close = datetime(trade_date.year, trade_date.month, trade_date.day, hour, minute, tzinfo=TAIPEI_TZ)
    return local_close.astimezone(timezone.utc)


def _tw_trading_day_status(day: date, holiday_lookup) -> str:
    """Classify `day` as "weekend", "holiday", "unverifiable", or "trading"."""
    if day.weekday() >= 5:
        return "weekend"
    try:
        evidence = holiday_lookup(day.isoformat())
    except Exception:
        return "unverifiable"
    if evidence is CALENDAR_EVIDENCE_UNVERIFIABLE:
        return "unverifiable"
    if evidence is None:
        return "trading"
    if isinstance(evidence, Mapping) and evidence.get("authority"):
        return "holiday"
    return "unverifiable"


def evaluate_taiwan_market_freshness(
    *,
    event_time_dt: datetime,
    now_dt: datetime,
    refresh_receipt_dt: Optional[datetime],
    lineage: Any,
    max_refresh_age_seconds: int,
    holiday_lookup=lookup_official_tw_holiday,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Deterministic Taiwan (Asia/Taipei) market-session freshness rule.

    Returns `(ok, reason_code, detail)`. `ok` is True only when the close is
    the latest official session's close (or the gap since it is fully
    explained by weekends and evidenced official holidays) *and* the
    refresh receipt (`observed_at`) and lineage are themselves fresh and
    official. Every rejection carries a typed `reason_code` so callers can
    distinguish weekday staleness, a stale refresh receipt, unverifiable
    calendar evidence, non-official lineage, and future timestamps.
    """
    if not _is_official_tw_lineage(lineage):
        return False, "market_input_non_official_lineage", "snapshot lineage is not an official TWSE/TPEx source"

    if refresh_receipt_dt is None:
        return False, "market_input_stale_refresh", "no refresh receipt (observed_at) present"

    if (refresh_receipt_dt - now_dt).total_seconds() > 300:
        return False, "market_input_invalid", "refresh receipt observed_at is too far in the future"

    refresh_age_seconds = (now_dt - refresh_receipt_dt).total_seconds()
    if refresh_age_seconds > max_refresh_age_seconds:
        return (
            False,
            "market_input_stale_refresh",
            f"refresh receipt is {int(refresh_age_seconds)}s old; maximum is {max_refresh_age_seconds}s",
        )

    taipei_event_date = event_time_dt.astimezone(TAIPEI_TZ).date()
    taipei_now_date = now_dt.astimezone(TAIPEI_TZ).date()

    if taipei_event_date > taipei_now_date:
        return False, "market_input_invalid", "event_time trade date is in the future"

    cursor = taipei_event_date + timedelta(days=1)
    while cursor <= taipei_now_date:
        status = _tw_trading_day_status(cursor, holiday_lookup)
        if status == "unverifiable":
            return (
                False,
                "market_input_calendar_unverifiable",
                f"official Taiwan market-session evidence for {cursor.isoformat()} is missing or unverifiable",
            )
        if status == "trading":
            if cursor < taipei_now_date:
                return (
                    False,
                    "market_input_stale",
                    f"a newer official Taiwan session closed on {cursor.isoformat()}",
                )
            # cursor == taipei_now_date: only stale once today's own session
            # has actually closed; otherwise the gap is fully explained.
            today_close = _tw_session_close_utc(cursor)
            if now_dt >= today_close:
                return (
                    False,
                    "market_input_stale",
                    f"a newer official Taiwan session closed at {today_close.isoformat()}",
                )
        # "weekend" and "holiday" are evidenced non-trading days; continue.
        cursor += timedelta(days=1)

    return True, None, None


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

    if age_seconds < -300:
        return rejected(
            "market_input_invalid",
            f"Source snapshot event_time is too far in the future ({int(age_seconds)}s){b_ctx}",
            snapshot_id=snapshot_id,
            event_time=event_time_str,
            age_seconds=age_seconds,
        )

    if is_taiwan_symbol(snapshot_symbol):
        observed_at_raw = snapshot.get("observed_at")
        refresh_dt: Optional[datetime] = None
        if observed_at_raw not in (None, ""):
            refresh_dt, refresh_err = parse_rfc3339(observed_at_raw, field_name="observed_at")
            if refresh_err or refresh_dt is None:
                return rejected(
                    "market_input_invalid",
                    f"invalid observed_at: {refresh_err}{b_ctx}",
                    snapshot_id=snapshot_id,
                    event_time=event_time_str,
                    age_seconds=age_seconds,
                )
        ok, tw_reason_code, tw_detail = evaluate_taiwan_market_freshness(
            event_time_dt=event_time_dt,
            now_dt=now_dt,
            refresh_receipt_dt=refresh_dt,
            lineage=snapshot.get("lineage"),
            max_refresh_age_seconds=max_age_seconds,
        )
        if not ok:
            return rejected(
                tw_reason_code or "market_input_stale",
                f"{tw_detail}{b_ctx}",
                snapshot_id=snapshot_id,
                event_time=event_time_str,
                age_seconds=age_seconds,
            )
    elif age_seconds > max_age_seconds:
        return rejected(
            "market_input_stale",
            f"Source snapshot {snapshot_id!r} is {int(age_seconds)}s old; maximum is {max_age_seconds}s{b_ctx}",
            snapshot_id=snapshot_id,
            event_time=event_time_str,
            age_seconds=age_seconds,
        )

    return admitted(
        snapshot_id=snapshot_id,
        event_time=event_time_str,
        age_seconds=age_seconds,
    )
