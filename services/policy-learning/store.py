from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


_PG_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Candidate backlog lease states.  ``claimed`` is owned by exactly one worker
# until its lease expires; every other state is unowned.
STATUS_PROPOSED = "proposed"
STATUS_CLAIMED = "claimed"
STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"
STATUS_DEGRADED = "degraded"

DEFAULT_LEASE_SECONDS = 60
MAX_LEASE_SECONDS = 900
DEFAULT_CLAIM_BATCH_SIZE = 25
MAX_CLAIM_BATCH_SIZE = 200


class LeaseLostError(RuntimeError):
    """A worker tried to settle a claim it no longer owns.

    Raised when the stored lease token no longer matches, which happens after a
    lease expires and another worker reclaims the candidate.  The losing worker
    must discard its result instead of overwriting the new owner's work.
    """


class LeaseHeldError(RuntimeError):
    """An operator action targeted a candidate a live worker still owns.

    Requeuing a leased candidate would let the eventual settle of the current
    owner and the result of the requeued run both claim to be authoritative.
    """


class CandidateStoreCorrupt(RuntimeError):
    """The JSON candidate backlog on disk is not readable as a backlog.

    This is deliberately *not* treated as an empty backlog.  A truncated or
    partially written file is indistinguishable from "no work" if it decodes to
    nothing, and an empty backlog silently satisfies every read: the scheduler
    would report a healthy, idle loop while real candidates were unreachable.
    """

    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(f"policy-learning candidate backlog at {path} is unreadable: {detail}")
        self.path = str(path)
        self.detail = detail


def candidate_dedupe_key(tenant_id: Any, tick_id: Any, dataset_version_id: Any) -> str:
    """Authority key for one (tenant, tick, dataset version) unit of work.

    This triple is the identity of a scheduled shadow evaluation.  Two ticks in
    the same window, a scheduler restart, and two schedulers racing all resolve
    to the same key, so duplicate suppression is a property of the key rather
    than of a read-then-write window that a concurrent caller can slip through.
    """

    tenant = str(tenant_id or "").strip()
    tick = str(tick_id or "").strip()
    version = str(dataset_version_id or "").strip()
    if not tick or not version:
        raise ValueError("tick_id and dataset_version_id are required for a candidate key")
    # Length-prefixed so no id containing the separator can forge another key.
    return "/".join(f"{len(part)}:{part}" for part in (tenant, tick, version))


def candidate_id_for(tenant_id: Any, tick_id: Any, dataset_version_id: Any) -> str:
    """Derive the collision-free candidate id for a dedupe key.

    The id is a digest of the same triple rather than a backlog-length counter.
    A counter is only unique against the rows one process happened to read, so
    two processes ticking concurrently mint the same ``sic-<date>-001`` for
    different datasets and the second write destroys the first.
    """

    digest = hashlib.sha256(
        candidate_dedupe_key(tenant_id, tick_id, dataset_version_id).encode("utf-8")
    ).hexdigest()
    return f"sic-{digest[:32]}"


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def clamp_lease_seconds(value: Any) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LEASE_SECONDS
    return max(1, min(MAX_LEASE_SECONDS, resolved))


def clamp_batch_size(value: Any) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CLAIM_BATCH_SIZE
    return max(1, min(MAX_CLAIM_BATCH_SIZE, resolved))


def candidate_tenant_id(candidate: Dict[str, Any]) -> str:
    """Tenant scope a candidate belongs to, read from its dataset reference."""

    dataset_ref = candidate.get("dataset_ref")
    if isinstance(dataset_ref, dict):
        tenant = str(dataset_ref.get("tenant_id") or "").strip()
        if tenant:
            return tenant
    return str(candidate.get("tenant_id") or "").strip()


def is_lease_live(candidate: Dict[str, Any], now: datetime) -> bool:
    """True when a worker still holds an unexpired lease on this candidate."""

    if str(candidate.get("status") or "").lower() != STATUS_CLAIMED:
        return False
    expires_at = _parse_iso(candidate.get("lease_expires_at"))
    return expires_at is not None and expires_at > now


