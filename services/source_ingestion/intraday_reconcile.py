"""Daily reconciliation of ``tw_price_1m`` against official ``tw_price_daily``
(SD_INTRADAY_MINUTE_BARS §5, DEV_PLAN §4 K3).

Per ``(symbol, session_date)``: aggregate the stored regular-session minute bars to
OHLCV and compare with the official daily row.

* ``pass``  -> a reconciliation record is written (real ``reconciled_at``, computed
  ``bar_availability_ts``). Only :func:`read_reconciled_rows` exposes such a day.
* ``fail`` / ``no_official_row`` / ``official_row_conflict`` -> the store quarantines
  the symbol-day (``IntradayStore.quarantine_symbol_day``), which hides it from
  ``IntradayStore.read_rows`` as well.
* ``pending_official`` (the official partition for that date is absent or not yet
  available) and ``minute_missing`` (official row, no minute data) change nothing.

Never done here: editing/interpolating/clipping minute bars, writing ``tw_price_daily``,
widening tolerances, back-dating availability. A quarantined day returns to a loader
only through :func:`readmit_after_refetch` (a later re-fetch that itself passes) or an
operator :func:`record_waiver` (``waived=True``, never replication evidence). Minute
shards stay immutable: readmitted rows live in a sidecar, the original shard is kept.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .intraday_store import (
    STATUS_ACCEPTED,
    STATUS_QUARANTINED,
    IntradayStore,
    IntradayStoreError,
    _atomic_write,
    _canonical,
    _gz_bytes,
    _iso,
    _read_gz_lines,
    _safe_date,
    _safe_symbol,
    _sha256,
)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "intraday_reconciliation.json"
RECONCILED_DIR = Path("normalized") / "tw_price_1m_reconciled"
RECORD_SCHEMA = "tw_price_1m_reconciliation.v1"
TAIPEI = timezone(timedelta(hours=8))

OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_NO_OFFICIAL = "no_official_row"
OUTCOME_CONFLICT = "official_row_conflict"
OUTCOME_PENDING = "pending_official"
OUTCOME_MINUTE_MISSING = "minute_missing"
OUTCOME_ALREADY_QUARANTINED = "already_quarantined"
OUTCOME_NOT_APPLICABLE = "not_applicable"
QUARANTINE_OUTCOMES = frozenset({OUTCOME_FAIL, OUTCOME_NO_OFFICIAL, OUTCOME_CONFLICT})
# Outcomes that count in the pass-rate denominator (a decided day).
RATE_OUTCOMES = QUARANTINE_OUTCOMES | {OUTCOME_PASS, OUTCOME_ALREADY_QUARANTINED}

PRICE_FIELDS = ("open", "high", "low", "close")


class ReconcileError(RuntimeError):
    pass


# ---------------------------------------------------------------- config
@dataclass(frozen=True)
class ReconcileConfig:
    price_tolerance_pct: Decimal
    price_tolerance_ticks: int
    volume_tolerance_pct: Decimal
    min_coverage: Decimal
    max_gap_minutes: int
    expected_bars: int
    session_open: tuple[int, int]
    session_last: tuple[int, int]
    volume_unit_factor: Decimal
    volume_unit_status: str
    tick_bands: tuple[tuple[Decimal, Decimal], ...]
    volume_component_categories: tuple[str, ...]
    pass_rate_review_below: Decimal
    pass_rate_window_days: int
    config_hash: str
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def tick_size(self, price: Decimal) -> Decimal:
        tick = self.tick_bands[0][1]
        for low, size in self.tick_bands:
            if price >= low:
                tick = size
        return tick

    def tolerances(self) -> dict[str, Any]:
        return {
            "price_pct": str(self.price_tolerance_pct), "price_ticks": self.price_tolerance_ticks,
            "volume_pct": str(self.volume_tolerance_pct), "min_coverage": str(self.min_coverage),
            "max_gap_minutes": self.max_gap_minutes, "volume_unit_factor": str(self.volume_unit_factor),
            "volume_unit_status": self.volume_unit_status,
        }


def _hm(text: str) -> tuple[int, int]:
    hh, mm = str(text).split(":")
    return int(hh), int(mm)


def load_config(path: str | Path | None = None) -> ReconcileConfig:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    if raw.get("auto_widen_tolerance"):
        raise ReconcileError("auto_widen_tolerance must stay false (SD §5)")
    bands = tuple(sorted((Decimal(b["min_price"]), Decimal(b["tick"])) for b in raw["tick_size_bands"]))
    cfg = ReconcileConfig(
        price_tolerance_pct=Decimal(raw["price_tolerance_pct"]),
        price_tolerance_ticks=int(raw["price_tolerance_ticks"]),
        volume_tolerance_pct=Decimal(raw["volume_tolerance_pct"]),
        min_coverage=Decimal(raw["min_coverage"]),
        max_gap_minutes=int(raw["max_consecutive_missing_minutes"]),
        expected_bars=int(raw["expected_bars"]),
        session_open=_hm(raw["session_open"]),
        session_last=_hm(raw["session_last_bar_start"]),
        volume_unit_factor=Decimal(raw["volume_unit_factor"]),
        volume_unit_status=str(raw.get("volume_unit_status") or ""),
        tick_bands=bands,
        volume_component_categories=tuple(raw.get("volume_component_categories") or ()),
        pass_rate_review_below=Decimal(raw["pass_rate_operator_review_below"]),
        pass_rate_window_days=int(raw["pass_rate_window_trading_days"]),
        config_hash=_sha256(_canonical(raw)),
        raw=raw,
    )
    span = (cfg.session_last[0] * 60 + cfg.session_last[1]) - (cfg.session_open[0] * 60 + cfg.session_open[1]) + 1
    if span != cfg.expected_bars:
        raise ReconcileError(f"expected_bars {cfg.expected_bars} != session minutes {span}")
    return cfg


# ---------------------------------------------------------------- time helpers
def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= 10:
        text = f"{text}T14:30:00+08:00"  # date-only availability = regular daily publish time
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=TAIPEI)).astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return out if out.is_finite() else None


# ---------------------------------------------------------------- official daily rows
SHARES_PER_LOT = Decimal(1000)


def _official_volume_shares(body: Mapping[str, Any]) -> Decimal | None:
    """Official volume in shares. TPEx monthly history only publishes ``volume_lots``
    (成交張數, 1 lot = 1000 shares) with ``volume`` null; TWSE publishes shares.
    Both null means a true no-trade quote."""
    shares = _dec(body.get("volume"))
    if shares is not None:
        return shares
    lots = _dec(body.get("volume_lots"))
    return None if lots is None else lots * SHARES_PER_LOT


@dataclass(frozen=True)
class VolumeComponent:
    """One official non-regular volume category (odd lot / after-hours fixed / block ...) of a
    symbol-day, bound to its own stock/date/market/unit and source line hash."""
    symbol: str
    date: str
    market: str | None
    category: str
    shares: Decimal | None  # normalized to shares; None = malformed/unsupported unit
    unit: str
    available_at: datetime | None
    ref: Mapping[str, Any]


def _component_from_body(body: Mapping[str, Any], ref: Mapping[str, Any], default_avail: Any) -> VolumeComponent:
    unit = str(body.get("unit") or "").lower()
    raw = _dec(body.get("volume"))
    shares = raw if unit == "shares" else raw * SHARES_PER_LOT if unit == "lots" and raw is not None else None
    market = body.get("market")
    return VolumeComponent(
        str(body["symbol"]).upper(), str(body["date"])[:10], None if market is None else str(market).upper(),
        str(body.get("category") or ""), shares, unit, _parse_ts(body.get("available_time") or default_avail), ref)


@dataclass(frozen=True)
class OfficialRow:
    symbol: str
    date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    available_at: datetime | None
    ref: Mapping[str, Any]
    market: str | None = None
    components: tuple["VolumeComponent", ...] = ()

    def ohlcv(self) -> tuple[Decimal, ...]:
        return (self.open, self.high, self.low, self.close, self.volume)


class OfficialDaily:
    """Read-only view of ``normalized/tw_price_daily`` (date-partitioned jsonl)."""

    def __init__(self, rows: Iterable[OfficialRow], dates_present: Iterable[str],
                 components: Iterable[VolumeComponent] = ()) -> None:
        self._rows: dict[tuple[str, str], list[OfficialRow]] = {}
        self._components: dict[tuple[str, str], list[VolumeComponent]] = {}
        self._unattributable: list[VolumeComponent] = []
        for comp in components:
            if not comp.symbol:  # unparseable line: cannot be attributed to any stock
                self._unattributable.append(comp)
                continue
            self._components.setdefault((comp.symbol, comp.date), []).append(comp)
        for row in rows:
            self._rows.setdefault((row.symbol, row.date), []).append(row)
        self.dates_present = frozenset(dates_present)

    @classmethod
    def from_snapshot(cls, root: str | Path) -> "OfficialDaily":
        base = Path(root)
        if (base / "normalized" / "tw_price_daily").is_dir():
            base = base / "normalized" / "tw_price_daily"
        if not base.is_dir():
            raise ReconcileError(f"official tw_price_daily snapshot missing: {base}")
        rows: list[OfficialRow] = []
        dates: set[str] = set()
        for path in sorted(p for p in base.rglob("*.jsonl") if p.is_file()):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    meta = rec.get("metadata") or {}
                    body = meta.get("body") or meta.get("normalized_row") or rec
                    body = json.loads(body) if isinstance(body, str) else dict(body)
                    sym, day = str(body["symbol"]).upper(), str(body["date"])[:10]
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                dates.add(day)
                vals = [_dec(body.get(k)) for k in PRICE_FIELDS]
                vals.append(_official_volume_shares(body))
                if any(v is None for v in vals):
                    continue  # all-null no-trade quote: not an official bar
                avail = _parse_ts(body.get("available_time") or meta.get("available_time"))
                mkt = body.get("market")
                rows.append(OfficialRow(
                    sym, day, *vals, avail,
                    {"path": path.relative_to(base).as_posix(), "line": number,
                     "line_sha256": _sha256(line), "source_id": rec.get("source_id")},
                    None if mkt is None else str(mkt).upper(),
                ))
        return cls(rows, dates, cls._load_components(Path(root)))

    @staticmethod
    def _load_components(root: Path) -> list[VolumeComponent]:
        """Optional ``normalized/tw_price_daily_volume_components`` jsonl: one line per official
        category {symbol,date,market,category,volume,unit(shares|lots),available_time}."""
        base = root / "normalized" / "tw_price_daily_volume_components"
        out: list[VolumeComponent] = []
        if not base.is_dir():
            return out
        for path in sorted(p for p in base.rglob("*.jsonl") if p.is_file()):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                ref = {"path": path.relative_to(base).as_posix(), "line": number, "line_sha256": _sha256(line)}
                try:
                    rec = json.loads(line)
                    meta = rec.get("metadata") or {}
                    body = meta.get("body") or rec
                    body = json.loads(body) if isinstance(body, str) else dict(body)
                    ref["source_id"] = rec.get("source_id")
                    out.append(_component_from_body(body, ref, meta.get("available_time")))
                except (ValueError, KeyError, TypeError, AttributeError):
                    # Unparseable line: keep an unusable marker so the basis is unavailable, never zero.
                    out.append(VolumeComponent("", "", None, "", None, "", None, ref))
        return out

    def components_for(self, symbol: str, day: str) -> list[VolumeComponent]:
        found = list(self._components.get((symbol.upper(), day), ()))
        # An unreadable component line could belong to this stock: its basis is then unavailable.
        return found + self._unattributable

    def rows_for(self, symbol: str, day: str) -> list[OfficialRow]:
        return list(self._rows.get((symbol.upper(), day), ()))


# ---------------------------------------------------------------- result
@dataclass
class ReconciliationResult:
    symbol: str
    session_date: str
    outcome: str
    violations: list[dict[str, Any]] = field(default_factory=list)
    observed: dict[str, Any] = field(default_factory=dict)
    official: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    reconciled_at: str | None = None
    bar_availability_ts: str | None = None
    official_available_at: str | None = None
    unchanged: bool = False
    source_refs: dict[str, Any] = field(default_factory=dict)
    tolerances: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- pure evaluation
def _minute_offset(bar_start: str, cfg: ReconcileConfig) -> tuple[int | None, bool]:
    dt = datetime.fromisoformat(str(bar_start)).astimezone(TAIPEI)
    minute = dt.hour * 60 + dt.minute
    start = cfg.session_open[0] * 60 + cfg.session_open[1]
    last = cfg.session_last[0] * 60 + cfg.session_last[1]
    if dt.second or dt.microsecond or not start <= minute <= last:
        return None, False
    return minute - start, True


def aggregate_minutes(rows: Sequence[Mapping[str, Any]], cfg: ReconcileConfig) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: str(r["bar_start"]))
    offsets: list[int] = []
    outside: list[str] = []
    for row in ordered:
        offset, ok = _minute_offset(row["bar_start"], cfg)
        (offsets if ok else outside).append(offset if ok else str(row["bar_start"]))  # type: ignore[arg-type]
    present = set(offsets)
    longest = run = 0
    for i in range(cfg.expected_bars):
        run = 0 if i in present else run + 1
        longest = max(longest, run)
    regular = [r for r in ordered if _minute_offset(r["bar_start"], cfg)[1]]
    agg: dict[str, Any] = {
        "bar_count": len(present), "expected_bars": cfg.expected_bars,
        "coverage": (Decimal(len(present)) / Decimal(cfg.expected_bars)),
        "longest_missing_run": longest, "outside_session": outside,
    }
    if regular:
        values = [[_dec(r.get(k)) for k in (*PRICE_FIELDS, "volume")] for r in regular]
        if any(v is None for row in values for v in row):
            agg["malformed"] = True
        else:
            agg.update(
                open=values[0][0], close=values[-1][3],
                high=max(v[1] for v in values), low=min(v[2] for v in values),
                raw_volume=sum((v[4] for v in values), Decimal(0)),
            )
            agg["volume"] = agg["raw_volume"] * cfg.volume_unit_factor
    return agg


def _fmt(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value


def volume_basis(official: OfficialRow, components: Sequence[VolumeComponent], cfg: ReconcileConfig,
                 now: datetime) -> dict[str, Any]:
    """Comparable regular-session official volume (shares), evidence-bound.

    * no component evidence at all -> legacy basis: the full official total (raw volume kept).
    * any component evidence -> every configured category must be present exactly once, with the
      same stock/date/market, a supported unit, a non-negative amount and a known availability;
      otherwise ``status='unavailable'`` (never infer a missing category as zero).
    ``status='pending'`` when a component is not yet available at ``now``.
    """
    out: dict[str, Any] = {"official_total_volume": str(official.volume), "components": [],
                           "basis": "official_total", "status": "ok", "volume": official.volume}
    if not components:
        return out
    need = set(cfg.volume_component_categories)
    problems: list[str] = []
    seen: dict[str, VolumeComponent] = {}
    for c in components:
        out["components"].append({"category": c.category, "shares": None if c.shares is None else str(c.shares),
                                  "unit": c.unit, "market": c.market, "ref": dict(c.ref),
                                  "available_at": None if c.available_at is None else _iso(c.available_at)})
        if c.symbol != official.symbol or c.date != official.date:
            problems.append("component_stock_or_date_mismatch")
        if c.market is None or official.market is None or c.market != official.market:
            problems.append("component_market_unbound_or_mismatch")
        if c.shares is None:
            problems.append("component_unit_or_amount_unsupported")
        elif c.shares < 0:
            problems.append("component_negative")
        if c.category not in need:
            problems.append("component_category_unsupported")
        elif c.category in seen:
            problems.append("component_category_duplicate")
        seen[c.category] = c
    problems += [f"component_category_missing:{k}" for k in sorted(need - set(seen))]
    if not problems and any(c.available_at is None for c in components):
        problems.append("component_availability_unknown")
    if problems:
        out.update(status="unavailable", reason=",".join(sorted(set(problems))), volume=None)
        return out
    if any(c.available_at > now for c in components):  # type: ignore[operator]
        out.update(status="pending", reason="official_volume_component_not_yet_available", volume=None)
        return out
    total = sum((c.shares for c in components), Decimal(0))  # type: ignore[misc]
    comparable = official.volume - total
    if comparable < 0:
        out.update(status="unavailable", reason="components_exceed_official_total", volume=None)
        return out
    out.update(basis="official_total_minus_non_regular_categories", volume=comparable,
               non_regular_total=str(total), comparable_volume=str(comparable),
               component_available_at=_iso(max(c.available_at for c in components)))  # type: ignore[type-var]
    return out



def _basis_ref(basis: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe copy for source_refs (drops the Decimal working value)."""
    return {k: v for k, v in basis.items() if k != "volume"}


