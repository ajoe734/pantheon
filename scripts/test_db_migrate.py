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
