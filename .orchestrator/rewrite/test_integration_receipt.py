from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import integration_receipt as ir
import task_state_store as store


HEAD_A = "254d2e7b05096dad3f6c7512db089ae2cbd8fe08"
MERGE_A = "8f8383b507b1fb631d44422031f01ebea5024d5e"
HEAD_B = "111111119999888877776666555544443333dead"


def valid_receipt_payload(**overrides) -> dict:
    payload = {
        "version": 1,
        "result": "landed",
        "observation": "performed_merge",
        "task_generation": 4,
        "repository": "ajoe734/pantheon",
        "target_branch": "dev",
        "pr": 5411,
        "head_sha": HEAD_A,
        "merge_commit_sha": MERGE_A,
        "observed_at": "2026-08-29T23:05:12Z",
        "source": "canonical_auto_integrator",
    }
    payload.update(overrides)
    return payload


def task_row(**overrides) -> dict:
    row = {
        "id": "DTG-TEST-1",
        "status": "review_approved",
        "generation": 4,
        "owner": "Claude",
        "reviewer": "Codex",
        "review_binding": {
            "pr": 5411,
            "head_sha": HEAD_A,
            "head_branch": "task/DTG-TEST-1",
            "base": "dev",
        },
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# schema accept/reject matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p,  # baseline valid
    ],
)
def test_parse_accepts_valid_receipt(mutate) -> None:
    assert ir.parse_integration_receipt(mutate(valid_receipt_payload())) is not None


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("version", 2),
        ("version", "1"),
        ("result", "merged"),
        ("observation", "force_pushed"),
        ("task_generation", 0),
        ("task_generation", "4"),
        ("task_generation", True),
        ("repository", ""),
        ("target_branch", ""),
        ("pr", 0),
        ("pr", -1),
        ("pr", "5411"),
        ("pr", True),
        ("head_sha", "not-a-sha"),
        ("head_sha", HEAD_A.upper()),
        ("head_sha", HEAD_A[:39]),
        ("merge_commit_sha", "zz" * 20),
        ("observed_at", "2026-08-29 23:05:12"),
        ("observed_at", "2026-08-29T23:05:12"),
        ("source", "ai-status"),
        ("source", "AutoIntegrator"),
    ],
)
def test_parse_rejects_malformed_fields(field, bad_value) -> None:
    payload = valid_receipt_payload(**{field: bad_value})
    assert ir.parse_integration_receipt(payload) is None


def test_parse_rejects_non_mapping_and_missing() -> None:
    assert ir.parse_integration_receipt(None) is None
    assert ir.parse_integration_receipt("landed") is None
    assert ir.parse_integration_receipt([1, 2]) is None
    assert ir.parse_integration_receipt({}) is None


def test_parse_rejects_mixed_case_sha_rather_than_normalizing() -> None:
    payload = valid_receipt_payload(head_sha=HEAD_A[:20] + HEAD_A[20:].upper())
    assert ir.parse_integration_receipt(payload) is None


# ---------------------------------------------------------------------------
# pure identity / consumption predicate matrix
# ---------------------------------------------------------------------------


def test_predicate_true_for_matching_receipt_and_binding() -> None:
    task = task_row(integration_receipt=valid_receipt_payload())
    assert ir.integration_receipt_consumes_candidate(task) is True


def test_predicate_false_without_receipt() -> None:
    assert ir.integration_receipt_consumes_candidate(task_row()) is False


