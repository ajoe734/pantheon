from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
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
    / "2026-07-26-twelve-loop-gap"
    / "tasks.json"
)
PROOF_OWNERSHIP = CATALOG.with_name("proof-ownership.json")
ASSIGNMENT_REVISION = CATALOG.with_name("assignment-revision-1.json")
SPEC = importlib.util.spec_from_file_location("dispatch_twelve_loop_gap", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
from rewrite.task_state_store import append_state_commit


def catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def proof_ownership() -> dict:
    return json.loads(PROOF_OWNERSHIP.read_text(encoding="utf-8"))


def assignment_revision() -> dict:
    return json.loads(ASSIGNMENT_REVISION.read_text(encoding="utf-8"))


def external_task() -> dict:
    return {
        "id": "PPL-ALLOC-009",
        "status": "blocked",
        "artifacts": [
            "services/control-plane/bff",
            "execute-plans:src",
        ],
    }


def active_state(tasks: list[dict] | None = None) -> dict:
    return {"tasks": [external_task(), *(tasks or [])]}


def write_status(root: Path, tasks: list[dict] | None = None) -> None:
    (root / "ai-status.json").write_text(
        json.dumps(active_state(tasks)),
        encoding="utf-8",
    )


def write_authority(root: Path, tasks: list[dict] | None = None) -> tuple[Path, dict]:
    event_log = root / "task-state-events.jsonl"
    append_state_commit(event_log, active_state(tasks), source="test")
    live_config = root / "live-config.json"
    live_config.write_text(
        json.dumps(
            {
                "paths": {"status_file": str(root / "ai-status.json")},
                "task_state_store": {
                    "mode": "authoritative",
                    "event_log": str(event_log),
                },
            }
        ),
        encoding="utf-8",
    )
    write_status(root, tasks)
    authority = module.resolve_task_state_authority(
        live_config,
        status_root=root,
    )
    return live_config, authority


def test_canonical_catalog_is_valid_and_has_unique_sink() -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    assert len(tasks) == 25
    assert tasks[-1]["id"] == "L12-CLOSE-001"
    assert set(tasks[-1]["loop_ids"]) == module.CANONICAL_LOOP_IDS


def test_every_unfinished_assignment_uses_antigravity_or_claude_owner() -> None:
    finished = {
        "L12-FLEET-001",
        "L12-REC-001",
        "L12-SRC-001",
        "L12-ALPHA-001",
        "L12-CONS-001",
        "L12-DEP-001",
    }
    tasks = module.validate_catalog(catalog())

    unfinished = [task for task in tasks if task["id"] not in finished]
    assert unfinished
    assert {task["owner"] for task in unfinished} == {"Antigravity", "Claude"}
    assert all(task["owner"] != task["reviewer"] for task in unfinished)


def test_catalog_rejects_unapproved_fleet_actor() -> None:
    payload = catalog()
    payload["tasks"][1]["owner"] = "Copilot"

    with pytest.raises(module.DispatchError, match="approved fleet actor"):
        module.validate_catalog(payload)


def test_assignment_revision_is_exactly_bound_to_unfinished_catalog_tasks() -> None:
    payload = catalog()
    revision = assignment_revision()
    finished = set(revision["completed_task_contracts_unchanged"])
    expected = {
        task["id"]: {
            "task_id": task["id"],
            "owner": task["owner"],
            "reviewer": task["reviewer"],
        }
        for task in payload["tasks"]
        if task["id"] not in finished
    }

    assert revision["previous_catalog_sha256"] == (
        "a7fbbaa560bd7f2d97750b25cd20af69b64d4f522b293689849b0e1b1763717f"
    )
    assert revision["revised_catalog_sha256"] == module.canonical_json_sha256(
        payload
    )
    assert {
        assignment["task_id"]: assignment
        for assignment in revision["assignments"]
    } == expected
    assert revision["provider_auth_probes"]["Antigravity"]["ready"] is True
    assert revision["provider_auth_probes"]["Claude"]["ready"] is True
    assert revision["provider_auth_probes"]["Antigravity2"]["ready"] is False


def test_proof_ownership_is_bound_to_catalog_and_forward_dag() -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)

    delegations = module.validate_proof_ownership(
        proof_ownership(),
        catalog=payload,
        tasks=tasks,
    )

    assert delegations == proof_ownership()["delegations"]
    assert delegations[0]["source_task_id"] == "L12-TEACH-001"
    assert delegations[0]["owner_task_id"] == "L12-VERIFY-LEARN-001"
    assert delegations[0]["final_witness_task_id"] == "L12-HOSTED-001"


