"""Provisional collector close vs official OpenAPI close (TW-SAME-DAY-CLOSE-VIA-COLLECTOR-20261112).

The exchange-website close relayed by the approved collector host is a provisional same-day input.
The OpenAPI ``tw_price_daily`` row stays the official truth: it is never replaced or hidden by a
collector row, and when it arrives it is compared with the collector close. Agreement is recorded;
a difference keeps the official value and records a typed ``collector_close_mismatch``.

This module is pure (no I/O) so the snapshot store, the admission rule and the refresh preflight
share one definition of "valid collector close lineage" and one comparison.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

COLLECTOR_CLOSE_KEY = "collector_close"
COLLECTOR_CLOSE_MISMATCH = "collector_close_mismatch"
OUTCOME_AGREE = "agree"
OUTCOME_MISMATCH = "mismatch"
CLOSE_COMPARE_DECIMALS = 4
# Only the exchange website hosts the operator approved fetching through the collector host.
EXCHANGE_WEBSITE_HOSTS = ("www.twse.com.tw", "www.tpex.org.tw")
COLLECTOR_PRICE_HISTORY_SOURCE_IDS = (
    "collector-tw-twse-price-history",
    "collector-tw-tpex-price-history",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TAIPEI = timezone(timedelta(hours=8))
MAX_RECONCILIATION_ENTRIES = 60


def taipei_date(timestamp: str) -> str:
    parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_TAIPEI).date().isoformat()


def collector_close_lineage(metadata: Any) -> dict[str, str] | None:
    """Strictly validated lineage of a collector-relayed close, or None when absent/invalid.

    It must name the collector source, the exchange website endpoint (https, an approved
    exchange host) and the bundle digest; anything less is not admissible as a close.
    """
    if not isinstance(metadata, Mapping):
        return None
    raw = metadata.get(COLLECTOR_CLOSE_KEY)
    if not isinstance(raw, Mapping) or raw.get("provisional") is not True or raw.get("official") is not False:
        return None
    source_id = str(raw.get("collector_source_id") or "").strip()
    endpoint = str(raw.get("exchange_endpoint") or "").strip()
    digest = str(raw.get("bundle_sha256") or "").strip().lower()
    trade_date = str(raw.get("trade_date") or "").strip()
    if source_id not in COLLECTOR_PRICE_HISTORY_SOURCE_IDS or not _SHA256_RE.fullmatch(digest):
        return None
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or (parsed.hostname or "") not in EXCHANGE_WEBSITE_HOSTS:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", trade_date):
        return None
    return {
        "collector_source_id": source_id,
        "exchange_endpoint": endpoint,
        "bundle_sha256": digest,
        "trade_date": trade_date,
    }


def compare_close(
    *,
    symbol: str,
    trade_date: str,
    collector_close: float,
    official_close: float,
    lineage: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare one symbol/date; the official close always wins."""
    agree = round(float(collector_close), CLOSE_COMPARE_DECIMALS) == round(float(official_close), CLOSE_COMPARE_DECIMALS)
    entry: dict[str, Any] = {
        "symbol": symbol,
        "trade_date": trade_date,
        "outcome": OUTCOME_AGREE if agree else OUTCOME_MISMATCH,
        "collector_close": float(collector_close),
        "official_close": float(official_close),
        "kept": "official",
        "collector_source_id": lineage.get("collector_source_id"),
        "exchange_endpoint": lineage.get("exchange_endpoint"),
        "bundle_sha256": lineage.get("bundle_sha256"),
    }
    if not agree:
        entry["reason_code"] = COLLECTOR_CLOSE_MISMATCH
    return entry


def merge_reconciliation(
    prior: Sequence[Mapping[str, Any]], new: Sequence[Mapping[str, Any]], *, oldest_trade_date: str | None
) -> tuple[dict[str, Any], ...]:
    by_date = {str(e["trade_date"]): dict(e) for e in prior}
    by_date.update({str(e["trade_date"]): dict(e) for e in new})
    kept = [
        by_date[d] for d in sorted(by_date)
        if oldest_trade_date is None or d >= oldest_trade_date
    ]
    return tuple(kept[-MAX_RECONCILIATION_ENTRIES:])


def reconciliation_readback(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    mismatches = [dict(e) for e in entries if e.get("outcome") == OUTCOME_MISMATCH]
    return {
        "compared": len(entries),
        "agreed": sum(1 for e in entries if e.get("outcome") == OUTCOME_AGREE),
        "mismatched": len(mismatches),
        "mismatches": mismatches,
    }


def covered_collector_close_session(connector_items: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Session (``{trade_date, lineage}``) for which every collector price-history source has an admitted close.

    ``connector_items`` are controller readback connector entries. A session counts only when
    BOTH the TWSE and the TPEx collector connectors report a latest record whose lineage is valid
    and names that same trade date (the official connector spans both exchanges).
    """
    dates: dict[str, str] = {}
    lineages: list[dict[str, str]] = []
    for item in connector_items:
        if not isinstance(item, Mapping):
            continue
        connector_id = str(item.get("connector_id") or "")
        if connector_id not in COLLECTOR_PRICE_HISTORY_SOURCE_IDS:
            continue
        record = item.get("latest_source_record")
        meta = record.get("metadata") if isinstance(record, Mapping) else None
        lineage = collector_close_lineage(meta)
        if lineage is None or lineage["collector_source_id"] != connector_id:
            return None
        dates[connector_id] = lineage["trade_date"]
        lineages.append(lineage)
    if set(dates) != set(COLLECTOR_PRICE_HISTORY_SOURCE_IDS) or len(set(dates.values())) != 1:
        return None
    return {"trade_date": next(iter(dates.values())), "lineage": lineages}
