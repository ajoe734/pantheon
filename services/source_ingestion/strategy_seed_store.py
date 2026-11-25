"""Persistent store for StrategySpecSeed objects.

JSONL dev implementation backed by JsonlRegistryStore. Production uses Postgres.
The store is the write boundary: only the materializer and seed review flows
write seeds. All other services read through the owner API.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.source_ingestion.registry.jsonl_store import (
    JsonlRegistryStore,
    _local_lock_for,
    _resolved_key,
)
from services.source_ingestion.negative_memory import (
    is_blocking_negative_memory_match,
    negative_memory_record_from_seed,
)
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)

_IDS_AUDIT_OPTIONAL = True  # lazy import to avoid circular dependency

DEFAULT_MAX_SEEDS_PER_SOURCE = 50
DEFAULT_MAX_TOTAL_SEEDS = 10_000
DEFAULT_MAX_PRUNED_BUNDLES = 20_000

_MUTABLE_PRUNE_STATUSES = frozenset(
    {
        StrategySpecSeedStatus.DRAFT.value,
        StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value,
    }
)
_NEGATIVE_MEMORY_STATUSES = frozenset(
    {
        StrategySpecSeedStatus.REJECTED.value,
        StrategySpecSeedStatus.CONVERTED_TO_NEGATIVE.value,
    }
)
_GOVERNED_PROTECTED_STATUSES = frozenset(
    {
        StrategySpecSeedStatus.ACCEPTED.value,
        StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC.value,
    }
)

_FORBIDDEN_EXECUTION_HINTS = frozenset(
    [
        "broker",
        "live",
        "order_router",
        "execution",
        "runtime_direct",
        "lean_direct",
        "live_trading",
        "order_routing",
    ]
)


def _assert_no_direct_execution_route(seed: StrategySpecSeed) -> None:
    """Raise if seed metadata requests a direct execution route."""
    meta = seed.metadata
    if meta.get("execution_route") not in (None, "none", ""):
        route = meta["execution_route"]
        if str(route).lower() not in ("none", "research"):
            raise StrategySpecSeedStoreError(
                f"Seed metadata requests forbidden execution route: {route!r}"
            )
    backend = str(seed.backend_hint or "").lower()
    for forbidden in _FORBIDDEN_EXECUTION_HINTS:
        if forbidden in backend:
            raise StrategySpecSeedStoreError(
                f"Seed backend_hint contains forbidden execution keyword: {seed.backend_hint!r}"
            )
    lineage = seed.lineage
    if lineage.get("execution_route") not in (None, "none", ""):
        route = lineage["execution_route"]
        if str(route).lower() not in ("none",):
            raise StrategySpecSeedStoreError(
                f"Seed lineage requests forbidden execution route: {route!r}"
            )


def _assert_no_blocking_negative_memory(seed: StrategySpecSeed) -> None:
    if is_blocking_negative_memory_match(seed.negative_memory_match):
        match = dict(seed.negative_memory_match)
        memory_id = str(match.get("matched_memory_id") or "negative-memory-record")
        raise StrategySpecSeedStoreError(
            f"Seed has blocking negative_memory_match against {memory_id}: {match.get('reason')}"
        )


class StrategySpecSeedStoreError(ValueError):
    """Raised when a store invariant is violated."""


class StrategySpecSeedReviewError(StrategySpecSeedStoreError):
    """Raised when a seed review action cannot be applied."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SeedReviewDecisionAction(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    REQUEST_EVIDENCE = "request_evidence"
    CONVERT_TO_SPEC_SEED = "convert_to_spec_seed"
    CONVERT_TO_RISK = "convert_to_risk"
    CONVERT_TO_NEGATIVE = "convert_to_negative"
    ARCHIVE = "archive"
    MERGE = "merge"


@dataclass(frozen=True)
class SeedReviewDecision:
    """Audit record for a governed StrategySpecSeed review transition."""

    decision_id: str
    seed_id: str
    reviewer_id: str
    decision: str
    reason: str
    target_refs: Sequence[Mapping[str, Any]]
    created_at: str
    from_status: str
    to_status: str
    idempotency_key: str | None = None
    request_hash: str | None = None
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        record = {
            "decision_id": self.decision_id,
            "seed_id": self.seed_id,
            "reviewer_id": self.reviewer_id,
            "decision": self.decision,
            "reason": self.reason,
            "target_refs": [dict(item) for item in self.target_refs],
            "created_at": self.created_at,
            "from_status": self.from_status,
            "to_status": self.to_status,
        }
        if self.idempotency_key:
            record["idempotency_key"] = self.idempotency_key
        if self.request_hash:
            record["request_hash"] = self.request_hash
        return record

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        idempotent_replay: bool = False,
    ) -> "SeedReviewDecision":
        return cls(
            decision_id=str(data.get("decision_id") or ""),
            seed_id=str(data.get("seed_id") or ""),
            reviewer_id=str(data.get("reviewer_id") or ""),
            decision=str(data.get("decision") or ""),
            reason=str(data.get("reason") or ""),
            target_refs=[
                dict(item)
                for item in data.get("target_refs") or []
                if isinstance(item, Mapping)
            ],
            created_at=str(data.get("created_at") or ""),
            from_status=str(data.get("from_status") or ""),
            to_status=str(data.get("to_status") or ""),
            idempotency_key=str(data.get("idempotency_key") or "").strip() or None,
            request_hash=str(data.get("request_hash") or "").strip() or None,
            idempotent_replay=idempotent_replay,
        )


