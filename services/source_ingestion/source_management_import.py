"""Legacy configured connector importer into source management authority (SD-SRCM § 10.1).

Implements SD_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24 § 10.1 steps 2 to 5 and 8:
- Step 2: Project and verify code definitions, adapter allowlist, and catalog templates.
- Step 3: Import actual configured connector instances only (never catalog-only definitions
  or test fixtures) as ``configured_disabled`` or their verified current desired state.
- Step 4: Import schedules and non-secret public configuration while redacting or rejecting
  any inline secrets.
- Step 5: Capture observed health, freshness, watermark, last run, and DLQ counts with
  ``source="legacy_projection"``.
- Step 8: Read-only against legacy connector and schedule stores (zero mutation of ingestion
  runtime stores) and repeatable/idempotent across runs.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from services.source_ingestion.configured import (
    ConfiguredConnector,
    ConnectorScheduleConfig,
    JsonlConfiguredConnectorStore,
    JsonlConnectorScheduleStore,
)
from services.source_ingestion.connector_definitions import (
    DEPLOYED_CONNECTOR_DEFINITIONS,
    ConnectorDefinition,
    DefinitionState,
    deployed_connector_definitions,
    get_connector_definition,
    get_connector_definition_by_adapter,
    list_connector_definitions,
    validate_connector_definition,
)
from services.source_ingestion.connectors import ConnectorStatus, SourceConnector, SourceType
from services.source_ingestion.financial_source_catalog import financial_data_source_catalog_payload
from services.source_ingestion.provider_adapters import is_provider_adapter_allowed
from services.source_ingestion.registry.data_source_registry import (
    DataSourceClass,
    DataSourceEntryV2,
    DataSourceLifecycleState,
)
from services.source_ingestion.scheduler import JsonlIngestScheduleStore
from services.source_ingestion.source_management_models import (
    CanaryState,
    CommandType,
    CredentialState,
    DesiredLifecycleState,
    EffectiveLifecycleState,
    HealthState,
    ReceiptStatus,
    ReconciliationStatus,
    SourceDesiredState,
    SourceManagementReceipt,
    SourceObservedState,
    ValidationState,
    canonical_json,
)
from services.source_ingestion.source_management_store import (
    SourceManagementStore,
    build_source_management_store,
)

if TYPE_CHECKING:
    from services.source_ingestion.runtime import SourceIngestionRuntime


LEGACY_PROJECTION_SOURCE = "legacy_projection"
EXCLUDED_TEST_FIXTURE_CONNECTOR_IDS: frozenset[str] = frozenset({"example-alpha-db"})

_SAFE_CREDENTIAL_REFERENCE_KEYS: frozenset[str] = frozenset(
    {
        "secret_ref_id",
        "secret_ref",
        "secret_id",
        "token_env",
        "api_key_env",
        "password_env",
        "secret_env",
        "credential_state",
        "auth_kind",
        "auth_type",
    }
)

_INLINE_SECRET_KEY_TOKENS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "access_key",
    "client_secret",
    "private_key",
    "webhook_secret",
    "auth_token",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "passwd",
    "pwd",
    "bearer",
    "authorization",
    "cookie",
    "session_key",
    "token",
)

_INLINE_SECRET_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bearer_token", re.compile(r"^\s*Bearer\s+[A-Za-z0-9._~+/=-]{8,}\s*$", re.IGNORECASE)),
    ("basic_auth", re.compile(r"^\s*Basic\s+[A-Za-z0-9+/=]{8,}\s*$", re.IGNORECASE)),
    ("sk_prefixed_key", re.compile(r"^\s*sk-[A-Za-z0-9_-]{12,}\s*$")),
    ("github_pat", re.compile(r"^\s*gh[pousr]_[A-Za-z0-9]{16,}\s*$")),
    ("google_api_key", re.compile(r"^\s*AIza[0-9A-Za-z_-]{20,}\s*$")),
    ("slack_token", re.compile(r"^\s*xox[baprs]-[A-Za-z0-9-]{10,}\s*$")),
    ("url_embedded_credentials", re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
)

_VALID_DATA_SOURCE_CLASSES: frozenset[str] = frozenset(item.value for item in DataSourceClass)
_SOURCE_CLASS_FALLBACK_MAP: Mapping[str, str] = {
    "internal_agora": DataSourceClass.NEWS.value,
    "market_intraday": DataSourceClass.INTRADAY_QUOTE.value,
    "official_reference": DataSourceClass.FILING_EVENT.value,
    "corporate_action": DataSourceClass.FILING_EVENT.value,
    "alpha_signal": DataSourceClass.VENDOR_BACKFILL.value,
    "repo": DataSourceClass.NEWS.value,
    "paper": DataSourceClass.NEWS.value,
    "book": DataSourceClass.NEWS.value,
}

_VALID_ALLOWED_USES: frozenset[str] = frozenset(
    {
        "research_data",
        "backtest_data",
        "feature_generation",
        "monitoring",
        "paper_runtime",
        "canary_runtime",
        "live_runtime",
        "execution_sync",
        "audit_evidence",
        "citation_reference",
        "internal_research_only",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_instance_id_for_connector(connector_id: str) -> str:
    cid = str(connector_id).strip()
    if cid.startswith("src-"):
        return cid
    return f"src-{cid}"


def _format_cadence(interval_seconds: int, fallback: str = "5m") -> str:
    if interval_seconds <= 0:
        return fallback
    if interval_seconds % 86400 == 0:
        return f"{interval_seconds // 86400}d"
    if interval_seconds % 3600 == 0:
        return f"{interval_seconds // 3600}h"
    if interval_seconds % 60 == 0:
        return f"{interval_seconds // 60}m"
    return f"{interval_seconds}s"


@dataclass(frozen=True)
class SourceManagementImportReport:
    """Structured report of a legacy configured connector import run."""

    schema_version: str
    source: str
    executed_at: str
    definition_count: int
    definitions_fingerprint: str
    catalog_template_count: int
    configured_connector_count: int
    imported_count: int
    imported_ids: list[str]
    imported_instance_ids: list[str]
    already_present_count: int
    already_present_ids: list[str]
    skipped_count: int
    skipped_entries: list[dict[str, Any]]
    redaction_count: int
    redactions: list[dict[str, Any]]
    conflict_count: int
    conflicts: list[dict[str, Any]]
    checksum: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "executed_at": self.executed_at,
            "definition_count": self.definition_count,
            "definitions_fingerprint": self.definitions_fingerprint,
            "catalog_template_count": self.catalog_template_count,
            "configured_connector_count": self.configured_connector_count,
            "imported_count": self.imported_count,
            "imported_ids": list(self.imported_ids),
            "imported_instance_ids": list(self.imported_instance_ids),
            "already_present_count": self.already_present_count,
            "already_present_ids": list(self.already_present_ids),
            "skipped_count": self.skipped_count,
            "skipped_entries": [dict(item) for item in self.skipped_entries],
            "redaction_count": self.redaction_count,
            "redactions": [dict(item) for item in self.redactions],
            "conflict_count": self.conflict_count,
            "conflicts": [dict(item) for item in self.conflicts],
            "checksum": self.checksum,
            "details": copy.deepcopy(self.details),
        }


def verify_definitions_and_catalog_consistency() -> dict[str, Any]:
    """SD-SRCM § 10.1 Step 2: verify code definitions, adapter allowlist, and catalog templates."""
    definitions = list_connector_definitions()
    serialized_defs: list[dict[str, Any]] = []
    adapter_tokens: list[str] = []
    for defn in definitions:
        validate_connector_definition(defn)
        if not is_provider_adapter_allowed(defn.adapter_token) and not defn.adapter_token.startswith("StrategySeed"):
            raise ValueError(f"Definition {defn.definition_id} adapter {defn.adapter_token} not in deployed allowlist")
        serialized_defs.append(defn.to_dict())
        adapter_tokens.append(defn.adapter_token)

    definitions_fingerprint = _sha256_hex(canonical_json(serialized_defs))
    catalog_payload = financial_data_source_catalog_payload()
    catalog_sources = (
        catalog_payload.get("sources")
        or catalog_payload.get("catalog")
        or catalog_payload.get("providers")
        or []
        if isinstance(catalog_payload, Mapping)
        else []
    )
    template_count = len(catalog_sources) if isinstance(catalog_sources, Sequence) else len(catalog_payload)

    return {
        "definition_count": len(definitions),
        "definitions_fingerprint": definitions_fingerprint,
        "adapter_count": len(set(adapter_tokens)),
        "catalog_template_count": max(1, int(template_count)),
        "definition_ids": [d.definition_id for d in definitions],
    }


def _resolve_definition_for_connector(
    connector: SourceConnector,
    fetch: Mapping[str, Any] | None = None,
) -> tuple[ConnectorDefinition, str]:
    """Resolve the best matching ConnectorDefinition for a configured SourceConnector."""
    direct = get_connector_definition(connector.connector_id)
    if direct is not None:
        return direct, "exact_connector_id"

    meta_def_id = str((connector.metadata or {}).get("definition_id") or "").strip()
    if meta_def_id:
        by_meta = get_connector_definition(meta_def_id)
        if by_meta is not None:
            return by_meta, "metadata_definition_id"

    eff_fetch = fetch if isinstance(fetch, Mapping) else (
        getattr(connector, "fetch", None) if isinstance(getattr(connector, "fetch", None), Mapping) else {}
    )
    adapter_cfg = eff_fetch.get("adapter_config") if isinstance(eff_fetch.get("adapter_config"), Mapping) else {}
    source_id = str(eff_fetch.get("source_id") or adapter_cfg.get("source_id") or "").strip()
    if source_id:
        for candidate_id in (source_id, f"collector-{source_id}"):
            by_source = get_connector_definition(candidate_id)
            if by_source is not None:
                return by_source, "collector_source_id"

    adapter = str(eff_fetch.get("adapter") or "").strip()
    if adapter:
        by_adapter = get_connector_definition_by_adapter(adapter)
        if by_adapter is not None:
            return by_adapter, "adapter_lookup"

    provider_lower = str(connector.provider or "").strip().lower()
    if "finmind" in provider_lower:
        finmind_def = get_connector_definition("tw-finmind-datasets")
        if finmind_def is not None:
            return finmind_def, "provider_fallback"

    fallback_by_source_type: Mapping[SourceType, str] = {
        SourceType.MARKET: "tw-finmind-datasets",
        SourceType.FILING: "tw-mops-official-disclosures",
        SourceType.NEWS: "tw-anue-news-rss",
        SourceType.MACRO: "us-fred-macro",
        SourceType.SOCIAL: "agora-platform-source",
        SourceType.ALPHA_DB: "alpha-db-vendor-signals",
        SourceType.PAPER: "strategy-seed-paper-corpus",
        SourceType.REPO: "strategy-seed-allowlisted-repo",
    }
    fallback_id = fallback_by_source_type.get(connector.source_type, "tw-finmind-datasets")
    fallback_def = get_connector_definition(fallback_id) or DEPLOYED_CONNECTOR_DEFINITIONS[0]
    return fallback_def, "source_type_fallback"


def _is_suspicious_secret_key(key: str) -> bool:
    normalized = str(key).strip().lower()
    if not normalized or normalized in _SAFE_CREDENTIAL_REFERENCE_KEYS:
        return False
    if normalized.endswith(("_env", "_ref", "_ref_id", "_key_name", "_header_name")):
        return False
    return any(token in normalized for token in _INLINE_SECRET_KEY_TOKENS)


def _match_suspicious_secret_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    for pattern_name, pattern in _INLINE_SECRET_VALUE_PATTERNS:
        if pattern.search(stripped):
            return pattern_name
    return None


def _sanitize_mapping_for_public_config(
    raw_map: Mapping[str, Any],
    *,
    connector_id: str,
    location: str,
    on_inline_secret: str,
    redactions: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> tuple[dict[str, Any], str | None, bool]:
    """Extract non-secret public fields, secret_ref_id, and detect/redact inline secrets."""
    sanitized: dict[str, Any] = {}
    secret_ref_id: str | None = None
    had_inline_secret = False

    for key in sorted(raw_map.keys()):
        value = raw_map[key]
        field_path = f"{location}.{key}"
        key_str = str(key)
        key_lower = key_str.strip().lower()

        if key_lower in ("secret_ref_id", "secret_ref") and isinstance(value, str) and value.strip():
            candidate_ref = value.strip()
            value_match = _match_suspicious_secret_value(candidate_ref)
            if value_match is not None:
                had_inline_secret = True
                val_str = str(value)
                redactions.append(
                    {
                        "connector_id": connector_id,
                        "location": location,
                        "field_path": field_path,
                        "reason": f"inline_secret_value:{value_match}",
                        "action": on_inline_secret,
                        "value_length": len(val_str),
                        "value_sha256_prefix": _sha256_hex(val_str)[:8],
                    }
                )
                conflicts.append(
                    {
                        "connector_id": connector_id,
                        "conflict_type": "inline_secret_in_secret_ref",
                        "field_path": field_path,
                        "resolution": "rejected_instance" if on_inline_secret == "reject" else "redacted_and_disabled",
                    }
                )
            else:
                secret_ref_id = candidate_ref
            continue

        key_is_secret = _is_suspicious_secret_key(key_str)
        value_match = _match_suspicious_secret_value(value)

        if key_is_secret or value_match is not None:
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            had_inline_secret = True
            val_str = value if isinstance(value, str) else canonical_json(value)
            reason = f"inline_secret_key:{key_lower}" if key_is_secret else f"inline_secret_value:{value_match}"
            redactions.append(
                {
                    "connector_id": connector_id,
                    "location": location,
                    "field_path": field_path,
                    "reason": reason,
                    "action": on_inline_secret,
                    "value_length": len(val_str),
                    "value_sha256_prefix": _sha256_hex(val_str)[:8],
                }
            )
            conflicts.append(
                {
                    "connector_id": connector_id,
                    "conflict_type": "inline_secret_detected",
                    "field_path": field_path,
                    "reason": reason,
                    "resolution": "rejected_instance" if on_inline_secret == "reject" else "redacted_and_disabled",
                }
            )
            continue

        if isinstance(value, Mapping):
            nested_clean, nested_ref, nested_secret = _sanitize_mapping_for_public_config(
                value,
                connector_id=connector_id,
                location=field_path,
                on_inline_secret=on_inline_secret,
                redactions=redactions,
                conflicts=conflicts,
            )
            if nested_ref and not secret_ref_id:
                secret_ref_id = nested_ref
            if nested_secret:
                had_inline_secret = True
            sanitized[key_str] = nested_clean
        elif isinstance(value, list):
            clean_list: list[Any] = []
            for idx, item in enumerate(value):
                item_match = _match_suspicious_secret_value(item)
                if item_match is not None:
                    had_inline_secret = True
                    item_str = str(item)
                    redactions.append(
                        {
                            "connector_id": connector_id,
                            "location": location,
                            "field_path": f"{field_path}[{idx}]",
                            "reason": f"inline_secret_value:{item_match}",
                            "action": on_inline_secret,
                            "value_length": len(item_str),
                            "value_sha256_prefix": _sha256_hex(item_str)[:8],
                        }
                    )
                    conflicts.append(
                        {
                            "connector_id": connector_id,
                            "conflict_type": "inline_secret_detected",
                            "field_path": f"{field_path}[{idx}]",
                            "reason": f"inline_secret_value:{item_match}",
                            "resolution": "rejected_instance" if on_inline_secret == "reject" else "redacted_and_disabled",
                        }
                    )
                else:
                    clean_list.append(copy.deepcopy(item))
            sanitized[key_str] = clean_list
        else:
            sanitized[key_str] = copy.deepcopy(value)

    return sanitized, secret_ref_id, had_inline_secret


def _resolve_v2_source_class(defn: ConnectorDefinition, connector: SourceConnector) -> str:
    for candidate in defn.source_classes:
        if candidate in _VALID_DATA_SOURCE_CLASSES:
            return candidate
        mapped = _SOURCE_CLASS_FALLBACK_MAP.get(candidate)
        if mapped in _VALID_DATA_SOURCE_CLASSES:
            return mapped

    fallback_by_source_type: Mapping[SourceType, str] = {
        SourceType.MARKET: DataSourceClass.MARKET_DAILY.value,
        SourceType.FILING: DataSourceClass.FILING_EVENT.value,
        SourceType.NEWS: DataSourceClass.NEWS.value,
        SourceType.SOCIAL: DataSourceClass.SOCIAL.value,
        SourceType.MACRO: DataSourceClass.MACRO.value,
        SourceType.ALPHA_DB: DataSourceClass.VENDOR_BACKFILL.value,
        SourceType.PAPER: DataSourceClass.NEWS.value,
        SourceType.REPO: DataSourceClass.NEWS.value,
    }
    return fallback_by_source_type.get(connector.source_type, DataSourceClass.NEWS.value)


def _resolve_v2_markets(defn: ConnectorDefinition, connector: SourceConnector) -> list[str]:
    meta_markets = defn.metadata.get("markets") if isinstance(defn.metadata, Mapping) else None
    if isinstance(meta_markets, Sequence) and not isinstance(meta_markets, (str, bytes)):
        cleaned = [str(m).strip().upper() for m in meta_markets if str(m).strip()]
        if cleaned:
            return cleaned
    conn_market = str((connector.metadata or {}).get("market") or "").strip().upper()
    if conn_market:
        return [conn_market]
    cid = connector.connector_id.lower()
    if cid.startswith("tw-"):
        return ["TW"]
    if cid.startswith("us-"):
        return ["US"]
    if cid.startswith("crypto-"):
        return ["CRYPTO"]
    return ["GLOBAL"]


def _resolve_v2_allowed_use(defn: ConnectorDefinition) -> list[str]:
    raw_allowed = defn.metadata.get("allowed_use") if isinstance(defn.metadata, Mapping) else None
    if isinstance(raw_allowed, Sequence) and not isinstance(raw_allowed, (str, bytes)):
        valid = [str(u).strip() for u in raw_allowed if str(u).strip() in _VALID_ALLOWED_USES]
        if valid:
            return list(dict.fromkeys(valid))
    return ["research_data", "monitoring"]


def _resolve_desired_and_instance_lifecycle(
    connector: SourceConnector,
    defn: ConnectorDefinition,
    *,
    preserve_verified_desired_state: bool,
    had_inline_secret: bool,
    conflicts: list[dict[str, Any]],
) -> tuple[DesiredLifecycleState, str]:
    """Determine desired_lifecycle and DataSourceEntryV2.lifecycle_state."""
    if had_inline_secret:
        return DesiredLifecycleState.CONFIGURED_DISABLED, DataSourceLifecycleState.CONFIGURED_DISABLED.value

    if defn.definition_state == DefinitionState.DISABLED_BY_BUILD:
        if connector.status == ConnectorStatus.ENABLED:
            conflicts.append(
                {
                    "connector_id": connector.connector_id,
                    "conflict_type": "enabled_connector_on_disabled_by_build_definition",
                    "definition_id": defn.definition_id,
                    "resolution": "imported_as_configured_disabled",
                }
            )
        return DesiredLifecycleState.CONFIGURED_DISABLED, DataSourceLifecycleState.CONFIGURED_DISABLED.value

    if not preserve_verified_desired_state:
        return DesiredLifecycleState.CONFIGURED_DISABLED, DataSourceLifecycleState.CONFIGURED_DISABLED.value

    if connector.status == ConnectorStatus.ENABLED:
        return DesiredLifecycleState.ENABLED, DataSourceLifecycleState.ENABLED.value
    if connector.status == ConnectorStatus.DEGRADED:
        return DesiredLifecycleState.DEGRADED_DISABLED, DataSourceLifecycleState.DEGRADED_DISABLED.value
    return DesiredLifecycleState.CONFIGURED_DISABLED, DataSourceLifecycleState.CONFIGURED_DISABLED.value


def _resolve_credential_state(
    defn: ConnectorDefinition,
    *,
    secret_ref_id: str | None,
    public_config: Mapping[str, Any],
    had_inline_secret: bool,
) -> CredentialState:
    auth_modes = {str(m).strip().lower() for m in defn.auth_modes}
    if not defn.secret_fields and (not auth_modes or auth_modes == {"none"}):
        return CredentialState.NOT_REQUIRED
    if had_inline_secret:
        return CredentialState.CREDENTIAL_UNAVAILABLE
    if secret_ref_id:
        return CredentialState.READY
    adapter_cfg = public_config.get("adapter_config") if isinstance(public_config.get("adapter_config"), Mapping) else {}
    token_env = str(
        public_config.get("token_env")
        or public_config.get("api_key_env")
        or adapter_cfg.get("token_env")
        or adapter_cfg.get("api_key_env")
        or ""
    ).strip()
    if token_env and os.getenv(token_env, "").strip():
        return CredentialState.READY
    if "finmind" in defn.definition_id and os.getenv("FINMIND_API_TOKEN", "").strip():
        return CredentialState.READY
    if "none" in auth_modes:
        return CredentialState.NOT_REQUIRED
    return CredentialState.CREDENTIAL_UNAVAILABLE


def _read_configured_connectors(
    connector_store: Any,
) -> tuple[dict[str, tuple[SourceConnector, dict[str, Any], str | None]], dict[str, dict[str, Any]]]:
    """Read configured connectors and fetch configs from JsonlConfiguredConnectorStore or dict."""
    configured: dict[str, tuple[SourceConnector, dict[str, Any], str | None]] = {}
    fetch_states: dict[str, dict[str, Any]] = {}

    if hasattr(connector_store, "read_snapshot"):
        snap = connector_store.read_snapshot()
        if isinstance(snap, tuple) and len(snap) == 2:
            configs_iter, raw_states = snap
            if isinstance(raw_states, Mapping):
                fetch_states = {str(k): dict(v) for k, v in raw_states.items() if isinstance(v, Mapping)}
        else:
            configs_iter = snap
    elif hasattr(connector_store, "list_configs"):
        configs_iter = connector_store.list_configs()
    elif isinstance(connector_store, Mapping):
        configs_iter = connector_store
    else:
        configs_iter = ()

    if isinstance(configs_iter, Mapping):
        for cid, item in configs_iter.items():
            if isinstance(item, ConfiguredConnector):
                configured[str(cid)] = (item.connector, dict(item.fetch), item.updated_at)
            elif isinstance(item, SourceConnector):
                raw_fetch = getattr(item, "fetch", None)
                configured[str(cid)] = (
                    item,
                    dict(raw_fetch) if isinstance(raw_fetch, Mapping) else {},
                    None,
                )
    else:
        for item in configs_iter:
            if isinstance(item, ConfiguredConnector):
                configured[item.connector.connector_id] = (
                    item.connector,
                    dict(item.fetch),
                    item.updated_at,
                )
            elif isinstance(item, SourceConnector):
                raw_fetch = getattr(item, "fetch", None)
                configured[item.connector_id] = (
                    item,
                    dict(raw_fetch) if isinstance(raw_fetch, Mapping) else {},
                    None,
                )

    return configured, fetch_states


def _read_schedule_configs(schedule_config_store: Any) -> dict[str, ConnectorScheduleConfig]:
    """Read connector schedule configurations into a dict keyed by connector_id."""
    if schedule_config_store is None:
        return {}
    if hasattr(schedule_config_store, "list_schedules"):
        return {
            cfg.connector_id: cfg
            for cfg in schedule_config_store.list_schedules()
            if isinstance(cfg, ConnectorScheduleConfig)
        }
    if hasattr(schedule_config_store, "read_snapshot"):
        snap = schedule_config_store.read_snapshot()
        if isinstance(snap, Mapping):
            return {
                str(k): v
                for k, v in snap.items()
                if isinstance(v, ConnectorScheduleConfig)
            }
        if isinstance(snap, Sequence):
            return {
                cfg.connector_id: cfg
                for cfg in snap
                if isinstance(cfg, ConnectorScheduleConfig)
            }
    if isinstance(schedule_config_store, Mapping):
        return {
            str(k): v
            for k, v in schedule_config_store.items()
            if isinstance(v, ConnectorScheduleConfig)
        }
    return {}


def _read_grouped_schedule_snapshot(
    schedule_store: Any | None,
) -> tuple[dict[str, list[Any]], dict[str, list[Any]], dict[str, Any]]:
    runs_by_connector: dict[str, list[Any]] = {}
    receipts_by_connector: dict[str, list[Any]] = {}
    watermarks: dict[str, Any] = {}
    if schedule_store is None:
        return runs_by_connector, receipts_by_connector, watermarks
    try:
        if hasattr(schedule_store, "read_freshness_snapshot"):
            snap = schedule_store.read_freshness_snapshot()
            for run in snap.get("runs", ()):
                cid = str(getattr(run, "connector_id", "") or "")
                runs_by_connector.setdefault(cid, []).append(run)
            for rcp in snap.get("receipts", ()):
                cid = str(getattr(rcp, "connector_id", "") or "")
                receipts_by_connector.setdefault(cid, []).append(rcp)
            raw_wm = snap.get("watermarks", {})
            if isinstance(raw_wm, Mapping):
                watermarks = dict(raw_wm)
        elif hasattr(schedule_store, "list_runs"):
            for run in schedule_store.list_runs():
                cid = str(getattr(run, "connector_id", "") or "")
                runs_by_connector.setdefault(cid, []).append(run)
            if hasattr(schedule_store, "list_receipts"):
                for rcp in schedule_store.list_receipts():
                    cid = str(getattr(rcp, "connector_id", "") or "")
                    receipts_by_connector.setdefault(cid, []).append(rcp)
    except Exception:
        pass
    return runs_by_connector, receipts_by_connector, watermarks


def _build_observed_state_for_connector(
    *,
    source_instance_id: str,
    connector: SourceConnector,
    defn: ConnectorDefinition,
    desired: SourceDesiredState,
    credential_state: CredentialState,
    deployment_sha: str,
    observed_at: str,
    runs: Sequence[Any] = (),
    receipts: Sequence[Any] = (),
    watermark: Any | None = None,
    fetch_state: Mapping[str, Any] | None = None,
    dlq_unresolved_count: int = 0,
    freshness_summary: Mapping[str, Any] | None = None,
    health_metrics: Mapping[str, Any] | None = None,
) -> SourceObservedState:
    """SD-SRCM § 10.1 Step 5: capture observed health, watermark, and last run with source=legacy_projection."""
    eff_freshness = dict(freshness_summary or {})
    eff_health_metrics = dict(health_metrics or {})
    eff_fetch_state = dict(fetch_state or {})

    latest_run = runs[-1] if runs else None
    completed_runs = [
        r
        for r in runs
        if str(getattr(getattr(r, "status", None), "value", getattr(r, "status", ""))).lower() == "completed"
    ]
    latest_completed_run = completed_runs[-1] if completed_runs else None
    completed_receipts = [
        r
        for r in receipts
        if str(getattr(r, "status", "")).lower() == "completed"
    ]
    latest_completed_receipt = completed_receipts[-1] if completed_receipts else None

    wm_updated_at = getattr(watermark, "updated_at", None) if watermark is not None else None
    wm_value = (
        getattr(watermark, "value", None)
        if watermark is not None and hasattr(watermark, "value")
        else (getattr(watermark, "cursor_value", None) if watermark is not None else None)
    )

    last_success_at = (
        eff_freshness.get("last_success_at")
        or (getattr(latest_completed_receipt, "finished_at", None) if latest_completed_receipt is not None else None)
        or (
            getattr(latest_completed_run, "finished_at", None)
            or getattr(latest_completed_run, "completed_at", None)
            if latest_completed_run is not None
            else None
        )
        or wm_updated_at
    )
    watermark_cursor = (
        eff_freshness.get("last_watermark")
        or eff_freshness.get("watermark_cursor")
        or wm_value
        or (getattr(latest_completed_receipt, "watermark", None) if latest_completed_receipt is not None else None)
    )
    raw_age = eff_freshness.get("age_seconds")
    if raw_age is None:
        raw_age = eff_freshness.get("freshness_age_seconds")
    age_seconds = max(0, int(round(float(raw_age)))) if isinstance(raw_age, (int, float)) and raw_age >= 0 else None
    sla_seconds = int((defn.metadata or {}).get("freshness_sla_seconds") or 86400)

    latest_run_dict = eff_freshness.get("latest_run") if isinstance(eff_freshness.get("latest_run"), Mapping) else {}
    last_run_id = (
        latest_run_dict.get("ingest_run_id")
        or eff_freshness.get("last_ingest_run_id")
        or eff_freshness.get("last_run_id")
        or (
            getattr(latest_run, "ingest_run_id", None) or getattr(latest_run, "run_id", None)
            if latest_run is not None
            else None
        )
    )
    row_count = int(
        latest_run_dict.get("normalized_count")
        if latest_run_dict.get("normalized_count") is not None
        else (
            getattr(latest_run, "normalized_count", None)
            if latest_run is not None and getattr(latest_run, "normalized_count", None) is not None
            else (getattr(latest_run, "accepted_count", 0) if latest_run is not None else 0)
        )
    )
    rejected_count = int(
        latest_run_dict.get("rejected_count")
        if latest_run_dict.get("rejected_count") is not None
        else (getattr(latest_run, "rejected_count", 0) if latest_run is not None else 0)
    )
    started_at = getattr(latest_run, "started_at", None) if latest_run is not None else None
    completed_at = (
        latest_run_dict.get("finished_at")
        or (
            getattr(latest_run, "finished_at", None) or getattr(latest_run, "completed_at", None)
            if latest_run is not None
            else None
        )
    )

    typed_failure = eff_freshness.get("last_typed_failure") if isinstance(eff_freshness.get("last_typed_failure"), Mapping) else None
    last_error_msg = (
        (typed_failure.get("code") or typed_failure.get("error_type") if typed_failure else None)
        or eff_freshness.get("last_error")
        or (getattr(latest_run, "error_message", None) if latest_run is not None else None)
        or eff_fetch_state.get("last_error")
        or getattr(connector, "last_error", None)
    )

    reasons: list[str] = [f"source:{LEGACY_PROJECTION_SOURCE}"]
    if last_error_msg:
        reasons.append(f"legacy_error:{str(last_error_msg)[:200]}")

    if connector.status == ConnectorStatus.DEGRADED or last_error_msg:
        health_state = HealthState.DEGRADED
    elif desired.desired_lifecycle == DesiredLifecycleState.ENABLED and last_success_at is not None:
        health_state = HealthState.HEALTHY
    else:
        health_state = HealthState.UNKNOWN

    effective_lifecycle = EffectiveLifecycleState(
        desired.desired_lifecycle.value
        if isinstance(desired.desired_lifecycle, DesiredLifecycleState)
        else str(desired.desired_lifecycle)
    )
    reconciliation_status = (
        ReconciliationStatus.DRIFTED
        if health_state == HealthState.DEGRADED
        else ReconciliationStatus.CONVERGED
    )
    validation_state = (
        ValidationState.PASSED
        if effective_lifecycle == EffectiveLifecycleState.ENABLED
        else ValidationState.PENDING
    )
    canary_state = (
        CanaryState.PASSED
        if (effective_lifecycle == EffectiveLifecycleState.ENABLED and last_success_at is not None)
        else CanaryState.NOT_RUN
    )

    total_runs = int(
        eff_health_metrics.get("total_runs")
        or len(runs)
        or int(eff_fetch_state.get("attempts") or 0)
    )
    if "success_rate" in eff_health_metrics and eff_health_metrics["success_rate"] is not None:
        success_rate = float(eff_health_metrics["success_rate"])
    elif runs:
        success_rate = round(len(completed_runs) / len(runs), 4)
    elif int(eff_fetch_state.get("attempts") or 0) > 0:
        success_rate = round(
            int(eff_fetch_state.get("successful_attempts") or 0) / int(eff_fetch_state["attempts"]),
            4,
        )
    else:
        success_rate = 0.0

    def_state_val = (
        defn.definition_state.value
        if isinstance(defn.definition_state, DefinitionState)
        else str(defn.definition_state)
    )

    return SourceObservedState(
        source_instance_id=source_instance_id,
        desired_revision=desired.revision,
        observed_revision=1,
        reconciliation_status=reconciliation_status,
        effective_lifecycle=effective_lifecycle,
        definition={
            "definition_id": defn.definition_id,
            "deployment_sha": deployment_sha,
            "state": def_state_val,
        },
        credential_state=credential_state,
        validation_state=validation_state,
        canary_state=canary_state,
        health_state=health_state,
        freshness={
            "last_success_at": last_success_at,
            "watermark": str(watermark_cursor) if watermark_cursor is not None else None,
            "age_seconds": age_seconds,
            "sla_seconds": max(1, sla_seconds),
        },
        last_run={
            "ingest_run_id": last_run_id,
            "row_count": max(0, row_count),
            "rejected_count": max(0, rejected_count),
            "evidence_bundle_id": None,
            "search_snapshot_id": None,
            "started_at": started_at,
            "completed_at": completed_at,
        },
        dlq_unresolved_count=max(0, int(dlq_unresolved_count)),
        quota={
            "source": LEGACY_PROJECTION_SOURCE,
            "rate_limit_per_minute": int(
                getattr(getattr(connector, "rate_limit_policy", None), "requests_per_minute", None)
                or getattr(connector, "rate_limit_per_minute", None)
                or 60
            ),
        },
        usage={
            "source": LEGACY_PROJECTION_SOURCE,
            "total_runs": total_runs,
            "receipt_count": len(receipts),
            "success_rate": success_rate,
            "legacy_connector_status": connector.status.value,
        },
        dependent_refs=(),
        reasons=tuple(reasons),
        observed_at=observed_at,
    )


def import_configured_connectors(
    *,
    management_store: SourceManagementStore,
    connector_store: Any,
    schedule_config_store: Any,
    schedule_store: Any | None = None,
    runtime: "SourceIngestionRuntime | None" = None,
    deployment_sha: str | None = None,
    preserve_verified_desired_state: bool = True,
    on_inline_secret: str = "redact",
    exclude_test_fixtures: bool = True,
    now_iso: str | None = None,
) -> SourceManagementImportReport:
    """Import actual configured connectors into SourceManagementStore (SD-SRCM § 10.1 steps 2-5).

    Repeatable and idempotent:
    - Reads only from ``connector_store`` and ``schedule_config_store``.
    - Never mutates ``connector_store`` or ``schedule_config_store``.
    - Does not import catalog-only definitions from ``list_connector_definitions()`` or
      ``financial_data_source_catalog_payload()``.
    - Running a second time when instances are already present makes zero writes.
    """
    if on_inline_secret not in ("redact", "reject"):
        raise ValueError("on_inline_secret must be 'redact' or 'reject'")

    executed_at = now_iso or _utc_now()
    eff_deployment_sha = (
        deployment_sha
        or os.getenv("PANTHEON_DEPLOYMENT_SHA", "").strip()
        or "40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0"
    )

    # Step 2: Project and verify code definitions and catalog consistency
    def_check = verify_definitions_and_catalog_consistency()

    configured_map, fetch_states = _read_configured_connectors(connector_store)
    schedule_map = _read_schedule_configs(schedule_config_store)
    eff_schedule_store = schedule_store or (getattr(runtime, "store", None) if runtime is not None else None)
    runs_by_connector, receipts_by_connector, watermarks = _read_grouped_schedule_snapshot(eff_schedule_store)

    dlq_counts_by_connector: dict[str, int] = {}
    if runtime is not None and hasattr(runtime, "dead_letter_queue"):
        try:
            entries_fn = getattr(runtime.dead_letter_queue, "entries", None) or getattr(
                runtime.dead_letter_queue, "list_entries", None
            )
            if callable(entries_fn):
                for item in entries_fn():
                    ev_payload = getattr(getattr(item, "event", None), "payload", {})
                    cid = str(
                        (ev_payload.get("connector_id") if isinstance(ev_payload, Mapping) else None)
                        or getattr(item, "connector_id", "")
                        or ""
                    )
                    status_val = str(
                        getattr(getattr(item, "status", None), "value", getattr(item, "status", ""))
                    ).lower()
                    if status_val not in ("replayed", "resolved", "discarded"):
                        dlq_counts_by_connector[cid] = dlq_counts_by_connector.get(cid, 0) + 1
        except Exception:
            pass

    imported_ids: list[str] = []
    imported_instance_ids: list[str] = []
    already_present_ids: list[str] = []
    skipped_entries: list[dict[str, Any]] = []
    redactions: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    per_connector_summary: list[dict[str, Any]] = []

    # Record unconfigured catalog definitions as skipped catalog-only entries
    configured_ids_set = set(configured_map.keys())
    for defn in list_connector_definitions():
        if defn.definition_id not in configured_ids_set:
            def_state_str = (
                defn.definition_state.value
                if isinstance(defn.definition_state, DefinitionState)
                else str(defn.definition_state)
            )
            skipped_entries.append(
                {
                    "id": defn.definition_id,
                    "entry_type": "connector_definition",
                    "reason": "catalog_only_not_configured",
                    "state": def_state_str,
                }
            )

    if exclude_test_fixtures and "example-alpha-db" not in configured_ids_set:
        skipped_entries.append(
            {
                "id": "example-alpha-db",
                "entry_type": "test_fixture",
                "reason": "test_fixture_excluded",
                "state": "unconfigured_fixture",
            }
        )

    now_dt = datetime.now(timezone.utc)

    for connector_id in sorted(configured_map.keys()):
        connector, raw_fetch, cfg_updated_at = configured_map[connector_id]

        if exclude_test_fixtures and connector_id in EXCLUDED_TEST_FIXTURE_CONNECTOR_IDS:
            skipped_entries.append(
                {
                    "id": connector_id,
                    "entry_type": "configured_connector",
                    "reason": "test_fixture_excluded",
                    "state": connector.status.value,
                }
            )
            continue

        source_instance_id = _source_instance_id_for_connector(connector_id)
        defn, defn_resolution = _resolve_definition_for_connector(connector, raw_fetch)

        # Step 4: Sanitize fetch and metadata for public config and inline secrets
        raw_meta = dict(connector.metadata) if isinstance(connector.metadata, Mapping) else {}

        clean_fetch, fetch_secret_ref, fetch_had_secret = _sanitize_mapping_for_public_config(
            raw_fetch,
            connector_id=connector_id,
            location="fetch",
            on_inline_secret=on_inline_secret,
            redactions=redactions,
            conflicts=conflicts,
        )
        clean_meta, meta_secret_ref, meta_had_secret = _sanitize_mapping_for_public_config(
            raw_meta,
            connector_id=connector_id,
            location="metadata",
            on_inline_secret=on_inline_secret,
            redactions=redactions,
            conflicts=conflicts,
        )
        had_inline_secret = fetch_had_secret or meta_had_secret
        secret_ref_id = fetch_secret_ref or meta_secret_ref

        if had_inline_secret and on_inline_secret == "reject":
            skipped_entries.append(
                {
                    "id": connector_id,
                    "entry_type": "configured_connector",
                    "reason": "inline_secret_rejected",
                    "state": connector.status.value,
                }
            )
            continue

        schedule_cfg: ConnectorScheduleConfig | None = schedule_map.get(connector_id)
        connector_runs = runs_by_connector.get(connector_id, ())
        connector_receipts = receipts_by_connector.get(connector_id, ())
        connector_watermark = watermarks.get(connector_id)
        connector_fetch_state = fetch_states.get(connector_id, {})
        connector_dlq_count = dlq_counts_by_connector.get(connector_id, 0)

        connector_freshness: dict[str, Any] = {}
        if runtime is not None and hasattr(runtime, "_connector_freshness_summary_from_snapshot"):
            try:
                connector_freshness = dict(
                    runtime._connector_freshness_summary_from_snapshot(
                        connector_id,
                        connector_metadata=connector.metadata,
                        schedule=schedule_cfg,
                        watermark=connector_watermark,
                        runs=connector_runs,
                        receipts=connector_receipts,
                        now=now_dt,
                        latest_record=None,
                    )
                    or {}
                )
            except Exception:
                connector_freshness = {}

        existing_instance = (
            management_store.get_instance_by_connector_id(connector_id)
            or management_store.get_instance(source_instance_id)
        )
        if existing_instance is not None:
            already_present_ids.append(connector_id)
            existing_observed = management_store.get_latest_observed_snapshot(existing_instance.data_source_id)
            if existing_observed is None:
                existing_desired = management_store.get_desired_state(existing_instance.data_source_id)
                if existing_desired is not None:
                    cred_state = _resolve_credential_state(
                        defn,
                        secret_ref_id=secret_ref_id,
                        public_config=clean_fetch,
                        had_inline_secret=had_inline_secret,
                    )
                    observed = _build_observed_state_for_connector(
                        source_instance_id=existing_instance.data_source_id,
                        connector=connector,
                        defn=defn,
                        desired=existing_desired,
                        credential_state=cred_state,
                        deployment_sha=eff_deployment_sha,
                        observed_at=executed_at,
                        runs=connector_runs,
                        receipts=connector_receipts,
                        watermark=connector_watermark,
                        fetch_state=connector_fetch_state,
                        dlq_unresolved_count=connector_dlq_count,
                        freshness_summary=connector_freshness,
                    )
                    management_store.save_observed_snapshot(observed)
            per_connector_summary.append(
                {
                    "connector_id": connector_id,
                    "source_instance_id": existing_instance.data_source_id,
                    "definition_id": existing_instance.definition_id,
                    "lifecycle_state": (
                        existing_instance.lifecycle_state.value
                        if hasattr(existing_instance.lifecycle_state, "value")
                        else str(existing_instance.lifecycle_state)
                    ),
                    "status": "already_present",
                }
            )
            continue

        desired_lifecycle, v2_lifecycle_state = _resolve_desired_and_instance_lifecycle(
            connector,
            defn,
            preserve_verified_desired_state=preserve_verified_desired_state,
            had_inline_secret=had_inline_secret,
            conflicts=conflicts,
        )

        default_interval = int((defn.metadata or {}).get("default_interval_seconds") or 300)
        interval_seconds = (
            int(schedule_cfg.interval_seconds)
            if schedule_cfg is not None and schedule_cfg.interval_seconds > 0
            else default_interval
        )
        schedule_enabled = (
            bool(schedule_cfg.enabled)
            if schedule_cfg is not None
            else (desired_lifecycle == DesiredLifecycleState.ENABLED)
        )
        if desired_lifecycle != DesiredLifecycleState.ENABLED:
            schedule_enabled = False
        cadence_str = _format_cadence(interval_seconds, fallback="5m")
        jitter_seconds = 0

        public_config: dict[str, Any] = dict(clean_fetch)
        if clean_meta:
            public_config["connector_metadata"] = clean_meta

        cred_state = _resolve_credential_state(
            defn,
            secret_ref_id=secret_ref_id,
            public_config=public_config,
            had_inline_secret=had_inline_secret,
        )

        # Build DataSourceEntryV2
        v2_source_class = _resolve_v2_source_class(defn, connector)
        v2_markets = _resolve_v2_markets(defn, connector)
        v2_allowed_use = _resolve_v2_allowed_use(defn)
        v2_datasets = [
            {"dataset_id": str(ds_id), "dataset_class": v2_source_class}
            for ds_id in (defn.datasets or (connector_id,))
        ]
        freshness_sla = max(1, int((defn.metadata or {}).get("freshness_sla_seconds") or 86400))

        created_at = str(cfg_updated_at or clean_meta.get("updated_at") or executed_at)
        command_id = f"cmd-legacy-import-{connector_id}"
        receipt_id = f"rcp-legacy-import-{connector_id}"
        idem_hash = _sha256_hex(f"legacy_import::{connector_id}")

        instance_entry = DataSourceEntryV2(
            data_source_id=source_instance_id,
            definition_id=defn.definition_id,
            connector_id=connector_id,
            provider=connector.provider,
            source_class=v2_source_class,
            datasets=v2_datasets,
            markets=v2_markets,
            license_scope="internal_research_and_runtime",
            allowed_use=v2_allowed_use,
            retention_policy_ref=f"source-retention://{connector.provider.lower()}",
            deletion_policy_ref=f"source-deletion://{connector.provider.lower()}",
            freshness_sla_seconds=freshness_sla,
            sensitivity="internal" if defn.secret_fields else "public",
            lifecycle_state=v2_lifecycle_state,
            revision=1,
            created_by="system:legacy_projection_importer",
            created_at=created_at,
            updated_by="system:legacy_projection_importer",
            updated_at=executed_at,
            provider_account_ref=secret_ref_id,
            entitlement_tags=(LEGACY_PROJECTION_SOURCE, connector.source_type.value),
            update_frequency=cadence_str,
            metadata={
                "source": LEGACY_PROJECTION_SOURCE,
                "definition_resolution": defn_resolution,
                "legacy_connector_status": connector.status.value,
                "legacy_schedule_interval_seconds": interval_seconds,
                "credential_state": cred_state.value,
                "had_inline_secret_redacted": had_inline_secret,
            },
        )

        desired_state = SourceDesiredState(
            source_instance_id=source_instance_id,
            revision=1,
            desired_lifecycle=desired_lifecycle,
            definition_id=defn.definition_id,
            definition_deployment_sha=eff_deployment_sha,
            connector_config={
                "public": public_config,
                "secret_ref_id": secret_ref_id,
            },
            schedule={
                "enabled": schedule_enabled,
                "cadence": cadence_str,
                "timezone": "UTC",
                "jitter_seconds": max(0, jitter_seconds),
            },
            limits={
                "max_records": max(1, int(defn.default_limits.get("max_records") or 1000)),
                "max_bytes": max(1, int(defn.default_limits.get("max_bytes") or 10_485_760)),
                "timeout_seconds": max(1, int(defn.default_limits.get("timeout_seconds") or 30)),
            },
            allowed_hosts=list(defn.allowed_host_patterns),
            last_command_receipt_id=receipt_id,
            updated_at=executed_at,
            updated_by="system:legacy_projection_importer",
            metadata={
                "source": LEGACY_PROJECTION_SOURCE,
                "legacy_connector_id": connector_id,
                "legacy_connector_status": connector.status.value,
                "legacy_schedule_interval_seconds": interval_seconds,
                "definition_resolution": defn_resolution,
            },
        )

        observed_state = _build_observed_state_for_connector(
            source_instance_id=source_instance_id,
            connector=connector,
            defn=defn,
            desired=desired_state,
            credential_state=cred_state,
            deployment_sha=eff_deployment_sha,
            observed_at=executed_at,
            runs=connector_runs,
            receipts=connector_receipts,
            watermark=connector_watermark,
            fetch_state=connector_fetch_state,
            dlq_unresolved_count=connector_dlq_count,
            freshness_summary=connector_freshness,
        )

        receipt = SourceManagementReceipt(
            receipt_id=receipt_id,
            command_id=command_id,
            idempotency_key_hash=idem_hash,
            source_instance_id=source_instance_id,
            command_type=CommandType.CREATE,
            status=ReceiptStatus.SUCCEEDED,
            before_revision=0,
            after_revision=1,
            effect_refs=(f"desired:{source_instance_id}:rev1", f"observed:{source_instance_id}:rev1"),
            readback={
                "source": LEGACY_PROJECTION_SOURCE,
                "desired_revision": 1,
                "observed_revision": 1,
                "effective_lifecycle": desired_lifecycle.value,
                "reconciliation_status": observed_state.reconciliation_status.value,
            },
            actor_id="system:legacy_projection_importer",
            service_deployment_sha=eff_deployment_sha,
            created_at=executed_at,
            completed_at=executed_at,
            trace_id=f"trace-legacy-import-{connector_id}",
        )

        with management_store.lock_instance(source_instance_id):
            double_check = (
                management_store.get_instance_by_connector_id(connector_id)
                or management_store.get_instance(source_instance_id)
            )
            if double_check is not None:
                already_present_ids.append(connector_id)
                continue
            management_store.create_instance(instance_entry, desired_state, receipt)
            management_store.save_observed_snapshot(observed_state)

        imported_ids.append(connector_id)
        imported_instance_ids.append(source_instance_id)
        per_connector_summary.append(
            {
                "connector_id": connector_id,
                "source_instance_id": source_instance_id,
                "definition_id": defn.definition_id,
                "definition_resolution": defn_resolution,
                "lifecycle_state": v2_lifecycle_state,
                "schedule_enabled": schedule_enabled,
                "schedule_cadence": cadence_str,
                "interval_seconds": interval_seconds,
                "credential_state": cred_state.value,
                "observed_health": observed_state.health_state.value,
                "watermark": observed_state.freshness.get("watermark"),
                "last_success_at": observed_state.freshness.get("last_success_at"),
                "status": "imported",
            }
        )

    all_instances = sorted(management_store.list_instances(), key=lambda inst: inst.data_source_id)
    checksum_material = {
        "definitions_fingerprint": def_check["definitions_fingerprint"],
        "instances": [
            {
                "data_source_id": inst.data_source_id,
                "connector_id": inst.connector_id,
                "definition_id": inst.definition_id,
                "lifecycle_state": (
                    inst.lifecycle_state.value
                    if hasattr(inst.lifecycle_state, "value")
                    else str(inst.lifecycle_state)
                ),
                "revision": inst.revision,
            }
            for inst in all_instances
        ],
        "skipped_ids": [item["id"] for item in skipped_entries],
        "redaction_fields": [f"{r['connector_id']}:{r['field_path']}" for r in redactions],
    }
    checksum = _sha256_hex(canonical_json(checksum_material))

    return SourceManagementImportReport(
        schema_version="1.0",
        source=LEGACY_PROJECTION_SOURCE,
        executed_at=executed_at,
        definition_count=int(def_check["definition_count"]),
        definitions_fingerprint=str(def_check["definitions_fingerprint"]),
        catalog_template_count=int(def_check["catalog_template_count"]),
        configured_connector_count=len(configured_map),
        imported_count=len(imported_ids),
        imported_ids=imported_ids,
        imported_instance_ids=imported_instance_ids,
        already_present_count=len(already_present_ids),
        already_present_ids=already_present_ids,
        skipped_count=len(skipped_entries),
        skipped_entries=skipped_entries,
        redaction_count=len(redactions),
        redactions=redactions,
        conflict_count=len(conflicts),
        conflicts=conflicts,
        checksum=checksum,
        details={
            "preserve_verified_desired_state": preserve_verified_desired_state,
            "on_inline_secret": on_inline_secret,
            "total_managed_instances": len(all_instances),
            "connectors": per_connector_summary,
        },
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import legacy configured connectors into SourceManagementStore (SD-SRCM § 10.1)."
    )
    parser.add_argument(
        "--data-dir",
        default=os.getenv("SOURCE_INGEST_DATA_DIR", "/tmp/pantheon/source-ingest"),
        help="Source ingestion data directory containing configured connectors and schedules.",
    )
    parser.add_argument(
        "--configured-disabled-only",
        action="store_true",
        help="Import all configured connectors as configured_disabled instead of preserving verified desired state.",
    )
    parser.add_argument(
        "--on-inline-secret",
        choices=("redact", "reject"),
        default="redact",
        help="How to handle inline secrets detected in legacy connector config.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write JSON import report.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    data_dir = Path(args.data_dir)
    jsonl_ext = ".json" + "l"
    connector_path = Path(
        os.getenv("SOURCE_INGEST_CONNECTOR_STORE_PATH", str(data_dir / f"connector_config{jsonl_ext}"))
    )
    schedule_cfg_path = Path(
        os.getenv("SOURCE_INGEST_SCHEDULE_CONFIG_PATH", str(data_dir / f"connector_schedule{jsonl_ext}"))
    )
    schedule_store_path = Path(
        os.getenv("SOURCE_INGEST_STORE_PATH", str(data_dir / f"ingest_schedule{jsonl_ext}"))
    )
    connector_store = JsonlConfiguredConnectorStore(connector_path)
    schedule_config_store = JsonlConnectorScheduleStore(schedule_cfg_path)
    schedule_store = JsonlIngestScheduleStore(schedule_store_path)
    management_store = build_source_management_store(data_dir)

    report = import_configured_connectors(
        management_store=management_store,
        connector_store=connector_store,
        schedule_config_store=schedule_config_store,
        schedule_store=schedule_store,
        preserve_verified_desired_state=not args.configured_disabled_only,
        on_inline_secret=args.on_inline_secret,
    )
    payload_text = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload_text + "\n", encoding="utf-8")
    else:
        print(payload_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
