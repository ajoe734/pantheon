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
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

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


def collector_host() -> str:
    return (os.environ.get(COLLECTOR_HOST_ENV) or COLLECTOR_HOST_PLACEHOLDER).strip().lower()


def compute_bundle_sha256(content_hashes: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(sorted(str(h) for h in content_hashes)).encode("utf-8")).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc(value: Any, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise SourceEvidenceError(f"collector bundle {field_name} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
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
        with open_external_url(request, caller="source_ingest.collector_bundle", timeout=self.timeout_seconds) as response:
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
) -> VerifiedBundle:
    """Fetch and verify one bundle; raises on any mismatch, returns nothing partial."""

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
    finished_at = _utc(manifest.get("finished_at"), "finished_at")
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

    raw_bytes: dict[str, bytes] = {}
    for item_id, item in manifest_items.items():
        declared = str(item.get("content_sha256") or "").strip().lower()
        raw_path = _safe_raw_path(str(item.get("raw_path") or ""))
        data = transport.read(f"{base}/{raw_path}", max_bytes=MAX_RAW_BYTES)
        actual = _sha256(data)
        if declared != actual:
            raise SourceEvidenceError(f"collector bundle raw hash mismatch for item {item_id}: declared={declared} actual={actual}")
        if "bytes" in item and int(item["bytes"]) != len(data):
            raise SourceEvidenceError(f"collector bundle raw size mismatch for item {item_id}")
        raw_bytes[actual] = data

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
    )


