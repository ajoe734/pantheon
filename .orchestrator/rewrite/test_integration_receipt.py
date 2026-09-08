from __future__ import annotations

from contextlib import contextmanager
import fcntl
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


def test_predicate_accepts_canonical_pantheon_repository_slug() -> None:
    task = task_row(
        target_repo="ajoe734/pantheon",
        integration_receipt=valid_receipt_payload(),
    )
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


def test_receipt_survives_assignment_generation_for_same_immutable_delivery() -> None:
    task = task_row(generation=5, integration_receipt=valid_receipt_payload(task_generation=4))
    assert ir.integration_receipt_consumes_candidate(task) is True
    assert task["integration_receipt"]["task_generation"] == 4


@pytest.mark.parametrize("generation", [0, -1, 3, True, "5"])
def test_receipt_rejects_future_or_invalid_generation(generation) -> None:
    task = task_row(generation=generation, integration_receipt=valid_receipt_payload())
    assert ir.integration_receipt_consumes_candidate(task) is False


@pytest.mark.parametrize("field,value", [
    ("pr", 999), ("head_sha", HEAD_B), ("base", "master"),
    ("head_branch", "task/different"), ("kind", "artifact_contract"),
])
def test_old_receipt_cannot_consume_conflicting_current_delivery(field, value) -> None:
    task = task_row(generation=5, integration_receipt=valid_receipt_payload())
    task["delivery_binding"] = {"kind": "pull_request", **task["review_binding"]}
    assert ir.integration_receipt_consumes_candidate(task)
    task["delivery_binding"][field] = value
    assert not ir.integration_receipt_consumes_candidate(task)


def test_predicate_false_when_repository_id_is_not_default() -> None:
    # A receipt with pantheon slug cannot consume an execute_plans task
    task = task_row(target_repo="execute_plans", integration_receipt=valid_receipt_payload())
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_true_when_execute_plans_receipt_matches() -> None:
    task = task_row(
        target_repo="execute-plans",
        review_binding={"pr": 747, "head_sha": HEAD_A, "base": "dev"},
        integration_receipt=valid_receipt_payload(
            repository="ajoe734/execute-plans",
            pr=747,
            head_sha=HEAD_A,
            target_branch="dev",
        ),
    )
    assert ir.integration_receipt_consumes_candidate(task) is True


@pytest.mark.parametrize(
    "target_repo",
    ["execute-plans", "execute_plans", "ajoe734/execute-plans"],
)
def test_predicate_true_for_execute_plans_target_repo_variants(target_repo) -> None:
    task = task_row(
        target_repo=target_repo,
        review_binding={"pr": 747, "head_sha": HEAD_A, "base": "dev"},
        integration_receipt=valid_receipt_payload(
            repository="ajoe734/execute-plans",
            pr=747,
            head_sha=HEAD_A,
            target_branch="dev",
        ),
    )
    assert ir.integration_receipt_consumes_candidate(task) is True


def test_predicate_false_for_unknown_repository() -> None:
    task = task_row(
        target_repo="unknown-repo",
        integration_receipt=valid_receipt_payload(repository="unknown-repo"),
    )
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_false_for_repository_without_slug() -> None:
    task = task_row(
        target_repo="runtime_platform",
        integration_receipt=valid_receipt_payload(repository="lean-platform"),
    )
    assert ir.integration_receipt_consumes_candidate(task) is False


def test_predicate_respects_config_slug_override() -> None:
    config = {
        "coordination": {
            "repositories": {
                "execute_plans": {"repo": "custom/execute-plans-fork"}
            }
        }
    }
    task_custom = task_row(
        target_repo="execute-plans",
        review_binding={"pr": 747, "head_sha": HEAD_A, "base": "dev"},
        integration_receipt=valid_receipt_payload(
            repository="custom/execute-plans-fork",
            pr=747,
            head_sha=HEAD_A,
            target_branch="dev",
        ),
    )
    # With override config, matches custom slug
    assert ir.integration_receipt_consumes_candidate(task_custom, config=config) is True
    # Without override config, default slug is expected so custom slug is rejected
    assert ir.integration_receipt_consumes_candidate(task_custom, config={}) is False


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


def _make_authority(
    root: Path,
    *,
    lock_path: Path,
    lock_pid: int,
    lock_inode: int | None = None,
    lock_device: int | None = None,
) -> ir.IntegrationAuthority:
    return ir.IntegrationAuthority(
        command_root=root,
        command_sha=_head_sha(root),
        command_remote="ajoe734/pantheon",
        command_base_ref="dev",
        status_root=root,
        lock_path=lock_path,
        lock_schema="test-lock/v1",
        lock_pid=lock_pid,
        lock_inode=lock_inode,
        lock_device=lock_device,
    )


