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


# SOURCE-MARKET-STORE-BACKFILL-AND-CLEANUP-20261118-R2: two named settings, both default to today's behaviour.
DAILY_PRICE_READER_ENV = "PANTHEON_DAILY_PRICE_READER"  # run_files (default, old read) | daily_bars
TW_PRICE_DAILY_RUN_FILES_ENV = "PANTHEON_TW_PRICE_DAILY_RUN_FILES"  # on (default) | off


def daily_price_reader_mode(env: Mapping[str, str] | None = None) -> str:
    """Which store the daily price readers use. An unknown value fails closed (no silent fallback)."""
    import os

    raw = str((env if env is not None else os.environ).get(DAILY_PRICE_READER_ENV, "") or "").strip().lower()
    if raw in ("", "run_files"):
        return "run_files"
    if raw == "daily_bars":
        return "daily_bars"
    raise ValueError(f"{DAILY_PRICE_READER_ENV}={raw!r} is not one of run_files, daily_bars")


def tw_price_daily_run_files_enabled(env: Mapping[str, str] | None = None) -> bool:
    """False stops the raw and normalized run files of tw_price_daily; other datasets are untouched."""
    import os

    raw = str((env if env is not None else os.environ).get(TW_PRICE_DAILY_RUN_FILES_ENV, "") or "").strip().lower()
    if raw in ("", "on", "1", "true"):
        return True
    if raw in ("off", "0", "false"):
        return False
    raise ValueError(f"{TW_PRICE_DAILY_RUN_FILES_ENV}={raw!r} is not one of on, off")


# SOURCE-DATASETS-KEYED-WRITE-20261119: three named settings, defaults keep today's reads working.
KEYED_DATASETS_WRITE_ENV = "PANTHEON_KEYED_DATASETS_WRITE"  # dual (default) | keyed_only | run_files
KEYED_DATASET_READER_ENV = "PANTHEON_KEYED_DATASET_READER"  # run_files (default, old read) | keyed
FEATURE_FILES_ENV = "PANTHEON_FEATURE_FILES"  # off (default: derived on read, not stored) | on (old write)


def _env_choice(name: str, allowed: Sequence[str], default: str, env: Mapping[str, str] | None) -> str:
    import os

    raw = str((env if env is not None else os.environ).get(name, "") or "").strip().lower()
    if raw == "":
        return default
    if raw in allowed:
        return raw
    raise ValueError(f"{name}={raw!r} is not one of {', '.join(allowed)}")


def keyed_datasets_write_mode(env: Mapping[str, str] | None = None) -> str:
    """dual: run files and keyed store; keyed_only: keyed store only for declared datasets; run_files: old only."""
    return _env_choice(KEYED_DATASETS_WRITE_ENV, ("dual", "keyed_only", "run_files"), "dual", env)


def keyed_dataset_reader_mode(env: Mapping[str, str] | None = None) -> str:
    return _env_choice(KEYED_DATASET_READER_ENV, ("run_files", "keyed"), "run_files", env)


