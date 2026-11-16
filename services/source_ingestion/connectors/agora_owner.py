"""Agora owner platform data source connector (§12.4, AGORA-PLATFORM-SOURCE-20261116).

Pulls Agora decision locks (C4 / A4), settled shadow arms, decision journal
entries, and servant insights (AgoraInsight) incrementally by cursor from the
Agora owner's internal service-token read route (/internal/agora/platform-source/records).

Invariants:
- Never reads the Agora database directly; all reads go through the Agora owner
  service-token route.
- Preserves full fields: trader identity (tenant_id, agora_user_id, user_id),
  account_id, broker_ids, and raw_text per operator decision (2026-11-16).
- Enforces point-in-time timestamps (event_time <= available_time <= ingest_time)
  and provenance='agora'.
- Records are facts only; nothing becomes a strategy, rule, or lesson without
  the existing validation gates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import urllib.error
import urllib.parse
import urllib.request

from services.knowledge.evidence import EvidenceBundleBuilder

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
    SourceRecordStatus,
    SourceType,
)
from .bounded_reader import read_bounded_response

AGORA_PLATFORM_SOURCE_CONNECTOR_ID = "agora-platform-source"
AGORA_OWNER_ADAPTER_TOKEN = "AgoraOwnerAdapter.records_from_payload"
AGORA_PLATFORM_SOURCE_DATASET = "agora_platform_records"
AGORA_PLATFORM_SOURCE_SCHEMA_HASH = "agora_platform_record.v1"
AGORA_PLATFORM_SOURCE_URL_ENV = "AGORA_PLATFORM_SOURCE_URL"
AGORA_PLATFORM_SOURCE_TOKEN_ENV = "AGORA_PLATFORM_SOURCE_SERVICE_TOKEN"
AGORA_PLATFORM_SOURCE_CURSOR_PATH_ENV = "AGORA_PLATFORM_SOURCE_CURSOR_PATH"
DEFAULT_AGORA_PLATFORM_SOURCE_URL = "http://operator-bff:8001/internal/agora/platform-source/records"
LOCAL_AGORA_PLATFORM_SOURCE_TOKEN = "pantheon-local-agora-platform-source-service-token"
VALID_RECORD_KINDS = frozenset({"decision_lock", "settled_arm", "decision_journal", "servant_insight"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_rfc3339(value: Any, field_name: str) -> Tuple[datetime, str]:
    text = _clean(value)
    if not text:
        raise SourceEvidenceError(f"Agora platform source record {field_name} is required")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceEvidenceError(
            f"Agora platform source record {field_name} must be ISO-8601/RFC3339: {value!r}"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt, dt.isoformat().replace("+00:00", "Z")


def _string_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _content_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgoraOwnerPullResult:
    """Result of an incremental cursor pull from the Agora owner route."""

    records: Tuple[SourceRecord, ...]
    next_cursor: Optional[str]
    has_more: bool
    raw_items: Tuple[Dict[str, Any], ...]


@dataclass(frozen=True)
class AgoraOwnerAdapter(SourceConnectorProvider):
    """Governed source-ingestion adapter reading Agora records from the Agora owner route."""

    connector_id: str = AGORA_PLATFORM_SOURCE_CONNECTOR_ID
    api_url: Optional[str] = None
    secret_ref_id: str = f"env://{AGORA_PLATFORM_SOURCE_TOKEN_ENV}"
    service_actor: str = "source-ingest"
    tenant_id: str = "default"
    max_records: int = 200
    timeout_seconds: float = 15.0
    cursor_state_path: Optional[str] = None
    transport: Optional[Callable[..., Mapping[str, Any]]] = None
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def resolve_api_url(self) -> str:
        url = (
            _clean(self.api_url)
            or _clean(os.getenv(AGORA_PLATFORM_SOURCE_URL_ENV))
            or DEFAULT_AGORA_PLATFORM_SOURCE_URL
        )
        return url

    def resolve_service_token(self) -> str:
        if self.secret_ref_id and self.secret_ref_id.startswith("env://"):
            env_name = self.secret_ref_id[len("env://") :].strip()
            if env_name in os.environ:
                token = os.environ[env_name].strip()
                if not token:
                    raise SourceEvidenceError(f"Agora service token env {env_name} is empty")
                return token
        if AGORA_PLATFORM_SOURCE_TOKEN_ENV in os.environ:
            token = os.environ[AGORA_PLATFORM_SOURCE_TOKEN_ENV].strip()
            if not token:
                raise SourceEvidenceError(f"Agora service token env {AGORA_PLATFORM_SOURCE_TOKEN_ENV} is empty")
            return token
        return LOCAL_AGORA_PLATFORM_SOURCE_TOKEN

    def _cursor_file(self) -> Path:
        configured = (
            _clean(self.cursor_state_path)
            or _clean(os.getenv(AGORA_PLATFORM_SOURCE_CURSOR_PATH_ENV))
        )
        if configured:
            return Path(configured)
        data_dir = Path(os.getenv("SOURCE_INGEST_DATA_DIR") or "/tmp/pantheon/source-ingest")
        return data_dir / f"agora_owner_cursor_{self.connector_id}.json"

    def load_resume_cursor(self) -> Optional[str]:
        path = self._cursor_file()
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        cursor = _clean(data.get("cursor")) if isinstance(data, Mapping) else ""
        return cursor or None

    def save_resume_cursor(self, cursor: Optional[str]) -> None:
        clean_cursor = _clean(cursor)
        if not clean_cursor:
            return
        path = self._cursor_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "connector_id": self.connector_id,
                    "cursor": clean_cursor,
                    "updated_at": _utc_now(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type=SourceType.INTERNAL_NOTE,
            provider="Agora Owner",
            license_scope="internal_agora_governed",
            auth_type=AuthType.SECRET_REF,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=AuthType.SECRET_REF,
                secret_ref=self.secret_ref_id,
                auth_scope=("agora.platform_source.read",),
                audience="operator-bff",
            ),
            license_policy=LicensePolicy(
                license_scope="internal_agora_governed",
                allowed_use=("research", "search_index", "experiment", "attribution", "consultation_case_file"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://policy/agora-platform-source-v1",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=120,
                burst=10,
                retry_after_seconds=15,
                concurrency=2,
                policy_ref="source-ingest://policy/agora-platform-source-rate-limit-v1",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="Agora Platform Data Source (Decision Locks, Settled Arms, Journal, Servant Insights)",
                homepage_url="https://pantheon.internal/agora",
                docs_url="https://pantheon.internal/docs/agora-platform-source",
                owner="agora-svc",
                tags=(
                    "agora",
                    "shadow_mode",
                    "decision_locks",
                    "settled_arms",
                    "decision_journal",
                    "servant_insights",
                    "research_only",
                ),
            ),
            metadata={
                "definition_id": AGORA_PLATFORM_SOURCE_CONNECTOR_ID,
                "provenance": "agora",
                "dataset": AGORA_PLATFORM_SOURCE_DATASET,
                "dataset_schema_hash": AGORA_PLATFORM_SOURCE_SCHEMA_HASH,
                "source_class": "internal_agora",
                "source_type": SourceType.INTERNAL_NOTE.value,
                "auth_modes": ["bearer_token"],
                "secret_ref_id": self.secret_ref_id,
                "entitlement_tags": ["agora_platform_source", "internal_research"],
                "access_scope": ["operator", "owner", "research"],
                "allowed_use": ["research", "search_index", "experiment", "attribution", "consultation_case_file"],
                "allowed_host_patterns": ["operator-bff", "127.0.0.1", "localhost"],
                "approved": True,
                "approval_status": "approved",
                "governance": {
                    "provenance": "agora",
                    "facts_only": True,
                    "canonical_sink": "SourceRecord/EvidenceBundle",
                    "direct_execution_allowed": False,
                    "lean_consumption": "research_only_not_direct_action",
                    "broker_consumption": "not_direct_action",
                    "validation_gates_required": True,
                    "environment_ceiling": "paper",
                },
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        adapter_cfg: Dict[str, Any] = {
            "api_url": self.resolve_api_url(),
            "secret_ref_id": self.secret_ref_id,
            "service_actor": self.service_actor,
            "tenant_id": self.tenant_id,
            "max_records": self.max_records,
        }
        if self.cursor_state_path is not None:
            adapter_cfg["cursor_state_path"] = str(self.cursor_state_path)
        return {
            "mode": "provider_owned_adapter",
            "adapter": AGORA_OWNER_ADAPTER_TOKEN,
            "adapter_config": adapter_cfg,
            "request": {
                "dataset": AGORA_PLATFORM_SOURCE_DATASET,
                "tenant_id": self.tenant_id,
                "max_records": self.max_records,
            },
            "next_watermark": None,
            "max_records": self.max_records,
            "allow_empty": True,
            "empty_reason": "Agora platform source incremental cursor pull returned no new records",
        }

    def fetch_payload(
        self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        kind: Optional[str] = None,
        tenant_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Mapping[str, Any]:
        """Fetch incremental Agora records from the Agora owner internal route."""
        resolved_tenant = (
            _clean(tenant_id)
            or _clean(self.tenant_id)
            or _clean(os.getenv("PANTHEON_TENANT_ID"))
            or _clean(os.getenv("PANTHEON_BFF_TENANT_ID"))
            or "default"
        )
        resolved_limit = max(1, min(int(limit or self.max_records or 200), 1000))
        token = self.resolve_service_token()

        if self.transport is not None:
            response_payload = self.transport(
                url=self.resolve_api_url(),
                cursor=cursor,
                limit=resolved_limit,
                kind=kind,
                tenant_id=resolved_tenant,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Pantheon-Service-Actor": self.service_actor,
                    "X-Tenant-Id": resolved_tenant,
                },
            )
            if not isinstance(response_payload, Mapping):
                raise SourceEvidenceError("Agora owner route returned non-object payload")
            return response_payload

        base_url = self.resolve_api_url()
        parsed_url = urllib.parse.urlsplit(base_url)
        query_params = dict(urllib.parse.parse_qsl(parsed_url.query, keep_blank_values=True))
        query_params["limit"] = str(resolved_limit)
        query_params["tenant_id"] = resolved_tenant
        if _clean(cursor):
            query_params["cursor"] = _clean(cursor)
        if _clean(kind):
            query_params["kind"] = _clean(kind)
        full_url = urllib.parse.urlunsplit(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                urllib.parse.urlencode(query_params),
                "",
            )
        )
        req = urllib.request.Request(
            full_url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "X-Pantheon-Service-Actor": self.service_actor,
                "X-Tenant-Id": resolved_tenant,
                "User-Agent": "pantheon-source-ingest-agora-owner/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=float(timeout_seconds or self.timeout_seconds)) as resp:
                raw_bytes = read_bounded_response(resp, max_bytes=16 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            raise SourceEvidenceError(
                f"Agora owner route HTTP {exc.code} failure: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise SourceEvidenceError(f"Agora owner route unreachable: {exc}") from exc

        try:
            parsed = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceEvidenceError("Agora owner route returned invalid JSON") from exc
        if not isinstance(parsed, Mapping):
            raise SourceEvidenceError("Agora owner route payload must be a JSON object")
        return parsed

    def pull(
        self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        kind: Optional[str] = None,
        tenant_id: Optional[str] = None,
        trace_id: str = "",
        ingest_time: Optional[str] = None,
        persist_cursor: bool = True,
    ) -> AgoraOwnerPullResult:
        effective_cursor = _clean(cursor) or self.load_resume_cursor()
        payload = self.fetch_payload(
            cursor=effective_cursor,
            limit=limit,
            kind=kind,
            tenant_id=tenant_id,
        )
        records = self.records_from_payload(
            payload,
            tenant_id=tenant_id,
            max_records=limit or self.max_records,
            trace_id=trace_id,
            ingest_time=ingest_time,
        )
        next_cursor = _clean(payload.get("next_cursor")) or effective_cursor
        if persist_cursor and next_cursor:
            self.save_resume_cursor(next_cursor)
        raw_items = tuple(
            dict(item)
            for item in (payload.get("items") or payload.get("records") or ())
            if isinstance(item, Mapping)
        )
        return AgoraOwnerPullResult(
            records=records,
            next_cursor=next_cursor,
            has_more=bool(payload.get("has_more", False)),
            raw_items=raw_items,
        )

    def records_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        tenant_id: Optional[str] = None,
        max_records: Optional[int] = None,
        trace_id: str = "",
        ingest_time: Optional[str] = None,
    ) -> Tuple[SourceRecord, ...]:
        """Convert Agora owner route payload into governed, point-in-time SourceRecords."""
        resolved_ingest_dt, resolved_ingest_iso = _parse_rfc3339(
            ingest_time or _utc_now(),
            "ingest_time",
        )
        default_tenant = (
            _clean(tenant_id)
            or (
                _clean(payload.get("tenant_id"))
                if isinstance(payload, Mapping)
                else ""
            )
            or _clean(self.tenant_id)
            or "default"
        )

        if isinstance(payload, Mapping):
            raw_items: List[Mapping[str, Any]] = []
            if isinstance(payload.get("items"), Sequence) and not isinstance(payload.get("items"), (str, bytes)):
                raw_items = [item for item in payload["items"] if isinstance(item, Mapping)]
            elif isinstance(payload.get("records"), Sequence) and not isinstance(payload.get("records"), (str, bytes)):
                raw_items = [item for item in payload["records"] if isinstance(item, Mapping)]
            else:
                for key, kind_name in (
                    ("decision_locks", "decision_lock"),
                    ("settled_arms", "settled_arm"),
                    ("decision_journal_entries", "decision_journal"),
                    ("servant_insights", "servant_insight"),
                ):
                    group = payload.get(key)
                    if isinstance(group, Sequence) and not isinstance(group, (str, bytes)):
                        for item in group:
                            if isinstance(item, Mapping):
                                raw_items.append({"record_kind": kind_name, **dict(item)})
        elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
            raw_items = [item for item in payload if isinstance(item, Mapping)]
        else:
            raise SourceEvidenceError("Agora owner payload must be a mapping or sequence of mappings")

        cap = max(1, int(max_records or self.max_records or 200))
        out: List[SourceRecord] = []
        base_url = self.resolve_api_url()

        for raw in raw_items[:cap]:
            record = self._build_source_record(
                raw,
                default_tenant=default_tenant,
                base_url=base_url,
                ingest_dt=resolved_ingest_dt,
                ingest_iso=resolved_ingest_iso,
                trace_id=trace_id,
            )
            out.append(record)

        return tuple(out)

    def _build_source_record(
        self,
        raw: Mapping[str, Any],
        *,
        default_tenant: str,
        base_url: str,
        ingest_dt: datetime,
        ingest_iso: str,
        trace_id: str,
    ) -> SourceRecord:
        item = dict(raw)
        record_kind = _clean(item.get("record_kind")).lower()
        if not record_kind:
            if "arms" in item or "decision_time" in item or "human_decision_id" in item:
                record_kind = "decision_lock"
            elif "settlement_source" in item or "settled_at" in item:
                record_kind = "settled_arm"
            elif "insight_id" in item or "insightId" in item or item.get("record_type") == "AgoraInsight":
                record_kind = "servant_insight"
            elif "entry_id" in item or "entryId" in item or "body" in item:
                record_kind = "decision_journal"
        if record_kind not in VALID_RECORD_KINDS:
            raise SourceEvidenceError(
                f"Agora platform source record has unsupported record_kind: {record_kind!r}"
            )

        record_id = _clean(
            item.get("record_id")
            or item.get("decision_id")
            or item.get("human_decision_id")
            or item.get("settled_arm_id")
            or item.get("entry_id")
            or item.get("insight_id")
            or item.get("id")
        )
        if not record_id:
            raise SourceEvidenceError(f"Agora {record_kind} record is missing record_id")

        trader_identity = (
            dict(item["trader_identity"])
            if isinstance(item.get("trader_identity"), Mapping)
            else {}
        )
        rec_tenant = (
            _clean(item.get("tenant_id"))
            or _clean(trader_identity.get("tenant_id"))
            or default_tenant
        )
        if not rec_tenant:
            raise SourceEvidenceError(f"Agora {record_kind} {record_id} is missing tenant_id")

        agora_user_id = (
            _clean(item.get("agora_user_id"))
            or _clean(item.get("user_id"))
            or _clean(trader_identity.get("agora_user_id"))
            or _clean(trader_identity.get("user_id"))
            or _clean(item.get("author"))
            or _clean(item.get("createdBy"))
        )
        if not agora_user_id:
            raise SourceEvidenceError(
                f"Agora {record_kind} {record_id} is missing trader identity (agora_user_id / user_id)"
            )

        trader_identity = {
            **trader_identity,
            "tenant_id": rec_tenant,
            "user_id": agora_user_id,
            "agora_user_id": agora_user_id,
        }
        account_id = _clean(item.get("account_id")) or None
        broker_ids = _string_list(item.get("broker_ids"))
        raw_text_val = item.get("raw_text")
        if raw_text_val is None:
            raw_text_val = (
                item.get("raw_rationale")
                if item.get("raw_rationale") is not None
                else (item.get("body") if item.get("body") is not None else item.get("content", ""))
            )
        raw_text = str(raw_text_val if not isinstance(raw_text_val, Mapping) else json.dumps(raw_text_val, ensure_ascii=False, sort_keys=True))

        event_raw = (
            item.get("event_time")
            or item.get("decision_time")
            or item.get("captured_at")
            or item.get("created_at")
            or item.get("createdAt")
        )
        available_raw = (
            item.get("available_time")
            or item.get("settled_at")
            or item.get("updated_at")
            or item.get("updatedAt")
            or item.get("created_at")
            or item.get("createdAt")
            or event_raw
        )
        event_dt, event_iso = _parse_rfc3339(event_raw, "event_time")
        avail_dt, avail_iso = _parse_rfc3339(available_raw, "available_time")
        if avail_dt < event_dt:
            raise SourceEvidenceError(
                f"Agora {record_kind} {record_id} violates PIT: available_time ({avail_iso}) < event_time ({event_iso})"
            )
        effective_ingest_iso = ingest_iso if ingest_dt >= avail_dt else avail_iso

        # Enforce A4 rule: paper proxy is never labelled as an actual fill.
        obs_kind = _clean(
            item.get("execution_observation_kind")
            or item.get("settlement_source")
            or ((item.get("arms") or {}).get("human_actual") or {}).get("execution_observation_kind")
        )
        if obs_kind == "pantheon_paper_proxy" and item.get("actual_fill_claimed") is True:
            raise SourceEvidenceError(
                f"Agora {record_kind} {record_id} illegally claims actual_fill_claimed=True on pantheon_paper_proxy"
            )

        title = _clean(item.get("title")) or f"Agora {record_kind} {record_id} ({agora_user_id})"
        body_text = (
            f"[Agora {record_kind}] id={record_id} trader={agora_user_id} "
            f"account={account_id or 'none'} brokers={','.join(broker_ids) or 'none'} "
            f"event_time={event_iso}: {raw_text}"
        ).strip()

        normalized_item = {
            **item,
            "record_kind": record_kind,
            "record_id": record_id,
            "tenant_id": rec_tenant,
            "agora_user_id": agora_user_id,
            "user_id": agora_user_id,
            "trader_identity": trader_identity,
            "account_id": account_id,
            "broker_ids": broker_ids,
            "raw_text": raw_text,
            "event_time": event_iso,
            "available_time": avail_iso,
            "ingest_time": effective_ingest_iso,
            "provenance": "agora",
        }
        chash = _content_hash(normalized_item)
        source_id = f"agora:{rec_tenant}:{record_kind}:{record_id}"
        parsed_base = urllib.parse.urlsplit(base_url)
        base_no_query = urllib.parse.urlunsplit((parsed_base.scheme, parsed_base.netloc, parsed_base.path, "", ""))
        content_ref = (
            f"{base_no_query}?tenant_id={urllib.parse.quote(rec_tenant)}"
            f"&kind={urllib.parse.quote(record_kind)}&record_id={urllib.parse.quote(record_id)}"
        )

        target_symbol = _clean(item.get("target_instrument") or item.get("symbol")) or "TW_EQUITY"
        metadata: Dict[str, Any] = {
            "provenance": "agora",
            "dataset": AGORA_PLATFORM_SOURCE_DATASET,
            "schema_hash": AGORA_PLATFORM_SOURCE_SCHEMA_HASH,
            "definition_id": AGORA_PLATFORM_SOURCE_CONNECTOR_ID,
            "record_kind": record_kind,
            "record_type": item.get("record_type") or record_kind,
            "record_id": record_id,
            "tenant_id": rec_tenant,
            "agora_user_id": agora_user_id,
            "user_id": agora_user_id,
            "owner_user_id": agora_user_id,
            "trader_identity": trader_identity,
            "account_id": account_id,
            "broker_ids": broker_ids,
            "raw_text": raw_text,
            "body": body_text,
            "excerpt": raw_text[:500] if raw_text else title,
            "citation_label": f"Agora {record_kind} {record_id}",
            "event_time": event_iso,
            "available_time": avail_iso,
            "ingest_time": effective_ingest_iso,
            "data_cutoff": item.get("data_cutoff") or item.get("available_data_cutoff"),
            "cursor": item.get("cursor") or f"{avail_iso}|{record_kind}|{record_id}",
            "content_hash": chash,
            "body_hash": chash,
            "license_scope": "internal_agora_governed",
            "entitlement_tags": ["agora_platform_source", "internal_research"],
            "access_scope": ["operator", "owner", "research"],
            "allowed_roles": ["operator", "admin", "governance", "trader"],
            "keywords": ["agora", record_kind, agora_user_id, *broker_ids],
            # Seed materialization compatibility fields (always draft-only, validation-gated):
            "hypothesis": f"Observed Agora {record_kind} fact ({record_id}): {raw_text or title}",
            "asset_class": ["equity"],
            "market_scope": ["TW", "US"],
            "required_data": [AGORA_PLATFORM_SOURCE_DATASET, "tw_price_daily"],
            "feature_hints": [record_kind, target_symbol],
            "pit": {
                "event_time": event_iso,
                "available_time": avail_iso,
                "ingest_time": effective_ingest_iso,
                "data_cutoff": item.get("data_cutoff") or item.get("available_data_cutoff"),
                "validated": True,
            },
            "governance": {
                "provenance": "agora",
                "facts_only": True,
                "canonical_sink": "SourceRecord/EvidenceBundle",
                "direct_execution_allowed": False,
                "lean_consumption": "research_only_not_direct_action",
                "broker_consumption": "not_direct_action",
                "validation_gates_required": True,
                "becomes_strategy_without_validation": False,
                "becomes_rule_without_validation": False,
                "becomes_lesson_without_validation": False,
                "required_validation_gates": [
                    "research_validation_and_trial_admission_panel",
                    "lesson_risk_review_and_paper_endorse",
                    "harness_precheck_shadow_and_panel",
                ],
                "environment_ceiling": "paper",
            },
            "agora_record": normalized_item,
        }
        for key in (
            "decision_id",
            "human_decision_id",
            "decision_time",
            "arms",
            "unavailable_arms",
            "execution_observation_kind",
            "settlement_source",
            "outcome",
            "settled_at",
            "entry_id",
            "insight_id",
            "persona_id",
            "category",
            "confidence",
            "impact",
            "tags",
            "linked_strategy_ids",
            "linked_persona_ids",
            "context_refs",
        ):
            if key in normalized_item and normalized_item[key] is not None:
                metadata[key] = normalized_item[key]

        return SourceRecord(
            source_id=source_id,
            connector_id=self.connector_id,
            source_type=SourceType.INTERNAL_NOTE,
            title=title,
            content_ref=content_ref,
            status=SourceRecordStatus.NORMALIZED,
            metadata=metadata,
            trace_id=trace_id or f"trace-agora-{record_id}",
            created_at=avail_iso,
        )


def check_record_access(
    record: SourceRecord | Mapping[str, Any],
    *,
    tenant_id: str,
    user_id: str,
    roles: Sequence[str] | str,
) -> bool:
    """Verify whether a caller with (tenant_id, user_id, roles) may read an ingested Agora record.

    Follows existing operator and owner roles:
    - Operators / admin / governance roles may read all records in their tenant.
    - Traders / owner roles may read only records whose agora_user_id / user_id matches user_id.
    """
    meta = record.metadata if isinstance(record, SourceRecord) else record.get("metadata", record)
    if not isinstance(meta, Mapping):
        return False
    rec_tenant = _clean(meta.get("tenant_id"))
    if not tenant_id or rec_tenant != _clean(tenant_id):
        return False
    role_set = (
        {r.strip().lower() for r in roles.split(",") if r.strip()}
        if isinstance(roles, str)
        else {str(r).strip().lower() for r in (roles or ()) if str(r).strip()}
    )
    if role_set & {"operator", "admin", "governance", "service", "system"}:
        return True
    owner_uid = _clean(meta.get("agora_user_id") or meta.get("user_id") or meta.get("owner_user_id"))
    return bool(user_id and owner_uid and owner_uid == _clean(user_id))


def enforce_validation_gate_before_promotion(
    record_or_artifact: SourceRecord | Mapping[str, Any],
    *,
    target_kind: str,
    validation_evidence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Fail closed if an Agora record is promoted to a strategy, rule, or lesson without validation gates.

    Acceptance 4: No record becomes a strategy or rule or lesson without the existing validation gates.
    """
    kind = _clean(target_kind).lower()
    if kind not in {"strategy", "rule", "lesson"}:
        raise SourceEvidenceError(f"Unsupported promotion target_kind: {target_kind!r}")

    gate_map = {
        "strategy": ("validation_passed", "trial_admission_panel_approved"),
        "rule": ("risk_review_approved", "paper_endorsed", "harness_panel_approved"),
        "lesson": ("risk_review_approved", "paper_endorsed"),
    }
    required_keys = gate_map[kind]
    evidence = dict(validation_evidence or {})
    missing = [k for k in required_keys if evidence.get(k) is not True]
    if missing:
        raise SourceEvidenceError(
            f"Agora fact record cannot become a {kind} without passing existing validation gates; "
            f"missing_gates={missing}"
        )
    return {
        "target_kind": kind,
        "status": "validation_gates_satisfied",
        "verified_gates": list(required_keys),
    }


