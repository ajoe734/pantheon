from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dispatch_twelve_loop_gap_2026_07_26.py"
CATALOG = (
    ROOT
    / "docs"
    / "bff"
    / "execution-tasks"
    / "2026-07-31-l12-current-gap-supervisor-dispatch"
    / "guarded-remediation-tasks.json"
)
SPEC = importlib.util.spec_from_file_location("current_guarded_dispatch", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
from rewrite.task_state_store import append_state_commit, load_snapshot


def catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def readiness(
    *,
    antigravity: bool = True,
    claude2: bool = False,
    claude: bool = False,
    codex2: bool = True,
    codex: bool = True,
) -> dict:
    ready_by_agent = {
        "Antigravity": antigravity,
        "Claude2": claude2,
        "Claude": claude,
        "Codex2": codex2,
        "Codex": codex,
    }
    payload = {
        "schema_version": 1,
        "source": "live-supervisor-readiness",
        "observed_at": "2026-07-31T16:00:00Z",
        "provider_capabilities_generated_at": "2026-07-31T15:59:00Z",
        "candidates": {
            agent: {
                "agent": agent,
                "ready": is_ready,
                "reasons": [] if is_ready else ["auth_not_ready"],
            }
            for agent, is_ready in ready_by_agent.items()
        },
    }
    payload["sha256"] = module.canonical_json_sha256(payload)
    return payload


def external_rows() -> list[dict]:
    dependency_ids = sorted(
        module.CURRENT_EXTERNAL_DEPENDENCY_IDS
        | module.CURRENT_RUNTIME_GATE_IDS
    )
    return [
        {
            "id": task_id,
            "status": "done",
            "artifacts": [],
        }
        for task_id in dependency_ids
    ]


def active_state(tasks: list[dict] | None = None) -> dict:
    return {"tasks": [*external_rows(), *(tasks or [])]}


def canonical_test_state(ai_status) -> dict:
    state = ai_status.default_state()
    state["tasks"] = [
        {
            "id": row["id"],
            "title": f"External dependency {row['id']}",
            "summary_zh": "Authoritative transaction fixture dependency",
            "phase": "Test prerequisite",
            "owner": "Codex2",
            "reviewer": "Codex",
            "status": "done",
            "depends_on": [],
            "artifacts": [],
            "acceptance": ["fixture dependency is complete"],
            "next": "Satisfied",
            "last_update": "2026-07-31T16:00:00Z",
        }
        for row in external_rows()
    ]
    state["handoffs"] = []
    state["blockers"] = []
    return state


def isolated_ai_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(tmp_path))
    monkeypatch.setenv("PANTHEON_TASK_STATE_STORE_MODE", "authoritative")
    spec = importlib.util.spec_from_file_location(
        f"guarded_dispatch_ai_status_{tmp_path.name}",
        ROOT / "scripts" / "ai_status.py",
    )
    assert spec and spec.loader
    ai_status = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ai_status)
    ai_status.load_config = lambda: {}
    ai_status.validate_status_command_runtime_binding = lambda: None
    ai_status.validate_status_root_binding = lambda: None
    ai_status.refresh_derived_status_views_if_current = lambda _state: True
    monkeypatch.setattr(module, "_load_ai_status_module", lambda _script: ai_status)
    return ai_status


def current_apply_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, dict, list[dict], dict, dict, tuple[Path, Path, Path]]:
    ai_status = isolated_ai_status(tmp_path, monkeypatch)
    state = canonical_test_state(ai_status)
    authority = write_authority(tmp_path, state)
    config_path, runtime_path, capabilities_path = readiness_files(tmp_path)
    command_runtime = {
        "root": str(ROOT),
        "script": str(ROOT / "scripts" / "ai_status.py"),
        "source_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "remote": "ajoe734/pantheon",
        "base_ref": "origin/dev",
    }
    payload = catalog()
    tasks = module.validate_catalog(payload)
    monkeypatch.setenv("AI_NAME", "Codex2")
    return (
        ai_status,
        payload,
        tasks,
        authority,
        command_runtime,
        (config_path, runtime_path, capabilities_path),
    )


def apply_fixture(
    fixture: tuple[object, dict, list[dict], dict, dict, tuple[Path, Path, Path]],
    *,
    status_root: Path,
) -> dict:
    _ai_status, payload, tasks, authority, command_runtime, readiness_paths = fixture
    config_path, runtime_path, capabilities_path = readiness_paths
    return module.apply_current_materialization_atomic(
        catalog=payload,
        tasks=tasks,
        status_root=status_root,
        authority=authority,
        command_runtime=command_runtime,
        config_path=config_path,
        runtime_state_path=runtime_path,
        provider_capabilities_path=capabilities_path,
    )


