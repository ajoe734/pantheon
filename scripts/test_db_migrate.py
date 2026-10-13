"""Both database clients consume one ordered migration definition, offline tested."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATE = ROOT / "scripts/db_migrate.sh"
BOOTSTRAP = ROOT / "scripts/bootstrap.sh"


def _sql() -> str:
    return subprocess.run(
        ["bash", str(MIGRATE), "--print-sql"], check=True, capture_output=True, text=True
    ).stdout


def test_render_and_asyncpg_execute_identical_ordered_migrations(tmp_path: Path) -> None:
    (tmp_path / "asyncpg.py").write_text('''
import json
import os
from pathlib import Path

class Connection:
    def __init__(self, dsn):
        self.record = {"dsn": dsn, "sql": []}
    async def execute(self, sql):
        self.record["sql"].append(sql.strip())
    async def close(self):
        Path(os.environ["MIGRATION_RECORD"]).write_text(json.dumps(self.record))

async def connect(dsn):
    return Connection(dsn)
''')
    env = dict(os.environ, PYTHONPATH=str(tmp_path), MIGRATION_RECORD=str(tmp_path / "run.json"))
    env.pop("TELEMETRY_DB_DSN", None)
    env.pop("DATABASE_URL", None)
    result = subprocess.run(["bash", str(MIGRATE)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    record = json.loads((tmp_path / "run.json").read_text())
    assert record["dsn"] == "postgresql://pantheon_app:pantheon_app@localhost:15432/pantheon"
    assert _sql() == "\n".join(record["sql"]) + "\n"
    assert "CREATE TABLE IF NOT EXISTS loop_controller_records" in _sql()
    assert "CREATE TABLE IF NOT EXISTS source_ingest.data_source_instances" in _sql()
    assert record["dsn"] not in result.stdout + result.stderr


@pytest.mark.parametrize("args", [["--unknown"], ["--print-sql", "extra"]])
def test_migration_rejects_unknown_arguments(args: list[str]) -> None:
    result = subprocess.run(["bash", str(MIGRATE), *args], capture_output=True, text=True)
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert not result.stdout


FAKE_CLIENT = r'''
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if Path(sys.argv[0]).name == "docker" and "ps" in args:
    print(json.dumps({"Service": args[-1], "Health": "healthy"}))
    raise SystemExit(0)
is_psql = Path(sys.argv[0]).name == "psql" or "psql" in args
sql = sys.stdin.read() if is_psql and "-c" not in args else ""
with open(os.environ["CLIENT_RECORD"], "a") as handle:
    handle.write(json.dumps({"client": Path(sys.argv[0]).name, "args": args, "sql": sql}) + "\n")
if sql and os.environ.get("FAIL_SQL") == "1":
    raise SystemExit(3)
'''


def _bootstrap(tmp_path: Path, *, host_psql: bool, skip: bool = False,
               fail_sql: bool = False, fail_render: bool = False) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A controlled PATH proves both transports without depending on whether
    # the test host happens to have psql installed.
    for name in ("bash", "dirname", "env"):
        (bin_dir / name).symlink_to(shutil.which(name))
    if fail_render:
        import shlex
        (bin_dir / "python3").write_text(
            '#!/bin/bash\nif [[ "${2:-}" == "--print-sql" ]]; then exit 44; fi\n'
            f'exec {shlex.quote(sys.executable)} "$@"\n'
        )
        (bin_dir / "python3").chmod(0o755)
    else:
        (bin_dir / "python3").symlink_to(sys.executable)
    for name in (("docker", "psql") if host_psql else ("docker",)):
        client = bin_dir / name
        client.write_text(f"#!{sys.executable}\n" + FAKE_CLIENT)
        client.chmod(0o755)
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    env = dict(os.environ, PATH=str(bin_dir), CLIENT_RECORD=str(tmp_path / "clients.jsonl"),
               FAIL_SQL="1" if fail_sql else "0")
    args = ["/bin/bash", str(BOOTSTRAP), "--env-file", str(env_file), "--skip-telemetry-replay"]
    if skip:
        args.append("--skip-migration")
    return subprocess.run(args, env=env, input="", capture_output=True, text=True, timeout=30)


def _records(tmp_path: Path) -> list[dict]:
    return [json.loads(line) for line in (tmp_path / "clients.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("host_psql", [True, False])
def test_bootstrap_delegates_complete_schema_to_existing_migrator(tmp_path: Path, host_psql: bool) -> None:
    result = _bootstrap(tmp_path, host_psql=host_psql)
    assert result.returncode == 0, result.stdout + result.stderr
    migrations = [row for row in _records(tmp_path) if row["sql"]]
    assert len(migrations) == 1
    assert migrations[0]["sql"] == _sql()
    assert migrations[0]["client"] == ("psql" if host_psql else "docker")
    assert "ON_ERROR_STOP=1" in migrations[0]["args"]
    assert "Final service status" in result.stdout


@pytest.mark.parametrize("host_psql", [True, False])
def test_migration_failure_prevents_application_start(tmp_path: Path, host_psql: bool) -> None:
    result = _bootstrap(tmp_path, host_psql=host_psql, fail_sql=True)
    assert result.returncode != 0
    assert "Starting all application services" not in result.stdout
    assert len([row for row in _records(tmp_path) if row["sql"]]) == 1


def test_render_failure_is_not_hidden_by_successful_psql(tmp_path: Path) -> None:
    result = _bootstrap(tmp_path, host_psql=False, fail_render=True)
    assert result.returncode != 0
    assert "Starting all application services" not in result.stdout


def test_explicit_skip_migration_does_not_invoke_migrator(tmp_path: Path) -> None:
    result = _bootstrap(tmp_path, host_psql=False, skip=True)
    assert result.returncode == 0, result.stderr
    assert not any(row["sql"] for row in _records(tmp_path))
    assert "Skipping DB migrations" in result.stdout


def test_ordered_binding_index_migration_structure() -> None:
    sql = _sql()
    create_stmt = "CREATE INDEX IF NOT EXISTS idx_telemetry_events_binding_id_created_at_ingested_seq\n        ON telemetry_events ((payload->>'binding_id'), created_at DESC, ingested_seq DESC);"
    drop_stmt = "DROP INDEX IF EXISTS idx_telemetry_events_binding_id;"
    assert create_stmt in sql
    assert drop_stmt in sql
    create_idx = sql.index("idx_telemetry_events_binding_id_created_at_ingested_seq")
    drop_idx = sql.index("DROP INDEX IF EXISTS idx_telemetry_events_binding_id;")
    assert create_idx < drop_idx, "Replacement ordered index must be created before legacy index retirement"
    assert "CREATE INDEX IF NOT EXISTS idx_telemetry_events_binding_id\n        ON telemetry_events ((payload->>'binding_id'));" not in sql
    # Verify canonical indexes retained
    assert "idx_telemetry_events_ingested_seq" in sql
    assert "idx_telemetry_events_ingested_at" in sql
    assert "idx_telemetry_events_event_type_ingested_seq" in sql
    assert "idx_telemetry_events_created_at" in sql
    assert "idx_telemetry_events_event_type" in sql
    assert "idx_telemetry_events_runtime_id" in sql
    assert "idx_telemetry_events_deployment_stage" in sql
    assert "idx_telemetry_events_payload_gin" in sql


@pytest.fixture(scope="module")
def pg_cluster():
    explicit = os.environ.get("TELEMETRY_TEST_PG_DSN")
    if explicit:
        yield explicit
        return
    if shutil.which("docker") is None:
        pytest.skip("Docker not available for live PostgreSQL migration tests")
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        pytest.skip("Docker daemon not reachable for live PostgreSQL migration tests")
    import socket
    import uuid

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    cname = f"pantheon-migrate-qa-{uuid.uuid4().hex[:10]}"
    started = subprocess.run([
        "docker", "run", "--rm", "-d",
        "--name", cname,
        "-e", "POSTGRES_PASSWORD=postgres",
        "-e", "POSTGRES_USER=postgres",
        "-e", "POSTGRES_DB=postgres",
        "-p", f"127.0.0.1:{port}:5432",
        "postgres:16-alpine",
    ], capture_output=True, text=True, check=False)
    if started.returncode != 0:
        pytest.skip(f"Failed to start PostgreSQL container: {started.stderr}")
    dsn = f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres"
    import asyncio
    import time
    import asyncpg

    deadline = time.monotonic() + 15
    connected = False
    while time.monotonic() < deadline:
        try:
            async def _probe():
                conn = await asyncpg.connect(dsn, timeout=1.0)
                await conn.close()
            asyncio.run(_probe())
            connected = True
            break
        except Exception:
            time.sleep(0.2)
    if not connected:
        subprocess.run(["docker", "rm", "-f", "-v", cname], capture_output=True)
        pytest.skip("PostgreSQL container did not become healthy in time")
    try:
        yield dsn
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", cname], capture_output=True)


def _run_migration(dsn: str) -> subprocess.CompletedProcess[str]:
    py_dir = str(Path(sys.executable).parent)
    env = dict(os.environ, TELEMETRY_DB_DSN=dsn, PATH=f"{py_dir}:{os.environ.get('PATH', '')}")
    return subprocess.run(["bash", str(MIGRATE)], capture_output=True, text=True, env=env)


async def _create_isolated_db(admin_dsn: str, db_name: str) -> str:
    import asyncpg
    import urllib.parse
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {db_name}")
        await conn.execute(f"CREATE DATABASE {db_name}")
    finally:
        await conn.close()
    parsed = urllib.parse.urlparse(admin_dsn)
    return parsed._replace(path=f"/{db_name}").geturl()


def test_fresh_database_migration_creates_ordered_index(pg_cluster: str) -> None:
    import asyncio
    import asyncpg

    db_dsn = asyncio.run(_create_isolated_db(pg_cluster, "test_fresh_db"))
    res = _run_migration(db_dsn)
    assert res.returncode == 0, res.stderr
    assert "DB migrations complete." in res.stdout

    async def verify():
        conn = await asyncpg.connect(db_dsn)
        try:
            rows = await conn.fetch("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'telemetry_events'")
            indexes = {r["indexname"]: r["indexdef"] for r in rows}
            assert "idx_telemetry_events_binding_id_created_at_ingested_seq" in indexes
            assert "idx_telemetry_events_binding_id" not in indexes
            def_str = indexes["idx_telemetry_events_binding_id_created_at_ingested_seq"]
            assert "created_at DESC" in def_str
            assert "ingested_seq DESC" in def_str
            assert "payload ->> 'binding_id'" in def_str
            for required_idx in (
                "idx_telemetry_events_ingested_seq",
                "idx_telemetry_events_ingested_at",
                "idx_telemetry_events_event_type_ingested_seq",
                "idx_telemetry_events_created_at",
                "idx_telemetry_events_event_type",
                "idx_telemetry_events_runtime_id",
                "idx_telemetry_events_deployment_stage",
                "idx_telemetry_events_payload_gin",
            ):
                assert required_idx in indexes
        finally:
            await conn.close()

    asyncio.run(verify())


def test_old_index_database_migration_upgrades_and_retains_data(pg_cluster: str) -> None:
    import asyncio
    import asyncpg

    db_dsn = asyncio.run(_create_isolated_db(pg_cluster, "test_old_db"))

    async def setup_old_schema():
        conn = await asyncpg.connect(db_dsn)
        try:
            await conn.execute("""
                CREATE SEQUENCE telemetry_events_ingested_seq_seq AS BIGINT;
                CREATE TABLE telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL,
                    ingested_seq BIGINT NOT NULL DEFAULT nextval('telemetry_events_ingested_seq_seq'),
                    ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
                );
                CREATE UNIQUE INDEX idx_telemetry_events_ingested_seq ON telemetry_events (ingested_seq);
                CREATE INDEX idx_telemetry_events_created_at ON telemetry_events (created_at DESC);
                CREATE INDEX idx_telemetry_events_binding_id ON telemetry_events ((payload->>'binding_id'));
                INSERT INTO telemetry_events (event_id, event_type, created_at, payload)
                VALUES
                    ('evt-old-1', 'heartbeat', '2026-10-12T08:00:00Z', '{"binding_id": "rb-pre-existing", "tenant_id": "t1"}'),
                    ('evt-old-2', 'heartbeat', '2026-10-12T09:00:00Z', '{"binding_id": "rb-pre-existing", "tenant_id": "t1"}'),
                    ('evt-other', 'heartbeat', '2026-10-12T10:00:00Z', '{"binding_id": "rb-other", "tenant_id": "t2"}');
            """)
        finally:
            await conn.close()

    asyncio.run(setup_old_schema())
    res = _run_migration(db_dsn)
    assert res.returncode == 0, res.stderr

    async def verify():
        conn = await asyncpg.connect(db_dsn)
        try:
            rows = await conn.fetch("SELECT indexname FROM pg_indexes WHERE tablename = 'telemetry_events'")
            index_names = {r["indexname"] for r in rows}
            assert "idx_telemetry_events_binding_id_created_at_ingested_seq" in index_names
            assert "idx_telemetry_events_binding_id" not in index_names
            # Verify pre-existing data retained untouched
            count = await conn.fetchval("SELECT count(*) FROM telemetry_events WHERE payload->>'binding_id' = 'rb-pre-existing'")
            assert count == 2
            other = await conn.fetchval("SELECT count(*) FROM telemetry_events WHERE payload->>'binding_id' = 'rb-other'")
            assert other == 1
        finally:
            await conn.close()

    asyncio.run(verify())


def test_migration_idempotency_repeated_application(pg_cluster: str) -> None:
    import asyncio
    import asyncpg

    db_dsn = asyncio.run(_create_isolated_db(pg_cluster, "test_idempotent_db"))
    # Run 1
    res1 = _run_migration(db_dsn)
    assert res1.returncode == 0, res1.stderr
    # Run 2
    res2 = _run_migration(db_dsn)
    assert res2.returncode == 0, res2.stderr
    # Run 3
    res3 = _run_migration(db_dsn)
    assert res3.returncode == 0, res3.stderr

    async def verify():
        conn = await asyncpg.connect(db_dsn)
        try:
            rows = await conn.fetch("SELECT indexname FROM pg_indexes WHERE tablename = 'telemetry_events'")
            index_names = {r["indexname"] for r in rows}
            assert "idx_telemetry_events_binding_id_created_at_ingested_seq" in index_names
            assert "idx_telemetry_events_binding_id" not in index_names
        finally:
            await conn.close()

    asyncio.run(verify())


def test_exact_index_ordering_and_deterministic_tie_behavior(pg_cluster: str) -> None:
    import asyncio
    import asyncpg

    db_dsn = asyncio.run(_create_isolated_db(pg_cluster, "test_ordering_db"))
    res = _run_migration(db_dsn)
    assert res.returncode == 0, res.stderr

    async def verify():
        conn = await asyncpg.connect(db_dsn)
        try:
            await conn.execute("""
                INSERT INTO telemetry_events (event_id, event_type, created_at, payload, ingested_seq)
                VALUES
                    ('evt-tie-1', 'hb', '2026-10-12T12:00:00Z', '{"binding_id": "rb-tie-test", "tenant_id": "t"}', 100),
                    ('evt-tie-2', 'hb', '2026-10-12T12:00:00Z', '{"binding_id": "rb-tie-test", "tenant_id": "t"}', 200),
                    ('evt-tie-3', 'hb', '2026-10-12T13:00:00Z', '{"binding_id": "rb-tie-test", "tenant_id": "t"}', 50),
                    ('evt-tie-4', 'hb', '2026-10-12T11:00:00Z', '{"binding_id": "rb-tie-test", "tenant_id": "t"}', 300);
                ANALYZE telemetry_events;
                SET enable_seqscan = off;
            """)

            query = """
            SELECT event_id, created_at, ingested_seq
            FROM telemetry_events
            WHERE payload->>'binding_id' = $1
            ORDER BY created_at DESC, ingested_seq DESC
            LIMIT 10
            """
            explain_rows = await conn.fetch(f"EXPLAIN {query}", "rb-tie-test")
            plan_text = "\n".join(r[0] for r in explain_rows)
            assert "idx_telemetry_events_binding_id_created_at_ingested_seq" in plan_text

            rows = await conn.fetch(query, "rb-tie-test")
            returned_ids = [r["event_id"] for r in rows]
            assert returned_ids == ["evt-tie-3", "evt-tie-2", "evt-tie-1", "evt-tie-4"]

            lim_query = """
            SELECT event_id FROM telemetry_events
            WHERE payload->>'binding_id' = $1
            ORDER BY created_at DESC, ingested_seq DESC
            LIMIT 2
            """
            lim_rows = await conn.fetch(lim_query, "rb-tie-test")
            assert [r["event_id"] for r in lim_rows] == ["evt-tie-3", "evt-tie-2"]
        finally:
            await conn.close()

    asyncio.run(verify())