def ingest_agora_records_to_knowledge_base(
    records: Sequence[SourceRecord],
    *,
    manager: Any,
    evidence_repository: Any,
    distillation_job_queue: Any,
    evidence_builder: Optional[EvidenceBundleBuilder] = None,
    ingest_run_id: str = "run-agora-platform-source",
    trace_id: str = "trace-agora-platform-source",
) -> Dict[str, Any]:
    """Route ingested Agora SourceRecords into the Knowledge Base and Distillation Queue via existing pipeline."""
    from ..pipeline import persist_source_evidence_refs

    if not records:
        return {
            "source_ids": [],
            "evidence_item_ids": [],
            "evidence_bundle_id": None,
            "knowledge_object_ids": [],
            "distillation_admissions": {},
        }
    connector_id = records[0].connector_id
    if manager.get_connector(connector_id) is None:
        manager.upsert_connector(AgoraOwnerAdapter(connector_id=connector_id).connector())

    run_stub = SimpleNamespace(
        run=SimpleNamespace(
            connector_id=connector_id,
            ingest_run_id=ingest_run_id,
            trace_id=trace_id,
            trigger_type="scheduled",
        ),
        records=tuple(records),
        collector_delivery=None,
    )
    return persist_source_evidence_refs(
        manager=manager,
        evidence_repository=evidence_repository,
        evidence_builder=evidence_builder or EvidenceBundleBuilder(),
        distillation_job_queue=distillation_job_queue,
        result=run_stub,
        storage_refs={},
    )


