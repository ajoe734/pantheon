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

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

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
COLLECTOR_SOURCE_TYPES = frozenset({"social", "news", "paper", "macro", "filing"})


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


def _cursor_key(finished_at: datetime, run_id: str) -> str:
    return f"{_iso(finished_at)}#{run_id}"


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
                "redistribution_allowed": False,
                "governance": {"direct_execution_allowed": False, "canonical_sink": "SourceRecord/EvidenceBundle"},
            },
        )

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

    def pull(self, *, cursor: str | None = None, trace_id: str = "") -> CollectorPullResult:
        transport = self._transport()
        index = _loads(transport.read("bundles/index.json", max_bytes=MAX_JSON_BYTES), "index.json")
        entries = index.get("bundles") if isinstance(index, dict) else None
        if not isinstance(entries, list):
            raise SourceEvidenceError("collector bundle index.json must contain a bundles list")

        pending: list[tuple[datetime, str, Mapping[str, Any]]] = []
        for entry in entries:
            if not isinstance(entry, dict) or str(entry.get("source_id") or "") != self.source_id:
                continue
            finished = _utc(entry.get("finished_at"), "index finished_at")
            key = _cursor_key(finished, str(entry.get("run_id") or ""))
            if cursor is None or key > cursor:
                pending.append((finished, key, entry))
        pending.sort(key=lambda t: t[1])

        verified = [verify_bundle(transport, expected_source_id=self.source_id, entry=e) for _, _, e in pending]

        records: list[SourceRecord] = []
        raw_plan: list[tuple[VerifiedBundle, Mapping[str, Any]]] = []
        paused: list[str] = []
        next_cursor = cursor
        for bundle in verified:
            next_cursor = bundle.cursor
            if bundle.status == "paused":
                paused.append(f"{bundle.run_id}:{bundle.manifest.get('paused_reason') or 'paused'}")
                continue
            raw_plan.append((bundle, {}))
            for row in bundle.items:
                records.append(self._record(bundle, row, trace_id=trace_id))
        records = records[: self.max_records]
        # Truncation must not skip unconsumed bundles: only advance the cursor to the
        # last bundle fully represented in `records`.
        if len(records) < sum(len(b.items) for b, _ in raw_plan):
            kept = len(records)
            next_cursor = cursor
            running = 0
            for bundle in verified:
                n = 0 if bundle.status == "paused" else len(bundle.items)
                if running + n > kept:
                    break
                running += n
                next_cursor = bundle.cursor
            raw_plan = [(b, x) for b, x in raw_plan if b.cursor <= (next_cursor or "")]

        self._write_raw(raw_plan, records)
        observed = observed_from_collector_status(
            self.read_status(), source_id=self.source_id, cursor=next_cursor, cadence_seconds=self.cadence_seconds
        )
        return CollectorPullResult(
            records=tuple(records),
            next_cursor=next_cursor,
            bundle_count=len(verified),
            paused_reasons=tuple(paused),
            observed=observed,
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
        for optional in ("symbols", "series_id", "value", "unit", "authors", "doi", "arxiv_id"):
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

    # -- raw storage -----------------------------------------------------------
    def _raw_root(self) -> Path:
        if self.raw_root:
            return Path(self.raw_root)
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


def _slug(value: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.=-]+", "-", str(value).strip().lower()).strip("-") or "unknown"
