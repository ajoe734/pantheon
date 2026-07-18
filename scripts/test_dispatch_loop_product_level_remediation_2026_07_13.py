from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
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
SEQUENCING_OVERLAY = CATALOG.with_name("sequencing-overlay-2026-07-16.json")
SEQUENCING_MATRIX = CATALOG.with_name(
    "SEQUENCING_EXECUTION_MATRIX_2026-07-16.md"
)
SEQUENCING_ADDENDUM = (
    ROOT
    / "docs"
    / "04"
    / "pantheon_loop_product_level_remediation_2026-07-13"
    / "REMEDIATION_SEQUENCING_ADDENDUM_2026-07-16.md"
)
PRODUCT_EVIDENCE_SCHEMA = ROOT / "schemas" / "product-evidence.schema.json"
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
    lock_path = Path(path).parent / ".orchestrator" / "activity-audit.lock"
    with _lock(
        lock_path,
        shared=shared,
        nonblocking=nonblocking,
        label="activity_audit",
    ):
        yield
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
        "LOOP_PRODUCT_TEST_BASE_CATALOG": "1",
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
        archive = root / "archive" / "logs" / "ai-activity-log.jsonl-2000.gz"
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
        assert "duplicate activity audit event_id" in recovered.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        assert after["program_activity_outbox"] is not None
        assert (root / "ai-activity-log.jsonl").read_text(encoding="utf-8") == body


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
            assert "duplicate activity audit event_id" in result.stderr
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
            if line.startswith("acquire:")
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


