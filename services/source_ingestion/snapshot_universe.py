"""Active-universe membership for the latest daily market snapshot projection.

DATA_STRATEGY_SOURCE_SYSTEM_DESIGN §4.4: every tier keeps daily price in the governed
``market_data_store`` partitions; only the *latest snapshot projection* (a read model served
per symbol by ``GET /api/source-ingest/snapshots/latest``) is limited to symbols in use.

Membership = governed active paper personas + ``SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS`` (always
members) + every symbol requested through the snapshot API within the last
``MEMBERSHIP_TRADING_DAYS`` trading days.  A first request for a non-member symbol rebuilds its
snapshot from the stored governed daily partitions through the same point-building path the
live projection uses, so ``snapshot_id`` and points match the all-symbol projection.  A member
not requested for the window is withdrawn with the existing tombstone and recorded as a
``UniverseTransition`` (core/candidate -> archive; archive keeps daily price in the store).

Fail-safe: if the membership state is missing or unreadable nothing is ever dropped and
``journal_stats`` reports the reason.  This module reuses the types of ``active_universe``.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .active_universe import (
    GOVERNED_ACTIVE_TW_PAPER_PERSONAS,
    UniverseTier,
    UniverseTransition,
    _extract_symbols_from_persona_entry,
    _normalize_tw_underlying_code,
)
from .connectors.base import SourceRecord
from .requirement_state import (
    DAILY_SNAPSHOT_DATASET,
    LatestMarketSnapshot,
    LatestMarketSnapshotStore,
    MarketSnapshotStateError,
)

STATE_SCHEMA_VERSION = "source_ingest_snapshot_universe.v1"
MEMBERSHIP_TRADING_DAYS = 10
DAILY_PRICE_DATASET = "tw_price_daily"
TRIGGERED_BY = "snapshot_universe"
MAX_TRANSITIONS_KEPT = 500
_SWEEP_INTERVAL_SECONDS = 3600
_MISS_TTL_SECONDS = 600
_FALLBACK = object()  # the daily bars seed cannot answer: the run file read runs instead
_DATE_DIR = re.compile(r"^date=(\d{4}-\d{2}-\d{2})$")


def member_key(symbol: Any) -> str:
    """One key per instrument: ``2330``, ``2330.TW`` and ``2330.TWSE`` are the same member."""
    return _normalize_tw_underlying_code(symbol)


def static_member_symbols() -> dict[str, str]:
    """Always-members: governed active paper personas and ``SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS``."""
    symbols: dict[str, str] = {}
    for entry in GOVERNED_ACTIVE_TW_PAPER_PERSONAS:
        for symbol in _extract_symbols_from_persona_entry(entry):
            symbols.setdefault(member_key(symbol), str(symbol).upper())
    for part in re.split(r"[,;\s]+", os.getenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "").strip()):
        if part:
            symbols.setdefault(member_key(part), part.upper())
    return symbols


def weekdays_between(start: str, end: str) -> int:
    """Weekdays in (start, end]; the trading-day measure (no exchange calendar is available)."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    count, day = 0, first
    while day < last:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return count