def test_predicate_false_for_malformed_receipt() -> None:
    task = task_row(integration_receipt={"version": 1, "result": "landed"})
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_false_for_unknown_version() -> None:
    task = task_row(integration_receipt=valid_receipt_payload(version=99))
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_false_for_non_landed_result() -> None:
    payload = valid_receipt_payload()
    payload["result"] = "pending"
    task = task_row(integration_receipt=payload)
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_false_when_generation_changed() -> None:
    task = task_row(generation=5, integration_receipt=valid_receipt_payload(task_generation=4))
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_false_when_repository_id_is_not_default() -> None:
    task = task_row(target_repo="execute_plans", integration_receipt=valid_receipt_payload())
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_false_when_pr_rebound() -> None:
    task = task_row(
        review_binding={"pr": 9999, "head_sha": HEAD_A, "base": "dev"},
        integration_receipt=valid_receipt_payload(),
    )
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_false_when_head_changed() -> None:
    task = task_row(
        review_binding={"pr": 5411, "head_sha": HEAD_B, "base": "dev"},
        integration_receipt=valid_receipt_payload(),
    )
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_false_when_target_branch_changed() -> None:
    task = task_row(
        review_binding={"pr": 5411, "head_sha": HEAD_A, "base": "release"},
        integration_receipt=valid_receipt_payload(),
    )
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_false_when_review_binding_missing() -> None:
    task = task_row(integration_receipt=valid_receipt_payload())
    del task["review_binding"]
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_performs_no_io(monkeypatch) -> None:
    """No filesystem/network calls are reachable from the predicate path."""

    def _boom(*_a, **_k):
        raise AssertionError("predicate must not perform I/O")

    monkeypatch.setattr("builtins.open", _boom)
    task = task_row(integration_receipt=valid_receipt_payload())
    assert ir.integration_receipt_consumes_candidate(task) is True


# ---------------------------------------------------------------------------
# git fixture for authority checks
# ---------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture()
def command_root(tmp_path: Path) -> Path:
    repo = tmp_path / "command-root"
    repo.mkdir()
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-q", "-m", "init"], repo)
    _run(["git", "remote", "add", "origin", "https://github.com/ajoe734/pantheon.git"], repo)
    _run(["git", "branch", "dev"], repo)
    return repo