@contextmanager
def _held_lock(lock_path: Path):
    with lock_path.open("r+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield handle


@contextmanager
def _held_authority(
    command_root: Path,
    lock_path: Path,
    lock_pid: int | None = None,
    **kwargs,
):
    pid = os.getpid() if lock_pid is None else lock_pid
    if not lock_path.exists():
        _write_lock(lock_path, pid=pid)
    with lock_path.open("r+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stat = os.fstat(held.fileno())
        yield _make_authority(
            command_root,
            lock_path=lock_path,
            lock_pid=pid,
            lock_inode=kwargs.pop("lock_inode", stat.st_ino),
            lock_device=kwargs.pop("lock_device", stat.st_dev),
            **kwargs,
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
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
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
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
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
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
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
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
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
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
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
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
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
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_lock(lock_path):
        authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
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
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
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
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
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


def test_record_writes_receipt_and_consumes_candidate_for_execute_plans(
    command_root: Path,
) -> None:
    task = task_row(
        id="OPS-FE-REVIEW-PROOF-001",
        target_repo="execute-plans",
        review_binding={"pr": 747, "head_sha": HEAD_A, "base": "dev"},
    )
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    binding = ir.IntegrationBinding(
        repository="ajoe734/execute-plans", target_branch="dev", pr=747, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
        result = ir.record_integration_receipt(
            config=_config_for(status_file),
            task_id="OPS-FE-REVIEW-PROOF-001",
            expected_generation=4,
            expected_delivery_binding=binding,
            observation=ir.RECEIPT_OBSERVATION_RECONCILED,
            merge_commit_sha=MERGE_A,
            observed_at="2026-09-08T04:45:00Z",
            status_file=status_file,
            event_path=None,
            authority=authority,
        )
        assert result.written is True
        assert result.replay is False
        on_disk = json.loads(status_file.read_text())
        receipt = on_disk["tasks"][0]["integration_receipt"]
        assert receipt["repository"] == "ajoe734/execute-plans"
        assert receipt["pr"] == 747
        assert receipt["observation"] == ir.RECEIPT_OBSERVATION_RECONCILED
        assert receipt["merge_commit_sha"] == MERGE_A

        # Subsequent read correctly consumed by pure predicate
        reloaded_task = on_disk["tasks"][0]
        assert ir.integration_receipt_consumes_candidate(reloaded_task) is True

        # Idempotent replay
        replay_result = ir.record_integration_receipt(
            config=_config_for(status_file),
            task_id="OPS-FE-REVIEW-PROOF-001",
            expected_generation=4,
            expected_delivery_binding=binding,
            observation=ir.RECEIPT_OBSERVATION_RECONCILED,
            merge_commit_sha=MERGE_A,
            observed_at="2026-09-08T04:45:00Z",
            status_file=status_file,
            event_path=None,
            authority=authority,
        )
        assert replay_result.written is False
        assert replay_result.replay is True


def test_record_persists_through_v2_journal_for_execute_plans(
    command_root: Path,
) -> None:
    task = task_row(
        id="OPS-FE-REVIEW-PROOF-001",
        target_repo="execute-plans",
        review_binding={"pr": 747, "head_sha": HEAD_A, "base": "dev"},
    )
    status_file = _setup_status_file(command_root, task)
    event_path = command_root / "task-state.jsonl"
    store.append_state_commit(event_path, {"tasks": [task]}, source="test-seed")
    lock_path = command_root / "lock.json"
    binding = ir.IntegrationBinding(
        repository="ajoe734/execute-plans", target_branch="dev", pr=747, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
        ir.record_integration_receipt(
            config=_config_for(status_file),
            task_id="OPS-FE-REVIEW-PROOF-001",
            expected_generation=4,
            expected_delivery_binding=binding,
            observation=ir.RECEIPT_OBSERVATION_RECONCILED,
            merge_commit_sha=MERGE_A,
            observed_at="2026-09-08T04:45:00Z",
            status_file=status_file,
            event_path=event_path,
            authority=authority,
        )
    events = store.load_events(event_path)
    assert len(events) == 2
    assert events[-1]["source"] == "canonical_auto_integrator"
    committed_task = events[-1]["state"]["tasks"][0]
    assert committed_task["integration_receipt"]["repository"] == "ajoe734/execute-plans"
    assert committed_task["integration_receipt"]["merge_commit_sha"] == MERGE_A


def test_record_rejects_mismatched_repository_slug_binding(
    command_root: Path,
) -> None:
    task = task_row(
        id="OPS-FE-REVIEW-PROOF-001",
        target_repo="execute-plans",
        review_binding={"pr": 747, "head_sha": HEAD_A, "base": "dev"},
    )
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    # Wrong slug (pantheon slug instead of execute-plans slug)
    wrong_binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=747, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
        with pytest.raises(ir.IntegrationReceiptBindingError) as exc_info:
            ir.record_integration_receipt(
                config=_config_for(status_file),
                task_id="OPS-FE-REVIEW-PROOF-001",
                expected_generation=4,
                expected_delivery_binding=wrong_binding,
                observation=ir.RECEIPT_OBSERVATION_RECONCILED,
                merge_commit_sha=MERGE_A,
                observed_at="2026-09-08T04:45:00Z",
                status_file=status_file,
                event_path=None,
                authority=authority,
            )
    assert "delivery binding no longer matches" in str(exc_info.value)


def test_record_rejects_unknown_target_repo(command_root: Path) -> None:
    task = task_row(
        id="UNKNOWN-001",
        target_repo="unknown-repo-xyz",
        review_binding={"pr": 1, "head_sha": HEAD_A, "base": "dev"},
    )
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    binding = ir.IntegrationBinding(
        repository="unknown-repo-xyz", target_branch="dev", pr=1, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
        with pytest.raises(ir.IntegrationReceiptBindingError):
            ir.record_integration_receipt(
                config=_config_for(status_file),
                task_id="UNKNOWN-001",
                expected_generation=4,
                expected_delivery_binding=binding,
                observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
                merge_commit_sha=MERGE_A,
                observed_at="2026-09-08T04:45:00Z",
                status_file=status_file,
                event_path=None,
                authority=authority,
            )


def test_record_rejects_conflicting_receipt_for_execute_plans(
    command_root: Path,
) -> None:
    task = task_row(
        id="OPS-FE-REVIEW-PROOF-001",
        target_repo="execute-plans",
        review_binding={"pr": 747, "head_sha": HEAD_A, "base": "dev"},
        integration_receipt=valid_receipt_payload(
            repository="ajoe734/execute-plans",
            pr=747,
            head_sha=HEAD_A,
            merge_commit_sha="9" * 40,
        ),
    )
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    binding = ir.IntegrationBinding(
        repository="ajoe734/execute-plans", target_branch="dev", pr=747, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
        with pytest.raises(ir.IntegrationReceiptConflictError):
            ir.record_integration_receipt(
                config=_config_for(status_file),
                task_id="OPS-FE-REVIEW-PROOF-001",
                expected_generation=4,
                expected_delivery_binding=binding,
                observation=ir.RECEIPT_OBSERVATION_RECONCILED,
                merge_commit_sha=MERGE_A,
                observed_at="2026-09-08T04:45:00Z",
                status_file=status_file,
                event_path=None,
                authority=authority,
            )


@pytest.mark.parametrize("replace_held_inode", [False, True])
def test_reject_unheld_or_replaced_lock(
    command_root: Path, replace_held_inode: bool
) -> None:
    task = task_row(
        id="OPS-FE-REVIEW-PROOF-001",
        target_repo="execute-plans",
        review_binding={"pr": 747, "head_sha": HEAD_A, "base": "dev"},
    )
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/execute-plans", target_branch="dev", pr=747, head_sha=HEAD_A
    )
    with lock_path.open("r+") as old:
        if replace_held_inode:
            fcntl.flock(old, fcntl.LOCK_EX | fcntl.LOCK_NB)
            replacement = command_root / "replacement.json"
            _write_lock(replacement, pid=os.getpid())
            os.replace(replacement, lock_path)
            assert os.fstat(old.fileno()).st_ino != lock_path.stat().st_ino
        authority = _make_authority(command_root, lock_path=lock_path, lock_pid=os.getpid())
        with pytest.raises(ir.IntegrationReceiptAuthorityError):
            ir.record_integration_receipt(
                config=_config_for(status_file),
                task_id="OPS-FE-REVIEW-PROOF-001",
                expected_generation=4,
                expected_delivery_binding=binding,
                observation=ir.RECEIPT_OBSERVATION_RECONCILED,
                merge_commit_sha=MERGE_A,
                observed_at="2026-09-08T04:45:00Z",
                status_file=status_file,
                event_path=None,
                authority=authority,
            )


def test_reject_lock_inode_mismatch(command_root: Path) -> None:
    task = task_row()
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    _write_lock(lock_path, pid=os.getpid())
    with _held_lock(lock_path) as held:
        actual_inode = os.fstat(held.fileno()).st_ino
        authority = _make_authority(
            command_root,
            lock_path=lock_path,
            lock_pid=os.getpid(),
            lock_inode=actual_inode + 12345,
        )
        with pytest.raises(ir.IntegrationReceiptAuthorityError) as exc_info:
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
        assert "canonical auto-integrator lock inode changed" in str(exc_info.value)


@pytest.mark.parametrize(
    "malformed_payload",
    [
        {"version": 999},
        {"version": 1, "result": "invalid"},
        {"version": 1, "result": "landed", "head_sha": "not-a-40-hex-oid"},
        {"version": 1, "result": "landed", "observed_at": "not-utc"},
        {},
        "not-a-dict",
    ],
)
def test_reject_malformed_existing_receipt(
    command_root: Path, malformed_payload: Any
) -> None:
    task = task_row(
        id="OPS-FE-REVIEW-PROOF-001",
        target_repo="execute-plans",
        review_binding={"pr": 747, "head_sha": HEAD_A, "base": "dev"},
        integration_receipt=malformed_payload,
    )
    status_file = _setup_status_file(command_root, task)
    lock_path = command_root / "lock.json"
    binding = ir.IntegrationBinding(
        repository="ajoe734/execute-plans", target_branch="dev", pr=747, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
        with pytest.raises(ir.IntegrationReceiptConflictError):
            ir.record_integration_receipt(
                config=_config_for(status_file),
                task_id="OPS-FE-REVIEW-PROOF-001",
                expected_generation=4,
                expected_delivery_binding=binding,
                observation=ir.RECEIPT_OBSERVATION_RECONCILED,
                merge_commit_sha=MERGE_A,
                observed_at="2026-09-08T04:45:00Z",
                status_file=status_file,
                event_path=None,
                authority=authority,
            )


def test_record_rejects_malformed_existing_receipt_without_mutation(
    command_root: Path,
) -> None:
    task = task_row(integration_receipt={"version": 999})
    status_file = _setup_status_file(command_root, task)
    before_content = status_file.read_text(encoding="utf-8")
    lock_path = command_root / "lock.json"
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
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
    after_content = status_file.read_text(encoding="utf-8")
    assert after_content == before_content
    on_disk = json.loads(after_content)
    assert on_disk["tasks"][0]["integration_receipt"] == {"version": 999}


def test_record_rejects_malformed_existing_receipt_with_v2_journal_without_mutation(
    command_root: Path,
) -> None:
    task = task_row(integration_receipt={"version": 999})
    status_file = _setup_status_file(command_root, task)
    event_path = command_root / "task-state.jsonl"
    store.append_state_commit(event_path, {"tasks": [task]}, source="test-seed")
    before_status = status_file.read_text(encoding="utf-8")
    events_before = store.load_events(event_path)
    assert len(events_before) == 1

    lock_path = command_root / "lock.json"
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
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
                event_path=event_path,
                authority=authority,
            )
    assert status_file.read_text(encoding="utf-8") == before_status
    events_after = store.load_events(event_path)
    assert len(events_after) == 1
    assert events_after[0]["state"]["tasks"][0]["integration_receipt"] == {"version": 999}


def test_record_absent_receipt_creates_receipt_with_v2_journal(
    command_root: Path,
) -> None:
    task = task_row()
    assert "integration_receipt" not in task
    status_file = _setup_status_file(command_root, task)
    event_path = command_root / "task-state.jsonl"
    store.append_state_commit(event_path, {"tasks": [task]}, source="test-seed")

    lock_path = command_root / "lock.json"
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
        result = ir.record_integration_receipt(
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
    assert result.written is True
    assert result.replay is False
    events = store.load_events(event_path)
    assert len(events) == 2
    assert events[-1]["source"] == "canonical_auto_integrator"
    assert events[-1]["state"]["tasks"][0]["integration_receipt"]["merge_commit_sha"] == MERGE_A


def test_record_exact_replay_does_not_mutate_v2_journal(
    command_root: Path,
) -> None:
    matching_receipt = valid_receipt_payload()
    task = task_row(integration_receipt=matching_receipt)
    status_file = _setup_status_file(command_root, task)
    event_path = command_root / "task-state.jsonl"
    store.append_state_commit(event_path, {"tasks": [task]}, source="test-seed")
    events_before = store.load_events(event_path)
    assert len(events_before) == 1

    lock_path = command_root / "lock.json"
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_authority(command_root, lock_path) as authority:
        result = ir.record_integration_receipt(
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
    assert result.written is False
    assert result.replay is True
    events_after = store.load_events(event_path)
    assert len(events_after) == 1


def test_shared_lock_must_not_authorize_receipt_write_and_leaves_state_and_v2_journal_unchanged(
    command_root: Path,
) -> None:
    task = task_row()
    status_file = _setup_status_file(command_root, task)
    event_path = command_root / "task-state.jsonl"
    store.append_state_commit(event_path, {"tasks": [task]}, source="test-seed")
    before_state = status_file.read_bytes()
    before_events = event_path.read_bytes()

    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with lock_path.open("r+") as held:
        fcntl.flock(held, fcntl.LOCK_SH | fcntl.LOCK_NB)
        stat = os.fstat(held.fileno())
        authority = _make_authority(
            command_root,
            lock_path=lock_path,
            lock_pid=os.getpid(),
            lock_inode=stat.st_ino,
            lock_device=stat.st_dev,
        )
        with pytest.raises(ir.IntegrationReceiptAuthorityError):
            ir.record_integration_receipt(
                config=_config_for(status_file),
                task_id=task["id"],
                expected_generation=4,
                expected_delivery_binding=binding,
                observation=ir.RECEIPT_OBSERVATION_RECONCILED,
                merge_commit_sha=MERGE_A,
                observed_at="2026-09-08T04:45:00Z",
                status_file=status_file,
                event_path=event_path,
                authority=authority,
            )

    assert status_file.read_bytes() == before_state
    assert event_path.read_bytes() == before_events


def test_replaced_lock_inode_rejection_leaves_state_and_v2_journal_unchanged(
    command_root: Path,
) -> None:
    task = task_row()
    status_file = _setup_status_file(command_root, task)
    event_path = command_root / "task-state.jsonl"
    store.append_state_commit(event_path, {"tasks": [task]}, source="test-seed")
    before_state = status_file.read_bytes()
    before_events = event_path.read_bytes()

    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid())
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with lock_path.open("r+") as old:
        fcntl.flock(old, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original_stat = os.fstat(old.fileno())
        replacement = command_root / "replacement.json"
        _write_lock(replacement, pid=os.getpid())
        with replacement.open("r+") as second:
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.replace(replacement, lock_path)
            assert os.fstat(second.fileno()).st_ino != original_stat.st_ino

            authority = _make_authority(
                command_root,
                lock_path=lock_path,
                lock_pid=os.getpid(),
                lock_inode=original_stat.st_ino,
                lock_device=original_stat.st_dev,
            )
            with pytest.raises(ir.IntegrationReceiptAuthorityError):
                ir.record_integration_receipt(
                    config=_config_for(status_file),
                    task_id=task["id"],
                    expected_generation=4,
                    expected_delivery_binding=binding,
                    observation=ir.RECEIPT_OBSERVATION_RECONCILED,
                    merge_commit_sha=MERGE_A,
                    observed_at="2026-09-08T04:45:00Z",
                    status_file=status_file,
                    event_path=event_path,
                    authority=authority,
                )

    assert status_file.read_bytes() == before_state
    assert event_path.read_bytes() == before_events


def test_lock_held_by_other_pid_leaves_state_and_v2_journal_unchanged(
    command_root: Path,
) -> None:
    task = task_row()
    status_file = _setup_status_file(command_root, task)
    event_path = command_root / "task-state.jsonl"
    store.append_state_commit(event_path, {"tasks": [task]}, source="test-seed")
    before_state = status_file.read_bytes()
    before_events = event_path.read_bytes()

    lock_path = command_root / "lock.json"
    _write_lock(lock_path, pid=os.getpid() + 999999)
    binding = ir.IntegrationBinding(
        repository="ajoe734/pantheon", target_branch="dev", pr=5411, head_sha=HEAD_A
    )
    with _held_lock(lock_path) as held:
        stat = os.fstat(held.fileno())
        authority = _make_authority(
            command_root,
            lock_path=lock_path,
            lock_pid=os.getpid(),
            lock_inode=stat.st_ino,
            lock_device=stat.st_dev,
        )
        with pytest.raises(ir.IntegrationReceiptAuthorityError):
            ir.record_integration_receipt(
                config=_config_for(status_file),
                task_id=task["id"],
                expected_generation=4,
                expected_delivery_binding=binding,
                observation=ir.RECEIPT_OBSERVATION_PERFORMED_MERGE,
                merge_commit_sha=MERGE_A,
                observed_at="2026-08-29T23:05:12Z",
                status_file=status_file,
                event_path=event_path,
                authority=authority,
            )

    assert status_file.read_bytes() == before_state
    assert event_path.read_bytes() == before_events
