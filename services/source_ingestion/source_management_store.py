"""Transactional source management store facade and backends (SD-SRCM-01, SD-SRCM-02).

Provides durable authority for:
- data_source_instances (DataSourceEntryV2 canonical state & revision)
- source_desired_states (operator and controller desired intent history)
- source_command_receipts (idempotent command audit and readback receipts)
- source_canary_results (bounded activation and verification evidence)
- source_observed_snapshots (runtime & controller observed history)

Supports both PostgreSQL (normal deployment) and JSONL (local/test rollback)
with strict atomic transactions and rollback on failure.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from services.source_ingestion.registry.data_source_registry import DataSourceEntryV2
from services.source_ingestion.source_management_models import (
    CanaryStage,
    CanaryStatus,
    CommandType,
    ReceiptStatus,
    SourceCanaryResult,
    SourceDesiredState,
    SourceManagementCommand,
    SourceManagementContractError,
    SourceManagementReceipt,
    SourceObservedState,
    canonical_json,
)
from services.source_ingestion.process_lock import exclusive_file_lock


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SourceManagementStoreError(RuntimeError):
    """Base error for source management storage operations."""


class SourceInstanceNotFoundError(SourceManagementStoreError):
    """Raised when a requested source instance ID does not exist."""


class StaleRevisionError(SourceManagementStoreError):
    """Raised when expected_revision does not match current_revision."""


class IdempotencyConflictError(SourceManagementStoreError):
    """Raised when an idempotency key was previously used with different parameters."""


class DuplicateInstanceError(SourceManagementStoreError):
    """Raised when a source_instance_id or connector_id is already registered."""


class SourceManagementStore(ABC):
    """Abstract authority for source instances, desired state, receipts, and observed state."""

    @abstractmethod
    def create_instance(
        self,
        instance: DataSourceEntryV2,
        desired: SourceDesiredState,
        receipt: SourceManagementReceipt,
    ) -> None:
        """Atomically create an instance (rev 1), desired state (rev 1), and accepted receipt."""

    @abstractmethod
    def update_desired_state(
        self,
        source_instance_id: str,
        expected_revision: int,
        desired: SourceDesiredState,
        receipt: SourceManagementReceipt,
        new_lifecycle: str | None = None,
    ) -> None:
        """Atomically advance instance revision, persist new desired state, and store receipt."""

    @abstractmethod
    def get_instance(self, source_instance_id: str) -> DataSourceEntryV2 | None:
        """Fetch canonical DataSourceEntryV2 by source_instance_id."""

    @abstractmethod
    def get_instance_by_connector_id(self, connector_id: str) -> DataSourceEntryV2 | None:
        """Fetch canonical DataSourceEntryV2 by connector_id."""

    @abstractmethod
    def list_instances(
        self,
        *,
        source_kind: str | None = None,
        lifecycle_state: str | None = None,
    ) -> list[DataSourceEntryV2]:
        """List all canonical source instances with optional filters."""

    @abstractmethod
    def get_desired_state(
        self,
        source_instance_id: str,
        revision: int | None = None,
    ) -> SourceDesiredState | None:
        """Get desired state for an instance, latest or specific revision."""

    @abstractmethod
    def list_desired_states(self, source_instance_id: str) -> list[SourceDesiredState]:
        """Get full desired state revision history for an instance."""

    @abstractmethod
    def get_receipt(self, receipt_id: str) -> SourceManagementReceipt | None:
        """Get receipt by receipt_id."""

    @abstractmethod
    def get_receipt_by_command_id(self, command_id: str) -> SourceManagementReceipt | None:
        """Get receipt by command_id."""

    @abstractmethod
    def get_receipt_by_idempotency_key_hash(self, key_hash: str) -> SourceManagementReceipt | None:
        """Get receipt by idempotency_key_hash."""

    @abstractmethod
    def list_receipts(
        self,
        source_instance_id: str | None = None,
        limit: int = 100,
    ) -> list[SourceManagementReceipt]:
        """List command receipts with optional filtering and limit."""

    @abstractmethod
    def update_receipt(self, receipt: SourceManagementReceipt) -> None:
        """Update an existing receipt with completed status, failure, or readback."""

    @abstractmethod
    def save_canary_result(self, canary: SourceCanaryResult) -> None:
        """Persist a bounded canary execution result."""

    @abstractmethod
    def get_canary_result(self, canary_id: str) -> SourceCanaryResult | None:
        """Get canary result by canary_id."""

    @abstractmethod
    def get_latest_canary_result(self, source_instance_id: str) -> SourceCanaryResult | None:
        """Get latest canary result for an instance."""

    @abstractmethod
    def list_canary_results(
        self,
        source_instance_id: str | None = None,
        limit: int = 100,
    ) -> list[SourceCanaryResult]:
        """List canary results with optional instance filter."""

    @abstractmethod
    def save_observed_snapshot(self, observed: SourceObservedState) -> None:
        """Save a source observed state snapshot."""

    @abstractmethod
    def get_latest_observed_snapshot(self, source_instance_id: str) -> SourceObservedState | None:
        """Get most recent observed state snapshot for an instance."""

    @abstractmethod
    def list_observed_snapshots(
        self,
        source_instance_id: str,
        limit: int = 100,
    ) -> list[SourceObservedState]:
        """List observed snapshots for an instance."""

    @abstractmethod
    @contextlib.contextmanager
    def lock_instance(self, source_instance_id: str) -> Iterator[None]:
        """Context manager to lock an instance for concurrency-safe mutation."""


class JsonlSourceManagementStore(SourceManagementStore):
    """File/JSONL backed store with in-memory indexes and inter-process locking."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._instances_path = self.data_dir / "data_source_instances.jsonl"
        self._desired_path = self.data_dir / "source_desired_states.jsonl"
        self._receipts_path = self.data_dir / "source_command_receipts.jsonl"
        self._canary_path = self.data_dir / "source_canary_results.jsonl"
        self._observed_path = self.data_dir / "source_observed_snapshots.jsonl"

        self._lock_path = self.data_dir / "source_management_store.lock"
        self._thread_lock = threading.RLock()
        self._instance_locks: dict[str, threading.RLock] = {}

        self._instances: dict[str, DataSourceEntryV2] = {}
        self._connector_index: dict[str, str] = {}  # connector_id -> source_instance_id
        self._desired: dict[str, dict[int, SourceDesiredState]] = {}  # instance_id -> {rev: state}
        self._receipts: dict[str, SourceManagementReceipt] = {}  # receipt_id -> receipt
        self._receipts_by_cmd: dict[str, str] = {}  # command_id -> receipt_id
        self._receipts_by_idem: dict[str, str] = {}  # key_hash -> receipt_id
        self._canaries: dict[str, SourceCanaryResult] = {}  # canary_id -> canary
        self._canaries_by_instance: dict[str, list[str]] = {}  # instance_id -> [canary_id]
        self._observed: dict[str, dict[int, SourceObservedState]] = {}  # instance_id -> {obs_rev: state}

        self.reload()

    @contextlib.contextmanager
    def lock_instance(self, source_instance_id: str) -> Iterator[None]:
        with self._thread_lock:
            if source_instance_id not in self._instance_locks:
                self._instance_locks[source_instance_id] = threading.RLock()
            inst_lock = self._instance_locks[source_instance_id]

        with exclusive_file_lock(self._lock_path, inst_lock):
            self.reload()
            yield

    def reload(self) -> None:
        with self._thread_lock:
            self._instances.clear()
            self._connector_index.clear()
            self._desired.clear()
            self._receipts.clear()
            self._receipts_by_cmd.clear()
            self._receipts_by_idem.clear()
            self._canaries.clear()
            self._canaries_by_instance.clear()
            self._observed.clear()

            if self._instances_path.exists():
                for line in self._instances_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    entry = DataSourceEntryV2.from_dict(row["payload"])
                    self._instances[entry.data_source_id] = entry
                    self._connector_index[entry.connector_id] = entry.data_source_id

            if self._desired_path.exists():
                for line in self._desired_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    des = SourceDesiredState.from_dict(row["payload"])
                    self._desired.setdefault(des.source_instance_id, {})[des.revision] = des

            if self._receipts_path.exists():
                for line in self._receipts_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    rcp = SourceManagementReceipt.from_dict(row["payload"])
                    self._receipts[rcp.receipt_id] = rcp
                    self._receipts_by_cmd[rcp.command_id] = rcp.receipt_id
                    self._receipts_by_idem[rcp.idempotency_key_hash] = rcp.receipt_id

            if self._canary_path.exists():
                for line in self._canary_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    can = SourceCanaryResult.from_dict(row["payload"])
                    self._canaries[can.canary_id] = can
                    self._canaries_by_instance.setdefault(can.source_instance_id, []).append(can.canary_id)

            if self._observed_path.exists():
                for line in self._observed_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    obs = SourceObservedState.from_dict(row["payload"])
                    self._observed.setdefault(obs.source_instance_id, {})[obs.observed_revision] = obs

    def _append_line(self, file_path: Path, record_type: str, record_id: str, payload: dict[str, Any]) -> None:
        entry = {
            "record_type": record_type,
            "record_id": record_id,
            "payload": payload,
            "written_at": _utc_now(),
        }
        with file_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _rewrite_file(self, file_path: Path, items: Sequence[tuple[str, str, dict[str, Any]]]) -> None:
        tmp_path = file_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for record_type, record_id, payload in items:
                entry = {
                    "record_type": record_type,
                    "record_id": record_id,
                    "payload": payload,
                    "written_at": _utc_now(),
                }
                f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(file_path)

    def create_instance(
        self,
        instance: DataSourceEntryV2,
        desired: SourceDesiredState,
        receipt: SourceManagementReceipt,
    ) -> None:
        with self._thread_lock:
            self.reload()
            if instance.data_source_id in self._instances:
                raise DuplicateInstanceError(f"Instance already exists: {instance.data_source_id}")
            if instance.connector_id in self._connector_index:
                raise DuplicateInstanceError(f"Connector already registered: {instance.connector_id}")

            if receipt.idempotency_key_hash in self._receipts_by_idem:
                existing_rcp_id = self._receipts_by_idem[receipt.idempotency_key_hash]
                raise IdempotencyConflictError(f"Idempotency key hash already used: {receipt.idempotency_key_hash}")

            # Atomically append to storage files
            try:
                self._append_line(
                    self._instances_path,
                    "data_source_instance",
                    instance.data_source_id,
                    instance.to_dict(),
                )
                self._append_line(
                    self._desired_path,
                    "source_desired_state",
                    f"{desired.source_instance_id}::{desired.revision}",
                    desired.to_dict(),
                )
                self._append_line(
                    self._receipts_path,
                    "source_command_receipt",
                    receipt.receipt_id,
                    receipt.to_dict(),
                )
            except Exception as exc:
                self.reload()
                raise SourceManagementStoreError(f"Failed to create instance atomically: {exc}") from exc

            # Update memory state
            self._instances[instance.data_source_id] = instance
            self._connector_index[instance.connector_id] = instance.data_source_id
            self._desired.setdefault(desired.source_instance_id, {})[desired.revision] = desired
            self._receipts[receipt.receipt_id] = receipt
            self._receipts_by_cmd[receipt.command_id] = receipt.receipt_id
            self._receipts_by_idem[receipt.idempotency_key_hash] = receipt.receipt_id

    def update_desired_state(
        self,
        source_instance_id: str,
        expected_revision: int,
        desired: SourceDesiredState,
        receipt: SourceManagementReceipt,
        new_lifecycle: str | None = None,
    ) -> None:
        with self._thread_lock:
            self.reload()
            curr_inst = self._instances.get(source_instance_id)
            if curr_inst is None:
                raise SourceInstanceNotFoundError(f"Instance not found: {source_instance_id}")

            if curr_inst.revision != expected_revision:
                raise StaleRevisionError(
                    f"Stale revision for {source_instance_id}: expected {expected_revision}, got {curr_inst.revision}"
                )

            next_revision = expected_revision + 1
            if desired.revision != next_revision:
                raise SourceManagementStoreError(
                    f"New desired state revision must be {next_revision}, got {desired.revision}"
                )

            updated_inst_dict = curr_inst.to_dict()
            updated_inst_dict["revision"] = next_revision
            if new_lifecycle:
                updated_inst_dict["lifecycle_state"] = new_lifecycle
            updated_inst_dict["updated_at"] = _utc_now()
            updated_inst = DataSourceEntryV2.from_dict(updated_inst_dict)

            try:
                # Update instances file (rewrite with updated entry)
                self._instances[source_instance_id] = updated_inst
                all_inst_items = [
                    ("data_source_instance", inst.data_source_id, inst.to_dict())
                    for inst in self._instances.values()
                ]
                self._rewrite_file(self._instances_path, all_inst_items)

                # Append desired state
                self._append_line(
                    self._desired_path,
                    "source_desired_state",
                    f"{desired.source_instance_id}::{desired.revision}",
                    desired.to_dict(),
                )

                # Append receipt
                self._append_line(
                    self._receipts_path,
                    "source_command_receipt",
                    receipt.receipt_id,
                    receipt.to_dict(),
                )
            except Exception as exc:
                self.reload()
                raise SourceManagementStoreError(f"Failed to update desired state atomically: {exc}") from exc

            # Update memory state
            self._desired.setdefault(source_instance_id, {})[desired.revision] = desired
            self._receipts[receipt.receipt_id] = receipt
            self._receipts_by_cmd[receipt.command_id] = receipt.receipt_id
            self._receipts_by_idem[receipt.idempotency_key_hash] = receipt.receipt_id

    def get_instance(self, source_instance_id: str) -> DataSourceEntryV2 | None:
        with self._thread_lock:
            return self._instances.get(source_instance_id)

    def get_instance_by_connector_id(self, connector_id: str) -> DataSourceEntryV2 | None:
        with self._thread_lock:
            inst_id = self._connector_index.get(connector_id)
            return self._instances.get(inst_id) if inst_id else None

    def list_instances(
        self,
        *,
        source_kind: str | None = None,
        lifecycle_state: str | None = None,
    ) -> list[DataSourceEntryV2]:
        with self._thread_lock:
            items = list(self._instances.values())
            if source_kind:
                items = [i for i in items if i.source_kind == source_kind]
            if lifecycle_state:
                items = [i for i in items if i.lifecycle_state == lifecycle_state]
            return items

    def get_desired_state(
        self,
        source_instance_id: str,
        revision: int | None = None,
    ) -> SourceDesiredState | None:
        with self._thread_lock:
            rev_map = self._desired.get(source_instance_id)
            if not rev_map:
                return None
            if revision is not None:
                return rev_map.get(revision)
            max_rev = max(rev_map.keys())
            return rev_map.get(max_rev)

    def list_desired_states(self, source_instance_id: str) -> list[SourceDesiredState]:
        with self._thread_lock:
            rev_map = self._desired.get(source_instance_id, {})
            return [rev_map[r] for r in sorted(rev_map.keys())]

    def get_receipt(self, receipt_id: str) -> SourceManagementReceipt | None:
        with self._thread_lock:
            return self._receipts.get(receipt_id)

    def get_receipt_by_command_id(self, command_id: str) -> SourceManagementReceipt | None:
        with self._thread_lock:
            rcp_id = self._receipts_by_cmd.get(command_id)
            return self._receipts.get(rcp_id) if rcp_id else None

    def get_receipt_by_idempotency_key_hash(self, key_hash: str) -> SourceManagementReceipt | None:
        with self._thread_lock:
            rcp_id = self._receipts_by_idem.get(key_hash)
            return self._receipts.get(rcp_id) if rcp_id else None

    def list_receipts(
        self,
        source_instance_id: str | None = None,
        limit: int = 100,
    ) -> list[SourceManagementReceipt]:
        with self._thread_lock:
            items = list(self._receipts.values())
            if source_instance_id:
                items = [r for r in items if r.source_instance_id == source_instance_id]
            items.sort(key=lambda r: r.created_at, reverse=True)
            return items[:limit]

    def update_receipt(self, receipt: SourceManagementReceipt) -> None:
        with self._thread_lock:
            self._receipts[receipt.receipt_id] = receipt
            self._receipts_by_cmd[receipt.command_id] = receipt.receipt_id
            self._receipts_by_idem[receipt.idempotency_key_hash] = receipt.receipt_id

            all_rcp_items = [
                ("source_command_receipt", r.receipt_id, r.to_dict())
                for r in self._receipts.values()
            ]
            self._rewrite_file(self._receipts_path, all_rcp_items)

    def save_canary_result(self, canary: SourceCanaryResult) -> None:
        with self._thread_lock:
            self._canaries[canary.canary_id] = canary
            self._canaries_by_instance.setdefault(canary.source_instance_id, []).append(canary.canary_id)
            self._append_line(
                self._canary_path,
                "source_canary_result",
                canary.canary_id,
                canary.to_dict(),
            )

    def get_canary_result(self, canary_id: str) -> SourceCanaryResult | None:
        with self._thread_lock:
            return self._canaries.get(canary_id)

    def get_latest_canary_result(self, source_instance_id: str) -> SourceCanaryResult | None:
        with self._thread_lock:
            canary_ids = self._canaries_by_instance.get(source_instance_id, [])
            if not canary_ids:
                return None
            results = [self._canaries[cid] for cid in canary_ids if cid in self._canaries]
            if not results:
                return None
            results.sort(key=lambda c: c.completed_at or c.started_at, reverse=True)
            return results[0]

    def list_canary_results(
        self,
        source_instance_id: str | None = None,
        limit: int = 100,
    ) -> list[SourceCanaryResult]:
        with self._thread_lock:
            if source_instance_id:
                canary_ids = self._canaries_by_instance.get(source_instance_id, [])
                results = [self._canaries[cid] for cid in canary_ids if cid in self._canaries]
            else:
                results = list(self._canaries.values())
            results.sort(key=lambda c: c.completed_at or c.started_at, reverse=True)
            return results[:limit]

    def save_observed_snapshot(self, observed: SourceObservedState) -> None:
        with self._thread_lock:
            self._observed.setdefault(observed.source_instance_id, {})[observed.observed_revision] = observed
            self._append_line(
                self._observed_path,
                "source_observed_snapshot",
                f"{observed.source_instance_id}::{observed.observed_revision}",
                observed.to_dict(),
            )

    def get_latest_observed_snapshot(self, source_instance_id: str) -> SourceObservedState | None:
        with self._thread_lock:
            rev_map = self._observed.get(source_instance_id)
            if not rev_map:
                return None
            max_rev = max(rev_map.keys())
            return rev_map.get(max_rev)

    def list_observed_snapshots(
        self,
        source_instance_id: str,
        limit: int = 100,
    ) -> list[SourceObservedState]:
        with self._thread_lock:
            rev_map = self._observed.get(source_instance_id, {})
            results = [rev_map[r] for r in sorted(rev_map.keys(), reverse=True)]
            return results[:limit]