def observed_from_collector_status(
    status_doc: Mapping[str, Any] | None,
    *,
    source_id: str,
    cursor: str | None,
    cadence_seconds: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project the host ``status.json`` + our cursor into health fields.

    Returns ``{"health_state", "reasons", "freshness", "last_run"}`` for
    ``SourceObservedState``. ``degraded`` when the source is paused/failing on
    the host, the newest bundle is older than twice its cadence, or our cursor
    lags the host's newest bundle by more than seven days.
    """

    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    entry: Mapping[str, Any] = {}
    sources = (status_doc or {}).get("sources") if isinstance(status_doc, Mapping) else None
    if isinstance(sources, Mapping):
        entry = sources.get(source_id) or {}
    elif isinstance(sources, list):
        entry = next((s for s in sources if isinstance(s, Mapping) and s.get("source_id") == source_id), {})
    if not entry:
        reasons.append("collector_status_missing_source")

    host_status = str(entry.get("status") or "")
    if host_status in {"paused", "failed", "failing"}:
        reasons.append(f"collector_source_{host_status}" + (f":{entry.get('paused_reason')}" if entry.get("paused_reason") else ""))

    last_run_at = None
    if entry.get("last_run_at"):
        last_run_at = _utc(entry["last_run_at"], "status.last_run_at")
    cadence = int(cadence_seconds or entry.get("cadence_seconds") or 0)
    if last_run_at and cadence and now - last_run_at > timedelta(seconds=2 * cadence):
        reasons.append("newest_bundle_older_than_twice_cadence")

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
        original = [self.dataset] if self.dataset != derived else []
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
    ) -> CollectorPullResult:
        transport = self._transport()
        index = _loads(transport.read("bundles/index.json", max_bytes=MAX_JSON_BYTES), "index.json")
        entries = index.get("bundles") if isinstance(index, dict) else None
        if not isinstance(entries, list):
            raise SourceEvidenceError("collector bundle index.json must contain a bundles list")

        base_cursor, partial_skip = _split_cursor(cursor)
        pending: list[tuple[datetime, str, Mapping[str, Any]]] = []
        for entry in entries:
            if not isinstance(entry, dict) or str(entry.get("source_id") or "") != self.source_id:
                continue
            finished = _utc(entry.get("finished_at"), "index finished_at")
            key = _cursor_key(finished, str(entry.get("run_id") or ""))
            if base_cursor is None or key > base_cursor or (partial_skip and key == base_cursor):
                pending.append((finished, key, entry))
        pending.sort(key=lambda t: t[1])

        verified = [verify_bundle(transport, expected_source_id=self.source_id, entry=e) for _, _, e in pending]

        all_records: list[SourceRecord] = []
        paused: list[str] = []
        for bundle in verified:
            if bundle.status == "paused":
                paused.append(f"{bundle.run_id}:{bundle.manifest.get('paused_reason') or 'paused'}")
                continue
            skip = partial_skip if bundle.cursor == base_cursor else 0
            for row in bundle.items[skip:]:
                all_records.append(self._record(bundle, row, trace_id=trace_id))

        # The caller's output cap covers raw + derived feature records together. Shrink the
        # raw budget until both fit; derived rows are acknowledged (ledger / emission
        # markers) only if actually returned, so a capped expansion stays pending.
        total_cap = self.max_records if max_records is None else max(min(self.max_records, int(max_records)), 0)
        budget = total_cap
        while True:
            records, next_cursor, raw_plan = self._select_raw(
                verified, all_records, cursor, budget, skips={base_cursor: partial_skip} if partial_skip else {}
            )
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
                candidate = [r for r in all_records[: max(len(first.items) - first_skip, 0)]]
                plan = self._plan_governed_features(candidate)
        feature_records, feature_rejected = self._commit_governed_features(
            plan, limit=max(total_cap - len(records), 0), trace_id=trace_id
        )
        self._write_raw(raw_plan, records)
        observed = observed_from_collector_status(
            self.read_status(), source_id=self.source_id, cursor=next_cursor, cadence_seconds=self.cadence_seconds
        )
        return CollectorPullResult(
            records=(*feature_records, *records),
            next_cursor=next_cursor,
            bundle_count=len(verified),
            paused_reasons=tuple(paused),
            observed=observed,
            feature_rejected=feature_rejected,
        )

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
        published_at = _utc(published_raw, f"item {item_id} published_at") if published_raw else fetched_at
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
            if market_row:
                metadata.setdefault("normalized_row", market_row)
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
        next_cursor = verified[-1].cursor if verified else cursor
        records = list(all_records[:budget])
        # Truncation must not skip unconsumed bundles: advance the cursor past whole
        # bundles fully represented in `records`. A bundle larger than the remaining
        # budget is consumed partially (``<key>|items=<n>``) so a bundle bigger than the
        # public cap still makes bounded progress instead of starving every later pull.
        if len(records) < len(all_records):
            kept = len(records)
            next_cursor = cursor
            running = 0
            consumed_bundles: list[VerifiedBundle] = []
            for bundle in verified:
                n = 0 if bundle.status == "paused" else max(len(bundle.items) - skips.get(bundle.cursor, 0), 0)
                if running + n > kept:
                    take = kept - running
                    if take > 0:
                        next_cursor = f"{bundle.cursor}{_PARTIAL_CURSOR_MARK}{skips.get(bundle.cursor, 0) + take}"
                        consumed_bundles.append(bundle)
                        running += take
                    break
                running += n
                next_cursor = bundle.cursor
                consumed_bundles.append(bundle)
            keep_ids = {id(b) for b in consumed_bundles}
            raw_plan = [(b, x) for b, x in raw_plan if id(b) in keep_ids]
            records = records[:running]
        return records, next_cursor, raw_plan

    def _macro_emitted_path(self) -> Path:
        return self._raw_root() / "state" / _slug(self.connector_id) / "macro_emitted.jsonl"

    @staticmethod
    def _read_markers(path: Path) -> list[tuple[str, str, str]]:
        if not path.is_file():
            return []
        return [
            (d["symbol"], d["day"], d["step"])
            for d in (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
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
            known = frozenset(self._read_markers(self._macro_emitted_path()))
            return _FeaturePlan(tuple(rows), tuple(rejected), (), known)
        if self.dataset == TW_COMMUNITY_DAILY_DATASET or self.source_type == "social":
            path = self._community_ledger_path()
            ledger = dict(load_community_ledger(path))
            now = _feature_iso(self.clock() if self.clock else datetime.now(timezone.utc))
            entries, rejected_list = community_ledger_entries(
                records, authority=self._instrument_authority(), seen_at=now
            )
            fresh = list({e["key"]: e for e in entries if e["key"] not in ledger}.values())
            for entry in fresh:
                ledger[entry["key"]] = entry
            emitted_path = path.with_name("community_emitted.jsonl")
            done = []
            if emitted_path.is_file():
                done = [
                    (d["symbol"], d["day"], d["step"])
                    for d in (json.loads(x) for x in emitted_path.read_text(encoding="utf-8").splitlines() if x.strip())
                ]
            rows = community_daily_rows_from_ledger(
                ledger, {(e["symbol"], e["day"]) for e in entries}, now=_feature_time(now), emitted=done
            )
            known = set(done)
            return _FeaturePlan(tuple(rows), tuple(rejected_list), tuple(fresh), frozenset(known))
        return _FeaturePlan((), (), (), frozenset())

    def _commit_governed_features(
        self, plan: "_FeaturePlan", *, limit: int | None, trace_id: str = ""
    ) -> tuple[tuple[SourceRecord, ...], tuple[dict[str, str], ...]]:
        """Return up to ``limit`` rows; acknowledge ledger/markers only for what is returned."""
        rows = plan.rows
        if limit is not None and len(rows) > limit:
            # An output cap must never starve unacknowledged revisions: keep those first
            # (then already-acknowledged replays), preserving chronological order.
            ranked = sorted(range(len(rows)), key=lambda i: (plan.is_acked(rows[i]), i))[:limit]
            rows = tuple(rows[i] for i in sorted(ranked))
        if self.dataset == TW_COMMUNITY_DAILY_DATASET or self.source_type == "social":
            path = self._community_ledger_path()
            if plan.fresh_entries:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    for entry in plan.fresh_entries:
                        handle.write(json.dumps(entry, sort_keys=True) + "\n")
            known = set(plan.known_markers)
            new_markers = []
            for r in rows:
                marker = (r["symbol"], r["event_time"], r["available_time"])
                if marker not in known:
                    known.add(marker)
                    new_markers.append(marker)
            if new_markers:
                emitted_path = path.with_name("community_emitted.jsonl")
                emitted_path.parent.mkdir(parents=True, exist_ok=True)
                with emitted_path.open("a", encoding="utf-8") as handle:
                    for m in new_markers:
                        handle.write(json.dumps({"symbol": m[0], "day": m[1], "step": m[2]}) + "\n")
        elif self.dataset == TW_MACRO_SERIES_DATASET or self.source_type == "macro":
            known = set(plan.known_markers)
            new_markers = []
            for r in rows:
                marker = (r["symbol"], r["event_time"], r["available_time"])
                if marker not in known:
                    known.add(marker)
                    new_markers.append(marker)
            if new_markers:
                emitted_path = self._macro_emitted_path()
                emitted_path.parent.mkdir(parents=True, exist_ok=True)
                with emitted_path.open("a", encoding="utf-8") as handle:
                    for m in new_markers:
                        handle.write(json.dumps({"symbol": m[0], "day": m[1], "step": m[2]}) + "\n")
        return feature_rows_to_records(rows, connector_id=self.connector_id, trace_id=trace_id), plan.rejected

    def fetch_bundle(self, *, cursor: str | None = None, trace_id: str = "") -> CollectorPullResult:
        return self.pull(cursor=cursor, trace_id=trace_id)

    # -- raw storage -----------------------------------------------------------
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
        root = self._raw_root()
        for bundle, _ in plan:
            target = root / "raw" / _slug(self.connector_id) / _slug(self.dataset) / f"date={bundle.date}" / f"bundle-{bundle.run_id}"
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(tempfile.mkdtemp(prefix=".bundle-", dir=target.parent))
            try:
                for item_id, item in bundle.manifest_items.items():
                    (tmp / PurePosixPath(item["raw_path"]).name).write_bytes(bundle.raw_bytes[str(item["content_sha256"]).lower()])
                (tmp / "manifest.json").write_text(json.dumps(bundle.manifest, sort_keys=True), encoding="utf-8")
                os.replace(tmp, target)
            finally:
                if tmp.exists():
                    shutil.rmtree(tmp, ignore_errors=True)


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
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
                }
            )
    return entries, rejected


def load_community_ledger(path: Path) -> dict[str, dict[str, Any]]:
    ledger: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return ledger
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
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
) -> tuple[SourceRecord, ...]:
    """Wrap governed macro/community feature rows so the storage writer can persist them."""
    out: list[SourceRecord] = []
    for row in rows:
        dataset = str(row["dataset"])
        entity = str(row.get("series_id") or row.get("symbol") or "")
        row_hash = hashlib.sha256(json.dumps(dict(row), sort_keys=True, default=str).encode()).hexdigest()[:24]
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
                    "access_scope": ["public", "research"],
                    "license_scope": "collector_public_web",
                    "schema_hash": f"{dataset}.v1",
                },
                trace_id=trace_id,
            )
        )
    return tuple(out)
