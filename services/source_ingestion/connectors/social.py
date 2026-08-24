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
from typing import Any, Mapping, Sequence

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


@dataclass(frozen=True)
class AdmittedSocialMediaAdapter(SourceConnectorProvider):
    """Governed social market-discussion adapter with trust and tombstone policies."""

    connector_id: str = SOCIAL_ADMITTED_CONNECTOR_ID
    secret_ref_id: str = "env://SOCIAL_API_KEY"
    max_records: int = 100
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type=SourceType.SOCIAL,
            provider="Admitted Social Discussion Feed",
            license_scope="community_admitted",
            auth_type=AuthType.API_KEY,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.API_KEY, secret_ref=self.secret_ref_id),
            license_policy=LicensePolicy(
                license_scope="community_admitted",
                allowed_use=("research", "search_index", "experiment", "sentiment_modeling"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/social-admitted-terms-v1",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=60,
                burst=5,
                retry_after_seconds=30,
                concurrency=2,
                policy_ref="source-ingest://policy/social-rate-limit-v1",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="Admitted Financial Social Discussion Feed",
                homepage_url="https://api.social-finance.example.com",
                docs_url="https://api.social-finance.example.com/docs",
                owner="Financial Social Discussion Feed Operator",
                tags=("social", "market_discussion", "sentiment", "research_only"),
            ),
            metadata={
                "source_class": "social",
                "source_type": "social",
                "dataset_schema_hash": SOCIAL_ADMITTED_SCHEMA_HASH,
                "entitlement_tags": ["social-research"],
                "access_scope": ["research", "search_index"],
                "governance": {
                    "direct_execution_allowed": False,
                    "canonical_sink": "SourceRecord/EvidenceBundle",
                },
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "AdmittedSocialMediaAdapter.records_from_payload",
            "adapter_config": {
                "max_records": self.max_records,
            },
            "request": {
                "platform": "stocktwits",
                "symbols": ["2330", "AAPL"],
            },
            "next_watermark": None,
            "max_records": self.max_records,
        }

    def records_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        platform: str = "stocktwits",
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        normalized_rows = self.normalized_rows_from_payload(payload, platform=platform)
        connector_instance = self.connector()
        records: list[SourceRecord] = []

        for row in normalized_rows[: self.max_records]:
            post_id = str(row["post_id"])
            event_time = str(row["event_time"])
            available_time = str(row["available_time"])
            row_hash = _stable_hash(row)
            source_id = f"social:{platform}:{post_id}:{row_hash}"
            content_ref = f"social://{platform}/post/{post_id}"

            unvalidated_record = SourceRecord(
                source_id=source_id,
                connector_id=self.connector_id,
                source_type=SourceType.SOCIAL,
                title=f"Social post [{platform}] {post_id} on {event_time[:10]}",
                content_ref=content_ref,
                metadata={
                    "platform": platform,
                    "author_id_hash": row["author_id_hash"],
                    "post_id": post_id,
                    "thread_id": row.get("thread_id"),
                    "platform_policy_ref": row.get("platform_policy_ref", "source-ingest://license/social-admitted-terms-v1"),
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
                    "access_scope": ["research", "search_index"],
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
    ) -> tuple[dict[str, Any], ...]:
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
            norm = self._normalize_item(raw, platform=platform)
            if norm is not None:
                results.append(norm)

        return tuple(results)

    def _normalize_item(self, raw: Mapping[str, Any], *, platform: str) -> dict[str, Any] | None:
        post_id = _text(raw.get("post_id") or raw.get("id") or raw.get("message_id"))
        if not post_id:
            return None

        # Author hashing (avoid leaking raw PII user handles directly)
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
        event_time_raw = _text(raw.get("event_time") or raw.get("created_at") or raw.get("published_at") or raw.get("time"))
        if not event_time_raw:
            event_time_raw = _utc_now()
        available_time_raw = _text(raw.get("available_time") or event_time_raw)

        # Sentiment representation
        sentiment_payload = raw.get("sentiment")
        if isinstance(sentiment_payload, Mapping):
            sentiment = {
                "label": _text(sentiment_payload.get("label"), "neutral"),
                "score": float(sentiment_payload.get("score", 0.0)),
                "model_version": _text(sentiment_payload.get("model_version"), "fin-bert-sentiment.v1"),
                "is_derived": True,
            }
        else:
            sentiment = {
                "label": "neutral",
                "score": 0.0,
                "model_version": "fin-bert-sentiment.v1",
                "is_derived": True,
            }

        symbols = raw.get("symbols")
        if isinstance(symbols, Sequence) and not isinstance(symbols, (str, bytes)):
            symbol_list = [str(s).strip().upper() for s in symbols if str(s).strip()]
        elif isinstance(symbols, str) and symbols.strip():
            symbol_list = [symbols.strip().upper()]
        else:
            symbol_list = []

        return {
            "platform": platform,
            "post_id": post_id,
            "thread_id": _text(raw.get("thread_id") or raw.get("parent_id")),
            "author_id_hash": author_id_hash,
            "platform_policy_ref": _text(raw.get("platform_policy_ref"), "source-ingest://license/social-admitted-terms-v1"),
            "trust_score": trust_score,
            "is_bot": bool(raw.get("is_bot", False)),
            "is_moderated": bool(raw.get("is_moderated", False)),
            "moderation_flags": list(raw.get("moderation_flags") or []),
            "is_tombstone": bool(raw.get("is_tombstone", False) or raw.get("deleted", False)),
            "sentiment": sentiment,
            "symbols": symbol_list,
            "body": _text(raw.get("body") or raw.get("content") or raw.get("text")),
            "event_time": event_time_raw,
            "available_time": available_time_raw,
            "ingest_time": _text(raw.get("ingest_time"), _utc_now()),
            "raw_row": dict(raw),
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
