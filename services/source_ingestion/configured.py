"""Persistent configured-connector fetch support for source ingestion."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .connectors import SourceConnector, SourceEvidenceError, SourceRecord
from .scheduler import IngestBatch


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
        self._configs: dict[str, ConfiguredConnector] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
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
                connector = SourceConnector.from_dict(connector_payload)
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
        normalized_fetch = self._validate_fetch_config(fetch)
        config = ConfiguredConnector(connector=connector, fetch=normalized_fetch, updated_at=_utc_now())
        self._configs[connector.connector_id] = config
        self._append("connector_config", connector.connector_id, config.to_dict())
        return config

    def get_config(self, connector_id: str) -> ConfiguredConnector | None:
        return self._configs.get(connector_id)

    def list_configs(self) -> list[ConfiguredConnector]:
        return list(self._configs.values())

    def get_fetch_state(self, connector_id: str) -> dict[str, Any]:
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
        state = self.get_fetch_state(connector_id)
        state["connector_id"] = connector_id
        state["attempts"] = int(state.get("attempts") or 0) + 1
        if success:
            state["successful_attempts"] = int(state.get("successful_attempts") or 0) + 1
            state["last_error"] = None
        else:
            state["failed_attempts"] = int(state.get("failed_attempts") or 0) + 1
            state["last_error"] = str(error or "configured connector fetch failed")
        state["updated_at"] = _utc_now()
        self._states[connector_id] = state
        self._append("connector_fetch_state", connector_id, state)
        return dict(state)

    def _validate_fetch_config(self, fetch: Mapping[str, Any]) -> dict[str, Any]:
        mode = str(fetch.get("mode") or "").strip()
        if mode != "static_records":
            raise SourceEvidenceError("fetch.mode must be static_records")
        records = fetch.get("records")
        if not isinstance(records, list):
            raise SourceEvidenceError("fetch.records must be a list")
        fail_until_attempt = int(fetch.get("fail_until_attempt") or 0)
        if fail_until_attempt < 0:
            raise SourceEvidenceError("fetch.fail_until_attempt must be >= 0")
        return {
            "mode": mode,
            "records": [dict(record) for record in records],
            "next_watermark": fetch.get("next_watermark"),
            "fail_until_attempt": fail_until_attempt,
            "failure_reason": str(fetch.get("failure_reason") or "configured connector fetch failed"),
        }

    def _append(self, record_type: str, record_id: str, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "schema_version": "source_connector_config_store.v1",
            "record_type": record_type,
            "record_id": record_id,
            "payload": dict(payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")


class ConfiguredConnectorFetcher:
    """Fetches source batches from connector-owned service configuration."""

    def __init__(self, store: JsonlConfiguredConnectorStore) -> None:
        self.store = store

    def fetch_batch(self, connector_id: str, watermark: str | None) -> IngestBatch:
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

        records = tuple(
            self._source_record_from_config(
                payload,
                connector=config.connector,
                watermark=watermark,
            )
            for payload in config.fetch.get("records", [])
        )
        self.store.record_fetch_attempt(connector_id, success=True)
        return IngestBatch(records=records, next_watermark=config.fetch.get("next_watermark"))

    def _source_record_from_config(
        self,
        payload: Mapping[str, Any],
        *,
        connector: SourceConnector,
        watermark: str | None,
    ) -> SourceRecord:
        metadata = dict(payload.get("metadata") or {})
        if watermark is not None:
            metadata.setdefault("starting_watermark", watermark)
        metadata.setdefault("license_scope", connector.license_scope)
        return SourceRecord(
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