def _utc_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SnapshotUniverse:
    def __init__(
        self,
        store: LatestMarketSnapshotStore,
        *,
        state_path: str | Path,
        storage_root: str | Path,
        connector_market: Callable[[str], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
        window_trading_days: int = MEMBERSHIP_TRADING_DAYS,
    ) -> None:
        self.store = store
        self.state_path = Path(state_path)
        self.storage_root = Path(storage_root)
        self._connector_market = connector_market or (lambda _connector_id: None)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.window = window_trading_days
        self._lock = threading.RLock()
        self._members: dict[str, dict[str, Any]] = {}
        self._transitions: list[dict[str, Any]] = []
        self._transitions_total = 0
        self._state = "missing"  # ready | missing | unreadable
        self._reason: str | None = "membership_state_missing"
        self._misses: dict[str, float] = {}
        self._last_sweep = 0.0
        self._last_seed: dict[str, Any] | None = None
        self._last_fallback_reason: str | None = None
        self._reader: Any = None
        self._load()
        store.admission = self.admits
        store.stats_extra = self.stats

    # ----- state ---------------------------------------------------------------------------
    def _load(self) -> None:
        with self._lock:
            if not self.state_path.exists():
                # Not activated yet (or lost): behave exactly like the all-symbol projection.
                self._state, self._reason = "missing", "membership_state_missing"
                return
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if data.get("schema_version") != STATE_SCHEMA_VERSION or not isinstance(data.get("members"), dict):
                    raise ValueError("schema")
                members = {
                    str(k): {"symbol": str(v["symbol"]), "last_requested_date": date.fromisoformat(v["last_requested_date"]).isoformat(),
                             "origin": str(v.get("origin") or "request")}
                    for k, v in data["members"].items()
                }
                transitions = [dict(t) for t in data.get("transitions", [])]
                self._transitions_total = int(data.get("transitions_total", len(transitions)))
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self._state, self._reason = "unreadable", "membership_state_unreadable"
                return
            self._members, self._transitions = members, transitions
            self._state, self._reason = "ready", None

    def _write_state(self) -> None:
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "updated_at": _utc_iso(self._clock()),
            "members": self._members,
            "transitions": self._transitions[-MAX_TRANSITIONS_KEPT:],
            "transitions_total": self._transitions_total,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(self.state_path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.state_path)
        self._state, self._reason = "ready", None

    @property
    def ready(self) -> bool:
        return self._state == "ready"

    def initialize(self) -> dict[str, Any]:
        """Operator action: adopt membership for an existing journal, then withdraw the rest.

        Never replaces an unreadable state (that needs a human); a ready state is left alone.
        """
        with self._lock:
            if self._state == "unreadable":
                raise MarketSnapshotStateError("membership state is unreadable; refusing to reinitialize")
            if self._state == "missing":
                self._write_state()
            return self.sweep(force=True)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {
                "state": self._state,
                "member_count": len(self._members),
                "static_member_count": len(static_member_symbols()),
                "window_trading_days": self.window,
                "transition_count": self._transitions_total,
            }
            if self._reason:
                out["reason"] = self._reason
            if self._last_seed:
                out["last_seed"] = dict(self._last_seed)
            return out

    # ----- membership ----------------------------------------------------------------------
    def admits(self, symbol: str) -> bool:
        """Write-side filter: while state is not ready every symbol is admitted (nothing dropped)."""
        if not self.ready:
            return True
        key = member_key(symbol)
        return key in self._members or key in static_member_symbols()

    def is_member(self, symbol: str) -> bool:
        return self.admits(symbol)

    def _today(self) -> str:
        return self._clock().astimezone(timezone(timedelta(hours=8))).date().isoformat()

    def _touch(self, symbol: str, origin: str) -> None:
        key = member_key(symbol)
        today = self._today()
        entry = self._members.get(key)
        if entry is not None and entry["last_requested_date"] == today:
            return
        self._members[key] = {"symbol": str(symbol).upper(), "last_requested_date": today, "origin": origin}
        self._write_state()

    # ----- request path --------------------------------------------------------------------
    def snapshot_for_request(self, symbol: str) -> LatestMarketSnapshot | None:
        """The daily snapshot for ``symbol``; a non-member is seeded from stored partitions first."""
        snapshot = self.store.get(symbol, DAILY_SNAPSHOT_DATASET)
        if not self.ready:
            return snapshot
        with self._lock:
            try:
                if snapshot is not None:
                    self._touch(symbol, "request")
                    return snapshot
                key = member_key(symbol)
                if self._misses.get(key, 0.0) > time.monotonic():
                    return None
                snapshot = self.seed(symbol)
                if snapshot is None:
                    self._misses[key] = time.monotonic() + _MISS_TTL_SECONDS
                    return None
                self._touch(symbol, "request")
                return snapshot
            except OSError:
                return snapshot
            finally:
                self.maybe_sweep()

    # ----- seeding -------------------------------------------------------------------------
    def _partition_dates(self) -> list[tuple[str, list[Path]]]:
        by_date: dict[str, list[Path]] = {}
        raw = self.storage_root / "raw"
        if raw.is_dir():
            for source in sorted(raw.iterdir()):
                base = source / DAILY_PRICE_DATASET
                if not base.is_dir():
                    continue
                for part in base.iterdir():
                    match = _DATE_DIR.match(part.name)
                    if match and part.is_dir():
                        by_date.setdefault(match.group(1), []).extend(sorted(part.glob("*.jsonl*")))
        return sorted(by_date.items())

    @staticmethod
    def _lines(path: Path, code: str) -> Iterator[str]:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
            for line in handle:
                if code in line:  # cheap prefilter before JSON parsing
                    yield line

    def seed(self, symbol: str) -> LatestMarketSnapshot | None:
        """Build ``symbol``'s snapshot from stored governed daily partitions and append it.

        With ``PANTHEON_DAILY_PRICE_READER=daily_bars`` the newest ``max_closes`` closes come from the daily
        bars store (one partition per trade date); when it cannot answer (see ``_seed_from_daily_bars``) the run
        file read below runs exactly as before.  ``last_seed["path"]`` says which path answered.
        """
        from .market_data_storage import daily_price_reader_mode

        started = time.monotonic()
        fallback: str | None = None
        if daily_price_reader_mode() == "daily_bars":
            outcome = self._seed_from_daily_bars(symbol, started)
            if outcome is not _FALLBACK:
                return outcome  # type: ignore[return-value]
            fallback = self._last_fallback_reason
        snapshot = self._seed_from_run_files(symbol, started)
        if fallback is not None and self._last_seed is not None:
            self._last_seed["fallback_reason"] = fallback
        return snapshot

    def _bars_reader(self) -> Any:
        from .daily_bars_reader import DailyBarsReader

        if self._reader is None:
            self._reader = DailyBarsReader(self.storage_root)
        return self._reader

    def _seed_from_daily_bars(self, symbol: str, started: float) -> Any:
        """Seed from the newest ``max_closes`` stored trade dates; ``_FALLBACK`` when that cannot answer.

        Falls back (run file read, reason in ``last_seed``) when: the store holds fewer trade dates than needed,
        the symbol holds closes on some but fewer than ``needed`` of them (older run files may hold more), a
        form of the code is ambiguous, a lineage run or the observed time is missing, the day carries several
        calendar proofs, or the store cannot be read.  A symbol with NO close in a window the store fully covers
        is a miss (``None``) without any run file read.

        Named differences from the run file seed (closes, opens and market are equal; see the evidence file):
        ``event_time`` is the 14:30 +08:00 publish time of the trade date, ``content_ref`` is
        ``daily_bars://<symbol>/<date>``, ``collector_close`` (provisional relay lineage) is not stored in daily
        bars so a provisional close reads as a plain close, and ``observed_at`` is the newest run time of the
        lineage instead of the newest file mtime.
        """
        from .daily_bars_store import DailyBarsError

        target = member_key(symbol)
        needed = self.store.max_closes
        columns = ["symbol", "trade_date", "open", "close", "market", "source_id", "ingest_run_id"]
        try:
            reader = self._bars_reader()
            dates = reader.trade_dates()
            if len(dates) < needed:
                return self._fallback(f"daily bars hold {len(dates)} trade dates, {needed} needed")
            window = dates[-needed:]
            rows = reader.read_close_window(target, dates[-1], needed, any_form=True, columns=columns)
            lineage = {d: reader.read_lineage(d) for d in sorted({r["trade_date"] for r in rows})}
        except (DailyBarsError, ImportError, OSError, ValueError) as exc:
            return self._fallback(f"daily bars unreadable: {type(exc).__name__}: {exc}")
        rows = [r for r in rows if r.get("close") is not None and member_key(r["symbol"]) == target]
        partitions = len(window)
        if not rows:
            self._last_seed = {"symbol": str(symbol).upper(), "records": 0, "files_read": 0, "partitions_read": partitions,
                               "path": "daily_bars", "seconds": round(time.monotonic() - started, 4)}
            return None
        if len(rows) < needed:
            return self._fallback(f"symbol holds {len(rows)} closes in the newest {needed} stored dates")
        records: list[SourceRecord] = []
        context: list[tuple[str, str | None]] = []
        observed_times: list[str] = []
        for number, row in enumerate(rows):
            day = row["trade_date"]
            run_id = str(row.get("ingest_run_id") or "")
            run = (lineage.get(day) or {}).get("runs", {}).get(run_id)
            if not run_id or not isinstance(run, dict) or not run.get("connector_id") or not run.get("observed_at"):
                return self._fallback(f"lineage of run {run_id or '?'} on {day} is missing")
            observed_times.append(str(run["observed_at"]))
            normalized: dict[str, Any] = {
                "symbol": row["symbol"], "close": row["close"], "open": row.get("open"), "market": row.get("market"),
                "trade_date": day, "event_time": f"{day}T06:30:00Z",
            }
            if number == len(rows) - 1:  # the snapshot keeps the newest governed calendar proof
                proofs = list(((lineage.get(day) or {}).get("calendar_evidence") or {}).values())
                if len(proofs) > 1:
                    return self._fallback(f"{len(proofs)} calendar proofs on {day}")
                if proofs:
                    normalized["calendar_evidence"] = proofs[0]
            records.append(SourceRecord(
                source_id=str(row.get("source_id") or run_id), connector_id=str(run["connector_id"]),
                source_type="market", title=f"{row['symbol']} {day}",
                content_ref=f"daily_bars://{row['symbol']}/{day}",
                metadata={"dataset": DAILY_PRICE_DATASET, "normalized_row": normalized}, created_at=f"{day}T06:30:00Z"))
            context.append((run_id, self._connector_market(str(run["connector_id"]))))
        self.store.append_normalized_records(
            records, ingest_run_id=context[-1][0], observed_at=max(observed_times), record_context=context,
            enforce_admission=False)
        self._last_seed = {"symbol": str(symbol).upper(), "records": len(records), "files_read": 0,
                           "partitions_read": partitions, "path": "daily_bars",
                           "seconds": round(time.monotonic() - started, 4)}
        return self.store.get(symbol, DAILY_SNAPSHOT_DATASET)

    def _fallback(self, reason: str) -> Any:
        self._last_fallback_reason = reason
        return _FALLBACK

    def _seed_from_run_files(self, symbol: str, started: float) -> LatestMarketSnapshot | None:
        from .pipeline import is_index_breadth_record, is_minute_bar_record

        target = member_key(symbol)
        code = target.split(".")[0]
        needed = self.store.max_closes
        records: list[SourceRecord] = []
        context: list[tuple[str, str | None]] = []
        newest_mtime = 0.0
        days_with_rows = 0
        files_read = 0
        for day, files in reversed(self._partition_dates()):
            day_records: list[tuple[SourceRecord, str, str | None]] = []
            for path in sorted(files, key=lambda p: p.name):
                files_read += 1
                run_id = path.name.split(".jsonl")[0]
                for line in self._lines(path, code):
                    try:
                        record = SourceRecord.from_dict(json.loads(line))
                    except (ValueError, KeyError, TypeError):
                        continue
                    if is_minute_bar_record(record) or is_index_breadth_record(record):
                        continue
                    candidate = self.store._point_from_record(record, ingest_run_id=run_id, default_market=None)
                    if candidate is None or member_key(candidate[0]) != target:
                        continue
                    day_records.append((record, run_id, self._connector_market(record.connector_id)))
                    newest_mtime = max(newest_mtime, path.stat().st_mtime)
            if day_records:
                days_with_rows += 1
                # prepend: partitions are walked newest first, replay must be oldest first
                records = [r for r, _, _ in day_records] + records
                context = [(rid, mkt) for _, rid, mkt in day_records] + context
            if days_with_rows >= needed:
                break
        elapsed = round(time.monotonic() - started, 4)
        self._last_seed = {"symbol": str(symbol).upper(), "records": len(records), "files_read": files_read,
                           "path": "run_files", "seconds": elapsed}
        if not records:
            return None
        observed = _utc_iso(datetime.fromtimestamp(newest_mtime, tz=timezone.utc))
        self.store.append_normalized_records(
            records,
            ingest_run_id=context[-1][0],
            observed_at=observed,
            record_context=context,
            enforce_admission=False,
        )
        return self.store.get(symbol, DAILY_SNAPSHOT_DATASET)

    # ----- sweep ---------------------------------------------------------------------------
    def maybe_sweep(self) -> None:
        if time.monotonic() - self._last_sweep >= _SWEEP_INTERVAL_SECONDS:
            try:
                self.sweep()
            except (MarketSnapshotStateError, OSError):
                pass

    def sweep(self, *, force: bool = False) -> dict[str, Any]:
        """Withdraw non-members and expired members; seed static members missing from the store."""
        with self._lock:
            self._last_sweep = time.monotonic()
            if not self.ready:
                return {"swept": False, "reason": self._reason}
            today = self._today()
            effective_at = _utc_iso(self._clock())
            statics = static_member_symbols()
            transitions: list[UniverseTransition] = []
            for key, entry in list(self._members.items()):
                if key in statics:
                    continue
                if weekdays_between(entry["last_requested_date"], today) > self.window:
                    del self._members[key]
                    transitions.append(UniverseTransition(
                        symbol=entry["symbol"], from_tier=UniverseTier.CANDIDATE, to_tier=UniverseTier.ARCHIVE,
                        reason=f"not_requested_{self.window}_trading_days", triggered_by=TRIGGERED_BY,
                        effective_at=effective_at, metadata={"last_requested_date": entry["last_requested_date"]}))
            live = self.store.live_symbols()
            drop = [sym for sym in live if member_key(sym) not in self._members and member_key(sym) not in statics]
            removed = self.store.withdraw_symbols(drop, observed_at=effective_at) if drop else []
            seen = {t.symbol for t in transitions}
            for sym in removed:
                if sym not in seen:
                    transitions.append(UniverseTransition(
                        symbol=sym, from_tier=UniverseTier.CANDIDATE, to_tier=UniverseTier.ARCHIVE,
                        reason="not_a_member_of_active_universe", triggered_by=TRIGGERED_BY, effective_at=effective_at))
            self._transitions.extend(t.to_dict() for t in transitions)
            self._transitions_total += len(transitions)
            del self._transitions[:-MAX_TRANSITIONS_KEPT]
            seeded = []
            for key, symbol in statics.items():
                if self.store.get(symbol, DAILY_SNAPSHOT_DATASET) is None and self.seed(symbol) is not None:
                    seeded.append(symbol)
                self._members.setdefault(key, {"symbol": symbol, "last_requested_date": today, "origin": "governed"})
            self._write_state()
            return {"swept": True, "withdrawn": len(removed), "transitions": len(transitions), "seeded_static": seeded}
