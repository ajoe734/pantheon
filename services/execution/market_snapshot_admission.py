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

TW_OFFICIAL_CALENDAR_DOMAINS = {
    "twse.com.tw",
    "www.twse.com.tw",
    "openapi.twse.com.tw",
    "tpex.org.tw",
    "www.tpex.org.tw",
    "openapi.tpex.org.tw",
}
TW_VALID_MARKETS = {"TW", "TWSE", "TPEX"}
TW_VALID_VENUES = {"TWSE", "TPEX", "TWSE/TPEX", "TAIFEX", "TW"}
TW_VALID_TIMEZONES = {"Asia/Taipei", "UTC+8", "+08:00", "UTC+08:00"}

# Sentinel returned by a calendar_evidence lookup to signal that evidence
# was consulted but could not be verified (malformed record, missing source
# citation, or the evidence source itself is unreachable) -- kept distinct
# from "no record" (a plain weekday with no holiday claim, which is
# fail-closed as an ordinary trading day, not as "unverifiable").
CALENDAR_EVIDENCE_UNVERIFIABLE = object()


def validate_taiwan_calendar_evidence(
    evidence: Any,
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Validate explicit Taiwan exchange market calendar evidence.

    Enforces:
      - market / venue is Taiwan (TW, TWSE, TPEX)
      - timezone is Asia/Taipei
      - authority is present and non-empty
      - source_url is an official TWSE/TPEx domain
      - fetched_at / observed_at timestamp is valid
      - version or checksum/sha256 is present and valid
      - coverage interval or explicit dates are extracted
    """
    import urllib.parse

    if not isinstance(evidence, Mapping):
        return False, "calendar evidence must be a dictionary/object", None

    market = str(evidence.get("market") or "").strip().upper()
    venue = str(evidence.get("venue") or "").strip().upper()
    if not market and not venue:
        return False, "calendar evidence missing required market or venue", None
    if market and market not in TW_VALID_MARKETS:
        return False, f"calendar evidence market {market!r} is not a valid Taiwan market (TW, TWSE, TPEX)", None
    if venue and venue not in TW_VALID_VENUES:
        return False, f"calendar evidence venue {venue!r} is not a valid Taiwan venue (TWSE, TPEX, TAIFEX, TW)", None

    tz = str(evidence.get("timezone") or evidence.get("tz") or "").strip()
    if not tz or tz not in TW_VALID_TIMEZONES:
        return False, f"calendar evidence timezone {tz!r} is not valid Taiwan timezone (expected 'Asia/Taipei')", None

    authority = str(evidence.get("authority") or "").strip()
    if not authority:
        return False, "calendar evidence missing required authority citation", None

    source_url = str(evidence.get("source_url") or evidence.get("url") or "").strip()
    if not source_url:
        return False, "calendar evidence missing required source_url", None

    try:
        parsed_url = urllib.parse.urlparse(source_url)
    except Exception as exc:
        return False, f"calendar evidence source_url {source_url!r} is invalid: {exc}", None

    if parsed_url.scheme not in ("http", "https"):
        return False, f"calendar evidence source_url scheme {parsed_url.scheme!r} must be http or https", None

    hostname = (parsed_url.hostname or "").lower()
    if not hostname:
        return False, f"calendar evidence source_url {source_url!r} has no hostname", None

    is_official_domain = (
        hostname in TW_OFFICIAL_CALENDAR_DOMAINS
        or hostname.endswith(".twse.com.tw")
        or hostname.endswith(".tpex.org.tw")
    )
    if not is_official_domain:
        return False, (
            f"calendar evidence source_url domain {hostname!r} is not an official "
            f"TWSE/TPEx domain ({', '.join(sorted(TW_OFFICIAL_CALENDAR_DOMAINS))})"
        ), None

    fetched_raw = evidence.get("fetched_at") or evidence.get("observed_at") or evidence.get("timestamp")
    if not fetched_raw:
        return False, "calendar evidence missing required fetched_at / observed_at timestamp", None
    fetched_dt, err = parse_rfc3339(fetched_raw, field_name="fetched_at")
    if err or fetched_dt is None:
        return False, f"calendar evidence invalid fetched_at: {err}", None

    version = evidence.get("version")
    checksum = evidence.get("checksum") or evidence.get("sha256")
    if not version and not checksum:
        return False, "calendar evidence must include a version or sha256 checksum", None

    if checksum:
        cs = str(checksum).strip().lower()
        if len(cs) < 8:
            return False, f"calendar evidence checksum {checksum!r} is invalid / too short", None
        if "expected_checksum" in evidence and cs != str(evidence["expected_checksum"]).strip().lower():
            return False, f"calendar evidence checksum mismatch: {cs} != {evidence['expected_checksum']}", None
        if "expected_sha256" in evidence and cs != str(evidence["expected_sha256"]).strip().lower():
            return False, f"calendar evidence sha256 mismatch: {cs} != {evidence['expected_sha256']}", None

    holidays_map: Dict[str, Dict[str, Any]] = {}
    trading_days: set[str] = set()

    raw_sessions = evidence.get("sessions")
    if isinstance(raw_sessions, Mapping):
        for d_str, s_info in raw_sessions.items():
            if isinstance(s_info, Mapping):
                stype = str(s_info.get("type") or s_info.get("session_type") or "").strip().lower()
                is_hol = s_info.get("holiday_flag") is True or stype in ("holiday", "closed", "non_trading")
                if is_hol:
                    holidays_map[str(d_str)] = dict(s_info)
                elif stype in ("trading", "cash", "regular", "open"):
                    trading_days.add(str(d_str))
            elif isinstance(s_info, str):
                if s_info.lower() in ("holiday", "closed", "non_trading"):
                    holidays_map[str(d_str)] = {"name": s_info}
                else:
                    trading_days.add(str(d_str))

    raw_holidays = evidence.get("holidays")
    if isinstance(raw_holidays, Mapping):
        for d_str, h_info in raw_holidays.items():
            if isinstance(h_info, Mapping):
                holidays_map[str(d_str)] = dict(h_info)
            else:
                holidays_map[str(d_str)] = {"name": str(h_info)}
    elif isinstance(raw_holidays, (list, tuple, set)):
        for item in raw_holidays:
            if isinstance(item, str):
                holidays_map[item] = {"name": "Official Holiday"}
            elif isinstance(item, Mapping) and item.get("date"):
                holidays_map[str(item["date"])] = dict(item)

    coverage_start = str(evidence.get("coverage_start") or evidence.get("start_date") or "").strip() or None
    coverage_end = str(evidence.get("coverage_end") or evidence.get("end_date") or "").strip() or None

    norm = {
        "market": market or "TW",
        "venue": venue or "TWSE",
        "timezone": "Asia/Taipei",
        "authority": authority,
        "source_url": source_url,
        "fetched_at": fetched_raw,
        "version": str(version) if version else None,
        "checksum": str(checksum) if checksum else None,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "holidays": holidays_map,
        "trading_days": trading_days,
    }
    return True, None, norm


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


def evaluate_taiwan_market_freshness(
    *,
    event_time_dt: datetime,
    now_dt: datetime,
    refresh_receipt_dt: Optional[datetime],
    lineage: Any,
    max_refresh_age_seconds: int,
    calendar_evidence: Optional[Any] = None,
    holiday_lookup: Optional[Any] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Deterministic Taiwan (Asia/Taipei) market-session freshness rule.

    Returns `(ok, reason_code, detail)`. `ok` is True only when the close is
    the latest official session's close (or the gap since it is fully
    explained by weekends and validated official exchange calendar evidence)
    *and* the refresh receipt (`observed_at`) and lineage are themselves fresh
    and official. Every rejection carries a typed `reason_code` so callers can
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

    validated_evidence = None
    if calendar_evidence is not None:
        val_ok, val_err, val_norm = validate_taiwan_calendar_evidence(calendar_evidence)
        if not val_ok:
            return (
                False,
                "market_input_calendar_unverifiable",
                f"official Taiwan calendar evidence validation failed: {val_err}",
            )
        validated_evidence = val_norm

    cursor = taipei_event_date + timedelta(days=1)
    while cursor <= taipei_now_date:
        if cursor.weekday() >= 5:
            # Deterministic weekends (Saturday, Sunday) need no calendar feed.
            cursor += timedelta(days=1)
            continue

        # Weekday:
        if cursor == taipei_now_date:
            today_close = _tw_session_close_utc(cursor)
            if now_dt < today_close:
                # Today's regular session has not closed yet.
                cursor += timedelta(days=1)
                continue

        # For any weekday in the gap where regular session closed (or today after 13:30),
        # an official exchange holiday must be evidenced to explain why trading was suspended.
        if holiday_lookup is not None:
            try:
                evidence_rec = holiday_lookup(cursor.isoformat())
            except Exception as exc:
                return (
                    False,
                    "market_input_calendar_unverifiable",
                    f"official Taiwan market-session evidence for {cursor.isoformat()} failed lookup: {exc}",
                )
            if evidence_rec is CALENDAR_EVIDENCE_UNVERIFIABLE:
                return (
                    False,
                    "market_input_calendar_unverifiable",
                    f"official Taiwan market-session evidence for {cursor.isoformat()} is missing or unverifiable",
                )
            if evidence_rec is None:
                if cursor < taipei_now_date:
                    return (
                        False,
                        "market_input_stale",
                        f"a newer official Taiwan session closed on {cursor.isoformat()}",
                    )
                today_close = _tw_session_close_utc(cursor)
                return (
                    False,
                    "market_input_stale",
                    f"a newer official Taiwan session closed at {today_close.isoformat()}",
                )
            if isinstance(evidence_rec, Mapping) and evidence_rec.get("authority"):
                cursor += timedelta(days=1)
                continue
            return (
                False,
                "market_input_calendar_unverifiable",
                f"official Taiwan market-session evidence for {cursor.isoformat()} is missing required authority citation",
            )

        if validated_evidence is not None:
            c_iso = cursor.isoformat()
            c_start = validated_evidence.get("coverage_start")
            c_end = validated_evidence.get("coverage_end")
            if c_start and c_iso < c_start:
                return (
                    False,
                    "market_input_calendar_unverifiable",
                    f"official Taiwan market-session evidence for {c_iso} is before coverage_start {c_start}",
                )
            if c_end and c_iso > c_end:
                return (
                    False,
                    "market_input_calendar_unverifiable",
                    f"official Taiwan market-session evidence for {c_iso} is after coverage_end {c_end}",
                )

            if c_iso in validated_evidence["holidays"]:
                cursor += timedelta(days=1)
                continue
            elif c_iso in validated_evidence["trading_days"] or (c_start and c_end):
                if cursor < taipei_now_date:
                    return (
                        False,
                        "market_input_stale",
                        f"a newer official Taiwan session closed on {c_iso}",
                    )
                today_close = _tw_session_close_utc(cursor)
                return (
                    False,
                    "market_input_stale",
                    f"a newer official Taiwan session closed at {today_close.isoformat()}",
                )
            else:
                return (
                    False,
                    "market_input_calendar_unverifiable",
                    f"official Taiwan market-session evidence missing coverage for weekday {c_iso}",
                )

        # No calendar_evidence and no holiday_lookup provided:
        if cursor < taipei_now_date:
            return (
                False,
                "market_input_stale",
                f"a newer official Taiwan session closed on {cursor.isoformat()}",
            )
        today_close = _tw_session_close_utc(cursor)
        return (
            False,
            "market_input_stale",
            f"a newer official Taiwan session closed at {today_close.isoformat()}",
        )

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
    calendar_evidence: Optional[Any] = None,
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
        ev = calendar_evidence
        if ev is None:
            ev = snapshot.get("calendar_evidence")
        if ev is None and isinstance(snapshot.get("lineage"), Mapping):
            ev = snapshot["lineage"].get("calendar_evidence")

        ok, tw_reason_code, tw_detail = evaluate_taiwan_market_freshness(
            event_time_dt=event_time_dt,
            now_dt=now_dt,
            refresh_receipt_dt=refresh_dt,
            lineage=snapshot.get("lineage"),
            max_refresh_age_seconds=max_age_seconds,
            calendar_evidence=ev,
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
