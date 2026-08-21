"""Focused tests for the LIFECYCLE-PROJ-CAPACITY-001 harness.

These tests run the harness at a small, fast scale to prove the corpus
generator, RSS/latency instrumentation and fault matrix are each correct.
They do not attempt the full 1,000,000-event / 150,000-loop-run gates from
section 14: that run is multi-minute and host-load-sensitive, and must be
driven through the ``lifecycle_projector_capacity`` CLI on the documented
dev profile when the host is quiet, not from the unit test suite.
"""

from __future__ import annotations

import os
import subprocess
from argparse import Namespace
from pathlib import Path
from uuid import uuid4

import pytest

import services.trade_journey.lifecycle_projector_capacity as capacity_module
from services.trade_journey.lifecycle_projector import RelationalLifecycleProjector
from services.trade_journey.lifecycle_projector_capacity import (
    CAPACITY_SCHEMA_PREFIX,
    CapacityReport,
    FAULT_SCENARIOS,
    MANAGED_ADMISSION_LOCK,
    MANAGED_WORKTREE_ROOT,
    _teardown_capacity_schema,
    _write_json_atomically,
    _journey_event_budgets,
    _journey_event_types,
    _managed_state_paths,
    _assert_quiet_managed_host,
    cleanup_managed_capacity_run,
    collect_managed_capacity_result,
    explain_bff_read_paths,
    generate_corpus_batches,
    journey_rows,
    launch_managed_capacity_run,
    main,
    run_managed_capacity_worker,
    run_bff_read_benchmark,
    run_capacity_benchmark,
    run_fault_matrix,
    rss_bytes,
)
from services.trade_journey.projection_store import ProjectionStore


@pytest.fixture
def capacity_dsn() -> str:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    return dsn


def _schema() -> str:
    return f"{CAPACITY_SCHEMA_PREFIX}test_{uuid4().hex[:12]}"


def test_journey_event_budgets_hits_exact_totals():
    budgets = _journey_event_budgets(2_000, 300)
    assert sum(budgets) == 2_000
    assert len(budgets) == 300
    assert all(2 <= b <= 8 for b in budgets)


def test_journey_event_budgets_rejects_impossible_targets():
    with pytest.raises(ValueError):
        _journey_event_budgets(100, 100)  # needs >= 2 events per journey
    with pytest.raises(ValueError):
        _journey_event_budgets(100, 0)


def test_journey_event_types_always_closes_with_reconciliation():
    for budget in range(1, 12):
        stages = _journey_event_types(budget)
        assert stages[-1] == "reconciliation_completed"
        assert 2 <= len(stages) <= 8
        assert len(stages) == len(set(stages))


def test_journey_rows_are_unique_and_monotonic_across_journeys():
    first = journey_rows(0, event_types=_journey_event_types(8), starting_seq=1)
    second = journey_rows(1, event_types=_journey_event_types(8), starting_seq=9)

    assert [row["ingested_seq"] for row in first] == list(range(1, 9))
    assert [row["ingested_seq"] for row in second] == list(range(9, 17))

    first_journey_ids = {row["payload"]["correlation_envelope"]["journey_id"] for row in first}
    second_journey_ids = {row["payload"]["correlation_envelope"]["journey_id"] for row in second}
    assert first_journey_ids.isdisjoint(second_journey_ids)

    first_event_ids = {row["event_id"] for row in first}
    second_event_ids = {row["event_id"] for row in second}
    assert first_event_ids.isdisjoint(second_event_ids)


def test_generate_corpus_batches_covers_exact_scale_in_seq_order():
    batches = list(
        generate_corpus_batches(2_000, 300, batch_size=100, tenant_id="tenant-x", environment="paper")
    )
    all_rows = [row for batch in batches for row in batch]
    assert len(all_rows) == 2_000
    seqs = [row["ingested_seq"] for row in all_rows]
    assert seqs == sorted(seqs)
    assert seqs == list(range(1, 2_001))
    assert all(len(batch) <= 100 for batch in batches)

    completed_loop_runs = sum(
        1 for row in all_rows if row["event_type"] == "reconciliation_completed"
    )
    assert completed_loop_runs == 300


