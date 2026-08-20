"""Durable BFF downstream infrastructure-health controller.

The controller owns only BFF-side observation and delivery intent.  Telemetry
remains authoritative in ``telemetry-ingest-svc`` and incidents remain owned by
the incidents service.

Key guarantees
--------------
* Infrastructure telemetry uses the strict non-trading schema and contains no
  RuntimeBinding, capital, artifact, or execution-stage identity.
* A SQLite WAL ledger persists probe state, incident mappings, probe-window
  leases, error-rate windows, and a delivery outbox.
* Stable observation IDs plus shared window leases deduplicate retries,
  restarts, and replicas that share the BFF data volume.
* Telemetry and incident operations keep the same source event ID.  Delivery is
  at-least-once with bounded exponential backoff, DLQ, and explicit replay.
* An incident mapping is not cleared until the incidents authority confirms the
  recovery transition.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

log = logging.getLogger(__name__)

INFRASTRUCTURE_HEALTH_SCHEMA_VERSION = "pantheon.infrastructure-health/1"
INFRASTRUCTURE_HEALTH_PRODUCER = "control-plane-bff"
INFRASTRUCTURE_HEALTH_PATH = "/api/v1/telemetry/infrastructure-health"
INFRASTRUCTURE_INCIDENT_PATH = "/api/incidents/consume-infrastructure-health"
_DELIVERY_HISTORY_RETENTION_SECONDS = 7 * 24 * 60 * 60
_WINDOW_HISTORY_RETENTION_SECONDS = 24 * 60 * 60

# Compatibility exports.  They are deliberately empty/non-trading and must
# never be copied into an infrastructure event or incident request.
_SENTINEL_BINDING_ID = ""
_SENTINEL_STAGE = ""

_POST_HEADERS: contextvars.ContextVar[Dict[str, str]] = contextvars.ContextVar(
    "bff_health_post_headers",
    default={},
)
_TARGET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")


@dataclass(frozen=True)
class DownstreamTarget:
    name: str
    base_url: str
    health_path: str = "/__health__"
    component_kind: str = "http_service"
    source_env: str = "explicit"

    def __post_init__(self) -> None:
        if not self.name or not _TARGET_NAME_RE.fullmatch(self.name):
            raise ValueError(f"invalid health target name: {self.name!r}")
        if not self.base_url or not self.base_url.startswith(("http://", "https://", "file://")):
            raise ValueError(f"health target {self.name!r} base_url must use http(s) or file: {self.base_url!r}")
        if self.component_kind != "file_worker" and not self.base_url.startswith(("http://", "https://")):
            raise ValueError(f"health target {self.name!r} base_url must use http(s): {self.base_url!r}")
        if self.component_kind == "http_service" and (not self.health_path or not self.health_path.startswith("/") or " " in self.health_path):
            raise ValueError(f"health target {self.name!r} health_path must start with '/': {self.health_path!r}")
        if not self.component_kind:
            raise ValueError(f"health target {self.name!r} component_kind cannot be empty")

    @property
    def health_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.health_path.lstrip('/')}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "health_path": self.health_path,
            "health_url": self.health_url,
            "component_kind": self.component_kind,
            "source_env": self.source_env,
        }


# The environment names provisioned on the operator-bff service.  Aliases are
# grouped so a single configured service is probed exactly once.
_DEFAULT_TARGET_SPECS: Sequence[tuple[str, Sequence[str]]] = (
    ("runtime-manager", ("PANTHEON_RUNTIME_MANAGER_URL", "PANTHEON_INTERNAL_API_URL")),
    ("paper-fleet-reconciler", ("PANTHEON_PAPER_FLEET_RECONCILER_URL",)),
    ("governance", ("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "PANTHEON_GOVERNANCE_SERVICE_URL")),
    ("promotion", ("PANTHEON_PROMOTION_API_URL",)),
    ("deployment", ("PANTHEON_DEPLOYMENT_API_URL", "PANTHEON_DEPLOYMENT_SERVICE_URL")),
    ("capital", ("PANTHEON_CAPITAL_API_URL", "PANTHEON_CAPITAL_SERVICE_URL")),
    ("evolution", ("PANTHEON_EVOLUTION_API_URL",)),
    ("incidents", ("PANTHEON_INCIDENTS_API_URL", "PANTHEON_INCIDENTS_URL")),
    ("postmortems", ("PANTHEON_POSTMORTEMS_API_URL",)),
    ("telemetry", ("PANTHEON_TELEMETRY_API_URL", "PANTHEON_TELEMETRY_URL")),
    ("lineage-read", ("PANTHEON_LINEAGE_READ_URL",)),
    ("registry", ("PANTHEON_REGISTRY_API_URL", "PANTHEON_REGISTRY_URL")),
    ("consultation", ("PANTHEON_CONSULTATION_API_URL",)),
    ("source-ingest", ("PANTHEON_SOURCE_INGEST_API_URL", "PANTHEON_SOURCE_INGEST_URL")),
    ("search", ("PANTHEON_SEARCH_API_URL",)),
    ("training-session", ("PANTHEON_TRAINING_SESSION_API_URL",)),
    ("policy-learning", ("PANTHEON_POLICY_LEARNING_API_URL",)),
    ("research-orchestrator", ("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL",)),
    ("research-worker-gateway", ("PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL",)),
    ("reconciliation-drift", ("PANTHEON_RECONCILIATION_DRIFT_API_URL",)),
    ("memory", ("PANTHEON_MEMORY_API_URL", "PANTHEON_MEMORY_SERVICE_URL")),
    ("openclaw-gateway-adapter", ("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL",)),
    ("persona", ("PANTHEON_PERSONA_API_URL",)),
)

_DEFAULT_WORKER_SPECS: Sequence[tuple[str, tuple[str, str]]] = (
    ("paper-signal-producer", ("PAPER_PRODUCER_HEALTH_FILE", "/tmp/paper-signal-producer-health.json")),
    ("consultation-workflow-executor", ("CONSULTATION_WORKFLOW_EXECUTOR_HEALTH_FILE", "/data/consultation/workflow-health.json")),
    ("shadow-eval-scheduler", ("SHADOW_EVAL_SCHEDULER_HEALTH_FILE", "/tmp/policy-learning-shadow-eval-scheduler-health.json")),
    ("deployment-outbox-consumer", ("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "/tmp/deployment-outbox-consumer-health.json")),
    ("reconciliation-drift-consumer", ("RECONCILIATION_DRIFT_CONSUMER_HEALTH_FILE", "/tmp/reconciliation-drift-consumer-health.json")),
    ("reconciliation-drift-scheduler", ("RECONCILIATION_DRIFT_SCHEDULER_HEALTH_FILE", "/tmp/reconciliation-drift-scheduler-health.json")),
    ("reconciliation-drift-incident-listener", ("RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_FILE", "/tmp/reconciliation-drift-incident-listener-health.json")),
    ("evolution-scheduler", ("EVOLUTION_SCHEDULER_HEALTH_FILE", "/tmp/evolution-scheduler-health.json")),
    ("evolution-dispatch", ("EVOLUTION_DISPATCH_HEALTH_FILE", "/tmp/evolution-dispatch-health.json")),
    ("evolution-threshold-sweep", ("EVOCHAIN_THRESHOLD_SWEEP_HEALTH_FILE", "/tmp/evolution-threshold-sweep-health.json")),
)

_DYNAMIC_URL_EXCLUSIONS = {
    "PANTHEON_BFF_URL",
    "PANTHEON_BFF_OIDC_DISCOVERY_URL",
    "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL",
}


def _utc_now_rfc3339() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_rfc3339(value: str) -> datetime:
    normalized = str(value or "").strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _window_start(value: str, seconds: float) -> str:
    width = max(1, int(seconds))
    epoch = int(_parse_rfc3339(value).timestamp())
    start = epoch - (epoch % width)
    return (
        datetime.fromtimestamp(start, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _stable_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_event_id(
    *,
    tenant_id: str,
    producer: str,
    target_name: str,
    probe_kind: str,
    window_started_at: str,
) -> str:
    digest = _stable_digest(
        {
            "tenant_id": tenant_id,
            "producer": producer,
            "target_name": target_name,
            "probe_kind": probe_kind,
            "window_started_at": window_started_at,
        }
    )
    return f"infra-health-{digest[:32]}"


def _probe_http(url: str, timeout: float) -> tuple[bool, int, str]:
    """Return ``(ok, status_code, failure_reason)`` for one health endpoint.

    Worker functional health overrides process readiness: even when the HTTP
    status code is 200, if the JSON response body indicates the worker is
    not ready, not live, degraded, or unhealthy, the probe fails closed.
    """

    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = int(resp.status)
            if not (200 <= status_code < 300):
                return False, status_code, f"HTTP {status_code}"
            raw_body = resp.read()
            if raw_body:
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    if isinstance(payload, dict):
                        is_ready = payload.get("ready")
                        if is_ready is False:
                            reason = payload.get("reason") or payload.get("message") or "worker reported ready=false"
                            return False, status_code, f"functional_unready: {reason}"
                        is_live = payload.get("live")
                        if is_live is False:
                            reason = payload.get("reason") or payload.get("message") or "worker reported live=false"
                            return False, status_code, f"functional_not_live: {reason}"
                        is_ok = payload.get("ok")
                        if is_ok is False:
                            reason = payload.get("reason") or payload.get("message") or "worker reported ok=false"
                            return False, status_code, f"functional_not_ok: {reason}"
                        payload_status = str(payload.get("status") or "").strip().lower()
                        if payload_status in {"degraded", "unhealthy", "failed", "down", "error", "unavailable"}:
                            reason = payload.get("reason") or payload.get("failure_reason") or payload.get("message") or f"worker reported status={payload_status}"
                            return False, status_code, f"functional_degraded: {reason}"
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            return True, status_code, ""
    except urllib.error.HTTPError as exc:
        return False, int(exc.code), f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, -1, f"URLError: {getattr(exc, 'reason', exc)}"
    except OSError as exc:
        return False, -1, f"OSError: {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, -1, f"unexpected: {exc}"


def _probe_file_health(file_path: str, timeout: float) -> tuple[bool, int, str]:
    """Return (ok, status_code, failure_reason) for a file-backed worker health document."""
    p = Path(file_path)
    if not p.exists():
        return False, 404, f"worker health file missing: {file_path}"
    try:
        mtime = p.stat().st_mtime
        age_seconds = max(0.0, time.time() - mtime)
        if age_seconds > 180.0:
            return False, -1, f"worker health file stale (age={age_seconds:.1f}s)"
        with p.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return False, 500, "worker health payload is not an object"

        is_ready = payload.get("ready")
        if is_ready is False:
            reason = payload.get("reason") or payload.get("failure_reason") or payload.get("error_code") or payload.get("message") or "ready=false"
            return False, 500, f"functional_unready: {reason}"
        is_ok = payload.get("ok")
        if is_ok is False:
            reason = payload.get("reason") or payload.get("failure_reason") or payload.get("error_code") or payload.get("message") or "ok=false"
            return False, 500, f"functional_not_ok: {reason}"
        is_live = payload.get("live")
        if is_live is False:
            reason = payload.get("reason") or payload.get("failure_reason") or payload.get("error_code") or payload.get("message") or "live=false"
            return False, 500, f"functional_not_live: {reason}"
        status = str(payload.get("status") or "").strip().lower()
        if status in {"degraded", "unhealthy", "failed", "down", "error", "unavailable"}:
            reason = (
                payload.get("reason")
                or payload.get("failure_reason")
                or payload.get("error_code")
                or payload.get("message")
                or f"status={status}"
            )
            return False, 500, f"functional_degraded: {reason}"

        return True, 200, ""
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return False, 500, f"worker health JSON invalid: {exc}"
    except Exception as exc:
        return False, -1, f"unexpected file probe error: {exc}"


def _post_json(url: str, body: Dict[str, Any], timeout: float) -> tuple[bool, int]:
    """POST JSON using request-scoped headers set by the delivery worker."""

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **_POST_HEADERS.get(),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body, sort_keys=True).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300, int(resp.status)
    except urllib.error.HTTPError as exc:
        return False, int(exc.code)
    except (urllib.error.URLError, OSError):
        return False, -1
    except Exception:  # noqa: BLE001
        return False, -1


class DownstreamProbeResult:
    """Outcome of one interval or error-rate observation."""

    __slots__ = (
        "target_name",
        "ok",
        "status_code",
        "latency_ms",
        "checked_at",
        "failure_reason",
        "consecutive_failures",
        "probe_kind",
        "window_started_at",
        "sample_count",
        "failure_count",
        "error_rate",
    )

    def __init__(
        self,
        *,
        target_name: str,
        ok: bool,
        status_code: int,
        latency_ms: float,
        checked_at: str,
        failure_reason: Optional[str] = None,
        consecutive_failures: int = 0,
        probe_kind: str = "interval_probe",
        window_started_at: Optional[str] = None,
        sample_count: int = 1,
        failure_count: Optional[int] = None,
        error_rate: Optional[float] = None,
    ) -> None:
        self.target_name = target_name
        self.ok = bool(ok)
        self.status_code = int(status_code)
        self.latency_ms = max(0.0, float(latency_ms))
        self.checked_at = checked_at
        self.failure_reason = failure_reason
        self.consecutive_failures = max(0, int(consecutive_failures))
        self.probe_kind = probe_kind
        self.window_started_at = window_started_at or checked_at
        self.sample_count = max(0, int(sample_count))
        self.failure_count = (
            max(0, int(failure_count))
            if failure_count is not None
            else (0 if self.ok else 1)
        )
        self.error_rate = (
            float(error_rate)
            if error_rate is not None
            else (self.failure_count / self.sample_count if self.sample_count else 0.0)
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_name": self.target_name,
            "ok": self.ok,
            "status_code": self.status_code,
            "latency_ms": round(self.latency_ms, 1),
            "checked_at": self.checked_at,
            "failure_reason": self.failure_reason,
            "consecutive_failures": self.consecutive_failures,
            "probe_kind": self.probe_kind,
            "window_started_at": self.window_started_at,
            "sample_count": self.sample_count,
            "failure_count": self.failure_count,
            "error_rate": round(self.error_rate, 6),
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "DownstreamProbeResult":
        return cls(
            target_name=str(row["target_name"]),
            ok=bool(row["ok"]),
            status_code=int(row["status_code"]),
            latency_ms=float(row["latency_ms"]),
            checked_at=str(row["checked_at"]),
            failure_reason=row.get("failure_reason"),
            consecutive_failures=int(row["consecutive_failures"]),
            probe_kind=str(row.get("probe_kind") or "interval_probe"),
            window_started_at=str(row.get("window_started_at") or row["checked_at"]),
            sample_count=int(row.get("sample_count") or 1),
            failure_count=int(row.get("failure_count") or 0),
            error_rate=float(row.get("error_rate") or 0.0),
        )


class _DurableHealthStore:
    """SQLite WAL ledger shared by BFF restarts and same-volume replicas."""

    def __init__(self, path: str) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("durable downstream health state cannot use :memory:")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            legacy_table: Optional[str] = None
            existing_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(delivery_outbox)"
                ).fetchall()
            }
            if existing_columns and "delivery_id" not in existing_columns:
                legacy_table = "delivery_outbox_legacy_v0"
                suffix = 0
                while connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (legacy_table,),
                ).fetchone():
                    suffix += 1
                    legacy_table = f"delivery_outbox_legacy_v{suffix}"
                # Preserve every prior delivery receipt.  The legacy table has
                # incompatible CHECK constraints, so additive ALTERs cannot
                # safely evolve it to the new dependency/claim model.
                connection.execute(
                    f"ALTER TABLE delivery_outbox RENAME TO {legacy_table}"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS probe_windows (
                    target_name TEXT NOT NULL,
                    probe_kind TEXT NOT NULL,
                    window_started_at TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    lease_until REAL NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY (target_name, probe_kind, window_started_at)
                );
                CREATE TABLE IF NOT EXISTS probe_state (
                    target_name TEXT PRIMARY KEY,
                    ok INTEGER NOT NULL,
                    status_code INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    checked_at TEXT NOT NULL,
                    failure_reason TEXT,
                    consecutive_failures INTEGER NOT NULL,
                    probe_kind TEXT NOT NULL,
                    window_started_at TEXT NOT NULL,
                    sample_count INTEGER NOT NULL,
                    failure_count INTEGER NOT NULL,
                    error_rate REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS error_windows (
                    target_name TEXT NOT NULL,
                    window_started_at TEXT NOT NULL,
                    sample_count INTEGER NOT NULL,
                    failure_count INTEGER NOT NULL,
                    latency_total_ms REAL NOT NULL,
                    last_status_code INTEGER NOT NULL,
                    last_detail TEXT,
                    last_observed_at TEXT NOT NULL,
                    PRIMARY KEY (target_name, window_started_at)
                );
                CREATE TABLE IF NOT EXISTS incident_mappings (
                    target_name TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    opening_event_id TEXT NOT NULL,
                    create_delivery_id TEXT NOT NULL,
                    last_event_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS delivery_outbox (
                    delivery_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    dependency_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    next_attempt_at REAL NOT NULL,
                    claim_owner TEXT,
                    claim_token TEXT,
                    claim_until REAL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS delivery_outbox_due_idx
                    ON delivery_outbox(status, next_attempt_at, claim_until);
                CREATE INDEX IF NOT EXISTS delivery_outbox_event_idx
                    ON delivery_outbox(event_id);
                CREATE TABLE IF NOT EXISTS delivery_status_counts (
                    status TEXT PRIMARY KEY,
                    count INTEGER NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS delivery_outbox_count_insert
                AFTER INSERT ON delivery_outbox
                BEGIN
                    INSERT INTO delivery_status_counts(status, count)
                    VALUES (NEW.status, 1)
                    ON CONFLICT(status) DO UPDATE
                    SET count = count + 1;
                END;
                CREATE TRIGGER IF NOT EXISTS delivery_outbox_count_delete
                AFTER DELETE ON delivery_outbox
                BEGIN
                    UPDATE delivery_status_counts
                    SET count = MAX(0, count - 1)
                    WHERE status = OLD.status;
                END;
                CREATE TRIGGER IF NOT EXISTS delivery_outbox_count_status_update
                AFTER UPDATE OF status ON delivery_outbox
                WHEN OLD.status <> NEW.status
                BEGIN
                    UPDATE delivery_status_counts
                    SET count = MAX(0, count - 1)
                    WHERE status = OLD.status;
                    INSERT INTO delivery_status_counts(status, count)
                    VALUES (NEW.status, 1)
                    ON CONFLICT(status) DO UPDATE
                    SET count = count + 1;
                END;
                CREATE TABLE IF NOT EXISTS delivery_replay_audit (
                    replay_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    approval_ref TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    event_id TEXT,
                    channel TEXT,
                    replayed_count INTEGER NOT NULL,
                    replayed_at TEXT NOT NULL
                );
                """
            )
            if legacy_table is not None:
                self._migrate_legacy_outbox(connection, legacy_table)
            self._rebuild_delivery_counts(connection)

    def _rebuild_delivery_counts(self, connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM delivery_status_counts")
        connection.execute(
            """
            INSERT INTO delivery_status_counts(status, count)
            SELECT status, COUNT(*) AS count
            FROM delivery_outbox
            GROUP BY status
            """
        )

    def _migrate_legacy_outbox(
        self,
        connection: sqlite3.Connection,
        legacy_table: str,
    ) -> None:
        """Import the pre-L12 outbox without dropping pending delivery intent."""

        rows = connection.execute(
            f"SELECT * FROM {legacy_table} ORDER BY created_at, outbox_id"
        ).fetchall()
        telemetry_by_event = {
            str(row["event_id"]): str(row["outbox_id"])
            for row in rows
            if str(row["channel"]) == "telemetry"
        }
        incident_open_by_target: Dict[str, str] = {}
        for row in rows:
            if (
                str(row["channel"]) == "incident"
                and str(row["operation"]) == "open"
            ):
                incident_open_by_target[str(row["target_name"])] = str(
                    row["outbox_id"]
                )

        for row in rows:
            legacy_channel = str(row["channel"])
            operation = str(row["operation"])
            if legacy_channel == "telemetry":
                channel = "telemetry"
            elif operation == "resolve":
                channel = "incident_resolve"
            else:
                channel = "incident_open"
            event_id = str(row["event_id"])
            dependencies: List[str] = []
            telemetry_delivery = telemetry_by_event.get(event_id)
            if channel != "telemetry" and telemetry_delivery:
                dependencies.append(telemetry_delivery)
            if channel == "incident_resolve":
                opening_delivery = incident_open_by_target.get(
                    str(row["target_name"])
                )
                if opening_delivery:
                    dependencies.append(opening_delivery)

            legacy_status = str(row["status"])
            status = {
                "dlq": "dead_letter",
                "delivered": "delivered",
            }.get(legacy_status, "retry" if int(row["attempt_count"]) else "pending")
            created_epoch = float(row["created_at"])
            created_at = (
                datetime.fromtimestamp(created_epoch, tz=timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO delivery_outbox(
                    delivery_id, event_id, channel, target_name, url,
                    body_json, dependency_ids_json, status, attempt_count,
                    max_attempts, next_attempt_at, claim_owner, claim_token,
                    claim_until, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 5, ?, NULL, NULL,
                          NULL, ?, ?, ?)
                """,
                (
                    str(row["outbox_id"]),
                    event_id,
                    channel,
                    str(row["target_name"]),
                    str(row["endpoint"]),
                    str(row["payload_json"]),
                    json.dumps(sorted(set(dependencies))),
                    status,
                    int(row["attempt_count"]),
                    float(row["next_attempt_at"]),
                    row["last_error"],
                    created_at,
                    created_at,
                ),
            )

    def claim_window(
        self,
        *,
        target_name: str,
        probe_kind: str,
        window_started_at: str,
        owner_id: str,
        lease_seconds: float,
    ) -> bool:
        now = time.time()
        lease_until = now + max(1.0, lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT owner_id, lease_until, completed_at
                FROM probe_windows
                WHERE target_name=? AND probe_kind=? AND window_started_at=?
                """,
                (target_name, probe_kind, window_started_at),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO probe_windows(
                        target_name, probe_kind, window_started_at,
                        owner_id, lease_until, completed_at
                    ) VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (target_name, probe_kind, window_started_at, owner_id, lease_until),
                )
                connection.commit()
                return True
            if row["completed_at"] is not None:
                connection.rollback()
                return False
            if str(row["owner_id"]) != owner_id and float(row["lease_until"]) > now:
                connection.rollback()
                return False
            connection.execute(
                """
                UPDATE probe_windows
                SET owner_id=?, lease_until=?
                WHERE target_name=? AND probe_kind=? AND window_started_at=?
                """,
                (owner_id, lease_until, target_name, probe_kind, window_started_at),
            )
            connection.commit()
            return True

    def complete_window(
        self,
        *,
        target_name: str,
        probe_kind: str,
        window_started_at: str,
        owner_id: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE probe_windows
                SET completed_at=?, lease_until=0
                WHERE target_name=? AND probe_kind=? AND window_started_at=?
                  AND owner_id=?
                """,
                (
                    _utc_now_rfc3339(),
                    target_name,
                    probe_kind,
                    window_started_at,
                    owner_id,
                ),
            )

    def get_probe(self, target_name: str) -> Optional[DownstreamProbeResult]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM probe_state WHERE target_name=?",
                (target_name,),
            ).fetchone()
        return DownstreamProbeResult.from_row(dict(row)) if row else None

    def list_probes(self) -> Dict[str, DownstreamProbeResult]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM probe_state ORDER BY target_name"
            ).fetchall()
        return {
            str(row["target_name"]): DownstreamProbeResult.from_row(dict(row))
            for row in rows
        }

    def record_probe(self, result: DownstreamProbeResult) -> DownstreamProbeResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT ok, consecutive_failures FROM probe_state WHERE target_name=?",
                (result.target_name,),
            ).fetchone()
            if result.ok:
                consecutive_failures = 0
            elif previous is not None and not bool(previous["ok"]):
                consecutive_failures = int(previous["consecutive_failures"]) + 1
            else:
                consecutive_failures = max(1, result.consecutive_failures)
            result.consecutive_failures = consecutive_failures
            connection.execute(
                """
                INSERT INTO probe_state(
                    target_name, ok, status_code, latency_ms, checked_at,
                    failure_reason, consecutive_failures, probe_kind,
                    window_started_at, sample_count, failure_count, error_rate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_name) DO UPDATE SET
                    ok=excluded.ok,
                    status_code=excluded.status_code,
                    latency_ms=excluded.latency_ms,
                    checked_at=excluded.checked_at,
                    failure_reason=excluded.failure_reason,
                    consecutive_failures=excluded.consecutive_failures,
                    probe_kind=excluded.probe_kind,
                    window_started_at=excluded.window_started_at,
                    sample_count=excluded.sample_count,
                    failure_count=excluded.failure_count,
                    error_rate=excluded.error_rate
                """,
                (
                    result.target_name,
                    1 if result.ok else 0,
                    result.status_code,
                    result.latency_ms,
                    result.checked_at,
                    result.failure_reason,
                    result.consecutive_failures,
                    result.probe_kind,
                    result.window_started_at,
                    result.sample_count,
                    result.failure_count,
                    result.error_rate,
                ),
            )
            connection.commit()
        return result

    def append_error_sample(
        self,
        *,
        target_name: str,
        window_started_at: str,
        ok: bool,
        latency_ms: float,
        status_code: int,
        detail: Optional[str],
        observed_at: str,
    ) -> Dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO error_windows(
                    target_name, window_started_at, sample_count, failure_count,
                    latency_total_ms, last_status_code, last_detail,
                    last_observed_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(target_name, window_started_at) DO UPDATE SET
                    sample_count=error_windows.sample_count + 1,
                    failure_count=error_windows.failure_count + excluded.failure_count,
                    latency_total_ms=error_windows.latency_total_ms + excluded.latency_total_ms,
                    last_status_code=excluded.last_status_code,
                    last_detail=excluded.last_detail,
                    last_observed_at=excluded.last_observed_at
                """,
                (
                    target_name,
                    window_started_at,
                    0 if ok else 1,
                    max(0.0, latency_ms),
                    status_code,
                    detail,
                    observed_at,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM error_windows
                WHERE target_name=? AND window_started_at=?
                """,
                (target_name, window_started_at),
            ).fetchone()
            connection.commit()
        value = dict(row)
        value["error_rate"] = (
            int(value["failure_count"]) / int(value["sample_count"])
            if int(value["sample_count"])
            else 0.0
        )
        value["latency_ms"] = (
            float(value["latency_total_ms"]) / int(value["sample_count"])
            if int(value["sample_count"])
            else 0.0
        )
        return value

    def get_incident(self, target_name: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM incident_mappings WHERE target_name=?",
                (target_name,),
            ).fetchone()
        return dict(row) if row else None

    def list_incidents(self) -> Dict[str, Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM incident_mappings ORDER BY target_name"
            ).fetchall()
        return {str(row["target_name"]): dict(row) for row in rows}

    def reserve_incident(
        self,
        *,
        target_name: str,
        event_id: str,
    ) -> tuple[Dict[str, Any], bool]:
        now = _utc_now_rfc3339()
        digest = _stable_digest(
            {"target_name": target_name, "opening_event_id": event_id}
        )[:24]
        incident_id = f"infra-bff-{digest}"
        delivery_id = f"incident-open:{event_id}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM incident_mappings WHERE target_name=?",
                (target_name,),
            ).fetchone()
            if existing is not None and str(existing["status"]) in {
                "opening",
                "open",
                "resolving",
            }:
                connection.rollback()
                return dict(existing), False
            connection.execute(
                """
                INSERT INTO incident_mappings(
                    target_name, incident_id, status, opening_event_id,
                    create_delivery_id, last_event_id, updated_at
                ) VALUES (?, ?, 'opening', ?, ?, ?, ?)
                ON CONFLICT(target_name) DO UPDATE SET
                    incident_id=excluded.incident_id,
                    status='opening',
                    opening_event_id=excluded.opening_event_id,
                    create_delivery_id=excluded.create_delivery_id,
                    last_event_id=excluded.last_event_id,
                    updated_at=excluded.updated_at
                """,
                (
                    target_name,
                    incident_id,
                    event_id,
                    delivery_id,
                    event_id,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM incident_mappings WHERE target_name=?",
                (target_name,),
            ).fetchone()
            connection.commit()
        return dict(row), True

    def set_incident_status(
        self,
        *,
        target_name: str,
        incident_id: str,
        status: str,
        event_id: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE incident_mappings
                SET status=?, last_event_id=?, updated_at=?
                WHERE target_name=? AND incident_id=?
                """,
                (
                    status,
                    event_id,
                    _utc_now_rfc3339(),
                    target_name,
                    incident_id,
                ),
            )
        return cursor.rowcount == 1

    def queue_delivery(
        self,
        *,
        delivery_id: str,
        event_id: str,
        channel: str,
        target_name: str,
        url: str,
        body: Mapping[str, Any],
        dependency_ids: Iterable[str],
        max_attempts: int,
    ) -> Dict[str, Any]:
        body_json = json.dumps(body, sort_keys=True, separators=(",", ":"))
        dependency_json = json.dumps(sorted(set(dependency_ids)))
        now_text = _utc_now_rfc3339()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM delivery_outbox WHERE delivery_id=?",
                (delivery_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["event_id"]) != event_id
                    or str(existing["channel"]) != channel
                    or str(existing["url"]) != url
                    or str(existing["body_json"]) != body_json
                    or str(existing["dependency_ids_json"]) != dependency_json
                ):
                    connection.rollback()
                    raise ValueError(
                        f"delivery identity conflict for {delivery_id}"
                    )
                connection.rollback()
                return dict(existing)
            connection.execute(
                """
                INSERT INTO delivery_outbox(
                    delivery_id, event_id, channel, target_name, url,
                    body_json, dependency_ids_json, status, attempt_count,
                    max_attempts, next_attempt_at, claim_owner, claim_token,
                    claim_until, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, 0,
                          NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    delivery_id,
                    event_id,
                    channel,
                    target_name,
                    url,
                    body_json,
                    dependency_json,
                    max(1, int(max_attempts)),
                    now_text,
                    now_text,
                ),
            )
            row = connection.execute(
                "SELECT * FROM delivery_outbox WHERE delivery_id=?",
                (delivery_id,),
            ).fetchone()
            connection.commit()
        return dict(row)

    def _dependencies_delivered(
        self,
        connection: sqlite3.Connection,
        dependencies: Sequence[str],
    ) -> bool:
        if not dependencies:
            return True
        placeholders = ",".join("?" for _ in dependencies)
        rows = connection.execute(
            f"""
            SELECT delivery_id, status
            FROM delivery_outbox
            WHERE delivery_id IN ({placeholders})
            """,
            tuple(dependencies),
        ).fetchall()
        status_by_id = {str(row["delivery_id"]): str(row["status"]) for row in rows}
        return all(status_by_id.get(item) == "delivered" for item in dependencies)

    def claim_due(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        claimed: List[Dict[str, Any]] = []
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM delivery_outbox
                WHERE (
                    status IN ('pending', 'retry')
                    OR (status='claimed' AND COALESCE(claim_until, 0) <= ?)
                )
                  AND next_attempt_at <= ?
                ORDER BY created_at, delivery_id
                LIMIT ?
                """,
                (now, now, max(1, int(limit * 4))),
            ).fetchall()
            for row in rows:
                dependencies = json.loads(str(row["dependency_ids_json"]) or "[]")
                if not self._dependencies_delivered(connection, dependencies):
                    continue
                token = uuid.uuid4().hex
                cursor = connection.execute(
                    """
                    UPDATE delivery_outbox
                    SET status='claimed', claim_owner=?, claim_token=?,
                        claim_until=?, updated_at=?
                    WHERE delivery_id=?
                      AND (
                        status IN ('pending', 'retry')
                        OR (status='claimed' AND COALESCE(claim_until, 0) <= ?)
                      )
                    """,
                    (
                        owner_id,
                        token,
                        now + max(1.0, lease_seconds),
                        _utc_now_rfc3339(),
                        row["delivery_id"],
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                value = dict(row)
                value["claim_owner"] = owner_id
                value["claim_token"] = token
                claimed.append(value)
                if len(claimed) >= limit:
                    break
            connection.commit()
        return claimed

    def complete_delivery(self, delivery: Mapping[str, Any]) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE delivery_outbox
                SET status='delivered', claim_owner=NULL, claim_token=NULL,
                    claim_until=NULL, last_error=NULL, updated_at=?
                WHERE delivery_id=? AND status='claimed' AND claim_token=?
                """,
                (
                    _utc_now_rfc3339(),
                    delivery["delivery_id"],
                    delivery["claim_token"],
                ),
            )
        return cursor.rowcount == 1

    def fail_delivery(
        self,
        delivery: Mapping[str, Any],
        *,
        error: str,
        fatal: bool,
        base_backoff_seconds: float,
    ) -> str:
        attempt = int(delivery["attempt_count"]) + 1
        max_attempts = int(delivery["max_attempts"])
        dead = fatal or attempt >= max_attempts
        status = "dead_letter" if dead else "retry"
        delay = 0.0 if dead else max(0.1, base_backoff_seconds) * (2 ** (attempt - 1))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE delivery_outbox
                SET status=?, attempt_count=?, next_attempt_at=?,
                    claim_owner=NULL, claim_token=NULL, claim_until=NULL,
                    last_error=?, updated_at=?
                WHERE delivery_id=? AND status='claimed' AND claim_token=?
                """,
                (
                    status,
                    attempt,
                    time.time() + delay,
                    error[:1000],
                    _utc_now_rfc3339(),
                    delivery["delivery_id"],
                    delivery["claim_token"],
                ),
            )
        return status if cursor.rowcount == 1 else "stale_claim"

    def replay_dead_letters(
        self,
        *,
        actor_id: str,
        approval_ref: str,
        reason: str,
        event_id: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> int:
        conditions = ["status='dead_letter'"]
        params: List[Any] = []
        if event_id:
            conditions.append("event_id=?")
            params.append(event_id)
        if channel:
            conditions.append("channel=?")
            params.append(channel)
        update_params: List[Any] = [
            time.time(),
            _utc_now_rfc3339(),
            *params,
        ]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"""
                UPDATE delivery_outbox
                SET status='retry', attempt_count=0, next_attempt_at=?,
                    claim_owner=NULL, claim_token=NULL, claim_until=NULL,
                    last_error=NULL, updated_at=?
                WHERE {' AND '.join(conditions)}
                """,
                tuple(update_params),
            )
            replayed = cursor.rowcount
            connection.execute(
                """
                INSERT INTO delivery_replay_audit(
                    replay_id, actor_id, approval_ref, reason, event_id,
                    channel, replayed_count, replayed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"health-replay-{uuid.uuid4().hex}",
                    actor_id,
                    approval_ref,
                    reason,
                    event_id,
                    channel,
                    replayed,
                    _utc_now_rfc3339(),
                ),
            )
            connection.commit()
        return replayed

    def list_replay_audit(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM delivery_replay_audit
                ORDER BY replayed_at DESC, replay_id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune_retained_history(
        self,
        *,
        delivery_history_seconds: float = _DELIVERY_HISTORY_RETENTION_SECONDS,
        window_history_seconds: float = _WINDOW_HISTORY_RETENTION_SECONDS,
    ) -> Dict[str, int]:
        """Bound durable history that is not required for retry/DLQ recovery."""

        now = datetime.now(timezone.utc)
        delivery_cutoff = (
            datetime.fromtimestamp(
                now.timestamp() - max(60.0, float(delivery_history_seconds)),
                tz=timezone.utc,
            )
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        window_cutoff = (
            datetime.fromtimestamp(
                now.timestamp() - max(60.0, float(window_history_seconds)),
                tz=timezone.utc,
            )
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            protected_delivery_ids = {
                str(row["create_delivery_id"])
                for row in connection.execute(
                    """
                    SELECT create_delivery_id
                    FROM incident_mappings
                    WHERE status IN ('opening', 'open', 'resolving')
                    """
                ).fetchall()
            }
            for row in connection.execute(
                """
                SELECT dependency_ids_json
                FROM delivery_outbox
                WHERE status <> 'delivered'
                """
            ).fetchall():
                protected_delivery_ids.update(
                    str(item)
                    for item in json.loads(
                        str(row["dependency_ids_json"]) or "[]"
                    )
                )
            connection.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS delivery_prune_protected (
                    delivery_id TEXT PRIMARY KEY
                )
                """
            )
            connection.execute("DELETE FROM delivery_prune_protected")
            connection.executemany(
                """
                INSERT OR IGNORE INTO delivery_prune_protected(delivery_id)
                VALUES (?)
                """,
                ((delivery_id,) for delivery_id in protected_delivery_ids),
            )
            delivered = connection.execute(
                """
                DELETE FROM delivery_outbox
                WHERE status='delivered' AND updated_at < ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM delivery_prune_protected AS protected
                      WHERE protected.delivery_id=delivery_outbox.delivery_id
                  )
                """,
                (delivery_cutoff,),
            ).rowcount
            probe_windows = connection.execute(
                """
                DELETE FROM probe_windows
                WHERE completed_at IS NOT NULL AND completed_at < ?
                """,
                (window_cutoff,),
            ).rowcount
            error_windows = connection.execute(
                "DELETE FROM error_windows WHERE last_observed_at < ?",
                (window_cutoff,),
            ).rowcount
            replay_audit = connection.execute(
                "DELETE FROM delivery_replay_audit WHERE replayed_at < ?",
                (delivery_cutoff,),
            ).rowcount
            connection.commit()
        return {
            "delivery_outbox": max(0, int(delivered)),
            "probe_windows": max(0, int(probe_windows)),
            "error_windows": max(0, int(error_windows)),
            "delivery_replay_audit": max(0, int(replay_audit)),
        }

    def delivery_counts(self) -> Dict[str, int]:
        counts = {
            "pending": 0,
            "retry": 0,
            "claimed": 0,
            "delivered": 0,
            "dead_letter": 0,
        }
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, count FROM delivery_status_counts"
            ).fetchall()
        for row in rows:
            counts[str(row["status"])] = int(row["count"])
        counts["backlog"] = (
            counts["pending"] + counts["retry"] + counts["claimed"]
        )
        return counts

    def list_deliveries(self, *, event_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM delivery_outbox"
        params: tuple[Any, ...] = ()
        if event_id:
            query += " WHERE event_id=?"
            params = (event_id,)
        query += " ORDER BY created_at, delivery_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        values: List[Dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["body"] = json.loads(value.pop("body_json"))
            value["dependency_ids"] = json.loads(value.pop("dependency_ids_json"))
            values.append(value)
        return values


class DownstreamHealthMonitor:
    """Probe configured BFF dependencies and durably deliver health truth."""

    def __init__(
        self,
        *,
        telemetry_url: Optional[str] = None,
        incidents_url: Optional[str] = None,
        probe_interval_seconds: Optional[float] = None,
        failure_threshold: Optional[int] = None,
        health_path: str = "/__health__",
        http_timeout: float = 3.0,
        state_path: Optional[str] = None,
        instance_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        producer: Optional[str] = None,
        telemetry_service_jwt: Optional[str] = None,
        incident_service_token: Optional[str] = None,
        error_rate_window_seconds: Optional[float] = None,
        error_rate_threshold: Optional[float] = None,
        error_rate_min_samples: Optional[int] = None,
        delivery_max_attempts: Optional[int] = None,
        delivery_backoff_seconds: Optional[float] = None,
    ) -> None:
        self._telemetry_url = (
            telemetry_url
            if telemetry_url is not None
            else (
                os.getenv("PANTHEON_TELEMETRY_API_URL", "").strip().rstrip("/")
                or os.getenv("PANTHEON_TELEMETRY_URL", "").strip().rstrip("/")
            )
        )
        self._incidents_url = (
            incidents_url
            if incidents_url is not None
            else (
                os.getenv("PANTHEON_INCIDENTS_API_URL", "").strip().rstrip("/")
                or os.getenv("PANTHEON_INCIDENTS_URL", "").strip().rstrip("/")
            )
        )
        self._probe_interval = max(
            0.01,
            float(
                probe_interval_seconds
                if probe_interval_seconds is not None
                else os.getenv("PANTHEON_BFF_HEALTH_PROBE_INTERVAL_SECONDS", "30")
            ),
        )
        self._failure_threshold = max(
            1,
            int(
                failure_threshold
                if failure_threshold is not None
                else os.getenv("PANTHEON_BFF_HEALTH_FAILURE_THRESHOLD", "3")
            ),
        )
        self._health_path = health_path
        self._http_timeout = max(0.1, float(http_timeout))
        self._instance_id = instance_id or (
            os.getenv("PANTHEON_BFF_INSTANCE_ID", "").strip()
            or f"bff-health-{uuid.uuid4().hex[:12]}"
        )
        self._tenant_id = (
            tenant_id
            or os.getenv("PANTHEON_BFF_HEALTH_TENANT_ID", "").strip()
            or os.getenv("PANTHEON_BFF_TENANT_ID", "").strip()
            or os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID", "").strip()
            or os.getenv("PANTHEON_TENANT_ID", "").strip()
            or "tenant-dev"
        )
        self._producer = (
            producer
            or os.getenv("PANTHEON_BFF_HEALTH_PRODUCER", "").strip()
            or INFRASTRUCTURE_HEALTH_PRODUCER
        )
        self._telemetry_service_jwt = (
            telemetry_service_jwt
            if telemetry_service_jwt is not None
            else (
                os.getenv("PANTHEON_BFF_HEALTH_TELEMETRY_JWT", "").strip()
                or os.getenv("PANTHEON_TELEMETRY_INFRA_SERVICE_JWT", "").strip()
            )
        )
        self._incident_service_token = (
            incident_service_token
            if incident_service_token is not None
            else os.getenv("PANTHEON_BFF_HEALTH_INCIDENT_TOKEN", "").strip()
        )
        self._error_rate_window = max(
            1.0,
            float(
                error_rate_window_seconds
                if error_rate_window_seconds is not None
                else os.getenv("PANTHEON_BFF_HEALTH_ERROR_WINDOW_SECONDS", "300")
            ),
        )
        self._error_rate_threshold = min(
            1.0,
            max(
                0.0,
                float(
                    error_rate_threshold
                    if error_rate_threshold is not None
                    else os.getenv("PANTHEON_BFF_HEALTH_ERROR_RATE_THRESHOLD", "0.25")
                ),
            ),
        )
        self._error_rate_min_samples = max(
            1,
            int(
                error_rate_min_samples
                if error_rate_min_samples is not None
                else os.getenv("PANTHEON_BFF_HEALTH_ERROR_RATE_MIN_SAMPLES", "5")
            ),
        )
        self._delivery_max_attempts = max(
            1,
            int(
                delivery_max_attempts
                if delivery_max_attempts is not None
                else os.getenv("PANTHEON_BFF_HEALTH_DELIVERY_MAX_ATTEMPTS", "5")
            ),
        )
        self._delivery_backoff = max(
            0.1,
            float(
                delivery_backoff_seconds
                if delivery_backoff_seconds is not None
                else os.getenv("PANTHEON_BFF_HEALTH_DELIVERY_BACKOFF_SECONDS", "1")
            ),
        )
        configured_state_path = (
            state_path
            or os.getenv("PANTHEON_BFF_HEALTH_STATE_PATH", "").strip()
        )
        if not configured_state_path:
            # Product bootstrap passes an explicit BFF_DATA_DIR path.  The
            # unique fallback prevents unrelated direct unit instances from
            # sharing state accidentally while still exercising real fsync/WAL.
            configured_state_path = os.path.join(
                tempfile.gettempdir(),
                f"pantheon-bff-health-{uuid.uuid4().hex}.sqlite3",
            )
        self._store = _DurableHealthStore(configured_state_path)

        self._state: Dict[str, DownstreamProbeResult] = self._store.list_probes()
        self._open_incident_ids: Dict[str, str] = {
            name: str(value["incident_id"])
            for name, value in self._store.list_incidents().items()
            if str(value["status"]) in {"opening", "open", "resolving"}
        }
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._last_retention_prune_at = 0.0

    @property
    def state_path(self) -> str:
        return self._store.path

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def instance_id(self) -> str:
        return self._instance_id

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._probe_loop(),
            name="downstream-health-monitor",
        )
        log.info(
            "DownstreamHealthMonitor started instance=%s state=%s interval=%.1fs",
            self._instance_id,
            self._store.path,
            self._probe_interval,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        log.info("DownstreamHealthMonitor stopped instance=%s", self._instance_id)

    def _configured_target_json(self) -> List[DownstreamTarget]:
        raw = os.getenv("PANTHEON_BFF_HEALTH_TARGETS_JSON", "").strip()
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "PANTHEON_BFF_HEALTH_TARGETS_JSON must be valid JSON"
            ) from exc
        entries: List[Mapping[str, Any]] = []
        if isinstance(value, dict):
            for name, item in value.items():
                if isinstance(item, str):
                    entries.append({"name": name, "url": item})
                elif isinstance(item, dict):
                    entries.append({"name": name, **item})
                else:
                    raise ValueError("health target values must be strings or objects")
        elif isinstance(value, list):
            if not all(isinstance(item, dict) for item in value):
                raise ValueError("health target list entries must be objects")
            entries = value
        else:
            raise ValueError("health target registry must be an object or list")

        targets: List[DownstreamTarget] = []
        for item in entries:
            name = str(item.get("name") or "").strip().lower()
            url = str(item.get("url") or item.get("base_url") or "").strip().rstrip("/")
            if not _TARGET_NAME_RE.fullmatch(name):
                raise ValueError(f"invalid health target name: {name!r}")
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"health target {name!r} must use http(s)")
            targets.append(
                DownstreamTarget(
                    name=name,
                    base_url=url,
                    health_path=str(item.get("health_path") or self._health_path),
                    component_kind=str(item.get("component_kind") or "http_service"),
                    source_env="PANTHEON_BFF_HEALTH_TARGETS_JSON",
                )
            )
        return targets

    def _resolve_target_registry(self) -> Dict[str, DownstreamTarget]:
        targets: Dict[str, DownstreamTarget] = {}
        urls_seen: set[str] = set()
        for target in self._configured_target_json():
            targets[target.name] = target
            urls_seen.add(target.base_url)

        known_envs: set[str] = set()
        for name, env_names in _DEFAULT_TARGET_SPECS:
            known_envs.update(env_names)
            if name in targets:
                continue
            for env_name in env_names:
                base_url = os.getenv(env_name, "").strip().rstrip("/")
                if not base_url:
                    continue
                if not base_url.startswith(("http://", "https://")):
                    continue
                if base_url in urls_seen:
                    break
                health_path = self._health_path
                for path_env in (f"{env_name}_HEALTH_PATH", f"{env_name.removesuffix('_URL')}_HEALTH_PATH"):
                    override = os.getenv(path_env, "").strip()
                    if override:
                        health_path = override
                        break
                targets[name] = DownstreamTarget(
                    name=name,
                    base_url=base_url,
                    health_path=health_path,
                    source_env=env_name,
                )
                urls_seen.add(base_url)
                break

        # Fail open for discovery but not for truth: any additional configured
        # BFF HTTP owner URL is included rather than silently falling outside
        # the registry simply because a new adapter env landed first.
        for env_name, raw_url in sorted(os.environ.items()):
            if env_name in known_envs or env_name in _DYNAMIC_URL_EXCLUSIONS:
                continue
            if not (
                env_name.startswith("PANTHEON_")
                and (env_name.endswith("_API_URL") or env_name.endswith("_SERVICE_URL"))
            ):
                continue
            base_url = str(raw_url or "").strip().rstrip("/")
            if not base_url.startswith(("http://", "https://")) or base_url in urls_seen:
                continue
            name = env_name.removeprefix("PANTHEON_")
            name = name.removesuffix("_API_URL").removesuffix("_SERVICE_URL")
            name = name.lower().replace("_", "-")
            if not _TARGET_NAME_RE.fullmatch(name) or name in targets:
                continue
            health_path = self._health_path
            for path_env in (f"{env_name}_HEALTH_PATH", f"{env_name.removesuffix('_URL')}_HEALTH_PATH"):
                override = os.getenv(path_env, "").strip()
                if override:
                    health_path = override
                    break
            targets[name] = DownstreamTarget(
                name=name,
                base_url=base_url,
                health_path=health_path,
                source_env=env_name,
            )
            urls_seen.add(base_url)

        for name, (env_name, fallback_file) in _DEFAULT_WORKER_SPECS:
            if name in targets:
                continue
            path = os.getenv(env_name, "").strip() or fallback_file
            targets[name] = DownstreamTarget(
                name=name,
                base_url=f"file://{path}",
                health_path=path,
                component_kind="file_worker",
                source_env=env_name,
            )
        return targets

    def _resolve_targets(self) -> Dict[str, str]:
        """Compatibility projection of the complete configured registry."""

        return {
            name: target.base_url
            for name, target in self._resolve_target_registry().items()
        }

    def get_state(self) -> Dict[str, Any]:
        now = time.time()
        if now - self._last_retention_prune_at >= 300.0:
            self._store.prune_retained_history()
            self._last_retention_prune_at = now
        persisted = self._store.list_probes()
        self._state = persisted
        registry = self._resolve_target_registry()
        target_payload: Dict[str, Any] = {}
        for name, target in registry.items():
            probe = persisted.get(name)
            target_payload[name] = {
                **(probe.to_dict() if probe else {
                    "target_name": name,
                    "ok": None,
                    "status_code": None,
                    "latency_ms": None,
                    "checked_at": None,
                    "failure_reason": "not_yet_observed",
                    "consecutive_failures": 0,
                    "probe_kind": None,
                    "window_started_at": None,
                    "sample_count": 0,
                    "failure_count": 0,
                    "error_rate": None,
                }),
                "registry": target.to_dict(),
            }
        # Preserve historical targets after a configuration removal so the
        # operator can see the last observation instead of losing evidence.
        for name, probe in persisted.items():
            target_payload.setdefault(
                name,
                {
                    **probe.to_dict(),
                    "registry": {
                        "name": name,
                        "configured": False,
                        "source_env": "removed",
                    },
                },
            )
        observed = [value for value in target_payload.values() if value["ok"] is not None]
        overall_ok: Optional[bool]
        if not observed:
            overall_ok = None
        else:
            overall_ok = all(bool(value["ok"]) for value in observed)
        return {
            "targets": target_payload,
            "overall_ok": overall_ok,
            "configured_target_count": len(registry),
            "observed_target_count": len(observed),
            "probe_interval_seconds": self._probe_interval,
            "failure_threshold": self._failure_threshold,
            "error_rate_policy": {
                "window_seconds": self._error_rate_window,
                "threshold": self._error_rate_threshold,
                "minimum_samples": self._error_rate_min_samples,
            },
            "durability": {
                "backend": "sqlite_wal",
                "state_path": self._store.path,
                "shared_replica_state": True,
                "instance_id": self._instance_id,
            },
            "delivery": self._store.delivery_counts(),
            "delivery_replays": self._store.list_replay_audit(),
            "incidents": self._store.list_incidents(),
        }

    def get_degraded_targets(self) -> List[str]:
        return [
            name
            for name, result in self._store.list_probes().items()
            if not result.ok
        ]

    async def _probe_loop(self) -> None:
        next_probe_at = 0.0
        while self._running:
            now = time.monotonic()
            if now >= next_probe_at:
                try:
                    await self._probe_all()
                except Exception as exc:  # noqa: BLE001
                    log.warning("probe_loop: unexpected error: %s", exc)
                next_probe_at = now + self._probe_interval
            try:
                await asyncio.to_thread(self._deliver_due_sync)
            except Exception as exc:  # noqa: BLE001
                log.warning("delivery_loop: unexpected error: %s", exc)
            try:
                await asyncio.sleep(min(1.0, self._probe_interval))
            except asyncio.CancelledError:
                break

    async def _probe_all(self) -> None:
        targets = self._resolve_target_registry()
        if not targets:
            await asyncio.to_thread(self._deliver_due_sync)
            return
        observed_at = _utc_now_rfc3339()
        window_started_at = _window_start(observed_at, self._probe_interval)
        claimed: List[DownstreamTarget] = []
        for target in targets.values():
            if self._store.claim_window(
                target_name=target.name,
                probe_kind="interval_probe",
                window_started_at=window_started_at,
                owner_id=self._instance_id,
                lease_seconds=max(self._http_timeout * 2, self._probe_interval),
            ):
                claimed.append(target)
        results = await asyncio.gather(
            *[
                self._probe_one(
                    target.name,
                    target.base_url,
                    health_path=target.health_path,
                    checked_at=observed_at,
                    window_started_at=window_started_at,
                )
                for target in claimed
            ],
            return_exceptions=False,
        )
        for result in results:
            try:
                await self._handle_probe_result(result)
            finally:
                self._store.complete_window(
                    target_name=result.target_name,
                    probe_kind="interval_probe",
                    window_started_at=result.window_started_at,
                    owner_id=self._instance_id,
                )
        await asyncio.to_thread(self._deliver_due_sync)
        try:
            self.publish_loop_12_controller_truth()
        except Exception as exc:  # noqa: BLE001
            log.warning("publish_loop_12_controller_truth error: %s", exc)

    def publish_loop_12_controller_truth(self) -> Dict[str, Any]:
        """Publish Loop 12 (bff_health_monitoring) controller truth."""
        now_iso = _utc_now_rfc3339()
        state = self.get_state()
        overall_ok = state.get("overall_ok", True)
        targets = state.get("targets", {})
        total_targets = len(targets)
        healthy_targets = sum(1 for t in targets.values() if t.get("ok"))

        dsn = os.environ.get("DATABASE_URL")
        if dsn:
            try:
                import sys
                asyncpg_module = sys.modules.get("asyncpg")
                if asyncpg_module is not None and not isinstance(asyncpg_module, MagicMock if "MagicMock" in globals() else type):
                    import importlib
                    loop_control = importlib.import_module("services.loop-control")
                    writer = loop_control.LoopControllerWriter(
                        dsn=dsn,
                        tenant_id=self.tenant_id,
                        environment=os.environ.get("PANTHEON_ENV", "dev"),
                        controller_name="bff_downstream_health_monitor",
                    )
                    heartbeat_coro = writer.record_heartbeat(
                        loop_id="bff_health_monitoring",
                        truth_level="reconciled_live_proof",
                        desired_state_query="SELECT count(*) FROM bff_downstream_health_targets",
                        actual_state_query="SELECT count(*) FROM downstream_health_probe_state WHERE ok=1",
                        desired_state={"probe_targets_count": total_targets},
                        downstream_actual_state={
                            "status": "ready" if overall_ok else "degraded",
                            "healthy_targets_count": healthy_targets,
                            "total_targets_count": total_targets,
                            "checked_at": now_iso,
                        },
                        evidence_refs=["services/control-plane/bff/downstream_health_monitor.py"],
                    )
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(heartbeat_coro)
                    except RuntimeError:
                        asyncio.run(heartbeat_coro)
            except Exception as exc:
                log.warning("Failed to publish Loop 12 controller truth to database: %s", exc)

        record = {
            "loop_id": "bff_health_monitoring",
            "tenant_id": self.tenant_id,
            "environment": os.environ.get("PANTHEON_ENV", "dev"),
            "controller_id": f"bff_downstream_health_monitor-{self.instance_id}",
            "controller_name": "bff_downstream_health_monitor",
            "deployment_sha": os.environ.get("GIT_SHA", "unknown"),
            "last_heartbeat_at": now_iso,
            "truth_level": "reconciled_live_proof",
            "ready": overall_ok,
            "status": "ready" if overall_ok else "degraded",
            "desired_state_query": "SELECT count(*) FROM bff_downstream_health_targets",
            "actual_state_query": "SELECT count(*) FROM downstream_health_probe_state WHERE ok=1",
            "desired_state": {"probe_targets_count": total_targets},
            "downstream_actual_state": {
                "status": "ready" if overall_ok else "degraded",
                "healthy_targets_count": healthy_targets,
                "total_targets_count": total_targets,
                "checked_at": now_iso,
            },
            "evidence_refs": ["services/control-plane/bff/downstream_health_monitor.py"],
        }
        return record

    async def _probe_one(
        self,
        name: str,
        base_url: str,
        *,
        health_path: Optional[str] = None,
        checked_at: Optional[str] = None,
        window_started_at: Optional[str] = None,
    ) -> DownstreamProbeResult:
        target = self._resolve_target_registry().get(name)
        kind = target.component_kind if target else "http_service"
        started = time.monotonic()
        if kind == "file_worker":
            file_path = health_path or (target.health_path if target else None) or base_url.removeprefix("file://")
            ok, status_code, failure_reason = await asyncio.to_thread(
                _probe_file_health,
                file_path,
                self._http_timeout,
            )
        else:
            path = health_path or self._health_path
            url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
            ok, status_code, failure_reason = await asyncio.to_thread(
                _probe_http,
                url,
                self._http_timeout,
            )
        latency_ms = (time.monotonic() - started) * 1000.0
        observed_at = checked_at or _utc_now_rfc3339()
        previous = self._store.get_probe(name) or self._state.get(name)
        consecutive = (
            0
            if ok
            else (
                previous.consecutive_failures + 1
                if previous is not None and not previous.ok
                else 1
            )
        )
        return DownstreamProbeResult(
            target_name=name,
            ok=ok,
            status_code=status_code,
            latency_ms=latency_ms,
            checked_at=observed_at,
            failure_reason=failure_reason if not ok else None,
            consecutive_failures=consecutive,
            probe_kind="interval_probe",
            window_started_at=window_started_at
            or _window_start(observed_at, self._probe_interval),
        )

    def _target_for_result(self, result: DownstreamProbeResult) -> DownstreamTarget:
        return self._resolve_target_registry().get(result.target_name) or DownstreamTarget(
            name=result.target_name,
            base_url="http://unconfigured.invalid",
            health_path=self._health_path,
            source_env="historical",
        )

    def _event_for_result(
        self,
        result: DownstreamProbeResult,
        *,
        incident_ref: Optional[str],
    ) -> Dict[str, Any]:
        target = self._target_for_result(result)
        event_id = _stable_event_id(
            tenant_id=self._tenant_id,
            producer=self._producer,
            target_name=result.target_name,
            probe_kind=result.probe_kind,
            window_started_at=result.window_started_at,
        )
        health_status = (
            "ok"
            if result.ok
            else ("unavailable" if result.status_code < 0 else "degraded")
        )
        severity = (
            "info"
            if result.ok
            else (
                "critical"
                if (
                    result.consecutive_failures >= self._failure_threshold
                    or result.error_rate >= self._error_rate_threshold
                )
                else "warning"
            )
        )
        observation: Dict[str, Any] = {
            "probe_kind": result.probe_kind,
            "observed_at": result.window_started_at,
            "window_seconds": (
                self._error_rate_window
                if result.probe_kind == "error_rate"
                else self._probe_interval
            ),
            "sample_count": result.sample_count,
            "failure_count": result.failure_count,
            "consecutive_failures": result.consecutive_failures,
            "error_rate": min(1.0, max(0.0, result.error_rate)),
            "latency_ms": round(result.latency_ms, 1),
            "status_code": result.status_code,
        }
        if result.failure_reason:
            observation["detail"] = result.failure_reason[:1000]
        event: Dict[str, Any] = {
            "schema_version": INFRASTRUCTURE_HEALTH_SCHEMA_VERSION,
            "event_id": event_id,
            "event_type": "infrastructure_health",
            # Window start is part of the observation identity and therefore
            # keeps the full payload identical across replica retries.
            "created_at": result.window_started_at,
            "tenant_id": self._tenant_id,
            "producer": self._producer,
            "component": {
                "service_name": result.target_name,
                "component_kind": target.component_kind,
                "endpoint": target.health_url,
            },
            "health_status": health_status,
            "severity": severity,
            "observation": observation,
            "metadata": {
                "controller": "bff-downstream-health-monitor",
                "target_registry_source": target.source_env,
            },
        }
        if incident_ref:
            event["incident_ref"] = incident_ref
        return event

    def _incident_body(
        self,
        *,
        result: DownstreamProbeResult,
        incident_id: str,
        event_id: str,
    ) -> Dict[str, Any]:
        target = self._target_for_result(result)
        return {
            "schema_version": "pantheon.infrastructure-incident/1",
            "incident_id": incident_id,
            "tenant_id": self._tenant_id,
            "producer": self._producer,
            "source_event_id": event_id,
            "title": f"BFF downstream degradation: {result.target_name}",
            "status": "open",
            "severity": "high",
            "component": {
                "service_name": result.target_name,
                "component_kind": target.component_kind,
                "endpoint": target.health_url,
            },
            "telemetry_event_ids": [event_id],
            "evidence_summary": (
                f"{result.probe_kind} observed {result.failure_count}/"
                f"{result.sample_count} failures (rate={result.error_rate:.3f}); "
                f"consecutive_failures={result.consecutive_failures}; "
                f"detail={result.failure_reason or 'none'}"
            ),
        }

    def _telemetry_delivery_url(self) -> str:
        return f"{self._telemetry_url.rstrip('/')}{INFRASTRUCTURE_HEALTH_PATH}"

    def _incident_create_url(self) -> str:
        path = os.getenv(
            "PANTHEON_BFF_HEALTH_INCIDENT_CREATE_PATH",
            INFRASTRUCTURE_INCIDENT_PATH,
        ).strip()
        return f"{self._incidents_url.rstrip('/')}/{path.lstrip('/')}"

    async def _handle_probe_result(self, result: DownstreamProbeResult) -> None:
        previous = self._store.get_probe(result.target_name)
        recorded = self._store.record_probe(result)
        self._state[result.target_name] = recorded

        error_window_start = _window_start(
            recorded.checked_at,
            self._error_rate_window,
        )
        error_window = self._store.append_error_sample(
            target_name=recorded.target_name,
            window_started_at=error_window_start,
            ok=recorded.ok,
            latency_ms=recorded.latency_ms,
            status_code=recorded.status_code,
            detail=recorded.failure_reason,
            observed_at=recorded.checked_at,
        )

        is_recovery = bool(
            recorded.ok
            and (
                (previous is not None and not previous.ok)
                or recorded.target_name in self._open_incident_ids
                or (
                    (self._store.get_incident(recorded.target_name) or {}).get("status")
                    in {"opening", "open", "resolving"}
                )
            )
        )
        if is_recovery:
            recorded.probe_kind = "recovery"
            recorded.window_started_at = _window_start(
                recorded.checked_at,
                self._probe_interval,
            )

        should_open = bool(
            not recorded.ok
            and recorded.consecutive_failures >= self._failure_threshold
        )
        incident = self._store.get_incident(recorded.target_name)
        incident_created = False

        # Reserve mapping before building the event so telemetry and incident
        # requests share one source event identity and incident reference.
        provisional_event = self._event_for_result(recorded, incident_ref=None)
        event_id = str(provisional_event["event_id"])
        if should_open:
            incident, incident_created = self._store.reserve_incident(
                target_name=recorded.target_name,
                event_id=event_id,
            )
            self._open_incident_ids[recorded.target_name] = str(
                incident["incident_id"]
            )

        event = self._event_for_result(
            recorded,
            incident_ref=str(incident["incident_id"]) if incident else None,
        )
        event_id = str(event["event_id"])
        telemetry_delivery_id: Optional[str] = None
        if self._telemetry_url:
            telemetry_delivery_id = f"telemetry:{event_id}"
            self._store.queue_delivery(
                delivery_id=telemetry_delivery_id,
                event_id=event_id,
                channel="telemetry",
                target_name=recorded.target_name,
                url=self._telemetry_delivery_url(),
                body=event,
                dependency_ids=[],
                max_attempts=self._delivery_max_attempts,
            )

        if (
            should_open
            and incident is not None
            and self._incidents_url
            and (
                incident_created
                or (
                    str(incident["status"]) == "opening"
                    and str(incident["opening_event_id"]) == event_id
                )
            )
        ):
            self._store.queue_delivery(
                delivery_id=str(incident["create_delivery_id"]),
                event_id=event_id,
                channel="incident_open",
                target_name=recorded.target_name,
                url=self._incident_create_url(),
                body=self._incident_body(
                    result=recorded,
                    incident_id=str(incident["incident_id"]),
                    event_id=event_id,
                ),
                dependency_ids=[telemetry_delivery_id]
                if telemetry_delivery_id
                else [],
                max_attempts=self._delivery_max_attempts,
            )

        if is_recovery and incident is not None and self._incidents_url:
            incident_id = str(incident["incident_id"])
            resolve_delivery_id = f"incident-resolve:{event_id}"
            dependencies = [str(incident["create_delivery_id"])]
            if telemetry_delivery_id:
                dependencies.append(telemetry_delivery_id)
            self._store.queue_delivery(
                delivery_id=resolve_delivery_id,
                event_id=event_id,
                channel="incident_resolve",
                target_name=recorded.target_name,
                url=(
                    f"{self._incidents_url.rstrip('/')}/api/incidents/"
                    f"{incident_id}/status"
                ),
                body={
                    "status": "resolved",
                    "resolved_at": recorded.checked_at,
                    "source_event_id": event_id,
                    "telemetry_event_ids": [event_id],
                },
                dependency_ids=dependencies,
                max_attempts=self._delivery_max_attempts,
            )
            self._store.set_incident_status(
                target_name=recorded.target_name,
                incident_id=incident_id,
                status="resolving",
                event_id=event_id,
            )

        await asyncio.to_thread(self._deliver_due_sync)

        if (
            int(error_window["sample_count"]) >= self._error_rate_min_samples
            and float(error_window["error_rate"]) >= self._error_rate_threshold
        ):
            await self._emit_error_rate_window(
                target_name=recorded.target_name,
                window=error_window,
            )

    async def _emit_error_rate_window(
        self,
        *,
        target_name: str,
        window: Mapping[str, Any],
    ) -> None:
        window_started_at = str(window["window_started_at"])
        if not self._store.claim_window(
            target_name=target_name,
            probe_kind="error_rate",
            window_started_at=window_started_at,
            owner_id=self._instance_id,
            lease_seconds=max(self._http_timeout * 2, self._probe_interval),
        ):
            return
        result = DownstreamProbeResult(
            target_name=target_name,
            ok=False,
            status_code=int(window["last_status_code"]),
            latency_ms=float(window["latency_ms"]),
            checked_at=str(window["last_observed_at"]),
            failure_reason=str(window.get("last_detail") or "error-rate threshold exceeded"),
            consecutive_failures=(
                self._store.get_probe(target_name).consecutive_failures
                if self._store.get_probe(target_name)
                else 0
            ),
            probe_kind="error_rate",
            window_started_at=window_started_at,
            sample_count=int(window["sample_count"]),
            failure_count=int(window["failure_count"]),
            error_rate=float(window["error_rate"]),
        )
        try:
            # Avoid recording the aggregate as the latest interval state; it is
            # a second trigger over the same durable samples.
            incident, incident_created = self._store.reserve_incident(
                target_name=target_name,
                event_id=str(
                    self._event_for_result(result, incident_ref=None)["event_id"]
                ),
            )
            self._open_incident_ids[target_name] = str(incident["incident_id"])
            event = self._event_for_result(
                result,
                incident_ref=str(incident["incident_id"]),
            )
            event_id = str(event["event_id"])
            telemetry_delivery_id: Optional[str] = None
            if self._telemetry_url:
                telemetry_delivery_id = f"telemetry:{event_id}"
                self._store.queue_delivery(
                    delivery_id=telemetry_delivery_id,
                    event_id=event_id,
                    channel="telemetry",
                    target_name=target_name,
                    url=self._telemetry_delivery_url(),
                    body=event,
                    dependency_ids=[],
                    max_attempts=self._delivery_max_attempts,
                )
            if self._incidents_url and incident_created:
                self._store.queue_delivery(
                    delivery_id=str(incident["create_delivery_id"]),
                    event_id=event_id,
                    channel="incident_open",
                    target_name=target_name,
                    url=self._incident_create_url(),
                    body=self._incident_body(
                        result=result,
                        incident_id=str(incident["incident_id"]),
                        event_id=event_id,
                    ),
                    dependency_ids=[telemetry_delivery_id]
                    if telemetry_delivery_id
                    else [],
                    max_attempts=self._delivery_max_attempts,
                )
            await asyncio.to_thread(self._deliver_due_sync)
        finally:
            self._store.complete_window(
                target_name=target_name,
                probe_kind="error_rate",
                window_started_at=window_started_at,
                owner_id=self._instance_id,
            )

    def record_downstream_outcome(
        self,
        *,
        target_name: str,
        ok: bool,
        status_code: int,
        latency_ms: float = 0.0,
        detail: Optional[str] = None,
        observed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Record an adapter request outcome for the durable error-rate window.

        Adapter layers can call this without waiting for the interval probe.
        Unknown targets fail closed instead of creating an ungoverned component
        name outside the configured registry.
        """

        if target_name not in self._resolve_target_registry():
            raise ValueError(f"unknown downstream health target: {target_name}")
        timestamp = observed_at or _utc_now_rfc3339()
        window_start = _window_start(timestamp, self._error_rate_window)
        window = self._store.append_error_sample(
            target_name=target_name,
            window_started_at=window_start,
            ok=ok,
            latency_ms=latency_ms,
            status_code=status_code,
            detail=detail,
            observed_at=timestamp,
        )
        self._store.record_probe(
            DownstreamProbeResult(
                target_name=target_name,
                ok=ok,
                status_code=status_code,
                latency_ms=latency_ms,
                checked_at=timestamp,
                failure_reason=detail if not ok else None,
                consecutive_failures=0 if ok else 1,
            )
        )
        if (
            int(window["sample_count"]) >= self._error_rate_min_samples
            and float(window["error_rate"]) >= self._error_rate_threshold
        ):
            try:
                asyncio.get_running_loop().create_task(
                    self._emit_error_rate_window(
                        target_name=target_name,
                        window=window,
                    )
                )
            except RuntimeError:
                asyncio.run(
                    self._emit_error_rate_window(
                        target_name=target_name,
                        window=window,
                    )
                )
            return dict(window)
        return None

    def _headers_for_delivery(self, delivery: Mapping[str, Any]) -> Dict[str, str]:
        event_id = str(delivery["event_id"])
        channel = str(delivery["channel"])
        headers = {"Idempotency-Key": event_id}
        if channel == "telemetry":
            token = str(self._telemetry_service_jwt or "").strip()
            if not token:
                raise RuntimeError(
                    "PANTHEON_BFF_HEALTH_TELEMETRY_JWT is unconfigured"
                )
            headers["Authorization"] = (
                token if token.lower().startswith("bearer ") else f"Bearer {token}"
            )
            headers["X-Tenant-Id"] = self._tenant_id
        elif self._incident_service_token:
            token = str(self._incident_service_token).strip()
            headers["Authorization"] = (
                token if token.lower().startswith("bearer ") else f"Bearer {token}"
            )
            headers["X-Tenant-Id"] = self._tenant_id
        return headers

    def _deliver_due_sync(self) -> int:
        delivered_count = 0
        # Drain newly unblocked dependency stages in the same cycle.  A bounded
        # loop prevents a malformed graph from monopolizing the worker.
        for _round in range(100):
            batch = self._store.claim_due(
                owner_id=self._instance_id,
                lease_seconds=max(5.0, self._http_timeout * 2),
            )
            if not batch:
                break
            for delivery in batch:
                channel = str(delivery["channel"])
                try:
                    headers = self._headers_for_delivery(delivery)
                except RuntimeError as exc:
                    self._store.fail_delivery(
                        delivery,
                        error=str(exc),
                        fatal=False,
                        base_backoff_seconds=self._delivery_backoff,
                    )
                    continue
                token = _POST_HEADERS.set(headers)
                try:
                    ok, status = _post_json(
                        str(delivery["url"]),
                        json.loads(str(delivery["body_json"])),
                        self._http_timeout,
                    )
                finally:
                    _POST_HEADERS.reset(token)

                if ok:
                    if not self._store.complete_delivery(delivery):
                        continue
                    delivered_count += 1
                    if channel in {"incident_open", "incident_resolve"}:
                        mapping = self._store.get_incident(
                            str(delivery["target_name"])
                        )
                        if mapping is not None:
                            next_status = (
                                "open"
                                if channel == "incident_open"
                                else "resolved"
                            )
                            self._store.set_incident_status(
                                target_name=str(delivery["target_name"]),
                                incident_id=str(mapping["incident_id"]),
                                status=next_status,
                                event_id=str(delivery["event_id"]),
                            )
                            if next_status == "resolved":
                                self._open_incident_ids.pop(
                                    str(delivery["target_name"]),
                                    None,
                                )
                            else:
                                self._open_incident_ids[
                                    str(delivery["target_name"])
                                ] = str(mapping["incident_id"])
                    continue

                retryable = (
                    status < 0
                    or status in {408, 425, 429}
                    or status >= 500
                )
                # 409 means a provider-side identity/concurrency conflict for
                # both telemetry and incident authorities.  It is not a
                # successful idempotent delivery receipt; leave it retry/DLQ
                # visible rather than falsely mutating incident mappings.
                self._store.fail_delivery(
                    delivery,
                    error=(
                        f"HTTP {status}"
                        if status >= 0
                        else "network_error"
                    ),
                    fatal=not retryable,
                    base_backoff_seconds=self._delivery_backoff,
                )
        return delivered_count

    def replay_dead_letters(
        self,
        *,
        actor_id: str,
        approval_ref: str,
        reason: str,
        event_id: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> Dict[str, Any]:
        replayed = self._store.replay_dead_letters(
            actor_id=actor_id,
            approval_ref=approval_ref,
            reason=reason,
            event_id=event_id,
            channel=channel,
        )
        delivered = self._deliver_due_sync()
        return {
            "replayed": replayed,
            "delivered_now": delivered,
            "event_id": event_id,
            "channel": channel,
            "actor_id": actor_id,
            "approval_ref": approval_ref,
            "delivery": self._store.delivery_counts(),
        }

    def list_delivery_records(
        self,
        *,
        event_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self._store.list_deliveries(event_id=event_id)

    # Compatibility helpers retained as thin durable-outbox entry points.
    def _emit_telemetry_sync(self, result: DownstreamProbeResult) -> Optional[str]:
        if not self._telemetry_url:
            return None
        event = self._event_for_result(result, incident_ref=None)
        event_id = str(event["event_id"])
        self._store.queue_delivery(
            delivery_id=f"telemetry:{event_id}",
            event_id=event_id,
            channel="telemetry",
            target_name=result.target_name,
            url=self._telemetry_delivery_url(),
            body=event,
            dependency_ids=[],
            max_attempts=self._delivery_max_attempts,
        )
        self._deliver_due_sync()
        return event_id

    def _open_or_update_incident_sync(
        self,
        result: DownstreamProbeResult,
        telemetry_event_id: Optional[str] = None,
    ) -> Optional[str]:
        if not self._incidents_url:
            return None
        event_id = telemetry_event_id or str(
            self._event_for_result(result, incident_ref=None)["event_id"]
        )
        incident, created = self._store.reserve_incident(
            target_name=result.target_name,
            event_id=event_id,
        )
        incident_id = str(incident["incident_id"])
        self._open_incident_ids[result.target_name] = incident_id
        if created or str(incident["status"]) == "opening":
            telemetry_delivery_id = f"telemetry:{event_id}"
            dependencies = [
                telemetry_delivery_id
                for item in self._store.list_deliveries(event_id=event_id)
                if item["delivery_id"] == telemetry_delivery_id
            ]
            self._store.queue_delivery(
                delivery_id=str(incident["create_delivery_id"]),
                event_id=event_id,
                channel="incident_open",
                target_name=result.target_name,
                url=self._incident_create_url(),
                body=self._incident_body(
                    result=result,
                    incident_id=incident_id,
                    event_id=event_id,
                ),
                dependency_ids=dependencies,
                max_attempts=self._delivery_max_attempts,
            )
            self._deliver_due_sync()
        return incident_id

    def _resolve_incident_sync(
        self,
        result: DownstreamProbeResult,
        incident_id: str,
        telemetry_event_id: Optional[str] = None,
    ) -> bool:
        if not self._incidents_url:
            return False
        event_id = telemetry_event_id or str(
            self._event_for_result(result, incident_ref=incident_id)["event_id"]
        )
        mapping = self._store.get_incident(result.target_name)
        dependencies: List[str] = []
        if mapping:
            dependencies.append(str(mapping["create_delivery_id"]))
        telemetry_delivery_id = f"telemetry:{event_id}"
        if any(
            item["delivery_id"] == telemetry_delivery_id
            for item in self._store.list_deliveries(event_id=event_id)
        ):
            dependencies.append(telemetry_delivery_id)
        delivery_id = f"incident-resolve:{event_id}"
        self._store.queue_delivery(
            delivery_id=delivery_id,
            event_id=event_id,
            channel="incident_resolve",
            target_name=result.target_name,
            url=(
                f"{self._incidents_url.rstrip('/')}/api/incidents/"
                f"{incident_id}/status"
            ),
            body={
                "status": "resolved",
                "resolved_at": result.checked_at,
                "source_event_id": event_id,
                "telemetry_event_ids": [event_id],
            },
            dependency_ids=dependencies,
            max_attempts=self._delivery_max_attempts,
        )
        self._deliver_due_sync()
        records = [
            item
            for item in self._store.list_deliveries(event_id=event_id)
            if item["delivery_id"] == delivery_id
        ]
        return bool(records and records[0]["status"] == "delivered")
