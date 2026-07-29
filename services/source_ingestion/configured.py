"""Persistent configured-connector fetch support for source ingestion."""

from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.external_egress import is_internal_host, open_external_url

from .connectors import SourceConnector, SourceEvidenceError, SourceRecord
from .external_sources import validate_external_source_connector, validate_external_source_record
from .provider_adapters import execute_provider_owned_adapter, validate_provider_adapter_token
from .process_lock import exclusive_file_lock
from .scheduler import IngestBatch


SENSITIVE_CONFIG_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "bearer_token",
    "client_secret",
    "password",
    "private_key",
    "secret",
    "secret_key",
    "token",
}


@dataclass(frozen=True)
class ConnectorScheduleConfig:
    """Autonomous schedule configuration for a configured connector."""

    connector_id: str
    interval_seconds: int
    enabled: bool
    updated_at: str
    schema_version: str = "connector_schedule_config.v1"

    def __post_init__(self) -> None:
        if not str(self.connector_id).strip():
            raise SourceEvidenceError("schedule connector_id is required")
        if self.interval_seconds < 0:
            raise SourceEvidenceError("schedule interval_seconds must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "connector_id": self.connector_id,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConnectorScheduleConfig":
        return cls(
            connector_id=str(data["connector_id"]),
            interval_seconds=int(data.get("interval_seconds", 0)),
            enabled=bool(data.get("enabled", False)),
            updated_at=str(data["updated_at"]),
            schema_version=str(data.get("schema_version", "connector_schedule_config.v1")),
        )


class JsonlConnectorScheduleStore:
    """Append/replay store for connector autonomous schedule configuration."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock = threading.RLock()
        self._schedules: dict[str, ConnectorScheduleConfig] = {}
        self.reload()

    def reload(self) -> None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked()

    def _reload_unlocked(self) -> None:
        self._schedules = {}
        if not self.path.exists():
            return
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SourceEvidenceError(f"Invalid connector schedule JSONL at {self.path}:{line_no}: {exc.msg}") from exc
            record_type = str(entry.get("record_type") or "")
            payload = entry.get("payload")
            if not isinstance(payload, Mapping):
                raise SourceEvidenceError(f"Invalid connector schedule payload at {self.path}:{line_no}")
            if record_type == "connector_schedule":
                config = ConnectorScheduleConfig.from_dict(payload)
                self._schedules[config.connector_id] = config
            else:
                raise SourceEvidenceError(f"Unsupported connector schedule record: {record_type or '<missing>'}")

    def upsert_schedule(
        self,
        connector_id: str,
        *,
        interval_seconds: int,
        enabled: bool,
    ) -> ConnectorScheduleConfig:
        if not str(connector_id).strip():
            raise SourceEvidenceError("schedule connector_id is required")
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked()
            existing = self._schedules.get(connector_id)
            if (
                existing is not None
                and existing.interval_seconds == interval_seconds
                and existing.enabled == enabled
            ):
                return existing
            config = ConnectorScheduleConfig(
                connector_id=connector_id,
                interval_seconds=interval_seconds,
                enabled=enabled,
                updated_at=_utc_now(),
            )
            self._append("connector_schedule", connector_id, config.to_dict())
            self._schedules[connector_id] = config
            return config

    def get_schedule(self, connector_id: str) -> ConnectorScheduleConfig | None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked()
            return self._schedules.get(connector_id)

    def list_schedules(self) -> list[ConnectorScheduleConfig]:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked()
            return list(self._schedules.values())

    def _append(self, record_type: str, record_id: str, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_preexisted = self.path.exists()
        entry = {
            "schema_version": "connector_schedule_store.v1",
            "record_type": record_type,
            "record_id": record_id,
            "payload": dict(payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if not file_preexisted:
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ConfiguredConnector:
    connector: SourceConnector
    fetch: Mapping[str, Any]
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector": self.connector.to_dict(),
            "fetch": dict(self.fetch),
            "updated_at": self.updated_at,
        }


class JsonlConfiguredConnectorStore:
    """Append/replay store for connector fetch configuration and fetch state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock = threading.RLock()
        self._configs: dict[str, ConfiguredConnector] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked()

    def _reload_unlocked(self) -> None:
        self._configs = {}
        self._states = {}
        if not self.path.exists():
            return
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SourceEvidenceError(f"Invalid connector config JSONL at {self.path}:{line_no}: {exc.msg}") from exc
            record_type = str(entry.get("record_type") or "")
            payload = entry.get("payload")
            if not isinstance(payload, Mapping):
                raise SourceEvidenceError(f"Invalid connector config payload at {self.path}:{line_no}")
            if record_type == "connector_config":
                connector_payload = payload.get("connector")
                fetch_payload = payload.get("fetch")
                if not isinstance(connector_payload, Mapping) or not isinstance(fetch_payload, Mapping):
                    raise SourceEvidenceError(f"Invalid connector config record at {self.path}:{line_no}")
                connector = validate_external_source_connector(SourceConnector.from_dict(connector_payload))
                self._configs[connector.connector_id] = ConfiguredConnector(
                    connector=connector,
                    fetch=dict(fetch_payload),
                    updated_at=str(payload.get("updated_at") or _utc_now()),
                )
            elif record_type == "connector_fetch_state":
                connector_id = str(payload.get("connector_id") or "")
                if not connector_id:
                    raise SourceEvidenceError(f"Invalid connector fetch state at {self.path}:{line_no}")
                self._states[connector_id] = dict(payload)
            else:
                raise SourceEvidenceError(f"Unsupported connector config record: {record_type or '<missing>'}")

    def upsert_config(self, connector: SourceConnector, fetch: Mapping[str, Any]) -> ConfiguredConnector:
        connector = validate_external_source_connector(connector)
        normalized_fetch = self.normalize_fetch_config(fetch)
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked()
            existing = self._configs.get(connector.connector_id)
            if (
                existing is not None
                and existing.connector.to_dict() == connector.to_dict()
                and dict(existing.fetch) == normalized_fetch
            ):
                return existing
            config = ConfiguredConnector(connector=connector, fetch=normalized_fetch, updated_at=_utc_now())
            self._append("connector_config", connector.connector_id, config.to_dict())
            self._configs[connector.connector_id] = config
            return config

    def normalize_fetch_config(self, fetch: Mapping[str, Any]) -> dict[str, Any]:
        """Return the persisted fetch contract without mutating the store."""
        return self._validate_fetch_config(fetch)

    def get_config(self, connector_id: str) -> ConfiguredConnector | None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked()
            return self._configs.get(connector_id)

    def list_configs(self) -> list[ConfiguredConnector]:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked()
            return list(self._configs.values())

    def get_fetch_state(self, connector_id: str) -> dict[str, Any]:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked()
            return self._fetch_state_unlocked(connector_id)

    def _fetch_state_unlocked(self, connector_id: str) -> dict[str, Any]:
        return dict(
            self._states.get(
                connector_id,
                {
                    "connector_id": connector_id,
                    "attempts": 0,
                    "successful_attempts": 0,
                    "failed_attempts": 0,
                    "last_error": None,
                    "updated_at": None,
                },
            )
        )

    def record_fetch_attempt(self, connector_id: str, *, success: bool, error: str | None = None) -> dict[str, Any]:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked()
            state = self._fetch_state_unlocked(connector_id)
            state["connector_id"] = connector_id
            state["attempts"] = int(state.get("attempts") or 0) + 1
            if success:
                state["successful_attempts"] = int(state.get("successful_attempts") or 0) + 1
                state["last_error"] = None
            else:
                state["failed_attempts"] = int(state.get("failed_attempts") or 0) + 1
                state["last_error"] = str(error or "configured connector fetch failed")
            state["updated_at"] = _utc_now()
            self._append("connector_fetch_state", connector_id, state)
            self._states[connector_id] = state
            return dict(state)

    def _validate_fetch_config(self, fetch: Mapping[str, Any]) -> dict[str, Any]:
        _reject_inline_fetch_secrets(fetch)
        mode = str(fetch.get("mode") or "").strip()
        fail_until_attempt = int(fetch.get("fail_until_attempt") or 0)
        if fail_until_attempt < 0:
            raise SourceEvidenceError("fetch.fail_until_attempt must be >= 0")
        failure_reason = str(fetch.get("failure_reason") or "configured connector fetch failed")
        allow_empty = bool(fetch.get("allow_empty", False))
        empty_reason = str(fetch.get("empty_reason") or "")
        if allow_empty and not empty_reason.strip():
            raise SourceEvidenceError("fetch.empty_reason is required when fetch.allow_empty is true")

        if mode == "static_records":
            records = fetch.get("records")
            if not isinstance(records, list):
                raise SourceEvidenceError("fetch.records must be a list")
            return {
                "mode": mode,
                "records": [dict(record) for record in records],
                "next_watermark": fetch.get("next_watermark"),
                "allow_empty": allow_empty,
                "empty_reason": empty_reason,
                "fail_until_attempt": fail_until_attempt,
                "failure_reason": failure_reason,
            }

        if mode == "external_feed":
            url = str(fetch.get("url") or "").strip()
            if not url:
                raise SourceEvidenceError("fetch.url is required for external_feed")
            _validate_feed_url(url, "fetch.url")
            allowed_prefixes = _normalized_string_list(fetch.get("allowed_url_prefixes"))
            if not allowed_prefixes:
                raise SourceEvidenceError("fetch.allowed_url_prefixes is required for external_feed")
            for prefix in allowed_prefixes:
                _validate_feed_url(prefix, "fetch.allowed_url_prefixes")
            if not _url_is_allowed(url, allowed_prefixes):
                raise SourceEvidenceError("fetch.url is outside allowed_url_prefixes")
            network_scope = str(fetch.get("network_scope") or "external").strip()
            if network_scope not in {"external", "internal_service"}:
                raise SourceEvidenceError("fetch.network_scope must be external or internal_service")
            parsed_url = urllib.parse.urlparse(url)
            if network_scope == "external" and parsed_url.scheme not in {"https", "file"}:
                raise SourceEvidenceError("external network fetch.url must use https")
            if network_scope == "internal_service" and not is_internal_host(parsed_url.hostname or ""):
                raise SourceEvidenceError("internal_service fetch.url must target an internal host")
            timeout_seconds = float(fetch.get("timeout_seconds") or 5.0)
            if timeout_seconds <= 0 or timeout_seconds > 30:
                raise SourceEvidenceError("fetch.timeout_seconds must be > 0 and <= 30")
            max_bytes = int(fetch.get("max_bytes") or 1_000_000)
            if max_bytes <= 0 or max_bytes > 10_000_000:
                raise SourceEvidenceError("fetch.max_bytes must be > 0 and <= 10000000")
            max_records = int(fetch.get("max_records") or 100)
            if max_records <= 0 or max_records > 1000:
                raise SourceEvidenceError("fetch.max_records must be > 0 and <= 1000")
            return {
                "mode": mode,
                "url": url,
                "allowed_url_prefixes": allowed_prefixes,
                "timeout_seconds": timeout_seconds,
                "max_bytes": max_bytes,
                "max_records": max_records,
                "next_watermark": fetch.get("next_watermark"),
                "default_access_scope": _normalized_string_list(fetch.get("default_access_scope")) or ["public"],
                "respect_robots_txt": bool(fetch.get("respect_robots_txt", True)),
                "network_scope": network_scope,
                "allow_empty": allow_empty,
                "empty_reason": empty_reason,
                "fail_until_attempt": fail_until_attempt,
                "failure_reason": failure_reason,
            }

        if mode == "provider_owned_adapter":
            adapter = validate_provider_adapter_token(
                str(fetch.get("adapter") or fetch.get("provider_owned_fetcher") or "")
            )
            max_records = int(fetch.get("max_records") or 100)
            if max_records <= 0 or max_records > 1000:
                raise SourceEvidenceError("fetch.max_records must be > 0 and <= 1000")
            adapter_config = fetch.get("adapter_config") or {}
            request = fetch.get("request") or {}
            if not isinstance(adapter_config, Mapping):
                raise SourceEvidenceError("fetch.adapter_config must be an object")
            if not isinstance(request, Mapping):
                raise SourceEvidenceError("fetch.request must be an object")
            return {
                "mode": mode,
                "adapter": adapter,
                "adapter_config": dict(adapter_config),
                "request": dict(request),
                "max_records": max_records,
                "next_watermark": fetch.get("next_watermark"),
                "allow_empty": allow_empty,
                "empty_reason": empty_reason,
                "fail_until_attempt": fail_until_attempt,
                "failure_reason": failure_reason,
            }

        raise SourceEvidenceError("fetch.mode must be static_records, external_feed, or provider_owned_adapter")

    def _append(self, record_type: str, record_id: str, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_preexisted = self.path.exists()
        entry = {
            "schema_version": "source_connector_config_store.v1",
            "record_type": record_type,
            "record_id": record_id,
            "payload": dict(payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if not file_preexisted:
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)


class ConfiguredConnectorFetcher:
    """Fetches source batches from connector-owned service configuration."""

    def __init__(self, store: JsonlConfiguredConnectorStore) -> None:
        self.store = store

    def fetch_batch(
        self,
        connector_id: str,
        watermark: str | None,
        *,
        trace_id: str = "",
        job_parameters: Mapping[str, Any] | None = None,
    ) -> IngestBatch:
        config = self.store.get_config(connector_id)
        if config is None:
            raise SourceEvidenceError(f"Connector fetch is not configured: {connector_id}")

        state = self.store.get_fetch_state(connector_id)
        attempt_number = int(state.get("attempts") or 0) + 1
        fail_until_attempt = int(config.fetch.get("fail_until_attempt") or 0)
        if attempt_number <= fail_until_attempt:
            reason = str(config.fetch.get("failure_reason") or "configured connector fetch failed")
            self.store.record_fetch_attempt(connector_id, success=False, error=reason)
            raise SourceEvidenceError(reason)

        try:
            if config.fetch.get("mode") == "external_feed":
                payload = self._fetch_external_payload(config.fetch)
                record_payloads = payload.get("records")
                if not isinstance(record_payloads, list):
                    raise SourceEvidenceError("external feed records must be a list")
                max_records = int(config.fetch.get("max_records") or 0)
                if len(record_payloads) > max_records:
                    raise SourceEvidenceError(f"external feed records exceeds fetch.max_records={max_records}")
                next_watermark = payload.get("next_watermark", config.fetch.get("next_watermark"))
                records = tuple(
                    self._source_record_from_config(
                        payload,
                        connector=config.connector,
                        watermark=watermark,
                        fetch=config.fetch,
                    )
                    for payload in record_payloads
                )
            elif config.fetch.get("mode") == "provider_owned_adapter":
                records = execute_provider_owned_adapter(
                    connector=config.connector,
                    fetch=config.fetch,
                    trace_id=trace_id,
                    job_parameters=job_parameters,
                )
                next_watermark = config.fetch.get("next_watermark")
            else:
                record_payloads = config.fetch.get("records", [])
                next_watermark = config.fetch.get("next_watermark")
                records = tuple(
                    self._source_record_from_config(
                        payload,
                        connector=config.connector,
                        watermark=watermark,
                        fetch=config.fetch,
                    )
                    for payload in record_payloads
                )
        except Exception as exc:
            self.store.record_fetch_attempt(connector_id, success=False, error=str(exc))
            raise
        self.store.record_fetch_attempt(connector_id, success=True)
        return IngestBatch(
            records=records,
            next_watermark=next_watermark,
            empty_ok=bool(config.fetch.get("allow_empty", False)),
            empty_reason=str(config.fetch.get("empty_reason") or "") or None,
            metadata={
                "fetch_mode": config.fetch.get("mode"),
                "job_parameters": dict(job_parameters or {}),
            },
        )

    def _fetch_external_payload(self, fetch: Mapping[str, Any]) -> Mapping[str, Any]:
        url = str(fetch["url"])
        allowed_prefixes = _normalized_string_list(fetch.get("allowed_url_prefixes"))
        _validate_feed_url(url, "fetch.url")
        for prefix in allowed_prefixes:
            _validate_feed_url(prefix, "fetch.allowed_url_prefixes")
        if not _url_is_allowed(url, allowed_prefixes):
            raise SourceEvidenceError("external feed URL is outside allowed_url_prefixes")
        if bool(fetch.get("respect_robots_txt", True)):
            _assert_robots_allowed(
                url,
                allowed_prefixes,
                float(fetch["timeout_seconds"]),
                network_scope=str(fetch.get("network_scope") or "external"),
            )
        max_bytes = int(fetch["max_bytes"])
        raw: bytes
        if url.startswith("file://"):
            path = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path))
            with path.open("rb") as handle:
                raw = handle.read(max_bytes + 1)
        else:
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "pantheon-source-ingest/0.1"},
            )
            with _open_configured_url(
                request,
                caller="source_ingest.configured_feed",
                timeout=float(fetch["timeout_seconds"]),
                network_scope=str(fetch.get("network_scope") or "external"),
                allowed_prefixes=allowed_prefixes,
            ) as response:
                final_url = response.geturl()
                _validate_feed_url(final_url, "external feed redirect")
                if not _url_is_allowed(final_url, allowed_prefixes):
                    raise SourceEvidenceError("external feed redirect is outside allowed_url_prefixes")
                if bool(fetch.get("respect_robots_txt", True)):
                    _assert_robots_allowed(
                        final_url,
                        allowed_prefixes,
                        float(fetch["timeout_seconds"]),
                        network_scope=str(fetch.get("network_scope") or "external"),
                    )
                raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise SourceEvidenceError(f"external feed response exceeds fetch.max_bytes={max_bytes}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceEvidenceError("external feed response must be UTF-8 JSON") from exc
        if not isinstance(payload, Mapping):
            raise SourceEvidenceError("external feed response must be a JSON object")
        return payload

    def _source_record_from_config(
        self,
        payload: Mapping[str, Any],
        *,
        connector: SourceConnector,
        watermark: str | None,
        fetch: Mapping[str, Any],
    ) -> SourceRecord:
        metadata = dict(payload.get("metadata") or {})
        if watermark is not None:
            metadata.setdefault("starting_watermark", watermark)
        metadata.setdefault("license_scope", connector.license_scope)
        metadata.setdefault("access_scope", list(fetch.get("default_access_scope") or ["public"]))
        if fetch.get("mode") == "external_feed":
            metadata.setdefault("source_feed_url", fetch.get("url"))
            metadata.setdefault("source_fetch_mode", "external_feed")
        record = SourceRecord(
            source_id=str(payload["source_id"]),
            connector_id=str(payload.get("connector_id") or connector.connector_id),
            source_type=str(payload.get("source_type") or connector.source_type.value),
            title=str(payload["title"]),
            content_ref=str(payload["content_ref"]),
            status=str(payload.get("status") or "normalized"),
            metadata=metadata,
            trace_id=str(payload.get("trace_id") or ""),
            created_at=payload.get("created_at") or _utc_now(),
        )
        return validate_external_source_record(record, connector=connector)


def _normalized_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _url_is_allowed(url: str, allowed_prefixes: list[str]) -> bool:
    return any(url.startswith(prefix) for prefix in allowed_prefixes)


def _validate_feed_url(url: str, field_name: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https", "file"}:
        raise SourceEvidenceError(f"{field_name} must use http, https, or file scheme")
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise SourceEvidenceError(f"{field_name} must include a host")
    if parsed.username or parsed.password:
        raise SourceEvidenceError(f"{field_name} must not include inline credentials")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    sensitive_query_keys = sorted(key for key in query if key.strip().lower() in SENSITIVE_CONFIG_KEYS)
    if sensitive_query_keys:
        raise SourceEvidenceError(f"{field_name} must not include inline secret query parameters")


def _open_configured_url(
    request: urllib.request.Request,
    *,
    caller: str,
    timeout: float,
    network_scope: str,
    allowed_prefixes: list[str],
):
    if network_scope == "internal_service":
        host = urllib.parse.urlparse(request.full_url).hostname or ""
        if not is_internal_host(host):
            raise SourceEvidenceError("internal_service redirect escaped to an external host")
        opener = urllib.request.build_opener(_InternalServiceRedirectHandler(allowed_prefixes))
        return opener.open(request, timeout=timeout)
    return open_external_url(request, caller=caller, timeout=timeout)


class _InternalServiceRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_prefixes: list[str]) -> None:
        super().__init__()
        self.allowed_prefixes = allowed_prefixes

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute_url = urllib.parse.urljoin(req.full_url, newurl)
        host = urllib.parse.urlparse(absolute_url).hostname or ""
        if not is_internal_host(host) or not _url_is_allowed(absolute_url, self.allowed_prefixes):
            raise SourceEvidenceError("internal_service redirect escaped its internal URL allowlist")
        return super().redirect_request(req, fp, code, msg, headers, absolute_url)


def _assert_robots_allowed(
    url: str,
    allowed_prefixes: list[str],
    timeout_seconds: float,
    *,
    network_scope: str = "external",
) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    _validate_feed_url(robots_url, "robots.txt URL")
    if not _url_is_allowed(robots_url, allowed_prefixes):
        raise SourceEvidenceError("robots.txt URL is outside allowed_url_prefixes")
    try:
        request = urllib.request.Request(
            robots_url,
            headers={"Accept": "text/plain", "User-Agent": "pantheon-source-ingest/0.1"},
        )
        with _open_configured_url(
            request,
            caller="source_ingest.configured_robots",
            timeout=timeout_seconds,
            network_scope=network_scope,
            allowed_prefixes=allowed_prefixes,
        ) as response:
            if response.status >= 400:
                return
            robots_txt = response.read(100_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise SourceEvidenceError("robots.txt denied source-ingest access") from exc
        return
    except OSError:
        return
    user_agent = "pantheon-source-ingest"
    if not _robots_allows(robots_txt, user_agent, parsed.path or "/"):
        raise SourceEvidenceError("robots.txt disallows source-ingest access to external feed")


def _robots_allows(robots_txt: str, user_agent: str, path: str) -> bool:
    active = False
    matched = False
    rules: list[tuple[str, str]] = []
    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            active = value == "*" or value.lower() == user_agent.lower()
            matched = matched or active
            continue
        if active and key in {"allow", "disallow"}:
            rules.append((key, value))
    if not matched:
        return True
    decision = True
    longest = -1
    for key, value in rules:
        if value == "":
            continue
        if path.startswith(value) and len(value) >= longest:
            longest = len(value)
            decision = key == "allow"
    return decision


def _reject_inline_fetch_secrets(fetch: Mapping[str, Any]) -> None:
    for key, value in fetch.items():
        normalized_key = str(key).strip().lower()
        if normalized_key == "records":
            continue
        if normalized_key in SENSITIVE_CONFIG_KEYS and value not in (None, "", [], {}):
            raise SourceEvidenceError(f"fetch.{key} must use connector.secret_ref_id instead of an inline secret")
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                child_normalized = str(child_key).strip().lower()
                if child_normalized in SENSITIVE_CONFIG_KEYS and child_value not in (None, "", [], {}):
                    raise SourceEvidenceError(
                        f"fetch.{key}.{child_key} must use connector.secret_ref_id instead of an inline secret"
                    )
