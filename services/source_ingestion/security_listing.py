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
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA_VERSION = "security_listing.v1"
MAX_STALE_TRADING_DAYS = 3

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
        """Compact form (design budget: <= 2 MB at ~22,500 rows): categories interned, rows as arrays."""
        categories = sorted({r["category"] for r in self.allowlist.values()})
        index = {c: i for i, c in enumerate(categories)}
        return {
            "schema_version": SCHEMA_VERSION,
            "last_success_date": self.last_success_date,
            "categories": categories,
            "listing_allowlist": {
                sym: [r["venue"], index[r["category"]], r["cfi"], r["first_seen"], r["last_seen"]]
                for sym, r in self.allowlist.items()
            },
            "ordinary_share_registry": {s: [r["venue"], r["first_seen"]] for s, r in self.ordinary_share_registry.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ListingState":
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ListingParseError("listing state: unknown schema_version")
        cats = list(data.get("categories") or [])
        allow = {
            sym: {
                "venue": v[0], "category": cats[v[1]], "cfi": v[2], "is_warrant": str(v[2]).startswith("RW"),
                "first_seen": v[3], "last_seen": v[4],
            }
            for sym, v in (data.get("listing_allowlist") or {}).items()
        }
        reg = {s: {"venue": v[0], "first_seen": v[1]} for s, v in (data.get("ordinary_share_registry") or {}).items()}
        return cls(allowlist=allow, ordinary_share_registry=reg, last_success_date=data.get("last_success_date"))

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        tmp.replace(p)

    @classmethod
    def load(cls, path: str | Path) -> "ListingState":
        p = Path(path)
        if not p.exists():
            return cls()
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

    @classmethod
    def from_state(cls, state: ListingState, *, enabled: bool = True) -> "ListingFilter":
        return cls(listed=state.listed_symbols(), listing_date=state.last_success_date, enabled=enabled)

    def admits(self, symbol: str) -> bool:
        if not self.enabled:  # rollback switch: previous behaviour
            return True
        ok = bare_symbol(symbol) in self.listed
        if not ok:
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
