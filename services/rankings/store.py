"""Independent persistent write owner for the Rankings domain.

Write authority: Rankings domain only (rankings-svc). This module is the
sole write path for ranking records and deliberately does not import
``services/control-plane/bff/read_store.py``: that module's ranking helpers
keep an in-process local overlay dict as a response fallback, which is not a
durable write path and does not survive process restart or a second reader
process.

Generation 2 narrows this store to a single concrete backend:
``PostgresJsonOwnerStore`` (see
``DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md``). Generation 1 shipped a
JSON-file backend and a Postgres backend side by side, selected by an
environment variable -- two persistence implementations for the same
domain, which duplicates the durability guarantee this module exists to
provide instead of concentrating it in one place. There is exactly one
storage implementation now: every read re-runs a fresh ``SELECT`` against
the owner table, so a ``get``/``list`` immediately observes a write
committed by a different store instance, including one in a different
process.

Source Ingestion remains reconcile-only for this domain: it may read the
durable rankings state to reconcile, but it is not a write owner and must
not call the write methods below.

Generation 3 adds two additional immutable record kinds on the same sole
``PostgresJsonOwnerStore`` table: ``RankingSnapshotRecord`` and
``AllocationEvaluationRecord``. They are namespaced under distinct record-id
prefixes so they can never collide with each other or with a legacy
``RankingRecord`` ranking_id, and they intentionally expose no update or
delete: once created, a snapshot or evaluation is either an idempotent
byte-identical replay of what already exists, or a rejected conflict. Every
generation-3 durable payload carries an explicit ``record_type`` envelope so
the legacy CRUD surface can positively recognize and skip exactly the known
non-legacy kinds, rather than inferring "not a legacy row" from the mere
absence of ``ranking_id``. The legacy ``RankingRecord`` CRUD surface above is
unchanged.
"""
from __future__ import annotations

import math
import os
import threading
from collections.abc import Mapping as AbcMapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RankingWriteOwnerError(RuntimeError):
    """Raised for invalid ranking records or write-ownership violations."""


class RankingConflictError(RankingWriteOwnerError):
    """Raised when ``create_ranking`` targets an existing ``ranking_id``."""


@dataclass
class RankingRecord:
    ranking_id: str
    title: str
    criteria: str
    entries: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "active"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RankingRecord":
        return cls(
            ranking_id=str(payload["ranking_id"]),
            title=str(payload.get("title", "")),
            criteria=str(payload.get("criteria", "")),
            entries=[dict(entry) for entry in (payload.get("entries") or [])],
            status=str(payload.get("status", "active")),
            created_at=str(payload.get("created_at") or utc_now()),
            updated_at=str(payload.get("updated_at") or utc_now()),
        )


def _validate(record: RankingRecord) -> None:
    if not record.ranking_id or not record.ranking_id.strip():
        raise RankingWriteOwnerError("ranking_id is required")
    if not record.title or not record.title.strip():
        raise RankingWriteOwnerError("title is required")
    if not isinstance(record.entries, list):
        raise RankingWriteOwnerError("entries must be a list")