_TERMINAL_REVIEW_STATUSES = frozenset(
    {
        StrategySpecSeedStatus.REJECTED.value,
        StrategySpecSeedStatus.ARCHIVED_AS_INSIGHT.value,
        StrategySpecSeedStatus.MERGED.value,
        StrategySpecSeedStatus.CONVERTED_TO_RISK_CONSTRAINT.value,
        StrategySpecSeedStatus.CONVERTED_TO_NEGATIVE.value,
    }
)

_REVIEW_TRANSITIONS: dict[str, dict[SeedReviewDecisionAction, str]] = {
    StrategySpecSeedStatus.DRAFT.value: {
        SeedReviewDecisionAction.ACCEPT: StrategySpecSeedStatus.ACCEPTED.value,
        SeedReviewDecisionAction.REJECT: StrategySpecSeedStatus.REJECTED.value,
        SeedReviewDecisionAction.REQUEST_EVIDENCE: StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value,
        SeedReviewDecisionAction.ARCHIVE: StrategySpecSeedStatus.ARCHIVED_AS_INSIGHT.value,
        SeedReviewDecisionAction.MERGE: StrategySpecSeedStatus.MERGED.value,
        SeedReviewDecisionAction.CONVERT_TO_RISK: StrategySpecSeedStatus.CONVERTED_TO_RISK_CONSTRAINT.value,
        SeedReviewDecisionAction.CONVERT_TO_NEGATIVE: StrategySpecSeedStatus.CONVERTED_TO_NEGATIVE.value,
    },
    StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value: {
        SeedReviewDecisionAction.ACCEPT: StrategySpecSeedStatus.ACCEPTED.value,
        SeedReviewDecisionAction.REJECT: StrategySpecSeedStatus.REJECTED.value,
        SeedReviewDecisionAction.REQUEST_EVIDENCE: StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value,
        SeedReviewDecisionAction.ARCHIVE: StrategySpecSeedStatus.ARCHIVED_AS_INSIGHT.value,
        SeedReviewDecisionAction.MERGE: StrategySpecSeedStatus.MERGED.value,
        SeedReviewDecisionAction.CONVERT_TO_RISK: StrategySpecSeedStatus.CONVERTED_TO_RISK_CONSTRAINT.value,
        SeedReviewDecisionAction.CONVERT_TO_NEGATIVE: StrategySpecSeedStatus.CONVERTED_TO_NEGATIVE.value,
    },
    StrategySpecSeedStatus.ACCEPTED.value: {
        SeedReviewDecisionAction.CONVERT_TO_SPEC_SEED: StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC.value,
        SeedReviewDecisionAction.CONVERT_TO_RISK: StrategySpecSeedStatus.CONVERTED_TO_RISK_CONSTRAINT.value,
        SeedReviewDecisionAction.CONVERT_TO_NEGATIVE: StrategySpecSeedStatus.CONVERTED_TO_NEGATIVE.value,
        SeedReviewDecisionAction.REJECT: StrategySpecSeedStatus.REJECTED.value,
        SeedReviewDecisionAction.REQUEST_EVIDENCE: StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value,
        SeedReviewDecisionAction.ARCHIVE: StrategySpecSeedStatus.ARCHIVED_AS_INSIGHT.value,
        SeedReviewDecisionAction.MERGE: StrategySpecSeedStatus.MERGED.value,
    },
}


