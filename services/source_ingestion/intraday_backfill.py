"""Resumable newest-first two-year ``tw_price_1m`` backfill (SD_INTRADAY_MINUTE_BARS §4).

* One request = one ``(symbol, session_date)`` through the K1 budgeted ``fetch_kbar``
  (default 4,500 requests/hour). HTTP 402 or an exhausted hour stops the run and reports
  ``resume_after_seconds``; credential errors stop immediately. Nothing falls back to
  another source and nothing is retried inside the same hour.
* Order: newest session first, then backwards over the governed trading calendar.
* Resume: a symbol-day already complete in the store manifest is skipped, so a rerun
  continues where the previous one stopped and a finished backfill makes zero requests.
* Records come from K1's ``normalize_kbar_payload`` (complete 271-bar session, governed
  closures). No bar is truncated, fabricated, imputed or volume-adjusted here, and the
  complete raw response is persisted beside the normalized rows.
* The disk guard runs before the first request and before every request and write.
* Nothing here touches ``tw_price_daily`` or the latest daily snapshot store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from .connectors.finmind_taiwan import (
    FINMIND_KBAR_DATASET,
    FINMIND_KBAR_HOURLY_SELF_LIMIT,
    FinMindCredentialError,
    FinMindFetchError,
    FinMindTaiwanKBarAdapter,
    KBarHourlyBudget,
    fetch_kbar,
    shared_kbar_budget,
)
from .intraday_store import (
    DiskBudgetExhausted,
    IntradayStore,
    STATUS_ACCEPTED,
    STATUS_NON_TRADING_DAY,
    STATUS_PROVIDER_EMPTY,
    STATUS_QUARANTINED,
)

DEFAULT_UNIVERSE_SIZE = 200
DEFAULT_HISTORY_DAYS = 730  # two years, rolling
MAX_CONSECUTIVE_FETCH_ERRORS = 5

STOP_COMPLETED = "completed"
STOP_BUDGET = "budget_exhausted"
STOP_QUOTA = "quota_402"
STOP_CREDENTIAL = "credential_error"
STOP_DISK = "disk_budget_exhausted"
STOP_MAX_REQUESTS = "max_requests_reached"
STOP_FETCH_ERRORS = "repeated_fetch_errors"


def governed_trading_days(start: date, end: date, *, extra_closed: Sequence[str] = ()) -> list[date]:
    """Trading days in ``[start, end]``, newest first, from the governed calendar."""
    from services.execution.lean_runtime.instrument_candidates import is_trading_day

    closed = {str(d) for d in extra_closed}
    out: list[date] = []
    day = end
    while day >= start:
        if is_trading_day(day) and day.isoformat() not in closed:
            out.append(day)
        day -= timedelta(days=1)
    return out


def _governed_gate(day: date) -> bool:
    from services.execution.lean_runtime.instrument_candidates import is_trading_day

    return bool(is_trading_day(day))


@dataclass
class BackfillReport:
    status: str = STOP_COMPLETED
    requests: int = 0
    written: int = 0
    unchanged: int = 0
    quarantined: int = 0
    provider_empty: int = 0
    non_trading_day: int = 0
    skipped_already_complete: int = 0
    fetch_errors: list[dict[str, Any]] = field(default_factory=list)
    resume_after_seconds: int = 0
    detail: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _projected_remaining(store: IntradayStore, symbols: Sequence[str], days: Sequence[date]) -> int:
    remaining = 0
    for day in days:
        manifest = store.read_manifest(day.isoformat()) or {"symbols": {}}
        done = {s for s, e in manifest["symbols"].items() if e.get("status") in {
            STATUS_ACCEPTED, STATUS_QUARANTINED, STATUS_PROVIDER_EMPTY, STATUS_NON_TRADING_DAY}}
        remaining += sum(1 for s in symbols if s not in done)
    return remaining


def run_backfill(
    store: IntradayStore,
    fetcher: Any,
    symbols: Sequence[str],
    *,
    end_date: date,
    history_days: int = DEFAULT_HISTORY_DAYS,
    budget: KBarHourlyBudget | None = None,
    adapter: FinMindTaiwanKBarAdapter | None = None,
    max_symbols: int = DEFAULT_UNIVERSE_SIZE,
    max_requests: int | None = None,
    trading_days: Sequence[date] | None = None,
    is_trading_day: Callable[[date], bool] | None = None,
) -> BackfillReport:
    """Run (or resume) the backfill until done, throttled, blocked or out of disk."""
    budget = budget or shared_kbar_budget()
    adapter = adapter or FinMindTaiwanKBarAdapter()
    gate = is_trading_day or _governed_gate
    syms = list(dict.fromkeys(str(s).strip().upper() for s in symbols if str(s).strip()))
    if len(syms) > max_symbols:
        raise ValueError(f"universe of {len(syms)} symbols exceeds max_symbols={max_symbols}")
    days = list(trading_days) if trading_days is not None else governed_trading_days(
        end_date - timedelta(days=history_days), end_date
    )
    days = sorted(days, reverse=True)  # newest first
    report = BackfillReport()

    def finish(status: str, **kw: Any) -> BackfillReport:
        report.status = status
        report.budget = budget.snapshot()
        for key, value in kw.items():
            setattr(report, key, value)
        return report

    remaining = _projected_remaining(store, syms, days)
    try:
        store.check_disk_budget(remaining)
    except DiskBudgetExhausted as exc:
        return finish(STOP_DISK, detail=exc.detail)

    consecutive_errors = 0
    for day in days:
        day_iso = day.isoformat()
        pending = [s for s in syms if not store.is_symbol_day_complete(s, day_iso)]
        report.skipped_already_complete += len(syms) - len(pending)
        if not pending:
            continue
        try:
            store.check_disk_budget(max(remaining, 0))
        except DiskBudgetExhausted as exc:
            return finish(STOP_DISK, detail=exc.detail)
        for symbol in pending:
            if max_requests is not None and report.requests >= max_requests:
                return finish(STOP_MAX_REQUESTS)
            try:
                store.check_disk_budget(max(remaining, 0))
            except DiskBudgetExhausted as exc:
                return finish(STOP_DISK, detail=exc.detail)
            remaining -= 1
            try:
                result = fetch_kbar(fetcher, symbol, day_iso, budget=budget)
            except FinMindCredentialError as exc:
                # Credential error: report and stop; never retry, never substitute data.
                return finish(STOP_CREDENTIAL, detail={"error": type(exc).__name__, "symbol": symbol,
                                                      "session_date": day_iso})
            except FinMindFetchError as exc:
                report.requests += 1
                consecutive_errors += 1
                report.fetch_errors.append({"symbol": symbol, "session_date": day_iso,
                                            "error": type(exc).__name__})
                if consecutive_errors >= MAX_CONSECUTIVE_FETCH_ERRORS:
                    return finish(STOP_FETCH_ERRORS)
                continue
            if result.status == "budget_exhausted":
                return finish(STOP_BUDGET, resume_after_seconds=result.resume_after_seconds)
            if result.status == "quota_402":
                report.requests += 1
                return finish(STOP_QUOTA, resume_after_seconds=result.resume_after_seconds)
            report.requests += 1
            consecutive_errors = 0
            try:
                store.check_disk_budget(max(remaining + 1, 0))  # re-check before the write
            except DiskBudgetExhausted as exc:
                return finish(STOP_DISK, detail=exc.detail)
            _store_result(store, adapter, report, symbol, day, day_iso, result, gate)
    return finish(STOP_COMPLETED)


def _store_result(
    store: IntradayStore,
    adapter: FinMindTaiwanKBarAdapter,
    report: BackfillReport,
    symbol: str,
    day: date,
    day_iso: str,
    result: Any,
    gate: Callable[[date], bool],
) -> None:
    fetched_at = str((result.quota_meta or {}).get("fetched_at") or "")
    if not fetched_at:
        fetched_at = datetime.now(timezone.utc).isoformat()
    norm = adapter.normalize_kbar_payload(
        result.payload or {}, symbol=symbol, session_date=day_iso, fetched_at=fetched_at,
        quota_meta=result.quota_meta, is_trading_day=gate,
    )
    envelope = {
        "dataset": FINMIND_KBAR_DATASET, "symbol": symbol, "session_date": day_iso,
        "fetched_at": fetched_at, "response_content_hash": norm.response_content_hash,
        "raw_row_count": norm.raw_row_count, "kept_bars": norm.kept_bars,
        "dropped_outside_session": norm.dropped_outside_session,
        "dropped_malformed": norm.dropped_malformed,
        "quota_meta": {k: v for k, v in dict(result.quota_meta or {}).items() if k != "request_url"},
        "raw_rows": [dict(r) for r in norm.raw_rows],
    }
    if norm.rejected_non_trading_day:
        store.mark_non_trading_day(symbol, day_iso, envelope)
        report.non_trading_day += 1
        return
    rows = [dict(r.metadata["normalized_row"]) for r in norm.records]
    if norm.conflicting_duplicates or norm.unsupported_session_minutes:
        reason = ("conflicting_duplicate_minute_bars" if norm.conflicting_duplicates
                  else "unsupported_session_shape")
        store.quarantine_symbol_day(
            symbol, day_iso, reason=reason,
            detail={"conflicting_duplicates": list(norm.conflicting_duplicates),
                    "unsupported_session_minutes": list(norm.unsupported_session_minutes)},
            incoming_rows=rows, raw_envelope=envelope,
        )
        report.quarantined += 1
        return
    put = store.put_symbol_day(symbol, day_iso, rows, raw_envelope=envelope)
    if put.status in ("written", "added"):
        report.written += 1
    elif put.status == "unchanged":
        report.unchanged += 1
    elif put.status == "quarantined":
        report.quarantined += 1
    elif put.status == STATUS_PROVIDER_EMPTY:
        report.provider_empty += 1


def coverage_report(
    store: IntradayStore,
    symbols: Sequence[str],
    *,
    end_date: date,
    history_days: int = DEFAULT_HISTORY_DAYS,
    trading_days: Sequence[date] | None = None,
) -> dict[str, Any]:
    """Actual per-symbol coverage and gaps against the governed trading calendar."""
    days = sorted(
        trading_days if trading_days is not None
        else governed_trading_days(end_date - timedelta(days=history_days), end_date),
        reverse=True,
    )
    day_isos = [d.isoformat() for d in days]
    manifests = {d: (store.read_manifest(d) or {"symbols": {}})["symbols"] for d in day_isos}
    per_symbol: dict[str, Any] = {}
    for symbol in dict.fromkeys(str(s).strip().upper() for s in symbols):
        covered, quarantined, empty, missing = [], [], [], []
        for d in day_isos:
            entry = manifests[d].get(symbol)
            status = entry.get("status") if entry else None
            if status == STATUS_ACCEPTED:
                covered.append(d)
            elif status == STATUS_QUARANTINED:
                quarantined.append(d)
            elif status == STATUS_PROVIDER_EMPTY:
                empty.append(d)
            elif status != STATUS_NON_TRADING_DAY:
                missing.append(d)
        per_symbol[symbol] = {
            "expected_trading_days": len(day_isos),
            "covered_days": len(covered),
            "newest_covered": covered[0] if covered else None,
            "oldest_covered": covered[-1] if covered else None,
            "quarantined_days": quarantined,
            "provider_empty_days": empty,
            "missing_days": missing,
            "missing_count": len(missing),
            "coverage_ratio": (len(covered) / len(day_isos)) if day_isos else 0.0,
        }
    return {
        "schema_version": "tw_price_1m_coverage.v1",
        "window": {"start": day_isos[-1] if day_isos else None, "end": day_isos[0] if day_isos else None,
                   "trading_days": len(day_isos)},
        "symbols": per_symbol,
    }