def _deep_freeze(value: Any) -> Any:
    """Recursively convert nested mapping/list values into immutable structures.

    Accepts any ``collections.abc.Mapping`` -- not just ``dict`` -- so a
    caller-supplied custom mapping type is copied field-by-field into a
    ``MappingProxyType`` rather than referenced. The original object (and any
    later mutation of it) can never reach the durable/frozen state.
    """

    if isinstance(value, AbcMapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    """Reverse of ``_deep_freeze`` for durable storage payloads."""

    if isinstance(value, AbcMapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


_SNAPSHOT_ID_PREFIX = "ranking-snapshot::"
_EVALUATION_ID_PREFIX = "allocation-evaluation::"

# Explicit envelope discriminators. Generation-3 rows always carry one of
# these so the legacy CRUD surface can recognize and skip exactly the known
# non-legacy kinds instead of treating every payload lacking ``ranking_id``
# as safely ignorable.
_SNAPSHOT_RECORD_TYPE = "ranking_snapshot"
_EVALUATION_RECORD_TYPE = "allocation_evaluation"
_RECOGNIZED_NON_LEGACY_RECORD_TYPES = frozenset(
    (_SNAPSHOT_RECORD_TYPE, _EVALUATION_RECORD_TYPE)
)

_SNAPSHOT_FIELDS: Tuple[str, ...] = (
    "record_type",
    "ranking_snapshot_id",
    "surface",
    "period",
    "formula_version",
    "content_digest",
    "items",
    "evidence_assertion_digests",
    "created_at",
)

_EVALUATION_REQUIRED_FIELDS: Tuple[str, ...] = (
    "record_type",
    "allocation_evaluation_id",
    "ranking_snapshot_id",
    "allocation_policy_version",
    "content_digest",
    "lines",
    "created_at",
    "applied",
)
_EVALUATION_OPTIONAL_FIELDS: Tuple[str, ...] = (
    "authority_mode",
    "promotion_review_id",
)


def _validate_durable_value(value: Any, *, path: str = "value") -> None:
    """Reject anything that cannot round-trip through JSON unchanged.

    Accepts the frozen shapes produced by ``_deep_freeze`` (``MappingProxyType``
    and ``tuple``) as well as their unfrozen originals -- and, for mappings,
    any ``collections.abc.Mapping`` implementation, not just ``dict`` -- so
    this can validate either a raw constructor argument or an already-frozen
    attribute. ``NaN``/``Infinity``/``-Infinity`` floats are rejected: they
    serialize through ``json.dumps`` by default but are not valid portable
    JSON, so a durable payload containing one would not round-trip through a
    strict JSON reader.
    """

    if value is None or isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RankingWriteOwnerError(f"{path} must be a finite number, got {value!r}")
        return
    if isinstance(value, (str, int)):
        return
    if isinstance(value, AbcMapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise RankingWriteOwnerError(f"{path} keys must be strings")
            _validate_durable_value(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_durable_value(item, path=f"{path}[{index}]")
        return
    raise RankingWriteOwnerError(f"{path} must be a JSON-compatible value, got {type(value).__name__}")


def _validate_mapping_collection(value: Any, *, path: str) -> None:
    """Require the historical durable shape: a list/tuple of mapping entries.

    Used for ``items``/``lines``, which have always been collections of
    record-shaped mappings. A bare scalar or a malformed (non-mapping) entry
    is rejected here rather than silently accepted as "JSON-compatible".
    """

    if not isinstance(value, (list, tuple)):
        raise RankingWriteOwnerError(f"{path} must be a list of mapping entries, got {type(value).__name__}")
    for index, item in enumerate(value):
        if not isinstance(item, AbcMapping):
            raise RankingWriteOwnerError(f"{path}[{index}] must be a mapping, got {type(item).__name__}")
    _validate_durable_value(value, path=path)


def _validate_evidence_assertion_digests(value: Any) -> None:
    """Enforce the historical shape: persona id -> list of digest strings."""

    if not isinstance(value, AbcMapping):
        raise RankingWriteOwnerError(
            "evidence_assertion_digests must be a mapping of persona id to digest list"
        )
    for persona_id, digests in value.items():
        if not isinstance(persona_id, str) or not persona_id.strip():
            raise RankingWriteOwnerError("evidence_assertion_digests keys must be non-empty persona id strings")
        if not isinstance(digests, (list, tuple)):
            raise RankingWriteOwnerError(
                f"evidence_assertion_digests[{persona_id!r}] must be a list of digest strings"
            )
        for digest in digests:
            if not isinstance(digest, str) or not digest.strip():
                raise RankingWriteOwnerError(
                    f"evidence_assertion_digests[{persona_id!r}] entries must be non-empty strings"
                )


@dataclass(frozen=True)
class RankingSnapshotRecord:
    """An immutable, point-in-time ranking snapshot.

    Distinct from the legacy mutable ``RankingRecord``: it exposes no update
    or delete, and every nested value is deep-frozen at construction so a
    caller cannot mutate ``items``/``evidence_assertion_digests`` after the
    fact and believe the durable snapshot changed with it.
    """

    ranking_snapshot_id: str
    surface: str
    period: str
    formula_version: str
    content_digest: str
    items: Any
    evidence_assertion_digests: Any
    created_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", _deep_freeze(self.items))
        object.__setattr__(
            self, "evidence_assertion_digests", _deep_freeze(self.evidence_assertion_digests)
        )

    def to_canonical_dict(self) -> Dict[str, Any]:
        return {
            "record_type": _SNAPSHOT_RECORD_TYPE,
            "ranking_snapshot_id": self.ranking_snapshot_id,
            "surface": self.surface,
            "period": self.period,
            "formula_version": self.formula_version,
            "content_digest": self.content_digest,
            "items": _thaw(self.items),
            "evidence_assertion_digests": _thaw(self.evidence_assertion_digests),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class AllocationEvaluationRecord:
    """An immutable allocation evaluation derived from one ranking snapshot.

    Exposes no update or delete: once created, an evaluation is either an
    idempotent byte-identical replay or a rejected conflict.
    """

    allocation_evaluation_id: str
    ranking_snapshot_id: str
    allocation_policy_version: str
    content_digest: str
    lines: Any
    created_at: str
    applied: bool
    authority_mode: Optional[str] = None
    promotion_review_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lines", _deep_freeze(self.lines))

    def to_canonical_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "record_type": _EVALUATION_RECORD_TYPE,
            "allocation_evaluation_id": self.allocation_evaluation_id,
            "ranking_snapshot_id": self.ranking_snapshot_id,
            "allocation_policy_version": self.allocation_policy_version,
            "content_digest": self.content_digest,
            "lines": _thaw(self.lines),
            "created_at": self.created_at,
            "applied": self.applied,
        }
        # Absent optional fields are omitted entirely rather than stored as
        # explicit nulls, so a durable row that never had them looks
        # byte-identical to one from before these fields existed.
        if self.authority_mode is not None:
            payload["authority_mode"] = self.authority_mode
        if self.promotion_review_id is not None:
            payload["promotion_review_id"] = self.promotion_review_id
        return payload


def _validate_snapshot(record: RankingSnapshotRecord) -> None:
    if not isinstance(record, RankingSnapshotRecord):
        raise RankingWriteOwnerError(
            "ranking snapshot create requires a RankingSnapshotRecord instance"
        )
    for attr in ("ranking_snapshot_id", "surface", "period", "formula_version", "content_digest", "created_at"):
        value = getattr(record, attr)
        if not isinstance(value, str) or not value.strip():
            raise RankingWriteOwnerError(f"{attr} is required")
    _validate_mapping_collection(record.items, path="items")
    _validate_evidence_assertion_digests(record.evidence_assertion_digests)


def _validate_evaluation(record: AllocationEvaluationRecord) -> None:
    if not isinstance(record, AllocationEvaluationRecord):
        raise RankingWriteOwnerError(
            "allocation evaluation create requires an AllocationEvaluationRecord instance"
        )
    for attr in (
        "allocation_evaluation_id",
        "ranking_snapshot_id",
        "allocation_policy_version",
        "content_digest",
        "created_at",
    ):
        value = getattr(record, attr)
        if not isinstance(value, str) or not value.strip():
            raise RankingWriteOwnerError(f"{attr} is required")
    if not isinstance(record.applied, bool):
        raise RankingWriteOwnerError("applied must be a bool")
    for optional_attr in ("authority_mode", "promotion_review_id"):
        value = getattr(record, optional_attr)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise RankingWriteOwnerError(f"{optional_attr} must be a non-empty string when present")
    _validate_mapping_collection(record.lines, path="lines")


def _decode_ranking_snapshot(payload: Mapping[str, Any]) -> RankingSnapshotRecord:
    keys = set(payload.keys())
    if keys != set(_SNAPSHOT_FIELDS):
        raise RankingWriteOwnerError(f"malformed ranking snapshot payload keys: {sorted(keys)}")
    if payload["record_type"] != _SNAPSHOT_RECORD_TYPE:
        raise RankingWriteOwnerError(f"unexpected record_type for ranking snapshot: {payload['record_type']!r}")
    record = RankingSnapshotRecord(
        ranking_snapshot_id=payload["ranking_snapshot_id"],
        surface=payload["surface"],
        period=payload["period"],
        formula_version=payload["formula_version"],
        content_digest=payload["content_digest"],
        items=payload["items"],
        evidence_assertion_digests=payload["evidence_assertion_digests"],
        created_at=payload["created_at"],
    )
    _validate_snapshot(record)
    return record


def _decode_allocation_evaluation(payload: Mapping[str, Any]) -> AllocationEvaluationRecord:
    keys = set(payload.keys())
    required = set(_EVALUATION_REQUIRED_FIELDS)
    optional = set(_EVALUATION_OPTIONAL_FIELDS)
    if not required.issubset(keys):
        raise RankingWriteOwnerError(
            f"malformed allocation evaluation payload, missing required keys: {sorted(required - keys)}"
        )
    extra = keys - required
    if not extra.issubset(optional):
        raise RankingWriteOwnerError(
            f"malformed allocation evaluation payload, unrecognized keys: {sorted(extra - optional)}"
        )
    if payload["record_type"] != _EVALUATION_RECORD_TYPE:
        raise RankingWriteOwnerError(
            f"unexpected record_type for allocation evaluation: {payload['record_type']!r}"
        )
    record = AllocationEvaluationRecord(
        allocation_evaluation_id=payload["allocation_evaluation_id"],
        ranking_snapshot_id=payload["ranking_snapshot_id"],
        allocation_policy_version=payload["allocation_policy_version"],
        content_digest=payload["content_digest"],
        lines=payload["lines"],
        created_at=payload["created_at"],
        applied=payload["applied"],
        authority_mode=payload.get("authority_mode"),
        promotion_review_id=payload.get("promotion_review_id"),
    )
    _validate_evaluation(record)
    return record


class RankingWriteStore:
    """The sole durable write owner for Rankings records.

    Backed by one concrete implementation, ``PostgresJsonOwnerStore``. Every
    method re-reads the backing table before answering, so this store never
    returns a stale in-memory snapshot and a write is immediately visible to
    any other store instance pointed at the same table.
    """

    def __init__(
        self,
        dsn: str,
        table: str = "rankings.rankings",
        bootstrap: bool = True,
    ) -> None:
        self._records_table = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="rankings-svc",
            bootstrap=bootstrap,
        )
        self._thread_lock = threading.RLock()

    def _refresh(self) -> Dict[str, RankingRecord]:
        records: Dict[str, RankingRecord] = {}
        for payload in self._records_table.list_all():
            # Generation 3 stores RankingSnapshotRecord/AllocationEvaluationRecord
            # rows in this same table under namespaced record ids, each tagged
            # with an explicit record_type envelope. Key presence of "record_type"
            # is inspected first, before reading its value or inspecting ranking_id:
            # legacy decode is allowed only when there is no record_type field in
            # the envelope at all.
            # An envelope where the record_type key exists (even with null or
            # unrecognized value) must fail integrity if present alongside ranking_id
            # (mixed envelope) or if its value is not a recognized non-legacy kind.
            # Only a recognized non-legacy kind with no ranking_id is cleanly
            # skipped by the legacy CRUD surface.
            has_record_type = "record_type" in payload
            has_ranking_id = "ranking_id" in payload
            if has_record_type:
                record_type = payload["record_type"]
                if has_ranking_id:
                    raise RankingWriteOwnerError(
                        "encountered a ranking row with a mixed envelope: record_type="
                        f"{record_type!r} masquerading as legacy with a ranking_id present"
                    )
                if record_type not in _RECOGNIZED_NON_LEGACY_RECORD_TYPES:
                    raise RankingWriteOwnerError(
                        f"encountered a ranking row with an unrecognized record_type: {record_type!r}"
                    )
                continue
            if has_ranking_id:
                record = RankingRecord.from_dict(payload)
                records[record.ranking_id] = record
                continue
            raise RankingWriteOwnerError(
                "encountered a ranking row that is neither a legacy RankingRecord "
                f"(missing ranking_id) nor a recognized record_type: {sorted(payload.keys())}"
            )
        return records

    # ---- reads ----

    def get_ranking(self, ranking_id: str) -> Optional[RankingRecord]:
        with self._thread_lock:
            record = self._refresh().get(ranking_id)
            return deepcopy(record) if record is not None else None

    def list_rankings(self) -> List[RankingRecord]:
        with self._thread_lock:
            return [deepcopy(record) for record in self._refresh().values()]

    # ---- writes ----

    def create_ranking(self, record: RankingRecord) -> RankingRecord:
        """Insert a brand-new ranking; atomic against concurrent creators."""

        with self._thread_lock:
            _validate(record)
            created, canonical = self._records_table.compare_and_set(
                record.ranking_id, None, record.to_dict()
            )
            if not created:
                raise RankingConflictError(f"ranking already exists: {record.ranking_id}")
            return RankingRecord.from_dict(canonical)

    def put_ranking(self, record: RankingRecord) -> RankingRecord:
        """Create-or-replace. The sole owner-service write entrypoint."""

        with self._thread_lock:
            _validate(record)
            record.updated_at = utc_now()
            self._records_table.put(record.ranking_id, record.to_dict())
            return deepcopy(record)

    def delete_ranking(self, ranking_id: str) -> bool:
        with self._thread_lock:
            current = self._refresh().get(ranking_id)
            if current is None:
                return False
            return self._records_table.delete_if_matches(ranking_id, current.to_dict())

    # ---- ranking snapshot (generation 3, immutable, no update/delete) ----

    def create_ranking_snapshot(self, record: RankingSnapshotRecord) -> RankingSnapshotRecord:
        """Atomic insert-if-absent or full-payload idempotent replay.

        Same ``ranking_snapshot_id`` plus a byte-identical canonical payload
        is an idempotent replay. Any field divergence -- including a payload
        that only differs in a field other than ``content_digest`` while
        ``content_digest`` still matches -- is a conflict, never a silent
        overwrite. Two competing creators racing on the same id resolve
        atomically at the backing table: only one insert can win.
        """

        with self._thread_lock:
            _validate_snapshot(record)
            record_id = _SNAPSHOT_ID_PREFIX + record.ranking_snapshot_id
            payload = record.to_canonical_dict()
            created, canonical = self._records_table.compare_and_set(record_id, None, payload)
            if not created:
                if canonical == payload:
                    return _decode_ranking_snapshot(canonical)
                raise RankingConflictError(
                    "ranking snapshot already exists with a divergent payload: "
                    f"{record.ranking_snapshot_id}"
                )
            return _decode_ranking_snapshot(canonical)

    def get_ranking_snapshot(self, ranking_snapshot_id: str) -> Optional[RankingSnapshotRecord]:
        with self._thread_lock:
            payload = self._records_table.get(_SNAPSHOT_ID_PREFIX + ranking_snapshot_id)
            return _decode_ranking_snapshot(payload) if payload is not None else None

    # ---- allocation evaluation (generation 3, immutable, no update/delete) ----

    def create_allocation_evaluation(
        self, record: AllocationEvaluationRecord
    ) -> AllocationEvaluationRecord:
        """Atomic insert-if-absent or full-payload idempotent replay.

        Same semantics as ``create_ranking_snapshot``: same id plus an
        identical canonical payload replays; any divergence -- even with a
        matching ``content_digest`` -- conflicts.
        """

        with self._thread_lock:
            _validate_evaluation(record)
            record_id = _EVALUATION_ID_PREFIX + record.allocation_evaluation_id
            payload = record.to_canonical_dict()
            created, canonical = self._records_table.compare_and_set(record_id, None, payload)
            if not created:
                if canonical == payload:
                    return _decode_allocation_evaluation(canonical)
                raise RankingConflictError(
                    "allocation evaluation already exists with a divergent payload: "
                    f"{record.allocation_evaluation_id}"
                )
            return _decode_allocation_evaluation(canonical)

    def get_allocation_evaluation(
        self, allocation_evaluation_id: str
    ) -> Optional[AllocationEvaluationRecord]:
        with self._thread_lock:
            payload = self._records_table.get(_EVALUATION_ID_PREFIX + allocation_evaluation_id)
            return _decode_allocation_evaluation(payload) if payload is not None else None


def build_rankings_store(
    dsn: Optional[str] = None,
    table: Optional[str] = None,
    bootstrap: Optional[bool] = None,
) -> RankingWriteStore:
    """Build the Rankings write-owner store.

    ``PostgresJsonOwnerStore`` is the only backend, matching the
    write-ownership pattern documented in
    ``DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md``. The DSN, table, and
    bootstrap flag may be passed explicitly or resolved from the environment.
    """

    resolved_dsn = dsn or os.getenv("RANKING_STORE_DSN") or os.getenv("DATABASE_URL")
    if not resolved_dsn:
        raise ValueError("RANKING_STORE_DSN or DATABASE_URL is required for the Rankings write-owner store")
    resolved_table = table or os.getenv("RANKING_STORE_TABLE", "rankings.rankings")
    if bootstrap is None:
        bootstrap = os.getenv("RANKING_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
    return RankingWriteStore(dsn=resolved_dsn, table=resolved_table, bootstrap=bootstrap)