def is_claimable(candidate: Dict[str, Any], now: datetime) -> bool:
    """True when no live lease owns this candidate and it still needs work."""

    status = str(candidate.get("status") or "").lower()
    if status == STATUS_PROPOSED:
        return True
    if status != STATUS_CLAIMED:
        return False
    expires_at = _parse_iso(candidate.get("lease_expires_at"))
    return expires_at is None or expires_at <= now


def apply_claim(
    candidate: Dict[str, Any],
    *,
    worker_id: str,
    lease_seconds: int,
    now: datetime,
) -> Dict[str, Any]:
    """Stamp a fresh lease onto a candidate and return it."""

    candidate["status"] = STATUS_CLAIMED
    candidate["lease_owner"] = worker_id
    candidate["lease_token"] = f"lease-{uuid.uuid4().hex}"
    candidate["lease_expires_at"] = _iso(now + timedelta(seconds=lease_seconds))
    candidate["lease_acquired_at"] = _iso(now)
    candidate["attempt_count"] = int(candidate.get("attempt_count") or 0) + 1
    candidate["updated_at"] = _iso(now)
    return candidate


def clear_lease(candidate: Dict[str, Any]) -> Dict[str, Any]:
    for field in ("lease_owner", "lease_token", "lease_expires_at", "lease_acquired_at"):
        candidate.pop(field, None)
    return candidate


def _quote_pg_identifier(identifier: str) -> str:
    parts = identifier.split(".")
    if not parts or any(_PG_IDENTIFIER_RE.fullmatch(part) is None for part in parts):
        raise ValueError(f"Invalid Postgres identifier: {identifier}")
    return ".".join(f'"{part}"' for part in parts)


