"""SourceHealth and SourceUsageDaily models and JSONL stores.

SourceHealth captures the last-run health of a data or strategy seed source.
SourceUsageDaily captures daily aggregated usage for each source.
Both stores are backed by JSONL dev stores; Postgres is the production target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .registry.jsonl_store import JsonlRegistryStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require(value: Any, name: str) -> str:
    s = str(value or "").strip()
    if not s:
        raise SourceHealthError(f"{name} is required")
    return s


class SourceHealthError(ValueError):
    """Raised when SourceHealth invariants are violated."""


class SourceHealthStatus(str, Enum):
    OK = "ok"
    STALE = "stale"
    DEGRADED = "degraded"
    FAILED = "failed"
    DISABLED = "disabled"
    RETIRED = "retired"


class SourceKind(str, Enum):
    DATA_SOURCE = "data_source"
    STRATEGY_SEED_SOURCE = "strategy_seed_source"


_SCHEMA_VERSION = "source_health.v1"
_USAGE_SCHEMA_VERSION = "source_usage_daily.v1"


@dataclass(frozen=True)
class SourceHealth:
    """Health snapshot for one data or strategy seed source."""

    source_id: str
    source_kind: str
    status: str = SourceHealthStatus.OK.value
    last_success_at: str | None = None
    last_failure_at: str | None = None
    latest_watermark: str | None = None
    row_count_last_run: int = 0
    rejected_count_last_run: int = 0
    schema_hash: str | None = None
    staleness_seconds: int | None = None
    error_rate_7d: float = 0.0
    cost_estimate_30d: float | None = None
    updated_at: str = field(default_factory=_utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _require(self.source_id, "source_id"))
        try:
            SourceKind(str(self.source_kind))
        except ValueError:
            allowed = ", ".join(k.value for k in SourceKind)
            raise SourceHealthError(f"source_kind must be one of: {allowed}")
        try:
            SourceHealthStatus(str(self.status))
        except ValueError:
            allowed = ", ".join(s.value for s in SourceHealthStatus)
            raise SourceHealthError(f"status must be one of: {allowed}")
        object.__setattr__(self, "row_count_last_run", max(0, int(self.row_count_last_run or 0)))
        object.__setattr__(self, "rejected_count_last_run", max(0, int(self.rejected_count_last_run or 0)))
        object.__setattr__(self, "error_rate_7d", max(0.0, min(1.0, float(self.error_rate_7d or 0.0))))
        if self.staleness_seconds is not None:
            object.__setattr__(self, "staleness_seconds", int(self.staleness_seconds))
        if self.cost_estimate_30d is not None:
            object.__setattr__(self, "cost_estimate_30d", float(self.cost_estimate_30d))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "status": self.status,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "latest_watermark": self.latest_watermark,
            "row_count_last_run": self.row_count_last_run,
            "rejected_count_last_run": self.rejected_count_last_run,
            "schema_hash": self.schema_hash,
            "staleness_seconds": self.staleness_seconds,
            "error_rate_7d": self.error_rate_7d,
            "cost_estimate_30d": self.cost_estimate_30d,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceHealth":
        return cls(
            source_id=str(data["source_id"]),
            source_kind=str(data["source_kind"]),
            status=str(data.get("status", SourceHealthStatus.OK.value)),
            last_success_at=data.get("last_success_at"),
            last_failure_at=data.get("last_failure_at"),
            latest_watermark=data.get("latest_watermark"),
            row_count_last_run=int(data.get("row_count_last_run") or 0),
            rejected_count_last_run=int(data.get("rejected_count_last_run") or 0),
            schema_hash=data.get("schema_hash"),
            staleness_seconds=data.get("staleness_seconds"),
            error_rate_7d=float(data.get("error_rate_7d") or 0.0),
            cost_estimate_30d=data.get("cost_estimate_30d"),
            updated_at=str(data.get("updated_at") or _utc_now()),
            metadata=dict(data.get("metadata") or {}),
        )


class SourceHealthStore:
    """JSONL-backed store for SourceHealth records keyed by source_id."""

    def __init__(self, store: JsonlRegistryStore | None = None) -> None:
        self._index: dict[str, SourceHealth] = {}
        self._store = store
        if store is not None:
            self._load()

    def _load(self) -> None:
        for record in self._store.read_all():
            try:
                h = SourceHealth.from_dict(record)
                self._index[h.source_id] = h
            except (SourceHealthError, KeyError, TypeError):
                pass

    def upsert(self, health: SourceHealth) -> SourceHealth:
        self._index[health.source_id] = health
        if self._store is not None:
            self._store.upsert(health.to_dict())
        return health

    def get(self, source_id: str) -> SourceHealth | None:
        return self._index.get(source_id)

    def list(self, *, source_kind: str | None = None) -> list[SourceHealth]:
        results = list(self._index.values())
        if source_kind is not None:
            results = [h for h in results if h.source_kind == source_kind]
        return results

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "SourceHealthStore":
        store = JsonlRegistryStore(path, id_field="source_id")
        return cls(store=store)


@dataclass(frozen=True)
class SourceUsageDaily:
    """Daily aggregated usage counts for one source."""

    date: str  # YYYY-MM-DD
    source_id: str
    source_kind: str
    ingest_run_count: int = 0
    query_count: int = 0
    search_hit_count: int = 0
    persona_match_count: int = 0
    strategy_seed_yield_count: int = 0
    strategy_promotion_count: int = 0
    experiment_dependency_count: int = 0
    active_strategy_dependency_count: int = 0
    cost_estimate: float | None = None
    updated_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "date", _require(self.date, "date"))
        object.__setattr__(self, "source_id", _require(self.source_id, "source_id"))
        try:
            SourceKind(str(self.source_kind))
        except ValueError:
            allowed = ", ".join(k.value for k in SourceKind)
            raise SourceHealthError(f"source_kind must be one of: {allowed}")
        for fname in (
            "ingest_run_count", "query_count", "search_hit_count",
            "persona_match_count", "strategy_seed_yield_count",
            "strategy_promotion_count", "experiment_dependency_count",
            "active_strategy_dependency_count",
        ):
            object.__setattr__(self, fname, max(0, int(getattr(self, fname) or 0)))
        if self.cost_estimate is not None:
            object.__setattr__(self, "cost_estimate", float(self.cost_estimate))

    @property
    def record_id(self) -> str:
        return f"{self.date}::{self.source_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _USAGE_SCHEMA_VERSION,
            "record_id": self.record_id,
            "date": self.date,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "ingest_run_count": self.ingest_run_count,
            "query_count": self.query_count,
            "search_hit_count": self.search_hit_count,
            "persona_match_count": self.persona_match_count,
            "strategy_seed_yield_count": self.strategy_seed_yield_count,
            "strategy_promotion_count": self.strategy_promotion_count,
            "experiment_dependency_count": self.experiment_dependency_count,
            "active_strategy_dependency_count": self.active_strategy_dependency_count,
            "cost_estimate": self.cost_estimate,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceUsageDaily":
        return cls(
            date=str(data["date"]),
            source_id=str(data["source_id"]),
            source_kind=str(data["source_kind"]),
            ingest_run_count=int(data.get("ingest_run_count") or 0),
            query_count=int(data.get("query_count") or 0),
            search_hit_count=int(data.get("search_hit_count") or 0),
            persona_match_count=int(data.get("persona_match_count") or 0),
            strategy_seed_yield_count=int(data.get("strategy_seed_yield_count") or 0),
            strategy_promotion_count=int(data.get("strategy_promotion_count") or 0),
            experiment_dependency_count=int(data.get("experiment_dependency_count") or 0),
            active_strategy_dependency_count=int(data.get("active_strategy_dependency_count") or 0),
            cost_estimate=data.get("cost_estimate"),
            updated_at=str(data.get("updated_at") or _utc_now()),
        )


class SourceUsageDailyStore:
    """JSONL-backed store for SourceUsageDaily records.

    Primary key is ``record_id`` = ``date::source_id``.
    """

    def __init__(self, store: JsonlRegistryStore | None = None) -> None:
        self._index: dict[str, SourceUsageDaily] = {}
        self._store = store
        if store is not None:
            self._load()

    def _load(self) -> None:
        for record in self._store.read_all():
            try:
                r = SourceUsageDaily.from_dict(record)
                self._index[r.record_id] = r
            except (SourceHealthError, KeyError, TypeError):
                pass

    def upsert(self, record: SourceUsageDaily) -> SourceUsageDaily:
        self._index[record.record_id] = record
        if self._store is not None:
            self._store.upsert(record.to_dict())
        return record

    def get(self, date: str, source_id: str) -> SourceUsageDaily | None:
        return self._index.get(f"{date}::{source_id}")

    def list(
        self,
        *,
        source_id: str | None = None,
        source_kind: str | None = None,
        date: str | None = None,
    ) -> list[SourceUsageDaily]:
        results = list(self._index.values())
        if source_id is not None:
            results = [r for r in results if r.source_id == source_id]
        if source_kind is not None:
            results = [r for r in results if r.source_kind == source_kind]
        if date is not None:
            results = [r for r in results if r.date == date]
        return results

    def aggregate_for_source(self, source_id: str, *, days: int = 30) -> dict[str, Any]:
        """Sum usage fields across the most recent N days of records for a source."""
        records = sorted(
            [r for r in self.list(source_id=source_id)],
            key=lambda r: r.date,
            reverse=True,
        )[:days]
        if not records:
            return {
                "source_id": source_id,
                "days": days,
                "record_count": 0,
                "total_ingest_run_count": 0,
                "total_query_count": 0,
                "total_search_hit_count": 0,
                "total_persona_match_count": 0,
                "total_strategy_seed_yield_count": 0,
                "total_strategy_promotion_count": 0,
                "total_experiment_dependency_count": 0,
                "max_active_strategy_dependency_count": 0,
                "total_cost_estimate": None,
            }
        total_cost: float | None = None
        for r in records:
            if r.cost_estimate is not None:
                total_cost = (total_cost or 0.0) + r.cost_estimate
        return {
            "source_id": source_id,
            "days": days,
            "record_count": len(records),
            "total_ingest_run_count": sum(r.ingest_run_count for r in records),
            "total_query_count": sum(r.query_count for r in records),
            "total_search_hit_count": sum(r.search_hit_count for r in records),
            "total_persona_match_count": sum(r.persona_match_count for r in records),
            "total_strategy_seed_yield_count": sum(r.strategy_seed_yield_count for r in records),
            "total_strategy_promotion_count": sum(r.strategy_promotion_count for r in records),
            "total_experiment_dependency_count": sum(r.experiment_dependency_count for r in records),
            "max_active_strategy_dependency_count": max(r.active_strategy_dependency_count for r in records),
            "total_cost_estimate": total_cost,
        }

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "SourceUsageDailyStore":
        store = JsonlRegistryStore(path, id_field="record_id")
        return cls(store=store)
