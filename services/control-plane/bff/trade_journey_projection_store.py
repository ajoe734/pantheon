"""Scoped PostgreSQL read repository for Trade Journey projections.

The lifecycle projector writer owns ``trade_journey_projection``.  This module
is deliberately read-only: every public lookup requires a tenant and an
environment, and list/timeline reads use HMAC-bound keyset cursors rather than
offsets.  It is selected only by the disabled-by-default BFF reader flag.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from services.trade_journey.materializer import JourneyProjection, STAGES, TERMINAL_STATUSES

READER_BACKEND_ENV = "PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND"
READER_DSN_ENV = "PANTHEON_BFF_TRADE_JOURNEY_PROJECTION_DSN"
READER_SCHEMA_ENV = "PANTHEON_BFF_TRADE_JOURNEY_PROJECTION_SCHEMA"
READER_TOKEN_SECRET_ENV = "PANTHEON_BFF_TRADE_JOURNEY_PAGE_TOKEN_SECRET"
DEFAULT_SCHEMA = "trade_journey_projection"
DEFAULT_CONTROLLER_ID = "canonical-lifecycle-projector"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SORTS = {
    "updated_at_desc": ("updated_at", "DESC", "<"),
    "updated_at_asc": ("updated_at", "ASC", ">"),
    "created_at_desc": ("created_at", "DESC", "<"),
    "created_at_asc": ("created_at", "ASC", ">"),
}


class ProjectionReadError(RuntimeError):
    """Base error for a Postgres projection read."""


class ProjectionReadUnavailable(ProjectionReadError):
    """The configured projection reader cannot provide current truth."""


class InvalidPageToken(ProjectionReadError):
    """A cursor was malformed, tampered with, or reused across a scope."""


class UnavailableProjectionReader:
    """Fail-closed sentinel used when the selected backend is misconfigured."""

    def __init__(self, message: str) -> None:
        self.message = message

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def unavailable(*args: Any, **kwargs: Any) -> Any:
            raise ProjectionReadUnavailable(self.message)
        return unavailable


@dataclass(frozen=True)
class ProjectionPage:
    items: list[JourneyProjection]
    next_page_token: Optional[str]
    total: int


@dataclass(frozen=True)
class TimelinePage:
    items: list[dict[str, Any]]
    next_page_token: Optional[str]
    total: int


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return str(value or "")


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


class PageTokenCodec:
    """Short authenticated cursors tied to request scope, sort, and filters."""

    def __init__(self, secret: str) -> None:
        if len(secret.encode("utf-8")) < 16:
            raise ValueError("PANTHEON_BFF_TRADE_JOURNEY_PAGE_TOKEN_SECRET must be at least 16 bytes")
        self._secret = secret.encode("utf-8")

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        try:
            return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))
        except Exception as exc:  # binascii varies by Python version
            raise InvalidPageToken("invalid page token") from exc

    def encode(self, payload: Mapping[str, Any]) -> str:
        raw = json.dumps(_canonical(payload), separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(self._secret, raw, hashlib.sha256).digest()
        return f"{self._encode(raw)}.{self._encode(signature)}"

    def decode(self, token: str, *, expected: Mapping[str, Any]) -> dict[str, Any]:
        if not token or len(token) > 4096 or token.count(".") != 1:
            raise InvalidPageToken("invalid page token")
        raw_part, sig_part = token.split(".", 1)
        raw = self._decode(raw_part)
        signature = self._decode(sig_part)
        expected_signature = hmac.new(self._secret, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise InvalidPageToken("page token signature is invalid")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidPageToken("invalid page token") from exc
        if not isinstance(payload, dict):
            raise InvalidPageToken("invalid page token")
        for key, value in _canonical(expected).items():
            if payload.get(key) != value:
                raise InvalidPageToken("page token does not match this scope or filter")
        return payload


class TradeJourneyProjectionStore:
    """Read-only repository over the relational lifecycle projection tables."""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = DEFAULT_SCHEMA,
        token_secret: str,
        connect: Optional[Callable[..., Any]] = None,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for the projection reader")
        if not _SCHEMA_RE.fullmatch(schema):
            raise ValueError("invalid projection reader schema")
        self.dsn = dsn
        self.schema = schema
        self.tokens = PageTokenCodec(token_secret)
        if connect is None:
            try:
                import psycopg  # type: ignore[import]
            except ImportError as exc:
                raise ProjectionReadUnavailable("psycopg is required for the Postgres projection reader") from exc
            connect = psycopg.connect
        self._connect = connect

    @classmethod
    def from_environment(cls) -> "TradeJourneyProjectionStore":
        backend = os.getenv(READER_BACKEND_ENV, "postgres").strip().lower()
        if backend != "postgres":
            raise ProjectionReadUnavailable(
                f"Legacy JSON projection reader is retired; {READER_BACKEND_ENV} must be 'postgres' (got {backend!r})"
            )
        dsn = os.getenv(READER_DSN_ENV, "").strip() or os.getenv("TELEMETRY_DB_DSN", "").strip()
        secret = os.getenv(READER_TOKEN_SECRET_ENV, "").strip() or os.getenv("PANTHEON_BFF_PAGE_TOKEN_SECRET", "default-page-token-secret-minimum-16-bytes")
        if not dsn:
            raise ProjectionReadUnavailable(
                f"{READER_DSN_ENV} or TELEMETRY_DB_DSN is required for Postgres projection reader"
            )
        return cls(dsn, schema=os.getenv(READER_SCHEMA_ENV, DEFAULT_SCHEMA), token_secret=secret)


    @staticmethod
    def _require_scope(tenant_id: str, environment: str) -> None:
        if not str(tenant_id or "").strip() or not str(environment or "").strip():
            raise ValueError("tenant_id and environment are required for projection reads")

    def _rows(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        try:
            with self._connect(self.dsn) as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                description = getattr(cur, "description", None) or []
        except ProjectionReadError:
            raise
        except Exception as exc:
            raise ProjectionReadUnavailable("Postgres projection reader is unavailable") from exc
        names = [getattr(column, "name", column[0] if isinstance(column, tuple) else str(column)) for column in description]
        result: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, Mapping):
                result.append(dict(row))
            elif names:
                result.append(dict(zip(names, row)))
        return result

    def _one(self, sql: str, params: Sequence[Any]) -> Optional[dict[str, Any]]:
        rows = self._rows(sql, params)
        return rows[0] if rows else None

    def _cursor_scope(self, *, tenant_id: str, environment: str, sort: str, filters: Mapping[str, Any], kind: str) -> dict[str, Any]:
        return {"v": 1, "kind": kind, "tenant_id": tenant_id, "environment": environment, "sort": sort, "filters": _canonical(filters)}

    def _journey_where(self, *, tenant_id: str, environment: str, filters: Mapping[str, Any]) -> tuple[list[str], list[Any]]:
        clauses = ["tenant_id=%s", "environment=%s"]
        params: list[Any] = [tenant_id, environment]
        for key in ("status",):
            if filters.get(key):
                clauses.append(f"{key}=%s")
                params.append(str(filters[key]))
        for key in ("date_from", "date_to"):
            if filters.get(key):
                clauses.append(f"updated_at {'>=' if key == 'date_from' else '<='} %s::timestamptz")
                params.append(str(filters[key]))
        if filters.get("stage"):
            clauses.append("stage_coverage ? %s")
            params.append(str(filters["stage"]))
        if filters.get("reconciliation_state"):
            clauses.append("stage_coverage -> 'reconciliation' ->> 'status' = %s")
            params.append(str(filters["reconciliation_state"]))
        if "waiting_human" in filters:
            waiting_stage = "EXISTS (SELECT 1 FROM jsonb_each(COALESCE(stage_coverage, '{}'::jsonb)) AS stage(stage_name, stage_state) WHERE stage.stage_state ->> 'status' = 'waiting_human')"
            if bool(filters["waiting_human"]):
                clauses.append(f"(status=%s OR {waiting_stage})")
            else:
                clauses.append(f"(status<>%s AND NOT {waiting_stage})")
            params.append("waiting_human")
        if "stalled" in filters:
            terminal_placeholders = ", ".join("%s" for _ in TERMINAL_STATUSES)
            terminal_params = tuple(sorted(TERMINAL_STATUSES))
            if bool(filters["stalled"]):
                clauses.append(f"(status NOT IN ({terminal_placeholders}) AND last_occurred_at < clock_timestamp() - interval '900 seconds')")
            else:
                clauses.append(f"(status IN ({terminal_placeholders}) OR last_occurred_at >= clock_timestamp() - interval '900 seconds')")
            params.extend(terminal_params)
        for key, identifier_type in (("persona_id", "persona_id"), ("strategy_id", "strategy_id"), ("decision_id", "decision_id"), ("order_id", "order_id"), ("broker_order_id", "broker_order_id")):
            if filters.get(key):
                clauses.append(f"EXISTS (SELECT 1 FROM {self.schema}.identity_links link WHERE link.tenant_id={self.schema}.journeys.tenant_id AND link.environment={self.schema}.journeys.environment AND link.journey_id={self.schema}.journeys.journey_id AND link.identifier_type=%s AND link.identifier_value=%s)")
                params.extend((identifier_type, str(filters[key])))
        if filters.get("q"):
            if filters.get("q_journey_only"):
                clauses.append("journey_id ILIKE %s")
                params.append(f"%{filters['q']}%")
            else:
                clauses.append(f"(journey_id ILIKE %s OR EXISTS (SELECT 1 FROM {self.schema}.identity_links link WHERE link.tenant_id={self.schema}.journeys.tenant_id AND link.environment={self.schema}.journeys.environment AND link.journey_id={self.schema}.journeys.journey_id AND link.identifier_value ILIKE %s))")
                params.extend((f"%{filters['q']}%", f"%{filters['q']}%"))
        return clauses, params

    def page_journeys(self, *, tenant_id: str, environment: str, filters: Optional[Mapping[str, Any]] = None, sort: str = "updated_at_desc", page_size: int = DEFAULT_PAGE_SIZE, page_token: Optional[str] = None) -> ProjectionPage:
        self._require_scope(tenant_id, environment)
        if sort not in _SORTS:
            raise ValueError("unsupported journey sort")
        if not 1 <= int(page_size) <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        # Keep explicit false values in the signed scope: ``waiting_human=false``
        # and ``stalled=false`` are filters, not equivalent to an omitted filter.
        active_filters = {key: value for key, value in dict(filters or {}).items() if value is not None and value != ""}
        scope = self._cursor_scope(tenant_id=tenant_id, environment=environment, sort=sort, filters=active_filters, kind="journeys")
        base_clauses, base_params = self._journey_where(tenant_id=tenant_id, environment=environment, filters=active_filters)
        clauses, params = list(base_clauses), list(base_params)
        field, direction, comparator = _SORTS[sort]
        if page_token:
            cursor = self.tokens.decode(page_token, expected=scope)
            values = cursor.get("after")
            if not isinstance(values, list) or len(values) != 2:
                raise InvalidPageToken("invalid journey cursor")
            clauses.append(f"({field}, journey_id) {comparator} (%s::timestamptz, %s)")
            params.extend((values[0], values[1]))
        where = " AND ".join(clauses)
        sql = f"SELECT tenant_id, environment, journey_id, status, stage_coverage, is_terminal, first_occurred_at, last_occurred_at, current_identity_summary, evidence_summary, diagnostic_summary, loop_run_id, projection_revision, created_at, updated_at FROM {self.schema}.journeys WHERE {where} ORDER BY {field} {direction}, journey_id {direction} LIMIT %s"
        rows = self._rows(sql, [*params, int(page_size) + 1])
        count_row = self._one(f"SELECT COUNT(*) AS total FROM {self.schema}.journeys WHERE {' AND '.join(base_clauses)}", base_params)
        total = int((count_row or {}).get("total") or 0)
        page_rows, has_more = rows[: int(page_size)], len(rows) > int(page_size)
        items = [self._projection_from_journey(row, stages=[]) for row in page_rows]
        next_token = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_token = self.tokens.encode({**scope, "after": [_iso(last.get(field)), str(last.get("journey_id") or "")]})
        return ProjectionPage(items=items, next_page_token=next_token, total=total)

    def get_journey(self, *, tenant_id: str, environment: str, journey_id: str) -> Optional[JourneyProjection]:
        self._require_scope(tenant_id, environment)
        row = self._one(f"SELECT tenant_id, environment, journey_id, status, stage_coverage, is_terminal, first_occurred_at, last_occurred_at, current_identity_summary, evidence_summary, diagnostic_summary, loop_run_id, projection_revision, created_at, updated_at FROM {self.schema}.journeys WHERE tenant_id=%s AND environment=%s AND journey_id=%s", (tenant_id, environment, journey_id))
        if row is None:
            return None
        stages = self._rows(f"SELECT DISTINCT ON (stage_name) source_event_id, stage_name, stage_status, stage_ordinal, source_ingested_seq, event_sequence, occurred_at, recorded_at, contract_fields, evidence_references FROM {self.schema}.journey_stages WHERE tenant_id=%s AND environment=%s AND journey_id=%s ORDER BY stage_name, stage_ordinal DESC, event_sequence DESC, source_ingested_seq DESC, source_event_id DESC", (tenant_id, environment, journey_id))
        return self._projection_from_journey(row, stages=stages)

    def page_timeline(self, *, tenant_id: str, environment: str, journey_id: str, page_size: int = DEFAULT_PAGE_SIZE, page_token: Optional[str] = None) -> TimelinePage:
        self._require_scope(tenant_id, environment)
        if not 1 <= int(page_size) <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        scope = self._cursor_scope(tenant_id=tenant_id, environment=environment, sort="timeline_asc", filters={"journey_id": journey_id}, kind="timeline")
        clauses = ["tenant_id=%s", "environment=%s", "journey_id=%s"]
        params: list[Any] = [tenant_id, environment, journey_id]
        if page_token:
            cursor = self.tokens.decode(page_token, expected=scope)
            values = cursor.get("after")
            if not isinstance(values, list) or len(values) != 5:
                raise InvalidPageToken("invalid timeline cursor")
            clauses.append("(stage_ordinal, event_sequence, occurred_at, source_ingested_seq, source_event_id) > (%s, %s, %s::timestamptz, %s, %s)")
            params.extend(values)
        where = " AND ".join(clauses)
        sql = f"SELECT source_event_id, stage_name, stage_status, stage_ordinal, source_ingested_seq, event_sequence, occurred_at, recorded_at, contract_fields, evidence_references FROM {self.schema}.journey_stages WHERE {where} ORDER BY stage_ordinal ASC, event_sequence ASC, occurred_at ASC, source_ingested_seq ASC, source_event_id ASC LIMIT %s"
        rows = self._rows(sql, [*params, int(page_size) + 1])
        count_row = self._one(f"SELECT COUNT(*) AS total FROM {self.schema}.journey_stages WHERE tenant_id=%s AND environment=%s AND journey_id=%s", (tenant_id, environment, journey_id))
        page_rows, has_more = rows[: int(page_size)], len(rows) > int(page_size)
        next_token = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_token = self.tokens.encode({**scope, "after": [int(last.get("stage_ordinal") or 0), int(last.get("event_sequence") or 0), _iso(last.get("occurred_at")), int(last.get("source_ingested_seq") or 0), str(last.get("source_event_id") or "")]})
        return TimelinePage(items=[self._timeline_row(row, journey_id=journey_id) for row in page_rows], next_page_token=next_token, total=int((count_row or {}).get("total") or 0))

    def resolve(self, *, tenant_id: str, environment: str, identifier_type: str, identifier_value: str) -> list[str]:
        self._require_scope(tenant_id, environment)
        if identifier_type == "journey_id":
            rows = self._rows(f"SELECT journey_id FROM {self.schema}.journeys WHERE tenant_id=%s AND environment=%s AND journey_id=%s", (tenant_id, environment, identifier_value))
        else:
            rows = self._rows(f"SELECT journey_id FROM {self.schema}.identity_links WHERE tenant_id=%s AND environment=%s AND identifier_type=%s AND identifier_value=%s ORDER BY journey_id ASC LIMIT {MAX_PAGE_SIZE}", (tenant_id, environment, identifier_type, identifier_value))
        return sorted({str(row.get("journey_id") or "") for row in rows if row.get("journey_id")})

    def metrics(self, *, tenant_id: str, environment: str) -> dict[str, Any]:
        self._require_scope(tenant_id, environment)
        status_rows = self._rows(f"SELECT status, COUNT(*) AS count FROM {self.schema}.journeys WHERE tenant_id=%s AND environment=%s GROUP BY status", (tenant_id, environment))
        stage_rows = self._rows(f"SELECT COALESCE(stage_name, 'none') AS stage_name, COUNT(*) AS count FROM (SELECT DISTINCT ON (journey_id) journey_id, stage_name FROM {self.schema}.journey_stages WHERE tenant_id=%s AND environment=%s ORDER BY journey_id, stage_ordinal DESC, event_sequence DESC, source_ingested_seq DESC) current_stage GROUP BY stage_name", (tenant_id, environment))
        latency_rows = self._rows(f"SELECT stage_name, COUNT(*) AS sample_count, percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (recorded_at - occurred_at)) * 1000) AS p50_ms, percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (recorded_at - occurred_at)) * 1000) AS p95_ms FROM {self.schema}.journey_stages WHERE tenant_id=%s AND environment=%s AND recorded_at IS NOT NULL GROUP BY stage_name", (tenant_id, environment))
        by_status = {str(row.get("status") or "unknown"): int(row.get("count") or 0) for row in status_rows}
        by_stage = {str(row.get("stage_name") or "none"): int(row.get("count") or 0) for row in stage_rows}
        total = sum(by_status.values())
        return {"total_journeys": total, "by_status": by_status, "by_current_stage": by_stage, "stalled_count": 0, "diagnostics_counts": {}, "diagnostics_rate": {}, "stage_latency_ms": {str(row.get("stage_name")): {"p50_ms": float(row["p50_ms"]) if row.get("p50_ms") is not None else None, "p95_ms": float(row["p95_ms"]) if row.get("p95_ms") is not None else None, "sample_count": int(row.get("sample_count") or 0)} for row in latency_rows}}

    def controller_freshness(self, *, tenant_id: str, environment: str, controller_id: str = DEFAULT_CONTROLLER_ID) -> Optional[dict[str, Any]]:
        self._require_scope(tenant_id, environment)
        row = self._one(f"SELECT controller_id, tenant_scope, environment_scope, checkpoint_seq, source_high_watermark, backlog_count, projection_revision, deployment_sha, mode, status, accepted_live, last_poll_at, last_success_at, last_live_success_at, last_failure_at, last_error_message, unresolved_quarantine_count, updated_at FROM {self.schema}.controller WHERE controller_id=%s AND tenant_scope IN (%s, '*') AND environment_scope IN (%s, '*') ORDER BY (tenant_scope=%s AND environment_scope=%s) DESC, updated_at DESC LIMIT 1", (controller_id, tenant_id, environment, tenant_id, environment))
        if row is None:
            return None
        return {"controller_id": row.get("controller_id"), "checkpoint": int(row.get("checkpoint_seq") or 0), "source_high_watermark": int(row.get("source_high_watermark") or 0), "backlog": int(row.get("backlog_count") or 0), "generation": int(row.get("projection_revision") or 0), "deployment_sha": row.get("deployment_sha"), "mode": row.get("mode"), "status": "ready" if str(row.get("status") or "").lower() in {"ok", "ready"} else row.get("status"), "accepted_live": bool(row.get("accepted_live")), "last_poll_at": _iso(row.get("last_poll_at")) or None, "last_successful_publish_at": _iso(row.get("last_success_at")) or None, "last_live_success_at": _iso(row.get("last_live_success_at")) or None, "last_error": row.get("last_error_message") or None, "quarantine_count": int(row.get("unresolved_quarantine_count") or 0)}

    def page_loop_runs(self, *, tenant_id: str, environment: str, statuses: Optional[Sequence[str]] = None, page_size: int = DEFAULT_PAGE_SIZE, page_token: Optional[str] = None) -> tuple[list[dict[str, Any]], Optional[str]]:
        self._require_scope(tenant_id, environment)
        if not 1 <= int(page_size) <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        clean_statuses = sorted({str(status).strip().lower() for status in (statuses or []) if str(status).strip()})
        scope = self._cursor_scope(tenant_id=tenant_id, environment=environment, sort="updated_at_desc", filters={"statuses": clean_statuses}, kind="loop_runs")
        clauses, params = ["tenant_id=%s", "environment=%s"], [tenant_id, environment]
        if clean_statuses:
            clauses.append(f"status IN ({', '.join('%s' for _ in clean_statuses)})")
            params.extend(clean_statuses)
        if page_token:
            cursor = self.tokens.decode(page_token, expected=scope)
            after = cursor.get("after")
            if not isinstance(after, list) or len(after) != 2:
                raise InvalidPageToken("invalid loop cursor")
            clauses.append("(updated_at, loop_run_id) < (%s::timestamptz, %s)")
            params.extend(after)
        rows = self._rows(f"SELECT loop_run_id, journey_id, status, lifecycle_summary, freshness_lineage, contract_payload, projection_revision, created_at, updated_at FROM {self.schema}.loop_runs WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC, loop_run_id DESC LIMIT %s", [*params, int(page_size) + 1])
        page_rows, has_more = rows[: int(page_size)], len(rows) > int(page_size)
        token = None
        if has_more and page_rows:
            last = page_rows[-1]
            token = self.tokens.encode({**scope, "after": [_iso(last.get("updated_at")), str(last.get("loop_run_id") or "")]})
        return [self._loop_row(row, tenant_id=tenant_id, environment=environment) for row in page_rows], token

    def get_loop_run(self, *, tenant_id: str, environment: str, loop_run_id: str) -> Optional[dict[str, Any]]:
        self._require_scope(tenant_id, environment)
        row = self._one(f"SELECT loop_run_id, journey_id, status, lifecycle_summary, freshness_lineage, contract_payload, projection_revision, created_at, updated_at FROM {self.schema}.loop_runs WHERE tenant_id=%s AND environment=%s AND loop_run_id=%s", (tenant_id, environment, loop_run_id))
        return self._loop_row(row, tenant_id=tenant_id, environment=environment) if row else None

    def _projection_from_journey(self, row: Mapping[str, Any], *, stages: Sequence[Mapping[str, Any]]) -> JourneyProjection:
        coverage = _json_value(row.get("stage_coverage"), {})
        coverage = dict(coverage) if isinstance(coverage, Mapping) else {}
        stage_map: dict[str, dict[str, Any]] = {}
        for name, value in coverage.items():
            stage_map[str(name)] = dict(value) if isinstance(value, Mapping) else {"status": str(value)}
        timeline = [self._timeline_row(stage, journey_id=str(row.get("journey_id") or "")) for stage in stages]
        for event in timeline:
            stage = str(event.get("stage") or "")
            if stage:
                stage_map[stage] = {"status": event.get("stage_status") or event.get("status") or "unknown", "updated_at": event.get("occurred_at"), "revision": event.get("event_sequence") or 0}
        raw_identifiers = _json_value(row.get("current_identity_summary"), {})
        if isinstance(raw_identifiers, Mapping) and isinstance(raw_identifiers.get("identifiers"), Mapping):
            raw_identifiers = raw_identifiers["identifiers"]
        identifiers = {str(name): value if isinstance(value, list) else [value] for name, value in dict(raw_identifiers).items() if value not in (None, "")} if isinstance(raw_identifiers, Mapping) else {}
        diagnostics_raw = _json_value(row.get("diagnostic_summary"), {})
        diagnostics = list(diagnostics_raw.get("diagnostics") or []) if isinstance(diagnostics_raw, Mapping) else []
        if not diagnostics and isinstance(diagnostics_raw, Mapping):
            diagnostics = [{"code": key, "value": value} for key, value in diagnostics_raw.items() if value]
        graph_edges: list[dict[str, str]] = []
        for event in timeline:
            for edge in event.get("graph_edges") or []:
                if isinstance(edge, Mapping) and all(edge.get(key) for key in ("from", "to", "type")):
                    graph_edges.append({key: str(edge[key]) for key in ("from", "to", "type")})
        present = [stage for stage in STAGES if stage in stage_map]
        furthest = max((STAGES.index(stage) for stage in present), default=-1)
        snapshot = {"journey_id": row.get("journey_id"), "tenant_id": row.get("tenant_id"), "environment": row.get("environment"), "status": row.get("status"), "stages": stage_map, "revision": int(row.get("projection_revision") or 0), "created_at": _iso(row.get("first_occurred_at")), "updated_at": _iso(row.get("last_occurred_at")), "identifiers": identifiers, "completeness": {"missing_stages": [stage for stage in STAGES[: furthest + 1] if stage not in stage_map], "complete": bool(present) and len(present) == len(STAGES)} }
        return JourneyProjection(str(row.get("journey_id") or ""), str(row.get("tenant_id") or ""), str(row.get("environment") or ""), timeline, snapshot, graph_edges, [item for item in diagnostics if isinstance(item, Mapping)])

    def _timeline_row(self, row: Mapping[str, Any], *, journey_id: str) -> dict[str, Any]:
        fields = _json_value(row.get("contract_fields"), {})
        fields = dict(fields) if isinstance(fields, Mapping) else {}
        refs = _json_value(row.get("evidence_references"), [])
        result = {**fields, "event_id": row.get("source_event_id"), "journey_id": journey_id, "stage": row.get("stage_name"), "stage_status": row.get("stage_status"), "status": row.get("stage_status"), "occurred_at": _iso(row.get("occurred_at")), "recorded_at": _iso(row.get("recorded_at")), "event_sequence": int(row.get("event_sequence") or 0)}
        result.setdefault("evidence_refs", refs if isinstance(refs, list) else [])
        return result

    def _loop_row(self, row: Mapping[str, Any], *, tenant_id: str, environment: str) -> dict[str, Any]:
        payload = _json_value(row.get("contract_payload"), {})
        payload = dict(payload) if isinstance(payload, Mapping) else {}
        summary = _json_value(row.get("lifecycle_summary"), {})
        summary = dict(summary) if isinstance(summary, Mapping) else {}
        freshness = _json_value(row.get("freshness_lineage"), {})
        freshness = dict(freshness) if isinstance(freshness, Mapping) else {}
        return {**payload, **summary, "id": str(row.get("loop_run_id") or ""), "loop_run_id": str(row.get("loop_run_id") or ""), "journey_id": row.get("journey_id") or None, "tenant_id": tenant_id, "environment": environment, "status": row.get("status"), "projection_revision": int(row.get("projection_revision") or 0), "created_at": _iso(row.get("created_at")), "updated_at": _iso(row.get("updated_at")), "freshness_lineage": freshness, "source": "postgres_lifecycle_projection"}


def configured_projection_reader() -> TradeJourneyProjectionStore | UnavailableProjectionReader:
    """Resolve the Postgres projection reader without silently selecting any fallback."""
    try:
        return TradeJourneyProjectionStore.from_environment()
    except (ProjectionReadUnavailable, ValueError) as exc:
        return UnavailableProjectionReader(str(exc))
