"""Partitioned gz-JSONL store for ``tw_price_1m`` (SD_INTRADAY_MINUTE_BARS §4).

Layout under ``INTRADAY_STORE_ROOT``::

    normalized/tw_price_1m/<session_date>/<SYMBOL>.jsonl.gz   rows sorted by bar_start
    normalized/tw_price_1m/<session_date>/manifest.json       per-day manifest
    raw/tw_price_1m/<session_date>/<SYMBOL>.raw.jsonl.gz      raw provider envelope
    normalized/tw_price_1m_quarantine/<session_date>/<SYMBOL>.json
    _logs/tw_price_1m_prune.jsonl                             pruner deletion record

Contract
* Idempotent on ``(symbol, session_date, bar_start)``. Re-storing identical OHLCV is
  a no-op (nothing is rewritten). A different OHLCV for an existing key is a conflict:
  the existing data is never overwritten and the symbol-day is quarantined.
* Only this dataset is ever deleted: the pruner is restricted to the three dataset
  directories above and logs every deletion with the manifest hash.
* Disk guard: free space on the store mount must be >= 10x projected remaining bytes
  and >= 20 GB, otherwise ``DiskBudgetExhausted`` (``disk_budget_exhausted``) is raised.
* Nothing here writes ``tw_price_daily`` or any other dataset.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

DATASET = "tw_price_1m"
NORMALIZED_DIR = Path("normalized") / DATASET
QUARANTINE_DIR = Path("normalized") / f"{DATASET}_quarantine"
RAW_DIR = Path("raw") / DATASET
LOG_DIR = Path("_logs")
PRUNE_LOG = LOG_DIR / f"{DATASET}_prune.jsonl"
STORE_ROOT_ENV = "INTRADAY_STORE_ROOT"
MANIFEST_SCHEMA = "tw_price_1m_manifest.v1"
MANIFEST_NAME = "manifest.json"

RETENTION_DAYS = 730  # rolling two years
QUARANTINE_RESOLVED_EXPIRY_DAYS = 90
DISK_MULTIPLIER = 10
DISK_FLOOR_BYTES = 20 * 1024**3
# SD §4 sizing, per row: normalized ~130 B and raw ~200 B at ~8:1 gzip.
PROJECTED_BYTES_PER_ROW = (130 + 200) // 8 + 1
PROJECTED_ROWS_PER_SYMBOL_DAY = 271
QUARANTINE_ALLOWANCE = 0.10
TEMP_ALLOWANCE_BYTES = 200 * 1024**2

# Identity of a bar is its key; content is OHLCV only. Fetch-time stamps never
# make two otherwise identical bars "different".
CONTENT_FIELDS = ("open", "high", "low", "close", "volume")
KEY_FIELDS = ("symbol", "session_date", "bar_start")

# Symbol-day statuses (manifest). Only ``accepted`` rows are loader-visible, and
# even those remain pending_reconciliation until K3 reconciles them.
STATUS_ACCEPTED = "accepted"
STATUS_QUARANTINED = "quarantined"
STATUS_PROVIDER_EMPTY = "provider_empty"
STATUS_NON_TRADING_DAY = "non_trading_day"
COMPLETE_STATUSES = frozenset(
    {STATUS_ACCEPTED, STATUS_QUARANTINED, STATUS_PROVIDER_EMPTY, STATUS_NON_TRADING_DAY}
)


class IntradayStoreError(RuntimeError):
    pass


class DiskBudgetExhausted(IntradayStoreError):
    reason = "disk_budget_exhausted"

    def __init__(self, detail: Mapping[str, Any]) -> None:
        self.detail = dict(detail)
        super().__init__(f"disk_budget_exhausted: {json.dumps(self.detail, sort_keys=True)}")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _gz_bytes(lines: Iterable[str]) -> bytes:
    buffer = io.BytesIO()
    # mtime=0 keeps identical content byte-identical on disk.
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, compresslevel=6) as handle:
        for line in lines:
            handle.write(line.encode("utf-8"))
            handle.write(b"\n")
    return buffer.getvalue()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_gz_lines(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _safe_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if not text or not all(ch.isalnum() or ch in "._-" for ch in text) or text.startswith("."):
        raise IntradayStoreError(f"invalid symbol for store path: {symbol!r}")
    return text


def _safe_date(value: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise IntradayStoreError(f"invalid session_date: {value!r}") from exc


def _content_of(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(field_name) for field_name in CONTENT_FIELDS)


def measure_mount(path: str | Path) -> dict[str, Any]:
    """``df --output=source,size,avail`` plus identity for the mount holding ``path``.

    Used to record the hosted measurement (SD §4). It reports the machine it runs on;
    evidence must only be claimed for the hosted dev host when run there.
    """
    target = Path(path)
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    result: dict[str, Any] = {
        "path": str(target),
        "measured_path": str(probe),
        "total_bytes": usage.total,
        "free_bytes": usage.free,
        "hostname": os.uname().nodename,
        "measured_at": _iso(_utc_now()),
    }
    try:
        out = subprocess.run(
            ["df", "-B1", "--output=source,size,avail,target", str(probe)],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip().splitlines()
        if len(out) >= 2:
            source, size, avail, mount = out[1].split(None, 3)
            result.update(
                df_source=source, df_size_bytes=int(size), df_avail_bytes=int(avail), df_mount=mount
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        result["df_error"] = "df_unavailable"
    return result


def project_remaining_bytes(remaining_symbol_days: int) -> int:
    """Projected bytes still to be written: raw + normalized + quarantine + temp."""
    if remaining_symbol_days < 0:
        raise ValueError("remaining_symbol_days must be >= 0")
    base = remaining_symbol_days * PROJECTED_ROWS_PER_SYMBOL_DAY * PROJECTED_BYTES_PER_ROW
    return int(base * (1 + QUARANTINE_ALLOWANCE)) + TEMP_ALLOWANCE_BYTES


@dataclass(frozen=True)
class PutResult:
    status: str  # written | added | unchanged | quarantined | provider_empty | non_trading_day
    symbol: str
    session_date: str
    row_count: int = 0
    new_rows: int = 0
    reason: str = ""


@dataclass
class IntradayStore:
    root: Path
    retention_days: int = RETENTION_DAYS
    disk_multiplier: int = DISK_MULTIPLIER
    disk_floor_bytes: int = DISK_FLOOR_BYTES
    clock: Callable[[], datetime] = _utc_now
    usage_fn: Callable[[Path], Any] = field(default=lambda p: shutil.disk_usage(p))

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    @classmethod
    def from_env(cls, **kwargs: Any) -> "IntradayStore":
        value = str(os.environ.get(STORE_ROOT_ENV) or "").strip()
        if not value:
            raise IntradayStoreError(f"{STORE_ROOT_ENV} is not configured")
        return cls(Path(value), **kwargs)

    # ---- paths ----
    def day_dir(self, session_date: str) -> Path:
        return self.root / NORMALIZED_DIR / _safe_date(session_date)

    def shard_path(self, symbol: str, session_date: str) -> Path:
        return self.day_dir(session_date) / f"{_safe_symbol(symbol)}.jsonl.gz"

    def raw_path(self, symbol: str, session_date: str) -> Path:
        return self.root / RAW_DIR / _safe_date(session_date) / f"{_safe_symbol(symbol)}.raw.jsonl.gz"

    def quarantine_path(self, symbol: str, session_date: str) -> Path:
        return self.root / QUARANTINE_DIR / _safe_date(session_date) / f"{_safe_symbol(symbol)}.json"

    def manifest_path(self, session_date: str) -> Path:
        return self.day_dir(session_date) / MANIFEST_NAME

    # ---- disk guard ----
    def check_disk_budget(self, remaining_symbol_days: int) -> dict[str, Any]:
        """Raise ``DiskBudgetExhausted`` unless the store mount has the required room.

        Free space must be >= ``disk_multiplier`` x projected remaining bytes and
        >= ``disk_floor_bytes``. Nothing is deleted on failure.
        """
        probe = self.root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        free = int(self.usage_fn(probe).free)
        projected = project_remaining_bytes(remaining_symbol_days)
        required = max(self.disk_multiplier * projected, self.disk_floor_bytes)
        detail = {
            "free_bytes": free,
            "projected_remaining_bytes": projected,
            "required_free_bytes": required,
            "multiplier": self.disk_multiplier,
            "floor_bytes": self.disk_floor_bytes,
            "root": str(self.root),
        }
        if free < required:
            raise DiskBudgetExhausted(detail)
        return {**detail, "ok": True}

    # ---- manifest ----
    def read_manifest(self, session_date: str) -> dict[str, Any] | None:
        path = self.manifest_path(session_date)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise IntradayStoreError(f"unreadable manifest: {path}") from exc

    def _write_manifest(self, session_date: str, manifest: dict[str, Any]) -> None:
        symbols = manifest["symbols"]
        manifest["schema_version"] = MANIFEST_SCHEMA
        manifest["dataset"] = DATASET
        manifest["session_date"] = session_date
        manifest["symbol_count"] = len(symbols)
        manifest["row_count"] = sum(int(e.get("row_count") or 0) for e in symbols.values())
        manifest["content_hash"] = _sha256(
            _canonical({s: [e.get("status"), e.get("content_hash")] for s, e in sorted(symbols.items())})
        )
        manifest["updated_at"] = _iso(self.clock())
        _atomic_write(self.manifest_path(session_date), (_canonical(manifest) + "\n").encode("utf-8"))

    def _manifest_for_update(self, session_date: str) -> dict[str, Any]:
        return self.read_manifest(session_date) or {"symbols": {}}

    def symbol_day_status(self, symbol: str, session_date: str) -> str | None:
        manifest = self.read_manifest(session_date)
        if not manifest:
            return None
        entry = manifest.get("symbols", {}).get(_safe_symbol(symbol))
        return None if entry is None else str(entry.get("status"))

    def is_symbol_day_complete(self, symbol: str, session_date: str) -> bool:
        return self.symbol_day_status(symbol, session_date) in COMPLETE_STATUSES

    # ---- reads ----
    def read_rows(self, symbol: str, session_date: str) -> list[dict[str, Any]]:
        """Accepted rows only; quarantined symbol-days are invisible to readers."""
        if self.symbol_day_status(symbol, session_date) != STATUS_ACCEPTED:
            return []
        path = self.shard_path(symbol, session_date)
        return _read_gz_lines(path) if path.exists() else []

    # ---- writes ----
    def put_symbol_day(
        self,
        symbol: str,
        session_date: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        raw_envelope: Mapping[str, Any] | None = None,
    ) -> PutResult:
        """Store one symbol-day idempotently by ``(symbol, session_date, bar_start)``."""
        sym = _safe_symbol(symbol)
        day = _safe_date(session_date)
        by_key: dict[str, dict[str, Any]] = {}
        for raw in rows:
            row = dict(raw)
            if str(row.get("symbol", "")).upper() != sym or row.get("session_date") != day:
                raise IntradayStoreError(f"row outside symbol-day {sym} {day}: {row.get('bar_start')}")
            start = str(row.get("bar_start") or "")
            if not start:
                raise IntradayStoreError("row without bar_start")
            prev = by_key.get(start)
            if prev is not None and _content_of(prev) != _content_of(row):
                return self.quarantine_symbol_day(
                    sym, day, reason="conflicting_duplicate_in_batch", detail={"bar_start": start},
                    incoming_rows=rows, raw_envelope=raw_envelope,
                )
            by_key[start] = row
        if not by_key:
            return self._mark_empty(sym, day, STATUS_PROVIDER_EMPTY, raw_envelope)

        manifest = self._manifest_for_update(day)
        entry = manifest["symbols"].get(sym)
        existing: dict[str, dict[str, Any]] = {}
        if entry and entry.get("status") == STATUS_QUARANTINED:
            return PutResult("quarantined", sym, day, int(entry.get("row_count") or 0),
                             reason="already_quarantined")
        if entry and entry.get("status") == STATUS_ACCEPTED and self.shard_path(sym, day).exists():
            existing = {r["bar_start"]: r for r in _read_gz_lines(self.shard_path(sym, day))}

        conflicts = [
            k for k, row in by_key.items() if k in existing and _content_of(existing[k]) != _content_of(row)
        ]
        if conflicts:
            return self.quarantine_symbol_day(
                sym, day, reason="conflicting_content_for_existing_key",
                detail={"bar_starts": sorted(conflicts)[:50], "conflict_count": len(conflicts)},
                incoming_rows=rows, raw_envelope=raw_envelope,
            )
        new_keys = [k for k in by_key if k not in existing]
        if existing and not new_keys:
            return PutResult("unchanged", sym, day, len(existing))  # no rewrite

        merged = dict(existing)
        for key in new_keys:
            merged[key] = by_key[key]
        ordered = [merged[k] for k in sorted(merged)]
        payload = _gz_bytes(_canonical(r) for r in ordered)
        content_hash = _sha256(_canonical([[r["bar_start"], *_content_of(r)] for r in ordered]))
        # Raw first, then shard, then manifest: a crash leaves the day incomplete and
        # therefore re-fetched, never claimed complete without its data.
        if raw_envelope is not None:
            self._write_raw(sym, day, raw_envelope)
        _atomic_write(self.shard_path(sym, day), payload)
        manifest["symbols"][sym] = {
            "status": STATUS_ACCEPTED,
            "shard": self.shard_path(sym, day).name,
            "row_count": len(ordered),
            "first_bar_start": ordered[0]["bar_start"],
            "last_bar_start": ordered[-1]["bar_start"],
            "content_hash": content_hash,
            "shard_sha256": _sha256(payload),
            "raw_response_hash": (raw_envelope or {}).get("response_content_hash")
            or (entry or {}).get("raw_response_hash"),
            "stored_at": _iso(self.clock()),
        }
        self._write_manifest(day, manifest)
        return PutResult("added" if existing else "written", sym, day, len(ordered), len(new_keys))

    def _write_raw(
        self, symbol: str, day: str, envelope: Mapping[str, Any], *, path: Path | None = None
    ) -> None:
        path = path or self.raw_path(symbol, day)
        header = {k: v for k, v in envelope.items() if k != "raw_rows"}
        lines = [_canonical({"_envelope": header})]
        lines.extend(_canonical(r) for r in envelope.get("raw_rows") or ())
        data = _gz_bytes(lines)
        if path.exists() and path.read_bytes() == data:
            return
        _atomic_write(path, data)

    def _mark_empty(
        self, symbol: str, day: str, status: str, envelope: Mapping[str, Any] | None
    ) -> PutResult:
        manifest = self._manifest_for_update(day)
        entry = manifest["symbols"].get(symbol)
        if entry and entry.get("status") in (STATUS_ACCEPTED, STATUS_QUARANTINED):
            return PutResult("unchanged", symbol, day, int(entry.get("row_count") or 0),
                             reason=f"existing_status_{entry.get('status')}")
        if envelope is not None:
            self._write_raw(symbol, day, envelope)
        manifest["symbols"][symbol] = {
            "status": status, "row_count": 0, "content_hash": None,
            "raw_response_hash": (envelope or {}).get("response_content_hash"),
            "stored_at": _iso(self.clock()),
        }
        self._write_manifest(day, manifest)
        return PutResult(status, symbol, day)

    def mark_non_trading_day(
        self, symbol: str, session_date: str, raw_envelope: Mapping[str, Any] | None = None
    ) -> PutResult:
        return self._mark_empty(_safe_symbol(symbol), _safe_date(session_date),
                                STATUS_NON_TRADING_DAY, raw_envelope)

    def quarantine_symbol_day(
        self,
        symbol: str,
        session_date: str,
        *,
        reason: str,
        detail: Mapping[str, Any] | None = None,
        incoming_rows: Sequence[Mapping[str, Any]] = (),
        raw_envelope: Mapping[str, Any] | None = None,
    ) -> PutResult:
        """Isolate a symbol-day. Existing accepted data is left untouched, never edited."""
        sym, day = _safe_symbol(symbol), _safe_date(session_date)
        record = {
            "schema_version": "tw_price_1m_quarantine.v1",
            "symbol": sym, "session_date": day, "reason": reason, "detail": dict(detail or {}),
            "quarantined_at": _iso(self.clock()), "resolved_at": None, "resolution": None,
            "incoming_row_count": len(incoming_rows),
            "incoming_rows": [dict(r) for r in incoming_rows],
        }
        path = self.quarantine_path(sym, day)
        if path.exists():
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prior = {}
            if prior.get("resolved_at") is None and prior.get("reason") == reason:
                record["quarantined_at"] = prior.get("quarantined_at", record["quarantined_at"])
        manifest = self._manifest_for_update(day)
        previous = manifest["symbols"].get(sym) or {}
        if raw_envelope is not None:
            raw_main = self.raw_path(sym, day)
            incoming_hash = str(raw_envelope.get("response_content_hash") or "")
            if raw_main.exists() and previous.get("status") == STATUS_ACCEPTED:
                # Never overwrite the original raw response that backs the retained
                # shard: keep the incoming one beside it under its own hash.
                suffix = (incoming_hash or _sha256(_canonical(raw_envelope)))[:16]
                conflict_path = raw_main.with_name(f"{sym}.raw.conflict-{suffix}.jsonl.gz")
                self._write_raw(sym, day, raw_envelope, path=conflict_path)
                record["incoming_raw_path"] = str(conflict_path.relative_to(self.root))
            else:
                self._write_raw(sym, day, raw_envelope)
                record["incoming_raw_path"] = str(raw_main.relative_to(self.root))
            record["incoming_raw_response_hash"] = incoming_hash or None
            record["original_raw_response_hash"] = previous.get("raw_response_hash")
        _atomic_write(path, (_canonical(record) + "\n").encode("utf-8"))
        manifest["symbols"][sym] = {
            "status": STATUS_QUARANTINED, "reason": reason,
            "row_count": int(previous.get("row_count") or 0),
            "content_hash": previous.get("content_hash"),
            "shard": previous.get("shard"),
            "raw_response_hash": previous.get("raw_response_hash"),
            "incoming_raw_response_hash": (raw_envelope or {}).get("response_content_hash"),
            "stored_at": _iso(self.clock()),
        }
        self._write_manifest(day, manifest)
        return PutResult("quarantined", sym, day, int(previous.get("row_count") or 0), reason=reason)

    def resolve_quarantine(
        self, symbol: str, session_date: str, *, resolution: str, waived: bool = False
    ) -> dict[str, Any]:
        """Record resolution; the record then expires 90 days later. Does not edit bars."""
        sym, day = _safe_symbol(symbol), _safe_date(session_date)
        path = self.quarantine_path(sym, day)
        if not path.exists():
            raise IntradayStoreError(f"no quarantine record for {sym} {day}")
        if not str(resolution or "").strip():
            raise IntradayStoreError("resolution reason is required")
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(resolved_at=_iso(self.clock()), resolution=resolution, waived=bool(waived))
        _atomic_write(path, (_canonical(record) + "\n").encode("utf-8"))
        return record

    # ---- retention ----
    def _log_deletion(self, entry: Mapping[str, Any]) -> None:
        path = self.root / PRUNE_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(entry) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _delete_dir(self, directory: Path, *, kind: str, session_date: str, manifest_hash: str | None) -> None:
        base = (self.root / "normalized").resolve(), (self.root / "raw").resolve()
        resolved = directory.resolve()
        if directory.is_symlink() or not any(resolved.is_relative_to(b) for b in base):
            raise IntradayStoreError(f"refusing to delete outside store datasets: {directory}")
        if DATASET not in str(resolved.relative_to(self.root.resolve())):
            raise IntradayStoreError(f"refusing to delete non-{DATASET} path: {directory}")
        files = sorted(p.name for p in resolved.iterdir())
        self._log_deletion({
            "deleted_at": _iso(self.clock()), "dataset": DATASET, "kind": kind,
            "session_date": session_date, "path": str(resolved.relative_to(self.root.resolve())),
            "files": files, "manifest_content_hash": manifest_hash,
        })
        shutil.rmtree(resolved)

    def prune(self, *, today: date | None = None) -> dict[str, Any]:
        """Delete accepted days older than the retention window and resolved quarantine
        past its 90-day expiry. Manifest-driven, this dataset only, every deletion logged.
        Unresolved quarantine is never deleted.
        """
        now = self.clock()
        today = today or now.astimezone(timezone.utc).date()
        cutoff = today - timedelta(days=self.retention_days)
        deleted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        q_root = self.root / QUARANTINE_DIR
        if q_root.is_dir():
            for day_dir in sorted(q_root.iterdir()):
                if not day_dir.is_dir() or day_dir.is_symlink():
                    continue
                for rec_path in sorted(day_dir.glob("*.json")):
                    try:
                        record = json.loads(rec_path.read_text(encoding="utf-8"))
                        resolved_at = record.get("resolved_at")
                        if not resolved_at:
                            continue
                        expiry = datetime.fromisoformat(str(resolved_at).replace("Z", "+00:00")) + timedelta(
                            days=QUARANTINE_RESOLVED_EXPIRY_DAYS
                        )
                    except (OSError, ValueError):
                        skipped.append({"path": str(rec_path.relative_to(self.root)), "reason": "unreadable"})
                        continue
                    if now < expiry:
                        continue
                    self._log_deletion({
                        "deleted_at": _iso(now), "dataset": DATASET, "kind": "resolved_quarantine",
                        "session_date": day_dir.name, "path": str(rec_path.relative_to(self.root)),
                        "resolved_at": resolved_at, "reason": record.get("reason"),
                    })
                    rec_path.unlink()
                    deleted.append({"session_date": day_dir.name, "kind": "resolved_quarantine",
                                    "symbol": record.get("symbol")})
                if day_dir.is_dir() and not any(day_dir.iterdir()):
                    day_dir.rmdir()
        norm_root = self.root / NORMALIZED_DIR
        if norm_root.is_dir():
            for child in sorted(norm_root.iterdir()):
                if not child.is_dir() or child.is_symlink():
                    continue
                try:
                    day = date.fromisoformat(child.name)
                except ValueError:
                    skipped.append({"path": child.name, "reason": "not_a_session_date"})
                    continue
                if day >= cutoff:
                    continue
                manifest = self.read_manifest(day.isoformat())
                if manifest is None:
                    skipped.append({"session_date": child.name, "reason": "no_manifest"})
                    continue
                held = [
                    sym for sym, entry in (manifest.get("symbols") or {}).items()
                    if entry.get("status") == STATUS_QUARANTINED
                    and self.quarantine_path(sym, child.name).exists()
                ]
                if held:
                    # Unresolved (or <90 days resolved) quarantine keeps its shard,
                    # raw responses and manifest: the whole day is retained.
                    skipped.append({"session_date": child.name, "reason": "quarantine_retained",
                                    "symbols": sorted(held)})
                    continue
                mhash = manifest.get("content_hash")
                self._delete_dir(child, kind="normalized", session_date=child.name, manifest_hash=mhash)
                raw_dir = self.root / RAW_DIR / child.name
                if raw_dir.is_dir():
                    self._delete_dir(raw_dir, kind="raw", session_date=child.name, manifest_hash=mhash)
                deleted.append({"session_date": child.name, "kind": "accepted_day"})

        return {"cutoff": cutoff.isoformat(), "deleted": deleted, "skipped": skipped}