class PostgresPolicyLearningJobStore:
    def __init__(self, dsn: str, table: str = "policy_learning.jobs", bootstrap: bool = True) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required")
        self.dsn = dsn
        self.table = _quote_pg_identifier(table)
        self.schema = table.split(".", 1)[0] if "." in table else ""
        if bootstrap:
            self.bootstrap()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("psycopg is required when POLICY_LEARNING_STORE_BACKEND=postgres") from exc
        return psycopg.connect(self.dsn)

    def bootstrap(self) -> None:
        with self._connect() as conn:
            if self.schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_pg_identifier(self.schema)}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    job_id TEXT PRIMARY KEY,
                    policy_id TEXT,
                    status TEXT,
                    proposed_at TEXT,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(f"SELECT payload FROM {self.table} ORDER BY proposed_at NULLS LAST, job_id")
            rows = cursor.fetchall()
        records: List[Dict[str, Any]] = []
        for row in rows:
            payload = row[0] if isinstance(row, tuple) else row.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(f"SELECT payload FROM {self.table} WHERE job_id = %s", (job_id,))
            rows = cursor.fetchall()
        if not rows:
            return None
        payload = rows[0][0] if isinstance(rows[0], tuple) else rows[0].get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        return payload if isinstance(payload, dict) else None

    def put_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(job))
        job_id = str(record.get("job_id") or record.get("id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        record["job_id"] = job_id
        record["id"] = job_id
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table} (job_id, policy_id, status, proposed_at, payload)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (job_id) DO UPDATE SET
                    policy_id = EXCLUDED.policy_id,
                    status = EXCLUDED.status,
                    proposed_at = EXCLUDED.proposed_at,
                    payload = EXCLUDED.payload,
                    updated_at = now()
                """,
                (
                    job_id,
                    record.get("policy_id"),
                    record.get("status"),
                    record.get("proposed_at"),
                    json.dumps(record, ensure_ascii=True, sort_keys=True),
                ),
            )
        return record


class PostgresPolicyLearningCandidateStore:
    def __init__(self, dsn: str, table: str = "policy_learning.candidates", bootstrap: bool = True) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required")
        self.dsn = dsn
        self.table = _quote_pg_identifier(table)
        self.schema = table.split(".", 1)[0] if "." in table else ""
        if bootstrap:
            self.bootstrap()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("psycopg is required when POLICY_LEARNING_STORE_BACKEND=postgres") from exc
        return psycopg.connect(self.dsn)

    def bootstrap(self) -> None:
        with self._connect() as conn:
            if self.schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_pg_identifier(self.schema)}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    candidate_id TEXT PRIMARY KEY,
                    tick_id TEXT,
                    eval_type TEXT,
                    status TEXT,
                    tenant_id TEXT,
                    dedupe_key TEXT,
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_expires_at TIMESTAMPTZ,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            # ADD COLUMN keeps deployments created before L12-IMIT-001 usable.
            for ddl in (
                "ADD COLUMN IF NOT EXISTS tenant_id TEXT",
                "ADD COLUMN IF NOT EXISTS dedupe_key TEXT",
                "ADD COLUMN IF NOT EXISTS lease_owner TEXT",
                "ADD COLUMN IF NOT EXISTS lease_token TEXT",
                "ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ",
                "ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0",
            ):
                conn.execute(f"ALTER TABLE {self.table} {ddl}")
            # The (tenant, tick, dataset version) authority lives in the
            # database, not in the caller: a concurrent duplicate tick loses the
            # INSERT rather than winning a read-then-write race.
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_learning_candidate_dedupe "
                f"ON {self.table} (dedupe_key)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_policy_learning_candidate_claim "
                f"ON {self.table} (status, lease_expires_at, candidate_id)"
            )

    def list_candidates(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(f"SELECT payload FROM {self.table} ORDER BY candidate_id")
            rows = cursor.fetchall()
        records: List[Dict[str, Any]] = []
        for row in rows:
            payload = row[0] if isinstance(row, tuple) else row.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def get_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(f"SELECT payload FROM {self.table} WHERE candidate_id = %s", (candidate_id,))
            rows = cursor.fetchall()
        if not rows:
            return None
        payload = rows[0][0] if isinstance(rows[0], tuple) else rows[0].get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        return payload if isinstance(payload, dict) else None

    def put_candidate(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(candidate))
        candidate_id = str(record.get("candidate_id") or record.get("id") or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        record["candidate_id"] = candidate_id
        record["id"] = candidate_id
        with self._connect() as conn:
            self._upsert(conn, record)
        return record

    def _row_values(self, record: Dict[str, Any]) -> tuple:
        return (
            record["candidate_id"],
            record.get("tick_id"),
            record.get("eval_type"),
            record.get("status"),
            candidate_tenant_id(record) or None,
            record.get("dedupe_key") or None,
            record.get("lease_owner"),
            record.get("lease_token"),
            _parse_iso(record.get("lease_expires_at")),
            int(record.get("attempt_count") or 0),
            json.dumps(record, ensure_ascii=True, sort_keys=True),
        )

    _INSERT_COLUMNS = (
        "candidate_id, tick_id, eval_type, status, tenant_id, dedupe_key, "
        "lease_owner, lease_token, lease_expires_at, attempt_count, payload"
    )
    _INSERT_PLACEHOLDERS = "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb"

    def _upsert(self, conn: Any, record: Dict[str, Any]) -> None:
        conn.execute(
            f"""
            INSERT INTO {self.table} ({self._INSERT_COLUMNS})
            VALUES ({self._INSERT_PLACEHOLDERS})
            ON CONFLICT (candidate_id) DO UPDATE SET
                tick_id = EXCLUDED.tick_id,
                eval_type = EXCLUDED.eval_type,
                status = EXCLUDED.status,
                tenant_id = EXCLUDED.tenant_id,
                dedupe_key = EXCLUDED.dedupe_key,
                lease_owner = EXCLUDED.lease_owner,
                lease_token = EXCLUDED.lease_token,
                lease_expires_at = EXCLUDED.lease_expires_at,
                attempt_count = EXCLUDED.attempt_count,
                payload = EXCLUDED.payload,
                updated_at = now()
            """,
            self._row_values(record),
        )

    def create_candidate_if_absent(self, candidate: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """Insert a candidate unless its dedupe key already exists.

        ``ON CONFLICT DO NOTHING`` makes the (tenant, tick, dataset version)
        authority atomic across processes: a concurrent duplicate tick observes
        zero inserted rows and reads back the winner instead of overwriting it.
        """

        record = json.loads(json.dumps(candidate))
        candidate_id = str(record.get("candidate_id") or record.get("id") or "").strip()
        dedupe_key = str(record.get("dedupe_key") or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        if not dedupe_key:
            raise ValueError("dedupe_key is required")
        record["candidate_id"] = candidate_id
        record["id"] = candidate_id
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                INSERT INTO {self.table} ({self._INSERT_COLUMNS})
                VALUES ({self._INSERT_PLACEHOLDERS})
                ON CONFLICT DO NOTHING
                RETURNING payload
                """,
                self._row_values(record),
            ).fetchall()
            if rows:
                return record, True
            existing = conn.execute(
                f"SELECT payload FROM {self.table} WHERE dedupe_key = %s",
                (dedupe_key,),
            ).fetchall()
        if not existing:
            # Losing the insert without a readable winner means the conflict was
            # on candidate_id, which is derived from the same dedupe key.
            return self.get_candidate(candidate_id) or record, False
        payload = existing[0][0] if isinstance(existing[0], tuple) else existing[0].get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        return (payload if isinstance(payload, dict) else record), False

    def claim_candidates(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        batch_size: int,
        tenant_id: str = "",
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Claim unowned backlog work with ``FOR UPDATE SKIP LOCKED``.

        Concurrent workers never receive the same candidate: rows locked by a
        peer are skipped rather than waited on, and a row whose lease has
        expired becomes claimable again so a crashed worker's work is recovered.
        """

        moment = now or utc_now_dt()
        clauses = [
            "(status = %s OR (status = %s AND (lease_expires_at IS NULL OR lease_expires_at <= %s)))"
        ]
        params: List[Any] = [STATUS_PROPOSED, STATUS_CLAIMED, moment]
        if tenant_id:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        params.append(clamp_batch_size(batch_size))

        claimed: List[Dict[str, Any]] = []
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM {self.table} WHERE {' AND '.join(clauses)} "
                "ORDER BY candidate_id FOR UPDATE SKIP LOCKED LIMIT %s",
                tuple(params),
            ).fetchall()
            for row in rows:
                payload = row[0] if isinstance(row, tuple) else row.get("payload")
                if isinstance(payload, str):
                    payload = json.loads(payload)
                if not isinstance(payload, dict):
                    continue
                record = apply_claim(
                    payload,
                    worker_id=worker_id,
                    lease_seconds=clamp_lease_seconds(lease_seconds),
                    now=moment,
                )
                self._upsert(conn, record)
                claimed.append(record)
        return claimed

    def settle_candidate(self, candidate: Dict[str, Any], *, lease_token: str) -> Dict[str, Any]:
        """Persist a terminal result, rejecting a lease the worker has lost."""

        record = json.loads(json.dumps(candidate))
        candidate_id = str(record.get("candidate_id") or record.get("id") or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        record["candidate_id"] = candidate_id
        record["id"] = candidate_id
        clear_lease(record)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT lease_token FROM {self.table} WHERE candidate_id = %s FOR UPDATE",
                (candidate_id,),
            ).fetchall()
            if not rows:
                raise LeaseLostError(f"candidate {candidate_id} no longer exists")
            stored = rows[0][0] if isinstance(rows[0], tuple) else rows[0].get("lease_token")
            if str(stored or "") != str(lease_token or ""):
                raise LeaseLostError(
                    f"lease for candidate {candidate_id} was reclaimed by another worker"
                )
            self._upsert(conn, record)
        return record

    def requeue_candidate(
        self,
        candidate_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Return one terminal candidate to the backlog, fencing live leases."""

        moment = now or utc_now_dt()
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM {self.table} WHERE candidate_id = %s FOR UPDATE",
                (candidate_id,),
            ).fetchall()
            if not rows:
                raise KeyError(candidate_id)
            payload = rows[0][0] if isinstance(rows[0], tuple) else rows[0].get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                raise KeyError(candidate_id)
            if is_lease_live(payload, moment):
                raise LeaseHeldError(
                    f"candidate {candidate_id} is leased by {payload.get('lease_owner')!r} "
                    f"until {payload.get('lease_expires_at')}"
                )
            clear_lease(payload)
            payload["status"] = STATUS_PROPOSED
            payload["updated_at"] = _iso(moment)
            payload.pop("error_message", None)
            payload.pop("degradation", None)
            self._upsert(conn, payload)
        return payload

    def release_expired_leases(
        self,
        *,
        tenant_id: str = "",
        now: Optional[datetime] = None,
    ) -> List[str]:
        """Return orphaned claims to the backlog after a worker restart."""

        moment = now or utc_now_dt()
        released: List[str] = []
        clauses = ["status = %s", "(lease_expires_at IS NULL OR lease_expires_at <= %s)"]
        params: List[Any] = [STATUS_CLAIMED, moment]
        if tenant_id:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM {self.table} WHERE {' AND '.join(clauses)} "
                "ORDER BY candidate_id FOR UPDATE SKIP LOCKED",
                tuple(params),
            ).fetchall()
            for row in rows:
                payload = row[0] if isinstance(row, tuple) else row.get("payload")
                if isinstance(payload, str):
                    payload = json.loads(payload)
                if not isinstance(payload, dict):
                    continue
                clear_lease(payload)
                payload["status"] = STATUS_PROPOSED
                payload["updated_at"] = _iso(moment)
                self._upsert(conn, payload)
                released.append(str(payload.get("candidate_id") or ""))
        return released