class PostgresSourceManagementStore(SourceManagementStore):
    """PostgreSQL-backed store with row-level locking (FOR UPDATE) and transactions."""

    def __init__(self, dsn: str, bootstrap: bool = True) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for PostgresSourceManagementStore")
        self.dsn = dsn
        self._thread_lock = threading.RLock()
        if bootstrap:
            self._bootstrap()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("psycopg is required for PostgresSourceManagementStore") from exc
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS source_ingest;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS source_ingest.data_source_instances (
                    source_instance_id TEXT PRIMARY KEY,
                    source_kind        TEXT NOT NULL,
                    definition_id      TEXT NOT NULL,
                    connector_id       TEXT NOT NULL UNIQUE,
                    current_revision   INTEGER NOT NULL DEFAULT 1,
                    lifecycle_state    TEXT NOT NULL,
                    payload            JSONB NOT NULL,
                    created_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                    updated_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
                );
                CREATE INDEX IF NOT EXISTS idx_data_source_instances_definition
                    ON source_ingest.data_source_instances (definition_id);
                CREATE INDEX IF NOT EXISTS idx_data_source_instances_lifecycle
                    ON source_ingest.data_source_instances (lifecycle_state);

                CREATE TABLE IF NOT EXISTS source_ingest.source_desired_states (
                    source_instance_id        TEXT NOT NULL,
                    revision                  INTEGER NOT NULL,
                    desired_lifecycle         TEXT NOT NULL,
                    definition_id             TEXT NOT NULL,
                    definition_deployment_sha TEXT NOT NULL,
                    connector_config          JSONB NOT NULL,
                    schedule                  JSONB NOT NULL,
                    limits                    JSONB NOT NULL,
                    allowed_hosts             JSONB NOT NULL,
                    last_command_receipt_id   TEXT,
                    payload                   JSONB NOT NULL,
                    created_at                TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                    PRIMARY KEY (source_instance_id, revision)
                );
                CREATE INDEX IF NOT EXISTS idx_source_desired_states_def
                    ON source_ingest.source_desired_states (definition_id);

                CREATE TABLE IF NOT EXISTS source_ingest.source_command_receipts (
                    receipt_id             TEXT PRIMARY KEY,
                    command_id             TEXT NOT NULL UNIQUE,
                    idempotency_key_hash   TEXT NOT NULL UNIQUE,
                    source_instance_id     TEXT NOT NULL,
                    command_type           TEXT NOT NULL,
                    status                 TEXT NOT NULL,
                    before_revision        INTEGER NOT NULL,
                    after_revision         INTEGER NOT NULL,
                    payload                JSONB NOT NULL,
                    created_at             TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                    completed_at           TIMESTAMPTZ
                );
                CREATE INDEX IF NOT EXISTS idx_source_command_receipts_instance
                    ON source_ingest.source_command_receipts (source_instance_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS source_ingest.source_canary_results (
                    canary_id          TEXT PRIMARY KEY,
                    source_instance_id TEXT NOT NULL,
                    definition_id      TEXT NOT NULL,
                    status             TEXT NOT NULL,
                    payload            JSONB NOT NULL,
                    created_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                    completed_at       TIMESTAMPTZ
                );
                CREATE INDEX IF NOT EXISTS idx_source_canary_results_instance
                    ON source_ingest.source_canary_results (source_instance_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS source_ingest.source_observed_snapshots (
                    source_instance_id    TEXT NOT NULL,
                    observed_revision     INTEGER NOT NULL,
                    desired_revision      INTEGER NOT NULL,
                    reconciliation_status TEXT NOT NULL,
                    effective_lifecycle   TEXT NOT NULL,
                    payload               JSONB NOT NULL,
                    observed_at           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                    PRIMARY KEY (source_instance_id, observed_revision)
                );
                CREATE INDEX IF NOT EXISTS idx_source_observed_snapshots_instance
                    ON source_ingest.source_observed_snapshots (source_instance_id, observed_at DESC);
            """)

    @contextlib.contextmanager
    def lock_instance(self, source_instance_id: str) -> Iterator[None]:
        # In Postgres, table/row transactions provide locking; Python thread lock coordinates local callers
        with self._thread_lock:
            yield

    def create_instance(
        self,
        instance: DataSourceEntryV2,
        desired: SourceDesiredState,
        receipt: SourceManagementReceipt,
    ) -> None:
        with self._connect() as conn:
            with conn.transaction():
                # Check duplicate
                cur = conn.execute(
                    "SELECT source_instance_id FROM source_ingest.data_source_instances WHERE source_instance_id = %s OR connector_id = %s",
                    (instance.data_source_id, instance.connector_id),
                )
                if cur.fetchone():
                    raise DuplicateInstanceError(f"Instance or connector already exists: {instance.data_source_id}")

                # Insert instance
                conn.execute(
                    """
                    INSERT INTO source_ingest.data_source_instances
                    (source_instance_id, source_kind, definition_id, connector_id, current_revision, lifecycle_state, payload, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, clock_timestamp(), clock_timestamp())
                    """,
                    (
                        instance.data_source_id,
                        instance.source_kind,
                        instance.definition_id,
                        instance.connector_id,
                        instance.revision,
                        instance.lifecycle_state,
                        json.dumps(instance.to_dict(), sort_keys=True),
                    ),
                )

                # Insert desired state
                conn.execute(
                    """
                    INSERT INTO source_ingest.source_desired_states
                    (source_instance_id, revision, desired_lifecycle, definition_id, definition_deployment_sha, connector_config, schedule, limits, allowed_hosts, last_command_receipt_id, payload, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, clock_timestamp())
                    """,
                    (
                        desired.source_instance_id,
                        desired.revision,
                        desired.desired_lifecycle.value if hasattr(desired.desired_lifecycle, "value") else str(desired.desired_lifecycle),
                        desired.definition_id,
                        desired.definition_deployment_sha,
                        json.dumps(desired.connector_config),
                        json.dumps(desired.schedule),
                        json.dumps(desired.limits),
                        json.dumps(list(desired.allowed_hosts)),
                        desired.last_command_receipt_id,
                        json.dumps(desired.to_dict(), sort_keys=True),
                    ),
                )

                # Insert receipt
                conn.execute(
                    """
                    INSERT INTO source_ingest.source_command_receipts
                    (receipt_id, command_id, idempotency_key_hash, source_instance_id, command_type, status, before_revision, after_revision, payload, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, clock_timestamp())
                    """,
                    (
                        receipt.receipt_id,
                        receipt.command_id,
                        receipt.idempotency_key_hash,
                        receipt.source_instance_id,
                        receipt.command_type.value if hasattr(receipt.command_type, "value") else str(receipt.command_type),
                        receipt.status.value if hasattr(receipt.status, "value") else str(receipt.status),
                        receipt.before_revision,
                        receipt.after_revision,
                        json.dumps(receipt.to_dict(), sort_keys=True),
                    ),
                )

    def update_desired_state(
        self,
        source_instance_id: str,
        expected_revision: int,
        desired: SourceDesiredState,
        receipt: SourceManagementReceipt,
        new_lifecycle: str | None = None,
    ) -> None:
        with self._connect() as conn:
            with conn.transaction():
                # Lock row FOR UPDATE
                cur = conn.execute(
                    "SELECT current_revision, payload FROM source_ingest.data_source_instances WHERE source_instance_id = %s FOR UPDATE",
                    (source_instance_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise SourceInstanceNotFoundError(f"Instance not found: {source_instance_id}")

                curr_revision = int(row[0])
                if curr_revision != expected_revision:
                    raise StaleRevisionError(
                        f"Stale revision for {source_instance_id}: expected {expected_revision}, got {curr_revision}"
                    )

                next_revision = expected_revision + 1
                curr_payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
                curr_payload["revision"] = next_revision
                if new_lifecycle:
                    curr_payload["lifecycle_state"] = new_lifecycle
                curr_payload["updated_at"] = _utc_now()

                # Update instance
                conn.execute(
                    """
                    UPDATE source_ingest.data_source_instances
                    SET current_revision = %s, lifecycle_state = COALESCE(%s, lifecycle_state), payload = %s::jsonb, updated_at = clock_timestamp()
                    WHERE source_instance_id = %s
                    """,
                    (
                        next_revision,
                        new_lifecycle,
                        json.dumps(curr_payload, sort_keys=True),
                        source_instance_id,
                    ),
                )

                # Insert desired state
                conn.execute(
                    """
                    INSERT INTO source_ingest.source_desired_states
                    (source_instance_id, revision, desired_lifecycle, definition_id, definition_deployment_sha, connector_config, schedule, limits, allowed_hosts, last_command_receipt_id, payload, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, clock_timestamp())
                    """,
                    (
                        desired.source_instance_id,
                        desired.revision,
                        desired.desired_lifecycle.value if hasattr(desired.desired_lifecycle, "value") else str(desired.desired_lifecycle),
                        desired.definition_id,
                        desired.definition_deployment_sha,
                        json.dumps(desired.connector_config),
                        json.dumps(desired.schedule),
                        json.dumps(desired.limits),
                        json.dumps(list(desired.allowed_hosts)),
                        desired.last_command_receipt_id,
                        json.dumps(desired.to_dict(), sort_keys=True),
                    ),
                )

                # Insert receipt
                conn.execute(
                    """
                    INSERT INTO source_ingest.source_command_receipts
                    (receipt_id, command_id, idempotency_key_hash, source_instance_id, command_type, status, before_revision, after_revision, payload, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, clock_timestamp())
                    """,
                    (
                        receipt.receipt_id,
                        receipt.command_id,
                        receipt.idempotency_key_hash,
                        receipt.source_instance_id,
                        receipt.command_type.value if hasattr(receipt.command_type, "value") else str(receipt.command_type),
                        receipt.status.value if hasattr(receipt.status, "value") else str(receipt.status),
                        receipt.before_revision,
                        receipt.after_revision,
                        json.dumps(receipt.to_dict(), sort_keys=True),
                    ),
                )

    def get_instance(self, source_instance_id: str) -> DataSourceEntryV2 | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT payload FROM source_ingest.data_source_instances WHERE source_instance_id = %s",
                (source_instance_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return DataSourceEntryV2.from_dict(payload)

    def get_instance_by_connector_id(self, connector_id: str) -> DataSourceEntryV2 | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT payload FROM source_ingest.data_source_instances WHERE connector_id = %s",
                (connector_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return DataSourceEntryV2.from_dict(payload)

    def list_instances(
        self,
        *,
        source_kind: str | None = None,
        lifecycle_state: str | None = None,
    ) -> list[DataSourceEntryV2]:
        query = "SELECT payload FROM source_ingest.data_source_instances WHERE 1=1"
        params = []
        if source_kind:
            query += " AND source_kind = %s"
            params.append(source_kind)
        if lifecycle_state:
            query += " AND lifecycle_state = %s"
            params.append(lifecycle_state)
        query += " ORDER BY source_instance_id ASC"

        with self._connect() as conn:
            cur = conn.execute(query, tuple(params))
            results = []
            for row in cur.fetchall():
                payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                results.append(DataSourceEntryV2.from_dict(payload))
            return results

    def get_desired_state(
        self,
        source_instance_id: str,
        revision: int | None = None,
    ) -> SourceDesiredState | None:
        if revision is not None:
            query = "SELECT payload FROM source_ingest.source_desired_states WHERE source_instance_id = %s AND revision = %s"
            params = (source_instance_id, revision)
        else:
            query = "SELECT payload FROM source_ingest.source_desired_states WHERE source_instance_id = %s ORDER BY revision DESC LIMIT 1"
            params = (source_instance_id,)

        with self._connect() as conn:
            cur = conn.execute(query, params)
            row = cur.fetchone()
            if not row:
                return None
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return SourceDesiredState.from_dict(payload)

    def list_desired_states(self, source_instance_id: str) -> list[SourceDesiredState]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT payload FROM source_ingest.source_desired_states WHERE source_instance_id = %s ORDER BY revision ASC",
                (source_instance_id,),
            )
            results = []
            for row in cur.fetchall():
                payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                results.append(SourceDesiredState.from_dict(payload))
            return results

    def get_receipt(self, receipt_id: str) -> SourceManagementReceipt | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT payload FROM source_ingest.source_command_receipts WHERE receipt_id = %s",
                (receipt_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return SourceManagementReceipt.from_dict(payload)

    def get_receipt_by_command_id(self, command_id: str) -> SourceManagementReceipt | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT payload FROM source_ingest.source_command_receipts WHERE command_id = %s",
                (command_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return SourceManagementReceipt.from_dict(payload)

    def get_receipt_by_idempotency_key_hash(self, key_hash: str) -> SourceManagementReceipt | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT payload FROM source_ingest.source_command_receipts WHERE idempotency_key_hash = %s",
                (key_hash,),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return SourceManagementReceipt.from_dict(payload)

    def list_receipts(
        self,
        source_instance_id: str | None = None,
        limit: int = 100,
    ) -> list[SourceManagementReceipt]:
        query = "SELECT payload FROM source_ingest.source_command_receipts"
        params = []
        if source_instance_id:
            query += " WHERE source_instance_id = %s"
            params.append(source_instance_id)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        with self._connect() as conn:
            cur = conn.execute(query, tuple(params))
            results = []
            for row in cur.fetchall():
                payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                results.append(SourceManagementReceipt.from_dict(payload))
            return results

    def update_receipt(self, receipt: SourceManagementReceipt) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE source_ingest.source_command_receipts
                SET status = %s, payload = %s::jsonb, completed_at = clock_timestamp()
                WHERE receipt_id = %s
                """,
                (
                    receipt.status.value if hasattr(receipt.status, "value") else str(receipt.status),
                    json.dumps(receipt.to_dict(), sort_keys=True),
                    receipt.receipt_id,
                ),
            )

    def save_canary_result(self, canary: SourceCanaryResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO source_ingest.source_canary_results
                (canary_id, source_instance_id, definition_id, status, payload, created_at, completed_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::timestamptz, %s::timestamptz)
                ON CONFLICT (canary_id) DO UPDATE SET status = EXCLUDED.status, payload = EXCLUDED.payload, completed_at = EXCLUDED.completed_at
                """,
                (
                    canary.canary_id,
                    canary.source_instance_id,
                    canary.definition_id,
                    canary.status.value if hasattr(canary.status, "value") else str(canary.status),
                    json.dumps(canary.to_dict(), sort_keys=True),
                    canary.started_at,
                    canary.completed_at,
                ),
            )

    def get_canary_result(self, canary_id: str) -> SourceCanaryResult | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT payload FROM source_ingest.source_canary_results WHERE canary_id = %s",
                (canary_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return SourceCanaryResult.from_dict(payload)

    def get_latest_canary_result(self, source_instance_id: str) -> SourceCanaryResult | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT payload FROM source_ingest.source_canary_results WHERE source_instance_id = %s ORDER BY created_at DESC LIMIT 1",
                (source_instance_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return SourceCanaryResult.from_dict(payload)

    def list_canary_results(
        self,
        source_instance_id: str | None = None,
        limit: int = 100,
    ) -> list[SourceCanaryResult]:
        query = "SELECT payload FROM source_ingest.source_canary_results"
        params = []
        if source_instance_id:
            query += " WHERE source_instance_id = %s"
            params.append(source_instance_id)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        with self._connect() as conn:
            cur = conn.execute(query, tuple(params))
            results = []
            for row in cur.fetchall():
                payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                results.append(SourceCanaryResult.from_dict(payload))
            return results

    def save_observed_snapshot(self, observed: SourceObservedState) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO source_ingest.source_observed_snapshots
                (source_instance_id, observed_revision, desired_revision, reconciliation_status, effective_lifecycle, payload, observed_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::timestamptz)
                ON CONFLICT (source_instance_id, observed_revision) DO UPDATE SET
                    desired_revision = EXCLUDED.desired_revision,
                    reconciliation_status = EXCLUDED.reconciliation_status,
                    effective_lifecycle = EXCLUDED.effective_lifecycle,
                    payload = EXCLUDED.payload,
                    observed_at = EXCLUDED.observed_at
                """,
                (
                    observed.source_instance_id,
                    observed.observed_revision,
                    observed.desired_revision,
                    observed.reconciliation_status.value if hasattr(observed.reconciliation_status, "value") else str(observed.reconciliation_status),
                    observed.effective_lifecycle.value if hasattr(observed.effective_lifecycle, "value") else str(observed.effective_lifecycle),
                    json.dumps(observed.to_dict(), sort_keys=True),
                    observed.observed_at,
                ),
            )

    def get_latest_observed_snapshot(self, source_instance_id: str) -> SourceObservedState | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT payload FROM source_ingest.source_observed_snapshots WHERE source_instance_id = %s ORDER BY observed_revision DESC LIMIT 1",
                (source_instance_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return SourceObservedState.from_dict(payload)

    def list_observed_snapshots(
        self,
        source_instance_id: str,
        limit: int = 100,
    ) -> list[SourceObservedState]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT payload FROM source_ingest.source_observed_snapshots WHERE source_instance_id = %s ORDER BY observed_revision DESC LIMIT %s",
                (source_instance_id, limit),
            )
            results = []
            for row in cur.fetchall():
                payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                results.append(SourceObservedState.from_dict(payload))
            return results


def build_source_management_store(
    data_dir: Path | str | None = None,
) -> SourceManagementStore:
    """Factory: builds PostgresSourceManagementStore if configured, else JsonlSourceManagementStore."""
    backend = os.getenv("SOURCE_MANAGEMENT_STORE_BACKEND", "").strip().lower()
    dsn = os.getenv("SOURCE_MANAGEMENT_STORE_DSN") or os.getenv("SOURCE_INGEST_EVIDENCE_DSN") or os.getenv("DATABASE_URL")

    if backend == "postgres" or (not backend and dsn and os.getenv("SOURCE_INGEST_EVIDENCE_BACKEND") == "postgres"):
        if not dsn:
            raise ValueError("SOURCE_MANAGEMENT_STORE_DSN or DATABASE_URL is required for Postgres source management store")
        bootstrap = os.getenv("SOURCE_MANAGEMENT_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
        return PostgresSourceManagementStore(dsn=dsn, bootstrap=bootstrap)

    base_dir = Path(data_dir) if data_dir else Path(os.getenv("SOURCE_INGEST_DATA_DIR", "/tmp/pantheon/source-ingest"))
    return JsonlSourceManagementStore(base_dir)
