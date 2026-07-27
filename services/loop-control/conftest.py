from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg
import pytest


TEST_DATABASE_URL_ENV = "PANTHEON_LOOP_CONTROL_TEST_DATABASE_URL"
AMBIENT_DATABASE_URL_ENV = "DATABASE_URL"
_SCHEMA_PREFIX = "pantheon_loop_control_test_"
_SAFE_SCHEMA = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

_LOOP_CONTROLLER_TABLE_SQL = """
CREATE TABLE loop_controller_records (
    loop_id             TEXT        NOT NULL,
    tenant_id           TEXT        NOT NULL,
    environment         TEXT        NOT NULL,
    controller_id       TEXT        NOT NULL,
    controller_name     TEXT        NOT NULL,
    deployment_sha      TEXT        NOT NULL,
    desired_state_query TEXT,
    actual_state_query  TEXT,
    last_heartbeat_at   TIMESTAMPTZ,
    last_tick_at        TIMESTAMPTZ,
    last_success_at     TIMESTAMPTZ,
    last_failure_at     TIMESTAMPTZ,
    last_failure_reason TEXT,
    last_repair_at      TIMESTAMPTZ,
    last_repair_reason  TEXT,
    backlog             INTEGER,
    lag                 INTEGER,
    dlq_count           INTEGER,
    evidence_refs       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    truth_level         TEXT        NOT NULL,
    lease_expires_at    TIMESTAMPTZ,
    payload             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (loop_id, tenant_id, environment)
);
CREATE INDEX idx_loop_controller_records_updated_at
    ON loop_controller_records (updated_at DESC);
"""


class LoopControlTestDatabaseError(RuntimeError):
    """Raised before a loop-control test can reach an ambient database."""


@dataclass(frozen=True)
class LoopControlDatabaseIsolation:
    base_dsn: str
    isolated_dsn: str
    schema: str
    legitimate_before: dict[str, dict[str, object]]


def resolve_explicit_test_database_url(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return only the task-specific test DSN; never inherit DATABASE_URL."""

    values = os.environ if environ is None else environ
    explicit = str(values.get(TEST_DATABASE_URL_ENV, "") or "").strip()
    ambient = str(values.get(AMBIENT_DATABASE_URL_ENV, "") or "").strip()
    if not explicit:
        if ambient:
            raise LoopControlTestDatabaseError(
                f"{AMBIENT_DATABASE_URL_ENV} is set but loop-control database "
                f"tests refuse ambient dev/production state; set "
                f"{TEST_DATABASE_URL_ENV} to an explicitly authorized test "
                "database. The suite did not connect to DATABASE_URL."
            )
        return None

    parsed = urlsplit(explicit)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.path.strip("/"):
        raise LoopControlTestDatabaseError(
            f"{TEST_DATABASE_URL_ENV} must be a PostgreSQL URL naming a database"
        )
    return explicit


def _validate_schema_name(schema: str) -> str:
    if not _SAFE_SCHEMA.fullmatch(schema):
        raise LoopControlTestDatabaseError(
            f"unsafe generated test schema identifier: {schema!r}"
        )
    return schema


def build_isolated_schema_dsn(base_dsn: str, schema: str) -> str:
    """Pin every store connection to one generated schema."""

    _validate_schema_name(schema)
    parsed = urlsplit(base_dsn)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if any(key.lower() == "options" for key, _ in query):
        raise LoopControlTestDatabaseError(
            f"{TEST_DATABASE_URL_ENV} must not contain libpq options; the "
            "fixture owns search_path isolation"
        )
    query = [
        (key, value)
        for key, value in query
        if key.lower() != "application_name"
    ]
    query.extend(
        [
            ("application_name", "pantheon_loop_control_test"),
            ("options", f"-csearch_path={schema},pg_catalog"),
        ]
    )
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


async def snapshot_legitimate_records(
    base_dsn: str,
    *,
    excluded_schemas: set[str] | None = None,
) -> dict[str, dict[str, object]]:
    """Capture count and content digest for every pre-existing controller table."""

    excluded = excluded_schemas or set()
    conn = await asyncpg.connect(base_dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT n.nspname AS schema_name
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE c.relname = 'loop_controller_records'
              AND c.relkind IN ('r', 'p')
              AND n.nspname NOT LIKE 'pg_%'
              AND n.nspname <> 'information_schema'
            ORDER BY n.nspname
            """
        )
        snapshot: dict[str, dict[str, object]] = {}
        for row in rows:
            schema = str(row["schema_name"])
            if schema in excluded:
                continue
            qualified = (
                f"{_quote_identifier(schema)}."
                f"{_quote_identifier('loop_controller_records')}"
            )
            state = await conn.fetchrow(
                f"""
                SELECT
                    count(*)::bigint AS row_count,
                    COALESCE(
                        md5(string_agg(row_json, E'\\n' ORDER BY row_json)),
                        md5('')
                    ) AS row_digest
                FROM (
                    SELECT to_jsonb(record)::text AS row_json
                    FROM {qualified} AS record
                ) AS stable_rows
                """
            )
            snapshot[schema] = {
                "row_count": int(state["row_count"]),
                "row_digest": str(state["row_digest"]),
            }
        return snapshot
    finally:
        await conn.close()


