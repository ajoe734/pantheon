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

import hashlib
import json
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
TW_VALID_MARKETS = {"TW", "TWSE", "TPEX", "TWSE/TPEX"}
TW_VALID_CASH_VENUES = {"TWSE", "TPEX"}
TW_VALID_TIMEZONES = {"Asia/Taipei"}

# Governed external trusted pins for Taiwan exchange calendar evidence.
# An untrusted evidence object cannot self-assert its own expected checksum.
# The 64-hex SHA-256 digest in the evidence must equal the recomputed digest
# of its canonical decision payload *and* the exact version pin below.
#
# twse-2026-lny-v1 is the bounded 2026-02-11..2026-02-23 cash-market payload
# derived from the official TWSE 115-year schedule.  It records 2/11 as the
# final trading day, 2/12..2/20 as no-trading/holiday dates, and 2/23 as the
# reopening date.  The source is the official TWSE response at:
# https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=json&queryYear=115
TW_GOVERNED_CALENDAR_PINS: Mapping[str, str] = {
    "twse-2026-lny-v1": "55b2e23b9bd30af666a99c98da2dbbfad568dcd655631b1c6347d12ee8381596",
}
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# Sentinel returned by a calendar_evidence lookup to signal that evidence
# was consulted but could not be verified (malformed record, missing source
# citation, or the evidence source itself is unreachable).
CALENDAR_EVIDENCE_UNVERIFIABLE = object()


def parse_iso_date(value: Any, *, field_name: str = "date") -> Tuple[Optional[date], Optional[str]]:
    """Parse a strict ISO-8601 date string (YYYY-MM-DD).

    Returns (date, None) on success or (None, error_message) on failure.
    """
    text = str(value or "").strip()
    if not text or len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None, f"{field_name} {value!r} is not a valid strict ISO date (expected YYYY-MM-DD)"
    try:
        parsed = date.fromisoformat(text)
        return parsed, None
    except ValueError as exc:
        return None, f"{field_name} {value!r} is not a valid strict ISO date: {exc}"