@dataclass
class _SeedStoreIndex:
    synced_generation: int = -1
    seeds_cache: dict[str, StrategySpecSeed] = None  # type: ignore[assignment]
    seed_ids_by_bundle: dict[str, list[str]] = None  # type: ignore[assignment]
    seed_ids_by_source: dict[str, list[str]] = None  # type: ignore[assignment]
    negative_memory_by_id: dict[str, dict[str, Any]] = None  # type: ignore[assignment]
    insertion_order: dict[str, int] = None  # type: ignore[assignment]
    next_seq: int = 0
    pruned_by_bundle: dict[tuple[str, frozenset[str]], StrategySpecSeed] = None  # type: ignore[assignment]
    pruned_by_seed_id: dict[str, StrategySpecSeed] = None  # type: ignore[assignment]
    order_generation: int = -2
    order: list[str] = None  # type: ignore[assignment]
    order_by_digest: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.seeds_cache = {}
        self.seed_ids_by_bundle = {}
        self.seed_ids_by_source = {}
        self.negative_memory_by_id = {}
        self.insertion_order = {}
        self.pruned_by_bundle = {}
        self.pruned_by_seed_id = {}
        self.order = []
        self.order_by_digest = {}

    def record_pruned(self, seed: StrategySpecSeed) -> None:
        bundle_key = (seed.evidence_bundle_id, frozenset(seed.source_ids))
        self.pruned_by_bundle.pop(bundle_key, None)
        self.pruned_by_bundle[bundle_key] = seed
        while len(self.pruned_by_bundle) > DEFAULT_MAX_PRUNED_BUNDLES:
            oldest_key = next(iter(self.pruned_by_bundle))
            self.pruned_by_bundle.pop(oldest_key, None)
        self.pruned_by_seed_id.pop(seed.seed_id, None)
        self.pruned_by_seed_id[seed.seed_id] = seed
        while len(self.pruned_by_seed_id) > DEFAULT_MAX_PRUNED_BUNDLES:
            oldest_sid = next(iter(self.pruned_by_seed_id))
            self.pruned_by_seed_id.pop(oldest_sid, None)

    def remove_active_seed(self, seed_id: str, raw: Mapping[str, Any] | None = None) -> None:
        cached = self.seeds_cache.pop(seed_id, None)
        self.negative_memory_by_id.pop(seed_id, None)
        self.insertion_order.pop(seed_id, None)
        bundle_id = (
            cached.evidence_bundle_id
            if cached is not None
            else str((raw or {}).get("evidence_bundle_id") or "")
        )
        if bundle_id and bundle_id in self.seed_ids_by_bundle:
            lst = self.seed_ids_by_bundle[bundle_id]
            if seed_id in lst:
                lst.remove(seed_id)
            if not lst:
                self.seed_ids_by_bundle.pop(bundle_id, None)
        source_id = (
            cached.source_id
            if cached is not None
            else str((raw or {}).get("source_id") or "")
        )
        if source_id and source_id in self.seed_ids_by_source:
            lst = self.seed_ids_by_source[source_id]
            if seed_id in lst:
                lst.remove(seed_id)
            if not lst:
                self.seed_ids_by_source.pop(source_id, None)

    def upsert_active_seed(self, seed: StrategySpecSeed, record: dict[str, Any]) -> None:
        sid = seed.seed_id
        self.seeds_cache[sid] = seed
        if sid not in self.insertion_order:
            self.next_seq += 1
            self.insertion_order[sid] = self.next_seq

        bundle_list = self.seed_ids_by_bundle.setdefault(seed.evidence_bundle_id, [])
        if sid not in bundle_list:
            bundle_list.append(sid)

        source_list = self.seed_ids_by_source.setdefault(seed.source_id, [])
        if sid not in source_list:
            source_list.append(sid)

        neg = negative_memory_record_from_seed(record)
        if neg is not None:
            self.negative_memory_by_id[sid] = neg
        else:
            self.negative_memory_by_id.pop(sid, None)

        self.pruned_by_seed_id.pop(sid, None)
        bundle_key = (seed.evidence_bundle_id, frozenset(seed.source_ids))
        existing_pruned = self.pruned_by_bundle.get(bundle_key)
        if existing_pruned is not None and existing_pruned.seed_id == sid:
            self.pruned_by_bundle.pop(bundle_key, None)


_SEED_INDEXES: dict[str, _SeedStoreIndex] = {}


def _seed_index_for(path: Path) -> _SeedStoreIndex:
    key = _resolved_key(path)
    idx = _SEED_INDEXES.get(key)
    if idx is None:
        idx = _SeedStoreIndex()
        _SEED_INDEXES[key] = idx
    return idx


def _prune_priority_tier(status: str) -> int:
    if status in _MUTABLE_PRUNE_STATUSES:
        return 0
    if status not in _NEGATIVE_MEMORY_STATUSES and status not in _GOVERNED_PROTECTED_STATUSES:
        return 1
    if status not in _NEGATIVE_MEMORY_STATUSES:
        return 2
    return 3


def _select_prune_candidates(
    candidate_ids: Sequence[str],
    raw_records: Mapping[str, dict[str, Any]],
    insertion_order: Mapping[str, int],
    count: int,
) -> list[str]:
    if count <= 0 or not candidate_ids:
        return []

    def _sort_key(sid: str) -> tuple[int, str, int]:
        raw = raw_records.get(sid) or {}
        status = str(raw.get("status") or StrategySpecSeedStatus.DRAFT.value)
        created_at = str(raw.get("created_at") or "")
        seq = insertion_order.get(sid, 0)
        return (_prune_priority_tier(status), created_at, seq)

    ordered = sorted(candidate_ids, key=_sort_key)
    return ordered[:count]


def _seed_records_replay_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if left == right:
        return True
    if left.get("seed_id") != right.get("seed_id"):
        return False
    for key in set(left.keys()) | set(right.keys()):
        if key == "metadata":
            l_meta = dict(left.get("metadata") or {})
            r_meta = dict(right.get("metadata") or {})
            l_meta.pop("materialization_mode", None)
            r_meta.pop("materialization_mode", None)
            if l_meta != r_meta:
                return False
            continue
        if left.get(key) != right.get(key):
            return False
    return True


def _seeds_replay_equal(left: StrategySpecSeed, right: StrategySpecSeed) -> bool:
    return _seed_records_replay_equal(left.to_dict(), right.to_dict())


def _resolve_positive_int(
    explicit: int | None,
    env_name: str,
    default: int,
) -> int:
    if explicit is not None:
        val = int(explicit)
    else:
        raw = os.environ.get(env_name, "").strip()
        val = int(raw) if raw else default
    if val < 1:
        raise StrategySpecSeedStoreError(f"{env_name} must be >= 1; got {val}")
    return val