def project_agora_records_for_attribution(
    records: Iterable[SourceRecord],
) -> Dict[str, List[Dict[str, Any]]]:
    """Project ingested Agora SourceRecords into attribution owner inputs (C4 locks, settled arms, journals)."""
    decision_locks: List[Dict[str, Any]] = []
    settled_arms: List[Dict[str, Any]] = []
    decision_journal_entries: List[Dict[str, Any]] = []
    servant_insights: List[Dict[str, Any]] = []

    for record in records:
        meta = dict(record.metadata)
        agora_rec = dict(meta.get("agora_record") or meta)
        agora_rec["source_id"] = record.source_id
        agora_rec["provenance"] = "agora"
        agora_rec["pit"] = dict(meta.get("pit") or {})
        kind = _clean(meta.get("record_kind"))
        if kind == "decision_lock":
            decision_locks.append(agora_rec)
        elif kind == "settled_arm":
            settled_arms.append(agora_rec)
        elif kind == "decision_journal":
            decision_journal_entries.append(agora_rec)
        elif kind == "servant_insight":
            servant_insights.append(agora_rec)

    return {
        "decision_locks": decision_locks,
        "settled_arms": settled_arms,
        "decision_journal_entries": decision_journal_entries,
        "servant_insights": servant_insights,
    }