def _canonical_calendar_payload_sha256(payload: Mapping[str, Any]) -> str:
    """Return SHA-256 over the stable JSON representation of calendar truth."""
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_taiwan_calendar_evidence(
    evidence: Any,
    *,
    trusted_pins: Optional[Mapping[str, str]] = None,
    now_dt: Optional[datetime] = None,
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Validate explicit Taiwan exchange market calendar evidence.

    Enforces:
      - cash venue is explicitly and exactly TWSE or TPEX
      - timezone is exactly 'Asia/Taipei' (no UTC/offset aliases)
      - authority is present and non-empty
      - source_url uses HTTPS and an official TWSE/TPEx domain
      - fetched_at is valid strict RFC3339 and never future-dated
      - version is present and maps to one exact external trusted pin
      - checksum is recomputed over the actual canonical calendar payload
        and matches both the evidence claim and the version pin
      - no self-asserted checksum within the evidence object is trusted
      - coverage dates, session dates, and holiday dates are strict ISO dates
    """
    import urllib.parse

    if not isinstance(evidence, Mapping):
        return False, "calendar evidence must be a dictionary/object", None

    market = str(evidence.get("market") or "").strip().upper()
    venue = str(evidence.get("venue") or "").strip().upper()
    if not market:
        return False, "calendar evidence missing required market", None
    if market not in TW_VALID_MARKETS:
        return False, f"calendar evidence market {market!r} is not a valid Taiwan market (TW, TWSE, TPEX, TWSE/TPEX)", None

    # Venue is a required atomic identity.  A market fallback or the combined
    # value TWSE/TPEX cannot prove which venue's sessions were attested.
    if not venue:
        return False, "calendar evidence missing required explicit venue (expected TWSE or TPEX)", None
    if venue not in TW_VALID_CASH_VENUES:
        return False, f"calendar evidence venue {venue!r} is not an official Taiwan cash venue (expected exactly TWSE or TPEX)", None

    tz = str(evidence.get("timezone") or evidence.get("tz") or "").strip()
    if tz != "Asia/Taipei":
        return False, f"calendar evidence timezone {tz!r} is invalid (expected exact 'Asia/Taipei')", None

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

    if parsed_url.scheme != "https":
        return False, f"calendar evidence source_url scheme {parsed_url.scheme!r} must be https", None

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
        return False, "calendar evidence missing required fetched_at timestamp", None
    fetched_dt, err = parse_rfc3339(fetched_raw, field_name="fetched_at")
    if err or fetched_dt is None:
        return False, f"calendar evidence invalid fetched_at: {err}", None

    ref_now = now_dt if now_dt is not None else datetime.now(timezone.utc)
    if fetched_dt > ref_now:
        return False, f"calendar evidence fetched_at {fetched_raw!r} is in the future", None

    version_str = str(evidence.get("version") or "").strip()
    if not version_str:
        return False, "calendar evidence missing required governed version", None

    # The checksum claim is only parsed here.  It is verified against the
    # recomputed payload digest after all session/holiday content is normalized.
    raw_checksum = evidence.get("checksum") or evidence.get("sha256")
    if not raw_checksum:
        return False, "calendar evidence must include a 64-hex sha256 checksum", None
    cs = str(raw_checksum).strip().lower()
    if len(cs) != 64 or not all(c in "0123456789abcdef" for c in cs):
        return False, f"calendar evidence checksum {raw_checksum!r} must be a 64-hex SHA-256 digest", None

    coverage_start_raw = evidence.get("coverage_start") or evidence.get("start_date")
    coverage_end_raw = evidence.get("coverage_end") or evidence.get("end_date")
    c_start_iso = None
    c_end_iso = None
    if coverage_start_raw is not None and str(coverage_start_raw).strip():
        c_start_d, err = parse_iso_date(coverage_start_raw, field_name="coverage_start")
        if err or c_start_d is None:
            return False, f"calendar evidence invalid coverage_start: {err}", None
        c_start_iso = c_start_d.isoformat()
    if coverage_end_raw is not None and str(coverage_end_raw).strip():
        c_end_d, err = parse_iso_date(coverage_end_raw, field_name="coverage_end")
        if err or c_end_d is None:
            return False, f"calendar evidence invalid coverage_end: {err}", None
        c_end_iso = c_end_d.isoformat()
    if c_start_iso and c_end_iso and c_start_iso > c_end_iso:
        return False, f"calendar evidence coverage_start {c_start_iso} is after coverage_end {c_end_iso}", None

    holidays_map: Dict[str, Dict[str, Any]] = {}
    trading_days: set[str] = set()

    raw_sessions = evidence.get("sessions")
    if isinstance(raw_sessions, Mapping):
        for d_str, s_info in raw_sessions.items():
            parsed_d, err = parse_iso_date(d_str, field_name="session date")
            if err or parsed_d is None:
                return False, f"calendar evidence invalid session date {d_str!r}: {err}", None
            d_iso = parsed_d.isoformat()
            if isinstance(s_info, Mapping):
                stype = str(s_info.get("type") or s_info.get("session_type") or "").strip().lower()
                is_hol = s_info.get("holiday_flag") is True or stype in ("holiday", "closed", "non_trading")
                if is_hol:
                    holidays_map[d_iso] = dict(s_info)
                elif stype in ("trading", "cash", "regular", "open"):
                    trading_days.add(d_iso)
                else:
                    return False, f"calendar evidence session {d_iso} has unrecognized type {stype!r}", None
            elif isinstance(s_info, str):
                stype = s_info.strip().lower()
                if stype in ("holiday", "closed", "non_trading"):
                    holidays_map[d_iso] = {"name": s_info}
                elif stype in ("trading", "cash", "regular", "open"):
                    trading_days.add(d_iso)
                else:
                    return False, f"calendar evidence session {d_iso} has unrecognized type {s_info!r}", None
            else:
                return False, f"calendar evidence session for {d_iso} must be an object or string", None
    elif raw_sessions is not None:
        return False, "calendar evidence sessions must be a mapping", None

    raw_holidays = evidence.get("holidays")
    if isinstance(raw_holidays, Mapping):
        for d_str, h_info in raw_holidays.items():
            parsed_d, err = parse_iso_date(d_str, field_name="holiday date")
            if err or parsed_d is None:
                return False, f"calendar evidence invalid holiday date {d_str!r}: {err}", None
            d_iso = parsed_d.isoformat()
            if isinstance(h_info, Mapping):
                holidays_map[d_iso] = dict(h_info)
            else:
                holidays_map[d_iso] = {"name": str(h_info)}
    elif isinstance(raw_holidays, (list, tuple, set)):
        for item in raw_holidays:
            if isinstance(item, str):
                parsed_d, err = parse_iso_date(item, field_name="holiday date")
                if err or parsed_d is None:
                    return False, f"calendar evidence invalid holiday date {item!r}: {err}", None
                holidays_map[parsed_d.isoformat()] = {"name": "Official Holiday"}
            elif isinstance(item, Mapping) and item.get("date"):
                d_str = item["date"]
                parsed_d, err = parse_iso_date(d_str, field_name="holiday date")
                if err or parsed_d is None:
                    return False, f"calendar evidence invalid holiday date {d_str!r}: {err}", None
                holidays_map[parsed_d.isoformat()] = dict(item)
            else:
                return False, "calendar evidence holiday entry must be a date string or object with date", None
    elif raw_holidays is not None:
        return False, "calendar evidence holidays must be a mapping or list", None

    raw_trading_days = evidence.get("trading_days")
    if isinstance(raw_trading_days, (list, tuple, set)):
        for item in raw_trading_days:
            if isinstance(item, str):
                parsed_d, err = parse_iso_date(item, field_name="trading date")
                if err or parsed_d is None:
                    return False, f"calendar evidence invalid trading date {item!r}: {err}", None
                trading_days.add(parsed_d.isoformat())
            elif isinstance(item, Mapping) and item.get("date"):
                d_str = item["date"]
                parsed_d, err = parse_iso_date(d_str, field_name="trading date")
                if err or parsed_d is None:
                    return False, f"calendar evidence invalid trading date {d_str!r}: {err}", None
                trading_days.add(parsed_d.isoformat())
            else:
                return False, "calendar evidence trading_days entry must be a date string or object with date", None
    elif raw_trading_days is not None:
        return False, "calendar evidence trading_days must be a list or sequence", None

    overlapping_dates = sorted(set(holidays_map).intersection(trading_days))
    if overlapping_dates:
        return False, (
            "calendar evidence marks dates as both holiday and trading: "
            + ", ".join(overlapping_dates)
        ), None
    if not holidays_map and not trading_days:
        return False, "calendar evidence contains no explicit session, holiday, or trading-day records", None

    canonical_payload = {
        "authority": authority,
        "coverage_end": c_end_iso,
        "coverage_start": c_start_iso,
        "holidays": holidays_map,
        "market": market,
        "source_url": source_url,
        "timezone": tz,
        "trading_days": sorted(trading_days),
        "venue": venue,
        "version": version_str,
    }
    try:
        computed_sha256 = _canonical_calendar_payload_sha256(canonical_payload)
    except (TypeError, ValueError) as exc:
        return False, f"calendar evidence canonical payload is not valid JSON: {exc}", None

    if cs != computed_sha256:
        return False, (
            f"calendar evidence claimed sha256 {cs!r} does not match canonical "
            f"payload sha256 {computed_sha256!r}"
        ), None

    pins_source = trusted_pins if trusted_pins is not None else TW_GOVERNED_CALENDAR_PINS
    if not isinstance(pins_source, Mapping):
        return False, "trusted_pins must be an exact version-to-sha256 mapping", None
    if version_str not in pins_source:
        return False, f"calendar evidence version {version_str!r} is not governed by a trusted pin", None
    expected_pin = str(pins_source[version_str]).strip().lower()

    if len(expected_pin) != 64 or not all(c in "0123456789abcdef" for c in expected_pin):
        return False, f"trusted pin for version {version_str!r} is not a full SHA-256 digest", None
    if expected_pin == EMPTY_SHA256:
        return False, f"trusted pin for version {version_str!r} is the forbidden empty-payload SHA-256", None
    if computed_sha256 != expected_pin:
        return False, (
            f"calendar evidence canonical payload sha256 {computed_sha256!r} does not "
            f"match trusted pin for version {version_str!r} ({expected_pin!r})"
        ), None

    norm = {
        "market": market,
        "venue": venue,
        "timezone": "Asia/Taipei",
        "authority": authority,
        "source_url": source_url,
        "fetched_at": fetched_raw,
        "version": version_str,
        "checksum": cs,
        "computed_sha256": computed_sha256,
        "coverage_start": c_start_iso,
        "coverage_end": c_end_iso,
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
    trusted_pins: Optional[Mapping[str, str]] = None,
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
    if event_time_dt > now_dt:
        return False, "market_input_invalid", "event_time is in the future"

    if not _is_official_tw_lineage(lineage):
        return False, "market_input_non_official_lineage", "snapshot lineage is not an official TWSE/TPEx source"

    if refresh_receipt_dt is None:
        return False, "market_input_stale_refresh", "no refresh receipt (observed_at) present"

    if refresh_receipt_dt > now_dt:
        return False, "market_input_invalid", "refresh receipt observed_at is in the future"

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
        val_ok, val_err, val_norm = validate_taiwan_calendar_evidence(
            calendar_evidence,
            trusted_pins=trusted_pins,
            now_dt=now_dt,
        )
        if not val_ok:
            return (
                False,
                "market_input_calendar_unverifiable",
                f"official Taiwan calendar evidence validation failed: {val_err}",
            )
        validated_evidence = val_norm

    event_date_iso = taipei_event_date.isoformat()
    if taipei_event_date.weekday() >= 5:
        return (
            False,
            "market_input_invalid",
            f"event_time trade date {event_date_iso} is a Taiwan market weekend",
        )

    # The gap check below only proves that no *newer* session is missing.  It
    # must not make the snapshot's own trade date self-authenticating: a
    # fabricated Saturday (or a known exchange closure) cannot be an official
    # cash-market close merely because there is no later completed weekday.
    # When governed evidence covers the event date, require its explicit
    # trading-session record and reject an explicit closure.  A weekday beyond
    # a bounded evidence window is deliberately left to the normal gap rule;
    # this preserves the deterministic Friday-to-weekend case that needs no
    # calendar evidence at all.
    if validated_evidence is not None:
        coverage_start = validated_evidence.get("coverage_start")
        coverage_end = validated_evidence.get("coverage_end")
        covered_event_date = (
            (coverage_start is None or event_date_iso >= coverage_start)
            and (coverage_end is None or event_date_iso <= coverage_end)
        )
        if covered_event_date:
            if event_date_iso in validated_evidence["holidays"]:
                return (
                    False,
                    "market_input_invalid",
                    f"event_time trade date {event_date_iso} is an explicit Taiwan market closure",
                )
            if event_date_iso not in validated_evidence["trading_days"]:
                return (
                    False,
                    "market_input_calendar_unverifiable",
                    "official Taiwan market-session evidence missing explicit "
                    f"trading record for event date {event_date_iso}",
                )

    cursor = taipei_event_date + timedelta(days=1)
    while cursor <= taipei_now_date:
        if cursor.weekday() >= 5:
            # Deterministic weekends (Saturday, Sunday) need no calendar feed.
            cursor += timedelta(days=1)
            continue

        c_iso = cursor.isoformat()

        # Weekday:
        if cursor == taipei_now_date:
            today_close = _tw_session_close_utc(cursor)
            if now_dt < today_close:
                # Today's regular session has not closed yet.
                cursor += timedelta(days=1)
                continue

        # For any completed weekday session in the gap (or today after 13:30),
        # an explicit validated session record (holiday/closure) must be present.
        if holiday_lookup is not None:
            try:
                evidence_rec = holiday_lookup(c_iso)
            except Exception as exc:
                return (
                    False,
                    "market_input_calendar_unverifiable",
                    f"official Taiwan market-session evidence for {c_iso} failed lookup: {exc}",
                )
            if evidence_rec is CALENDAR_EVIDENCE_UNVERIFIABLE or evidence_rec is None:
                return (
                    False,
                    "market_input_calendar_unverifiable",
                    f"official Taiwan market-session evidence for {c_iso} is missing or unverifiable",
                )
            # Full evidence contract validation (no authority-only bypass)
            h_ok, h_err, h_norm = validate_taiwan_calendar_evidence(
                evidence_rec,
                trusted_pins=trusted_pins,
                now_dt=now_dt,
            )
            if not h_ok or h_norm is None:
                return (
                    False,
                    "market_input_calendar_unverifiable",
                    f"official Taiwan market-session evidence for {c_iso} validation failed: {h_err}",
                )
            if c_iso in h_norm["holidays"]:
                cursor += timedelta(days=1)
                continue
            elif c_iso in h_norm["trading_days"]:
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
                    f"official Taiwan market-session evidence missing explicit session record for weekday {c_iso}",
                )

        if validated_evidence is not None:
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
            elif c_iso in validated_evidence["trading_days"]:
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
                    f"official Taiwan market-session evidence missing explicit session record for weekday {c_iso}",
                )

        # No calendar_evidence and no holiday_lookup provided for this completed weekday:
        # Fails closed as market_input_calendar_unverifiable (stale is reserved for explicit validated trading sessions).
        return (
            False,
            "market_input_calendar_unverifiable",
            f"no official Taiwan calendar evidence provided for completed weekday session {c_iso}",
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
    trusted_calendar_pins: Optional[Mapping[str, str]] = None,
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
    trusted_calendar_pins : Optional[Mapping[str, str]]
        Externally governed version-to-SHA256 pins. Production callers use
        the module's governed catalog; deterministic tests may inject an
        isolated trust catalog without accepting pins from snapshot data.

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

    if age_seconds < 0:
        return rejected(
            "market_input_invalid",
            f"Source snapshot event_time is in the future ({age_seconds:.6f}s){b_ctx}",
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
            trusted_pins=trusted_calendar_pins,
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