def evaluate_aggregate(agg: Mapping[str, Any], official: OfficialRow, cfg: ReconcileConfig,
                       basis: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return the list of violations (empty = pass). Pure; no I/O."""
    out: list[dict[str, Any]] = []
    if agg.get("malformed") or "open" not in agg:
        return [{"field": "bars", "observed": None, "official": None, "tolerance": None,
                 "reason": "no_usable_regular_session_bars"}]
    if agg["outside_session"]:
        out.append({"field": "session_shape", "observed": list(agg["outside_session"])[:20],
                    "official": None, "tolerance": "regular 09:00-13:30 only",
                    "reason": "bars_outside_regular_session_in_store"})
    tick = cfg.tick_size(official.close)
    for name in PRICE_FIELDS:
        obs, off = agg[name], getattr(official, name)
        tol = max(tick * cfg.price_tolerance_ticks, abs(off) * cfg.price_tolerance_pct)
        if abs(obs - off) > tol:
            out.append({"field": name, "observed": str(obs), "official": str(off),
                        "tolerance": f"max({cfg.price_tolerance_ticks} tick={tick}, {cfg.price_tolerance_pct * 100}%)={tol}",
                        "reason": "price_outside_tolerance"})
    basis = basis or {"status": "ok", "volume": official.volume, "basis": "official_total"}
    if basis["status"] != "ok":
        out.append({"field": "volume", "observed": str(agg["volume"]), "official": str(official.volume),
                    "tolerance": None, "reason": "volume_basis_unavailable", "basis_reason": basis.get("reason")})
        off_v = None
    else:
        off_v = basis["volume"]
    obs_v = agg["volume"]
    if off_v is None:
        vol_bad = False
        rel: Decimal | None = None
    elif off_v == 0:
        vol_bad = obs_v != 0
        rel: Decimal | None = None
    else:
        rel = abs(obs_v - off_v) / off_v
        vol_bad = rel > cfg.volume_tolerance_pct
    if vol_bad:
        out.append({"field": "volume", "observed": str(obs_v), "official": str(off_v),
                    "basis": basis["basis"], "official_total_volume": str(official.volume),
                    "tolerance": f"{cfg.volume_tolerance_pct * 100}% (unit_factor={cfg.volume_unit_factor}, "
                                 f"{cfg.volume_unit_status})",
                    "relative_error": None if rel is None else str(rel),
                    "reason": "volume_outside_tolerance"})
    if agg["coverage"] < cfg.min_coverage:
        out.append({"field": "coverage", "observed": f"{agg['bar_count']}/{agg['expected_bars']}",
                    "official": None, "tolerance": f">={cfg.min_coverage * 100}%", "reason": "coverage_below_minimum"})
    if agg["longest_missing_run"] > cfg.max_gap_minutes:
        out.append({"field": "gap", "observed": agg["longest_missing_run"], "official": None,
                    "tolerance": f"<={cfg.max_gap_minutes} consecutive minutes", "reason": "gap_too_long"})
    return out


# ---------------------------------------------------------------- sidecar records
def record_path(store: IntradayStore, symbol: str, day: str) -> Path:
    return store.root / RECONCILED_DIR / _safe_date(day) / f"{_safe_symbol(symbol)}.json"


def _readmit_rows_path(store: IntradayStore, symbol: str, day: str) -> Path:
    return store.root / RECONCILED_DIR / _safe_date(day) / f"{_safe_symbol(symbol)}.readmitted.jsonl.gz"


def read_record(store: IntradayStore, symbol: str, day: str) -> dict[str, Any] | None:
    path = record_path(store, symbol, day)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IntradayStoreError(f"unreadable reconciliation record: {path}") from exc


def _file_sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _availability(rows: Sequence[Mapping[str, Any]], fetched: datetime, reconciled: datetime) -> str:
    """max(bar_end, fetched_at, reconciled_at): the time the day was really obtainable."""
    ends = [t for t in (_parse_ts(r.get("bar_end")) for r in rows) if t]
    return _iso(max([*ends, fetched, reconciled]))


def _fetched_at(rows: Sequence[Mapping[str, Any]], manifest_entry: Mapping[str, Any]) -> datetime | None:
    stamps = [t for t in (_parse_ts(r.get("fetched_at")) for r in rows) if t]
    return max(stamps) if stamps else _parse_ts(manifest_entry.get("stored_at"))


def _recovery_state(store: IntradayStore, symbol: str, day: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(sidecar, quarantine record) iff the sidecar is bound to the *current, resolved*
    quarantine generation. Unresolved, reopened or interrupted-resolution -> None (fail closed)."""
    rec = read_record(store, symbol, day)
    if not rec or rec.get("kind") not in ("readmitted", "waived"):
        return None
    qpath = store.quarantine_path(symbol, day)
    try:
        qrec = json.loads(qpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    resolved = qrec.get("resolved_at")
    if not resolved or rec.get("quarantine_resolved_at") != resolved:
        return None
    if rec.get("quarantine_quarantined_at") != qrec.get("quarantined_at"):
        return None
    if bool(qrec.get("waived")) != (rec.get("kind") == "waived"):
        return None
    return rec, qrec


def _bind_recovery(store: IntradayStore, symbol: str, day: str, qrec: Mapping[str, Any]) -> None:
    """Bind the sidecar to the quarantine generation that has just been resolved."""
    path = record_path(store, symbol, day)
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["quarantine_resolved_at"] = qrec.get("resolved_at")
    rec["quarantine_quarantined_at"] = qrec.get("quarantined_at")
    _atomic_write(path, (_canonical(rec) + "\n").encode("utf-8"))


def _pending(store: IntradayStore, res: ReconciliationResult, write: bool, reason: str,
             *, drop_record: bool) -> ReconciliationResult:
    """Pending outcome; a write run withdraws any earlier pass so it cannot stay loader-visible."""
    res.outcome, res.reason = OUTCOME_PENDING, reason
    if write and drop_record:
        stale = record_path(store, res.symbol, res.session_date)
        if stale.exists():
            rec = read_record(store, res.symbol, res.session_date)
            if rec and rec.get("kind") == "readmitted":
                # The quarantine generation is already resolved, so a refetch can never
                # re-run. Keep the bound sidecar but withhold it: the loader stays closed
                # and the next reconcile with official data present can re-pass it.
                if not rec.get("withheld"):
                    rec["withheld"] = True
                    rec["withheld_reason"] = reason
                    _atomic_write(stale, (_canonical(rec) + "\n").encode("utf-8"))
            else:
                stale.unlink()
    return res


# ---------------------------------------------------------------- reconcile
def reconcile_symbol_day(
    store: IntradayStore,
    official: OfficialDaily,
    symbol: str,
    session_date: str,
    cfg: ReconcileConfig,
    *,
    clock: Callable[[], datetime] = _utc_now,
    write: bool = True,
) -> ReconciliationResult:
    """Reconcile one symbol-day. ``write=False`` is the read-only (report) mode."""
    sym, day = _safe_symbol(symbol), _safe_date(session_date)
    res = ReconciliationResult(sym, day, OUTCOME_NOT_APPLICABLE, tolerances=cfg.tolerances())
    manifest = store.read_manifest(day) or {}
    entry = (manifest.get("symbols") or {}).get(sym)
    status = None if entry is None else str(entry.get("status"))
    off_rows = official.rows_for(sym, day)
    distinct = {r.ohlcv() for r in off_rows}

    recovered: tuple[dict[str, Any], dict[str, Any]] | None = None
    if status == STATUS_QUARANTINED:
        recovered = _recovery_state(store, sym, day)
        if (recovered is None or recovered[0].get("kind") != "readmitted"
                or _file_sha(_readmit_rows_path(store, sym, day)) != recovered[0].get("shard_sha256")):
            res.outcome, res.reason = OUTCOME_ALREADY_QUARANTINED, str((entry or {}).get("reason") or "")
            return res
    elif status != STATUS_ACCEPTED:
        if off_rows:
            res.outcome, res.reason = OUTCOME_MINUTE_MISSING, f"minute_status_{status or 'absent'}"
        return res

    now = clock()
    if not off_rows:
        if day not in official.dates_present:
            return _pending(store, res, write, "official_partition_not_available", drop_record=True)
        res.outcome, res.reason = OUTCOME_NO_OFFICIAL, "no_official_row_for_symbol_day"
        return _quarantine(store, res, write, now)
    if len(distinct) > 1:
        res.outcome, res.reason = OUTCOME_CONFLICT, "conflicting_official_rows"
        res.official = {"rows": [[str(v) for v in o.ohlcv()] for o in off_rows]}
        res.source_refs = {"official": [dict(o.ref) for o in off_rows]}
        return _quarantine(store, res, write, now)

    row = off_rows[0]
    res.official_available_at = None if row.available_at is None else _iso(row.available_at)
    res.official = {k: str(getattr(row, k)) for k in (*PRICE_FIELDS, "volume")}
    if row.available_at is None or row.available_at > now:
        return _pending(store, res, write, "official_row_not_yet_available", drop_record=True)
    basis = volume_basis(row, official.components_for(sym, day), cfg, now)
    if basis["status"] == "pending":
        return _pending(store, res, write, basis["reason"], drop_record=True)

    if recovered is not None:
        rec0 = recovered[0]
        kind = "readmitted"
        rows = _read_gz_lines(_readmit_rows_path(store, sym, day))
        shard_hash, content_hash = rec0.get("shard_sha256"), rec0.get("content_hash")
        fetched = _parse_ts(rec0.get("fetched_at"))
        minute_ref = {"readmitted_rows_sha256": shard_hash, "row_count": len(rows)}
        extra = {k: rec0.get(k) for k in ("quarantine_resolved_at", "quarantine_quarantined_at")}
    else:
        kind = "reconciled"
        rows = store.read_rows(sym, day)
        shard_hash, content_hash = entry.get("shard_sha256"), entry.get("content_hash")
        fetched = _fetched_at(rows, entry)
        minute_ref = {"shard_sha256": shard_hash, "content_hash": content_hash,
                      "raw_response_hash": entry.get("raw_response_hash"), "row_count": entry.get("row_count")}
        extra = None
    agg = aggregate_minutes(rows, cfg)
    res.observed = {k: _fmt(agg.get(k)) for k in (*PRICE_FIELDS, "volume", "raw_volume", "bar_count",
                                                  "coverage", "longest_missing_run")}
    res.source_refs = {"official": dict(row.ref), "minute": minute_ref, "volume_basis": _basis_ref(basis)}
    res.violations = evaluate_aggregate(agg, row, cfg, basis)
    if res.violations:
        res.outcome, res.reason = OUTCOME_FAIL, ",".join(sorted({v["reason"] for v in res.violations}))
        return _quarantine(store, res, write, now)

    if fetched is None:
        return _pending(store, res, write, "fetched_at_unknown", drop_record=True)
    prior = read_record(store, sym, day)
    if (prior and not prior.get("withheld") and prior.get("kind") == kind and prior.get("shard_sha256") == shard_hash
            and prior.get("official_row_hash") == row.ref.get("line_sha256")
            and (prior.get("source_refs") or {}).get("volume_basis") == json.loads(_canonical(_basis_ref(basis)))
            and prior.get("config_hash") == cfg.config_hash):
        res.outcome, res.unchanged = OUTCOME_PASS, True
        res.reconciled_at, res.bar_availability_ts = prior["reconciled_at"], prior["bar_availability_ts"]
        return res
    reconciled = max(now, fetched, row.available_at,
                     *([_parse_ts(basis["component_available_at"])] if "component_available_at" in basis else []))
    res.outcome = OUTCOME_PASS
    res.reconciled_at = _iso(reconciled)
    res.bar_availability_ts = _availability(rows, fetched, reconciled)
    if write:
        _write_record(store, res, kind=kind, shard_sha256=shard_hash, content_hash=content_hash,
                      row_count=len(rows), official_row_hash=row.ref.get("line_sha256"), cfg=cfg,
                      fetched=fetched, extra=extra)
    return res


def _write_record(store: IntradayStore, res: ReconciliationResult, *, kind: str, shard_sha256: Any,
                  content_hash: Any, row_count: int, official_row_hash: Any, cfg: ReconcileConfig,
                  fetched: datetime | None, extra: Mapping[str, Any] | None = None) -> None:
    record = {
        "schema_version": RECORD_SCHEMA, "kind": kind, "symbol": res.symbol, "session_date": res.session_date,
        "outcome": res.outcome, "reconciled_at": res.reconciled_at, "bar_availability_ts": res.bar_availability_ts,
        "fetched_at": None if fetched is None else _iso(fetched),
        "official_available_at": res.official_available_at, "shard_sha256": shard_sha256,
        "content_hash": content_hash, "row_count": row_count, "official_row_hash": official_row_hash,
        "config_hash": cfg.config_hash, "tolerances": res.tolerances, "observed": res.observed,
        "official": res.official, "source_refs": res.source_refs,
        "waived": kind == "waived", "replication_eligible": kind != "waived", **dict(extra or {}),
    }
    _atomic_write(record_path(store, res.symbol, res.session_date), (_canonical(record) + "\n").encode("utf-8"))


def _quarantine(store: IntradayStore, res: ReconciliationResult, write: bool, now: datetime) -> ReconciliationResult:
    res.reconciled_at = None
    if write:
        store.quarantine_symbol_day(
            res.symbol, res.session_date, reason=f"reconciliation_{res.outcome}",
            detail={"outcome": res.outcome, "reason": res.reason, "violations": res.violations,
                    "observed": res.observed, "official": res.official, "tolerances": res.tolerances,
                    "source_refs": res.source_refs, "checked_at": _iso(now)},
        )
        stale = record_path(store, res.symbol, res.session_date)
        if stale.exists():
            stale.unlink()  # a day that now fails must not stay visible through an old pass record
    return res


def reconcile_day(
    store: IntradayStore, official: OfficialDaily, session_date: str, cfg: ReconcileConfig, *,
    symbols: Sequence[str] | None = None, clock: Callable[[], datetime] = _utc_now, write: bool = True,
    snapshot_store: Any | None = None, projection_report: dict[str, Any] | None = None,
) -> list[ReconciliationResult]:
    """Reconcile one session.

    With ``snapshot_store`` (a ``LatestMarketSnapshotStore``) and ``write=True``, the run ends by
    projecting this run's ``pass`` days via ``project_reconciled_minute_snapshots`` (SD §9 K4);
    the projection summary is copied into ``projection_report`` when given.
    """
    day = _safe_date(session_date)
    manifest = store.read_manifest(day) or {"symbols": {}}
    names = sorted(symbols if symbols is not None else manifest["symbols"])
    results = [reconcile_symbol_day(store, official, s, day, cfg, clock=clock, write=write) for s in names]
    if snapshot_store is not None and write:
        from .intraday_snapshot_projection import project_reconciled_minute_snapshots  # lazy: it imports this module

        report = project_reconciled_minute_snapshots(snapshot_store, results, intraday_store=store)
        if projection_report is not None:
            projection_report.update(report)
    return results


# ---------------------------------------------------------------- recovery paths
def readmit_after_refetch(
    store: IntradayStore, official: OfficialDaily, symbol: str, session_date: str,
    rows: Sequence[Mapping[str, Any]], cfg: ReconcileConfig, *,
    raw_envelope: Mapping[str, Any] | None = None, clock: Callable[[], datetime] = _utc_now,
) -> ReconciliationResult:
    """Leave quarantine only through a *later* re-fetch that itself reconciles to pass.

    The quarantined shard is untouched; the passing rows are kept in a sidecar and the
    quarantine record is resolved. A refetch that still fails changes nothing.
    """
    sym, day = _safe_symbol(symbol), _safe_date(session_date)
    qpath = store.quarantine_path(sym, day)
    if store.symbol_day_status(sym, day) != STATUS_QUARANTINED or not qpath.exists():
        raise ReconcileError(f"{sym} {day} is not quarantined")
    qrec = json.loads(qpath.read_text(encoding="utf-8"))
    if qrec.get("resolved_at"):
        raise ReconcileError(f"{sym} {day} quarantine already resolved")
    now = clock()
    quarantined_at = _parse_ts(qrec.get("quarantined_at"))
    fetched = _fetched_at(rows, {})
    res = ReconciliationResult(sym, day, OUTCOME_NOT_APPLICABLE, tolerances=cfg.tolerances())
    if fetched is None or (quarantined_at and fetched <= quarantined_at) or fetched > now:
        res.outcome, res.reason = OUTCOME_FAIL, "refetch_not_after_quarantine"
        return res
    clean: list[dict[str, Any]] = []
    for raw in rows:
        r = dict(raw)
        if str(r.get("symbol", "")).upper() != sym or r.get("session_date") != day:
            raise ReconcileError(f"row outside symbol-day {sym} {day}")
        clean.append(r)
    off_rows = official.rows_for(sym, day)
    distinct = {r.ohlcv() for r in off_rows}
    if len(distinct) != 1:
        res.outcome = OUTCOME_CONFLICT if distinct else OUTCOME_NO_OFFICIAL
        res.reason = "official_row_missing_or_conflicting"
        return res
    off = off_rows[0]
    if off.available_at is None or off.available_at > now:
        res.outcome, res.reason = OUTCOME_PENDING, "official_row_not_yet_available"
        return res
    agg = aggregate_minutes(clean, cfg)
    if len({r["bar_start"] for r in clean}) != len(clean):
        res.outcome, res.reason = OUTCOME_FAIL, "duplicate_bar_start_in_refetch"
        return res
    basis = volume_basis(off, official.components_for(sym, day), cfg, now)
    if basis["status"] == "pending":
        res.outcome, res.reason = OUTCOME_PENDING, basis["reason"]
        return res
    res.violations = evaluate_aggregate(agg, off, cfg, basis)
    res.observed = {k: _fmt(agg.get(k)) for k in (*PRICE_FIELDS, "volume", "bar_count")}
    res.official = {k: str(getattr(off, k)) for k in (*PRICE_FIELDS, "volume")}
    if res.violations:
        res.outcome, res.reason = OUTCOME_FAIL, ",".join(sorted({v["reason"] for v in res.violations}))
        return res
    ordered = sorted(clean, key=lambda r: r["bar_start"])
    if raw_envelope is not None:
        digest = str(raw_envelope.get("response_content_hash") or _sha256(_canonical(raw_envelope)))[:16]
        store._write_raw(sym, day, raw_envelope, path=store.raw_path(sym, day).with_name(f"{sym}.raw.refetch-{digest}.jsonl.gz"))
    payload = _gz_bytes(_canonical(r) for r in ordered)
    _atomic_write(_readmit_rows_path(store, sym, day), payload)
    reconciled = max(now, fetched, off.available_at,
                     *([_parse_ts(basis["component_available_at"])] if "component_available_at" in basis else []))
    res.outcome, res.reconciled_at = OUTCOME_PASS, _iso(reconciled)
    res.bar_availability_ts = _availability(ordered, fetched, reconciled)
    res.official_available_at = _iso(off.available_at)
    res.source_refs = {"official": dict(off.ref), "volume_basis": _basis_ref(basis), "readmitted_rows_sha256": _sha256(payload),
                       "quarantine_reason": qrec.get("reason")}
    _write_record(store, res, kind="readmitted", shard_sha256=_sha256(payload), content_hash=_sha256(_canonical(
        [[r["bar_start"], r["open"], r["high"], r["low"], r["close"], r["volume"]] for r in ordered])),
        row_count=len(ordered), official_row_hash=off.ref.get("line_sha256"), cfg=cfg, fetched=fetched)
    resolved = store.resolve_quarantine(sym, day, resolution="refetch_reconciled_pass", waived=False)
    _bind_recovery(store, sym, day, resolved)
    return res


def record_waiver(
    store: IntradayStore, symbol: str, session_date: str, *, operator: str, reason: str,
    clock: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Operator waiver with a reason. The day is marked ``waived`` and is never
    replication evidence; rows stay unedited and are only served on explicit request."""
    sym, day = _safe_symbol(symbol), _safe_date(session_date)
    if not str(operator or "").strip() or not str(reason or "").strip():
        raise ReconcileError("waiver needs operator and reason")
    entry = ((store.read_manifest(day) or {}).get("symbols") or {}).get(sym) or {}
    if entry.get("status") != STATUS_QUARANTINED or not entry.get("shard"):
        raise ReconcileError(f"{sym} {day} has no quarantined minute shard to waive")
    now = clock()
    record = {
        "schema_version": RECORD_SCHEMA, "kind": "waived", "symbol": sym, "session_date": day,
        "outcome": "waived", "waived": True, "replication_eligible": False, "operator": operator.strip(),
        "reason": reason.strip(), "reconciled_at": _iso(now), "bar_availability_ts": None,
        "shard_sha256": _file_sha(store.shard_path(sym, day)), "quarantine_reason": entry.get("reason"),
        "fetched_at": None,
    }
    if record["shard_sha256"] is None:
        raise ReconcileError(f"{sym} {day} shard missing")
    _atomic_write(record_path(store, sym, day), (_canonical(record) + "\n").encode("utf-8"))
    resolved = store.resolve_quarantine(sym, day, resolution=f"operator_waiver: {reason.strip()}", waived=True)
    _bind_recovery(store, sym, day, resolved)
    return {**record, "quarantine_resolved_at": resolved.get("resolved_at"),
            "quarantine_quarantined_at": resolved.get("quarantined_at")}


# ---------------------------------------------------------------- loader-facing read
def read_reconciled_rows(
    store: IntradayStore, symbol: str, session_date: str, *, include_waived: bool = False,
    stamp_waived_availability: Callable[[], datetime] = _utc_now,
) -> list[dict[str, Any]]:
    """Rows of a reconciled symbol-day with ``reconciled_at``/``bar_availability_ts`` set.

    Returns ``[]`` for anything unreconciled, quarantined, tampered (hash mismatch) or
    waived (unless ``include_waived``; waived rows carry ``replication_eligible=False``).
    """
    sym, day = _safe_symbol(symbol), _safe_date(session_date)
    rec = read_record(store, sym, day)
    if not rec or rec.get("withheld"):
        return []
    entry = ((store.read_manifest(day) or {}).get("symbols") or {}).get(sym) or {}
    kind, status = rec.get("kind"), entry.get("status")
    if kind == "reconciled":
        path, ok = store.shard_path(sym, day), status == STATUS_ACCEPTED and entry.get("shard_sha256") == rec.get("shard_sha256")
    elif kind == "readmitted":
        path = _readmit_rows_path(store, sym, day)
        ok = status == STATUS_QUARANTINED and _recovery_state(store, sym, day) is not None
    elif kind == "waived":
        path = store.shard_path(sym, day)
        ok = include_waived and status == STATUS_QUARANTINED and _recovery_state(store, sym, day) is not None
    else:
        return []
    if not ok or _file_sha(path) != rec.get("shard_sha256"):
        return []
    fetched = _parse_ts(rec.get("fetched_at"))
    out: list[dict[str, Any]] = []
    for row in _read_gz_lines(path):
        row = dict(row)
        reconciled = _parse_ts(rec.get("reconciled_at"))
        row["reconciled_at"] = rec.get("reconciled_at")
        if kind == "waived":
            row["bar_availability_ts"] = None  # no availability claim for waived days
        else:
            ends = [t for t in (_parse_ts(row.get("bar_end")), fetched, reconciled) if t]
            row["bar_availability_ts"] = _iso(max(ends))
        row["waived"] = bool(rec.get("waived"))
        row["replication_eligible"] = bool(rec.get("replication_eligible"))
        out.append(row)
    return out


# ---------------------------------------------------------------- pass rate
def pass_rate_summary(results: Iterable[ReconciliationResult], cfg: ReconcileConfig) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    decided = sum(n for k, n in counts.items() if k in RATE_OUTCOMES)
    rate = None if decided == 0 else Decimal(counts.get(OUTCOME_PASS, 0)) / Decimal(decided)
    review = rate is not None and rate < cfg.pass_rate_review_below
    return {
        "outcome_counts": dict(sorted(counts.items())), "decided_symbol_days": decided,
        "pass_rate": None if rate is None else str(rate),
        "operator_review_threshold": str(cfg.pass_rate_review_below),
        "operator_review_required": review,
        "tolerance_widened": False,
        "note": ("no decided symbol-days: pass rate not measured" if rate is None else
                 "below threshold: report to operator; tolerances are never auto-widened" if review else
                 "at/above threshold"),
    }