def feature_files_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Derived feature files stay written (dual-write step of the rollout) until the named setting is
    ``off``; training-session authority still reads ``feature_refs``, so the stop is an explicit rollout step."""
    return _env_choice(FEATURE_FILES_ENV, ("on", "off"), "on", env) == "on"


def normalized_dataset_base(root: str | Path, dataset: str, *, env: Mapping[str, str] | None = None) -> Path:
    """Directory a reader scans for ``date=*/<file>.jsonl[.gz]`` of a dataset. Declared 6A datasets
    resolve to the keyed store when ``PANTHEON_KEYED_DATASET_READER=keyed``; everything else (and the
    default) is the run-file tree. Both trees hold the same record shape."""
    from .daily_bars_store import KEYED_DATASETS, keyed_dataset_dir

    if keyed_dataset_reader_mode(env) == "keyed" and dataset in KEYED_DATASETS:
        return keyed_dataset_dir(root, dataset)
    return Path(root) / "normalized" / dataset


def _run_observed_at(result: Any) -> str:
    """Observation time of a run = its recorded finish time (stable across replays), else now."""
    try:
        finished = result.run.to_dict().get("finished_at")
    except Exception:  # noqa: BLE001 - observation time is metadata only
        finished = None
    return str(finished) if finished else utc_now_iso()


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
    meta = connector.metadata or {}
    for value in meta.get("feature_targets") or ():
        text = str(value).strip()
        if text and text not in targets:
            targets.append(text)
    for value in meta.get("storage_targets") or ():
        text = str(value).strip()
        if text.startswith("features/"):
            feat = text.split("features/", 1)[1].strip()
            if feat and feat not in targets:
                targets.append(feat)
    if not targets:
        try:
            from .connector_definitions import get_connector_definition

            defn = get_connector_definition(connector.connector_id)
            if defn is not None and defn.metadata:
                for value in defn.metadata.get("feature_targets") or ():
                    text = str(value).strip()
                    if text and text not in targets:
                        targets.append(text)
                for value in defn.metadata.get("storage_targets") or ():
                    text = str(value).strip()
                    if text.startswith("features/"):
                        feat = text.split("features/", 1)[1].strip()
                        if feat and feat not in targets:
                            targets.append(feat)
        except Exception:
            pass
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


def _record_symbol(record: SourceRecord) -> str | None:
    meta = record.metadata if isinstance(record.metadata, Mapping) else {}
    nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
    sym = nrow.get("symbol_canonical") or nrow.get("symbol") or meta.get("symbol_canonical") or meta.get("symbol")
    sym = str(sym or "").strip()
    return sym or None


@dataclass(frozen=True)
class MarketDataStorageManifest:
    ingest_run_id: str
    raw_refs: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    normalized_refs: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    feature_refs: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    created_at: str = field(default_factory=utc_now_iso)
    schema_version: str = "market_data_storage_manifest.v1"
    skipped_not_listed_count: int = 0

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
                "skipped_not_listed_count": self.skipped_not_listed_count,
            },
        }


def _compact_keyed_record(record: SourceRecord, dataset: str) -> dict[str, Any]:
    """Record shape of the keyed store = the normalized run-file shape, minus what the run-file tree
    already holds per run: ``content_ref`` (a per-run raw pointer) and metadata entries that repeat
    the same value inside ``normalized_row``. Readers read ``metadata.normalized_row`` and fall back to
    the row for ``available_time`` / ``ingest_time``, so values are unchanged."""
    meta = dict(record.metadata)
    nrow = meta.get("normalized_row")
    if isinstance(nrow, Mapping):
        meta = {k: v for k, v in meta.items() if k == "normalized_row" or k not in nrow or nrow[k] != v}
    return {"source_id": record.source_id, "connector_id": record.connector_id, "dataset": dataset, "metadata": meta}


class MarketDataStorageWriter:
    """Writes bounded JSONL dev artifacts and returns storage refs."""

    def __init__(
        self,
        root: str | Path,
        *,
        daily_bars_store: Any | None = None,
        listing_filter: Any | None = None,
    ) -> None:
        from .daily_bars_store import DailyBarsStore

        # SOURCE-WARRANT-LISTING-EXPIRY: defence in depth. A tw_price_daily record whose symbol is in
        # no current exchange listing (expired warrant, unknown code) is not stored. None = off.
        self.listing_filter = listing_filter

        self.root = Path(root)
        # Dual write (SD_MARKET_DATA_ONE_WRITE_PER_DAY T1): nothing reads it yet except tests.
        self.daily_bars_store = daily_bars_store if daily_bars_store is not None else DailyBarsStore(self.root)
        self.keyed_errors: dict[str, str] = {}
        self.keyed_counters: dict[str, int] = {"partitions_written": 0, "partitions_unchanged": 0, "rejected_no_key": 0}

    def _write_keyed(self, dataset: str, run_date: str, dataset_records: Sequence[SourceRecord], *, ingest_run_id: str) -> None:
        """Write one declared 6A dataset through the keyed partition writer. In dual mode a failure is
        recorded in ``keyed_errors`` and does not fail the run; in keyed_only mode it raises (fail closed)."""
        from .daily_bars_store import keyed_dataset_store

        store = keyed_dataset_store(self.root, dataset)
        if store is None:
            return
        rows = [_compact_keyed_record(r, dataset) for r in dataset_records]
        try:
            out = store.write_records(rows, ingest_run_id=ingest_run_id)
        except Exception as exc:  # noqa: BLE001
            if keyed_datasets_write_mode() == "keyed_only":
                raise
            self.keyed_errors[dataset] = f"{type(exc).__name__}: {exc}"[:300]
            return
        self.keyed_counters["partitions_written"] += len(out["partitions_written"])
        self.keyed_counters["partitions_unchanged"] += len(out["partitions_unchanged"])
        self.keyed_counters["rejected_no_key"] += out["rejected_no_key"]

    def _dual_write_daily_bars(self, grouped, *, result: Any, connector: SourceConnector) -> None:
        """Also write tw_price_daily records to daily_bars. Never changes the existing stores:
        a failure is counted and shown in the health payload, not raised into the ingest run."""
        daily = [r for (dataset, _), recs in grouped.items() if _slug(dataset) == _slug(_DAILY_BAR_DATASET) for r in recs]
        if not daily:
            return
        try:
            market = (connector.metadata or {}).get("market")
            self.daily_bars_store.write_records(
                daily,
                ingest_run_id=result.run.ingest_run_id,
                default_market=str(market).strip() if market else None,
                connector_id=connector.connector_id,
                observed_at=_run_observed_at(result),
            )
        except Exception as exc:  # noqa: BLE001 - dual write must not fail the run; health reports it
            self.daily_bars_store.record_dual_write_error(exc)

    def write_run(
        self,
        *,
        result: Any,
        connector: SourceConnector,
    ) -> MarketDataStorageManifest:
        records = [record for record in result.records if not record.is_rejected]
        skipped_not_listed = 0
        if self.listing_filter is not None and getattr(self.listing_filter, "enabled", True):
            kept: list[SourceRecord] = []
            for record in records:
                if _slug(_dataset_for_record(record, connector)) == _slug(_DAILY_BAR_DATASET):
                    sym = _record_symbol(record)
                    if sym is not None and not self.listing_filter.admits(sym):
                        skipped_not_listed += 1
                        continue
                kept.append(record)
            records = kept
        if not records:
            return MarketDataStorageManifest(
                ingest_run_id=result.run.ingest_run_id, skipped_not_listed_count=skipped_not_listed
            )

        grouped: dict[tuple[str, str], list[SourceRecord]] = {}
        for record in records:
            grouped.setdefault((_dataset_for_record(record, connector), _date_for_record(record)), []).append(record)

        raw_refs: list[dict[str, Any]] = []
        normalized_refs: list[dict[str, Any]] = []
        feature_refs: list[dict[str, Any]] = []
        source = _slug(connector.connector_id)
        skip_daily_run_files = not tw_price_daily_run_files_enabled()
        for (dataset, run_date), dataset_records in sorted(grouped.items()):
            dataset_slug = _slug(dataset)
            if skip_daily_run_files and dataset_slug == _slug(_DAILY_BAR_DATASET):
                continue  # daily_bars (dual write below) is the only store of this dataset
            keyed_mode = keyed_datasets_write_mode()
            from .daily_bars_store import KEYED_DATASETS

            if keyed_mode != "run_files" and dataset_slug in KEYED_DATASETS:
                self._write_keyed(dataset_slug, run_date, dataset_records, ingest_run_id=result.run.ingest_run_id)
                if keyed_mode == "keyed_only":
                    continue  # the keyed store is the only store of this dataset
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
            raw_only = {str(v) for v in (connector.metadata or {}).get("raw_only_datasets") or ()}
            for feature_dataset in () if (dataset in raw_only or not feature_files_enabled()) else _feature_targets(dataset_records, connector):
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

        self._dual_write_daily_bars(grouped, result=result, connector=connector)
        return MarketDataStorageManifest(
            ingest_run_id=result.run.ingest_run_id,
            raw_refs=tuple(raw_refs),
            normalized_refs=tuple(normalized_refs),
            feature_refs=tuple(feature_refs),
            skipped_not_listed_count=skipped_not_listed,
        )


# ---------------------------------------------------------------------------
# Stored history depth (task GOVERNED-FEATURE-DATASETS-20261110)
# ---------------------------------------------------------------------------

HISTORY_DEPTH_SCHEMA_VERSION = "feature_history_depth.v1"
_DAILY_BAR_DATASET = "tw_price_daily"


def _iter_normalized_rows(path: Path) -> Any:
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _row_entity_and_event_date(row: Mapping[str, Any]) -> tuple[str, str] | None:
    meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
    entity = (
        meta.get("symbol_canonical")
        or nrow.get("symbol_canonical")
        or nrow.get("series_id")
        or meta.get("series_id")
        or meta.get("symbol")
        or nrow.get("symbol")
    )
    event = nrow.get("event_time") or nrow.get("date") or meta.get("event_time") or meta.get("date") or meta.get("trade_date")
    if not entity or not event or len(str(event)) < 10:
        return None
    return str(entity), str(event)[:10]


def _depth_stats(dates: set[str]) -> dict[str, Any]:
    if not dates:
        return {"distinct_event_dates": 0, "first_event_date": None, "last_event_date": None, "span_days": 0}
    first, last = min(dates), max(dates)
    span = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).days + 1
    return {
        "distinct_event_dates": len(dates),
        "first_event_date": first,
        "last_event_date": last,
        "span_days": span,
    }


def _stats_from_range(days: int, first: str, last: str) -> dict[str, Any]:
    span = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).days + 1
    return {"distinct_event_dates": days, "first_event_date": first, "last_event_date": last, "span_days": span}


def _bars_depth_entry(stats: Mapping[str, tuple[int, str, str]], dates: Sequence[str]) -> dict[str, Any]:
    """Depth entry of the daily price dataset read from daily_bars (one row per symbol and day)."""
    if not stats:
        return {"row_count": 0, "rows_without_entity_or_event_time": 0, "entity_count": 0,
                **_depth_stats(set()), "per_entity": {}}
    rows = sum(v[0] for v in stats.values())
    return {
        "row_count": rows, "rows_without_entity_or_event_time": 0, "entity_count": len(stats),
        **_stats_from_range(len(dates), min(dates), max(dates)),
        "per_entity": {s: _stats_from_range(*v) for s, v in sorted(stats.items())},
    }


def measure_stored_history_depth(root: str | Path, *, daily_bar_dataset: str = _DAILY_BAR_DATASET) -> dict[str, Any]:
    """Measure stored event-time history per normalized dataset and per symbol.

    Reads ``<root>/normalized/<dataset>/date=*/<run>.jsonl[.gz]``. Depth is counted in
    distinct event dates (re-ingested rows are not double counted). For symbol-keyed
    datasets, ``per_daily_bar_symbol`` lists every symbol that has stored daily bars,
    reporting zero depth where the dataset holds nothing for it; macro series are
    reported per series.
    """
    base = Path(root) / "normalized"
    per_dataset: dict[str, dict[str, set[str]]] = {}
    row_counts: dict[str, int] = {}
    skipped: dict[str, int] = {}
    bars_mode = daily_price_reader_mode() == "daily_bars"
    bars_stats: dict[str, tuple[int, str, str]] = {}
    bars_dates: list[str] = []
    if bars_mode:
        from .daily_bars_store import DailyBarsStore

        _store = DailyBarsStore(root)
        bars_stats = _store.symbol_stats()
        bars_dates = sorted(p.name[5:-5] for p in _store.manifest_dir.glob("date=*.json")) if _store.manifest_dir.is_dir() else []
    if base.is_dir():
        for dataset_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            if bars_mode and dataset_dir.name == _slug(daily_bar_dataset):
                continue  # read from daily_bars below
            entities = per_dataset.setdefault(dataset_dir.name, {})
            for path in sorted(dataset_dir.glob("date=*/*.jsonl*")):
                for row in _iter_normalized_rows(path):
                    row_counts[dataset_dir.name] = row_counts.get(dataset_dir.name, 0) + 1
                    parsed = _row_entity_and_event_date(row)
                    if parsed is None:
                        skipped[dataset_dir.name] = skipped.get(dataset_dir.name, 0) + 1
                        continue
                    entities.setdefault(parsed[0], set()).add(parsed[1])
    from .feature_catalogue import dataset_specs

    for spec in dataset_specs():  # governed datasets with nothing stored are reported as zero
        per_dataset.setdefault(_slug(spec.normalized_dataset), {})
    if bars_mode:
        per_dataset.setdefault(_slug(daily_bar_dataset), {})
    bar_symbols = sorted(bars_stats) if bars_mode else sorted(per_dataset.get(_slug(daily_bar_dataset), {}))
    datasets: dict[str, Any] = {}
    for name, entities in sorted(per_dataset.items()):
        if bars_mode and name == _slug(daily_bar_dataset):
            datasets[name] = _bars_depth_entry(bars_stats, bars_dates)
            continue
        all_dates = set().union(*entities.values()) if entities else set()
        entry: dict[str, Any] = {
            "row_count": row_counts.get(name, 0),
            "rows_without_entity_or_event_time": skipped.get(name, 0),
            "entity_count": len(entities),
            **_depth_stats(all_dates),
        }
        if name == _slug(daily_bar_dataset) or name == "tw_macro_series":
            entry["per_entity"] = {e: _depth_stats(d) for e, d in sorted(entities.items())}
        else:
            entry["per_daily_bar_symbol"] = {s: _depth_stats(entities.get(s, set())) for s in bar_symbols}
            entry["daily_bar_symbols_without_history"] = sum(1 for s in bar_symbols if not entities.get(s))
        datasets[name] = entry
    return {
        "schema_version": HISTORY_DEPTH_SCHEMA_VERSION,
        "storage_root": Path(root).as_posix(),
        "daily_bar_dataset": _slug(daily_bar_dataset),
        "daily_bar_symbol_count": len(bar_symbols),
        "datasets": datasets,
    }


if __name__ == "__main__":  # pragma: no cover - operator CLI
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Measure stored feature history depth for a market data store root.")
    parser.add_argument("root", help="market_data_store root containing normalized/<dataset>/date=*/")
    args = parser.parse_args()
    json.dump(measure_stored_history_depth(args.root), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