class PolicyLearningStore:
    def __init__(
        self,
        data_dir: str | Path,
        job_store: PostgresPolicyLearningJobStore | None = None,
        candidate_store: PostgresPolicyLearningCandidateStore | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_path = self.data_dir / "policy_learning_jobs.json"
        self.candidates_path = self.data_dir / "shadow_imitation_candidates.json"
        self.candidates_lock_path = self.data_dir / "shadow_imitation_candidates.lock"
        self.job_store = job_store
        self.candidate_store = candidate_store
        self.authority_resolution: Dict[str, Any] = {
            "candidate_authority": "postgres" if candidate_store is not None else "json",
            "durable": candidate_store is not None,
            "reason": "explicit_construction",
            "requested_store_backend": "unset",
            "dsn_configured": candidate_store is not None,
            "dsn": "",
        }
        self._thread_lock = threading.RLock()

    def describe(self) -> Dict[str, Any]:
        """Non-secret description of which backend owns candidate authority."""

        return {
            key: value
            for key, value in self.authority_resolution.items()
            if key != "dsn"
        }

    @contextlib.contextmanager
    def _candidate_lock(self) -> Iterator[None]:
        """Serialize candidate read-modify-write across threads and processes.

        The JSON backlog is a single file shared by the API process and any
        sibling worker process, so an in-process lock alone would still let two
        workers claim the same candidate.  ``flock`` closes that gap; on a
        platform without it the thread lock still holds.
        """

        with self._thread_lock:
            try:
                import fcntl  # type: ignore[import]
            except ImportError:  # pragma: no cover - non-POSIX platforms
                yield
                return
            self.candidates_lock_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.candidates_lock_path, "a+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_candidates(self) -> Dict[str, Dict[str, Any]]:
        """Read the backlog, refusing to present damaged state as an empty one.

        Raises:
            CandidateStoreCorrupt: the file exists but is not a JSON object of
                candidate records.  A missing or zero-length file is a genuine
                empty backlog and is not corruption.
        """

        if not self.candidates_path.exists():
            return {}
        text = self.candidates_path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise CandidateStoreCorrupt(self.candidates_path, str(exc)) from exc
        if not isinstance(payload, dict):
            raise CandidateStoreCorrupt(
                self.candidates_path,
                f"expected a JSON object of candidates, found {type(payload).__name__}",
            )
        records = {str(key): value for key, value in payload.items() if isinstance(value, dict)}
        if len(records) != len(payload):
            raise CandidateStoreCorrupt(
                self.candidates_path,
                f"{len(payload) - len(records)} of {len(payload)} entries are not candidate objects",
            )
        return records

    def _write_candidates(self, candidates: Dict[str, Dict[str, Any]]) -> None:
        """Write the backlog atomically so a crash cannot truncate it.

        The worker and the API process share this file; a partial write from an
        interrupted process would be read by its peer as corruption on the very
        next claim.  Rename is atomic within the directory, so a reader sees
        either the previous backlog or the complete new one.
        """

        self.candidates_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.candidates_path.with_name(
            f"{self.candidates_path.name}.{os.getpid()}.tmp"
        )
        temp_path.write_text(json.dumps(candidates, indent=2, ensure_ascii=True), encoding="utf-8")
        os.replace(temp_path, self.candidates_path)

    def list_candidates(self) -> List[Dict[str, Any]]:
        if self.candidate_store is not None:
            return self.candidate_store.list_candidates()
        return list(self._read_candidates().values())

    def get_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        if self.candidate_store is not None:
            return self.candidate_store.get_candidate(candidate_id)
        return self._read_candidates().get(candidate_id)

    def put_candidate(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        if self.candidate_store is not None:
            return self.candidate_store.put_candidate(candidate)
        candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        record = json.loads(json.dumps(candidate))
        record["candidate_id"] = candidate_id
        record["id"] = candidate_id
        with self._candidate_lock():
            records = self._read_candidates()
            records[candidate_id] = record
            self._write_candidates(records)
        return record

    def create_candidate_if_absent(self, candidate: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """Insert a candidate unless its dedupe key already exists.

        The check and the insert happen inside the same cross-process lock, so
        two ticks racing on the same (tenant, tick, dataset version) produce one
        candidate and the loser reads back the winner.
        """

        candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "").strip()
        dedupe_key = str(candidate.get("dedupe_key") or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        if not dedupe_key:
            raise ValueError("dedupe_key is required")
        if self.candidate_store is not None:
            return self.candidate_store.create_candidate_if_absent(candidate)
        record = json.loads(json.dumps(candidate))
        record["candidate_id"] = candidate_id
        record["id"] = candidate_id
        with self._candidate_lock():
            records = self._read_candidates()
            for existing in records.values():
                if str(existing.get("dedupe_key") or "") == dedupe_key:
                    return existing, False
            if candidate_id in records:
                return records[candidate_id], False
            records[candidate_id] = record
            self._write_candidates(records)
        return record, True

    # -- backlog claim / lease -------------------------------------------

    def claim_candidates(
        self,
        *,
        worker_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        batch_size: int = DEFAULT_CLAIM_BATCH_SIZE,
        tenant_id: str = "",
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Take an exclusive, expiring lease on up to ``batch_size`` candidates.

        A duplicate scheduler tick or a second worker calling this concurrently
        gets a disjoint set: claiming is a single locked read-modify-write, and
        a candidate stays owned until its lease expires.
        """

        if self.candidate_store is not None:
            return self.candidate_store.claim_candidates(
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                batch_size=batch_size,
                tenant_id=tenant_id,
                now=now,
            )
        moment = now or utc_now_dt()
        limit = clamp_batch_size(batch_size)
        lease = clamp_lease_seconds(lease_seconds)
        with self._candidate_lock():
            records = self._read_candidates()
            claimable = [
                record
                for record in records.values()
                if is_claimable(record, moment)
                and (not tenant_id or candidate_tenant_id(record) == tenant_id)
            ]
            claimable.sort(key=lambda record: str(record.get("candidate_id") or ""))
            claimed = [
                apply_claim(record, worker_id=worker_id, lease_seconds=lease, now=moment)
                for record in claimable[:limit]
            ]
            if claimed:
                self._write_candidates(records)
        return json.loads(json.dumps(claimed))

    def settle_candidate(self, candidate: Dict[str, Any], *, lease_token: str) -> Dict[str, Any]:
        """Persist a terminal candidate result under the worker's own lease.

        Raises:
            LeaseLostError: the lease expired and another worker reclaimed the
                candidate.  The caller must discard its result rather than
                overwrite the new owner's.
        """

        if self.candidate_store is not None:
            return self.candidate_store.settle_candidate(candidate, lease_token=lease_token)
        candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        record = json.loads(json.dumps(candidate))
        record["candidate_id"] = candidate_id
        record["id"] = candidate_id
        clear_lease(record)
        with self._candidate_lock():
            records = self._read_candidates()
            stored = records.get(candidate_id)
            if stored is None:
                raise LeaseLostError(f"candidate {candidate_id} no longer exists")
            if str(stored.get("lease_token") or "") != str(lease_token or ""):
                raise LeaseLostError(
                    f"lease for candidate {candidate_id} was reclaimed by another worker"
                )
            records[candidate_id] = record
            self._write_candidates(records)
        return record

    def requeue_candidate(
        self,
        candidate_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Return one terminal candidate to the backlog, fencing live leases.

        Raises:
            KeyError: the candidate does not exist.
            LeaseHeldError: a worker still owns an unexpired lease.  Requeuing
                anyway would produce two runs that both believe they are
                authoritative for the same candidate.
        """

        if self.candidate_store is not None:
            return self.candidate_store.requeue_candidate(candidate_id, now=now)
        moment = now or utc_now_dt()
        with self._candidate_lock():
            records = self._read_candidates()
            record = records.get(candidate_id)
            if record is None:
                raise KeyError(candidate_id)
            if is_lease_live(record, moment):
                raise LeaseHeldError(
                    f"candidate {candidate_id} is leased by {record.get('lease_owner')!r} "
                    f"until {record.get('lease_expires_at')}"
                )
            clear_lease(record)
            record["status"] = STATUS_PROPOSED
            record["updated_at"] = _iso(moment)
            record.pop("error_message", None)
            record.pop("degradation", None)
            self._write_candidates(records)
        return json.loads(json.dumps(record))

    def release_expired_leases(
        self,
        *,
        tenant_id: str = "",
        now: Optional[datetime] = None,
    ) -> List[str]:
        """Return orphaned claims to the backlog after a worker restart.

        A tenant's restart only recovers its own orphans.  Sweeping every
        tenant would let one tenant's routine restart write to another tenant's
        backlog rows, which is a cross-tenant effect even though the rows it
        touches are already ownerless.
        """

        if self.candidate_store is not None:
            return self.candidate_store.release_expired_leases(tenant_id=tenant_id, now=now)
        moment = now or utc_now_dt()
        released: List[str] = []
        with self._candidate_lock():
            records = self._read_candidates()
            for candidate_id, record in records.items():
                if str(record.get("status") or "") != STATUS_CLAIMED:
                    continue
                if tenant_id and candidate_tenant_id(record) != tenant_id:
                    continue
                expires_at = _parse_iso(record.get("lease_expires_at"))
                if expires_at is not None and expires_at > moment:
                    continue
                clear_lease(record)
                record["status"] = STATUS_PROPOSED
                record["updated_at"] = _iso(moment)
                released.append(candidate_id)
            if released:
                self._write_candidates(records)
        return released

    def _read_jobs(self) -> Dict[str, Dict[str, Any]]:
        if not self.jobs_path.exists():
            return {}
        text = self.jobs_path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    def _write_jobs(self, jobs: Dict[str, Dict[str, Any]]) -> None:
        self.jobs_path.parent.mkdir(parents=True, exist_ok=True)
        self.jobs_path.write_text(json.dumps(jobs, indent=2, ensure_ascii=True), encoding="utf-8")

    def list_jobs(self) -> List[Dict[str, Any]]:
        if self.job_store is not None:
            return self.job_store.list_jobs()
        return list(self._read_jobs().values())

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self.job_store is not None:
            return self.job_store.get_job(job_id)
        return self._read_jobs().get(job_id)

    def put_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        if self.job_store is not None:
            return self.job_store.put_job(job)
        job_id = str(job.get("job_id") or job.get("id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        records = self._read_jobs()
        record = json.loads(json.dumps(job))
        record["job_id"] = job_id
        record["id"] = job_id
        records[job_id] = record
        self._write_jobs(records)
        return record


CANDIDATE_AUTHORITY_ENV = "POLICY_LEARNING_CANDIDATE_AUTHORITY"


def _configured_dsn() -> str:
    return (os.getenv("POLICY_LEARNING_STORE_DSN") or os.getenv("DATABASE_URL") or "").strip()


def resolve_candidate_authority(
    *,
    backend: Optional[str] = None,
    dsn: Optional[str] = None,
    product_mode: bool = True,
) -> Dict[str, Any]:
    """Decide which backend owns candidate authority, and say why.

    The candidate backlog is the loop's authoritative record of scheduled and
    settled shadow evaluations, so in the product path it belongs in Postgres.
    A JSON file on a container volume is a development convenience: it does not
    survive a container replacement and gives two processes no transaction to
    agree on.

    Resolution order:

    1. an explicit ``POLICY_LEARNING_CANDIDATE_AUTHORITY``;
    2. ``POLICY_LEARNING_STORE_BACKEND=postgres``;
    3. product dataset mode with a reachable DSN configured — this is the
       default compose path, where ``POLICY_LEARNING_STORE_BACKEND`` still says
       ``json`` but ``DATABASE_URL`` is present, and durability wins;
    4. otherwise JSON, reported truthfully as non-durable.
    """

    requested_backend = (
        backend if backend is not None else os.getenv("POLICY_LEARNING_STORE_BACKEND", "")
    ).strip().lower()
    resolved_dsn = dsn if dsn is not None else _configured_dsn()
    if requested_backend and requested_backend not in ("json", "jsonl", "postgres"):
        raise ValueError("POLICY_LEARNING_STORE_BACKEND must be json or postgres")

    explicit = (os.getenv(CANDIDATE_AUTHORITY_ENV) or "").strip().lower()
    if explicit:
        if explicit not in ("json", "jsonl", "postgres"):
            raise ValueError(f"{CANDIDATE_AUTHORITY_ENV} must be json or postgres")
        if explicit == "postgres" and not resolved_dsn:
            raise ValueError(
                f"{CANDIDATE_AUTHORITY_ENV}=postgres requires POLICY_LEARNING_STORE_DSN or DATABASE_URL"
            )
        authority = "postgres" if explicit == "postgres" else "json"
        reason = "explicit_candidate_authority"
    elif requested_backend == "postgres":
        if not resolved_dsn:
            raise ValueError(
                "POLICY_LEARNING_STORE_DSN or DATABASE_URL is required for Postgres store"
            )
        authority = "postgres"
        reason = "explicit_store_backend"
    elif product_mode and resolved_dsn:
        authority = "postgres"
        reason = "product_mode_durable_default"
    else:
        authority = "json"
        reason = "no_dsn_configured" if product_mode else "non_product_dataset_mode"

    return {
        "candidate_authority": authority,
        "durable": authority == "postgres",
        "reason": reason,
        "requested_store_backend": requested_backend or "unset",
        "dsn_configured": bool(resolved_dsn),
        "dsn": resolved_dsn,
    }


def build_policy_learning_store(
    data_dir: str | Path,
    *,
    product_mode: bool = True,
) -> PolicyLearningStore:
    resolution = resolve_candidate_authority(product_mode=product_mode)
    dsn = resolution["dsn"]
    if resolution["candidate_authority"] == "json":
        store = PolicyLearningStore(data_dir)
        store.authority_resolution = resolution
        return store

    job_table = os.getenv("POLICY_LEARNING_STORE_TABLE", "policy_learning.jobs")
    candidate_table = os.getenv("POLICY_LEARNING_CANDIDATE_STORE_TABLE", "policy_learning.candidates")
    bootstrap = os.getenv("POLICY_LEARNING_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
    # The job surface keeps following POLICY_LEARNING_STORE_BACKEND; only the
    # candidate backlog is upgraded to the durable authority by default.
    job_store = (
        PostgresPolicyLearningJobStore(dsn=dsn, table=job_table, bootstrap=bootstrap)
        if resolution["requested_store_backend"] == "postgres"
        else None
    )
    store = PolicyLearningStore(
        data_dir,
        job_store=job_store,
        candidate_store=PostgresPolicyLearningCandidateStore(
            dsn=dsn, table=candidate_table, bootstrap=bootstrap
        ),
    )
    store.authority_resolution = resolution
    return store
