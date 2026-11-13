"""Collector bundle adapter (SD-STRATEGY-DISCOVERY §7, task SOURCE-COLLECTOR-BUNDLE-CONNECTOR).

The single door between the separate collector host and Pantheon. The host
publishes bundles (``manifest.json`` + ``items.jsonl`` + ``raw/<sha256>.<ext>``)
behind a bearer token; this adapter is the only code that reads them.

Layout on the host::

    bundles/index.json
    bundles/<source_id>/<yyyy-mm-dd>/<run_id>/manifest.json
    bundles/<source_id>/<yyyy-mm-dd>/<run_id>/items.jsonl
    bundles/<source_id>/<yyyy-mm-dd>/<run_id>/raw/<sha256>.<ext>
    status.json

``index.json``: ``{"bundles": [{"source_id", "date", "run_id", "finished_at"}]}``.
``bundle_sha256`` = sha256 over the sorted item ``content_sha256`` values joined by ``\\n``.

Fail-closed rules:
* any hash / source id / schema mismatch rejects the whole pull; nothing is
  returned and nothing is written (raw copy happens only after every bundle
  verified and every record passed the external source contract);
* every record is research-only, ``redistribution_allowed = false``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence

from services.external_egress import open_external_url

from ..external_sources import validate_external_source_record
from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceEvidenceError,
    SourceMetadata,
    SourceRecord,
    SourceType,
)
from .bounded_reader import read_bounded_response

COLLECTOR_BUNDLE_ADAPTER_TOKEN = "CollectorBundleAdapter.records_from_bundle"
COLLECTOR_BUNDLE_TOKEN_ENV = "PANTHEON_COLLECTOR_BUNDLE_TOKEN"
COLLECTOR_HOST_ENV = "PANTHEON_COLLECTOR_HOST"
# Placeholder until the operator provisions the host (H1); never resolves, so the
# egress canary fails closed while the real host is not configured.
COLLECTOR_HOST_PLACEHOLDER = "collector.pantheon-collector.invalid"
SUPPORTED_BUNDLE_SCHEMA_VERSIONS = frozenset({"collector_bundle.v1"})
BUNDLE_STATUSES = frozenset({"complete", "partial", "paused"})
CURSOR_LAG_DEGRADED_DAYS = 7
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_RAW_BYTES = 32 * 1024 * 1024
COLLECTOR_SOURCE_TYPES = frozenset({"social", "news", "paper", "macro", "filing", "market"})
COLLECTOR_BUNDLE_ROOT_ENV = "PANTHEON_COLLECTOR_BUNDLE_ROOT"

# Configured ingestion owns persistence; tuple adapter callers retain their
# existing eager acknowledgement contract. ContextVar isolates concurrent pulls.
_DEFER_ACK = ContextVar("collector_defer_ack", default=False)
# Whole-operation monotonic deadline for one pull(): priority, ordered sweep, metadata and
# every transport read share it, so one request can never run past the controller boundary.
COLLECTOR_REQUEST_READ_BUDGET_SECONDS = 15.0
# Market raw reads overlap. Metadata and bundle/cursor traversal remain
# ordered; at most four guarded Collector requests are in flight, with no queue
# beyond that batch and no reader surviving the pull.
LATEST_CLOSE_RAW_READ_CONCURRENCY = 4
_OP_DEADLINE: ContextVar[float | None] = ContextVar("collector_op_deadline", default=None)


class _DeadlineBody:
    """Response wrapper: a body stream never outlives the operation deadline.

    Raises TimeoutError between chunks once the deadline passes; partial bytes are never
    returned as data.
    """

    def __init__(self, response: Any, deadline: float) -> None:
        self._response = response
        self._deadline = deadline
        self.headers = getattr(response, "headers", None)

    def read(self, size: int = -1) -> bytes:
        if time.monotonic() >= self._deadline:
            raise TimeoutError("collector response body read exceeded the operation deadline")
        # read1 performs at most one underlying receive, so the deadline is re-checked
        # after every socket receive instead of once per (possibly many-recv) buffered
        # read; the caller loops until EOF and Content-Length truncation is still checked.
        read1 = getattr(self._response, "read1", None)
        if read1 is None:
            return self._response.read(size)
        chunk = read1(size)
        if chunk and time.monotonic() >= self._deadline:
            raise TimeoutError("collector response body read exceeded the operation deadline")
        return chunk


@contextmanager
def operation_deadline(seconds: float | None):
    if seconds is None or _OP_DEADLINE.get() is not None:
        yield
        return
    token = _OP_DEADLINE.set(time.monotonic() + max(seconds, 0.0))
    try:
        yield
    finally:
        _OP_DEADLINE.reset(token)


@contextmanager
def deferred_collector_acknowledgement():
    token = _DEFER_ACK.set(True)
    try:
        yield
    finally:
        _DEFER_ACK.reset(token)


@dataclass(frozen=True)
class CollectorDelivery:
    """In-process verified delivery, never accepted from serialized batch metadata."""

    expected: tuple[SourceRecord, ...]
    acknowledge: Callable[[], None]
    _payloads: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # SourceRecord metadata contains mutable nested mappings. Snapshot the
        # verified payload so an in-place change cannot mutate its own proof.
        object.__setattr__(self, "_payloads", tuple(
            json.dumps(r.to_dict(), sort_keys=True, default=str) for r in self.expected))

    def validate(self, records: Sequence[SourceRecord]) -> None:
        if len(records) != len(self._payloads):
            raise SourceEvidenceError("collector delivery record coverage mismatch")
        for actual, snapshot in zip(records, self._payloads):
            payload = json.loads(json.dumps(actual.to_dict(), sort_keys=True, default=str))
            original = json.loads(snapshot)
            metadata = payload.pop("metadata")
            expected_metadata = original.pop("metadata")
            # The maintained provider wrapper adds these defaults after pull;
            # it cannot overwrite any value already verified by the adapter.
            for key in ("tenant_id", "provider_owned_adapter", "ingest_job", "as_of_date", "dataset", "symbol"):
                if key not in expected_metadata:
                    metadata.pop(key, None)
            if payload != original or metadata != expected_metadata:
                raise SourceEvidenceError("collector delivery identity/hash/PIT/schema mismatch")


def collector_host() -> str:
    return (os.environ.get(COLLECTOR_HOST_ENV) or COLLECTOR_HOST_PLACEHOLDER).strip().lower()


def compute_bundle_sha256(content_hashes: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(sorted(str(h) for h in content_hashes)).encode("utf-8")).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_RFC2822_RE = re.compile(
    r"^(?:(?P<dow>Mon|Tue|Wed|Thu|Fri|Sat|Sun), )?(?P<d>\d{1,2}) "
    r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (?P<y>\d{4}) "
    r"(?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2}) (?:(?P<utc>GMT|UTC?)|(?P<sign>[+-])(?P<oh>\d{2})(?P<om>\d{2}))$"
)
_RFC2822_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_RFC2822_DOWS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _strict_rfc2822_utc(text: str) -> datetime | None:
    """Strictly parse a full RFC 2822 date with GMT/UT/UTC or a 4-digit numeric offset.

    Anything else (no zone, -0000, short/oversized offsets, named zones, trailing text,
    impossible calendar values, weekday mismatch) returns None; never guesses.
    """
    m = _RFC2822_RE.match(text)
    if not m:
        return None
    if m["utc"]:
        offset = timedelta(0)
    else:
        oh, om = int(m["oh"]), int(m["om"])
        if oh > 23 or om > 59 or (m["sign"] == "-" and oh == 0 and om == 0):
            return None
        offset = (1 if m["sign"] == "+" else -1) * timedelta(hours=oh, minutes=om)
    try:
        local = datetime(int(m["y"]), _RFC2822_MONTHS.index(m["mon"]) + 1, int(m["d"]),
                         int(m["H"]), int(m["M"]), int(m["S"]))
    except ValueError:
        return None
    if m["dow"] and _RFC2822_DOWS[local.weekday()] != m["dow"]:
        return None
    return (local - offset).replace(tzinfo=timezone.utc)


def _utc(value: Any, field_name: str, *, allow_rfc2822: bool = False) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise SourceEvidenceError(f"collector bundle {field_name} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        parsed = _strict_rfc2822_utc(text) if allow_rfc2822 else None
        if parsed is None:
            raise SourceEvidenceError(f"collector bundle {field_name} must be ISO-8601: {text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class BundleTransport:
    """Reads one relative path from the collector host."""

    def read(self, relative_path: str, *, max_bytes: int) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class HttpBundleTransport(BundleTransport):
    """Bearer-token HTTPS reader that goes through the guarded egress client."""

    host: str
    token_env: str = COLLECTOR_BUNDLE_TOKEN_ENV
    timeout_seconds: float = 20.0

    def read(self, relative_path: str, *, max_bytes: int) -> bytes:
        token = os.environ.get(self.token_env, "").strip()
        if not token:
            raise SourceEvidenceError(f"collector bundle token env {self.token_env} is not set")
        url = f"https://{self.host}/{relative_path.lstrip('/')}"
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "*/*", "User-Agent": "pantheon-source-ingest/0.1"},
        )
        timeout = self.timeout_seconds
        op_deadline = _OP_DEADLINE.get()
        if op_deadline is not None:
            # A read never outlives the operation budget (1s floor keeps it a real read);
            # the body stream is held to the same floored deadline, chunk by chunk.
            timeout = min(timeout, max(op_deadline - time.monotonic(), 1.0))
            op_deadline = max(op_deadline, time.monotonic() + 1.0)
        with open_external_url(
            request, caller="source_ingest.collector_bundle", timeout=timeout, deadline=op_deadline
        ) as response:
            if op_deadline is not None:
                response = _DeadlineBody(response, op_deadline)
            return read_bounded_response(response, max_bytes=max_bytes)


@dataclass(frozen=True)
class LocalDirectoryBundleTransport(BundleTransport):
    """Reads collector bundles from a local or mounted collector directory."""

    root_dir: Path | str

    def read(self, relative_path: str, *, max_bytes: int) -> bytes:
        p = PurePosixPath(str(relative_path).lstrip("/"))
        if p.is_absolute() or ".." in p.parts or not p.parts:
            raise SourceEvidenceError(f"collector bundle relative path is invalid: {relative_path!r}")
        target = Path(self.root_dir) / Path(*p.parts)
        if not target.is_file():
            raise SourceEvidenceError(f"collector bundle file not found: {target}")
        data = target.read_bytes()
        if len(data) > max_bytes:
            raise SourceEvidenceError(f"collector bundle file exceeds max_bytes={max_bytes}: {target}")
        return data


@dataclass(frozen=True)
class VerifiedBundle:
    source_id: str
    date: str
    run_id: str
    finished_at: datetime
    manifest: Mapping[str, Any]
    items: tuple[Mapping[str, Any], ...]
    manifest_items: Mapping[str, Mapping[str, Any]]
    raw_bytes: Mapping[str, bytes]  # sha256 -> bytes
    cursor: str
    # Absolute items.jsonl row index up to which raw files were verified this pull;
    # None means the whole requested window was verified (no read-budget cut).
    verified_through: int | None = None

    @property
    def status(self) -> str:
        return str(self.manifest.get("status"))


@dataclass(frozen=True)
class CollectorPullResult:
    records: tuple[SourceRecord, ...]
    next_cursor: str | None
    bundle_count: int
    paused_reasons: tuple[str, ...] = ()
    observed: Mapping[str, Any] = field(default_factory=dict)
    feature_rejected: tuple[Mapping[str, str], ...] = ()
    complete: bool = True  # False: the output cap left raw items / derived rows for a later pull
    priority_key: str | None = None  # newest-trading-day bundle delivered ahead of the cursor sweep
    priority_delivered: int = 0  # items of that bundle delivered so far (it may exceed one pull's cap)
    priority_done: bool = False
    priority_held: tuple[str, ...] = ()  # held instrument codes the delivery order was built for
    no_new_data: bool = False  # verified complete boundary agrees with the consumed cursor and host status
    delivery: CollectorDelivery | None = None


PRIORITY_LATEST_DATASETS = frozenset({"tw_price_daily"})
PRIORITY_LATEST_MAX_CADENCE_SECONDS = 3600
# The newest session is delivered whole in one pull (bounded by this ceiling, above any single
# exchange day) so the 30-minute deadline never depends on a second scheduled pull.
PRIORITY_LATEST_MAX_ROWS = 5000
_BUNDLE_TRADE_DATE_RE = re.compile(r"^(?:backfill|close)-(\d{4}-\d{2}-\d{2})")


def _held_codes(symbols: Sequence[str]) -> tuple[str, ...]:
    """Bare instrument codes (``2330.TWSE`` -> ``2330``) of the active paper symbols, sorted."""
    return tuple(sorted({str(s).strip().split(".", 1)[0].upper() for s in symbols if str(s).strip()}))


def _bundle_trade_date(entry: Mapping[str, Any]) -> str:
    """Trade date a price-history bundle covers: backfill/close run ids name it, others use the run date."""
    match = _BUNDLE_TRADE_DATE_RE.match(str(entry.get("run_id") or ""))
    return match.group(1) if match else str(entry.get("date") or "")


def _cursor_key(finished_at: datetime, run_id: str) -> str:
    return f"{_iso(finished_at)}#{run_id}"


_PARTIAL_CURSOR_MARK = "|items="


def _split_cursor(cursor: str | None) -> tuple[str | None, int]:
    """Split ``<key>|items=<n>`` (a bundle partially consumed under the output cap)."""
    if not cursor or _PARTIAL_CURSOR_MARK not in cursor:
        return cursor, 0
    base, _, raw_n = cursor.rpartition(_PARTIAL_CURSOR_MARK)
    try:
        n = int(raw_n)
    except ValueError as exc:
        raise SourceEvidenceError(f"collector cursor has an invalid partial item offset: {cursor!r}") from exc
    if n < 0 or not base:
        raise SourceEvidenceError(f"collector cursor has an invalid partial item offset: {cursor!r}")
    return base, n


def _safe_raw_path(raw_path: str) -> str:
    p = PurePosixPath(str(raw_path))
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != "raw" or len(p.parts) != 2:
        raise SourceEvidenceError(f"collector bundle raw_path must be raw/<sha256>.<ext>: {raw_path!r}")
    return p.as_posix()


def _loads(data: bytes, what: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SourceEvidenceError(f"collector bundle {what} is not valid JSON") from exc


def verify_bundle(
    transport: BundleTransport,
    *,
    expected_source_id: str,
    entry: Mapping[str, Any],
    item_window: tuple[int, int] | None = None,
    deadline: float | None = None,
    priority_codes: Collection[str] = (),
    raw_read_concurrency: int = 1,
) -> VerifiedBundle:
    """Fetch and verify one bundle; raises on any mismatch, returns nothing partial.

    ``item_window`` (start, stop over items.jsonl rows) bounds per-pull transport work:
    manifest, items.jsonl, id coverage and bundle_sha256 (declared hashes) are always
    verified, but raw files are fetched and hash/size checked only for the windowed rows.
    Rows outside the window are verified when a later pull's window reaches them.

    ``priority_codes`` (instrument codes such as ``2330``) reorder ``items``/the window so the
    rows whose item id ends in ``-<code>`` come first (stable, otherwise manifest order); every
    raw file is still fetched and hash/size verified, only the order of verification changes.

    ``deadline`` (monotonic seconds) is a whole-operation read budget: once it passes, no
    further raw file is fetched (at least one is always read so every pull progresses) and
    ``verified_through`` records how far verification got. Nothing is skipped or assumed.

    ``raw_read_concurrency`` overlaps at most four reads per bounded batch for
    newest closes. Every reader shares the operation context/deadline and joins
    before return; a timeout can retain only an ordered verified prefix.
    """

    if deadline is None:
        deadline = _OP_DEADLINE.get()
    source_id = str(entry.get("source_id") or "").strip()
    date = str(entry.get("date") or "").strip()
    run_id = str(entry.get("run_id") or "").strip()
    if not (source_id and date and run_id) or any("/" in x or ".." in x for x in (source_id, date, run_id)):
        raise SourceEvidenceError(f"collector bundle index entry is malformed: {dict(entry)!r}")
    base = f"bundles/{source_id}/{date}/{run_id}"
    manifest = _loads(transport.read(f"{base}/manifest.json", max_bytes=MAX_JSON_BYTES), "manifest.json")
    if not isinstance(manifest, dict):
        raise SourceEvidenceError("collector bundle manifest must be an object")

    if str(manifest.get("source_id") or "") != expected_source_id or source_id != expected_source_id:
        raise SourceEvidenceError(
            f"collector bundle source_id mismatch: manifest={manifest.get('source_id')!r} "
            f"index={source_id!r} expected={expected_source_id!r}"
        )
    if str(manifest.get("run_id") or "") != run_id:
        raise SourceEvidenceError("collector bundle run_id does not match its index entry")
    if str(manifest.get("schema_version") or "") not in SUPPORTED_BUNDLE_SCHEMA_VERSIONS:
        raise SourceEvidenceError(f"collector bundle schema_version is not supported: {manifest.get('schema_version')!r}")
    status = str(manifest.get("status") or "")
    if status not in BUNDLE_STATUSES:
        raise SourceEvidenceError(f"collector bundle status must be one of {sorted(BUNDLE_STATUSES)}: {status!r}")
    if "status" in entry and entry["status"] != status:
        raise SourceEvidenceError("collector bundle status does not match its index entry")
    finished_at = _utc(manifest.get("finished_at"), "finished_at")
    if finished_at != _utc(entry.get("finished_at"), "index finished_at"):
        raise SourceEvidenceError("collector bundle finished_at does not match its index entry")
    _utc(manifest.get("started_at"), "started_at")
    for required in ("terms_ref", "robots_decision", "collector_version", "fetch_policy_hash"):
        if not str(manifest.get(required) or "").strip():
            raise SourceEvidenceError(f"collector bundle manifest.{required} is required")

    manifest_items_raw = manifest.get("items")
    if not isinstance(manifest_items_raw, list):
        raise SourceEvidenceError("collector bundle manifest.items must be a list")
    manifest_items: dict[str, Mapping[str, Any]] = {}
    for item in manifest_items_raw:
        item_id = str(item.get("item_id") or "").strip() if isinstance(item, dict) else ""
        if not item_id or item_id in manifest_items:
            raise SourceEvidenceError("collector bundle manifest.items need unique item_id values")
        manifest_items[item_id] = item

    if compute_bundle_sha256([str(i["content_sha256"]).lower() for i in manifest_items.values()]) != str(
        manifest.get("bundle_sha256") or ""
    ).lower():
        raise SourceEvidenceError("collector bundle bundle_sha256 mismatch")

    items_text = transport.read(f"{base}/items.jsonl", max_bytes=MAX_JSON_BYTES).decode("utf-8")
    items: list[Mapping[str, Any]] = []
    for line in items_text.splitlines():
        if line.strip():
            row = _loads(line.encode("utf-8"), "items.jsonl line")
            if not isinstance(row, dict):
                raise SourceEvidenceError("collector bundle items.jsonl lines must be objects")
            items.append(row)
    item_ids = [str(row.get("id") or "").strip() for row in items]
    if sorted(item_ids) != sorted(manifest_items) or len(set(item_ids)) != len(item_ids):
        raise SourceEvidenceError("collector bundle items.jsonl ids do not match manifest.items")

    if priority_codes:
        codes = {str(c).strip().upper() for c in priority_codes if str(c).strip()}
        first = [
            r for r in items
            if str(r.get("id") or "").rsplit("-", 1)[-1].upper() in codes
            or str(r.get("symbol") or "").strip().upper() in codes
        ]
        if first:
            lead = {id(r) for r in first}
            items = first + [r for r in items if id(r) not in lead]
            item_ids = [str(row.get("id") or "").strip() for row in items]

    if item_window is None:
        wanted_ids = list(manifest_items)
    else:
        wanted_ids = item_ids[max(item_window[0], 0) : max(item_window[1], 0)]
    raw_bytes: dict[str, bytes] = {}
    verified_through: int | None = None
    window_start = max(item_window[0], 0) if item_window is not None else 0
    def read_verified(item_id: str) -> tuple[str, bytes]:
        item = manifest_items[item_id]
        declared = str(item.get("content_sha256") or "").strip().lower()
        raw_path = _safe_raw_path(str(item.get("raw_path") or ""))
        data = transport.read(f"{base}/{raw_path}", max_bytes=MAX_RAW_BYTES)
        actual = _sha256(data)
        if declared != actual:
            raise SourceEvidenceError(f"collector bundle raw hash mismatch for item {item_id}: declared={declared} actual={actual}")
        if "bytes" in item and int(item["bytes"]) != len(data):
            raise SourceEvidenceError(f"collector bundle raw size mismatch for item {item_id}")
        return actual, data

    width = max(1, min(raw_read_concurrency, LATEST_CLOSE_RAW_READ_CONCURRENCY))
    # A separate context per worker preserves the *same* operation deadline and
    # egress policy. Never acknowledge out of order or discard a genuine failure
    # from a later parallel read merely because an earlier read timed out.
    with (ThreadPoolExecutor(max_workers=width) if width > 1 else nullcontext()) as readers:
        for offset in range(0, len(wanted_ids), width):
            if deadline is not None and offset > 0 and time.monotonic() >= deadline:
                verified_through = window_start + offset
                break
            group = wanted_ids[offset:offset + width]
            futures = ([readers.submit(copy_context().run, read_verified, item_id) for item_id in group]
                       if readers is not None else [None])
            outcomes: list[tuple[str, bytes] | TimeoutError] = []
            for future in futures:
                try:
                    outcomes.append(future.result() if future is not None else read_verified(group[0]))
                except TimeoutError as exc:
                    outcomes.append(exc)
            for index, outcome in enumerate(outcomes):
                if isinstance(outcome, TimeoutError):
                    # Only a contiguous verified prefix can survive a deadline cut.
                    # The missing item and any later prefetched items replay next pull.
                    if offset + index > 0 and deadline is not None and time.monotonic() >= deadline:
                        verified_through = window_start + offset + index
                        break
                    raise outcome
                actual, data = outcome
                raw_bytes[actual] = data
            if verified_through is not None:
                break

    return VerifiedBundle(
        source_id=source_id,
        date=date,
        run_id=run_id,
        finished_at=finished_at,
        manifest=manifest,
        items=tuple(items),
        manifest_items=manifest_items,
        raw_bytes=raw_bytes,
        cursor=_cursor_key(finished_at, run_id),
        verified_through=verified_through,
    )


def observed_from_collector_status(
    status_doc: Mapping[str, Any] | None,
    *,
    source_id: str,
    cursor: str | None,
    cadence_seconds: int | None = None,
    now: datetime | None = None,
    verified_bundle: VerifiedBundle | None = None,
    delivery_complete: bool = False,
) -> dict[str, Any]:
    """Project the host ``status.json`` + our cursor into health fields.

    Returns ``{"health_state", "reasons", "freshness", "last_run"}`` for
    ``SourceObservedState``. ``degraded`` when the source is paused/failing on
    the host, the newest bundle is older than twice its cadence, or our cursor
    lags the host's newest bundle by more than seven days.
    Producer ``complete`` additionally requires the verified latest bundle,
    full delivery coverage, matching run/time/cursor and a known fresh cadence.
    """

    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    entry: Mapping[str, Any] = {}
    sources = (status_doc or {}).get("sources") if isinstance(status_doc, Mapping) else None
    if isinstance(sources, Mapping):
        entry = sources.get(source_id) or {}
    elif isinstance(sources, list):
        entry = next((s for s in sources if isinstance(s, Mapping) and s.get("source_id") == source_id), {})
    if not isinstance(entry, Mapping):
        raise SourceEvidenceError("collector status source entry must be an object")
    if not entry:
        reasons.append("collector_status_missing_source")

    host_status = str(entry.get("status") or "")
    if host_status in {"paused", "failed", "failing", "unavailable"}:
        reasons.append(f"collector_source_{host_status}" + (f":{entry.get('paused_reason')}" if entry.get("paused_reason") else ""))
    elif host_status not in {"ok", "healthy", "complete"}:
        reasons.append(f"collector_source_status_unverified:{host_status or 'missing'}")

    last_run_at = None
    if entry.get("last_run_at"):
        last_run_at = _utc(entry["last_run_at"], "status.last_run_at")
    else:
        reasons.append("collector_last_run_missing")
    cadence = int(cadence_seconds or entry.get("cadence_seconds") or 0)
    if last_run_at and cadence and now - last_run_at > timedelta(seconds=2 * cadence):
        reasons.append("newest_bundle_older_than_twice_cadence")

    if host_status == "complete":
        # BundleWriter publishes completion of one exact run, not a blanket
        # health assertion. A status-only observation has no verified delivery.
        if (verified_bundle is None or verified_bundle.source_id != source_id
                or verified_bundle.status != "complete" or not delivery_complete
                or cursor != verified_bundle.cursor):
            reasons.append("collector_complete_boundary_unverified")
        if verified_bundle is not None:
            if last_run_at != verified_bundle.finished_at:
                reasons.append("collector_complete_time_mismatch")
            if entry.get("last_run_id") != verified_bundle.run_id:
                reasons.append("collector_complete_run_mismatch")
        if cadence <= 0:
            reasons.append("collector_complete_cadence_missing")
        if last_run_at and last_run_at > now:
            reasons.append("collector_last_run_in_future")
        if entry.get("paused_reason"):
            reasons.append(f"collector_source_paused:{entry['paused_reason']}")

    lag_days: float | None = None
    cursor_at = None
    if cursor:
        cursor_at = _utc(cursor.split("#", 1)[0], "cursor")
    if cursor_at and last_run_at:
        lag_days = max((last_run_at - cursor_at).total_seconds(), 0.0) / 86400
        if lag_days > CURSOR_LAG_DEGRADED_DAYS:
            reasons.append("cursor_lag_over_7_days")
    elif cursor_at is None and last_run_at and now - last_run_at > timedelta(days=CURSOR_LAG_DEGRADED_DAYS):
        reasons.append("cursor_lag_over_7_days")

    return {
        "health_state": "degraded" if reasons else "healthy",
        "reasons": tuple(reasons),
        "freshness": {
            "collector_last_run_at": _iso(last_run_at) if last_run_at else None,
            "cursor": cursor,
            "cursor_lag_days": round(lag_days, 3) if lag_days is not None else None,
            "cadence_seconds": cadence or None,
        },
        "last_run": {"collector_status": host_status or None, "paused_reason": entry.get("paused_reason")},
    }


def source_offering(status_doc: Mapping[str, Any] | None, source_id: str) -> dict[str, Any]:
    """Additive host admission. Legacy status without offered retains its old behaviour."""
    sources = (status_doc or {}).get("sources", {})
    if isinstance(sources, list):
        entry = next((s for s in sources if isinstance(s, Mapping) and s.get("source_id") == source_id), {})
    else:
        entry = sources.get(source_id, {}) if isinstance(sources, Mapping) else {}
    if not isinstance(entry, Mapping) or not isinstance(entry.get("offered"), bool):
        return {"offered": None, "reason": "collector_offering_unverified"}
    return {"offered": entry["offered"], "reason": entry.get("offered_reason")}


@dataclass(frozen=True)
class CollectorBundleAdapter:
    """Pull, verify and map collector bundles for one collector source."""

    connector_id: str
    source_id: str = ""
    source_type: str = "news"
    platform: str | None = None
    publisher: str | None = None
    trust_score: float = 0.3
    attribution_required: bool = False
    license_scope: str = "collector_public_web"
    cadence_seconds: int = 86400
    dataset: str = "collector_bundle_items"
    host: str | None = None
    raw_root: str | None = None
    max_records: int = 500
    # Whole-pull raw read budget, kept below the controller's per-request timeout; the
    # remainder stays pending behind a partial cursor. None disables the budget.
    read_budget_seconds: float | None = COLLECTOR_REQUEST_READ_BUDGET_SECONDS
    transport: BundleTransport | None = None
    # Venue-qualified instruments (e.g. ``2330.TWSE``) that bare social symbols may resolve to,
    # in addition to the symbols that have stored daily bars under the storage roots.
    known_instruments: tuple[str, ...] = ()
    # Extra provider-series mappings: (provider_series_id, dataset, field, source, period_kind).
    macro_series_map: tuple[tuple[str, str, str, str, str], ...] = ()
    # Injectable clock for ingest-time stamping (tests, replays); defaults to UTC now.
    clock: Callable[[], datetime] | None = None

    def __post_init__(self) -> None:
        if self.source_type not in COLLECTOR_SOURCE_TYPES:
            raise SourceEvidenceError(f"collector source_type must be one of {sorted(COLLECTOR_SOURCE_TYPES)}")
        if not (self.source_id or "").strip():
            raise SourceEvidenceError("collector source_id is required")

    # -- SourceConnectorProvider ------------------------------------------------
    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type=SourceType(self.source_type),
            provider=f"collector:{self.source_id}",
            license_scope=self.license_scope,
            auth_type=AuthType.NONE,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE, secret_ref=f"env://{COLLECTOR_BUNDLE_TOKEN_ENV}"),
            license_policy=LicensePolicy(
                license_scope=self.license_scope,
                allowed_use=("research", "search_index", "experiment"),
                attribution_required=self.attribution_required,
                redistribution_allowed=False,
                policy_ref=f"collector-terms://{self.source_id}",
            ),
            rate_limit_policy=RateLimitPolicy(requests_per_minute=30, burst=5, concurrency=1,
                                              policy_ref="source-ingest://policy/collector-bundle-pull-v1"),
            source_metadata=SourceMetadata(display_name=f"Collector {self.source_id}", owner="collector-host",
                                           tags=("collector", self.source_type, "research_only")),
            metadata={
                "source_class": self.source_type,
                "entitlement_tags": [f"{self.source_type}-research"],
                "access_scope": ["research"],
                "allowed_use": ["research", "search_index", "experiment"],
                "allowed_host_patterns": [self.host or collector_host()],
                **self._feature_routing(),
                "redistribution_allowed": False,
                "governance": {"direct_execution_allowed": False, "canonical_sink": "SourceRecord/EvidenceBundle"},
            },
        )

    def _feature_routing(self) -> dict[str, Any]:
        """Derived governed feature dataset routing; raw items stay a raw-only dataset."""
        derived = (
            TW_MACRO_SERIES_DATASET
            if self.dataset == TW_MACRO_SERIES_DATASET or self.source_type == "macro"
            else TW_COMMUNITY_DAILY_DATASET
            if self.dataset == TW_COMMUNITY_DAILY_DATASET or self.source_type == "social"
            else None
        )
        if derived is None:
            return {}
        original = [self.dataset if self.dataset != derived else "collector_bundle_items"]
        return {
            "normalized_datasets": [derived, *original],
            "feature_targets": [derived],
            "raw_only_datasets": original,
            "storage_targets": [f"normalized/{derived}", f"features/{derived}"],
        }

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN,
            "adapter_config": {"source_id": self.source_id, "max_records": self.max_records},
            "request": {},
            "next_watermark": None,
            "max_records": self.max_records,
        }

    # -- pull ------------------------------------------------------------------
    def _transport(self) -> BundleTransport:
        return self.transport or HttpBundleTransport(host=self.host or collector_host())

    def read_status(self) -> Mapping[str, Any] | None:
        try:
            doc = _loads(self._transport().read("status.json", max_bytes=MAX_JSON_BYTES), "status.json")
        except SourceEvidenceError:
            raise
        except Exception:  # transport failure: health shows missing status, pull proceeds to its own error
            return None
        return doc if isinstance(doc, dict) else None

    def pull(
        self,
        *,
        cursor: str | None = None,
        trace_id: str = "",
        max_records: int | None = None,
        priority_symbols: Sequence[str] = (),
    ) -> CollectorPullResult:
        """Cursor-ordered pull; price-history sources deliver the newest trading day first.

        The cursor sweep is oldest-first, so a two-year backfill would hold the newest close
        behind hundreds of older per-date bundles. For ``tw_price_daily`` the newest-trade-date
        bundle is verified and returned ahead of the sweep (its key is persisted separately), and
        the cursor sweep continues with whatever output cap remains. Replays are idempotent.
        """

        with operation_deadline(self.read_budget_seconds):
            return self._pull_bounded(
                cursor=cursor, trace_id=trace_id, max_records=max_records, priority_symbols=priority_symbols
            )

    def _pull_bounded(
        self,
        *,
        cursor: str | None = None,
        trace_id: str = "",
        max_records: int | None = None,
        priority_symbols: Sequence[str] = (),
    ) -> CollectorPullResult:
        total_cap = self.max_records if max_records is None else max(min(self.max_records, int(max_records)), 0)
        priority = self._pull_priority_latest(
            cursor, trace_id=trace_id, total_cap=total_cap, held=_held_codes(priority_symbols)
        )
        if priority is None:
            return self._pull_ordered(cursor=cursor, trace_id=trace_id, max_records=max_records)
        records, key, paused, delivered, done, held = priority
        remaining = total_cap - len(records)
        op_deadline = _OP_DEADLINE.get()
        base = None
        if remaining > 0 and not (op_deadline is not None and time.monotonic() >= op_deadline):
            try:
                base = self._pull_ordered(cursor=cursor, trace_id=trace_id, max_records=remaining)
            except TimeoutError:
                # The shared deadline cut the ordered sweep before any of its rows verified:
                # keep the already verified priority prefix; ordered work stays pending.
                if op_deadline is None or time.monotonic() < op_deadline:
                    raise
        if base is None:
            base = CollectorPullResult(
                records=(), next_cursor=cursor, bundle_count=0, complete=False,
                observed=observed_from_collector_status(
                    self.read_status(), source_id=self.source_id, cursor=cursor, cadence_seconds=self._health_cadence_seconds()
                ),
            )
        seen = {r.source_id for r in records}
        merged = (*records, *(r for r in base.records if r.source_id not in seen))
        result = CollectorPullResult(
            records=merged,
            next_cursor=base.next_cursor,
            bundle_count=base.bundle_count + 1,
            paused_reasons=(*paused, *base.paused_reasons),
            # Newest-session rows still undelivered: the controller may re-pull before the
            # finished-source cadence. Only this pull's own acknowledged outcome carries it.
            observed=({**base.observed, "continuation_pending": True}
                      if merged and (not done or not base.complete) and not paused and not base.paused_reasons
                      else base.observed),
            feature_rejected=base.feature_rejected,
            complete=base.complete,
            no_new_data=not merged and not paused and base.no_new_data,
            priority_key=key,
            priority_delivered=delivered,
            priority_done=done,
            priority_held=held,
        )
        if _DEFER_ACK.get():
            def acknowledge() -> None:
                # The ordered part may have verified a complete persisted
                # boundary. Composing priority rows must retain its native ack;
                # otherwise the next due pull rejects the consumed cursor.
                if base.delivery is not None:
                    base.delivery.acknowledge()
                self.save_resume_cursor(result)
            result = replace(result, delivery=CollectorDelivery(result.records, acknowledge))
        return result

    def _priority_latest_enabled(self) -> bool:
        # A sub-hourly Pantheon pull cadence (price-history definitions: 30 min) means the newest
        # session must not wait behind the oldest-first backfill sweep; daily cadence keeps the
        # plain cursor order.
        return (
            self.source_type == "market"
            and self.dataset in PRIORITY_LATEST_DATASETS
            and 0 < self.cadence_seconds <= PRIORITY_LATEST_MAX_CADENCE_SECONDS
        )

    def _health_cadence_seconds(self) -> int | None:
        # The Pantheon pull cadence of a price-history source (30 min) is faster than the collector's
        # own run cadence; staleness is judged against the host's cadence from status.json.
        if self.source_type == "market" and self.dataset in PRIORITY_LATEST_DATASETS:
            return None
        return self.cadence_seconds

    def _priority_key_path(self) -> Path:
        return self._raw_root() / "state" / _slug(self.connector_id) / "latest_priority.json"

    def _load_priority_state(self) -> tuple[str | None, int, bool, tuple[str, ...]]:
        path = self._priority_key_path()
        try:
            doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError):
            return None, 0, False, ()
        if isinstance(doc, dict) and doc.get("source_id") == self.source_id and doc.get("key"):
            held = doc.get("held")
            return (str(doc["key"]), int(doc.get("delivered") or 0), bool(doc.get("done")),
                    tuple(str(c) for c in held) if isinstance(held, list) else ())
        return None, 0, False, ()

    def _pull_priority_latest(
        self, cursor: str | None, *, trace_id: str, total_cap: int, held: tuple[str, ...] = ()
    ) -> tuple[list[SourceRecord], str, tuple[str, ...], int, bool, tuple[str, ...]] | None:
        if (
            not self._priority_latest_enabled()
            or self.source_type != "market"
            or self.dataset not in PRIORITY_LATEST_DATASETS
            or total_cap <= 0
        ):
            return None
        transport = self._transport()
        index = _loads(transport.read("bundles/index.json", max_bytes=MAX_JSON_BYTES), "index.json")
        entries = index.get("bundles") if isinstance(index, dict) else None
        if not isinstance(entries, list):
            raise SourceEvidenceError("collector bundle index.json must contain a bundles list")
        mine = [
            e for e in entries
            if isinstance(e, dict) and str(e.get("source_id") or "") == self.source_id
            and str(e.get("status") or "complete") == "complete"
        ]
        if not mine:
            return None
        newest = max(
            mine,
            key=lambda e: (
                _bundle_trade_date(e),
                _cursor_key(_utc(e.get("finished_at"), "index finished_at"), str(e.get("run_id") or "")),
            ),
        )
        key = _cursor_key(_utc(newest.get("finished_at"), "index finished_at"), str(newest.get("run_id") or ""))
        base_cursor, _ = _split_cursor(cursor)
        if base_cursor is not None and (key < base_cursor or (key == base_cursor and _PARTIAL_CURSOR_MARK not in str(cursor))):
            return None  # the sweep already consumed it
        state_key, delivered, done, state_held = self._load_priority_state()
        if state_key is not None:
            if key < state_key or (key == state_key and done):
                return None
            if key > state_key or state_held != held:
                # New session, or the held set changed so the persisted prefix no longer names
                # the same rows: restart this session's order (idempotent replay, never a skip).
                delivered = 0
        else:
            delivered = 0
        bundle = verify_bundle(
            transport, expected_source_id=self.source_id, entry=newest, item_window=(delivered, delivered + total_cap),
            priority_codes=held,
            raw_read_concurrency=LATEST_CLOSE_RAW_READ_CONCURRENCY,
        )
        if bundle.status == "paused":
            return [], key, (f"{bundle.run_id}:{bundle.manifest.get('paused_reason') or 'paused'}",), delivered, False, held
        stop = delivered + total_cap if bundle.verified_through is None else bundle.verified_through
        window = bundle.items[delivered:stop]
        rows = [self._record(bundle, row, trace_id=trace_id) for row in window]
        self._write_raw([(bundle, {})], rows)
        new_delivered = delivered + len(window)
        return rows, key, (), new_delivered, new_delivered >= len(bundle.items), held

    def _pull_ordered(
        self,
        *,
        cursor: str | None = None,
        trace_id: str = "",
        max_records: int | None = None,
    ) -> CollectorPullResult:
        transport = self._transport()
        index = _loads(transport.read("bundles/index.json", max_bytes=MAX_JSON_BYTES), "index.json")
        entries = index.get("bundles") if isinstance(index, dict) else None
        if not isinstance(entries, list):
            raise SourceEvidenceError("collector bundle index.json must contain a bundles list")

        base_cursor, partial_skip = _split_cursor(cursor)
        partial_cursor = _PARTIAL_CURSOR_MARK in str(cursor)
        pending: list[tuple[datetime, str, Mapping[str, Any]]] = []
        source_entries: list[tuple[str, Mapping[str, Any]]] = []
        for entry in entries:
            if not isinstance(entry, dict) or str(entry.get("source_id") or "") != self.source_id:
                continue
            finished = _utc(entry.get("finished_at"), "index finished_at")
            key = _cursor_key(finished, str(entry.get("run_id") or ""))
            source_entries.append((key, entry))
            if base_cursor is None or key > base_cursor or (partial_cursor and key == base_cursor):
                pending.append((finished, key, entry))
        pending.sort(key=lambda t: t[1])

        # Bound per-pull verification work to what the output cap can return: verify bundles
        # lazily and only fetch raw files for the rows that can fit under the cap. Later
        # rows/bundles are verified by the pulls that reach them (persisted resume cursor).
        total_cap = self.max_records if max_records is None else max(min(self.max_records, int(max_records)), 0)
        verified: list[VerifiedBundle] = []
        window_stops: dict[str, int] = {}
        remaining = max(total_cap, 1)
        unverified_pending = False
        deadline = _OP_DEADLINE.get()
        if deadline is None and self.read_budget_seconds is not None:
            deadline = time.monotonic() + max(self.read_budget_seconds, 0.0)
        for _, key, entry in pending:
            if verified and deadline is not None and time.monotonic() >= deadline:
                # Budget spent between bundles: the rest stays pending behind the cursor.
                unverified_pending = True
                break
            if remaining <= 0:
                unverified_pending = True
                break
            skip = partial_skip if key == base_cursor else 0
            try:
                bundle = verify_bundle(
                    transport, expected_source_id=self.source_id, entry=entry, item_window=(skip, skip + remaining),
                    deadline=deadline,
                    raw_read_concurrency=LATEST_CLOSE_RAW_READ_CONCURRENCY if self.source_type == "market" else 1,
                )
            except TimeoutError:
                # A later bundle cut by the shared deadline before any of its rows verified
                # keeps the already verified prefix; that bundle stays pending (no skip, no
                # forged completion). With nothing verified yet, the timeout is not progress.
                if verified and deadline is not None and time.monotonic() >= deadline:
                    unverified_pending = True
                    break
                raise
            verified.append(bundle)
            window_stops[bundle.cursor] = skip + remaining
            if bundle.verified_through is not None:
                # Read budget exhausted mid-window: later rows/bundles stay pending.
                unverified_pending = True
                break
            if bundle.status in {"paused", "partial"}:
                # Material after an unresolved bundle must remain pending.
                unverified_pending = True
                break
            if bundle.status != "paused":
                remaining -= max(len(bundle.items) - skip, 0)

        all_records: list[SourceRecord] = []
        paused: list[str] = []
        for bundle in verified:
            if bundle.status == "paused":
                paused.append(f"{bundle.run_id}:{bundle.manifest.get('paused_reason') or 'paused'}")
                break
            skip = partial_skip if bundle.cursor == base_cursor else 0
            stop = min(len(bundle.items), window_stops[bundle.cursor])
            if bundle.verified_through is not None:
                stop = min(stop, bundle.verified_through)
            for row in bundle.items[skip:stop]:
                all_records.append(self._record(bundle, row, trace_id=trace_id))

        # The caller's output cap covers raw + derived feature records together. Shrink the
        # raw budget until both fit; derived rows are acknowledged (ledger / emission
        # markers) only if actually returned, so a capped expansion stays pending.
        budget = total_cap
        while True:
            records, next_cursor, raw_plan = self._select_raw(
                verified, all_records, cursor, budget, skips={base_cursor: partial_skip} if partial_skip else {}
            )
            cut = verified[-1] if verified and verified[-1].verified_through is not None else None
            if cut is not None and len(records) == len(all_records):
                # Everything verified is returned; resume exactly where the budget stopped.
                next_cursor = f"{cut.cursor}{_PARTIAL_CURSOR_MARK}{cut.verified_through}"
            plan = self._plan_governed_features(records)
            # Already-acknowledged replays are idempotent and droppable; only pending
            # rows must fit alongside the raw items.
            pending = sum(1 for r in plan.rows if not plan.is_acked(r))
            overflow = pending + len(records) - total_cap
            if overflow <= 0 or budget <= 0:
                break
            budget = max(min(budget - 1, total_cap - pending), 0)
        if not records and all_records and budget < total_cap:
            # Even one bundle's expansion cannot fit alongside its raw items. Persist its
            # posts to the durable ledger and return only the (capped) derived rows; raw
            # items and the cursor stay put, and the unacknowledged rows flush on later
            # pulls until the bundle's raw items fit.
            first = next((b for b in verified if b.status != "paused"), None)
            if first is not None:
                first_skip = partial_skip if first.cursor == base_cursor else 0
                # Only the raw window verified by this pull may contribute to
                # derived rows, even when a bundle exceeds the public cap.
                candidate = list(all_records[: min(max(len(first.items) - first_skip, 0), total_cap)])
                plan = self._plan_governed_features(candidate)
        limit = max(total_cap - len(records), 0)
        selected = self._select_feature_rows(plan, limit)
        if _DEFER_ACK.get():
            feature_records = feature_rows_to_records(selected, connector_id=self.connector_id,
                                                     trace_id=trace_id, license_scope=self.license_scope)
            feature_rejected = plan.rejected
        else:
            feature_records, feature_rejected = self._commit_governed_features(plan, limit=limit, trace_id=trace_id)
        # Cache the verified window even for a feature-only capped delivery.
        # Its provenance must already resolve; this cache is not a cursor ack.
        self._write_raw([(b, {}) for b in verified if b.status != "paused"], records)
        pending_rows = sum(1 for r in plan.rows if not plan.is_acked(r))
        complete = not unverified_pending and (not verified or next_cursor == verified[-1].cursor) and max(total_cap - len(records), 0) >= pending_rows
        latest_key, latest_entry = max(source_entries, key=lambda pair: pair[0]) if source_entries else (None, None)
        boundary = verified[-1] if verified and verified[-1].cursor == latest_key else None
        if not records and not feature_records and not verified and cursor == latest_key and cursor and not partial_cursor:
            # The cache alone cannot acknowledge a configured delivery; the
            # persistence owner must also have committed this exact boundary.
            boundary = verify_bundle(transport, expected_source_id=self.source_id,
                                     entry=latest_entry, item_window=(0, 0))
        observed = observed_from_collector_status(
            self.read_status(), source_id=self.source_id, cursor=next_cursor,
            cadence_seconds=self._health_cadence_seconds(), verified_bundle=boundary,
            delivery_complete=complete and not feature_rejected,
        )
        no_new_data = False
        if not records and not feature_records and not verified and source_entries and cursor and not partial_cursor:
            # Revalidate the exact consumed boundary over the guarded transport. No raw
            # rows are fetched here: the acknowledged cursor represents their previous
            # consumption; manifest hashes and item coverage are verified again.
            if cursor == latest_key:
                if boundary.status == "paused":
                    paused.append(f"{boundary.run_id}:{boundary.manifest.get('paused_reason') or 'paused'}")
                no_new_data = (
                    boundary.status == "complete" and boundary.cursor == cursor and complete
                    and observed["health_state"] == "healthy"
                    and observed["freshness"]["collector_last_run_at"] == _iso(boundary.finished_at)
                    and not feature_rejected
                    and self._has_verified_boundary(boundary, persisted_only=_DEFER_ACK.get())
                )
                if not no_new_data:
                    observed = {**observed, "health_state": "degraded",
                                "reasons": (*observed["reasons"], "collector_consumed_boundary_unverified")}
        if paused:
            observed = {**observed, "health_state": "degraded",
                        "reasons": (*observed["reasons"], *(f"collector_bundle_paused:{r}" for r in paused))}
        if (self.source_type == "market" and records and not complete and not paused
                and all(b.status == "complete" for b in verified)):
            # A verified prefix cut by the output/deadline budget is ready to continue.
            # Producer partial/paused bundles wait for cadence rather than retrying hot.
            observed = {**observed, "continuation_pending": True}
        result = CollectorPullResult(
            records=(*feature_records, *records),
            next_cursor=next_cursor,
            bundle_count=len(verified),
            paused_reasons=tuple(paused),
            observed=observed,
            feature_rejected=feature_rejected,
            complete=complete,
            no_new_data=no_new_data,
        )
        if _DEFER_ACK.get():
            def acknowledge() -> None:
                self._commit_governed_features(replace(plan, rows=selected), limit=None,
                                               trace_id=trace_id, persisted=True)
                if complete and boundary is not None and next_cursor == boundary.cursor and not feature_rejected:
                    self._acknowledge_boundary(boundary)
                self.save_resume_cursor(result)
            result = replace(result, delivery=CollectorDelivery(result.records, acknowledge))
        return result

    # -- durable resume cursor (capped public pulls) -------------------------------
    def _resume_cursor_path(self) -> Path:
        return self._raw_root() / "state" / _slug(self.connector_id) / "resume_cursor.json"

    def load_resume_cursor(self) -> str | None:
        if _DEFER_ACK.get():
            # Only the schedule store proves configured ingestion committed.
            # Legacy adapter cursors may have been saved by a failed raw job.
            return None
        path = self._resume_cursor_path()
        if not path.is_file():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        value = doc.get("cursor") if isinstance(doc, dict) and doc.get("source_id") == self.source_id else None
        return str(value) if value else None

    def save_resume_cursor(self, result: CollectorPullResult) -> None:
        """Persist partial progress so a capped pull resumes instead of re-returning the head.

        Cleared once a pull reaches the end, so a finished sweep replays from scratch exactly
        like an un-cursored pull (idempotent downstream).
        """
        if _DEFER_ACK.get():
            return
        if result.priority_key:
            pri = self._priority_key_path()
            pri.parent.mkdir(parents=True, exist_ok=True)
            tmp = pri.with_name(pri.name + ".tmp")
            tmp.write_text(
                json.dumps({
                    "source_id": self.source_id, "key": result.priority_key,
                    "delivered": result.priority_delivered, "done": result.priority_done,
                    "held": list(result.priority_held),
                }),
                encoding="utf-8",
            )
            os.replace(tmp, pri)
        path = self._resume_cursor_path()
        if result.complete or not result.next_cursor:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"source_id": self.source_id, "cursor": result.next_cursor}), encoding="utf-8")
        os.replace(tmp, path)

    # -- mapping ---------------------------------------------------------------
    def _record(self, bundle: VerifiedBundle, row: Mapping[str, Any], *, trace_id: str) -> SourceRecord:
        item_id = str(row["id"]).strip()
        manifest_item = bundle.manifest_items[item_id]
        manifest = bundle.manifest
        sha = str(manifest_item["content_sha256"]).lower()
        url = str(row.get("url") or manifest_item.get("url") or "").strip()
        if not url:
            raise SourceEvidenceError(f"collector item {item_id} has no url")
        fetched_at = _utc(row.get("fetched_at"), f"item {item_id} fetched_at")
        published_raw = row.get("published_at")
        published_at = _utc(published_raw, f"item {item_id} published_at", allow_rfc2822=True) if published_raw else fetched_at
        if self.source_type == "market" and row.get("trade_date"):
            published_at = self._market_event_time(item_id, row, published_raw, published_at)
        text = row.get("text")
        title = str(row.get("title") or (str(text).strip()[:80] if text else "") or item_id)
        raw_ref = f"raw/{_slug(self.connector_id)}/{_slug(self.dataset)}/date={bundle.date}/bundle-{bundle.run_id}/{PurePosixPath(manifest_item['raw_path']).name}"
        attribution = str(manifest.get("attribution") or "").strip()
        if self.attribution_required and not attribution:
            raise SourceEvidenceError(f"collector source {self.source_id} requires attribution text in the manifest")

        metadata: dict[str, Any] = {
            "license_scope": self.license_scope,
            "entitlement_tags": [f"{self.source_type}-research"],
            "access_scope": ["research"],
            "event_time": _iso(published_at),
            "available_time": _iso(fetched_at),
            "ingest_time": _iso(datetime.now(timezone.utc)),
            "content_hash": f"sha256:{sha}",
            "direct_execution_allowed": False,
            "redistribution_allowed": False,
            "attribution_required": bool(attribution) or self.attribution_required,
            "license_policy": {
                "license_scope": self.license_scope,
                "attribution_required": bool(attribution) or self.attribution_required,
                "redistribution_allowed": False,
                "policy_ref": f"collector-terms://{self.source_id}",
            },
            "terms_ref": str(manifest["terms_ref"]),
            "robots_decision": str(manifest["robots_decision"]),
            "raw_uri": raw_ref,
            "lang": row.get("lang"),
            "collector": {
                "source_id": self.source_id,
                "run_id": bundle.run_id,
                "collector_version": str(manifest["collector_version"]),
                "fetch_policy_hash": str(manifest["fetch_policy_hash"]),
                "bundle_sha256": str(manifest["bundle_sha256"]).lower(),
                "bundle_status": bundle.status,
                "http_status": manifest_item.get("http_status"),
            },
            "metrics": dict(row.get("metrics") or {}),
            "trace_id": trace_id,
        }
        if attribution:
            metadata["attribution_text"] = attribution
        if text:
            metadata["body"] = str(text)
        for optional in (
            "symbols",
            "symbol",
            "security_name",
            "trade_date",
            "dataset",
            "exchange",
            "source_dataset",
            "upstream_endpoint",
            "open",
            "high",
            "low",
            "close",
            "change",
            "volume_shares",
            "turnover_twd",
            "transactions",
            "foreign_net_shares",
            "investment_trust_net_shares",
            "dealer_net_shares",
            "total_institutional_net_shares",
            "series_id",
            "period_end",
            "value",
            "unit",
            "authors",
            "doi",
            "arxiv_id",
        ):
            if row.get(optional) not in (None, "", [], {}):
                metadata[optional] = row[optional]

        if self.source_type in {"macro", "social"}:
            # Provider item labels cannot turn raw evidence into a governed row.
            metadata["dataset"] = (self.dataset if self.dataset not in {
                TW_MACRO_SERIES_DATASET, TW_COMMUNITY_DAILY_DATASET} else "collector_bundle_items")

        if self.source_type == "social":
            author = str(row.get("hashed_author") or "").strip()
            if not author:
                raise SourceEvidenceError(f"collector social item {item_id} has no hashed_author")
            metadata.update(
                platform=self.platform or self.source_id,
                author_id_hash=author,
                post_id=item_id,
                platform_policy_ref=str(manifest["terms_ref"]),
                trust_score=self.trust_score,
            )
        elif self.source_type == "news":
            metadata.update(publisher=str(row.get("publisher") or self.publisher or self.source_id), published_at=_iso(published_at))
        elif self.source_type == "market":
            metrics_dict = dict(row.get("metrics") or {})
            market_row: dict[str, Any] = dict(metrics_dict)
            for market_key in (
                "symbol",
                "security_name",
                "trade_date",
                "dataset",
                "exchange",
                "source_dataset",
                "upstream_endpoint",
                "open",
                "high",
                "low",
                "close",
                "change",
                "volume_shares",
                "turnover_twd",
                "transactions",
                "foreign_net_shares",
                "investment_trust_net_shares",
                "dealer_net_shares",
                "total_institutional_net_shares",
            ):
                if metadata.get(market_key) not in (None, "", [], {}):
                    market_row.setdefault(market_key, metadata[market_key])
            exchange = str(market_row.get("exchange") or "").upper()
            if self.dataset in PRIORITY_LATEST_DATASETS and market_row.get("symbol") and exchange in {"TWSE", "TPEX"}:
                # Same canonical identity as the official OpenAPI row, so a session's collector and
                # official closes land on one symbol series and can be compared.
                market_row.setdefault("symbol_canonical", f"{str(market_row['symbol']).strip().upper()}.{exchange}")
                market_row.setdefault("market", "TW")
            if market_row:
                metadata.setdefault("normalized_row", market_row)
            if self.dataset in PRIORITY_LATEST_DATASETS:
                # Exchange-website close relayed by the collector: provisional, never official truth.
                metadata["collector_close"] = {
                    "provisional": True,
                    "official": False,
                    "collector_source_id": self.source_id,
                    "exchange_endpoint": url,
                    "upstream_endpoint": row.get("upstream_endpoint"),
                    "bundle_sha256": str(manifest["bundle_sha256"]).lower(),
                    "bundle_run_id": bundle.run_id,
                    "trade_date": row.get("trade_date"),
                }
            for key, val in metrics_dict.items():
                if val not in (None, "", [], {}):
                    metadata.setdefault(key, val)

        record = SourceRecord(
            source_id=f"collector:{self.source_id}:{item_id}:{sha[:12]}",
            connector_id=self.connector_id,
            source_type=self.source_type,
            title=title,
            content_ref=url,
            metadata=metadata,
            trace_id=trace_id,
        )
        return validate_external_source_record(record, connector=self.connector(), force=True)

    def _market_event_time(
        self, item_id: str, row: Mapping[str, Any], published_raw: Any, published_at: datetime
    ) -> datetime:
        """Point-in-time history: a market row's event time is its own trade date.

        A backfilled bundle is fetched long after the trade date, so falling back to the fetch
        time would stamp two years of history with today. The row's trade date (Taipei) is the
        event date; a stated publish time on another date is rejected, never trusted.
        """

        try:
            trade_date = date.fromisoformat(str(row["trade_date"]).strip())
        except ValueError as exc:
            raise SourceEvidenceError(f"collector item {item_id} has an invalid trade_date") from exc
        if published_raw:
            if published_at.astimezone(_TAIPEI_TZ).date() != trade_date:
                raise SourceEvidenceError(
                    f"collector item {item_id} published_at is not on its trade_date {trade_date.isoformat()}"
                )
            return published_at
        close_utc = _MARKET_EVENT_CLOSE_UTC.get(str(row.get("dataset") or self.dataset), (6, 30))
        return datetime(trade_date.year, trade_date.month, trade_date.day, *close_utc, tzinfo=timezone.utc)

    def _instrument_authority(self) -> dict[str, tuple[str, ...]]:
        authority = {k: set(v) for k, v in stored_instrument_authority(self._raw_root()).items()}
        explicit = os.environ.get("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT")
        if explicit:
            for k, v in stored_instrument_authority(explicit).items():
                authority.setdefault(k, set()).update(v)
        for symbol in self.known_instruments:
            canonical = str(symbol).strip().upper()
            if "." in canonical:
                authority.setdefault(canonical.rsplit(".", 1)[0], set()).add(canonical)
        return {k: tuple(sorted(v)) for k, v in authority.items()}

    def _select_raw(
        self,
        verified: Sequence[VerifiedBundle],
        all_records: Sequence[SourceRecord],
        cursor: str | None,
        budget: int,
        skips: Mapping[str, int] | None = None,
    ) -> tuple[list[SourceRecord], str | None, list[tuple[VerifiedBundle, Mapping[str, Any]]]]:
        skips = skips or {}
        raw_plan = [(b, {}) for b in verified if b.status != "paused"]
        next_cursor = cursor
        for bundle in verified:
            if bundle.status == "paused":
                break
            next_cursor = (f"{bundle.cursor}{_PARTIAL_CURSOR_MARK}{len(bundle.items)}"
                           if bundle.status == "partial" else bundle.cursor)
        records = list(all_records[:budget])
        # Truncation must not skip unconsumed bundles: advance the cursor past whole
        # bundles fully represented in `records`. A bundle larger than the remaining
        # budget is consumed partially (``<key>|items=<n>``) so a bundle bigger than the
        # public cap still makes bounded progress instead of starving every later pull.
        if len(records) < len(all_records) or len(records) < sum(
            max(len(b.items) - skips.get(b.cursor, 0), 0) for b in verified if b.status != "paused"
        ):
            kept = len(records)
            next_cursor = cursor
            running = 0
            consumed_bundles: list[VerifiedBundle] = []
            for bundle in verified:
                if bundle.status == "paused":
                    break
                n = max(len(bundle.items) - skips.get(bundle.cursor, 0), 0)
                if running + n > kept:
                    take = kept - running
                    if take > 0:
                        next_cursor = f"{bundle.cursor}{_PARTIAL_CURSOR_MARK}{skips.get(bundle.cursor, 0) + take}"
                        consumed_bundles.append(bundle)
                        running += take
                    break
                running += n
                next_cursor = (f"{bundle.cursor}{_PARTIAL_CURSOR_MARK}{len(bundle.items)}"
                               if bundle.status == "partial" else bundle.cursor)
                consumed_bundles.append(bundle)
            keep_ids = {id(b) for b in consumed_bundles}
            raw_plan = [(b, x) for b, x in raw_plan if id(b) in keep_ids]
            records = records[:running]
        return records, next_cursor, raw_plan

    def _macro_emitted_path(self) -> Path:
        return self._raw_root() / "state" / _slug(self.connector_id) / "macro_emitted.jsonl"

    @staticmethod
    def _read_markers(path: Path, *, persisted_only: bool = False) -> list[tuple[str, str, str]]:
        if not path.is_file():
            return []
        return [
            (d["symbol"], d["day"], d["step"])
            for d in (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
            if not persisted_only or d.get("persisted") is True
        ]

    def _community_ledger_path(self) -> Path:
        return self._raw_root() / "state" / _slug(self.connector_id) / "community_ledger.jsonl"

    def governed_feature_records(
        self,
        records: Sequence[SourceRecord],
        *,
        trace_id: str = "",
    ) -> tuple[tuple[SourceRecord, ...], tuple[dict[str, str], ...]]:
        """Derive storable governed feature records (macro series or community daily).

        Community rows are rebuilt from a persisted per-post ledger (de-duplicated, so
        replays and multiple pulls in one Taipei day aggregate the whole day). Returns
        ``(records, rejected)``; other datasets yield nothing.
        """
        return self._commit_governed_features(self._plan_governed_features(records), limit=None, trace_id=trace_id)

    def _plan_governed_features(self, records: Sequence[SourceRecord]) -> "_FeaturePlan":
        """Derive feature rows without any durable write (ledger / emission markers)."""
        if self.dataset == TW_MACRO_SERIES_DATASET or self.source_type == "macro":
            extra: dict[str, list] = {}
            for provider_id, dataset, field_name, source, period_kind in self.macro_series_map:
                extra.setdefault(provider_id, []).append((dataset, field_name, source, period_kind))
            rows, rejected = macro_feature_rows(records, extra_series_map=extra)
            known = frozenset(self._read_markers(self._macro_emitted_path(), persisted_only=_DEFER_ACK.get()))
            return _FeaturePlan(tuple(rows), tuple(rejected), (), known)
        if self.dataset == TW_COMMUNITY_DAILY_DATASET or self.source_type == "social":
            path = self._community_ledger_path()
            ledger = dict(load_community_ledger(path, persisted_only=_DEFER_ACK.get()))
            now = _feature_iso(self.clock() if self.clock else datetime.now(timezone.utc))
            entries, rejected_list = community_ledger_entries(
                records, authority=self._instrument_authority(), seen_at=now
            )
            fresh = list({e["key"]: e for e in entries if e["key"] not in ledger}.values())
            for entry in fresh:
                ledger[entry["key"]] = entry
            emitted_path = path.with_name("community_emitted.jsonl")
            done = self._read_markers(emitted_path, persisted_only=_DEFER_ACK.get())
            rows = community_daily_rows_from_ledger(
                ledger, {(e["symbol"], e["day"]) for e in entries}, now=_feature_time(now), emitted=done
            )
            known = set(done)
            return _FeaturePlan(tuple(rows), tuple(rejected_list), tuple(fresh), frozenset(known))
        return _FeaturePlan((), (), (), frozenset())

    def _commit_governed_features(
        self, plan: "_FeaturePlan", *, limit: int | None, trace_id: str = "", persisted: bool = False
    ) -> tuple[tuple[SourceRecord, ...], tuple[dict[str, str], ...]]:
        """Return up to ``limit`` rows; acknowledge ledger/markers only for what is returned."""
        rows = self._select_feature_rows(plan, limit)
        if self.dataset == TW_COMMUNITY_DAILY_DATASET or self.source_type == "social":
            path = self._community_ledger_path()
            if plan.fresh_entries:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    for entry in plan.fresh_entries:
                        handle.write(json.dumps({**entry, "persisted": persisted}, sort_keys=True) + "\n")
            emitted_path = path.with_name("community_emitted.jsonl")
        elif self.dataset == TW_MACRO_SERIES_DATASET or self.source_type == "macro":
            emitted_path = self._macro_emitted_path()
        else:
            return (), plan.rejected
        known = set(self._read_markers(emitted_path, persisted_only=persisted))
        new_markers = []
        for r in rows:
            marker = (r["symbol"], r["event_time"], r["available_time"])
            if marker not in known:
                known.add(marker)
                new_markers.append(marker)
        if new_markers:
            emitted_path.parent.mkdir(parents=True, exist_ok=True)
            with emitted_path.open("a", encoding="utf-8") as handle:
                for m in new_markers:
                    handle.write(json.dumps({"symbol": m[0], "day": m[1], "step": m[2],
                                             "persisted": persisted}) + "\n")
        return feature_rows_to_records(rows, connector_id=self.connector_id,
                                       trace_id=trace_id, license_scope=self.license_scope), plan.rejected

    @staticmethod
    def _select_feature_rows(plan: "_FeaturePlan", limit: int | None) -> tuple:
        rows = tuple(r for r in plan.rows if not plan.is_acked(r)) if _DEFER_ACK.get() else plan.rows
        if limit is not None and len(rows) > limit:
            # An output cap must never starve unacknowledged revisions: keep those first
            # (then already-acknowledged replays), preserving chronological order.
            ranked = sorted(range(len(rows)), key=lambda i: (plan.is_acked(rows[i]), i))[:limit]
            rows = tuple(rows[i] for i in sorted(ranked))
        return tuple(rows)

    def fetch_bundle(self, *, cursor: str | None = None, trace_id: str = "") -> CollectorPullResult:
        return self.pull(cursor=cursor, trace_id=trace_id)

    # -- raw storage -----------------------------------------------------------
    def _raw_bundle_path(self, bundle: VerifiedBundle) -> Path:
        return (self._raw_root() / "raw" / _slug(self.connector_id) / _slug(self.dataset)
                / f"date={bundle.date}" / f"bundle-{bundle.run_id}")

    def _boundary_ack(self, bundle: VerifiedBundle) -> dict[str, str]:
        return {"source_id": self.source_id, "connector_id": self.connector_id, "cursor": bundle.cursor,
                "manifest_sha256": _sha256(json.dumps(dict(bundle.manifest), sort_keys=True).encode())}

    def _acknowledge_boundary(self, bundle: VerifiedBundle) -> None:
        # Called only by the native delivery acknowledgement, after the
        # scheduler/pipeline's existing persistence checks. Raw caching and
        # legacy eager pulls never create this receipt.
        path = self._raw_bundle_path(bundle) / "persisted_boundary.json"
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self._boundary_ack(bundle), sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def _has_verified_boundary(self, bundle: VerifiedBundle, *, persisted_only: bool = False) -> bool:
        """A supplied cursor alone cannot prove that this exact bundle was consumed.

        Reuse the raw evidence written only after verification. The manifest and
        local raw hashes must still match. Configured ingestion also requires
        the native persistence acknowledgement. No network raw reads are made.
        """
        root = self._raw_bundle_path(bundle)
        try:
            if persisted_only and json.loads((root / "persisted_boundary.json").read_text()) != self._boundary_ack(bundle):
                return False
            if json.loads((root / "manifest.json").read_text(encoding="utf-8")) != bundle.manifest:
                return False
            for item in bundle.manifest_items.values():
                path = root / PurePosixPath(_safe_raw_path(str(item["raw_path"]))).name
                size = path.stat().st_size
                if size > MAX_RAW_BYTES or ("bytes" in item and size != int(item["bytes"])):
                    return False
                if _sha256(path.read_bytes()) != str(item["content_sha256"]).lower():
                    return False
            return True
        except (OSError, ValueError):
            return False

    def _raw_root(self) -> Path:
        if self.raw_root:
            return Path(self.raw_root)
        if isinstance(self.transport, LocalDirectoryBundleTransport):
            return Path(self.transport.root_dir) / "raw_store"
        explicit = os.environ.get("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT")
        if explicit:
            return Path(explicit)
        return Path(os.environ.get("SOURCE_INGEST_DATA_DIR", "/data/source-ingest")) / "market_data_store"

    def _write_raw(self, plan: Sequence[tuple[VerifiedBundle, Mapping[str, Any]]], records: Sequence[SourceRecord]) -> None:
        for bundle, _ in plan:
            target = self._raw_bundle_path(bundle)
            target.mkdir(parents=True, exist_ok=True)
            # Raw files are written per verified item (windowed pulls verify a bundle in
            # slices), each atomically; the manifest lands once the first slice does.
            for item in bundle.manifest_items.values():
                sha = str(item["content_sha256"]).lower()
                if sha not in bundle.raw_bytes:
                    continue
                dest = target / PurePosixPath(item["raw_path"]).name
                if dest.exists():
                    continue
                tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
                tmp.write_bytes(bundle.raw_bytes[sha])
                os.replace(tmp, dest)
            manifest_path = target / "manifest.json"
            if not manifest_path.exists():
                tmp = manifest_path.with_name(f".manifest.{os.getpid()}.tmp")
                tmp.write_text(json.dumps(bundle.manifest, sort_keys=True), encoding="utf-8")
                os.replace(tmp, manifest_path)


@dataclass(frozen=True)
class _FeaturePlan:
    rows: tuple
    rejected: tuple
    fresh_entries: tuple
    known_markers: frozenset

    def is_acked(self, row: Mapping[str, Any]) -> bool:
        return (row["symbol"], row["event_time"], row["available_time"]) in self.known_markers


def _slug(value: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.=-]+", "-", str(value).strip().lower()).strip("-") or "unknown"


# ---------------------------------------------------------------------------
# Governed feature rows from collector bundles (task GOVERNED-FEATURE-DATASETS-20261110)
# ---------------------------------------------------------------------------

TW_MACRO_SERIES_DATASET = "tw_macro_series"
TW_COMMUNITY_DAILY_DATASET = "tw_community_daily"
COMMUNITY_ZSCORE_WINDOW_DAYS = 60  # design §8.5: attention vs 60-day mean
COMMUNITY_AUTHOR_DOMINANCE_SHARE = 0.5  # one author above this share of a day's posts => down-weighted
_ENGAGEMENT_KEYS = ("engagement", "likes", "like_count", "comments", "comment_count", "replies", "reply_count", "shares", "share_count", "push_count", "upvotes")
COMMUNITY_ZSCORE_MIN_HISTORY_DAYS = 5
_TAIPEI_TZ = timezone(timedelta(hours=8))
# Event time of a market row without a stated publish time: the day's published close (UTC).
_MARKET_EVENT_CLOSE_UTC: dict[str, tuple[int, int]] = {"tw_institutional_flow": (8, 30)}
_BULLISH_LABELS = frozenset({"bullish", "bull", "positive", "long", "看多", "多"})
_BEARISH_LABELS = frozenset({"bearish", "bear", "negative", "short", "看空", "空"})


def _feature_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise SourceEvidenceError("feature row time is empty")
        dt = datetime.fromisoformat(text[:-1] + "+00:00" if text[-1] in "zZ" else text)
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _feature_iso(dt: datetime) -> str:
    # Distinct same-second ingestion steps must remain distinct PIT revisions.
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _month_end(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def normalize_macro_period(raw: Any, period_kind: str) -> date:
    """Normalize a provider period (``YYYY-MM-DD``, ``YYYY-MM``, ``YYYYMM``, ``YYYY-Qn``) to its event date.

    ``day`` keeps the date; ``month`` and ``quarter`` use the period end so the declared
    availability lag counts from when the period closed (never from its first day).
    """
    import re

    text = str(raw or "").strip()
    if not text:
        raise SourceEvidenceError("macro period is empty")
    quarter = re.fullmatch(r"(\d{4})-?Q([1-4])", text, re.IGNORECASE)
    if quarter:
        year, q = int(quarter.group(1)), int(quarter.group(2))
        return _month_end(year, q * 3)
    if re.fullmatch(r"\d{6}", text):
        text = f"{text[:4]}-{text[4:]}"
    month_only = re.fullmatch(r"(\d{4})-(\d{2})", text)
    if month_only:
        return _month_end(int(month_only.group(1)), int(month_only.group(2)))
    day = _feature_time(text).astimezone(_TAIPEI_TZ).date() if "T" in text else date.fromisoformat(text[:10])
    if period_kind == "month":
        return _month_end(day.year, day.month)
    if period_kind == "quarter":
        return _month_end(day.year, (day.month - 1) // 3 * 3 + 3)
    return day


def _macro_source_value(meta: Mapping[str, Any], source: str) -> Any:
    if source == "value":
        return meta.get("value")
    if source.startswith("metrics."):
        metrics = meta.get("metrics") if isinstance(meta.get("metrics"), Mapping) else {}
        return metrics.get(source.split(".", 1)[1])
    return None


def macro_feature_rows(
    records: Sequence[SourceRecord],
    *,
    extra_series_map: Mapping[str, Sequence[Any]] | None = None,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, str], ...]]:
    """Normalize macro bundle records (CBC / NDC / DGBAS) into governed series rows.

    Provider series ids (``CBC:BP01D01:01``, ``NDC:6099:MONITORING_SCORE``, ...) are mapped
    through ``PROVIDER_SERIES_MAP`` (plus ``extra_series_map``); one record can expand into
    several catalogue fields (NDC score + leading + coincident). Catalogue-native ids
    (``<agency>_<field>``) are also accepted. Unmapped series, non-numeric values, or
    availability earlier than the period are rejected, never imputed.
    """
    from ..feature_catalogue import MACRO_SERIES_MAP, PROVIDER_SERIES_MAP, ProviderSeriesMapping, lag_days_for

    provider_map: dict[str, tuple[Any, ...]] = {k: tuple(v) for k, v in PROVIDER_SERIES_MAP.items()}
    for key, mappings in (extra_series_map or {}).items():
        provider_map[str(key)] = tuple(
            m if isinstance(m, ProviderSeriesMapping) else ProviderSeriesMapping(*m) for m in mappings
        )
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for record in records:
        meta = record.metadata
        series_id = str(meta.get("series_id") or "").strip()
        mappings = provider_map.get(series_id)
        if mappings is None and series_id in MACRO_SERIES_MAP:
            dataset, field_name = MACRO_SERIES_MAP[series_id]
            mappings = (ProviderSeriesMapping(dataset, field_name, "value", "day"),)
        if mappings is None:
            rejected.append({"source_id": record.source_id, "reason": f"series_id {series_id!r} is not catalogued"})
            continue
        metrics = meta.get("metrics") if isinstance(meta.get("metrics"), Mapping) else {}
        try:
            available = _feature_time(meta.get("available_time"))
            ingest = _feature_time(meta.get("ingest_time"))
        except (ValueError, SourceEvidenceError) as exc:
            rejected.append({"source_id": record.source_id, "reason": f"invalid time: {exc}"})
            continue
        for mapping in mappings:
            value = _macro_source_value(meta, mapping.source)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
                rejected.append({"source_id": record.source_id, "reason": f"{mapping.field}: value is not numeric"})
                continue
            try:
                period_raw = meta.get("period_end") or metrics.get("period") or meta.get("event_time")
                period_day = normalize_macro_period(period_raw, mapping.period_kind)
            except (ValueError, SourceEvidenceError) as exc:
                rejected.append({"source_id": record.source_id, "reason": f"invalid time: {exc}"})
                continue
            if available.date() < period_day:
                rejected.append({"source_id": record.source_id, "reason": "available_time precedes period"})
                continue
            rows.append(
                {
                    "dataset": TW_MACRO_SERIES_DATASET,
                    "catalogue_dataset": mapping.dataset,
                    "field": mapping.field,
                    "series_id": f"{mapping.dataset.removeprefix('tw_macro_')}_{mapping.field}",
                    "provider_series_id": series_id,
                    "symbol": f"{mapping.dataset.removeprefix('tw_macro_')}_{mapping.field}",
                    "event_time": period_day.isoformat(),
                    "date": period_day.isoformat(),
                    "available_time": _feature_iso(available),
                    "ingest_time": _feature_iso(ingest),
                    "availability_lag_days": lag_days_for(mapping.dataset, mapping.field),
                    "value": float(value),
                    "unit": str(meta.get("unit") or ""),
                    "source_id": record.source_id,
                    "connector_id": record.connector_id,
                    "content_hash": meta.get("content_hash"),
                    "raw_uri": meta.get("raw_uri"),
                }
            )
    return tuple(rows), tuple(rejected)


# -- community daily features -------------------------------------------------

SOCIAL_SYMBOL_AUTHORITY_DATASET = "tw_price_daily"


def stored_instrument_authority(*roots: str | Path) -> dict[str, tuple[str, ...]]:
    """Governed instrument identities: venue-qualified symbols that have stored daily bars.

    Returns ``{ticker: (canonical, ...)}`` (e.g. ``{"2330": ("2330.TWSE",)}``) read from the
    normalized ``tw_price_daily`` rows under each root.
    """
    found: dict[str, set[str]] = {}
    for root in roots:
        base = Path(root) / "normalized" / _slug(SOCIAL_SYMBOL_AUTHORITY_DATASET)
        if not base.is_dir():
            continue
        for path in sorted(base.glob("date=*/*.jsonl*")):
            opener = gzip.open if path.suffix == ".gz" else Path.open
            try:
                with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
                    for line in handle:
                        if not line.strip():
                            continue
                        meta = (json.loads(line) or {}).get("metadata") or {}
                        nrow = meta.get("normalized_row") or {}
                        canonical = str(meta.get("symbol_canonical") or nrow.get("symbol_canonical") or "").upper()
                        if "." in canonical:
                            found.setdefault(canonical.rsplit(".", 1)[0], set()).add(canonical)
            except (OSError, ValueError):
                continue
    return {ticker: tuple(sorted(c)) for ticker, c in found.items()}


def resolve_social_symbol(raw: Any, authority: Mapping[str, Sequence[str]]) -> str:
    """Resolve a collector symbol to its venue-qualified canonical symbol.

    Explicit suffixes (``2330.TW`` / ``.TWO``) are canonicalized. A bare ticker must match
    exactly one governed instrument; unknown or ambiguous tickers raise (never guessed).
    """
    from .taiwan_official import canonical_taiwan_equity_symbol

    text = str(raw or "").strip().upper()
    if not text:
        raise SourceEvidenceError("social symbol is empty")
    if "." in text:
        return canonical_taiwan_equity_symbol(text)
    candidates = tuple(authority.get(text) or ())
    if not candidates:
        raise SourceEvidenceError(f"symbol {text!r} has no governed instrument (no stored daily bars)")
    if len(candidates) > 1:
        raise SourceEvidenceError(f"symbol {text!r} is ambiguous across venues: {', '.join(candidates)}")
    return candidates[0]


def community_ledger_entries(
    records: Sequence[SourceRecord],
    *,
    authority: Mapping[str, Sequence[str]],
    seen_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Per-(post, canonical symbol) ledger entries from social records; unresolvable symbols are rejected."""
    entries: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for record in records:
        meta = record.metadata
        symbols = meta.get("symbols") or ([meta["symbol"]] if meta.get("symbol") else [])
        if isinstance(symbols, str):
            symbols = [symbols]
        try:
            event = _feature_time(meta.get("event_time"))
            available = _feature_time(meta.get("available_time"))
        except (ValueError, SourceEvidenceError) as exc:
            rejected.append({"source_id": record.source_id, "reason": f"invalid time: {exc}"})
            continue
        post_key = str(meta.get("post_id") or record.source_id)
        metrics = meta.get("metrics") if isinstance(meta.get("metrics"), Mapping) else {}
        raw_stance = metrics.get("sentiment_tag") or metrics.get("sentiment") or metrics.get("stance") or meta.get("sentiment") or ""
        if isinstance(raw_stance, Mapping):
            raw_stance = raw_stance.get("label") or ""
        stance = str(raw_stance).strip().lower()
        stance = "bull" if stance in _BULLISH_LABELS else "bear" if stance in _BEARISH_LABELS else ""
        raw_author = str(
            meta.get("author_id_hash") or meta.get("author_hash") or meta.get("author_id") or meta.get("author") or ""
        ).strip()
        author = (
            raw_author.lower()
            if len(raw_author) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw_author)
            else (hashlib.sha256(raw_author.encode("utf-8")).hexdigest() if raw_author else "")
        )
        engagement = 0.0
        for k in _ENGAGEMENT_KEYS:
            try:
                v = float(metrics.get(k) if k in metrics else meta.get(k) or 0)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0 and v == v and v != float("inf"):
                engagement += v
        canonical_symbols: set[str] = set()
        for raw in symbols:
            if not str(raw).strip():
                continue
            try:
                canonical_symbols.add(resolve_social_symbol(raw, authority))
            except SourceEvidenceError as exc:
                rejected.append({"source_id": record.source_id, "reason": str(exc)})
        for symbol in sorted(canonical_symbols):
            entries.append(
                {
                    "key": f"{post_key}|{symbol}",
                    "symbol": symbol,
                    "day": event.astimezone(_TAIPEI_TZ).date().isoformat(),
                    "stance": stance,
                    "author": author,
                    "engagement": engagement,
                    "available": _feature_iso(available),
                    "first_seen": seen_at,
                    "source_id": record.source_id,
                    "content_hash": meta.get("content_hash"),
                    "raw_uri": meta.get("raw_uri"),
                }
            )
    return entries, rejected


