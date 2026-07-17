from __future__ import annotations

from copy import deepcopy
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import runpy
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
HISTORICAL_CATALOG_COMMIT = "9ad17546abc0573c8a362aa8ee1e864d1b04c711"
HISTORICAL_CATALOG_SHA256 = (
    "2b5183712e7e0d4dbdf21214aaa215b4d16fe186b8c83a7cea5420dea0022b91"
)
HISTORICAL_CATALOG_PATH = (
    "docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/tasks.json"
)
RUNTIME_PROTOCOL_ID = "pantheon-runtime-task-audit-lock-v1"
INCIDENT_FIXTURE = (
    ROOT
    / "docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/"
    "fixtures/browser-auth-incidents.v1.json"
)
ROUTE_FIXTURE = (
    ROOT
    / "docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/"
    "fixtures/browser-auth-route-matrix.v1.json"
)
DISPATCH = runpy.run_path(str(SCRIPT))


FAKE_RUNTIME_PROTOCOL = '''from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path

from common import activity_audit_lock_file as shared_activity_audit_lock_file


RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_VERSION = 1
RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID = "pantheon-runtime-task-audit-lock-v1"


def _trace(label: str) -> None:
    target = os.environ.get("LOOP_TEST_LOCK_TRACE")
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write(label + "\\n")


@contextmanager
def _lock(path: Path, *, shared: bool, nonblocking: bool, label: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        if nonblocking:
            operation |= fcntl.LOCK_NB
        _trace("request:" + label)
        fcntl.flock(handle.fileno(), operation)
        _trace("acquire:" + label)
        try:
            yield
        finally:
            _trace("release:" + label)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read(path: Path) -> bytes:
    return path.read_bytes() if path.is_file() else b""


def _strict_object(raw: bytes):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("not object")
    return value


def _canonical_sha256(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def verify_runtime_lock_capability(
    *, manifest, manifest_sha256, writer_registry, completion_evidence, repository_root
):
    root = Path(repository_root)
    registry_sha256 = hashlib.sha256(
        (root / manifest["writer_registry_path"]).read_bytes()
    ).hexdigest()
    evidence_sha256 = hashlib.sha256(
        (root / manifest["bootstrap_completion_evidence_path"]).read_bytes()
    ).hexdigest()
    allowed = bool(
        writer_registry.get("transaction_scope")
        == "complete_read_validate_mutate_replace"
        and completion_evidence.get("conclusion") == "passed"
        and completion_evidence.get("signature_algorithm") == "ed25519"
        and completion_evidence.get("verifier_capability_sha256")
        == manifest["writers"][manifest["module_path"]]
        and completion_evidence.get("signature")
        == "test-only-protected-signature"
        and registry_sha256 == manifest["writer_registry_sha256"]
        and evidence_sha256 == manifest["bootstrap_completion_evidence_sha256"]
    )
    decision = {
        "schema_version": 1,
        "protocol_id": RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID,
        "allowed": allowed,
        "reason_id": "verified" if allowed else "protected_evidence_invalid",
        "manifest_sha256": manifest_sha256,
        "writer_registry_sha256": registry_sha256,
        "completion_evidence_sha256": evidence_sha256,
        "merged_commit_sha": manifest["merged_commit_sha"],
    }
    mutation = os.environ.get("LOOP_TEST_CAPABILITY_VERIFIER_MUTATION")
    if mutation == "deny":
        decision["allowed"] = False
        decision["reason_id"] = "protected_evidence_invalid"
    elif mutation == "extra":
        decision["unbound"] = True
    return decision


@contextmanager
def tasks_runtime_admission_guard(
    config, task_ids, *, strict: bool, shared: bool, nonblocking: bool
):
    state_path = Path(config["paths"]["state_file"])
    lock_path = state_path.parent / "runtime-admission.lock"
    with _lock(
        lock_path,
        shared=shared,
        nonblocking=nonblocking,
        label="runtime_admission",
    ):
        sources = [
            ("runtime_state", state_path),
            ("event_queue", Path(config["paths"]["event_queue"])),
            ("approval_queue", Path(config["paths"]["approval_queue"])),
        ]
        bodies = {source_id: _read(path) for source_id, path in sources}
        source_sha256 = {
            source_id: hashlib.sha256(body).hexdigest()
            for source_id, body in bodies.items()
        }
        conflicts = []
        reason_id = "clear"
        conflict_statuses = {
            "queued", "started", "running", "waiting_approval",
            "suspended_approval", "manual_pending", "retry_backoff",
            "stalled", "fallback", "admitted",
        }
        try:
            if any(not body.strip() for body in bodies.values()):
                raise ValueError("empty source")
            runtime = _strict_object(bodies["runtime_state"])
            if set(runtime) != {"schema_version", "workers"} or runtime["schema_version"] != 1 or not isinstance(runtime["workers"], list):
                raise ValueError("runtime schema")
            queue_rows = [
                _strict_object(line)
                for line in bodies["event_queue"].splitlines()
                if line.strip()
            ]
            if not queue_rows:
                raise ValueError("event queue schema")
            approval = _strict_object(bodies["approval_queue"])
            if set(approval) != {"schema_version", "requests"} or approval["schema_version"] != 1 or not isinstance(approval["requests"], list):
                raise ValueError("approval schema")
            candidates = [
                ("runtime_state", item) for item in runtime["workers"]
            ] + [
                ("event_queue", item) for item in queue_rows
            ] + [
                ("approval_queue", item) for item in approval["requests"]
            ]
            for source_id, item in candidates:
                if not isinstance(item, dict):
                    raise ValueError("foreign row")
                task_id = item.get("task_id")
                status = item.get("status")
                if task_id in task_ids and status in conflict_statuses:
                    conflicts.append({
                        "source_id": source_id,
                        "task_id": task_id,
                        "status": status,
                    })
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            reason_id = "runtime_source_invalid"
        if os.environ.get("LOOP_TEST_RUNTIME_BLOCK") == "1":
            conflicts.append({
                "source_id": "runtime_state",
                "task_id": task_ids[0],
                "status": "admitted",
            })
        conflicts = sorted(
            conflicts,
            key=lambda item: (item["source_id"], item["task_id"], item["status"]),
        )
        if conflicts and reason_id == "clear":
            reason_id = "target_has_runtime_admission"
        allowed = strict is True and reason_id == "clear" and not conflicts
        decision = {
            "schema_version": 1,
            "protocol_id": RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID,
            "strict": strict,
            "lock_mode": "shared" if shared else "exclusive",
            "task_ids": list(task_ids),
            "source_sha256": source_sha256,
            "conflicts": conflicts,
            "allowed": allowed,
            "reason_id": reason_id,
            "snapshot_sha256": _canonical_sha256(source_sha256),
        }
        mutation = os.environ.get("LOOP_TEST_DECISION_MUTATION")
        if mutation == "extra_field":
            decision["unbound"] = True
        elif mutation == "missing_field":
            decision.pop("reason_id")
        elif mutation == "snapshot":
            decision["snapshot_sha256"] = "0" * 64
        elif mutation == "source_order":
            decision["source_sha256"] = dict(reversed(list(source_sha256.items())))
            decision["snapshot_sha256"] = _canonical_sha256(decision["source_sha256"])
        elif mutation == "clear_with_conflict":
            decision["conflicts"] = [{
                "source_id": "runtime_state",
                "task_id": task_ids[0],
                "status": "admitted",
            }]
        yield decision


@contextmanager
def canonical_task_state_lock_file(path, *, shared: bool, nonblocking: bool):
    lock_path = Path(path).parent / ".orchestrator" / "task-state.lock"
    with _lock(
        lock_path,
        shared=shared,
        nonblocking=nonblocking,
        label="task_state",
    ):
        yield


@contextmanager
def activity_audit_lock_file(path, *, shared: bool, nonblocking: bool):
    _trace("request:activity_audit")
    with shared_activity_audit_lock_file(
        path,
        shared=shared,
        nonblocking=nonblocking,
    ):
        _trace("acquire:activity_audit")
        try:
            yield
        finally:
            _trace("release:activity_audit")
'''


