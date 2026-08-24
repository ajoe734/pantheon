"""External Alpha DB connector and signal record adapter (SD-SRCM-05 §7.5).

Governed ingestion for vendor-neutral external alpha databases and factor streams:
- Structured AlphaSignalRecord contract (alpha_signal_record.v1)
- Point-in-time survivorship and corporate-action adjustment policy tracking
- License and entitlement tag propagation
- Strict governance: research/experimentation only, never direct execution
- Strict invariant: example-alpha-db is a test fixture only and is never configured or live.
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


ALPHA_DB_VENDOR_CONNECTOR_ID = "alpha-db-vendor-signals"
ALPHA_SIGNAL_RECORD_SCHEMA_VERSION = "alpha_signal_record.v1"
ALPHA_SIGNAL_SCHEMA_HASH = "alpha_signal_record.v1"
FMP_API_BASE_URL = "https://financialmodelingprep.com/api/v3"


import re

def _read_bounded_response(response: Any, max_bytes: int = 4194304, chunk_size: int = 65536) -> bytes:
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


def _to_rfc3339(val: Any) -> str:
    s = _text(val)
    if not s:
        return _utc_now()
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$", s):
        return s
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", s):
        return s.replace(" ", "T") + "Z"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s + "T00:00:00Z"
    return s


def _validate_rfc3339(val: str, name: str) -> None:
    s = _text(val)
    if not s or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$", s):
        raise SourceEvidenceError(f"{name} must be a valid RFC3339 timestamp; got: {val!r}")
    try:
        iso_str = s.replace("Z", "+00:00")
        datetime.fromisoformat(iso_str)
    except Exception as err:
        raise SourceEvidenceError(
            f"{name} must be a valid RFC3339 timestamp with valid calendar date/time; got: {val!r}"
        ) from err


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _require(value: Any, name: str) -> str:
    text = _text(value)
    if not text:
        raise SourceEvidenceError(f"{name} is required for AlphaSignalRecord")
    return text


@dataclass(frozen=True)
class AlphaSignalRecord:
    """Immutable structured alpha signal record (schema: alpha_signal_record.v1)."""

    alpha_vendor_id: str
    signal_id: str
    signal_version: str
    field_schema_version: str
    universe: Sequence[str]
    entity_id: str
    event_time: str
    as_of_time: str
    available_time: str
    values: Mapping[str, Any]
    units: Mapping[str, str]
    corporate_action_policy: str = "raw"
    survivorship_policy: str = "point_in_time"
    license_scope: str = "vendor"
    allowed_use: Sequence[str] = ("research", "experiment")
    entitlement_tags: Sequence[str] = ("alpha-research",)
    provider_record_ref: str = ""
    currency: str | None = None
    ingest_time: str = field(default_factory=_utc_now)
    body_hash: str = field(default="", init=False)
    schema_version: str = field(default=ALPHA_SIGNAL_RECORD_SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", ALPHA_SIGNAL_RECORD_SCHEMA_VERSION)
        _require(self.alpha_vendor_id, "alpha_vendor_id")
        _require(self.signal_id, "signal_id")
        _require(self.signal_version, "signal_version")
        _require(self.field_schema_version, "field_schema_version")
        _require(self.entity_id, "entity_id")
        _require(self.event_time, "event_time")
        _require(self.as_of_time, "as_of_time")
        _require(self.available_time, "available_time")
        if not self.provider_record_ref:
            object.__setattr__(
                self,
                "provider_record_ref",
                f"ref://{self.alpha_vendor_id}/{self.signal_id}/{self.entity_id}",
            )
        _require(self.provider_record_ref, "provider_record_ref")

        # Validate RFC3339 timestamps
        _validate_rfc3339(self.event_time, "event_time")
        _validate_rfc3339(self.as_of_time, "as_of_time")
        _validate_rfc3339(self.available_time, "available_time")
        _validate_rfc3339(self.ingest_time, "ingest_time")

        # Universe string vs sequence normalization
        if isinstance(self.universe, str):
            object.__setattr__(self, "universe", tuple(u.strip() for u in self.universe.split(",") if u.strip()))
        elif isinstance(self.universe, Sequence) and not isinstance(self.universe, (bytes, bytearray)):
            object.__setattr__(self, "universe", tuple(str(u).strip() for u in self.universe if str(u).strip()))
        else:
            object.__setattr__(self, "universe", tuple())

        if not self.universe:
            raise SourceEvidenceError("universe must not be empty for AlphaSignalRecord")
        if not self.values:
            raise SourceEvidenceError("values must not be empty for AlphaSignalRecord")

        if self.corporate_action_policy not in ("provider_adjusted", "pantheon_adjusted", "raw"):
            raise SourceEvidenceError(f"invalid corporate_action_policy: {self.corporate_action_policy}")
        if self.survivorship_policy not in ("point_in_time", "survivor_only"):
            raise SourceEvidenceError(f"invalid survivorship_policy: {self.survivorship_policy}")
        if self.license_scope not in ("restricted", "vendor", "enterprise_research", "official_reference"):
            raise SourceEvidenceError(f"invalid license_scope: {self.license_scope}")

        canonical_body = {
            "alpha_vendor_id": self.alpha_vendor_id,
            "signal_id": self.signal_id,
            "signal_version": self.signal_version,
            "field_schema_version": self.field_schema_version,
            "universe": list(self.universe),
            "entity_id": self.entity_id,
            "event_time": self.event_time,
            "as_of_time": self.as_of_time,
            "available_time": self.available_time,
            "values": dict(self.values),
            "units": dict(self.units),
            "currency": self.currency,
            "corporate_action_policy": self.corporate_action_policy,
            "survivorship_policy": self.survivorship_policy,
            "license_scope": self.license_scope,
            "allowed_use": list(self.allowed_use),
            "entitlement_tags": list(self.entitlement_tags),
            "provider_record_ref": self.provider_record_ref,
        }
        object.__setattr__(self, "body_hash", _stable_hash(canonical_body))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "alpha_vendor_id": self.alpha_vendor_id,
            "signal_id": self.signal_id,
            "signal_version": self.signal_version,
            "field_schema_version": self.field_schema_version,
            "universe": list(self.universe),
            "entity_id": self.entity_id,
            "event_time": self.event_time,
            "as_of_time": self.as_of_time,
            "available_time": self.available_time,
            "ingest_time": self.ingest_time,
            "values": dict(self.values),
            "units": dict(self.units),
            "currency": self.currency,
            "corporate_action_policy": self.corporate_action_policy,
            "survivorship_policy": self.survivorship_policy,
            "license_scope": self.license_scope,
            "allowed_use": list(self.allowed_use),
            "entitlement_tags": list(self.entitlement_tags),
            "provider_record_ref": self.provider_record_ref,
            "body_hash": self.body_hash,
        }


SUPPORTED_FMP_INDICATORS: dict[str, int] = {
    "rsi": 14,
    "sma": 20,
    "ema": 20,
    "wma": 20,
    "dema": 20,
    "tema": 20,
    "williams": 14,
    "standarddeviation": 20,
}


def _resolve_fmp_indicator(signal_id: str) -> tuple[str, int]:
    """Parse technical indicator type and period from signal_id or reject unsupported signals."""
    s = _text(signal_id).lower().replace("-", "_")
    m = re.match(r"^(?:technical_)?([a-z]+)_(\d+)(?:d)?$", s)
    if m:
        ind_type = m.group(1)
        period = int(m.group(2))
        if ind_type in SUPPORTED_FMP_INDICATORS:
            return ind_type, period
    if s in SUPPORTED_FMP_INDICATORS:
        return s, SUPPORTED_FMP_INDICATORS[s]
    m_tech = re.match(r"^technical_([a-z]+)$", s)
    if m_tech:
        ind_type = m_tech.group(1)
        if ind_type in SUPPORTED_FMP_INDICATORS:
            return ind_type, SUPPORTED_FMP_INDICATORS[ind_type]
    raise SourceEvidenceError(
        f"Unsupported signal_id or indicator type for FMP alpha DB: {signal_id!r}; "
        f"supported indicator types are: {sorted(SUPPORTED_FMP_INDICATORS.keys())}"
    )


@dataclass(frozen=True)
class ExternalAlphaDbAdapter(SourceConnectorProvider):
    """Governed External Alpha DB connector adapter (SD §7.5)."""

    connector_id: str = ALPHA_DB_VENDOR_CONNECTOR_ID
    secret_ref_id: str = "env://ALPHA_DB_API_KEY"
    max_records: int = 100
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def resolve_api_key(self) -> str | None:
        return (
            os.getenv("ALPHA_DB_API_KEY")
            or os.getenv("FMP_API_KEY")
            or os.getenv("FINANCIAL_MODELING_PREP_API_KEY")
        )

    def __post_init__(self) -> None:
        # Invariant: example-alpha-db cannot be used as a configured/live connector
        if self.connector_id == "example-alpha-db":
            raise SourceEvidenceError(
                "example-alpha-db is a test fixture only and cannot be configured or marked live"
            )

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type=SourceType.ALPHA_DB,
            provider="Financial Modeling Prep",
            license_scope="vendor",
            auth_type=AuthType.API_KEY,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.API_KEY, secret_ref=self.secret_ref_id),
            license_policy=LicensePolicy(
                license_scope="vendor",
                allowed_use=("research", "experiment", "feature_generation"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/fmp-alpha-db-terms-v1",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=30,
                burst=5,
                retry_after_seconds=30,
                concurrency=2,
                policy_ref="source-ingest://policy/alpha-db-rate-limit-v1",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="Financial Modeling Prep Alpha & Factor Signals",
                homepage_url="https://financialmodelingprep.com",
                docs_url="https://site.financialmodelingprep.com/developer/docs",
                owner="Financial Modeling Prep",
                tags=("alpha_db", "signals", "factors", "fmp", "research_only"),
            ),
            metadata={
                "source_class": "alpha_signal",
                "source_type": "alpha_db",
                "dataset_schema_hash": ALPHA_SIGNAL_SCHEMA_HASH,
                "entitlement_tags": ["alpha_db-research"],
                "access_scope": ["research"],
                "allowed_host_patterns": [
                    "financialmodelingprep.com",
                    "api.finmindtrade.com",
                    "api.vendor-factors.io",
                ],
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
            "adapter": "ExternalAlphaDbAdapter.records_from_payload",
            "adapter_config": {
                "max_records": self.max_records,
                "secret_ref_id": "env://ALPHA_DB_API_KEY",
            },
            "request": {
                "alpha_vendor_id": "fmp-alpha-factors",
                "signal_id": "technical_rsi_14d",
                "universe": ["US_EQUITY"],
            },
            "next_watermark": None,
            "max_records": self.max_records,
        }

    def fetch_payload(
        self,
        entity_id: str = "AAPL",
        signal_id: str = "technical_rsi_14d",
        *,
        timeout_seconds: float = 15.0,
    ) -> Any:
        """Fetch live factor/signal payload from vendor OpenAPI endpoint."""
        ind_type, period = _resolve_fmp_indicator(signal_id)
        api_key = self.resolve_api_key()
        if not api_key:
            raise SourceEvidenceError(
                "External Alpha DB (FMP) fetch requires secret_ref_id env://ALPHA_DB_API_KEY; none found in environment"
            )
        url = f"{FMP_API_BASE_URL}/technical_indicator/daily/{entity_id}?type={ind_type}&period={period}&apikey={urllib.parse.quote(api_key)}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "pantheon-source-ingest/0.1",
            },
        )
        with open_external_url(
            request,
            caller="source_ingest.alpha_db_vendor",
            timeout=timeout_seconds,
        ) as response:
            raw_bytes = _read_bounded_response(response, max_bytes=4194304)
            return json.loads(raw_bytes.decode("utf-8"))


    def records_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        alpha_vendor_id: str = "fmp-alpha-factors",
        signal_id: str = "technical_rsi_14d",
        signal_version: str = "v1",
        field_schema_version: str = "v1",
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        if alpha_vendor_id == "example-alpha-db":
            raise SourceEvidenceError("example-alpha-db is test fixture only and cannot be configured or marked live")

        normalized_rows = self.normalized_rows_from_payload(
            payload,
            alpha_vendor_id=alpha_vendor_id,
            signal_id=signal_id,
            signal_version=signal_version,
            field_schema_version=field_schema_version,
        )
        connector_instance = self.connector()
        records: list[SourceRecord] = []

        for item in normalized_rows[: self.max_records]:
            entity_id = item.entity_id
            event_time = item.event_time
            as_of_time = item.as_of_time
            available_time = item.available_time
            content_hash = item.body_hash
            source_id = f"alpha_db:{alpha_vendor_id}:{signal_id}:{entity_id}:{content_hash[:16]}"
            content_ref = f"alpha-db://{alpha_vendor_id}/{signal_id}/{signal_version}/{entity_id}"

            unvalidated_record = SourceRecord(
                source_id=source_id,
                connector_id=self.connector_id,
                source_type=SourceType.ALPHA_DB,
                title=f"Alpha signal [{signal_id}:{signal_version}] {entity_id} as-of {as_of_time[:10]}",
                content_ref=content_ref,
                metadata={
                    "source_class": "alpha_signal",
                    "provider": "Financial Modeling Prep",
                    "alpha_vendor_id": item.alpha_vendor_id,
                    "signal_id": item.signal_id,
                    "signal_version": item.signal_version,
                    "field_schema": item.field_schema_version,
                    "field_schema_version": item.field_schema_version,
                    "universe": list(item.universe),
                    "entity_id": item.entity_id,
                    "event_time": event_time,
                    "as_of_time": as_of_time,
                    "available_time": available_time,
                    "ingest_time": item.ingest_time,
                    "values": dict(item.values),
                    "units": dict(item.units),
                    "currency": item.currency,
                    "corporate_action_policy": item.corporate_action_policy,
                    "survivorship_policy": item.survivorship_policy,
                    "license_scope": item.license_scope,
                    "allowed_use": list(item.allowed_use),
                    "entitlement_tags": list(item.entitlement_tags),
                    "provider_record_ref": item.provider_record_ref,
                    "body_hash": item.body_hash,
                    "access_scope": ["research"],
                    "schema_hash": ALPHA_SIGNAL_SCHEMA_HASH,
                    "signal_record": item.to_dict(),
                    "body": json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True),
                },
                trace_id=trace_id,
            )
            validated_record = validate_external_source_record(unvalidated_record, connector=connector_instance)
            records.append(validated_record)

        return tuple(records)

    def normalized_rows_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        alpha_vendor_id: str = "fmp-alpha-factors",
        signal_id: str = "technical_rsi_14d",
        signal_version: str = "v1",
        field_schema_version: str = "v1",
    ) -> tuple[AlphaSignalRecord, ...]:
        raw_items: list[Mapping[str, Any]] = []
        if isinstance(payload, Mapping):
            for key in ("signals", "factors", "items", "records", "data", "results"):
                if isinstance(payload.get(key), list):
                    raw_items = [item for item in payload[key] if isinstance(item, Mapping)]
                    break
            if not raw_items and ("entity_id" in payload or "symbol" in payload or "values" in payload):
                raw_items = [payload]
        elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
            raw_items = [item for item in payload if isinstance(item, Mapping)]

        results: list[AlphaSignalRecord] = []
        for raw in raw_items:
            norm = self._normalize_signal(
                raw,
                alpha_vendor_id=alpha_vendor_id,
                signal_id=signal_id,
                signal_version=signal_version,
                field_schema_version=field_schema_version,
            )
            if norm is not None:
                results.append(norm)

        return tuple(results)

    def _normalize_signal(
        self,
        raw: Mapping[str, Any],
        *,
        alpha_vendor_id: str,
        signal_id: str,
        signal_version: str,
        field_schema_version: str,
    ) -> AlphaSignalRecord | None:
        entity_id = _text(raw.get("entity_id") or raw.get("symbol") or raw.get("asset_id"))
        if not entity_id:
            return None

        event_time = _to_rfc3339(raw.get("event_time") or raw.get("as_of_time") or raw.get("date") or _utc_now())
        as_of_time = _to_rfc3339(raw.get("as_of_time") or event_time)
        available_time = _to_rfc3339(raw.get("available_time") or as_of_time)

        # Values
        raw_values = raw.get("values")
        if isinstance(raw_values, Mapping):
            values = dict(raw_values)
        else:
            # Extract non-metadata numerical/factor fields
            excluded_keys = {
                "entity_id", "symbol", "asset_id", "event_time", "as_of_time",
                "available_time", "alpha_vendor_id", "signal_id", "signal_version",
                "field_schema_version", "universe", "units", "currency",
                "corporate_action_policy", "survivorship_policy", "license_scope",
                "allowed_use", "entitlement_tags", "provider_record_ref",
                "date", "Date", "time", "timestamp", "ingest_time", "body_hash", "schema_version",
            }
            values = {
                k: v for k, v in raw.items()
                if k not in excluded_keys and isinstance(v, (int, float, bool))
            }
        if not values:
            return None

        raw_units = raw.get("units")
        units = dict(raw_units) if isinstance(raw_units, Mapping) else {k: "score" for k in values}

        universe_raw = raw.get("universe") or ["US_EQUITY"]
        if isinstance(universe_raw, str):
            universe = [u.strip() for u in universe_raw.split(",") if u.strip()]
        elif isinstance(universe_raw, Sequence) and not isinstance(universe_raw, (bytes, bytearray)):
            universe = [str(u).strip() for u in universe_raw if str(u).strip()]
        else:
            universe = [str(universe_raw)]

        resolved_signal_id = _text(raw.get("signal_id"), signal_id)
        resolved_vendor_id = _text(raw.get("alpha_vendor_id"), alpha_vendor_id)

        return AlphaSignalRecord(
            alpha_vendor_id=resolved_vendor_id,
            signal_id=resolved_signal_id,
            signal_version=_text(raw.get("signal_version"), signal_version),
            field_schema_version=_text(raw.get("field_schema_version"), field_schema_version),
            universe=universe,
            entity_id=entity_id,
            event_time=event_time,
            as_of_time=as_of_time,
            available_time=available_time,
            values=values,
            units=units,
            currency=raw.get("currency"),
            corporate_action_policy=_text(raw.get("corporate_action_policy"), "raw"),
            survivorship_policy=_text(raw.get("survivorship_policy"), "point_in_time"),
            license_scope=_text(raw.get("license_scope"), "vendor"),
            allowed_use=tuple(raw.get("allowed_use") or ("research", "experiment")),
            entitlement_tags=tuple(raw.get("entitlement_tags") or ("alpha_db-research",)),
            provider_record_ref=_text(raw.get("provider_record_ref"), f"ref://{resolved_vendor_id}/{resolved_signal_id}/{entity_id}"),
            ingest_time=_to_rfc3339(raw.get("ingest_time") or _utc_now()),
        )

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
            schema_hash=ALPHA_SIGNAL_SCHEMA_HASH,
            metadata={
                "connector_id": self.connector_id,
                "ingest_run_id": getattr(run, "ingest_run_id", None),
                "source_type": "alpha_db",
            },
        )