def load_community_ledger(path: Path, *, persisted_only: bool = False) -> dict[str, dict[str, Any]]:
    ledger: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return ledger
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            if persisted_only and entry.get("persisted") is not True:
                continue
            ledger.setdefault(str(entry["key"]), entry)  # first sight wins: replays never rewrite history
    return ledger


def _known_at(entry: Mapping[str, Any]) -> datetime:
    return max(_feature_time(entry["available"]), _feature_time(entry["first_seen"]))


def _community_post_weights(entries: Sequence[Mapping[str, Any]]) -> list[float]:
    """Per-post weights: 1.0, or 1/author_posts when one hashed author dominates the day (§8.5)."""
    n = len(entries)
    counts: dict[str, int] = {}
    for e in entries:
        if e.get("author"):
            counts[e["author"]] = counts.get(e["author"], 0) + 1
    if n and counts and max(counts.values()) / n > COMMUNITY_AUTHOR_DOMINANCE_SHARE and max(counts.values()) > 1:
        return [1.0 / counts[e["author"]] if e.get("author") else 1.0 for e in entries]
    return [1.0] * n


def community_daily_rows_from_ledger(
    ledger: Mapping[str, Mapping[str, Any]],
    touched: Iterable[tuple[str, str]] | None = None,
    *,
    now: datetime | None = None,
    emitted: Iterable[tuple[str, str, str]] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Rebuild point-in-time daily rows for ``touched`` (symbol, Taipei day) pairs from the ledger.

    Posts are de-duplicated by ledger key. A post is *known* at ``max(available, first_seen)``
    (a late-arriving or backfilled post is not retroactively visible). A day has one row per
    distinct knowledge step at or after the day close; later steps are new revisions and never
    rewrite earlier ones. Each revision only uses posts known by its own ``available_time``,
    including for the rolling history of earlier days. Revisions later than ``now`` (a day not
    yet closed) are not emitted. ``touched`` pairs always re-emit their revisions
    (deterministic, so replays are idempotent); other ledger pairs emit only revisions not in
    ``emitted`` (days that closed since they were last seen).
    """
    from statistics import mean, pstdev

    by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for entry in ledger.values():
        by_symbol.setdefault(str(entry["symbol"]), []).append(entry)
    rows: list[dict[str, Any]] = []
    touched_set = set(touched or ())
    done = {tuple(x) for x in emitted or ()}
    all_pairs = {(str(e["symbol"]), str(e["day"])) for e in ledger.values()}
    pairs = all_pairs if touched is None else (all_pairs | touched_set if emitted is not None else touched_set)
    for symbol, day in sorted(pairs):
        day_date = date.fromisoformat(day)
        close = datetime.combine(day_date + timedelta(days=1), datetime.min.time(), tzinfo=_TAIPEI_TZ)
        day_entries = [e for e in by_symbol.get(symbol, []) if e["day"] == day]
        steps = sorted({max(_known_at(e), close) for e in day_entries})
        for step in steps:
            if now is not None and step > now:
                continue
            if touched is not None and (symbol, day) not in touched_set and (symbol, day, _feature_iso(step)) in done:
                continue
            visible = [e for e in day_entries if _known_at(e) <= step]
            bull = sum(e["stance"] == "bull" for e in visible)
            bear = sum(e["stance"] == "bear" for e in visible)
            weights = _community_post_weights(visible)
            history_entries: dict[str, list[Mapping[str, Any]]] = {}
            for e in by_symbol.get(symbol, []):
                if e["day"] != day and _known_at(e) <= step:
                    history_entries.setdefault(e["day"], []).append(e)
            history_counts = {
                d: sum(_community_post_weights(es)) for d, es in history_entries.items()
            }
            history = [
                history_counts[(day_date - timedelta(days=offset)).isoformat()]
                for offset in range(1, COMMUNITY_ZSCORE_WINDOW_DAYS + 1)
                if (day_date - timedelta(days=offset)).isoformat() in history_counts
            ]
            row: dict[str, Any] = {
                "dataset": TW_COMMUNITY_DAILY_DATASET,
                "symbol": symbol,
                "symbol_canonical": symbol,
                "event_time": day,
                "date": day,
                "available_time": _feature_iso(step),
                "ingest_time": max(str(e["first_seen"]) for e in visible),
                "availability_lag_days": 0,
                "post_volume": float(len(visible)),
                "source_records": [{k: e.get(k) for k in ("source_id", "content_hash", "raw_uri")}
                                   for e in visible],
            }
            authors = {e.get("author") for e in visible if e.get("author")}
            if authors:
                row["unique_authors"] = float(len(authors))
            scored = [
                (w * (1.0 + float(e.get("engagement") or 0.0)), 1.0 if e["stance"] == "bull" else -1.0)
                for e, w in zip(visible, weights)
                if e["stance"]
            ]
            total_weight = sum(w for w, _ in scored)
            if total_weight > 0:
                row["engagement_weighted_sentiment"] = sum(w * x for w, x in scored) / total_weight
            effective_volume = sum(weights)
            if len(history) >= COMMUNITY_ZSCORE_MIN_HISTORY_DAYS:
                sd = pstdev(history)
                if sd > 0:
                    row["attention_zscore"] = (effective_volume - mean(history)) / sd
            if bull + bear > 0:
                row["bull_bear_ratio"] = (bull + 1) / (bear + 1)
            rows.append(row)
    return tuple(rows)


def community_daily_feature_rows(
    records: Sequence[SourceRecord],
    *,
    authority: Mapping[str, Sequence[str]],
    ledger: Mapping[str, Mapping[str, Any]] | None = None,
    seen_at: str | None = None,
    rejected: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Aggregate community posts into daily per-symbol features (design §8.5).

    Days are Asia/Taipei days; symbols are venue-qualified through ``authority``. ``ledger``
    holds previously seen posts (keyed, so replays do not double count); the rows returned
    cover every (symbol, day) touched by ``records``. ``post_volume`` is the day's post
    count, ``attention_zscore`` compares it with the preceding up to 60 observed days (needs
    5, else omitted), ``bull_bear_ratio`` is ``(bull + 1) / (bear + 1)``. A day's row is
    available only after that day closes and its latest contributing post was fetched.
    """
    merged: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (ledger or {}).items()}
    entries, rej = community_ledger_entries(
        records, authority=authority, seen_at=seen_at or _feature_iso(datetime.now(timezone.utc))
    )
    if rejected is not None:
        rejected.extend(rej)
    touched: set[tuple[str, str]] = set()
    for entry in entries:
        merged.setdefault(entry["key"], entry)
        touched.add((entry["symbol"], entry["day"]))
    return community_daily_rows_from_ledger(merged, touched, now=_feature_time(seen_at) if seen_at else None)