async def _create_isolation(base_dsn: str) -> LoopControlDatabaseIsolation:
    legitimate_before = await snapshot_legitimate_records(base_dsn)
    schema = _validate_schema_name(f"{_SCHEMA_PREFIX}{uuid.uuid4().hex}")
    isolated_dsn = build_isolated_schema_dsn(base_dsn, schema)
    conn = await asyncpg.connect(base_dsn)
    try:
        await conn.execute(f"CREATE SCHEMA {_quote_identifier(schema)}")
        await conn.execute(
            f"SET search_path TO {_quote_identifier(schema)}, pg_catalog"
        )
        await conn.execute(_LOOP_CONTROLLER_TABLE_SQL)
    except BaseException:
        await conn.execute(
            f"DROP SCHEMA IF EXISTS {_quote_identifier(schema)} CASCADE"
        )
        raise
    finally:
        await conn.close()
    return LoopControlDatabaseIsolation(
        base_dsn=base_dsn,
        isolated_dsn=isolated_dsn,
        schema=schema,
        legitimate_before=legitimate_before,
    )


async def _drop_isolation_and_verify(
    isolation: LoopControlDatabaseIsolation,
) -> None:
    conn = await asyncpg.connect(isolation.base_dsn)
    try:
        await conn.execute(
            f"DROP SCHEMA IF EXISTS {_quote_identifier(isolation.schema)} CASCADE"
        )
        schema_still_exists = await conn.fetchval(
            "SELECT to_regnamespace($1) IS NOT NULL",
            isolation.schema,
        )
    finally:
        await conn.close()
    if schema_still_exists:
        raise AssertionError(
            f"loop-control test schema {isolation.schema!r} was not removed"
        )

    legitimate_after = await snapshot_legitimate_records(isolation.base_dsn)
    if legitimate_after != isolation.legitimate_before:
        raise AssertionError(
            "legitimate loop_controller_records changed during the isolated "
            f"suite: before={isolation.legitimate_before!r}, "
            f"after={legitimate_after!r}"
        )


@pytest.fixture(scope="session")
def loop_control_db_isolation() -> LoopControlDatabaseIsolation:
    try:
        base_dsn = resolve_explicit_test_database_url()
    except LoopControlTestDatabaseError as exc:
        pytest.fail(str(exc), pytrace=False)
    if base_dsn is None:
        pytest.skip(
            f"real loop-control database tests require {TEST_DATABASE_URL_ENV}; "
            "DATABASE_URL is never inherited"
        )

    isolation = asyncio.run(_create_isolation(base_dsn))
    try:
        yield isolation
    finally:
        asyncio.run(_drop_isolation_and_verify(isolation))


@pytest.fixture(scope="session")
def loop_control_db_dsn(
    loop_control_db_isolation: LoopControlDatabaseIsolation,
) -> str:
    return loop_control_db_isolation.isolated_dsn
