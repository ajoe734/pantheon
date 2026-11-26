"""Retention sweeper (SOURCE-RETENTION-SWEEPER-20261118-R2).

Design of record: SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16 section 4.3 (expiry table, warrant rules), section 7
step 8, section 9 row T5.

What it deletes, per run, and what it never deletes:

* partitions past the ``retention_days`` declared for their dataset (daily bars 2555 days, keyed datasets as declared
  in ``KEYED_DATASETS``; a dataset with ``retention_days=None`` never expires);
* expired warrants (decision B, final 2026-11-17): at the first run after a warrant leaves the exchange listing its
  daily bars, its current snapshot record and its lineage are deleted; nothing is archived. Expiry comes only from the
  listing (``ListingState.expired_warrants``); the snapshot record goes through ``delete_symbols``;
* never: bars of a delisted ordinary share (the survivorship gate needs them), a date or run pinned by a gate proof or
  paper evidence (``PinSource``), any symbol the warrant step cannot prove is a warrant (a four digit code is never
  one).

Fail closed (a readable reason in the report and the health payload, nothing deleted):

* the pin source is unreadable or not configured -> nothing is deleted at all (a pin cannot be ruled out);
* the listing is unreadable or older than the last trading day -> no daily bars are deleted (neither warrants nor
  retention: the delisted-share rule needs a current listing), no snapshot record is deleted.

Dry run is the default. The real run needs ``SOURCE_INGEST_RETENTION_SWEEP_MODE=delete``. A real run refuses to delete
more than ``MAX_DELETE_SHARE`` (20 %) of a store in one run unless ``SOURCE_INGEST_RETENTION_ALLOW_LARGE_DELETE=1``.
It runs at most once per trading day (the report file records the day) and raises ``alarm`` in the health payload when
no run happened for ``ALARM_AFTER_TRADING_DAYS`` (2) trading days.

The only thing it persists is ONE report file, replaced each run (temp file then rename), at most
``REPORT_MAX_BYTES`` bytes: one compact JSON line, so the storage manifest registers it by name.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .daily_bars_store import KEYED_DATASETS, DailyBarsStore, KeyedDatasetStore
from .pin_source import PinSource, PinSourceUnavailable
from .security_listing import ListingState, bare_symbol

SCHEMA_VERSION = "retention_report.v1"
MODE_ENV = "SOURCE_INGEST_RETENTION_SWEEP_MODE"  # the one named setting: unset / dry_run = report only, delete = real
ALLOW_LARGE_ENV = "SOURCE_INGEST_RETENTION_ALLOW_LARGE_DELETE"  # explicit override of MAX_DELETE_SHARE
MODES = ("dry_run", "delete")
MAX_DELETE_SHARE = 0.20  # one real run never deletes more than 20 % of a store without the override
ALARM_AFTER_TRADING_DAYS = 2
REPORT_FILENAME = "retention-last-run.jsonl"
REPORT_MAX_BYTES = 64 * 1024  # one replaced file; asserted on write
SWEEP_YIELD_SECONDS = 0.005  # pause after each rewritten date so a reader thread of the same process is not starved
SNAPSHOT_BATCH = 20  # symbols per snapshot delete transaction: a short writer lock, never the whole sweep
SAMPLE_LIMIT = 10
DAILY_BARS_RETENTION_DAYS = 2555  # prices (design section 4.3)
ORDINARY_CODE_RE = re.compile(r"^\d{4}$")

STORES = ("daily_bars", "lineage", "snapshot_current", "keyed")


def _taipei_today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def _weekday_between(start: date, end: date) -> int:
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def sweep_mode(environ: Mapping[str, str] | None = None) -> tuple[str, str | None]:
    """(mode, problem). Unset is ``dry_run``; an unknown value is ``dry_run`` plus a reported problem, never ``delete``."""
    raw = str((environ if environ is not None else os.environ).get(MODE_ENV, "") or "").strip().lower()
    if not raw:
        return "dry_run", None
    if raw in MODES:
        return raw, None
    return "dry_run", f"{MODE_ENV}={raw!r} is not one of {list(MODES)}; using dry_run"


def _flag(environ: Mapping[str, str] | None, name: str) -> bool:
    return str((environ if environ is not None else os.environ).get(name, "") or "").strip().lower() in ("1", "true", "yes")


def _is_expired_warrant_candidate(symbol: str, registry: Mapping[str, Any]) -> bool:
    """The warrant step's own guard on top of the listing: never a four digit code, never a known ordinary share."""
    bare = bare_symbol(symbol)
    return not ORDINARY_CODE_RE.match(bare) and bare not in registry