def materialized_row(
    task: dict,
    *,
    payload: dict,
    tasks: list[dict],
    live_readiness: dict,
    status: str = "todo",
) -> dict:
    resolved = module.resolve_current_assignment(task, readiness=live_readiness)
    env = module.assignment_environment(resolved, catalog=payload, tasks=tasks)
    metadata = json.loads(env["TASK_METADATA_JSON"])
    row = {
        "id": task["id"],
        "title": task["title"],
        "summary_zh": task["summary_zh"],
        "phase": task["phase"],
        "owner": resolved["owner"],
        "reviewer": resolved["reviewer"],
        "status": status,
        "depends_on": list(task["depends_on"]),
        "artifacts": list(task["artifacts"]),
        "acceptance": list(task["acceptance"]),
        "next": task["next"],
        "last_update": "2026-07-31T16:00:00Z",
    }
    row.update(metadata)
    return row


def write_authority(root: Path, state: dict) -> dict:
    event_log = root / "task-state-events.jsonl"
    append_state_commit(event_log, state, source="test")
    (root / "ai-status.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    return {
        "mode": "authoritative",
        "event_log": event_log,
        "status_file": root / "ai-status.json",
    }


def readiness_files(root: Path) -> tuple[Path, Path, Path]:
    agent_names = module.CURRENT_OWNER_PREFERENCE
    config = {
        "agents": {
            agent.lower(): {
                "display_name": agent,
                "provider": agent.lower(),
            }
            for agent in agent_names
        },
        "providers": {
            agent.lower(): {"account": agent.lower()}
            for agent in agent_names
        },
        "ready_dispatcher": {
            "disabled_agents": [],
            "sidecar_only_agents": [],
        },
    }
    runtime = {
        "supervisor": {"last_successful_loop_at": "2026-07-31T16:00:00Z"},
        "provider_guardrails": {
            "dispatch_pauses": {
                "claude2": {"pause_kind": "auth"},
                "claude": {"pause_kind": "auth"},
            }
        },
    }
    capabilities = {
        "generated_at": "2026-07-31T15:59:00Z",
        "providers": {},
        "agent_adapters": {},
    }
    for agent in agent_names:
        provider = agent.lower()
        ready = agent in {"Antigravity", "Codex2", "Codex"}
        capabilities["providers"][provider] = {
            "auth_ready": ready,
            "local_cli_worker_supported": ready,
            "supports_auto_approve": ready,
            "last_auth_probe_at": "2026-07-31T15:59:00Z",
            "auth_probe": {"status": "ready" if ready else "auth_not_ready"},
        }
        capabilities["agent_adapters"][provider] = {
            "supported": True,
            "can_auto_deliver": ready,
        }
    config_path = root / "config.json"
    runtime_path = root / "state.json"
    capabilities_path = root / "provider-capabilities.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    capabilities_path.write_text(json.dumps(capabilities), encoding="utf-8")
    return config_path, runtime_path, capabilities_path


