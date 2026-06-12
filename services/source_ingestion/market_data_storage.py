"""JSONL-backed dev storage refs for market-data ingestion outputs."""

from __future__ import annotations

import json
import gzip
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .connectors.base import SourceConnector, SourceRecord


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(value: Any, default: str = "unknown") -> str:
    text = re.sub(r"[^A-Za-z0-9_.=-]+", "-", str(value or "").strip().lower()).strip("-")
    return text or default


def _date_for_record(record: SourceRecord) -> str:
    metadata = dict(record.metadata)
    for key in ("trade_date", "as_of_date", "date", "event_time", "available_time"):
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value)[:10]
    return utc_now_iso()[:10]


def _dataset_for_record(record: SourceRecord, connector: SourceConnector) -> str:
    metadata = dict(record.metadata)
    for key in ("normalized_dataset", "dataset", "source_dataset", "dataset_code"):
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            return str(value)
    for key in ("normalized_target", "dataset"):
        value = connector.metadata.get(key)
        if value not in (None, "", [], {}):
            return str(value)
    return record.source_type.value


def _schema_hash(records: Sequence[SourceRecord], connector: SourceConnector) -> str | None:
    for record in records:
        value = record.metadata.get("schema_hash")
        if value not in (None, "", [], {}):
            return str(value)
    value = connector.metadata.get("schema_hash")
    return str(value) if value not in (None, "", [], {}) else None


def _feature_targets(records: Sequence[SourceRecord], connector: SourceConnector) -> list[str]:
    targets: list[str] = []
    for value in connector.metadata.get("feature_targets") or ():
        text = str(value).strip()
        if text and text not in targets:
            targets.append(text)
    for record in records:
        metadata = dict(record.metadata)
        for key in ("feature_targets", "feature_datasets"):
            raw = metadata.get(key)
            if isinstance(raw, str):
                raw = [raw]
            for value in raw or ():
                text = str(value).strip()
                if text and text not in targets:
                    targets.append(text)
        target = metadata.get("feature_dataset")
        if target not in (None, "", [], {}):
            text = str(target).strip()
            if text and text not in targets:
                targets.append(text)
    return targets