def test_rss_bytes_is_positive():
    assert rss_bytes() > 0


def test_cli_requires_a_postgres_dsn():
    with pytest.raises(SystemExit, match="2"):
        main(["--events", "2", "--loop-runs", "1"])


def _managed_identity(commit: str = "a" * 40) -> dict[str, object]:
    return {
        "commit": commit,
        "dirty": False,
        "dirty_paths": [],
        "tree_status_sha256": "b" * 64,
        "source": "git",
    }


def _managed_args(tmp_path: Path) -> Namespace:
    return Namespace(
        repository_root=tmp_path,
        state_root=tmp_path / "docs" / "managed-runs",
        worktree_root=MANAGED_WORKTREE_ROOT,
        admission_lock=tmp_path / "managed-capacity.lock",
        unit_name=None,
        events=1_000_000,
        loop_runs=150_000,
        batch_size=500,
        fault_journey_count=4,
        catch_up_events=100_000,
        read_repeats=10,
        projection_dsn="postgresql://capacity:test@localhost:15432/pantheon",
    )


def test_managed_launch_requires_the_exact_full_acceptance_gate(tmp_path):
    args = _managed_args(tmp_path)
    args.events = 2_000

    with pytest.raises(ValueError, match="complete acceptance gate"):
        launch_managed_capacity_run(args)


def test_managed_launch_binds_clean_commit_and_private_dsn(tmp_path, monkeypatch):
    args = _managed_args(tmp_path)
    identity = _managed_identity()
    recorded: list[list[str]] = []
    runtime = {
        "python": str(tmp_path / "clean-python"),
        "module_file": str(tmp_path / "clean" / "services" / "trade_journey" / "lifecycle_projector_capacity.py"),
    }

    monkeypatch.setattr(capacity_module, "MANAGED_SECRET_ROOT", tmp_path / "runtime-secrets")
    monkeypatch.setattr(capacity_module, "_git_identity", lambda _root: identity)
    monkeypatch.setattr(
        capacity_module,
        "_assert_quiet_managed_host",
        lambda **_kwargs: {"containers": ["pantheon-postgres"], "networks": ["pantheon_default"]},
    )
    monkeypatch.setattr(
        capacity_module,
        "_create_clean_capacity_worktree",
        lambda *_args, **_kwargs: identity,
    )
    monkeypatch.setattr(
        capacity_module,
        "_provision_clean_capacity_runtime",
        lambda *_args, **_kwargs: runtime,
    )

    def fake_runner(command, **_kwargs):
        recorded.append(list(command))
        return subprocess.CompletedProcess(command, 0, "Running as unit test.service\n", "")

    manifest = launch_managed_capacity_run(args, runner=fake_runner)

    assert manifest["state"] == "running"
    assert manifest["git"] == identity
    assert manifest["requested"] == {
        "events": 1_000_000,
        "loop_runs": 150_000,
        "batch_size": 500,
        "catch_up_events": 100_000,
        "read_repeats": 10,
        "fault_journey_count": 4,
    }
    assert recorded and recorded[0][:4] == ["systemd-run", "--user", "--unit", manifest["unit"]]
    assert "--no-block" in recorded[0]
    assert "--setenv=PYTHONPATH=" in recorded[0]
    assert any(part.startswith("--property=ExecStopPost=") for part in recorded[0])
    assert runtime["python"] in recorded[0]
    assert manifest["runtime"] == runtime
    private_environment = Path(manifest["paths"]["private_environment"])
    assert private_environment.stat().st_mode & 0o777 == 0o600
    assert args.projection_dsn in private_environment.read_text(encoding="utf-8")
    assert args.projection_dsn not in Path(manifest["paths"]["status"]).read_text(encoding="utf-8")
    assert not str(private_environment).startswith(str(tmp_path / "docs"))
    assert Path(args.admission_lock).exists()


