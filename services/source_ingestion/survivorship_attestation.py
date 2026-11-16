"""Survivorship attestation for ``candidate_pool_market.v1`` (``all_governed_tw``).

The registry marks a screened pool ``survivorship_limited`` unless the market carries
``includes_delisted is True`` and ``includes_suspended is True``.  This module is the
only production place that may produce those two values, and it derives them from the
governed stored evidence of the window - never from a default or a configuration flag.

``includes_delisted`` is true only when every session of the window comes from a dated
all-market official capture of BOTH venues (TWSE ``MI_INDEX`` all-stock page and the
TPEx all-stock daily quotes, both relayed as collector bundles) so that a security
delisted later is present for the sessions it traded.  A weekday inside the window that
has no stored capture is a gap unless the governed dataset ``tw_trading_holidays`` (official
exchange holiday schedule; not stored today) records that day as closed: otherwise the feed
cannot tell a holiday from a missing ingest and does not guess.

``includes_suspended`` is true only when each session of the window has a stored
official per-session suspended-security capture for both venues
(dataset ``tw_trading_suspension``).  That dataset is not produced today: the collector
price-history spiders drop every all-market row without a closing price
(``deploy/collector/collector_stack/spiders.py`` ``if close_price is None: continue``),
which is exactly the row of a suspended or non-traded security.  Until it exists the
flag is false with the missing dataset named.

Every attestation lists the evidence references and the sessions it covers, and is
false (with readable reasons) for any window with a gap.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "survivorship_attestation.v1"
SUSPENSION_DATASET = "tw_trading_suspension"
HOLIDAY_DATASET = "tw_trading_holidays"
VENUES = ("TWSE", "TPEx")
# Collector bundles built from dated official per-date all-market pages.
ALL_MARKET_BUNDLES: Mapping[str, str] = {
    "TWSE": "bundle://collector-tw-twse-price-history",
    "TPEx": "bundle://collector-tw-tpex-price-history",
}
MISSING_SUSPENSION_DATASET = (
    f"{SUSPENSION_DATASET}: official per-session suspended / non-traded security list for each venue "
    "(TWSE MI_INDEX all-stock page rows without a close, and the TPEx all-stock daily quotes page rows "
    "without a close), currently dropped by the collector price-history spiders"
)
MISSING_CALENDAR = (
    "official TWSE/TPEx holiday schedule (https://www.twse.com.tw/zh/holidaySchedule/holidaySchedule) "
    "to account for weekdays without a session"
)

# (session, venue) -> evidence mapping
Captures = Mapping[tuple[str, str], Mapping[str, Any]]


def _interior_weekdays(first: date, last: date, accounted: set[str]) -> list[str]:
    out, day = [], first
    while day <= last:
        if day.weekday() < 5 and day.isoformat() not in accounted:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _coverage(sessions: Sequence[str], captures: Captures) -> tuple[list[str], list[dict[str, str]], list[str]]:
    covered, gaps, refs = [], [], set()
    for session in sessions:
        missing = [v for v in VENUES if (session, v) not in captures]
        if missing:
            gaps.append({"session": session, "reason": f"no stored capture for {','.join(missing)}"})
            continue
        covered.append(session)
        refs.update(str(captures[(session, v)]["ref"]) for v in VENUES)
    return covered, gaps, sorted(refs)


def _evidence(attested: bool, covered: list[str], gaps: list[Any], refs: list[str], reasons: list[str],
              **extra: Any) -> dict[str, Any]:
    return {"attested": attested, "sessions_covered": len(covered),
            "first_session": covered[0] if covered else None, "last_session": covered[-1] if covered else None,
            "evidence_refs": refs, "gaps": gaps, "reasons": reasons, **extra}


def attest_survivorship(
    sessions: Iterable[str], price_captures: Captures, suspension_captures: Captures,
    closed_days: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the attestation; a flag is true only when the evidence is complete."""
    window = sorted(set(sessions))
    if not window:
        empty = _evidence(False, [], [], [], ["window has no sessions"])
        return {"schema_version": SCHEMA_VERSION, "includes_delisted": False, "includes_suspended": False,
                "window": {"first": None, "last": None, "sessions": 0}, "delisted": empty, "suspended": empty}

    covered, gaps, refs = _coverage(window, price_captures)
    closed = dict(closed_days or {})
    calendar_gaps = _interior_weekdays(
        date.fromisoformat(window[0]), date.fromisoformat(window[-1]), set(window) | set(closed))
    refs = sorted({*refs, *(str(closed[d]["ref"]) for d in closed if window[0] <= d <= window[-1])})
    reasons = []
    if gaps:
        reasons.append(f"{len(gaps)} session(s) lack an all-market capture for a venue")
    if calendar_gaps:
        reasons.append(
            f"{len(calendar_gaps)} weekday(s) inside the window have no stored session ({calendar_gaps[0]}..): "
            f"cannot be accounted for without the {MISSING_CALENDAR}")
        gaps = [*gaps, *({"session": d, "reason": "weekday without stored session"} for d in calendar_gaps)]
    delisted = not gaps
    delisted_block = _evidence(delisted, covered, gaps, refs, reasons)

    s_covered, s_gaps, s_refs = _coverage(window, suspension_captures)
    s_reasons = []
    missing = None
    if not suspension_captures:
        missing = MISSING_SUSPENSION_DATASET
        s_reasons.append(f"missing official dataset {missing}")
    elif s_gaps:
        s_reasons.append(f"{len(s_gaps)} session(s) lack a suspension capture for a venue")
    suspended = bool(suspension_captures) and not s_gaps and delisted
    if suspension_captures and not delisted:
        s_reasons.append("suspension evidence is only attested for a window without price-capture gaps")
    suspended_block = _evidence(suspended, s_covered, s_gaps, s_refs, s_reasons,
                                **({"missing_dataset": missing} if missing else {}))
    return {"schema_version": SCHEMA_VERSION, "includes_delisted": delisted, "includes_suspended": suspended,
            "window": {"first": window[0], "last": window[-1], "sessions": len(window)},
            "delisted": delisted_block, "suspended": suspended_block}
