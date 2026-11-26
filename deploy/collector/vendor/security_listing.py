"""Exchange security listing: identifies warrants and detects expiry.

Design of record: SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16 section 4.3 (decision B:
warrants are fetched while listed and deleted at expiry).

Source pages (same parser on the collector host and on hosted):
  TWSE listed  https://isin.twse.com.tw/isin/C_public.jsp?strMode=2
  TPEx         https://isin.twse.com.tw/isin/C_public.jsp?strMode=4

A symbol is a warrant if and only if its listing row sits under a warrant category header
AND its CFI code starts with ``RW``. A header/CFI disagreement is a parse error (fail closed),
never a guess. The symbol shape is not an identification rule.

Fail closed:
  * parse error / fetch failure -> the previous allowlist is kept;
  * previous allowlist older than ``MAX_STALE_TRADING_DAYS`` trading days -> ``listing_unavailable``;
  * while the listing is unreadable (stale at all) nothing is reported expired.
"""

from __future__ import annotations

import html as _html
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA_VERSION = "security_listing.v2"
LEGACY_SCHEMA_VERSION = "security_listing.v1"
MAX_STALE_TRADING_DAYS = 3
# SOURCE-HOSTED-LISTING-STATE-AND-PIN-SOURCE-20261118: one dated state file, replaced per listing day. The live shape
# (57,531 rows on 2026-11-17) measures about 3.2 MB; the cap is the runtime budget, enforced on save and on load.
MAX_STATE_BYTES = 20 * 1024 * 1024
# Where the collector host publishes its state (served by the existing read only bundle server under bundles/) and
# the file name hosted keeps it under in the source-ingest data directory. The state is ONE line of compact JSON
# (valid JSONL), so the storage manifest checker, which discovers ``*.jsonl`` names, registers it.
BUNDLE_STATE_PATH = "bundles/security-listing.jsonl"
HOSTED_STATE_FILENAME = "security-listing.jsonl"
LISTING_MODE_ENV = "SOURCE_INGEST_SECURITY_LISTING_MODE"
LISTING_MODES = ("off", "shadow", "enforce")
DEFAULT_LISTING_MODE = "shadow"

TWSE_LISTED_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
TPEX_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"

EXPECTED_COLUMNS = ("有價證券代號及名稱", "國際證券辨識號碼(ISIN Code)", "上市日", "市場別", "產業別", "CFICode", "備註")
WARRANT_HEADER_RE = re.compile(r"認購\(售\)權證|認購權證|認售權證")
SHARE_CFI_PREFIXES = ("ES", "EP")  # common / preferred shares: ordinary_share_registry

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_UPDATE_RE = re.compile(r"最近更新日期:\s*(\d{4})/(\d{2})/(\d{2})")


def bare_symbol(symbol: Any) -> str:
    """'2330.TW' / '2330.TWSE' / '6488.TWO' / '6488.TPEX' -> '2330' / '6488' (the listing holds bare exchange codes)."""
    text = str(symbol or "").strip()
    return re.sub(r"\.(TWSE|TPEX|TWO|TW)$", "", text, flags=re.I)


