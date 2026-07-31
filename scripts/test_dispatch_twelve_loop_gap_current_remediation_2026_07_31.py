from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
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
from rewrite.task_state_store import append_state_commit


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
        "source": "test-live-readiness",
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
        ["python3", str(SCRIPT), "--validate-only", "--catalog", str(CATALOG)],
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


def test_current_dry_run_cli_is_read_only(tmp_path: Path) -> None:
    state = active_state()
    authority = write_authority(tmp_path, state)
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
    before_status = (tmp_path / "ai-status.json").read_bytes()
    before_journal = authority["event_log"].read_bytes()
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
    assert (tmp_path / "ai-status.json").read_bytes() == before_status
    assert authority["event_log"].read_bytes() == before_journal


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


def test_stale_command_root_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def rejected_runtime(*_args, **_kwargs):
        raise RuntimeError("source SHA is not merged into origin/dev")

    monkeypatch.setattr(module, "validate_status_command_runtime", rejected_runtime)
    with pytest.raises(module.DispatchError, match="not merged"):
        module.resolve_command_runtime(tmp_path, expected_sha="a" * 40)