def test_current_catalog_validate_only_binds_exact_pr_head() -> None:
    result = subprocess.run(
        ["python3", str(SCRIPT), "--validate-only", "--current"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body == {
        "catalog_file_sha256": module.CURRENT_CATALOG_FILE_SHA256,
        "catalog_sha256": module.CURRENT_CATALOG_CANONICAL_SHA256,
        "maximum_parallel_frontier_G1": 25,
        "program_id": module.CURRENT_PROGRAM_ID,
        "source_branch_ci_conclusion": "success",
        "source_branch_ci_run": 30635898120,
        "source_head": module.CURRENT_SOURCE_HEAD,
        "source_pr": 4394,
        "status": "valid",
        "task_count": 28,
    }


def test_current_catalog_rejects_duplicate_dangling_and_g1_overlap() -> None:
    duplicate = catalog()
    duplicate["tasks"][1]["id"] = duplicate["tasks"][0]["id"]
    with pytest.raises(module.DispatchError, match="duplicate task IDs"):
        module.validate_catalog(duplicate)

    dangling = catalog()
    dangling["tasks"][0]["depends_on"].append("UNKNOWN-DEPENDENCY")
    with pytest.raises(module.DispatchError, match="unknown task"):
        module.validate_catalog(dangling)

    overlap = catalog()
    overlap["tasks"][1]["artifacts"].append("services/training-session/subpath")
    with pytest.raises(module.DispatchError, match="G1 artifact-prefix overlap"):
        module.validate_catalog(overlap)


def test_current_dry_run_materializes_only_safe_g1_and_records_fallbacks(
    tmp_path: Path,
) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    live_readiness = readiness()
    state = active_state()
    before = deepcopy(state)

    plan = module.plan_materialization(
        payload,
        tasks,
        status_root=tmp_path,
        state=state,
        readiness=live_readiness,
    )

    assert len(plan["create"]) == 25
    assert plan["create"] == [task["id"] for task in tasks if task["wave"] == "G1"]
    assert plan["deferred"] == [task["id"] for task in tasks if task["wave"] != "G1"]
    assert state == before
    decision = plan["assignment_decisions"][plan["create"][0]]
    assert decision["owner"] == "Antigravity"
    assert decision["reviewer"] == "Codex2"
    assert decision["owner_fallbacks"] == []
    assert [item["agent"] for item in decision["reviewer_fallbacks"]] == [
        "Claude2",
        "Antigravity",
    ]


def file_identity(path: Path) -> tuple[bool, bytes | None]:
    return path.exists(), path.read_bytes() if path.exists() else None


def stat_identity(path: Path) -> tuple[int, int, int, int, int, int] | None:
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    return (
        stat.S_IFMT(info.st_mode) | stat.S_IMODE(info.st_mode),
        info.st_ino,
        info.st_size,
        info.st_atime_ns,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def directory_identity(parent: Path) -> tuple[str, ...]:
    return tuple(sorted(path.name for path in parent.iterdir()))


def checkpoint_temp_files(checkpoint: Path) -> tuple[str, ...]:
    return tuple(
        sorted(path.name for path in checkpoint.parent.glob(f"{checkpoint.name}.*.tmp"))
    )


@pytest.mark.parametrize(
    ("checkpoint_case", "checkpoint_used", "revalidated_tail_events"),
    [
        ("warm", True, 0),
        ("stale_tail", True, 1),
        ("missing", False, 1),
        ("corrupt", False, 1),
    ],
)
def test_current_dry_run_cli_is_read_only(
    checkpoint_case: str,
    checkpoint_used: bool,
    revalidated_tail_events: int,
    tmp_path: Path,
) -> None:
    state = active_state()
    authority = write_authority(tmp_path, state)
    checkpoint = authority["event_log"].with_name(
        f"{authority['event_log'].name}.checkpoint.json"
    )
    if checkpoint_case == "stale_tail":
        stale_checkpoint = checkpoint.read_bytes()
        tail_state = deepcopy(state)
        tail_state["updated_at"] = "2026-08-02T09:40:00Z"
        append_state_commit(
            authority["event_log"],
            tail_state,
            source="test-stale-tail",
        )
        checkpoint.write_bytes(stale_checkpoint)
    elif checkpoint_case == "missing":
        checkpoint.unlink()
    elif checkpoint_case == "corrupt":
        checkpoint.write_bytes(b"not-json")

    live_config = tmp_path / "live-config.json"
    live_config.write_text(
        json.dumps(
            {
                "paths": {"status_file": str(tmp_path / "ai-status.json")},
                "task_state_store": {
                    "mode": "authoritative",
                    "event_log": str(authority["event_log"]),
                },
            }
        ),
        encoding="utf-8",
    )
    config_path, runtime_path, capabilities_path = readiness_files(tmp_path)
    lock = authority["event_log"].with_name(f"{authority['event_log'].name}.lock")
    lock.write_bytes(b"provisioned observational lock\n")
    lock.chmod(0o640)
    expected_snapshot = load_snapshot(
        authority["event_log"],
        refresh_checkpoint=False,
    )
    before_files = {
        "projection": file_identity(tmp_path / "ai-status.json"),
        "journal": file_identity(authority["event_log"]),
        "checkpoint": file_identity(checkpoint),
        "lock": file_identity(lock),
    }
    before_stats = {
        name: stat_identity(path)
        for name, path in {
            "projection": tmp_path / "ai-status.json",
            "journal": authority["event_log"],
            "checkpoint": checkpoint,
            "lock": lock,
        }.items()
    }
    before_directory = directory_identity(tmp_path)
    before_temp_files = checkpoint_temp_files(checkpoint)
    env = {**os.environ, "PANTHEON_STATUS_ROOT": str(tmp_path)}
    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--dry-run",
            "--catalog",
            str(CATALOG),
            "--live-config",
            str(live_config),
            "--readiness-config",
            str(config_path),
            "--runtime-state",
            str(runtime_path),
            "--provider-capabilities",
            str(capabilities_path),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["status"] == "dry_run"
    assert len(body["create"]) == 25
    assert len(body["deferred"]) == 3
    assert body["task_state_store"]["snapshot"] == {
        "byte_size": authority["event_log"].stat().st_size,
        "checkpoint_used": checkpoint_used,
        "event_count": expected_snapshot["event_count"],
        "last_event_id": expected_snapshot["last_event_id"],
        "last_event_sha256": expected_snapshot["last_event_sha256"],
        "revalidated_tail_events": revalidated_tail_events,
        "state_sha256": expected_snapshot["state_sha256"],
    }
    assert {
        "projection": file_identity(tmp_path / "ai-status.json"),
        "journal": file_identity(authority["event_log"]),
        "checkpoint": file_identity(checkpoint),
        "lock": file_identity(lock),
    } == before_files
    assert {
        name: stat_identity(path)
        for name, path in {
            "projection": tmp_path / "ai-status.json",
            "journal": authority["event_log"],
            "checkpoint": checkpoint,
            "lock": lock,
        }.items()
    } == before_stats
    assert directory_identity(tmp_path) == before_directory
    assert checkpoint_temp_files(checkpoint) == before_temp_files


def test_current_dry_run_fails_closed_without_provisioned_lock(
    tmp_path: Path,
) -> None:
    state = active_state()
    authority = write_authority(tmp_path, state)
    lock = authority["event_log"].with_name(f"{authority['event_log'].name}.lock")
    lock.unlink()
    live_config = tmp_path / "live-config.json"
    live_config.write_text(
        json.dumps(
            {
                "paths": {"status_file": str(tmp_path / "ai-status.json")},
                "task_state_store": {
                    "mode": "authoritative",
                    "event_log": str(authority["event_log"]),
                },
            }
        ),
        encoding="utf-8",
    )
    config_path, runtime_path, capabilities_path = readiness_files(tmp_path)
    before = directory_identity(tmp_path)
    before_journal = file_identity(authority["event_log"])
    before_journal_stat = stat_identity(authority["event_log"])

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--dry-run",
            "--catalog",
            str(CATALOG),
            "--live-config",
            str(live_config),
            "--readiness-config",
            str(config_path),
            "--runtime-state",
            str(runtime_path),
            "--provider-capabilities",
            str(capabilities_path),
        ],
        cwd=ROOT,
        env={**os.environ, "PANTHEON_STATUS_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "lock must be an existing regular file" in result.stderr
    assert not lock.exists()
    assert directory_identity(tmp_path) == before
    assert file_identity(authority["event_log"]) == before_journal
    assert stat_identity(authority["event_log"]) == before_journal_stat


@pytest.mark.parametrize("missing", ["parent", "event"])
def test_current_dry_run_fails_closed_without_journal_authority(
    missing: str,
    tmp_path: Path,
) -> None:
    (tmp_path / "ai-status.json").write_text(
        json.dumps(active_state()),
        encoding="utf-8",
    )
    event_log = (
        tmp_path / "absent-parent" / "task-state-events.jsonl"
        if missing == "parent"
        else tmp_path / "task-state-events.jsonl"
    )
    live_config = tmp_path / "live-config.json"
    live_config.write_text(
        json.dumps(
            {
                "paths": {"status_file": str(tmp_path / "ai-status.json")},
                "task_state_store": {
                    "mode": "authoritative",
                    "event_log": str(event_log),
                },
            }
        ),
        encoding="utf-8",
    )
    config_path, runtime_path, capabilities_path = readiness_files(tmp_path)
    before = directory_identity(tmp_path)

    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--dry-run",
            "--catalog",
            str(CATALOG),
            "--live-config",
            str(live_config),
            "--readiness-config",
            str(config_path),
            "--runtime-state",
            str(runtime_path),
            "--provider-capabilities",
            str(capabilities_path),
        ],
        cwd=ROOT,
        env={**os.environ, "PANTHEON_STATUS_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "journal is missing or empty" in result.stderr
    assert not event_log.exists()
    if missing == "parent":
        assert not event_log.parent.exists()
    assert directory_identity(tmp_path) == before


def test_authority_uses_one_validated_snapshot_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = active_state()
    snapshot = {
        "event_count": 8632,
        "byte_size": 2_174_900_966,
        "last_event_id": "task-state-head",
        "last_event_sha256": "a" * 64,
        "state": state,
        "state_sha256": "b" * 64,
        "resumed_from_checkpoint": True,
        "revalidated_events": 0,
    }
    calls: list[tuple[Path, bool]] = []

    def one_snapshot(path: Path, *, refresh_checkpoint: bool = True) -> dict:
        calls.append((path, refresh_checkpoint))
        return snapshot

    monkeypatch.setattr(module, "load_snapshot", one_snapshot)
    authority = {"event_log": tmp_path / "task-state-events.jsonl"}

    loaded = module.load_authoritative_task_snapshot(authority)

    assert loaded is snapshot
    assert calls == [(authority["event_log"], True)]
    assert module.load_authoritative_task_state(authority) == state
    assert calls == [
        (authority["event_log"], True),
        (authority["event_log"], True),
    ]
    assert (
        module.load_authoritative_task_snapshot(
            authority,
            refresh_checkpoint=False,
        )
        is snapshot
    )
    assert calls[-1] == (authority["event_log"], False)
    assert module.authoritative_snapshot_evidence(loaded) == {
        "event_count": 8632,
        "byte_size": 2_174_900_966,
        "last_event_id": "task-state-head",
        "last_event_sha256": "a" * 64,
        "state_sha256": "b" * 64,
        "checkpoint_used": True,
        "revalidated_tail_events": 0,
    }


def test_current_plan_accepts_archived_dependency(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    archived_id = "L12-FLEET-001"
    state = active_state()
    state["tasks"] = [task for task in state["tasks"] if task["id"] != archived_id]
    archive_root = tmp_path / "ai-task-archive" / "tasks"
    archive_root.mkdir(parents=True)
    (archive_root / f"{archived_id}.json").write_text(
        json.dumps(
            {
                "task_id": archived_id,
                "terminal_status": "done",
                "task": {"id": archived_id, "status": "done"},
            }
        ),
        encoding="utf-8",
    )

    plan = module.plan_materialization(
        payload,
        tasks,
        status_root=tmp_path,
        state=state,
        readiness=readiness(),
    )

    assert plan["external_dependencies"][archived_id] == {
        "source": "archive",
        "status": "done",
        "satisfied": True,
    }
    assert len(plan["create"]) == 25


def test_current_readiness_falls_back_without_claiming_unavailable_provider() -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    live_readiness = readiness(
        antigravity=False,
        claude2=True,
        claude=True,
        codex2=True,
    )

    resolved = module.resolve_current_assignment(tasks[0], readiness=live_readiness)

    decision = resolved["provider_assignment_resolution"]
    assert resolved["owner"] == "Claude2"
    assert resolved["reviewer"] == "Codex2"
    assert decision["owner_fallbacks"][0]["agent"] == "Antigravity"
    assert [item["agent"] for item in decision["reviewer_fallbacks"]] == [
        "Claude2",
        "Antigravity",
    ]
    assert all(
        item["selected"] is False
        for item in decision["reviewer_fallbacks"]
    )


def test_current_replay_is_exact_and_partial_materialization_fails(
    tmp_path: Path,
) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    live_readiness = readiness()
    g1_rows = [
        materialized_row(
            task,
            payload=payload,
            tasks=tasks,
            live_readiness=live_readiness,
        )
        for task in tasks
        if task["wave"] == "G1"
    ]
    replay = module.plan_materialization(
        payload,
        tasks,
        status_root=tmp_path,
        state=active_state(g1_rows),
        readiness=live_readiness,
    )
    assert replay["create"] == []
    assert set(replay["exact"]) == {row["id"] for row in g1_rows}

    with pytest.raises(module.DispatchError, match="partial materialization"):
        module.plan_materialization(
            payload,
            tasks,
            status_root=tmp_path,
            state=active_state(g1_rows[:1]),
            readiness=live_readiness,
        )


@pytest.mark.parametrize("truth_source", ["active", "archive"])
@pytest.mark.parametrize(
    "mutation",
    ["truncated_resolution", "contradictory_resolution", "missing_catalog_defaults"],
)
def test_current_full_g1_replay_rejects_inexact_assignment_evidence(
    truth_source: str,
    mutation: str,
    tmp_path: Path,
) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    live_readiness = readiness()
    g1_rows = [
        materialized_row(
            task,
            payload=payload,
            tasks=tasks,
            live_readiness=live_readiness,
            status="done" if truth_source == "archive" else "todo",
        )
        for task in tasks
        if task["wave"] == "G1"
    ]
    for row in g1_rows:
        if mutation == "truncated_resolution":
            row["provider_assignment_resolution"] = {
                "owner": row["owner"],
                "reviewer": row["reviewer"],
            }
        elif mutation == "contradictory_resolution":
            evaluation = row["provider_assignment_resolution"]["owner_evaluations"][0]
            evaluation.update(
                {
                    "ready": False,
                    "reasons": ["auth_not_ready"],
                    "selected": True,
                }
            )
        else:
            row.pop("catalog_assignment_defaults")

    if truth_source == "active":
        state = active_state(g1_rows)
    else:
        state = active_state()
        archive_root = tmp_path / "ai-task-archive" / "tasks"
        archive_root.mkdir(parents=True)
        for row in g1_rows:
            (archive_root / f"{row['id']}.json").write_text(
                json.dumps(
                    {
                        "task_id": row["id"],
                        "terminal_status": "done",
                        "task": row,
                    }
                ),
                encoding="utf-8",
            )

    with pytest.raises(module.DispatchError, match="current task"):
        module.plan_materialization(
            payload,
            tasks,
            status_root=tmp_path,
            state=state,
            readiness=live_readiness,
        )


def test_current_concurrent_live_artifact_conflict_fails_closed(
    tmp_path: Path,
) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    rogue = {
        "id": "ROGUE-CURRENT-CONTROLLER",
        "status": "in_progress",
        "artifacts": ["services/training-session/private"],
    }

    with pytest.raises(module.DispatchError, match="live nonterminal artifact overlap"):
        module.plan_materialization(
            payload,
            tasks,
            status_root=tmp_path,
            state=active_state([rogue]),
            readiness=readiness(),
        )


def test_current_atomic_failure_never_mutates_input_state(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    state = active_state()
    before = deepcopy(state)
    plan = module.plan_materialization(
        payload,
        tasks,
        status_root=tmp_path,
        state=state,
        readiness=readiness(),
    )
    calls = 0

    def failing_assign(working: dict, task: dict, _env: dict[str, str]) -> None:
        nonlocal calls
        calls += 1
        working["tasks"].append({"id": task["id"]})
        if calls == 2:
            raise module.DispatchError("injected assign failure")

    with pytest.raises(module.DispatchError, match="injected assign failure"):
        module.materialize_current_in_memory(
            state,
            plan,
            catalog=payload,
            tasks=tasks,
            assign_one=failing_assign,
        )
    assert state == before


def test_current_apply_rejects_human_ops_actor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AI_NAME", "Human/Ops")
    with pytest.raises(module.DispatchError, match="is not allowed"):
        module.apply_current_materialization_atomic(
            catalog={},
            tasks=[],
            status_root=tmp_path,
            authority={},
            command_runtime={"root": str(ROOT)},
            config_path=tmp_path / "config.json",
            runtime_state_path=tmp_path / "state.json",
            provider_capabilities_path=tmp_path / "capabilities.json",
        )


@pytest.mark.parametrize("failure_point", ["assign", "sync"])
def test_current_authoritative_transaction_failure_leaves_zero_catalog_tasks(
    failure_point: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = current_apply_fixture(tmp_path, monkeypatch)
    ai_status, _payload, tasks, authority, _runtime, _paths = fixture
    if failure_point == "assign":
        real_assign = ai_status.command_assign
        calls = 0

        def fail_second_assign(state: dict, args: list[str]):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise module.DispatchError("injected authoritative assign failure")
            return real_assign(state, args)

        ai_status.command_assign = fail_second_assign
        expected = "injected authoritative assign failure"
    else:
        ai_status.sync_all = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.DispatchError("injected authoritative sync failure")
        )
        expected = "injected authoritative sync failure"

    with pytest.raises(module.DispatchError, match=expected):
        apply_fixture(fixture, status_root=tmp_path)

    snapshot = load_snapshot(authority["event_log"])
    catalog_ids = {task["id"] for task in tasks}
    assert snapshot["event_count"] == 1
    assert not catalog_ids.intersection(
        row["id"] for row in snapshot["state"]["tasks"]
    )
    projection = json.loads((tmp_path / "ai-status.json").read_text(encoding="utf-8"))
    assert not catalog_ids.intersection(row["id"] for row in projection["tasks"])
    log_path = tmp_path / "ai-activity-log.jsonl"
    assert not log_path.exists() or "program_catalog_materialized" not in log_path.read_text(
        encoding="utf-8"
    )
    archives = list(
        (tmp_path / ".orchestrator" / "program-dispatch-admissions").rglob("*.json")
    )
    if failure_point == "sync":
        assert len(archives) == 1
        assert json.loads(archives[0].read_text(encoding="utf-8"))["status"] == "prepared"
    else:
        assert archives == []


def test_current_concurrent_authoritative_apply_is_one_commit_plus_exact_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = current_apply_fixture(tmp_path, monkeypatch)
    _ai_status, _payload, tasks, authority, _runtime, _paths = fixture

    process_context = multiprocessing.get_context("fork")
    result_queue = process_context.Queue()

    def run_apply() -> None:
        try:
            result_queue.put(
                {"ok": apply_fixture(fixture, status_root=tmp_path)}
            )
        except BaseException as exc:  # pragma: no cover - surfaced in parent
            result_queue.put({"error": f"{type(exc).__name__}: {exc}"})

    processes = [process_context.Process(target=run_apply) for _index in range(2)]
    for process in processes:
        process.start()
    received = [result_queue.get(timeout=30) for _process in processes]
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    assert all("ok" in item for item in received), received
    results = [item["ok"] for item in received]

    assert sorted(len(result["created"]) for result in results) == [0, 25]
    snapshot = load_snapshot(authority["event_log"])
    g1_ids = {task["id"] for task in tasks if task["wave"] == "G1"}
    materialized = [
        row["id"]
        for row in snapshot["state"]["tasks"]
        if row.get("id") in g1_ids
    ]
    assert len(materialized) == 25
    assert set(materialized) == g1_ids
    events = [
        json.loads(line)
        for line in (tmp_path / "ai-activity-log.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert sum(event.get("type") == "program_catalog_materialized" for event in events) == 1
    archive_paths = list(
        (tmp_path / ".orchestrator" / "program-dispatch-admissions").rglob("*.json")
    )
    assert len(archive_paths) == 1
    archive = json.loads(archive_paths[0].read_text(encoding="utf-8"))
    assert archive["status"] == "committed"
    assert set(archive["admitted_task_ids"]) == g1_ids

    replay = apply_fixture(fixture, status_root=tmp_path)
    assert replay["created"] == []
    assert set(replay["exact"]) == g1_ids
    assert replay["admission_archive"] == str(archive_paths[0])


def test_current_post_commit_archive_gap_is_recovered_by_exact_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = current_apply_fixture(tmp_path, monkeypatch)
    _ai_status, _payload, tasks, authority, _runtime, _paths = fixture
    real_readback = module.verify_current_canonical_readback
    monkeypatch.setattr(
        module,
        "verify_current_canonical_readback",
        lambda **_kwargs: (_ for _ in ()).throw(
            module.DispatchError("injected post-commit readback failure")
        ),
    )

    with pytest.raises(module.DispatchError, match="post-commit readback failure"):
        apply_fixture(fixture, status_root=tmp_path)

    snapshot = load_snapshot(authority["event_log"])
    g1_ids = {task["id"] for task in tasks if task["wave"] == "G1"}
    assert {
        row["id"]
        for row in snapshot["state"]["tasks"]
        if row.get("id") in g1_ids
    } == g1_ids
    archive_paths = list(
        (tmp_path / ".orchestrator" / "program-dispatch-admissions").rglob("*.json")
    )
    assert len(archive_paths) == 1
    prepared = json.loads(archive_paths[0].read_text(encoding="utf-8"))
    assert prepared["status"] == "prepared"
    assert prepared["canonical_readback"] is None

    monkeypatch.setattr(module, "verify_current_canonical_readback", real_readback)
    replay = apply_fixture(fixture, status_root=tmp_path)
    assert replay["created"] == []
    assert set(replay["exact"]) == g1_ids
    recovered = json.loads(archive_paths[0].read_text(encoding="utf-8"))
    assert recovered["status"] == "committed"
    assert recovered["canonical_readback"]["exact"] == [
        task["id"] for task in tasks if task["wave"] == "G1"
    ]


def test_current_exact_canonical_readback_and_mismatch(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    live_readiness = readiness()
    g1_rows = [
        materialized_row(
            task,
            payload=payload,
            tasks=tasks,
            live_readiness=live_readiness,
        )
        for task in tasks
        if task["wave"] == "G1"
    ]
    state = active_state(g1_rows)
    authority = write_authority(tmp_path, state)
    task_ids = [row["id"] for row in g1_rows]

    readback = module.verify_current_canonical_readback(
        catalog=payload,
        tasks=tasks,
        status_root=tmp_path,
        authority=authority,
        admitted_task_ids=task_ids,
        readiness=live_readiness,
    )
    assert readback["state_sha256"] == readback["projection_sha256"]
    assert readback["exact"] == task_ids
    assert readback["task_state_snapshot"] == {
        "byte_size": authority["event_log"].stat().st_size,
        "checkpoint_used": True,
        "event_count": 1,
        "last_event_id": load_snapshot(authority["event_log"])["last_event_id"],
        "last_event_sha256": load_snapshot(authority["event_log"])[
            "last_event_sha256"
        ],
        "revalidated_tail_events": 0,
        "state_sha256": load_snapshot(authority["event_log"])["state_sha256"],
    }

    projection = json.loads((tmp_path / "ai-status.json").read_text(encoding="utf-8"))
    projection["tasks"][0]["status"] = "blocked"
    (tmp_path / "ai-status.json").write_text(
        json.dumps(projection),
        encoding="utf-8",
    )
    with pytest.raises(module.DispatchError, match="readback mismatch"):
        module.verify_current_canonical_readback(
            catalog=payload,
            tasks=tasks,
            status_root=tmp_path,
            authority=authority,
            admitted_task_ids=task_ids,
            readiness=live_readiness,
        )


def test_current_full_g1_canonical_readback_rejects_truncated_resolution(
    tmp_path: Path,
) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    live_readiness = readiness()
    g1_rows = [
        materialized_row(
            task,
            payload=payload,
            tasks=tasks,
            live_readiness=live_readiness,
        )
        for task in tasks
        if task["wave"] == "G1"
    ]
    for row in g1_rows:
        row["provider_assignment_resolution"] = {
            "owner": row["owner"],
            "reviewer": row["reviewer"],
        }
    authority = write_authority(tmp_path, active_state(g1_rows))

    with pytest.raises(module.DispatchError, match="resolution schema is not exact"):
        module.verify_current_canonical_readback(
            catalog=payload,
            tasks=tasks,
            status_root=tmp_path,
            authority=authority,
            admitted_task_ids=[row["id"] for row in g1_rows],
            readiness=live_readiness,
        )


@pytest.mark.parametrize("mutation", ["truncated", "contradictory"])
def test_current_admission_archive_rejects_inexact_assignment_evidence(
    mutation: str,
    tmp_path: Path,
) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    live_readiness = readiness()
    plan = module.plan_materialization(
        payload,
        tasks,
        status_root=tmp_path,
        state=active_state(),
        readiness=live_readiness,
    )
    admitted_task_ids = list(plan["create"])
    command_runtime = {
        "source_sha": "a" * 40,
        "remote": "ajoe734/pantheon",
        "base_ref": "origin/dev",
    }
    archive_path = module.prepare_current_admission_archive(
        status_root=tmp_path,
        plan=plan,
        admitted_task_ids=admitted_task_ids,
        command_runtime=command_runtime,
        actor="Codex",
        allow_committed=False,
    )
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    for decision in archive["assignment_decisions"].values():
        if mutation == "truncated":
            owner = decision["owner"]
            reviewer = decision["reviewer"]
            decision.clear()
            decision.update({"owner": owner, "reviewer": reviewer})
        else:
            decision["reviewer_evaluations"][0]["selected"] = True
    archive_path.write_text(json.dumps(archive), encoding="utf-8")

    with pytest.raises(module.DispatchError, match="admission archive task"):
        module.prepare_current_admission_archive(
            status_root=tmp_path,
            plan=plan,
            admitted_task_ids=admitted_task_ids,
            command_runtime=command_runtime,
            actor="Codex",
            allow_committed=False,
        )


def test_stale_command_root_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def rejected_runtime(*_args, **_kwargs):
        raise RuntimeError("source SHA is not merged into origin/dev")

    monkeypatch.setattr(module, "validate_status_command_runtime", rejected_runtime)
    with pytest.raises(module.DispatchError, match="not merged"):
        module.resolve_command_runtime(tmp_path, expected_sha="a" * 40)