def write_state_bytes(path: Path, data: bytes) -> None:
    """Replace the state file in one step (temp file in the same directory, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


class ListingParseError(ValueError):
    """Listing page shape changed or is unreadable."""


class ListingUnavailable(RuntimeError):
    reason = "listing_unavailable"


@dataclass(frozen=True)
class ListingRow:
    symbol: str
    venue: str
    category: str
    cfi: str
    is_warrant: bool
    name: str = ""


@dataclass(frozen=True)
class ParsedListing:
    venue: str
    page_date: str
    rows: tuple[ListingRow, ...]


def _text(cell: str) -> str:
    return _html.unescape(_TAG_RE.sub("", cell)).replace("\xa0", " ").strip()


def parse_isin_page(page_html: str, *, venue: str) -> ParsedListing:
    """Parse one ISIN page (already decoded text). Raises ListingParseError on any shape change."""
    venue = venue.upper()
    m = _UPDATE_RE.search(page_html)
    if not m:
        raise ListingParseError("listing page: update date marker not found")
    page_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    columns_seen = False
    category: str | None = None
    rows: list[ListingRow] = []
    for raw_row in _ROW_RE.findall(page_html):
        cells = [_text(c) for c in _CELL_RE.findall(raw_row)]
        if not cells:
            continue
        if len(cells) == 1:
            if not columns_seen:
                raise ListingParseError("listing page: category header before column header")
            category = cells[0]
            continue
        if not columns_seen:
            if tuple(cells) != EXPECTED_COLUMNS:
                raise ListingParseError(f"listing page: column header changed {cells!r}")
            columns_seen = True
            continue
        if len(cells) != len(EXPECTED_COLUMNS):
            raise ListingParseError(f"listing page: malformed row {cells!r}")
        if category is None:
            raise ListingParseError("listing page: data row without category header")
        code_name, _isin, _listed, _market, _industry, cfi, _note = cells
        parts = re.split(r"[　 ]+", code_name, maxsplit=1)
        symbol = parts[0].strip()
        if not symbol or not re.fullmatch(r"[0-9A-Za-z]{4,8}", symbol):
            raise ListingParseError(f"listing page: unreadable symbol in {code_name!r}")
        if not re.fullmatch(r"[A-Z]{6}", cfi):
            raise ListingParseError(f"listing page: unreadable CFI {cfi!r} for {symbol}")
        header_warrant = bool(WARRANT_HEADER_RE.search(category))
        cfi_warrant = cfi.startswith("RW")
        if header_warrant != cfi_warrant:
            raise ListingParseError(
                f"listing page: header {category!r} and CFI {cfi!r} disagree for {symbol}"
            )
        rows.append(
            ListingRow(
                symbol=symbol,
                venue=venue,
                category=category,
                cfi=cfi,
                is_warrant=header_warrant,
                name=parts[1].strip() if len(parts) > 1 else "",
            )
        )
    if not columns_seen or not rows:
        raise ListingParseError("listing page: no data rows")
    return ParsedListing(venue=venue, page_date=page_date, rows=tuple(rows))


# ---------------------------------------------------------------------------
# State: listing_allowlist + ordinary_share_registry
# ---------------------------------------------------------------------------


def _weekday_trading_days_between(start: date, end: date) -> int:
    """Trading days after ``start`` up to and including ``end`` (weekdays by default)."""
    n = 0
    d = start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


@dataclass
class ListingState:
    """listing_allowlist rows (symbol -> venue/category/cfi/first_seen/last_seen), the share registry
    and the last successful listing day. JSON persisted, replaced by the newest listing."""

    allowlist: dict[str, dict[str, Any]] = field(default_factory=dict)
    ordinary_share_registry: dict[str, dict[str, str]] = field(default_factory=dict)
    last_success_date: str | None = None

    # ----- persistence
    def to_dict(self) -> dict[str, Any]:
        """Compact form: categories, venues and dates interned, rows as arrays. Live listing (57,531 rows,
        41,965 TWSE warrants) must stay <= 2 MB; measured in the tests."""
        categories = sorted({r["category"] for r in self.allowlist.values()})
        venues = sorted({r["venue"] for r in self.allowlist.values()} | {r["venue"] for r in self.ordinary_share_registry.values()})
        dates = sorted(
            {r["first_seen"] for r in self.allowlist.values()} | {r["last_seen"] for r in self.allowlist.values()}
            | {r["first_seen"] for r in self.ordinary_share_registry.values()}
        )
        ci = {c: i for i, c in enumerate(categories)}
        vi = {v: i for i, v in enumerate(venues)}
        di = {d: i for i, d in enumerate(dates)}
        return {
            "schema_version": SCHEMA_VERSION,
            "last_success_date": self.last_success_date,
            "categories": categories,
            "venues": venues,
            "dates": dates,
            "listing_allowlist": {
                sym: [vi[r["venue"]], ci[r["category"]], r["cfi"], di[r["first_seen"]], di[r["last_seen"]]]
                for sym, r in self.allowlist.items()
            },
            "ordinary_share_registry": {s: [vi[r["venue"]], di[r["first_seen"]]] for s, r in self.ordinary_share_registry.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ListingState":
        version = data.get("schema_version")
        if version not in (SCHEMA_VERSION, LEGACY_SCHEMA_VERSION):
            raise ListingParseError("listing state: unknown schema_version")
        cats = list(data.get("categories") or [])
        if version == SCHEMA_VERSION:
            venues, dates = list(data.get("venues") or []), list(data.get("dates") or [])
        else:  # v1 stored venue and dates inline
            venues = dates = None
        ven = (lambda x: venues[x]) if venues is not None else (lambda x: x)
        dat = (lambda x: dates[x]) if dates is not None else (lambda x: x)
        allow = {
            sym: {
                "venue": ven(v[0]), "category": cats[v[1]], "cfi": v[2], "is_warrant": str(v[2]).startswith("RW"),
                "first_seen": dat(v[3]), "last_seen": dat(v[4]),
            }
            for sym, v in (data.get("listing_allowlist") or {}).items()
        }
        reg = {s: {"venue": ven(v[0]), "first_seen": dat(v[1])} for s, v in (data.get("ordinary_share_registry") or {}).items()}
        return cls(allowlist=allow, ordinary_share_registry=reg, last_success_date=data.get("last_success_date"))

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_STATE_BYTES:
            raise ListingParseError(f"listing state is {len(data)} bytes, over the {MAX_STATE_BYTES} byte cap")
        write_state_bytes(p, data)

    @classmethod
    def load(cls, path: str | Path) -> "ListingState":
        p = Path(path)
        if not p.exists():
            return cls()
        if p.stat().st_size > MAX_STATE_BYTES:
            raise ListingParseError(f"listing state file is over the {MAX_STATE_BYTES} byte cap")
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    # ----- update (once per trading day)
    def apply_listing(self, listings: Sequence[ParsedListing], *, listing_date: str) -> None:
        """Replace the allowlist by the newest listing. Both venues are required so one missing
        venue is never read as 'all of its symbols expired'."""
        venues = {l.venue for l in listings}
        if venues != {"TWSE", "TPEX"}:
            raise ListingParseError(f"listing update needs both venues, got {sorted(venues)}")
        seen: set[str] = set()
        new_allow: dict[str, dict[str, Any]] = {}
        for listing in listings:
            for row in listing.rows:
                prev = self.allowlist.get(row.symbol) or {}
                new_allow[row.symbol] = {
                    "venue": row.venue,
                    "category": row.category,
                    "cfi": row.cfi,
                    "is_warrant": row.is_warrant,
                    "first_seen": prev.get("first_seen") or listing_date,
                    "last_seen": listing_date,
                }
                seen.add(row.symbol)
                if row.cfi.startswith(SHARE_CFI_PREFIXES):
                    self.ordinary_share_registry.setdefault(
                        row.symbol, {"venue": row.venue, "first_seen": listing_date}
                    )
        # Symbols no longer listed keep their row (last_seen stays old) so expiry is computable
        # until the sweeper has deleted them; ordinary share registry only ever gains.
        for sym, row in self.allowlist.items():
            if sym not in seen:
                new_allow[sym] = dict(row)
        self.allowlist = new_allow
        self.last_success_date = listing_date

    # ----- fail-closed status
    def staleness_days(self, today: str, *, trading_days_between: Callable[[date, date], int] | None = None) -> int | None:
        if not self.last_success_date:
            return None
        fn = trading_days_between or _weekday_trading_days_between
        return fn(date.fromisoformat(self.last_success_date), date.fromisoformat(today))

    def status(self, today: str, **kw: Any) -> str:
        """'fresh' (listing of today or previous trading day), 'stale_ok' (<=3 trading days),
        'listing_unavailable' (none or older)."""
        age = self.staleness_days(today, **kw)
        if age is None or age > MAX_STALE_TRADING_DAYS:
            return "listing_unavailable"
        return "fresh" if age <= 1 else "stale_ok"

    def require_collectable(self, today: str, **kw: Any) -> None:
        if self.status(today, **kw) == "listing_unavailable":
            raise ListingUnavailable("listing_unavailable")

    # ----- lookups
    def is_listed(self, symbol: str) -> bool:
        row = self.allowlist.get(symbol)
        return bool(row) and row["last_seen"] == self.last_success_date

    def listed_symbols(self) -> frozenset[str]:
        return frozenset(s for s in self.allowlist if self.is_listed(s))

    def is_warrant(self, symbol: str) -> bool:
        row = self.allowlist.get(symbol)
        return bool(row and row.get("is_warrant"))

    def expired_warrants(self, today: str | None = None, **kw: Any) -> frozenset[str]:
        """Warrants whose last_seen is older than the last successful listing day.
        Empty while the listing is unreadable (status check when ``today`` is given): nothing
        is expired on a stale listing."""
        if not self.last_success_date:
            return frozenset()
        if today is not None and self.status(today, **kw) != "fresh":
            return frozenset()
        return frozenset(
            s for s, r in self.allowlist.items() if r.get("is_warrant") and r["last_seen"] < self.last_success_date
        )


# ---------------------------------------------------------------------------
# Collection filter (shared by collector spiders, hosted connectors, storage guard)
# ---------------------------------------------------------------------------


@dataclass
class ListingFilter:
    """Admits listed symbols only. Counts what it skips (``skipped_not_listed_count``)."""

    listed: frozenset[str]
    listing_date: str | None = None
    enabled: bool = True
    skipped_not_listed_count: int = 0
    # ``shadow``: nothing is skipped; the symbols ``enforce`` would skip are only counted (would_skip_count).
    mode: str = "enforce"
    would_skip_count: int = 0

    @classmethod
    def from_state(cls, state: ListingState, *, enabled: bool = True, mode: str = "enforce") -> "ListingFilter":
        # listed now + every symbol ever listed as an ordinary share (delisted shares keep their daily history)
        return cls(listed=state.listed_symbols() | frozenset(state.ordinary_share_registry), listing_date=state.last_success_date, enabled=enabled, mode=mode)

    def admits(self, symbol: str) -> bool:
        if not self.enabled:  # rollback switch: previous behaviour
            return True
        ok = bare_symbol(symbol) in self.listed
        if not ok:
            if self.mode == "shadow":
                self.would_skip_count += 1
                return True
            self.skipped_not_listed_count += 1
        return ok

    def filter_records(self, records: Iterable[Any], symbol_of: Callable[[Any], str | None]) -> list[Any]:
        out = []
        for rec in records:
            sym = symbol_of(rec)
            if sym is None or self.admits(sym):
                out.append(rec)
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "skipped_not_listed_count": self.skipped_not_listed_count,
            "would_skip_count": self.would_skip_count,
            "listing_mode": self.mode,
            "listing_date": self.listing_date,
            "listing_filter_enabled": self.enabled,
        }


# ---------------------------------------------------------------------------
# Refresh (the same function runs on the collector host and on hosted)
# ---------------------------------------------------------------------------


def decode_isin_bytes(raw: bytes) -> str:
    """The ISIN pages are Big5 (HKSCS for rare characters)."""
    return raw.decode("big5hkscs", errors="replace")


def refresh_listing(
    state: ListingState,
    fetch_page: Callable[[str], str],
    *,
    listing_date: str,
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    """Fetch both venue pages, parse, replace the allowlist, persist.

    Fail closed: any fetch or parse failure leaves ``state`` and the persisted file unchanged and
    returns ``ok=False`` with a readable reason; the caller keeps using the previous allowlist
    (at most MAX_STALE_TRADING_DAYS trading days, see ListingState.status).
    """
    try:
        parsed = [
            parse_isin_page(fetch_page(TWSE_LISTED_URL), venue="TWSE"),
            parse_isin_page(fetch_page(TPEX_URL), venue="TPEX"),
        ]
        probe = ListingState(
            allowlist={k: dict(v) for k, v in state.allowlist.items()},
            ordinary_share_registry={k: dict(v) for k, v in state.ordinary_share_registry.items()},
            last_success_date=state.last_success_date,
        )
        probe.apply_listing(parsed, listing_date=listing_date)
    except Exception as exc:  # noqa: BLE001 - every failure is the same fail-closed outcome
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "listing_date": state.last_success_date}
    state.allowlist = probe.allowlist
    state.ordinary_share_registry = probe.ordinary_share_registry
    state.last_success_date = probe.last_success_date
    if state_path is not None:
        state.save(state_path)
    return {
        "ok": True,
        "listing_date": listing_date,
        "listed_count": len(state.listed_symbols()),
        "listed_warrant_count": sum(1 for s in state.listed_symbols() if state.is_warrant(s)),
    }


# ---------------------------------------------------------------------------
# Hosted: one dated state file read through the existing collector bundle transport
# ---------------------------------------------------------------------------


def listing_mode(environ: Mapping[str, str] | None = None) -> tuple[str, str | None]:
    """The named setting ``SOURCE_INGEST_SECURITY_LISTING_MODE``: ``off`` | ``shadow`` | ``enforce``.

    Unset means ``shadow``. An unknown value also means ``shadow`` (never a silent ``enforce``) and is returned as
    the second item so the health payload can say so."""
    raw = str((environ if environ is not None else os.environ).get(LISTING_MODE_ENV, "") or "").strip().lower()
    if not raw:
        return DEFAULT_LISTING_MODE, None
    if raw in LISTING_MODES:
        return raw, None
    return DEFAULT_LISTING_MODE, f"{LISTING_MODE_ENV}={raw!r} is not one of {list(LISTING_MODES)}; using {DEFAULT_LISTING_MODE}"


def _taipei_today() -> str:
    from datetime import timezone

    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


class HostedListing:
    """Hosted side of the listing: keeps the collector host's dated state as ONE file and builds the filter.

    * One fetch, one listing: the state is read from the collector host through the bundle transport the pull
      already uses (``bundles/security-listing.jsonl``); hosted never fetches the exchange pages. The bytes are kept
      verbatim, so the hosted file equals the collector file.
    * One file replaced per listing day (growth per day zero, cap ``MAX_STATE_BYTES``); an older remote state never
      replaces a newer local one.
    * Fail closed and visible: an unreadable or too old listing yields no filter (no symbol is treated as expired)
      and ``health()`` says why, with the age in trading days.
    * ``shadow`` (default) counts what ``enforce`` would skip and skips nothing; ``off`` does not read the listing.
    """

    def __init__(
        self,
        state_path: str | Path,
        *,
        transport: Callable[[], Any] | Any | None = None,
        mode: str | None = None,
        today: Callable[[], str] | None = None,
        trading_days_between: Callable[[date, date], int] | None = None,
        retry_seconds: float = 900.0,
        same_day_retry_seconds: float = 3600.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.state_path = Path(state_path)
        self._transport = transport
        if mode is None:
            self.mode, self.mode_problem = listing_mode()
        else:
            self.mode, self.mode_problem = (mode, None) if mode in LISTING_MODES else (DEFAULT_LISTING_MODE, f"mode {mode!r} unknown")
        self._today = today or _taipei_today
        self._tdb = trading_days_between
        self._retry = retry_seconds
        self._same_day_retry = same_day_retry_seconds
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._next_fetch_at = 0.0
        self._cache_key: tuple[int, int] | None = None
        self._cache: tuple[ListingState | None, str | None] = (None, "listing_missing")
        self.last_sync: dict[str, Any] = {"attempted": False}
        self.fetch_count = 0
        self.last_run: dict[str, Any] | None = None
        self._day_totals: dict[str, dict[str, int]] = {}

    # ----- local state
    def _load(self) -> tuple[ListingState | None, str | None]:
        try:
            st = self.state_path.stat()
        except OSError:
            self._cache_key, self._cache = None, (None, "listing_missing")
            return self._cache
        key = (st.st_mtime_ns, st.st_size)
        if key != self._cache_key:
            try:
                state = ListingState.load(self.state_path)
                if not state.last_success_date:
                    raise ListingParseError("listing state has no last_success_date")
                self._cache = (state, None)
            except Exception as exc:  # noqa: BLE001 - unreadable is a reported state, not a crash
                self._cache = (None, f"listing_unreadable: {type(exc).__name__}: {exc}")
            self._cache_key = key
        return self._cache

    # ----- fetch (once per listing day; read through the existing bundle transport)
    def sync(self) -> dict[str, Any]:
        with self._lock:
            if self.mode == "off":
                return {"attempted": False, "reason": "mode_off"}
            today = self._today()
            due = date.fromisoformat(today)
            while due.weekday() >= 5:  # no listing is published on a weekend: the last weekday's listing is current
                due -= timedelta(days=1)
            due_day = due.isoformat()
            state, _problem = self._load()
            if state is not None and state.last_success_date and state.last_success_date >= due_day:
                return {"attempted": False, "reason": "up_to_date", "listing_date": state.last_success_date}
            now = self._monotonic()
            if now < self._next_fetch_at:
                return {"attempted": False, "reason": "retry_later"}
            transport = self._transport() if callable(self._transport) else self._transport
            if transport is None:
                self._next_fetch_at = now + self._retry
                self.last_sync = {"attempted": False, "ok": False, "reason": "transport_unavailable"}
                return self.last_sync
            self.fetch_count += 1
            try:
                raw = transport.read(BUNDLE_STATE_PATH, max_bytes=MAX_STATE_BYTES)
                if len(raw) > MAX_STATE_BYTES:
                    raise ListingParseError(f"listing state is over the {MAX_STATE_BYTES} byte cap")
                remote = ListingState.from_dict(json.loads(raw.decode("utf-8")))
                if not remote.last_success_date:
                    raise ListingParseError("collector listing state has no last_success_date")
            except Exception as exc:  # noqa: BLE001 - keep the previous state; reported in health
                self._next_fetch_at = now + self._retry
                self.last_sync = {"attempted": True, "ok": False, "reason": f"{type(exc).__name__}: {exc}"}
                return self.last_sync
            if state is not None and state.last_success_date and remote.last_success_date < state.last_success_date:
                self._next_fetch_at = now + self._same_day_retry
                self.last_sync = {"attempted": True, "ok": False, "reason": "remote_older_than_local",
                                  "remote_listing_date": remote.last_success_date, "listing_date": state.last_success_date}
                return self.last_sync
            replaced = state is None or remote.last_success_date != state.last_success_date
            if replaced:
                write_state_bytes(self.state_path, raw)  # verbatim: hosted state == collector state
                self._cache_key = None
            # While the collector has not published today's listing yet, look again later, not on every call.
            self._next_fetch_at = now + (self._same_day_retry if remote.last_success_date < due_day else 0.0)
            self.last_sync = {"attempted": True, "ok": True, "replaced": replaced, "bytes": len(raw),
                              "listing_date": remote.last_success_date}
            return self.last_sync

    # ----- status and filter
    def _status(self, state: ListingState | None, problem: str | None, today: str) -> dict[str, Any]:
        if state is None:
            return {"status": "listing_unavailable", "readable": False, "reason": problem, "listing_date": None,
                    "age_trading_days": None}
        age = state.staleness_days(today, trading_days_between=self._tdb)
        status = state.status(today, trading_days_between=self._tdb)
        reason = None
        if status == "listing_unavailable":
            reason = f"listing_unavailable: last listing {state.last_success_date} is {age} trading days old (limit {MAX_STALE_TRADING_DAYS})"
        elif status == "stale_ok":
            reason = f"listing is {age} trading days old (within the {MAX_STALE_TRADING_DAYS} day limit)"
        return {"status": status, "readable": True, "reason": reason, "listing_date": state.last_success_date,
                "age_trading_days": age}

    def filter_for_run(self, *, layer: str = "writer") -> ListingFilter | None:
        """A new filter for one run (its counters are that run's counts), or None: no filtering."""
        if self.mode == "off":
            return None
        self.sync()
        with self._lock:
            state, problem = self._load()
            info = self._status(state, problem, self._today())
        if state is None or info["status"] == "listing_unavailable":
            return None  # unreadable or too old: nothing is treated as expired, health says why
        return ListingFilter.from_state(state, mode="shadow" if self.mode == "shadow" else "enforce")

    def record_run(self, run_id: str, flt: ListingFilter | None, *, layer: str = "writer") -> dict[str, Any]:
        """Remember one run's counts for the health payload; also returned for the run log."""
        counts = {
            "ingest_run_id": run_id, "layer": layer, "listing_mode": self.mode,
            "listing_date": flt.listing_date if flt else None,
            "would_skip_count": flt.would_skip_count if flt else 0,
            "skipped_not_listed_count": flt.skipped_not_listed_count if flt else 0,
            "filter_active": flt is not None,
        }
        with self._lock:
            if layer == "writer":
                self.last_run = counts
            day = self._day_totals.setdefault(self._today(), {})
            for k in ("would_skip_count", "skipped_not_listed_count"):
                day[f"{layer}_{k}"] = day.get(f"{layer}_{k}", 0) + counts[k]
            day["runs"] = day.get("runs", 0) + (1 if layer == "writer" else 0)
            for old in sorted(self._day_totals)[:-7]:
                del self._day_totals[old]
        return counts

    def health(self) -> dict[str, Any]:
        with self._lock:
            state, problem = self._load()
            today = self._today()
            info = self._status(state, problem, today)
            try:
                size = self.state_path.stat().st_size
            except OSError:
                size = None
            return {
                "mode": self.mode,
                "mode_problem": self.mode_problem,
                "state_file": self.state_path.name,
                "state_bytes": size,
                "state_bytes_cap": MAX_STATE_BYTES,
                "today": today,
                **info,
                "expired_warrant_count": len(state.expired_warrants(today, trading_days_between=self._tdb)) if state is not None else None,
                "last_sync": dict(self.last_sync),
                "fetch_count": self.fetch_count,
                "last_run": dict(self.last_run) if self.last_run else None,
                "day_totals": {d: dict(v) for d, v in self._day_totals.items()},
            }


_HOSTED: HostedListing | None = None


def set_hosted_listing(hosted: HostedListing | None) -> None:
    global _HOSTED
    _HOSTED = hosted


def hosted_listing() -> HostedListing | None:
    return _HOSTED