def test_managed_admission_rejects_e2e_or_task_resources():
    def fake_runner(command, **_kwargs):
        if command[:2] == ["systemctl", "--user"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["docker", "ps", "--format"]:
            return subprocess.CompletedProcess(command, 0, "task-e2e-sidecar\n", "")
        return subprocess.CompletedProcess(command, 0, "pantheon_default\n", "")

    with pytest.raises(RuntimeError, match="admission rejected"):
        _assert_quiet_managed_host(runner=fake_runner)


def test_managed_admission_rejects_unsafe_load_or_memory(monkeypatch):
    monkeypatch.setattr(
        capacity_module,
        "_managed_host_resources",
        lambda: {
            "cpu_count": 8,
            "load_1m": 8.0,
            "load_per_cpu": 1.0,
            "available_memory_bytes": 16 * 1024**3,
        },
    )

    def fake_runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(RuntimeError, match="unsafe host load"):
        _assert_quiet_managed_host(runner=fake_runner)

    monkeypatch.setattr(
        capacity_module,
        "_managed_host_resources",
        lambda: {
            "cpu_count": 8,
            "load_1m": 0.0,
            "load_per_cpu": 0.0,
            "available_memory_bytes": 1,
        },
    )
    with pytest.raises(RuntimeError, match="insufficient available memory"):
        _assert_quiet_managed_host(runner=fake_runner)


def test_managed_launch_failure_removes_secret_schema_and_worktree(tmp_path, monkeypatch):
    args = _managed_args(tmp_path)
    identity = _managed_identity()
    removed: list[tuple[Path, Path]] = []
    monkeypatch.setattr(capacity_module, "MANAGED_SECRET_ROOT", tmp_path / "runtime-secrets")
    monkeypatch.setattr(capacity_module, "_git_identity", lambda _root: identity)
    monkeypatch.setattr(capacity_module, "_assert_quiet_managed_host", lambda **_kwargs: {})
    monkeypatch.setattr(capacity_module, "_create_clean_capacity_worktree", lambda *_args, **_kwargs: identity)
    monkeypatch.setattr(
        capacity_module,
        "_provision_clean_capacity_runtime",
        lambda *_args, **_kwargs: {"python": str(tmp_path / "clean-python"), "module_file": str(tmp_path / "module.py")},
    )
    monkeypatch.setattr(capacity_module, "_fresh_capacity_schema", lambda *_args: "lifecycle_capacity_launch_failure")
    monkeypatch.setattr(capacity_module, "_teardown_capacity_schema", lambda *_args: True)
    monkeypatch.setattr(
        capacity_module,
        "_remove_clean_capacity_worktree",
        lambda root, clean, **_kwargs: removed.append((root, clean)),
    )

    def failing_runner(command, **_kwargs):
        if command[0] == "systemd-run":
            raise subprocess.CalledProcessError(1, command, stderr="submission failed")
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(subprocess.CalledProcessError):
        launch_managed_capacity_run(args, runner=failing_runner)

    manifests = list((tmp_path / "docs" / "managed-runs").glob("*/run.json"))
    assert len(manifests) == 1
    failed = capacity_module._read_json_object(manifests[0])
    assert failed["state"] == "launcher_failed"
    assert failed["cleanup"] == {
        "schema_dropped": True,
        "worktree_removed": True,
        "private_environment_removed": True,
        "error": None,
    }
    assert not list((tmp_path / "runtime-secrets").glob("*.env"))
    assert removed == [(tmp_path, MANAGED_WORKTREE_ROOT / failed["run_id"])]
    assert not Path(args.admission_lock).exists()


def test_managed_runtime_probe_rejects_module_outside_clean_worktree(tmp_path):
    provisioner = tmp_path / "scripts" / "dev" / "provision_python_distribution.py"
    provisioner.parent.mkdir(parents=True)
    provisioner.write_text("# test fixture\n", encoding="utf-8")
    clean_python = tmp_path / ".venv-pantheon" / "bin" / "python"
    clean_python.parent.mkdir(parents=True)
    clean_python.touch()

    def fake_runner(command, **_kwargs):
        if command[1] == str(provisioner):
            return subprocess.CompletedProcess(command, 0, f"{clean_python}\n", "")
        return subprocess.CompletedProcess(
            command,
            0,
            '{"python": "' + str(clean_python) + '", "module_file": "/foreign/module.py"}',
            "",
        )

    with pytest.raises(RuntimeError, match="outside the clean worktree"):
        capacity_module._provision_clean_capacity_runtime(tmp_path, runner=fake_runner)


def test_managed_runtime_probe_uses_empty_unique_cwd_and_clean_bindings(tmp_path, monkeypatch):
    provisioner = tmp_path / "scripts" / "dev" / "provision_python_distribution.py"
    provisioner.parent.mkdir(parents=True)
    provisioner.write_text("# test fixture\n", encoding="utf-8")
    clean_python = tmp_path / ".venv-pantheon" / "bin" / "python"
    clean_python.parent.mkdir(parents=True)
    clean_python.touch()
    clean_module = tmp_path / "services" / "trade_journey" / "lifecycle_projector_capacity.py"
    clean_module.parent.mkdir(parents=True)
    clean_module.touch()

    contaminated_temp_root = tmp_path / "contaminated-tmp"
    shadowed_services = contaminated_temp_root / "services"
    shadowed_services.mkdir(parents=True)
    (shadowed_services / "__init__.py").write_text("# foreign shadow\n", encoding="utf-8")
    monkeypatch.setattr(capacity_module.tempfile, "tempdir", str(contaminated_temp_root))
    recorded: dict[str, object] = {}

    def fake_runner(command, **kwargs):
        if command[1] == str(provisioner):
            return subprocess.CompletedProcess(command, 0, f"{clean_python}\n", "")
        probe_cwd = Path(str(kwargs["cwd"]))
        recorded["cwd"] = probe_cwd
        assert probe_cwd.parent == contaminated_temp_root
        assert probe_cwd != contaminated_temp_root
        assert list(probe_cwd.iterdir()) == []
        assert not (probe_cwd / "services").exists()
        assert kwargs["env"].get("PYTHONPATH") is None
        assert command[:2] == [str(clean_python), "-c"]
        return subprocess.CompletedProcess(
            command,
            0,
            '{"python": "' + str(clean_python) + '", "module_file": "' + str(clean_module) + '"}',
            "",
        )

    runtime = capacity_module._provision_clean_capacity_runtime(tmp_path, runner=fake_runner)

    assert runtime == {"python": str(clean_python.absolute()), "module_file": str(clean_module.resolve())}
    assert not Path(str(recorded["cwd"])).exists()


def test_managed_worker_records_result_before_systemd_cleanup(tmp_path, monkeypatch):
    paths = _managed_state_paths(tmp_path)
    manifest = {
        "task_id": "LIFECYCLE-PROJ-CAPACITY-001",
        "run_id": "managed-test",
        "state": "running",
        "clean_repository_root": "/clean/source",
        "schema": "lifecycle_capacity_managed_test",
        "paths": paths,
        "requested": {
            "events": 1_000_000,
            "loop_runs": 150_000,
            "batch_size": 500,
            "fault_journey_count": 4,
            "catch_up_events": 100_000,
            "read_repeats": 10,
        },
    }
    _write_json_atomically(tmp_path / "run.json", manifest)
    received: list[str] = []

    def fake_main(arguments):
        received.extend(arguments)
        return 0

    monkeypatch.setattr(capacity_module, "main", fake_main)
    monkeypatch.setattr(capacity_module, "_assert_managed_worker_binding", lambda _manifest: None)

    assert run_managed_capacity_worker(tmp_path) == 0
    assert "--projection-schema" in received
    assert received[received.index("--projection-schema") + 1] == manifest["schema"]
    completed = capacity_module._read_json_object(tmp_path / "run.json")
    assert completed["state"] == "benchmark_finished"
    assert completed["benchmark_exit_code"] == 0


def test_managed_cleanup_tears_down_recorded_schema_and_releases_lock(tmp_path, monkeypatch):
    lock_path = tmp_path / "capacity.lock"
    lock_path.write_text("managed-test\n", encoding="utf-8")
    monkeypatch.setattr(capacity_module, "MANAGED_SECRET_ROOT", tmp_path / "runtime-secrets")
    paths = _managed_state_paths(tmp_path)
    private_environment = Path(paths["private_environment"])
    private_environment.parent.mkdir(parents=True)
    private_environment.write_text(
        "LIFECYCLE_PROJECTOR_PROJECTION_DSN=postgresql://capacity:test@localhost/db\n",
        encoding="utf-8",
    )
    manifest = {
        "task_id": "LIFECYCLE-PROJ-CAPACITY-001",
        "run_id": "managed-test",
        "state": "benchmark_finished",
        "repository_root": "/repository",
        "clean_repository_root": str(MANAGED_WORKTREE_ROOT / "managed-test"),
        "schema": "lifecycle_capacity_managed_test",
        "admission": {"lock_path": str(lock_path)},
        "paths": paths,
    }
    _write_json_atomically(tmp_path / "run.json", manifest)
    removed: list[tuple[Path, Path]] = []

    monkeypatch.setenv("LIFECYCLE_PROJECTOR_PROJECTION_DSN", "postgresql://capacity:test@localhost/db")
    monkeypatch.setattr(capacity_module, "_teardown_capacity_schema", lambda dsn, schema: dsn.endswith("/db") and schema == manifest["schema"])
    monkeypatch.setattr(
        capacity_module,
        "_remove_clean_capacity_worktree",
        lambda root, clean, **_kwargs: removed.append((root, clean)),
    )

    assert cleanup_managed_capacity_run(tmp_path)
    assert removed == [(Path("/repository"), MANAGED_WORKTREE_ROOT / "managed-test")]
    assert not lock_path.exists()
    assert not private_environment.exists()
    cleaned = capacity_module._read_json_object(tmp_path / "run.json")
    assert cleaned["state"] == "cleaned"
    assert cleaned["cleanup"] == {
        "schema_dropped": True,
        "worktree_removed": True,
        "private_environment_removed": True,
        "error": None,
    }


def test_collect_managed_result_verifies_checksum_commit_schema_and_teardown(tmp_path, monkeypatch):
    identity = _managed_identity("c" * 40)
    paths = _managed_state_paths(tmp_path)
    evidence = {
        "task_id": "LIFECYCLE-PROJ-CAPACITY-001",
        "git": identity,
        "projection_schema": "lifecycle_capacity_managed_collect",
        "teardown": {"schema_dropped": True},
        "gate_failures": [],
    }
    result_path = Path(paths["result"])
    _write_json_atomically(result_path, evidence)
    checksum = capacity_module.hashlib.sha256(result_path.read_bytes()).hexdigest()
    Path(paths["result_checksum"]).write_text(
        f"{checksum}  evidence.json\n", encoding="utf-8"
    )
    manifest = {
        "task_id": "LIFECYCLE-PROJ-CAPACITY-001",
        "run_id": "managed-collect",
        "git": identity,
        "schema": "lifecycle_capacity_managed_collect",
        "paths": paths,
        "benchmark_exit_code": 0,
        "cleanup": {
            "schema_dropped": True,
            "worktree_removed": True,
            "private_environment_removed": True,
            "error": None,
        },
        "systemd": {
            "returncode": 0,
            "status": "ActiveState=inactive\nResult=success\nExecMainStatus=0",
            "stderr": "",
        },
    }
    monkeypatch.setattr(capacity_module, "managed_capacity_status", lambda _state: manifest)

    collection = collect_managed_capacity_result(tmp_path)

    assert collection["evidence_sha256"] == checksum
    assert collection["gate_failures"] == []
    assert (tmp_path / "collection.json").exists()


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ({"benchmark_exit_code": 1}, "exit code is nonzero"),
        ({"cleanup": {"schema_dropped": True, "worktree_removed": False}}, "cleanup did not complete"),
        ({"systemd": {"returncode": 0, "status": "Result=exit-code\nExecMainStatus=1"}}, "systemd result"),
    ],
)
def test_managed_collect_rejects_failed_execution_state(tmp_path, monkeypatch, mutation, error):
    identity = _managed_identity("d" * 40)
    paths = _managed_state_paths(tmp_path)
    evidence = {
        "task_id": "LIFECYCLE-PROJ-CAPACITY-001",
        "git": identity,
        "projection_schema": "lifecycle_capacity_managed_collect_failure",
        "teardown": {"schema_dropped": True},
        "gate_failures": [],
    }
    result_path = Path(paths["result"])
    _write_json_atomically(result_path, evidence)
    Path(paths["result_checksum"]).write_text(
        f"{capacity_module.hashlib.sha256(result_path.read_bytes()).hexdigest()}  evidence.json\n",
        encoding="utf-8",
    )
    manifest = {
        "task_id": "LIFECYCLE-PROJ-CAPACITY-001",
        "run_id": "managed-collect-failure",
        "git": identity,
        "schema": "lifecycle_capacity_managed_collect_failure",
        "paths": paths,
        "benchmark_exit_code": 0,
        "cleanup": {
            "schema_dropped": True,
            "worktree_removed": True,
            "private_environment_removed": True,
            "error": None,
        },
        "systemd": {"returncode": 0, "status": "Result=success\nExecMainStatus=0"},
    }
    manifest.update(mutation)
    monkeypatch.setattr(capacity_module, "managed_capacity_status", lambda _state: manifest)

    with pytest.raises(RuntimeError, match=error):
        collect_managed_capacity_result(tmp_path)