class RetentionSweeper:
    def __init__(
        self,
        *,
        daily_bars_store: DailyBarsStore,
        report_path: str | Path,
        listing_state_path: str | Path | None = None,
        listing_provider: Any | None = None,
        snapshot_store: Any | None = None,
        pin_source: PinSource | None = None,
        keyed_root: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
        today: Callable[[], str] | None = None,
        trading_days_between: Callable[[date, date], int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bars = daily_bars_store
        self.report_path = Path(report_path)
        self.listing_state_path = Path(listing_state_path) if listing_state_path else getattr(listing_provider, "state_path", None)
        self.snapshot_store = snapshot_store
        self.pin_source = pin_source
        self.keyed_root = Path(keyed_root) if keyed_root is not None else daily_bars_store.root
        self._environ = environ
        self._today = today or _taipei_today
        self._tdb = trading_days_between or _weekday_between
        self._clock = clock
        self._running = threading.Lock()

    # ----- report file -------------------------------------------------------------------------
    def last_report(self) -> dict[str, Any] | None:
        try:
            if self.report_path.stat().st_size > REPORT_MAX_BYTES:
                return {"unreadable": f"report file is over the {REPORT_MAX_BYTES} byte cap"}
            return json.loads(self.report_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            return {"unreadable": f"{type(exc).__name__}: {exc}"[:200]}

    def _write_report(self, report: dict[str, Any]) -> int:
        data = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        raw = (data + "\n").encode("utf-8")
        if len(raw) > REPORT_MAX_BYTES:
            raise RuntimeError(f"retention report is {len(raw)} bytes, over the {REPORT_MAX_BYTES} byte cap")
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.report_path.with_name(self.report_path.name + ".tmp")
        with open(tmp, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.report_path)
        return len(raw)

    # ----- health --------------------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        mode, problem = sweep_mode(self._environ)
        today = self._today()
        report = self.last_report()
        out: dict[str, Any] = {
            "schema_version": "retention_health.v1", "mode": mode, "mode_problem": problem, "today": today,
            "last_run_date": None, "last_run_at": None, "last_run_status": None, "last_run_reason": None,
            "days_since_last_run": None, "alarm": None, "stores": None,
        }
        if report is None:
            out["alarm"] = "retention_never_ran"
            return out
        if "unreadable" in report:
            out["alarm"] = "retention_report_unreadable"
            out["last_run_reason"] = report["unreadable"]
            return out
        last = report.get("run_date")
        out.update(last_run_date=last, last_run_at=report.get("finished_at"), last_run_status=report.get("status"),
                   last_run_reason=report.get("reason"), last_run_mode=report.get("mode"), stores=report.get("stores"),
                   warrant_step=report.get("warrant_step"), duration_seconds=report.get("duration_seconds"))
        try:
            age = self._tdb(date.fromisoformat(last), date.fromisoformat(today))
        except (TypeError, ValueError):
            out["alarm"] = "retention_report_unreadable"
            return out
        out["days_since_last_run"] = age
        if age >= ALARM_AFTER_TRADING_DAYS:
            out["alarm"] = f"retention_not_run_for_{age}_trading_days"
        return out

    # ----- one run -------------------------------------------------------------------------------
    def run_if_due(self) -> dict[str, Any]:
        """The controller entry: at most once per trading day (a completed report of today ends the call)."""
        today = self._today()
        if date.fromisoformat(today).weekday() >= 5:
            return {"ran": False, "reason": "not_a_trading_day", "run_date": today}
        report = self.last_report()
        if report and report.get("run_date") == today and "unreadable" not in report:
            return {"ran": False, "reason": "already_ran_today", "run_date": today, "status": report.get("status")}
        return self.run()

    def run(self) -> dict[str, Any]:
        if not self._running.acquire(blocking=False):
            return {"ran": False, "reason": "already_running"}
        try:
            return self._run()
        finally:
            self._running.release()

    def _run(self) -> dict[str, Any]:
        started = self._clock()
        today = self._today()
        mode, mode_problem = sweep_mode(self._environ)
        real = mode == "delete"
        override = _flag(self._environ, ALLOW_LARGE_ENV)
        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION, "run_date": today, "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": mode, "mode_problem": mode_problem, "status": "completed", "reason": None,
            "max_delete_share": MAX_DELETE_SHARE, "large_delete_override": override,
            "stores": {s: _empty_store() for s in STORES}, "warrant_step": {"status": "not_run", "reason": None},
            "pins": {"resolved": False, "reason": None},
        }
        try:
            self._sweep(report, today=today, real=real, override=override)
        except Exception as exc:  # noqa: BLE001 - a failed sweep is reported, never raised into the controller tick
            report["status"] = "failed"
            report["reason"] = f"{type(exc).__name__}: {exc}"[:300]
        report["duration_seconds"] = round(self._clock() - started, 3)
        report["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            report["report_bytes"] = self._write_report(report)
        except Exception as exc:  # noqa: BLE001 - an oversized or unwritable report is refused, never half written
            report["status"] = "failed"
            report["reason"] = f"report_not_written: {type(exc).__name__}: {exc}"[:300]
        return {"ran": True, **report}

    # ----- plan + apply --------------------------------------------------------------------------
    def _sweep(self, report: dict[str, Any], *, today: str, real: bool, override: bool) -> None:
        stores = report["stores"]
        # 1. pins: unresolved -> nothing at all is deleted
        pin_dates: frozenset[str] = frozenset()
        pin_runs: frozenset[str] = frozenset()
        if self.pin_source is None:
            report["pins"] = {"resolved": False, "reason": "pin_source_not_configured"}
        else:
            try:
                pins = self.pin_source.read()
                pin_dates, pin_runs = pins.trade_dates, pins.run_ids
                report["pins"] = {"resolved": True, "pinned_run_ids": len(pin_runs), "pinned_trade_dates": len(pin_dates)}
            except PinSourceUnavailable as exc:
                report["pins"] = {"resolved": False, "reason": str(exc)[:200]}
        if not report["pins"]["resolved"]:
            report["status"] = "blocked"
            report["reason"] = "pins_unresolved: " + str(report["pins"]["reason"])
            report["warrant_step"] = {"status": "blocked", "reason": report["reason"]}
            for name in ("daily_bars", "snapshot_current", "keyed"):
                stores[name]["blocked"] = report["reason"]
            return

        # 2. listing: unreadable or older than the last trading day -> no bars, no snapshot records
        state, listing_reason = self._read_listing(today)
        expired: frozenset[str] = frozenset()
        keep_delisted: frozenset[str] = frozenset()
        registry: Mapping[str, Any] = {}
        if state is None:
            report["warrant_step"] = {"status": "blocked", "reason": listing_reason}
            stores["daily_bars"]["blocked"] = stores["snapshot_current"]["blocked"] = listing_reason
        else:
            registry = state.ordinary_share_registry
            expired = frozenset(s for s in state.expired_warrants(today, trading_days_between=self._tdb)
                                if _is_expired_warrant_candidate(s, registry))
            keep_delisted = frozenset(registry) - state.listed_symbols()
            report["warrant_step"] = {"status": "ok", "listing_date": state.last_success_date, "expired_warrants": len(expired)}

        # 3. plan everything first (counts and bytes), so the share limit is checked before any deletion
        bars_plan = self._plan_bars(today, pin_dates, pin_runs, expired, keep_delisted) if state is not None else None
        snap_plan = self._plan_snapshot(expired) if state is not None else None
        keyed_plan = self._plan_keyed(today, pin_dates)
        self._fill_plan_report(stores, bars_plan, snap_plan, keyed_plan)

        # 4. apply (real run only), store by store, each store refused alone when over the share limit
        for name in ("daily_bars", "snapshot_current", "keyed"):
            share = stores[name]["delete_share"]
            if real and not override and share > MAX_DELETE_SHARE:
                stores[name]["refused"] = (f"would delete {share:.1%} of the store in one run, over the stated limit "
                                           f"{MAX_DELETE_SHARE:.0%}; set {ALLOW_LARGE_ENV}=1 to override")
                if name == "daily_bars":
                    stores["lineage"]["refused"] = stores[name]["refused"]
                report["status"] = "partial" if report["status"] == "completed" else report["status"]
        if not real:
            return
        if bars_plan is not None and "refused" not in stores["daily_bars"]:
            self._apply_bars(bars_plan, stores, pin_dates, pin_runs, expired, keep_delisted, today)
        if snap_plan is not None and "refused" not in stores["snapshot_current"]:
            self._apply_snapshot(snap_plan, stores)
        if keyed_plan and "refused" not in stores["keyed"]:
            self._apply_keyed(keyed_plan, stores)

    def _read_listing(self, today: str) -> tuple[ListingState | None, str | None]:
        if self.listing_state_path is None:
            return None, "listing_unavailable: no listing state path"
        try:
            if not Path(self.listing_state_path).exists():
                return None, "listing_unavailable: listing_missing"
            state = ListingState.load(self.listing_state_path)
        except Exception as exc:  # noqa: BLE001
            return None, f"listing_unavailable: listing_unreadable: {type(exc).__name__}: {exc}"[:240]
        if not state.last_success_date:
            return None, "listing_unavailable: listing has no last_success_date"
        age = state.staleness_days(today, trading_days_between=self._tdb)
        if age is None or age > 1:
            return None, (f"listing_unavailable: last listing {state.last_success_date} is {age} trading days old; "
                          "it must be the last trading day's listing")
        return state, None

    # ----- daily bars (+ lineage) ------------------------------------------------------------------
    def _removal_rule(self, trade_date: str, *, past_retention: bool, pin_runs: frozenset[str], expired: frozenset[str],
                      keep_delisted: frozenset[str]) -> Callable[[Mapping[str, Any]], bool]:
        def should_remove(row: Mapping[str, Any]) -> bool:
            if row.get("lineage_ref") in pin_runs:
                return False  # pinned run: its bars stay even past retention or expired
            bare = bare_symbol(row["symbol"])
            if bare in expired:
                return True  # expired warrant (already guarded: never a 4 digit code or ordinary share)
            if past_retention:
                return bare not in keep_delisted  # delisted ordinary shares keep their history
            return False
        return should_remove

    def _plan_bars(self, today: str, pin_dates: frozenset[str], pin_runs: frozenset[str], expired: frozenset[str],
                   keep_delisted: frozenset[str]) -> dict[str, Any]:
        retention = self.bars.declaration.retention_days or DAILY_BARS_RETENTION_DAYS
        cutoff = (date.fromisoformat(today) - timedelta(days=retention)).isoformat()
        plan: dict[str, Any] = {"cutoff": cutoff, "dates": {}, "pinned_dates_kept": [], "total_rows": 0, "total_bytes": 0,
                                "past_retention_partitions": 0, "past_retention_bytes": 0, "retention_rows": 0,
                                "warrant_rows": 0, "warrant_bytes": 0, "lineage_total_bytes": 0, "lineage_days": 0}
        for day in self.bars.dates():
            manifest = self.bars.read_manifest(day) or {}
            plan["total_rows"] += int(manifest.get("row_count", 0))
            plan["total_bytes"] += int(manifest.get("bytes", 0))
        for day in self.bars.lineage_dates():
            plan["lineage_days"] += 1
            plan["lineage_total_bytes"] += self.bars._lineage_size(day)
        for day in self.bars.dates():
            past = day < cutoff
            manifest = self.bars.read_manifest(day) or {}
            if past:
                plan["past_retention_partitions"] += 1
                plan["past_retention_bytes"] += int(manifest.get("bytes", 0))
            if not past and not expired:
                continue
            if day in pin_dates:
                if past or expired:
                    plan["pinned_dates_kept"].append(day)
                continue
            rule = self._removal_rule(day, past_retention=past, pin_runs=pin_runs, expired=expired, keep_delisted=keep_delisted)
            res = self.bars.remove_rows(day, rule, dry_run=True)
            if res["removed"]:
                res["past_retention"] = past
                plan["dates"][day] = res
                # warrant share of the removal (rows that are expired warrants) vs retention
                plan["retention_rows"] += res["removed"] if past else 0
                plan["warrant_rows"] += 0 if past else res["removed"]
                plan["warrant_bytes"] += 0 if past else res["bytes_freed"]
        return plan

    def _apply_bars(self, plan: dict[str, Any], stores: dict[str, Any], pin_dates, pin_runs, expired, keep_delisted, today) -> None:
        bars, lineage = stores["daily_bars"], stores["lineage"]
        for day, planned in sorted(plan["dates"].items()):
            rule = self._removal_rule(day, past_retention=planned["past_retention"], pin_runs=pin_runs, expired=expired,
                                      keep_delisted=keep_delisted)
            res = self.bars.remove_rows(day, rule)
            time.sleep(SWEEP_YIELD_SECONDS)
            bars["deleted_rows"] += res["removed"]
            bars["deleted_bytes"] += res["bytes_freed"]
            lineage["deleted_bytes"] += res["lineage_bytes_freed"]
            if res["deleted_day"]:
                bars["deleted_partitions"] += 1
                lineage["deleted_partitions"] += 1
            else:
                bars["rewritten_partitions"] += 1
        orphan = self.bars.delete_orphan_lineage()
        lineage["deleted_bytes"] += orphan
        self.bars.sweep_interrupted()

    # ----- snapshot --------------------------------------------------------------------------------
    def _plan_snapshot(self, expired: frozenset[str]) -> dict[str, Any] | None:
        store = self.snapshot_store
        if store is None:
            return {"records": 0, "expired_symbols": [], "total_records": 0, "bytes": 0, "total_bytes": 0, "unavailable": "no_snapshot_store"}
        from .requirement_state import _FREQUENCY_SNAPSHOT_DATASETS

        stored: dict[str, int] = {}
        total = 0
        for dataset in sorted(set(_FREQUENCY_SNAPSHOT_DATASETS.values())):
            for symbol in store.live_symbols(dataset):
                total += 1
                if bare_symbol(symbol) in expired:
                    stored[symbol] = stored.get(symbol, 0) + 1
        stats = {}
        try:
            stats = store.snapshot_current_stats()
        except Exception:  # noqa: BLE001
            pass
        live = int(stats.get("live_bytes", 0) or 0)
        per_record = (live / total) if total else 0
        records = sum(stored.values())
        return {"records": records, "expired_symbols": sorted(stored), "total_records": total,
                "bytes": int(records * per_record), "total_bytes": live}

    def _apply_snapshot(self, plan: dict[str, Any], stores: dict[str, Any]) -> None:
        if plan.get("unavailable") or not plan["expired_symbols"]:
            return
        out = stores["snapshot_current"]
        removed: list[str] = []
        symbols = plan["expired_symbols"]
        for start in range(0, len(symbols), SNAPSHOT_BATCH):
            removed += self.snapshot_store.delete_symbols(symbols[start:start + SNAPSHOT_BATCH], reason="expired")
        out["deleted_symbols"] = len(removed)
        out["deleted_rows"] = plan["records"]
        out["deleted_bytes"] = plan["bytes"]

    # ----- keyed datasets ----------------------------------------------------------------------------
    def _plan_keyed(self, today: str, pin_dates: frozenset[str]) -> dict[str, Any]:
        plan: dict[str, Any] = {"datasets": {}, "partitions": 0, "past": 0, "bytes": 0, "total_bytes": 0, "pinned_kept": []}
        for name, decl in KEYED_DATASETS.items():
            store = KeyedDatasetStore(self.keyed_root, decl)
            parts = store.partitions()
            sizes = {p: int((store.read_manifest(p) or {}).get("bytes", 0)) for p in parts}
            plan["partitions"] += len(parts)
            plan["total_bytes"] += sum(sizes.values())
            if decl.retention_days is None:
                continue
            cutoff = (date.fromisoformat(today) - timedelta(days=decl.retention_days)).isoformat()
            doomed = []
            for part in parts:
                if len(part) == 10 and part[4] == "-" and part < cutoff:
                    if part in pin_dates:
                        plan["pinned_kept"].append(f"{name}:{part}")
                        continue
                    doomed.append(part)
            if doomed:
                plan["datasets"][name] = {"partitions": doomed, "bytes": sum(sizes[p] for p in doomed)}
                plan["past"] += len(doomed)
                plan["bytes"] += plan["datasets"][name]["bytes"]
        return plan

    def _apply_keyed(self, plan: dict[str, Any], stores: dict[str, Any]) -> None:
        out = stores["keyed"]
        for name, item in plan["datasets"].items():
            store = KeyedDatasetStore(self.keyed_root, KEYED_DATASETS[name])
            for part in item["partitions"]:
                res = store.delete_partition(part)
                if res["deleted"]:
                    out["deleted_partitions"] += 1
                    out["deleted_bytes"] += res["bytes_freed"]
                    out["deleted_rows"] += res["rows"]

    # ----- report ------------------------------------------------------------------------------------
    def _fill_plan_report(self, stores: dict[str, Any], bars: dict[str, Any] | None, snap: dict[str, Any] | None,
                          keyed: dict[str, Any]) -> None:
        if bars is not None:
            removed_rows = sum(d["removed"] for d in bars["dates"].values())
            removed_bytes = sum(d["bytes_freed"] for d in bars["dates"].values())
            full_days = sum(1 for d in bars["dates"].values() if d["deleted_day"])
            b = stores["daily_bars"]
            b.update(
                total_rows=bars["total_rows"], total_bytes=bars["total_bytes"], cutoff=bars["cutoff"],
                would_delete_rows=removed_rows, would_delete_bytes=removed_bytes,
                would_delete_partitions=full_days, would_rewrite_partitions=len(bars["dates"]) - full_days,
                past_retention_partitions=bars["past_retention_partitions"], past_retention_bytes=bars["past_retention_bytes"],
                expired_warrant_rows=bars["warrant_rows"], expired_warrant_bytes=bars["warrant_bytes"],
                pinned_dates_kept=len(bars["pinned_dates_kept"]), pinned_dates_kept_sample=bars["pinned_dates_kept"][:SAMPLE_LIMIT],
                delete_share=(removed_rows / bars["total_rows"]) if bars["total_rows"] else 0.0,
                sample_dates=sorted(bars["dates"])[:SAMPLE_LIMIT],
            )
            lin = stores["lineage"]
            lin.update(total_bytes=bars["lineage_total_bytes"], total_partitions=bars["lineage_days"],
                       would_delete_partitions=full_days,
                       would_delete_bytes=sum(d["lineage_bytes_freed"] for d in bars["dates"].values()),
                       delete_share=(full_days / bars["lineage_days"]) if bars["lineage_days"] else 0.0)
        if snap is not None:
            s = stores["snapshot_current"]
            s.update(total_records=snap["total_records"], total_bytes=snap["total_bytes"],
                     expired_warrant_symbols_stored=len(snap["expired_symbols"]),
                     would_delete_rows=snap["records"], would_delete_bytes=snap["bytes"],
                     delete_share=(snap["records"] / snap["total_records"]) if snap["total_records"] else 0.0)
            if snap.get("unavailable"):
                s["note"] = snap["unavailable"]
        k = stores["keyed"]
        k.update(total_partitions=keyed["partitions"], total_bytes=keyed["total_bytes"],
                 would_delete_partitions=keyed["past"], would_delete_bytes=keyed["bytes"],
                 pinned_dates_kept=len(keyed["pinned_kept"]), pinned_dates_kept_sample=keyed["pinned_kept"][:SAMPLE_LIMIT],
                 datasets={n: {"partitions": len(v["partitions"]), "bytes": v["bytes"]} for n, v in keyed["datasets"].items()},
                 delete_share=(keyed["past"] / keyed["partitions"]) if keyed["partitions"] else 0.0)


def _empty_store() -> dict[str, Any]:
    return {"deleted_rows": 0, "deleted_bytes": 0, "deleted_partitions": 0, "rewritten_partitions": 0,
            "would_delete_rows": 0, "would_delete_bytes": 0, "would_delete_partitions": 0, "delete_share": 0.0}


def count_expired_warrant_symbols(state: ListingState, symbols: Iterable[str], today: str,
                                  trading_days_between: Callable[[date, date], int] | None = None) -> int:
    """Stored symbols that are expired warrants by the listing (used by the reproduce step of the evidence)."""
    expired = state.expired_warrants(today, trading_days_between=trading_days_between)
    return sum(1 for s in set(symbols) if bare_symbol(s) in expired and _is_expired_warrant_candidate(s, state.ordinary_share_registry))