def _head_sha(repo: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


# ---------------------------------------------------------------------------
# record_integration_receipt: authority, mutation, replay, conflict
# ---------------------------------------------------------------------------


def _make_authority(root: Path, *, lock_path: Path, lock_pid: int) -> ir.IntegrationAuthority:
    return ir.IntegrationAuthority(
        command_root=root,
        command_sha=_head_sha(root),
        command_remote="ajoe734/pantheon",
        command_base_ref="dev",
        status_root=root,
        lock_path=lock_path,
        lock_schema="test-lock/v1",
        lock_pid=lock_pid,
    )


def _write_lock(lock_path: Path, *, pid: int, state_value: str = "held") -> None:
    lock_path.write_text(
        json.dumps({"schema": "test-lock/v1", "state": state_value, "pid": pid}),
        encoding="utf-8",
    )


def _setup_status_file(root: Path, task: dict) -> Path:
    status_file = root / "ai-status.json"
    status_file.write_text(
        json.dumps({"tasks": [task]}, indent=2) + "\n", encoding="utf-8"
    )
    return status_file


def _config_for(status_file: Path) -> dict:
    return {"paths": {"status_file": str(status_file)}}


def test_record_writes_receipt_and_updates_status_file(command_root: Path) -> None:
    task = task_row()
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    result = ir.record_integration_receipt(
        config=_config_for(status_file),
        task_id="DTG-TEST-1",
        expected_generation=4,
        expected_delivery_binding=binding,
        observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
        merge_commit_sha=MERGE_A,
        observed_at="2026-08-29T23:05:12Z",
        status_file=status_file,
        event_path=None,
        authority=authority,
    )
    assert result.written is True
    assert result.replay is False
    on_disk = json.loads(status_file.read_text())
    assert on_disk["tasks"][0]["integration_receipt"]["pr"] == 5411
    assert on_disk["tasks"][0]["status"] == "review_approved"


def test_record_persists_through_v2_journal_when_authoritative(command_root: Path) -> None:
    task = task_row()
    status_file = _setup_status_file(command_root, task)
    event_path = command_root / "task-state.jsonl"
    # Seed the V2 journal with the same initial state ai-status.json carries,
    # as production keeps both in sync before any receipt write is attempted.
    store.append_state_commit(event_path, {"tasks": [task]}, source="test-seed")
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    ir.record_integration_receipt(
        config=_config_for(status_file),
        task_id="DTG-TEST-1",
        expected_generation=4,
        expected_delivery_binding=binding,
        observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
        merge_commit_sha=MERGE_A,
        observed_at="2026-08-29T23:05:12Z",
        status_file=status_file,
        event_path=event_path,
        authority=authority,
    )
    events = store.load_events(event_path)
    assert len(events) == 2  # the fixture's seed commit, then the receipt commit
    assert events[-1]["source"] == "canonical_auto_integrator"
    committed_task = events[-1]["state"]["tasks"][0]
    assert committed_task["integration_receipt"]["merge_commit_sha"] == MERGE_A


def test_record_is_idempotent_on_exact_replay(command_root: Path) -> None:
    task = task_row()
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    kwargs = dict(
        config=_config_for(status_file),
        task_id="DTG-TEST-1",
        expected_generation=4,
        expected_delivery_binding=binding,
        observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
        merge_commit_sha=MERGE_A,
        observed_at="2026-08-29T23:05:12Z",
        status_file=status_file,
        event_path=None,
        authority=authority,
    )
    first = ir.record_integration_receipt(**kwargs)
    second = ir.record_integration_receipt(**kwargs)
    assert first.written is True
    assert second.written is False
    assert second.replay is True


def test_record_rejects_conflicting_receipt(command_root: Path) -> None:
    task = task_row(integration_receipt=valid_receipt_payload(pr=1))
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with pytest.raises(ir.IntegrationReceiptConflictError):
        ir.record_integration_receipt(
            config=_config_for(status_file),
            task_id="DTG-TEST-1",
            expected_generation=4,
            expected_delivery_binding=binding,
            observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
            merge_commit_sha=MERGE_A,
            observed_at="2026-08-29T23:05:12Z",
            status_file=status_file,
            event_path=None,
            authority=authority,
        )
    # never overwritten
    on_disk = json.loads(status_file.read_text())
    assert on_disk["tasks"][0]["integration_receipt"]["pr"] == 1


@pytest.mark.parametrize(
    "mutate_row,expected_error",
    [
        (lambda row: row.update(generation=5), ir.IntegrationReceiptBindingError),
        (lambda row: row.update(status="done"), ir.IntegrationReceiptBindingError),
        (
            lambda row: row.update(review_binding={"pr": 1, "head_sha": HEAD_A, "base": "dev"}),
            ir.IntegrationReceiptBindingError,
        ),
        (
            lambda row: row.update(
                review_binding={"pr": 5411, "head_sha": HEAD_B, "base": "dev"}
            ),
            ir.IntegrationReceiptBindingError,
        ),
        (
            lambda row: row.update(
                review_binding={"pr": 5411, "head_sha": HEAD_A, "base": "release"}
            ),
            ir.IntegrationReceiptBindingError,
        ),
    ],
)
def test_record_rejects_generation_and_binding_invalidation(
    command_root: Path, mutate_row, expected_error
) -> None:
    task = task_row()
    mutate_row(task)
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with pytest.raises(expected_error):
        ir.record_integration_receipt(
            config=_config_for(status_file),
            task_id="DTG-TEST-1",
            expected_generation=4,
            expected_delivery_binding=binding,
            observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
            merge_commit_sha=MERGE_A,
            observed_at="2026-08-29T23:05:12Z",
            status_file=status_file,
            event_path=None,
            authority=authority,
        )


def test_record_allows_active_merge_then_review_status(command_root: Path) -> None:
    task = task_row(status="in_progress")
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    result = ir.record_integration_receipt(
        config=_config_for(status_file),
        task_id="DTG-TEST-1",
        expected_generation=4,
        expected_delivery_binding=binding,
        observation=ir.RECEIPT_OBSERVATION_RECONCILED,
        merge_commit_sha=MERGE_A,
        observed_at="2026-08-29T23:05:12Z",
        status_file=status_file,
        event_path=None,
        authority=authority,
    )
    assert result.written is True


def test_record_fails_when_flock_owner_pid_mismatches(command_root: Path) -> None:
    task = task_row()
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid() + 999999)  # a different process
    authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with pytest.raises(ir.IntegrationReceiptAuthorityError):
        ir.record_integration_receipt(
            config=_config_for(status_file),
            task_id="DTG-TEST-1",
            expected_generation=4,
            expected_delivery_binding=binding,
            observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
            merge_commit_sha=MERGE_A,
            observed_at="2026-08-29T23:05:12Z",
            status_file=status_file,
            event_path=None,
            authority=authority,
        )
    on_disk = json.loads(status_file.read_text())
    assert "integration_receipt" not in on_disk["tasks"][0]