def _append_jsonl(path: Path, rows: Sequence[Mapping[str, Any]], *, compression: str = "none") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if compression == "gzip" or path.suffix == ".gz" else Path.open
    mode = "at" if opener is gzip.open else "a"
    with opener(path, mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _merge_policy(target: dict[str, Any], value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if item not in (None, "", [], {}):
                target[str(key)] = item


def _raw_storage_policy(
    records: Sequence[SourceRecord],
    connector: SourceConnector,
    dataset: str,
) -> dict[str, Any]:
    policy: dict[str, Any] = {}
    _merge_policy(policy, connector.metadata.get("raw_storage_policy"))
    storage_meta = connector.metadata.get("storage")
    if isinstance(storage_meta, Mapping):
        _merge_policy(policy, storage_meta.get("raw_storage_policy"))
    dataset_overrides = policy.pop("dataset_overrides", None)
    if isinstance(dataset_overrides, Mapping):
        _merge_policy(policy, dataset_overrides.get(dataset))
    for record in records:
        _merge_policy(policy, record.metadata.get("raw_storage_policy"))
        record_overrides = record.metadata.get("raw_storage_policy_by_dataset")
        if isinstance(record_overrides, Mapping):
            _merge_policy(policy, record_overrides.get(dataset))
    compression = str(policy.get("compression") or "none").strip().lower()
    if compression not in {"none", "gzip"}:
        compression = "none"
    policy["compression"] = compression
    if policy.get("retention_days") not in (None, "", [], {}):
        policy["retention_days"] = int(policy["retention_days"])
    return policy


@dataclass(frozen=True)
class MarketDataStorageManifest:
    ingest_run_id: str
    raw_refs: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    normalized_refs: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    feature_refs: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    created_at: str = field(default_factory=utc_now_iso)
    schema_version: str = "market_data_storage_manifest.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ingest_run_id": self.ingest_run_id,
            "created_at": self.created_at,
            "raw_refs": [dict(ref) for ref in self.raw_refs],
            "normalized_refs": [dict(ref) for ref in self.normalized_refs],
            "feature_refs": [dict(ref) for ref in self.feature_refs],
            "summary": {
                "raw_ref_count": len(self.raw_refs),
                "normalized_ref_count": len(self.normalized_refs),
                "feature_ref_count": len(self.feature_refs),
                "normalized_row_count": sum(int(ref.get("row_count") or 0) for ref in self.normalized_refs),
            },
        }


class MarketDataStorageWriter:
    """Writes bounded JSONL dev artifacts and returns storage refs."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def write_run(
        self,
        *,
        result: Any,
        connector: SourceConnector,
    ) -> MarketDataStorageManifest:
        records = [record for record in result.records if not record.is_rejected]
        if not records:
            return MarketDataStorageManifest(ingest_run_id=result.run.ingest_run_id)

        grouped: dict[tuple[str, str], list[SourceRecord]] = {}
        for record in records:
            grouped.setdefault((_dataset_for_record(record, connector), _date_for_record(record)), []).append(record)

        raw_refs: list[dict[str, Any]] = []
        normalized_refs: list[dict[str, Any]] = []
        feature_refs: list[dict[str, Any]] = []
        source = _slug(connector.connector_id)
        for (dataset, run_date), dataset_records in sorted(grouped.items()):
            dataset_slug = _slug(dataset)
            raw_policy = _raw_storage_policy(dataset_records, connector, dataset)
            raw_suffix = ".jsonl.gz" if raw_policy.get("compression") == "gzip" else ".jsonl"
            raw_path = self.root / "raw" / source / dataset_slug / f"date={run_date}" / f"{result.run.ingest_run_id}{raw_suffix}"
            normalized_path = (
                self.root
                / "normalized"
                / dataset_slug
                / f"date={run_date}"
                / f"{result.run.ingest_run_id}.jsonl"
            )
            _append_jsonl(
                raw_path,
                [record.to_dict() for record in dataset_records],
                compression=str(raw_policy.get("compression") or "none"),
            )
            _append_jsonl(
                normalized_path,
                [
                    {
                        "source_id": record.source_id,
                        "connector_id": record.connector_id,
                        "dataset": dataset,
                        "content_ref": record.content_ref,
                        "metadata": dict(record.metadata),
                    }
                    for record in dataset_records
                ],
            )
            raw_refs.append(
                {
                    "ref_type": "raw_object",
                    "source": connector.connector_id,
                    "dataset": dataset,
                    "date": run_date,
                    "uri": raw_path.as_posix(),
                    "row_count": len(dataset_records),
                    "compression": raw_policy.get("compression", "none"),
                    "retention_days": raw_policy.get("retention_days"),
                    "retention_policy_ref": raw_policy.get("retention_policy_ref"),
                    "storage_class": raw_policy.get("storage_class"),
                }
            )
            normalized_refs.append(
                {
                    "ref_type": "normalized_rows",
                    "dataset": dataset,
                    "date": run_date,
                    "uri": normalized_path.as_posix(),
                    "row_count": len(dataset_records),
                    "schema_hash": _schema_hash(dataset_records, connector),
                }
            )
            for feature_dataset in _feature_targets(dataset_records, connector):
                feature_path = (
                    self.root
                    / "features"
                    / _slug(feature_dataset)
                    / f"date={run_date}"
                    / f"{result.run.ingest_run_id}.jsonl"
                )
                _append_jsonl(
                    feature_path,
                    [
                        {
                            "source_id": record.source_id,
                            "connector_id": record.connector_id,
                            "source_dataset": dataset,
                            "feature_dataset": feature_dataset,
                            "feature_as_of_time": record.metadata.get("feature_as_of_time")
                            or record.metadata.get("available_time")
                            or record.metadata.get("event_time")
                            or run_date,
                        }
                        for record in dataset_records
                    ],
                )
                feature_refs.append(
                    {
                        "ref_type": "feature_rows",
                        "dataset": feature_dataset,
                        "source_dataset": dataset,
                        "date": run_date,
                        "uri": feature_path.as_posix(),
                        "row_count": len(dataset_records),
                        "feature_as_of_time": run_date,
                    }
                )

        return MarketDataStorageManifest(
            ingest_run_id=result.run.ingest_run_id,
            raw_refs=tuple(raw_refs),
            normalized_refs=tuple(normalized_refs),
            feature_refs=tuple(feature_refs),
        )