def project_agora_records_for_meeting_case_file(
    records: Iterable[SourceRecord],
    *,
    subject_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Build shared-fact meeting case-file items (e.g. for MT-4 attribution reviews) from Agora SourceRecords."""
    clean_subject = _clean(subject_id)
    clean_tenant = _clean(tenant_id)
    case_file_items: List[Dict[str, Any]] = []

    for record in records:
        meta = dict(record.metadata)
        if clean_tenant and _clean(meta.get("tenant_id")) != clean_tenant:
            continue
        user_id = _clean(meta.get("agora_user_id") or meta.get("user_id"))
        persona_id = _clean(meta.get("persona_id"))
        if clean_subject and clean_subject not in {user_id, persona_id, _clean(meta.get("record_id"))}:
            continue
        case_file_items.append(
            {
                "case_file_item_id": f"cf-{record.source_id}",
                "source_id": record.source_id,
                "provenance": "agora",
                "record_kind": meta.get("record_kind"),
                "record_id": meta.get("record_id"),
                "tenant_id": meta.get("tenant_id"),
                "agora_user_id": user_id,
                "trader_identity": dict(meta.get("trader_identity") or {}),
                "account_id": meta.get("account_id"),
                "broker_ids": list(meta.get("broker_ids") or []),
                "raw_text": meta.get("raw_text", ""),
                "event_time": meta.get("event_time"),
                "available_time": meta.get("available_time"),
                "data_cutoff": meta.get("data_cutoff"),
                "arms": meta.get("arms"),
                "unavailable_arms": meta.get("unavailable_arms"),
                "outcome": meta.get("outcome"),
                "settlement_source": meta.get("settlement_source"),
                "fact_only": True,
                "private_memory_included": False,
                "validation_gates_required": True,
            }
        )
    return case_file_items


def project_agora_records_for_imitation(
    records: Iterable[SourceRecord],
) -> Dict[str, List[Any]]:
    """Build schema-validated CorrectionTrace and PreferenceExample contracts from Agora decision locks."""
    from services.research.imitation.preference_models import CorrectionTrace, PreferenceExample

    traces: List[CorrectionTrace] = []
    preferences: List[PreferenceExample] = []

    for record in records:
        meta = dict(record.metadata)
        if meta.get("record_kind") != "decision_lock":
            continue
        agora_rec = dict(meta.get("agora_record") or meta)
        decision_id = _clean(agora_rec.get("decision_id") or meta.get("record_id"))
        arms = agora_rec.get("arms") if isinstance(agora_rec.get("arms"), Mapping) else {}
        human = arms.get("human_actual") if isinstance(arms.get("human_actual"), Mapping) else {}
        base = arms.get("base_strategy") if isinstance(arms.get("base_strategy"), Mapping) else {}
        if not human or not base:
            continue

        human_action = _clean(human.get("action")) or "hold"
        base_action = _clean(base.get("suggestion") or base.get("suggested_action")) or "enter"
        human_qty = human.get("quantity")
        base_qty = base.get("suggested_size") or base.get("quantity")
        if human_action == base_action and human_qty == base_qty:
            continue

        strategy_id = _clean(base.get("strategy_ref") or base.get("strategy_id") or agora_rec.get("strategy_id")) or "agora-shadow-strategy"
        actor_id = _clean(agora_rec.get("agora_user_id") or agora_rec.get("user_id")) or "trader-1"
        raw_text = _clean(agora_rec.get("raw_text")) or "Trader shadow decision diverged from base strategy."
        event_time = _clean(meta.get("event_time")) or _utc_now()
        avail_time = _clean(meta.get("available_time")) or event_time

        target = {
            "registry_id": f"reg-{strategy_id}",
            "strategy_id": strategy_id,
            "artifact_version": "1.0.0",
            "artifact_type": "strategy_spec",
            "promotion_state": "candidate",
        }
        before_artifact = {
            "artifact_id": f"reg-{strategy_id}:base",
            "action": base_action,
            "quantity": base_qty if base_qty is not None else 1,
        }
        after_artifact = {
            "artifact_id": f"reg-{strategy_id}:human:{decision_id}",
            "action": human_action,
            "quantity": human_qty if human_qty is not None else (0 if human_action in {"reject", "hold"} else 1),
        }
        trace_id_str = f"corr-agora-{decision_id}"
        feedback_event_id = f"fb-agora-{decision_id}"
        trace = CorrectionTrace.from_dict(
            {
                "schema_version": "1.0",
                "trace_id": trace_id_str,
                "strategy_id": strategy_id,
                "source_feedback_event_id": feedback_event_id,
                "actor_id": actor_id,
                "actor_role": "operator",
                "target": target,
                "before_artifact": before_artifact,
                "after_artifact": after_artifact,
                "operations": [
                    {
                        "path": "/action",
                        "operation": "replace",
                        "old_value": before_artifact["action"],
                        "new_value": after_artifact["action"],
                        "rationale": raw_text,
                    }
                ],
                "rationale": raw_text,
                "task_ref": "AGORA-PLATFORM-SOURCE-20261116",
                "decision_ref": decision_id,
                "created_at": avail_time,
                "metadata": {
                    "research_only": True,
                    "provenance": "agora",
                    "agora_user_id": actor_id,
                    "trader_identity": dict(agora_rec.get("trader_identity") or {}),
                    "account_id": agora_rec.get("account_id"),
                    "broker_ids": list(agora_rec.get("broker_ids") or []),
                },
            }
        )
        pref = PreferenceExample.from_dict(
            {
                "schema_version": "1.0",
                "example_id": f"pref-agora-{decision_id}",
                "strategy_id": strategy_id,
                "source_feedback_event_id": feedback_event_id,
                "correction_trace_id": trace_id_str,
                "actor_id": actor_id,
                "actor_role": "operator",
                "action": "edit",
                "target": target,
                "chosen_artifact": after_artifact,
                "rejected_artifact": before_artifact,
                "confidence": 0.75,
                "rationale": raw_text,
                "observed_at": event_time,
                "created_at": avail_time,
                "context": {
                    "source_channel": "agora",
                    "decision_id": decision_id,
                },
                "metadata": {
                    "research_only": True,
                    "provenance": "agora",
                },
            }
        )
        traces.append(trace)
        preferences.append(pref)

    return {"correction_traces": traces, "preference_examples": preferences}


def _agora_owner_handler(
    adapter: AgoraOwnerAdapter,
    request: Mapping[str, Any],
    trace_id: str,
) -> Tuple[SourceRecord, ...]:
    payload = request.get("payload")
    max_records = int(request.get("_effective_max_records") or request.get("max_records") or adapter.max_records)
    tenant_id = _clean(request.get("tenant_id")) or adapter.tenant_id
    if payload is not None:
        return adapter.records_from_payload(
            payload,
            tenant_id=tenant_id,
            max_records=max_records,
            trace_id=trace_id,
            ingest_time=request.get("ingest_time"),
        )
    cursor = _clean(request.get("cursor") or request.get("watermark")) or None
    result = adapter.pull(
        cursor=cursor,
        limit=max_records,
        kind=_clean(request.get("kind")) or None,
        tenant_id=tenant_id,
        trace_id=trace_id,
        ingest_time=request.get("ingest_time"),
        persist_cursor=cursor is None,
    )
    return result.records


def load_agora_platform_source_desired_state(
    desired_state_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Load the governed Agora platform source desired-state declaration from default_desired_state.json."""
    register_agora_provider_adapter()
    target_path = (
        Path(desired_state_path)
        if desired_state_path is not None
        else Path(__file__).resolve().parent.parent / "default_desired_state.json"
    )
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    platform_sources = payload.get("platform_sources") or []
    return [dict(item) for item in platform_sources if isinstance(item, Mapping)]


def register_agora_provider_adapter() -> None:
    """Idempotently register AgoraOwnerAdapter in provider_adapters and SourceProvisioningReconciler."""
    from .. import provider_adapters as pa

    if AGORA_OWNER_ADAPTER_TOKEN not in pa.ALLOWED_PROVIDER_ADAPTERS:
        pa.ALLOWED_PROVIDER_ADAPTERS[AGORA_OWNER_ADAPTER_TOKEN] = pa.ProviderAdapterSpec(
            token=AGORA_OWNER_ADAPTER_TOKEN,
            adapter_cls=AgoraOwnerAdapter,
            handler=_agora_owner_handler,
            config_keys=(
                "api_url",
                "secret_ref_id",
                "service_actor",
                "tenant_id",
                "max_records",
                "timeout_seconds",
                "cursor_state_path",
                "transport",
            ),
        )
    pa.PROVIDER_ADAPTER_ALIASES.setdefault("AgoraOwnerAdapter", AGORA_OWNER_ADAPTER_TOKEN)

    try:
        from .. import persona_source_reconciler as psr

        orig_init = psr.SourceProvisioningReconciler.__init__
        if not getattr(orig_init, "_agora_owner_registered", False):

            def _wrapped_init(self: Any, *args: Any, **kwargs: Any) -> None:
                orig_init(self, *args, **kwargs)
                self.provider_factories.setdefault(
                    AGORA_PLATFORM_SOURCE_CONNECTOR_ID,
                    lambda cid: AgoraOwnerAdapter(connector_id=cid),
                )

            setattr(_wrapped_init, "_agora_owner_registered", True)
            psr.SourceProvisioningReconciler.__init__ = _wrapped_init  # type: ignore[method-assign]
    except Exception:
        pass


register_agora_provider_adapter()

__all__ = [
    "AGORA_OWNER_ADAPTER_TOKEN",
    "AGORA_PLATFORM_SOURCE_CONNECTOR_ID",
    "AGORA_PLATFORM_SOURCE_DATASET",
    "AGORA_PLATFORM_SOURCE_SCHEMA_HASH",
    "AGORA_PLATFORM_SOURCE_TOKEN_ENV",
    "AGORA_PLATFORM_SOURCE_URL_ENV",
    "AgoraOwnerAdapter",
    "AgoraOwnerPullResult",
    "LOCAL_AGORA_PLATFORM_SOURCE_TOKEN",
    "check_record_access",
    "enforce_validation_gate_before_promotion",
    "ingest_agora_records_to_knowledge_base",
    "load_agora_platform_source_desired_state",
    "project_agora_records_for_attribution",
    "project_agora_records_for_imitation",
    "project_agora_records_for_meeting_case_file",
    "register_agora_provider_adapter",
]