class StrategySpecSeedStore:
    """JSONL-backed store for StrategySpecSeed persistence.

    One record per seed_id (upsert semantics).  Idempotency: the builder
    derives seed_id from a stable hash of evidence_bundle_id + source_ids +
    hypothesis, so re-materializing the same evidence produces the same key
    and upserts rather than duplicates.

    Growth bounds:
    - ``max_seeds_per_source`` (default 50): bounds active seeds per ``source_id``,
      pruning superseded mutable bundle versions while retaining replay idempotency.
    - ``max_total_seeds`` (default 10,000): bounds total active seeds in the store.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        audit_store: Any = None,
        max_seeds_per_source: int | None = None,
        max_total_seeds: int | None = None,
    ) -> None:
        resolved = (
            Path(path)
            if path
            else Path(
                os.environ.get(
                    "STRATEGY_SEED_STORE_PATH",
                    "data/strategy_seed_store/seeds.jsonl",
                )
            )
        )
        self._max_seeds_per_source = _resolve_positive_int(
            max_seeds_per_source,
            "STRATEGY_SEED_MAX_PER_SOURCE",
            DEFAULT_MAX_SEEDS_PER_SOURCE,
        )
        self._max_total_seeds = _resolve_positive_int(
            max_total_seeds,
            "STRATEGY_SEED_MAX_TOTAL",
            DEFAULT_MAX_TOTAL_SEEDS,
        )
        self._store = JsonlRegistryStore(resolved, id_field="seed_id")
        self._audit_store = audit_store

    @property
    def path(self) -> Path:
        return self._store.path

    @property
    def max_seeds_per_source(self) -> int:
        return self._max_seeds_per_source

    @property
    def max_total_seeds(self) -> int:
        return self._max_total_seeds

    def _ensure_index_locked(self) -> tuple[_SeedStoreIndex, dict[str, dict[str, Any]]]:
        gen, raw_records = self._store._raw_records_snapshot_locked()
        index = _seed_index_for(self._store.path)
        if index.synced_generation == gen:
            return index, raw_records

        if not raw_records and index.synced_generation != -1:
            index.pruned_by_bundle.clear()
            index.pruned_by_seed_id.clear()

        old_cache = index.seeds_cache
        index.seeds_cache = {sid: old_cache[sid] for sid in raw_records if sid in old_cache}
        index.seed_ids_by_bundle.clear()
        index.seed_ids_by_source.clear()
        index.negative_memory_by_id.clear()
        index.insertion_order.clear()
        index.next_seq = 0

        for sid, raw in raw_records.items():
            index.next_seq += 1
            index.insertion_order[sid] = index.next_seq
            bundle_id = str(raw.get("evidence_bundle_id") or "")
            if bundle_id:
                index.seed_ids_by_bundle.setdefault(bundle_id, []).append(sid)
            source_id = str(raw.get("source_id") or "")
            if source_id:
                index.seed_ids_by_source.setdefault(source_id, []).append(sid)
            neg = negative_memory_record_from_seed(raw)
            if neg is not None:
                index.negative_memory_by_id[sid] = neg
            index.pruned_by_seed_id.pop(sid, None)

        index.synced_generation = gen
        return index, raw_records

    def _get_or_build_seed_locked(
        self,
        seed_id: str,
        raw_records: Mapping[str, dict[str, Any]],
        index: _SeedStoreIndex,
    ) -> StrategySpecSeed | None:
        cached = index.seeds_cache.get(seed_id)
        if cached is not None:
            return cached
        raw = raw_records.get(seed_id)
        if raw is None:
            return None
        seed = StrategySpecSeed.from_dict(raw)
        index.seeds_cache[seed_id] = seed
        return seed

    def is_active_seed(self, seed_id: str) -> bool:
        with _local_lock_for(self._store.path):
            _, raw_records = self._ensure_index_locked()
            return str(seed_id) in raw_records

    def save(self, seed: StrategySpecSeed) -> bool:
        """Persist seed (upsert).  Validates governance invariants before writing.

        Returns True when a durable write was performed, or False when the call
        was an idempotent replay.
        """
        _assert_no_direct_execution_route(seed)
        _assert_no_blocking_negative_memory(seed)
        record = seed.to_dict()
        # Promote license_scope and allowed_use to top-level queryable fields.
        record.setdefault("license_scope", seed.metadata.get("source_license_scope", ""))
        record.setdefault("allowed_use", list(seed.metadata.get("access_scope") or []))

        with self._store._mutation_lease():
            index, raw_records = self._ensure_index_locked()
            existing_raw = raw_records.get(seed.seed_id)
            if existing_raw is not None and _seed_records_replay_equal(existing_raw, record):
                return False

            pruned_existing = index.pruned_by_seed_id.get(seed.seed_id)
            if pruned_existing is not None and _seeds_replay_equal(pruned_existing, seed):
                return False

            pruned_ids: list[str] = []
            src_candidates = [
                sid
                for sid in index.seed_ids_by_source.get(seed.source_id, ())
                if sid != seed.seed_id and sid not in pruned_ids and sid in raw_records
            ]
            max_other_for_source = max(0, self._max_seeds_per_source - 1)
            if len(src_candidates) > max_other_for_source:
                excess_src = len(src_candidates) - max_other_for_source
                pruned_ids.extend(
                    _select_prune_candidates(
                        src_candidates,
                        raw_records,
                        index.insertion_order,
                        excess_src,
                    )
                )

            pruned_set = set(pruned_ids)
            active_other_count = sum(
                1 for sid in raw_records if sid != seed.seed_id and sid not in pruned_set
            )
            max_other_total = max(0, self._max_total_seeds - 1)
            if active_other_count > max_other_total:
                excess_total = active_other_count - max_other_total
                total_candidates = [
                    sid
                    for sid in raw_records
                    if sid != seed.seed_id and sid not in pruned_set
                ]
                pruned_ids.extend(
                    _select_prune_candidates(
                        total_candidates,
                        raw_records,
                        index.insertion_order,
                        excess_total,
                    )
                )

            for pid in pruned_ids:
                pruned_raw = raw_records.get(pid)
                pruned_seed = self._get_or_build_seed_locked(pid, raw_records, index)
                if pruned_seed is not None:
                    index.record_pruned(pruned_seed)
                index.remove_active_seed(pid, pruned_raw)

            wrote = self._store.upsert_and_delete(record, delete_ids=pruned_ids)
            index.upsert_active_seed(seed, record)
            index.synced_generation = self._store.generation
            return wrote

    def get(self, seed_id: str) -> StrategySpecSeed | None:
        key = str(seed_id)
        with _local_lock_for(self._store.path):
            index, raw_records = self._ensure_index_locked()
            return self._get_or_build_seed_locked(key, raw_records, index)

    def list_all(self) -> list[StrategySpecSeed]:
        with _local_lock_for(self._store.path):
            index, raw_records = self._ensure_index_locked()
            out: list[StrategySpecSeed] = []
            for sid in raw_records:
                seed = self._get_or_build_seed_locked(sid, raw_records, index)
                if seed is not None:
                    out.append(seed)
            return out

    def read_window(
        self, *, page_token: str | None, max_scan: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return up to ``max_scan`` raw seed records after ``page_token`` and the next token.

        Bounded work: no seed object is built and nothing outside the window is touched. The
        order is the store's insertion order, so seeds appended while a caller pages arrive after
        its cursor. The token is opaque (ordinal plus a digest of the last seed id) and never
        carries a seed id, because the window may cross seeds the caller cannot see. ``None``
        means the end of the store was reached.
        """
        limit = max(1, int(max_scan))
        with _local_lock_for(self._store.path):
            index, raw_records = self._ensure_index_locked()
            if index.order_generation != index.synced_generation:
                index.order = list(raw_records)
                index.order_by_digest = {_cursor_digest(sid): i for i, sid in enumerate(index.order)}
                index.order_generation = index.synced_generation
            start = _cursor_start(page_token, index.order_by_digest, len(index.order))
            ids = index.order[start:start + limit]
            window = [raw_records[sid] for sid in ids if sid in raw_records]
            end = start + len(ids)
            if end >= len(index.order):
                return window, None
            return window, f"{end}.{_cursor_digest(index.order[end - 1])}"

    def cursor_after(self, seed_id: str, *, fallback: str | None = None) -> str | None:
        """Token that resumes right after ``seed_id`` in the order of the last ``read_window``."""
        with _local_lock_for(self._store.path):
            index, _ = self._ensure_index_locked()
            pos = index.order_by_digest.get(_cursor_digest(seed_id)) if index.order_generation == index.synced_generation else None
            if pos is None:
                return fallback
            return None if pos + 1 >= len(index.order) else f"{pos + 1}.{_cursor_digest(seed_id)}"

    def list_by_status(self, status: str | StrategySpecSeedStatus) -> list[StrategySpecSeed]:
        target = status.value if isinstance(status, StrategySpecSeedStatus) else str(status)
        with _local_lock_for(self._store.path):
            index, raw_records = self._ensure_index_locked()
            out: list[StrategySpecSeed] = []
            for sid, raw in raw_records.items():
                if raw.get("status") != target:
                    continue
                seed = self._get_or_build_seed_locked(sid, raw_records, index)
                if seed is not None:
                    out.append(seed)
            return out

    def list_by_bundle(self, evidence_bundle_id: str) -> list[StrategySpecSeed]:
        with _local_lock_for(self._store.path):
            index, raw_records = self._ensure_index_locked()
            out: list[StrategySpecSeed] = []
            for sid in index.seed_ids_by_bundle.get(evidence_bundle_id, ()):
                seed = self._get_or_build_seed_locked(sid, raw_records, index)
                if seed is not None:
                    out.append(seed)
            return out

    def list_negative_memory_records(self) -> list[dict[str, Any]]:
        """Return rejected/retired/failed seed records as matcher inputs."""
        with _local_lock_for(self._store.path):
            index, _ = self._ensure_index_locked()
            return [dict(record) for record in index.negative_memory_by_id.values()]

    def record_review_decision(
        self,
        seed_id: str,
        *,
        decision: str | SeedReviewDecisionAction,
        reviewer_id: str,
        reason: str = "",
        target_refs: Sequence[Mapping[str, Any]] = (),
        created_at: datetime | str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> tuple[StrategySpecSeed, SeedReviewDecision]:
        """Apply a governed review decision and append an audit record."""
        action = _normalize_review_decision(decision)
        if action == SeedReviewDecisionAction.MERGE:
            raise StrategySpecSeedReviewError(
                "invalid_review_action",
                "Use merge_seed for merge review decisions.",
            )
        seed = self._load_seed_for_review(seed_id)
        existing_decision = _find_review_decision_by_idempotency_key(seed, idempotency_key)
        if existing_decision is not None:
            _assert_review_idempotency_match(
                existing_decision,
                action=action,
                reviewer_id=reviewer_id,
                reason=reason,
                target_refs=target_refs,
                request_hash=request_hash,
            )
            return seed, replace(existing_decision, idempotent_replay=True)

        from_status = _status_value(seed.status)
        to_status = _review_transition(from_status, action)
        decision_record = _seed_review_decision(
            seed=seed,
            action=action,
            reviewer_id=reviewer_id,
            reason=reason,
            target_refs=target_refs,
            created_at=created_at,
            from_status=from_status,
            to_status=to_status,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        updated = self._save_review_transition(seed, to_status, decision_record)
        self._emit_review_audit(updated, decision_record, action)
        return updated, decision_record

    def merge_seed(
        self,
        seed_id: str,
        *,
        target_seed_id: str,
        reviewer_id: str,
        reason: str = "",
        created_at: datetime | str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
        target_refs: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[StrategySpecSeed, SeedReviewDecision]:
        """Mark a seed as merged into another seed candidate and audit the action."""
        source = self._load_seed_for_review(seed_id)
        target = _require_text(target_seed_id, "target_seed_id")
        refs = list(target_refs) or [{"type": "strategy_spec_seed", "id": target}]
        existing_decision = _find_review_decision_by_idempotency_key(source, idempotency_key)
        if existing_decision is not None:
            _assert_review_idempotency_match(
                existing_decision,
                action=SeedReviewDecisionAction.MERGE,
                reviewer_id=reviewer_id,
                reason=reason,
                target_refs=refs,
                request_hash=request_hash,
            )
            return source, replace(existing_decision, idempotent_replay=True)

        if target == source.seed_id:
            raise StrategySpecSeedReviewError(
                "invalid_merge_target",
                "target_seed_id must be different from seed_id",
            )
        if self.get(target) is None:
            raise StrategySpecSeedReviewError(
                "merge_target_not_found",
                f"StrategySpecSeed merge target not found: {target}",
            )

        from_status = _status_value(source.status)
        to_status = _review_transition(from_status, SeedReviewDecisionAction.MERGE)
        decision_record = _seed_review_decision(
            seed=source,
            action=SeedReviewDecisionAction.MERGE,
            reviewer_id=reviewer_id,
            reason=reason,
            target_refs=refs,
            created_at=created_at,
            from_status=from_status,
            to_status=to_status,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        updated = self._save_review_transition(
            source,
            to_status,
            decision_record,
            lineage_updates={"merged_into_seed_id": target},
        )
        return updated, decision_record

    def record_replication_submission(
        self,
        seed_id: str,
        *,
        replication_ref: str,
        experiment_task_id: str,
        strategy_id: str,
        strategy_spec_version: str,
        submitted_by: str,
        submitted_at: str,
        idempotency_key: str,
        research_task_ref: str | None = None,
    ) -> StrategySpecSeed:
        """Attach a research replication submission ref to seed lineage."""
        seed = self.get(seed_id)
        if seed is None:
            raise StrategySpecSeedStoreError(f"StrategySpecSeed not found: {seed_id}")

        lineage = dict(seed.lineage)
        existing_ref = str(lineage.get("replication_ref") or "").strip()
        existing_task_id = str(lineage.get("experiment_task_id") or "").strip()
        if existing_ref and existing_task_id:
            return seed

        submission = {
            "replication_ref": _require_text(replication_ref, "replication_ref"),
            "experiment_task_id": _require_text(experiment_task_id, "experiment_task_id"),
            "strategy_id": _require_text(strategy_id, "strategy_id"),
            "strategy_spec_version": _require_text(strategy_spec_version, "strategy_spec_version"),
            "submitted_by": _require_text(submitted_by, "submitted_by"),
            "submitted_at": _require_text(submitted_at, "submitted_at"),
            "idempotency_key": _require_text(idempotency_key, "idempotency_key"),
            "registry_write_performed": False,
            "execution_route": "none",
        }
        if research_task_ref:
            submission["research_task_ref"] = str(research_task_ref)

        lineage.update(submission)
        lineage["registry_write_performed"] = False
        lineage["execution_route"] = "none"
        submissions = list(lineage.get("replication_submissions") or [])
        already_recorded = any(
            item.get("replication_ref") == submission["replication_ref"]
            for item in submissions
            if isinstance(item, dict)
        )
        if not already_recorded:
            submissions.append(dict(submission))
        lineage["replication_submissions"] = submissions

        payload = seed.to_dict()
        payload["lineage"] = lineage
        updated = StrategySpecSeed.from_dict(payload)
        self.save(updated)
        return updated

    def _load_seed_for_review(self, seed_id: str) -> StrategySpecSeed:
        normalized = _require_text(seed_id, "seed_id")
        seed = self.get(normalized)
        if seed is None:
            raise StrategySpecSeedReviewError(
                "seed_not_found",
                f"StrategySpecSeed not found: {normalized}",
            )
        return seed

    def _save_review_transition(
        self,
        seed: StrategySpecSeed,
        to_status: str,
        decision: SeedReviewDecision,
        *,
        lineage_updates: Mapping[str, Any] | None = None,
    ) -> StrategySpecSeed:
        payload = seed.to_dict()
        payload["status"] = to_status
        lineage = dict(payload.get("lineage") or {})
        decisions = [
            dict(item)
            for item in lineage.get("review_decisions") or []
            if isinstance(item, Mapping)
        ]
        decision_payload = decision.to_dict()
        decisions.append(decision_payload)
        lineage["review_decisions"] = decisions
        lineage["last_review_decision"] = decision_payload
        lineage["review_status"] = to_status
        lineage["review_state_machine"] = "strategy_seed_review_v1"
        lineage["registry_write_performed"] = False
        lineage["execution_route"] = "none"
        if to_status == StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC.value:
            lineage["strategy_spec_conversion_eligible"] = True
            lineage["promotion_review_decision_id"] = decision.decision_id
        if lineage_updates:
            lineage.update(dict(lineage_updates))
        payload["lineage"] = lineage
        updated = StrategySpecSeed.from_dict(payload)
        self.save(updated)
        return updated

    def _emit_review_audit(
        self,
        seed: StrategySpecSeed,
        decision: SeedReviewDecision,
        action: SeedReviewDecisionAction,
    ) -> None:
        if self._audit_store is None:
            return
        from services.source_ingestion.ids_audit import IDSAuditEmitter, IDSAuditOutcome, IDSAuditStep, make_pipeline_id
        interaction_id = str(
            (seed.lineage or {}).get("interaction_source_record_id")
            or seed.metadata.get("interaction_source_record_id")
            or ""
        ).strip() or None
        source_surface = str(seed.metadata.get("source_surface") or "unknown")
        pipeline_id = make_pipeline_id(source_surface, seed.seed_id, decision.decision_id)
        auditor = IDSAuditEmitter(self._audit_store, pipeline_id=pipeline_id)
        step = IDSAuditStep.ACCEPT if action == SeedReviewDecisionAction.ACCEPT else IDSAuditStep.REJECT
        outcome = IDSAuditOutcome.SUCCESS
        auditor.emit(
            step,
            source_surface=source_surface,
            outcome=outcome,
            interaction_id=interaction_id,
            seed_id=seed.seed_id,
            actor=decision.reviewer_id,
            details={
                "seed_id": seed.seed_id,
                "decision": decision.decision,
                "from_status": decision.from_status,
                "to_status": decision.to_status,
                "reason": decision.reason,
            },
        )

    def get_by_bundle_idempotent(
        self,
        evidence_bundle_id: str,
        source_ids: Sequence[str],
    ) -> StrategySpecSeed | None:
        """Return an existing seed for this bundle+sources if one exists.

        Used by the materializer to detect idempotent re-runs without
        re-building the full seed payload, including when an older version of a
        bundle was superseded and pruned from active seeds.
        """
        source_set = frozenset(source_ids)
        with _local_lock_for(self._store.path):
            index, raw_records = self._ensure_index_locked()
            for sid in index.seed_ids_by_bundle.get(evidence_bundle_id, ()):
                raw = raw_records.get(sid)
                if raw is None:
                    continue
                stored_sources = frozenset(raw.get("source_ids") or ())
                if stored_sources == source_set:
                    return self._get_or_build_seed_locked(sid, raw_records, index)
            pruned = index.pruned_by_bundle.get((evidence_bundle_id, source_set))
            if pruned is not None:
                return pruned
        return None

    def count(self) -> int:
        with _local_lock_for(self._store.path):
            _, raw_records = self._ensure_index_locked()
            return len(raw_records)


def _cursor_digest(seed_id: str) -> str:
    return hashlib.sha256(str(seed_id).encode("utf-8")).hexdigest()[:16]


def _cursor_start(token: str | None, order_by_digest: Mapping[str, int], size: int) -> int:
    """Position after the cursor seed; falls back to the ordinal when that seed was pruned."""
    if not token:
        return 0
    ordinal_text, _, digest = str(token).partition(".")
    if not ordinal_text.isdigit() or not digest:
        raise StrategySpecSeedStoreError("invalid_page_token")
    pos = order_by_digest.get(digest)
    return min(pos + 1 if pos is not None else int(ordinal_text), size)


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise StrategySpecSeedStoreError(f"{field_name} is required")
    return text


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime | str | None) -> str:
    if value is None:
        value = _utc_now()
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _status_value(status: StrategySpecSeedStatus | str) -> str:
    return status.value if isinstance(status, StrategySpecSeedStatus) else str(status)


def _normalize_review_decision(value: str | SeedReviewDecisionAction) -> SeedReviewDecisionAction:
    if isinstance(value, SeedReviewDecisionAction):
        return value
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "request_more_evidence": SeedReviewDecisionAction.REQUEST_EVIDENCE.value,
        "needs_more_evidence": SeedReviewDecisionAction.REQUEST_EVIDENCE.value,
        "convert": SeedReviewDecisionAction.CONVERT_TO_SPEC_SEED.value,
        "convert_to_strategy_spec": SeedReviewDecisionAction.CONVERT_TO_SPEC_SEED.value,
        "convert_to_spec_seed": SeedReviewDecisionAction.CONVERT_TO_SPEC_SEED.value,
        "archived_as_insight": SeedReviewDecisionAction.ARCHIVE.value,
    }
    normalized = aliases.get(normalized, normalized)
    try:
        return SeedReviewDecisionAction(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SeedReviewDecisionAction)
        raise StrategySpecSeedReviewError(
            "invalid_review_action",
            f"review decision must be one of: {allowed}",
        ) from exc


def _normalized_idempotency_key(value: str | None) -> str:
    return str(value or "").strip()


def _normalized_request_hash(value: str | None) -> str:
    return str(value or "").strip()


def _normalized_target_refs(refs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(ref)
        for ref in refs
        if isinstance(ref, Mapping)
    ]


def _find_review_decision_by_idempotency_key(
    seed: StrategySpecSeed,
    idempotency_key: str | None,
) -> SeedReviewDecision | None:
    normalized_key = _normalized_idempotency_key(idempotency_key)
    if not normalized_key:
        return None
    lineage = dict(seed.lineage or {})
    for raw in lineage.get("review_decisions") or []:
        if not isinstance(raw, Mapping):
            continue
        if _normalized_idempotency_key(str(raw.get("idempotency_key") or "")) == normalized_key:
            return SeedReviewDecision.from_dict(raw, idempotent_replay=True)
    return None


def _assert_review_idempotency_match(
    existing: SeedReviewDecision,
    *,
    action: SeedReviewDecisionAction,
    reviewer_id: str,
    reason: str,
    target_refs: Sequence[Mapping[str, Any]],
    request_hash: str | None,
) -> None:
    existing_hash = _normalized_request_hash(existing.request_hash)
    incoming_hash = _normalized_request_hash(request_hash)
    if existing_hash and incoming_hash and existing_hash != incoming_hash:
        raise StrategySpecSeedReviewError(
            "idempotency_conflict",
            "Idempotency key was already used with a different review request.",
        )

    if existing_hash and not incoming_hash:
        return

    reviewer = _require_text(reviewer_id, "reviewer_id")
    if (
        existing.decision != action.value
        or existing.reviewer_id != reviewer
        or existing.reason != str(reason or "").strip()
        or _normalized_target_refs(existing.target_refs) != _normalized_target_refs(target_refs)
    ):
        raise StrategySpecSeedReviewError(
            "idempotency_conflict",
            "Idempotency key was already used with a different review request.",
        )


def _review_transition(from_status: str, action: SeedReviewDecisionAction) -> str:
    if from_status in _TERMINAL_REVIEW_STATUSES:
        raise StrategySpecSeedReviewError(
            "terminal_seed_status",
            f"StrategySpecSeed status {from_status!r} is terminal and cannot be reviewed further.",
        )
    allowed = _REVIEW_TRANSITIONS.get(from_status, {})
    if action not in allowed:
        allowed_actions = ", ".join(item.value for item in allowed) or "none"
        raise StrategySpecSeedReviewError(
            "invalid_status_transition",
            (
                f"Review decision {action.value!r} is not allowed from status "
                f"{from_status!r}; allowed actions: {allowed_actions}"
            ),
        )
    return allowed[action]


def _seed_review_decision(
    *,
    seed: StrategySpecSeed,
    action: SeedReviewDecisionAction,
    reviewer_id: str,
    reason: str,
    target_refs: Sequence[Mapping[str, Any]],
    created_at: datetime | str | None,
    from_status: str,
    to_status: str,
    idempotency_key: str | None,
    request_hash: str | None,
) -> SeedReviewDecision:
    timestamp = _iso(created_at)
    normalized_refs = [
        dict(ref)
        for ref in target_refs
        if isinstance(ref, Mapping)
    ]
    reviewer = _require_text(reviewer_id, "reviewer_id")
    decision_id = "seed-review-" + hashlib.sha1(
        "\n".join(
            [
                seed.seed_id,
                action.value,
                reviewer,
                timestamp,
                str(idempotency_key or ""),
            ]
        ).encode("utf-8")
    ).hexdigest()[:12]
    return SeedReviewDecision(
        decision_id=decision_id,
        seed_id=seed.seed_id,
        reviewer_id=reviewer,
        decision=action.value,
        reason=str(reason or "").strip(),
        target_refs=normalized_refs,
        created_at=timestamp,
        from_status=from_status,
        to_status=to_status,
        idempotency_key=str(idempotency_key or "").strip() or None,
        request_hash=str(request_hash or "").strip() or None,
    )