def test_managed_collect_rejects_nonempty_gate_failures(tmp_path, monkeypatch):
    identity = _managed_identity("e" * 40)
    paths = _managed_state_paths(tmp_path)
    evidence = {
        "task_id": "LIFECYCLE-PROJ-CAPACITY-001",
        "git": identity,
        "projection_schema": "lifecycle_capacity_managed_collect_gates",
        "teardown": {"schema_dropped": True},
        "gate_failures": ["peak RSS"],
    }
    result_path = Path(paths["result"])
    _write_json_atomically(result_path, evidence)
    Path(paths["result_checksum"]).write_text(
        f"{capacity_module.hashlib.sha256(result_path.read_bytes()).hexdigest()}  evidence.json\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        capacity_module,
        "managed_capacity_status",
        lambda _state: {
            "run_id": "managed-collect-gates",
            "git": identity,
            "schema": evidence["projection_schema"],
            "paths": paths,
            "benchmark_exit_code": 0,
            "cleanup": {
                "schema_dropped": True,
                "worktree_removed": True,
                "private_environment_removed": True,
                "error": None,
            },
            "systemd": {"returncode": 0, "status": "Result=success\nExecMainStatus=0"},
        },
    )

    with pytest.raises(RuntimeError, match="nonempty gate failures"):
        collect_managed_capacity_result(tmp_path)


