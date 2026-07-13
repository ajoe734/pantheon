from __future__ import annotations

from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dispatch_loop_product_level_remediation_2026-07-13.py"
CATALOG = (
    ROOT
    / "docs"
    / "bff"
    / "execution-tasks"
    / "2026-07-13-loop-product-level-remediation"
    / "tasks.json"
)


def load_catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def baseline_catalog() -> dict:
    payload = deepcopy(load_catalog())
    additive_ids = set(payload.pop("additive_task_ids"))
    migrations = payload.pop("catalog_migrations")
    payload.pop("completion_authority")
    payload.pop("planning_addenda")
    payload.pop("task_doc_contract_source")
    payload["schema_version"] = 1
    payload["tasks"] = [
        task for task in payload["tasks"] if task["id"] not in additive_ids
    ]
    by_id = {task["id"]: task for task in payload["tasks"]}
    for migration in migrations:
        for patch in migration["required_live_task_patches"]:
            by_id[patch["task_id"]]["depends_on"] = patch["before_depends_on"]
    payload["task_count"] = len(payload["tasks"])
    return payload


def base_state(catalog: dict | None = None) -> dict:
    payload = catalog or load_catalog()
    external_tasks = [
        {"id": task_id, "status": "done", "owner": "Codex", "reviewer": "Codex2"}
        for task_id in payload["external_dependencies"]
    ]
    return {
        "updated_at": "2026-07-13T00:00:00Z",
        "wave_state": {
            "current_wave_id": "2026-W25",
            "status": "open",
            "frozen_at": None,
        },
        "agents": [
            {
                "name": "Codex",
                "status": "idle",
                "current_task_ids": [],
                "next": "",
            },
            {
                "name": "Codex2",
                "status": "idle",
                "current_task_ids": [],
                "next": "",
            },
        ],
        "tasks": external_tasks,
    }


