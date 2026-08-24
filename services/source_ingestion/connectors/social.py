"""Admitted social media and market discussion adapter (SD-SRCM-05 §7.4).

Governed ingestion for policy-compliant social media feeds:
- Account/post identity with privacy-preserving author hashing
- Bot/spam detection metadata and trust scoring (0.0 to 1.0)
- Tombstone and deletion event propagation
- Derived sentiment classification with explicit model/version references
- Strict research-only governance: never triggers direct runtime execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any, Mapping, Sequence
import urllib.parse
import urllib.request

from services.external_egress import open_external_url
from services.source_ingestion.source_health import SourceHealth

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceConnectorProvider,
    SourceEvidenceError,
    SourceMetadata,
    SourceRecord,
    SourceType,
)
from ..external_sources import validate_external_source_record


SOCIAL_ADMITTED_CONNECTOR_ID = "social-admitted-market-discussion"
SOCIAL_ADMITTED_SCHEMA_HASH = "social_admitted_post.v1"
STOCKTWITS_API_BASE_URL = "https://api.stocktwits.com/api/2"


import re

def _read_bounded_response(response: Any, max_bytes: int = 2097152, chunk_size: int = 65536) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise SourceEvidenceError(f"Payload exceeded max byte limit ({max_bytes} bytes)")
        chunks.append(chunk)
        if len(chunk) < chunk_size:
            break
    return b"".join(chunks)


def _validate_or_convert_rfc3339(val: Any, name: str = "available_time") -> str:
    s = _text(val)
    if not s:
        return _utc_now()
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$", s):
        try:
            iso_str = s.replace("Z", "+00:00")
            datetime.fromisoformat(iso_str)
            return s
        except Exception as err:
            raise SourceEvidenceError(
                f"{name} must be a valid RFC3339 timestamp with valid calendar date/time; got: {val!r}"
            ) from err
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", s):
        iso_str = s.replace(" ", "T") + "+00:00"
        try:
            datetime.fromisoformat(iso_str)
            return s.replace(" ", "T") + "Z"
        except Exception as err:
            raise SourceEvidenceError(
                f"{name} must be a valid RFC3339 timestamp with valid calendar date/time; got: {val!r}"
            ) from err
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        iso_str = s + "T00:00:00+00:00"
        try:
            datetime.fromisoformat(iso_str)
            return s + "T00:00:00Z"
        except Exception as err:
            raise SourceEvidenceError(
                f"{name} must be a valid RFC3339 timestamp with valid calendar date/time; got: {val!r}"
            ) from err
    raise SourceEvidenceError(f"{name} must be a valid RFC3339 timestamp; got: {val!r}")


def _to_rfc3339(val: Any) -> str:
    return _validate_or_convert_rfc3339(val, name="timestamp")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


ADMITTED_SOCIAL_PLATFORMS = frozenset({"stocktwits"})


def _validate_admitted_platform(platform: str) -> str:
    clean = str(platform or "").strip().lower()
    if clean not in ADMITTED_SOCIAL_PLATFORMS:
        raise SourceEvidenceError(
            f"Social platform '{platform}' is not an admitted social provider; "
            f"admitted platforms are: {sorted(ADMITTED_SOCIAL_PLATFORMS)}"
        )
    return clean


@dataclass(frozen=True)
class AdmittedSocialMediaAdapter(SourceConnectorProvider):
    """Governed social market-discussion adapter with trust and tombstone policies."""

    connector_id: str = SOCIAL_ADMITTED_CONNECTOR_ID
    platform: str = "stocktwits"
    symbols: Sequence[str] | None = None
    secret_ref_id: str = "env://STOCKTWITS_API_KEY"
    max_records: int = 100
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def resolve_api_key(self) -> str | None:
        if self.secret_ref_id and self.secret_ref_id.startswith("env://"):
            var_name = self.secret_ref_id[len("env://"):].strip()
            val = os.getenv(var_name, "").strip()
            if val:
                return val
        return os.getenv("STOCKTWITS_API_KEY") or os.getenv("STOCKTWITS_ACCESS_TOKEN")

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type=SourceType.SOCIAL,
            provider="StockTwits",
            license_scope="community_admitted",
            auth_type=AuthType.NONE,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE, secret_ref=self.secret_ref_id),
            license_policy=LicensePolicy(
                license_scope="community_admitted",
                allowed_use=("research", "search_index", "experiment", "sentiment_modeling"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/stocktwits-terms-v1",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=60,
                burst=5,
                retry_after_seconds=30,
                concurrency=2,
                policy_ref="source-ingest://policy/stocktwits-rate-limit-v1",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="StockTwits market discussion stream",
                homepage_url="https://stocktwits.com",
                docs_url="https://api.stocktwits.com/developers/docs",
                owner="StockTwits, Inc.",
                tags=("social", "market_discussion", "stocktwits", "sentiment", "research_only"),
            ),
            metadata={
                "source_class": "social",
                "source_type": "social",
                "dataset_schema_hash": SOCIAL_ADMITTED_SCHEMA_HASH,
                "auth_modes": ["none", "api_key"],
                "auth_type": "none",
                "secret_ref_id": self.secret_ref_id,
                "entitlement_tags": ["social-research"],
                "access_scope": ["research", "search_index", "experiment", "sentiment_modeling"],
                "allowed_use": ["research", "search_index", "experiment", "sentiment_modeling"],
                "allowed_host_patterns": ["api.stocktwits.com", "stocktwits.com"],
                "terms_ref": "source-ingest://license/stocktwits-terms-v1",
                "retention_policy": "tombstone_purge_on_deletion_30d_cache",
                "full_text_rights": "display_snippets_and_derived_features_only_no_raw_redistribution",
                "community_scope": "public_streams_only",
                "governance": {
                    "direct_execution_allowed": False,
                    "canonical_sink": "SourceRecord/EvidenceBundle",
                    "retention_days": 30,
                    "tombstone_propagation": True,
                    "full_text_redistribution_allowed": False,
                    "public_community_only": True,
                },
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "AdmittedSocialMediaAdapter.records_from_payload",
            "adapter_config": {
                "platform": self.platform,
                "symbols": list(self.symbols) if self.symbols else None,
                "max_records": self.max_records,
                "secret_ref_id": self.secret_ref_id,
            },
            "request": {
                "platform": self.platform,
                "symbols": list(self.symbols) if self.symbols else ["AAPL"],
                "secret_ref_id": self.secret_ref_id,
            },
            "next_watermark": None,
            "max_records": self.max_records,
        }

    def fetch_payload(
        self,
        symbol: str = "AAPL",
        *,
        timeout_seconds: float = 15.0,
    ) -> Any:
        """Fetch live public social market discussion stream from StockTwits OpenAPI."""
        url = f"{STOCKTWITS_API_BASE_URL}/streams/symbol/{symbol}.json"
        api_key = self.resolve_api_key()
        if api_key:
            url = f"{url}?access_token={urllib.parse.quote(api_key)}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "pantheon-source-ingest/0.1",
            },
        )
        with open_external_url(
            request,
            caller="source_ingest.stocktwits_discussion",
            timeout=timeout_seconds,
        ) as response:
            raw_bytes = _read_bounded_response(response, max_bytes=2097152)
            return json.loads(raw_bytes.decode("utf-8"))


    def records_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        platform: str = "stocktwits",
        symbols: Sequence[str] | None = None,
        max_records: int | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        admitted_platform = _validate_admitted_platform(platform)
        resolved_symbols = symbols if symbols is not None else self.symbols
        limit = max_records or self.max_records
        normalized_rows = self.normalized_rows_from_payload(
            payload,
            platform=admitted_platform,
            symbols=resolved_symbols,
            max_records=limit,
        )
        connector_instance = self.connector()
        records: list[SourceRecord] = []

        for row in normalized_rows[:limit]:
            post_id = str(row["post_id"])
            event_time = str(row["event_time"])
            available_time = str(row["available_time"])
            row_hash = _stable_hash(row)
            source_id = f"social:{admitted_platform}:{post_id}:{row_hash}"
            content_ref = f"social://{admitted_platform}/post/{post_id}"

            unvalidated_record = SourceRecord(
                source_id=source_id,
                connector_id=self.connector_id,
                source_type=SourceType.SOCIAL,
                title=f"Social post [{admitted_platform}] {post_id} on {event_time[:10]}",
                content_ref=content_ref,
                metadata={
                    "source_class": "social",
                    "provider": "StockTwits",
                    "platform": admitted_platform,
                    "author_id_hash": row["author_id_hash"],
                    "post_id": post_id,
                    "thread_id": row.get("thread_id"),
                    "platform_policy_ref": row.get("platform_policy_ref", "source-ingest://license/stocktwits-terms-v1"),
                    "trust_score": row["trust_score"],
                    "is_bot": row.get("is_bot", False),
                    "is_moderated": row.get("is_moderated", False),
                    "moderation_flags": list(row.get("moderation_flags", [])),
                    "is_tombstone": row.get("is_tombstone", False),
                    "sentiment": dict(row.get("sentiment", {})),
                    "symbols": list(row.get("symbols", [])),
                    "body": str(row.get("body", "")),
                    "event_time": event_time,
                    "available_time": available_time,
                    "ingest_time": row.get("ingest_time", _utc_now()),
                    "access_scope": ["research", "search_index", "experiment", "sentiment_modeling"],
                    "license_scope": "community_admitted",
                    "entitlement_tags": ["social-research"],
                    "schema_hash": SOCIAL_ADMITTED_SCHEMA_HASH,
                    "raw_row": dict(row.get("raw_row", {})),
                },
                trace_id=trace_id,
            )
            # Enforce external sources policy validation
            validated_record = validate_external_source_record(unvalidated_record, connector=connector_instance)
            records.append(validated_record)

        return tuple(records)

    def normalized_rows_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        platform: str = "stocktwits",
        symbols: Sequence[str] | None = None,
        max_records: int | None = None,
    ) -> tuple[dict[str, Any], ...]:
        admitted_platform = _validate_admitted_platform(platform)
        resolved_symbols = symbols if symbols is not None else self.symbols
        target_symbols = {str(s).strip().upper() for s in (resolved_symbols or ())} if resolved_symbols else None
        limit = max_records or self.max_records
        raw_items: list[Mapping[str, Any]] = []
        if isinstance(payload, Mapping):
            for key in ("items", "messages", "posts", "data", "results"):
                if isinstance(payload.get(key), list):
                    raw_items = [item for item in payload[key] if isinstance(item, Mapping)]
                    break
            if not raw_items and ("post_id" in payload or "id" in payload or "body" in payload):
                raw_items = [payload]
        elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
            raw_items = [item for item in payload if isinstance(item, Mapping)]

        results: list[dict[str, Any]] = []
        for raw in raw_items:
            norm = self._normalize_item(raw, platform=admitted_platform)
            if norm is not None:
                if target_symbols and norm.get("symbols"):
                    if not any(sym in target_symbols for sym in norm["symbols"]):
                        continue
                results.append(norm)
                if limit and len(results) >= limit:
                    break

        return tuple(results)

    def _normalize_item(self, raw: Mapping[str, Any], *, platform: str) -> dict[str, Any] | None:
        admitted_platform = _validate_admitted_platform(platform)
        post_id = _text(raw.get("post_id") or raw.get("id") or raw.get("message_id"))
        if not post_id:
            return None

        # Author hashing (avoid hashing mutable full user object or leaking raw PII user handles directly)
        user_data = raw.get("user")
        if isinstance(user_data, Mapping):
            raw_author = _text(user_data.get("id") or user_data.get("username") or user_data.get("user_id") or "anonymous")
        else:
            raw_author = _text(raw.get("author") or raw.get("user_id") or raw.get("username") or "anonymous")
        author_id_hash = raw.get("author_id_hash") or hashlib.sha256(raw_author.encode("utf-8")).hexdigest()[:16]

        # Trust score
        trust_raw = raw.get("trust_score")
        if trust_raw is None:
            trust_score = 0.5  # default moderate trust
        else:
            try:
                trust_score = float(trust_raw)
            except (ValueError, TypeError):
                trust_score = 0.0

        if trust_score < 0.0 or trust_score > 1.0:
            raise SourceEvidenceError("trust_score must be between 0.0 and 1.0")

        # Time handling
        event_time_raw = _to_rfc3339(raw.get("event_time") or raw.get("created_at") or raw.get("published_at") or raw.get("time") or _utc_now())
        available_time_raw = _to_rfc3339(raw.get("available_time") or event_time_raw)

        is_tombstone = bool(raw.get("is_tombstone", False) or raw.get("deleted", False))

        # Sentiment representation: platform-tagged sentiment vs NLP model vs tombstone
        if is_tombstone:
            sentiment = {
                "label": "neutral",
                "score": 0.0,
                "model_version": "tombstone",
                "is_derived": False,
            }
        else:
            sentiment_payload = raw.get("sentiment")
            entities_payload = raw.get("entities")
            platform_sentiment = None
            if isinstance(entities_payload, Mapping):
                ent_sentiment = entities_payload.get("sentiment")
                if isinstance(ent_sentiment, Mapping):
                    platform_sentiment = ent_sentiment.get("basic")

            if isinstance(sentiment_payload, Mapping):
                sentiment = {
                    "label": _text(sentiment_payload.get("label"), "neutral"),
                    "score": float(sentiment_payload.get("score", 0.0)),
                    "model_version": _text(sentiment_payload.get("model_version"), "user_or_model_specified"),
                    "is_derived": bool(sentiment_payload.get("is_derived", True)),
                }
            elif platform_sentiment:
                plat_label = str(platform_sentiment).strip().lower()
                score = 1.0 if plat_label == "bullish" else (-1.0 if plat_label == "bearish" else 0.0)
                sentiment = {
                    "label": plat_label,
                    "score": score,
                    "model_version": "stocktwits_platform_sentiment.v1",
                    "is_derived": False,
                }
            else:
                sentiment = {
                    "label": "neutral",
                    "score": 0.0,
                    "model_version": "unspecified",
                    "is_derived": False,
                }

        symbols = raw.get("symbols")
        symbol_list: list[str] = []
        if isinstance(symbols, Sequence) and not isinstance(symbols, (str, bytes)):
            for s in symbols:
                if isinstance(s, Mapping):
                    sym = _text(s.get("symbol") or s.get("ticker") or s.get("id"))
                    if sym:
                        symbol_list.append(sym.upper())
                elif isinstance(s, str) and s.strip():
                    symbol_list.append(s.strip().upper())
        elif isinstance(symbols, str) and symbols.strip():
            symbol_list.append(symbols.strip().upper())

        # Tombstone body & raw_row sanitation: never retain deleted body or raw user identities
        if is_tombstone:
            body = ""
            raw_row = {
                "post_id": post_id,
                "is_tombstone": True,
                "deleted": True,
                "event_time": event_time_raw,
                "available_time": available_time_raw,
                "author_id_hash": author_id_hash,
            }
        else:
            body = _text(raw.get("body") or raw.get("content") or raw.get("text"))
            raw_row = dict(raw)

        return {
            "platform": admitted_platform,
            "post_id": post_id,
            "thread_id": _text(raw.get("thread_id") or raw.get("parent_id")),
            "author_id_hash": author_id_hash,
            "platform_policy_ref": _text(raw.get("platform_policy_ref"), "source-ingest://license/stocktwits-terms-v1"),
            "trust_score": trust_score,
            "is_bot": bool(raw.get("is_bot", False)),
            "is_moderated": bool(raw.get("is_moderated", False)),
            "moderation_flags": list(raw.get("moderation_flags") or []),
            "is_tombstone": is_tombstone,
            "sentiment": sentiment,
            "symbols": symbol_list,
            "body": body,
            "event_time": event_time_raw,
            "available_time": available_time_raw,
            "ingest_time": _to_rfc3339(raw.get("ingest_time") or _utc_now()),
            "raw_row": raw_row,
        }

    def source_health_from_result(self, result: Any) -> SourceHealth:
        watermark = getattr(result, "watermark", None)
        run = getattr(result, "run", None)
        status = getattr(run, "status", "failed")
        run_status = status.value if hasattr(status, "value") else str(status)
        finished_at = getattr(run, "finished_at", None)
        finished = finished_at.isoformat().replace("+00:00", "Z") if hasattr(finished_at, "isoformat") else finished_at
        return SourceHealth(
            source_id=self.connector_id,
            source_kind="data_source",
            status="ok" if run_status == "completed" else "failed",
            last_success_at=finished if run_status == "completed" else None,
            last_failure_at=finished if run_status != "completed" else None,
            latest_watermark=getattr(watermark, "value", None),
            row_count_last_run=int(getattr(run, "normalized_count", 0) or 0),
            rejected_count_last_run=int(getattr(run, "rejected_count", 0) or 0),
            schema_hash=SOCIAL_ADMITTED_SCHEMA_HASH,
            metadata={
                "connector_id": self.connector_id,
                "ingest_run_id": getattr(run, "ingest_run_id", None),
                "source_type": "social",
            },
        )
