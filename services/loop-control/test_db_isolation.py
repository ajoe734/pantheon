from __future__ import annotations

import importlib
from urllib.parse import parse_qs, urlsplit

import asyncpg
import pytest


isolation_module = importlib.import_module("services.loop-control.conftest")
AMBIENT_DATABASE_URL_ENV = isolation_module.AMBIENT_DATABASE_URL_ENV
TEST_DATABASE_URL_ENV = isolation_module.TEST_DATABASE_URL_ENV
LoopControlTestDatabaseError = isolation_module.LoopControlTestDatabaseError
build_isolated_schema_dsn = isolation_module.build_isolated_schema_dsn
resolve_explicit_test_database_url = (
    isolation_module.resolve_explicit_test_database_url
)
snapshot_legitimate_records = isolation_module.snapshot_legitimate_records


def test_ambient_database_url_is_rejected_before_any_connection():
    with pytest.raises(
        LoopControlTestDatabaseError,
        match="refuse ambient dev/production state",
    ):
        resolve_explicit_test_database_url(
            {
                AMBIENT_DATABASE_URL_ENV: (
                    "postgresql://operator:secret@dev-db.example/pantheon"
                )
            }
        )


def test_missing_database_configuration_skips_instead_of_using_dev_default():
    assert resolve_explicit_test_database_url({}) is None


def test_explicit_test_database_url_wins_over_hostile_ambient_value():
    explicit = "postgresql://tester:secret@127.0.0.1:55432/pantheon_test"
    assert (
        resolve_explicit_test_database_url(
            {
                AMBIENT_DATABASE_URL_ENV: (
                    "postgresql://operator:secret@dev-db.example/pantheon"
                ),
                TEST_DATABASE_URL_ENV: explicit,
            }
        )
        == explicit
    )


def test_isolated_dsn_pins_generated_search_path_and_application_name():
    dsn = build_isolated_schema_dsn(
        "postgresql://tester:secret@127.0.0.1:55432/pantheon_test?sslmode=disable",
        "pantheon_loop_control_test_a1b2",
    )
    query = parse_qs(urlsplit(dsn).query)
    assert query == {
        "application_name": ["pantheon_loop_control_test"],
        "options": [
            "-csearch_path=pantheon_loop_control_test_a1b2,pg_catalog"
        ],
        "sslmode": ["disable"],
    }


def test_isolated_dsn_rejects_caller_owned_libpq_options():
    with pytest.raises(
        LoopControlTestDatabaseError,
        match="fixture owns search_path isolation",
    ):
        build_isolated_schema_dsn(
            "postgresql://tester@127.0.0.1/test?options=-csearch_path%3Dpublic",
            "pantheon_loop_control_test_a1b2",
        )


@pytest.mark.asyncio
async def test_real_fixture_preserves_legitimate_count_and_digest(
    loop_control_db_isolation,
):
    isolation = loop_control_db_isolation
    conn = await asyncpg.connect(isolation.isolated_dsn)
    try:
        assert await conn.fetchval("SELECT current_schema()") == isolation.schema
        assert await conn.fetchval(
            "SELECT to_regclass('loop_controller_records') IS NOT NULL"
        )
    finally:
        await conn.close()

    legitimate_during = await snapshot_legitimate_records(
        isolation.base_dsn,
        excluded_schemas={isolation.schema},
    )
    assert legitimate_during == isolation.legitimate_before
