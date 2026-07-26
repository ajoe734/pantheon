"""Durable exclusive leases for the Deployment saga outbox.

Deployment events remain owned by the canonical saga store.  This ledger adds
the dispatcher ownership state that the legacy outbox record did not model:
claim, expiry/recovery, and acknowledged/released terminal lease receipts.
Every ledger mutation is serialized with an OS file lock and committed through
an atomic replace, so two service workers sharing the same volume cannot both
own one event.
"""
from __future__ import annotations

import copy
import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping


class OutboxLeaseError(ValueError):
    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_rfc3339(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class DeploymentOutboxLeaseStore:
    """File-backed claim ledger with process-safe compare-and-set semantics."""

    def __init__(
        self,
        storage_path: str | Path,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.storage_path = Path(storage_path)
        self.lock_path = self.storage_path.with_suffix(
            self.storage_path.suffix + ".lock"
        )
        self._clock = clock

    @contextmanager
    def _locked_state(self) -> Iterator[dict[str, Any]]:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            state = self._load()
            try:
                yield state
            except Exception:
                raise
            else:
                self._persist(state)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def claim(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        tenant_id: str,
        consumer_name: str,
        lease_seconds: int,
        limit: int,
        aggregate_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if lease_seconds < 1:
            raise OutboxLeaseError("lease_seconds must be >= 1", status_code=400)
        if limit < 1:
            raise OutboxLeaseError("limit must be >= 1", status_code=400)
        now = self._clock()
        candidates = [copy.deepcopy(dict(record)) for record in records]
        claimed: list[dict[str, Any]] = []
        with self._locked_state() as state:
            self._recover_expired(state, now=now)
            leases = state["leases"]
            for record in candidates:
                if len(claimed) >= limit:
                    break
                event = record.get("event")
                if not isinstance(event, Mapping):
                    continue
                event_id = str(event.get("event_id") or "")
                event_aggregate = str(event.get("aggregate_id") or "")
                if not event_id or (aggregate_id and event_aggregate != aggregate_id):
                    continue
                existing = leases.get(event_id)
                if isinstance(existing, Mapping) and existing.get("status") == "active":
                    continue

                recovery_count = int(
                    (existing or {}).get("recovery_count", 0)
                    if isinstance(existing, Mapping)
                    else 0
                )
                token = uuid.uuid4().hex
                lease = {
                    "event_id": event_id,
                    "aggregate_id": event_aggregate,
                    "tenant_id": tenant_id,
                    "consumer_name": consumer_name,
                    "claim_token": token,
                    "status": "active",
                    "claimed_at": rfc3339(now),
                    "lease_expires_at": rfc3339(
                        now + timedelta(seconds=lease_seconds)
                    ),
                    "acknowledged_at": None,
                    "released_at": None,
                    "release_reason": None,
                    "recovery_count": recovery_count,
                }
                leases[event_id] = lease
                response_lease = copy.deepcopy(lease)
                response_lease["lease_status"] = response_lease.pop("status")
                record.update(response_lease)
                claimed.append(record)
            state["updated_at"] = rfc3339(now)
        return claimed

    def acknowledge(
        self,
        *,
        event_id: str,
        claim_token: str,
        tenant_id: str,
        consumer_name: str,
    ) -> dict[str, Any]:
        now = self._clock()
        with self._locked_state() as state:
            self._recover_expired(state, now=now)
            lease = self._require_active(
                state,
                event_id=event_id,
                claim_token=claim_token,
                tenant_id=tenant_id,
                consumer_name=consumer_name,
            )
            lease["status"] = "acknowledged"
            lease["acknowledged_at"] = rfc3339(now)
            state["updated_at"] = rfc3339(now)
            return copy.deepcopy(lease)

    def release(
        self,
        *,
        event_id: str,
        claim_token: str,
        tenant_id: str,
        consumer_name: str,
        reason: str,
    ) -> dict[str, Any]:
        now = self._clock()
        with self._locked_state() as state:
            self._recover_expired(state, now=now)
            lease = self._require_active(
                state,
                event_id=event_id,
                claim_token=claim_token,
                tenant_id=tenant_id,
                consumer_name=consumer_name,
            )
            lease["status"] = "released"
            lease["released_at"] = rfc3339(now)
            lease["release_reason"] = str(reason or "delivery failure")
            state["updated_at"] = rfc3339(now)
            return copy.deepcopy(lease)

    def require_active(
        self,
        *,
        event_id: str,
        claim_token: str,
        tenant_id: str,
        consumer_name: str,
    ) -> dict[str, Any]:
        now = self._clock()
        with self._locked_state() as state:
            self._recover_expired(state, now=now)
            return copy.deepcopy(
                self._require_active(
                    state,
                    event_id=event_id,
                    claim_token=claim_token,
                    tenant_id=tenant_id,
                    consumer_name=consumer_name,
                )
            )

    def health(self) -> dict[str, Any]:
        now = self._clock()
        with self._locked_state() as state:
            recovered = self._recover_expired(state, now=now)
            leases = list(state["leases"].values())
            active = [lease for lease in leases if lease.get("status") == "active"]
            return {
                "status": "ok",
                "active_claim_count": len(active),
                "acknowledged_claim_count": sum(
                    lease.get("status") == "acknowledged" for lease in leases
                ),
                "released_claim_count": sum(
                    lease.get("status") == "released" for lease in leases
                ),
                "recovered_claim_count": int(state.get("recovered_claim_count", 0)),
                "recovered_this_check": recovered,
                "oldest_active_claimed_at": min(
                    (
                        str(lease.get("claimed_at"))
                        for lease in active
                        if lease.get("claimed_at")
                    ),
                    default=None,
                ),
                "updated_at": state.get("updated_at"),
            }

    def _recover_expired(self, state: dict[str, Any], *, now: datetime) -> int:
        recovered = 0
        for lease in state["leases"].values():
            if lease.get("status") != "active":
                continue
            expires_at = parse_rfc3339(lease.get("lease_expires_at"))
            if expires_at is not None and expires_at > now:
                continue
            lease["status"] = "released"
            lease["released_at"] = rfc3339(now)
            lease["release_reason"] = "lease_expired_idle_recovery"
            lease["recovery_count"] = int(lease.get("recovery_count", 0)) + 1
            recovered += 1
        if recovered:
            state["recovered_claim_count"] = int(
                state.get("recovered_claim_count", 0)
            ) + recovered
            state["last_recovered_at"] = rfc3339(now)
            state["updated_at"] = rfc3339(now)
        return recovered

    @staticmethod
    def _require_active(
        state: Mapping[str, Any],
        *,
        event_id: str,
        claim_token: str,
        tenant_id: str,
        consumer_name: str,
    ) -> dict[str, Any]:
        lease = state["leases"].get(event_id)
        if not isinstance(lease, dict):
            raise OutboxLeaseError(f"Outbox event {event_id!r} has no active claim.")
        expected = {
            "status": "active",
            "claim_token": claim_token,
            "tenant_id": tenant_id,
            "consumer_name": consumer_name,
        }
        mismatches = [
            f"{field} expected {value!r}, got {lease.get(field)!r}"
            for field, value in expected.items()
            if lease.get(field) != value
        ]
        if mismatches:
            raise OutboxLeaseError(
                f"Outbox claim for event {event_id!r} is not owned by this caller: "
                + "; ".join(mismatches)
            )
        return lease

    def _load(self) -> dict[str, Any]:
        if not self.storage_path.exists():
            return {
                "schema_version": 1,
                "leases": {},
                "recovered_claim_count": 0,
                "last_recovered_at": None,
                "updated_at": None,
            }
        text = self.storage_path.read_text(encoding="utf-8")
        if not text.strip():
            return {
                "schema_version": 1,
                "leases": {},
                "recovered_claim_count": 0,
                "last_recovered_at": None,
                "updated_at": None,
            }
        payload = json.loads(text)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("leases"), dict
        ):
            raise OutboxLeaseError(
                f"Invalid deployment outbox lease ledger: {self.storage_path}",
                status_code=500,
            )
        payload.setdefault("schema_version", 1)
        payload.setdefault("recovered_claim_count", 0)
        payload.setdefault("last_recovered_at", None)
        payload.setdefault("updated_at", None)
        return payload

    def _persist(self, state: Mapping[str, Any]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.storage_path.with_name(
            f".{self.storage_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        tmp_path.write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.storage_path)


__all__ = [
    "DeploymentOutboxLeaseStore",
    "OutboxLeaseError",
    "parse_rfc3339",
    "rfc3339",
    "utc_now",
]