def prepare_status(root: Path, state: dict | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ai-status.json").write_text(
        json.dumps(state or base_state(), indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "ai-activity-log.jsonl").write_text("", encoding="utf-8")
    (root / "ai-task-archive" / "tasks").mkdir(parents=True, exist_ok=True)


def run_dispatch(
    root: Path,
    *args: str,
    catalog: Path = CATALOG,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PANTHEON_STATUS_ROOT": str(root),
        "LOOP_PRODUCT_TASK_CATALOG": str(catalog),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        ["python3", str(SCRIPT), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def write_catalog(root: Path, mutator) -> Path:
    payload = deepcopy(load_catalog())
    mutator(payload)
    path = root / "tasks.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_payload(root: Path, payload: dict, name: str = "tasks.json") -> Path:
    path = root / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def program_tasks(state: dict) -> list[dict]:
    return [
        task
        for task in state["tasks"]
        if str(task.get("id") or "").startswith("LOOP-PROD-")
    ]


def test_catalog_validates_and_has_complete_task_documents() -> None:
    result = subprocess.run(
        ["python3", str(SCRIPT), "--validate-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    catalog = load_catalog()
    packet_index = (CATALOG.parent / "INDEX.md").read_text(encoding="utf-8")
    assert catalog["task_count"] == len(catalog["tasks"]) == 45
    assert len({task["id"] for task in catalog["tasks"]}) == 45
    assert len(catalog["additive_task_ids"]) == 9
    assert catalog["tasks"][-1]["id"] == "LOOP-PROD-CLOSE-002"
    for task in catalog["tasks"]:
        task_doc = ROOT / task["task_doc"]
        assert task_doc.is_file(), task_doc
        text = task_doc.read_text(encoding="utf-8")
        assert "## Acceptance" in text
        assert "## Required proof" in text
        assert "## Non-goals" in text
        assert "## Dispatch and closeout rules" in text
        assert f"[{task['id']}]({task['id']}.md)" in packet_index
        for value in task["depends_on"] + task["artifacts"]:
            assert f"`{value}`" in text
        for value in task["non_goals"]:
            assert value in text


def test_dry_run_is_zero_write() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root, "--dry-run")

        assert result.returncode == 0, result.stderr
        assert "summary migrate=0 create=45" in result.stdout
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before
        assert not list(root.glob(".ai-status.json.*.tmp"))


def test_dispatch_is_idempotent_and_preserves_supervisor_agent_queues() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        initial = base_state()
        initial["agents"][0].update(
            {
                "status": "working",
                "current_task_ids": ["EXISTING-CODEX-001"],
                "next": "Continue existing Codex work",
            }
        )
        initial["agents"][1].update(
            {
                "status": "blocked",
                "current_task_ids": ["EXISTING-CODEX2-001"],
                "next": "Wait for an existing dependency",
            }
        )
        agents_before = deepcopy(initial["agents"])
        prepare_status(root, initial)

        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr
        state_after_first = (root / "ai-status.json").read_bytes()
        log_after_first = (root / "ai-activity-log.jsonl").read_bytes()

        state = json.loads(state_after_first)
        tasks = program_tasks(state)
        assert len(tasks) == 45
        assert all(task["auto_created_by"].startswith("dispatch_loop_product") for task in tasks)
        assert all(task["loop_ids"] for task in tasks)
        assert all(task["proof_required"] for task in tasks)
        assert all(task["product_level_required"] is True for task in tasks)
        assert state["agents"] == agents_before
        assert len(log_after_first.decode("utf-8").splitlines()) == 45

        second = run_dispatch(root)
        assert second.returncode == 0, second.stderr
        assert "No state changes required." in second.stdout
        assert (root / "ai-status.json").read_bytes() == state_after_first
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_after_first


def test_archived_terminal_primary_id_is_never_resurrected() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        archive = root / "ai-task-archive" / "tasks" / "LOOP-PROD-000.json"
        archive.write_text(
            json.dumps(
                {
                    "version": 1,
                    "task_id": "LOOP-PROD-000",
                    "terminal_status": "done",
                    "task": {"id": "LOOP-PROD-000", "status": "done"},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = run_dispatch(root)

        assert result.returncode == 0, result.stderr
        assert "SKIP-ARCHIVED LOOP-PROD-000:done" in result.stdout
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        assert "LOOP-PROD-000" not in {task["id"] for task in program_tasks(state)}
        assert len(program_tasks(state)) == 44
        assert len((root / "ai-activity-log.jsonl").read_text(encoding="utf-8").splitlines()) == 44

        second = run_dispatch(root)
        assert second.returncode == 0, second.stderr
        assert len(program_tasks(json.loads((root / "ai-status.json").read_text()))) == 44


def test_existing_non_todo_task_record_is_preserved_in_full() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        sentinel = {
            "id": "LOOP-PROD-000",
            "title": "owner changed title while working",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Codex2",
            "branch": "task/LOOP-PROD-000",
            "review_file": "evidence/review.json",
            "review_notes_zh": ["do not overwrite"],
            "waiting_for": {"kind": "ci", "run": 123},
            "evidence": {"sentinel": True},
            "custom_progress_field": {"nested": [1, 2, 3]},
        }
        state["tasks"] = [
            deepcopy(sentinel) if task.get("id") == "LOOP-PROD-000" else task
            for task in state["tasks"]
        ]
        prepare_status(root, state)

        result = run_dispatch(root)

        assert result.returncode == 0, result.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        preserved = next(task for task in after["tasks"] if task["id"] == "LOOP-PROD-000")
        assert preserved == sentinel
        assert after["agents"] == state["agents"]


def test_frozen_wave_rejects_dispatch_without_writes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state = base_state()
        state["wave_state"]["status"] = "frozen"
        prepare_status(root, state)
        before = (root / "ai-status.json").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "planning wave is frozen" in result.stderr
        assert (root / "ai-status.json").read_bytes() == before
        assert (root / "ai-activity-log.jsonl").read_text(encoding="utf-8") == ""


def test_status_lock_rejects_concurrent_dispatcher() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        with (root / "ai-status.json").open("r+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run_dispatch(root)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        assert result.returncode == 2
        assert "holds the status lock" in result.stderr
        assert not program_tasks(
            json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        )


def prepare_baseline_program(root: Path) -> tuple[dict, Path]:
    baseline = baseline_catalog()
    baseline_path = write_payload(root, baseline, "baseline-tasks.json")
    prepare_status(root, base_state(baseline))
    result = run_dispatch(root, catalog=baseline_path)
    assert result.returncode == 0, result.stderr

    state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
    migration = load_catalog()["catalog_migrations"][0]
    targets = {
        patch["task_id"] for patch in migration["required_live_task_patches"]
    }
    for task in state["tasks"]:
        if task.get("id") in targets:
            task["source_ref"]["catalog_sha256"] = migration["from_catalog_sha256"]
            task["custom_preserved_field"] = {"sentinel": task["id"]}
    (root / "ai-status.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return state, baseline_path


def test_addendum_delta_is_exact_and_has_one_final_authority() -> None:
    current = load_catalog()
    baseline = baseline_catalog()
    current_by_id = {task["id"]: task for task in current["tasks"]}
    baseline_by_id = {task["id"]: task for task in baseline["tasks"]}
    added = set(current_by_id) - set(baseline_by_id)

    assert added == set(current["additive_task_ids"])
    assert len(added) == 9
    assert not (set(baseline_by_id) - set(current_by_id))
    migration_targets = {
        patch["task_id"]
        for migration in current["catalog_migrations"]
        for patch in migration["required_live_task_patches"]
    }
    for task_id in set(baseline_by_id) - migration_targets:
        assert current_by_id[task_id] == baseline_by_id[task_id]
    for migration in current["catalog_migrations"]:
        for patch in migration["required_live_task_patches"]:
            before = baseline_by_id[patch["task_id"]]
            after = current_by_id[patch["task_id"]]
            expected = deepcopy(before)
            expected["depends_on"] = [
                *patch["before_depends_on"],
                *patch["append_dependencies"],
            ]
            assert after == expected

    authority = current["completion_authority"]["task_id"]
    dependents = {
        dependency
        for task in current["tasks"]
        for dependency in task["depends_on"]
        if dependency in current_by_id
    }
    assert set(current_by_id) - dependents == {authority}


def test_live_addendum_migration_is_atomic_audited_and_idempotent() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        before, _ = prepare_baseline_program(root)
        before_by_id = {task["id"]: deepcopy(task) for task in before["tasks"]}

        result = run_dispatch(root)

        assert result.returncode == 0, result.stderr
        assert "summary migrate=2 create=9 preserve=36 archived=0 total=45" in result.stdout
        state_after_first = (root / "ai-status.json").read_bytes()
        log_after_first = (root / "ai-activity-log.jsonl").read_bytes()
        state = json.loads(state_after_first)
        current = load_catalog()
        current_by_id = {task["id"]: task for task in current["tasks"]}
        state_by_id = {task["id"]: task for task in program_tasks(state)}
        assert len(state_by_id) == 45
        for task_id in ("LOOP-PROD-AGORA-002", "LOOP-PROD-MAI-001"):
            expected = deepcopy(before_by_id[task_id])
            expected["depends_on"] = current_by_id[task_id]["depends_on"]
            assert state_by_id[task_id] == expected
        records = state["program_catalog_migrations"]
        assert [record["id"] for record in records] == ["loop-product-gap-addendum-v2"]
        assert len(records[0]["patches"]) == 2
        assert len(log_after_first.decode("utf-8").splitlines()) == 47

        second = run_dispatch(root)
        assert second.returncode == 0, second.stderr
        assert "No state changes required." in second.stdout
        assert "summary migrate=0 create=0 preserve=45 archived=0 total=45" in second.stdout
        assert (root / "ai-status.json").read_bytes() == state_after_first
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_after_first


@pytest.mark.parametrize("mutation", ["branch", "partial_dependency"])
def test_live_addendum_migration_rejects_changed_preimage_without_writes(mutation) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        by_id = {task["id"]: task for task in state["tasks"]}
        if mutation == "branch":
            by_id["LOOP-PROD-MAI-001"]["branch"] = "task/already-admitted"
        else:
            by_id["LOOP-PROD-AGORA-002"]["depends_on"].append(
                "LOOP-PROD-ATTEST-001"
            )
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


@pytest.mark.parametrize(
    ("name", "mutator", "expected"),
    [
        (
            "cycle",
            lambda payload: payload["tasks"][0]["depends_on"].append("LOOP-PROD-001"),
            "dependency cycle",
        ),
        (
            "same_reviewer",
            lambda payload: payload["tasks"][0].__setitem__("reviewer", "Codex"),
            "owner and reviewer must differ",
        ),
        (
            "colon_route",
            lambda payload: payload["tasks"][4]["artifacts"].__setitem__(
                0, "execute-plans:src/unsafe.ts"
            ),
            "slash routing",
        ),
        (
            "artifact_conflict",
            lambda payload: payload["tasks"][5].__setitem__(
                "artifacts",
                [payload["tasks"][3]["artifacts"][-1]],
            ),
            "overlapping artifact scopes",
        ),
        (
            "missing_dependency",
            lambda payload: payload["tasks"][0]["depends_on"].append("UNKNOWN-999"),
            "undeclared task",
        ),
        (
            "non_boolean_signoff",
            lambda payload: payload["tasks"][-1].__setitem__(
                "requires_human_ops_signoff", "yes"
            ),
            "requires_human_ops_signoff must be a boolean",
        ),
        (
            "stale_contract_marker",
            lambda payload: payload["tasks"][-2]["acceptance"].append(
                "new unrendered security requirement"
            ),
            "canonical contract marker is stale or missing",
        ),
        (
            "migration_catalog_drift",
            lambda payload: payload["tasks"][
                next(
                    index
                    for index, task in enumerate(payload["tasks"])
                    if task["id"] == "LOOP-PROD-AGORA-002"
                )
            ]["depends_on"].remove("LOOP-PROD-ATTEST-001"),
            "catalog dependencies do not match migration",
        ),
        (
            "multiple_completion_sinks",
            lambda payload: payload["tasks"][-1]["depends_on"].remove(
                "LOOP-PROD-FLEET-001"
            ),
            "unique sink",
        ),
    ],
)
def test_catalog_validation_fails_closed(name, mutator, expected) -> None:
    with tempfile.TemporaryDirectory(prefix=f"loop-product-{name}-") as temp:
        root = Path(temp)
        prepare_status(root)
        catalog = write_catalog(root, mutator)

        result = run_dispatch(root, "--validate-only", catalog=catalog)

        assert result.returncode == 2
        assert expected in result.stderr


def test_missing_external_dependency_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state = base_state()
        state["tasks"] = [
            task for task in state["tasks"] if task["id"] != "TJ-E2E-014"
        ]
        prepare_status(root, state)
        before = (root / "ai-status.json").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "external dependency is missing: TJ-E2E-014" in result.stderr
        assert (root / "ai-status.json").read_bytes() == before


def test_archived_superseded_external_dependency_does_not_satisfy() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state = base_state()
        state["tasks"] = [
            task for task in state["tasks"] if task["id"] != "AG-GAP-005"
        ]
        prepare_status(root, state)
        archive = root / "ai-task-archive" / "tasks" / "AG-GAP-005.json"
        archive.write_text(
            json.dumps(
                {
                    "task_id": "AG-GAP-005",
                    "terminal_status": "superseded",
                    "task": {"status": "superseded"},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "only done can satisfy" in result.stderr