def feature_rows_to_records(
    rows: Sequence[Mapping[str, Any]],
    *,
    connector_id: str,
    trace_id: str = "",
    license_scope: str = "collector_public_web",
) -> tuple[SourceRecord, ...]:
    """Wrap governed macro/community feature rows so the storage writer can persist them."""
    out: list[SourceRecord] = []
    for row in rows:
        dataset = str(row["dataset"])
        entity = str(row.get("series_id") or row.get("symbol") or "")
        content_hash = hashlib.sha256(json.dumps(dict(row), sort_keys=True, default=str).encode()).hexdigest()
        row_hash = content_hash[:24]
        out.append(
            SourceRecord(
                source_id=f"{connector_id}:{dataset}:{entity}:{row.get('field') or 'daily'}:{row['event_time']}:{row_hash}",
                connector_id=connector_id,
                source_type="market",
                title=f"{dataset} {entity} {row['event_time']}",
                content_ref=f"collector-feature://{dataset}/{entity}/{row['event_time']}/{row_hash}",
                metadata={
                    "dataset": dataset,
                    "symbol": entity,
                    "symbol_canonical": row.get("symbol_canonical") or entity,
                    "series_id": row.get("series_id"),
                    "date": row["event_time"],
                    "event_time": row["event_time"],
                    "available_time": row["available_time"],
                    "ingest_time": row["ingest_time"],
                    "availability_lag_days": row["availability_lag_days"],
                    "normalized_row": dict(row),
                    "body": json.dumps(dict(row), ensure_ascii=False, sort_keys=True),
                    "content_hash": f"sha256:{content_hash}",
                    "access_scope": ["research"],
                    "allowed_use": ["research", "search_index", "experiment"],
                    "entitlement_tags": ["collector-research"],
                    "direct_execution_allowed": False,
                    "redistribution_allowed": False,
                    "license_scope": license_scope,
                    "schema_hash": f"{dataset}.v1",
                },
                trace_id=trace_id,
            )
        )
    return tuple(out)