def test_proof_ownership_rejects_wrong_catalog_digest() -> None:
    payload = catalog()
    overlay = proof_ownership()
    overlay["base_catalog_sha256"] = "0" * 64

    with pytest.raises(module.DispatchError, match="base catalog digest"):
        module.validate_proof_ownership(
            overlay,
            catalog=payload,
            tasks=module.validate_catalog(payload),
        )


def test_proof_ownership_rejects_backward_or_unrelated_owner() -> None:
    payload = catalog()
    overlay = proof_ownership()
    overlay["delegations"][0]["owner_task_id"] = "L12-FLEET-001"

    with pytest.raises(module.DispatchError, match="descendant"):
        module.validate_proof_ownership(
            overlay,
            catalog=payload,
            tasks=module.validate_catalog(payload),
        )


def test_proof_ownership_rejects_non_catalog_proof() -> None:
    payload = catalog()
    overlay = proof_ownership()
    overlay["delegations"][0]["proof"] = "invented proof"

    with pytest.raises(module.DispatchError, match="exact source proof"):
        module.validate_proof_ownership(
            overlay,
            catalog=payload,
            tasks=module.validate_catalog(payload),
        )


def test_human_task_cards_mirror_assignment_header() -> None:
    tasks = module.validate_catalog(catalog())

    for task in tasks:
        card = (ROOT / task["task_doc"]).read_text(encoding="utf-8")
        expected = (
            f"Wave {task['wave']}, lane `{task['fleet_lane']}`, "
            f"owner `{task['owner']}`, reviewer `{task['reviewer']}`"
        )
        assert expected in card, task["id"]


def test_assignment_guards_protect_every_catalog_artifact_scope() -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    guarded_external = module.EXPECTED_EXTERNAL_DEPENDENCY_CONSUMERS["PPL-ALLOC-009"]

    for task in tasks:
        env = module.assignment_environment(task, catalog=payload, tasks=tasks)
        guard = json.loads(env["TASK_METADATA_JSON"])["artifact_conflict_guard"]
        assert guard["task_id"] == task["id"]
        assert guard["catalog_sha256"] == module.canonical_json_sha256(payload)
        if task["id"] in guarded_external:
            assert "PPL-ALLOC-009" in guard["allowed_overlap_task_ids"]


def test_dirty_runtime_detection_includes_catalog_and_docs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout=" M docs/bff/execution-tasks/program/tasks.json\n M docs/plan.md\n",
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module._dirty_command_runtime_files(tmp_path) == [
        "docs/bff/execution-tasks/program/tasks.json",
        "docs/plan.md",
    ]


def test_validate_only_cli() -> None:
    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--validate-only",
            "--catalog",
            str(CATALOG),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["status"] == "valid"
    assert body["task_count"] == 25
    assert body["proof_delegation_count"] == 1
    assert len(body["proof_ownership_sha256"]) == 64


def test_rejects_owner_reviewer_match() -> None:
    payload = catalog()
    payload["tasks"][0]["reviewer"] = payload["tasks"][0]["owner"]
    with pytest.raises(module.DispatchError, match="distinct"):
        module.validate_catalog(payload)


def test_rejects_unknown_dependency() -> None:
    payload = catalog()
    payload["tasks"][1]["depends_on"] = ["UNKNOWN"]
    with pytest.raises(module.DispatchError, match="unknown task"):
        module.validate_catalog(payload)


def test_rejects_missing_external_overlap_dependency() -> None:
    payload = catalog()
    controller = next(task for task in payload["tasks"] if task["id"] == "L12-CTRL-001")
    controller["depends_on"].remove("PPL-ALLOC-009")
    with pytest.raises(module.DispatchError, match="external dependency consumers"):
        module.validate_catalog(payload)


def test_rejects_descriptive_only_completion_authority() -> None:
    payload = catalog()
    payload["completion_authority"]["guard_install_task_id"] = "L12-CLOSE-001"
    with pytest.raises(module.DispatchError, match="completion_authority"):
        module.validate_catalog(payload)


def test_rejects_final_authority_that_does_not_depend_on_guard() -> None:
    payload = catalog()
    close = next(task for task in payload["tasks"] if task["id"] == "L12-CLOSE-001")
    close["depends_on"].remove("L12-SIGNOFF-001")
    with pytest.raises(module.DispatchError, match="ancestor|authority|sink"):
        module.validate_catalog(payload)