def load_catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def baseline_catalog() -> dict:
    result = subprocess.run(
        [
            "git",
            "show",
            f"{HISTORICAL_CATALOG_COMMIT}:{HISTORICAL_CATALOG_PATH}",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert hashlib.sha256(result.stdout).hexdigest() == HISTORICAL_CATALOG_SHA256
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["program_id"] == "loop-product-level-remediation-2026-07-13"
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
    install_runtime_protocol(root)


def install_runtime_protocol(root: Path) -> None:
    orchestrator = root / ".orchestrator"
    orchestrator.mkdir(parents=True, exist_ok=True)
    writers = {
        ".orchestrator/runtime_state.py": FAKE_RUNTIME_PROTOCOL.encode("utf-8"),
        ".orchestrator/supervisor.py": b"# test supervisor using the shared lock protocol\n",
        ".orchestrator/common.py": b"# test shared runtime helpers\n",
        ".orchestrator/approval_queue.py": b"# test approval queue writer using the shared lock protocol\n",
        ".orchestrator/adapters/file_inbox.py": b"# test file inbox writer using the shared lock protocol\n",
        ".orchestrator/watch_events.py": b"# test event watcher using the shared lock protocol\n",
        ".orchestrator/supervisor_watchdog.py": b"# test watchdog writer using the shared lock protocol\n",
        "scripts/ai_status.py": b"# test status writer using the shared lock protocol\n",
        "scripts/dispatch_loop_product_level_remediation_2026-07-13.py": SCRIPT.read_bytes(),
    }
    writer_digests: dict[str, str] = {}
    for relative_path, body in writers.items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        writer_digests[relative_path] = hashlib.sha256(body).hexdigest()
    (orchestrator / "state.json").write_text(
        json.dumps({"schema_version": 1, "workers": []}) + "\n",
        encoding="utf-8",
    )
    (orchestrator / "event-queue.jsonl").write_text(
        json.dumps({"schema_version": 1, "type": "queue_snapshot", "events": []})
        + "\n",
        encoding="utf-8",
    )
    (orchestrator / "approval-queue.json").write_text(
        json.dumps({"schema_version": 1, "requests": []}) + "\n",
        encoding="utf-8",
    )
    writer_registry_path = orchestrator / "runtime-task-audit-writer-registry.json"
    writer_registry = {
        "schema_version": 1,
        "protocol_id": RUNTIME_PROTOCOL_ID,
        "transaction_scope": "complete_read_validate_mutate_replace",
        "direct_canonical_writes_forbidden": True,
        "writers": writer_digests,
    }
    writer_registry_path.write_text(
        json.dumps(writer_registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evidence_relative = (
        "docs/deployment/evidence/loop-product-level/"
        "LOOP-PROD-RUNTIME-BOOT-001/completion.json"
    )
    evidence_path = root / evidence_relative
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema_version": 1,
        "task_id": "LOOP-PROD-RUNTIME-BOOT-001",
        "task_contract_sha256": load_catalog()["dispatch_prerequisite"]
        ["task_contract_sha256"],
        "conclusion": "passed",
        "worker_runtime_identity": "Codex2",
        "reviewer_runtime_identity": "Codex",
        "checks_sha256": "2" * 64,
        "verdict_id": "runtime-bootstrap-verdict-1",
        "verifier_capability_sha256": writer_digests[
            ".orchestrator/runtime_state.py"
        ],
        "signature_algorithm": "ed25519",
        "key_id": "protected-runtime-review-key-1",
        "policy_version": "runtime-lock-capability-v1",
        "signature": "test-only-protected-signature",
        "revocation_checked_at": "2026-07-14T00:00:00Z",
        "ledger_entry_id": "protected-ledger-entry-1",
    }
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    tracked = [*writer_digests, ".orchestrator/runtime-task-audit-writer-registry.json", evidence_relative]
    subprocess.run(["git", "-C", str(root), "add", "--", *tracked], check=True)
    staged = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--quiet"],
        check=False,
    )
    if staged.returncode == 1:
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Runtime Test",
                "-c",
                "user.email=runtime-test@example.invalid",
                "commit",
                "-q",
                "-m",
                "test runtime capability",
            ],
            check=True,
        )
    elif staged.returncode != 0:
        raise AssertionError("failed to inspect staged runtime capability fixture")
    merged_commit_sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "update-ref",
            "refs/remotes/origin/dev",
            merged_commit_sha,
        ],
        check=True,
    )
    manifest = {
        "schema_version": 1,
        "protocol_id": RUNTIME_PROTOCOL_ID,
        "module_path": ".orchestrator/runtime_state.py",
        "lock_order": ["runtime_admission", "task_state", "activity_audit"],
        "stable_lock_paths": [
            ".orchestrator/runtime-admission.lock",
            ".orchestrator/task-state.lock",
            ".orchestrator/activity-audit.lock",
        ],
        "shared_read_supported": True,
        "api": [
            "tasks_runtime_admission_guard",
            "canonical_task_state_lock_file",
            "activity_audit_lock_file",
            "verify_runtime_lock_capability",
        ],
        "writers": writer_digests,
        "writer_registry_path": (
            ".orchestrator/runtime-task-audit-writer-registry.json"
        ),
        "writer_registry_sha256": hashlib.sha256(
            writer_registry_path.read_bytes()
        ).hexdigest(),
        "dispatcher_sha256": writer_digests[
            "scripts/dispatch_loop_product_level_remediation_2026-07-13.py"
        ],
        "bootstrap_task_id": "LOOP-PROD-RUNTIME-BOOT-001",
        "bootstrap_task_contract_sha256": load_catalog()["dispatch_prerequisite"]
        ["task_contract_sha256"],
        "bootstrap_completion_evidence_path": evidence_relative,
        "bootstrap_completion_evidence_sha256": hashlib.sha256(
            evidence_path.read_bytes()
        ).hexdigest(),
        "merged_commit_sha": merged_commit_sha,
    }
    (orchestrator / "runtime-task-audit-lock-capability.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_dispatch(
    root: Path,
    *args: str,
    catalog: Path = CATALOG,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cli_args = list(args) if args else ["--apply"]
    env = {
        **os.environ,
        "AI_NAME": "Codex",
        "PANTHEON_STATUS_ROOT": str(root),
        "LOOP_PRODUCT_TASK_CATALOG": str(catalog),
        "PYTHONDONTWRITEBYTECODE": "1",
        **(extra_env or {}),
    }
    return subprocess.run(
        ["python3", str(SCRIPT), *cli_args],
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


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def write_legacy_activity_fold(
    root: Path,
    *,
    conflicting_tail: bool = False,
) -> tuple[Path, Path]:
    predecessor_rows = [
        {
            "event_id": f"synthetic-legacy-event-{index:04d}",
            "type": "synthetic_legacy_activity",
            "message": f"synthetic legacy activity {index}",
        }
        for index in range(1001)
    ]
    successor_rows = deepcopy(predecessor_rows[-1000:])
    if conflicting_tail:
        successor_rows.append(
            {
                **predecessor_rows[0],
                "message": "synthetic legacy activity conflicting payload",
            }
        )
    else:
        successor_rows.append(
            {
                "event_id": "synthetic-legacy-event-successor",
                "type": "synthetic_legacy_activity",
                "message": "synthetic legacy activity successor",
            }
        )

    archive_root = root / "archive" / "logs"
    archive_root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, rows in (
        ("ai-activity-log.jsonl-2026-07-13T0000Z.gz", predecessor_rows),
        ("ai-activity-log.jsonl-2026-07-13T0001Z.gz", successor_rows),
    ):
        path = archive_root / name
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        paths.append(path)
    return paths[0], paths[1]


def refresh_route_derived(payload: dict) -> dict:
    row_key_fields = payload["required_row_key_fields"]
    payload["required_row_ids"] = [row["row_id"] for row in payload["rows"]]
    payload["required_row_keys"] = sorted(
        json.dumps(
            {field: row[field] for field in row_key_fields},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        for row in payload["rows"]
    )
    payload["required_path_method_pairs"] = [
        {"method": method, "path_template": path}
        for method, path in sorted(
            {(row["method"], row["path_template"]) for row in payload["rows"]}
        )
    ]
    return {"required_fixture_ids": payload["required_row_ids"]}


def program_tasks(state: dict) -> list[dict]:
    primary_ids = {str(task["id"]) for task in load_catalog()["tasks"]}
    return [
        task
        for task in state["tasks"]
        if str(task.get("id") or "") in primary_ids
    ]


def graph_binding(state: dict) -> dict:
    program_id = load_catalog()["program_id"]
    return state["program_catalog_graph_bindings"][program_id]


def write_terminal_archive(root: Path, task: dict, *, archived_at: str) -> dict:
    archived_task = deepcopy(task)
    archived_task["status"] = "done"
    terminal_outcome = str(archived_task.get("terminal_outcome") or "completed")
    payload = {
        "version": 1,
        "task_id": str(archived_task["id"]),
        "archived_at": archived_at,
        "terminal_status": "done",
        "terminal_outcome": terminal_outcome,
        "task": archived_task,
        "handoffs": [],
        "blockers": [],
    }
    path = root / "ai-task-archive" / "tasks" / f"{archived_task['id']}.json"
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return archived_task


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
    assert catalog["task_count"] == len(catalog["tasks"]) == 48
    assert len({task["id"] for task in catalog["tasks"]}) == 48
    assert len(catalog["additive_task_ids"]) == 12
    assert catalog["execution_authority"]["planner_may_edit_declared_product_artifacts"] is False
    assert catalog["execution_authority"]["implementation_role"] == "supervisor_admitted_fleet_worker"
    assert catalog["execution_authority"]["review_role"] == "distinct_supervisor_admitted_fleet_reviewer"
    assert catalog["execution_authority"]["required_worker_bindings"][-2:] == [
        "remote",
        "merge_target",
    ]
    assert catalog["completion_authority"]["dispatcher_pre_completion_policy"] == (
        "reject_preexisting_consumption_or_program_completed"
    )
    assert catalog["completion_authority"]["consumption_writer_task_id"] == (
        catalog["completion_authority"]["task_id"]
    )
    assert catalog["auth_lifecycle"]["strict_auth_prebootstrap_semantics"] == (
        "strict_auth_code_and_non_pristine_state_may_be_delivered_or_preserved_"
        "independently; hosted_qualification_lease_lifecycle_and_browser_"
        "activation_require_bootstrap"
    )
    assert len(catalog["loop_scope"]["canonical_l1_loop_ids"]) == 12
    assert catalog["loop_scope"]["composite_overlay_ids"] == ["per_persona_ooda"]
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


def test_strict_json_rejects_duplicate_keys_and_catalog_rejects_extra_task_keys() -> None:
    dispatch_error = DISPATCH["DispatchError"]
    with pytest.raises(dispatch_error, match="duplicate JSON key"):
        DISPATCH["strict_json_loads"](
            '{"task":{"id":"one","id":"two"}}',
            source="duplicate-test",
        )
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        candidate = write_catalog(
            root,
            lambda payload: payload["tasks"][0].__setitem__("unbound", True),
        )
        result = run_dispatch(root, "--validate-only", catalog=candidate)
        assert result.returncode == 2
        assert "unbound fields" in result.stderr


@pytest.mark.parametrize(
    "mutation",
    ["head_sha", "head_tree_sha", "replay_decision", "projection_digest"],
)
def test_incident_fixture_immutable_projection_rejects_mutation(mutation: str) -> None:
    payload = json.loads(INCIDENT_FIXTURE.read_text(encoding="utf-8"))
    reference = {"required_fixture_ids": [row["fixture_id"] for row in payload["fixtures"]]}
    row = payload["fixtures"][2]
    if mutation == "head_sha":
        row["pr"]["head_sha"] = "0" * 40
    elif mutation == "head_tree_sha":
        row["pr"]["head_tree_sha"] = "0" * 40
    elif mutation == "replay_decision":
        row["expected_replay"]["pre_switch_decision"] = "reject"
    else:
        payload["immutable_projection_sha256"] = "0" * 64
    with pytest.raises(
        DISPATCH["DispatchError"],
        match="immutable PR/tree/replay projection",
    ):
        DISPATCH["validate_browser_incident_fixture"](payload, reference)


@pytest.mark.parametrize(
    "mutation",
    ["historical_get", "privileged_negative", "attack_union", "logout"],
)
def test_browser_route_matrix_exact_unions_and_logout_reject_mutation(
    mutation: str,
) -> None:
    payload = json.loads(ROUTE_FIXTURE.read_text(encoding="utf-8"))
    by_id = {row["row_id"]: row for row in payload["rows"]}
    if mutation == "historical_get":
        payload["rows"] = [
            row for row in payload["rows"] if row["row_id"] != "viewer-cookie-me-get"
        ]
    elif mutation == "privileged_negative":
        by_id["viewer-tools-execute-deny"]["expected"]["product_success"] = True
    elif mutation == "attack_union":
        by_id["near-match-viewer-subject-me-deny"]["attack_classes"] = []
    else:
        by_id["viewer-cookie-logout-post"]["expected"]["state_delta"] = "clear_cookie_only"
    reference = refresh_route_derived(payload)
    with pytest.raises(DISPATCH["DispatchError"]):
        DISPATCH["validate_browser_route_fixture"](payload, reference)


@pytest.mark.parametrize(
    "mutation",
    [
        "planner_write",
        "implementation_role",
        "review_role",
        "same_runtime_review",
        "missing_binding",
        "missing_universal_rule",
    ],
)
def test_catalog_rejects_weakened_planner_fleet_authority(mutation: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)

        def mutate(payload: dict) -> None:
            authority = payload["execution_authority"]
            if mutation == "planner_write":
                authority["planner_may_edit_declared_product_artifacts"] = True
            elif mutation == "implementation_role":
                authority["implementation_role"] = "planner_or_worker"
            elif mutation == "review_role":
                authority["review_role"] = "same_session_reviewer"
            elif mutation == "same_runtime_review":
                authority["owner_reviewer_must_be_distinct_runtime_identities"] = False
            elif mutation == "missing_binding":
                authority["required_worker_bindings"].remove("task_worktree")
            else:
                payload["universal_dispatch_rules"] = [
                    rule
                    for rule in payload["universal_dispatch_rules"]
                    if "planner review is not independent review" not in rule
                ]

        candidate = write_catalog(root, mutate)
        result = run_dispatch(root, "--validate-only", catalog=candidate)

        assert result.returncode == 2
        assert "authority" in result.stderr


@pytest.mark.parametrize(
    "mutation",
    ["scope_contract", "unknown_task_loop", "inventory_union", "final_union", "coverage"],
)
def test_catalog_rejects_incomplete_or_foreign_loop_scope(mutation: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)

        def mutate(payload: dict) -> None:
            by_id = {task["id"]: task for task in payload["tasks"]}
            if mutation == "scope_contract":
                payload["loop_scope"]["canonical_l1_loop_ids"].pop()
            elif mutation == "unknown_task_loop":
                by_id["LOOP-PROD-SRC-001"]["loop_ids"].append("phantom_loop")
            elif mutation == "inventory_union":
                by_id["LOOP-PROD-000"]["loop_ids"].remove("source_ingestion")
            elif mutation == "final_union":
                by_id["LOOP-PROD-CLOSE-002"]["loop_ids"].remove("source_ingestion")
            else:
                excluded = {
                    "LOOP-PROD-000",
                    "LOOP-PROD-CLOSE-001",
                    "LOOP-PROD-CLOSE-002",
                }
                for task in payload["tasks"]:
                    if (
                        task["id"] not in excluded
                        and "source_ingestion" in task["loop_ids"]
                        and task["target_maturity"] == "product-level"
                    ):
                        task["target_maturity"] = "integrated"

        candidate = write_catalog(root, mutate)
        result = run_dispatch(root, "--validate-only", catalog=candidate)

        assert result.returncode == 2
        assert "loop" in result.stderr.lower()


def test_dry_run_is_zero_write() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root, "--dry-run")

        assert result.returncode == 0, result.stderr
        assert "summary migrate=0 create=48" in result.stdout
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
        assert len(tasks) == 48
        assert all(task["auto_created_by"].startswith("dispatch_loop_product") for task in tasks)
        assert all(task["loop_ids"] for task in tasks)
        assert all(task["proof_required"] for task in tasks)
        assert all(task["product_level_required"] is True for task in tasks)
        assert all(task["execution_role"] == "supervisor_admitted_fleet_worker" for task in tasks)
        assert all(task["review_role"] == "distinct_supervisor_admitted_fleet_reviewer" for task in tasks)
        assert all(task["planner_may_edit_declared_product_artifacts"] is False for task in tasks)
        assert all(task["formal_review_required"] is True for task in tasks)
        assert all(task["source_ref"]["execution_authority_sha256"] for task in tasks)
        assert state["agents"] == agents_before
        binding = graph_binding(state)
        projection = binding["graph_projection"]
        assert binding["binding_reason"] == "dispatcher_initial"
        assert binding["missing_binding_recovery_policy"] == "supervisor_signed_only"
        assert projection["catalog_sha256"] == binding["catalog_sha256"]
        assert projection["task_count"] == len(projection["tasks"]) == 48
        assert [row["task_id"] for row in projection["tasks"]] == [
            task["id"] for task in load_catalog()["tasks"]
        ]
        assert all(
            row["task_contract_sha256"] == canonical_sha256(
                {
                    field: next(
                        item
                        for item in load_catalog()["tasks"]
                        if item["id"] == row["task_id"]
                    ).get(field)
                    for field in sorted(DISPATCH["TASK_CONTRACT_FIELDS"])
                }
            )
            for row in projection["tasks"]
        )
        overlay = state["program_completion_authorities"][
            load_catalog()["program_id"]
        ]
        assert overlay["catalog_graph_binding_sha256"] == canonical_sha256(binding)
        assert overlay["catalog_graph_projection_sha256"] == binding[
            "graph_projection_sha256"
        ]
        assert len(log_after_first.decode("utf-8").splitlines()) == 49

        second = run_dispatch(root)
        assert second.returncode == 0, second.stderr
        assert "No state changes required." in second.stdout
        assert (root / "ai-status.json").read_bytes() == state_after_first
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_after_first


def test_activity_outbox_recovers_crash_between_status_and_log() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)

        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )

        assert interrupted.returncode == 2
        assert "activity audit remains in outbox" in interrupted.stderr
        interrupted_state = json.loads(
            (root / "ai-status.json").read_text(encoding="utf-8")
        )
        assert len(program_tasks(interrupted_state)) == 48
        outbox = interrupted_state["program_activity_outbox"]
        assert outbox["schema_version"] == 5
        assert outbox["actor_policy"] == load_catalog()["allowed_owners"]
        assert outbox["actor_policy_sha256"] == canonical_sha256(
            outbox["actor_policy"]
        )
        assert all(
            event["actor_policy_sha256"] == outbox["actor_policy_sha256"]
            for event in outbox["events"]
        )
        assert len(outbox["events"]) == 49
        assert (root / "ai-activity-log.jsonl").read_text(encoding="utf-8") == ""

        recovered = run_dispatch(root)

        assert recovered.returncode == 0, recovered.stderr
        assert "Recovered pending activity audit outbox." in recovered.stdout
        assert "No state changes required." in recovered.stdout
        recovered_state = json.loads(
            (root / "ai-status.json").read_text(encoding="utf-8")
        )
        assert recovered_state["program_activity_outbox"] is None
        records = [
            json.loads(line)
            for line in (root / "ai-activity-log.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(records) == 49
        assert len({record["event_id"] for record in records}) == 49


def test_activity_outbox_deduplicates_crash_after_log_append() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)

        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_ACTIVITY_APPEND": "1"},
        )

        assert interrupted.returncode == 2
        assert "status outbox remains pending" in interrupted.stderr
        interrupted_state = json.loads(
            (root / "ai-status.json").read_text(encoding="utf-8")
        )
        assert len(interrupted_state["program_activity_outbox"]["events"]) == 49
        assert len(
            (root / "ai-activity-log.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ) == 49
        exact_log = (root / "ai-activity-log.jsonl").read_bytes()

        recovered = run_dispatch(root)

        assert recovered.returncode == 0, recovered.stderr
        assert "Recovered pending activity audit outbox." in recovered.stdout
        recovered_state = json.loads(
            (root / "ai-status.json").read_text(encoding="utf-8")
        )
        assert recovered_state["program_activity_outbox"] is None
        assert (root / "ai-activity-log.jsonl").read_bytes() == exact_log
        records = [
            json.loads(line)
            for line in (root / "ai-activity-log.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(records) == 49
        assert len({record["event_id"] for record in records}) == 49


def test_activity_outbox_recovers_after_assigned_task_is_archived() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        task = next(item for item in state["tasks"] if item.get("id") == "LOOP-PROD-000")
        state["tasks"] = [
            item for item in state["tasks"] if item.get("id") != "LOOP-PROD-000"
        ]
        write_terminal_archive(
            root,
            task,
            archived_at="2026-07-15T00:00:00Z",
        )
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        recovered = run_dispatch(root)

        assert recovered.returncode == 0, recovered.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        assert after["program_activity_outbox"] is None
        assert not any(task.get("id") == "LOOP-PROD-000" for task in after["tasks"])
        assert (root / "ai-task-archive" / "tasks" / "LOOP-PROD-000.json").is_file()
        records = [
            json.loads(line)
            for line in (root / "ai-activity-log.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(records) == 49
        assert len({record["event_id"] for record in records}) == 49


def test_activity_outbox_deduplicates_events_in_old_mtime_archive() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_ACTIVITY_APPEND": "1"},
        )
        assert interrupted.returncode == 2
        active_log = root / "ai-activity-log.jsonl"
        exact_log = active_log.read_text(encoding="utf-8")
        archive = (
            root
            / "archive"
            / "logs"
            / "ai-activity-log.jsonl-2000-01-01T0000Z.gz"
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(archive, "wt", encoding="utf-8") as handle:
            handle.write(exact_log)
        os.utime(archive, (1, 1))
        active_log.write_text("", encoding="utf-8")

        recovered = run_dispatch(root)

        assert recovered.returncode == 0, recovered.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        assert after["program_activity_outbox"] is None
        assert active_log.read_text(encoding="utf-8") == ""
        with gzip.open(archive, "rt", encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        assert len(records) == 49
        assert len({record["event_id"] for record in records}) == 49


def test_activity_outbox_repairs_interrupted_tail_then_replays_once() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        events = state["program_activity_outbox"]["events"]
        (root / "ai-activity-log.jsonl").write_bytes(
            (json.dumps(events[0], ensure_ascii=False) + "\n").encode("utf-8")
            + b'{"event_id":"interrupted-tail"'
        )

        recovered = run_dispatch(root)

        assert recovered.returncode == 0, recovered.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        assert after["program_activity_outbox"] is None
        rows = [
            json.loads(line)
            for line in (root / "ai-activity-log.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(rows) == len(events)
        assert {row["event_id"] for row in rows} == {
            event["event_id"] for event in events
        }


def test_activity_outbox_same_id_different_payload_retains_outbox() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        conflicting = deepcopy(state["program_activity_outbox"]["events"][0])
        conflicting["message"] += " forged"
        (root / "ai-activity-log.jsonl").write_text(
            json.dumps(conflicting, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        recovered = run_dispatch(root, "--apply")

        assert recovered.returncode == 2
        assert "activity audit event_id payload binding mismatch" in recovered.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before
        assert json.loads(status_before)["program_activity_outbox"] is not None


@pytest.mark.parametrize("mutation", ["member", "order", "hash"])
def test_activity_outbox_rejects_actor_policy_mutation(mutation: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        outbox = state["program_activity_outbox"]
        if mutation == "member":
            outbox["actor_policy"] = ["Codex2"]
            outbox["actor_policy_sha256"] = canonical_sha256(
                outbox["actor_policy"]
            )
        elif mutation == "order":
            outbox["actor_policy"] = list(reversed(outbox["actor_policy"]))
            outbox["actor_policy_sha256"] = canonical_sha256(
                outbox["actor_policy"]
            )
        else:
            outbox["actor_policy_sha256"] = "0" * 64
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        recovered = run_dispatch(root, "--apply", extra_env={"AI_NAME": "Codex2"})

        assert recovered.returncode == 2
        assert "actor policy" in recovered.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


@pytest.mark.parametrize(
    "mutation",
    [
        "event_id",
        "transaction_id",
        "ordinal",
        "program_id",
        "catalog_sha256",
        "task_id",
        "duplicate_event_id",
        "extra_field",
    ],
)
def test_activity_outbox_rejects_unbound_or_corrupt_events(mutation: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        events = state["program_activity_outbox"]["events"]
        first = events[0]
        if mutation == "event_id":
            first["event_id"] += "-forged"
        elif mutation == "transaction_id":
            first["transaction_id"] += "-forged"
        elif mutation == "ordinal":
            first["ordinal"] = 99
        elif mutation == "program_id":
            first["program_id"] = "foreign-program"
        elif mutation == "catalog_sha256":
            first["catalog_sha256"] = "0" * 64
        elif mutation == "task_id":
            first["task_id"] = "FOREIGN-TASK"
        elif mutation == "duplicate_event_id":
            events[1]["event_id"] = first["event_id"]
        else:
            first["unbound"] = True
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        recovered = run_dispatch(root)

        assert recovered.returncode == 2
        assert "program_activity_outbox" in recovered.stderr or "activity" in recovered.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_frozen_wave_recovers_committed_audit_then_blocks_new_dispatch() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        state["wave_state"]["status"] = "frozen"
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        recovered = run_dispatch(root)

        assert recovered.returncode == 2
        assert "planning wave is frozen" in recovered.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        assert after["program_activity_outbox"] is None
        records = (root / "ai-activity-log.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(records) == 49


@pytest.mark.parametrize("corruption", ["malformed", "duplicate"])
def test_activity_outbox_recovery_rejects_corrupt_active_audit_log(
    corruption: str,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        if corruption == "malformed":
            body = '{"event_id":"truncated"\n'
        else:
            line = json.dumps(
                state["program_activity_outbox"]["events"][0],
                ensure_ascii=False,
            )
            body = line + "\n" + line + "\n"
        (root / "ai-activity-log.jsonl").write_text(body, encoding="utf-8")
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        recovered = run_dispatch(root)

        assert recovered.returncode == 2
        assert "activity audit" in recovered.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_activity_outbox_rejects_exact_duplicates_across_rotated_history() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        body = "".join(
            json.dumps(entry, ensure_ascii=False) + "\n"
            for entry in state["program_activity_outbox"]["events"]
        )
        archive = root / "archive" / "logs" / "ai-activity-log.jsonl-2026-07-13T0000Z.gz"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(archive, "wt", encoding="utf-8") as handle:
            handle.write(body)
        (root / "ai-activity-log.jsonl").write_text(body, encoding="utf-8")

        recovered = run_dispatch(root)

        assert recovered.returncode == 2
        assert "activity event_id duplicate across sources" in recovered.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        assert after["program_activity_outbox"] is not None
        assert (root / "ai-activity-log.jsonl").read_text(encoding="utf-8") == body


def test_dispatch_accepts_shared_legacy_fold_and_remains_idempotent() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        sources = write_legacy_activity_fold(root)
        sources_before = {path: path.read_bytes() for path in sources}

        first = run_dispatch(root)

        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        assert [task["id"] for task in program_tasks(state)] == [
            task["id"] for task in load_catalog()["tasks"]
        ]
        status_after_first = (root / "ai-status.json").read_bytes()
        log_after_first = (root / "ai-activity-log.jsonl").read_bytes()

        second = run_dispatch(root)

        assert second.returncode == 0, second.stderr
        assert "No state changes required." in second.stdout
        assert (root / "ai-status.json").read_bytes() == status_after_first
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_after_first
        assert {path: path.read_bytes() for path in sources} == sources_before


def test_dispatch_rejects_payload_mismatch_after_shared_legacy_fold() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        sources = write_legacy_activity_fold(root, conflicting_tail=True)
        sources_before = {path: path.read_bytes() for path in sources}
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "activity event_id payload mismatch" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before
        assert {path: path.read_bytes() for path in sources} == sources_before


def test_activity_event_index_fully_drains_shared_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function_globals = DISPATCH["activity_event_index"].__globals__

    def mutating_stream(_path: Path):
        yield (
            {
                "event_id": "synthetic-logical-event",
                "type": "synthetic_activity",
            },
            Path("synthetic-source.jsonl"),
            1,
        )
        raise RuntimeError("Source mutated or truncated during read")

    monkeypatch.setitem(
        function_globals,
        "stream_logical_activity",
        mutating_stream,
    )

    with pytest.raises(
        DISPATCH["DispatchError"],
        match="Source mutated or truncated during read",
    ):
        DISPATCH["activity_event_index"]()


@pytest.mark.parametrize("source", ["active", "archive"])
def test_foreign_additive_final_authority_collision_fails_closed(source: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_baseline_program(root)
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        rogue = {
            "id": "LOOP-PROD-CLOSE-002",
            "status": "done",
            "acceptance": ["candidate-controlled closeout"],
            "completion_role": "final_authority",
        }
        if source == "active":
            state["tasks"].append(rogue)
            (root / "ai-status.json").write_text(
                json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        else:
            (root / "ai-task-archive" / "tasks" / "LOOP-PROD-CLOSE-002.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "task_id": "LOOP-PROD-CLOSE-002",
                        "terminal_status": "done",
                        "task": rogue,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        if source == "active":
            assert "graph binding is missing" in result.stderr
        else:
            assert "must retain an exact full task contract" in result.stderr
            assert "LOOP-PROD-CLOSE-002" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


@pytest.mark.parametrize(
    "axis",
    [
        "program_id",
        "catalog_sha256",
        "task_contract_sha256",
        "execution_authority_sha256",
        "completion_role",
        "helper_kind",
        "execution_role",
        "review_role",
        "planner_controller_identity",
        "planner_may_edit_declared_product_artifacts",
        "formal_review_required",
        "contract_field",
    ],
)
def test_additive_collision_rejects_each_foreign_or_stale_axis(axis: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        task = next(
            item for item in state["tasks"] if item.get("id") == "LOOP-PROD-CLOSE-002"
        )
        if axis in {
            "program_id",
            "catalog_sha256",
            "task_contract_sha256",
            "execution_authority_sha256",
        }:
            task["source_ref"][axis] = "foreign"
        elif axis == "contract_field":
            task["acceptance"] = ["candidate-controlled closeout"]
        elif axis in {
            "planner_may_edit_declared_product_artifacts",
            "formal_review_required",
        }:
            task[axis] = not task[axis]
        else:
            task[axis] = "foreign"
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        if axis == "contract_field":
            assert "graph contract or dependency mismatch" in result.stderr
        else:
            assert "foreign or stale active collision" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_nonadditive_active_preserve_rejects_missing_or_mismatched_source_ref() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr

        mutations = (
            lambda task: task.pop("source_ref", None),
            lambda task: task.__setitem__("source_ref", {}),
            lambda task: task["source_ref"].__setitem__(
                "program_id", "foreign-program"
            ),
            lambda task: task["source_ref"].__setitem__("catalog_sha256", ""),
        )
        for mutation in mutations:
            state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
            task = next(
                item for item in state["tasks"] if item.get("id") == "LOOP-PROD-000"
            )
            mutation(task)
            (root / "ai-status.json").write_text(
                json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            status_before = (root / "ai-status.json").read_bytes()
            log_before = (root / "ai-activity-log.jsonl").read_bytes()

            result = run_dispatch(root)

            assert result.returncode == 2, result.stdout
            assert (
                "missing or mismatched source_ref provenance for active catalog "
                "task LOOP-PROD-000" in result.stderr
            )
            assert (root / "ai-status.json").read_bytes() == status_before
            assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_exact_additive_archive_collision_is_preserved_without_resurrection() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        archived_task = next(
            item for item in state["tasks"] if item.get("id") == "LOOP-PROD-CLOSE-002"
        )
        state["tasks"] = [
            item for item in state["tasks"] if item.get("id") != "LOOP-PROD-CLOSE-002"
        ]
        write_terminal_archive(
            root,
            archived_task,
            archived_at="2026-07-15T00:00:00Z",
        )
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 0, result.stderr
        assert "SKIP-ARCHIVED LOOP-PROD-CLOSE-002:done" in result.stdout
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_program_task_cannot_exist_in_active_and_archive_state() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        task = next(item for item in state["tasks"] if item.get("id") == "LOOP-PROD-000")
        write_terminal_archive(
            root,
            task,
            archived_at="2026-07-15T00:00:00Z",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "exist in both active and archive state" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_duplicate_live_task_ids_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state = base_state()
        state["tasks"].extend(
            [
                {"id": "LOOP-PROD-CLOSE-002", "status": "todo"},
                {"id": "LOOP-PROD-CLOSE-002", "status": "done"},
            ]
        )
        prepare_status(root, state)
        status_before = (root / "ai-status.json").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "duplicate live task IDs" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_text(encoding="utf-8") == ""


def test_minimal_terminal_archive_without_exact_task_contract_fails_closed() -> None:
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

        assert result.returncode == 2
        assert "must retain an exact full task contract" in result.stderr
        assert not program_tasks(
            json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        )
        assert (root / "ai-activity-log.jsonl").read_text(encoding="utf-8") == ""


def test_exact_nonadditive_terminal_archive_is_preserved_without_resurrection() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        task = next(item for item in state["tasks"] if item.get("id") == "LOOP-PROD-000")
        state["tasks"] = [
            item for item in state["tasks"] if item.get("id") != "LOOP-PROD-000"
        ]
        write_terminal_archive(
            root,
            task,
            archived_at="2026-07-15T00:00:00Z",
        )
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 0, result.stderr
        assert "SKIP-ARCHIVED LOOP-PROD-000:done" in result.stdout
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_terminal_archive_symlink_is_rejected_without_writes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        task = next(item for item in state["tasks"] if item.get("id") == "LOOP-PROD-000")
        state["tasks"] = [
            item for item in state["tasks"] if item.get("id") != "LOOP-PROD-000"
        ]
        write_terminal_archive(
            root,
            task,
            archived_at="2026-07-15T00:00:00Z",
        )
        archive = root / "ai-task-archive" / "tasks" / "LOOP-PROD-000.json"
        external = root / "external-LOOP-PROD-000.json"
        archive.replace(external)
        archive.symlink_to(external)
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "canonical task archive must be a regular file" in result.stderr
        assert archive.is_symlink()
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_external_dependency_archive_symlink_is_rejected_without_writes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        dep_id = "LOOP-PROD-RUNTIME-BOOT-001"
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        task = next(item for item in state["tasks"] if item.get("id") == dep_id)
        state["tasks"] = [item for item in state["tasks"] if item.get("id") != dep_id]
        write_terminal_archive(root, task, archived_at="2026-07-15T00:00:00Z")
        archive = root / "ai-task-archive" / "tasks" / f"{dep_id}.json"
        external = root / f"external-{dep_id}.json"
        archive.replace(external)
        archive.symlink_to(external)
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "canonical task archive must be a regular file" in result.stderr
        assert archive.is_symlink()
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_in_progress_task_with_rewritten_contract_fails_graph_binding() -> None:
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
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "contract or dependency mismatch" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


@pytest.mark.parametrize("mutation", ["missing", "tampered"])
def test_missing_or_tampered_catalog_graph_binding_fails_without_writes(
    mutation: str,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        program_id = load_catalog()["program_id"]
        if mutation == "missing":
            del state["program_catalog_graph_bindings"][program_id]
        else:
            state["program_catalog_graph_bindings"][program_id][
                "graph_projection"
            ]["tasks"][0]["depends_on"].append("FOREIGN-TASK")
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "graph binding" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_graph_binding_requires_one_unique_install_audit_event(mutation: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr
        log_path = root / "ai-activity-log.jsonl"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        install = next(
            line
            for line in lines
            if json.loads(line).get("type") == "completion_authority_install"
        )
        if mutation == "missing":
            lines = [line for line in lines if line != install]
        else:
            lines.append(install)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        status_before = (root / "ai-status.json").read_bytes()
        log_before = log_path.read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        if mutation == "missing":
            assert "exactly one durable install audit event" in result.stderr
        else:
            assert "duplicate activity event_id in" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert log_path.read_bytes() == log_before


def test_self_consistent_graph_binding_recreation_cannot_bypass_install_audit() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        program_id = load_catalog()["program_id"]
        binding = state["program_catalog_graph_bindings"][program_id]
        binding["bound_at"] = "2026-07-15T23:59:59Z"
        unsigned = {key: deepcopy(value) for key, value in binding.items() if key != "binding_id"}
        binding["binding_id"] = "loop-product-graph-binding-" + canonical_sha256(unsigned)
        state["program_completion_authorities"][program_id][
            "catalog_graph_binding_sha256"
        ] = canonical_sha256(binding)
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "does not match its durable install audit event" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_fabricated_historical_graph_binding_requires_supervisor_recovery() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        catalog = load_catalog()
        projection = DISPATCH["historical_catalog_graph_projection"](catalog)
        binding = DISPATCH["build_program_graph_binding"](
            projection,
            bound_at="2026-07-15T00:00:00Z",
            binding_reason="fabricated-recovery",
            previous_graph_projection_sha256="0" * 64,
        )
        state["program_catalog_graph_bindings"] = {
            catalog["program_id"]: binding,
        }
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "historical graph binding recreation is forbidden" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_baseline_nonmigration_dependency_rewrite_fails_graph_binding() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        task = next(
            item for item in state["tasks"] if item.get("id") == "LOOP-PROD-AUTH-001"
        )
        task["depends_on"].append("LOOP-PROD-ATTEST-001")
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "contract or dependency mismatch" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_terminal_archive_stub_cannot_mask_rewritten_live_task() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root)
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        task = next(item for item in state["tasks"] if item.get("id") == "LOOP-PROD-000")
        task["depends_on"].append("LOOP-PROD-ATTEST-001")
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
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "must retain an exact full task contract" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


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


def test_stable_task_lock_rejects_concurrent_dispatcher() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        lock_path = root / ".orchestrator" / "task-state.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run_dispatch(root)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        assert result.returncode == 2
        assert "lock set is busy" in result.stderr
        assert not program_tasks(
            json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        )


def prepare_baseline_program(root: Path) -> tuple[dict, Path]:
    baseline = baseline_catalog()
    baseline_path = write_payload(root, baseline, "baseline-tasks.json")
    state = base_state()
    timestamp = "2026-07-13T00:00:00Z"
    source_ref = {
        "plan": baseline["source_plan"],
        "packet": baseline["packet"],
        "catalog": HISTORICAL_CATALOG_PATH,
        "catalog_sha256": HISTORICAL_CATALOG_SHA256,
        "program_id": baseline["program_id"],
    }
    for task in baseline["tasks"]:
        live_task = deepcopy(task)
        live_task.update(
            {
                "created_at": timestamp,
                "last_update": timestamp,
                "task_class": "execution",
                "auto_created_by": "dispatch_loop_product_level_remediation_2026-07-13",
                "auto_generated": True,
                "delivery_layer": "primary",
                "mutates_canonical": True,
                "helper_kind": "loop_product_level_execution_slice",
                "source_ref": deepcopy(source_ref),
            }
        )
        state["tasks"].append(live_task)
    prepare_status(root, state)
    return state, baseline_path


def test_addendum_delta_is_exact_and_has_one_final_authority() -> None:
    current = load_catalog()
    baseline = baseline_catalog()
    current_by_id = {task["id"]: task for task in current["tasks"]}
    baseline_by_id = {task["id"]: task for task in baseline["tasks"]}
    added = set(current_by_id) - set(baseline_by_id)

    assert added == set(current["additive_task_ids"])
    assert len(added) == 12
    assert not (set(baseline_by_id) - set(current_by_id))
    migration_targets = {
        patch["task_id"]
        for migration in current["catalog_migrations"]
        for patch in migration["required_live_task_patches"]
    }
    assert migration_targets == {
        "LOOP-PROD-AGORA-002",
        "LOOP-PROD-MAI-001",
        "LOOP-PROD-CLOSE-001",
    }
    assert current_by_id["LOOP-PROD-AUTH-001"] == baseline_by_id[
        "LOOP-PROD-AUTH-001"
    ]
    for task_id in set(baseline_by_id) - migration_targets:
        assert current_by_id[task_id] == baseline_by_id[task_id]
    for migration in current["catalog_migrations"]:
        for patch in migration["required_live_task_patches"]:
            before = baseline_by_id[patch["task_id"]]
            after = current_by_id[patch["task_id"]]
            changed_fields = {
                field
                for field in set(before) | set(after)
                if before.get(field) != after.get(field)
            }
            assert changed_fields == set(patch["allowed_contract_field_changes"])

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
        assert "summary migrate=3 create=12 preserve=36 archived=0 total=48" in result.stdout
        state_after_first = (root / "ai-status.json").read_bytes()
        log_after_first = (root / "ai-activity-log.jsonl").read_bytes()
        state = json.loads(state_after_first)
        current = load_catalog()
        current_by_id = {task["id"]: task for task in current["tasks"]}
        state_by_id = {task["id"]: task for task in program_tasks(state)}
        assert len(state_by_id) == 48
        migration = current["catalog_migrations"][0]
        for patch in migration["required_live_task_patches"]:
            task_id = patch["task_id"]
            expected = deepcopy(before_by_id[task_id])
            for field in patch["allowed_contract_field_changes"]:
                expected[field] = deepcopy(current_by_id[task_id][field])
            for field, value in patch["set_runtime_fields"].items():
                expected[field] = deepcopy(value)
            assert state_by_id[task_id] == expected
        records = state["program_catalog_migrations"]
        assert [record["id"] for record in records] == ["loop-product-gap-addendum-v5"]
        assert len(records[0]["patches"]) == 3
        assert len(log_after_first.decode("utf-8").splitlines()) == 16

        second = run_dispatch(root)
        assert second.returncode == 0, second.stderr
        assert "No state changes required." in second.stdout
        assert "summary migrate=0 create=0 preserve=48 archived=0 total=48" in second.stdout
        assert (root / "ai-status.json").read_bytes() == state_after_first
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_after_first


def test_running_auth_task_is_preserved_and_browser_gate_is_additive() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        auth = next(task for task in state["tasks"] if task.get("id") == "LOOP-PROD-AUTH-001")
        auth.update(
            {
                "status": "in_progress",
                "branch": "task/existing-auth-run",
                "custom_preserved_field": {"sentinel": "running-auth"},
            }
        )
        auth_before = deepcopy(auth)
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        result = run_dispatch(root)

        assert result.returncode == 0, result.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        by_id = {task["id"]: task for task in after["tasks"]}
        assert by_id["LOOP-PROD-AUTH-001"] == auth_before
        assert by_id["LOOP-PROD-BROWSER-AUTH-001"]["depends_on"] == [
            "LOOP-PROD-AUTH-BOOT-001",
            "LOOP-PROD-AUTH-001",
            "LOOP-PROD-FE-001",
            "LOOP-PROD-DELIVERY-001",
            "LOOP-PROD-LEASE-001",
            "LOOP-PROD-AUTH-OPS-001",
        ]
        assert {
            patch["task_id"]
            for record in after["program_catalog_migrations"]
            for patch in record["patches"]
        } == {
            "LOOP-PROD-AGORA-002",
            "LOOP-PROD-MAI-001",
            "LOOP-PROD-CLOSE-001",
        }


def test_live_addendum_migration_rejects_exact_record_while_tasks_are_preimage() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        preimage_state, _ = prepare_baseline_program(root)
        catalog = load_catalog()
        migration = catalog["catalog_migrations"][0]
        applied = run_dispatch(root)
        assert applied.returncode == 0, applied.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        before_by_id = {
            task["id"]: task
            for task in preimage_state["tasks"]
            if isinstance(task, dict) and "id" in task
        }
        target_ids = {
            patch["task_id"] for patch in migration["required_live_task_patches"]
        }
        state["tasks"] = [
            deepcopy(before_by_id[task["id"]])
            if task.get("id") in target_ids
            else task
            for task in state["tasks"]
        ]
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "graph contract or dependency mismatch" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


@pytest.mark.parametrize(
    "mutation",
    ["program_id", "patch_hash", "appended_dependencies", "extra_field", "duplicate"],
)
def test_live_addendum_migration_rejects_noncanonical_or_duplicate_record(
    mutation: str,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_baseline_program(root)
        migrated = run_dispatch(root)
        assert migrated.returncode == 0, migrated.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        record = state["program_catalog_migrations"][0]
        if mutation == "program_id":
            record["program_id"] = "foreign-program"
        elif mutation == "patch_hash":
            record["patches"][0]["before_depends_on_sha256"] = "0" * 64
        elif mutation == "appended_dependencies":
            record["patches"][0]["appended_dependencies"] = ["FOREIGN-TASK"]
        elif mutation == "extra_field":
            record["unbound"] = True
        else:
            state["program_catalog_migrations"].append(deepcopy(record))
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "migration" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


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


def test_every_runtime_admission_alias_is_detected_without_catalog_field_confusion() -> None:
    markers = DISPATCH["LIVE_ADMISSION_MARKER_FIELDS"]
    required_runtime_aliases = {
        "run_id",
        "worker_provider",
        "worker_slot",
        "task_worktree",
        "declared_scope",
        "expected_branch",
        "remote",
    }
    assert required_runtime_aliases.issubset(markers)
    assert "merge_target" not in markers
    assert DISPATCH["_has_live_admission"]({"merge_target": "dev"}) is False
    for field in markers:
        assert DISPATCH["_has_live_admission"]({field: "bound"}) is True


@pytest.mark.parametrize(
    "task_id",
    ["LOOP-PROD-AGORA-002", "LOOP-PROD-MAI-001", "LOOP-PROD-CLOSE-001"],
)
def test_each_migration_target_rejects_full_preimage_or_admission_drift(
    task_id: str,
) -> None:
    for mutation in ("immutable", "admission"):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, _ = prepare_baseline_program(root)
            target = next(task for task in state["tasks"] if task.get("id") == task_id)
            if mutation == "immutable":
                target["title"] += " tampered"
            else:
                target["remote"] = "https://example.invalid/foreign.git"
            (root / "ai-status.json").write_text(
                json.dumps(state, indent=2) + "\n",
                encoding="utf-8",
            )
            before = (root / "ai-status.json").read_bytes()
            result = run_dispatch(root, "--apply")
            assert result.returncode == 2
            assert (root / "ai-status.json").read_bytes() == before


@pytest.mark.parametrize(
    "task_id",
    ["LOOP-PROD-AGORA-002", "LOOP-PROD-MAI-001", "LOOP-PROD-CLOSE-001"],
)
@pytest.mark.parametrize(
    "mutation",
    ["owner", "same_reviewer", "created_at", "last_update", "next"],
)
def test_each_migration_target_validates_mutable_preimage_fields(
    task_id: str,
    mutation: str,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        target = next(task for task in state["tasks"] if task.get("id") == task_id)
        if mutation == "owner":
            target["owner"] = "Rogue"
        elif mutation == "same_reviewer":
            target["reviewer"] = target["owner"]
        elif mutation == "created_at":
            target["created_at"] = "not-a-timestamp"
        elif mutation == "last_update":
            target["last_update"] = "2000-01-01T00:00:00Z"
        else:
            target["next"] = ""
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2) + "\n",
            encoding="utf-8",
        )
        before = (root / "ai-status.json").read_bytes()
        result = run_dispatch(root, "--apply")
        assert result.returncode == 2
        assert (root / "ai-status.json").read_bytes() == before


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
            "canonical contract marker is stale or missing",
        ),
        (
            "multiple_completion_sinks",
            lambda payload: payload["tasks"][-1]["depends_on"].remove(
                "LOOP-PROD-FLEET-001"
            ),
            "canonical contract marker is stale or missing",
        ),
        (
            "missing_verdict_task_binding",
            lambda payload: payload["completion_authority"][
                "verdict_binding_fields"
            ].remove("task_id"),
            "completion_authority must match the exact reviewed contract",
        ),
        (
            "missing_verdict_attestation_policy",
            lambda payload: payload["completion_authority"][
                "verdict_binding_fields"
            ].remove("attestation_policy"),
            "completion_authority must match the exact reviewed contract",
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


def test_dispatch_requires_an_explicit_mode_and_validate_only_is_read_only() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        before = (root / "ai-status.json").read_bytes()
        result = subprocess.run(
            ["python3", str(SCRIPT)],
            env={
                **os.environ,
                "PANTHEON_STATUS_ROOT": str(root),
                "AI_NAME": "Codex",
            },
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 2
        assert "one of the arguments" in result.stderr
        assert (root / "ai-status.json").read_bytes() == before


def test_validate_only_does_not_require_runtime_capability() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        (root / ".orchestrator" / "runtime-task-audit-lock-capability.json").unlink()
        result = run_dispatch(root, "--validate-only")
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["--dry-run", "--apply"])
def test_authoritative_modes_fail_closed_without_runtime_capability(mode: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        (root / ".orchestrator" / "runtime-task-audit-lock-capability.json").unlink()
        before = (root / "ai-status.json").read_bytes()
        result = run_dispatch(root, mode)
        assert result.returncode == 2
        assert "runtime lock capability" in result.stderr
        assert (root / "ai-status.json").read_bytes() == before
        assert (root / "ai-activity-log.jsonl").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_writer",
        "shared_false",
        "bad_digest",
        "dispatcher_digest",
        "registry_digest",
        "bootstrap_task",
        "evidence_digest",
        "merged_commit",
        "registry_content",
        "evidence_content",
        "evidence_signature",
    ],
)
def test_runtime_capability_manifest_is_exact(mutation: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        path = root / ".orchestrator" / "runtime-task-audit-lock-capability.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if mutation == "extra_writer":
            manifest["writers"]["scripts/extra_writer.py"] = "0" * 64
        elif mutation == "shared_false":
            manifest["shared_read_supported"] = False
        elif mutation == "bad_digest":
            manifest["writers"]["scripts/ai_status.py"] = "0" * 64
        elif mutation == "dispatcher_digest":
            manifest["dispatcher_sha256"] = "0" * 64
        elif mutation == "registry_digest":
            manifest["writer_registry_sha256"] = "0" * 64
        elif mutation == "bootstrap_task":
            manifest["bootstrap_task_id"] = "FOREIGN-BOOTSTRAP"
        elif mutation == "evidence_digest":
            manifest["bootstrap_completion_evidence_sha256"] = "0" * 64
        elif mutation == "merged_commit":
            manifest["merged_commit_sha"] = "0" * 40
        elif mutation == "registry_content":
            registry_path = root / manifest["writer_registry_path"]
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["transaction_scope"] = "partial_write"
            registry_path.write_text(json.dumps(registry) + "\n", encoding="utf-8")
            manifest["writer_registry_sha256"] = hashlib.sha256(
                registry_path.read_bytes()
            ).hexdigest()
        else:
            evidence_path = root / manifest["bootstrap_completion_evidence_path"]
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            if mutation == "evidence_content":
                evidence["conclusion"] = "self_attested"
            else:
                evidence["signature"] = "forged-signature"
            evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
            manifest["bootstrap_completion_evidence_sha256"] = hashlib.sha256(
                evidence_path.read_bytes()
            ).hexdigest()
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        before = (root / "ai-status.json").read_bytes()
        result = run_dispatch(root, "--dry-run")
        assert result.returncode == 2
        assert result.stderr.startswith("ERROR:")
        assert (root / "ai-status.json").read_bytes() == before


@pytest.mark.parametrize("mutation", ["deny", "extra"])
def test_runtime_capability_requires_exact_protected_verifier_decision(
    mutation: str,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        before = (root / "ai-status.json").read_bytes()
        result = run_dispatch(
            root,
            "--dry-run",
            extra_env={"LOOP_TEST_CAPABILITY_VERIFIER_MUTATION": mutation},
        )
        assert result.returncode == 2
        assert "protected capability verifier decision is not exact" in result.stderr
        assert (root / "ai-status.json").read_bytes() == before


def test_lock_order_is_runtime_then_task_then_audit() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        trace = root / "lock-trace.log"
        result = run_dispatch(
            root,
            "--dry-run",
            extra_env={"LOOP_TEST_LOCK_TRACE": str(trace)},
        )
        assert result.returncode == 0, result.stderr
        acquired = [
            line.removeprefix("acquire:")
            for line in trace.read_text(encoding="utf-8").splitlines()
            if line.startswith("acquire:") and line.count(":") == 1
        ]
        assert acquired == ["runtime_admission", "task_state", "activity_audit"]


def test_stable_lock_survives_ai_status_inode_replace() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        lock_path = root / ".orchestrator" / "task-state.lock"
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            replacement = root / "ai-status.replacement.json"
            replacement.write_bytes((root / "ai-status.json").read_bytes())
            os.replace(replacement, root / "ai-status.json")
            result = run_dispatch(root, "--apply")
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        assert result.returncode == 2
        assert "lock set is busy" in result.stderr


def test_runtime_admission_snapshot_blocks_queued_task_without_status_write() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        (root / ".orchestrator" / "event-queue.jsonl").write_text(
            json.dumps({"task_id": "LOOP-PROD-AGORA-002", "status": "queued"}) + "\n",
            encoding="utf-8",
        )
        before = (root / "ai-status.json").read_bytes()
        result = run_dispatch(root, "--apply")
        assert result.returncode == 2
        assert "runtime admission blocked" in result.stderr
        assert (root / "ai-status.json").read_bytes() == before
        assert (root / "ai-activity-log.jsonl").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    "mutation",
    ["extra_field", "missing_field", "snapshot", "source_order", "clear_with_conflict"],
)
def test_runtime_admission_decision_schema_is_exact_and_zero_write(
    mutation: str,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        before_status = (root / "ai-status.json").read_bytes()
        before_log = (root / "ai-activity-log.jsonl").read_bytes()
        result = run_dispatch(
            root,
            "--apply",
            extra_env={"LOOP_TEST_DECISION_MUTATION": mutation},
        )
        assert result.returncode == 2
        assert "runtime admission" in result.stderr
        assert (root / "ai-status.json").read_bytes() == before_status
        assert (root / "ai-activity-log.jsonl").read_bytes() == before_log


@pytest.mark.parametrize(
    ("source", "body"),
    [
        ("state.json", None),
        ("state.json", b""),
        ("event-queue.jsonl", b"{malformed\n"),
        ("approval-queue.json", b'{"schema_version":1,"requests":[],"requests":[]}\n'),
    ],
)
def test_runtime_admission_missing_empty_malformed_sources_fail_closed(
    source: str,
    body: bytes | None,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        source_path = root / ".orchestrator" / source
        if body is None:
            source_path.unlink()
        else:
            source_path.write_bytes(body)
        before_status = (root / "ai-status.json").read_bytes()
        result = run_dispatch(root, "--dry-run")
        assert result.returncode == 2
        assert "runtime admission blocked: runtime_source_invalid" in result.stderr
        assert (root / "ai-status.json").read_bytes() == before_status
        assert (root / "ai-activity-log.jsonl").read_bytes() == b""


def test_invalid_actor_is_rejected_before_first_status_commit() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        before = (root / "ai-status.json").read_bytes()
        result = run_dispatch(root, "--apply", extra_env={"AI_NAME": "Rogue"})
        assert result.returncode == 2
        assert "AI_NAME" in result.stderr
        assert (root / "ai-status.json").read_bytes() == before
        assert (root / "ai-activity-log.jsonl").read_text(encoding="utf-8") == ""


def test_dry_run_validates_but_does_not_flush_a_valid_pending_outbox() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            "--apply",
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()
        result = run_dispatch(root, "--dry-run")
        assert result.returncode == 0, result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


@pytest.mark.parametrize(
    ("audit_state", "expected_returncode"),
    [
        ("all_missing", 0),
        ("partial", 0),
        ("all_exact", 0),
        ("conflict", 2),
    ],
)
def test_dry_run_pending_outbox_preflight_is_zero_write(
    audit_state: str,
    expected_returncode: int,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            "--apply",
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        events = state["program_activity_outbox"]["events"]
        selected: list[dict]
        if audit_state == "all_missing":
            selected = []
        elif audit_state == "partial":
            selected = events[:17]
        elif audit_state == "all_exact":
            selected = events
        else:
            conflicting = deepcopy(events[0])
            conflicting["message"] += " conflicting-payload"
            selected = [conflicting]
        (root / "ai-activity-log.jsonl").write_text(
            "".join(
                json.dumps(event, ensure_ascii=False) + "\n" for event in selected
            ),
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root, "--dry-run", extra_env={"AI_NAME": "Codex2"})

        assert result.returncode == expected_returncode
        if audit_state == "conflict":
            assert "activity audit event_id payload binding mismatch" in result.stderr
        else:
            assert result.stderr == ""
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_dry_run_rejects_corrupt_pending_outbox_without_writes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            "--apply",
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        state["program_activity_outbox"]["created_at"] = "2000-01-01T00:00:00Z"
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()
        result = run_dispatch(root, "--dry-run")
        assert result.returncode == 2
        assert "outbox" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_legacy_v4_pending_outbox_requires_supervisor_signed_recovery() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        state["program_activity_outbox"]["schema_version"] = 4
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root)

        assert result.returncode == 2
        assert "schema 4 requires supervisor-signed recovery" in result.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_codex2_recovers_codex_outbox_after_assignment_reassignment() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        interrupted = run_dispatch(
            root,
            "--apply",
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        task = next(item for item in state["tasks"] if item.get("id") == "LOOP-PROD-CLOSE-002")
        task["owner"], task["reviewer"] = task["reviewer"], task["owner"]
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        recovered = run_dispatch(root, "--apply", extra_env={"AI_NAME": "Codex2"})
        assert recovered.returncode == 0, recovered.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        assert after["program_activity_outbox"] is None
        records = [
            json.loads(line)
            for line in (root / "ai-activity-log.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(records) == 49
        assert {record["agent"] for record in records} == {"Codex"}


@pytest.mark.parametrize("crash_after", [1, 24, 49])
def test_outbox_recovery_is_exactly_once_after_each_event_crash(crash_after: int) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(
            root,
            "--apply",
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_ACTIVITY_EVENT": str(crash_after)},
        )
        assert first.returncode == 2
        second = run_dispatch(root, "--apply")
        assert second.returncode == 0, second.stderr
        records = [
            json.loads(line)
            for line in (root / "ai-activity-log.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(records) == 49
        assert len({record["event_id"] for record in records}) == 49


def test_completion_overlay_tamper_and_consumption_schema_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root, "--apply")
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        state["program_completion_authorities"][
            "loop-product-level-remediation-2026-07-13"
        ]["roles"]["LOOP-PROD-CLOSE-001"] = "final_authority"
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        tampered = run_dispatch(root, "--apply")
        assert tampered.returncode == 2
        assert "completion overlay" in tampered.stderr

        state["program_completion_authorities"][
            "loop-product-level-remediation-2026-07-13"
        ] = json.loads((root / "ai-status.json").read_text(encoding="utf-8")).get(
            "program_completion_authorities", {}
        ).get("loop-product-level-remediation-2026-07-13", {})


def test_initial_dispatcher_rejects_signed_looking_checkpoint_consumption() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(root, "--apply")
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        catalog = load_catalog()
        program_id = catalog["program_id"]
        contract = catalog["completion_authority"]["checkpoint_consumption_record_contract"]
        record = {field: "a" * 64 for field in contract["required_binding_fields"]}
        record.update(
            {
                "program_id": program_id,
                "checkpoint_task_id": contract["checkpoint_task_id"],
                "guard_task_id": contract["guard_task_id"],
                "consumer_task_id": contract["consumer_task_id"],
                "actor_id": "ops-1",
                "actor_role": "human_ops",
                "signature_algorithm": "ed25519",
                "key_id": "ops-key-1",
                "policy_version": "v1",
                "consumed_at": "2026-07-14T00:00:00Z",
                "revocation_checked_at": "2026-07-14T00:00:00Z",
                "nonce": "nonce-1",
            }
        )
        state["program_completion_checkpoint_consumptions"] = {program_id: record}
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        before = (root / "ai-status.json").read_bytes()
        rejected = run_dispatch(root, "--apply")
        assert rejected.returncode == 2
        assert "protected LOOP-PROD-CLOSE-002 verifier" in rejected.stderr
        assert (root / "ai-status.json").read_bytes() == before

        state.pop("program_completion_checkpoint_consumptions")
        state["program_completed"] = True
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        before = (root / "ai-status.json").read_bytes()
        rejected = run_dispatch(root, "--apply")
        assert rejected.returncode == 2
        assert "protected LOOP-PROD-CLOSE-002 verifier" in rejected.stderr
        assert (root / "ai-status.json").read_bytes() == before


def test_completion_authority_and_auth_lifecycle_are_exact_contracts() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for section in ("completion_authority", "auth_lifecycle"):
            candidate = write_catalog(
                root,
                lambda payload, section=section: payload[section].update({"unbound": True}),
            )
            result = run_dispatch(root, "--validate-only", catalog=candidate)
            assert result.returncode == 2
            assert "exact reviewed contract" in result.stderr


def test_migration_rejects_an_unbound_live_field_without_writes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        target = next(item for item in state["tasks"] if item.get("id") == "LOOP-PROD-AGORA-002")
        target["unbound_live_field"] = True
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        before = (root / "ai-status.json").read_bytes()
        result = run_dispatch(root, "--apply")
        assert result.returncode == 2
        assert "historical live-task schema changed" in result.stderr
        assert (root / "ai-status.json").read_bytes() == before


def test_migration_preserves_allowed_reassignment_fields() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        target = next(item for item in state["tasks"] if item.get("id") == "LOOP-PROD-AGORA-002")
        target.update({"owner": "Codex2", "reviewer": "Codex", "next": "fleet reassigned"})
        before_created = target["created_at"]
        target["last_update"] = "2026-07-13T00:01:00Z"
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        result = run_dispatch(root, "--apply")
        assert result.returncode == 0, result.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        task = next(item for item in after["tasks"] if item.get("id") == "LOOP-PROD-AGORA-002")
        assert task["owner"] == "Codex2"
        assert task["reviewer"] == "Codex"
        assert task["next"] == "fleet reassigned"
        assert task["created_at"] == before_created


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "title",
        "summary_zh",
        "phase",
        "depends_on",
        "artifacts",
        "acceptance",
        "wave",
        "fleet_lane",
        "target_repo",
        "merge_target",
        "loop_ids",
        "current_maturity",
        "target_maturity",
        "desired_state_sources",
        "actual_state_sources",
        "proof_required",
        "non_goals",
        "dispatch_rules",
        "product_level_required",
        "evidence_root",
        "task_doc",
        "requires_human_ops_signoff",
    ],
)
def test_every_immutable_migration_contract_field_is_bound(field: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        target = next(item for item in state["tasks"] if item.get("id") == "LOOP-PROD-AGORA-002")
        value = target[field]
        if field == "id":
            target[field] = "FOREIGN-TASK"
        elif isinstance(value, list):
            target[field] = [*value, "__tampered__"]
        elif isinstance(value, bool):
            target[field] = not value
        elif isinstance(value, int):
            target[field] = value + 1
        else:
            target[field] = f"{value}-tampered"
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        before = (root / "ai-status.json").read_bytes()
        result = run_dispatch(root, "--apply")
        assert result.returncode == 2
        assert (root / "ai-status.json").read_bytes() == before


def test_sequencing_overlay_checks() -> None:
    # 1. 48/48 classification - correct overlay should pass
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        
        overlay_src = ROOT / "docs" / "bff" / "execution-tasks" / "2026-07-13-loop-product-level-remediation" / "sequencing-overlay-2026-07-16.json"
        overlay_dest = root / "sequencing-overlay-2026-07-16.json"
        overlay_dest.parent.mkdir(parents=True, exist_ok=True)
        overlay_dest.write_text(overlay_src.read_text(encoding="utf-8"), encoding="utf-8")
        
        result = run_dispatch(root, "--validate-only", "--sequencing-overlay", str(overlay_dest))
        assert result.returncode == 0, result.stderr

    # 2. Hash mismatch
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        
        overlay_src = ROOT / "docs" / "bff" / "execution-tasks" / "2026-07-13-loop-product-level-remediation" / "sequencing-overlay-2026-07-16.json"
        overlay_data = json.loads(overlay_src.read_text(encoding="utf-8"))
        overlay_data["source_hashes"]["tasks_catalog_sha256"] = "invalid_hash"
        
        overlay_dest = root / "sequencing-overlay-2026-07-16.json"
        overlay_dest.write_text(json.dumps(overlay_data, indent=2), encoding="utf-8")
        
        result = run_dispatch(root, "--validate-only", "--sequencing-overlay", str(overlay_dest))
        assert result.returncode == 2
        assert "tasks_catalog_sha256 hash mismatch" in result.stderr

    # 3. Missing / extra ID
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        
        overlay_src = ROOT / "docs" / "bff" / "execution-tasks" / "2026-07-13-loop-product-level-remediation" / "sequencing-overlay-2026-07-16.json"
        overlay_data = json.loads(overlay_src.read_text(encoding="utf-8"))
        del overlay_data["tasks"]["LOOP-PROD-000"]
        
        overlay_dest = root / "sequencing-overlay-2026-07-16.json"
        overlay_dest.write_text(json.dumps(overlay_data, indent=2), encoding="utf-8")
        
        result = run_dispatch(root, "--validate-only", "--sequencing-overlay", str(overlay_dest))
        assert result.returncode == 2
        assert "sequencing overlay is missing tasks" in result.stderr

    # 4. Cycle detection
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        state, _ = prepare_baseline_program(root)
        
        overlay_src = ROOT / "docs" / "bff" / "execution-tasks" / "2026-07-13-loop-product-level-remediation" / "sequencing-overlay-2026-07-16.json"
        overlay_data = json.loads(overlay_src.read_text(encoding="utf-8"))
        overlay_data["tasks"]["LOOP-PROD-000"]["depends_on"] = ["LOOP-PROD-001"]
        
        overlay_dest = root / "sequencing-overlay-2026-07-16.json"
        overlay_dest.write_text(json.dumps(overlay_data, indent=2), encoding="utf-8")
        
        result = run_dispatch(root, "--validate-only", "--sequencing-overlay", str(overlay_dest))
        assert result.returncode == 2
        assert "dependency cycle detected" in result.stderr


def test_g2_evidence_validation() -> None:
    # Dynamically import dispatcher
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dispatcher",
        str(ROOT / "scripts" / "dispatch_loop_product_level_remediation_2026-07-13.py")
    )
    dispatcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dispatcher)

    catalog = load_catalog()
    catalog["g2_evidence_contract"] = {
        "version": 1,
        "target_task": "LOOP-PROD-CLOSE-001",
        "tasks_catalog_sha256": "44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357",
        "sequencing_addendum_sha256": "9a3b735ac161b612e35a1d0e313cc7037da444f8b0311c623d27396a06d4b519",
        "merge_pr_3737_sha": "a4b5df9a51bc3da6df0d39d422d9db4edc553aba"
    }

    # 1. check_g2_evidence_valid: no evidence (no CLOSE-001 at all)
    state = {"tasks": []}
    assert dispatcher.check_g2_evidence_valid(state, catalog) is False

    # 2. check_g2_evidence_valid: CLOSE-001 done, but evidence.json missing
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        dispatcher.REPO_ROOT = temp_path
        dispatcher.ARCHIVE_ROOT = temp_path / "ai-task-archive" / "tasks"
        
        state = {
            "tasks": [
                {
                    "id": "LOOP-PROD-CLOSE-001",
                    "status": "done"
                }
            ]
        }
        assert dispatcher.check_g2_evidence_valid(state, catalog) is False

        # 3. check_g2_evidence_valid: invalid evidence schema (missing telemetry)
        evidence_dir = temp_path / "docs" / "deployment" / "evidence" / "loop-product-level" / "LOOP-PROD-CLOSE-001"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        
        bad_evidence = {
            "version": 1,
            "task_id": "LOOP-PROD-CLOSE-001",
            "program_id": "loop-product-level-remediation-2026-07-13",
            "catalog_sha256": "44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357",
            "addendum_sha256": "9a3b735ac161b612e35a1d0e313cc7037da444f8b0311c623d27396a06d4b519",
            "merge_pr_3737_sha": "a4b5df9a51bc3da6df0d39d422d9db4edc553aba",
            "issued_at": "2026-07-16T12:00:00Z",
            "paper_trade_chains": [
                {
                    "signal": {
                        "id": "sig-123",
                        "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
                    },
                    "order": {
                        "id": "ord-456",
                        "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                        "signal_id": "sig-123"
                    },
                    "fill": {
                        "id": "fil-789",
                        "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                        "order_id": "ord-456"
                    },
                    "loop_run_projection": {
                        "id": "proj-def",
                        "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                        "telemetry_id": "tel-abc"
                    }
                }
            ]
        }
        evidence_file = evidence_dir / "evidence.json"
        evidence_file.write_text(json.dumps(bad_evidence, indent=2), encoding="utf-8")
        assert dispatcher.check_g2_evidence_valid(state, catalog) is False

        # 3b. check_g2_evidence_valid: bare/fabricated string "sig1" instead of dict
        bad_evidence_bare = {
            "version": 1,
            "task_id": "LOOP-PROD-CLOSE-001",
            "program_id": "loop-product-level-remediation-2026-07-13",
            "catalog_sha256": "44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357",
            "addendum_sha256": "9a3b735ac161b612e35a1d0e313cc7037da444f8b0311c623d27396a06d4b519",
            "merge_pr_3737_sha": "a4b5df9a51bc3da6df0d39d422d9db4edc553aba",
            "issued_at": "2026-07-16T12:00:00Z",
            "paper_trade_chains": [
                {
                    "signal": "sig1",
                    "order": "ord1",
                    "fill": "fil1",
                    "telemetry": "tel1",
                    "loop_run_projection": "proj1"
                }
            ]
        }
        evidence_file.write_text(json.dumps(bad_evidence_bare, indent=2), encoding="utf-8")
        assert dispatcher.check_g2_evidence_valid(state, catalog) is False

        # 4. check_g2_evidence_valid: valid evidence
        good_evidence = {
            "version": 1,
            "task_id": "LOOP-PROD-CLOSE-001",
            "program_id": "loop-product-level-remediation-2026-07-13",
            "catalog_sha256": "44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357",
            "addendum_sha256": "9a3b735ac161b612e35a1d0e313cc7037da444f8b0311c623d27396a06d4b519",
            "merge_pr_3737_sha": "a4b5df9a51bc3da6df0d39d422d9db4edc553aba",
            "issued_at": "2026-07-16T12:00:00Z",
            "paper_trade_chains": [
                {
                    "signal": {
                        "id": "sig-123",
                        "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
                    },
                    "order": {
                        "id": "ord-456",
                        "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                        "signal_id": "sig-123"
                    },
                    "fill": {
                        "id": "fil-789",
                        "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                        "order_id": "ord-456"
                    },
                    "telemetry": {
                        "id": "tel-abc",
                        "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                        "fill_id": "fil-789"
                    },
                    "loop_run_projection": {
                        "id": "proj-def",
                        "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                        "telemetry_id": "tel-abc"
                    }
                }
            ]
        }
        evidence_file.write_text(json.dumps(good_evidence, indent=2), encoding="utf-8")
        assert dispatcher.check_g2_evidence_valid(state, catalog) is True

        # 5. check_g2_evidence_valid: CLOSE-001 from archive instead of active list
        state = {"tasks": []}
        assert dispatcher.check_g2_evidence_valid(state, catalog) is False
        
        archive_dir = temp_path / "ai-task-archive" / "tasks"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_payload = {
            "version": 1,
            "task_id": "LOOP-PROD-CLOSE-001",
            "archived_at": "2026-07-16T12:00:00Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {
                "id": "LOOP-PROD-CLOSE-001",
                "status": "done",
                "source_ref": {
                    "program_id": "loop-product-level-remediation-2026-07-13",
                    "catalog_sha256": "44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357",
                    "task_contract_sha256": "dummy"
                }
            },
            "handoffs": [],
            "blockers": []
        }
        (archive_dir / "LOOP-PROD-CLOSE-001.json").write_text(json.dumps(archive_payload, indent=2), encoding="utf-8")
        assert dispatcher.check_g2_evidence_valid(state, catalog) is True

        # 6. pre-G2 denial: wave >= 5 task fails closed when evidence is invalid
        evidence_file.write_text(json.dumps(bad_evidence, indent=2), encoding="utf-8")
        
        catalog["overlay_applied"] = True
        auth_task = next(t for t in catalog["tasks"] if t["id"] == "LOOP-PROD-AUTH-001")
        task_to_materialize = deepcopy(auth_task)
        task_to_materialize["wave"] = 5
        tasks_to_materialize = [task_to_materialize]
        
        # Patch check_g2_evidence_valid mock behavior by passing catalog or patching env
        os.environ["LOOP_PRODUCT_TASK_CATALOG"] = str(CATALOG)
        # Write temporary catalog with g2_evidence_contract to match
        temp_catalog_file = temp_path / "tasks.json"
        temp_catalog_file.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
        dispatcher.catalog_path = lambda: temp_catalog_file
        
        with pytest.raises(dispatcher.DispatchError, match="G2 paper-trade evidence not found or invalid"):
            dispatcher.materialize(
                state=state,
                tasks=tasks_to_materialize,
                catalog=catalog,
                catalog_digest="dummy",
                timestamp="2026-07-16T12:00:00Z"
            )

        # 7. post-G2 release: wave >= 5 task materializes successfully when evidence is valid
        evidence_file.write_text(json.dumps(good_evidence, indent=2), encoding="utf-8")
        created, preserved, archived, logs, changed = dispatcher.materialize(
            state=state,
            tasks=tasks_to_materialize,
            catalog=catalog,
            catalog_digest="dummy",
            timestamp="2026-07-16T12:00:00Z"
        )
        assert "LOOP-PROD-AUTH-001" in created
