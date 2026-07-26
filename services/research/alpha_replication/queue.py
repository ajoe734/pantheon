"""Tenant-scoped leased queue for approved StrategySpec replication.

The queue accepts only immutable registry entries that carry an explicit
approval decision.  Its canonical key is ``(tenant_id, strategy_spec_id)``;
strategy family/version fields are lineage attributes, never alternate keys.

Claims use expiring leases and fencing tokens.  A worker must present the
current token before it can acknowledge success or failure, so a stale worker
cannot overwrite a reclaimed attempt.  DLQ replay is idempotent by replay id.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


REVIEWABLE_STATES = frozenset({"approved"})
_IMMUTABLE_BINDING_FIELDS = (
    "strategy_id",
    "spec_version",
    "checksum",
    "approval_decision_id",
    "approver",
    "approved_at",
)


def _utc_now(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _key(entry: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(entry.get("tenant_id") or ""),
        str(entry.get("strategy_spec_id") or ""),
    )


class AlphaReplicationQueue:
    """Process-safe durable queue for approved immutable StrategySpecs."""

    def __init__(self, data_dir: str | Path) -> None:
        self._path = Path(data_dir) / "alpha_replication_queue.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._path.with_suffix(".lock")
        self._lock = threading.Lock()

    @contextmanager
    def _lock_context(self):
        with self._lock:
            with self._lock_path.open("w", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def enqueue(
        self,
        spec_payload: Mapping[str, Any],
        *,
        enqueued_by: str = "system",
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Enqueue one approved, reviewed, immutable StrategySpec.

        Duplicate delivery of the same immutable approval returns ``None``.
        Reusing a tenant/spec id with different immutable content fails closed.
        """

        tenant_id = _require_text(spec_payload.get("tenant_id"), "tenant_id")
        strategy_spec_id = _require_text(
            spec_payload.get("strategy_spec_id"),
            "strategy_spec_id",
        )
        strategy_id = _require_text(spec_payload.get("strategy_id"), "strategy_id")
        spec_version = _require_text(
            spec_payload.get("spec_version") or spec_payload.get("strategy_spec_version"),
            "spec_version",
        )
        lifecycle_state = str(
            spec_payload.get("artifact_state")
            or spec_payload.get("lifecycle_state")
            or ""
        ).strip().lower()
        if lifecycle_state != "approved":
            raise ValueError(
                "Only approved reviewed StrategySpec entries enter the replication queue; "
                f"got artifact_state={lifecycle_state!r}"
            )
        immutable = {
            "strategy_id": strategy_id,
            "spec_version": spec_version,
            "checksum": _require_text(spec_payload.get("checksum"), "checksum"),
            "approval_decision_id": _require_text(
                spec_payload.get("approval_decision_id"),
                "approval_decision_id",
            ),
            "approver": _require_text(spec_payload.get("approver"), "approver"),
            "approved_at": _require_text(spec_payload.get("approved_at"), "approved_at"),
        }
        timestamp = _utc_now(now)

        with self._lock_context():
            entries = self._read()
            for entry in entries:
                if _key(entry) != (tenant_id, strategy_spec_id):
                    continue
                changed = [
                    field_name
                    for field_name in _IMMUTABLE_BINDING_FIELDS
                    if entry.get(field_name) != immutable[field_name]
                ]
                if changed:
                    raise ValueError(
                        "StrategySpec immutable binding conflict for "
                        f"tenant_id={tenant_id!r}, strategy_spec_id={strategy_spec_id!r}: "
                        + ", ".join(changed)
                    )
                return None

            new_entry: dict[str, Any] = {
                "tenant_id": tenant_id,
                "strategy_spec_id": strategy_spec_id,
                **immutable,
                "lifecycle_state": "approved",
                "enqueued_at": timestamp,
                "enqueued_by": _require_text(enqueued_by, "enqueued_by"),
                "status": "pending",
                "attempt_count": 0,
                "revalidation_count": 0,
                "claim_generation": 0,
                "reclaimed_count": 0,
                "replay_count": 0,
                "consumed_replay_ids": [],
                "last_revalidation_at": None,
                "last_revalidation_status": None,
                "authority_task_id": None,
                "authority_run_ids": [],
                "experiment_task_id": None,
                "experiment_run_ids": [],
            }
            entries.append(new_entry)
            self._rewrite_durable(entries)
            return dict(new_entry)

    def claim_next_pending(
        self,
        tenant_id: str,
        *,
        claimant: str = "system",
        lease_seconds: int = 300,
        ignore_keys: set[tuple[str, str]] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Claim pending work, first reclaiming expired leases for the tenant."""

        tenant = _require_text(tenant_id, "tenant_id")
        worker = _require_text(claimant, "claimant")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        claimed_at = parse_utc(_utc_now(now))
        assert claimed_at is not None
        with self._lock_context():
            entries = self._read()
            changed = self._recover_expired(entries, tenant_id=tenant, now=claimed_at)
            for entry in entries:
                entry_key = _key(entry)
                if ignore_keys and entry_key in ignore_keys:
                    continue
                if entry_key[0] != tenant or entry.get("status") != "pending":
                    continue
                generation = int(entry.get("claim_generation") or 0) + 1
                entry["status"] = "claimed"
                entry["claimed_by"] = worker
                entry["claimed_at"] = _utc_now(claimed_at)
                entry["lease_expires_at"] = _utc_now(
                    claimed_at + timedelta(seconds=lease_seconds)
                )
                entry["claim_generation"] = generation
                entry["claim_token"] = uuid.uuid4().hex
                self._rewrite_durable(entries)
                return dict(entry)
            if changed:
                self._rewrite_durable(entries)
            return None

    def renew_claim(
        self,
        tenant_id: str,
        strategy_spec_id: str,
        *,
        claim_token: str,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> bool:
        """Extend a live claim without changing its fencing token."""

        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        current = parse_utc(_utc_now(now))
        assert current is not None
        with self._lock_context():
            entries = self._read()
            entry = self._find(entries, tenant_id, strategy_spec_id)
            if not self._owns_live_claim(entry, claim_token, current):
                return False
            entry["lease_expires_at"] = _utc_now(current + timedelta(seconds=lease_seconds))
            self._rewrite_durable(entries)
            return True

    def recover_expired_claims(
        self,
        tenant_id: str,
        *,
        now: datetime | None = None,
    ) -> int:
        """Return expired tenant claims to pending for crash recovery."""

        tenant = _require_text(tenant_id, "tenant_id")
        current = parse_utc(_utc_now(now))
        assert current is not None
        with self._lock_context():
            entries = self._read()
            recovered = self._recover_expired(entries, tenant_id=tenant, now=current)
            if recovered:
                self._rewrite_durable(entries)
            return recovered

    def mark_revalidated(
        self,
        tenant_id: str,
        strategy_spec_id: str,
        *,
        claim_token: str,
        authority_task_id: str,
        authority_run_id: str,
        experiment_task_id: str,
        experiment_run_id: str,
        status: str = "completed",
        now: datetime | None = None,
    ) -> bool:
        """Acknowledge authoritative success using the current claim token."""

        current = parse_utc(_utc_now(now))
        assert current is not None
        with self._lock_context():
            entries = self._read()
            entry = self._find(entries, tenant_id, strategy_spec_id)
            if not self._owns_live_claim(entry, claim_token, current):
                return False
            entry["last_revalidation_at"] = _utc_now(current)
            entry["last_revalidation_status"] = _require_text(status, "status")
            entry["revalidation_count"] = int(entry.get("revalidation_count") or 0) + 1
            entry["authority_task_id"] = _require_text(
                authority_task_id,
                "authority_task_id",
            )
            authority_run_ids = list(entry.get("authority_run_ids") or [])
            resolvable_run_id = _require_text(authority_run_id, "authority_run_id")
            if resolvable_run_id not in authority_run_ids:
                authority_run_ids.append(resolvable_run_id)
            entry["authority_run_ids"] = authority_run_ids
            entry["experiment_task_id"] = _require_text(
                experiment_task_id,
                "experiment_task_id",
            )
            experiment_run_ids = list(entry.get("experiment_run_ids") or [])
            domain_run_id = _require_text(experiment_run_id, "experiment_run_id")
            if domain_run_id not in experiment_run_ids:
                experiment_run_ids.append(domain_run_id)
            entry["experiment_run_ids"] = experiment_run_ids
            entry["status"] = "completed"
            self._clear_claim(entry)
            self._rewrite_durable(entries)
            return True

    def mark_failed(
        self,
        tenant_id: str,
        strategy_spec_id: str,
        *,
        claim_token: str,
        error: str,
        max_retries: int = 3,
        authority_task_id: str | None = None,
        authority_run_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Record a fenced failed attempt and move exhausted work to the DLQ."""

        if max_retries <= 0:
            raise ValueError("max_retries must be positive")
        current = parse_utc(_utc_now(now))
        assert current is not None
        with self._lock_context():
            entries = self._read()
            entry = self._find(entries, tenant_id, strategy_spec_id)
            if not self._owns_live_claim(entry, claim_token, current):
                return False
            attempt_count = int(entry.get("attempt_count") or 0) + 1
            entry["attempt_count"] = attempt_count
            entry["last_revalidation_at"] = _utc_now(current)
            entry["last_revalidation_status"] = "failed"
            entry["failure_reason"] = _require_text(error, "error")[:2000]
            entry["status"] = "dlq" if attempt_count >= max_retries else "pending"
            if authority_task_id:
                entry["authority_task_id"] = _require_text(
                    authority_task_id,
                    "authority_task_id",
                )
            if authority_run_id:
                authority_run_ids = list(entry.get("authority_run_ids") or [])
                resolvable_run_id = _require_text(
                    authority_run_id,
                    "authority_run_id",
                )
                if resolvable_run_id not in authority_run_ids:
                    authority_run_ids.append(resolvable_run_id)
                entry["authority_run_ids"] = authority_run_ids
            if task_id:
                entry["experiment_task_id"] = _require_text(task_id, "task_id")
            if run_id:
                run_ids = list(entry.get("experiment_run_ids") or [])
                authoritative_run_id = _require_text(run_id, "run_id")
                if authoritative_run_id not in run_ids:
                    run_ids.append(authoritative_run_id)
                entry["experiment_run_ids"] = run_ids
            self._clear_claim(entry)
            self._rewrite_durable(entries)
            return True

    def replay_dlq(
        self,
        tenant_id: str,
        strategy_spec_id: str,
        *,
        replay_id: str,
        replayed_by: str,
        reason: str,
        now: datetime | None = None,
    ) -> bool:
        """Idempotently reset one DLQ entry to pending."""

        replay_key = _require_text(replay_id, "replay_id")
        with self._lock_context():
            entries = self._read()
            entry = self._find(entries, tenant_id, strategy_spec_id)
            if entry is None:
                return False
            consumed_replay_ids = [
                str(value)
                for value in list(entry.get("consumed_replay_ids") or [])
                if str(value).strip()
            ]
            legacy_last_replay_id = str(entry.get("last_replay_id") or "").strip()
            if (
                legacy_last_replay_id
                and legacy_last_replay_id not in consumed_replay_ids
            ):
                consumed_replay_ids.append(legacy_last_replay_id)
            if replay_key in consumed_replay_ids:
                return False
            if entry.get("status") != "dlq":
                return False
            entry["status"] = "pending"
            entry["attempt_count"] = 0
            entry["last_revalidation_status"] = None
            entry["failure_reason"] = None
            entry["last_replay_id"] = replay_key
            entry["last_replayed_at"] = _utc_now(now)
            entry["last_replayed_by"] = _require_text(replayed_by, "replayed_by")
            entry["last_replay_reason"] = _require_text(reason, "reason")
            entry["replay_count"] = int(entry.get("replay_count") or 0) + 1
            consumed_replay_ids.append(replay_key)
            entry["consumed_replay_ids"] = consumed_replay_ids
            self._rewrite_durable(entries)
            return True

    def list_pending(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock_context():
            entries = [entry for entry in self._read() if entry.get("status") == "pending"]
        if tenant_id is not None:
            entries = [entry for entry in entries if entry.get("tenant_id") == tenant_id]
        return entries

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock_context():
            return list(self._read())

    def get_metrics(self) -> dict[str, Any]:
        with self._lock_context():
            entries = self._read()
        return {
            "total": len(entries),
            "pending": sum(1 for entry in entries if entry.get("status") == "pending"),
            "claimed": sum(1 for entry in entries if entry.get("status") == "claimed"),
            "completed": sum(1 for entry in entries if entry.get("status") == "completed"),
            "dlq": sum(1 for entry in entries if entry.get("status") == "dlq"),
            "revalidated": sum(
                1 for entry in entries if int(entry.get("revalidation_count") or 0) > 0
            ),
            "last_revalidation_failed": sum(
                1 for entry in entries if entry.get("last_revalidation_status") == "failed"
            ),
        }

    def _recover_expired(
        self,
        entries: list[dict[str, Any]],
        *,
        tenant_id: str,
        now: datetime,
    ) -> int:
        recovered = 0
        for entry in entries:
            if entry.get("tenant_id") != tenant_id or entry.get("status") != "claimed":
                continue
            expires_at = parse_utc(entry.get("lease_expires_at"))
            if expires_at is not None and expires_at > now:
                continue
            entry["status"] = "pending"
            entry["reclaimed_count"] = int(entry.get("reclaimed_count") or 0) + 1
            entry["last_reclaimed_at"] = _utc_now(now)
            self._clear_claim(entry)
            recovered += 1
        return recovered

    @staticmethod
    def _find(
        entries: list[dict[str, Any]],
        tenant_id: str,
        strategy_spec_id: str,
    ) -> dict[str, Any] | None:
        target = (
            _require_text(tenant_id, "tenant_id"),
            _require_text(strategy_spec_id, "strategy_spec_id"),
        )
        return next((entry for entry in entries if _key(entry) == target), None)

    @staticmethod
    def _owns_live_claim(
        entry: dict[str, Any] | None,
        claim_token: str,
        now: datetime,
    ) -> bool:
        if entry is None or entry.get("status") != "claimed":
            return False
        if entry.get("claim_token") != _require_text(claim_token, "claim_token"):
            return False
        expires_at = parse_utc(entry.get("lease_expires_at"))
        return expires_at is not None and expires_at > now

    @staticmethod
    def _clear_claim(entry: dict[str, Any]) -> None:
        for field_name in (
            "claimed_by",
            "claimed_at",
            "lease_expires_at",
            "claim_token",
        ):
            entry.pop(field_name, None)

    def _read(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                entries.append(payload)
        return entries

    def _rewrite_durable(self, entries: list[dict[str, Any]]) -> None:
        content = "\n".join(
            json.dumps(entry, ensure_ascii=True, sort_keys=True) for entry in entries
        ) + "\n"
        temp_path = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._path)
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception as exc:
            raise RuntimeError(f"Failed to write queue entries: {exc}") from exc
        finally:
            if temp_path.exists():
                temp_path.unlink()


__all__ = ["AlphaReplicationQueue", "REVIEWABLE_STATES", "parse_utc"]