def test_rejects_unordered_overlapping_artifacts() -> None:
    payload = catalog()
    payload["tasks"][2]["artifacts"].append("services/loop-control/writer.py")
    with pytest.raises(module.DispatchError, match="overlapping artifact"):
        module.validate_catalog(payload)


def test_rejects_frontend_artifact_in_pantheon_task() -> None:
    payload = catalog()
    payload["tasks"][1]["artifacts"].append("execute-plans/src/bad.ts")
    with pytest.raises(module.DispatchError, match="frontend artifacts"):
        module.validate_catalog(payload)


def test_dry_run_is_read_only(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    write_status(tmp_path)
    before = (tmp_path / "ai-status.json").read_bytes()
    plan = module.plan_materialization(
        payload,
        tasks,
        status_root=tmp_path,
        state=active_state(),
    )
    assert len(plan["create"]) == 25
    assert plan["exact"] == []
    assert plan["external_dependencies"]["PPL-ALLOC-009"] == {
        "source": "active",
        "status": "blocked",
        "satisfied": False,
    }
    assert (tmp_path / "ai-status.json").read_bytes() == before


def test_rejects_unordered_live_artifact_overlap(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    rogue = {
        "id": "ROGUE-BFF-001",
        "status": "in_progress",
        "artifacts": ["services/control-plane/bff"],
    }

    with pytest.raises(module.DispatchError, match="live nonterminal artifact overlap"):
        module.plan_materialization(
            payload,
            tasks,
            status_root=tmp_path,
            state=active_state([rogue]),
        )


def test_authority_projects_journal_instead_of_stale_status_file(tmp_path: Path) -> None:
    live_config, authority = write_authority(tmp_path)
    (tmp_path / "ai-status.json").write_text(
        json.dumps({"tasks": [{"id": "STALE-ONLY"}]}),
        encoding="utf-8",
    )

    resolved = module.resolve_task_state_authority(live_config, status_root=tmp_path)
    state = module.load_authoritative_task_state(resolved)

    assert [task["id"] for task in state["tasks"]] == ["PPL-ALLOC-009"]


def test_authority_rejects_status_root_mismatch(tmp_path: Path) -> None:
    live_config, _ = write_authority(tmp_path)

    with pytest.raises(module.DispatchError, match="status authority mismatch"):
        module.resolve_task_state_authority(
            live_config,
            status_root=tmp_path / "other-root",
        )


def test_exact_active_materialization_is_idempotent(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    first = deepcopy(tasks[0])
    first["status"] = "in_progress"
    first["next"] = "worker is running"
    write_status(tmp_path, [first])
    plan = module.plan_materialization(
        payload,
        tasks,
        status_root=tmp_path,
        state=active_state([first]),
    )
    assert plan["exact"] == ["L12-FLEET-001"]
    assert "L12-FLEET-001" not in plan["create"]


def test_conflicting_active_materialization_fails_closed(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    first = deepcopy(tasks[0])
    first["owner"] = "Codex2"
    write_status(tmp_path, [first])
    with pytest.raises(module.DispatchError, match="conflicts"):
        module.plan_materialization(
            payload,
            tasks,
            status_root=tmp_path,
            state=active_state([first]),
        )


def write_archived_catalog_task(
    root: Path,
    payload: dict,
    task: dict,
    *,
    terminal_status: str = "done",
) -> Path:
    archived_task = deepcopy(task)
    archived_task.update(
        {
            "status": terminal_status,
            "next": "Archived after governed completion.",
            "program_id": payload["program_id"],
            "auto_created_by": module.AUTO_CREATED_BY,
            "catalog_task_contract_sha256": module.canonical_json_sha256(
                module.task_contract(task)
            ),
        }
    )
    archive = root / "ai-task-archive" / "tasks"
    archive.mkdir(parents=True, exist_ok=True)
    archive_path = archive / f"{task['id']}.json"
    archive_path.write_text(
        json.dumps(
            {
                "version": 1,
                "task_id": task["id"],
                "terminal_status": terminal_status,
                "task": archived_task,
            }
        ),
        encoding="utf-8",
    )
    return archive_path


def test_exact_done_archive_is_idempotent(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    write_status(tmp_path)
    write_archived_catalog_task(tmp_path, payload, tasks[0])

    plan = module.plan_materialization(
        payload,
        tasks,
        status_root=tmp_path,
        state=active_state(),
    )

    assert plan["exact"] == ["L12-FLEET-001"]
    assert "L12-FLEET-001" not in plan["create"]
    assert len(plan["create"]) == 24


def test_archive_aware_resume_preserves_exact_21_create_4(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    active = [deepcopy(task) for task in tasks[1:21]]
    write_status(tmp_path, active)
    archive_path = write_archived_catalog_task(tmp_path, payload, tasks[0])
    status_before = (tmp_path / "ai-status.json").read_bytes()
    archive_before = archive_path.read_bytes()

    plan = module.plan_materialization(
        payload,
        tasks,
        status_root=tmp_path,
        state=active_state(active),
    )

    assert plan["exact"] == [task["id"] for task in tasks[:21]]
    assert plan["create"] == [
        "L12-VERIFY-RUNTIME-001",
        "L12-VERIFY-OBS-001",
        "L12-HOSTED-001",
        "L12-CLOSE-001",
    ]
    assert (tmp_path / "ai-status.json").read_bytes() == status_before
    assert archive_path.read_bytes() == archive_before


def test_malformed_archived_id_fails_closed(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    write_status(tmp_path)
    archive = tmp_path / "ai-task-archive" / "tasks"
    archive.mkdir(parents=True)
    (archive / "L12-FLEET-001.json").write_text("{}", encoding="utf-8")
    with pytest.raises(module.DispatchError, match="archived"):
        module.plan_materialization(
            payload,
            tasks,
            status_root=tmp_path,
            state=active_state(),
        )


def test_non_done_archive_fails_closed(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    write_status(tmp_path)
    write_archived_catalog_task(
        tmp_path,
        payload,
        tasks[0],
        terminal_status="cancelled",
    )
    with pytest.raises(module.DispatchError, match="not successfully complete"):
        module.plan_materialization(
            payload,
            tasks,
            status_root=tmp_path,
            state=active_state(),
        )


def test_conflicting_done_archive_fails_closed(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    write_status(tmp_path)
    archive_path = write_archived_catalog_task(tmp_path, payload, tasks[0])
    archive_payload = json.loads(archive_path.read_text(encoding="utf-8"))
    archive_payload["task"]["owner"] = "Codex2"
    archive_path.write_text(json.dumps(archive_payload), encoding="utf-8")
    with pytest.raises(module.DispatchError, match="contract conflicts"):
        module.plan_materialization(
            payload,
            tasks,
            status_root=tmp_path,
            state=active_state(),
        )


def test_same_id_in_active_and_archive_fails_closed(tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    first = deepcopy(tasks[0])
    write_status(tmp_path, [first])
    write_archived_catalog_task(tmp_path, payload, tasks[0])
    with pytest.raises(module.DispatchError, match="both active and archive"):
        module.plan_materialization(
            payload,
            tasks,
            status_root=tmp_path,
            state=active_state([first]),
        )


def test_apply_uses_canonical_assign_writer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = catalog()
    tasks = module.validate_catalog(payload)
    _, authority = write_authority(tmp_path)
    plan = module.plan_materialization(
        payload,
        tasks,
        status_root=tmp_path,
        state=active_state(),
    )
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setenv("AI_NAME", "Human/Ops")
    created = module.apply_materialization(
        plan,
        catalog=payload,
        tasks=tasks,
        status_root=tmp_path,
        authority=authority,
        command_runtime={
            "root": str(ROOT),
            "source_sha": "test-sha",
            "script": str(ROOT / "scripts" / "ai_status.py"),
        },
        runner=runner,
        verify_after_write=False,
    )
    assert created == [task["id"] for task in tasks]
    assert len(calls) == 25
    command, env = calls[0]
    assert command[-4:] == [
        "L12-FLEET-001",
        "Codex",
        "Codex2",
        tasks[0]["title"],
    ]
    metadata = json.loads(env["TASK_METADATA_JSON"])
    assert metadata["program_id"] == module.PROGRAM_ID
    assert metadata["catalog_task_contract_sha256"]
    assert metadata["artifact_conflict_guard"]["task_id"] == "L12-FLEET-001"
    assert env["TASK_ASSIGN_CREATE_ONLY"] == "true"
    assert env["TASK_NEXT"] == tasks[0]["next"]
    assert env["PANTHEON_TASK_STATE_STORE_MODE"] == "authoritative"
    assert env["PANTHEON_TASK_STATE_EVENT_LOG"] == str(authority["event_log"])