def test_run_capacity_benchmark_projects_full_small_scale_corpus(capacity_dsn):
    schema = _schema()
    try:
        store = ProjectionStore(capacity_dsn, schema=schema, bootstrap=True)
        projector = RelationalLifecycleProjector(store, deployment_sha="capacity-test")
        report = run_capacity_benchmark(
            projector,
            total_events=2_000,
            total_loop_runs=300,
            batch_size=500,
        )

        assert isinstance(report, CapacityReport)
        assert len(report.samples) == 4
        assert projector.checkpoint == 2_000
        assert report.peak_rss_bytes >= report.steady_rss_bytes > 0
        assert report.batch_latency_p95_seconds >= 0
        # Small-scale runs are nowhere near the section 14 ceilings.
        assert report.gate_failures() == []

        summary = report.to_dict()
        assert summary["total_events"] == 2_000
        assert summary["total_loop_runs"] == 300
        assert len(summary["samples"]) == 4
    finally:
        assert _teardown_capacity_schema(capacity_dsn, schema)


def test_capacity_report_flags_gate_violations_without_a_full_scale_run():
    report = CapacityReport(total_events=1_000_000, total_loop_runs=150_000, batch_size=500)
    from services.trade_journey.lifecycle_projector_capacity import BatchSample

    # Synthesize an over-budget steady/peak RSS and slow batch latency so
    # gate_failures() can be proven without actually allocating gigabytes or
    # running the full corpus.
    report.samples = [
        BatchSample(
            batch_index=0,
            events_applied=500_000,
            checkpoint=500_000,
            latency_seconds=1.0,
            rss_bytes=int(1.0 * (1024 ** 3)),
            backlog_age_seconds=0.0,
        ),
        BatchSample(
            batch_index=1,
            events_applied=500_000,
            checkpoint=1_000_000,
            latency_seconds=6.0,
            rss_bytes=int(2.6 * (1024 ** 3)),
            backlog_age_seconds=0.0,
        ),
    ]
    failures = report.gate_failures()
    assert any("peak RSS" in f for f in failures)
    assert any("batch latency" in f for f in failures)
    assert any("RSS slope" in f for f in failures)