@pytest.mark.parametrize("malformed_outbox", [{}, [], "", 0])
def test_present_nonnull_malformed_program_outbox_fails_closed_without_writes(
    malformed_outbox: object,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        state["program_activity_outbox"] = malformed_outbox
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        result = run_dispatch(root, "--dry-run")

        assert result.returncode == 2
        assert "program_activity_outbox transaction schema is not exact" in result.stderr
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


def _load_dispatcher_module():
    spec = importlib.util.spec_from_file_location("loop_product_dispatcher", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_sequencing_gate_module():
    module_name = "loop_product_sequencing_gate_test"
    path = ROOT / ".orchestrator" / "sequencing_gate.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_json_artifact(path: Path, payload: object) -> str:
    raw = _json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _overlay_payload() -> dict:
    return json.loads(SEQUENCING_OVERLAY.read_text(encoding="utf-8"))


def _write_mutated_overlay(dispatcher, path: Path, payload: dict) -> Path:
    digest = _write_json_artifact(path, payload)
    dispatcher.EXPECTED_SEQUENCING_OVERLAY_SHA256 = digest
    dispatcher.catalog_path = lambda: CATALOG
    return path


def test_sequencing_overlay_v2_is_exact_and_partitions_all_48_tasks() -> None:
    dispatcher = _load_dispatcher_module()
    dispatcher.catalog_path = lambda: CATALOG
    catalog = load_catalog()

    assert hashlib.sha256(CATALOG.read_bytes()).hexdigest() == (
        "44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357"
    )
    assert hashlib.sha256(SEQUENCING_ADDENDUM.read_bytes()).hexdigest() == (
        "9a3b735ac161b612e35a1d0e313cc7037da444f8b0311c623d27396a06d4b519"
    )

    dispatcher.apply_sequencing_overlay(catalog, SEQUENCING_OVERLAY)

    entries = catalog["sequencing_entries"]
    assert catalog["overlay_applied"] is True
    assert len(entries) == 48
    assert set(entries) == {task["id"] for task in catalog["tasks"]}
    assert all(
        set(entry)
        == {
            "wave",
            "classification",
            "rationale",
            "original_depends_on",
            "amended_depends_on",
        }
        for entry in entries.values()
    )
    gated = {
        task_id
        for task_id, entry in entries.items()
        if entry["classification"]
        in dispatcher.GATED_SEQUENCING_CLASSIFICATIONS
    }
    assert set(catalog["release_gate"]["gated_task_ids"]) == gated
    assert Counter(
        entry["classification"] for entry in entries.values()
    ) == {
        "permitted before the paper-trade proof": 21,
        "part of the G2 proof path": 8,
        "deferred strict-auth/security/governance work": 11,
        "final verification/closeout after the appropriate gate": 8,
    }
    assert Counter(entry["wave"] for entry in entries.values()) == {
        0: 4,
        1: 16,
        2: 6,
        3: 3,
        5: 11,
        6: 4,
        7: 1,
        8: 1,
        9: 1,
        10: 1,
    }
    assert len(gated) == 19
    assert len(set(entries) - gated) == 29
    assert catalog["g2_evidence_contract"]["version"] == 4
    assert (
        catalog["g2_evidence_contract"]["target_task"]
        == "LOOP-PROD-VERIFY-EXEC-001"
    )


def test_sequencing_execution_matrix_matches_all_48_overlay_entries() -> None:
    overlay = _overlay_payload()
    matrix = SEQUENCING_MATRIX.read_text(encoding="utf-8")
    headings = list(
        re.finditer(r"^#### (LOOP-PROD-[^:]+):", matrix, flags=re.MULTILINE)
    )
    ids = [match.group(1) for match in headings]
    assert len(ids) == 48
    assert len(ids) == len(set(ids))
    assert set(ids) == set(overlay["tasks"])
    dispatcher = _load_dispatcher_module()
    dispatcher.catalog_path = lambda: CATALOG
    effective = load_catalog()
    dispatcher.apply_sequencing_overlay(effective, SEQUENCING_OVERLAY)
    effective_digest = dispatcher.canonical_json_sha256(effective)
    effective_graph_digest = dispatcher.canonical_json_sha256(
        dispatcher.catalog_graph_projection(effective, effective_digest)
    )
    raw_overlay_digest = hashlib.sha256(SEQUENCING_OVERLAY.read_bytes()).hexdigest()
    for digest in (
        raw_overlay_digest,
        effective_digest,
        effective_graph_digest,
    ):
        assert f"`{digest}`" in matrix
    classifications = Counter(
        entry["classification"] for entry in overlay["tasks"].values()
    )
    assert classifications == {
        "permitted before the paper-trade proof": 21,
        "part of the G2 proof path": 8,
        "deferred strict-auth/security/governance work": 11,
        "final verification/closeout after the appropriate gate": 8,
    }
    assert "keeps all 19 gated contracts absent" in matrix
    assert "materializes only the 29 ungated tasks" in matrix
    for row in overlay["g2_evidence_contract"]["verifier_source_files"]:
        assert f"`{row['path']}`=`{row['sha256']}`" in matrix

    for index, match in enumerate(headings):
        task_id = match.group(1)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(matrix)
        block = matrix[match.start():end]
        entry = overlay["tasks"][task_id]

        def rendered(dependencies: list[str]) -> str:
            return ", ".join(f"`{item}`" for item in dependencies) or "None"

        assert f"- **Wave**: {entry['wave']}" in block
        assert f"- **Classification**: {entry['classification']}" in block
        assert f"- **Rationale**: {entry['rationale']}" in block
        assert (
            f"- **Original Dependencies**: {rendered(entry['original_depends_on'])}"
            in block
        )
        assert (
            f"- **Amended Dependencies**: {rendered(entry['amended_depends_on'])}"
            in block
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_merge_sha",
        "missing_id",
        "extra_id",
        "extra_entry_key",
        "wave_inversion",
        "invalid_classification",
        "empty_rationale",
        "missing_amended_dependencies",
        "non_list_amended_dependencies",
        "wrong_catalog_source_hash",
        "wrong_addendum_source_hash",
        "wrong_verifier_source_hash",
    ],
)
def test_sequencing_overlay_v2_rejects_authority_set_and_schema_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    dispatcher = _load_dispatcher_module()
    catalog = load_catalog()
    original = deepcopy(catalog)
    payload = _overlay_payload()
    if mutation == "wrong_merge_sha":
        payload["source_hashes"]["merge_pr_3737_sha"] = "0" * 40
        payload["g2_evidence_contract"]["merge_pr_3737_sha"] = "0" * 40
    elif mutation == "missing_id":
        del payload["tasks"]["LOOP-PROD-000"]
    elif mutation == "extra_id":
        payload["tasks"]["LOOP-PROD-EXTRA-001"] = deepcopy(
            payload["tasks"]["LOOP-PROD-000"]
        )
    elif mutation == "wave_inversion":
        payload["tasks"]["LOOP-PROD-000"]["wave"] = 1
    elif mutation == "invalid_classification":
        payload["tasks"]["LOOP-PROD-000"]["classification"] = "unclassified"
    elif mutation == "empty_rationale":
        payload["tasks"]["LOOP-PROD-000"]["rationale"] = ""
    elif mutation == "missing_amended_dependencies":
        payload["tasks"]["LOOP-PROD-000"].pop("amended_depends_on")
    elif mutation == "non_list_amended_dependencies":
        payload["tasks"]["LOOP-PROD-000"]["amended_depends_on"] = "none"
    elif mutation == "wrong_catalog_source_hash":
        payload["source_hashes"]["tasks_catalog_sha256"] = "0" * 64
        payload["g2_evidence_contract"]["tasks_catalog_sha256"] = "0" * 64
    elif mutation == "wrong_addendum_source_hash":
        payload["source_hashes"]["sequencing_addendum_sha256"] = "0" * 64
        payload["g2_evidence_contract"]["sequencing_addendum_sha256"] = "0" * 64
    elif mutation == "wrong_verifier_source_hash":
        payload["g2_evidence_contract"]["verifier_source_files"][0][
            "sha256"
        ] = "0" * 64
    else:
        payload["tasks"]["LOOP-PROD-000"]["unbound"] = True
    overlay = _write_mutated_overlay(
        dispatcher, tmp_path / f"{mutation}.json", payload
    )

    with pytest.raises(dispatcher.DispatchError):
        dispatcher.apply_sequencing_overlay(catalog, overlay)

    assert catalog == original


def test_sequencing_overlay_rejects_raw_digest_mismatch_atomically(
    tmp_path: Path,
) -> None:
    dispatcher = _load_dispatcher_module()
    dispatcher.catalog_path = lambda: CATALOG
    catalog = load_catalog()
    original = deepcopy(catalog)
    payload = _overlay_payload()
    payload["tasks"]["LOOP-PROD-000"]["rationale"] += " tampered"
    overlay = tmp_path / "digest-mismatch.json"
    _write_json_artifact(overlay, payload)

    with pytest.raises(dispatcher.DispatchError, match="overlay digest mismatch"):
        dispatcher.apply_sequencing_overlay(catalog, overlay)

    assert catalog == original


def test_sequencing_overlay_v2_rejects_duplicate_task_id_json_key(
    tmp_path: Path,
) -> None:
    dispatcher = _load_dispatcher_module()
    dispatcher.catalog_path = lambda: CATALOG
    catalog = load_catalog()
    original = deepcopy(catalog)
    raw = SEQUENCING_OVERLAY.read_text(encoding="utf-8").replace(
        '"tasks": {',
        '"tasks": {\n    "LOOP-PROD-000": {},',
        1,
    )
    overlay = tmp_path / "duplicate-task-id.json"
    overlay.write_text(raw, encoding="utf-8")

    with pytest.raises(dispatcher.DispatchError, match="duplicate JSON key"):
        dispatcher.apply_sequencing_overlay(catalog, overlay)

    assert catalog == original


def test_sequencing_overlay_v2_rejects_dependency_cycle_atomically(
    tmp_path: Path,
) -> None:
    dispatcher = _load_dispatcher_module()
    catalog = load_catalog()
    original = deepcopy(catalog)
    payload = _overlay_payload()
    payload["tasks"]["LOOP-PROD-000"]["amended_depends_on"] = [
        "LOOP-PROD-001"
    ]
    overlay = _write_mutated_overlay(dispatcher, tmp_path / "cycle.json", payload)

    with pytest.raises(dispatcher.DispatchError, match="dependency cycle"):
        dispatcher.apply_sequencing_overlay(catalog, overlay)

    assert catalog == original


G2_PROJECTED_AT = "2026-07-15T00:02:00Z"
G2_CAPTURED_AT = "2026-07-15T00:02:05Z"
G2_PROBE_AT = "2026-07-15T00:02:10Z"
G2_EVIDENCE_CUT_AT = "2026-07-15T00:02:20Z"
G2_VALIDATED_AT = "2026-07-15T00:02:20Z"
G2_VERDICT_AT = "2026-07-15T00:02:30Z"
G2_MERGED_AT = "2026-07-15T00:02:40Z"
G2_ISSUED_AT = "2026-07-15T00:02:45Z"
G2_REVIEWED_AT = "2026-07-15T00:02:50Z"
G2_ARTIFACT_MERGED_AT = "2026-07-15T00:02:55Z"
G2_CLOSEOUT_AT = "2026-07-15T00:03:00Z"
G2_RELEASED_AT = "2026-07-15T00:03:01Z"
G2_EXPIRES_AT = "2026-07-16T00:02:45Z"
G2_NOW = datetime(2026, 7, 15, 0, 3, 1, tzinfo=timezone.utc)
G2_DEPLOYMENT_SHA = "d" * 40
G2_IMAGE_MANIFEST_DIGEST = "sha256:" + "9" * 64


def _g2_hosted_readback_payload(deployment_sha: str) -> dict:
    return {
        "pre_deploy": {"status": "pass"},
        "capture_time_hosted_readback": {
            "status": "pass",
            "observed_at": G2_PROBE_AT,
            "deployment_sha": deployment_sha,
            "runtime_commit_sha": deployment_sha,
            "runtime_source_commit_sha": deployment_sha,
            "image_manifest_digest": G2_IMAGE_MANIFEST_DIGEST,
        },
    }


def _g2_trusted_deployment_identity_payload(deployment_sha: str) -> dict:
    hosted_readback = _g2_hosted_readback_payload(deployment_sha)
    return {
        "schema_version": "pantheon.g2-deployment-identity.v1",
        "environment": "dev",
        "deployment_sha": deployment_sha,
        "runtime_commit_sha": deployment_sha,
        "runtime_source_commit_sha": deployment_sha,
        "image_manifest_digest": G2_IMAGE_MANIFEST_DIGEST,
        "hosted_readback_sha256": canonical_sha256(hosted_readback),
        "deployment_run_id": 5001,
        "deployment_job_id": 6001,
        "observed_at": G2_PROBE_AT,
    }


def _fixture_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _product_evidence_payload(
    *,
    contract: dict,
    closeout_task: dict,
    verdict: dict,
    implementation_base_sha: str,
    implementation_head_sha: str,
    implementation_merge_sha: str,
    deployment_sha: str,
    deployment_manifest_sha256: str,
) -> dict:
    task_id = contract["target_task"]
    review_file = contract["closeout_manifest_path"]
    sidecar = str(Path(review_file).with_name("evidence.sha256"))
    canonical_root_deploy = {
        "conclusion": "success",
        "deployment_sha": deployment_sha,
        "run_id": 5001,
        "job_id": 6001,
        "runtime_commit_sha": deployment_sha,
        "runtime_source_commit_sha": deployment_sha,
        "image_manifest_digest": G2_IMAGE_MANIFEST_DIGEST,
    }
    hosted_readback = _g2_hosted_readback_payload(deployment_sha)
    return {
        "schema_version": "loop_product_evidence.v1",
        "schema_status": {
            "formal_schema_owner": "LOOP-PROD-002",
            "formalization_trigger": "LOOP-PROD-002 product evidence schema",
            "note": "Canonical G2 execution-spine closeout evidence.",
            "status": "formalized",
        },
        "evidence_policy": {
            "checksum_file": sidecar,
            "missing_or_contradicted_proof_fails_closed": True,
            "mutation_rule": "Immutable closeout evidence with checksummed records.",
            "recording_mode": "append_only",
            "redacted": True,
            "self_hashing": False,
        },
        "task": {
            "base_branch": "dev",
            "evidence_cut_at": G2_EVIDENCE_CUT_AT,
            "evidence_cut_semantics": "Merged canonical paper lifecycle proof.",
            "id": task_id,
            "overall_admission": "review_approved_owner_closeout_ready",
            "owner": closeout_task["owner"],
            "phase": "Loop Product-Level Remediation / Wave 2",
            "product_level_required": True,
            "repository": "ajoe734/pantheon",
            "review_file": review_file,
            "reviewer": closeout_task["reviewer"],
            "target_environment": "dev",
            "target_maturity": "product-level",
            "task_branch": f"task/{task_id}",
            "title": "Target-dev Execution spine product verifier",
        },
        "authorities": {
            "actual_state": ["telemetry_events", "canonical lifecycle projector"],
            "desired_state": ["Scenario B canonical paper execution spine"],
            "task_packet": (
                "docs/bff/execution-tasks/2026-07-13-loop-product-level-"
                f"remediation/{task_id}.md"
            ),
        },
        "scope": {
            "authoritative_write_owner": closeout_task["owner"],
            "composes_with": ["LOOP-PROD-TEL-002"],
            "evidence_changed_files": [review_file, sidecar],
            "implementation_changed_files": [],
            "not_changing": "Live broker or live capital authority",
            "owned_layer": "Canonical G2 evidence and independent closeout",
        },
        "implementation_delivery": {
            "anchor_commits": [
                {
                    "sha": implementation_head_sha,
                    "subject": f"{task_id}: canonical G2 delivery",
                }
            ],
            "pull_request": {
                "number": 4001,
                "url": "https://github.com/ajoe734/pantheon/pull/4001",
                "head_sha": implementation_head_sha,
                "base": "dev",
                "merged_at": G2_MERGED_AT,
                "merge_sha": implementation_merge_sha,
            },
            "required_checks": [
                {
                    "workflow": name,
                    "event": "pull_request",
                    "conclusion": "success",
                }
                for name in (
                    "Commit trailers",
                    "Runtime mirror guard",
                    "Smoke acceptance",
                )
            ],
        },
        "validation": {
            "commands": [
                {"command": "canonical lifecycle hosted probe", "result": "pass"}
            ],
            "validated_at": G2_VALIDATED_AT,
            "validated_base_sha": implementation_base_sha,
            "validated_head_sha": implementation_head_sha,
        },
        "deployment": {
            "applicable": True,
            "environment": "dev",
            "public_bff_base_url": "https://pantheon-dev.invalid",
            "publish_cut": {
                "conclusion": "success",
                "deployment_sha": deployment_sha,
            },
            "canonical_root_deploy": canonical_root_deploy,
            "identity_admission": {
                "status": "accepted",
                "deployment_sha": deployment_sha,
                "runtime_commit_sha": deployment_sha,
                "runtime_source_commit_sha": deployment_sha,
                "image_manifest_digest": G2_IMAGE_MANIFEST_DIGEST,
                "deployment_manifest_sha256": deployment_manifest_sha256,
                "hosted_readback_sha256": canonical_sha256(hosted_readback),
            },
        },
        "hosted_readback": hosted_readback,
        "behavioral_proof": {
            "duplicate_safety": {"proof": ["canonical IDs"], "status": "pass"},
            "failure_and_degraded_behavior": {
                "proof": ["fail closed"],
                "status": "pass",
            },
            "request_receipt_downstream_correlation": {
                "proof": ["stable identity"],
                "status": "pass",
            },
            "restart_and_recovery": {
                "proof": ["projector restart recovery"],
                "status": "pass",
            },
            "rollback_or_compensation": {
                "proof": ["paper-only rollback"],
                "status": "pass",
            },
        },
        "security_and_safety": {
            "environment_boundary": {"status": "pass"},
            "hosted_frontend": {"status": "not_applicable"},
            "mfa": {"status": "not_applicable"},
            "no_live_capital": {"status": "pass"},
            "rbac": {"status": "pass"},
            "tenant_isolation": {"status": "pass"},
            "two_person_approval": {"status": "not_applicable"},
        },
        "acceptance": [
            {
                "evidence_refs": ["canonical lifecycle bundle"],
                "id": f"AC-G2-{index:02d}",
                "statement": statement,
                "status": "pass",
            }
            for index, statement in enumerate(closeout_task["acceptance"], start=1)
        ],
        "residual_risks": {
            "RISK-PAPER-ONLY": {
                "blocking_for_this_task": False,
                "containment": "No live capital or broker authority.",
                "description": "This proof is intentionally paper-only.",
                "expiry": "2026-08-31T00:00:00Z",
                "owner": closeout_task["owner"],
                "recheck_trigger": "Live-capital program",
                "severity": "low",
            }
        },
        "integrity": {
            "algorithm": "sha256",
            "checksum_coverage": [review_file],
            "companion_checksum_path": sidecar,
            "hosted_semantic_sha256": {},
            "manifest_path": review_file,
            "normalized_hosted_readback": {},
            "self_hash_omitted": True,
            "self_hash_reason": "Companion checksum avoids recursive self-hash.",
            "source_artifact_sha256_by_epoch": {},
        },
        "record_log": [verdict],
    }


@pytest.fixture
def g2_v2_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    from services.trade_journey import hosted_lifecycle_probe as probe
    from services.trade_journey.lifecycle_projector import LifecycleProjector
    from services.trade_journey.test_hosted_lifecycle_probe import (
        FakeSource,
        _natural_lifecycle_rows,
    )

    dispatcher = _load_dispatcher_module()
    production_g2_resolver = dispatcher._resolve_authoritative_g2_snapshot
    trusted_deployment_path = tmp_path / "g2-deployment-identity.json"
    dispatcher.catalog_path = lambda: CATALOG
    monkeypatch.setattr(dispatcher, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(dispatcher, "STATUS_ROOT", tmp_path)
    monkeypatch.setattr(dispatcher, "AUTHORITATIVE_STATUS_ROOT", tmp_path)
    monkeypatch.setattr(dispatcher, "STATUS_PATH", tmp_path / "ai-status.json")
    monkeypatch.setattr(
        dispatcher,
        "TRUSTED_G2_DEPLOYMENT_IDENTITY_ROOT",
        tmp_path,
    )
    monkeypatch.setattr(
        dispatcher,
        "TRUSTED_G2_DEPLOYMENT_IDENTITY_PATH",
        trusted_deployment_path,
    )
    monkeypatch.setattr(
        dispatcher,
        "TRUSTED_G2_DEPLOYMENT_IDENTITY_OWNER_UID",
        os.getuid(),
    )
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(tmp_path))
    catalog = load_catalog()
    dispatcher.apply_sequencing_overlay(catalog, SEQUENCING_OVERLAY)
    catalog_digest = dispatcher.canonical_json_sha256(catalog)
    contract = catalog["g2_evidence_contract"]
    task_spec = next(
        task for task in catalog["tasks"] if task["id"] == contract["target_task"]
    )
    closeout_task = dispatcher.build_task(
        task_spec, catalog, catalog_digest, G2_ISSUED_AT
    )
    closeout_task.update(
        {
            "status": "done",
            "terminal_outcome": "completed",
            "review_file": contract["closeout_manifest_path"],
            "review_notes_zh": ["Codex2 approved the canonical G2 evidence."],
            "last_update": G2_CLOSEOUT_AT,
            "next": "Finalize exact G2 evidence after approved review.",
            "delivery": {
                "commit": "0" * 40,
                "head_merged_to_target": True,
                "merge_target_branch": "dev",
                "merge_target_sha": "0" * 40,
                "push_status": "in_sync",
                "recorded_at": G2_CLOSEOUT_AT,
            },
        }
    )

    activity_log = tmp_path / "ai-activity-log.jsonl"
    activity_log.write_text("", encoding="utf-8")
    monkeypatch.setattr(dispatcher, "LOG_PATH", activity_log)
    archive_root = tmp_path / "ai-task-archive" / "tasks"
    archive_root.mkdir(parents=True)
    monkeypatch.setattr(dispatcher, "ARCHIVE_ROOT", archive_root)
    (tmp_path / "ai-status.json").write_text("{}\n", encoding="utf-8")
    schema_path = tmp_path / "schemas" / "product-evidence.schema.json"
    schema_path.parent.mkdir(parents=True)
    schema_path.write_bytes(PRODUCT_EVIDENCE_SCHEMA.read_bytes())
    workflow_path = tmp_path / ".github" / "workflows" / "branch-ci.yml"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_bytes(
        (ROOT / ".github" / "workflows" / "branch-ci.yml").read_bytes()
    )
    verifier_source_paths = [
        tmp_path / row["path"]
        for row in contract["verifier_source_files"]
    ]
    for source_path, row in zip(
        verifier_source_paths,
        contract["verifier_source_files"],
        strict=True,
    ):
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes((ROOT / row["path"]).read_bytes())

    _fixture_git(tmp_path, "init", "-b", "dev")
    _fixture_git(tmp_path, "config", "user.name", "G2 Fixture")
    _fixture_git(tmp_path, "config", "user.email", "g2-fixture@example.invalid")
    _fixture_git(
        tmp_path,
        "add",
        str(schema_path.relative_to(tmp_path)),
        str(activity_log.relative_to(tmp_path)),
        str(workflow_path.relative_to(tmp_path)),
        *(str(path.relative_to(tmp_path)) for path in verifier_source_paths),
    )
    _fixture_git(tmp_path, "commit", "-m", "fixture: canonical base")
    implementation_base_sha = _fixture_git(tmp_path, "rev-parse", "HEAD")
    implementation_branch = "task/LOOP-PROD-VERIFY-EXEC-001-implementation"
    _fixture_git(tmp_path, "checkout", "-b", implementation_branch)
    implementation_marker = tmp_path / "g2-implementation-marker.txt"
    implementation_marker.write_text(
        "canonical paper execution spine\n", encoding="utf-8"
    )
    _fixture_git(
        tmp_path,
        "add",
        str(implementation_marker.relative_to(tmp_path)),
    )
    _fixture_git(tmp_path, "commit", "-m", "fixture: G2 implementation")
    implementation_head_sha = _fixture_git(tmp_path, "rev-parse", "HEAD")
    _fixture_git(tmp_path, "checkout", "dev")
    _fixture_git(
        tmp_path,
        "merge",
        "--no-ff",
        implementation_branch,
        "-m",
        "fixture: merge G2 implementation",
    )
    implementation_merge_sha = _fixture_git(tmp_path, "rev-parse", "HEAD")
    _fixture_git(
        tmp_path,
        "checkout",
        "-b",
        "task/LOOP-PROD-VERIFY-EXEC-001-evidence",
    )

    rows = _natural_lifecycle_rows()
    projection_root = tmp_path / "projection"
    projector = LifecycleProjector(
        state_path=projection_root / "controller_state.json",
        bundle_root=projection_root,
        deployment_sha=implementation_merge_sha,
        clock=lambda: G2_PROJECTED_AT,
    )
    projector.project_records(rows, mode="live", source_high_watermark=len(rows))
    projection_root.chmod(0o755)
    current = projection_root / "current"
    runtime_generation = current.resolve()
    assert re.fullmatch(r"g[0-9]{12}-[0-9a-f]{12}", runtime_generation.name)
    assert runtime_generation.stat().st_mode & stat.S_IWUSR
    assert not runtime_generation.stat().st_mode & stat.S_IWOTH
    for filename in (
        "manifest.json",
        "trade_journey_events.json",
        "loop_runs.json",
    ):
        mode = (runtime_generation / filename).stat().st_mode
        assert mode & stat.S_IWUSR
        assert not mode & stat.S_IWOTH
    manifest = json.loads((current / "manifest.json").read_text(encoding="utf-8"))
    journeys = json.loads(
        (current / "trade_journey_events.json").read_text(encoding="utf-8")
    )
    loops = json.loads((current / "loop_runs.json").read_text(encoding="utf-8"))

    monkeypatch.setattr(probe, "_utc_now", lambda: G2_PROBE_AT)
    hosted = asyncio.run(
        probe.run_probe(
            source=FakeSource(len(rows), rows),
            projection_root=projection_root,
            expected_sha=implementation_merge_sha,
            timeout_seconds=1.0,
            poll_seconds=0.001,
        )
    )
    candidate = probe._complete_candidates(rows)[0]
    identity = candidate["identity"]
    bundle = {
        "schema_version": contract["record_bundle_schema"],
        "captured_at": G2_CAPTURED_AT,
        "source": {
            "store": "telemetry_events",
            "snapshot_isolation": "repeatable_read",
            "baseline_high_watermark": hosted["proof"]["source"][
                "baseline_high_watermark"
            ],
            "source_high_watermark": hosted["proof"]["source"][
                "source_high_watermark"
            ],
        },
        "rows": rows,
        "projection": {
            "manifest": manifest,
            "trade_journey_events": journeys,
            "loop_runs": loops,
        },
    }
    authoritative_snapshot = {
        "source_high_watermark": bundle["source"]["source_high_watermark"],
        "rows": deepcopy(rows),
        "projection": deepcopy(bundle["projection"]),
        "attestation": {
            "database": contract["canonical_database_name"],
            "role": contract["canonical_database_role"],
            "schema": contract["canonical_database_schema"],
            "table": contract["canonical_database_table"],
            "database_host": contract["canonical_database_host"],
            "database_port": contract["canonical_database_port"],
            "database_tls_mode": contract["canonical_database_tls_mode"],
            "database_server_address": "10.0.0.2",
            "database_tls_protocol": "TLSv1.3",
            "database_tls_cipher": "TLS_AES_256_GCM_SHA384",
            "projection_root": contract["canonical_projection_root"],
            "live_source_high_watermark": bundle["source"][
                "source_high_watermark"
            ],
            "captured_generation_name": hosted["proof"]["projection"][
                "generation_name"
            ],
            "current_generation_name": hosted["proof"]["projection"][
                "generation_name"
            ],
            "captured_projection_checkpoint": loops["controller"]["checkpoint"],
            "captured_projection_source_high_watermark": loops["controller"][
                "source_high_watermark"
            ],
            "current_projection_checkpoint": loops["controller"]["checkpoint"],
            "current_projection_source_high_watermark": loops["controller"][
                "source_high_watermark"
            ],
            "rows_sha256": dispatcher.canonical_json_sha256(rows),
            "projection_sha256": dispatcher.canonical_json_sha256(
                bundle["projection"]
            ),
            "current_projection_sha256": dispatcher.canonical_json_sha256(
                bundle["projection"]
            ),
        },
    }

    def resolve_authoritative_snapshot(
        _contract: dict,
        _identity: dict,
        _generation_name: str,
    ) -> dict:
        return deepcopy(authoritative_snapshot)

    monkeypatch.setattr(
        dispatcher,
        "_resolve_authoritative_g2_snapshot",
        resolve_authoritative_snapshot,
    )
    authoritative_remote_head = {"sha": ""}
    monkeypatch.setattr(
        dispatcher,
        "_resolve_authoritative_git_remote_ref",
        lambda _url, _ref: authoritative_remote_head["sha"],
    )
    def github_checks(head_sha: str) -> list[dict]:
        check_suite_id = int(head_sha[:12], 16)
        return [
            {
                "name": row["name"],
                "status": "completed",
                "conclusion": "success",
                "head_sha": head_sha,
                "app_id": row["app_id"],
                "check_suite_id": check_suite_id,
            }
            for row in contract["required_github_checks"]
        ]

    def github_workflow_runs(head_sha: str) -> list[dict]:
        check_suite_id = int(head_sha[:12], 16)
        return [
            {
                "id": check_suite_id + 1,
                "check_suite_id": check_suite_id,
                "event": "pull_request",
                "path": contract["required_github_workflow_path"],
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": "success",
            }
        ]

    required_checks_policy = {
        "strict": True,
        "checks": sorted(
            deepcopy(contract["required_github_checks"]),
            key=lambda row: (row["name"], str(row["app_id"])),
        ),
    }
    authoritative_github_pr = {
        "repository": contract["required_github_repository"],
        "number": 4001,
        "url": "https://github.com/ajoe734/pantheon/pull/4001",
        "state": "closed",
        "merged": True,
        "merged_at": G2_MERGED_AT,
        "base": "dev",
        "head_sha": implementation_head_sha,
        "merge_sha": implementation_merge_sha,
        "checks": github_checks(implementation_head_sha),
        "workflow_runs": github_workflow_runs(implementation_head_sha),
        "required_checks_policy": required_checks_policy,
    }
    authoritative_artifact_prs: dict[int, dict] = {}

    def resolve_authoritative_github_pr(_contract: dict, number: int) -> dict:
        if number == 4001:
            return deepcopy(authoritative_github_pr)
        if number in authoritative_artifact_prs:
            return deepcopy(authoritative_artifact_prs[number])
        raise AssertionError(f"unexpected fixture PR query: {number}")

    monkeypatch.setattr(
        dispatcher,
        "_resolve_authoritative_github_pr",
        resolve_authoritative_github_pr,
    )

    def repo_path(relative: str) -> Path:
        return tmp_path / relative

    bundle_path = repo_path(contract["canonical_record_bundle_path"])
    probe_path = repo_path(contract["hosted_probe_path"])
    product_path = repo_path(contract["closeout_manifest_path"])
    g2_path = repo_path(contract["evidence_path"])
    bundle_digest = _write_json_artifact(bundle_path, bundle)
    probe_digest = _write_json_artifact(probe_path, hosted)

    verdict = {
        "sequence": 1,
        "recorded_at": G2_VERDICT_AT,
        "kind": "reviewer_approval_verdict",
        "status": "approved",
        "actor": closeout_task["reviewer"],
        "reference": f"ai-status.json#{contract['target_task']}",
    }
    trusted_deployment_digest = _write_json_artifact(
        trusted_deployment_path,
        _g2_trusted_deployment_identity_payload(implementation_merge_sha),
    )
    trusted_deployment_path.chmod(0o600)
    product = _product_evidence_payload(
        contract=contract,
        closeout_task=closeout_task,
        verdict=verdict,
        implementation_base_sha=implementation_base_sha,
        implementation_head_sha=implementation_head_sha,
        implementation_merge_sha=implementation_merge_sha,
        deployment_sha=implementation_merge_sha,
        deployment_manifest_sha256=trusted_deployment_digest,
    )
    product_digest = _write_json_artifact(product_path, product)
    sidecar_path = product_path.with_name("evidence.sha256")
    sidecar_raw = f"{product_digest}  evidence.json\n".encode("utf-8")
    sidecar_path.write_bytes(sidecar_raw)

    rows_by_type = {row["event_type"]: row for row in rows}
    role_types = contract["record_event_types"]
    loop_record = loops["records"][identity["loop_run_id"]]
    evidence = {
        "schema_version": "pantheon.loop-prod-g2-evidence.v4",
        "task_id": contract["target_task"],
        "program_id": catalog["program_id"],
        "target_environment": contract["required_target_environment"],
        "issued_at": G2_ISSUED_AT,
        "expires_at": G2_EXPIRES_AT,
        "authority": {
            "tasks_catalog_sha256": catalog["source_hashes"][
                "tasks_catalog_sha256"
            ],
            "sequencing_addendum_sha256": catalog["source_hashes"][
                "sequencing_addendum_sha256"
            ],
            "merge_pr_3737_sha": catalog["source_hashes"][
                "merge_pr_3737_sha"
            ],
            "overlay_sha256": catalog["sequencing_overlay_sha256"],
            "target_task_original_contract_sha256": contract[
                "target_task_original_contract_sha256"
            ],
            "target_task_amended_contract_sha256": contract[
                "target_task_amended_contract_sha256"
            ],
        },
        "identity": identity,
        "record_bundle": {
            "path": contract["canonical_record_bundle_path"],
            "sha256": bundle_digest,
        },
        "hosted_probe": {
            "path": contract["hosted_probe_path"],
            "sha256": probe_digest,
        },
        "records": {
            role: {
                "event_id": rows_by_type[event_type]["event_id"],
                "event_type": event_type,
                "sha256": dispatcher.canonical_json_sha256(
                    rows_by_type[event_type]
                ),
            }
            for role, event_type in role_types.items()
        },
        "closeout_admission": {
            "review_file": contract["closeout_manifest_path"],
            "review_manifest_sha256": product_digest,
            "review_manifest_sidecar_sha256": hashlib.sha256(
                sidecar_raw
            ).hexdigest(),
            "task_snapshot_sha256": dispatcher.canonical_json_sha256(
                dispatcher._g2_closeout_task_projection(closeout_task)
            ),
            "reviewer": closeout_task["reviewer"],
            "review_verdict_sha256": dispatcher.canonical_json_sha256(verdict),
        },
    }
    evidence["records"]["loop_run_projection"] = {
        "id": loop_record["id"],
        "sha256": dispatcher.canonical_json_sha256(loop_record),
        "generation": loops["generation"],
        "last_canonical_event_id": rows[-1]["event_id"],
    }
    _write_json_artifact(g2_path, evidence)

    artifact_relatives = [
        str(path.relative_to(tmp_path))
        for path in (g2_path, bundle_path, probe_path, product_path, sidecar_path)
    ]
    artifact_commit_counter = 0

    def commit_artifacts() -> tuple[str, str]:
        nonlocal artifact_commit_counter
        artifact_commit_counter += 1
        branch = _fixture_git(tmp_path, "branch", "--show-current")
        if branch == "dev":
            branch = f"task/g2-evidence-update-{artifact_commit_counter}"
            _fixture_git(tmp_path, "checkout", "-b", branch)
        _fixture_git(tmp_path, "add", *artifact_relatives)
        _fixture_git(
            tmp_path,
            "commit",
            "-m",
            f"fixture: commit G2 artifacts {artifact_commit_counter}",
        )
        artifact_head_sha = _fixture_git(tmp_path, "rev-parse", "HEAD")
        _fixture_git(tmp_path, "checkout", "dev")
        _fixture_git(
            tmp_path,
            "merge",
            "--no-ff",
            branch,
            "-m",
            f"fixture: merge G2 artifacts {artifact_commit_counter}",
        )
        merge_target_sha = _fixture_git(tmp_path, "rev-parse", "HEAD")
        artifact_pr_number = 5000 + artifact_commit_counter
        authoritative_artifact_prs[artifact_pr_number] = {
            "repository": contract["required_github_repository"],
            "number": artifact_pr_number,
            "url": (
                "https://github.com/ajoe734/pantheon/pull/"
                + str(artifact_pr_number)
            ),
            "state": "closed",
            "merged": True,
            "merged_at": G2_ARTIFACT_MERGED_AT,
            "base": "dev",
            "head_sha": artifact_head_sha,
            "merge_sha": merge_target_sha,
            "checks": github_checks(artifact_head_sha),
            "workflow_runs": github_workflow_runs(artifact_head_sha),
            "required_checks_policy": deepcopy(required_checks_policy),
        }
        _fixture_git(
            tmp_path,
            "update-ref",
            "refs/remotes/origin/dev",
            merge_target_sha,
        )
        closeout_task["delivery"].update(
            {
                "commit": merge_target_sha,
                "merge_target_sha": merge_target_sha,
            }
        )
        authoritative_remote_head["sha"] = merge_target_sha
        review_binding = {
            "schema_version": contract["review_binding_schema"],
            "reviewer": closeout_task["reviewer"],
            "reviewed_at": G2_REVIEWED_AT,
            "artifact_commit_sha": artifact_head_sha,
            "artifact_sha256": {
                "g2_evidence_sha256": hashlib.sha256(
                    g2_path.read_bytes()
                ).hexdigest(),
                "canonical_record_bundle_sha256": hashlib.sha256(
                    bundle_path.read_bytes()
                ).hexdigest(),
                "hosted_probe_sha256": hashlib.sha256(
                    probe_path.read_bytes()
                ).hexdigest(),
                "product_manifest_sha256": hashlib.sha256(
                    product_path.read_bytes()
                ).hexdigest(),
                "product_manifest_sidecar_sha256": hashlib.sha256(
                    sidecar_path.read_bytes()
                ).hexdigest(),
            },
            "implementation_pr": {
                "number": 4001,
                "head_sha": implementation_head_sha,
                "merge_sha": implementation_merge_sha,
            },
            "artifact_pr": {
                "number": artifact_pr_number,
                "head_sha": artifact_head_sha,
            },
        }
        closeout_task["review_binding"] = review_binding
        review_event = {
            "ts": G2_REVIEWED_AT,
            "agent": closeout_task["reviewer"],
            "type": "review_approved",
            "task_id": closeout_task["id"],
            "message": "Approved exact G2 artifact head and digests.",
            "review_binding": review_binding,
        }
        review_event["event_id"] = (
            "loop-product-event-"
            + dispatcher.canonical_json_sha256(review_event)
        )
        done_event = {
            "ts": G2_CLOSEOUT_AT,
            "agent": closeout_task["owner"],
            "type": "done",
            "task_id": closeout_task["id"],
            "message": closeout_task["next"],
            "delivery": deepcopy(closeout_task["delivery"]),
        }
        done_event["event_id"] = (
            "ai-status-event-"
            + dispatcher.canonical_json_sha256(done_event)
        )
        activity_log.write_text(
            "\n".join(
                json.dumps(event, ensure_ascii=False)
                for event in (review_event, done_event)
            )
            + "\n",
            encoding="utf-8",
        )
        return artifact_head_sha, merge_target_sha

    artifact_head_sha, artifact_merge_target_sha = commit_artifacts()
    return {
        "dispatcher": dispatcher,
        "catalog": catalog,
        "catalog_digest": catalog_digest,
        "contract": contract,
        "state": {"tasks": [closeout_task]},
        "closeout_task": closeout_task,
        "authoritative_snapshot": authoritative_snapshot,
        "authoritative_remote_head": authoritative_remote_head,
        "authoritative_github_pr": authoritative_github_pr,
        "authoritative_artifact_prs": authoritative_artifact_prs,
        "commit_artifacts": commit_artifacts,
        "artifact_head_sha": artifact_head_sha,
        "artifact_merge_target_sha": artifact_merge_target_sha,
        "implementation_head_sha": implementation_head_sha,
        "implementation_merge_sha": implementation_merge_sha,
        "production_g2_resolver": production_g2_resolver,
        "projection_root": projection_root,
        "identity": identity,
        "now": G2_NOW,
        "paths": {
            "g2": g2_path,
            "bundle": bundle_path,
            "probe": probe_path,
            "product": product_path,
            "sidecar": sidecar_path,
            "trusted_deployment": trusted_deployment_path,
            "archive": archive_root / f"{contract['target_task']}.json",
        },
    }


def _read_artifact(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rewrite_g2(fixture: dict, evidence: dict) -> None:
    _write_json_artifact(fixture["paths"]["g2"], evidence)


def _rewrite_bundle(fixture: dict, bundle: dict) -> None:
    digest = _write_json_artifact(fixture["paths"]["bundle"], bundle)
    evidence = _read_artifact(fixture["paths"]["g2"])
    evidence["record_bundle"]["sha256"] = digest
    _rewrite_g2(fixture, evidence)


def _rewrite_product(fixture: dict, product: dict) -> None:
    digest = _write_json_artifact(fixture["paths"]["product"], product)
    sidecar_raw = f"{digest}  evidence.json\n".encode("utf-8")
    fixture["paths"]["sidecar"].write_bytes(sidecar_raw)
    evidence = _read_artifact(fixture["paths"]["g2"])
    admission = evidence["closeout_admission"]
    admission["review_manifest_sha256"] = digest
    admission["review_manifest_sidecar_sha256"] = hashlib.sha256(
        sidecar_raw
    ).hexdigest()
    reviewer = fixture["closeout_task"]["reviewer"]
    admitted_verdict = next(
        row
        for row in product["record_log"]
        if row.get("actor") == reviewer
        and row.get("kind") == "reviewer_approval_verdict"
    )
    admission["review_verdict_sha256"] = fixture[
        "dispatcher"
    ].canonical_json_sha256(admitted_verdict)
    _rewrite_g2(fixture, evidence)


def _archive_closeout(fixture: dict, *, outcome: str = "completed") -> None:
    task = fixture["state"]["tasks"].pop()
    _write_json_artifact(
        fixture["paths"]["archive"],
        {
            "version": 1,
            "task_id": task["id"],
            "archived_at": G2_CLOSEOUT_AT,
            "terminal_status": "done",
            "terminal_outcome": outcome,
            "task": task,
            "handoffs": [],
            "blockers": [],
        },
    )


@pytest.mark.parametrize("closeout_source", ["active", "archive"])
def test_g2_v2_accepts_real_canonical_projector_probe_and_closeout(
    g2_v2_fixture: dict,
    closeout_source: str,
) -> None:
    if closeout_source == "archive":
        _archive_closeout(g2_v2_fixture)
    dispatcher = g2_v2_fixture["dispatcher"]

    dispatcher._validate_g2_evidence(
        g2_v2_fixture["state"],
        g2_v2_fixture["catalog"],
        now=g2_v2_fixture["now"],
    )


def test_g2_v3_rejects_bundle_not_resolved_by_authoritative_source(
    g2_v2_fixture: dict,
) -> None:
    snapshot = g2_v2_fixture["authoritative_snapshot"]
    snapshot["rows"][0]["payload"]["signal_id"] = "canonical-source-drift"
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="does not resolve against authoritative stores",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_production_resolver_fails_closed_without_source_configuration(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = g2_v2_fixture["contract"]
    monkeypatch.delenv(contract["canonical_telemetry_dsn_env"], raising=False)
    monkeypatch.delenv(contract["canonical_projection_root_env"], raising=False)
    generation_name = _read_artifact(g2_v2_fixture["paths"]["probe"])[
        "proof"
    ]["projection"]["generation_name"]

    with pytest.raises(
        g2_v2_fixture["dispatcher"].DispatchError,
        match="source configuration is missing",
    ):
        g2_v2_fixture["production_g2_resolver"](
            contract,
            g2_v2_fixture["identity"],
            generation_name,
        )


def test_g2_v4_production_resolver_reads_pinned_canonical_generation(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    contract = deepcopy(g2_v2_fixture["contract"])
    projection_root = g2_v2_fixture["projection_root"].resolve()
    contract["canonical_projection_root"] = str(projection_root)
    monkeypatch.setenv(contract["canonical_telemetry_dsn_env"], "fixture-dsn")
    monkeypatch.setenv(
        contract["canonical_projection_root_env"], str(projection_root)
    )
    bundle = _read_artifact(g2_v2_fixture["paths"]["bundle"])
    query_count = 0

    async def query_rows(
        _dsn: str,
        _contract: dict,
        identity: dict,
        event_types: list[str],
    ):
        nonlocal query_count
        query_count += 1
        assert identity == g2_v2_fixture["identity"]
        assert event_types == list(dispatcher.G2_CANONICAL_QUERY_EVENT_TYPES)
        return (
            {
                "database": contract["canonical_database_name"],
                "role": contract["canonical_database_role"],
                "schema": contract["canonical_database_schema"],
                "table": contract["canonical_database_table"],
                "database_host": contract["canonical_database_host"],
                "database_port": contract["canonical_database_port"],
                "database_tls_mode": contract["canonical_database_tls_mode"],
                "database_server_address": "10.0.0.2",
                "database_tls_protocol": "TLSv1.3",
                "database_tls_cipher": "TLS_AES_256_GCM_SHA384",
            },
            bundle["source"]["source_high_watermark"],
            deepcopy(bundle["rows"]),
        )

    monkeypatch.setattr(dispatcher, "_query_authoritative_g2_rows", query_rows)
    generation_name = _read_artifact(g2_v2_fixture["paths"]["probe"])[
        "proof"
    ]["projection"]["generation_name"]
    snapshot = g2_v2_fixture["production_g2_resolver"](
        contract,
        g2_v2_fixture["identity"],
        generation_name,
    )

    assert snapshot["rows"] == bundle["rows"]
    assert snapshot["projection"] == bundle["projection"]
    assert snapshot["attestation"]["captured_generation_name"] == generation_name
    assert snapshot["attestation"]["projection_root"] == str(projection_root)
    assert query_count == 2


def test_g2_v4_rejects_symlinked_projection_generations_root(
    g2_v2_fixture: dict,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    projection_root = g2_v2_fixture["projection_root"].resolve()
    generations = projection_root / "generations"
    external = projection_root.parent / "external-projection-generations"
    generations.rename(external)
    generations.symlink_to(external, target_is_directory=True)
    generation_name = _read_artifact(g2_v2_fixture["paths"]["probe"])[
        "proof"
    ]["projection"]["generation_name"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="generations root is not canonical",
    ):
        dispatcher._read_g2_projection_generation(
            projection_root,
            generation_name,
            label="G2 captured canonical projection",
        )


@pytest.mark.parametrize(
    "relative_path",
    [
        None,
        "manifest.json",
        "trade_journey_events.json",
        "loop_runs.json",
    ],
)
def test_g2_v4_rejects_world_writable_canonical_generation_paths(
    g2_v2_fixture: dict,
    relative_path: str | None,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    projection_root = g2_v2_fixture["projection_root"].resolve()
    generation_name = _read_artifact(g2_v2_fixture["paths"]["probe"])[
        "proof"
    ]["projection"]["generation_name"]
    generation = projection_root / "generations" / generation_name
    target = generation if relative_path is None else generation / relative_path
    original_mode = stat.S_IMODE(target.stat().st_mode)
    target.chmod(original_mode | stat.S_IWOTH)
    try:
        with pytest.raises(dispatcher.DispatchError, match="not canonical"):
            dispatcher._read_g2_projection_generation(
                projection_root,
                generation_name,
                label="G2 captured canonical projection",
            )
    finally:
        target.chmod(original_mode)


def test_g2_v4_rejects_divergent_current_target_projection(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.trade_journey.lifecycle_projector import AtomicProjectionBundle

    dispatcher = g2_v2_fixture["dispatcher"]
    contract = deepcopy(g2_v2_fixture["contract"])
    projection_root = g2_v2_fixture["projection_root"].resolve()
    contract["canonical_projection_root"] = str(projection_root)
    monkeypatch.setenv(contract["canonical_telemetry_dsn_env"], "fixture-dsn")
    monkeypatch.setenv(
        contract["canonical_projection_root_env"], str(projection_root)
    )
    bundle = _read_artifact(g2_v2_fixture["paths"]["bundle"])
    source_identity = {
        key: value
        for key, value in g2_v2_fixture["authoritative_snapshot"][
            "attestation"
        ].items()
        if key
        in {
            "database",
            "role",
            "schema",
            "table",
            "database_host",
            "database_port",
            "database_tls_mode",
            "database_server_address",
            "database_tls_protocol",
            "database_tls_cipher",
        }
    }

    async def query_rows(_dsn, _contract, _identity, _event_types):
        return (
            source_identity,
            bundle["source"]["source_high_watermark"],
            deepcopy(bundle["rows"]),
        )

    monkeypatch.setattr(dispatcher, "_query_authoritative_g2_rows", query_rows)
    captured_generation_name = _read_artifact(g2_v2_fixture["paths"]["probe"])[
        "proof"
    ]["projection"]["generation_name"]
    journeys = deepcopy(bundle["projection"]["trade_journey_events"])
    loops = deepcopy(bundle["projection"]["loop_runs"])
    new_generation = int(journeys["generation"]) + 1
    journeys["generation"] = new_generation
    loops["generation"] = new_generation
    journeys["controller"]["generation"] = new_generation
    loops["controller"]["generation"] = new_generation
    loop_record = next(iter(loops["records"].values()))
    loop_record["controller_generation"] = new_generation
    journeys["events"] = journeys["events"][:-1]
    AtomicProjectionBundle(projection_root).publish(
        new_generation,
        journeys,
        loops,
    )

    with pytest.raises(
        dispatcher.DispatchError,
        match="current target chain mismatch",
    ):
        g2_v2_fixture["production_g2_resolver"](
            contract,
            g2_v2_fixture["identity"],
            captured_generation_name,
        )


def test_g2_v4_canonical_query_is_read_only_qualified_and_identity_scoped(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    from types import SimpleNamespace

    dispatcher = g2_v2_fixture["dispatcher"]
    bundle = _read_artifact(g2_v2_fixture["paths"]["bundle"])
    observed: dict[str, object] = {}

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Connection:
        def transaction(self, *, isolation: str, readonly: bool):
            observed["transaction"] = (isolation, readonly)
            return Transaction()

        async def fetchval(self, sql: str):
            if sql == "SHOW transaction_isolation":
                return "repeatable read"
            if sql == "SHOW transaction_read_only":
                return "on"
            assert "public.telemetry_events" in sql
            return bundle["source"]["source_high_watermark"]

        async def fetchrow(self, sql: str):
            assert "to_regclass('public.telemetry_events')" in sql
            return {
                "database": "pantheon",
                "role": "pantheon_app",
                "schema": "public",
                "table_name": "telemetry_events",
                "table_kind": "r",
                "server_address": "10.0.0.2",
                "server_port": 5432,
                "tls_enabled": True,
                "tls_protocol": "TLSv1.3",
                "tls_cipher": "TLS_AES_256_GCM_SHA384",
            }

        async def fetch(self, sql: str, *args):
            observed["query"] = sql
            observed["args"] = args
            return deepcopy(bundle["rows"])

        async def close(self):
            observed["closed"] = True

    async def connect(dsn: str, **kwargs):
        observed["dsn"] = dsn
        observed["connect_kwargs"] = kwargs
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(connect=connect))
    source_identity, high, rows = asyncio.run(
        dispatcher._query_authoritative_g2_rows(
            (
                "postgresql://pantheon_app:secret@postgres:5432/"
                "pantheon?sslmode=verify-full"
            ),
            g2_v2_fixture["contract"],
            g2_v2_fixture["identity"],
            [row["event_type"] for row in bundle["rows"]],
        )
    )

    assert observed["transaction"] == ("repeatable_read", True)
    assert observed["dsn"] == (
        "postgresql://pantheon_app:secret@postgres:5432/"
        "pantheon?sslmode=verify-full"
    )
    assert set(observed["connect_kwargs"]) == {
        "ssl",
        "timeout",
        "command_timeout",
        "server_settings",
    }
    assert "FROM public.telemetry_events" in str(observed["query"])
    assert "event_id = ANY" not in str(observed["query"])
    query = str(observed["query"])
    for field in ("run_id", "signal_id"):
        assert f"payload -> '{field}' IN" in query
        assert "'[]'::jsonb, '{}'::jsonb" in query
        assert f"jsonb_typeof(payload #> '{{metadata,{field}}}') = 'string'" in query
        assert f"payload #>> '{{metadata,{field}}}' <> ''" in query
    assert "payload #> '{correlation_envelope,trace_id}' IN" in query
    assert "jsonb_typeof(payload -> 'trace_id') = 'string'" in query
    assert "THEN payload #>> '{correlation_envelope,trace_id}' END = $17" in query
    for field in (
        "loop_run_id",
        "strategy_id",
        "runtime_id",
        "binding_id",
        "capital_pool_id",
        "persona_id",
        "persona_capital_binding_id",
        "artifact_id",
        "artifact_version",
        "plan_id",
    ):
        assert field in query
    assert observed["args"][1:] == tuple(
        g2_v2_fixture["identity"][field]
        for field in g2_v2_fixture["dispatcher"].G2_STABLE_IDENTITY_FIELDS
    )
    assert source_identity == {
        "database": "pantheon",
        "role": "pantheon_app",
        "schema": "public",
        "table": "telemetry_events",
        "database_host": "postgres",
        "database_port": 5432,
        "database_tls_mode": "verify-full",
        "database_server_address": "10.0.0.2",
        "database_tls_protocol": "TLSv1.3",
        "database_tls_cipher": "TLS_AES_256_GCM_SHA384",
    }
    assert high == bundle["source"]["source_high_watermark"]
    assert rows == bundle["rows"]
    assert observed["closed"] is True


def test_g2_v4_rejects_database_watermark_change_during_projection_read(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    contract = deepcopy(g2_v2_fixture["contract"])
    projection_root = g2_v2_fixture["projection_root"].resolve()
    contract["canonical_projection_root"] = str(projection_root)
    monkeypatch.setenv(contract["canonical_telemetry_dsn_env"], "fixture-dsn")
    monkeypatch.setenv(
        contract["canonical_projection_root_env"],
        str(projection_root),
    )
    bundle = _read_artifact(g2_v2_fixture["paths"]["bundle"])
    source_identity = {
        key: value
        for key, value in g2_v2_fixture["authoritative_snapshot"][
            "attestation"
        ].items()
        if key
        in {
            "database",
            "role",
            "schema",
            "table",
            "database_host",
            "database_port",
            "database_tls_mode",
            "database_server_address",
            "database_tls_protocol",
            "database_tls_cipher",
        }
    }
    query_count = 0

    async def query_rows(_dsn, _contract, _identity, _event_types):
        nonlocal query_count
        query_count += 1
        high_watermark = bundle["source"]["source_high_watermark"]
        if query_count == 2:
            high_watermark += 1
        return source_identity, high_watermark, deepcopy(bundle["rows"])

    monkeypatch.setattr(dispatcher, "_query_authoritative_g2_rows", query_rows)
    generation_name = _read_artifact(g2_v2_fixture["paths"]["probe"])[
        "proof"
    ]["projection"]["generation_name"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="changed while projection truth was resolved",
    ):
        g2_v2_fixture["production_g2_resolver"](
            contract,
            g2_v2_fixture["identity"],
            generation_name,
        )


def test_g2_v3_rejects_uncommitted_admitted_artifact_bytes(
    g2_v2_fixture: dict,
) -> None:
    evidence_path = g2_v2_fixture["paths"]["g2"]
    evidence_path.write_bytes(evidence_path.read_bytes() + b"\n")
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="admitted artifact is not the committed blob",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_ignores_forged_local_remote_tracking_ref(
    g2_v2_fixture: dict,
) -> None:
    repo = g2_v2_fixture["dispatcher"].REPO_ROOT
    _fixture_git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD^")
    dispatcher = g2_v2_fixture["dispatcher"]

    dispatcher._validate_g2_evidence(
        g2_v2_fixture["state"],
        g2_v2_fixture["catalog"],
        now=g2_v2_fixture["now"],
    )


def test_g2_v4_rejects_delivery_absent_from_authoritative_remote(
    g2_v2_fixture: dict,
) -> None:
    g2_v2_fixture["authoritative_remote_head"]["sha"] = g2_v2_fixture[
        "implementation_merge_sha"
    ]
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="repository git verification failed",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_ignores_caller_git_directory_override(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GIT_DIR",
        str(g2_v2_fixture["dispatcher"].REPO_ROOT / "attacker-git-dir"),
    )
    dispatcher = g2_v2_fixture["dispatcher"]

    dispatcher._validate_g2_evidence(
        g2_v2_fixture["state"],
        g2_v2_fixture["catalog"],
        now=g2_v2_fixture["now"],
    )


def test_g2_v4_rejects_git_replace_object_forgery(
    g2_v2_fixture: dict,
) -> None:
    repo = g2_v2_fixture["dispatcher"].REPO_ROOT
    _fixture_git(
        repo,
        "replace",
        g2_v2_fixture["artifact_merge_target_sha"],
        g2_v2_fixture["implementation_merge_sha"],
    )
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="Git repository trust policy",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_self_attested_github_pr_truth(
    g2_v2_fixture: dict,
) -> None:
    g2_v2_fixture["authoritative_github_pr"]["head_sha"] = "0" * 40
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="does not resolve against GitHub truth",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_required_check_from_push_workflow(
    g2_v2_fixture: dict,
) -> None:
    g2_v2_fixture["authoritative_github_pr"]["workflow_runs"][0][
        "event"
    ] = "push"
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="does not resolve against GitHub truth",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_artifact_pr_merge_before_review(
    g2_v2_fixture: dict,
) -> None:
    artifact_pr_number = g2_v2_fixture["closeout_task"]["review_binding"][
        "artifact_pr"
    ]["number"]
    g2_v2_fixture["authoritative_artifact_prs"][artifact_pr_number][
        "merged_at"
    ] = G2_MERGED_AT
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="artifact PR does not resolve against GitHub truth",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_wrong_implementation_merge_base(
    g2_v2_fixture: dict,
) -> None:
    product = _read_artifact(g2_v2_fixture["paths"]["product"])
    product["validation"]["validated_base_sha"] = g2_v2_fixture[
        "implementation_merge_sha"
    ]
    _rewrite_product(g2_v2_fixture, product)
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="implementation merge does not contain its exact head",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_reviewer_binding_not_bound_to_artifact_head(
    g2_v2_fixture: dict,
) -> None:
    g2_v2_fixture["closeout_task"]["review_binding"][
        "artifact_commit_sha"
    ] = g2_v2_fixture["implementation_head_sha"]
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="artifact commit binding policy is invalid",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_missing_governed_reviewer_approval_event(
    g2_v2_fixture: dict,
) -> None:
    g2_v2_fixture["dispatcher"].LOG_PATH.write_text("", encoding="utf-8")
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="reviewer approval audit is not exact",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_missing_governed_owner_done_event(
    g2_v2_fixture: dict,
) -> None:
    activity_log = g2_v2_fixture["dispatcher"].LOG_PATH
    records = activity_log.read_text(encoding="utf-8").splitlines()
    activity_log.write_text(records[0] + "\n", encoding="utf-8")
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="owner closeout audit is not exact",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


@pytest.mark.parametrize(
    "outbox_key",
    ["status_archive_outbox", "status_activity_outbox"],
)
def test_g2_v4_rejects_pending_closeout_status_transaction(
    g2_v2_fixture: dict,
    outbox_key: str,
) -> None:
    g2_v2_fixture["state"][outbox_key] = {"pending": True}
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="closeout has a pending status transaction",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_closeout_without_recorded_delivery_timestamp(
    g2_v2_fixture: dict,
) -> None:
    g2_v2_fixture["closeout_task"]["delivery"].pop("recorded_at")
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="closeout delivery truth is not accepted",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_drifted_verifier_source_bytes(
    g2_v2_fixture: dict,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    source = (
        dispatcher.REPO_ROOT
        / g2_v2_fixture["contract"]["verifier_source_files"][0]["path"]
    )
    source.write_bytes(source.read_bytes() + b"\n# drift\n")

    with pytest.raises(
        dispatcher.DispatchError,
        match="verifier source bytes drifted",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_uses_pinned_source_bytes_not_preloaded_modules(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.trade_journey import hosted_lifecycle_probe
    from services.trade_journey import lifecycle_projector

    monkeypatch.setattr(hosted_lifecycle_probe, "_complete_candidates", lambda rows: [])
    monkeypatch.setattr(lifecycle_projector, "_fingerprint", lambda value: "0" * 64)
    admission = g2_v2_fixture["dispatcher"]._validate_g2_evidence(
        g2_v2_fixture["state"],
        g2_v2_fixture["catalog"],
        now=g2_v2_fixture["now"],
    )

    assert admission["g2_evidence_sha256"] == hashlib.sha256(
        g2_v2_fixture["paths"]["g2"].read_bytes()
    ).hexdigest()


def test_g2_v4_rejects_authoritative_remote_transitive_source_drift(
    g2_v2_fixture: dict,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    relative = "services/control-plane/specs/trade_journey/correlation_envelope.py"
    source = dispatcher.REPO_ROOT / relative
    original = source.read_bytes()
    source.write_bytes(original + b"\n# remote drift\n")
    _fixture_git(dispatcher.REPO_ROOT, "add", relative)
    _fixture_git(
        dispatcher.REPO_ROOT,
        "commit",
        "-m",
        "fixture: drift authoritative verifier source",
    )
    drifted_commit = _fixture_git(dispatcher.REPO_ROOT, "rev-parse", "HEAD")
    source.write_bytes(original)

    with pytest.raises(
        dispatcher.DispatchError,
        match="authoritative verifier source bytes drifted",
    ):
        dispatcher._validate_g2_verifier_sources(
            g2_v2_fixture["contract"],
            authoritative_commit_sha=drifted_commit,
        )


@pytest.mark.parametrize("field", ["owner", "reviewer"])
def test_g2_v4_rejects_closeout_actor_outside_fleet(
    g2_v2_fixture: dict,
    field: str,
) -> None:
    g2_v2_fixture["closeout_task"][field] = "Mallory"
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="closeout owner and reviewer must be distinct",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_g2_v4_rejects_nonexact_manifest_schema_directly(
    g2_v2_fixture: dict,
    mutation: str,
) -> None:
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    if mutation == "missing":
        evidence.pop("closeout_admission")
    else:
        evidence["unbound"] = True
    _rewrite_g2(g2_v2_fixture, evidence)
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="G2 evidence manifest schema is not exact",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_g2_v4_rejects_nonexact_record_reference_schema_directly(
    g2_v2_fixture: dict,
    mutation: str,
) -> None:
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    signal = evidence["records"]["signal"]
    if mutation == "missing":
        signal.pop("sha256")
    else:
        signal["unbound"] = True
    _rewrite_g2(g2_v2_fixture, evidence)
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="G2 signal record reference schema is not exact",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_wrong_target_task_directly(
    g2_v2_fixture: dict,
) -> None:
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    evidence["task_id"] = "LOOP-PROD-FORGED-001"
    _rewrite_g2(g2_v2_fixture, evidence)
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="G2 evidence manifest authority mismatch",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


@pytest.mark.parametrize(
    "field",
    [
        "tasks_catalog_sha256",
        "sequencing_addendum_sha256",
        "merge_pr_3737_sha",
        "overlay_sha256",
        "target_task_original_contract_sha256",
        "target_task_amended_contract_sha256",
    ],
)
def test_g2_v4_rejects_wrong_source_authority_directly(
    g2_v2_fixture: dict,
    field: str,
) -> None:
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    width = 40 if field == "merge_pr_3737_sha" else 64
    evidence["authority"][field] = "0" * width
    _rewrite_g2(g2_v2_fixture, evidence)
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="G2 evidence hash authority mismatch",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


@pytest.mark.parametrize(
    ("role", "field", "expected"),
    [
        ("signal", "event_id", "G2 signal record digest resolution mismatch"),
        ("order", "event_id", "G2 order record digest resolution mismatch"),
        ("fill", "event_id", "G2 fill record digest resolution mismatch"),
        (
            "telemetry",
            "event_id",
            "G2 telemetry record digest resolution mismatch",
        ),
        ("loop_run_projection", "id", "G2 loop projection digest resolution mismatch"),
    ],
)
def test_g2_v4_rejects_wrong_record_linkage_directly(
    g2_v2_fixture: dict,
    role: str,
    field: str,
    expected: str,
) -> None:
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    evidence["records"][role][field] = "forged-link"
    _rewrite_g2(g2_v2_fixture, evidence)
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(dispatcher.DispatchError, match=expected):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_authoritative_source_row_omission_directly(
    g2_v2_fixture: dict,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    snapshot = g2_v2_fixture["authoritative_snapshot"]
    snapshot["rows"] = snapshot["rows"][:-1]
    snapshot["attestation"]["rows_sha256"] = dispatcher.canonical_json_sha256(
        snapshot["rows"]
    )

    with pytest.raises(
        dispatcher.DispatchError,
        match="G2 canonical bundle does not resolve against authoritative stores",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


@pytest.mark.parametrize("mutation", ["causal_parent", "sequence"])
def test_g2_v4_rejects_coherent_chain_topology_mutation_directly(
    g2_v2_fixture: dict,
    mutation: str,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    bundle = _read_artifact(g2_v2_fixture["paths"]["bundle"])
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    if mutation == "causal_parent":
        row = next(
            item
            for item in bundle["rows"]
            if item["event_type"] == "paper_fill_simulated"
        )
        payload = row["payload"]
        payload["causal_parent_id"] = "forged-causal-parent"
        payload["metadata"]["causal_parent_id"] = "forged-causal-parent"
        payload["correlation_envelope"]["causation_event_id"] = (
            "forged-causal-parent"
        )
        role = "fill"
    else:
        row = next(
            item
            for item in bundle["rows"]
            if item["event_type"] == "order_submitted"
        )
        row["payload"]["sequence_no"] = 4
        row["payload"]["metadata"]["sequence_no"] = 4
        role = "order"
    evidence["records"][role]["sha256"] = dispatcher.canonical_json_sha256(
        row
    )
    evidence["record_bundle"]["sha256"] = _write_json_artifact(
        g2_v2_fixture["paths"]["bundle"], bundle
    )
    _rewrite_g2(g2_v2_fixture, evidence)

    with pytest.raises(
        dispatcher.DispatchError,
        match="G2 bundle contains canonical rows outside complete lifecycles",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_stale_evidence(g2_v2_fixture: dict) -> None:
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    evidence["issued_at"] = "2026-07-13T00:03:00Z"
    evidence["expires_at"] = "2026-07-14T00:03:00Z"
    _rewrite_g2(g2_v2_fixture, evidence)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="G2 evidence is stale, future-dated, or expired",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_canonical_rows_stale_against_verifier_now(
    g2_v2_fixture: dict,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    verifier_now = datetime(2026, 7, 16, 0, 1, 30, tzinfo=timezone.utc)

    with pytest.raises(
        dispatcher.DispatchError,
        match="canonical lifecycle is stale",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=verifier_now,
        )


def test_g2_v4_rejects_non_authoritative_status_root(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    other_root = tmp_path / "other-status-root"
    other_root.mkdir()
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(other_root))
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="central status root authority mismatch",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_wrong_merge_sha_authority(g2_v2_fixture: dict) -> None:
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    evidence["authority"]["merge_pr_3737_sha"] = "0" * 40
    _rewrite_g2(g2_v2_fixture, evidence)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="G2 evidence hash authority mismatch",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


@pytest.mark.parametrize("closeout_source", ["active", "archive"])
def test_g2_v2_rejects_false_active_or_archive_closeout(
    g2_v2_fixture: dict,
    closeout_source: str,
) -> None:
    if closeout_source == "active":
        g2_v2_fixture["state"]["tasks"][0]["status"] = "review_approved"
    else:
        _archive_closeout(g2_v2_fixture, outcome="superseded")

    dispatcher = g2_v2_fixture["dispatcher"]
    expected = (
        "G2 target is not done with completed outcome"
        if closeout_source == "active"
        else "G2 target archive closeout is not exact"
    )
    with pytest.raises(dispatcher.DispatchError, match=expected):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_active_done_without_source_approval_truth(
    g2_v2_fixture: dict,
) -> None:
    target = g2_v2_fixture["state"]["tasks"][0]
    target.pop("source_ref")
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="closeout source provenance mismatch",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_minimal_archived_done_without_admitted_review(
    g2_v2_fixture: dict,
) -> None:
    target = g2_v2_fixture["state"]["tasks"].pop()
    minimal = {
        key: deepcopy(target[key])
        for key in (
            "id",
            "status",
            "terminal_outcome",
            "owner",
            "reviewer",
            "last_update",
            "next",
            "delivery",
        )
    }
    _write_json_artifact(
        g2_v2_fixture["paths"]["archive"],
        {
            "version": 1,
            "task_id": target["id"],
            "archived_at": target["last_update"],
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": minimal,
            "handoffs": [],
            "blockers": [],
        },
    )
    dispatcher = g2_v2_fixture["dispatcher"]

    with pytest.raises(
        dispatcher.DispatchError,
        match="closeout review_file mismatch",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_missing_canonical_row(g2_v2_fixture: dict) -> None:
    bundle = _read_artifact(g2_v2_fixture["paths"]["bundle"])
    bundle["rows"] = [
        row for row in bundle["rows"] if row["event_type"] != "position_snapshot"
    ]
    _rewrite_bundle(g2_v2_fixture, bundle)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="G2 canonical rows are not exact",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_canonical_record_digest_mismatch(
    g2_v2_fixture: dict,
) -> None:
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    evidence["records"]["fill"]["sha256"] = "0" * 64
    _rewrite_g2(g2_v2_fixture, evidence)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="G2 fill record digest resolution mismatch",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


@pytest.mark.parametrize(
    ("mutation", "invalid_value"),
    [("producer_revision", True), ("sequence_no", 1.0)],
)
def test_g2_v2_rejects_bool_revision_and_float_sequence_with_valid_digests(
    g2_v2_fixture: dict,
    mutation: str,
    invalid_value: object,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    bundle = _read_artifact(g2_v2_fixture["paths"]["bundle"])
    signal = bundle["rows"][0]
    if mutation == "producer_revision":
        signal["payload"]["correlation_envelope"][mutation] = invalid_value
    else:
        signal["payload"][mutation] = invalid_value
        signal["payload"]["metadata"][mutation] = invalid_value
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    evidence["records"]["signal"]["sha256"] = (
        dispatcher.canonical_json_sha256(signal)
    )
    evidence["record_bundle"]["sha256"] = _write_json_artifact(
        g2_v2_fixture["paths"]["bundle"], bundle
    )
    _rewrite_g2(g2_v2_fixture, evidence)

    with pytest.raises(
        dispatcher.DispatchError,
        match="canonical sequence number is not an exact integer",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_integer_one_for_controller_accepted_live(
    g2_v2_fixture: dict,
) -> None:
    from services.trade_journey.lifecycle_projector import _fingerprint

    bundle = _read_artifact(g2_v2_fixture["paths"]["bundle"])
    projection = bundle["projection"]
    projection["trade_journey_events"]["controller"]["accepted_live"] = 1
    projection["loop_runs"]["controller"]["accepted_live"] = 1
    projection["manifest"]["journey_sha256"] = _fingerprint(
        projection["trade_journey_events"]
    )
    projection["manifest"]["loop_runs_sha256"] = _fingerprint(
        projection["loop_runs"]
    )
    _rewrite_bundle(g2_v2_fixture, bundle)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="projection controller is not canonical live truth",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_projection_before_last_canonical_ingest(
    g2_v2_fixture: dict,
) -> None:
    from services.trade_journey.lifecycle_projector import _fingerprint

    dispatcher = g2_v2_fixture["dispatcher"]
    bundle = _read_artifact(g2_v2_fixture["paths"]["bundle"])
    projection = bundle["projection"]
    invalid_projection_at = "2026-07-15T00:01:05Z"
    for document in (
        projection["trade_journey_events"],
        projection["loop_runs"],
    ):
        document["controller"]["last_projection_success_at"] = (
            invalid_projection_at
        )
    loop_record = next(iter(projection["loop_runs"]["records"].values()))
    loop_record["last_projected_at"] = invalid_projection_at
    projection["manifest"]["journey_sha256"] = _fingerprint(
        projection["trade_journey_events"]
    )
    projection["manifest"]["loop_runs_sha256"] = _fingerprint(
        projection["loop_runs"]
    )
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    evidence["records"]["loop_run_projection"]["sha256"] = (
        dispatcher.canonical_json_sha256(loop_record)
    )
    evidence["record_bundle"]["sha256"] = _write_json_artifact(
        g2_v2_fixture["paths"]["bundle"], bundle
    )
    _rewrite_g2(g2_v2_fixture, evidence)

    with pytest.raises(
        dispatcher.DispatchError,
        match="projection controller freshness mismatch",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


@pytest.mark.parametrize("mutation", ["empty_id", "empty_evidence_refs"])
def test_g2_v2_rejects_empty_acceptance_identity_or_evidence(
    g2_v2_fixture: dict,
    mutation: str,
) -> None:
    product = _read_artifact(g2_v2_fixture["paths"]["product"])
    if mutation == "empty_id":
        product["acceptance"][0]["id"] = ""
    else:
        product["acceptance"][0]["evidence_refs"] = []
    _rewrite_product(g2_v2_fixture, product)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="product evidence acceptance is not positive",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_opaque_hosted_readback_without_positive_observation(
    g2_v2_fixture: dict,
) -> None:
    product = _read_artifact(g2_v2_fixture["paths"]["product"])
    product["hosted_readback"] = {
        "pre_deploy": {"observation": "opaque prose"}
    }
    _rewrite_product(g2_v2_fixture, product)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="hosted readback has no positive observation",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_vacuous_deployment_identity_admission(
    g2_v2_fixture: dict,
) -> None:
    product = _read_artifact(g2_v2_fixture["paths"]["product"])
    product["deployment"]["identity_admission"] = {
        "deployment_sha": G2_DEPLOYMENT_SHA,
    }
    _rewrite_product(g2_v2_fixture, product)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="deployment identity admission mismatch",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_missing_trusted_deployment_identity_manifest(
    g2_v2_fixture: dict,
) -> None:
    g2_v2_fixture["paths"]["trusted_deployment"].unlink()

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="trusted deployment identity manifest is unavailable",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_artifact_self_asserted_deployment_identity(
    g2_v2_fixture: dict,
) -> None:
    product = _read_artifact(g2_v2_fixture["paths"]["product"])
    candidate_digest = "sha256:" + "a" * 64
    product["deployment"]["canonical_root_deploy"][
        "image_manifest_digest"
    ] = candidate_digest
    product["deployment"]["identity_admission"][
        "image_manifest_digest"
    ] = candidate_digest
    product["hosted_readback"]["capture_time_hosted_readback"][
        "image_manifest_digest"
    ] = candidate_digest
    product["deployment"]["identity_admission"][
        "hosted_readback_sha256"
    ] = canonical_sha256(product["hosted_readback"])
    _rewrite_product(g2_v2_fixture, product)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="trusted deployment identity manifest mismatch",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_blank_behavioral_proof_reference(
    g2_v2_fixture: dict,
) -> None:
    product = _read_artifact(g2_v2_fixture["paths"]["product"])
    product["behavioral_proof"]["duplicate_safety"]["proof"] = ["   "]
    _rewrite_product(g2_v2_fixture, product)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="behavioral proof is not positive",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_contradictory_reviewer_verdict(
    g2_v2_fixture: dict,
) -> None:
    product = _read_artifact(g2_v2_fixture["paths"]["product"])
    product["record_log"].append(
        {
            "sequence": 2,
            "recorded_at": G2_VERDICT_AT,
            "kind": "formal_review_verdict",
            "status": "rejected",
            "actor": g2_v2_fixture["closeout_task"]["reviewer"],
            "reference": "independent review contradiction",
        }
    )
    _rewrite_product(g2_v2_fixture, product)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="reviewer verdict is not exact",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_archive_closeout_before_merged_delivery(
    g2_v2_fixture: dict,
) -> None:
    _archive_closeout(g2_v2_fixture)
    archive = _read_artifact(g2_v2_fixture["paths"]["archive"])
    archive["archived_at"] = G2_VERDICT_AT
    archive["task"]["last_update"] = G2_VERDICT_AT
    archive["task"]["delivery"]["recorded_at"] = G2_VERDICT_AT
    _write_json_artifact(g2_v2_fixture["paths"]["archive"], archive)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="closeout chronology is invalid",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_closeout_after_verifier_now(
    g2_v2_fixture: dict,
) -> None:
    target_id = g2_v2_fixture["contract"]["target_task"]
    closeout = next(
        task
        for task in g2_v2_fixture["state"]["tasks"]
        if task.get("id") == target_id
    )
    closeout["last_update"] = "2026-07-15T00:04:00Z"
    closeout["delivery"]["recorded_at"] = "2026-07-15T00:04:00Z"

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="closeout chronology is invalid",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_product_schema_raw_sha_drift(
    g2_v2_fixture: dict,
) -> None:
    schema_path = (
        g2_v2_fixture["dispatcher"].REPO_ROOT
        / "schemas"
        / "product-evidence.schema.json"
    )
    schema_path.write_bytes(schema_path.read_bytes() + b"\n")

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="product evidence schema digest mismatch",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v2_rejects_product_evidence_leaf_symlink(
    g2_v2_fixture: dict,
) -> None:
    product_path = g2_v2_fixture["paths"]["product"]
    external = product_path.with_name("external-product-evidence.json")
    product_path.replace(external)
    product_path.symlink_to(external)

    dispatcher = g2_v2_fixture["dispatcher"]
    with pytest.raises(
        dispatcher.DispatchError,
        match="G2 product evidence must be a regular file",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("status", "G2 loop-run projection record mismatch"),
        (
            "tenant",
            "G2 bundle contains canonical rows outside complete lifecycles",
        ),
        ("environment", "G2 record environment mismatch"),
        ("run", "G2 bundle does not resolve the declared natural lifecycle"),
        (
            "order",
            "G2 bundle contains canonical rows outside complete lifecycles",
        ),
    ],
)
def test_g2_v2_rejects_status_identity_environment_run_and_order_mutations(
    g2_v2_fixture: dict,
    mutation: str,
    expected: str,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    if mutation in {"environment", "run"}:
        key = "environment" if mutation == "environment" else "run_id"
        evidence["identity"][key] = f"wrong-{mutation}"
        _rewrite_g2(g2_v2_fixture, evidence)
    else:
        bundle = _read_artifact(g2_v2_fixture["paths"]["bundle"])
        if mutation == "status":
            loops = bundle["projection"]["loop_runs"]
            loop = next(iter(loops["records"].values()))
            loop["status"] = "failed"
            from services.trade_journey.lifecycle_projector import _fingerprint

            bundle["projection"]["manifest"]["loop_runs_sha256"] = _fingerprint(
                loops
            )
            evidence["records"]["loop_run_projection"][
                "sha256"
            ] = dispatcher.canonical_json_sha256(loop)
        elif mutation == "tenant":
            row = bundle["rows"][0]
            row["payload"]["correlation_envelope"]["tenant_id"] = "other-tenant"
            evidence["records"]["signal"][
                "sha256"
            ] = dispatcher.canonical_json_sha256(row)
        else:
            order = next(
                row
                for row in bundle["rows"]
                if row["event_type"] == "order_submitted"
            )
            order["payload"]["sequence_no"] = 4
            order["payload"]["metadata"]["sequence_no"] = 4
            evidence["records"]["order"][
                "sha256"
            ] = dispatcher.canonical_json_sha256(order)
        digest = _write_json_artifact(g2_v2_fixture["paths"]["bundle"], bundle)
        evidence["record_bundle"]["sha256"] = digest
        _rewrite_g2(g2_v2_fixture, evidence)

    with pytest.raises(dispatcher.DispatchError, match=expected):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def test_g2_v4_rejects_bundle_with_a_second_complete_lifecycle(
    g2_v2_fixture: dict,
) -> None:
    import uuid

    from services.trade_journey import hosted_lifecycle_probe as probe
    from services.trade_journey.lifecycle_projector import LifecycleProjector
    from services.trade_journey.test_hosted_lifecycle_probe import (
        _natural_lifecycle_rows,
    )

    dispatcher = g2_v2_fixture["dispatcher"]
    evidence = _read_artifact(g2_v2_fixture["paths"]["g2"])
    bundle = _read_artifact(g2_v2_fixture["paths"]["bundle"])
    declared_rows = deepcopy(bundle["rows"])
    replacements = {
        "tj-paper-001": "tj-paper-002",
        "run-paper-001": "run-paper-002",
        "signal-paper-001": "signal-paper-002",
        "strategy-paper-001": "strategy-paper-002",
        "runtime-paper-001": "runtime-paper-002",
        "10000000-0000-0000-0000-000000000001": (
            "10000000-0000-0000-0000-000000000002"
        ),
        "pool-paper-001": "pool-paper-002",
        "persona-paper-001": "persona-paper-002",
        "pcb-paper-001": "pcb-paper-002",
        "artifact-paper-001": "artifact-paper-002",
        "plan-paper-001": "plan-paper-002",
        "20000000-0000-0000-0000-000000000001": (
            "20000000-0000-0000-0000-000000000002"
        ),
        "decision-paper-001": "decision-paper-002",
        "client-order-paper-001": "client-order-paper-002",
        "order-paper-001": "order-paper-002",
        "reconciliation-paper-001": "reconciliation-paper-002",
        "evaluation-paper-001": "evaluation-paper-002",
        "signal:signal-paper-001": "signal:signal-paper-002",
    }

    def replace_identity(value):
        if isinstance(value, dict):
            return {key: replace_identity(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_identity(item) for item in value]
        return replacements.get(value, value)

    later_rows = replace_identity(_natural_lifecycle_rows())
    signal_event_id = "30000000-0000-0000-0000-000000000001"
    fill_event_id = "30000000-0000-0000-0000-000000000006"
    evaluation_id = "evaluation-paper-002"
    event_ids = [
        signal_event_id,
        str(
            uuid.uuid5(
                probe.PAPER_LIFECYCLE_UUID_NAMESPACE,
                f"{signal_event_id}:trade_decision",
            )
        ),
        str(
            uuid.uuid5(
                probe.PAPER_LIFECYCLE_UUID_NAMESPACE,
                f"{fill_event_id}:order_submitted",
            )
        ),
        fill_event_id,
        str(
            uuid.uuid5(
                probe.PAPER_LIFECYCLE_UUID_NAMESPACE,
                f"{fill_event_id}:position_snapshot",
            )
        ),
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"pantheon:scheduled-reconciliation:{evaluation_id}",
            )
        ),
    ]
    causal_parent = "signal:signal-paper-002"
    for ordinal, (row, event_id) in enumerate(
        zip(later_rows, event_ids), start=1
    ):
        created_at = f"2026-07-15T00:00:{10 + ordinal:02d}Z"
        row.update(
            {
                "ingested_seq": 6 + ordinal,
                "ingested_at": f"2026-07-15T00:01:{10 + ordinal:02d}Z",
                "event_id": event_id,
                "created_at": created_at,
            }
        )
        payload = row["payload"]
        payload.update(
            {
                "event_id": event_id,
                "created_at": created_at,
                "sequence_no": ordinal,
                "causal_parent_id": causal_parent,
            }
        )
        payload["metadata"].update(
            {"sequence_no": ordinal, "causal_parent_id": causal_parent}
        )
        if payload["event_type"] == "reconciliation_completed":
            payload["metadata"]["reconciliation_evaluation_id"] = evaluation_id
        payload["correlation_envelope"].update(
            {
                "event_id": event_id,
                "causation_event_id": causal_parent,
                "event_time": created_at,
                "received_at": created_at,
            }
        )
        causal_parent = event_id
    for index, row in enumerate(later_rows):
        payload = row["payload"]
        if payload["event_type"] == "reconciliation_completed":
            metadata_envelope = payload["correlation_envelope"]
        elif index == 0:
            metadata_envelope = {"event_id": "signal:signal-paper-002"}
        else:
            metadata_envelope = later_rows[index - 1]["payload"][
                "correlation_envelope"
            ]
        payload["metadata"]["correlation_envelope"] = deepcopy(
            metadata_envelope
        )

    combined_rows = [*declared_rows, *later_rows]
    projection_root = dispatcher.REPO_ROOT / "multi-lifecycle-projection"
    projector = LifecycleProjector(
        state_path=projection_root / "controller_state.json",
        bundle_root=projection_root,
        deployment_sha=g2_v2_fixture["implementation_merge_sha"],
        clock=lambda: G2_PROJECTED_AT,
    )
    projector.project_records(
        combined_rows,
        mode="live",
        source_high_watermark=len(combined_rows),
    )
    current = projection_root / "current"
    manifest = _read_artifact(current / "manifest.json")
    journeys = _read_artifact(current / "trade_journey_events.json")
    loops = _read_artifact(current / "loop_runs.json")
    candidates = probe._complete_candidates(combined_rows)
    assert [candidate["identity"]["journey_id"] for candidate in candidates] == [
        "tj-paper-002",
        evidence["identity"]["journey_id"],
    ]
    declared_candidate = next(
        candidate
        for candidate in candidates
        if candidate["identity"] == evidence["identity"]
    )
    proof = probe._correlate(
        candidate=declared_candidate,
        baseline_high_watermark=0,
        high_watermark=len(combined_rows),
        journeys=journeys,
            loops=loops,
            generation_name=current.resolve().name,
            expected_sha=g2_v2_fixture["implementation_merge_sha"],
    )
    hosted = _read_artifact(g2_v2_fixture["paths"]["probe"])
    hosted["proof"] = proof
    probe_digest = _write_json_artifact(
        g2_v2_fixture["paths"]["probe"], hosted
    )
    bundle.update(
        {
            "rows": combined_rows,
            "source": {
                "store": "telemetry_events",
                "snapshot_isolation": "repeatable_read",
                "baseline_high_watermark": 0,
                "source_high_watermark": len(combined_rows),
            },
            "projection": {
                "manifest": manifest,
                "trade_journey_events": journeys,
                "loop_runs": loops,
            },
        }
    )
    bundle_digest = _write_json_artifact(
        g2_v2_fixture["paths"]["bundle"], bundle
    )
    evidence["record_bundle"]["sha256"] = bundle_digest
    evidence["hosted_probe"]["sha256"] = probe_digest
    declared_loop = loops["records"][evidence["identity"]["loop_run_id"]]
    evidence["records"]["loop_run_projection"].update(
        {
            "sha256": dispatcher.canonical_json_sha256(declared_loop),
            "generation": manifest["generation"],
            "last_canonical_event_id": declared_candidate["selected_events"][-1][
                "event_id"
            ],
        }
    )
    _rewrite_g2(g2_v2_fixture, evidence)
    g2_v2_fixture["authoritative_snapshot"].update(
        {
            "source_high_watermark": len(combined_rows),
            "rows": deepcopy(combined_rows),
            "projection": deepcopy(bundle["projection"]),
            "attestation": {
                **g2_v2_fixture["authoritative_snapshot"]["attestation"],
                "live_source_high_watermark": len(combined_rows),
                "captured_generation_name": proof["projection"][
                    "generation_name"
                ],
                    "current_generation_name": proof["projection"][
                        "generation_name"
                    ],
                    "captured_projection_checkpoint": loops["controller"][
                        "checkpoint"
                    ],
                    "captured_projection_source_high_watermark": loops[
                        "controller"
                    ]["source_high_watermark"],
                    "current_projection_checkpoint": loops["controller"][
                        "checkpoint"
                    ],
                    "current_projection_source_high_watermark": loops[
                        "controller"
                    ]["source_high_watermark"],
                "rows_sha256": dispatcher.canonical_json_sha256(
                    combined_rows
                ),
                    "projection_sha256": dispatcher.canonical_json_sha256(
                        bundle["projection"]
                    ),
                    "current_projection_sha256": dispatcher.canonical_json_sha256(
                        bundle["projection"]
                    ),
            },
        }
    )
    g2_v2_fixture["commit_artifacts"]()

    with pytest.raises(
        dispatcher.DispatchError,
        match="one exact natural lifecycle",
    ):
        dispatcher._validate_g2_evidence(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            now=g2_v2_fixture["now"],
        )


def _park_complete_g2_catalog(
    fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tasks: list[dict] | None = None,
) -> tuple[list[dict], tuple[list[str], list[str], list[str], list[dict], bool]]:
    dispatcher = fixture["dispatcher"]
    materialized_tasks = tasks or deepcopy(fixture["catalog"]["tasks"])
    closeout_done = fixture["state"]["tasks"][0]
    target_spec = next(
        task
        for task in materialized_tasks
        if task["id"] == fixture["contract"]["target_task"]
    )
    fixture["state"]["tasks"][0] = dispatcher.build_task(
        target_spec,
        fixture["catalog"],
        fixture["catalog_digest"],
        G2_ISSUED_AT,
    )
    monkeypatch.setattr(
        dispatcher,
        "resolve_g2_evidence_admission",
        lambda state, catalog: None,
    )
    result = dispatcher.materialize(
        state=fixture["state"],
        tasks=materialized_tasks,
        catalog=fixture["catalog"],
        catalog_digest=fixture["catalog_digest"],
        timestamp=G2_ISSUED_AT,
    )
    source_catalog = load_catalog()
    epoch_logs, epoch_changed = dispatcher.install_fresh_sequencing_epoch(
        fixture["state"],
        source_catalog,
        hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
        fixture["catalog"],
        fixture["catalog_digest"],
        G2_ISSUED_AT,
        graph_prestate="fresh",
    )
    assert epoch_changed is True
    dispatcher.enqueue_activity_outbox(
        fixture["state"],
        [*result[3], *epoch_logs],
        catalog=fixture["catalog"],
        catalog_digest=fixture["catalog_digest"],
    )
    pending = fixture["state"]["program_activity_outbox"]
    dispatcher.append_logs(pending["events"])
    fixture["state"]["program_activity_outbox"] = None
    fixture["state"]["tasks"] = [
        closeout_done if task["id"] == closeout_done["id"] else task
        for task in fixture["state"]["tasks"]
    ]
    return materialized_tasks, result


def test_release_gate_is_exact_set_not_wave_and_runs_once_while_closed(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    calls = 0

    def counted_resolver(state, catalog):
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(
        dispatcher, "resolve_g2_evidence_admission", counted_resolver
    )
    tasks = deepcopy(g2_v2_fixture["catalog"]["tasks"])
    by_id = {task["id"]: task for task in tasks}
    by_id["LOOP-PROD-PER-001"]["wave"] = 99
    by_id["LOOP-PROD-AUTH-001"]["wave"] = 0

    created, preserved, _, _, changed = dispatcher.materialize(
        state=g2_v2_fixture["state"],
        tasks=tasks,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
        timestamp=G2_ISSUED_AT,
    )

    assert calls == 1
    assert len(created) == 28
    gated_ids = set(
        g2_v2_fixture["catalog"]["release_gate"]["gated_task_ids"]
    )
    assert set(preserved) == {
        f"{g2_v2_fixture['contract']['target_task']}:done",
        *(f"{task_id}:g2-gated-unmaterialized" for task_id in gated_ids),
    }
    state_by_id = {
        task["id"]: task for task in g2_v2_fixture["state"]["tasks"]
    }
    assert not (set(state_by_id) & gated_ids)
    assert state_by_id["LOOP-PROD-PER-001"]["wave"] == 99
    assert g2_v2_fixture["catalog"]["program_id"] not in (
        g2_v2_fixture["state"].get("program_sequencing_releases") or {}
    )
    assert changed is True


def test_explicit_sequencing_overlay_migrates_and_parks_complete_base_board() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        base_apply = run_dispatch(root, "--apply")
        assert base_apply.returncode == 0, base_apply.stderr
        before = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        before_migrations = deepcopy(before.get("program_catalog_migrations") or [])
        before_by_id = {
            task["id"]: deepcopy(task)
            for task in before["tasks"]
            if task.get("id", "").startswith("LOOP-PROD-")
        }

        applied = run_dispatch(
            root,
            "--apply",
            "--sequencing-overlay",
            str(SEQUENCING_OVERLAY),
        )
        assert applied.returncode == 0, applied.stderr
        state_after_first = (root / "ai-status.json").read_bytes()
        log_after_first = (root / "ai-activity-log.jsonl").read_bytes()
        after = json.loads(state_after_first)
        catalog = load_catalog()
        dispatcher = _load_dispatcher_module()
        dispatcher.catalog_path = lambda: CATALOG
        dispatcher.apply_sequencing_overlay(catalog, SEQUENCING_OVERLAY)
        gated_ids = set(catalog["release_gate"]["gated_task_ids"])
        catalog_ids = {task["id"] for task in catalog["tasks"]}
        program = [task for task in after["tasks"] if task.get("id") in catalog_ids]
        assert len(program) == 48
        assert {task["id"] for task in program if task["status"] == "blocked"} == gated_ids
        assert {
            task["id"] for task in program if task["status"] == "todo"
        } == {task["id"] for task in catalog["tasks"]} - gated_ids
        for task in program:
            assert task["source_ref"]["sequencing_overlay_sha256"] == (
                catalog["sequencing_overlay_sha256"]
            )
            if task["id"] in gated_ids:
                assert task["sequencing_release_gate"]["state"] == "parked"
        epoch = after["program_sequencing_epochs"][catalog["program_id"]]
        assert epoch["schema_version"] == 2
        assert epoch["task_count"] == 48
        assert len(epoch["task_transitions"]) == 48
        for transition in epoch["task_transitions"]:
            preimage = transition["before_task_snapshot"]
            assert preimage == before_by_id[transition["task_id"]]
            assert transition["before_task_snapshot_sha256"] == (
                dispatcher.canonical_json_sha256(preimage)
            )
            assert transition["before_source_ref_sha256"] == (
                dispatcher.canonical_json_sha256(preimage["source_ref"])
            )
        assert after.get("program_catalog_migrations", []) == before_migrations

        tampered = deepcopy(after)
        tampered_epoch = tampered["program_sequencing_epochs"][catalog["program_id"]]
        tampered_transition = tampered_epoch["task_transitions"][0]
        tampered_preimage = tampered_transition["before_task_snapshot"]
        tampered_preimage["summary_zh"] = "forged historical contract"
        tampered_transition["before_task_snapshot_sha256"] = (
            dispatcher.canonical_json_sha256(tampered_preimage)
        )
        tampered_transition["before_task_contract_sha256"] = (
            dispatcher.task_contract_sha256(tampered_preimage)
        )
        tampered_epoch["task_transition_set_sha256"] = (
            dispatcher.canonical_json_sha256(tampered_epoch["task_transitions"])
        )
        with pytest.raises(
            dispatcher.DispatchError,
            match="base sequencing epoch preimage is not pristine",
        ):
            dispatcher.validate_sequencing_epoch_record(
                tampered,
                catalog,
                dispatcher.canonical_json_sha256(catalog),
            )

        rerun = run_dispatch(
            root,
            "--apply",
            "--sequencing-overlay",
            str(SEQUENCING_OVERLAY),
        )
        assert rerun.returncode == 0, rerun.stderr
        assert "No state changes required." in rerun.stdout
        assert (root / "ai-status.json").read_bytes() == state_after_first
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_after_first


def test_explicit_sequencing_overlay_fresh_apply_defers_gate_tasks() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)

        applied = run_dispatch(
            root,
            "--apply",
            "--sequencing-overlay",
            str(SEQUENCING_OVERLAY),
        )

        assert applied.returncode == 0, applied.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        catalog = load_catalog()
        dispatcher = _load_dispatcher_module()
        dispatcher.catalog_path = lambda: CATALOG
        dispatcher.apply_sequencing_overlay(catalog, SEQUENCING_OVERLAY)
        gated_ids = set(catalog["release_gate"]["gated_task_ids"])
        by_id = {task["id"]: task for task in program_tasks(after)}
        assert set(by_id) == {
            task["id"] for task in catalog["tasks"]
        } - gated_ids
        assert all(task["status"] == "todo" for task in by_id.values())
        assert all("sequencing_release_gate" not in task for task in by_id.values())
        epoch = after["program_sequencing_epochs"][catalog["program_id"]]
        assert epoch["schema_version"] == 2
        assert epoch["install_mode"] == "fresh_materialization"
        assert epoch["task_count"] == 48
        assert len(epoch["task_transitions"]) == 48
        assert all(
            row["before_task_snapshot"] is None
            and row["before_task_snapshot_sha256"] == canonical_sha256(None)
            and row["before_task_contract_sha256"] == canonical_sha256(None)
            and row["before_source_ref_sha256"] == canonical_sha256(None)
            for row in epoch["task_transitions"]
        )
        gated_transitions = {
            row["task_id"]: row
            for row in epoch["task_transitions"]
            if row["task_id"] in gated_ids
        }
        assert set(gated_transitions) == gated_ids
        catalog_by_id = {task["id"]: task for task in catalog["tasks"]}
        for task_id, row in gated_transitions.items():
            planned = dispatcher.build_task(
                catalog_by_id[task_id],
                catalog,
                dispatcher.canonical_json_sha256(catalog),
                epoch["applied_at"],
            )
            assert row["after_status"] == "absent"
            assert row["after_task_snapshot_sha256"] == canonical_sha256(None)
            assert row["after_task_contract_sha256"] == (
                dispatcher.task_contract_sha256(planned)
            )
            assert row["after_source_ref_sha256"] == canonical_sha256(
                planned["source_ref"]
            )
            assert row["acceptance_deferral_sha256"] == canonical_sha256(
                planned.get("acceptance_deferral")
            )
            assert row["gate_marker_sha256"] == canonical_sha256(None)


def test_documented_default_apply_uses_authoritative_sequencing_overlay() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)

        applied = run_dispatch(
            root,
            "--apply",
            extra_env={"LOOP_PRODUCT_TEST_BASE_CATALOG": "0"},
        )

        assert applied.returncode == 0, applied.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        catalog = load_catalog()
        dispatcher = _load_dispatcher_module()
        dispatcher.catalog_path = lambda: CATALOG
        dispatcher.apply_sequencing_overlay(catalog, SEQUENCING_OVERLAY)
        gated_ids = set(catalog["release_gate"]["gated_task_ids"])
        by_id = {task["id"]: task for task in program_tasks(after)}
        assert set(by_id) == {
            task["id"] for task in catalog["tasks"]
        } - gated_ids
        assert all(task["status"] == "todo" for task in by_id.values())
        assert after["program_sequencing_epochs"][catalog["program_id"]][
            "install_mode"
        ] == "fresh_materialization"


def test_test_base_opt_out_cannot_disable_overlay_for_live_shaped_status_root() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "live-status-root"
        prepare_status(root)

        applied = run_dispatch(root, "--apply")

        assert applied.returncode == 0, applied.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        catalog = load_catalog()
        dispatcher = _load_dispatcher_module()
        dispatcher.catalog_path = lambda: CATALOG
        dispatcher.apply_sequencing_overlay(catalog, SEQUENCING_OVERLAY)
        gated_ids = set(catalog["release_gate"]["gated_task_ids"])
        assert {task["id"] for task in program_tasks(after)} == {
            task["id"] for task in catalog["tasks"]
        } - gated_ids
        assert catalog["program_id"] in after["program_sequencing_epochs"]


def test_current_sequencing_graph_rejects_deleted_epoch_atomically() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        first = run_dispatch(
            root,
            "--apply",
            extra_env={"LOOP_PRODUCT_TEST_BASE_CATALOG": "0"},
        )
        assert first.returncode == 0, first.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        state.pop("program_sequencing_epochs")
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        rejected = run_dispatch(
            root,
            "--apply",
            extra_env={"LOOP_PRODUCT_TEST_BASE_CATALOG": "0"},
        )

        assert rejected.returncode == 2
        assert "missing its immutable epoch record" in rejected.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_explicit_sequencing_overlay_rejects_nonpristine_base_epoch_atomically() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        base_apply = run_dispatch(root, "--apply")
        assert base_apply.returncode == 0, base_apply.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        next(task for task in state["tasks"] if task.get("id") == "LOOP-PROD-000")[
            "status"
        ] = "in_progress"
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        rejected = run_dispatch(
            root,
            "--apply",
            "--sequencing-overlay",
            str(SEQUENCING_OVERLAY),
        )

        assert rejected.returncode == 2
        assert "not pristine base todo" in rejected.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("formal_review_required", False),
        ("planner_may_edit_declared_product_artifacts", True),
        ("execution_role", "planner_implements_product_artifacts"),
        ("review_role", "self_review"),
    ],
)
def test_explicit_sequencing_overlay_rejects_forged_base_runtime_authority_atomically(
    field: str,
    forged_value: object,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        base_apply = run_dispatch(root, "--apply")
        assert base_apply.returncode == 0, base_apply.stderr
        state = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        task = next(
            task for task in state["tasks"] if task.get("id") == "LOOP-PROD-000"
        )
        task[field] = forged_value
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        status_before = (root / "ai-status.json").read_bytes()
        log_before = (root / "ai-activity-log.jsonl").read_bytes()

        rejected = run_dispatch(
            root,
            "--apply",
            "--sequencing-overlay",
            str(SEQUENCING_OVERLAY),
        )

        assert rejected.returncode == 2
        assert "not pristine base todo" in rejected.stderr
        assert (root / "ai-status.json").read_bytes() == status_before
        assert (root / "ai-activity-log.jsonl").read_bytes() == log_before


def test_sequencing_epoch_outbox_recovers_exactly_once_after_status_commit() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        prepare_status(root)
        base_apply = run_dispatch(root, "--apply")
        assert base_apply.returncode == 0, base_apply.stderr

        interrupted = run_dispatch(
            root,
            "--apply",
            "--sequencing-overlay",
            str(SEQUENCING_OVERLAY),
            extra_env={"LOOP_PRODUCT_DISPATCH_FAIL_AFTER_STATUS_COMMIT": "1"},
        )
        assert interrupted.returncode == 2
        interrupted_state = json.loads(
            (root / "ai-status.json").read_text(encoding="utf-8")
        )
        assert interrupted_state["program_activity_outbox"] is not None

        recovered = run_dispatch(
            root,
            "--apply",
            "--sequencing-overlay",
            str(SEQUENCING_OVERLAY),
        )
        assert recovered.returncode == 0, recovered.stderr
        after = json.loads((root / "ai-status.json").read_text(encoding="utf-8"))
        assert after["program_activity_outbox"] is None
        records = [
            json.loads(line)
            for line in (root / "ai-activity-log.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        overlay_events = [
            row for row in records if row.get("type") == "sequencing_overlay_install"
        ]
        assert len(overlay_events) == 1


def test_fresh_sequencing_epoch_rejects_nonnull_before_task_snapshot(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    _park_complete_g2_catalog(g2_v2_fixture, monkeypatch)
    epoch = g2_v2_fixture["state"]["program_sequencing_epochs"][
        g2_v2_fixture["catalog"]["program_id"]
    ]
    assert epoch["install_mode"] == "fresh_materialization"
    assert epoch["task_transitions"][0][
        "before_task_snapshot_sha256"
    ] == canonical_sha256(None)
    epoch["task_transitions"][0]["before_task_snapshot"] = {"id": "forged"}
    epoch["task_transitions"][0]["before_task_snapshot_sha256"] = (
        canonical_sha256({"id": "forged"})
    )
    epoch["task_transition_set_sha256"] = canonical_sha256(
        epoch["task_transitions"]
    )

    with pytest.raises(
        dispatcher.DispatchError,
        match="fresh sequencing epoch preimage must be null",
    ):
        dispatcher.validate_sequencing_epoch_record(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            g2_v2_fixture["catalog_digest"],
        )


def test_valid_g2_release_is_durable_and_does_not_recheck_wall_clock(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    tasks, (_, _, _, _, parked_changed) = _park_complete_g2_catalog(
        g2_v2_fixture, monkeypatch
    )
    assert parked_changed is True
    admission = dispatcher._validate_g2_evidence(
        g2_v2_fixture["state"],
        g2_v2_fixture["catalog"],
        now=g2_v2_fixture["now"],
    )
    monkeypatch.setattr(
        dispatcher,
        "resolve_g2_evidence_admission",
        lambda state, catalog: deepcopy(admission),
    )
    created, preserved, _, logs, changed = dispatcher.materialize(
        state=g2_v2_fixture["state"],
        tasks=tasks,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
        timestamp=G2_RELEASED_AT,
    )
    gated_ids = set(
        g2_v2_fixture["catalog"]["release_gate"]["gated_task_ids"]
    )
    assert set(created) == gated_ids
    assert len(preserved) == 29
    assert changed is True
    release_log = next(
        row for row in logs if row["type"] == "sequencing_gate_release"
    )
    release = g2_v2_fixture["state"]["program_sequencing_releases"][
        g2_v2_fixture["catalog"]["program_id"]
    ]
    assert {
        row["task_id"] for row in release["released_task_transitions"]
    } == gated_ids
    assert all(
        row["before_status"] == "absent" and row["after_status"] == "todo"
        for row in release["released_task_transitions"]
    )
    assert release_log["release_record_sha256"] == canonical_sha256(release)
    dispatcher.enqueue_activity_outbox(
        g2_v2_fixture["state"],
        logs,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
    )

    calls = 0

    def forbidden_recheck(state, catalog):
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(
        dispatcher, "resolve_g2_evidence_admission", forbidden_recheck
    )
    created_again, preserved, _, _, changed_again = dispatcher.materialize(
        state=g2_v2_fixture["state"],
        tasks=tasks,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
        timestamp="2026-07-20T00:00:00Z",
    )

    assert calls == 0
    assert created_again == []
    assert len(preserved) == 48
    assert all(
        task["status"] == "todo"
        and task["sequencing_release_admission_sha256"]
        == release["release_admission_sha256"]
        for task in g2_v2_fixture["state"]["tasks"]
        if task["id"] in gated_ids
    )
    assert changed_again is False


def test_fresh_dispatch_release_is_consumed_with_exact_task_authority(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    tasks, _ = _park_complete_g2_catalog(g2_v2_fixture, monkeypatch)
    admission = dispatcher._validate_g2_evidence(
        g2_v2_fixture["state"],
        g2_v2_fixture["catalog"],
        now=g2_v2_fixture["now"],
    )
    monkeypatch.setattr(
        dispatcher,
        "resolve_g2_evidence_admission",
        lambda state, catalog: deepcopy(admission),
    )
    created, _, _, logs, changed = dispatcher.materialize(
        state=g2_v2_fixture["state"],
        tasks=tasks,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
        timestamp=G2_RELEASED_AT,
    )
    assert changed is True
    assert set(created) == set(
        g2_v2_fixture["catalog"]["release_gate"]["gated_task_ids"]
    )
    dispatcher.enqueue_activity_outbox(
        g2_v2_fixture["state"],
        logs,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
    )
    durable_events = deepcopy(
        g2_v2_fixture["state"]["program_activity_outbox"]["events"]
    )
    g2_v2_fixture["state"]["program_activity_outbox"] = None

    sequencing_gate = _load_sequencing_gate_module()
    proof = sequencing_gate.build_sequencing_release_audit_proof(
        g2_v2_fixture["state"], durable_events
    )
    assert proof is not None
    by_id = {
        task["id"]: task for task in g2_v2_fixture["state"]["tasks"]
    }
    gated_ids = set(sequencing_gate.EXPECTED_GATED_TASK_IDS)
    assert gated_ids == set(created)
    assert all(
        not sequencing_gate.task_is_sequencing_parked(
            by_id[task_id],
            g2_v2_fixture["state"],
            release_audit_proof=proof,
        )
        for task_id in gated_ids
    )

    target = by_id["LOOP-PROD-AUTH-001"]

    def mutate_contract(task: dict) -> None:
        task["title"] += " forged"
        task["source_ref"]["task_contract_sha256"] = (
            sequencing_gate._task_contract_sha256(task)
        )

    def mutate_source_ref(task: dict) -> None:
        task["source_ref"]["unbound"] = "forged"

    def mutate_runtime_authority(task: dict) -> None:
        task["formal_review_required"] = False

    def mutate_reviewer(task: dict) -> None:
        task["reviewer"] = task["owner"]

    def mutate_acceptance_deferral(task: dict) -> None:
        task["acceptance_deferral"] = {"policy_id": "forged"}
        task["source_ref"]["acceptance_deferral_sha256"] = (
            sequencing_gate.canonical_sha256(task["acceptance_deferral"])
        )

    for mutate in (
        mutate_contract,
        mutate_source_ref,
        mutate_runtime_authority,
        mutate_reviewer,
        mutate_acceptance_deferral,
    ):
        forged = deepcopy(target)
        mutate(forged)
        assert sequencing_gate.task_is_sequencing_parked(
            forged,
            g2_v2_fixture["state"],
            release_audit_proof=proof,
        )


def test_new_g2_release_rechecks_freshness_immediately_before_commit(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    tasks, _ = _park_complete_g2_catalog(g2_v2_fixture, monkeypatch)
    admission = dispatcher._validate_g2_evidence(
        g2_v2_fixture["state"],
        g2_v2_fixture["catalog"],
        now=g2_v2_fixture["now"],
    )
    monkeypatch.setattr(
        dispatcher,
        "resolve_g2_evidence_admission",
        lambda state, catalog: deepcopy(admission),
    )
    _, _, _, logs, changed = dispatcher.materialize(
        state=g2_v2_fixture["state"],
        tasks=tasks,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
        timestamp=G2_RELEASED_AT,
    )
    assert changed is True
    dispatcher.enqueue_activity_outbox(
        g2_v2_fixture["state"],
        logs,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
    )
    release = g2_v2_fixture["state"]["program_sequencing_releases"][
        g2_v2_fixture["catalog"]["program_id"]
    ]
    assert release["g2_expires_at"] == G2_EXPIRES_AT
    assert release["owner_closeout_event_sha256"] == admission[
        "owner_closeout_event_sha256"
    ]
    dispatcher.validate_g2_release_fresh_at_commit(
        g2_v2_fixture["state"],
        g2_v2_fixture["catalog"],
        g2_v2_fixture["catalog_digest"],
        now=g2_v2_fixture["now"],
    )
    expired = datetime.fromisoformat(
        release["g2_fresh_until"].replace("Z", "+00:00")
    ) + timedelta(seconds=1)
    with pytest.raises(
        dispatcher.DispatchError,
        match="expired before atomic status commit",
    ):
        dispatcher.validate_g2_release_fresh_at_commit(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            g2_v2_fixture["catalog_digest"],
            now=expired,
        )


def test_atomic_status_write_aborts_when_pre_replace_freshness_fails(
    tmp_path: Path,
) -> None:
    dispatcher = _load_dispatcher_module()
    status_path = tmp_path / "ai-status.json"
    status_path.write_text('{"sentinel":"old"}\n', encoding="utf-8")
    before = status_path.read_bytes()
    callback_calls = 0

    def reject_expired_release() -> None:
        nonlocal callback_calls
        callback_calls += 1
        raise dispatcher.DispatchError("expired at replace boundary")

    with pytest.raises(
        dispatcher.DispatchError,
        match="expired at replace boundary",
    ):
        dispatcher.atomic_write_json(
            status_path,
            {"sentinel": "new"},
            before_replace=reject_expired_release,
        )

    assert callback_calls == 1
    assert status_path.read_bytes() == before
    assert list(tmp_path.glob(".ai-status.json.*.tmp")) == []


def test_pending_release_recovery_uses_committed_admission_after_artifact_drift(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    tasks, _ = _park_complete_g2_catalog(g2_v2_fixture, monkeypatch)
    admission = dispatcher._validate_g2_evidence(
        g2_v2_fixture["state"],
        g2_v2_fixture["catalog"],
        now=g2_v2_fixture["now"],
    )
    monkeypatch.setattr(
        dispatcher,
        "resolve_g2_evidence_admission",
        lambda state, catalog: deepcopy(admission),
    )
    _, _, _, release_logs, changed = dispatcher.materialize(
        state=g2_v2_fixture["state"],
        tasks=tasks,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
        timestamp=G2_RELEASED_AT,
    )
    assert changed is True
    dispatcher.enqueue_activity_outbox(
        g2_v2_fixture["state"],
        release_logs,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
    )
    pending_before = deepcopy(
        g2_v2_fixture["state"]["program_activity_outbox"]
    )
    status_path = tmp_path / "ai-status.json"
    monkeypatch.setattr(dispatcher, "STATUS_PATH", status_path)
    dispatcher.atomic_write_json(status_path, g2_v2_fixture["state"])

    product = _read_artifact(g2_v2_fixture["paths"]["product"])
    product["tampered_after_status_commit"] = True
    _write_json_artifact(g2_v2_fixture["paths"]["product"], product)
    for key in ("g2", "bundle", "probe", "sidecar"):
        g2_v2_fixture["paths"][key].unlink()

    def forbidden_live_evidence_read(*args, **kwargs):
        raise AssertionError("persisted release reread mutable G2 evidence")

    monkeypatch.setattr(dispatcher, "_read_g2_artifact", forbidden_live_evidence_read)
    monkeypatch.setattr(
        dispatcher,
        "_resolve_g2_closeout_task",
        forbidden_live_evidence_read,
    )
    monkeypatch.setenv("LOOP_PRODUCT_DISPATCH_FAIL_AFTER_ACTIVITY_APPEND", "1")
    with pytest.raises(
        dispatcher.DispatchError,
        match="injected failure after activity append",
    ):
        dispatcher.flush_activity_outbox(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            g2_v2_fixture["catalog_digest"],
        )

    interrupted = json.loads(status_path.read_text(encoding="utf-8"))
    assert interrupted["program_activity_outbox"] == pending_before
    activity_records = [
        json.loads(line)
        for line in dispatcher.LOG_PATH.read_text(encoding="utf-8").splitlines()
    ]
    release_events = [
        row for row in activity_records if row.get("type") == "sequencing_gate_release"
    ]
    assert len(release_events) == 1

    monkeypatch.delenv("LOOP_PRODUCT_DISPATCH_FAIL_AFTER_ACTIVITY_APPEND")
    assert dispatcher.flush_activity_outbox(
        interrupted,
        g2_v2_fixture["catalog"],
        g2_v2_fixture["catalog_digest"],
    )
    assert interrupted["program_activity_outbox"] is None
    assert not dispatcher.flush_activity_outbox(
        interrupted,
        g2_v2_fixture["catalog"],
        g2_v2_fixture["catalog_digest"],
    )
    activity_records = [
        json.loads(line)
        for line in dispatcher.LOG_PATH.read_text(encoding="utf-8").splitlines()
    ]
    release_events = [
        row for row in activity_records if row.get("type") == "sequencing_gate_release"
    ]
    assert len(release_events) == 1


def test_persisted_release_admission_tamper_fails_closed(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    tasks, _ = _park_complete_g2_catalog(g2_v2_fixture, monkeypatch)
    admission = dispatcher._validate_g2_evidence(
        g2_v2_fixture["state"],
        g2_v2_fixture["catalog"],
        now=g2_v2_fixture["now"],
    )
    monkeypatch.setattr(
        dispatcher,
        "resolve_g2_evidence_admission",
        lambda state, catalog: deepcopy(admission),
    )
    _, _, _, release_logs, changed = dispatcher.materialize(
        state=g2_v2_fixture["state"],
        tasks=tasks,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
        timestamp=G2_RELEASED_AT,
    )
    assert changed is True
    dispatcher.enqueue_activity_outbox(
        g2_v2_fixture["state"],
        release_logs,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
    )
    release = g2_v2_fixture["state"]["program_sequencing_releases"][
        g2_v2_fixture["catalog"]["program_id"]
    ]
    release["g2_evidence_sha256"] = "0" * 64

    with pytest.raises(
        dispatcher.DispatchError,
        match="persisted G2 release admission snapshot is not exact",
    ):
        dispatcher.validate_sequencing_release_record(
            g2_v2_fixture["state"],
            g2_v2_fixture["catalog"],
            g2_v2_fixture["catalog_digest"],
        )


def test_auto_unblock_cannot_reopen_sequencing_park_marker() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "ai-task-archive" / "tasks").mkdir(parents=True)
        (root / ".orchestrator").mkdir(parents=True)
        (root / "ai-status.json").write_text(
            json.dumps(
                {
                    "tasks": [
                        {"id": "DEP-001", "status": "done"},
                        {
                            "id": "LOOP-PROD-AUTH-001",
                            "status": "blocked",
                            "owner": "Codex",
                            "depends_on": ["DEP-001"],
                            "last_update": "2026-01-01T00:00:00Z",
                            "sequencing_release_gate": {
                                "state": "parked",
                            },
                        },
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["python3", str(ROOT / "scripts" / "auto_unblock_stale.py"), "--dry-run"],
            env={**os.environ, "PANTHEON_STATUS_ROOT": str(root)},
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "WOULD reopen" not in result.stdout
        assert "reopened=none" in result.stdout


@pytest.mark.parametrize("release_record", [{}, {"schema_version": 1}])
def test_auto_unblock_missing_marker_rejects_malformed_release_record(
    release_record: dict,
) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "ai-task-archive" / "tasks").mkdir(parents=True)
        (root / ".orchestrator").mkdir(parents=True)
        program_id = "loop-product-level-remediation-2026-07-13"
        (root / "ai-status.json").write_text(
            json.dumps(
                {
                    "tasks": [
                        {"id": "DEP-001", "status": "done"},
                        {
                            "id": "LOOP-PROD-AUTH-001",
                            "status": "blocked",
                            "owner": "Codex",
                            "depends_on": ["DEP-001"],
                            "last_update": "2026-01-01T00:00:00Z",
                            "sequencing_release_admission_sha256": "0" * 64,
                            "source_ref": {
                                "program_id": program_id,
                                "catalog_sha256": "a" * 64,
                                "sequencing_overlay_sha256": "b" * 64,
                                "release_gate_id": "loop-product-g2-release-v1",
                                "sequencing_classification": (
                                    "deferred strict-auth/security/governance work"
                                ),
                            },
                        },
                    ],
                    "program_sequencing_releases": {
                        program_id: release_record
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts" / "auto_unblock_stale.py"),
                "--dry-run",
            ],
            env={**os.environ, "PANTHEON_STATUS_ROOT": str(root)},
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "WOULD reopen" not in result.stdout
        assert "reopened=none" in result.stdout


def test_auto_unblock_pending_program_outbox_performs_zero_mutation() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "ai-task-archive" / "tasks").mkdir(parents=True)
        orchestrator = root / ".orchestrator"
        orchestrator.mkdir(parents=True)
        status_path = root / "ai-status.json"
        state_path = orchestrator / "auto-unblock-state.json"
        status_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {"id": "DEP-001", "status": "done"},
                        {
                            "id": "READY-BUT-PAUSED-001",
                            "status": "blocked",
                            "owner": "Codex",
                            "depends_on": ["DEP-001"],
                            "last_update": "2026-01-01T00:00:00Z",
                        },
                    ],
                    "program_activity_outbox": {
                        "schema_version": 5,
                        "events": [{"event_id": "pending-program-audit"}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        state_path.write_text('{"sentinel":"unchanged"}\n', encoding="utf-8")
        status_before = status_path.read_bytes()
        state_before = state_path.read_bytes()

        result = subprocess.run(
            ["python3", str(ROOT / "scripts" / "auto_unblock_stale.py")],
            env={**os.environ, "PANTHEON_STATUS_ROOT": str(root)},
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "program_activity_outbox is pending" in result.stdout
        assert status_path.read_bytes() == status_before
        assert state_path.read_bytes() == state_before


def test_release_gate_opens_once_for_valid_g2_exact_set(
    g2_v2_fixture: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = g2_v2_fixture["dispatcher"]
    tasks = deepcopy(g2_v2_fixture["catalog"]["tasks"])
    tasks, _ = _park_complete_g2_catalog(
        g2_v2_fixture, monkeypatch, tasks=tasks
    )
    admission = dispatcher._validate_g2_evidence(
        g2_v2_fixture["state"],
        g2_v2_fixture["catalog"],
        now=g2_v2_fixture["now"],
    )
    calls = 0

    def counted_resolver(state, catalog):
        nonlocal calls
        calls += 1
        return deepcopy(admission)

    monkeypatch.setattr(
        dispatcher, "resolve_g2_evidence_admission", counted_resolver
    )

    created, preserved, _, _, changed = dispatcher.materialize(
        state=g2_v2_fixture["state"],
        tasks=tasks,
        catalog=g2_v2_fixture["catalog"],
        catalog_digest=g2_v2_fixture["catalog_digest"],
        timestamp=G2_RELEASED_AT,
    )

    assert calls == 1
    gated_ids = set(
        g2_v2_fixture["catalog"]["release_gate"]["gated_task_ids"]
    )
    assert set(created) == gated_ids
    assert len(preserved) == 29
    by_id = {task["id"]: task for task in g2_v2_fixture["state"]["tasks"]}
    assert by_id["LOOP-PROD-AUTH-001"]["status"] == "todo"
    assert all(by_id[task_id]["status"] == "todo" for task_id in gated_ids)
    assert changed is True