def test_record_fails_when_lock_file_missing(command_root: Path) -> None:
    task = task_row()
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "does-not-exist.json"
    authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with pytest.raises(ir.IntegrationReceiptAuthorityError):
        ir.record_integration_receipt(
            config=_config_for(status_file),
            task_id="DTG-TEST-1",
            expected_generation=4,
            expected_delivery_binding=binding,
            observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
            merge_commit_sha=MERGE_A,
            observed_at="2026-08-29T23:05:12Z",
            status_file=status_file,
            event_path=None,
            authority=authority,
        )


def test_record_fails_when_status_root_is_not_canonical(command_root: Path, tmp_path: Path) -> None:
    task = task_row()
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    wrong_root = tmp_path / "elsewhere"
    wrong_root.mkdir()
    authority = ir.IntegrationAuthority(
        command_root=command_root,
        command_sha=_head_sha(command_root),
        command_remote="ajoe734/pantheon",
        command_base_ref="dev",
        status_root=wrong_root,
        lock_path=lock_path,
        lock_schema="test-lock/v1",
        lock_pid=os.getpid(),
    )
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with pytest.raises(ir.IntegrationReceiptAuthorityError):
        ir.record_integration_receipt(
            config=_config_for(status_file),
            task_id="DTG-TEST-1",
            expected_generation=4,
            expected_delivery_binding=binding,
            observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
            merge_commit_sha=MERGE_A,
            observed_at="2026-08-29T23:05:12Z",
            status_file=status_file,
            event_path=None,
            authority=authority,
        )


def test_record_fails_when_command_sha_mismatches(command_root: Path) -> None:
    task = task_row()
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    authority = ir.IntegrationAuthority(
        command_root=command_root,
        command_sha="0" * 40,
        command_remote="ajoe734/pantheon",
        command_base_ref="dev",
        status_root=command_root,
        lock_path=lock_path,
        lock_schema="test-lock/v1",
        lock_pid=os.getpid(),
    )
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with pytest.raises(ir.IntegrationReceiptAuthorityError):
        ir.record_integration_receipt(
            config=_config_for(status_file),
            task_id="DTG-TEST-1",
            expected_generation=4,
            expected_delivery_binding=binding,
            observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
            merge_commit_sha=MERGE_A,
            observed_at="2026-08-29T23:05:12Z",
            status_file=status_file,
            event_path=None,
            authority=authority,
        )


def test_task_remains_review_approved_after_receipt(command_root: Path) -> None:
    task = task_row(status="review_approved")
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    ir.record_integration_receipt(
        config=_config_for(status_file),
        task_id="DTG-TEST-1",
        expected_generation=4,
        expected_delivery_binding=binding,
        observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
        merge_commit_sha=MERGE_A,
        observed_at="2026-08-29T23:05:12Z",
        status_file=status_file,
        event_path=None,
        authority=authority,
    )
    on_disk = json.loads(status_file.read_text())
    assert on_disk["tasks"][0]["status"] == "review_approved"


def test_process_restart_suppression_end_to_end(command_root: Path) -> None:
    """Simulates the real defect: after a receipt lands, a *second, fresh*
    evaluation (a new cron process reading the same status file) must not
    re-select the task as a candidate."""

    task = task_row()
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    ir.record_integration_receipt(
        config=_config_for(status_file),
        task_id="DTG-TEST-1",
        expected_generation=4,
        expected_delivery_binding=binding,
        observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
        merge_commit_sha=MERGE_A,
        observed_at="2026-08-29T23:05:12Z",
        status_file=status_file,
        event_path=None,
        authority=authority,
    )
    # Fresh read, as a brand-new process/cron cycle would do.
    reloaded_task = json.loads(status_file.read_text())["tasks"][0]
    assert ir.integration_receipt_consumes_candidate(reloaded_task) is True