@pytest.mark.parametrize("scenario", FAULT_SCENARIOS, ids=lambda s: s.__name__)
def test_fault_scenario_passes_in_fresh_postgres_schema(capacity_dsn, scenario):
    rows = []
    seq = 1
    for journey_index in range(3):
        stages = _journey_event_types(8)
        journey = journey_rows(journey_index, event_types=stages, starting_seq=seq)
        rows.extend(journey)
        seq += len(journey)

    schema = _schema()
    ProjectionStore(capacity_dsn, schema=schema, bootstrap=True)
    try:
        result = scenario(capacity_dsn, schema, rows)
        assert result.passed, f"{result.name} failed: {result.detail}"
    finally:
        assert _teardown_capacity_schema(capacity_dsn, schema)


def test_run_fault_matrix_runs_every_scenario_in_isolated_postgres_schemas(capacity_dsn):
    results = run_fault_matrix(capacity_dsn, journey_count=2)

    assert len(results) == len(FAULT_SCENARIOS)
    assert {r.name for r in results} == {
        s.__name__.removeprefix("scenario_") for s in FAULT_SCENARIOS
    }
    assert all(r.passed for r in results), [
        (r.name, r.detail) for r in results if not r.passed
    ]
    # Every scenario reports that its harness-owned PostgreSQL schema was
    # removed; a shared schema would leak receipts into the next fault.
    assert all("teardown=True" in r.detail for r in results)


def test_bff_capacity_reads_use_real_repository_and_page_size(capacity_dsn):
    schema = _schema()
    try:
        projector = RelationalLifecycleProjector(
            ProjectionStore(capacity_dsn, schema=schema, bootstrap=True),
            deployment_sha="capacity-bff-read-test",
        )
        run_capacity_benchmark(
            projector, total_events=2_000, total_loop_runs=300, batch_size=500
        )
        reads = run_bff_read_benchmark(capacity_dsn, schema, repeats=2, page_size=200)
        assert set(reads.samples) == {"list", "detail", "timeline", "loop", "loop_detail"}
        assert not reads.gate_failures()
        plans = explain_bff_read_paths(capacity_dsn, schema)
        assert set(plans) == {"list", "detail", "timeline", "loop"}
        assert all(int(plan["page_limit"]) <= 200 for plan in plans.values())
    finally:
        assert _teardown_capacity_schema(capacity_dsn, schema)
